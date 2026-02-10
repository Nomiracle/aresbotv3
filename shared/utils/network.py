"""Network identity helpers for worker runtime and registration."""

from dataclasses import dataclass
import ipaddress
import json
import logging
import os
import socket
import time
from typing import Any, Optional
from urllib import error, request


logger = logging.getLogger(__name__)

PUBLIC_IP_PROVIDERS = (
    "https://api.ipify.org?format=json",
    "https://api64.ipify.org?format=json",
    "https://ifconfig.me/ip",
)

GEOLOCATION_PROVIDERS = (
    "https://ipwho.is/{ip}",
    "https://ipapi.co/{ip}/json/",
)


@dataclass(frozen=True)
class WorkerNetworkIdentity:
    hostname: str
    private_ip: str
    public_ip: str
    ip_location: str

    @property
    def worker_ip(self) -> str:
        """Return the preferred worker display IP (public first)."""
        return self.public_ip or self.private_ip


_cached_identity: Optional[WorkerNetworkIdentity] = None
_cached_until_monotonic = 0.0


def _normalize_ip(raw_value: str) -> str:
    candidate = str(raw_value or "").strip()
    if not candidate:
        return ""
    try:
        ipaddress.ip_address(candidate)
        return candidate
    except ValueError:
        return ""


def _is_public_ip(ip: str) -> bool:
    normalized = _normalize_ip(ip)
    if not normalized:
        return False

    parsed = ipaddress.ip_address(normalized)
    return not (
        parsed.is_private
        or parsed.is_loopback
        or parsed.is_link_local
        or parsed.is_multicast
        or parsed.is_reserved
        or parsed.is_unspecified
    )


def _read_env_value(*keys: str) -> str:
    for key in keys:
        value = os.environ.get(key, "")
        if value and value.strip():
            return value.strip()
    return ""


def _read_float_env(key: str, default: float) -> float:
    raw = os.environ.get(key)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _http_get(url: str, timeout_seconds: float) -> Optional[str]:
    req = request.Request(url, headers={"User-Agent": "aresbot-worker/1.0"})
    try:
        with request.urlopen(req, timeout=timeout_seconds) as resp:
            payload = resp.read()
            return payload.decode("utf-8", errors="ignore").strip()
    except (error.URLError, TimeoutError, ValueError, OSError) as err:
        logger.debug("network lookup request failed url=%s error=%s", url, err)
        return None


def _http_get_json(url: str, timeout_seconds: float) -> Optional[dict[str, Any]]:
    text = _http_get(url, timeout_seconds)
    if not text:
        return None

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        logger.debug("network lookup returned invalid JSON url=%s payload=%s", url, text)
        return None

    if isinstance(data, dict):
        return data
    return None


def _resolve_private_ip() -> str:
    env_ip = _normalize_ip(_read_env_value("WORKER_PRIVATE_IP", "WORKER_LOCAL_IP"))
    if env_ip:
        return env_ip

    udp_socket: Optional[socket.socket] = None
    try:
        udp_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        udp_socket.connect(("8.8.8.8", 80))
        discovered = _normalize_ip(udp_socket.getsockname()[0])
        if discovered:
            return discovered
    except OSError:
        pass
    finally:
        if udp_socket is not None:
            try:
                udp_socket.close()
            except OSError:
                pass

    try:
        fallback_ip = _normalize_ip(socket.gethostbyname(socket.gethostname()))
    except OSError:
        return ""
    return fallback_ip


def _extract_public_ip(payload: dict[str, Any]) -> str:
    candidates = [
        payload.get("ip"),
        payload.get("query"),
        payload.get("ip_address"),
        payload.get("address"),
    ]
    for item in candidates:
        normalized = _normalize_ip(str(item or ""))
        if normalized:
            return normalized
    return ""


def _resolve_public_ip(timeout_seconds: float) -> str:
    env_public_ip = _normalize_ip(_read_env_value("WORKER_PUBLIC_IP", "WORKER_EGRESS_IP"))
    if env_public_ip:
        return env_public_ip

    for url in PUBLIC_IP_PROVIDERS:
        if "format=json" in url:
            payload = _http_get_json(url, timeout_seconds)
            if not payload:
                continue
            public_ip = _extract_public_ip(payload)
        else:
            text = _http_get(url, timeout_seconds)
            public_ip = _normalize_ip(text or "")

        if public_ip:
            return public_ip

    return ""


def _build_ip_location(country: str, region: str, city: str) -> str:
    parts: list[str] = []
    for raw in (country, region, city):
        normalized = str(raw or "").strip()
        if not normalized:
            continue
        if normalized in parts:
            continue
        parts.append(normalized)
    return " / ".join(parts)


def _resolve_ip_location(ip: str, timeout_seconds: float) -> str:
    configured = _read_env_value("WORKER_IP_LOCATION")
    if configured:
        return configured

    normalized_ip = _normalize_ip(ip)
    if not normalized_ip:
        return ""

    for template in GEOLOCATION_PROVIDERS:
        payload = _http_get_json(template.format(ip=normalized_ip), timeout_seconds)
        if not payload:
            continue

        if "ipwho.is" in template and payload.get("success") is False:
            continue

        location = _build_ip_location(
            country=str(payload.get("country") or payload.get("country_name") or ""),
            region=str(payload.get("region") or payload.get("regionName") or ""),
            city=str(payload.get("city") or ""),
        )
        if location:
            return location

    return ""


def get_worker_network_identity(force_refresh: bool = False) -> WorkerNetworkIdentity:
    """Resolve worker host/private/public IP and IP geolocation with cache."""
    global _cached_identity
    global _cached_until_monotonic

    now_monotonic = time.monotonic()
    if not force_refresh and _cached_identity and now_monotonic < _cached_until_monotonic:
        return _cached_identity

    timeout_seconds = max(_read_float_env("WORKER_NETWORK_HTTP_TIMEOUT", 2.0), 0.2)
    cache_ttl_seconds = max(_read_float_env("WORKER_NETWORK_CACHE_SECONDS", 300.0), 0.0)

    hostname = socket.gethostname()
    private_ip = _resolve_private_ip()
    public_ip = _resolve_public_ip(timeout_seconds)

    if not public_ip and _is_public_ip(private_ip):
        public_ip = private_ip

    ip_location = _resolve_ip_location(public_ip, timeout_seconds)

    identity = WorkerNetworkIdentity(
        hostname=hostname,
        private_ip=private_ip,
        public_ip=public_ip,
        ip_location=ip_location,
    )

    _cached_identity = identity
    _cached_until_monotonic = now_monotonic + cache_ttl_seconds
    return identity

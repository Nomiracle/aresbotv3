"""Exchange account management routes."""
import asyncio
import json
import logging
import os
import string
from collections import defaultdict
from functools import partial
from typing import Any, Dict, List, Optional

import ccxt

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import get_current_user, get_db_session
from api.db.crud import AccountCRUD
from api.db.models import ExchangeAccount
from shared.core.redis_client import get_redis_client
from shared.exchanges import FUTURES_EXCHANGE_IDS
from shared.utils.crypto import decrypt_api_secret, encrypt_api_secret

router = APIRouter()
logger = logging.getLogger(__name__)

SYMBOLS_CACHE_TTL_SECONDS = 600
EXCHANGES_CACHE_KEY = "exchanges:supported:v3"
EXCHANGES_CACHE_TTL_SECONDS = int(os.environ.get("EXCHANGES_CACHE_TTL_SECONDS", "3600"))
INTERNAL_SUPPORTED_EXCHANGES = ("polymarket_updown15m", "polymarket_updown5m", "polymarket_updown1h")
DEFAULT_SUPPORTED_EXCHANGES = ("binance", *INTERNAL_SUPPORTED_EXCHANGES)


def _get_polymarket_updown15m_symbols() -> List[str]:
    markets = ("btc", "eth", "sol", "xrp")
    outcomes = ("Up", "Down")
    return [f"{market}-{outcome}" for market in markets for outcome in outcomes]


def _get_polymarket_updown5m_symbols() -> List[str]:
    outcomes = ("Up", "Down")
    return [f"btc-{outcome}" for outcome in outcomes]


def _get_polymarket_updown1h_symbols() -> List[str]:
    markets = ("btc", "eth", "sol", "xrp")
    outcomes = ("Up", "Down")
    return [f"{market}-{outcome}" for market in markets for outcome in outcomes]

EXCHANGE_LABEL_OVERRIDES = {
    "okx": "OKX",
    "binance": "Binance Spot",
    "binanceusdm": "Binance USDM",
    "binancecoinm": "Binance COIN-M",
    "polymarket_updown15m": "Polymarket UpDown 15m",
    "polymarket_updown5m": "Polymarket UpDown 5m",
    "polymarket_updown1h": "Polymarket UpDown 1h",
}


class AccountCreate(BaseModel):
    exchange: str
    label: str
    api_key: str
    api_secret: str
    testnet: bool = False


class AccountCopy(BaseModel):
    exchange: str


class AccountUpdate(BaseModel):
    label: Optional[str] = None
    api_key: Optional[str] = None
    api_secret: Optional[str] = None
    testnet: Optional[bool] = None
    is_active: Optional[bool] = None


class AccountResponse(BaseModel):
    id: int
    exchange: str
    label: str
    api_key: str
    testnet: bool
    is_active: bool
    created_at: str

    model_config = {"from_attributes": True}


class TradingFeeResponse(BaseModel):
    symbol: str
    maker: float
    taker: float


class ExchangeOptionResponse(BaseModel):
    value: str
    label: str


def _format_exchange_label(exchange_id: str) -> str:
    normalized_id = exchange_id.lower().strip()
    if normalized_id in EXCHANGE_LABEL_OVERRIDES:
        return EXCHANGE_LABEL_OVERRIDES[normalized_id]

    words = normalized_id.replace("_", " ").replace("-", " ").split()
    if not words:
        return normalized_id.upper()

    return " ".join(word.upper() if len(word) <= 3 else word.capitalize() for word in words)


def _get_supported_exchange_ids() -> List[str]:
    raw_config = os.environ.get("SUPPORTED_EXCHANGES", "")
    if raw_config.strip():
        configured = [
            item.strip().lower()
            for item in raw_config.split(",")
            if item.strip()
        ]
    else:
        configured = []

    available_exchanges = set(getattr(ccxt, "exchanges", []))
    internal_exchanges = set(INTERNAL_SUPPORTED_EXCHANGES)
    supported_exchanges = available_exchanges | internal_exchanges

    if not configured:
        # 未配置时返回所有 CCXT 交易所 + 内部交易所
        return sorted(available_exchanges) + list(INTERNAL_SUPPORTED_EXCHANGES)

    validated: List[str] = []
    for exchange_id in configured:
        if exchange_id not in supported_exchanges:
            logger.warning("skip unsupported exchange id from config: %s", exchange_id)
            continue
        if exchange_id in validated:
            continue
        validated.append(exchange_id)

    return validated or sorted(available_exchanges) + list(INTERNAL_SUPPORTED_EXCHANGES)


def _build_exchange_options() -> List[ExchangeOptionResponse]:
    exchange_ids = _get_supported_exchange_ids()
    return [
        ExchangeOptionResponse(value=exchange_id, label=_format_exchange_label(exchange_id))
        for exchange_id in exchange_ids
    ]


def mask_api_key(api_key: str) -> str:
    if len(api_key) <= 8:
        return "*" * len(api_key)
    return api_key[:4] + "*" * (len(api_key) - 8) + api_key[-4:]


def account_to_response(account: ExchangeAccount) -> AccountResponse:
    return AccountResponse(
        id=account.id,
        exchange=account.exchange,
        label=account.label,
        api_key=mask_api_key(account.api_key),
        testnet=account.testnet,
        is_active=account.is_active,
        created_at=account.created_at.isoformat(),
    )


def _get_symbols_cache_key(exchange: str, testnet: bool) -> str:
    normalized_exchange = exchange.lower().strip()
    return f"symbols:{normalized_exchange}:{str(bool(testnet)).lower()}"


def _normalize_polymarket_private_key(raw_secret: str) -> str:
    private_key = (raw_secret or "").strip()
    if private_key.lower().startswith("0x"):
        private_key = private_key[2:]

    if len(private_key) != 64 or any(ch not in string.hexdigits for ch in private_key):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Polymarket API Secret 必须是 64 位十六进制私钥（可带 0x 前缀）",
        )

    return private_key


def _is_futures_exchange(exchange_id: str) -> bool:
    return exchange_id.lower().strip() in FUTURES_EXCHANGE_IDS


def _is_contract_market(market: Dict[str, Any]) -> bool:
    return bool(
        market.get("contract")
        or market.get("swap")
        or market.get("future")
    )


def _create_ccxt_exchange(
    exchange: str,
    testnet: bool,
    api_key: Optional[str] = None,
    api_secret: Optional[str] = None,
) -> Any:
    normalized_exchange = exchange.lower().strip()
    exchange_class = getattr(ccxt, normalized_exchange, None)
    if exchange_class is None:
        raise ValueError(f"Unsupported exchange: {exchange}")

    default_market_type = "swap" if _is_futures_exchange(normalized_exchange) else "spot"

    config: Dict[str, Any] = {
        "enableRateLimit": True,
        "options": {
            "defaultType": default_market_type,
        },
    }
    if api_key:
        config["apiKey"] = api_key
    if api_secret:
        config["secret"] = api_secret

    exchange_client = exchange_class(config)

    if testnet:
        if hasattr(exchange_client, "set_sandbox_mode"):
            exchange_client.set_sandbox_mode(True)
        elif hasattr(exchange_client, "enable_demo_trading"):
            exchange_client.enable_demo_trading(True)

    return exchange_client


def _sort_symbols_by_quote(
    markets: Dict[str, Dict[str, Any]],
    exchange: str,
) -> List[str]:
    grouped_symbols: Dict[str, List[str]] = defaultdict(list)
    use_contract_markets = _is_futures_exchange(exchange)

    for market in markets.values():
        symbol = market.get("symbol")
        if not isinstance(symbol, str) or "/" not in symbol:
            continue

        if market.get("active") is False:
            continue

        if use_contract_markets:
            if not _is_contract_market(market):
                continue
            quote = str(
                market.get("settle")
                or market.get("quote")
                or symbol.split("/")[-1]
            ).upper()
        else:
            if not market.get("spot", False):
                continue
            quote = str(market.get("quote") or symbol.split("/")[-1]).upper()

        grouped_symbols[quote].append(symbol)

    sorted_quotes = sorted(grouped_symbols.keys(), key=lambda quote: (quote != "USDT", quote))

    symbols: List[str] = []
    for quote in sorted_quotes:
        quote_symbols = sorted(grouped_symbols[quote], key=lambda item: item.upper())
        symbols.extend(quote_symbols)

    return symbols


def _safe_close_exchange(exchange_client: Any) -> None:
    close_method = getattr(exchange_client, "close", None)
    if callable(close_method):
        close_method()


def _load_account_symbols_sync(exchange: str, testnet: bool) -> List[str]:
    exchange_client = _create_ccxt_exchange(exchange=exchange, testnet=testnet)
    try:
        markets = exchange_client.load_markets()
        return _sort_symbols_by_quote(markets, exchange=exchange)
    finally:
        _safe_close_exchange(exchange_client)


def _fetch_account_trading_fee_sync(
    exchange: str,
    testnet: bool,
    api_key: str,
    api_secret: str,
    symbol: str,
) -> TradingFeeResponse:
    exchange_client = _create_ccxt_exchange(
        exchange=exchange,
        testnet=testnet,
        api_key=api_key,
        api_secret=api_secret,
    )

    try:
        markets = exchange_client.load_markets()
        market = markets.get(symbol)
        if not isinstance(market, dict):
            raise ValueError(f"Unsupported symbol for {exchange}: {symbol}")

        maker = float(market.get("maker") or 0.0)
        taker = float(market.get("taker") or maker)

        has_fetch_trading_fee = bool(getattr(exchange_client, "has", {}).get("fetchTradingFee"))
        if has_fetch_trading_fee:
            try:
                fee_info = exchange_client.fetch_trading_fee(symbol)
            except Exception as err:
                logger.warning(
                    "fetch_trading_fee fallback to market fee exchange=%s symbol=%s error=%s",
                    exchange,
                    symbol,
                    err,
                )
            else:
                if isinstance(fee_info, dict):
                    if fee_info.get("maker") is not None:
                        maker = float(fee_info["maker"])
                    if fee_info.get("taker") is not None:
                        taker = float(fee_info["taker"])

        return TradingFeeResponse(symbol=symbol, maker=maker, taker=taker)
    finally:
        _safe_close_exchange(exchange_client)


@router.get("/exchanges", response_model=List[ExchangeOptionResponse])
async def list_supported_exchanges(
    refresh: bool = False,
    user_email: str = Depends(get_current_user),
):
    del user_email

    redis_client = get_redis_client().client

    if not refresh:
        try:
            cached_raw = redis_client.get(EXCHANGES_CACHE_KEY)
            if cached_raw:
                parsed = json.loads(cached_raw)
                if isinstance(parsed, list):
                    return [ExchangeOptionResponse(**item) for item in parsed if isinstance(item, dict)]
        except Exception as err:
            logger.warning("read exchanges cache failed key=%s error=%s", EXCHANGES_CACHE_KEY, err)

    options = _build_exchange_options()

    try:
        redis_client.setex(
            EXCHANGES_CACHE_KEY,
            max(EXCHANGES_CACHE_TTL_SECONDS, 60),
            json.dumps([option.model_dump() for option in options]),
        )
    except Exception as err:
        logger.warning("write exchanges cache failed key=%s error=%s", EXCHANGES_CACHE_KEY, err)

    return options


@router.get("", response_model=List[AccountResponse])
async def list_accounts(
    user_email: str = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
):
    accounts = await AccountCRUD.get_all(session, user_email)
    return [account_to_response(acc) for acc in accounts]


@router.post("", response_model=AccountResponse, status_code=status.HTTP_201_CREATED)
async def create_account(
    data: AccountCreate,
    user_email: str = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
):
    exchange_name = data.exchange.strip().lower()
    api_key = data.api_key.strip()
    api_secret = data.api_secret.strip()

    if exchange_name in ("polymarket_updown15m", "polymarket_updown5m", "polymarket_updown1h"):
        if not api_key:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Polymarket API Key 不能为空（应填写资金地址/funder）",
            )
        api_secret = _normalize_polymarket_private_key(api_secret)

    encrypted_key = encrypt_api_secret(api_key)
    encrypted_secret = encrypt_api_secret(api_secret)

    account = await AccountCRUD.create(
        session,
        user_email=user_email,
        exchange=exchange_name,
        label=data.label,
        api_key=encrypted_key,
        api_secret=encrypted_secret,
        testnet=data.testnet,
    )
    return account_to_response(account)


@router.post("/{account_id}/copy", response_model=AccountResponse, status_code=status.HTTP_201_CREATED)
async def copy_account(
    account_id: int,
    data: AccountCopy,
    user_email: str = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
):
    source = await AccountCRUD.get_by_id(session, account_id, user_email)
    if not source:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Account not found")

    target_exchange = data.exchange.strip().lower()
    exchange_label = _format_exchange_label(target_exchange)
    new_label = f"{source.label} ({exchange_label})"

    account = await AccountCRUD.create(
        session,
        user_email=user_email,
        exchange=target_exchange,
        label=new_label,
        api_key=source.api_key,
        api_secret=source.api_secret,
        testnet=source.testnet,
    )
    return account_to_response(account)


@router.get("/{account_id}", response_model=AccountResponse)
async def get_account(
    account_id: int,
    user_email: str = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
):
    account = await AccountCRUD.get_by_id(session, account_id, user_email)
    if not account:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Account not found")
    return account_to_response(account)


@router.get("/{account_id}/symbols", response_model=List[str])
async def get_account_symbols(
    account_id: int,
    user_email: str = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
):
    account = await AccountCRUD.get_by_id(session, account_id, user_email)
    if not account:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Account not found")

    if account.exchange.strip().lower() == "polymarket_updown15m":
        return _get_polymarket_updown15m_symbols()
    if account.exchange.strip().lower() == "polymarket_updown5m":
        return _get_polymarket_updown5m_symbols()
    if account.exchange.strip().lower() == "polymarket_updown1h":
        return _get_polymarket_updown1h_symbols()

    cache_key = _get_symbols_cache_key(account.exchange, account.testnet)
    redis_client = get_redis_client().client

    try:
        cached_symbols_raw = redis_client.get(cache_key)
        if cached_symbols_raw:
            cached_symbols = json.loads(cached_symbols_raw)
            if isinstance(cached_symbols, list):
                return [str(symbol) for symbol in cached_symbols]
    except Exception as err:
        logger.warning("read symbols cache failed key=%s error=%s", cache_key, err)

    try:
        loop = asyncio.get_running_loop()
        symbols = await loop.run_in_executor(
            None,
            partial(_load_account_symbols_sync, account.exchange, account.testnet),
        )
    except ValueError as err:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(err)) from err
    except Exception as err:
        logger.exception(
            "load account symbols failed account_id=%s exchange=%s testnet=%s",
            account.id,
            account.exchange,
            account.testnet,
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Failed to load symbols: {err}",
        ) from err

    try:
        redis_client.setex(cache_key, SYMBOLS_CACHE_TTL_SECONDS, json.dumps(symbols))
    except Exception as err:
        logger.warning("write symbols cache failed key=%s error=%s", cache_key, err)

    return symbols


@router.get("/{account_id}/trading-fee", response_model=TradingFeeResponse)
async def fetch_trading_fee(
    account_id: int,
    symbol: str,
    user_email: str = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
):
    account = await AccountCRUD.get_by_id(session, account_id, user_email)
    if not account:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Account not found")

    try:
        api_key = decrypt_api_secret(account.api_key)
        api_secret = decrypt_api_secret(account.api_secret)
    except Exception as err:
        logger.exception("decrypt account credentials failed account_id=%s", account.id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Account credentials are invalid",
        ) from err

    try:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None,
            partial(
                _fetch_account_trading_fee_sync,
                account.exchange,
                account.testnet,
                api_key,
                api_secret,
                symbol,
            ),
        )
    except ValueError as err:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(err)) from err
    except Exception as err:
        logger.exception(
            "fetch trading fee failed account_id=%s exchange=%s symbol=%s",
            account.id,
            account.exchange,
            symbol,
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Failed to fetch trading fee: {err}",
        ) from err


@router.put("/{account_id}", response_model=AccountResponse)
async def update_account(
    account_id: int,
    data: AccountUpdate,
    user_email: str = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
):
    account = await AccountCRUD.get_by_id(session, account_id, user_email)
    if not account:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Account not found")

    update_data = data.model_dump(exclude_unset=True)
    exchange_name = account.exchange.strip().lower()
    if "api_key" in update_data and update_data["api_key"]:
        normalized_key = str(update_data["api_key"]).strip()
        if exchange_name in ("polymarket_updown15m", "polymarket_updown5m", "polymarket_updown1h") and not normalized_key:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Polymarket API Key 不能为空（应填写资金地址/funder）",
            )
        update_data["api_key"] = encrypt_api_secret(normalized_key)
    if "api_secret" in update_data and update_data["api_secret"]:
        normalized_secret = str(update_data["api_secret"]).strip()
        if exchange_name in ("polymarket_updown15m", "polymarket_updown5m", "polymarket_updown1h"):
            normalized_secret = _normalize_polymarket_private_key(normalized_secret)
        update_data["api_secret"] = encrypt_api_secret(normalized_secret)

    account = await AccountCRUD.update(session, account, **update_data)
    return account_to_response(account)


@router.delete("/{account_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_account(
    account_id: int,
    user_email: str = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
):
    account = await AccountCRUD.get_by_id(session, account_id, user_email)
    if not account:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Account not found")
    await AccountCRUD.delete(session, account)

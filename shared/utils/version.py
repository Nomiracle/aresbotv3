"""Worker module version utility."""
import os
import subprocess

_cached_version: str | None = None


def get_worker_version() -> str:
    """Get the worker module version string.

    Priority: /app/VERSION file > WORKER_VERSION env > git command > "unknown"
    """
    global _cached_version
    if _cached_version is not None:
        return _cached_version

    # 1. Docker 环境：读取构建时写入的 VERSION 文件
    version_file = "/app/VERSION"
    if os.path.isfile(version_file):
        try:
            content = open(version_file).read().strip()
            if content:
                _cached_version = content
                return _cached_version
        except OSError:
            pass

    # 2. 环境变量
    env_version = os.environ.get("WORKER_VERSION", "").strip()
    if env_version:
        _cached_version = env_version
        return _cached_version

    # 3. 本地开发：通过 git 获取（提交时间戳 + short hash）
    try:
        short_hash = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            stderr=subprocess.DEVNULL,
            timeout=5,
        ).decode().strip()
        commit_time = subprocess.check_output(
            ["git", "log", "-1", "--format=%cd", "--date=format:%Y%m%d%H"],
            stderr=subprocess.DEVNULL,
            timeout=5,
        ).decode().strip()
        if short_hash and commit_time:
            _cached_version = f"v{commit_time}.{short_hash}"
            return _cached_version
    except Exception:
        pass

    _cached_version = "unknown"
    return _cached_version

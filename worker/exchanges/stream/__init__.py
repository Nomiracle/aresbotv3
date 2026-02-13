from .base import StreamManager
from .ccxt_stream import CcxtStreamManager

try:
    from .polymarket_stream import PolymarketStreamManager
except ModuleNotFoundError as err:
    if err.name != "websocket":
        raise
    PolymarketStreamManager = None  # type: ignore[assignment]

__all__ = ["StreamManager", "CcxtStreamManager", "PolymarketStreamManager"]

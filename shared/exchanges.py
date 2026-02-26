"""Shared exchange identifiers."""

from typing import Final, List

FUTURES_EXCHANGE_IDS: Final[frozenset[str]] = frozenset(
    {"binanceusdm", "binancecoinm"}
)

# Polymarket UpDown 支持的市场
POLYMARKET_UPDOWN_MARKETS: Final[tuple[str, ...]] = ("btc", "eth", "sol", "xrp")
POLYMARKET_UPDOWN_5M_MARKETS: Final[tuple[str, ...]] = POLYMARKET_UPDOWN_MARKETS
POLYMARKET_UPDOWN_15M_MARKETS: Final[tuple[str, ...]] = POLYMARKET_UPDOWN_MARKETS
POLYMARKET_UPDOWN_1H_MARKETS: Final[tuple[str, ...]] = POLYMARKET_UPDOWN_MARKETS
POLYMARKET_UPDOWN_1D_MARKETS: Final[tuple[str, ...]] = POLYMARKET_UPDOWN_MARKETS

_OUTCOMES: Final[tuple[str, ...]] = ("Up", "Down")


def get_polymarket_symbols(markets: tuple[str, ...] = POLYMARKET_UPDOWN_MARKETS) -> List[str]:
    return [f"{m}-{o}" for m in markets for o in _OUTCOMES]

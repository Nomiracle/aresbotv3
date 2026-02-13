"""Trade records routes."""
from decimal import Decimal
from typing import Any, List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import get_current_user, get_db_session
from api.db.crud import TradeCRUD
from api.db.models import Trade

router = APIRouter()


class TradeResponse(BaseModel):
    id: int
    strategy_id: int
    order_id: str
    symbol: str
    exchange: str
    side: str
    price: Decimal
    quantity: Decimal
    amount: Decimal
    fee: Decimal
    pnl: Optional[Decimal]
    grid_index: Optional[int]
    related_order_id: Optional[str]
    raw_order_info: Optional[dict[str, Any]]
    created_at: str

    model_config = {"from_attributes": True}


class PaginatedTradeResponse(BaseModel):
    items: List[TradeResponse]
    total: int
    limit: int
    offset: int


class TradeStatsResponse(BaseModel):
    period_days: int
    total_trades: int
    total_pnl: Decimal
    total_volume: Decimal
    total_fees: Decimal
    win_count: int
    loss_count: int
    win_rate: float


def trade_to_response(trade: Trade) -> TradeResponse:
    exchange = ""
    if trade.strategy is not None and trade.strategy.account is not None:
        exchange = trade.strategy.account.exchange
    return TradeResponse(
        id=trade.id,
        strategy_id=trade.strategy_id,
        order_id=trade.order_id,
        symbol=trade.symbol,
        exchange=exchange,
        side=trade.side,
        price=trade.price,
        quantity=trade.quantity,
        amount=trade.amount,
        fee=trade.fee,
        pnl=trade.pnl,
        grid_index=trade.grid_index,
        related_order_id=trade.related_order_id,
        raw_order_info=trade.raw_order_info,
        created_at=trade.created_at.isoformat(),
    )


@router.get("", response_model=PaginatedTradeResponse)
async def list_trades(
    strategy_id: Optional[int] = Query(None),
    limit: int = Query(20, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    user_email: str = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
):
    trades = await TradeCRUD.get_by_user(
        session,
        user_email=user_email,
        limit=limit,
        offset=offset,
        strategy_id=strategy_id,
    )
    total = await TradeCRUD.count_by_user(
        session,
        user_email=user_email,
        strategy_id=strategy_id,
    )

    return PaginatedTradeResponse(
        items=[trade_to_response(t) for t in trades],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/stats", response_model=TradeStatsResponse)
async def get_trade_stats(
    days: int = Query(30, ge=1, le=365),
    strategy_id: Optional[int] = Query(None),
    user_email: str = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
):
    stats = await TradeCRUD.get_stats(
        session,
        user_email=user_email,
        days=days,
        strategy_id=strategy_id,
    )
    return TradeStatsResponse(**stats)

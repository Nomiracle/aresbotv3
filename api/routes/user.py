"""User API routes."""
from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import get_current_user, get_db_session
from api.db.models import ExchangeAccount, Strategy, Trade

router = APIRouter()


@router.get("")
async def get_user_info(
    session: AsyncSession = Depends(get_db_session),
    user_email: str = Depends(get_current_user),
):
    """获取当前用户信息和统计数据"""
    # 统计账户数
    accounts = await session.execute(
        select(func.count(ExchangeAccount.id)).where(
            ExchangeAccount.user_email == user_email
        )
    )
    account_count = accounts.scalar() or 0

    # 统计策略数
    strategies = await session.execute(
        select(func.count(Strategy.id)).where(Strategy.user_email == user_email)
    )
    strategy_count = strategies.scalar() or 0

    # 统计交易数和盈亏
    trades = await session.execute(
        select(func.count(Trade.id), func.sum(Trade.pnl))
        .join(Strategy)
        .where(Strategy.user_email == user_email)
    )
    row = trades.one()
    trade_count = row[0] or 0
    total_pnl = float(row[1]) if row[1] else 0

    return {
        "email": user_email,
        "stats": {
            "accounts": account_count,
            "strategies": strategy_count,
            "trades": trade_count,
            "total_pnl": total_pnl,
        },
    }

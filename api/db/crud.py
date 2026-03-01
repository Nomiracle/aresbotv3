"""CRUD operations for database models."""
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, Optional, Sequence

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload, selectinload

from .models import (
    ExchangeAccount,
    Strategy,
    StrategyRecordStatus,
    Trade,
    NotificationChannel,
)


class AccountCRUD:
    """CRUD operations for exchange accounts."""

    @staticmethod
    async def create(
        session: AsyncSession,
        user_email: str,
        exchange: str,
        label: str,
        api_key: str,
        api_secret: str,
        testnet: bool = False,
    ) -> ExchangeAccount:
        """Create a new exchange account."""
        account = ExchangeAccount(
            user_email=user_email,
            exchange=exchange,
            label=label,
            api_key=api_key,
            api_secret=api_secret,
            testnet=testnet,
        )
        session.add(account)
        await session.flush()
        await session.refresh(account)
        return account

    @staticmethod
    async def get_by_id(
        session: AsyncSession, account_id: int, user_email: str
    ) -> Optional[ExchangeAccount]:
        """Get account by ID for a specific user."""
        result = await session.execute(
            select(ExchangeAccount).where(
                ExchangeAccount.id == account_id,
                ExchangeAccount.user_email == user_email,
            )
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def get_all(
        session: AsyncSession, user_email: str
    ) -> Sequence[ExchangeAccount]:
        """Get all accounts for a user."""
        result = await session.execute(
            select(ExchangeAccount)
            .where(ExchangeAccount.user_email == user_email)
            .order_by(ExchangeAccount.created_at.desc())
        )
        return result.scalars().all()

    @staticmethod
    async def update(
        session: AsyncSession,
        account: ExchangeAccount,
        **kwargs,
    ) -> ExchangeAccount:
        """Update an account."""
        for key, value in kwargs.items():
            if hasattr(account, key) and value is not None:
                setattr(account, key, value)
        await session.flush()
        await session.refresh(account)
        return account

    @staticmethod
    async def delete(session: AsyncSession, account: ExchangeAccount) -> None:
        """Delete an account."""
        await session.delete(account)


class StrategyCRUD:
    """CRUD operations for strategies."""

    @staticmethod
    async def create(
        session: AsyncSession,
        user_email: str,
        account_id: int,
        name: str,
        symbol: str,
        base_order_size: Decimal,
        buy_price_deviation: Decimal,
        sell_price_deviation: Decimal,
        grid_levels: int = 3,
        polling_interval: Decimal = Decimal("1.0"),
        price_tolerance: Decimal = Decimal("0.5"),
        stop_loss: Optional[Decimal] = None,
        stop_loss_delay: Optional[int] = None,
        market_close_buffer: Optional[int] = None,
        max_open_positions: int = 10,
        max_daily_drawdown: Optional[Decimal] = None,
        worker_name: Optional[str] = None,
        strategy_type: str = "grid",
        min_buy_price: Optional[Decimal] = None,
    ) -> Strategy:
        """Create a new strategy."""
        strategy = Strategy(
            user_email=user_email,
            account_id=account_id,
            name=name,
            symbol=symbol,
            strategy_type=strategy_type,
            base_order_size=base_order_size,
            buy_price_deviation=buy_price_deviation,
            sell_price_deviation=sell_price_deviation,
            grid_levels=grid_levels,
            polling_interval=polling_interval,
            price_tolerance=price_tolerance,
            stop_loss=stop_loss,
            stop_loss_delay=stop_loss_delay,
            market_close_buffer=market_close_buffer,
            max_open_positions=max_open_positions,
            max_daily_drawdown=max_daily_drawdown,
            worker_name=worker_name,
            min_buy_price=min_buy_price,
        )
        session.add(strategy)
        await session.flush()
        await session.refresh(strategy)
        return strategy

    @staticmethod
    async def get_by_id(
        session: AsyncSession,
        strategy_id: int,
        user_email: str,
        include_deleted: bool = False,
    ) -> Optional[Strategy]:
        """Get strategy by ID for a specific user."""
        filters = [
            Strategy.id == strategy_id,
            Strategy.user_email == user_email,
        ]
        if not include_deleted:
            filters.append(Strategy.status == StrategyRecordStatus.ACTIVE)

        result = await session.execute(
            select(Strategy)
            .options(selectinload(Strategy.account))
            .where(*filters)
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def get_by_id_internal(
        session: AsyncSession, strategy_id: int
    ) -> Optional[Strategy]:
        """Get strategy by ID (internal use, no user filter)."""
        result = await session.execute(
            select(Strategy).where(Strategy.id == strategy_id)
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def get_all(
        session: AsyncSession,
        user_email: str,
        status_filter: str = StrategyRecordStatus.ACTIVE,
    ) -> Sequence[Strategy]:
        """Get all strategies for a user."""
        query = (
            select(Strategy)
            .options(selectinload(Strategy.account))
            .where(Strategy.user_email == user_email)
            .order_by(Strategy.id.desc())
        )

        if status_filter == "all":
            pass
        elif status_filter == StrategyRecordStatus.ACTIVE:
            query = query.where(Strategy.status == StrategyRecordStatus.ACTIVE)
        elif status_filter == StrategyRecordStatus.DELETED:
            query = query.where(Strategy.status == StrategyRecordStatus.DELETED)
        else:
            raise ValueError(f"Invalid strategy status filter: {status_filter}")

        result = await session.execute(query)
        return result.scalars().all()

    @staticmethod
    async def get_all_active(session: AsyncSession) -> Sequence[Strategy]:
        """Get all strategies (for engine restart)."""
        result = await session.execute(
            select(Strategy)
            .where(Strategy.status == StrategyRecordStatus.ACTIVE)
            .order_by(Strategy.id)
        )
        return result.scalars().all()

    @staticmethod
    async def update(
        session: AsyncSession,
        strategy: Strategy,
        **kwargs,
    ) -> Strategy:
        """Update a strategy."""
        for key, value in kwargs.items():
            if hasattr(strategy, key):
                setattr(strategy, key, value)
        strategy.updated_at = datetime.now()
        await session.flush()
        await session.refresh(strategy)
        return strategy

    @staticmethod
    async def delete(session: AsyncSession, strategy: Strategy) -> None:
        """Delete a strategy."""
        await session.delete(strategy)

    @staticmethod
    async def soft_delete(session: AsyncSession, strategy: Strategy) -> Strategy:
        """Soft delete a strategy by marking status."""
        strategy.status = StrategyRecordStatus.DELETED
        strategy.updated_at = datetime.now()
        await session.flush()
        await session.refresh(strategy)
        return strategy


class TradeCRUD:
    """CRUD operations for trades."""

    @staticmethod
    async def create(
        session: AsyncSession,
        strategy_id: int,
        order_id: str,
        symbol: str,
        side: str,
        price: Decimal,
        quantity: Decimal,
        amount: Decimal,
        fee: Decimal = Decimal("0"),
        pnl: Optional[Decimal] = None,
        grid_index: Optional[int] = None,
        related_order_id: Optional[str] = None,
        raw_order_info: Optional[dict[str, Any]] = None,
    ) -> Trade:
        """Create a new trade record."""
        trade = Trade(
            strategy_id=strategy_id,
            order_id=order_id,
            symbol=symbol,
            side=side,
            price=price,
            quantity=quantity,
            amount=amount,
            fee=fee,
            pnl=pnl,
            grid_index=grid_index,
            related_order_id=related_order_id,
            raw_order_info=raw_order_info,
        )
        session.add(trade)
        await session.flush()
        await session.refresh(trade)
        return trade

    @staticmethod
    async def get_by_strategy(
        session: AsyncSession,
        strategy_id: int,
        limit: int = 100,
        offset: int = 0,
    ) -> Sequence[Trade]:
        """Get trades for a strategy."""
        result = await session.execute(
            select(Trade)
            .where(Trade.strategy_id == strategy_id)
            .order_by(Trade.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        return result.scalars().all()

    @staticmethod
    async def get_by_user(
        session: AsyncSession,
        user_email: str,
        limit: int = 100,
        offset: int = 0,
        strategy_id: Optional[int] = None,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
    ) -> Sequence[Trade]:
        """Get trades for a user."""
        query = (
            select(Trade)
            .join(Strategy)
            .options(
                joinedload(Trade.strategy).joinedload(Strategy.account)
            )
            .where(Strategy.user_email == user_email)
            .order_by(Trade.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        if strategy_id is not None:
            query = query.where(Trade.strategy_id == strategy_id)
        if start_date is not None:
            query = query.where(Trade.created_at >= start_date)
        if end_date is not None:
            query = query.where(Trade.created_at <= end_date)
        result = await session.execute(query)
        return result.unique().scalars().all()

    @staticmethod
    async def count_by_user(
        session: AsyncSession,
        user_email: str,
        strategy_id: Optional[int] = None,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
    ) -> int:
        """Count trades for a user."""
        query = (
            select(func.count())
            .select_from(Trade)
            .join(Strategy)
            .where(Strategy.user_email == user_email)
        )
        if strategy_id is not None:
            query = query.where(Trade.strategy_id == strategy_id)
        if start_date is not None:
            query = query.where(Trade.created_at >= start_date)
        if end_date is not None:
            query = query.where(Trade.created_at <= end_date)
        result = await session.execute(query)
        return result.scalar() or 0

    @staticmethod
    async def get_stats(
        session: AsyncSession,
        user_email: str,
        days: Optional[int] = None,
        strategy_id: Optional[int] = None,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
    ) -> dict:
        """Get trade statistics for a user."""
        # Priority: explicit dates > days > default 30 days
        if start_date is not None or end_date is not None:
            since = start_date
            until = end_date
            period_days = None
        else:
            effective_days = days if days is not None else 30
            since = datetime.utcnow() - timedelta(days=effective_days)
            until = None
            period_days = effective_days

        # Build base query
        base_filter = [
            Strategy.user_email == user_email,
        ]
        if since is not None:
            base_filter.append(Trade.created_at >= since)
        if until is not None:
            base_filter.append(Trade.created_at <= until)
        if strategy_id is not None:
            base_filter.append(Trade.strategy_id == strategy_id)

        # Total trades
        total_result = await session.execute(
            select(func.count(Trade.id))
            .join(Strategy)
            .where(*base_filter)
        )
        total_trades = total_result.scalar() or 0

        # Total PnL
        pnl_result = await session.execute(
            select(func.sum(Trade.pnl))
            .join(Strategy)
            .where(*base_filter, Trade.pnl.is_not(None))
        )
        total_pnl = pnl_result.scalar() or Decimal("0")

        # Total volume
        volume_result = await session.execute(
            select(func.sum(Trade.amount))
            .join(Strategy)
            .where(*base_filter)
        )
        total_volume = volume_result.scalar() or Decimal("0")

        # Total fees
        fee_result = await session.execute(
            select(func.sum(Trade.fee))
            .join(Strategy)
            .where(*base_filter)
        )
        total_fees = fee_result.scalar() or Decimal("0")

        # Win/loss count
        win_result = await session.execute(
            select(func.count(Trade.id))
            .join(Strategy)
            .where(*base_filter, Trade.pnl > 0)
        )
        win_count = win_result.scalar() or 0

        loss_result = await session.execute(
            select(func.count(Trade.id))
            .join(Strategy)
            .where(*base_filter, Trade.pnl < 0)
        )
        loss_count = loss_result.scalar() or 0

        # New metrics
        pnl_trade_count = win_count + loss_count
        net_pnl = total_pnl - total_fees
        avg_pnl = total_pnl / pnl_trade_count if pnl_trade_count > 0 else Decimal("0")

        # Max win
        max_win_result = await session.execute(
            select(func.max(Trade.pnl))
            .join(Strategy)
            .where(*base_filter, Trade.pnl > 0)
        )
        max_win = max_win_result.scalar() or Decimal("0")

        # Max loss
        max_loss_result = await session.execute(
            select(func.min(Trade.pnl))
            .join(Strategy)
            .where(*base_filter, Trade.pnl < 0)
        )
        max_loss = max_loss_result.scalar() or Decimal("0")

        # Profit factor: avg(wins) / abs(avg(losses))
        avg_win_result = await session.execute(
            select(func.avg(Trade.pnl))
            .join(Strategy)
            .where(*base_filter, Trade.pnl > 0)
        )
        avg_win = avg_win_result.scalar() or Decimal("0")

        avg_loss_result = await session.execute(
            select(func.avg(Trade.pnl))
            .join(Strategy)
            .where(*base_filter, Trade.pnl < 0)
        )
        avg_loss = avg_loss_result.scalar() or Decimal("0")

        profit_factor = (
            float(avg_win / abs(avg_loss)) if avg_loss != 0 else 0.0
        )

        return {
            "period_days": period_days,
            "total_trades": total_trades,
            "total_pnl": total_pnl,
            "total_volume": total_volume,
            "total_fees": total_fees,
            "win_count": win_count,
            "loss_count": loss_count,
            "win_rate": (
                win_count / (win_count + loss_count)
                if (win_count + loss_count) > 0
                else 0
            ),
            "net_pnl": net_pnl,
            "avg_pnl": avg_pnl,
            "max_win": max_win,
            "max_loss": max_loss,
            "profit_factor": profit_factor,
        }


    @staticmethod
    async def get_pnl_summary(
        session: AsyncSession,
        strategy_id: int,
    ) -> dict:
        """获取策略全部历史交易的盈亏摘要"""
        total_result = await session.execute(
            select(func.count(Trade.id)).where(Trade.strategy_id == strategy_id)
        )
        total_trades = total_result.scalar() or 0

        fee_result = await session.execute(
            select(func.coalesce(func.sum(Trade.fee), 0)).where(Trade.strategy_id == strategy_id)
        )
        total_fees = float(fee_result.scalar() or 0)

        volume_result = await session.execute(
            select(func.coalesce(func.sum(Trade.amount), 0)).where(Trade.strategy_id == strategy_id)
        )
        total_volume = float(volume_result.scalar() or 0)

        pnl_result = await session.execute(
            select(func.coalesce(func.sum(Trade.pnl), 0)).where(
                Trade.strategy_id == strategy_id, Trade.pnl.is_not(None)
            )
        )
        total_pnl = float(pnl_result.scalar() or 0)

        win_result = await session.execute(
            select(func.count(Trade.id)).where(
                Trade.strategy_id == strategy_id, Trade.pnl > 0
            )
        )
        win_count = win_result.scalar() or 0

        loss_result = await session.execute(
            select(func.count(Trade.id)).where(
                Trade.strategy_id == strategy_id, Trade.pnl < 0
            )
        )
        loss_count = loss_result.scalar() or 0

        return {
            "total_trades": total_trades,
            "total_fees": total_fees,
            "total_volume": total_volume,
            "total_pnl": total_pnl,
            "win_count": win_count,
            "loss_count": loss_count,
        }


class NotificationCRUD:
    """CRUD operations for notification channels."""

    @staticmethod
    async def create(
        session: AsyncSession,
        user_email: str,
        channel_type: str,
        name: str,
        config: dict,
        enabled_events: list[str] | None = None,
    ) -> NotificationChannel:
        channel = NotificationChannel(
            user_email=user_email,
            channel_type=channel_type,
            name=name,
            config=config,
            enabled_events=enabled_events or [],
        )
        session.add(channel)
        await session.flush()
        await session.refresh(channel)
        return channel

    @staticmethod
    async def get_by_id(
        session: AsyncSession, channel_id: int, user_email: str,
    ) -> Optional[NotificationChannel]:
        result = await session.execute(
            select(NotificationChannel).where(
                NotificationChannel.id == channel_id,
                NotificationChannel.user_email == user_email,
            )
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def get_all(
        session: AsyncSession, user_email: str,
    ) -> Sequence[NotificationChannel]:
        result = await session.execute(
            select(NotificationChannel)
            .where(NotificationChannel.user_email == user_email)
            .order_by(NotificationChannel.created_at.desc())
        )
        return result.scalars().all()

    @staticmethod
    async def update(
        session: AsyncSession,
        channel: NotificationChannel,
        **kwargs,
    ) -> NotificationChannel:
        for key, value in kwargs.items():
            if hasattr(channel, key) and value is not None:
                setattr(channel, key, value)
        await session.flush()
        await session.refresh(channel)
        return channel

    @staticmethod
    async def delete(
        session: AsyncSession, channel: NotificationChannel,
    ) -> None:
        await session.delete(channel)

"""Database models using SQLModel."""
from datetime import datetime
from decimal import Decimal
from typing import Optional, List

from sqlmodel import Field, SQLModel, Relationship


class ExchangeAccount(SQLModel, table=True):
    """Exchange account configuration."""

    __tablename__ = "exchange_account"

    id: Optional[int] = Field(default=None, primary_key=True)
    user_email: str = Field(max_length=255, index=True)
    exchange: str = Field(max_length=50)
    label: str = Field(max_length=100)
    api_key: str = Field(max_length=255)
    api_secret: str = Field(max_length=255)
    testnet: bool = Field(default=False)
    is_active: bool = Field(default=True)
    created_at: datetime = Field(default_factory=datetime.now)

    strategies: List["Strategy"] = Relationship(back_populates="account")


class Strategy(SQLModel, table=True):
    """Strategy configuration."""

    __tablename__ = "strategy"

    id: Optional[int] = Field(default=None, primary_key=True)
    user_email: str = Field(max_length=255, index=True)
    account_id: int = Field(foreign_key="exchange_account.id", index=True)
    name: str = Field(max_length=100)
    symbol: str = Field(max_length=20)

    # Order settings
    base_order_size: Decimal = Field(max_digits=20, decimal_places=8)
    buy_price_deviation: Decimal = Field(max_digits=10, decimal_places=4)
    sell_price_deviation: Decimal = Field(max_digits=10, decimal_places=4)
    grid_levels: int = Field(default=3)

    # Engine settings
    polling_interval: Decimal = Field(default=1.0, max_digits=10, decimal_places=2)
    price_tolerance: Decimal = Field(default=0.5, max_digits=10, decimal_places=4)

    # Risk settings
    stop_loss: Optional[Decimal] = Field(default=None, max_digits=10, decimal_places=4)
    stop_loss_delay: Optional[int] = Field(default=None)
    max_open_positions: int = Field(default=10)
    max_daily_drawdown: Optional[Decimal] = Field(
        default=None, max_digits=20, decimal_places=8
    )

    # Worker settings
    worker_name: Optional[str] = Field(default=None, max_length=100)

    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)

    account: Optional[ExchangeAccount] = Relationship(back_populates="strategies")
    trades: List["Trade"] = Relationship(back_populates="strategy")


class Trade(SQLModel, table=True):
    """Trade record."""

    __tablename__ = "trade"

    id: Optional[int] = Field(default=None, primary_key=True)
    strategy_id: int = Field(foreign_key="strategy.id", index=True)
    order_id: str = Field(max_length=64)
    symbol: str = Field(max_length=20)
    side: str = Field(max_length=10)
    price: Decimal = Field(max_digits=20, decimal_places=8)
    quantity: Decimal = Field(max_digits=20, decimal_places=8)
    amount: Decimal = Field(max_digits=20, decimal_places=8)
    fee: Decimal = Field(default=0, max_digits=20, decimal_places=8)
    pnl: Optional[Decimal] = Field(default=None, max_digits=20, decimal_places=8)
    grid_index: Optional[int] = Field(default=None)
    related_order_id: Optional[str] = Field(default=None, max_length=64)
    created_at: datetime = Field(default_factory=datetime.utcnow, index=True)

    strategy: Optional[Strategy] = Relationship(back_populates="trades")

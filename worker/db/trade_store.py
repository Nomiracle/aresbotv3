"""同步 MySQL 交易记录存储"""
from dataclasses import dataclass
from datetime import datetime, timedelta
import json
from typing import Any, Optional
from urllib.parse import quote_plus
import os

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker


def _parse_raw_order_info(raw_value: Any) -> Optional[dict[str, Any]]:
    if raw_value is None:
        return None
    if isinstance(raw_value, dict):
        return raw_value
    if isinstance(raw_value, str):
        try:
            parsed = json.loads(raw_value)
        except json.JSONDecodeError:
            return None
        if isinstance(parsed, dict):
            return parsed
    if isinstance(raw_value, (bytes, bytearray)):
        try:
            decoded = raw_value.decode("utf-8")
            parsed = json.loads(decoded)
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None
        if isinstance(parsed, dict):
            return parsed
    return None


def build_sync_database_url() -> str:
    """构建同步数据库 URL"""
    database_url = os.environ.get("DATABASE_URL", "")
    if database_url:
        # 转换异步 URL 为同步
        return database_url.replace("+aiomysql", "+pymysql")

    host = os.environ.get("DB_HOST", "localhost")
    port = os.environ.get("DB_PORT", "3306")
    user = os.environ.get("DB_USER", "aresbot")
    password = os.environ.get("DB_PASSWORD", "")
    database = os.environ.get("DB_NAME", "aresbot")

    encoded_password = quote_plus(password) if password else ""
    return f"mysql+pymysql://{user}:{encoded_password}@{host}:{port}/{database}"


@dataclass
class TradeRecord:
    """交易记录（兼容旧接口）"""
    id: Optional[int]
    symbol: str
    side: str
    price: float
    quantity: float
    fee: float
    pnl: Optional[float]
    order_id: str
    grid_index: int
    created_at: datetime
    related_order_id: Optional[str] = None
    raw_order_info: Optional[dict[str, Any]] = None


class TradeStore:
    """同步 MySQL 交易记录存储"""

    _engine = None
    _session_maker = None
    _raw_order_info_column_checked = False

    def __init__(self, strategy_id: int):
        self.strategy_id = strategy_id
        self._ensure_engine()

    @classmethod
    def _ensure_engine(cls) -> None:
        """确保引擎已初始化（单例）"""
        if cls._engine is None:
            url = build_sync_database_url()
            cls._engine = create_engine(url, pool_pre_ping=True)
            cls._session_maker = sessionmaker(bind=cls._engine)

        if not cls._raw_order_info_column_checked:
            cls._ensure_trade_raw_order_info_column()
            cls._raw_order_info_column_checked = True

    @classmethod
    def _ensure_trade_raw_order_info_column(cls) -> None:
        """确保 trade 表包含 raw_order_info 列。"""
        if cls._engine.dialect.name != "mysql":
            return

        with cls._engine.begin() as connection:
            column = connection.execute(
                text("SHOW COLUMNS FROM trade LIKE 'raw_order_info'")
            ).fetchone()
            if column is None:
                connection.execute(
                    text("ALTER TABLE trade ADD COLUMN raw_order_info JSON NULL")
                )

    def save_trade(self, trade: TradeRecord) -> int:
        """保存成交记录"""
        raw_order_info_json: Optional[str] = None
        if trade.raw_order_info is not None:
            raw_order_info_json = json.dumps(
                trade.raw_order_info,
                ensure_ascii=False,
                default=str,
            )

        with self._session_maker() as session:
            result = session.execute(
                text("""
                    INSERT INTO trade
                    (strategy_id, order_id, symbol, side, price, quantity, amount, fee, pnl, grid_index, related_order_id, raw_order_info, created_at)
                    VALUES (:strategy_id, :order_id, :symbol, :side, :price, :quantity, :amount, :fee, :pnl, :grid_index, :related_order_id, :raw_order_info, :created_at)
                """),
                {
                    "strategy_id": self.strategy_id,
                    "order_id": trade.order_id,
                    "symbol": trade.symbol,
                    "side": trade.side.upper(),
                    "price": trade.price,
                    "quantity": trade.quantity,
                    "amount": trade.price * trade.quantity,
                    "fee": trade.fee,
                    "pnl": trade.pnl,
                    "grid_index": trade.grid_index,
                    "related_order_id": trade.related_order_id,
                    "raw_order_info": raw_order_info_json,
                    "created_at": trade.created_at,
                }
            )
            session.commit()
            return result.lastrowid

    def get_buy_trade(self, order_id: str) -> Optional[TradeRecord]:
        """根据订单ID获取买入记录"""
        with self._session_maker() as session:
            result = session.execute(
                text("""
                    SELECT id, symbol, side, price, quantity, fee, pnl, order_id, grid_index, related_order_id, raw_order_info, created_at
                    FROM trade
                    WHERE strategy_id = :strategy_id AND order_id = :order_id AND side = 'BUY'
                    LIMIT 1
                """),
                {"strategy_id": self.strategy_id, "order_id": order_id}
            )
            row = result.fetchone()
            if row:
                return TradeRecord(
                    id=row[0], symbol=row[1], side=row[2], price=float(row[3]),
                    quantity=float(row[4]), fee=float(row[5]), pnl=float(row[6]) if row[6] else None,
                    order_id=row[7], grid_index=row[8], related_order_id=row[9],
                    raw_order_info=_parse_raw_order_info(row[10]), created_at=row[11]
                )
            return None

    def get_recent_pnl(self, symbol: str, hours: int = 24) -> float:
        """获取最近N小时盈亏"""
        with self._session_maker() as session:
            since = datetime.now() - timedelta(hours=hours)
            result = session.execute(
                text("""
                    SELECT COALESCE(SUM(pnl), 0) as total_pnl
                    FROM trade
                    WHERE strategy_id = :strategy_id AND symbol = :symbol AND side = 'SELL'
                    AND created_at > :since
                """),
                {"strategy_id": self.strategy_id, "symbol": symbol, "since": since}
            )
            row = result.fetchone()
            return float(row[0]) if row else 0.0

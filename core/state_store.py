from dataclasses import dataclass
from typing import List, Optional
from datetime import datetime
import sqlite3
import threading
import logging

logger = logging.getLogger(__name__)


@dataclass
class TradeRecord:
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


class StateStore:
    """状态持久化 - 只在成交时入库"""

    def __init__(self, db_path: str = "trades.db"):
        self.db_path = db_path
        self._lock = threading.Lock()
        self._init_db()

    def _init_db(self) -> None:
        with self._get_conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS trades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL,
                    side TEXT NOT NULL,
                    price REAL NOT NULL,
                    quantity REAL NOT NULL,
                    fee REAL NOT NULL,
                    pnl REAL,
                    order_id TEXT NOT NULL,
                    grid_index INTEGER NOT NULL,
                    related_order_id TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_trades_symbol ON trades(symbol)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_trades_order_id ON trades(order_id)
            """)

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def save_trade(self, trade: TradeRecord) -> int:
        """保存成交记录"""
        with self._lock:
            with self._get_conn() as conn:
                cursor = conn.execute(
                    """
                    INSERT INTO trades (symbol, side, price, quantity, fee, pnl,
                                       order_id, grid_index, related_order_id, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        trade.symbol,
                        trade.side,
                        trade.price,
                        trade.quantity,
                        trade.fee,
                        trade.pnl,
                        trade.order_id,
                        trade.grid_index,
                        trade.related_order_id,
                        trade.created_at,
                    ),
                )
                return cursor.lastrowid

    def get_buy_trade(self, order_id: str) -> Optional[TradeRecord]:
        """根据订单ID获取买入记录（用于计算卖出盈亏）"""
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM trades WHERE order_id = ? AND side = 'buy'",
                (order_id,),
            ).fetchone()
            if row:
                return self._row_to_trade(row)
        return None

    def get_trades_by_symbol(
        self, symbol: str, limit: int = 100
    ) -> List[TradeRecord]:
        """获取指定交易对的成交记录"""
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM trades WHERE symbol = ? ORDER BY created_at DESC LIMIT ?",
                (symbol, limit),
            ).fetchall()
            return [self._row_to_trade(row) for row in rows]

    def get_recent_pnl(self, symbol: str, hours: int = 24) -> float:
        """获取最近N小时的盈亏"""
        with self._get_conn() as conn:
            row = conn.execute(
                """
                SELECT COALESCE(SUM(pnl), 0) as total_pnl
                FROM trades
                WHERE symbol = ? AND side = 'sell'
                AND created_at > datetime('now', ?)
                """,
                (symbol, f"-{hours} hours"),
            ).fetchone()
            return row["total_pnl"] if row else 0

    def _row_to_trade(self, row: sqlite3.Row) -> TradeRecord:
        return TradeRecord(
            id=row["id"],
            symbol=row["symbol"],
            side=row["side"],
            price=row["price"],
            quantity=row["quantity"],
            fee=row["fee"],
            pnl=row["pnl"],
            order_id=row["order_id"],
            grid_index=row["grid_index"],
            related_order_id=row["related_order_id"],
            created_at=datetime.fromisoformat(row["created_at"]),
        )

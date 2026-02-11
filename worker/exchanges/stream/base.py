"""数据流管理抽象基类"""

from abc import ABC, abstractmethod
from typing import Dict, List, Optional

from worker.core.base_exchange import ExchangeOrder


class StreamManager(ABC):
    """数据流管理器抽象基类

    职责:
    - 定义数据流的统一读取接口
    - 所有方法都是同步的
    - 返回 None 表示无数据，由调用方决定是否走 REST
    """

    @abstractmethod
    def start(self, symbol: str) -> None:
        """订阅交易对

        Args:
            symbol: 交易对 (如 "BTC/USDT")
        """

    @abstractmethod
    def stop(self, symbol: str) -> None:
        """取消订阅交易对

        Args:
            symbol: 交易对
        """

    @abstractmethod
    def get_price(self, symbol: str) -> Optional[float]:
        """获取缓存价格

        Args:
            symbol: 交易对

        Returns:
            有效价格或 None（过期/不存在）
        """

    @abstractmethod
    def get_order(self, order_id: str) -> Optional[ExchangeOrder]:
        """获取缓存订单

        Args:
            order_id: 订单 ID

        Returns:
            缓存的订单或 None
        """

    @abstractmethod
    def get_open_orders(self, symbol: str) -> List[ExchangeOrder]:
        """获取未完成订单

        Args:
            symbol: 交易对

        Returns:
            未完成订单列表（可能为空）
        """

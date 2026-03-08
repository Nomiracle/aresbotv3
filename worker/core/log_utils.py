"""统一日志前缀工具"""

import logging
from typing import Callable


def make_log_prefix(symbol: str, api_key: str, exchange_id: str) -> str:
    """生成统一日志前缀 [SOL/USDC] [4y2xCN7r] [backpack]"""
    if "/" not in symbol:
        upper = symbol.upper()
        if upper.endswith("USDT") and len(upper) > 4:
            symbol = f"{upper[:-4]}/USDT"
        else:
            symbol = upper
    api_key_prefix = (api_key or "")[:8]
    return f"[{symbol}] [{api_key_prefix}] [{exchange_id}]"


class PrefixAdapter(logging.LoggerAdapter):
    """通用前缀日志适配器，支持动态前缀"""

    def __init__(self, logger, extra):
        super().__init__(logger, extra)
        # 如果 prefix 是可调用对象，则每次都动态获取
        self._prefix_callable = None
        if callable(extra.get('prefix')):
            self._prefix_callable = extra['prefix']

    def process(self, msg, kwargs):
        if self._prefix_callable:
            prefix = self._prefix_callable()
        else:
            prefix = self.extra['prefix']
        return f"{prefix} {msg}", kwargs

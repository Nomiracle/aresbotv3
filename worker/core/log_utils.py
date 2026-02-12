"""统一日志前缀工具"""

import logging


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
    """通用前缀日志适配器"""

    def process(self, msg, kwargs):
        return f"{self.extra['prefix']} {msg}", kwargs

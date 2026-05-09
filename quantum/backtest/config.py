"""
回测配置 - 独立文件解决循环依赖
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Optional


@dataclass
class BacktestEngineConfig:
    """回测引擎配置"""
    initial_capital: float = 1_000_000.0
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None
    commission_rate: float = 0.0003       # 佣金费率(万3)
    min_commission: float = 5.0           # 最低佣金
    tax_rate: float = 0.001               # 印花税(千1)
    slippage: float = 0.001               # 滑点(千1)
    slippage_mode: str = "fixed"          # fixed / percent / none
    allow_short: bool = False              # A股不允许做空
    lot_size: int = 100                    # 整手
    price_precision: int = 2               # 价格精度
    margin_rate: float = 1.0               # 保证金比例
    benchmark_symbol: str = "000300"       # 基准(沪深300)

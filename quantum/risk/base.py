"""
风控抽象基类
"""

from abc import ABC, abstractmethod
from datetime import datetime
from typing import List, Optional

from ..common.models import Signal, Portfolio, Position
from ..common.enums import SignalAction


class RiskManager(ABC):
    """风控管理器基类"""

    def __init__(self, name: str = "risk_manager"):
        self.name = name

    @abstractmethod
    def before_trade(
        self,
        signals: List[Signal],
        portfolio: Portfolio,
        current_time: datetime,
    ) -> List[Signal]:
        """交易前风控检查（过滤/修改信号）"""
        pass

    def after_trade(
        self,
        signals: List[Signal],
        portfolio: Portfolio,
        current_time: datetime,
    ) -> None:
        """交易后风控处理"""
        pass

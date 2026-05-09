"""
策略基类：定义策略框架和生命周期
"""

from abc import ABC, abstractmethod
from datetime import datetime
from typing import List, Optional, Dict, Any, Callable
from ..common.models import Bar, Signal, Tick, StrategyConfig, Position, Portfolio
from ..common.enums import SignalAction, StrategyCategory
from ..common.exceptions import StrategyError


class BaseStrategy(ABC):
    """
    策略抽象基类
    所有策略需实现 on_bar / on_tick 方法

    生命周期:
        __init__ → setup → on_bar/on_tick (循环) → teardown
    """

    def __init__(self, config: StrategyConfig):
        self.config = config
        self.name = config.name
        self.category = config.category
        self.symbols = config.symbols
        self.parameters = config.parameters
        self.timeframe = config.timeframe

        # 运行时状态
        self.signals: List[Signal] = []
        self.current_bars: Dict[str, Bar] = {}
        self.bar_history: Dict[str, List[Bar]] = {}
        self.position: Optional[Position] = None
        self.portfolio: Optional[Portfolio] = None
        self._signal_callbacks: List[Callable] = []

    @abstractmethod
    def setup(self) -> None:
        """策略初始化（在回测/实盘开始时调用一次）"""
        pass

    @abstractmethod
    def on_bar(self, bar: Bar) -> Optional[Signal]:
        """每个K线到达时触发"""
        pass

    def on_tick(self, tick: Tick) -> Optional[Signal]:
        """每个Tick到达时触发（可选覆盖）"""
        return None

    def on_order_filled(self, signal: Signal, fill_price: float, quantity: int) -> None:
        """订单成交回调（可选覆盖）"""
        pass

    def on_position_update(self, position: Position) -> None:
        """持仓更新回调（可选覆盖）"""
        self.position = position

    def on_portfolio_update(self, portfolio: Portfolio) -> None:
        """资产组合更新回调（可选覆盖）"""
        self.portfolio = portfolio

    def teardown(self) -> None:
        """策略清理"""
        pass

    def register_signal_callback(self, callback: Callable):
        """注册信号回调"""
        self._signal_callbacks.append(callback)

    def _emit_signal(self, signal: Signal) -> None:
        """触发信号"""
        self.signals.append(signal)
        for callback in self._signal_callbacks:
            callback(signal)

    def log(self, message: str, level: str = "INFO"):
        """策略日志（简化版）"""
        from loguru import logger
        getattr(logger, level.lower(), logger.info)(
            f"[{self.name}] {message}"
        )


class SignalGenerator(ABC):
    """
    信号生成器基类
    用于计算技术指标并生成原始信号
    """

    def __init__(self, name: str, parameters: Dict[str, Any] = None):
        self.name = name
        self.parameters = parameters or {}

    @abstractmethod
    def compute(self, bars: List[Bar]) -> List[Signal]:
        """根据K线数据计算信号"""
        pass

    def get_param(self, key: str, default=None):
        return self.parameters.get(key, default)


class PortfolioBuilder(ABC):
    """
    投资组合构建器：将信号转化为仓位分配
    """

    @abstractmethod
    def allocate(
        self,
        signals: List[Signal],
        portfolio: Portfolio,
        bars: Dict[str, Bar],
    ) -> List[Signal]:
        """将信号组合成最终的交易指令"""
        pass

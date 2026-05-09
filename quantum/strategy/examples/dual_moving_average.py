"""
双均线趋势跟踪策略 - 完整策略示例
"""

from typing import List, Optional, Dict, Any
import numpy as np

from ..base import BaseStrategy
from ..signals.trend import TrendSignalGenerator
from ...common.models import Bar, Signal, StrategyConfig
from ...common.enums import SignalAction, TimeFrame, StrategyCategory
from ...common.utils import IndicatorUtils


class DualMovingAverageStrategy(BaseStrategy):
    """
    双均线趋势跟踪策略
    - 快线(MA10)上穿慢线(MA30) → 买入
    - 快线下穿慢线 → 卖出
    - ATR动态止损
    """

    def __init__(self, config: Optional[StrategyConfig] = None):
        if config is None:
            config = StrategyConfig(
                name="DualMA_Trend",
                category=StrategyCategory.TREND_FOLLOWING,
                symbols=[],
                parameters={
                    "fast_period": 10,
                    "slow_period": 30,
                    "atr_period": 14,
                    "atr_stop_mult": 3.0,
                    "volume_confirmation": True,
                },
                timeframe=TimeFrame.DAILY,
            )
        super().__init__(config)
        self.signal_gen = TrendSignalGenerator({
            "method": "dual_ma",
            "fast_period": config.parameters.get("fast_period", 10),
            "slow_period": config.parameters.get("slow_period", 30),
            "volume_confirmation": config.parameters.get("volume_confirmation", True),
        })
        self.atr_period = config.parameters.get("atr_period", 14)
        self.atr_stop_mult = config.parameters.get("atr_stop_mult", 3.0)
        self._entry_price: Dict[str, float] = {}
        self._stop_price: Dict[str, float] = {}

    def setup(self):
        self.log(f"双均线策略初始化: MA{self.signal_gen.fast_period}/MA{self.signal_gen.slow_period}")
        self.log(f"ATR止损: {self.atr_stop_mult}倍ATR")

    def on_bar(self, bar: Bar) -> Optional[Signal]:
        symbol = bar.symbol
        if symbol not in self.bar_history:
            self.bar_history[symbol] = []
        self.bar_history[symbol].append(bar)

        bars = self.bar_history[symbol]
        if len(bars) < self.signal_gen.slow_period + 5:
            return None

        # 1. 检查ATR止损
        stop_signal = self._check_stop_loss(bar)
        if stop_signal:
            return stop_signal

        # 2. 生成趋势信号
        signals = self.signal_gen.compute(bars)

        # 3. 记录入场价和止损价
        for sig in signals:
            if sig.action == SignalAction.OPEN_LONG:
                self._entry_price[symbol] = bar.close
                # 设置ATR止损
                closes = np.array([b.close for b in bars])
                highs = np.array([b.high for b in bars])
                lows = np.array([b.low for b in bars])
                atr = IndicatorUtils.atr(highs, lows, closes, self.atr_period)
                if not np.isnan(atr[-1]):
                    self._stop_price[symbol] = bar.close - self.atr_stop_mult * atr[-1]
            elif sig.action in [SignalAction.CLOSE_LONG, SignalAction.EXIT]:
                self._entry_price.pop(symbol, None)
                self._stop_price.pop(symbol, None)

        return signals[-1] if signals else None

    def _check_stop_loss(self, bar: Bar) -> Optional[Signal]:
        """检查是否触发ATR止损"""
        symbol = bar.symbol
        stop_price = self._stop_price.get(symbol)
        if stop_price is None:
            return None
        if bar.close <= stop_price:
            self.log(f"ATR止损触发: {symbol} {bar.close:.2f} < {stop_price:.2f}")
            self._entry_price.pop(symbol, None)
            self._stop_price.pop(symbol, None)
            return Signal(
                timestamp=bar.time,
                symbol=symbol,
                action=SignalAction.CLOSE_LONG,
                price=bar.close,
                confidence=1.0,
                strategy_name=self.name,
                reason=f"ATR止损: {bar.close:.2f} < {stop_price:.2f}",
            )
        return None

    def teardown(self):
        self.log("双均线策略停止")


class TurtleStrategy(BaseStrategy):
    """海龟交易法则策略"""

    def __init__(self, config: Optional[StrategyConfig] = None):
        if config is None:
            config = StrategyConfig(
                name="Turtle_Trader",
                category=StrategyCategory.TREND_FOLLOWING,
                symbols=[],
                parameters={
                    "entry_period": 20,
                    "exit_period": 10,
                    "atr_period": 14,
                    "atr_stop_mult": 2.0,
                },
                timeframe=TimeFrame.DAILY,
            )
        super().__init__(config)
        self.signal_gen = TrendSignalGenerator({
            "method": "turtle",
            "entry_period": config.parameters.get("entry_period", 20),
            "exit_period": config.parameters.get("exit_period", 10),
            "slow_period": config.parameters.get("entry_period", 20),
            "atr_period": config.parameters.get("atr_period", 14),
            "atr_multiplier": config.parameters.get("atr_stop_mult", 2.0),
        })

    def setup(self):
        self.log("海龟交易策略初始化")

    def on_bar(self, bar: Bar) -> Optional[Signal]:
        symbol = bar.symbol
        if symbol not in self.bar_history:
            self.bar_history[symbol] = []
        self.bar_history[symbol].append(bar)

        bars = self.bar_history[symbol]
        if len(bars) < 30:
            return None

        signals = self.signal_gen.compute(bars)
        return signals[-1] if signals else None

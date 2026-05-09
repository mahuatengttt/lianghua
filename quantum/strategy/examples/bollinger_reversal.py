"""
布林带均值回归策略
"""

from typing import Optional
import numpy as np

from ..base import BaseStrategy
from ..signals.mean_reversion import MeanReversionSignalGenerator
from ...common.models import Bar, Signal, StrategyConfig
from ...common.enums import SignalAction, TimeFrame, StrategyCategory


class BollingerReversalStrategy(BaseStrategy):
    """
    布林带反转策略
    - 价格触及下轨+成交量确认 → 做多
    - 价格触及上轨 → 平仓
    - 回归中轨 → 平仓
    """

    def __init__(self, config: Optional[StrategyConfig] = None):
        if config is None:
            config = StrategyConfig(
                name="Bollinger_Reversal",
                category=StrategyCategory.MEAN_REVERSION,
                symbols=[],
                parameters={
                    "period": 20,
                    "std_dev": 2.0,
                    "filter_volume": True,
                    "stop_loss_pct": 0.05,
                    "take_profit_pct": 0.08,
                },
                timeframe=TimeFrame.DAILY,
            )
        super().__init__(config)
        self.signal_gen = MeanReversionSignalGenerator({
            "method": "bollinger",
            "period": config.parameters.get("period", 20),
            "std_dev": config.parameters.get("std_dev", 2.0),
            "filter_volume": config.parameters.get("filter_volume", True),
        })
        self.stop_loss_pct = config.parameters.get("stop_loss_pct", 0.05)
        self.take_profit_pct = config.parameters.get("take_profit_pct", 0.08)
        self._entry_price: dict = {}

    def setup(self):
        self.log(f"布林带反转策略初始化: {self.signal_gen.period}/{self.signal_gen.std_dev}σ")
        self.log(f"止损: {self.stop_loss_pct:.0%}, 止盈: {self.take_profit_pct:.0%}")

    def on_bar(self, bar: Bar) -> Optional[Signal]:
        symbol = bar.symbol
        if symbol not in self.bar_history:
            self.bar_history[symbol] = []
        self.bar_history[symbol].append(bar)

        bars = self.bar_history[symbol]
        if len(bars) < 25:
            return None

        # 1. 检查浮动止损/止盈
        pnl_signal = self._check_targets(bar)
        if pnl_signal:
            return pnl_signal

        # 2. 生成信号
        signals = self.signal_gen.compute(bars)

        # 3. 记录入场价
        for sig in signals:
            if sig.action == SignalAction.OPEN_LONG:
                self._entry_price[symbol] = bar.close
            elif sig.action in [SignalAction.CLOSE_LONG, SignalAction.EXIT]:
                self._entry_price.pop(symbol, None)

        return signals[-1] if signals else None

    def _check_targets(self, bar: Bar) -> Optional[Signal]:
        """检查止盈止损"""
        symbol = bar.symbol
        if symbol not in self._entry_price:
            return None

        entry = self._entry_price[symbol]
        pnl_pct = (bar.close - entry) / entry

        if pnl_pct <= -self.stop_loss_pct:
            self.log(f"止损触发: {symbol} {pnl_pct:.2%}")
            self._entry_price.pop(symbol, None)
            return Signal(
                timestamp=bar.time, symbol=symbol,
                action=SignalAction.CLOSE_LONG, price=bar.close,
                confidence=1.0, strategy_name=self.name,
                reason=f"止损: {pnl_pct:.2%}",
            )
        elif pnl_pct >= self.take_profit_pct:
            self.log(f"止盈触发: {symbol} {pnl_pct:.2%}")
            self._entry_price.pop(symbol, None)
            return Signal(
                timestamp=bar.time, symbol=symbol,
                action=SignalAction.CLOSE_LONG, price=bar.close,
                confidence=1.0, strategy_name=self.name,
                reason=f"止盈: {pnl_pct:.2%}",
            )
        return None

    def teardown(self):
        self.log("布林带反转策略停止")

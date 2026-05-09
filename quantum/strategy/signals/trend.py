"""
趋势跟踪信号生成器
"""

from datetime import datetime
from typing import List, Optional, Dict, Any
import numpy as np

from ..base import SignalGenerator
from ...common.models import Bar, Signal
from ...common.enums import SignalAction, StrategyCategory
from ...common.utils import IndicatorUtils


class TrendSignalGenerator(SignalGenerator):
    """
    趋势跟踪信号生成器
    支持多种趋势识别方法
    """

    def __init__(self, parameters: Dict[str, Any] = None):
        super().__init__("trend", parameters or {})
        self.method = self.get_param("method", "dual_ma")  # dual_ma / macd / breakout
        self.fast_period = self.get_param("fast_period", 10)
        self.slow_period = self.get_param("slow_period", 30)
        self.atr_period = self.get_param("atr_period", 14)
        self.atr_multiplier = self.get_param("atr_multiplier", 2.0)
        self.volume_confirmation = self.get_param("volume_confirmation", False)

    def compute(self, bars: List[Bar]) -> List[Signal]:
        if len(bars) < self.slow_period + 10:
            return []

        closes = np.array([b.close for b in bars])
        highs = np.array([b.high for b in bars])
        lows = np.array([b.low for b in bars])
        volumes = np.array([b.volume for b in bars])
        symbol = bars[0].symbol

        signals = []

        if self.method == "dual_ma":
            signals = self._dual_ma(symbol, bars, closes, volumes)
        elif self.method == "macd":
            signals = self._macd_trend(symbol, bars, closes)
        elif self.method == "breakout":
            signals = self._breakout(symbol, bars, highs, lows, closes)
        elif self.method == "turtle":
            signals = self._turtle(symbol, bars, highs, lows, closes, volumes)

        return signals

    def _dual_ma(self, symbol: str, bars: List[Bar],
                 closes: np.ndarray, volumes: np.ndarray) -> List[Signal]:
        """双均线策略"""
        fast_ma = IndicatorUtils.sma(closes, self.fast_period)
        slow_ma = IndicatorUtils.sma(closes, self.slow_period)

        if np.isnan(fast_ma[-1]) or np.isnan(slow_ma[-1]):
            return []

        signals = []
        bar = bars[-1]

        # 金叉做多
        if (fast_ma[-2] <= slow_ma[-2] and fast_ma[-1] > slow_ma[-1]):
            # 成交量确认：使用当前窗口的成交量
            if self.volume_confirmation:
                avg_vol = np.mean(volumes[-(self.fast_period+5):-1]) if len(volumes) > self.fast_period + 5 else np.mean(volumes[:self.fast_period])
                if volumes[-1] < avg_vol * 0.8:
                    return []

            signals.append(Signal(
                timestamp=bar.time,
                symbol=symbol,
                action=SignalAction.OPEN_LONG,
                price=bar.close,
                confidence=min(1.0, (fast_ma[-1] - slow_ma[-1]) / slow_ma[-1] * 10),
                strategy_name="DualMA_Trend",
                reason=f"金叉: MA{self.fast_period}={fast_ma[-1]:.2f} > MA{self.slow_period}={slow_ma[-1]:.2f}",
                metadata={"fast_ma": float(fast_ma[-1]), "slow_ma": float(slow_ma[-1])},
            ))

        # 死叉平多
        elif (fast_ma[-2] >= slow_ma[-2] and fast_ma[-1] < slow_ma[-1]):
            signals.append(Signal(
                timestamp=bar.time,
                symbol=symbol,
                action=SignalAction.CLOSE_LONG,
                price=bar.close,
                confidence=0.8,
                strategy_name="DualMA_Trend",
                reason=f"死叉: MA{self.fast_period}={fast_ma[-1]:.2f} < MA{self.slow_period}={slow_ma[-1]:.2f}",
                metadata={"fast_ma": float(fast_ma[-1]), "slow_ma": float(slow_ma[-1])},
            ))

        return signals

    def _macd_trend(self, symbol: str, bars: List[Bar],
                    closes: np.ndarray) -> List[Signal]:
        """MACD趋势跟踪"""
        dif, dea, hist = IndicatorUtils.macd(closes)

        if np.isnan(hist[-1]) or np.isnan(hist[-2]):
            return []

        bar = bars[-1]
        signals = []

        # MACD金叉做多
        if hist[-2] <= 0 and hist[-1] > 0:
            signals.append(Signal(
                timestamp=bar.time,
                symbol=symbol,
                action=SignalAction.OPEN_LONG,
                price=bar.close,
                confidence=min(0.9, abs(hist[-1]) * 100),
                strategy_name="MACD_Trend",
                reason=f"MACD金叉: hist={hist[-1]:.4f}",
                metadata={"dif": float(dif[-1]), "dea": float(dea[-1]), "hist": float(hist[-1])},
            ))

        # MACD死叉平多
        elif hist[-2] >= 0 and hist[-1] < 0:
            signals.append(Signal(
                timestamp=bar.time,
                symbol=symbol,
                action=SignalAction.CLOSE_LONG,
                price=bar.close,
                confidence=0.8,
                strategy_name="MACD_Trend",
                reason=f"MACD死叉: hist={hist[-1]:.4f}",
                metadata={"dif": float(dif[-1]), "dea": float(dea[-1]), "hist": float(hist[-1])},
            ))

        return signals

    def _breakout(self, symbol: str, bars: List[Bar],
                  highs: np.ndarray, lows: np.ndarray,
                  closes: np.ndarray) -> List[Signal]:
        """通道突破策略"""
        rolling_high = np.maximum.accumulate(highs[-self.slow_period:])
        rolling_low = np.minimum.accumulate(lows[-self.slow_period:])

        bar = bars[-1]
        signals = []

        # 突破上轨做多
        if bar.close >= rolling_high[-1] and bar.close > closes[-2]:
            signals.append(Signal(
                timestamp=bar.time,
                symbol=symbol,
                action=SignalAction.OPEN_LONG,
                price=bar.close,
                confidence=0.7,
                strategy_name="Breakout",
                reason=f"突破上轨: {bar.close:.2f} > {rolling_high[-1]:.2f}",
            ))

        # 跌破下轨平多
        elif bar.close <= rolling_low[-1] and bar.close < closes[-2]:
            signals.append(Signal(
                timestamp=bar.time,
                symbol=symbol,
                action=SignalAction.CLOSE_LONG,
                price=bar.close,
                confidence=0.7,
                strategy_name="Breakout",
                reason=f"跌破下轨: {bar.close:.2f} < {rolling_low[-1]:.2f}",
            ))

        return signals

    def _turtle(self, symbol: str, bars: List[Bar],
                highs: np.ndarray, lows: np.ndarray,
                closes: np.ndarray, volumes: np.ndarray) -> List[Signal]:
        """海龟交易法则"""
        entry_period = self.get_param("entry_period", 20)
        exit_period = self.get_param("exit_period", 10)

        if len(highs) < entry_period:
            return []

        entry_high = np.max(highs[-entry_period:])
        entry_low = np.min(lows[-entry_period:])
        exit_low = np.min(lows[-exit_period:])

        atr = IndicatorUtils.atr(highs, lows, closes, self.atr_period)
        if np.isnan(atr[-1]):
            return []

        bar = bars[-1]
        signals = []

        # 突破入场
        if bar.close >= entry_high and volumes[-1] > np.mean(volumes[-10:]):
            signals.append(Signal(
                timestamp=bar.time,
                symbol=symbol,
                action=SignalAction.OPEN_LONG,
                price=bar.close,
                confidence=0.75,
                strategy_name="Turtle",
                reason=f"唐奇安突破: {bar.close:.2f} > {entry_high:.2f}",
                metadata={"atr": float(atr[-1]), "entry_high": float(entry_high)},
            ))

        # 跌破出场
        elif bar.close <= exit_low:
            signals.append(Signal(
                timestamp=bar.time,
                symbol=symbol,
                action=SignalAction.CLOSE_LONG,
                price=bar.close,
                confidence=0.75,
                strategy_name="Turtle",
                reason=f"出场: {bar.close:.2f} < {exit_low:.2f}",
            ))

        return signals

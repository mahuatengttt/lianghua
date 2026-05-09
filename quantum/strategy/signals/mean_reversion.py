"""
均值回归信号生成器
"""

from datetime import datetime
from typing import List, Optional, Dict, Any
import numpy as np

from ..base import SignalGenerator
from ...common.models import Bar, Signal
from ...common.enums import SignalAction
from ...common.utils import IndicatorUtils


class MeanReversionSignalGenerator(SignalGenerator):
    """
    均值回归信号生成器
    策略类型：布林带反转、RSI极端值、Z-Score回归
    """

    def __init__(self, parameters: Dict[str, Any] = None):
        super().__init__("mean_reversion", parameters or {})
        self.method = self.get_param("method", "bollinger")  # bollinger / rsi / zscore
        self.period = self.get_param("period", 20)
        self.std_dev = self.get_param("std_dev", 2.0)
        self.rsi_period = self.get_param("rsi_period", 14)
        self.rsi_overbought = self.get_param("rsi_overbought", 80)
        self.rsi_oversold = self.get_param("rsi_oversold", 20)
        self.zscore_threshold = self.get_param("zscore_threshold", 2.0)
        self.stop_loss_pct = self.get_param("stop_loss_pct", 0.05)
        self.take_profit_pct = self.get_param("take_profit_pct", 0.10)
        self.filter_volume = self.get_param("filter_volume", True)

    def compute(self, bars: List[Bar]) -> List[Signal]:
        if len(bars) < self.period + 5:
            return []

        closes = np.array([b.close for b in bars])
        highs = np.array([b.high for b in bars])
        lows = np.array([b.low for b in bars])
        volumes = np.array([b.volume for b in bars])
        symbol = bars[0].symbol

        if self.method == "bollinger":
            return self._bollinger_reversal(symbol, bars, closes, volumes)
        elif self.method == "rsi":
            return self._rsi_extreme(symbol, bars, closes)
        elif self.method == "zscore":
            return self._zscore_reversal(symbol, bars, closes)
        return []

    def _bollinger_reversal(
        self, symbol: str, bars: List[Bar],
        closes: np.ndarray, volumes: np.ndarray,
    ) -> List[Signal]:
        """布林带反转策略"""
        upper, middle, lower = IndicatorUtils.bollinger(
            closes, self.period, self.std_dev
        )

        if np.isnan(upper[-1]) or np.isnan(lower[-1]):
            return []

        bar = bars[-1]
        signals = []

        # 成交量过滤
        vol_ok = True
        if self.filter_volume and len(volumes) > 20:
            avg_vol = np.mean(volumes[-20:])
            vol_ok = volumes[-1] > avg_vol * 0.5

        # 触碰上轨 → 做空
        if bar.close >= upper[-1] and vol_ok:
            # 检查是否有明显的趋势（避免逆势做空）
            if bar.close < closes[-2] or abs(bar.close - upper[-1]) / upper[-1] < 0.001:
                signals.append(Signal(
                    timestamp=bar.time,
                    symbol=symbol,
                    action=SignalAction.CLOSE_LONG if self._has_long_position(symbol, bars) else SignalAction.HOLD,
                    price=bar.close,
                    confidence=np.clip(1.0 - (bar.close - middle[-1]) / (upper[-1] - middle[-1]) * 0.5, 0, 1),
                    strategy_name="Bollinger_Reversal",
                    reason=f"触及布林上轨: {bar.close:.2f} ≥ {upper[-1]:.2f}",
                    metadata={
                        "upper": float(upper[-1]),
                        "middle": float(middle[-1]),
                        "lower": float(lower[-1]),
                        "bandwidth": float((upper[-1] - lower[-1]) / middle[-1]),
                    },
                ))

        # 触碰下轨 → 做多
        elif bar.close <= lower[-1] and vol_ok:
            if bar.close > closes[-2] or abs(bar.close - lower[-1]) / lower[-1] < 0.001:
                signals.append(Signal(
                    timestamp=bar.time,
                    symbol=symbol,
                    action=SignalAction.OPEN_LONG,
                    price=bar.close,
                    confidence=np.clip(0.5 + (middle[-1] - bar.close) / (middle[-1] - lower[-1]) * 0.5, 0, 1),
                    strategy_name="Bollinger_Reversal",
                    reason=f"触及布林下轨: {bar.close:.2f} ≤ {lower[-1]:.2f}",
                    metadata={
                        "upper": float(upper[-1]),
                        "middle": float(middle[-1]),
                        "lower": float(lower[-1]),
                        "bandwidth": float((upper[-1] - lower[-1]) / middle[-1]),
                    },
                ))

        # 回归中轨 → 平仓
        elif abs(bar.close - middle[-1]) / middle[-1] < 0.01:
            signals.append(Signal(
                timestamp=bar.time,
                symbol=symbol,
                action=SignalAction.EXIT,
                price=bar.close,
                confidence=0.9,
                strategy_name="Bollinger_Reversal",
                reason="回归中轨，平仓", ))

        return signals

    def _rsi_extreme(self, symbol: str, bars: List[Bar],
                     closes: np.ndarray) -> List[Signal]:
        """RSI极端值策略"""
        rsi_values = IndicatorUtils.rsi(closes, self.rsi_period)

        if np.isnan(rsi_values[-1]):
            return []

        bar = bars[-1]
        signals = []

        # RSI超买(<20) → 买入
        if rsi_values[-1] < self.rsi_oversold:
            signals.append(Signal(
                timestamp=bar.time,
                symbol=symbol,
                action=SignalAction.OPEN_LONG,
                price=bar.close,
                confidence=min(1.0, (self.rsi_oversold - rsi_values[-1]) / self.rsi_oversold),
                strategy_name="RSI_Reversal",
                reason=f"RSI超卖: {rsi_values[-1]:.1f} < {self.rsi_oversold}",
                metadata={"rsi": float(rsi_values[-1])},
            ))

        # RSI超买(>80) → 卖出
        elif rsi_values[-1] > self.rsi_overbought:
            signals.append(Signal(
                timestamp=bar.time,
                symbol=symbol,
                action=SignalAction.CLOSE_LONG,
                price=bar.close,
                confidence=min(1.0, (rsi_values[-1] - self.rsi_overbought) / (100 - self.rsi_overbought)),
                strategy_name="RSI_Reversal",
                reason=f"RSI超买: {rsi_values[-1]:.1f} > {self.rsi_overbought}",
                metadata={"rsi": float(rsi_values[-1])},
            ))

        # RSI回归中值(50) → 极端开仓单平仓
        if 45 < rsi_values[-1] < 55:
            if self._has_long_position(symbol, bars):
                signals.append(Signal(
                    timestamp=bar.time,
                    symbol=symbol,
                    action=SignalAction.EXIT,
                    price=bar.close,
                    confidence=0.6,
                    strategy_name="RSI_Reversal",
                    reason=f"RSI回归中性: {rsi_values[-1]:.1f}",
                ))

        return signals

    def _zscore_reversal(self, symbol: str, bars: List[Bar],
                         closes: np.ndarray) -> List[Signal]:
        """Z-Score回归策略"""
        zscores = IndicatorUtils.zscore(closes, self.period)

        if np.isnan(zscores[-1]):
            return []

        bar = bars[-1]
        signals = []

        # Z > threshold 卖出
        if zscores[-1] > self.zscore_threshold:
            signals.append(Signal(
                timestamp=bar.time,
                symbol=symbol,
                action=SignalAction.CLOSE_LONG,
                price=bar.close,
                confidence=min(1.0, (zscores[-1] - 1) / self.zscore_threshold),
                strategy_name="ZScore_Reversal",
                reason=f"Z-Score偏高: {zscores[-1]:.2f} > {self.zscore_threshold}",
                metadata={"zscore": float(zscores[-1])},
            ))

        # Z < -threshold 买入
        elif zscores[-1] < -self.zscore_threshold:
            signals.append(Signal(
                timestamp=bar.time,
                symbol=symbol,
                action=SignalAction.OPEN_LONG,
                price=bar.close,
                confidence=min(1.0, abs(zscores[-1] + 1) / self.zscore_threshold),
                strategy_name="ZScore_Reversal",
                reason=f"Z-Score偏低: {zscores[-1]:.2f} < {-self.zscore_threshold}",
                metadata={"zscore": float(zscores[-1])},
            ))

        return signals

    def _has_long_position(self, symbol: str, bars: List[Bar]) -> bool:
        """检查当前是否存在多头仓位（简化版，实际持仓由回测引擎管理）"""
        return False  # 信号生成器不维护仓位状态

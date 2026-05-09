"""
统计套利信号生成器 - 配对交易
"""

from typing import List, Dict, Any, Tuple
import numpy as np
from scipy import stats

from ..base import SignalGenerator
from ...common.models import Bar, Signal
from ...common.enums import SignalAction
from ...common.exceptions import SignalError


class PairsTradingSignalGenerator(SignalGenerator):
    """
    配对交易信号生成器
    原理：两只有高度相关性的股票，价差偏离均值时开仓，回归时平仓
    """

    def __init__(self, parameters: Dict[str, Any] = None):
        super().__init__("pairs_trading", parameters or {})
        self.pair: tuple = tuple(self.get_param("pair", []))
        self.zscore_entry = self.get_param("zscore_entry", 2.0)
        self.zscore_exit = self.get_param("zscore_exit", 0.5)
        self.window = self.get_param("window", 60)
        self.cointegration_pvalue = self.get_param("cointegration_pvalue", 0.05)
        self.lot_size = self.get_param("lot_size", 100)

    def compute(self, bars_dict: Dict[str, List[Bar]]) -> List[Signal]:
        """
        计算配对交易信号
        Args:
            bars_dict: {"stock_a": [Bar, ...], "stock_b": [Bar, ...]}
        """
        if len(self.pair) != 2:
            raise SignalError("配对交易需要两个标的")

        stock_a, stock_b = self.pair
        bars_a = bars_dict.get(stock_a, [])
        bars_b = bars_dict.get(stock_b, [])

        if len(bars_a) < self.window or len(bars_b) < self.window:
            return []

        # 对齐时间轴
        prices_a, prices_b = self._align_prices(bars_a, bars_b)
        if len(prices_a) < self.window:
            return []

        # 计算价差
        spread = self._compute_spread(prices_a, prices_b)
        if spread is None:
            return []

        zscores = self._compute_zscore(spread)
        if zscores is None or len(zscores) < 2:
            return []

        current_z = zscores[-1]
        prev_z = zscores[-2]

        signals = []
        now = bars_a[-1].time

        # 价差偏离过大 → 开仓
        if prev_z < self.zscore_entry <= current_z:
            signals.append(Signal(
                timestamp=now,
                symbol=f"{stock_a}-{stock_b}",
                action=SignalAction.OPEN_LONG,
                price=bars_a[-1].close,
                confidence=min(1.0, (current_z - self.zscore_entry) / self.zscore_entry),
                strategy_name="PairsTrading",
                reason=f"配对价差向上突破: z={current_z:.2f}",
                metadata={
                    "zscore": float(current_z),
                    "stock_a": stock_a,
                    "stock_b": stock_b,
                    "price_a": float(bars_a[-1].close),
                    "price_b": float(bars_b[-1].close),
                    "hedge_ratio": float(self._compute_hedge_ratio(prices_a, prices_b)),
                },
            ))

        elif prev_z > -self.zscore_entry >= current_z:
            signals.append(Signal(
                timestamp=now,
                symbol=f"{stock_a}-{stock_b}",
                action=SignalAction.OPEN_SHORT,
                price=bars_a[-1].close,
                confidence=min(1.0, abs(current_z + self.zscore_entry) / self.zscore_entry),
                strategy_name="PairsTrading",
                reason=f"配对价差向下突破: z={current_z:.2f}",
                metadata={
                    "zscore": float(current_z),
                    "stock_a": stock_a,
                    "stock_b": stock_b,
                    "price_a": float(bars_a[-1].close),
                    "price_b": float(bars_b[-1].close),
                    "hedge_ratio": float(self._compute_hedge_ratio(prices_a, prices_b)),
                },
            ))

        # 价差回归 → 平仓
        elif abs(current_z) < self.zscore_exit:
            signals.append(Signal(
                timestamp=now,
                symbol=f"{stock_a}-{stock_b}",
                action=SignalAction.EXIT,
                price=bars_a[-1].close,
                confidence=0.9,
                strategy_name="PairsTrading",
                reason=f"配对价差回归: z={current_z:.2f}",
                metadata={"zscore": float(current_z)},
            ))

        return signals

    def _align_prices(self, bars_a: List[Bar], bars_b: List[Bar]) -> Tuple[np.ndarray, np.ndarray]:
        """对齐两个标的价格时间序列"""
        prices_a = {}
        prices_b = {}

        for b in bars_a:
            prices_a[b.time] = b.close
        for b in bars_b:
            prices_b[b.time] = b.close

        common_times = sorted(set(prices_a.keys()) & set(prices_b.keys()))

        if len(common_times) < self.window:
            return np.array([]), np.array([])

        return (
            np.array([prices_a[t] for t in common_times]),
            np.array([prices_b[t] for t in common_times]),
        )

    def _compute_spread(self, prices_a: np.ndarray, prices_b: np.ndarray) -> np.ndarray:
        """计算价差 = log(price_a) - hedge_ratio * log(price_b)"""
        log_a = np.log(prices_a)
        log_b = np.log(prices_b)

        hedge_ratio = self._compute_hedge_ratio(prices_a, prices_b)
        if hedge_ratio is None:
            return None

        return log_a - hedge_ratio * log_b

    def _compute_hedge_ratio(self, prices_a: np.ndarray, prices_b: np.ndarray) -> float:
        """计算对冲比例（通过OLS回归）"""
        try:
            log_a = np.log(prices_a)
            log_b = np.log(prices_b)

            slope, intercept, r_value, p_value, std_err = stats.linregress(log_b, log_a)

            # 检查协整性
            if p_value > self.cointegration_pvalue:
                return None

            return float(slope)
        except Exception:
            return None

    def _compute_zscore(self, spread: np.ndarray) -> np.ndarray:
        """计算价差的Z-Score"""
        if len(spread) < self.window:
            return None

        mean = np.mean(spread[-self.window:])
        std = np.std(spread[-self.window:])

        if std == 0:
            return None

        return (spread[-self.window:] - mean) / std


class ETFArbitrageSignalGenerator(SignalGenerator):
    """ETF套利信号生成器"""

    def __init__(self, parameters: Dict[str, Any] = None):
        super().__init__("etf_arbitrage", parameters or {})
        self.threshold = self.get_param("threshold", 0.005)  # 0.5% 溢价阈值

    def compute(self, bars_dict: Dict[str, List[Bar]]) -> List[Signal]:
        # ETF套利需要实时净值(IOPV)数据，简化版
        return []

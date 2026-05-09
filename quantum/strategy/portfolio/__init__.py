"""
投资组合构建器
"""

from typing import List, Dict, Any
import numpy as np

from ..base import PortfolioBuilder
from ...common.models import Signal, Portfolio, Bar
from ...common.enums import SignalAction


class MeanVarianceOptimizer(PortfolioBuilder):
    """均值-方差优化组合"""

    def __init__(self, target_return: float = 0.15, risk_free_rate: float = 0.025):
        self.target_return = target_return
        self.risk_free_rate = risk_free_rate

    def allocate(
        self,
        signals: List[Signal],
        portfolio: Portfolio,
        bars: Dict[str, Bar],
    ) -> List[Signal]:
        """分配权重（简化版：等权重分配）"""
        if not signals:
            return []

        # 简单策略：等权重分配可用资金
        available_cash = portfolio.available_cash
        per_signal_cash = available_cash / max(len(signals), 1)

        for signal in signals:
            current_price = bars.get(signal.symbol, Bar).close if isinstance(bars.get(signal.symbol), Bar) else signal.price
            signal.quantity = int(per_signal_cash / current_price / 100) * 100

        return signals


class RiskParityPortfolio(PortfolioBuilder):
    """风险平价组合"""

    def __init__(self, lookback: int = 60):
        self.lookback = lookback
        self._volatilities: Dict[str, float] = {}

    def allocate(
        self,
        signals: List[Signal],
        portfolio: Portfolio,
        bars: Dict[str, Bar],
    ) -> List[Signal]:
        """风险平价分配：波动率越大的品种分配越少资金"""
        if not signals:
            return []

        total_risk = sum(1.0 / max(v, 0.001) for v in self._volatilities.values())
        available_cash = portfolio.available_cash

        for signal in signals:
            vol = self._volatilities.get(signal.symbol, 0.2)
            risk_weight = (1.0 / max(vol, 0.001)) / total_risk if total_risk > 0 else 1.0 / len(signals)
            cash = available_cash * risk_weight
            signal.quantity = int(cash / signal.price / 100) * 100

        return signals


class SimpleAllocator(PortfolioBuilder):
    """简单资金分配器"""

    def __init__(self, capital_pct: float = 1.0):
        """
        Args:
            capital_pct: 每次信号使用资金的百分比
        """
        self.capital_pct = capital_pct

    def allocate(
        self,
        signals: List[Signal],
        portfolio: Portfolio,
        bars: Dict[str, Bar],
    ) -> List[Signal]:
        available_cash = portfolio.available_cash * self.capital_pct

        for signal in signals:
            if signal.action in [SignalAction.OPEN_LONG, SignalAction.OPEN_SHORT]:
                price = bars[signal.symbol].close if signal.symbol in bars else signal.price
                signal.quantity = int(available_cash / price / 100) * 100

        return signals

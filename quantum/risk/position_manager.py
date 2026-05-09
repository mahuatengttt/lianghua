"""
仓位管理器
"""

from datetime import datetime
from typing import List, Dict, Optional
from .base import RiskManager
from quantum.common.models import Signal, Portfolio, Position
from quantum.common.enums import SignalAction
from quantum.common.exceptions import PositionLimitError


class PositionSizer(RiskManager):
    """
    仓位管理器：控制单品种/总仓位上限
    """

    def __init__(
        self,
        max_single_position_pct: float = 0.2,    # 单品种仓位上限(总资产%)
        max_total_positions: int = 10,             # 最大持仓品种数
        max_leverage: float = 1.0,                 # 最大杠杆(无杠杆=1.0)
        min_cash_reserve: float = 0.1,             # 最低现金储备(总资产%)
    ):
        super().__init__("position_sizer")
        self.max_single_pct = max_single_position_pct
        self.max_positions = max_total_positions
        self.max_leverage = max_leverage
        self.min_cash_pct = min_cash_reserve

    def before_trade(
        self, signals: List[Signal], portfolio: Portfolio, current_time: datetime,
    ) -> List[Signal]:
        filtered = []
        total_capital = portfolio.total_capital

        for sig in signals:
            if sig.action not in [SignalAction.OPEN_LONG, SignalAction.OPEN_SHORT]:
                filtered.append(sig)
                continue

            # 1. 检查持仓品种数上限
            if len(portfolio.positions) >= self.max_positions:
                if sig.symbol not in portfolio.positions:
                    continue  # 超过最大持仓数

            # 2. 计算本次交易占用资金比例
            cost = sig.price * max(sig.quantity, 100)
            position_pct = cost / total_capital if total_capital > 0 else 1

            # 加上已有仓位
            existing = portfolio.positions.get(sig.symbol)
            if existing and existing.quantity > 0:
                total_pct = position_pct + (existing.market_value / total_capital)
            else:
                total_pct = position_pct

            if total_pct > self.max_single_pct:
                # 缩量到上限
                max_cost = total_capital * self.max_single_pct
                sig.quantity = int(max_cost / sig.price / 100) * 100
                if sig.quantity <= 0:
                    continue

            # 3. 检查杠杆
            total_exposure = portfolio.market_value + cost
            leverage = total_exposure / total_capital if total_capital > 0 else 1
            if leverage > self.max_leverage:
                continue

            # 4. 检查最低现金储备
            cash_after = portfolio.available_cash - cost
            min_cash = total_capital * self.min_cash_pct
            if cash_after < min_cash:
                continue

            filtered.append(sig)

        return filtered

    def after_trade(self, signals: List[Signal], portfolio: Portfolio, current_time: datetime):
        pass


class RiskBudgetManager(RiskManager):
    """
    风险预算管理器：基于VaR/波动率的动态仓位调整
    """

    def __init__(self, target_volatility: float = 0.15, max_var_95: float = 0.02):
        super().__init__("risk_budget")
        self.target_vol = target_volatility
        self.max_var = max_var_95

    def before_trade(
        self, signals: List[Signal], portfolio: Portfolio, current_time: datetime,
    ) -> List[Signal]:
        """根据波动率调整仓位"""
        if not portfolio.positions:
            return signals

        from quantum.common.utils import MathUtils

        # 计算当前组合波动率（简化：取过去20日收益率的波动率）
        current_exposure = portfolio.market_value
        total_capital = portfolio.total_capital

        if total_capital == 0:
            return signals

        current_leverage = current_exposure / total_capital
        target_leverage = self.target_vol / 0.20  # 假设市场年化波动率20%

        if current_leverage > target_leverage:
            for sig in signals:
                if sig.action == SignalAction.OPEN_LONG:
                    sig.quantity = int(sig.quantity * target_leverage / max(current_leverage, 0.01) / 100) * 100

        return signals

    def after_trade(self, signals, portfolio, current_time):
        pass


class PositionCleaner(RiskManager):
    """隔日无持仓时清理空仓位"""

    def before_trade(self, signals, portfolio, current_time):
        return signals

    def after_trade(self, signals, portfolio, current_time):
        pass

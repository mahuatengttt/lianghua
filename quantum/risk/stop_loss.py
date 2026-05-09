"""
止损管理器和熔断机制
"""

from datetime import datetime, timedelta
from typing import List, Dict, Optional
import numpy as np

from .base import RiskManager
from quantum.common.models import Signal, Portfolio, Position
from quantum.common.enums import SignalAction


class TrailingStopLoss(RiskManager):
    """
    移动止损管理器
    策略：价格从高点回落N%时平仓
    """

    def __init__(self, trail_pct: float = 0.05, min_holding_period: int = 1):
        super().__init__("trailing_stop")
        self.trail_pct = trail_pct
        self.min_holding = min_holding_period
        self._highest_prices: Dict[str, float] = {}
        self._entry_times: Dict[str, datetime] = {}

    def before_trade(
        self, signals: List[Signal], portfolio: Portfolio, current_time: datetime,
    ) -> List[Signal]:
        additional_signals = []

        for symbol, pos in portfolio.positions.items():
            if pos.quantity <= 0:
                continue

            current_price = pos.market_value / pos.quantity if pos.quantity > 0 else 0
            if current_price <= 0:
                continue

            # 更新最高价
            if symbol not in self._highest_prices:
                self._highest_prices[symbol] = current_price
            self._highest_prices[symbol] = max(self._highest_prices[symbol], current_price)

            # 记录入场时间
            if symbol not in self._entry_times:
                self._entry_times[symbol] = current_time

            # 检查是否满足最小持仓期
            holding_days = (current_time - self._entry_times[symbol]).days
            if holding_days < self.min_holding:
                continue

            # 移动止损检查
            high_price = self._highest_prices[symbol]
            drawdown = (high_price - current_price) / high_price

            if drawdown >= self.trail_pct:
                additional_signals.append(Signal(
                    timestamp=current_time,
                    symbol=symbol,
                    action=SignalAction.CLOSE_LONG,
                    price=current_price,
                    confidence=1.0,
                    strategy_name="TrailingStop",
                    reason=f"移动止损触发: 回落{drawdown:.2%} >= {self.trail_pct:.2%}",
                ))
                self._highest_prices.pop(symbol, None)
                self._entry_times.pop(symbol, None)

        return signals + additional_signals

    def after_trade(self, signals, portfolio, current_time):
        pass


class TimeBasedStop(RiskManager):
    """基于持仓时间的强制平仓"""

    def __init__(self, max_holding_days: int = 60):
        super().__init__("time_stop")
        self.max_days = max_holding_days
        self._entry_times: Dict[str, datetime] = {}

    def before_trade(
        self, signals: List[Signal], portfolio: Portfolio, current_time: datetime,
    ) -> List[Signal]:
        additional = []

        for symbol, pos in portfolio.positions.items():
            if pos.quantity <= 0:
                continue
            if symbol not in self._entry_times:
                continue

            days = (current_time - self._entry_times[symbol]).days
            if days >= self.max_days:
                additional.append(Signal(
                    timestamp=current_time,
                    symbol=symbol,
                    action=SignalAction.CLOSE_LONG,
                    price=pos.market_value / pos.quantity,
                    confidence=1.0,
                    strategy_name="TimeStop",
                    reason=f"持仓超时: {days}天 > {self.max_days}天",
                ))
                self._entry_times.pop(symbol, None)

        for sig in signals:
            if sig.action == SignalAction.OPEN_LONG and sig.symbol not in self._entry_times:
                self._entry_times[sig.symbol] = current_time

        return signals + additional

    def after_trade(self, signals, portfolio, current_time):
        pass


class CircuitBreaker(RiskManager):
    """
    熔断机制
    当日内亏损/组合回撤超过阈值时暂停交易
    """

    def __init__(
        self,
        daily_loss_limit: float = 0.05,      # 日亏损上限
        drawdown_limit: float = 0.15,         # 最大回撤上限
        cooldown_bars: int = 5,               # 熔断后等待K线数
    ):
        super().__init__("circuit_breaker")
        self.daily_loss_limit = daily_loss_limit
        self.drawdown_limit = drawdown_limit
        self.cooldown = cooldown_bars
        self._triggered = False
        self._cooldown_counter = 0
        self._peak_capital: float = 0
        self._daily_start_capital: float = 0
        self._current_date: Optional[datetime] = None

    def before_trade(
        self, signals: List[Signal], portfolio: Portfolio, current_time: datetime,
    ) -> List[Signal]:
        # 检查日期变更
        if self._current_date is None or current_time.date() != self._current_date.date():
            self._current_date = current_time
            self._daily_start_capital = portfolio.total_capital
            self._triggered = False
            self._cooldown_counter = 0

        # 更新峰值
        self._peak_capital = max(self._peak_capital, portfolio.total_capital)

        # 计算回撤
        drawdown = (self._peak_capital - portfolio.total_capital) / self._peak_capital if self._peak_capital > 0 else 0

        # 计算日内亏损
        if self._daily_start_capital > 0:
            daily_pnl_pct = (portfolio.total_capital - self._daily_start_capital) / self._daily_start_capital
        else:
            daily_pnl_pct = 0

        # 熔断检查
        if self._triggered:
            self._cooldown_counter -= 1
            if self._cooldown_counter <= 0:
                self._triggered = False
            return [s for s in signals if s.action in [SignalAction.CLOSE_LONG, SignalAction.EXIT]]

        # 触发熔断
        if daily_pnl_pct <= -self.daily_loss_limit:
            self._triggered = True
            self._cooldown_counter = self.cooldown
            # 只允许平仓信号
            return [s for s in signals if s.action in [SignalAction.CLOSE_LONG, SignalAction.EXIT]]

        if drawdown >= self.drawdown_limit:
            self._triggered = True
            self._cooldown_counter = self.cooldown * 2
            return [s for s in signals if s.action in [SignalAction.CLOSE_LONG, SignalAction.EXIT]]

        return signals

    def after_trade(self, signals, portfolio, current_time):
        pass

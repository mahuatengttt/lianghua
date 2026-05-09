"""
网格均线策略 - 下跌分仓买入，反弹分批卖出
适合震荡下跌市，避免追涨杀跌
"""

from typing import List, Optional, Dict, Any
import numpy as np
from ...common.models import Bar, Signal, StrategyConfig
from ...common.enums import SignalAction, TimeFrame, StrategyCategory
from ..base import BaseStrategy


class GridMAStrategy(BaseStrategy):
    """
    网格均线策略
    - 只在 MA60 向上时做多（过滤下跌趋势）
    - 下跌时分档建仓（网格买入）
    - 反弹到均线以上分批止盈
    """

    def __init__(self, config: Optional[StrategyConfig] = None):
        if config is None:
            config = StrategyConfig(
                name="GridMA",
                category=StrategyCategory.TREND_FOLLOWING,
                symbols=[],
                parameters={
                    "ma_period": 60,       # 趋势过滤均线
                    "grid_levels": 3,       # 网格层数
                    "grid_spacing": 0.05,   # 每层间隔5%
                    "take_profit": 0.08,    # 止盈8%
                    "max_position_pct": 0.8,# 最大仓位80%
                },
                timeframe=TimeFrame.DAILY,
            )
        super().__init__(config)

        self.ma_period = config.parameters.get("ma_period", 60)
        self.grid_levels = config.parameters.get("grid_levels", 3)
        self.grid_spacing = config.parameters.get("grid_spacing", 0.05)
        self.take_profit = config.parameters.get("take_profit", 0.08)
        self.max_position_pct = config.parameters.get("max_position_pct", 0.8)

        # 运行时状态
        self._entry_prices: Dict[str, List[float]] = {}  # 各网格的买入价
        self._grid_capital: Dict[str, float] = {}  # 每层分配资金
        self._prev_ma: Dict[str, float] = {}

    def setup(self):
        self.log(f"网格均线策略初始化: MA{self.ma_period}, {self.grid_levels}层网格")
        self.log(f"网格间距: {self.grid_spacing*100:.0f}%, 止盈: {self.take_profit*100:.0f}%")

    def on_bar(self, bar: Bar) -> Optional[Signal]:
        symbol = bar.symbol
        if symbol not in self.bar_history:
            self.bar_history[symbol] = []
            self._entry_prices[symbol] = []
        self.bar_history[symbol].append(bar)

        bars = self.bar_history[symbol]
        if len(bars) < self.ma_period + 5:
            return None

        closes = np.array([b.close for b in bars])
        ma = np.mean(closes[-self.ma_period:])
        prev_ma = np.mean(closes[-self.ma_period-1:-1])
        trend_up = ma > prev_ma

        signals = []
        current_positions = self._entry_prices.get(symbol, [])
        current_price = bar.close

        # 每层的资金分配
        level_capital = (self.max_position_pct * 1000000) / self.grid_levels

        # === 买入逻辑 ===
        if len(current_positions) < self.grid_levels:
            # 基准价：最近一次买入价，如果没有则用当前MA
            ref_price = current_positions[-1] if current_positions else ma

            # 如果价格比上一次买入价低 grid_spacing，加仓
            if current_price <= ref_price * (1 - self.grid_spacing):
                qty = int(level_capital / current_price / 100) * 100
                if qty >= 100:
                    signals.append(Signal(
                        timestamp=bar.time, symbol=symbol,
                        action=SignalAction.OPEN_LONG,
                        price=current_price, quantity=qty,
                        confidence=0.7,
                        strategy_name=self.name,
                        reason=f"网格加仓({len(current_positions)+1}/{self.grid_levels}): ¥{current_price:.2f}",
                    ))

            # 如果没开过仓且价格低于MA，首次建仓
            if not current_positions and current_price < ma * 0.95:
                qty = int(level_capital / current_price / 100) * 100
                if qty >= 100:
                    signals.append(Signal(
                        timestamp=bar.time, symbol=symbol,
                        action=SignalAction.OPEN_LONG,
                        price=current_price, quantity=qty,
                        confidence=0.6,
                        strategy_name=self.name,
                        reason=f"首次建仓(低于MA5%): ¥{current_price:.2f}",
                    ))

        # === 卖出逻辑 ===
        if current_positions:
            avg_cost = np.mean(current_positions)
            profit_pct = (current_price - avg_cost) / avg_cost

            # 止盈
            if profit_pct >= self.take_profit:
                signals.append(Signal(
                    timestamp=bar.time, symbol=symbol,
                    action=SignalAction.CLOSE_LONG,
                    price=current_price,
                    confidence=0.8,
                    strategy_name=self.name,
                    reason=f"止盈: 成本¥{avg_cost:.2f}, +{profit_pct*100:.1f}%",
                ))
                return signals[-1]

            # 趋势转空时止损
            if not trend_up and profit_pct > -0.10:
                signals.append(Signal(
                    timestamp=bar.time, symbol=symbol,
                    action=SignalAction.CLOSE_LONG,
                    price=current_price,
                    confidence=0.6,
                    strategy_name=self.name,
                    reason=f"趋势走弱止损: 成本¥{avg_cost:.2f}, 现价¥{current_price:.2f}",
                ))
                return signals[-1]

        return signals[-1] if signals else None

    def on_order_filled(self, signal: Signal, fill_price: float, quantity: int):
        """订单成交后更新状态"""
        symbol = signal.symbol
        if signal.action == SignalAction.OPEN_LONG:
            if symbol not in self._entry_prices:
                self._entry_prices[symbol] = []
            self._entry_prices[symbol].append(fill_price)
        elif signal.action in [SignalAction.CLOSE_LONG, SignalAction.EXIT]:
            self._entry_prices[symbol] = []

    def teardown(self):
        self.log("网格均线策略停止")

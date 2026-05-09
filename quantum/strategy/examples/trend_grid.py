"""
增强型趋势网格策略 - 修复了网格状态的版本
"""

from typing import List, Optional, Dict, Any
import numpy as np
from ...common.models import Bar, Signal, StrategyConfig
from ...common.enums import SignalAction, TimeFrame, StrategyCategory
from ..base import BaseStrategy


class TrendGridStrategy(BaseStrategy):
    """
    趋势网格策略 v2 (修复版)
    
    核心逻辑：
    - 上升趋势：只买不卖，允许分层加仓
    - 下降趋势：只卖不买，清仓等待
    - MA趋势过滤 + 回撤分档买入 + 趋势反转卖出
    
    修复了v1中状态管理混乱的问题：用 _position_levels 记录每层的买入价
    和数量，真正做到真实网格仓位管理而不是每日重开。
    """

    def __init__(self, config: Optional[StrategyConfig] = None):
        if config is None:
            config = StrategyConfig(
                name="TrendGrid",
                category=StrategyCategory.TREND_FOLLOWING,
                symbols=[],
                parameters={
                    "trend_ma": 60,          # 趋势判断均线周期
                    "grid_levels": 3,         # 网格层数
                    "grid_spacing": 0.05,     # 每层回撤间距
                    "profit_target": 0.15,    # 目标止盈
                    "stop_loss_pct": 0.10,    # 止损
                    "max_position_pct": 0.75, # 最大仓位比例
                    "entry_ma_ratio": 0.95,   # 低于趋势线多少比例开仓
                },
                timeframe=TimeFrame.DAILY,
            )
        super().__init__(config)

        self.trend_ma = config.parameters.get("trend_ma", 60)
        self.grid_levels = config.parameters.get("grid_levels", 3)
        self.grid_spacing = config.parameters.get("grid_spacing", 0.05)
        self.profit_target = config.parameters.get("profit_target", 0.15)
        self.stop_loss_pct = config.parameters.get("stop_loss_pct", 0.10)
        self.max_pos_pct = config.parameters.get("max_position_pct", 0.75)
        self.entry_ma_ratio = config.parameters.get("entry_ma_ratio", 0.95)

        # 运行时状态
        self._position_levels: Dict[str, List[Dict]] = {}  # symbol -> [{price, qty}]
        self._in_position: Dict[str, bool] = {}  # 是否持有该标的

    def setup(self):
        self.log(f"趋势网格策略v2: MA{self.trend_ma}, {self.grid_levels}层网格, {self.grid_spacing*100:.0f}%间距")
        self.log(f"目标收益: {self.profit_target*100:.0f}%, 止损: {self.stop_loss_pct*100:.0f}%")

    def on_bar(self, bar: Bar) -> Optional[Signal]:
        symbol = bar.symbol
        if symbol not in self.bar_history:
            self.bar_history[symbol] = []
            self._position_levels[symbol] = []
            self._in_position[symbol] = False
        self.bar_history[symbol].append(bar)

        bars = self.bar_history[symbol]
        if len(bars) < self.trend_ma + 10:
            return None

        closes = np.array([b.close for b in bars])
        current_price = bar.close
        ma = float(np.mean(closes[-self.trend_ma:]))
        prev_ma = float(np.mean(closes[-self.trend_ma-1:-1]))
        trend_up = ma > prev_ma  # MA趋势向上

        positions = self._position_levels.get(symbol, [])
        total_qty = sum(p['qty'] for p in positions)
        total_cost = sum(p['price'] * p['qty'] for p in positions)
        avg_cost = total_cost / total_qty if total_qty > 0 else 0
        profit_pct = (current_price - avg_cost) / avg_cost if avg_cost > 0 else 0

        signals = []
        level_capital = (self.max_pos_pct * 1_000_000) / self.grid_levels

        # === 止损检查 ===
        if total_qty > 0 and profit_pct < -self.stop_loss_pct:
            signals.append(Signal(
                timestamp=bar.time, symbol=symbol,
                action=SignalAction.CLOSE_LONG,
                price=current_price, quantity=total_qty,
                confidence=0.9,
                strategy_name=self.name,
                reason=f"止损触发: 成本{avg_cost:.2f}, 跌幅{profit_pct*100:.1f}%",
            ))
            self._in_position[symbol] = False
            self._position_levels[symbol] = []
            return signals[-1]

        # === 卖出逻辑 ===
        if total_qty > 0:
            # 趋势转空 → 清仓
            if not trend_up:
                signals.append(Signal(
                    timestamp=bar.time, symbol=symbol,
                    action=SignalAction.CLOSE_LONG,
                    price=current_price, quantity=total_qty,
                    confidence=0.7,
                    strategy_name=self.name,
                    reason=f"趋势转空(MA{self.trend_ma}向下)，清仓，盈亏{profit_pct*100:.1f}%",
                ))
                self._in_position[symbol] = False
                self._position_levels[symbol] = []
                return signals[-1]

            # 达到止盈 → 清仓
            if profit_pct >= self.profit_target:
                signals.append(Signal(
                    timestamp=bar.time, symbol=symbol,
                    action=SignalAction.CLOSE_LONG,
                    price=current_price, quantity=total_qty,
                    confidence=0.8,
                    strategy_name=self.name,
                    reason=f"达到止盈{self.profit_target*100:.0f}%: 成本{avg_cost:.2f}→{current_price:.2f}, +{profit_pct*100:.1f}%",
                ))
                self._in_position[symbol] = False
                self._position_levels[symbol] = []
                return signals[-1]

        # === 买入逻辑（仅在上升趋势中）===
        if trend_up and len(positions) < self.grid_levels:
            ref_price = positions[-1]['price'] if positions else ma * self.entry_ma_ratio

            # 如果价格回撤到低于上一个买入价 - 间距，加仓
            if current_price <= ref_price * (1 - self.grid_spacing):
                qty = int(level_capital / current_price / 100) * 100
                if qty >= 100:
                    signals.append(Signal(
                        timestamp=bar.time, symbol=symbol,
                        action=SignalAction.OPEN_LONG,
                        price=current_price, quantity=qty,
                        confidence=0.7 + (len(positions) * 0.05),
                        strategy_name=self.name,
                        reason=f"趋势中回撤加仓({len(positions)+1}/{self.grid_levels}): ¥{current_price:.2f}, MA{self.trend_ma}={ma:.2f}",
                    ))

            # 首次建仓：价格在趋势线附近或以下
            if not positions and current_price <= ma * self.entry_ma_ratio:
                qty = int(level_capital / current_price / 100) * 100
                if qty >= 100:
                    signals.append(Signal(
                        timestamp=bar.time, symbol=symbol,
                        action=SignalAction.OPEN_LONG,
                        price=current_price, quantity=qty,
                        confidence=0.6,
                        strategy_name=self.name,
                        reason=f"趋势向上首次建仓: ¥{current_price:.2f}, MA{self.trend_ma}={ma:.2f}",
                    ))

        # === 趋势转多但还没建仓 ===
        if trend_up and not positions and current_price > ma * self.entry_ma_ratio:
            # 价格已突破趋势线，等回踩再买
            pass

        return signals[-1] if signals else None

    def on_order_filled(self, signal: Signal, fill_price: float, quantity: int):
        """订单成交后更新网格状态"""
        symbol = signal.symbol
        if symbol not in self._position_levels:
            self._position_levels[symbol] = []
            self._in_position[symbol] = False

        if signal.action == SignalAction.OPEN_LONG:
            self._position_levels[symbol].append({
                'price': fill_price,
                'qty': quantity,
            })
            self._in_position[symbol] = True
            total_qty = sum(p['qty'] for p in self._position_levels[symbol])
            total_cost = sum(p['price'] * p['qty'] for p in self._position_levels[symbol])
            avg = total_cost / total_qty
            self.log(f"网格买入: {signal.symbol} ¥{fill_price:.2f}×{quantity}, 累计{total_qty}股, 均价{avg:.2f}")

        elif signal.action in [SignalAction.CLOSE_LONG, SignalAction.EXIT]:
            qty_sold = sum(p['qty'] for p in self._position_levels.get(symbol, []))
            self._position_levels[symbol] = []
            self._in_position[symbol] = False
            self.log(f"网格清仓: {signal.symbol} 卖出{qty_sold}股 @ ¥{fill_price:.2f}")

    def teardown(self):
        self.log("趋势网格策略v2停止")

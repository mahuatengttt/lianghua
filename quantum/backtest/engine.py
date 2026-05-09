"""
回测引擎 - 事件驱动的核心引擎
"""

from datetime import datetime, timedelta
from typing import List, Optional, Dict, Any, Callable
from copy import deepcopy
from dataclasses import dataclass, field
import pandas as pd
import numpy as np

from .config import BacktestEngineConfig
from .analyzer import PerformanceAnalyzer
from ..common.models import (
    Bar, Tick, Order, Trade, Position, Portfolio,
    Signal, BacktestResult, StrategyConfig,
)
from ..common.enums import (
    OrderSide, OrderType, OrderStatus,
    TimeFrame, SignalAction,
)
from ..common.exceptions import BacktestError
from ..common.utils import MathUtils
from ..strategy.base import BaseStrategy
from ..risk.base import RiskManager


class BacktestEngine:
    """
    事件驱动的回测引擎
    支持: Bar驱动回测 / Tick驱动回测
    处理: 订单管理 / 资金管理 / 费用计算 / 滑点
    """

    def __init__(self, config: BacktestEngineConfig = None):
        self.config = config or BacktestEngineConfig()

        # 运行时状态
        self.portfolio = Portfolio(total_capital=self.config.initial_capital)
        self.portfolio.available_cash = self.config.initial_capital
        self.portfolio.updated_at = self.config.start_date or datetime.now()

        self.orders: List[Order] = []
        self.trades: List[Trade] = []
        self.signals: List[Signal] = []
        self.equity_curve: List[float] = []
        self.daily_returns: List[float] = []
        self._daily_prices: Dict[str, float] = {}

        # 策略
        self.strategies: List[BaseStrategy] = []
        self.risk_managers: List[RiskManager] = []
        self._strategy_signals: Dict[str, List[Signal]] = {}

        # 数据
        self._data_manager = None
        self._callbacks: Dict[str, List[Callable]] = {}

        # 回测统计
        self.current_bar: Optional[Bar] = None
        self.current_date: Optional[datetime] = None
        self._is_backtesting = False
        self._bar_count = 0

    def add_strategy(self, strategy: BaseStrategy):
        """注册策略"""
        self.strategies.append(strategy)
        self._strategy_signals[strategy.name] = []

    def add_risk_manager(self, rm: RiskManager):
        """注册风控"""
        self.risk_managers.append(rm)

    def set_data_manager(self, dm):
        """设置数据管理器"""
        self._data_manager = dm

    def on(self, event: str, callback: Callable):
        """注册事件回调"""
        if event not in self._callbacks:
            self._callbacks[event] = []
        self._callbacks[event].append(callback)

    def _emit(self, event: str, **kwargs):
        """触发事件"""
        for cb in self._callbacks.get(event, []):
            cb(**kwargs)

    def run(self, bars_dict: Dict[str, List[Bar]]) -> BacktestResult:
        """
        执行回测
        Args:
            bars_dict: {symbol: [Bar, ...]} 历史数据
        Returns:
            BacktestResult
        """
        if not self.strategies:
            raise BacktestError("未注册任何策略")

        # 1. 策略初始化
        for s in self.strategies:
            s.setup()

        # 2. 按时间排序所有K线
        all_bars = self._sort_bars(bars_dict)

        if not all_bars:
            raise BacktestError("没有回测数据")

        self._is_backtesting = True
        self.current_date = all_bars[0][0].time
        self._emit("backtest_start", engine=self)

        # 3. 逐Bar回放
        processed = 0
        total = len(all_bars)
        for bars_at_time in all_bars:
            processed += 1
            self._process_bars(bars_at_time)

            if processed % 1000 == 0:
                self._emit("progress", processed=processed, total=total)

        # 4. 收盘处理
        self._close_all_positions()
        for s in self.strategies:
            s.teardown()

        self._is_backtesting = False
        self._emit("backtest_end", engine=self)

        # 5. 分析结果
        analyzer = PerformanceAnalyzer()
        result = analyzer.analyze(
            self.portfolio,
            self.trades,
            self.equity_curve,
            self.daily_returns,
            self.config,
        )

        return result

    def _sort_bars(self, bars_dict: Dict[str, List[Bar]]) -> List[List[Bar]]:
        """将所有K线按时间排序，同一时间的合并为一组"""
        time_map: Dict[datetime, List[Bar]] = {}

        for symbol, bars in bars_dict.items():
            for bar in bars:
                if bar.time not in time_map:
                    time_map[bar.time] = []
                time_map[bar.time].append(bar)

        sorted_times = sorted(time_map.keys())
        return [time_map[t] for t in sorted_times]

    def _process_bars(self, bars: List[Bar]):
        """处理同一时间的一组K线"""
        self.current_bar = bars[0]
        self.current_date = bars[0].time
        self._bar_count += 1

        # 1. 更新当前价格
        for bar in bars:
            self._daily_prices[bar.symbol] = bar.close

        # 2. 更新持仓市值
        self._update_portfolio_value(bars)

        # 3. 每条K线分别跑策略
        all_signals = []
        for bar in bars:
            for strategy in self.strategies:
                if bar.symbol not in strategy.symbols:
                    continue
                signal = strategy.on_bar(bar)
                if signal:
                    strategy.signals.append(signal)
                    all_signals.append(signal)
                    self._strategy_signals[strategy.name].append(signal)

        # 4. 风控检查（先于交易执行）
        if self.risk_managers:
            for rm in self.risk_managers:
                all_signals = rm.before_trade(
                    all_signals, self.portfolio, self.current_date
                )

        # 5. 执行信号 → 订单
        for signal in all_signals:
            orders = self._signal_to_orders(signal)
            for order in orders:
                self._execute_order(order, bars)

        # 6. 记录权益曲线
        self.equity_curve.append(self.portfolio.total_capital)
        if len(self.equity_curve) > 1:
            daily_ret = (self.equity_curve[-1] / self.equity_curve[-2]) - 1
            self.daily_returns.append(daily_ret)

        self._emit("bar_processed", bar=bars[0], portfolio=self.portfolio)

    def _signal_to_orders(self, signal: Signal) -> List[Order]:
        """信号转订单"""
        orders = []

        if signal.action == SignalAction.OPEN_LONG:
            if not signal.quantity:
                # 计算可买数量
                cash = self.portfolio.available_cash
                price = signal.price
                qty = int(cash / price / self.config.lot_size) * self.config.lot_size
                signal.quantity = max(0, qty)

            if signal.quantity > 0:
                orders.append(Order(
                    order_id=f"O{len(self.orders)+1}",
                    symbol=signal.symbol,
                    side=OrderSide.BUY,
                    order_type=OrderType.MARKET,
                    price=signal.price,
                    quantity=signal.quantity,
                    created_at=signal.timestamp,
                    client_id=signal.strategy_name,
                ))

        elif signal.action == SignalAction.CLOSE_LONG:
            position = self.portfolio.positions.get(signal.symbol)
            if position and position.quantity > 0:
                orders.append(Order(
                    order_id=f"O{len(self.orders)+1}",
                    symbol=signal.symbol,
                    side=OrderSide.SELL,
                    order_type=OrderType.MARKET,
                    price=signal.price,
                    quantity=position.quantity,
                    created_at=signal.timestamp,
                    client_id=signal.strategy_name,
                ))

        elif signal.action == SignalAction.EXIT:
            for symbol, pos in self.portfolio.positions.items():
                if pos.quantity > 0:
                    orders.append(Order(
                        order_id=f"O{len(self.orders)+1}",
                        symbol=symbol,
                        side=OrderSide.SELL,
                        order_type=OrderType.MARKET,
                        price=signal.price,
                        quantity=pos.quantity,
                        created_at=signal.timestamp,
                        client_id=signal.strategy_name,
                    ))

        return orders

    def _execute_order(self, order: Order, bars: List[Bar]):
        """执行订单（模拟撮合）"""
        self.orders.append(order)

        # 找当前成交价
        fill_price = self._get_fill_price(order, bars)
        if fill_price is None:
            order.status = OrderStatus.REJECTED
            order.reason = "无法获取成交价"
            return

        # 应用滑点
        fill_price = self._apply_slippage(fill_price, order.side)

        # 数量约束
        fill_qty = order.quantity
        fill_qty = MathUtils.round_quantity(fill_qty, self.config.lot_size)
        if fill_qty <= 0:
            order.status = OrderStatus.REJECTED
            order.reason = "数量不符合最小交易单位"
            return

        # 资金检查
        if order.side in [OrderSide.BUY, OrderSide.BUY_COVER]:
            cost = fill_price * fill_qty
            if cost > self.portfolio.available_cash:
                # 部分成交
                fill_qty = int(self.portfolio.available_cash / fill_price / self.config.lot_size) * self.config.lot_size
                if fill_qty <= 0:
                    order.status = OrderStatus.REJECTED
                    order.reason = "资金不足"
                    return
                cost = fill_price * fill_qty

        # 计算费用
        commission, tax = self._calc_fees(fill_price, fill_qty, order.side)

        # 创建成交记录
        trade = Trade(
            trade_id=f"T{len(self.trades)+1}",
            order_id=order.order_id,
            symbol=order.symbol,
            side=order.side,
            price=fill_price,
            quantity=fill_qty,
            amount=fill_price * fill_qty,
            commission=commission,
            tax=tax,
            trade_time=self.current_date,
        )
        self.trades.append(trade)

        # 更新订单状态
        order.status = OrderStatus.FILLED
        order.filled_quantity = fill_qty
        order.filled_amount = trade.amount
        order.avg_fill_price = fill_price
        order.updated_at = self.current_date

        # 更新持仓和资金
        self._update_position_and_cash(trade)

        self._emit("trade_executed", trade=trade, portfolio=self.portfolio)

    def _get_fill_price(self, order: Order, bars: List[Bar]) -> Optional[float]:
        """获取成交价格"""
        for bar in bars:
            if bar.symbol == order.symbol:
                if order.order_type == OrderType.MARKET:
                    return bar.close
                elif order.order_type == OrderType.LIMIT:
                    if (order.side == OrderSide.BUY and bar.high >= order.price) or \
                       (order.side == OrderSide.SELL and bar.low <= order.price):
                        return order.price
                return None
        return self._daily_prices.get(order.symbol)

    def _apply_slippage(self, price: float, side: OrderSide) -> float:
        """应用滑点"""
        if self.config.slippage_mode == "none":
            return price

        slippage_amount = price * self.config.slippage
        if side == OrderSide.BUY:
            return price + slippage_amount  # 买时滑点更高
        else:
            return price - slippage_amount  # 卖时滑点更低

    def _calc_fees(self, price: float, quantity: int, side: OrderSide) -> tuple:
        """计算佣金和税费"""
        amount = price * quantity
        commission = max(amount * self.config.commission_rate, self.config.min_commission)
        tax = amount * self.config.tax_rate if side == OrderSide.SELL else 0
        return commission, tax

    def _update_position_and_cash(self, trade: Trade):
        """更新持仓和资金"""
        symbol = trade.symbol

        if symbol not in self.portfolio.positions:
            self.portfolio.positions[symbol] = Position(symbol=symbol)

        pos = self.portfolio.positions[symbol]

        if trade.side == OrderSide.BUY:
            # 更新成本
            total_cost = pos.avg_cost * abs(pos.quantity) + trade.amount
            pos.quantity += trade.quantity
            pos.avg_cost = total_cost / pos.quantity if pos.quantity > 0 else 0
            pos.realized_pnl += 0
            self.portfolio.available_cash -= (trade.amount + trade.commission + trade.tax)

        elif trade.side == OrderSide.SELL:
            # 计算收益
            sell_value = trade.amount - trade.commission - trade.tax
            buy_cost = pos.avg_cost * trade.quantity
            realized_pnl = sell_value - buy_cost
            pos.quantity -= trade.quantity
            pos.realized_pnl += realized_pnl

            if pos.quantity <= 0:
                self.portfolio.positions.pop(symbol, None)

            self.portfolio.available_cash += sell_value

    def _update_portfolio_value(self, bars: List[Bar]):
        """更新投资组合总价值"""
        total_market_value = 0.0

        for symbol, pos in self.portfolio.positions.items():
            current_price = self._daily_prices.get(symbol, pos.avg_cost)
            pos.market_value = pos.quantity * current_price
            pos.unrealized_pnl = (current_price - pos.avg_cost) * pos.quantity
            pos.unrealized_pnl_pct = (current_price / pos.avg_cost - 1) if pos.avg_cost > 0 else 0
            pos.total_pnl = pos.realized_pnl + pos.unrealized_pnl
            total_market_value += pos.market_value

        self.portfolio.market_value = total_market_value
        old_total = self.portfolio.total_capital
        self.portfolio.total_capital = self.portfolio.available_cash + total_market_value
        self.portfolio.total_pnl = self.portfolio.total_capital - (
            self.portfolio.total_capital - self.portfolio.total_pnl if old_total > 0
            else self.portfolio.total_capital - self.config.initial_capital
        )
        self.portfolio.total_pnl_pct = self.portfolio.total_pnl / self.config.initial_capital
        self.portfolio.daily_pnl = self.portfolio.total_capital - old_total if old_total > 0 else 0
        self.portfolio.updated_at = self.current_date

    def _close_all_positions(self):
        """收盘时平掉所有仓位"""
        for symbol, pos in list(self.portfolio.positions.items()):
            if pos.quantity == 0:
                continue

            price = self._daily_prices.get(symbol, pos.avg_cost)
            trade = Trade(
                trade_id=f"T{len(self.trades)+1}",
                order_id=f"O{len(self.orders)+1}",
                symbol=symbol,
                side=OrderSide.SELL,
                price=price,
                quantity=pos.quantity,
                amount=price * pos.quantity,
                commission=price * pos.quantity * self.config.commission_rate,
                tax=price * pos.quantity * self.config.tax_rate,
                trade_time=self.current_date or datetime.now(),
                note="回测结束强制平仓",
            )
            self.trades.append(trade)
            self._update_position_and_cash(trade)

        self.equity_curve.append(self.portfolio.total_capital)

    def generate_report(self, result: BacktestResult) -> str:
        """生成HTML报告"""
        report = BacktestReport()
        return report.generate(result, self.config)

    def get_summary(self) -> dict:
        """获取回测中间状态摘要"""
        return {
            "bars_processed": self._bar_count,
            "total_trades": len(self.trades),
            "total_signals": len(self.signals),
            "current_capital": self.portfolio.total_capital,
            "available_cash": self.portfolio.available_cash,
            "positions": len(self.portfolio.positions),
            "current_date": self.current_date,
        }

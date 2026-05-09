"""
模拟交易Broker - 用于实盘前的纸上交易
"""

import time
import uuid
from datetime import datetime
from typing import List, Optional, Dict, Any, Callable
from copy import deepcopy

from .base import BaseBroker
from ..common.models import Order, Trade, Position, Portfolio, Bar
from ..common.enums import OrderSide, OrderType, OrderStatus
from ..common.exceptions import BrokerError, OrderError


class PaperBroker(BaseBroker):
    """
    模拟交易券商
    用于策略实盘前的纸上交易模拟
    内部维护模拟订单簿和资金
    """

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.initial_capital = config.get("initial_capital", 1_000_000.0)

        # 内部状态
        self._connected = False
        self._orders: Dict[str, Order] = {}
        self._trades: List[Trade] = []
        self._portfolio = Portfolio(
            total_capital=self.initial_capital,
            available_cash=self.initial_capital,
        )
        self._prices: Dict[str, float] = {}

        # 回调
        self._tick_callbacks: List[Callable] = []
        self._order_callbacks: List[Callable] = []
        self._trade_callbacks: List[Callable] = []

        # 费用设置
        self.commission_rate = config.get("commission_rate", 0.0003)
        self.min_commission = config.get("min_commission", 5.0)
        self.tax_rate = config.get("tax_rate", 0.001)
        self.slippage = config.get("slippage", 0.001)
        self.lot_size = config.get("lot_size", 100)

    def connect(self) -> bool:
        self._connected = True
        self._portfolio = Portfolio(
            total_capital=self.initial_capital,
            available_cash=self.initial_capital,
        )
        return True

    def disconnect(self):
        self._connected = False

    def is_connected(self) -> bool:
        return self._connected

    def place_order(self, order: Order) -> str:
        if not self._connected:
            raise BrokerError("未连接")

        # 生成订单ID
        order.order_id = f"paper_{uuid.uuid4().hex[:8]}"
        order.status = OrderStatus.SUBMITTED
        order.created_at = datetime.now()

        # 检查资金/持仓
        if order.side == OrderSide.BUY:
            cost = order.price * order.quantity
            if cost > self._portfolio.available_cash:
                raise OrderError(f"资金不足: 需要{cost:.2f}, 可用{self._portfolio.available_cash:.2f}")
        elif order.side == OrderSide.SELL:
            pos = self._portfolio.positions.get(order.symbol)
            if not pos or pos.quantity < order.quantity:
                raise OrderError(f"持仓不足: {order.symbol}")

        # 模拟成交（立即完全成交）
        fill_price = self._apply_slippage(
            order.price or self._prices.get(order.symbol, order.price),
            order.side,
        )

        commission = max(order.quantity * fill_price * self.commission_rate, self.min_commission)
        tax = order.quantity * fill_price * self.tax_rate if order.side == OrderSide.SELL else 0

        trade = Trade(
            trade_id=f"trade_{uuid.uuid4().hex[:8]}",
            order_id=order.order_id,
            symbol=order.symbol,
            side=order.side,
            price=fill_price,
            quantity=order.quantity,
            amount=fill_price * order.quantity,
            commission=commission,
            tax=tax,
            trade_time=datetime.now(),
        )

        # 更新状态
        order.status = OrderStatus.FILLED
        order.filled_quantity = order.quantity
        order.filled_amount = trade.amount
        order.avg_fill_price = fill_price
        order.updated_at = datetime.now()

        self._orders[order.order_id] = order
        self._trades.append(trade)

        # 更新持仓和资金
        self._update_position(trade)

        # 触发回调
        for cb in self._order_callbacks:
            cb(order)
        for cb in self._trade_callbacks:
            cb(trade)

        return order.order_id

    def cancel_order(self, order_id: str) -> bool:
        order = self._orders.get(order_id)
        if order and order.status == OrderStatus.SUBMITTED:
            order.status = OrderStatus.CANCELLED
            return True
        return False

    def get_order_status(self, order_id: str) -> Dict:
        order = self._orders.get(order_id)
        if not order:
            return {}
        return {
            "order_id": order.order_id,
            "symbol": order.symbol,
            "side": order.side.value,
            "status": order.status.value,
            "filled_qty": order.filled_quantity,
            "filled_amount": order.filled_amount,
            "avg_price": order.avg_fill_price,
        }

    def get_positions(self) -> List[Position]:
        return list(self._portfolio.positions.values())

    def get_portfolio(self) -> Portfolio:
        return deepcopy(self._portfolio)

    def get_account_info(self) -> Dict:
        return {
            "total_asset": self._portfolio.total_capital,
            "available_cash": self._portfolio.available_cash,
            "market_value": self._portfolio.market_value,
            "frozen_cash": self._portfolio.frozen_cash,
            "total_pnl": self._portfolio.total_pnl,
        }

    def update_price(self, symbol: str, price: float):
        """更新行情价格（模拟用）"""
        self._prices[symbol] = price
        self._update_portfolio_value()

    def on_tick(self, callback):
        self._tick_callbacks.append(callback)

    def on_order_update(self, callback):
        self._order_callbacks.append(callback)

    def on_trade(self, callback):
        self._trade_callbacks.append(callback)

    def _update_position(self, trade: Trade):
        """更新持仓（同回测引擎逻辑）"""
        symbol = trade.symbol

        if symbol not in self._portfolio.positions:
            self._portfolio.positions[symbol] = Position(symbol=symbol)

        pos = self._portfolio.positions[symbol]

        if trade.side == OrderSide.BUY:
            total_cost = pos.avg_cost * abs(pos.quantity) + trade.amount
            pos.quantity += trade.quantity
            pos.avg_cost = total_cost / pos.quantity if pos.quantity > 0 else 0
            self._portfolio.available_cash -= (trade.amount + trade.commission + trade.tax)
        elif trade.side == OrderSide.SELL:
            sell_value = trade.amount - trade.commission - trade.tax
            buy_cost = pos.avg_cost * trade.quantity
            realized_pnl = sell_value - buy_cost
            pos.quantity -= trade.quantity
            pos.realized_pnl += realized_pnl
            if pos.quantity <= 0:
                self._portfolio.positions.pop(symbol, None)
            self._portfolio.available_cash += sell_value

    def _update_portfolio_value(self):
        """更新组合总价值"""
        total_mv = 0.0
        for symbol, pos in self._portfolio.positions.items():
            price = self._prices.get(symbol, pos.avg_cost)
            pos.market_value = pos.quantity * price
            pos.unrealized_pnl = (price - pos.avg_cost) * pos.quantity
            total_mv += pos.market_value

        self._portfolio.market_value = total_mv
        self._portfolio.total_capital = self._portfolio.available_cash + total_mv
        self._portfolio.total_pnl = self._portfolio.total_capital - self.initial_capital
        self._portfolio.total_pnl_pct = self._portfolio.total_pnl / self.initial_capital

    def _apply_slippage(self, price: float, side: OrderSide) -> float:
        slip = price * self.slippage
        if side == OrderSide.BUY:
            return price + slip
        return price - slip

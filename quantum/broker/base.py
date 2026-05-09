"""
Broker抽象基类 - 统一交易接口
"""

from abc import ABC, abstractmethod
from typing import List, Optional, Dict, Any
from datetime import datetime

from ..common.models import Order, Trade, Position, Portfolio, Bar, Tick
from ..common.enums import OrderSide, OrderType


class BaseBroker(ABC):
    """
    券商接口抽象基类
    所有券商接入需实现此接口
    确保回测和实盘使用同一套API
    """

    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.name = config.get("name", "broker")

    @abstractmethod
    def connect(self) -> bool:
        """连接交易服务器"""
        pass

    @abstractmethod
    def disconnect(self):
        """断开连接"""
        pass

    @abstractmethod
    def is_connected(self) -> bool:
        """检查连接状态"""
        pass

    @abstractmethod
    def place_order(self, order: Order) -> str:
        """下单，返回订单ID"""
        pass

    @abstractmethod
    def cancel_order(self, order_id: str) -> bool:
        """撤单"""
        pass

    @abstractmethod
    def get_order_status(self, order_id: str) -> Dict:
        """查询订单状态"""
        pass

    @abstractmethod
    def get_positions(self) -> List[Position]:
        """获取当前持仓"""
        pass

    @abstractmethod
    def get_portfolio(self) -> Portfolio:
        """获取账户资产"""
        pass

    @abstractmethod
    def get_account_info(self) -> Dict:
        """获取账户信息"""
        pass

    def subscribe_market_data(self, symbols: List[str]):
        """订阅行情"""
        pass

    def on_tick(self, callback):
        """注册Tick回调"""
        pass

    def on_order_update(self, callback):
        """注册订单状态变更回调"""
        pass

    def on_trade(self, callback):
        """注册成交回调"""
        pass

    def health_check(self) -> bool:
        """健康检查"""
        return self.is_connected()

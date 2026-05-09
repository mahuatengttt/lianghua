"""
核心数据模型 - 使用 Pydantic 定义强类型数据类
"""

from datetime import datetime
from decimal import Decimal
from typing import Optional, List, Dict, Any, Union
from pydantic import BaseModel, Field

try:
    from pydantic import ConfigDict as _ConfigDict
except ImportError:
    # Pydantic v1: config is a dict
    _ConfigDict = dict

from .enums import (
    OrderSide, OrderType, OrderStatus,
    TimeFrame, MarketType, SignalAction, StrategyCategory,
)


class Bar(BaseModel):
    """K线数据"""
    symbol: str = Field(..., description="股票代码")
    time: datetime = Field(..., description="时间戳")
    timeframe: TimeFrame = Field(..., description="周期")
    open: float = Field(..., description="开盘价")
    high: float = Field(..., description="最高价")
    low: float = Field(..., description="最低价")
    close: float = Field(..., description="收盘价")
    volume: float = Field(0.0, description="成交量")
    amount: float = Field(0.0, description="成交额")
    open_interest: Optional[float] = Field(None, description="持仓量")

    model_config = {"frozen": True}

    @property
    def mid_price(self) -> float:
        return (self.high + self.low) / 2

    @property
    def range(self) -> float:
        return self.high - self.low

    @property
    def return_rate(self) -> float:
        """涨跌幅"""
        return (self.close - self.open) / self.open if self.open != 0 else 0.0


class Tick(BaseModel):
    """Tick级行情数据"""
    symbol: str
    time: datetime
    price: float
    volume: int
    amount: float
    direction: Optional[str] = None  # buy/sell/unknown
    bid_prices: List[float] = Field(default_factory=list)
    bid_volumes: List[int] = Field(default_factory=list)
    ask_prices: List[float] = Field(default_factory=list)
    ask_volumes: List[int] = Field(default_factory=list)

    model_config = {"frozen": True}


class Order(BaseModel):
    """订单"""
    order_id: str
    symbol: str
    side: OrderSide
    order_type: OrderType
    price: float
    quantity: int
    filled_quantity: int = 0
    filled_amount: float = 0.0
    avg_fill_price: float = 0.0
    status: OrderStatus = OrderStatus.PENDING
    created_at: datetime
    updated_at: Optional[datetime] = None
    reason: Optional[str] = None
    client_id: Optional[str] = None


class Trade(BaseModel):
    """成交记录"""
    trade_id: str
    order_id: str
    symbol: str
    side: OrderSide
    price: float
    quantity: int
    amount: float
    commission: float = 0.0
    tax: float = 0.0
    trade_time: datetime
    note: Optional[str] = None


class Position(BaseModel):
    """持仓信息"""
    symbol: str
    quantity: int = 0  # 正数=多头，负数=空头
    avg_cost: float = 0.0
    market_value: float = 0.0
    unrealized_pnl: float = 0.0
    unrealized_pnl_pct: float = 0.0
    realized_pnl: float = 0.0
    total_pnl: float = 0.0

    @property
    def is_long(self) -> bool:
        return self.quantity > 0

    @property
    def is_short(self) -> bool:
        return self.quantity < 0

    @property
    def abs_quantity(self) -> int:
        return abs(self.quantity)


class Portfolio(BaseModel):
    """投资组合"""
    total_capital: float = 0.0          # 总资产
    available_cash: float = 0.0         # 可用现金
    frozen_cash: float = 0.0            # 冻结资金
    market_value: float = 0.0           # 持仓市值
    positions: Dict[str, Position] = Field(default_factory=dict)
    total_pnl: float = 0.0
    total_pnl_pct: float = 0.0
    daily_pnl: float = 0.0
    daily_pnl_pct: float = 0.0
    updated_at: Optional[datetime] = None


class Signal(BaseModel):
    """交易信号"""
    timestamp: datetime
    symbol: str
    action: SignalAction
    price: float
    confidence: float = Field(1.0, ge=0.0, le=1.0)
    quantity: int = 0
    strategy_name: str = ""
    reason: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class StrategyConfig(BaseModel):
    """策略配置"""
    name: str
    category: StrategyCategory
    symbols: List[str]
    parameters: Dict[str, Any] = Field(default_factory=dict)
    timeframe: TimeFrame = TimeFrame.DAILY
    capital_pct: float = Field(1.0, ge=0.0, le=1.0)  # 资金分配比例
    enabled: bool = True


class BacktestResult(BaseModel):
    """回测结果"""
    strategy_name: str
    start_date: datetime
    end_date: datetime
    initial_capital: float
    final_capital: float
    total_return: float
    annual_return: float
    max_drawdown: float
    sharpe_ratio: float
    sortino_ratio: float
    calmar_ratio: float
    win_rate: float
    profit_factor: float
    total_trades: int
    winning_trades: int
    losing_trades: int
    avg_holding_period: float         # 平均持仓天数
    avg_trade_return: float
    daily_returns: List[float] = Field(default_factory=list)
    equity_curve: List[float] = Field(default_factory=list)
    trades: List[Trade] = Field(default_factory=list)
    monthly_returns: Dict[str, float] = Field(default_factory=dict)


class RiskMetrics(BaseModel):
    """风险指标快照"""
    timestamp: datetime
    total_pnl_pct: float
    current_drawdown: float
    max_drawdown: float
    var_95: float                     # 95% VaR
    cvar_95: float                    # 95% CVaR
    volatility: float                 # 年化波动率
    position_concentration: float     # 持仓集中度
    leverage: float                   # 杠杆率
    sharpe_ratio: float
    beta: float
    correlation_with_market: float

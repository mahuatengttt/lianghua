"""
枚举类型定义
"""

from enum import Enum, auto


class OrderSide(Enum):
    """订单方向"""
    BUY = "buy"           # 买入
    SELL = "sell"         # 卖出
    BUY_COVER = "buy_cover"  # 平空
    SELL_SHORT = "sell_short"  # 开空


class OrderType(Enum):
    """订单类型"""
    MARKET = "market"        # 市价单
    LIMIT = "limit"          # 限价单
    STOP = "stop"            # 止损单
    STOP_LIMIT = "stop_limit"  # 止损限价单


class OrderStatus(Enum):
    """订单状态"""
    PENDING = "pending"         # 待提交
    SUBMITTED = "submitted"     # 已提交
    PARTIAL_FILLED = "partial"  # 部分成交
    FILLED = "filled"           # 全部成交
    CANCELLED = "cancelled"     # 已撤销
    REJECTED = "rejected"       # 已拒绝
    EXPIRED = "expired"         # 已过期


class TimeFrame(Enum):
    """K线时间周期"""
    TICK = "tick"          # Tick级
    MIN1 = "1min"          # 1分钟
    MIN5 = "5min"          # 5分钟
    MIN15 = "15min"        # 15分钟
    MIN30 = "30min"        # 30分钟
    MIN60 = "60min"        # 60分钟
    DAILY = "daily"        # 日线
    WEEKLY = "weekly"      # 周线
    MONTHLY = "monthly"    # 月线


class MarketType(Enum):
    """市场类型"""
    A_SHARE = "ashare"       # A股
    INDEX = "index"          # 指数
    FUTURES = "futures"      # 期货
    ETF = "etf"              # ETF
    CRYPTO = "crypto"        # 数字货币（扩展）


class SignalAction(Enum):
    """信号动作"""
    HOLD = "hold"             # 持仓不动
    OPEN_LONG = "open_long"   # 开多
    OPEN_SHORT = "open_short" # 开空
    CLOSE_LONG = "close_long" # 平多
    CLOSE_SHORT = "close_short" # 平空
    REVERSE = "reverse"       # 反向开仓
    EXIT = "exit"             # 全部平仓


class StrategyCategory(Enum):
    """策略分类"""
    TREND_FOLLOWING = "trend_following"     # 趋势跟踪
    MEAN_REVERSION = "mean_reversion"       # 均值回归
    ARBITRAGE = "arbitrage"                 # 套利
    FACTOR = "factor"                       # 因子
    ML_PREDICT = "ml_predict"              # 机器学习
    EVENT_DRIVEN = "event_driven"           # 事件驱动
    HIGH_FREQ = "high_frequency"            # 高频
    MIXED = "mixed"                         # 混合


class DataSourceType(Enum):
    """数据源类型"""
    TUSHARE = "tushare"
    AKSHARE = "akshare"
    LOCAL = "local"
    DATABASE = "database"

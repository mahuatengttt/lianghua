"""
自定义异常定义
"""


class QuantumError(Exception):
    """量子系统基础异常"""
    pass


class DataError(QuantumError):
    """数据模块异常"""
    pass


class DataSourceError(DataError):
    """数据源连接/获取异常"""
    pass


class DataValidationError(DataError):
    """数据校验异常"""
    pass


class StorageError(DataError):
    """数据存储异常"""
    pass


class StrategyError(QuantumError):
    """策略模块异常"""
    pass


class SignalError(StrategyError):
    """信号生成异常"""
    pass


class PortfolioError(StrategyError):
    """组合构建异常"""
    pass


class BacktestError(QuantumError):
    """回测模块异常"""
    pass


class BrokerError(QuantumError):
    """交易接口异常"""
    pass


class OrderError(BrokerError):
    """订单操作异常"""
    pass


class ConnectionError(BrokerError):
    """连接异常"""
    pass


class RiskError(QuantumError):
    """风控模块异常"""
    pass


class PositionLimitError(RiskError):
    """仓位超限异常"""
    pass


class DrawdownError(RiskError):
    """回撤超限异常"""
    pass


class ConfigError(QuantumError):
    """配置异常"""
    pass

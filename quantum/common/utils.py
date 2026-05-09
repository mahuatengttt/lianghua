"""
工具函数集合
"""

import json
import os
import yaml
from datetime import datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Any, Dict, Optional, Union, List
from loguru import logger


class DateTimeUtils:
    """日期时间工具"""

    @staticmethod
    def to_timestamp(dt: datetime) -> int:
        return int(dt.timestamp())

    @staticmethod
    def from_timestamp(ts: Union[int, float]) -> datetime:
        return datetime.fromtimestamp(ts)

    @staticmethod
    def is_trading_day(dt: datetime) -> bool:
        """简单判断交易日（排除周末，不包含节假日）"""
        return dt.weekday() < 5

    @staticmethod
    def get_trading_dates(start: datetime, end: datetime) -> List[datetime]:
        """获取交易日列表"""
        dates = []
        current = start
        while current <= end:
            if current.weekday() < 5:
                dates.append(current)
            current += timedelta(days=1)
        return dates

    @staticmethod
    def align_to_bar(time: datetime, timeframe: str) -> datetime:
        """将时间对齐到对应K线时间"""
        if timeframe == "1min":
            return time.replace(second=0, microsecond=0)
        elif timeframe == "5min":
            minute = (time.minute // 5) * 5
            return time.replace(minute=minute, second=0, microsecond=0)
        elif timeframe == "15min":
            minute = (time.minute // 15) * 15
            return time.replace(minute=minute, second=0, microsecond=0)
        elif timeframe == "30min":
            minute = (time.minute // 30) * 30
            return time.replace(minute=minute, second=0, microsecond=0)
        elif timeframe == "60min":
            return time.replace(minute=0, second=0, microsecond=0)
        elif timeframe == "daily":
            return time.replace(hour=0, minute=0, second=0, microsecond=0)
        return time


class MathUtils:
    """数学计算工具"""

    @staticmethod
    def round_price(price: float, precision: int = 2) -> float:
        """价格舍入"""
        return float(Decimal(str(price)).quantize(
            Decimal('0.' + '0' * precision), rounding=ROUND_HALF_UP
        ))

    @staticmethod
    def round_quantity(qty: float, lot_size: int = 100) -> int:
        """数量舍入到整手"""
        return (int(qty) // lot_size) * lot_size

    @staticmethod
    def max_drawdown(equity_curve: List[float]) -> float:
        """计算最大回撤"""
        if not equity_curve:
            return 0.0
        peak = equity_curve[0]
        max_dd = 0.0
        for value in equity_curve:
            if value > peak:
                peak = value
            dd = (peak - value) / peak if peak > 0 else 0
            max_dd = max(max_dd, dd)
        return max_dd

    @staticmethod
    def sharpe_ratio(returns: List[float], risk_free_rate: float = 0.025, periods: int = 252) -> float:
        """夏普比率"""
        if len(returns) < 2:
            return 0.0
        import numpy as np
        excess = np.array(returns) - risk_free_rate / periods
        if np.std(excess) == 0:
            return 0.0
        return float(np.mean(excess) / np.std(excess) * np.sqrt(periods))

    @staticmethod
    def sortino_ratio(returns: List[float], risk_free_rate: float = 0.025, periods: int = 252) -> float:
        """索提诺比率"""
        if len(returns) < 2:
            return 0.0
        import numpy as np
        excess = np.array(returns) - risk_free_rate / periods
        downside = excess[excess < 0]
        if len(downside) == 0 or np.std(downside) == 0:
            return 1e6
        return float(np.mean(excess) / np.std(downside) * np.sqrt(periods))

    @staticmethod
    def win_rate(trades: List) -> float:
        """胜率"""
        if not trades:
            return 0.0
        wins = sum(1 for t in trades if getattr(t, 'pnl', 0) > 0)
        return wins / len(trades)


class IndicatorUtils:
    """技术指标计算工具（基于numpy/pandas）"""
    import numpy as np
    import pandas as pd

    @staticmethod
    def sma(data: 'np.ndarray', period: int) -> 'np.ndarray':
        """简单移动平均"""
        import numpy as np
        result = np.zeros(len(data)) * np.nan
        for i in range(period - 1, len(data)):
            result[i] = np.mean(data[i - period + 1:i + 1])
        return result

    @staticmethod
    def ema(data: 'np.ndarray', period: int) -> 'np.ndarray':
        """指数移动平均"""
        import numpy as np
        result = np.zeros(len(data)) * np.nan
        multiplier = 2 / (period + 1)
        result[0] = data[0]
        for i in range(1, len(data)):
            result[i] = (data[i] - result[i - 1]) * multiplier + result[i - 1]
        return result

    @staticmethod
    def rsi(data: 'np.ndarray', period: int = 14) -> 'np.ndarray':
        """RSI指标"""
        import numpy as np
        deltas = np.diff(data)
        gains = np.where(deltas > 0, deltas, 0)
        losses = np.where(deltas < 0, -deltas, 0)
        avg_gain = np.zeros(len(gains)) * np.nan
        avg_loss = np.zeros(len(losses)) * np.nan
        for i in range(period - 1, len(gains)):
            avg_gain[i] = np.mean(gains[i - period + 1:i + 1])
            avg_loss[i] = np.mean(losses[i - period + 1:i + 1])
        rs = np.where(avg_loss == 0, 100, avg_gain / avg_loss)
        rsi_values = 100 - (100 / (1 + rs))
        result = np.zeros(len(data)) * np.nan
        result[1:] = rsi_values
        return result

    @staticmethod
    def bollinger(data: 'np.ndarray', period: int = 20, std_dev: float = 2.0):
        """布林带"""
        import numpy as np
        middle = IndicatorUtils.sma(data, period)
        std = np.zeros(len(data)) * np.nan
        for i in range(period - 1, len(data)):
            std[i] = np.std(data[i - period + 1:i + 1])
        upper = middle + std_dev * std
        lower = middle - std_dev * std
        return upper, middle, lower

    @staticmethod
    def macd(data: 'np.ndarray', fast: int = 12, slow: int = 26, signal: int = 9):
        """MACD指标"""
        ema_fast = IndicatorUtils.ema(data, fast)
        ema_slow = IndicatorUtils.ema(data, slow)
        dif = ema_fast - ema_slow
        dea = IndicatorUtils.ema(dif, signal)
        hist = dif - dea
        return dif, dea, hist

    @staticmethod
    def atr(high: 'np.ndarray', low: 'np.ndarray', close: 'np.ndarray', period: int = 14) -> 'np.ndarray':
        """ATR指标"""
        import numpy as np
        tr = np.zeros(len(high))
        for i in range(1, len(high)):
            hl = high[i] - low[i]
            hc = abs(high[i] - close[i - 1])
            lc = abs(low[i] - close[i - 1])
            tr[i] = max(hl, hc, lc)
        atr_values = np.zeros(len(tr)) * np.nan
        atr_values[period - 1] = np.mean(tr[:period])
        for i in range(period, len(tr)):
            atr_values[i] = (atr_values[i - 1] * (period - 1) + tr[i]) / period
        return atr_values

    @staticmethod
    def zscore(data: 'np.ndarray', period: int = 20) -> 'np.ndarray':
        """Z-Score标准化"""
        import numpy as np
        result = np.zeros(len(data)) * np.nan
        for i in range(period - 1, len(data)):
            window = data[i - period + 1:i + 1]
            if np.std(window) > 0:
                result[i] = (data[i] - np.mean(window)) / np.std(window)
            else:
                result[i] = 0
        return result


class ConfigLoader:
    """配置加载器"""

    @staticmethod
    def load(config_path: str) -> Dict[str, Any]:
        """加载YAML配置文件"""
        path = Path(config_path)
        if not path.exists():
            raise FileNotFoundError(f"配置文件不存在: {config_path}")
        with open(path, 'r', encoding='utf-8') as f:
            return yaml.safe_load(f)

    @staticmethod
    def merge(base: Dict, override: Dict) -> Dict:
        """递归合并配置"""
        result = base.copy()
        for key, value in override.items():
            if key in result and isinstance(result[key], dict) and isinstance(value, dict):
                result[key] = ConfigLoader.merge(result[key], value)
            else:
                result[key] = value
        return result


def setup_logger(name: str = "quantum", level: str = "INFO", log_file: Optional[str] = None):
    """配置日志"""
    logger.remove()
    fmt = "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | <level>{level:8}</level> | <cyan>{name}</cyan> | <level>{message}</level>"
    logger.add(lambda msg: print(msg), format=fmt, level=level, colorize=True)
    if log_file:
        logger.add(log_file, format="{time} | {level:8} | {name} | {message}", level=level, rotation="100 MB")
    return logger


def get_project_root() -> Path:
    """获取项目根目录"""
    return Path(__file__).parent.parent.parent

"""
数据预处理：清洗、对齐、周期转换
"""

from datetime import datetime, timedelta
from typing import List, Optional, Callable
import numpy as np
import pandas as pd

from ...common.models import Bar
from ...common.enums import TimeFrame
from ...common.exceptions import DataValidationError
from ...common.utils import DateTimeUtils


class DataCleaner:
    """数据清洗"""

    def __init__(self, config: Optional[dict] = None):
        self.config = config or {}

    def clean_bars(self, bars: List[Bar]) -> List[Bar]:
        """清洗K线数据"""
        if not bars:
            return bars

        # 1. 去除空值
        bars = [b for b in bars if not self._is_invalid(b)]

        # 2. 去除异常价格
        bars = self._remove_outliers(bars)

        # 3. 去除重复
        bars = self._remove_duplicates(bars)

        # 4. 排序
        bars.sort(key=lambda b: b.time)

        # 5. 填充缺失
        if self.config.get("fill_gaps", False):
            bars = self._fill_gaps(bars)

        return bars

    def _is_invalid(self, bar: Bar) -> bool:
        return (
            np.isnan(bar.open) or np.isnan(bar.high) or
            np.isnan(bar.low) or np.isnan(bar.close) or
            bar.open <= 0 or bar.high <= 0 or
            bar.low <= 0 or bar.close <= 0 or
            bar.high < bar.low or
            bar.high < bar.open or bar.high < bar.close or
            bar.low > bar.open or bar.low > bar.close
        )

    def _remove_outliers(self, bars: List[Bar], std_threshold: float = 5.0) -> List[Bar]:
        """去除异常值（价格突变超过N个标准差）"""
        if len(bars) < 20:
            return bars

        closes = np.array([b.close for b in bars])
        returns = np.diff(closes) / closes[:-1]

        mean_ret = np.nanmean(returns)
        std_ret = np.nanstd(returns)

        if std_ret == 0:
            return bars

        clean_indices = [0]  # 第一根保留
        for i in range(1, len(returns)):
            if abs(returns[i] - mean_ret) <= std_threshold * std_ret:
                clean_indices.append(i)
        clean_indices.append(len(bars) - 1)

        return [bars[i] for i in sorted(set(clean_indices))]

    def _remove_duplicates(self, bars: List[Bar]) -> List[Bar]:
        """去除重复K线"""
        seen = set()
        unique = []
        for b in bars:
            key = (b.symbol, b.time.isoformat(), b.timeframe.value)
            if key not in seen:
                seen.add(key)
                unique.append(b)
        return unique

    def _fill_gaps(self, bars: List[Bar]) -> List[Bar]:
        """填充缺失的K线"""
        if len(bars) < 2:
            return bars

        filled = [bars[0]]
        for i in range(1, len(bars)):
            prev = filled[-1]
            curr = bars[i]

            # 计算缺失的K线数量
            time_diff = (curr.time - prev.time).total_seconds()
            expected_interval = self._get_expected_interval(prev.timeframe)
            if expected_interval <= 0:
                filled.append(curr)
                continue

            missing_count = int(time_diff / expected_interval) - 1
            if 0 < missing_count <= 5:  # 最多填充5根
                for j in range(1, missing_count + 1):
                    fill_time = prev.time + timedelta(seconds=expected_interval * j)
                    filled.append(Bar(
                        symbol=prev.symbol,
                        time=fill_time,
                        timeframe=prev.timeframe,
                        open=prev.close,
                        high=prev.close,
                        low=prev.close,
                        close=prev.close,
                        volume=0.0,
                        amount=0.0,
                    ))
            filled.append(curr)

        return filled

    def _get_expected_interval(self, timeframe: TimeFrame) -> float:
        """获取预期K线间隔（秒）"""
        mapping = {
            TimeFrame.MIN1: 60,
            TimeFrame.MIN5: 300,
            TimeFrame.MIN15: 900,
            TimeFrame.MIN30: 1800,
            TimeFrame.MIN60: 3600,
            TimeFrame.DAILY: 86400,
            TimeFrame.WEEKLY: 604800,
        }
        return mapping.get(timeframe, 0)


class DataAligner:
    """数据对齐器：对齐多标的K线时间"""

    @staticmethod
    def align_bars(
        bar_dict: dict,
        method: str = "ffill",
    ) -> dict:
        """
        对齐多个标的时间轴
        Args:
            bar_dict: {symbol: [Bar, ...]}
            method: 对齐方法 (ffill/bfill/nearest)
        Returns:
            {symbol: [Bar, ...]}
        """
        # 构建DataFrame
        dfs = {}
        for symbol, bars in bar_dict.items():
            df = pd.DataFrame([{
                "time": b.time,
                "open": b.open, "high": b.high,
                "low": b.low, "close": b.close,
                "volume": b.volume, "amount": b.amount,
            } for b in bars])
            df.set_index("time", inplace=True)
            dfs[symbol] = df

        all_times = sorted(set(
            t for df in dfs.values() for t in df.index
        ))
        full_index = pd.DatetimeIndex(all_times)

        result = {}
        for symbol, df in dfs.items():
            df = df.reindex(full_index)
            if method == "ffill":
                df = df.ffill()
            elif method == "bfill":
                df = df.bfill()
            df = df.dropna()

            result[symbol] = [
                Bar(
                    symbol=symbol, time=idx,
                    timeframe=bar_dict[symbol][0].timeframe,
                    open=row["open"], high=row["high"],
                    low=row["low"], close=row["close"],
                    volume=row["volume"], amount=row["amount"],
                )
                for idx, row in df.iterrows()
            ]

        return result


class DataResampler:
    """周期转换器：将K线从一个周期转换为另一周期"""

    RESAMPLE_RULES = {
        TimeFrame.MIN1: {"1min": TimeFrame.MIN5, "5min": TimeFrame.MIN15},
        TimeFrame.MIN5: {"5min": TimeFrame.MIN15, "15min": TimeFrame.MIN60},
        TimeFrame.MIN15: {"15min": TimeFrame.MIN60, "60min": TimeFrame.DAILY},
        TimeFrame.MIN30: {"30min": TimeFrame.DAILY},
        TimeFrame.MIN60: {"60min": TimeFrame.DAILY, "daily": TimeFrame.DAILY},
        TimeFrame.DAILY: {"daily": TimeFrame.WEEKLY, "weekly": TimeFrame.MONTHLY},
    }

    @staticmethod
    def resample(bars: List[Bar], target: TimeFrame) -> List[Bar]:
        """将K线转换到目标周期"""
        if not bars:
            return bars

        timeframe = bars[0].timeframe
        symbol = bars[0].symbol

        # 构建OHLCV DataFrame
        df = pd.DataFrame([{
            "time": b.time, "open": b.open, "high": b.high,
            "low": b.low, "close": b.close,
            "volume": b.volume, "amount": b.amount,
        } for b in bars])
        df.set_index("time", inplace=True)
        df.sort_index(inplace=True)

        # 聚合OHLCV
        rule_map = {
            TimeFrame.MIN5: "5min", TimeFrame.MIN15: "15min",
            TimeFrame.MIN30: "30min", TimeFrame.MIN60: "60min",
            TimeFrame.DAILY: "D", TimeFrame.WEEKLY: "W",
            TimeFrame.MONTHLY: "ME",
        }
        rule = rule_map.get(target)
        if rule is None:
            raise DataValidationError(f"不支持的目标周期: {target}")

        ohlc_dict = {
            "open": "first", "high": "max",
            "low": "min", "close": "last",
            "volume": "sum", "amount": "sum",
        }
        resampled = df.resample(rule).agg(ohlc_dict).dropna()

        return [
            Bar(
                symbol=symbol, time=idx, timeframe=target,
                open=row["open"], high=row["high"],
                low=row["low"], close=row["close"],
                volume=row["volume"], amount=row["amount"],
            )
            for idx, row in resampled.iterrows()
        ]

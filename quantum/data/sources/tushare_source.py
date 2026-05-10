"""
Tushare数据源实现 - 专业级A股数据源
"""

import time
from datetime import datetime
from typing import List, Optional, Dict, Any

from ...common.models import Bar, Tick
from ...common.enums import TimeFrame
from ...common.exceptions import DataSourceError
from ..base import DataSource


class TushareDataSource(DataSource):
    """
    Tushare Pro 数据源
    需要token，数据质量高
    文档: https://tushare.pro/
    """

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.token = config.get("token", "")
        self._pro = None
        self.max_retries = config.get("max_retries", 3)
        self.retry_delay = config.get("retry_delay", 2)

    def _lazy_import(self):
        if self._pro is None:
            try:
                import tushare as ts
                ts.set_token(self.token)
                self._pro = ts.pro_api()
            except ImportError:
                raise DataSourceError("请安装 tushare: pip install tushare")

    def _normalize_symbol(self, symbol: str) -> str:
        """标准化为TuShare格式"""
        return symbol

    def get_bars(
        self,
        symbol: str,
        start: datetime,
        end: datetime,
        timeframe: TimeFrame = TimeFrame.DAILY,
        **kwargs
    ) -> List[Bar]:
        self._lazy_import()
        pro = self._pro
        bars = []
        code = self._normalize_symbol(symbol)
        start_str = start.strftime("%Y%m%d")
        end_str = end.strftime("%Y%m%d")

        try:
            if timeframe == TimeFrame.DAILY:
                df = pro.daily(
                    ts_code=code,
                    start_date=start_str,
                    end_date=end_str,
                )
            elif timeframe in [TimeFrame.MIN1, TimeFrame.MIN5, TimeFrame.MIN15,
                                TimeFrame.MIN30, TimeFrame.MIN60]:
                freq_map = {
                    TimeFrame.MIN1: "1min",
                    TimeFrame.MIN5: "5min",
                    TimeFrame.MIN15: "15min",
                    TimeFrame.MIN30: "30min",
                    TimeFrame.MIN60: "60min",
                }
                df = pro.stk_mins(
                    ts_code=code,
                    start_date=start_str,
                    end_date=end_str,
                    freq=freq_map[timeframe],
                )
            else:
                raise DataSourceError(f"不支持的周期: {timeframe}")

            if df is None or df.empty:
                return bars

            for _, row in df.iterrows():
                try:
                    bar = Bar(
                        symbol=symbol,
                        time=row.get("trade_date") or row.get("trade_time"),
                        timeframe=timeframe,
                        open=float(row.get("open", 0)),
                        high=float(row.get("high", 0)),
                        low=float(row.get("low", 0)),
                        close=float(row.get("close", 0)),
                        volume=float(row.get("vol", 0)),
                        amount=float(row.get("amount", 0)),
                    )
                    bars.append(bar)
                except (ValueError, TypeError):
                    continue

            bars.sort(key=lambda b: b.time)
            return bars

        except Exception as e:
            raise DataSourceError(f"TuShare获取 {symbol} 数据失败: {e}")

    def get_tick(self, symbol: str, date: datetime) -> List[Tick]:
        return []  # TuShare免费版不支持Tick

    def get_realtime_bar(self, symbol: str, timeframe: TimeFrame = TimeFrame.MIN1) -> Optional[Bar]:
        self._lazy_import()
        pro = self._pro
        try:
            code = self._normalize_symbol(symbol)
            df = pro.realtime_tick(ts_code=code)
            if df is None or df.empty:
                return None
            row = df.iloc[0]
            return Bar(
                symbol=symbol,
                time=datetime.now(),
                timeframe=timeframe,
                open=float(row.get("open", 0)),
                high=float(row.get("high", 0)),
                low=float(row.get("low", 0)),
                close=float(row.get("price", row.get("close", 0))),
                volume=float(row.get("volume", 0)),
                amount=float(row.get("amount", 0)),
            )
        except Exception:
            return None

    def health_check(self) -> bool:
        try:
            self._lazy_import()
            return bool(self.token)
        except Exception:
            return False

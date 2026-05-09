"""
AKShare 数据源实现 - 免费A股数据源
"""

import time
from datetime import datetime, timedelta
from typing import List, Optional, Dict, Any
from ..common.models import Bar, Tick
from ..common.enums import TimeFrame
from ..common.exceptions import DataSourceError
from .base import DataSource


class AKShareDataSource(DataSource):
    """
    AKShare 数据源
    免费、无需token、覆盖全面
    文档: https://akshare.akfamily.xyz/
    """

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self._akshare = None
        self.max_retries = config.get("max_retries", 3)
        self.retry_delay = config.get("retry_delay", 2)
        self.rate_limit_delay = config.get("rate_limit_delay", 0.5)

    def _lazy_import(self):
        """延迟导入akshare"""
        if self._akshare is None:
            try:
                import akshare as ak
                self._akshare = ak
            except ImportError:
                raise DataSourceError("请安装 akshare: pip install akshare")

    def _sleep(self):
        """请求限速"""
        time.sleep(self.rate_limit_delay)

    def _normalize_symbol(self, symbol: str) -> str:
        """标准化股票代码为AKShare格式"""
        # 如果已经是6位数字，添加后缀
        if symbol.isdigit() and len(symbol) == 6:
            if symbol.startswith(('6', '9')):
                return f"{symbol}"  # 沪市
            elif symbol.startswith(('0', '3')):
                return f"{symbol}"  # 深市
            elif symbol.startswith(('4', '8')):
                return f"{symbol}"  # 北交所
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
        ak = self._akshare
        bars = []

        try:
            start_str = start.strftime("%Y%m%d")
            end_str = end.strftime("%Y%m%d")
            code = self._normalize_symbol(symbol)

            if timeframe == TimeFrame.DAILY:
                df = ak.stock_zh_a_hist(
                    symbol=code,
                    period="daily",
                    start_date=start_str,
                    end_date=end_str,
                    adjust="qfq",  # 前复权
                )
            elif timeframe in [TimeFrame.MIN1, TimeFrame.MIN5, TimeFrame.MIN15,
                               TimeFrame.MIN30, TimeFrame.MIN60]:
                period_map = {
                    TimeFrame.MIN1: "1",
                    TimeFrame.MIN5: "5",
                    TimeFrame.MIN15: "15",
                    TimeFrame.MIN30: "30",
                    TimeFrame.MIN60: "60",
                }
                df = ak.stock_zh_a_hist_min_em(
                    symbol=code,
                    period=period_map[timeframe],
                    start_date=start_str,
                    end_date=end_str,
                    adjust="qfq",
                )
            else:
                raise DataSourceError(f"不支持的周期: {timeframe}")

            if df is None or df.empty:
                return bars

            # 统一列名并转换
            self._sleep()
            for _, row in df.iterrows():
                try:
                    bar = Bar(
                        symbol=symbol,
                        time=row.get("日期") or row.get("时间"),
                        timeframe=timeframe,
                        open=float(row.get("开盘", row.get("开盘价", 0))),
                        high=float(row.get("最高", row.get("最高价", 0))),
                        low=float(row.get("最低", row.get("最低价", 0))),
                        close=float(row.get("收盘", row.get("收盘价", 0))),
                        volume=float(row.get("成交量", 0)),
                        amount=float(row.get("成交额", 0)),
                    )
                    bars.append(bar)
                except (ValueError, KeyError) as e:
                    continue

            # 按时间排序
            bars.sort(key=lambda b: b.time)
            return bars

        except Exception as e:
            raise DataSourceError(f"AKShare获取 {symbol} 日线数据失败: {e}")

    def get_tick(self, symbol: str, date: datetime) -> List[Tick]:
        """获取Tick数据（AKShare不原生支持，返回空列表）"""
        self._lazy_import()
        ak = self._akshare

        try:
            date_str = date.strftime("%Y%m%d")
            code = self._normalize_symbol(symbol)
            df = ak.stock_zh_a_tick_tx(code, trade_date=date_str)

            if df is None or df.empty:
                return []

            ticks = []
            for _, row in df.iterrows():
                try:
                    tick = Tick(
                        symbol=symbol,
                        time=row.get("成交时间"),
                        price=float(row.get("成交价", 0)),
                        volume=int(row.get("成交量", 0)),
                        amount=float(row.get("成交金额", 0)),
                        direction=str(row.get("买卖方向", "")),
                    )
                    ticks.append(tick)
                except (ValueError, KeyError):
                    continue

            return ticks

        except Exception as e:
            raise DataSourceError(f"AKShare获取 {symbol} Tick数据失败: {e}")

    def get_realtime_bar(
        self,
        symbol: str,
        timeframe: TimeFrame = TimeFrame.MIN1,
    ) -> Optional[Bar]:
        """获取实时K线"""
        self._lazy_import()
        ak = self._akshare

        try:
            code = self._normalize_symbol(symbol)
            # 获取实时行情
            df = ak.stock_zh_a_spot_em()

            if df is None or df.empty:
                return None

            # 筛选目标股票
            row = df[df["代码"] == code]
            if row.empty:
                # 尝试通过名称匹配
                row = df[df["代码"].str.contains(code)]
                if row.empty:
                    return None

            row = row.iloc[0]
            return Bar(
                symbol=symbol,
                time=datetime.now(),
                timeframe=timeframe,
                open=float(row.get("今开", 0)),
                high=float(row.get("最高", 0)),
                low=float(row.get("最低", 0)),
                close=float(row.get("最新价", 0)),
                volume=float(row.get("成交量", 0)),
                amount=float(row.get("成交额", 0)),
            )

        except Exception as e:
            raise DataSourceError(f"AKShare获取实时行情失败: {e}")

    def get_universe(self, category: str = "all") -> List[str]:
        """获取A股全部股票列表"""
        self._lazy_import()
        ak = self._akshare

        try:
            df = ak.stock_zh_a_spot_em()
            if df is None or df.empty:
                return []
            return df["代码"].tolist()
        except Exception as e:
            raise DataSourceError(f"获取股票列表失败: {e}")

    def health_check(self) -> bool:
        """检查数据源是否可用"""
        try:
            self._lazy_import()
            return True
        except ImportError:
            return False

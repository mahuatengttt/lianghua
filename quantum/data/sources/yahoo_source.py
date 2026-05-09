"""
雅虎财经数据源 - 无需token，免费
支持全球股票市场数据
"""

import time
import json
import ssl
import urllib.request
from datetime import datetime, timedelta
from typing import List, Optional, Dict, Any, Tuple
from quantum.common.models import Bar, Tick
from quantum.common.enums import TimeFrame
from quantum.common.exceptions import DataSourceError
from quantum.data.base import DataSource


class YahooFinanceDataSource(DataSource):
    """
    雅虎财经数据源
    免费、无需token、覆盖全球市场
    数据范围：约2年历史日线
    """

    BASE_URL = "https://query1.finance.yahoo.com/v8/finance/chart"

    # 雅虎code映射
    MARKET_MAP = {
        "6": ".SS",   # 沪市 600/601/603/605/688
        "9": ".SS",
        "0": ".SZ",   # 深市 000/001/002/003
        "3": ".SZ",
        "4": ".BJ",   # 北交所
        "8": ".BJ",
    }

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.max_retries = config.get("max_retries", 3)
        self.retry_delay = config.get("retry_delay", 2)
        self.rate_limit_delay = config.get("rate_limit_delay", 0.5)
        self._ssl_ctx = self._create_ssl_context()

    def _create_ssl_context(self):
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx

    def _normalize_symbol(self, symbol: str) -> str:
        """
        将A股代码转为雅虎格式
        000001 -> 000001.SZ
        600519 -> 600519.SS
        """
        code = symbol.strip()
        # 去除可能的前缀
        for prefix in ["SH", "SZ", "sh", "sz", "SH.", "SZ.", "."]:
            if code.startswith(prefix):
                code = code.replace(prefix, "", 1)
                break

        # 如果是纯6位数字+后缀，直接返回
        if "." in code:
            return code.upper()

        # 纯6位数字，根据首位判断市场
        if code.isdigit() and len(code) == 6:
            prefix = code[0]
            suffix = self.MARKET_MAP.get(prefix, "")
            if suffix:
                return f"{code}{suffix}"
            return code

        return code

    def _request(self, url: str) -> dict:
        """发送HTTP请求"""
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                )
            }
        )
        resp = urllib.request.urlopen(req, context=self._ssl_ctx, timeout=15)
        return json.loads(resp.read())

    def get_bars(
        self,
        symbol: str,
        start: datetime,
        end: datetime,
        timeframe: TimeFrame = TimeFrame.DAILY,
        **kwargs
    ) -> List[Bar]:
        """
        获取历史K线数据
        雅虎只支持日线（最大2年范围）
        """
        yahoo_code = self._normalize_symbol(symbol)

        # 计算时间范围
        range_str = self._calc_range(start, end)

        if timeframe == TimeFrame.DAILY:
            return self._get_daily_bars(symbol, yahoo_code, range_str)
        else:
            # 雅虎不支持分钟线，报错提示
            raise DataSourceError(
                f"雅虎数据源仅支持日线(DAILY)，不支持 {timeframe.value}"
            )

    def _calc_range(self, start: datetime, end: datetime) -> str:
        """计算雅虎的range参数"""
        days = (end - start).days
        if days <= 30:
            return "1mo"
        elif days <= 90:
            return "3mo"
        elif days <= 180:
            return "6mo"
        elif days <= 365:
            return "1y"
        elif days <= 730:
            return "2y"
        else:
            return "max"

    def _get_daily_bars(
        self, symbol: str, yahoo_code: str, range_str: str
    ) -> List[Bar]:
        """获取日线数据"""
        url = f"{self.BASE_URL}/{yahoo_code}?range={range_str}&interval=1d"

        for attempt in range(self.max_retries):
            try:
                data = self._request(url)
                result = data.get("chart", {}).get("result", [{}])[0]
                meta = result.get("meta", {})

                timestamps = result.get("timestamp", [])
                quotes = result.get("indicators", {}).get("quote", [{}])[0]
                adjclose = result.get("indicators", {}).get("adjclose", [{}])[0]

                adj_closes = adjclose.get("adjclose", []) if adjclose else []

                bars = []
                for i in range(len(timestamps)):
                    o = quotes.get("open", [None] * len(timestamps))[i]
                    h = quotes.get("high", [None] * len(timestamps))[i]
                    l = quotes.get("low", [None] * len(timestamps))[i]
                    c = quotes.get("close", [None] * len(timestamps))[i]
                    v = quotes.get("volume", [None] * len(timestamps))[i]

                    if o is None or h is None or l is None or c is None:
                        continue

                    o, h, l, c = float(o), float(h), float(l), float(c)
                    v = float(v) if v else 0.0

                    bar_time = datetime.fromtimestamp(timestamps[i])

                    bar = Bar(
                        symbol=symbol,
                        time=bar_time,
                        timeframe=TimeFrame.DAILY,
                        open=o,
                        high=h,
                        low=l,
                        close=c,
                        volume=v,
                        amount=v * c,
                    )
                    bars.append(bar)

                if not bars:
                    raise DataSourceError(f"雅虎返回数据为空: {yahoo_code}")

                self._sleep()
                return bars

            except urllib.request.HTTPError as e:
                if e.code == 404:
                    raise DataSourceError(f"标的未找到: {yahoo_code}")
                elif e.code == 429:
                    wait = self.retry_delay * (2 ** attempt)
                    time.sleep(wait)
                    continue
                else:
                    raise DataSourceError(f"HTTP {e.code}: {yahoo_code}")
            except urllib.request.URLError as e:
                if attempt < self.max_retries - 1:
                    time.sleep(self.retry_delay * (2 ** attempt))
                    continue
                raise DataSourceError(f"雅虎请求失败: {e.reason}")
            except (KeyError, IndexError, ValueError) as e:
                raise DataSourceError(f"雅虎数据解析失败: {e}")

        raise DataSourceError(f"雅虎获取数据失败(重试{self.max_retries}次): {yahoo_code}")

    def get_tick(self, symbol: str, date: datetime) -> List[Tick]:
        """雅虎不支持Tick数据"""
        return []

    def get_realtime_bar(
        self,
        symbol: str,
        timeframe: TimeFrame = TimeFrame.MIN1,
    ) -> Optional[Bar]:
        """雅虎实时行情（通过1d范围取最新日线）"""
        yahoo_code = self._normalize_symbol(symbol)
        url = f"{self.BASE_URL}/{yahoo_code}?range=1d&interval=1d"

        try:
            data = self._request(url)
            result = data.get("chart", {}).get("result", [{}])[0]
            meta = result.get("meta", {})
            regular_price = meta.get("regularMarketPrice")

            if regular_price is None:
                return None

            previous_close = meta.get("previousClose", regular_price)
            chart_prev_close = meta.get("chartPreviousClose", previous_close)

            # 构建一个粗略的当日Bar
            return Bar(
                symbol=symbol,
                time=datetime.now(),
                timeframe=timeframe,
                open=float(chart_prev_close),
                high=float(regular_price),
                low=float(regular_price),
                close=float(regular_price),
                volume=0,
                amount=0,
            )
        except Exception:
            return None

    def get_universe(self, category: str = "all") -> List[str]:
        """雅虎不支持批量列表"""
        return []

    def health_check(self) -> bool:
        """检查雅虎API是否可达"""
        try:
            self._request(f"{self.BASE_URL}/600519.SS?range=1d&interval=1d")
            return True
        except Exception:
            return False

    def _sleep(self):
        """请求限速"""
        time.sleep(self.rate_limit_delay)

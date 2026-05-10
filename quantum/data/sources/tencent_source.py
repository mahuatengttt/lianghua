"""
腾讯财经数据源 - 基于腾讯股票行情接口
纯HTTP，无需Token，服务器可直接访问
"""

import time
import json
import urllib.request
from datetime import datetime, timedelta
from typing import List, Optional, Dict, Any, Tuple

from ...common.models import Bar, Tick
from ...common.enums import TimeFrame
from ...common.exceptions import DataSourceError
from ..base import DataSource


class TencentDataSource(DataSource):
    """
    腾讯财经数据源
    - 免费，无需 Token
    - 纯 HTTP（非 HTTPS），规避 SSL 握手问题
    - 覆盖 A 股、港股、美股
    - 支持日线和实时行情
    """

    # 代码转换映射
    MARKET_MAP = {
        "6": "sh",   # 600/601/603/605/688 → 沪市
        "9": "sh",
        "0": "sz",   # 000/001/002 → 深市
        "3": "sz",   # 300 → 创业板
        "4": "bj",   # 北交所
        "8": "bj",
    }

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.max_retries = config.get("max_retries", 3)
        self.retry_delay = config.get("retry_delay", 2)
        self.rate_limit_delay = config.get("rate_limit_delay", 0.3)

    def _normalize(self, symbol: str) -> Tuple[str, str]:
        """
        将A股代码转为腾讯格式
        返回: (tencent_code, original_code)
        001309 → sz001309
        600519 → sh600519
        """
        code = symbol.strip().upper()
        # 去掉已有后缀
        for suf in [".SZ", ".SS", ".BJ", ".SH"]:
            if code.endswith(suf):
                code = code[:-len(suf)]
                break

        if code.isdigit() and len(code) == 6:
            prefix = self.MARKET_MAP.get(code[0], "")
            if prefix:
                return f"{prefix}{code}", code
        return code, code  # 非A股直接返回

    def _request_text(self, url: str) -> str:
        """发起 HTTP 请求（text）"""
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
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.read().decode("gbk")

    def _request_json(self, url: str) -> dict:
        """发起 HTTP 请求（json）"""
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
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read())

    def _throttle(self):
        """请求限速"""
        if not hasattr(self, '_last_request'):
            self._last_request = 0.0
        elapsed = time.time() - self._last_request
        if elapsed < self.rate_limit_delay:
            time.sleep(self.rate_limit_delay - elapsed)
        self._last_request = time.time()

    def get_bars(
        self,
        symbol: str,
        start: datetime,
        end: datetime,
        timeframe: TimeFrame = TimeFrame.DAILY,
        **kwargs
    ) -> List[Bar]:
        """获取K线数据（目前仅支持日线）"""
        if timeframe != TimeFrame.DAILY:
            raise DataSourceError(f"腾讯数据源仅支持日线，不支持 {timeframe.value}")

        tc, _ = self._normalize(symbol)
        days = (end - start).days
        limit = max(min(days + 30, 1000), 30)  # 至少30，最多1000根

        for attempt in range(self.max_retries):
            try:
                self._throttle()
                return self._get_daily_bars(symbol, tc, limit, start, end)
            except Exception as e:
                if attempt < self.max_retries - 1:
                    time.sleep(self.retry_delay * (2 ** attempt))
                    continue
                raise DataSourceError(f"腾讯获取 {symbol} 日线失败: {e}")

    def _get_daily_bars(
        self, symbol: str, tc: str, limit: int,
        start: datetime, end: datetime
    ) -> List[Bar]:
        """获取日线K线"""
        url = (
            f"http://ifzq.gtimg.cn/appstock/app/fqkline/get?"
            f"param={tc},day,,,{limit},qfq"
        )
        data = self._request_json(url)

        # 解析 JSON
        if data.get("code") != 0:
            raise DataSourceError(f"腾讯返回错误: code={data.get('code')}")

        # 找到股票数据
        stock_data = data.get("data", {})
        # key 可能是 "sh600519" 或 "sz001309"
        item = None
        for key in stock_data:
            if tc in key or tc[2:] in key:
                item = stock_data[key]
                break

        if not item:
            raise DataSourceError(f"未找到 {tc} 的数据")

        # 取前复权日线
        kline = item.get("qfqday") or item.get("day")
        if not kline:
            raise DataSourceError(f"腾讯K线数据为空: {tc}")

        bars = []
        for row in kline:
            if len(row) < 6:
                continue
            bar_date_str = row[0]
            if isinstance(bar_date_str, str) and "-" in bar_date_str:
                try:
                    bar_time = datetime.strptime(bar_date_str, "%Y-%m-%d")
                except ValueError:
                    continue
            else:
                continue

            if bar_time < start or bar_time > end:
                continue

            # format: [date, open, close, high, low, volume]
            o, c, h, l = float(row[1]), float(row[2]), float(row[3]), float(row[4])
            v = float(row[5]) if row[5] else 0.0

            bar = Bar(
                symbol=symbol,
                time=bar_time,
                timeframe=TimeFrame.DAILY,
                open=o,
                high=h if h >= max(o, c) else max(o, c),
                low=l if l <= min(o, c) else min(o, c),
                close=c,
                volume=v,
                amount=v * c,
            )
            bars.append(bar)

        bars.sort(key=lambda b: b.time)
        return bars

    def get_realtime_bar(
        self,
        symbol: str,
        timeframe: TimeFrame = TimeFrame.DAILY,
    ) -> Optional[Bar]:
        """获取实时行情（腾讯实时接口）"""
        tc, code = self._normalize(symbol)

        try:
            self._throttle()
            raw = self._request_text(f"http://qt.gtimg.cn/q={tc}")

            if "=" not in raw:
                return None

            content = raw.split("=", 1)[1].strip('"').strip(";\n")
            fields = content.split("~")

            if len(fields) < 30:
                return None

            # 腾讯实时行情字段解析
            name = fields[1]
            code_display = fields[2]
            current_price = self._safe_float(fields[3], 0.0)
            prev_close = self._safe_float(fields[4], 0.0)
            open_price = self._safe_float(fields[5], 0.0)
            # fields[6]=成交量(手), 转成股
            volume_hand = self._safe_float(fields[6], 0.0)
            high = self._safe_float(fields[33], 0.0)
            low = self._safe_float(fields[34], 0.0)

            volume = volume_hand * 100  # 手转股

            return Bar(
                symbol=symbol,
                time=datetime.now(),
                timeframe=timeframe,
                open=open_price if open_price > 0 else current_price,
                high=high if high > current_price else current_price,
                low=low if low > 0 and low < current_price else current_price,
                close=current_price,
                volume=volume,
                amount=volume * current_price,
            )

        except Exception as e:
            # 实时行情失败不抛异常，返回 None
            return None

    def get_tick(self, symbol: str, date: datetime) -> List[Tick]:
        """不支持Tick"""
        return []

    def get_realtime_quote(self, symbols: List[str]) -> Dict[str, dict]:
        """
        批量获取多个股票的实时行情
        返回: {symbol: {name, price, change, change_pct, ...}}
        """
        codes = [self._normalize(s)[0] for s in symbols]
        query = ",".join(codes)

        try:
            self._throttle()
            raw = self._request_text(f"http://qt.gtimg.cn/q={query}")
        except Exception:
            return {}

        result = {}
        for line in raw.strip().split(";"):
            if "=" not in line:
                continue
            content = line.split("=", 1)[1].strip('"')
            fields = content.split("~")
            if len(fields) < 40:
                continue

            symbol = fields[2]
            # 转回标准格式
            if symbol.isdigit() and len(symbol) == 6:
                key = symbol
            else:
                key = symbol

            result[key] = {
                "name": fields[1],
                "code": fields[2],
                "price": self._safe_float(fields[3], 0.0),
                "prev_close": self._safe_float(fields[4], 0.0),
                "open": self._safe_float(fields[5], 0.0),
                "volume": self._safe_float(fields[6], 0.0) * 100,
                "high": self._safe_float(fields[33], 0.0),
                "low": self._safe_float(fields[34], 0.0),
                "change": self._safe_float(fields[31], 0.0),
                "change_pct": self._safe_float(fields[32], 0.0),
                "turnover_rate": self._safe_float(fields[38], 0.0),
                "pe": self._safe_float(fields[39], 0.0),
                "amplitude": self._safe_float(fields[43], 0.0),
                "circulating_market_cap": self._safe_float(fields[44], 0.0),
                "total_market_cap": self._safe_float(fields[45], 0.0),
            }

        return result

    def get_universe(self, category: str = "all") -> List[str]:
        """腾讯不支持批量列表，返回空"""
        return []

    def health_check(self) -> bool:
        """检查腾讯接口是否可达"""
        try:
            raw = self._request_text("http://qt.gtimg.cn/q=sh600519")
            return "茅台" in raw or "ę́" in raw
        except Exception:
            return False

    @staticmethod
    def _safe_float(val, default=0.0) -> float:
        """安全转浮点"""
        try:
            return float(val)
        except (ValueError, TypeError):
            return default

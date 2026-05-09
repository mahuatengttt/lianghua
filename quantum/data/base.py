"""
数据源抽象基类
"""

from abc import ABC, abstractmethod
from datetime import datetime
from typing import List, Optional, Dict, Any
from ..common.models import Bar, Tick
from ..common.enums import TimeFrame
from ..common.exceptions import DataSourceError


class DataSource(ABC):
    """数据源抽象基类"""

    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.name = config.get("name", self.__class__.__name__)

    @abstractmethod
    def get_bars(
        self,
        symbol: str,
        start: datetime,
        end: datetime,
        timeframe: TimeFrame = TimeFrame.DAILY,
        **kwargs
    ) -> List[Bar]:
        """获取历史K线数据"""
        pass

    @abstractmethod
    def get_tick(
        self,
        symbol: str,
        date: datetime,
    ) -> List[Tick]:
        """获取Tick数据"""
        pass

    @abstractmethod
    def get_realtime_bar(
        self,
        symbol: str,
        timeframe: TimeFrame = TimeFrame.MIN1,
    ) -> Optional[Bar]:
        """获取实时K线"""
        pass

    def get_universe(self, category: str = "all") -> List[str]:
        """获取标的列表"""
        return []

    def health_check(self) -> bool:
        """检查数据源是否可用"""
        return True


class DataManager:
    """
    数据管理器：统一管理多个数据源和存储
    支持多级缓存：内存 → 本地存储 → 远程数据源
    """

    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.sources: Dict[str, DataSource] = {}
        self.stores: Dict[str, 'DataStore'] = {}
        self._bar_cache: Dict[str, List[Bar]] = {}

    def register_source(self, name: str, source: DataSource):
        """注册数据源"""
        self.sources[name] = source

    def register_store(self, name: str, store: 'DataStore'):
        """注册存储"""
        self.stores[name] = store

    def get_data(
        self,
        symbol: str,
        start: datetime,
        end: datetime,
        timeframe: TimeFrame = TimeFrame.DAILY,
        source_name: Optional[str] = None,
        use_cache: bool = True,
    ) -> List[Bar]:
        """
        获取数据：先查缓存 → 再查存储 → 最后查数据源
        """
        cache_key = f"{symbol}_{timeframe.value}"

        # 1. 检查内存缓存
        if use_cache and cache_key in self._bar_cache:
            bars = self._bar_cache[cache_key]
            filtered = [b for b in bars if start <= b.time <= end]
            if filtered:
                return filtered

        # 2. 检查本地存储
        if self.stores:
            for store in self.stores.values():
                try:
                    bars = store.load(symbol, start, end, timeframe)
                    if bars:
                        self._bar_cache[cache_key] = bars
                        return bars
                except Exception:
                    continue

        # 3. 从远程数据源获取
        sources_to_try = (
            [self.sources[source_name]] if source_name and source_name in self.sources
            else list(self.sources.values())
        )

        for source in sources_to_try:
            try:
                bars = source.get_bars(symbol, start, end, timeframe)
                if bars:
                    self._bar_cache[cache_key] = bars
                    # 保存到本地存储
                    if self.stores:
                        for store in self.stores.values():
                            try:
                                store.save(bars)
                            except Exception:
                                pass
                    return bars
            except Exception as e:
                raise DataSourceError(f"从 {source.name} 获取数据失败: {e}")

        raise DataSourceError(f"所有数据源均无法获取 {symbol} 的数据")

    def refresh_cache(self, symbol: str, timeframe: TimeFrame):
        """清除指定缓存"""
        cache_key = f"{symbol}_{timeframe.value}"
        self._bar_cache.pop(cache_key, None)


class DataStore(ABC):
    """数据存储抽象基类"""

    @abstractmethod
    def save(self, bars: List[Bar]) -> None:
        pass

    @abstractmethod
    def load(
        self, symbol: str, start: datetime, end: datetime, timeframe: TimeFrame
    ) -> Optional[List[Bar]]:
        pass

    @abstractmethod
    def exists(self, symbol: str, timeframe: TimeFrame) -> bool:
        pass

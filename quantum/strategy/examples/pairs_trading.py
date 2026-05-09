"""
配对交易策略 - 统计套利
"""

from typing import List, Optional, Dict, Any

from ..base import BaseStrategy
from ..signals.arbitrage import PairsTradingSignalGenerator
from ...common.models import Bar, Signal, StrategyConfig
from ...common.enums import SignalAction, TimeFrame, StrategyCategory


class PairsTradingStrategy(BaseStrategy):
    """
    配对交易策略
    原理：两只同行业高相关性股票，价差偏离均值时开仓，回归时平仓
    适用于融资融券标的
    """

    def __init__(self, config: Optional[StrategyConfig] = None):
        if config is None:
            config = StrategyConfig(
                name="Pairs_Trading",
                category=StrategyCategory.ARBITRAGE,
                symbols=[],
                parameters={
                    "pair": [],
                    "zscore_entry": 2.0,
                    "zscore_exit": 0.5,
                    "window": 60,
                    "cointegration_pvalue": 0.05,
                },
                timeframe=TimeFrame.DAILY,
            )
        super().__init__(config)
        self.signal_gen = PairsTradingSignalGenerator({
            "pair": config.parameters.get("pair", []),
            "zscore_entry": config.parameters.get("zscore_entry", 2.0),
            "zscore_exit": config.parameters.get("zscore_exit", 0.5),
            "window": config.parameters.get("window", 60),
            "cointegration_pvalue": config.parameters.get("cointegration_pvalue", 0.05),
        })

    def setup(self):
        pair = self.signal_gen.pair
        self.log(f"配对交易策略初始化: {pair[0]} - {pair[1]}")
        self.log(f"Z-Score入场: {self.signal_gen.zscore_entry}σ, 出场: {self.signal_gen.zscore_exit}σ")

    def on_bar(self, bar: Bar) -> Optional[Signal]:
        """配对交易需要两个股票的数据同时到达"""
        # 收集两个股票的历史数据
        pair = self.signal_gen.pair
        if len(pair) != 2:
            return None

        bars_dict = {}
        for symbol in pair:
            if symbol not in self.bar_history:
                self.bar_history[symbol] = []
            if symbol == bar.symbol:
                self.bar_history[symbol].append(bar)
            bars_dict[symbol] = self.bar_history[symbol]

        # 确保两个都有足够数据
        if any(len(b) < self.signal_gen.window for b in bars_dict.values()):
            return None

        signals = self.signal_gen.compute(bars_dict)
        return signals[-1] if signals else None

    def teardown(self):
        self.log("配对交易策略停止")

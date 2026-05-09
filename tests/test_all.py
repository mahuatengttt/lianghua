"""
量子系统 - 单元测试
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import unittest
from datetime import datetime, timedelta
import numpy as np

from quantum.common.models import Bar, Signal, Order, Position, Portfolio
from quantum.common.enums import (
    OrderSide, OrderType, OrderStatus,
    TimeFrame, SignalAction, StrategyCategory,
)
from quantum.common.utils import (
    MathUtils, IndicatorUtils, DateTimeUtils,
)
from quantum.strategy.base import BaseStrategy, StrategyConfig
from quantum.strategy.signals.trend import TrendSignalGenerator
from quantum.strategy.signals.mean_reversion import MeanReversionSignalGenerator
from quantum.backtest.engine import BacktestEngine, BacktestEngineConfig
from quantum.risk.position_manager import PositionSizer
from quantum.risk.stop_loss import TrailingStopLoss


class TestCoreModels(unittest.TestCase):
    """测试核心数据模型"""

    def test_bar_creation(self):
        bar = Bar(
            symbol="000001",
            time=datetime(2024, 1, 1),
            timeframe=TimeFrame.DAILY,
            open=10.0, high=11.0, low=9.5, close=10.5,
            volume=1000000.0,
        )
        self.assertEqual(bar.symbol, "000001")
        self.assertEqual(bar.mid_price, 10.25)
        self.assertEqual(bar.range, 1.5)

    def test_signal_action(self):
        signal = Signal(
            timestamp=datetime.now(),
            symbol="000001",
            action=SignalAction.OPEN_LONG,
            price=10.0,
        )
        self.assertEqual(signal.action, SignalAction.OPEN_LONG)
        self.assertEqual(signal.confidence, 1.0)


class TestUtils(unittest.TestCase):
    """测试工具函数"""

    def test_sma(self):
        data = np.array([1, 2, 3, 4, 5, 6, 7, 8, 9, 10])
        sma = IndicatorUtils.sma(data, 3)
        self.assertTrue(np.isnan(sma[0]))
        self.assertTrue(np.isnan(sma[1]))
        self.assertAlmostEqual(sma[2], 2.0)
        self.assertAlmostEqual(sma[-1], 9.0)

    def test_rsi(self):
        # 持续上涨 → RSI接近100
        data = np.array([10 + i for i in range(20)])
        rsi = IndicatorUtils.rsi(data, 14)
        self.assertGreater(rsi[-1], 90)

        # 持续下跌 → RSI接近0
        data = np.array([30 - i for i in range(20)])
        rsi = IndicatorUtils.rsi(data, 14)
        self.assertLess(rsi[-1], 10)

    def test_max_drawdown(self):
        equity = [100, 110, 95, 105, 80, 90]
        dd = MathUtils.max_drawdown(equity)
        self.assertAlmostEqual(dd, 0.2727, 2)

    def test_sharpe_ratio(self):
        returns = [0.01] * 100 + [-0.01] * 100
        sharpe = MathUtils.sharpe_ratio(returns, 0.0, 252)
        self.assertAlmostEqual(sharpe, 0.0, 1)

    def test_round_quantity(self):
        self.assertEqual(MathUtils.round_quantity(150, 100), 100)
        self.assertEqual(MathUtils.round_quantity(199, 100), 100)
        self.assertEqual(MathUtils.round_quantity(200, 100), 200)


class TestTrendSignal(unittest.TestCase):
    """测试趋势信号生成"""

    def setUp(self):
        self.generator = TrendSignalGenerator({"method": "dual_ma", "fast_period": 5, "slow_period": 10})

    def _make_bars(self, prices):
        bars = []
        for i, p in enumerate(prices):
            bars.append(Bar(
                symbol="TEST",
                time=datetime(2024, 1, 1) + timedelta(days=i),
                timeframe=TimeFrame.DAILY,
                open=p * 0.99, high=p * 1.01, low=p * 0.99,
                close=p, volume=1000000.0,
            ))
        return bars

    def test_golden_cross(self):
        """上涨趋势应产生买入信号（逐根bar调用）"""
        prices = [10.0] * 25 + [10.1, 10.3, 10.6, 11.0, 11.5, 12.0, 12.5]
        bars = self._make_bars(prices)
        found = False
        for i in range(len(bars)):
            signals = self.generator.compute(bars[:i+1])
            if any(s.action == SignalAction.OPEN_LONG for s in signals):
                found = True
                break
        self.assertTrue(found, "上涨趋势应产生买入信号")

    def test_death_cross(self):
        """下跌趋势应产生卖出信号（逐根bar调用）"""
        prices = [10.0] * 25 + [9.9, 9.7, 9.4, 9.0, 8.5, 8.0, 7.8]
        bars = self._make_bars(prices)
        found = False
        for i in range(len(bars)):
            signals = self.generator.compute(bars[:i+1])
            if any(s.action in [SignalAction.CLOSE_LONG, SignalAction.OPEN_SHORT] for s in signals):
                found = True
                break
        self.assertTrue(found, "下跌趋势应产生卖出信号")


class TestMeanReversionSignal(unittest.TestCase):
    """测试均值回归信号"""

    def setUp(self):
        self.generator = MeanReversionSignalGenerator({"method": "rsi", "rsi_period": 14})

    def _make_bars(self, prices):
        bars = []
        for i, p in enumerate(prices):
            bars.append(Bar(
                symbol="TEST",
                time=datetime(2024, 1, 1) + timedelta(days=i),
                timeframe=TimeFrame.DAILY,
                open=p * 0.99, high=p * 1.01, low=p * 0.99,
                close=p, volume=1000000.0,
            ))
        return bars

    def test_oversold_signal(self):
        """超卖应产生买入信号"""
        # 大幅下跌
        prices = [10.0 * (0.95 ** i) for i in range(30)]
        bars = self._make_bars(prices)
        signals = self.generator.compute(bars)
        has_buy = any(s.action == SignalAction.OPEN_LONG for s in signals)
        has_rsi_reason = any("RSI超卖" in (s.reason or "") for s in signals)
        self.assertTrue(has_buy or has_rsi_reason, "超卖应产生买入信号")

    def test_overbought_signal(self):
        """超买应产生卖出信号"""
        prices = [10.0 * (1.05 ** i) for i in range(30)]
        bars = self._make_bars(prices)
        signals = self.generator.compute(bars)
        has_sell = any(s.action == SignalAction.CLOSE_LONG for s in signals)
        has_rsi_reason = any("RSI超买" in (s.reason or "") for s in signals)
        self.assertTrue(has_sell or has_rsi_reason, "超买应产生卖出信号")


class TestBacktestEngine(unittest.TestCase):
    """测试回测引擎"""

    def setUp(self):
        self.config = BacktestEngineConfig(
            initial_capital=1_000_000.0,
            commission_rate=0.0003,
            slippage=0.001,
        )
        self.engine = BacktestEngine(self.config)

    def test_strategy_registration(self):
        """测试策略注册"""
        config = StrategyConfig(
            name="Test", category=StrategyCategory.TREND_FOLLOWING,
            symbols=["TEST"], parameters={},
        )
        from quantum.strategy.base import BaseStrategy

        class MockStrategy(BaseStrategy):
            def setup(self): pass
            def on_bar(self, bar):
                return Signal(
                    timestamp=bar.time, symbol=bar.symbol,
                    action=SignalAction.HOLD, price=bar.close,
                )

        strategy = MockStrategy(config)
        self.engine.add_strategy(strategy)
        self.assertEqual(len(self.engine.strategies), 1)

    def test_risk_manager_registration(self):
        """测试风控注册"""
        rm = PositionSizer()
        self.engine.add_risk_manager(rm)
        self.assertEqual(len(self.engine.risk_managers), 1)


class TestRiskManager(unittest.TestCase):
    """测试风控模块"""

    def test_position_limit(self):
        """测试仓位上限"""
        mgr = PositionSizer(max_single_position_pct=0.1)
        portfolio = Portfolio(
            total_capital=1_000_000.0,
            available_cash=1_000_000.0,
        )
        signals = [
            Signal(
                timestamp=datetime.now(), symbol="TEST",
                action=SignalAction.OPEN_LONG, price=10.0,
                quantity=500000,  # 市值500万，超过10%
            )
        ]
        result = mgr.before_trade(signals, portfolio, datetime.now())
        # 95%的仓位应该被压缩到10%以内
        if result:
            max_cost = result[0].quantity * result[0].price
            self.assertLessEqual(max_cost / portfolio.total_capital, 0.11)

    def test_trailing_stop(self):
        """测试移动止损"""
        mgr = TrailingStopLoss(trail_pct=0.05, min_holding_period=0)

        now = datetime.now()
        portfolio = Portfolio(
            total_capital=100_000.0,
            available_cash=0.0,
            positions={
                "TEST": Position(
                    symbol="TEST", quantity=1000,
                    avg_cost=10.0, market_value=10.5,
                )
            },
        )
        # 第一次调用：价格在高点10.5，记录最高价
        signals = []
        mgr.before_trade(signals, portfolio, now)
        # 第二次调用：价格跌到9.5，从10.5高点回落9.5%
        portfolio.positions["TEST"].market_value = 9.5
        result = mgr.before_trade(signals, portfolio, now + timedelta(hours=1))
        self.assertTrue(any(s.action == SignalAction.CLOSE_LONG for s in result),
                        "移动止损应生成平仓信号")


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
"""
运行回测 - 示例脚本
"""
import sys
import os

# 将项目根目录加入path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime, timedelta
from quantum.backtest.engine import BacktestEngine, BacktestEngineConfig
from quantum.backtest.analyzer import PerformanceAnalyzer
from quantum.backtest.report import BacktestReport
from quantum.strategy.examples.dual_moving_average import DualMovingAverageStrategy
from quantum.strategy.examples.bollinger_reversal import BollingerReversalStrategy
from quantum.risk.base import RiskManager
from quantum.risk.stop_loss import TrailingStopLoss
from quantum.risk.position_manager import PositionSizer
from quantum.common.models import StrategyConfig
from quantum.common.enums import TimeFrame, StrategyCategory
from quantum.common.utils import setup_logger
from quantum.monitor.logger import setup_quantum_logger


def run_backtest():
    """运行回测"""
    logger = setup_logger()

    # ===== 1. 准备数据 =====
    # 使用示例数据（正式环境从数据源获取）
    import numpy as np
    import pandas as pd

    logger.info("生成示例回测数据...")
    dates = pd.date_range(start="2023-01-01", end="2025-01-01", freq="B")
    np.random.seed(42)

    bars_dict = {}
    for symbol in ["000001", "000002", "000858"]:
        price = 10.0 + hash(symbol) % 20
        bars = []
        for d in dates:
            change = np.random.randn() * 0.02
            price *= (1 + change)
            vol = int(np.random.exponential(5_000_000))

            from quantum.common.models import Bar
            bar = Bar(
                symbol=symbol,
                time=d.to_pydatetime(),
                timeframe=TimeFrame.DAILY,
                open=price * (1 + np.random.randn() * 0.005),
                high=price * (1 + abs(np.random.randn()) * 0.01),
                low=price * (1 - abs(np.random.randn()) * 0.01),
                close=price,
                volume=float(vol),
                amount=float(vol * price),
            )
            bars.append(bar)
        bars_dict[symbol] = bars
        logger.info(f"{symbol}: {len(bars)}根K线")

    # ===== 2. 配置回测 =====
    engine_config = BacktestEngineConfig(
        initial_capital=1_000_000.0,
        start_date=dates[0].to_pydatetime(),
        end_date=dates[-1].to_pydatetime(),
        commission_rate=0.0003,
        min_commission=5.0,
        tax_rate=0.001,
        slippage=0.001,
        slippage_mode="fixed",
        allow_short=False,
        lot_size=100,
        benchmark_symbol="000300",
    )

    engine = BacktestEngine(engine_config)

    # ===== 3. 注册策略 =====
    strategy = DualMovingAverageStrategy(StrategyConfig(
        name="DualMA_Trend",
        category=StrategyCategory.TREND_FOLLOWING,
        symbols=["000001", "000002", "000858"],
        parameters={"fast_period": 10, "slow_period": 30},
        timeframe=TimeFrame.DAILY,
    ))
    engine.add_strategy(strategy)

    # ===== 4. 注册风控 =====
    engine.add_risk_manager(PositionSizer(max_single_position_pct=0.3))
    engine.add_risk_manager(TrailingStopLoss(trail_pct=0.08))

    # ===== 5. 注册事件回调 =====
    def on_trade(trade, portfolio):
        logger.info(
            f"成交: {trade.side.value} {trade.symbol} "
            f"{trade.quantity}股@{trade.price:.2f} "
            f"| 总资产: {portfolio.total_capital:,.2f}"
        )

    engine.on("trade_executed", on_trade)

    # ===== 6. 执行回测 =====
    logger.info("\n" + "=" * 50)
    logger.info("开始回测...")
    logger.info(f"初始资金: ¥{engine_config.initial_capital:,.2f}")
    logger.info(f"数据范围: {engine_config.start_date} ~ {engine_config.end_date}")
    logger.info("=" * 50 + "\n")

    result = engine.run(bars_dict)

    # ===== 7. 输出结果 =====
    logger.info("\n" + "=" * 50)
    logger.info("回测结果")
    logger.info("=" * 50)
    logger.info(f"最终资金: ¥{result.final_capital:,.2f}")
    logger.info(f"总收益率: {result.total_return*100:.2f}%")
    logger.info(f"年化收益: {result.annual_return*100:.2f}%")
    logger.info(f"最大回撤: {result.max_drawdown*100:.2f}%")
    logger.info(f"夏普比率: {result.sharpe_ratio:.2f}")
    logger.info(f"索提诺比率: {result.sortino_ratio:.2f}")
    logger.info(f"胜率: {result.win_rate*100:.1f}%")
    logger.info(f"盈亏比: {result.profit_factor:.2f}")
    logger.info(f"总交易: {result.total_trades}笔")
    logger.info(f"盈利:{result.winning_trades} 亏损:{result.losing_trades}")
    logger.info(f"平均持仓: {result.avg_holding_period:.1f}天")
    logger.info("=" * 50)

    # ===== 8. 生成HTML报告 =====
    report_path = "./backtest_report.html"
    report = BacktestReport()
    html = report.generate(result, engine_config)
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(html)
    logger.info(f"报告已生成: {report_path}")

    return result


if __name__ == "__main__":
    run_backtest()

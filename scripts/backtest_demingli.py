#!/usr/bin/env python3
"""
回测德明利(001309) - 腾讯数据源 + TrendGrid策略
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime, timedelta
from quantum.data.base import DataManager
from quantum.data.sources.tencent_source import TencentDataSource
from quantum.data.sources.local_source import ParquetStore
from quantum.data.processors import DataCleaner, DataAligner
from quantum.backtest.engine import BacktestEngine, BacktestEngineConfig
from quantum.backtest.analyzer import PerformanceAnalyzer
from quantum.backtest.report import BacktestReport
from quantum.strategy.examples.trend_grid import TrendGridStrategy
from quantum.common.models import StrategyConfig
from quantum.common.enums import TimeFrame, StrategyCategory
from quantum.common.utils import setup_logger
import yaml


def run():
    logger = setup_logger()

    # ===== 1. 拉数据 =====
    config = yaml.safe_load(open('./config/default.yaml'))
    dm = DataManager(config['data'])

    tc = TencentDataSource(config['data']['sources']['tencent'])
    dm.register_source('tencent', tc)

    ps = ParquetStore('./data/parquet')
    dm.register_store('parquet', ps)

    end = datetime.now()
    start = end - timedelta(days=365)
    symbol = '001309'

    logger.info(f"拉取 {symbol} 数据...")
    bars = dm.get_data(symbol, start, end, TimeFrame.DAILY,
                       source_name='tencent')
    logger.info(f"获取到 {len(bars)} 根K线")

    # 清洗
    cleaner = DataCleaner({'fill_gaps': False})
    bars = cleaner.clean_bars(bars)
    logger.info(f"清洗后: {len(bars)} 根, {bars[0].time.date()} ~ {bars[-1].time.date()}")

    # 最新价
    last = bars[-1]
    first = bars[0]
    logger.info(f"区间: {first.close:.2f} → {last.close:.2f} ({(last.close/first.close-1)*100:+.2f}%)")

    # ===== 2. 配置回测 =====
    engine_config = BacktestEngineConfig(
        initial_capital=500_000.0,
        start_date=bars[0].time,
        end_date=bars[-1].time,
        commission_rate=0.00025,
        min_commission=5.0,
        tax_rate=0.001,
        slippage=0.001,
        allow_short=False,
        lot_size=100,
    )

    engine = BacktestEngine(engine_config)

    # ===== 3. TrendGrid策略 =====
    strategy = TrendGridStrategy(StrategyConfig(
        name="TrendGrid_德明利",
        category=StrategyCategory.TREND_FOLLOWING,
        symbols=[symbol],
        parameters={
            'trend_ma': 15,
            'grid_levels': 4,
            'grid_spacing': 0.04,
            'profit_target': 0.50,  # 放很大，实际依靠趋势反转离场
            'stop_loss_pct': 0.15,
            'max_position_pct': 0.85,
            'entry_ma_ratio': 0.98,
        },
        timeframe=TimeFrame.DAILY,
    ))
    engine.add_strategy(strategy)

    # ===== 4. 事件回调 =====
    def on_trade(trade, portfolio):
        logger.info(
            f"  📊 {trade.side.value} {trade.symbol} "
            f"{trade.quantity}股@{trade.price:.2f} "
            f"| 资产: ¥{portfolio.total_capital:,.0f}"
        )
    engine.on("trade_executed", on_trade)

    # ===== 5. 执行回测 =====
    print()
    logger.info("=" * 55)
    logger.info(f"  德明利(001309) 回测")
    logger.info(f"  初始资金: ¥500,000")
    logger.info(f"  数据: {bars[0].time.date()} ~ {bars[-1].time.date()} ({len(bars)}根)")
    logger.info(f"  策略: TrendGrid v2 (MA20, 3层网格)")
    logger.info("=" * 55)
    print()

    result = engine.run({symbol: bars})

    # ===== 6. 结果 =====
    buy_hold_return = (bars[-1].close / bars[0].close - 1) * 100
    strategy_return = result.total_return * 100
    print()
    logger.info("=" * 55)
    logger.info("  回测结果")
    logger.info("=" * 55)
    logger.info(f"  {'指标':<15} {'策略':>10}  {'买入持有':>10}")
    logger.info(f"  {'─'*40}")
    logger.info(f"  最终资金    ¥{result.final_capital:>8,.0f}  ¥{500000*(1+buy_hold_return/100):>8,.0f}")
    logger.info(f"  总收益      {strategy_return:>8.2f}%  {buy_hold_return:>8.2f}%")
    logger.info(f"  年化收益    {result.annual_return*100:>8.2f}%")
    logger.info(f"  最大回撤    {result.max_drawdown*100:>8.2f}%")
    logger.info(f"  夏普比率    {result.sharpe_ratio:>8.2f}")
    logger.info(f"  {'─'*40}")
    logger.info(f"  胜率        {result.win_rate*100:>8.1f}%")
    logger.info(f"  盈亏比      {result.profit_factor:>8.2f}")
    logger.info(f"  总交易      {result.total_trades:>8}笔")
    logger.info(f"  盈利/亏损   {result.winning_trades}/{result.losing_trades}")
    logger.info(f"  平均持仓    {result.avg_holding_period:>8.1f}天")
    logger.info("=" * 55)

    # 交易明细
    if result.trades:
        from quantum.common.enums import OrderSide
        print()
        logger.info("交易明细:")
        groups = {}
        for t in result.trades:
            k = t.order_id or t.symbol + str(t.trade_time)
            if k not in groups:
                groups[k] = []
            groups[k].append(t)

        for oid, grp in groups.items():
            buys = [t for t in grp if t.side in (OrderSide.BUY, OrderSide.BUY_COVER)]
            sells = [t for t in grp if t.side in (OrderSide.SELL, OrderSide.SELL_SHORT)]
            for t in buys:
                logger.info(f"  📥 {t.symbol} 买入 {t.quantity}股@{t.price:.2f} 金额{t.amount:.0f} ({t.trade_time.date()})")
            for t in sells:
                logger.info(f"  📤 {t.symbol} 卖出 {t.quantity}股@{t.price:.2f} 金额{t.amount:.0f} ({t.trade_time.date()})")

    # ===== 7. HTML报告 =====
    report = BacktestReport()
    html = report.generate(result, engine_config)
    report_path = "./backtest_report.html"
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(html)
    logger.info(f"\n详细报告: {report_path}")

    return result


if __name__ == "__main__":
    run()

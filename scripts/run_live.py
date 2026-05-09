#!/usr/bin/env python3
"""
启动实盘/模拟交易
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import time
from datetime import datetime
from threading import Thread

from quantum.broker.paper_broker import PaperBroker
from quantum.strategy.examples.dual_moving_average import DualMovingAverageStrategy
from quantum.strategy.examples.bollinger_reversal import BollingerReversalStrategy
from quantum.risk.stop_loss import TrailingStopLoss
from quantum.risk.position_manager import PositionSizer
from quantum.monitor.metrics import MetricsCollector
from quantum.monitor.logger import AlertManager, setup_quantum_logger
from quantum.monitor.dashboard import DashboardServer
from quantum.common.models import StrategyConfig
from quantum.common.enums import TimeFrame, StrategyCategory
from quantum.common.utils import setup_logger


def run_live():
    """启动实盘/模拟交易"""
    logger = setup_logger()

    # 1. 初始化监控
    logger.info("初始化监控系统...")
    collector = MetricsCollector()
    alert_mgr = AlertManager()
    dashboard = DashboardServer(collector, port=8050)

    # 2. 初始化模拟交易
    logger.info("初始化模拟交易...")
    broker = PaperBroker({
        "name": "paper",
        "initial_capital": 1_000_000.0,
        "commission_rate": 0.0003,
        "tax_rate": 0.001,
        "slippage": 0.001,
    })
    broker.connect()
    logger.info(f"模拟账户已创建: ¥{broker.initial_capital:,.2f}")

    # 3. 初始化策略
    strategies = [
        DualMovingAverageStrategy(StrategyConfig(
            name="DualMA_Trend",
            category=StrategyCategory.TREND_FOLLOWING,
            symbols=["000001", "000002"],
            parameters={"fast_period": 10, "slow_period": 30},
            timeframe=TimeFrame.DAILY,
            capital_pct=0.5,
        )),
        BollingerReversalStrategy(StrategyConfig(
            name="Bollinger_Reversal",
            category=StrategyCategory.MEAN_REVERSION,
            symbols=["600519"],
            parameters={"period": 20, "std_dev": 2.0},
            timeframe=TimeFrame.DAILY,
            capital_pct=0.3,
        )),
    ]

    for s in strategies:
        s.setup()
        logger.info(f"策略已加载: {s.name}")

    # 4. 初始化风控
    risk_managers = [
        PositionSizer(max_single_position_pct=0.3),
        TrailingStopLoss(trail_pct=0.05),
    ]

    # 5. 启动仪表盘
    if dashboard.start():
        logger.info(f"仪表盘已启动: http://localhost:{dashboard.port}")
    else:
        logger.warning("仪表盘启动失败（需要安装 dash）")

    # 6. 主循环
    logger.info("\n" + "=" * 50)
    logger.info("开始模拟交易...")
    logger.info("=" * 50)

    try:
        tick_count = 0
        while True:
            # 模拟行情更新
            symbol = strategies[0].symbols[tick_count % len(strategies[0].symbols)]
            import numpy as np
            mock_price = 10.0 + np.random.randn() * 0.5

            broker.update_price(symbol, mock_price)
            collector.record_signal("main")

            # 生成信号
            from quantum.common.models import Signal, Bar
            from quantum.common.enums import SignalAction

            mock_bar = Bar(
                symbol=symbol,
                time=datetime.now(),
                timeframe=TimeFrame.MIN1,
                open=mock_price, high=mock_price * 1.01,
                low=mock_price * 0.99, close=mock_price,
                volume=1000000.0,
            )

            for s in strategies:
                signal = s.on_bar(mock_bar)
                if signal:
                    logger.info(f"[{s.name}] {signal.action.value} {signal.symbol} @ {signal.price:.2f}")
                    try:
                        order_id = broker.place_order(Order(
                            order_id="",
                            symbol=signal.symbol,
                            side=OrderSide.BUY if signal.action == SignalAction.OPEN_LONG else OrderSide.SELL,
                            order_type=OrderType.MARKET,
                            price=signal.price,
                            quantity=100,
                            created_at=datetime.now(),
                        ))
                        logger.info(f"订单已提交: {order_id}")
                        collector.record_order()
                    except Exception as e:
                        alert_mgr.error("下单失败", str(e))

            time.sleep(5)
            tick_count += 1

            # 每分钟输出摘要
            if tick_count % 12 == 0:
                info = broker.get_account_info()
                logger.info(f"资产: {info['total_asset']:,.2f} | 现金: {info['available_cash']:,.2f}")

    except KeyboardInterrupt:
        logger.info("收到停止信号，正在关闭...")
    finally:
        for s in strategies:
            s.teardown()
        broker.disconnect()
        logger.info("系统已关闭")


if __name__ == "__main__":
    # 添加导入（实盘需要完整Order模型）
    from quantum.common.models import Order
    from quantum.common.enums import OrderSide, OrderType
    run_live()

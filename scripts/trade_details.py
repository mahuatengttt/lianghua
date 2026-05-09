import sys
sys.path.insert(0, '.')
from quantum.data.sources.yahoo_source import YahooFinanceDataSource
from quantum.common.enums import TimeFrame, StrategyCategory, OrderSide
from quantum.common.models import StrategyConfig
from quantum.backtest.engine import BacktestEngine
from quantum.backtest.config import BacktestEngineConfig
from quantum.strategy.examples.trend_grid import TrendGridStrategy
from datetime import datetime, timedelta

ds = YahooFinanceDataSource({})
end = datetime.now()
start = end - timedelta(days=730)

symbols = {
    '001309.SZ': ('德明利', {'trend_ma':45,'grid_levels':3,'grid_spacing':0.05,'profit_target':0.15,'stop_loss_pct':0.12,'max_position_pct':0.75,'entry_ma_ratio':0.95}),
    '300750.SZ': ('宁德时代', {'trend_ma':60,'grid_levels':3,'grid_spacing':0.05,'profit_target':0.15,'stop_loss_pct':0.12,'max_position_pct':0.60,'entry_ma_ratio':0.95}),
    '601398.SS': ('工商银行', {'trend_ma':60,'grid_levels':3,'grid_spacing':0.03,'profit_target':0.10,'stop_loss_pct':0.08,'max_position_pct':0.60,'entry_ma_ratio':0.95}),
}

for code, (name, params) in symbols.items():
    bars = ds.get_bars(code, start, end)
    stock_ret = (bars[-1].close / bars[0].close - 1)
    cfg = StrategyConfig(name='TG-'+name, category=StrategyCategory.TREND_FOLLOWING,
        symbols=[code], parameters=params, timeframe=TimeFrame.DAILY)
    s = TrendGridStrategy(cfg)
    eng = BacktestEngine(BacktestEngineConfig(
        initial_capital=1000000.0, commission_rate=0.0003, tax_rate=0.001, slippage=0.001))
    eng.add_strategy(s)
    r = eng.run({code: bars})

    print()
    print('=' * 86)
    print('  %s (%s)  策略收益: %+.2f%%  股票涨跌: %+.2f%%' % (
        name, code, r.total_return*100, stock_ret*100))
    print('=' * 86)
    print('  %-12s %-6s %8s %8s %10s %8s' % ('日期', '方向', '价格', '股数', '成交额', '费用'))
    print('  ' + '-' * 78)

    all_trades = []
    for t in r.trades:
        side = '买入' if t.side in (OrderSide.BUY, OrderSide.BUY_COVER) else '卖出'
        fee = t.commission + t.tax
        all_trades.append((t.trade_time, side, t.price, t.quantity, t.amount, fee))
    all_trades.sort()

    total_buy_qty = 0
    total_buy_cost = 0.0
    total_sell_qty = 0
    total_sell_amt = 0.0

    for dt, side, price, qty, amt, fee in all_trades:
        print('  %-12s %-6s %8.2f %8d %10.2f %8.2f' % (dt.strftime('%Y-%m-%d'), side, price, qty, amt, fee))
        if side == '买入':
            total_buy_qty += qty
            total_buy_cost += amt + fee
        else:
            total_sell_qty += qty
            total_sell_amt += amt - fee

    print('  ' + '-' * 78)
    buy_avg = total_buy_cost / total_buy_qty if total_buy_qty > 0 else 0
    sell_avg = total_sell_amt / total_sell_qty if total_sell_qty > 0 else 0
    net_pnl = total_sell_amt - total_buy_cost
    print('  买入共%d股, 均价¥%.2f, 总成本¥%s' % (total_buy_qty, buy_avg, format(total_buy_cost, ',.2f')))
    print('  卖出共%d股, 均价¥%.2f, 总收入¥%s' % (total_sell_qty, sell_avg, format(total_sell_amt, ',.2f')))
    print('  净盈亏: ¥%+.2f' % net_pnl)
    print('  交易%d笔(胜%d/输%d)  回撤%.2f%%  夏普%.2f' % (
        len(r.trades), r.winning_trades, r.losing_trades, r.max_drawdown*100, r.sharpe_ratio))

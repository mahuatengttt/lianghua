import sys; sys.path.insert(0, '.')
from quantum.data.sources.yahoo_source import YahooFinanceDataSource
from quantum.common.enums import TimeFrame, StrategyCategory, OrderSide
from quantum.common.models import StrategyConfig
from quantum.backtest.engine import BacktestEngine
from quantum.backtest.config import BacktestEngineConfig
from quantum.strategy.examples.trend_grid import TrendGridStrategy
from quantum.strategy.examples.dual_moving_average import DualMovingAverageStrategy
from datetime import datetime, timedelta

ds = YahooFinanceDataSource({})
code = '603986.SS'
bars = ds.get_bars(code, datetime.now()-timedelta(days=730), datetime.now())
stock_ret = (bars[-1].close/bars[0].close-1)
print('\n========== 兆易创新(603986.SS) 全面回测 ==========')
print('2年涨幅: %+.2f%%  最大回撤: %.2f%%\n' % (stock_ret*100, 33.89))

def run(name, StrategyCls, params):
    cfg = StrategyConfig(name=name, category=StrategyCategory.TREND_FOLLOWING,
        symbols=[code], parameters=params, timeframe=TimeFrame.DAILY)
    s = StrategyCls(cfg)
    eng = BacktestEngine(BacktestEngineConfig(
        initial_capital=1000000.0, commission_rate=0.0003, tax_rate=0.001, slippage=0.001))
    eng.add_strategy(s)
    return eng.run({code: bars})

# --- DualMA 参数扫描 ---
print('--- DualMA 参数扫描 ---')
for fast, slow in [(5,20),(10,30),(15,45),(20,60),(30,90)]:
    r = run('DualMA(%d/%d)'%(fast,slow), DualMovingAverageStrategy,
        {'fast_period':fast,'slow_period':slow,'atr_period':14,'atr_multiplier':3.0})
    print('  MA%d/%d  | %+7.1f%% | 回撤%5.1f%% | 夏普%.2f | %d笔' % (
        fast, slow, r.total_return*100, r.max_drawdown*100, r.sharpe_ratio, r.total_trades))

# --- TrendGrid 参数扫描 ---
print('\n--- TrendGrid 参数扫描 ---')
combos = [
    ('MA60/3档/5%%/15%%', 60, 3, 0.05, 0.15),
    ('MA60/4档/10%%/20%%', 60, 4, 0.10, 0.20),
    ('MA45/3档/5%%/15%%', 45, 3, 0.05, 0.15),
    ('MA45/4档/8%%/20%%', 45, 4, 0.08, 0.20),
    ('MA30/3档/5%%/15%%', 30, 3, 0.05, 0.15),
    ('MA30/4档/8%%/25%%', 30, 4, 0.08, 0.25),
    ('MA20/3档/5%%/15%%', 20, 3, 0.05, 0.15),
    ('MA20/4档/8%%/20%%', 20, 4, 0.08, 0.20),
]
for name, ma, lvls, sp, pt in combos:
    r = run('TG(%s)'%name, TrendGridStrategy,
        {'trend_ma':ma,'grid_levels':lvls,'grid_spacing':sp,
         'profit_target':pt,'stop_loss_pct':0.12,'max_position_pct':0.75,'entry_ma_ratio':0.95})
    print('  %-20s | %+7.1f%% | 回撤%5.1f%% | 夏普%.2f | %d笔' % (
        name, r.total_return*100, r.max_drawdown*100, r.sharpe_ratio, r.total_trades))

# --- 最佳配置详细交易 ---
print('\n--- 最佳配置交易明细 ---')
# 选TG(MA45/3档/5%/15%)
r = run('最佳', TrendGridStrategy,
    {'trend_ma':45,'grid_levels':3,'grid_spacing':0.05,
     'profit_target':0.15,'stop_loss_pct':0.12,'max_position_pct':0.75,'entry_ma_ratio':0.95})
print('  TG(MA45/3/5%%/15%%)  收益%+.2f%%  回撤%.2f%%  夏普%.2f  %d笔' % (
    r.total_return*100, r.max_drawdown*100, r.sharpe_ratio, r.total_trades))
print('  %-12s %-6s %8s %8s %10s %8s' % ('日期','方向','价格','股数','成交额','费用'))
for t in sorted(r.trades, key=lambda x: x.trade_time):
    side = '买入' if t.side in (OrderSide.BUY, OrderSide.BUY_COVER) else '卖出'
    fee = t.commission + t.tax
    print('  %-12s %-6s %8.2f %8d %10.2f %8.2f' % (
        t.trade_time.strftime('%Y-%m-%d'), side, t.price, t.quantity, t.amount, fee))
# 汇总
buys = [t for t in r.trades if t.side in (OrderSide.BUY,OrderSide.BUY_COVER)]
sells = [t for t in r.trades if t.side in (OrderSide.SELL,OrderSide.SELL_SHORT)]
buy_qty = sum(t.quantity for t in buys)
buy_cost = sum(t.amount + t.commission + t.tax for t in buys)
sell_qty = sum(t.quantity for t in sells)
sell_amt = sum(t.amount - t.commission - t.tax for t in sells)
buy_avg = buy_cost/buy_qty if buy_qty>0 else 0
sell_avg = sell_amt/sell_qty if sell_qty>0 else 0
print('  ─' * 20)
print('  买入%d股 均价¥%.2f 成本¥%.2f' % (buy_qty,buy_avg,buy_cost))
print('  卖出%d股 均价¥%.2f 收入¥%.2f' % (sell_qty,sell_avg,sell_amt))
print('  净利: ¥%+.2f' % (sell_amt - buy_cost))

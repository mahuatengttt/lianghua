#!/usr/bin/env python3
"""
A股主板超短线策略回测器
- 主板：60/000/002开头，排除ST
- 持股≤5天
- 回测区间：2025-12-01 ~ 2026-05-14（约6个月）
- 数据源：baostock
"""

import baostock as bs
import pandas as pd
import numpy as np
import warnings
import sys

warnings.filterwarnings('ignore')

END_DATE = "2026-05-14"
START_DATE = "2025-12-01"
PRELOAD_START = "2025-09-01"  # 提前加载用于计算技术指标

# 注册登录
lg = bs.login()
if lg.error_code != '0':
    print(f"baostock登录失败: {lg.error_msg}")
    sys.exit(1)
print(f"baostock登录成功")

# ============================
# 1. 获取所有主板股票列表
# ============================
print("[1/7] 获取股票列表...")
rs = bs.query_all_stock(END_DATE)
all_stocks = []
while rs.next():
    row = rs.get_row_data()
    code = row[0]
    name = row[2]
    # 只取A股主板：以 sh.60, sz.000, sz.002 开头
    is_st = 'ST' in name or '退' in name
    if (code.startswith('sh.60') or code.startswith('sz.000') or code.startswith('sz.002')) and not is_st:
        all_stocks.append((code, name))
print(f"    主板A股（含中小板）: {len(all_stocks)} 只")

# ============================
# 2. 获取上证指数（大盘环境判断）
# ============================
print("[2/7] 获取上证指数K线...")
rs = bs.query_history_k_data_plus('sh.000001',
    'date,close',
    start_date=PRELOAD_START, end_date=END_DATE)
sh_data = {}
while rs.next():
    row = rs.get_row_data()
    try:
        sh_data[row[0]] = float(row[1])
    except:
        pass

# 计算大盘均线
sh_dates = sorted(sh_data.keys())
sh_close = [sh_data[d] for d in sh_dates]
sh_df = pd.DataFrame({'date': sh_dates, 'close': sh_close})
sh_df['MA5'] = sh_df['close'].rolling(5).mean()
sh_df['MA10'] = sh_df['close'].rolling(10).mean()
sh_df['MA60'] = sh_df['close'].rolling(60).mean()
sh_index = {row['date']: row for _, row in sh_df.iterrows()}
print(f"    上证指数数据: {len(sh_dates)} 个交易日")

# ============================
# 3. 加载所有股票K线（抽样前500只）
# ============================
print("[3/7] 获取个股K线数据（抽样500只）...")

# 先用成交量筛选：加载少量数据判断流动性
def check_liquidity(code):
    """快速检查日均成交额>1亿（大致判断）"""
    try:
        rs = bs.query_history_k_data_plus(code,
            'date,close,volume',
            start_date="2026-04-01", end_date=END_DATE)
        vols = []
        while rs.next():
            row = rs.get_row_data()
            try:
                v = float(row[2])
                c = float(row[1])
                if v > 0 and c > 0:
                    vols.append(v * c)  # 近似成交额
            except:
                pass
        if len(vols) > 5:
            avg = np.mean(vols)
            return avg > 1e8  # 日均成交额>1亿
    except:
        pass
    return False

# 流动性筛选并排序
liquid_stocks = []
for code, name in all_stocks:
    if check_liquidity(code):
        liquid_stocks.append((code, name))
    if len(liquid_stocks) >= 300:
        break

print(f"    流动性合格: {len(liquid_stocks)} 只")

# 加载K线数据（完整）
klines = {}
for code, name in liquid_stocks:
    try:
        rs = bs.query_history_k_data_plus(code,
            'date,open,high,low,close,volume',
            start_date=PRELOAD_START, end_date=END_DATE)
        rows = []
        while rs.next():
            row = rs.get_row_data()
            try:
                rows.append({
                    'date': row[0],
                    'open': float(row[1]),
                    'high': float(row[2]),
                    'low': float(row[3]),
                    'close': float(row[4]),
                    'volume': float(row[5])
                })
            except:
                pass
        if len(rows) >= 40:
            klines[code] = {'rows': rows, 'name': name}
    except:
        pass

print(f"    成功加载K线: {len(klines)} 只")

# ============================
# 4. 计算技术指标
# ============================
print("[4/7] 计算技术指标...")

def calc_indicators(rows):
    """为个股数据计算技术指标"""
    df = pd.DataFrame(rows)
    closes = df['close'].values
    highs = df['high'].values
    lows = df['low'].values
    volumes = df['volume'].values
    n = len(df)
    
    indicators = []
    for i in range(n):
        item = {
            'date': df.iloc[i]['date'],
            'open': df.iloc[i]['open'],
            'high': df.iloc[i]['high'],
            'low': df.iloc[i]['low'],
            'close': df.iloc[i]['close'],
            'volume': df.iloc[i]['volume'],
        }
        
        # MA均线
        if i >= 4:
            item['MA5'] = np.mean(closes[i-4:i+1])
        else:
            item['MA5'] = None
        if i >= 9:
            item['MA10'] = np.mean(closes[i-9:i+1])
        else:
            item['MA10'] = None
        if i >= 19:
            item['MA20'] = np.mean(closes[i-19:i+1])
        else:
            item['MA20'] = None
        
        # 成交量均线
        if i >= 4:
            item['VOL5'] = np.mean(volumes[i-4:i+1])
        else:
            item['VOL5'] = None
        if i >= 19:
            item['VOL20'] = np.mean(volumes[i-19:i+1])
        else:
            item['VOL20'] = None
        
        # RSI(6)
        if i >= 6:
            gains = [max(closes[j] - closes[j-1], 0) for j in range(i-5, i+1)]
            losses = [max(closes[j-1] - closes[j], 0) for j in range(i-5, i+1)]
            avg_gain = np.mean(gains)
            avg_loss = np.mean(losses)
            if avg_loss > 0:
                rs_val = avg_gain / avg_loss
                item['RSI6'] = 100 - (100 / (1 + rs_val))
            else:
                item['RSI6'] = 100 if avg_gain > 0 else 50
        else:
            item['RSI6'] = None
        
        # MACD
        # 用完整的ema计算
        if i == 0:
            item['DIF'] = 0
            item['DEA'] = 0
        elif i >= 1:
            # EWM
            span12 = 12
            span26 = 9
            alpha12 = 2 / (span12 + 1)
            alpha26 = 2 / (span26 + 1)
            
            # 从0开始计算EMA
            ema12 = [closes[0]]
            ema26 = [closes[0]]
            for j in range(1, i+1):
                ema12.append(closes[j] * alpha12 + ema12[-1] * (1 - alpha12))
                ema26.append(closes[j] * alpha26 + ema26[-1] * (1 - alpha26))
            
            dif = ema12[-1] - ema26[-1]
            
            # DEA (9日EMA of DIF)
            if i < 9:
                prev_difs = [dif]
                for j in range(1, i+1):
                    ema12_j = [closes[max(0, j-8)]]
                    ema26_j = [closes[max(0, j-8)]]
                    for k in range(max(0, j-8)+1, j+1):
                        ema12_j.append(closes[k] * alpha12 + ema12_j[-1] * (1-alpha12))
                        ema26_j.append(closes[k] * alpha26 + ema26_j[-1] * (1-alpha26))
                    prev_difs.append(ema12_j[-1] - ema26_j[-1])
                dea = np.mean(prev_difs)
            else:
                difs = []
                for j in range(i-8, i+1):
                    ema12_j = [closes[j-8]]
                    ema26_j = [closes[j-8]]
                    for k in range(j-7, j+1):
                        ema12_j.append(closes[k] * alpha12 + ema12_j[-1] * (1-alpha12))
                        ema26_j.append(closes[k] * alpha26 + ema26_j[-1] * (1-alpha26))
                    difs.append(ema12_j[-1] - ema26_j[-1])
                dea = np.mean(difs)
            
            item['DIF'] = dif
            item['DEA'] = dea
            item['MACD'] = 2 * (dif - dea)
        
        # 涨跌幅
        if i > 0:
            item['涨跌幅'] = (closes[i] - closes[i-1]) / closes[i-1] * 100
        else:
            item['涨跌幅'] = 0
        
        # 20日最高价（前一日）
        if i >= 20:
            item['HIGH20'] = np.max(highs[i-20:i])
        else:
            item['HIGH20'] = None
        
        indicators.append(item)
    
    return indicators

# 为每个股票计算指标
stock_data = {}
for code, info in klines.items():
    indicators = calc_indicators(info['rows'])
    stock_data[code] = {
        'indicators': indicators,
        'name': info['name']
    }

# ============================
# 5. 信号检查
# ============================
def check_signals(item, prev_item):
    """返回信号数量和信号列表"""
    sigs = []
    
    # 信号1: 量价配合（涨幅3%-7%，成交量≥5日均量1.5倍）
    if item['涨跌幅'] is not None:
        if 3 <= item['涨跌幅'] <= 7:
            if item['MA5'] and item['VOL5'] and item['VOL5'] > 0 and item['volume'] >= item['VOL5'] * 1.5:
                sigs.append(1)
    
    # 信号2: 均线多头（MA5 > MA10 > MA20，且股价站稳MA5）
    if all(v is not None for v in [item['MA5'], item['MA10'], item['MA20']]):
        if item['MA5'] > item['MA10'] > item['MA20'] and item['close'] >= item['MA5']:
            sigs.append(2)
    
    # 信号3: RSI(6)在50-70之间
    if item['RSI6'] is not None and 50 <= item['RSI6'] <= 70:
        sigs.append(3)
    
    # 信号4: MACD零轴上方首次金叉
    if prev_item is not None and all(k in prev_item for k in ['DIF', 'DEA']) and all(k in item for k in ['DIF', 'DEA']):
        if prev_item['DIF'] <= prev_item['DEA'] and item['DIF'] > item['DEA'] and item['DIF'] > 0:
            sigs.append(4)
    
    # 信号5: 突破近20日最高价
    if item['HIGH20'] is not None and item['close'] > item['HIGH20']:
        if item['VOL20'] and item['VOL20'] > 0 and item['volume'] >= item['VOL20'] * 1.2:
            sigs.append(5)
    
    # 信号6: 昨日涨停今日温和放量
    if prev_item is not None and prev_item['涨跌幅'] is not None:
        if prev_item['涨跌幅'] >= 9.5:
            if item['涨跌幅'] is not None and 1 <= item['涨跌幅'] <= 6:
                if item['VOL5'] and item['VOL5'] > 0 and item['volume'] >= item['VOL5']:
                    sigs.append(6)
    
    return len(sigs), sigs

# ============================
# 6. 回测核心
# ============================
print("[5/7] 开始回测...")

# 收集所有交易日
all_trade_dates = sorted(set(
    item['date']
    for code, info in stock_data.items()
    for item in info['indicators']
))

# 过滤：只在2025-12-01之后
all_trade_dates = [d for d in all_trade_dates if d >= START_DATE and d <= END_DATE]
print(f"    回测交易日: {len(all_trade_dates)} 天")

trades = []
total_checks = 0
env_skip = 0
signal_skip = 0

for date_idx, buy_date in enumerate(all_trade_dates):
    # 大盘环境检查：MA5 > MA10（简化版，不要求MA60）
    env_ok = False
    if buy_date in sh_index:
        row = sh_index[buy_date]
        if row['MA5'] and row['MA10']:
            env_ok = row['MA5'] > row['MA10']
    
    if not env_ok:
        env_skip += 1
        continue
    
    # 遍历个股
    for code, info in stock_data.items():
        indicators = info['indicators']
        name = info['name']
        
        # 找到买入日期在indicator中的位置
        buy_idx = -1
        for j, item in enumerate(indicators):
            if item['date'] == buy_date:
                buy_idx = j
                break
        
        if buy_idx < 20:
            continue
        
        item = indicators[buy_idx]
        prev_item = indicators[buy_idx - 1] if buy_idx > 0 else None
        
        signal_count, sigs = check_signals(item, prev_item)
        total_checks += 1
        
        if signal_count < 2:
            signal_skip += 1
            continue
        
        # 确定仓位
        if signal_count >= 6:
            position_pct = 40
        elif signal_count >= 4:
            position_pct = 20
        else:
            position_pct = 10
        
        buy_price = item['close']
        
        # === 持股模拟（最多5天）===
        sell_idx = -1
        sell_price = 0
        days_held = 0
        
        for h in range(1, 6):
            if buy_idx + h >= len(indicators):
                break
            curr = indicators[buy_idx + h]
            days_held = h
            
            prev_close = indicators[buy_idx + h - 1]['close']
            open_price = curr['open']
            close_price = curr['close']
            
            gain_pct = (close_price - buy_price) / buy_price * 100
            intra_open_gain = (open_price - prev_close) / prev_close * 100
            
            # === 卖出逻辑 ===
            # 止盈1：次日开盘涨幅≥5%，卖一半（简化：全卖）
            if h == 1 and intra_open_gain >= 5:
                sell_price = open_price
                sell_idx = buy_idx + h
                break
            
            # 止盈2：累计涨幅≥8%
            if gain_pct >= 8:
                sell_price = close_price
                sell_idx = buy_idx + h
                break
            
            # 止损1：收盘跌破MA5
            if curr['MA5'] is not None and close_price < curr['MA5']:
                if buy_idx + h + 1 < len(indicators):
                    sell_price = indicators[buy_idx + h + 1]['open']
                    sell_idx = buy_idx + h + 1
                else:
                    sell_price = close_price
                    sell_idx = buy_idx + h
                break
            
            # 止损2：-5%
            if gain_pct <= -5:
                sell_price = close_price
                sell_idx = buy_idx + h
                break
        
        # 满5天未触发 → 第5天收盘清仓
        if sell_idx == -1 and buy_idx + 5 < len(indicators):
            sell_price = indicators[buy_idx + 5]['close']
            sell_idx = buy_idx + 5
            days_held = 5
        elif sell_idx == -1:
            continue
        
        if sell_price == 0:
            continue
        
        profit_pct = round((sell_price - buy_price) / buy_price * 100, 2)
        
        trades.append({
            '代码': code.replace('sh.', '').replace('sz.', ''),
            '名称': name,
            '买入日': buy_date,
            '卖出日': indicators[sell_idx]['date'],
            '买入价': round(buy_price, 2),
            '卖出价': round(sell_price, 2),
            '盈亏%': profit_pct,
            '持股天数': days_held,
            '信号数': signal_count,
            '信号': str(sigs),
            '仓位%': position_pct,
            '买入涨跌幅%': round(item['涨跌幅'], 2) if item['涨跌幅'] else 0
        })

# ============================
# 7. 输出结果
# ============================
print(f"[6/7] 整理结果...")
print(f"[7/7] 生成报告...\n")

# 登出
bs.logout()

if len(trades) == 0:
    print("\n⚠️ 没有产生任何有效交易信号")
    print(f"    大盘环境不达标天数: {env_skip}")
    print(f"    信号不足次数: {signal_skip}")
    sys.exit(0)

df = pd.DataFrame(trades)
total = len(df)
wins = len(df[df['盈亏%'] > 0])
losses = len(df[df['盈亏%'] < 0])
ties = len(df[df['盈亏%'] == 0])
win_rate = wins / total * 100

avg_profit = df['盈亏%'].mean()
avg_win = df[df['盈亏%'] > 0]['盈亏%'].mean() if wins > 0 else 0
avg_loss = df[df['盈亏%'] < 0]['盈亏%'].mean() if losses > 0 else 0
max_profit = df['盈亏%'].max()
max_loss = df['盈亏%'].min()
avg_days = df['持股天数'].mean()
profit_factor = abs(avg_win/avg_loss) if avg_loss != 0 else float('inf')

# ===== 打印结果 =====
print("=" * 60)
print("          📊 A股主板超短线策略回测结果")
print("=" * 60)
print(f"  回测区间: {START_DATE} ~ {END_DATE} ({len(all_trade_dates)}个交易日)")
print(f"  回测标的: {len(stock_data)} 只主板股票（日均成交额>1亿）")
print(f"  买入规则: 满足≥2个信号才买入（最大6个信号）")
print(f"  持股限制: ≤5个交易日")
print(f"  大盘要求: 上证MA5 > MA10")
print()
print(f"  🏆  胜率: {win_rate:.1f}% ({wins}/{total})")
print(f"  📈  平均盈亏: {avg_profit:+.2f}%")
print(f"  ✅  平均盈利: +{avg_win:.2f}%")
print(f"  ❌  平均亏损: {avg_loss:.2f}%")
print(f"  ⚖️   盈亏比: {profit_factor:.2f}")
print(f"  🏅  最大盈利: +{max_profit:.2f}%")
print(f"  💀  最大亏损: {max_loss:.2f}%")
print(f"  📆  平均持股: {avg_days:.1f}天")
print(f"  🎯  日均信号: {total/len(all_trade_dates):.2f} 笔/交易日")

# 按信号数分组
print()
print("-" * 60)
print("  📊 按信号数分组")
print("-" * 60)
for sig_c in range(6, 1, -1):
    sub = df[df['信号数'] == sig_c]
    if len(sub) > 0:
        sub_win = len(sub[sub['盈亏%'] > 0])
        sub_wr = sub_win / len(sub) * 100
        sub_avg = sub['盈亏%'].mean()
        print(f"  {sig_c}个信号: {len(sub):3d}笔 | 胜率{sub_wr:5.1f}% | 均盈亏{sub_avg:+.2f}%")

# 按月分组
print()
print("-" * 60)
print("  📊 月度表现")
print("-" * 60)
df['月'] = df['买入日'].str[:7]
for month, sub in sorted(df.groupby('月')):
    sub_win = len(sub[sub['盈亏%'] > 0])
    sub_wr = sub_win / len(sub) * 100
    sub_avg = sub['盈亏%'].mean()
    print(f"  {month}: {len(sub):3d}笔 | 胜率{sub_wr:5.1f}% | 均盈亏{sub_avg:+.2f}%")

# 按持股天数分组
print()
print("-" * 60)
print("  📊 持股天数表现")
print("-" * 60)
for d in range(1, 6):
    sub = df[df['持股天数'] == d]
    if len(sub) > 0:
        sub_win = len(sub[sub['盈亏%'] > 0])
        sub_wr = sub_win / len(sub) * 100
        sub_avg = sub['盈亏%'].mean()
        print(f"  持股{d}天: {len(sub):3d}笔 | 胜率{sub_wr:5.1f}% | 均盈亏{sub_avg:+.2f}%")

# 盈亏分布直方图
print()
print("-" * 60)
print("  📊 盈亏分布")
print("-" * 60)
bins = [-20, -10, -8, -5, -3, -1, 0, 1, 3, 5, 8, 10, 20]
labels = [
    '<-10%', '-10~-8%', '-8~-5%', '-5~-3%', '-3~-1%', '-1~0%',
    '0~1%', '1~3%', '3~5%', '5~8%', '8~10%', '>10%'
]
df['盈亏区间'] = pd.cut(df['盈亏%'], bins=bins, labels=labels)
dist = df['盈亏区间'].value_counts()
max_count = max(dist) if not dist.empty else 1
for l in labels:
    v = dist.get(l, 0)
    bar = '█' * int(v / max_count * 30) if max_count > 0 else ''
    print(f"  {l:>8}: {v:3d}笔 {bar}")

# 累计收益（等权复利）
print()
print("-" * 60)
print("  📊 累计收益（等权、复利）")
print("-" * 60)
cum_prod = 1.0
for _, t in df.iterrows():
    cum_prod *= (1 + t['盈亏%']/100)
total_return = (cum_prod - 1) * 100

# 简单夏普比估算
daily_rets = []
for _, t in df.iterrows():
    daily_rets.append(t['盈亏%']/100 / max(t['持股天数'], 1))
sharpe_approx = np.mean(daily_rets) / np.std(daily_rets) * np.sqrt(250) if len(daily_rets) > 0 and np.std(daily_rets) > 0 else 0

print(f"  总收益率（复利）: {total_return:+.2f}%")
print(f"  日均收益率: {np.mean(daily_rets)*100:.3f}%")
print(f"  估算夏普比: {sharpe_approx:.2f}")

# 保存结果
output_csv = "/home/admin/.openclaw/workspace/agents/trader/workspace/backtest_result.csv"
df.to_csv(output_csv, index=False, encoding='utf-8-sig')
print()
print(f"📁 详细交易记录已保存: backtest_result.csv")

# 最近交易预览
print()
print("-" * 60)
print("  最近15笔交易:")
print("-" * 60)
for _, t in df.tail(15).iterrows():
    emoji = "🟢" if t['盈亏%'] > 0 else "🔴"
    print(f"  {emoji} {t['名称']}({t['代码']}) | {t['买入日']}→{t['卖出日']} | {int(t['持股天数'])}天 | {t['盈亏%']:+.2f}% | 信号{t['信号数']}")

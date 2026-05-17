#!/usr/bin/env python3
"""
A股主板超短线策略回测器 v2
- 直接使用已知主板股票代码范围
- baostock K线查询
- 持股≤5天
"""

import baostock as bs
import pandas as pd
import numpy as np
import warnings
import sys
import time

warnings.filterwarnings('ignore')

END_DATE = "2026-05-08"
START_DATE = "2025-12-01"
PRELOAD = "2025-09-01"

lg = bs.login()
if lg.error_code != '0':
    print(f"login failed: {lg.error_msg}")
    sys.exit(1)
print("✅ baostock logged in")

# ============================
# 1. 生成主板股票代码
# ============================
print("[1] 生成主板代码列表...")
codes = []
# 沪市主板: 600000-605999
for i in range(0, 6000):
    codes.append(f'sh.60{i:04d}')
# 深市主板: 000001-003999
for i in range(1, 4000):
    codes.append(f'sz.{i:06d}')

print(f"    总代码数: {len(codes)}")

# ============================
# 2. 预检查：哪些代码有K线数据
# ============================
print("[2] 快速扫描活跃股票...")
active_codes = []
for code in codes:
    try:
        rs = bs.query_history_k_data_plus(code, 'date,close,volume',
            start_date=END_DATE, end_date=END_DATE)
        cnt = 0
        while rs.next():
            cnt += 1
        if cnt > 0:
            active_codes.append(code)
            if len(active_codes) >= 500:  # 足够了
                break
    except:
        pass

print(f"    活跃股票: {len(active_codes)}")

# ============================
# 3. 获取上证指数
# ============================
print("[3] 获取上证指数...")
rs = bs.query_history_k_data_plus('sh.000001',
    'date,close',
    start_date=PRELOAD, end_date=END_DATE)
sh_rows = []
while rs.next():
    row = rs.get_row_data()
    try:
        sh_rows.append({'date': row[0], 'close': float(row[1])})
    except:
        pass

sh_df = pd.DataFrame(sh_rows)
sh_df['MA5'] = sh_df['close'].rolling(5).mean()
sh_df['MA10'] = sh_df['close'].rolling(10).mean()
sh_idx = {r['date']: r for _, r in sh_df.iterrows()}

# 获取交易日列表
rs = bs.query_trade_dates(start_date=PRELOAD, end_date=END_DATE)
trade_dates = []
while rs.next():
    row = rs.get_row_data()
    if row[1] == '1':
        trade_dates.append(row[0])
trade_dates = [d for d in trade_dates if d >= START_DATE]
print(f"    交易日: {len(trade_dates)}")

# ============================
# 4. 加载个股K线
# ============================
print(f"[4] 加载 {len(active_codes)} 只股票K线...")
stock_data = {}
for idx, code in enumerate(active_codes):
    if (idx+1) % 50 == 0:
        print(f"    进度: {idx+1}/{len(active_codes)}")
    
    try:
        rs = bs.query_history_k_data_plus(code,
            'date,open,high,low,close,volume',
            start_date=PRELOAD, end_date=END_DATE)
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
            # 计算技术指标
            df_k = pd.DataFrame(rows)
            df_k['MA5'] = df_k['close'].rolling(5).mean()
            df_k['MA10'] = df_k['close'].rolling(10).mean()
            df_k['MA20'] = df_k['close'].rolling(20).mean()
            df_k['VOL5'] = df_k['volume'].rolling(5).mean()
            df_k['VOL20'] = df_k['volume'].rolling(20).mean()
            
            # RSI
            delta = df_k['close'].diff()
            gain = delta.where(delta > 0, 0).rolling(6).mean()
            loss = (-delta.where(delta < 0, 0)).rolling(6).mean()
            df_k['RSI6'] = 100 - 100 / (1 + gain / loss.replace(0, np.nan))
            
            # MACD
            ema12 = df_k['close'].ewm(span=12).mean()
            ema26 = df_k['close'].ewm(span=26).mean()
            df_k['DIF'] = ema12 - ema26
            df_k['DEA'] = df_k['DIF'].ewm(span=9).mean()
            
            # 涨跌幅
            df_k['涨跌幅'] = df_k['close'].pct_change() * 100
            
            # 20日最高
            df_k['HIGH20'] = df_k['high'].rolling(20).max().shift(1)
            
            # 周转率检查（近似成交量/流通股）
            avg_vol = df_k['volume'].iloc[-60:].mean() if len(df_k) >= 60 else df_k['volume'].mean()
            avg_price = df_k['close'].iloc[-60:].mean() if len(df_k) >= 60 else df_k['close'].mean()
            est_amount = avg_vol * avg_price
            
            # 日均成交额>1亿
            if est_amount > 1e8:
                # 排除ST（名称判断）
                name = code
                try:
                    rs_name = bs.query_stock_basic(code)
                    while rs_name.next():
                        row = rs_name.get_row_data()
                        name = row[2]
                except:
                    pass
                
                if 'ST' not in name and '退' not in name:
                    stock_data[code] = {
                        'df': df_k,
                        'name': name,
                        'est_amount': est_amount
                    }
    except:
        continue

print(f"    成功加载: {len(stock_data)} 只（日均成交额>1亿）")

# ============================
# 5. 信号检查 + 回测
# ============================
print("[5] 执行回测...")

def check_sigs(row, prev):
    sigs = []
    # 1: 量价配合
    if 3 <= row['涨跌幅'] <= 7 and row['volume'] >= row['VOL5'] * 1.5:
        sigs.append(1)
    # 2: 均线多头
    if row['MA5'] > row['MA10'] > row['MA20'] and row['close'] >= row['MA5']:
        sigs.append(2)
    # 3: RSI 50-70
    if 50 <= row['RSI6'] <= 70:
        sigs.append(3)
    # 4: MACD金叉零轴上
    if prev['DIF'] <= prev['DEA'] and row['DIF'] > row['DEA'] and row['DIF'] > 0:
        sigs.append(4)
    # 5: 突破20日高点
    if row['close'] > row['HIGH20'] and row['volume'] >= row['VOL20'] * 1.2:
        sigs.append(5)
    # 6: 昨日涨停温和放量
    if prev['涨跌幅'] >= 9.5 and 1 <= row['涨跌幅'] <= 6 and row['volume'] >= row['VOL5']:
        sigs.append(6)
    return sigs

trades = []
env_skip = 0
total_checks = 0

for date_idx, buy_date in enumerate(trade_dates):
    # 大盘检查
    if buy_date not in sh_idx:
        env_skip += 1
        continue
    sh_row = sh_idx[buy_date]
    if sh_row['MA5'] is None or sh_row['MA10'] is None or sh_row['MA5'] <= sh_row['MA10']:
        env_skip += 1
        continue
    
    for code, info in stock_data.items():
        df = info['df']
        name = info['name']
        
        buy_pos = df[df['date'] == buy_date].index
        if len(buy_pos) == 0:
            continue
        buy_pos = buy_pos[0]
        if buy_pos < 20:
            continue
        
        row = df.loc[buy_pos]
        prev = df.loc[buy_pos - 1]
        
        sigs = check_sigs(row, prev)
        total_checks += 1
        sig_count = len(sigs)
        
        if sig_count < 2:
            continue
        
        buy_price = row['close']
        
        # 持仓模拟
        sell_idx = -1
        sell_price = 0
        days_held = 0
        
        for h in range(1, 6):
            if buy_pos + h >= len(df):
                break
            curr = df.loc[buy_pos + h]
            days_held = h
            prev_close = df.loc[buy_pos + h - 1]['close']
            gain = (curr['close'] - buy_price) / buy_price * 100
            open_gain = (curr['open'] - prev_close) / prev_close * 100
            
            # 止盈
            if h == 1 and open_gain >= 5:
                sell_price = curr['open']
                sell_idx = buy_pos + h
                break
            if gain >= 8:
                sell_price = curr['close']
                sell_idx = buy_pos + h
                break
            
            # 止损
            if curr['close'] < curr['MA5']:
                if buy_pos + h + 1 < len(df):
                    sell_price = df.loc[buy_pos + h + 1]['open']
                    sell_idx = buy_pos + h + 1
                else:
                    sell_price = curr['close']
                    sell_idx = buy_pos + h
                break
            if gain <= -5:
                sell_price = curr['close']
                sell_idx = buy_pos + h
                break
        
        if sell_idx == -1 and buy_pos + 5 < len(df):
            sell_price = df.loc[buy_pos + 5]['close']
            sell_idx = buy_pos + 5
            days_held = 5
        elif sell_idx == -1:
            continue
        
        profit = round((sell_price - buy_price) / buy_price * 100, 2)
        
        trades.append({
            '代码': code,
            '名称': name,
            '买入日': buy_date,
            '卖出日': df.loc[sell_idx]['date'] if sell_idx < len(df) else 'N/A',
            '买入价': round(buy_price, 2),
            '卖出价': round(sell_price, 2),
            '盈亏%': profit,
            '持股天数': days_held,
            '信号数': sig_count
        })

bs.logout()

# ============================
# 6. 结果输出
# ============================
print(f"\n[6] 生成报告...")

if len(trades) == 0:
    print("❌ 无交易产生")
    print(f"   大盘环境跳过: {env_skip}")
    print(f"   信号检查: {total_checks}")
    sys.exit(0)

df_t = pd.DataFrame(trades)
total = len(df_t)
wins = len(df_t[df_t['盈亏%'] > 0])
win_rate = wins/total*100
avg_p = df_t['盈亏%'].mean()
avg_w = df_t[df_t['盈亏%']>0]['盈亏%'].mean() if wins>0 else 0
avg_l = df_t[df_t['盈亏%']<0]['盈亏%'].mean() if total-wins>0 else 0
max_p = df_t['盈亏%'].max()
max_l = df_t['盈亏%'].min()

print("=" * 60)
print("   📊 主板超短线策略回测结果")
print("=" * 60)
print(f"  区间: {START_DATE}~{END_DATE} ({len(trade_dates)}交易日)")
print(f"  标的: {len(stock_data)}只主板股")
print(f"  交易: {total}笔")
print(f"  🏆 胜率: {win_rate:.1f}% ({wins}/{total})")
print(f"  📈 均盈亏: {avg_p:+.2f}%")
print(f"  ✅ 均盈: +{avg_w:.2f}%")
print(f"  ❌ 均亏: {avg_l:.2f}%")
print(f"  ⚖️ 盈亏比: {abs(avg_w/avg_l):.2f}" if avg_l != 0 else "")
print(f"  🏅 最大盈: +{max_p:.2f}%")
print(f"  💀 最大亏: {max_l:.2f}%")
avg_d = df_t['持股天数'].mean()
print(f"  📆 均持股: {avg_d:.1f}天")

# 按信号数分组
print("\n  📊 信号数分组")
for sc in range(6, 1, -1):
    sub = df_t[df_t['信号数']==sc]
    if len(sub) > 0:
        sw = len(sub[sub['盈亏%']>0])
        print(f"  {sc}信号: {len(sub):3d}笔 胜率{sw/len(sub)*100:.0f}% 均{sub['盈亏%'].mean():+.2f}%")

# 按持股天数
print("\n  📊 持股天数")
for d in range(1, 6):
    sub = df_t[df_t['持股天数']==d]
    if len(sub) > 0:
        sw = len(sub[sub['盈亏%']>0])
        print(f"  {d}天: {len(sub):3d}笔 胜率{sw/len(sub)*100:.0f}% 均{sub['盈亏%'].mean():+.2f}%")

# 盈亏分布
print("\n  📊 盈亏分布")
bins = [-20, -10, -8, -5, -3, -1, 0, 1, 3, 5, 8, 10, 20]
labels = ['<-10%','-10~-8%','-8~-5%','-5~-3%','-3~-1%','-1~0%','0~1%','1~3%','3~5%','5~8%','8~10%','>10%']
df_t['区间'] = pd.cut(df_t['盈亏%'], bins=bins, labels=labels)
dist = df_t['区间'].value_counts()
mc = max(dist) if len(dist) > 0 else 1
for l in labels:
    v = dist.get(l, 0)
    bar = '█' * int(v/mc*30)
    print(f"  {l:>8}: {v:3d} {bar}")

# 累计收益
cum = 1.0
for _, t in df_t.iterrows():
    cum *= (1 + t['盈亏%']/100)
print(f"\n  📊 累计收益（等权复利）: {(cum-1)*100:+.2f}%")

# 保存
path = "/home/admin/.openclaw/workspace/agents/trader/workspace/backtest_result.csv"
df_t.to_csv(path, index=False, encoding='utf-8-sig')
print(f"\n  📁 保存至: backtest_result.csv")

# 最近20笔
print("\n  📊 最近20笔")
for _, t in df_t.tail(20).iterrows():
    e = "🟢" if t['盈亏%']>0 else "🔴"
    print(f"  {e} {t['名称']} | {t['买入日']}→{t['卖出日']} | {int(t['持股天数'])}天 | {t['盈亏%']:+.2f}% | 信号{t['信号数']}")

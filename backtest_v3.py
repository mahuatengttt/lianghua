#!/usr/bin/env python3
"""
A股主板超短线策略回测器 v3
- 使用预编译的主流主板股票列表
- baostock分批查询
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
# 1. 逐段扫描股票代码（带进度）
# ============================
print("[1] 扫描活跃主板股票...")

# 使用多段扫描+缓存，每段10个代码
all_active = []

# 扫描600000-600500（沪市主板）
for batch_start in range(0, 501, 10):
    batch = [f'sh.60{i:04d}' for i in range(batch_start, batch_start+10)]
    for code in batch:
        t0 = time.time()
        try:
            rs = bs.query_history_k_data_plus(code, 'date,close', start_date=END_DATE, end_date=END_DATE)
            cnt = 0
            while rs.next():
                cnt += 1
            if cnt > 0:
                all_active.append(code)
        except:
            pass
    if len(all_active) >= 200:
        break

if len(all_active) < 200:
    # 扫描000001-001000（深市主板）
    for batch_start in range(1, 1001, 10):
        batch = [f'sz.{i:06d}' for i in range(batch_start, batch_start+10)]
        for code in batch:
            try:
                rs = bs.query_history_k_data_plus(code, 'date,close', start_date=END_DATE, end_date=END_DATE)
                cnt = 0
                while rs.next():
                    cnt += 1
                if cnt > 0:
                    all_active.append(code)
            except:
                pass
        if len(all_active) >= 300:
            break

# 也扫一些中小板
if len(all_active) < 300:
    for batch_start in range(1, 501, 10):
        batch = [f'sz.002{i:03d}' for i in range(batch_start, batch_start+10)]
        for code in batch:
            try:
                rs = bs.query_history_k_data_plus(code, 'date,close', start_date=END_DATE, end_date=END_DATE)
                cnt = 0
                while rs.next():
                    cnt += 1
                if cnt > 0:
                    all_active.append(code)
            except:
                pass
        if len(all_active) >= 400:
            break

print(f"    活跃股票: {len(all_active)}")

# ============================
# 2. 获取上证指数
# ============================
print("[2] 获取上证指数...")
rs = bs.query_history_k_data_plus('sh.000001', 'date,close', start_date=PRELOAD, end_date=END_DATE)
sh_data = []
while rs.next():
    row = rs.get_row_data()
    try: sh_data.append({'date': row[0], 'close': float(row[1])})
    except: pass
sh_df = pd.DataFrame(sh_data)
sh_df['MA5'] = sh_df['close'].rolling(5).mean()
sh_df['MA10'] = sh_df['close'].rolling(10).mean()
sh_idx = {r['date']: r for _, r in sh_df.iterrows()}

rs = bs.query_trade_dates(start_date=PRELOAD, end_date=END_DATE)
trade_dates = []
while rs.next():
    row = rs.get_row_data()
    if row[1] == '1' and row[0] >= START_DATE:
        trade_dates.append(row[0])
print(f"    交易日: {len(trade_dates)}")

# ============================
# 3. 加载K线 + 回测
# ============================
print(f"[3] 加载 {len(all_active)} 只股票K线并回测...")

trades = []
env_skip = 0
total_checks = 0
loaded = 0

for idx, code in enumerate(all_active):
    t0 = time.time()
    if (idx+1) % 50 == 0:
        print(f"    进度: {idx+1}/{len(all_active)} | 已加载 {loaded} 只 | 交易 {len(trades)} 笔")
    
    try:
        rs = bs.query_history_k_data_plus(code,
            'date,open,high,low,close,volume',
            start_date=PRELOAD, end_date=END_DATE)
        rows = []
        while rs.next():
            row = rs.get_row_data()
            try:
                rows.append({
                    'date': row[0], 'open': float(row[1]), 'high': float(row[2]),
                    'low': float(row[3]), 'close': float(row[4]), 'volume': float(row[5])
                })
            except: pass
        
        if len(rows) < 40:
            continue
        
        df_k = pd.DataFrame(rows)
        
        # 技术指标
        df_k['MA5'] = df_k['close'].rolling(5).mean()
        df_k['MA10'] = df_k['close'].rolling(10).mean()
        df_k['MA20'] = df_k['close'].rolling(20).mean()
        df_k['VOL5'] = df_k['volume'].rolling(5).mean()
        df_k['VOL20'] = df_k['volume'].rolling(20).mean()
        
        delta = df_k['close'].diff()
        gain = delta.where(delta > 0, 0).rolling(6).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(6).mean()
        df_k['RSI6'] = 100 - 100 / (1 + gain / loss.replace(0, np.nan))
        
        ema12 = df_k['close'].ewm(span=12).mean()
        ema26 = df_k['close'].ewm(span=26).mean()
        df_k['DIF'] = ema12 - ema26
        df_k['DEA'] = df_k['DIF'].ewm(span=9).mean()
        df_k['涨跌幅'] = df_k['close'].pct_change() * 100
        df_k['HIGH20'] = df_k['high'].rolling(20).max().shift(1)
        
        # 流动性检查
        avg_vol = df_k['volume'].iloc[-40:].mean() if len(df_k) >= 40 else df_k['volume'].mean()
        avg_price = df_k['close'].iloc[-40:].mean() if len(df_k) >= 40 else df_k['close'].mean()
        if avg_vol * avg_price < 1e8:
            continue
        
        loaded += 1
        
        # === 每一天的信号检查 ===
        for buy_pos in range(20, len(df_k)):
            buy_date = df_k.iloc[buy_pos]['date']
            if buy_date not in trade_dates:
                continue
            
            # 大盘检查
            if buy_date not in sh_idx:
                env_skip += 1
                continue
            sh_row = sh_idx[buy_date]
            if sh_row['MA5'] is None or sh_row['MA10'] is None or sh_row['MA5'] <= sh_row['MA10']:
                env_skip += 1
                continue
            
            row = df_k.iloc[buy_pos]
            prev = df_k.iloc[buy_pos - 1]
            
            sigs = []
            if 3 <= row['涨跌幅'] <= 7 and row['volume'] >= row['VOL5'] * 1.5: sigs.append(1)
            if row['MA5'] > row['MA10'] > row['MA20'] and row['close'] >= row['MA5']: sigs.append(2)
            if 50 <= row['RSI6'] <= 70: sigs.append(3)
            if prev['DIF'] <= prev['DEA'] and row['DIF'] > row['DEA'] and row['DIF'] > 0: sigs.append(4)
            if row['close'] > row['HIGH20'] and row['volume'] >= row['VOL20'] * 1.2: sigs.append(5)
            if prev['涨跌幅'] >= 9.5 and 1 <= row['涨跌幅'] <= 6 and row['volume'] >= row['VOL5']: sigs.append(6)
            
            total_checks += 1
            if len(sigs) < 2:
                continue
            
            buy_price = row['close']
            
            # 模拟持仓
            sell_idx = -1; sell_price = 0; days_held = 0
            for h in range(1, 6):
                if buy_pos + h >= len(df_k): break
                curr = df_k.iloc[buy_pos + h]
                days_held = h
                prev_close = df_k.iloc[buy_pos + h - 1]['close']
                gain = (curr['close'] - buy_price) / buy_price * 100
                open_gain = (curr['open'] - prev_close) / prev_close * 100
                
                if h == 1 and open_gain >= 5: sell_price = curr['open']; sell_idx = buy_pos + h; break
                if gain >= 8: sell_price = curr['close']; sell_idx = buy_pos + h; break
                if curr['close'] < curr['MA5']:
                    sell_price = df_k.iloc[buy_pos + h + 1]['open'] if buy_pos + h + 1 < len(df_k) else curr['close']
                    sell_idx = buy_pos + h + 1 if buy_pos + h + 1 < len(df_k) else buy_pos + h
                    break
                if gain <= -5: sell_price = curr['close']; sell_idx = buy_pos + h; break
            
            if sell_idx == -1:
                if buy_pos + 5 < len(df_k):
                    sell_price = df_k.iloc[buy_pos + 5]['close']
                    sell_idx = buy_pos + 5; days_held = 5
                else: continue
            
            profit = round((sell_price - buy_price) / buy_price * 100, 2)
            
            # 获取名称（可选）
            name = code  # fallback
            
            trades.append({
                '代码': code,
                '名称': name,
                '买入日': buy_date,
                '卖出日': df_k.iloc[sell_idx]['date'] if sell_idx < len(df_k) else 'N/A',
                '买入价': round(buy_price, 2),
                '卖出价': round(sell_price, 2),
                '盈亏%': profit,
                '持股天数': days_held,
                '信号数': len(sigs)
            })
            
    except Exception as e:
        continue

bs.logout()

# ============================
# 4. 结果
# ============================
print(f"\n[4] 生成报告...")
print(f"    加载股票: {loaded}")
print(f"    交易: {len(trades)}")
print(f"    大盘跳过: {env_skip}")
print(f"    信号检查: {total_checks}")

if len(trades) == 0:
    print("❌ 无交易")
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

print("\n" + "=" * 60)
print("          📊 A股主板超短线策略回测")
print("=" * 60)
print(f"  区间: {START_DATE}~{END_DATE} ({len(trade_dates)}交易日)")
print(f"  标的: {loaded}只主板股（日均成交额>1亿）")
print(f"  总交易: {total}笔")
print(f"  🏆 胜率: {win_rate:.1f}% ({wins}/{total})")
print(f"  📈 均盈亏: {avg_p:+.2f}%")
print(f"  ✅ 均盈: +{avg_w:.2f}%")
print(f"  ❌ 均亏: {avg_l:.2f}%")
if avg_l != 0: print(f"  ⚖️  盈亏比: {abs(avg_w/avg_l):.2f}")
print(f"  🏅 最大盈: +{max_p:.2f}%")
print(f"  💀 最大亏: {max_l:.2f}%")
print(f"  📆 均持股: {df_t['持股天数'].mean():.1f}天")

# 信号数分组
print("\n  📊 按信号数")
for sc in range(6, 1, -1):
    sub = df_t[df_t['信号数']==sc]
    if len(sub)>0:
        sw = len(sub[sub['盈亏%']>0])
        print(f"  {sc}信号: {len(sub):3d}笔 胜率{sw/len(sub)*100:.0f}% 均{sub['盈亏%'].mean():+.2f}%")

# 持股天数
print("\n  📊 按持股天数")
for d in range(1,6):
    sub = df_t[df_t['持股天数']==d]
    if len(sub)>0:
        sw = len(sub[sub['盈亏%']>0])
        print(f"  {d}天: {len(sub):3d}笔 胜率{sw/len(sub)*100:.0f}% 均{sub['盈亏%'].mean():+.2f}%")

# 分布
print("\n  📊 盈亏分布")
bins = [-20,-10,-8,-5,-3,-1,0,1,3,5,8,10,20]
labels = ['<-10%','-10~-8%','-8~-5%','-5~-3%','-3~-1%','-1~0%','0~1%','1~3%','3~5%','5~8%','8~10%','>10%']
df_t['区间'] = pd.cut(df_t['盈亏%'], bins=bins, labels=labels)
dist = df_t['区间'].value_counts()
mc = max(dist) if len(dist)>0 else 1
for l in labels:
    v = dist.get(l,0)
    print(f"  {l:>8}: {v:3d} {'█'*int(v/mc*30)}")

# 累计收益
cum = 1.0
for _, t in df_t.iterrows():
    cum *= (1 + t['盈亏%']/100)
print(f"\n  📊 等权复利累计: {(cum-1)*100:+.2f}%")

path = "/home/admin/.openclaw/workspace/agents/trader/workspace/backtest_result.csv"
df_t.to_csv(path, index=False, encoding='utf-8-sig')
print(f"  📁 backtest_result.csv")

print("\n  📊 最近15笔:")
for _, t in df_t.tail(15).iterrows():
    e = "🟢" if t['盈亏%']>0 else "🔴"
    print(f"  {e} {str(t['代码'])[:10]:>10} | {t['买入日']}→{t['卖出日']} | {int(t['持股天数'])}天 | {t['盈亏%']:+.2f}% | 信号{t['信号数']}")

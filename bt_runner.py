#!/usr/bin/env python3
"""
A股量化回测 - Baostock
多因子选股超短线策略
"""
import baostock as bs
import pandas as pd
import numpy as np
import warnings, sys
warnings.filterwarnings('ignore')

END = "2026-05-18"
START = "2025-12-01"
PRELOAD = "2025-09-01"

lg = bs.login()
if lg.error_code != '0':
    print(f"login failed: {lg.error_msg}"); sys.exit(1)
print("✅ logged in")

# ===== 1. 扫描 =====
print("[1] 扫描活跃股...")

# 沪市主板 sh.600000-sh.605999 , sh.688001-sh.689999(科创板排除)
# 深市主板 sz.000001-sz.001399
# 中小板 sz.002001-sz.002999
# 创业板 sz.300001-sz.301999

def count_trade(code):
    try:
        rs = bs.query_history_k_data_plus(code, 'date,close', start_date=END, end_date=END)
        cnt = 0
        while rs.next(): cnt += 1
        return cnt > 0
    except:
        return False

active = set()

# 沪市主板
for i in range(0, 600, 20):
    batch = [f'sh.60{n:04d}' for n in range(i, i+20)]
    for c in batch:
        if count_trade(c): active.add(c)
    if len(active) >= 100: break

if len(active) < 150:
    # 深市主板
    for i in range(0, 1400, 20):
        batch = [f'sz.{n:06d}' for n in range(i, i+20)]
        for c in batch:
            if count_trade(c): active.add(c)
        if len(active) >= 250: break

if len(active) < 250:
    # 中小板
    for i in range(2000, 3000, 20):
        batch = [f'sz.{n:06d}' for n in range(i, i+20)]
        for c in batch:
            if count_trade(c): active.add(c)
        if len(active) >= 350: break

if len(active) < 350:
    # 创业板
    for i in range(3000, 3200, 20):
        batch = [f'sz.{n:06d}' for n in range(i, i+20)]
        for c in batch:
            if count_trade(c): active.add(c)
        if len(active) >= 400: break

codes = list(active)
print(f"    活跃: {len(codes)} 只")
for c in codes[:10]: print(f"      {c}")

# ===== 2. 大盘 =====
print("[2] 大盘 & 交易日历...")
rs = bs.query_history_k_data_plus('sh.000001', 'date,close', start_date=PRELOAD, end_date=END)
sh_data = []
while rs.next():
    row = rs.get_row_data()
    try: sh_data.append({'date': row[0], 'close': float(row[1])})
    except: pass
sh_df = pd.DataFrame(sh_data)
sh_df['MA5'] = sh_df['close'].rolling(5).mean()
sh_df['MA10'] = sh_df['close'].rolling(10).mean()
sh_df['MA20'] = sh_df['close'].rolling(20).mean()
sh_idx = {r['date']: r for _, r in sh_df.iterrows()}
print(f"    大盘: {len(sh_idx)} 天")

rs = bs.query_trade_dates(start_date=PRELOAD, end_date=END)
td_all = set()
while rs.next():
    row = rs.get_row_data()
    if row[1] == '1': td_all.add(row[0])
td_list = sorted([d for d in td_all if d >= START])
print(f"    交易日: {len(td_list)}")

# ===== 3. 回测 =====
print(f"[3] 回测 {len(codes)} 只...")
trades = []
loaded = 0

for idx, code in enumerate(codes):
    if (idx+1) % 20 == 0:
        print(f"    {idx+1}/{len(codes)} | 加载 {loaded} | 交易 {len(trades)}")
    try:
        rs = bs.query_history_k_data_plus(code,
            'date,open,high,low,close,volume,amount',
            start_date=PRELOAD, end_date=END)
        rows = []
        while rs.next():
            row = rs.get_row_data()
            try: rows.append({'date':row[0],'open':float(row[1]),'high':float(row[2]),
                              'low':float(row[3]),'close':float(row[4]),
                              'volume':float(row[5]),'amount':float(row[6])})
            except: pass
        if len(rows) < 40: continue
        df = pd.DataFrame(rows)

        amt = df['amount'].iloc[-40:].mean()
        if amt < 5e7: continue
        loaded += 1

        # 指标
        df['MA5'] = df['close'].rolling(5).mean()
        df['MA10'] = df['close'].rolling(10).mean()
        df['MA20'] = df['close'].rolling(20).mean()
        df['V5'] = df['volume'].rolling(5).mean()
        df['V20'] = df['volume'].rolling(20).mean()
        df['A5'] = df['amount'].rolling(5).mean()
        df['涨跌幅'] = df['close'].pct_change() * 100

        d = df['close'].diff()
        g = d.where(d>0,0).rolling(6).mean()
        l = (-d.where(d<0,0)).rolling(6).mean()
        df['RSI6'] = 100-100/(1+g/l.replace(0,np.nan))

        e12 = df['close'].ewm(span=12).mean()
        e26 = df['close'].ewm(span=26).mean()
        df['DIF'] = e12-e26
        df['DEA'] = df['DIF'].ewm(span=9).mean()

        df['BM'] = df['close'].rolling(20).mean()
        s20 = df['close'].rolling(20).std()
        df['BU'] = df['BM'] + 2*s20

        df['H20'] = df['high'].rolling(20).max().shift(1)

        l9 = df['low'].rolling(9).min()
        h9 = df['high'].rolling(9).max()
        df['RSV'] = (df['close']-l9)/(h9-l9).replace(0,np.nan)*100
        df['K'] = df['RSV'].ewm(com=2).mean()
        df['D_kd'] = df['K'].ewm(com=2).mean()

        # 信号扫描
        for bp in range(30, len(df)-1):
            bd = df.iloc[bp]['date']
            if bd not in td_list: continue
            if bd in sh_idx:
                sr = sh_idx[bd]
                if sr['MA5'] is None or sr['MA10'] is None or sr['close'] < sr['MA10']:
                    continue

            r = df.iloc[bp]
            p = df.iloc[bp-1]
            sigs = []
            pct = r['涨跌幅']

            if 3 <= pct <= 7 and r['volume'] >= r['V5']*1.5: sigs.append('S1放量')
            if r['MA5'] > r['MA10'] > r['MA20'] and r['close'] >= r['MA5']: sigs.append('S2多头')
            if 50 <= r['RSI6'] <= 72: sigs.append('S3RSI')
            if p['DIF'] <= p['DEA'] and r['DIF'] > r['DEA']: sigs.append('S4MACD')
            if r['close'] > r['H20'] and r['volume'] >= r['V20']*1.2: sigs.append('S5新高')
            if p['涨跌幅'] >= 9.5 and 1 <= pct <= 6 and r['volume'] >= r['V5']: sigs.append('S6续强')
            if r['close'] > r['BM']: sigs.append('S7布林')
            if p['K'] <= p['D_kd'] and r['K'] > r['D_kd']: sigs.append('S8KD')
            if r['V5'] > r['V20']: sigs.append('S9量')

            if len(sigs) < 3: continue

            bp_ = r['close']
            sc = len(sigs)
            sp = None; si = None; dh = 0

            for h in range(1,6):
                if bp+h >= len(df): break
                c = df.iloc[bp+h]; dh = h
                g = (c['close']-bp_)/bp_*100
                if h==1 and (c['open']-bp_)/bp_*100 >= 5:
                    sp=c['open']; si=bp+h; break
                if g>=8: sp=c['close']; si=bp+h; break
                if c['low'] < c['MA5']:
                    if bp+h+1 < len(df): sp=df.iloc[bp+h+1]['open']; si=bp+h+1
                    else: sp=c['close']; si=bp+h
                    break
                if g<=-5: sp=c['close']; si=bp+h; break
            if si is None:
                if bp+5 < len(df): sp=df.iloc[bp+5]['close']; si=bp+5; dh=5
                else: continue
            if sp is None: continue

            pr = round((sp-bp_)/bp_*100,2)
            nm = code.replace('sh.','').replace('sz.','')
            trades.append({'代码':nm,'买入日':bd,'卖出日':df.iloc[si]['date'],
                           '买入价':round(bp_,2),'卖出价':round(sp,2),'盈亏%':pr,
                           '持股天数':dh,'信号数':sc,'信号':'+'.join(sigs)})
    except: continue

bs.logout()

# ===== 4. 报告 =====
print(f"\n{'='*60}")
print(f"       📊 A股量化回测报告")
print(f"        2025-12-01 ~ 2026-05-18")
print(f"{'='*60}")

if not trades:
    print("❌ 无交易")
    sys.exit(0)

dt = pd.DataFrame(trades)
t=len(dt); w=len(dt[dt['盈亏%']>0]); l_=len(dt[dt['盈亏%']<0])
wr=w/t*100; ap=dt['盈亏%'].mean()
aw=dt[dt['盈亏%']>0]['盈亏%'].mean() if w>0 else 0
al=dt[dt['盈亏%']<0]['盈亏%'].mean() if l_>0 else 0
mx=dt['盈亏%'].max(); mn=dt['盈亏%'].min(); md=dt['盈亏%'].median()

cum=1.0
for _,r_ in dt.iterrows(): cum*=(1+r_['盈亏%']/100)
cr=(cum-1)*100
sharpe=(ap/100)/(dt['盈亏%'].std()/100)*np.sqrt(245) if dt['盈亏%'].std()>0 else 0

print(f"  标的池: {loaded} 只 (日均≥5000万)")
print(f"  总交易: {t} 笔")
print(f"  胜率: {wr:.1f}% ({w}胜/{l_}负)")
print(f"  均盈亏: {ap:+.2f}%")
print(f"  均盈利: +{aw:.2f}% | 均亏损: {al:.2f}%")
if al!=0: print(f"  盈亏比: {abs(aw/al):.2f}")
print(f"  最大: +{mx:.2f}% / {mn:.2f}%")
print(f"  中位: {md:+.2f}%")
print(f"  均持股: {dt['持股天数'].mean():.1f}天")
print(f"  等权复利: {cr:+.2f}%")
print(f"  年化夏普: {sharpe:.2f}")

print(f"\n  📊 按信号数")
for sc in sorted(dt['信号数'].unique(), reverse=True):
    sub=dt[dt['信号数']==sc]; sw=len(sub[sub['盈亏%']>0])
    print(f"  {sc}信号: {len(sub):3d}笔 胜率{sw/len(sub)*100:.0f}% 均{sub['盈亏%'].mean():+.2f}%")

print(f"\n  📊 按持股天数")
for d in sorted(dt['持股天数'].unique()):
    sub=dt[dt['持股天数']==d]; sw=len(sub[sub['盈亏%']>0])
    print(f"  {d}天: {len(sub):3d}笔 胜率{sw/len(sub)*100:.0f}% 均{sub['盈亏%'].mean():+.2f}%")

print(f"\n  📊 盈亏分布")
bins=[-20,-10,-8,-5,-3,-1,0,1,3,5,8,10,20]
lbs=['<-10%','-10~-8%','-8~-5%','-5~-3%','-3~-1%','-1~0%','0~1%','1~3%','3~5%','5~8%','8~10%','>10%']
dt['区间']=pd.cut(dt['盈亏%'],bins=bins,labels=lbs)
dist=dt['区间'].value_counts()
mc=max(dist) if len(dist)>0 else 1
for lb in lbs:
    v=dist.get(lb,0)
    if v>0: print(f"  {lb:>8}: {v:3d} {'█'*int(v/mc*30)}")

print(f"\n  📊 最近20笔")
for _,r_ in dt.tail(20).iterrows():
    e="🟢" if r_['盈亏%']>0 else "🔴"
    print(f"  {e} {r_['代码']:>6} | {r_['买入日']}→{r_['卖出日']} | {int(r_['持股天数'])}天 | {r_['盈亏%']:+7.2f}% | [{r_['信号数']}信号]")

path="/home/admin/.openclaw/workspace/agents/zidong/workspace/bt_result.csv"
dt.to_csv(path,index=False,encoding='utf-8-sig')
print(f"\n  📁 {path}")

import baostock as bs, pandas as pd, numpy as np, warnings, sys, time
warnings.filterwarnings('ignore')
END, START, PRE = "2026-05-08", "2025-12-01", "2025-09-01"

lg = bs.login()
if lg.error_code != '0': sys.exit(1)
print("✅ logged in")

print("Phase 1: scanning...")
active = []
for i in range(0, 500):
    code = f'sh.60{i:04d}'
    try:
        rs = bs.query_history_k_data_plus(code, 'date,close', start_date=END, end_date=END)
        cnt = 0
        while rs.next(): cnt += 1
        if cnt > 0: 
            active.append(code)
            if len(active) >= 150: break
    except: pass

print(f"Active sh: {len(active)}")

if len(active) < 100:
    for i in range(1, 500):
        code = f'sz.{i:06d}'
        try:
            rs = bs.query_history_k_data_plus(code, 'date,close', start_date=END, end_date=END)
            cnt = 0
            while rs.next(): cnt += 1
            if cnt > 0:
                active.append(code)
                if len(active) >= 200: break
        except: pass

if len(active) < 100:
    for i in range(1, 300):
        code = f'sz.002{i:03d}'
        try:
            rs = bs.query_history_k_data_plus(code, 'date,close', start_date=END, end_date=END)
            cnt = 0
            while rs.next(): cnt += 1
            if cnt > 0:
                active.append(code)
                if len(active) >= 200: break
        except: pass

print(f"Total active: {len(active)}")
if len(active) < 20:
    print("Too few stocks, aborting")
    bs.logout()
    sys.exit(1)

# Phase 2: get SH index
print("Phase 2: index...")
rs = bs.query_history_k_data_plus('sh.000001', 'date,close', start_date=PRE, end_date=END)
sh = []
while rs.next():
    r = rs.get_row_data()
    try: sh.append({'date':r[0],'close':float(r[1])})
    except: pass
sh_df = pd.DataFrame(sh)
sh_df['MA5'] = sh_df['close'].rolling(5).mean()
sh_df['MA10'] = sh_df['close'].rolling(10).mean()
sh_idx = {r['date']:r for _,r in sh_df.iterrows()}

rs = bs.query_trade_dates(start_date=PRE, end_date=END)
tdates = []
while rs.next():
    r = rs.get_row_data()
    if r[1]=='1' and r[0]>=START: tdates.append(r[0])
print(f"Trade days: {len(tdates)}")

# Phase 3: load K-lines & backtest
print("Phase 3: loading & backtesting...")
trades = []
loaded = 0

for idx, code in enumerate(active):
    if (idx+1)%20==0: print(f"  {idx+1}/{len(active)} loaded={loaded} trades={len(trades)}")
    try:
        rs = bs.query_history_k_data_plus(code, 'date,open,high,low,close,volume', start_date=PRE, end_date=END)
        rows = []
        while rs.next():
            r = rs.get_row_data()
            try: rows.append({'date':r[0],'open':float(r[1]),'high':float(r[2]),'low':float(r[3]),'close':float(r[4]),'volume':float(r[5])})
            except: pass
        if len(rows) < 40: continue
        
        df = pd.DataFrame(rows)
        df['MA5'] = df['close'].rolling(5).mean()
        df['MA10'] = df['close'].rolling(10).mean()
        df['MA20'] = df['close'].rolling(20).mean()
        df['VOL5'] = df['volume'].rolling(5).mean()
        df['VOL20'] = df['volume'].rolling(20).mean()
        d = df['close'].diff()
        g = d.where(d>0,0).rolling(6).mean()
        l = (-d.where(d<0,0)).rolling(6).mean()
        df['RSI6'] = 100-100/(1+g/l.replace(0,np.nan))
        e12 = df['close'].ewm(span=12).mean()
        e26 = df['close'].ewm(span=26).mean()
        df['DIF'] = e12 - e26
        df['DEA'] = df['DIF'].ewm(span=9).mean()
        df['PCT'] = df['close'].pct_change()*100
        df['H20'] = df['high'].rolling(20).max().shift(1)
        
        # liquidity check
        av = df['volume'].iloc[-40:].mean()
        ap_ = df['close'].iloc[-40:].mean()
        if av*ap_ < 1e8: continue
        loaded += 1
        
        for bp in range(20, len(df)):
            bd = df.iloc[bp]['date']
            if bd not in tdates: continue
            if bd not in sh_idx: continue
            sh_r = sh_idx[bd]
            if sh_r['MA5'] is None or sh_r['MA10'] is None or sh_r['MA5']<=sh_r['MA10']: continue
            
            row, prev = df.iloc[bp], df.iloc[bp-1]
            sigs = []
            if 3<=row['PCT']<=7 and row['volume']>=row['VOL5']*1.5: sigs.append(1)
            if row['MA5']>row['MA10']>row['MA20'] and row['close']>=row['MA5']: sigs.append(2)
            if 50<=row['RSI6']<=70: sigs.append(3)
            if prev['DIF']<=prev['DEA'] and row['DIF']>row['DEA'] and row['DIF']>0: sigs.append(4)
            if row['close']>row['H20'] and row['volume']>=row['VOL20']*1.2: sigs.append(5)
            if prev['PCT']>=9.5 and 1<=row['PCT']<=6 and row['volume']>=row['VOL5']: sigs.append(6)
            if len(sigs)<2: continue
            
            bp_ = row['close']
            si, sp, dh = -1, 0, 0
            for h in range(1,6):
                if bp+h>=len(df): break
                c = df.iloc[bp+h]; dh = h
                pc_ = df.iloc[bp+h-1]['close']
                g_ = (c['close']-bp_)/bp_*100
                og = (c['open']-pc_)/pc_*100
                if h==1 and og>=5: sp=c['open']; si=bp+h; break
                if g_>=8: sp=c['close']; si=bp+h; break
                if c['close']<c['MA5']:
                    sp = df.iloc[bp+h+1]['open'] if bp+h+1<len(df) else c['close']
                    si = bp+h+1 if bp+h+1<len(df) else bp+h; break
                if g_<=-5: sp=c['close']; si=bp+h; break
            if si==-1 and bp+5<len(df): sp=df.iloc[bp+5]['close']; si=bp+5; dh=5
            elif si==-1: continue
            
            profit = round((sp-bp_)/bp_*100,2)
            trades.append({'code':code[:12],'buy':bd,'sell':df.iloc[si]['date'] if si<len(df) else '?',
                          'pct':profit,'days':dh,'sigs':len(sigs)})
    except Exception as e:
        continue

bs.logout()

# RESULTS
print(f"\n=== RESULTS ===")
print(f"Loaded: {loaded}, Trades: {len(trades)}")
if len(trades)==0: print("No trades"); sys.exit(0)

df_t = pd.DataFrame(trades)
w = len(df_t[df_t['pct']>0])
print(f"Win rate: {w}/{len(df_t)} = {w/len(df_t)*100:.1f}%")
print(f"Avg profit: {df_t['pct'].mean():+.2f}%")
print(f"Avg win: {df_t[df_t['pct']>0]['pct'].mean():+.2f}%")
print(f"Avg loss: {df_t[df_t['pct']<0]['pct'].mean():.2f}%")
print(f"Max: +{df_t['pct'].max():.2f}% / {df_t['pct'].min():.2f}%")
print(f"Avg days: {df_t['days'].mean():.1f}")

# By signal count
print("\nBy signal count:")
for sc in range(6,1,-1):
    s = df_t[df_t['sigs']==sc]
    if len(s)>0:
        sw = len(s[s['pct']>0])
        print(f"  {sc}s: {len(s):3d} trades, WR {sw/len(s)*100:.0f}%, avg {s['pct'].mean():+.2f}%")

# Cumulative
cum = 1.0
for _, t in df_t.iterrows():
    cum *= (1+t['pct']/100)
print(f"\nCum return: {(cum-1)*100:+.2f}%")

# By hold days
print("\nBy hold days:")
for d in range(1,6):
    s = df_t[df_t['days']==d]
    if len(s)>0:
        sw = len(s[s['pct']>0])
        print(f"  {d}d: {len(s):3d} trades, WR {sw/len(s)*100:.0f}%, avg {s['pct'].mean():+.2f}%")

df_t.to_csv("bt_result.csv", index=False, encoding='utf-8-sig')
print(f"\nSaved bt_result.csv")

print("\nLast 20:")
for _, t in df_t.tail(20).iterrows():
    e = "🟢" if t['pct']>0 else "🔴"
    print(f"  {e} {t['code']:>10} | {t['buy']}→{t['sell']} | {int(t['days'])}d | {t['pct']:+.2f}% | {int(t['sigs'])}sig")

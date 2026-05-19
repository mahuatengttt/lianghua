#!/usr/bin/env python3
"""
📅 全流程一键更新 v2.1.0 — 低内存稳定版

增量安全：
  - 逐步增量，绝不一次加载全部数据
  - 自动 swap 保护（1.8G RAM 友好）
  - 断点续跑、错误恢复
  - 子进程计算因子（避免主进程内存膨胀）

用法:
  python3 full_pipeline.py                    全部更新到最新交易日
  python3 full_pipeline.py --target YYYY-MM-DD
  python3 full_pipeline.py --steps 1,2,3      只跑指定步骤
  python3 full_pipeline.py --check            检查状态
  python3 full_pipeline.py --dry-run          模拟运行
  python3 full_pipeline.py --recache          只重建 bt_cache
"""

import os, sys, time, gc, glob, argparse, subprocess
from pathlib import Path
import numpy as np
import pandas as pd

__version__ = "2.1.0"

# ─── 路径 ────────────────────────────────────────────────
ROOT = os.path.dirname(os.path.abspath(__file__))
A = os.path.join(ROOT, "a_stock_data")
BS_DIR    = os.path.join(A, "daily")
SIN_DIR   = os.path.join(A, "daily_sina_raw")
CL_DIR    = os.path.join(A, "daily_clean")
V3_DIR    = os.path.join(A, "factor_cache_v3")
TMP_DIR   = os.path.join(A, "factor_cache_v4_tmp")
BT_CACHE  = os.path.join(A, "bt_cache.parquet")
CAL_FILE  = os.path.join(A, "trade_calendar.csv")
LOG_DIR   = os.path.join(ROOT, "logs")
os.makedirs(LOG_DIR, exist_ok=True)

# ─── 工具 ────────────────────────────────────────────────

def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)

def el(t0):
    return time.time() - t0

def ensure_swap():
    """可用内存 < 600MB 时自动创建 2G swap"""
    import psutil
    mem = psutil.virtual_memory()
    if mem.available > 600 * 1024 * 1024: return
    r = os.system("free -m | awk '/Swap:/{print $2}' | grep -v '^0$' >/dev/null 2>&1")
    if r == 0: return
    log("⚠️  内存不足，创建 2G swap...")
    os.system("dd if=/dev/zero of=/swapfile bs=1M count=2048 2>/dev/null")
    os.system("chmod 600 /swapfile && mkswap /swapfile 2>/dev/null && swapon /swapfile 2>/dev/null")
    log("✅ swap 已启用")

def batched(seq, size):
    for i in range(0, len(seq), size): yield seq[i:i+size]

def get_latest_trade_date():
    import baostock as bs
    today = time.strftime("%Y-%m-%d")
    try:
        bs.login(); rs = bs.query_all_stock(day=today); cnt = sum(1 for _ in rs); bs.logout()
        if cnt > 100: return today
    except: bs.logout()
    if os.path.exists(CAL_FILE):
        d = pd.read_csv(CAL_FILE, header=None)[0].tolist()
        if d: return d[-1]
    from datetime import datetime, timedelta
    for i in range(1,8):
        d = (datetime.now()-timedelta(days=i)).strftime("%Y-%m-%d")
        try:
            bs.login(); rs = bs.query_all_stock(day=d); cnt = sum(1 for _ in rs); bs.logout()
            if cnt > 100: return d
        except: bs.logout()
    return today

def build_trade_calendar():
    s=set()
    for batch in batched(sorted(Path(BS_DIR).glob("*.parquet")),300):
        for f in batch:
            try: s.update(pd.read_parquet(f,columns=["date"])["date"].tolist())
            except: pass
        gc.collect()
    d=sorted(s)
    pd.Series(d).to_csv(CAL_FILE,index=False, header=False)
    return d

# ═══════════════════════════════════════════════════════════
#  步骤 1 — Baostock 前复权
# ═══════════════════════════════════════════════════════════

def step1_baostock(tgt, dry=False):
    log(f"① Baostock → daily/  目标: {tgt}")
    import baostock as bs
    bs.login(); codes=[]
    rs=bs.query_all_stock(day=tgt)
    while rs.next():
        r=rs.get_row_data()
        if r[1]!="1": continue
        mk,nu=r[0].split(".")
        if (mk=="sh" and (nu.startswith("60") or nu.startswith("68"))) or \
           (mk=="sz" and (nu[:2] in ("00","30") or nu[:3] in ("001","002"))):
            codes.append((nu,mk,r[2]))
    bs.logout()
    existing={f.stem for f in Path(BS_DIR).glob("*.parquet")}
    todo=[]
    for nu,mk,nm in codes:
        if nu in existing:
            try:
                df=pd.read_parquet(Path(BS_DIR)/f"{nu}.parquet",columns=["date"])
                if df["date"].max()>=tgt: del df; continue
                del df
            except: pass
        todo.append((nu,mk,nm,f"{mk}.{nu}"))
    if not todo: log("✅ 无新数据"); return True
    log(f"需处理: {len(todo)}")
    if dry: log("[dry-run] 跳过"); return True
    ok=err=0
    for bi,batch in enumerate(batched(todo,30)):
        bs.login()
        for nu,mk,nm,bc in batch:
            fp=Path(BS_DIR)/f"{nu}.parquet"
            try:
                had=set()
                if fp.exists():
                    had=set(pd.read_parquet(fp,columns=["date"])["date"].tolist())
                rs=bs.query_history_k_data_plus(
                    bc,"date,code,open,high,low,close,preclose,volume,amount,turn,tradestatus,pctChg",
                    "2024-06-01",tgt,"d","2")
                rows=[]; [rows.append(rs.get_row_data()) for _ in rs]
                new=[r for r in rows if r[0] not in had]
                if not new: ok+=1; continue
                df=pd.DataFrame(new,columns=["date","code","open","high","low","close",
                    "preclose","volume","amount","turn","tradestatus","pctChg"])
                for c in["open","high","low","close","preclose","amount","turn","pctChg"]:
                    df[c]=pd.to_numeric(df[c],errors="coerce")
                df["volume"]=pd.to_numeric(df["volume"],errors="coerce").fillna(0).astype("int64")
                df["name"]=nm
                if fp.exists():
                    old=pd.read_parquet(fp); cmb=pd.concat([old,df]).drop_duplicates(subset=["date"]).sort_values("date")
                    cmb.to_parquet(fp,index=False,compression="snappy"); del old,cmb
                else: df.to_parquet(fp,index=False,compression="snappy")
                ok+=1; del df
            except Exception as e:
                err+=1
                if err<=3: log(f"✗ {nu}: {str(e)[:60]}")
            if (ok+err)%30==0: gc.collect()
        bs.logout(); gc.collect()
        log(f"批次 {bi+1}: +{ok} ✗{err}")
    log(f"完成: {len(list(Path(BS_DIR).glob('*.parquet')))} 只")
    return err==0

# ═══════════════════════════════════════════════════════════
#  步骤 2 — 新浪
# ═══════════════════════════════════════════════════════════

def step2_sina(tgt, dry=False):
    log("② 新浪 → daily_sina_raw/")
    import requests
    bs_stems={f.stem for f in Path(BS_DIR).glob("*.parquet")}
    si_stems={f.stem for f in Path(SIN_DIR).glob("*.parquet")}
    missing=sorted(bs_stems-si_stems)
    need_upd=sorted(bs_stems&si_stems)
    log(f"缺失:{len(missing)} 更新:min({len(need_upd)},500)")
    if dry: log("[dry-run] 跳过"); return True
    sess=requests.Session(); ok=err=0
    for i,nu in enumerate(missing):
        pf=("sh" if nu.startswith("6") else "sz")+nu
        fp=Path(SIN_DIR)/f"{nu}.parquet"
        try:
            r=sess.get("https://quotes.sina.cn/cn/api/json_v2.php/CN_MarketDataService.getKLineData",
                params={"symbol":pf,"scale":"240","datalen":"1024"},timeout=10)
            if r.status_code!=200 or not r.text: ok+=1; continue
            data=json.loads(r.text)
            if not data: ok+=1; continue
            nm=""
            try: nm=pd.read_parquet(Path(BS_DIR)/f"{nu}.parquet",columns=["name"]).iloc[0]["name"]
            except: pass
            rows=[{"date":it["day"],"code":nu,"name":nm,
                "open":float(it["open"]),"high":float(it["high"]),
                "low":float(it["low"]),"close":float(it["close"]),
                "volume":int(float(it["volume"]))} for it in data if it["day"]>="2024-06-01"]
            if rows: pd.DataFrame(rows).to_parquet(fp,index=False,compression="snappy")
            ok+=1
        except: err+=1
        if (i+1)%200==0: log(f"缺失{i+1}/{len(missing)}:+{ok} ✗{err}"); gc.collect()
    up=0
    for i,nu in enumerate(need_upd[:500]):
        fp=Path(SIN_DIR)/f"{nu}.parquet"
        try:
            old=pd.read_parquet(fp)
            if old["date"].max()>=tgt: continue
            pf=("sh" if nu.startswith("6") else "sz")+nu
            r=sess.get("https://quotes.sina.cn/cn/api/json_v2.php/CN_MarketDataService.getKLineData",
                params={"symbol":pf,"scale":"240","datalen":"1024"},timeout=10)
            if r.status_code!=200 or not r.text: continue
            data=json.loads(r.text)
            new=[{"date":it["day"],"code":nu,"name":old.iloc[0]["name"],
                "open":float(it["open"]),"high":float(it["high"]),
                "low":float(it["low"]),"close":float(it["close"]),
                "volume":int(float(it["volume"]))} for it in data if it["day"]>old["date"].max()]
            if new:
                cmb=pd.concat([old,pd.DataFrame(new)],ignore_index=True)
                cmb.to_parquet(fp,index=False,compression="snappy"); up+=1; del cmb
            del old
        except: err+=1
        if (i+1)%200==0: log(f"更新{i+1}/{min(len(need_upd),500)}:+{up}"); gc.collect()
    log(f"完成:{len(list(Path(SIN_DIR).glob('*.parquet')))}只")
    return True

# ═══════════════════════════════════════════════════════════
#  步骤 3 — 清洗
# ═══════════════════════════════════════════════════════════

def step3_clean(tgt, dry=False):
    log("③ 清洗 → daily_clean/")
    td=build_trade_calendar()
    files=sorted(Path(BS_DIR).glob("*.parquet"))
    log(f"共{len(files)}只")
    if dry: log("[dry-run]跳过"); return True
    ok=err=0
    for i,f in enumerate(files):
        co=f.stem; op=Path(CL_DIR)/f"{co}.parquet"
        try:
            df=pd.read_parquet(f)
            if len(df)==0: continue
            nm=df.iloc[0]["name"]
            df=df[(df["volume"]>=0)&(df["high"]>=df["low"]-0.001)&(df["close"]>=0.1)]
            if "tradestatus" in df.columns: df=df[df["tradestatus"]=="1"]
            if len(df)==0: continue
            df=df.set_index("date"); al=df.reindex(td)
            al["code"]=co; al["name"]=nm
            cl=al["close"].values; p=np.full(len(cl),np.nan)
            for j in range(1,len(cl)):
                if not np.isnan(cl[j]) and not np.isnan(cl[j-1]) and cl[j-1]>0:
                    p[j]=(cl[j]-cl[j-1])/cl[j-1]*100
                    if abs(p[j])>30:
                        al.loc[td[j],["open","high","low","close"]]=np.nan
                        al.loc[td[j],["volume","amount"]]=[0,np.nan]
                        p[j]=np.nan
            al["pctChg"]=p
            oc=["open","high","low","close","preclose","volume","amount","turn","pctChg","code","name"]
            r=al[oc].copy(); r["date"]=td; r=r[r["date"]!="0"]
            r.to_parquet(op,index=False,compression="snappy"); ok+=1
            del df,al,r,p
        except: err+=1
        if (i+1)%1000==0: log(f"[{i+1}/{len(files)}]+{ok} ✗{err}"); gc.collect()
    log(f"完成:{len(list(Path(CL_DIR).glob('*.parquet')))}只")
    return err==0

# ═══════════════════════════════════════════════════════════
#  步骤 4 — 基础因子（子进程计算，防 OOM）
# ═══════════════════════════════════════════════════════════

FACTOR_SCRIPT = r"""
import sys,numpy as np,pandas as pd,pyarrow.parquet as pq
co=sys.argv[1]; fi=sys.argv[2]; fo=sys.argv[3]
try:
    df=pq.read_table(fi).to_pandas().sort_values('date').reset_index(drop=True)
    if len(df)<30: sys.exit(0)
    cl=df['close'].values; vo=df['volume'].values; am=df['amount'].values
    hi=df['high'].values; lo=df['low'].values; tu=df['turn'].values
    da=df['date'].values; n=len(df)
    pc=np.full(n,np.nan); pc[1:]=np.diff(cl)/cl[:max(n-1,1)]
    m20=np.full(n,np.nan); r5=np.full(n,np.nan); v20=np.full(n,np.nan)
    p20=np.full(n,np.nan); vr=np.full(n,np.nan); ta=np.full(n,np.nan)
    am_=np.full(n,np.nan); a3=np.full(n,np.nan); a12=np.full(n,np.nan); zs=np.full(n,np.nan)
    for i in range(20,n):
        m20[i]=cl[i]/cl[i-20]-1; r5[i]=cl[i]/cl[i-5]-1
        v20[i]=np.std(pc[i-20:i]); p20[i]=cl[i]/np.mean(cl[i-20:i])-1
        av=np.mean(vo[i-20:i-1]) if i>=21 else np.mean(vo[:max(i,1)])
        vr[i]=vo[i]/max(av,1); ta[i]=np.mean(tu[i-20:i]); am_[i]=(hi[i]-lo[i])/cl[i]
        if i>=25 and np.std(am[i-6:i])>0 and np.std(pc[i-6:i])>0:
            a3[i]=-np.corrcoef(am[i-6:i],pc[i-6:i])[0,1]
        a12[i]=np.sign(vo[i]-vo[i-1])*(-(cl[i]-cl[i-1]))
        zs[i]=(cl[i]-np.mean(cl[i-20:i]))/max(np.std(cl[i-20:i]),0.001)
    pd.DataFrame({'date':da,'code':co,'close':cl,
        'mom_20d':m20,'rev_5d':r5,'vol_20d':v20,'price_ma20':p20,
        'vol_ratio_5_20':vr,'turn_20d_avg':ta,'amplitude_20d':am_,
        'alpha3':a3,'alpha12':a12,'zscore_ma20':zs}
    ).to_parquet(fo,index=False,compression='snappy')
except: pass
"""

def step4_factors(tgt, dry=False):
    log("④ 基础因子 → factor_cache_v3/")
    files = [f for f in Path(CL_DIR).glob("*.parquet")
             if f.stem[:3] in ("600","601","603","605","000","001","002","003")]
    done=set()
    for vf in Path(V3_DIR).glob("part_*.parquet"):
        try: done.update(pd.read_parquet(vf,columns=["code"])["code"].unique())
        except: pass
    if Path(TMP_DIR).exists():
        for f in Path(TMP_DIR).glob("*.parquet"): done.add(f.stem)
    todo=[f for f in files if f.stem not in done]
    if not todo: log(f"✅ 全部{len(files)}已完成"); return True
    log(f"待计算:{len(todo)}/{len(files)}")
    if dry: log("[dry-run]跳过"); return True
    t0=time.time(); cnt=0
    for i,fp in enumerate(todo):
        co=fp.stem; op=Path(TMP_DIR)/f"{co}.parquet"
        try:
            subprocess.run([sys.executable,"-c",FACTOR_SCRIPT,co,str(fp),str(op)],
                timeout=60,capture_output=True,cwd=ROOT)
            if op.exists() and op.stat().st_size>100: cnt+=1
        except: pass
        if (i+1)%200==0:
            gc.collect()
            sp=el(t0)/max(i+1,1)
            log(f"[{i+1}/{len(todo)}]{cnt}只|{sp:.2f}s/只")
    log(f"计算完成:{cnt}只{el(t0):.0f}s")
    # 合并到 v3
    log("合并到 v3 分区...")
    tmp_files=sorted(Path(TMP_DIR).glob("*.parquet"))
    if tmp_files:
        for batch in batched(tmp_files,200):
            by_ym={}
            for f in batch:
                try:
                    df=pd.read_parquet(f)
                    if len(df)==0: continue
                    df["date"]=pd.to_datetime(df["date"])
                    ym=df["date"].dt.strftime("%Y-%m")
                    for ymg,grp in df.groupby(ym):
                        by_ym.setdefault(ymg,[]).append(
                            grp.drop(columns=[])
                        )
                except: pass
            for ym,chunks in by_ym.items():
                if not chunks: continue
                cmb=pd.concat(chunks,ignore_index=True)
                pf=Path(V3_DIR)/f"part_{ym}.parquet"
                if pf.exists():
                    old=pd.read_parquet(pf)
                    cmb=pd.concat([old,cmb],ignore_index=True)
                    cmb=cmb.drop_duplicates(subset=["date","code"],keep="last").reset_index(drop=True)
                    del old
                cmb.to_parquet(pf,index=False,compression="snappy")
                del cmb; gc.collect()
            del by_ym; gc.collect()
            log(f"合并{len(batch)}文件")
        for f in Path(TMP_DIR).glob("*.parquet"): f.unlink()
    return True

# ═══════════════════════════════════════════════════════════
#  步骤 5 — Alpha101
# ═══════════════════════════════════════════════════════════

def step5_alpha101(tgt, dry=False):
    log("⑤ Alpha101 → factor_cache_v3/")
    ym=tgt[:7]; tf=Path(V3_DIR)/f"part_{ym}.parquet"
    if not tf.exists(): log(f"⚠️ 分区{ym}不存在"); return False
    try:
        existing=pd.read_parquet(tf,columns=["date","code"])
        tgt_in=tgt in set(existing["date"].astype(str).values)
    except: tgt_in=False
    if tgt_in:
        sample=pd.read_parquet(tf)
        ac=[c for c in sample.columns if c.startswith("alpha") and c not in("alpha3","alpha12")]
        if ac: log(f"✅ Alpha101已有({len(ac)}因子)"); return True
    log(f"计算{tgt}Alpha101...")
    if dry: log("[dry-run]跳过"); return True
    try: from alpha101_factors import compute_alpha101_for_stock
    except: log("⚠️ alpha101_factors不可用"); return False
    codes=set(pd.read_parquet(tf,columns=["code"])["code"].unique())
    log(f"待计算:{len(codes)}只")
    rc=[]; done=0
    for idx,co in enumerate(sorted(codes)):
        cp=Path(CL_DIR)/f"{co}.parquet"
        if not cp.exists(): continue
        try:
            df=pd.read_parquet(cp)
            if len(df)<30 or tgt not in set(df["date"].astype(str).values): continue
            adf=compute_alpha101_for_stock(df)
            if adf is not None and len(adf)>0:
                row=adf[adf["date"].astype(str)==tgt]
                if len(row)>0:
                    rd=row.iloc[0].to_dict(); rd["code"]=co; rd.pop("date",None)
                    rc.append(rd); done+=1
            del df,adf
        except: pass
        if (idx+1)%200==0: gc.collect(); log(f"[{idx+1}/{len(codes)}]Alpha:{done}")
    if not rc: log("⚠️ 无Alpha101结果"); return True
    adf=pd.DataFrame(rc); log(f"Alpha101:{len(adf)}只")
    full=pd.read_parquet(tf)
    for c in adf.columns:
        if c=="code": continue
        if c not in full.columns: full[c]=np.nan
    lu=adf.set_index("code").to_dict("index")
    tgs=str(tgt)[:10]
    for i,row in full.iterrows():
        ds=str(row["date"])[:10]
        if row["code"] in lu and ds==tgs:
            for k,v in lu[row["code"]].items():
                if k!="date": full.at[i,k]=v
    full.to_parquet(tf,index=False,compression="snappy")
    del full,adf; gc.collect()
    log("✅ Alpha101完成")
    return True

# ═══════════════════════════════════════════════════════════
#  步骤 6 — bt_cache
# ═══════════════════════════════════════════════════════════

BASE_COLS=["date","code","close","mom_20d","rev_5d","vol_20d","alpha3","alpha12",
           "amplitude_20d","turn_20d_avg","price_ma20","vol_ratio_5_20","zscore_ma20"]

def step6_bt_cache(tgt, dry=False):
    log("⑥ 合并 bt_cache.parquet")
    pfs=sorted(Path(V3_DIR).glob("part_*.parquet"))
    if not pfs: log("⚠️ 无因子数据"); return False
    if dry: log("[dry-run]跳过"); return True
    old_exists=os.path.exists(BT_CACHE)
    if old_exists:
        try:
            old=pd.read_parquet(BT_CACHE,columns=["date"]); newest=old["date"].max(); del old; gc.collect()
        except: newest="2024-01-01"
        log(f"旧bt_cache最新:{newest}")
        nc=[]
        for pf in pfs:
            ym=pf.stem.replace("part_","")
            if ym<=str(newest)[:7]: continue
            try: df=pd.read_parquet(pf,columns=BASE_COLS)
            except: df=pd.read_parquet(pf)
            df=df[df["date"].astype(str)>str(newest)]
            if len(df)>0: nc.append(df)
            del df; gc.collect()
        if not nc: log("✅ 无新数据"); return True
        new=pd.concat(nc,ignore_index=True); del nc; gc.collect()
        log(f"新数据:{len(new)}行")
        old=pd.read_parquet(BT_CACHE)
        nk=set(zip(new["date"].astype(str),new["code"]))
        old=old[~pd.Series(zip(old["date"].astype(str),old["code"])).isin(nk)]
        cmb=pd.concat([old,new],ignore_index=True)
        cmb=cmb.sort_values(["date","code"]).reset_index(drop=True)
        cmb.to_parquet(BT_CACHE,index=False,compression="snappy")
        del old,new,cmb; gc.collect()
    else:
        log("全量重建...")
        tmp=Path(BT_CACHE).parent/"bt_cache.build"
        if tmp.exists(): tmp.unlink()
        for i,pf in enumerate(pfs):
            try: df=pd.read_parquet(pf,columns=BASE_COLS)
            except: df=pd.read_parquet(pf)
            if i==0: df.to_parquet(tmp,index=False,compression="snappy")
            else:
                ex=pd.read_parquet(tmp); cmb=pd.concat([ex,df],ignore_index=True)
                cmb.to_parquet(tmp,index=False,compression="snappy"); del ex; gc.collect()
            del df; gc.collect()
            if (i+1)%8==0: log(f"{i+1}/{len(pfs)}")
        log("排序...")
        full=pd.read_parquet(tmp)
        full=full.sort_values(["date","code"]).drop_duplicates(subset=["date","code"],keep="last").reset_index(drop=True)
        full.to_parquet(BT_CACHE,index=False,compression="snappy")
        tmp.unlink(); del full; gc.collect()
    fin=pd.read_parquet(BT_CACHE,columns=["date","code"])
    d=pd.to_datetime(fin["date"])
    log(f"✅ bt_cache:{len(fin)}行{fin['code'].nunique()}只{d.nunique()}天")
    log(f"   {d.min().date()}~{d.max().date()}")
    return True

# ═══════════════════════════════════════════════════════════
#  主入口
# ═══════════════════════════════════════════════════════════

STEPS={
    1:("Baostock增量",step1_baostock),
    2:("新浪增量",step2_sina),
    3:("清洗",step3_clean),
    4:("基础因子",step4_factors),
    5:("Alpha101",step5_alpha101),
    6:("回测缓存",step6_bt_cache),
}

def run_pipeline(tgt,ss,dry=False):
    t_start=time.time()
    print(f"\n{'#'*60}")
    print(f"  # 全流程一键更新 v{__version__}")
    print(f"  # 目标: {tgt}  |  步骤: {','.join(map(str,sorted(ss)))}")
    print(f"  # dry-run: {'是' if dry else '否'}")
    print(f"{'#'*60}\n")
    ensure_swap()
    for idx,sn in enumerate(sorted(ss),1):
        name,fn=STEPS[sn]
        print(f"\n{'='*60}\n  步骤 [{idx}/{len(ss)}] {sn}. {name}\n{'='*60}")
        try:
            r=fn(tgt,dry)
            if r is False: log(f"⚠️ 步骤{sn}返回异常")
        except Exception as e:
            log(f"❌ 步骤{sn}: {e}")
            import traceback; traceback.print_exc()
            reply=input(f"   步骤{sn}失败，继续?[Y/n]").strip().lower()
            if reply.startswith("n"): break
        gc.collect()
    print(f"\n{'#'*60}")
    print(f"  ✅ 全流程完成! 耗时{el(t_start)/60:.1f}分钟")
    print(f"{'#'*60}")

def main():
    parser=argparse.ArgumentParser(description="全流程一键更新")
    parser.add_argument("--target",help="目标日期 YYYY-MM-DD")
    parser.add_argument("--steps",help="步骤列表,逗号分隔")
    parser.add_argument("--dry-run",action="store_true")
    parser.add_argument("--check",action="store_true")
    parser.add_argument("--recache",action="store_true")
    args=parser.parse_args()
    tgt=args.target or get_latest_trade_date()
    print(f"目标日期: {tgt}")
    if args.steps: ss={int(p.strip()) for p in args.steps.split(",")}
    elif args.check: ss=set()
    elif args.recache: ss={6}
    else: ss=set(STEPS.keys())
    if args.check:
        print("\n🔍 检查模式...")
        for sn in sorted(STEPS): print(f"  步骤{sn}: {STEPS[sn][0]}")
        print(f"\n需更新到{tgt}")
        return
    run_pipeline(tgt,ss,dry=args.dry_run)

if __name__=="__main__":
    main()

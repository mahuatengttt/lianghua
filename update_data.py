#!/usr/bin/env python3
"""
📅 A股数据增量更新脚本

功能：
1. 获取最新交易日，包含哪些股票退市/新上市
2. 追加 baostock 前复权数据到 daily/
3. 追加新浪不复权数据到 daily_sina_raw/
4. 重新清洗 → daily_clean/

用法：
  python3 update_data.py              # 增量更新（自动判断最新日期）
  python3 update_data.py --force      # 强制全部重新同步（慎用）
  python3 update_data.py --check      # 只检查有没有新数据
"""

import baostock as bs
import pandas as pd
import requests
import json
from pathlib import Path
import time
import sys

BS_DIR = Path("a_stock_data/daily")
SINA_DIR = Path("a_stock_data/daily_sina_raw")
CLEAN_DIR = Path("a_stock_data/daily_clean")
CAL_FILE = Path("a_stock_data/trade_calendar.csv")

# 交易日历重新生成工具（用全部数据精确扫描，不要猜）
def build_exact_trade_calendar():
    """从 baostock 全量数据构建统一交易日历"""
    t0 = time.time()
    print("  构建交易日历（读取所有股票的日期）...")
    
    all_dates = set()
    files = list(BS_DIR.glob("*.parquet"))
    batch_size = 200
    for i in range(0, len(files), batch_size):
        batch = files[i:i+batch_size]
        for f in batch:
            df = pd.read_parquet(f, columns=['date'])
            all_dates.update(df['date'].tolist())
        if len(all_dates) > 470 and i >= 500:
            break
    
    trade_dates = sorted(all_dates)
    pd.Series(trade_dates).to_csv(CAL_FILE, index=False, header=False)
    print(f"  交易日: {len(trade_dates)} 天 ({trade_dates[0]} ~ {trade_dates[-1]}) [{time.time()-t0:.0f}s]")
    return trade_dates


def get_latest_date():
    """获取 baostock 数据最新日期"""
    files = list(BS_DIR.glob("*.parquet"))
    if not files:
        return None
    
    latest = "0000"
    for f in files[:100]:
        df = pd.read_parquet(f, columns=['date'])
        d = df['date'].max()
        if d > latest:
            latest = d
    return latest


def get_today_in_market():
    """查询今日是否交易日，返回最新一个完整交易日日期"""
    bs.login()
    # 今天
    today = time.strftime("%Y-%m-%d")
    # 查最近5个交易日
    rs = bs.query_all_stock(day=today)
    count = 0
    while rs.next():
        count += 1
    bs.logout()
    
    if count > 0:
        return today
    else:
        # 找最近交易日：回溯最多7天
        from datetime import datetime, timedelta
        for i in range(1, 8):
            d = (datetime.now() - timedelta(days=i)).strftime("%Y-%m-%d")
            bs.login()
            rs = bs.query_all_stock(day=d)
            cnt = 0
            while rs.next():
                cnt += 1
            bs.logout()
            if cnt > 100:
                return d
        return None


def fetch_baostock_incremental(start_date, end_date):
    """增量拉取 baostock 前复权数据"""
    print(f"\n[1/3] 拉取 baostock 前复权 {start_date} ~ {end_date}")
    
    bs.login()
    rs = bs.query_all_stock(day=end_date)
    all_codes = []
    while rs.next():
        row = rs.get_row_data()
        code, status, name = row[0], row[1], row[2]
        if status != '1':
            continue
        market, num = code.split('.')
        if (market == 'sh' and (num.startswith('60') or num.startswith('68'))) or \
           (market == 'sz' and (num[:2] in ('00','30') or num[:3] in ('001','002'))):
            all_codes.append((num, market, name))
    bs.logout()
    
    print(f"  当前A股: {len(all_codes)} 只")
    
    # 统计需要新增的
    existing = set(f.stem for f in BS_DIR.glob("*.parquet"))
    new_codes = [c for c in all_codes if c[0] not in existing]
    existing_codes = [c for c in all_codes if c[0] in existing]
    
    if not new_codes and len(existing) >= len(all_codes):
        print("  ✅ 无需新增股票，已有最新列表")
    elif new_codes:
        print(f"  📌 发现新股票: {len(new_codes)} 只")
        for num, mkt, name in new_codes[:5]:
            print(f"    {num} {name}")
    
    # 拉取数据
    bs.login()
    ok = err = 0
    for num, market, name in all_codes:
        fpath = BS_DIR / f"{num}.parquet"
        bs_code = f"{market}.{num}"
        
        # 已有文件：增量追加
        if fpath.exists():
            try:
                existing_data = pd.read_parquet(fpath)
                had_dates = set(existing_data['date'].tolist())
            except:
                had_dates = set()
        else:
            had_dates = set()
        
        try:
            rs = bs.query_history_k_data_plus(
                bs_code,
                "date,code,open,high,low,close,preclose,volume,amount,turn,tradestatus,pctChg",
                start_date=start_date, end_date=end_date,
                frequency="d", adjustflag="2"
            )
            rows = []
            while rs.next():
                rows.append(rs.get_row_data())
            
            if not rows:
                ok += 1
                continue
            
            new_rows = [r for r in rows if r[0] not in had_dates]
            if not new_rows:
                ok += 1
                continue
            
            df_new = pd.DataFrame(new_rows, columns=[
                'date','code','open','high','low','close',
                'preclose','volume','amount','turn','tradestatus','pctChg'
            ])
            for col in ['open','high','low','close','preclose','amount','turn','pctChg']:
                df_new[col] = pd.to_numeric(df_new[col], errors='coerce')
            df_new['volume'] = pd.to_numeric(df_new['volume'], errors='coerce').fillna(0).astype('int64')
            df_new['name'] = name
            
            if fpath.exists():
                existing_data = pd.read_parquet(fpath)
                combined = pd.concat([existing_data, df_new])
                combined = combined.drop_duplicates(subset=['date']).sort_values('date')
                combined.to_parquet(fpath, index=False)
            else:
                df_new.to_parquet(fpath, index=False)
            
            ok += 1
        except Exception as e:
            err += 1
        
        if (ok + err) % 500 == 0 and (ok + err) > 0:
            print(f"    [{ok+err}/{len(all_codes)}] +{ok} ✗{err}")
    
    bs.logout()
    print(f"  完成: +{ok}只更新, ✗{err}只失败")
    return ok, err


def fetch_sina_incremental(start_date, end_date):
    """增量拉取新浪不复权数据"""
    print(f"\n[2/3] 拉取新浪 不复权 {start_date} ~ {end_date}")
    
    bs_files = set(f.stem for f in BS_DIR.glob("*.parquet"))
    sina_files = set(f.stem for f in SINA_DIR.glob("*.parquet"))
    
    # 需要同步的 = baostock 已下但新浪缺失的
    to_get = sorted(bs_files - sina_files)
    # 以及已有新浪文件但可能缺最新日期的
    existing = sorted(bs_files & sina_files)
    
    # 先补缺失的
    session = requests.Session()
    ok = err = 0
    
    # 补缺失的股票
    for num in to_get:
        fpath = SINA_DIR / f"{num}.parquet"
        prefix = 'sh' if num.startswith('6') else 'sz'
        try:
            r = session.get(
                "https://quotes.sina.cn/cn/api/json_v2.php/CN_MarketDataService.getKLineData",
                params={"symbol": f"{prefix}{num}", "scale": "240", "datalen": "1024"},
                timeout=10
            )
            if r.status_code != 200 or not r.text:
                ok += 1
                continue
            data = json.loads(r.text)
            if not data:
                ok += 1
                continue
            
            name_val = ""
            try:
                name_val = pd.read_parquet(BS_DIR / f'{num}.parquet', columns=['name']).iloc[0]['name']
            except:
                pass
            
            rows = [{
                'date': item['day'], 'code': num, 'name': name_val,
                'open': float(item['open']), 'high': float(item['high']),
                'low': float(item['low']), 'close': float(item['close']),
                'volume': int(float(item['volume']))
            } for item in data if item['day'] >= '2024-06-01']
            
            if rows:
                pd.DataFrame(rows).to_parquet(fpath, index=False)
            ok += 1
        except:
            err += 1
    
    # 更新已有文件的最新数据（末尾追加）
    for num in existing[:200]:  # 抽200只更新最新数据
        fpath = SINA_DIR / f"{num}.parquet"
        try:
            # 读最新已有日期
            existing_df = pd.read_parquet(fpath)
            latest_had = existing_df['date'].max()
            if latest_had >= end_date:
                continue
            
            prefix = 'sh' if num.startswith('6') else 'sz'
            r = session.get(
                "https://quotes.sina.cn/cn/api/json_v2.php/CN_MarketDataService.getKLineData",
                params={"symbol": f"{prefix}{num}", "scale": "240", "datalen": "1024"},
                timeout=10
            )
            if r.status_code != 200 or not r.text:
                continue
            data = json.loads(r.text)
            new_rows = [{
                'date': item['day'], 'code': num,
                'name': existing_df.iloc[0]['name'],
                'open': float(item['open']), 'high': float(item['high']),
                'low': float(item['low']), 'close': float(item['close']),
                'volume': int(float(item['volume']))
            } for item in data if item['day'] > latest_had]
            
            if new_rows:
                df_new = pd.DataFrame(new_rows)
                combined = pd.concat([existing_df, df_new])
                combined.to_parquet(fpath, index=False)
                ok += 1
        except:
            err += 1
    
    print(f"  完成: +{ok}只更新, ✗{err}只失败")
    total = len(list(SINA_DIR.glob("*.parquet")))
    print(f"  新浪总计: {total} 只")
    return ok, err


def run_cleaning():
    """重新运行清洗"""
    print(f"\n[3/3] 重新清洗 → daily_clean/")
    
    trade_dates = build_exact_trade_calendar()
    
    files = sorted(BS_DIR.glob("*.parquet"))
    for i, f in enumerate(files):
        code = f.stem
        out_path = CLEAN_DIR / f"{code}.parquet"
        
        try:
            df = pd.read_parquet(f)
            if len(df) == 0:
                continue
            
            name = df.iloc[0]['name']
            df = df[df['volume'] >= 0].copy()
            df = df[df['high'] >= df['low'] - 0.001].copy()
            df = df[df['close'] >= 0.1].copy()
            if 'tradestatus' in df.columns:
                df = df[df['tradestatus'] == '1'].copy()
            
            if len(df) == 0:
                continue
            
            df = df.set_index('date')
            aligned = df.reindex(trade_dates)
            aligned['code'] = code
            aligned['name'] = name
            
            close = aligned['close'].values
            pct = np.full(len(close), np.nan)
            for j in range(1, len(close)):
                if not np.isnan(close[j]) and not np.isnan(close[j-1]) and close[j-1] > 0:
                    pct[j] = (close[j] - close[j-1]) / close[j-1] * 100
                    if abs(pct[j]) > 30:
                        aligned.loc[trade_dates[j], ['open','high','low','close']] = np.nan
                        aligned.loc[trade_dates[j], 'volume'] = 0
                        aligned.loc[trade_dates[j], 'amount'] = np.nan
                        pct[j] = np.nan
            
            aligned['pctChg'] = pct
            out_cols = ['open', 'high', 'low', 'close', 'preclose', 'volume', 'amount', 'turn', 'pctChg', 'code', 'name']
            result = aligned[out_cols].copy()
            result['date'] = trade_dates
            result = result[result['date'] != '0']
            result.to_parquet(out_path, index=False)
        except:
            pass
        
        if (i + 1) % 1000 == 0:
            print(f"  [{i+1}/{len(files)}]")
    
    clean_count = len(list(CLEAN_DIR.glob("*.parquet")))
    print(f"  清洗完成: {clean_count} 只")


def main():
    import numpy as np
    
    mode = "incremental"
    if "--force" in sys.argv:
        mode = "force"
    if "--check" in sys.argv:
        mode = "check"
    
    print("=" * 50)
    print("📅 A股数据增量更新")
    print(f"模式: {mode}")
    print("=" * 50)
    
    # 当前最新日期
    current_latest = get_latest_date()
    print(f"\n当前数据最新日期: {current_latest}")
    
    if mode == "check":
        # 只检查
        today_market = get_today_in_market()
        print(f"最新可能交易日: {today_market}")
        if today_market and today_market > current_latest:
            print("🔔 有新数据可更新！")
        else:
            print("✅ 数据已是最新")
        return
    
    # 确定结束日期
    if mode == "force":
        end_date = time.strftime("%Y-%m-%d")
        start_date = "2024-06-01"
        print(f"\n强制全量更新: {start_date} ~ {end_date}")
    else:
        end_date = get_today_in_market() or time.strftime("%Y-%m-%d")
        start_date = current_latest
        if start_date >= end_date:
            print("✅ 数据已是最新，无需更新")
            return
        print(f"增量更新: {start_date} ~ {end_date}")
    
    # [1] baostock
    fetch_baostock_incremental(start_date, end_date)
    
    # [2] 新浪
    fetch_sina_incremental(start_date, end_date)
    
    # [3] 重新清洗
    run_cleaning()
    
    print(f"\n{'='*50}")
    print(f"✅ 更新完成!")


if __name__ == "__main__":
    main()

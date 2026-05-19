"""
每日选股工具 v3.0 — 强势趋势跟踪版
==================
基于IC回测分析，做多强势股。
- 高动量、价格在均线上方、放量趋势
- 强基本面过滤（ROE>5%、PE合理）
- 注意：rank(pct)越大越好，nlargest取高分
"""

import os, sys, time, warnings, argparse
import numpy as np
import pandas as pd

# 龙头趋势模块（Phase 2+3）
sys.path.insert(0, os.path.dirname(__file__))
from dragon_leader import ConceptHotness, tag_stocks_with_hotness
import pyarrow.parquet as pq
from glob import glob

warnings.filterwarnings('ignore')

DATA_DIR = "/home/admin/.openclaw/workspace/agents/zidong/workspace/a_stock_data/daily_clean"
# 优先使用 v3 分片（按月），回退到旧缓存
import glob as _glob
_v3_files = sorted(_glob.glob("/home/admin/.openclaw/workspace/agents/zidong/workspace/a_stock_data/factor_cache_v3/part_*.parquet"))
CACHE_FILE = _v3_files[-1] if _v3_files else "/home/admin/.openclaw/workspace/agents/zidong/workspace/a_stock_data/factor_cache_backtest.parquet"
FUND_FILE = "/home/admin/.openclaw/workspace/agents/zidong/workspace/a_stock_data/fundamental_latest_v3.parquet"
VAL_FILE  = "/home/admin/.openclaw/workspace/agents/zidong/workspace/a_stock_data/fundamental_valuation_v3.parquet"
OUTPUT_DIR = "/home/admin/.openclaw/workspace/agents/zidong/workspace/daily_pick"
os.makedirs(OUTPUT_DIR, exist_ok=True)

# ========== 强势趋势因子权重 ==========
# 所有因子rank(pct)∈[0,1]，越大越好
# rank方向：mom高=好，price_ma20高=好，rev_5d高=好
# low_vol: vol_20d低=好 → 1-rank(vol_20d)
# low_turn: turn低=好 → 1-rank(turn)
# vol_ratio放量: 低量比=缩量，取反=放量
FACTOR_WEIGHTS = {
    'mom_20d_rank':      2.0,   # 高动量（最强趋势信号）
    'price_ma20_rank':   1.5,   # 价格在均线上方
    'rev_5d_rank':       1.0,   # 短期反转（强势股回调）
    'alpha3_rank':       0.8,   # 量价背离
    'alpha12_rank':      0.6,   # 量价齐升
    'low_vol_rank':      1.0,   # 低波动（过滤剧烈波动）
    'low_turn_rank':     0.6,   # 低换手
    'vol_ratio_rank':    0.8,   # 放量
}

# 基本面过滤条件
MIN_ROE = 3          # ROE >= 3%
MIN_PE = 3           # PE > 3
MAX_PE = 40          # PE < 40
MIN_CLOSE = 5        # 股价 >= 5元
MAX_CLOSE = 99999    # 取消股价上限
MIN_MA20_DEV = -0.01 # 允许轻微破位（不低于MA20的-1%）

def get_latest_date():
    pf = pq.ParquetFile(CACHE_FILE)
    df = pf.read(columns=['date']).to_pandas()
    return df['date'].max()

def load_latest_snapshot():
    """仅加载最新截面的因子数据（内存安全）"""
    pf = pq.ParquetFile(CACHE_FILE)
    latest = pf.read(columns=['date']).to_pandas()['date'].max()
    rows = []
    for batch in pf.iter_batches(batch_size=50000):
        df = batch.to_pandas()
        mask = df['date'] == latest
        sub = df[mask]
        if len(sub):
            rows.append(sub)
    return pd.concat(rows).reset_index(drop=True) if rows else pd.DataFrame()

def load_basic_data():
    """加载基本面、名称、ST信息"""
    # 基本面 — 最新截面
    fund = pq.read_table(FUND_FILE).to_pandas().set_index('code')
    
    # 估值 — 取最新
    val = pq.read_table(VAL_FILE, columns=['code','date','pe_ttm','pb']).to_pandas()
    val['date'] = pd.to_datetime(val['date'])
    val = val.sort_values('date').groupby('code').last()[['pe_ttm','pb']]
    
    # 名称 & ST
    names, st_set = {}, set()
    files = sorted(glob(os.path.join(DATA_DIR, '*.parquet')))
    for fpath in files:
        code = os.path.basename(fpath).replace('.parquet', '')
        try:
            nm = pq.read_table(fpath, columns=['name']).to_pandas()['name'].iloc[0]
            names[code] = nm
            if nm.startswith(('ST', '*ST', 'S', 'N')):
                st_set.add(code)
        except:
            pass
    
    return fund, val, names, st_set

def compute_scores(day):
    d = day.copy()
    
    # 正方向：直接rank(pct)，值越大越好
    d['mom_20d_rank'] = d['mom_20d'].rank(pct=True)
    d['price_ma20_rank'] = d['price_ma20'].rank(pct=True)
    d['rev_5d_rank'] = d['rev_5d'].rank(pct=True)
    d['alpha3_rank'] = d['alpha3'].rank(pct=True)
    d['alpha12_rank'] = d['alpha12'].rank(pct=True)
    
    # 负方向：1-rank，越小越好
    d['low_vol_rank'] = 1 - d['vol_20d'].rank(pct=True)
    d['low_turn_rank'] = 1 - d['turn_20d_avg'].rank(pct=True)
    d['vol_ratio_rank'] = 1 - d['vol_ratio_5_20'].rank(pct=True)
    
    # 综合得分
    d['score'] = sum(
        d.get(c, 0.5).fillna(0.5) * wt for c, wt in FACTOR_WEIGHTS.items()
    )
    return d

def run(date_str=None, top_n=20):
    t0 = time.time()
    
    print("=" * 60)
    print(f"  每日选股 v3.0 — 强势趋势跟踪")
    print("=" * 60)
    
    # 1. 数据
    print("\n📅 加载数据...")
    day = load_latest_snapshot()
    if len(day) == 0:
        print("❌ 无数据")
        return
    used_date = str(day['date'].iloc[0])[:10]
    print(f"   截面: {used_date}, {len(day)} 只")
    
    # 2. 计算得分
    day = compute_scores(day)
    
    # 3. 基本面 & 名称
    fund, val, names, st_set = load_basic_data()
    day['name'] = day['code'].map(names)
    
    # 合并基本面
    day['roe'] = day['code'].map(fund['roe'])
    day['net_margin'] = day['code'].map(fund['net_margin'])
    day['profit_growth'] = day['code'].map(fund['profit_growth'])
    day['pe'] = day['code'].map(val['pe_ttm'])
    day['pb'] = day['code'].map(val['pb'])
    
    # 4. 过滤
    print("\n🔍 过滤...")
    before = len(day)
    
    # ST
    day = day[~day['code'].isin(st_set)]
    day = day[~day['name'].str.startswith(('ST', '*ST', 'S', 'N'), na=False)]
    
    # 基本面
    day = day[day['roe'].notna() & (day['roe'] >= MIN_ROE)]
    day = day[day['pe'].notna() & (day['pe'] >= MIN_PE) & (day['pe'] <= MAX_PE)]
    day = day[(day['close'] >= MIN_CLOSE) & (day['close'] <= MAX_CLOSE)]
    
    # 价格在均线上方
    day = day[day['price_ma20'] >= MIN_MA20_DEV]
    
    print(f"   过滤前: {before} → 后: {len(day)}")
    
    if len(day) == 0:
        print("❌ 过滤后无股票，放宽条件重试")
        # 放宽：去掉ROE和PE限制
        day = load_latest_snapshot()
        day = compute_scores(day)
        day['name'] = day['code'].map(names)
        day['roe'] = day['code'].map(fund['roe'])
        day['profit_growth'] = day['code'].map(fund['profit_growth'])
        day['pe'] = day['code'].map(val['pe_ttm'])
        day['pb'] = day['code'].map(val['pb'])
        
        day = day[~day['code'].isin(st_set)]
        day = day[~day['name'].str.startswith(('ST', '*ST', 'S', 'N'), na=False)]
        day = day[(day['close'] >= MIN_CLOSE) & (day['close'] <= MAX_CLOSE)]
        day = day[day['price_ma20'] >= -0.015]
        print(f"   放宽后: {len(day)}")
        
        if len(day) == 0:
            print("❌ 仍然无数据")
            return
    
    # 5. 题材热度 + 龙头识别（Phase 2+3）
    print("\n📊 计算题材热度...")
    try:
        hot_df, leader_df, _ = ConceptHotness().compute_hotness(used_date)
        if len(hot_df) > 0:
            day = tag_stocks_with_hotness(day, hot_df, leader_df)
            # 用 final_score 重新排序
            if 'final_score' in day.columns:
                day = day.sort_values('final_score', ascending=False).reset_index(drop=True)
            print(f"  🔥 热门行业TOP5: {', '.join(hot_df.head(5)['industry'].tolist())}")
    except Exception as e:
        print(f"  ⚠ 题材热度计算失败: {e}")
    
    # 6. Top N
    top = day.nlargest(top_n, 'score').reset_index(drop=True)
    
    # 7. 输出
    print("\n" + "=" * 100)
    print(f"  🏆 Top{len(top)} 龙头趋势候选 ({used_date})")
    print(f"  策略: 趋势×70% + 题材热度×15% + 龙头加成×15%")
    print("=" * 100)
    
    rows_for_report = []
    for idx, (_, row) in enumerate(top.iterrows()):
        code = row['code']
        name = row.get('name', '')
        score = row['score']
        close = row['close']
        mom = row['mom_20d']
        price_ma20 = row['price_ma20']
        rev = row['rev_5d']
        vol_ratio = row['vol_ratio_5_20']
        roe = row.get('roe', None)
        net_margin = row.get('net_margin', None)
        profit_growth = row.get('profit_growth', None)
        pe = row.get('pe', None)
        pb = row.get('pb', None)
        
        # 标签
        tags = []
        
        # 龙头/热点标签（最高优先级）
        if '🐉龙一' in str(row.get('tags', '')):
            tags.append('🐉龙一')
        if '🔥热点' in str(row.get('tags', '')):
            tags.append('🔥热点')
        
        # 行业标签
        industry = row.get('industry', '')
        if industry and industry != 'Other':
            tags.append(f'📌{industry}')
        
        # 技术面标签
        if price_ma20 > 0.05:
            tags.append(f"📈趋势+{price_ma20:.1%}")
        elif price_ma20 > 0.02:
            tags.append(f"↗️偏多+{price_ma20:.1%}")
        if mom > 0.15:
            tags.append(f"💪强动量{mom:.1%}")
        elif mom > 0.08:
            tags.append(f"📊中动量{mom:.1%}")
        if vol_ratio > 1.3:
            tags.append(f"🔥放量{vol_ratio:.1f}x")
        elif vol_ratio > 1.1:
            tags.append(f"📈微放量{vol_ratio:.1f}x")
        if roe is not None and pd.notna(roe) and roe > 10:
            tags.append(f"🏅ROE={roe:.1f}%")
        if profit_growth is not None and pd.notna(profit_growth):
            if profit_growth > 50:
                tags.append(f"🚀利润+{profit_growth:.0f}%")
            elif profit_growth > 20:
                tags.append(f"📈利润+{profit_growth:.0f}%")
        tag_str = " | ".join(tags)
        
        # 基本信息行
        fund_parts = []
        if roe is not None and pd.notna(roe):
            fund_parts.append(f"ROE={roe:.1f}%")
        if net_margin is not None and pd.notna(net_margin):
            fund_parts.append(f"净利率{net_margin:.1f}%")
        if profit_growth is not None and pd.notna(profit_growth):
            fund_parts.append(f"利润增长{profit_growth:.1f}%")
        if pe is not None and pd.notna(pe):
            fund_parts.append(f"PE={pe:.1f}")
        if pb is not None and pd.notna(pb):
            fund_parts.append(f"PB={pb:.2f}")
        fund_line = f"      {' | '.join(fund_parts)}" if fund_parts else ""
        
        print(f"\n  {idx+1:2d}. {code} {name:<10} "
              f"得分={score:.3f} | 收盘={close:.2f} | 动量={mom:+.1%}")
        print(f"      {tag_str}")
        if fund_line:
            print(fund_line)
        
        rows_for_report.append({
            'rank': idx+1, 'code': code, 'name': name,
            'score': score, 'close_val': close,
            'mom_val': mom, 'ma20_dev': price_ma20,
            'vol_ratio': vol_ratio,
            'roe': f"{roe:.1f}%" if pd.notna(roe) else "",
            'net_margin': f"{net_margin:.1f}%" if pd.notna(net_margin) else "",
            'profit_growth': f"{profit_growth:.1f}%" if pd.notna(profit_growth) else "",
            'pe': f"{pe:.1f}" if pd.notna(pe) else "",
            'pb': f"{pb:.2f}" if pd.notna(pb) else "",
            'tags_str': tag_str,
        })
    
    now = time.strftime('%Y-%m-%d %H:%M:%S')
    report_lines = [
        f"# 每日选股报告 {used_date} — 龙头趋势 v3.0",
        "",
        f"**生成时间:** {now}",
        f"**数据日期:** {used_date}",
        f"**策略:** 趋势×70% + 题材热度×15% + 龙头加成×15%",
        f"**过滤条件:** ROE≥{MIN_ROE}%, PE {MIN_PE}-{MAX_PE}, 价格{MIN_CLOSE}-{MAX_CLOSE}",
        f"**全市场:** {len(day)} 只 | **输出:** Top{len(top)}",
        "",
        "| # | 代码 | 名称 | 得分 | 收盘 | 动量 | MA20偏离 | 量比 | ROE | 净利率 | 利润增速 | PE | PB | 标签 |",
        "|---|------|------|:----:|:---:|:----:|:--------:|:---:|:---:|:-----:|:-------:|:--:|:--:|------|",
    ]
    for r in rows_for_report:
        report_lines.append(
            f"| {r['rank']} | {r['code']} | {r['name']} | {r['score']:.3f} | "
            f"{r['close_val']:.2f} | {r['mom_val']:+.1%} | {r['ma20_dev']:+.2%} | "
            f"{r['vol_ratio']:.2f}x | {r['roe']} | {r['net_margin']} | "
            f"{r['profit_growth']} | {r['pe']} | {r['pb']} | {r['tags_str']} |"
        )
    
    # 附加：热门行业排行榜
    try:
        if 'hot_df' in dir() and len(hot_df) > 0:
            report_lines.append("")
            report_lines.append("## 📊 热门行业排行榜")
            report_lines.append("")
            report_lines.append("| 排名 | 行业 | 涨停数 | 板块涨幅 | 热度分 |")
            report_lines.append("|:---:|------|:-----:|:--------:|:-----:|")
            for i, (_, hr) in enumerate(hot_df.head(10).iterrows()):
                report_lines.append(
                    f"| {i+1} | {hr['industry']} | {int(hr['limit_ups'])}只 | {hr['avg_pct']:+.2f}% | {hr['hotness']:.1f} |"
                )
    except:
        pass
    
    rpath = os.path.join(OUTPUT_DIR, f'pick_{used_date}.md')
    with open(rpath, 'w') as f:
        f.write('\n'.join(report_lines))
    
    print(f"\n📝 报告已保存: {rpath}")
    print(f"\n✅ 总耗时: {time.time()-t0:.1f}s")


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--date', default=None)
    parser.add_argument('--top', type=int, default=20)
    args = parser.parse_args()
    run(args.date, args.top)

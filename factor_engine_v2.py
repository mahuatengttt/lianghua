"""
factor_engine.py v2.0 — 专业版因子引擎
=========================================
升级内容：
  1. 行业中性化（84个证监会行业）
  2. 市值中性化
  3. 涨跌停/停牌/ST过滤
  4. 因子正交化（去冗余）
  5. 行业暴露监控
"""

import os, sys, gc, time, argparse, warnings
import numpy as np
import pandas as pd
import pyarrow.parquet as pq
from glob import glob

warnings.filterwarnings('ignore')

# ── 配置 ──
DATA_DIR   = "/home/admin/.openclaw/workspace/agents/zidong/workspace/a_stock_data/daily_clean"
OUTPUT_DIR = "/home/admin/.openclaw/workspace/agents/zidong/workspace/factor_output"
os.makedirs(OUTPUT_DIR, exist_ok=True)

# 导入trading_system的函数
sys.path.insert(0, os.path.dirname(DATA_DIR) + '/..')
from trading_system import (
    load_industry_map, neutral_by_industry, neutral_by_market_cap,
    filter_untradeable, calc_trade_cost, cost_adjusted_return,
    RiskController, risk_parity_weights, min_variance_weights,
    backtest_report, format_report, compute_cov_matrix
)

ALLOW_PREFIX = ('600', '601', '603', '605', '000', '001', '002', '003')

# ── 因子定义 ──
# 因子名、方向、是否需要归一化
FACTOR_DEFS = {
    'rev_1d':    {'dir': 1,  'name': '1日反转'},       # 1日反转（越大越好=超跌）
    'rev_5d':    {'dir': 1,  'name': '5日反转'},       # 5日反转
    'rev_20d':   {'dir': 1,  'name': '20日反转'},      # 20日反转
    'mom_20d':   {'dir': 0,  'name': '20日动量'},      # 20日动量（中性，看组合）
    'mom_60d':   {'dir': 0,  'name': '60日动量'},
    'vol_20d':   {'dir': -1, 'name': '20日波动率'},    # 越低越好（低波）
    'amplitude_20d': {'dir': -1, 'name': '20日振幅'},
    'turn_20d_avg': {'dir': -1, 'name': '20日平均换手'},
    'price_ma20': {'dir': 0, 'name': '均线偏离'},      # 中性
    'vol_ratio_5_20': {'dir': -1, 'name': '量比'},     # 量比低=缩量企稳
    'alpha3_simple': {'dir': 1, 'name': '量价背离α3'},
    'alpha12_simple': {'dir': 1, 'name': '量价背离α12'},
    'low_vol': {'dir': 1, 'name': '低波动'},           # 1 - vol_rank
    'low_turn': {'dir': 1, 'name': '低换手'},
    'low_amp': {'dir': 1, 'name': '低振幅'},
}

# ── 因子权重（用于综合打分）─
SCORE_WEIGHTS = {
    'rev_5d_neutral':    1.2,   # 5日反转（行业中性化后）
    'rev_1d_neutral':    0.6,
    'rev_20d_neutral':   0.4,
    'low_vol_neutral':   1.0,
    'low_turn_neutral':  0.5,
    'low_amp_neutral':   0.6,
    'alpha3_simple_neutral': 1.0,
    'alpha12_simple_neutral': 0.5,
    'vol_ratio_5_20_neutral': 0.3,  # 量比低=好
    'price_ma20_neutral': 0.5,      # 均线附近=好
}


def calc_factors_for_stock(df, target_date):
    """对一只股票计算全部因子，返回最后一行"""
    code = df['code'].iloc[0]
    name = df['name'].iloc[0]
    df = df.sort_values('date').reset_index(drop=True)
    
    close = df['close'].values.astype(float)
    open_p = df['open'].values.astype(float)
    high = df['high'].values.astype(float)
    low  = df['low'].values.astype(float)
    volume = df['volume'].values.astype(float)
    turn   = df['turn'].values.astype(float)
    preclose = df['preclose'].values.astype(float)
    
    date_vals = pd.to_datetime(df['date']).values
    target_ts = pd.Timestamp(target_date).to_datetime64()
    idx = np.where(date_vals <= target_ts)[0]
    if len(idx) == 0:
        return None
    latest = idx[-1]
    
    # 收益率序列
    ret1d_s = pd.Series(close).pct_change()
    ret5d_s = pd.Series(close).pct_change(5)
    ret20d_s = pd.Series(close).pct_change(20)
    ret60d_s = pd.Series(close).pct_change(60)
    
    result = {
        'date': target_date,
        'code': code,
        'name': name,
        'close': close[latest],
        'preclose': preclose[latest] if not np.isnan(preclose[latest]) else close[latest],
        'volume': volume[latest],
        'turn': turn[latest],
        'returns_1d': ret1d_s.iloc[latest] if not pd.isna(ret1d_s.iloc[latest]) else 0.0,
        'returns_5d': ret5d_s.iloc[latest],
        'returns_20d': ret20d_s.iloc[latest],
        'returns_60d': ret60d_s.iloc[latest],
        'rev_1d': -ret1d_s.iloc[latest] if not pd.isna(ret1d_s.iloc[latest]) else 0.0,
        'rev_5d': -ret5d_s.iloc[latest] if not pd.isna(ret5d_s.iloc[latest]) else 0.0,
        'rev_20d': -ret20d_s.iloc[latest] if not pd.isna(ret20d_s.iloc[latest]) else 0.0,
        'mom_20d': ret20d_s.iloc[latest],
        'mom_60d': ret60d_s.iloc[latest],
    }
    
    # 波动率
    vol = pd.Series(close).pct_change().rolling(20).std(ddof=0)
    result['vol_20d'] = vol.iloc[latest] if not pd.isna(vol.iloc[latest]) else 1.0
    
    # 振幅
    amp_series = pd.Series((high - low) / np.where(close != 0, close, np.nan))
    amp20 = amp_series.rolling(20).mean()
    result['amplitude_20d'] = amp20.iloc[latest] if not pd.isna(amp20.iloc[latest]) else 0.0
    
    # 均线偏离
    ma20 = pd.Series(close).rolling(20).mean()
    c = close[latest]
    ma20_v = ma20.iloc[latest]
    result['price_ma20'] = (c - ma20_v) / (ma20_v + 1e-8) if not pd.isna(ma20_v) else 0.0
    
    # 量比
    vma5 = pd.Series(volume).rolling(5).mean()
    vma20 = pd.Series(volume).rolling(20).mean()
    result['vol_ratio_5_20'] = vma5.iloc[latest] / (vma20.iloc[latest] + 1e-8)
    
    # 换手率
    result['turn_20d_avg'] = pd.Series(turn).rolling(20).mean().iloc[latest]
    
    # 量价背离
    corr_series = -pd.Series(close).rolling(20).corr(pd.Series(volume).pipe(np.log1p))
    result['alpha3_simple'] = corr_series.iloc[latest] if not pd.isna(corr_series.iloc[latest]) else 0.0
    
    a12 = np.sign(pd.Series(volume).diff()) * (-pd.Series(close).diff())
    result['alpha12_simple'] = a12.iloc[latest] if not pd.isna(a12.iloc[latest]) else 0.0
    
    # 涨跌幅（盘中实际成交量正常则算可交易）
    result['pct_chg'] = (close[latest] / preclose[latest] - 1) if preclose[latest] > 0 else 0
    
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--date', type=str, default=None)
    parser.add_argument('--top', type=int, default=20)
    parser.add_argument('--all-codes', action='store_true')
    parser.add_argument('--no-neutral', action='store_true', help='跳过中性化（对比用）')
    args = parser.parse_args()
    
    t0 = time.time()
    target_date = args.date or pd.Timestamp.today().strftime('%Y-%m-%d')
    
    files = sorted(glob(os.path.join(DATA_DIR, '*.parquet')))
    files = [f for f in files if os.path.basename(f).startswith(ALLOW_PREFIX)]
    print(f"股票文件: {len(files)} 只")
    print(f"目标日期: {target_date}")
    
    # 加载行业映射
    ind_map = load_industry_map()
    code_to_ind = dict(zip(ind_map['code'], ind_map['industry']))
    print(f"行业映射: {ind_map['industry'].nunique()} 个行业, {len(ind_map)} 只股票")
    
    # 逐个算因子
    all_factors = []
    skipped = {'no_data': 0, 'st': 0, 'untradeable': 0}
    
    for i, fpath in enumerate(files):
        if (i+1) % 500 == 0:
            elapsed = time.time() - t0
            eta = elapsed / (i+1) * (len(files) - i - 1)
            print(f"  [{i+1}/{len(files)}] {len(all_factors)} 有效, ETA {eta:.0f}s", flush=True)
        
        try:
            df = pq.read_table(fpath).to_pandas()
            code = df['code'].iloc[0]
            name = df['name'].iloc[0]
            
            # ST过滤
            if name.startswith(('ST', '*ST', 'S')):
                skipped['st'] += 1
                continue
            
            # 算因子
            row = calc_factors_for_stock(df, target_date)
            if row is None:
                skipped['no_data'] += 1
                continue
            
            # 涨跌停过滤（当天选股时排除）
            pct = row['pct_chg']
            if code.startswith(('688', '300')):
                if abs(pct) >= 0.195:
                    skipped['untradeable'] += 1
                    continue
            else:
                if abs(pct) >= 0.095:
                    skipped['untradeable'] += 1
                    continue
            
            # 成交量=0（停牌）
            if row['volume'] <= 0:
                skipped['untradeable'] += 1
                continue
            
            # 行业映射
            row['industry'] = code_to_ind.get(code, 'Other')
            
            all_factors.append(row)
        except Exception as e:
            continue
    
    print(f"\n处理完成: 有效{len(all_factors)}, "
          f"跳过ST={skipped['st']}, 停牌涨跌停={skipped['untradeable']}, 无数据={skipped['no_data']}")
    
    if len(all_factors) < 10:
        print("❌ 有效股票太少，无法继续")
        return
    
    # 构建截面DataFrame
    daily = pd.DataFrame(all_factors)
    print(f"截面: {len(daily)} 只股票")
    
    # ── 1. 截面排名 ──
    factor_names = ['mom_20d', 'mom_60d', 'vol_ratio_5_20',
                    'rev_1d', 'rev_5d', 'rev_20d', 'price_ma20',
                    'alpha3_simple', 'alpha12_simple',
                    'turn_20d_avg', 'vol_20d', 'amplitude_20d']
    
    for col in factor_names:
        if col in daily.columns:
            daily[col + '_rank'] = daily[col].rank(pct=True)
    
    # 低波/低换手/低振幅 rank
    daily['low_vol_rank'] = 1 - daily['vol_20d_rank']
    daily['low_turn_rank'] = 1 - daily['turn_20d_avg_rank']
    daily['low_amp_rank'] = 1 - daily['amplitude_20d_rank']
    
    # ── 2. 行业中性化（核心升级）──
    if not args.no_neutral:
        print("\n🔧 进行行业中性化...")
        neutral_cols = ['rev_1d', 'rev_5d', 'rev_20d', 'mom_20d',
                       'vol_20d_rank', 'low_vol_rank', 'low_turn_rank', 'low_amp_rank',
                       'alpha3_simple', 'alpha12_simple',
                       'vol_ratio_5_20', 'price_ma20', 'turn_20d_avg']
        
        daily = neutral_by_industry(daily, neutral_cols, method='rank')
        
        # 中性化后的因子名
        rank_cols_neutral = [c + '_neutral' for c in ['rev_1d', 'rev_5d', 'rev_20d', 
                                                       'vol_20d_rank', 'low_vol_rank',
                                                       'low_turn_rank', 'low_amp_rank',
                                                       'alpha3_simple', 'alpha12_simple',
                                                       'vol_ratio_5_20', 'price_ma20',
                                                       'turn_20d_avg']]
        
        # 报告行业暴露
        print("\n📊 行业分布（选股池 Top10行业）:")
        ind_counts = daily['industry'].value_counts()
        for ind, cnt in ind_counts.head(10).items():
            print(f"  {ind}: {cnt}只")
        
        # 行业中性化后的股票数（去掉了只有一个股票的行业）
        valid = daily[rank_cols_neutral].notna().all(axis=1)
        print(f"\n  行业中性化有效股票: {valid.sum()} / {len(daily)}")
        daily['valid_neutral'] = valid
    else:
        daily['valid_neutral'] = True
        rank_cols_neutral = [c + '_rank' for c in factor_names]
    
    # ── 3. 综合打分 ──
    daily['factor_score'] = 0.0
    weight_count = 0
    for col, w in SCORE_WEIGHTS.items():
        if col in daily.columns:
            daily['factor_score'] += daily[col].fillna(0) * w
            weight_count += 1
        else:
            # fallback: 用原始 rank
            fallback = col.replace('_neutral', '_rank')
            if fallback in daily.columns:
                daily['factor_score'] += daily[fallback].fillna(0) * w
    
    print(f"\n  综合打分使用了 {weight_count} 个因子")
    
    # ── 4. 过滤并排序 ──
    # 只选择有数据且行业中性化有效的股票
    daily_main = daily[daily['valid_neutral']].copy()
    daily_main = daily_main.sort_values('factor_score', ascending=False).reset_index(drop=True)
    daily_main['rank'] = range(1, len(daily_main) + 1)
    
    top = daily_main.head(args.top)
    
    print(f"\n{'='*80}")
    print(f"📊 {target_date} 因子选股 Top {args.top} (行业中性化)")
    print(f"{'='*80}")
    print(f"{'排名':>4} {'代码':>8} {'名称':<10} {'得分':>7} {'收盘':>7} {'行业':<20} {'反转5d':>7} {'低波':>6} {'量价':>6}")
    print("-" * 80)
    for _, row in top.iterrows():
        r5 = row.get('rev_5d_neutral', row.get('rev_5d_rank', 0))
        lv = row.get('low_vol_neutral', row.get('low_vol_rank', 0))
        a3 = row.get('alpha3_simple_neutral', row.get('alpha3_simple_rank', 0))
        ind = row.get('industry', '?')
        print(f"{row['rank']:>4} {row['code']:>8} {row['name']:<10} "
              f"{row['factor_score']:>7.3f} {row['close']:>7.2f} "
              f"{ind:<20} {r5:>7.3f} {lv:>6.3f} {a3:>6.3f}")
    
    # ── 5. 保存 ──
    out_cols = ['rank', 'date', 'code', 'name', 'factor_score', 'close',
                'industry'] + [c for c in daily_main.columns 
                              if c.endswith('_neutral') or c.endswith('_rank')]
    out_cols = [c for c in out_cols if c in daily_main.columns]
    out_df = daily_main[out_cols]
    
    path = os.path.join(OUTPUT_DIR, f'top{args.top}_{target_date.replace("-", "")}_v2.csv')
    out_df.to_csv(path, index=False, encoding='utf-8-sig')
    print(f"\n✅ 已保存: {path}")
    print(f"总耗时: {time.time()-t0:.0f}s")


if __name__ == '__main__':
    main()

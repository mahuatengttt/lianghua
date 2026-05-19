"""
backtest_v3.py - IC加权 + 基本面因子 + 因子正交化回测框架
============================================================
升级 v2:
  1. 从 factor_cache_v3 读取 54 个 Alpha101 因子
  2. 每个因子计算滚动IC (60天) 作为权重,IC_IR加权
  3. 整合基本面因子(PE/PB/ROE/营收增速)
  4. 因子正交化(去冗余)
"""

import os, sys, time, warnings, gc
import numpy as np
import pandas as pd
import pyarrow.parquet as pq
from glob import glob

warnings.filterwarnings('ignore')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from trading_system import (
    load_industry_map, neutral_by_industry,
    SLIP_RATE, COMMISSION_RATE, STAMP_TAX
)

# ── 配置 ──
DATA_DIR = "/home/admin/.openclaw/workspace/agents/zidong/workspace/a_stock_data"
CACHE_V3_DIR = os.path.join(DATA_DIR, 'factor_cache_v3')
CACHE_BACKUP = os.path.join(DATA_DIR, 'factor_cache_backtest.parquet')  # 回退
FUNDAMENTAL_FILE = os.path.join(DATA_DIR, 'fundamental_ext_v3.parquet')
VALUATION_FILE = os.path.join(DATA_DIR, 'fundamental_valuation_v3.parquet')
META_FILE = os.path.join(DATA_DIR, 'meta_tradeable.parquet')
OUTPUT_DIR = os.path.join(DATA_DIR, '..', 'factor_output', 'backtest_v3')
os.makedirs(OUTPUT_DIR, exist_ok=True)

N_GROUPS = 5
HOLD_DAYS = [5, 10, 20]
MIN_STOCKS = 15
IC_WINDOW = 60  # 滚动IC窗口

cost_buy  = SLIP_RATE + COMMISSION_RATE
cost_sell = SLIP_RATE + COMMISSION_RATE + STAMP_TAX
cost_roundtrip = cost_buy + cost_sell

# 原始10因子
BASE_FACTORS = ['mom_20d', 'rev_5d', 'vol_20d', 'alpha3', 'alpha12',
                'amplitude_20d', 'turn_20d_avg', 'price_ma20',
                'vol_ratio_5_20', 'zscore_ma20']


def find_cache():
    """找v3缓存,回退到v2"""
    v3_file = os.path.join(DATA_DIR, 'factor_cache_v3.parquet')
    if os.path.exists(v3_file):
        return v3_file, 'v3_single'
    v3_parts = sorted(glob(os.path.join(CACHE_V3_DIR, 'part_*.parquet')))
    if v3_parts:
        return v3_parts, 'v3_parts'
    if os.path.exists(CACHE_BACKUP):
        return CACHE_BACKUP, 'v2'
    return None, None


def load_v3_cache_via_parts_clean(part_files, mode='v3_parts'):
    """读v3分片,只保留标准列 + 所有alpha列"""
    print(f"  缓存模式: {mode}, 分片: {len(part_files)}")

    # 先读一个小片获取列名
    sample = pq.read_table(part_files[0]).to_pandas()
    all_cols = sample.columns.tolist()

    # 确定保留的列
    keep_cols = ['date', 'code', 'close'] + [c for c in all_cols if c == 'close' or c in BASE_FACTORS or c.startswith('alpha') or c in ['mom_20d','rev_5d','vol_20d','alpha3','alpha12','amplitude_20d','turn_20d_avg','price_ma20','vol_ratio_5_20','zscore_ma20']]
    keep_cols = list(dict.fromkeys(keep_cols))  # dedup 保持顺序

    chunks = []
    for pf in part_files:
        try:
            df = pq.read_table(pf, columns=keep_cols).to_pandas()
            df['date'] = pd.to_datetime(df['date'])
            chunks.append(df)
        except Exception as e:
            print(f"    ⚠ 读 {pf}: {e}")

    if not chunks:
        return None

    full = pd.concat(chunks, ignore_index=True)
    full = full.sort_values(['date', 'code']).reset_index(drop=True)
    print(f"  缓存: {len(full)} 行, {full['code'].nunique()} 只股票, {len(keep_cols)} 列")
    return full


def load_fundamentals():
    """加载基本面因子"""
    if not os.path.exists(FUNDAMENTAL_FILE):
        print("  ⚠ 无基本面文件")
        return None

    df = pq.read_table(FUNDAMENTAL_FILE).to_pandas()
    df['date'] = pd.to_datetime(df['stat_date'] if 'stat_date' in df.columns else df.iloc[:, 0])

    # 选关键财务因子
    fin_cols = {
        'roe_ttm2': 'ROE_TTM',        # 净资产收益率
        'yoy_profit': 'YOY_PROFIT',   # 利润同比
        'yoy_revenue': 'YOY_REV',     # 营收同比
        'gross_profit_margin': 'GPM', # 毛利率
        'debt_to_asset': 'LEV',       # 负债率
        'profit_to_total_op_eps': 'NETMARGIN',  # 净利率
        'ttm_roe': 'ROE_TTM2',
    }

    available = {k: v for k, v in fin_cols.items() if k in df.columns}
    cols = ['code', 'date'] + list(available.keys())
    cols = [c for c in cols if c in df.columns]

    fin = df[cols].copy()
    fin.columns = [available.get(c, c) for c in fin.columns]

    print(f"  基本面: {len(fin)} 行, {fin['code'].nunique()} 只, {len(available)} 因子: {list(available.values())}")
    return fin


def load_valuation():
    """加载估值数据"""
    if not os.path.exists(VALUATION_FILE):
        return None

    df = pq.read_table(VALUATION_FILE).to_pandas()
    df['date'] = pd.to_datetime(df['date'])

    val_cols = {
        'pe_ttm': 'PE_TTM',
        'pb': 'PB',
        'eps_ttm': 'EPS_TTM',
        'bvps': 'BVPS',
    }
    available = {k: v for k, v in val_cols.items() if k in df.columns}
    cols = ['code', 'date'] + list(available.keys())
    cols = [c for c in cols if c in df.columns]

    val = df[cols].copy()
    val.columns = [available.get(c, c) for c in val.columns]

    print(f"  估值: {len(val)} 行, {len(available)} 因子: {list(available.values())}")
    return val


def ic_weighted_score(factor_name, ic_history, min_ic_window=IC_WINDOW):
    """
    根据滚动IC确定因子权重
    返回: 1(做多), -1(做空), 0(放弃)
    """
    if factor_name not in ic_history:
        return 0

    hist = ic_history[factor_name]
    if len(hist) < min_ic_window:
        return 0
    
    recent = np.array(hist[-min_ic_window:])
    mean_ic = recent.mean()
    std_ic = recent.std()
    ir = mean_ic / (std_ic + 1e-8)
    
    # 方向一致性：至少60%同号
    direction = np.sign(mean_ic)
    consistency = (np.sign(recent) == direction).mean()

    if abs(mean_ic) < 0.01 or ir < 0.2 or consistency < 0.55:
        return 0

    return direction  # 1 或 -1


def orthogonalize_factors(df, factor_cols, method='gram_schmidt'):
    """因子正交化(Gram-Schmidt),按IC排序后正交"""
    if len(factor_cols) < 2:
        return df, factor_cols

    # 提取因子矩阵
    fac_data = df[factor_cols].fillna(0.5).values
    fac_data = fac_data.astype(float)

    n = fac_data.shape[1]
    ortho = np.zeros_like(fac_data)

    # 按方差排序(近似按IC重要性)
    variances = np.var(fac_data, axis=0)
    order = np.argsort(-variances)

    ortho[:, 0] = fac_data[:, order[0]]
    for i in range(1, n):
        vec = fac_data[:, order[i]].copy()
        for j in range(i):
            proj = np.dot(vec, ortho[:, j]) / (np.dot(ortho[:, j], ortho[:, j]) + 1e-8)
            vec -= proj * ortho[:, j]
        ortho[:, i] = vec

    # 标准化
    eps = 1e-8
    ortho = (ortho - ortho.mean(axis=0)) / (ortho.std(axis=0) + eps)

    # 写回
    new_names = [f"{factor_cols[order[i]]}_orth" for i in range(n)]
    for i, name in enumerate(new_names):
        df[name] = ortho[:, i]

    return df, new_names


def main():
    t0 = time.time()

    # ── 1. 加载数据 ──
    print("=" * 60)
    print("  Backtest v3 - IC加权 + 基本面 + 正交化")
    print("=" * 60)

    # 1a. 因子缓存
    cache_path, cache_mode = find_cache()
    print(f"\n📦 因子缓存: {cache_mode}")
    if cache_mode == 'v3_single':
        df_cache = pd.read_parquet(cache_path)
        df_cache['date'] = pd.to_datetime(df_cache['date'])
    elif cache_mode == 'v3_parts':
        df_cache = load_v3_cache_via_parts_clean(cache_path)
    else:
        print("❌ 无可用因子缓存")
        return

    if df_cache is None or len(df_cache) < 1000:
        print("❌ 因子缓存不可用")
        return

    # 1b. 基本面 + 估值
    df_fin = load_fundamentals()
    df_val = load_valuation()

    # 1c. 行业映射
    ind_map = load_industry_map()
    code_to_ind = dict(zip(ind_map['code'], ind_map['industry']))

    # ── 2. 确定可用因子列表 ──
    all_candidate_factors = [c for c in df_cache.columns
                            if c not in ['date', 'code', 'close'] and not c.startswith('_')]
    print(f"\n📊 候选因子: {len(all_candidate_factors)} 个")

    # 按因子类型分组
    base_factors_avail = [c for c in BASE_FACTORS if c in all_candidate_factors]
    alpha_factors_avail = sorted([c for c in all_candidate_factors
                                 if c.startswith('alpha') and c not in base_factors_avail])
    print(f"  原始因子: {len(base_factors_avail)} 个")
    print(f"  Alpha101: {len(alpha_factors_avail)} 个")

    # ── 3. 准备截面数据 ──
    all_dates = sorted(df_cache['date'].unique())
    sample_dates = [d for d in all_dates
                    if d >= pd.Timestamp('2024-10-01') and d <= pd.Timestamp('2026-05-15')]
    sample_dates = sample_dates[::3]  # 每3天采样
    print(f"\n📅 交易日: {len(all_dates)}, 回测截面: {len(sample_dates)}")

    # ── 4. 预加载未来收益 ──
    print("\n📈 预计算未来收益...")
    all_close = df_cache[['date', 'code', 'close']].sort_values(['code', 'date']).reset_index(drop=True)
    for hd in HOLD_DAYS:
        fcol = f'fret_{hd}d'
        all_close[fcol] = all_close.groupby('code')['close'].transform(lambda x: x.shift(-hd) / x - 1)
        all_close[fcol] = all_close[fcol].replace([np.inf, -np.inf], np.nan)
    print(f"  预加载完成: {len(all_close)} 行")

    # ── 5. 滚动IC跟踪 ──
    ic_history = {fac: [] for fac in all_candidate_factors}

    # ── 6. 回测主循环 ──
    all_ic = []
    all_group = []
    all_ls = []
    factor_usage = {}  # 跟踪每个因子被使用的次数
    daily_weights = []  # 记录每日使用的因子权重

    for i, d in enumerate(sample_dates):
        if (i+1) % 20 == 0:
            elapsed = time.time() - t0
            eta = elapsed / (i+1) * (len(sample_dates) - i - 1)
            n_used = len(factor_usage)
            print(f"  [{i+1}/{len(sample_dates)}] IC={len(all_ic)} LS={len(all_ls)} "
                  f"因子={n_used} ETA={eta/60:.1f}min", flush=True)

        dt = pd.Timestamp(d).to_pydatetime()
        ds = str(d)[:10]

        # 读截面数据
        day = df_cache[df_cache['date'] == d].copy()
        if len(day) < MIN_STOCKS * 3:
            continue

        day['industry'] = day['code'].map(code_to_ind).fillna('Other')

        # 合并估值/基本面(取最新可用)
        if df_val is not None:
            day = day.merge(df_val[df_val['date'] <= d].groupby('code').last().reset_index(),
                          on='code', how='left', suffixes=('', '_val'))
        if df_fin is not None:
            day = day.merge(df_fin[df_fin['date'] <= d].groupby('code').last().reset_index(),
                          on='code', how='left', suffixes=('', '_fin'))

        # 已有因子名
        avail_factor_cols = [c for c in all_candidate_factors if c in day.columns]

        # 加入基本面因子
        fin_factor_cols = []
        for fcol in ['PE_TTM', 'PB', 'ROE_TTM', 'YOY_PROFIT', 'YOY_REV', 'GPM', 'LEV', 'NETMARGIN']:
            if fcol in day.columns:
                # 截面排名
                day[f'{fcol}_rank'] = day[fcol].rank(pct=True).fillna(0.5)
                fin_factor_cols.append(f'{fcol}_rank')

        all_today_factors = avail_factor_cols + fin_factor_cols

        if len(all_today_factors) < 5:
            continue

        # 行业中性化
        neutral_list = [c for c in all_today_factors if day[c].notna().sum() > 10]
        if neutral_list:
            day = neutral_by_industry(day, neutral_list, method='rank')

        # ── IC加权 ──
        # 先算今天对未来收益的IC(用最新因子)
        # 对未来5天收益算IC
        day_sorted = day.sort_values('code')
        codes_today = day_sorted['code'].values
        closes_today = day_sorted['close'].values

        # 直接算截面因子对未来收益的IC
        active_factors = []
        factor_weights = []

        for fac in all_today_factors:
            fac_neutral = fac + '_neutral'
            use_col = fac_neutral if fac_neutral in day_sorted.columns else fac
            vals = day_sorted[use_col].values

            # 更新IC历史(今天用截面IC)
            # 截面: 因子值 vs 未来5天收益
            # 用未来N天的收益在下一圈处理,这里先记录因子方向

            # 根据历史IC选方向
            direction = ic_weighted_score(fac, ic_history)
            if direction != 0:
                active_factors.append(use_col)
                # 权重 = IC均值 * IR
                hist = ic_history[fac]
                recent = np.array(hist[-IC_WINDOW:])
                mean_ic = recent.mean()
                std_ic = recent.std()
                ir = mean_ic / (std_ic + 1e-8)
                factor_weights.append(abs(mean_ic) * ir)
                factor_usage[fac] = factor_usage.get(fac, 0) + 1

        if len(active_factors) < 3:
            # IC窗口太短,用已有因子的截面IC作为冷启动
            # 直接用原始因子中等权
            simple_factors = ['rev_5d', 'mom_20d', 'vol_20d', 'alpha3', 'zscore_ma20',
                             'alpha001', 'alpha005', 'alpha036', 'alpha046', 'alpha096']
            simple_use = []
            for sf in simple_factors:
                use_col = sf + '_neutral' if sf + '_neutral' in day_sorted.columns else (sf if sf in day_sorted.columns else None)
                if use_col and day_sorted[use_col].notna().sum() > 10:
                    simple_use.append(use_col)
            if not simple_use:
                simple_use = [c for c in all_today_factors[:8] if day_sorted[c].notna().sum() > 10]
            active_factors = simple_use if simple_use else all_today_factors[:5]
            factor_weights = [1.0] * len(active_factors)

        factor_weights = np.array(factor_weights)
        factor_weights = factor_weights / (factor_weights.sum() + 1e-8)

        daily_weights.append({
            'date': ds,
            'n_factors': len(active_factors),
            'top3': sorted(zip(active_factors, factor_weights), key=lambda x: -x[1])[:3]
        })

        # ── 正交化 ──
        if len(active_factors) >= 3:
            day_sorted, ortho_names = orthogonalize_factors(day_sorted, active_factors[:15])
            ortho_use = ortho_names[:min(10, len(ortho_names))]
        else:
            ortho_use = active_factors

        # ── 综合打分 ──
        day_sorted['factor_score'] = 0.0
        for j, fac in enumerate(ortho_use[:15]):
            w = factor_weights[j] if j < len(factor_weights) else 1.0/len(ortho_use)
            day_sorted['factor_score'] += day_sorted[fac].fillna(0) * w

        # 分组
        n = len(day_sorted)
        rank_order = day_sorted['factor_score'].rank(method='first')
        day_sorted['group'] = np.ceil(rank_order / (n + 1) * N_GROUPS).astype(int).clip(1, N_GROUPS)

        # ── 合并未来收益 ──
        day_sorted = day_sorted.reset_index(drop=True)

        fret_slice = all_close[all_close['date'] == d][['code'] + [f'fret_{hd}d' for hd in HOLD_DAYS]].copy()
        if not fret_slice.empty:
            day_sorted = day_sorted.merge(fret_slice, on='code', how='left')

        # 更新IC历史
        for fac in all_today_factors:
            if fac not in day_sorted.columns:
                continue
            vals = day_sorted[fac].values
            if vals is None or np.isnan(vals).all():
                continue
            for hd in HOLD_DAYS:
                fcol = f'fret_{hd}d'
                if fcol not in day_sorted.columns:
                    continue
                valid = ~(np.isnan(vals) | np.isnan(day_sorted[fcol].values))
                if valid.sum() > MIN_STOCKS:
                    ic_v = np.corrcoef(vals[valid], day_sorted[fcol].values[valid])[0, 1]
                    if np.isfinite(ic_v):
                        if fac not in ic_history:
                            ic_history[fac] = []
                        ic_history[fac].append(ic_v)

        # ── 记录结果 ──
        # IC
        for fac in all_today_factors:
            for hd in HOLD_DAYS:
                fcol = f'fret_{hd}d'
                if fcol not in day_sorted.columns:
                    continue
                vals = day_sorted[fac].values
                valid = ~(np.isnan(vals) | np.isnan(day_sorted[fcol].values))
                if valid.sum() > MIN_STOCKS:
                    ic_val = np.corrcoef(vals[valid], day_sorted[fcol].values[valid])[0, 1]
                    if np.isfinite(ic_val):
                        all_ic.append({'date': ds, 'factor': fac, 'hold_days': hd, 'IC': ic_val})

        # 分组收益
        for hd in HOLD_DAYS:
            fcol = f'fret_{hd}d'
            if fcol not in day_sorted.columns:
                continue

            for g in range(1, N_GROUPS+1):
                gr = day_sorted[day_sorted['group'] == g][fcol].dropna()
                if len(gr) > 0:
                    raw = gr.mean()
                    if np.isfinite(raw):
                        all_group.append({'date': ds, 'hd': hd, 'g': g,
                                          'ret': raw, 'ret_net': raw - cost_roundtrip,
                                          'n': len(gr)})

            g1 = day_sorted[day_sorted['group'] == 1][fcol].dropna()
            g5 = day_sorted[day_sorted['group'] == N_GROUPS][fcol].dropna()
            if len(g1) > MIN_STOCKS and len(g5) > MIN_STOCKS:
                g1m = g1.mean(); g5m = g5.mean()
                if np.isfinite(g1m) and np.isfinite(g5m):
                    ls = g1m - g5m
                    all_ls.append({'date': ds, 'hd': hd,
                                   'g1': g1m, 'g5': g5m, 'ls': ls,
                                   'ls_net': ls - cost_roundtrip * 2})

    total_time = time.time() - t0

    # ── 6. 报告 ──
    print(f"\n{'='*60}")
    print(f"  Backtest v3 完成")
    print(f"  IC记录: {len(all_ic)}, 分组: {len(all_group)}, 多空: {len(all_ls)}")
    print(f"  耗时: {total_time:.0f}s")
    print(f"{'='*60}")

    ic_df = pd.DataFrame(all_ic)
    gdf = pd.DataFrame(all_group)
    ls_df = pd.DataFrame(all_ls)

    # IC报告
    if len(ic_df) > 0:
        print(f"\n📊 IC分析 (Top15因子)")
        print(f"{'因子':<20} {'持有':>4} {'IC均值':>8} {'ICIR':>8} {'胜率':>8}")
        print("-" * 50)

        # 按IC均值绝对值排序
        ic_summary = ic_df.groupby(['factor', 'hold_days']).agg(
            ic_mean=('IC', 'mean'),
            ic_std=('IC', 'std'),
            win_rate=('IC', lambda x: (x>0).mean()),
            n=('IC', 'count')
        ).reset_index()
        ic_summary['IR'] = ic_summary['ic_mean'] / (ic_summary['ic_std'] + 1e-8)
        ic_summary = ic_summary.sort_values('ic_mean', key=lambda x: abs(x), ascending=False)

        for _, row in ic_summary.head(15).iterrows():
            print(f"{row['factor']:<20} {row['hold_days']:>4}d "
                  f"{row['ic_mean']:>8.4f} {row['IR']:>8.4f} {row['win_rate']:>8.1%}")

    # 分组收益
    if len(gdf) > 0:
        print(f"\n📊 分组收益(成本调整后)")
        for hd in HOLD_DAYS:
            sub = gdf[gdf['hd'] == hd]
            if sub.empty: continue
            pf = sub.groupby('g').agg({'ret': ['mean', 'count'], 'ret_net': 'mean'})
            print(f"\n持有{hd:>2}d:")
            for g in range(1, N_GROUPS+1):
                if g not in pf.index: continue
                rm = pf.loc[g, ('ret','mean')]
                nm = pf.loc[g, ('ret_net','mean')]
                rc = int(pf.loc[g, ('ret','count')])
                print(f"  G{g}: 原始{rm:.4%} | 净{nm:.4%} | 样本{rc}")

            g1df = sub[sub['g']==1].set_index('date')['ret']
            g5df = sub[sub['g']==N_GROUPS].set_index('date')['ret']
            comm = g1df.index.intersection(g5df.index)
            if len(comm) > 5:
                ls_vals = g1df.loc[comm] - g5df.loc[comm]
                print(f"  多空: 均值{ls_vals.mean():.4%} | 年化{ls_vals.mean()*252/hd:.2%}")

    # 多空绩效
    if len(ls_df) > 0:
        print(f"\n📊 多空策略绩效")
        for hd in HOLD_DAYS:
            sub = ls_df[ls_df['hd'] == hd]
            if len(sub) < 10: continue
            rets = sub.set_index('date')['ls']
            cum = (1 + rets).cumprod()
            max_dd = ((cum/cum.cummax()) - 1).min()
            ann_ret = (1 + rets.mean()) ** (252 / hd) - 1
            ann_vol = rets.std() * np.sqrt(252 / hd)
            sharpe = ann_ret / ann_vol if ann_vol > 0 else 0
            print(f"\n持有{hd:>2}d:")
            print(f"  年化: {ann_ret:.2%} | 波动: {ann_vol:.2%} | "
                  f"夏普: {sharpe:.2f} | 最大回撤: {max_dd:.2%} | "
                  f"胜率: {(rets>0).mean():.1%}")

    # 因子使用统计
    if factor_usage:
        print(f"\n📊 因子使用频率(Top20)")
        usage_sorted = sorted(factor_usage.items(), key=lambda x: -x[1])[:20]
        for fac, cnt in usage_sorted:
            print(f"  {fac:<25} {cnt:>4}次")

    # 保存结果
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    if all_ic:
        pd.DataFrame(all_ic).to_csv(os.path.join(OUTPUT_DIR, 'ic_v3.csv'), index=False)
    if all_group:
        pd.DataFrame(all_group).to_csv(os.path.join(OUTPUT_DIR, 'group_v3.csv'), index=False)
    if all_ls:
        pd.DataFrame(all_ls).to_csv(os.path.join(OUTPUT_DIR, 'ls_v3.csv'), index=False)

    print(f"\n✅ 结果保存至: {OUTPUT_DIR}")
    print(f"总耗时: {(time.time()-t0)/60:.1f} 分钟")


if __name__ == '__main__':
    main()

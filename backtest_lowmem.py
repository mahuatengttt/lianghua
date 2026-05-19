#!/usr/bin/env python3
"""
backtest_lowmem.py — 低内存版全量回测 v1.0
========================================
按日期流式加载因子数据，每天只保留一行截面在内存中。
适合 1.8G 内存环境跑 473 个交易日 × 3000+ 只股票的全量回测。

与原 backtest_unified.py 核心逻辑保持一致：
- 因子评分 → IC加权 → 行业中性化 → 信号退出 → 逐日净值

用法:
  python3 backtest_lowmem.py
  python3 backtest_lowmem.py --start 2024-06-01 --end 2026-05-18
"""
import os, sys, time, gc, json, argparse, warnings
import numpy as np
import pandas as pd
import pyarrow.parquet as pq
from glob import glob
from collections import defaultdict

warnings.filterwarnings('ignore')

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from trading_system import load_industry_map

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "a_stock_data")
CACHE_V3_DIR = os.path.join(DATA_DIR, "factor_cache_v3")
OUTPUT_DIR = os.path.join(BASE_DIR, "bt_unified_output")
os.makedirs(OUTPUT_DIR, exist_ok=True)

# ═══════════════════════════════════════════
# 工具函数
# ═══════════════════════════════════════════

def get_v3_schema():
    """获取因子列名"""
    v3_files = sorted(glob(f"{CACHE_V3_DIR}/part_*.parquet"))
    pf = pq.ParquetFile(v3_files[-1])
    return [f.name for f in pf.schema]

def read_day_from_v3(date_str, v3_files, keep_cols=None):
    """仅读取指定日期的截面数据（低内存）"""
    dt = pd.Timestamp(date_str)
    # 只读需要的月份分片
    ym = dt.strftime("%Y-%m")
    target_file = None
    for f in v3_files:
        if ym in f:
            target_file = f
            break
    if not target_file:
        return None

    pf = pq.ParquetFile(target_file)
    rows = []
    for batch in pf.iter_batches(batch_size=50000):
        bdf = batch.to_pandas()
        bdf["date"] = pd.to_datetime(bdf["date"])
        sub = bdf[bdf["date"] == dt]
        if len(sub):
            if keep_cols:
                sub = sub[keep_cols]
            rows.append(sub)
        del bdf
    if not rows:
        return None
    result = pd.concat(rows, ignore_index=True)
    return result

def calc_trade_cost(price, amount, is_buy=True):
    """计算交易成本"""
    commission = max(price * amount * 0.00025, 5.0)
    stamp = price * amount * 0.001 if not is_buy else 0
    slippage = price * amount * 0.001
    return slippage + commission + stamp

def cost_adjusted_return(return_pct):
    """总收益 - 双边成本估算"""
    return return_pct - 0.0027

def load_industry_map_cached():
    ind = pd.read_csv(f"{DATA_DIR}/industry_map.csv")
    ind["code"] = ind["code"].astype(str)
    return dict(zip(ind["code"], ind["industry"]))

def load_fundamentals():
    """加载基本面（仅最新截面）"""
    result = {}
    fp = f"{DATA_DIR}/fundamental_ext_v3.parquet"
    vp = f"{DATA_DIR}/fundamental_valuation_v3.parquet"
    if os.path.exists(fp):
        fund = pq.read_table(fp).to_pandas()
        fund["code"] = fund["code"].astype(str)
        if "stat_date" in fund.columns:
            fund["date"] = pd.to_datetime(fund["stat_date"])
        elif "date" in fund.columns:
            fund["date"] = pd.to_datetime(fund["date"])
        fund = fund.sort_values("date").groupby("code").last()
        # rename
        rename_map = {"roe_ttm2": "roe", "yoy_profit": "profit_growth",
                      "yoy_revenue": "revenue_growth", "gross_profit_margin": "gross_margin",
                      "ttm_roe": "roe", "debt_to_asset": "debt_ratio"}
        fund = fund.rename(columns={k: v for k, v in rename_map.items() if k in fund.columns})
        result["fundamental"] = fund
    if os.path.exists(vp):
        val = pq.read_table(vp).to_pandas()
        val["code"] = val["code"].astype(str)
        val["date"] = pd.to_datetime(val["date"])
        val = val.sort_values("date").groupby("code").last()
        rename_map = {"pe_ttm": "pe", "pb": "pb"}
        val = val.rename(columns={k: v for k, v in rename_map.items() if k in val.columns})
        result["valuation"] = val
    return result if result else None

def get_stock_name(code):
    fpath = f"{DATA_DIR}/daily_clean/{code}.parquet"
    if os.path.exists(fpath):
        try:
            return pq.read_table(fpath, columns=["name"]).to_pandas()["name"].iloc[0]
        except:
            return ""
    return ""

# ═══════════════════════════════════════════
# ICTracker
# ═══════════════════════════════════════════

class ICTracker:
    def __init__(self, window=60, min_abs=0.01, min_ir=0.2, min_consistency=0.55):
        self.window = window
        self.min_abs = min_abs
        self.min_ir = min_ir
        self.min_consistency = min_consistency
        self.history = defaultdict(list)

    def update(self, factor_name, ic_value):
        if not np.isfinite(ic_value):
            return
        self.history[factor_name].append(ic_value)
        h = self.history[factor_name]
        if len(h) > self.window * 2:
            self.history[factor_name] = h[-self.window*2:]

    def get_weight(self, factor_name):
        h = self.history.get(factor_name, [])
        if len(h) < self.window // 2:
            return 1.0
        recent = np.array(h[-self.window:])
        mean_ic = recent.mean()
        std_ic = recent.std()
        if std_ic < 1e-8:
            return 0.0
        ir = mean_ic / std_ic if std_ic > 0 else 0
        if abs(mean_ic) < self.min_abs or ir < self.min_ir:
            return 0.0
        sign = np.sign(mean_ic)
        consist = (np.sign(recent) == sign).mean()
        if consist < self.min_consistency:
            return 0.0
        strength = abs(mean_ic) * ir
        return sign * min(strength, 5.0)

    def get_weights_for_list(self, factors):
        return {f: self.get_weight(f) for f in factors}

# ═══════════════════════════════════════════
# 策略运行
# ═══════════════════════════════════════════

def run_backtest(cfg):
    t0 = time.time()
    print(f"\n{'='*65}")
    print(f"  低内存全量回测 v1.0")
    print(f"  范围: {cfg['start']} → {cfg['end']}")
    print(f"  选股: Top{cfg['top_n']}, 每{cfg['rebalance']}天换仓")
    print(f"{'='*65}")

    # ── 交易日历 ──
    cal = pd.read_csv(f"{DATA_DIR}/trade_calendar.csv", header=None, names=["date"])
    cal["date"] = pd.to_datetime(cal["date"])
    cal = cal[(cal["date"] >= pd.Timestamp(cfg["start"])) &
              (cal["date"] <= pd.Timestamp(cfg["end"]))]
    trade_dates = sorted(cal["date"].tolist())
    n_days = len(trade_dates)
    print(f"  📅 交易日: {n_days} 天")

    if n_days == 0:
        print("❌ 无交易日数据")
        return

    # ── 分片文件列表 ──
    v3_files = sorted(glob(f"{CACHE_V3_DIR}/part_*.parquet"))
    schema_cols = get_v3_schema()
    base_factors = [c for c in ["mom_20d","rev_5d","vol_20d","alpha3","alpha12",
                                 "amplitude_20d","turn_20d_avg","price_ma20",
                                 "vol_ratio_5_20","zscore_ma20"] if c in schema_cols]
    alpha_factors = sorted([c for c in schema_cols if c.startswith("alpha")
                            and c not in base_factors])
    candidate_factors = base_factors + alpha_factors[:cfg["max_alpha"]]
    print(f"  🔬 候选因子: {len(candidate_factors)} (基础{len(base_factors)}+α{len(alpha_factors[:cfg['max_alpha']])})")

    # ── 加载静态数据 ──
    ind_map = load_industry_map_cached()
    print(f"  🏭 行业: {len(set(ind_map.values()))} 个")

    fundamentals = load_fundamentals()
    if fundamentals:
        print(f"  📥 基本面: {len(fundamentals.get('fundamental', []))} 只")
        print(f"  📥 估值: {len(fundamentals.get('valuation', []))} 只")

    # 名称缓存
    name_cache = {}

    # ── 冷处理/大盘追踪 ──
    cooldown_map = {}  # {code: cool_until_date}
    market_daily_rets = []
    market_dates = []

    def get_market_state(ds):
        """判断大盘状态：up/oscillate/down"""
        if len(market_daily_rets) < 10:
            return "oscillate", 1.0
        recent = np.array(market_daily_rets[-20:])
        cum_ret = np.prod(1 + recent) - 1
        if cum_ret < -0.05:
            return "down", 0.4
        elif cum_ret < -0.02:
            return "down_mild", 0.65
        elif cum_ret > 0.03:
            return "up", 1.0
        else:
            return "oscillate", 0.8

    # ── IC跟踪 & 换仓日历 ──
    ic_tracker = ICTracker(window=cfg.get("ic_window", 60))
    rebalance_set = set()
    for i, d in enumerate(trade_dates):
        if i % cfg["rebalance"] == 0:
            rebalance_set.add(d)

    # ── 持仓状态 ──
    # positions: {code: {name, buy_date, buy_price, shares, weight, factor_score}}
    positions = {}
    cash = 10_000_000.0
    equity_curve = []
    trades = []
    total_trades = 0
    peak_value = 1.0
    liquidated = False

    # ── 进度跟踪 ──
    last_report = time.time()
    factor_usage = defaultdict(int)
    daily_ic_records = []
    keep_columns = ["date","code","close"] + candidate_factors

    # ── 主循环 ──
    for i, date in enumerate(trade_dates):
        ds = str(date)[:10]
        now = time.time()

        # 进度报告
        if i == 0 or (i+1) % 20 == 0 or now - last_report > 20:
            elapsed = now - t0
            rate = (i+1) / elapsed if elapsed > 0 else 1
            remain = (n_days - i - 1) / rate if rate > 0 else 0
            pos_val = sum(p["shares"] * p.get("current_price", p["buy_price"]) for p in positions.values())
            tv = cash + pos_val
            print(f"  [{i+1}/{n_days}] {ds} | 持仓{len(positions)} | "
                  f"净值{tv:.1f} | 交易{total_trades}笔 | ETA {remain:.0f}s")
            last_report = now

        # ── 读取当天截面 ──
        day = read_day_from_v3(date, v3_files, keep_columns)
        if day is None or len(day) < 100:
            eq_rec = {"date": ds, "cash": cash,
                      "holdings_value": sum(
                          p["shares"] * p.get("current_price", p["buy_price"])
                          for p in positions.values()),
                      "n_positions": len(positions),
                      "daily_ret": 0}
            tv = eq_rec["cash"] + eq_rec["holdings_value"]
            eq_rec["total_value"] = tv
            eq_rec["peak"] = peak_value
            eq_rec["drawdown"] = (peak_value - tv) / peak_value if peak_value > 0 else 0
            equity_curve.append(eq_rec)
            continue

        # ── 更新大盘状态 ──
        if day is not None and len(day) > 100 and "close" in day.columns:
            mkt_ret = day["close"].pct_change().mean()
            if np.isfinite(mkt_ret):
                market_daily_rets.append(mkt_ret)
                if len(market_daily_rets) > 60:
                    market_daily_rets[:] = market_daily_rets[-60:]
        market_state, position_ratio = get_market_state(ds)

        # ── 附加行业 ──
        day["code_s"] = day["code"].astype(str)
        day["industry"] = day["code_s"].map(ind_map).fillna("Other")

        # ── 附加基本面 ──
        if fundamentals:
            fin = fundamentals.get("fundamental")
            if fin is not None:
                fin_cols = [c for c in fin.columns if c not in ["code","date"]]
                for fc in fin_cols:
                    if fc not in day.columns:
                        day[fc] = day["code_s"].map(fin[fc])
            val = fundamentals.get("valuation")
            if val is not None:
                val_cols = [c for c in val.columns if c not in ["code","date"]]
                for vc in val_cols:
                    if vc not in day.columns:
                        day[vc] = day["code_s"].map(val[vc])

        # ── 计算IC & 因子权重 ──
        # 未来收益（5日）
        future_row = read_day_from_v3(
            date + pd.Timedelta(days=7), v3_files, ["date","code","close"])
        if future_row is not None:
            fr_map = dict(zip(future_row["code"], future_row["close"]))
            day["fret"] = (day["code"].map(fr_map) / day["close"] - 1).fillna(0)
        else:
            day["fret"] = 0.0

        # 更新IC
        for fac in candidate_factors:
            if fac in day.columns:
                valid = day[fac].notna() & day["fret"].notna()
                if valid.sum() > 30:
                    ic = day.loc[valid, fac].rank().corr(day.loc[valid, "fret"].rank())
                    if np.isfinite(ic):
                        ic_tracker.update(fac, ic)

        # 获取因子权重
        fac_weights = ic_tracker.get_weights_for_list(candidate_factors)
        active_factors = [f for f, w in fac_weights.items() if abs(w) > 0.01 and f in day.columns]
        if not active_factors:
            active_factors = [f for f in candidate_factors if f in day.columns]

        for f in active_factors:
            factor_usage[f] += 1

        # ── 行业中性化 + 打分 ──
        neutral_cols = []
        for f in active_factors:
            nc = f + "_n"
            day[nc] = day.groupby("industry")[f].transform(
                lambda x: x.rank(pct=True) - 0.5)
            neutral_cols.append(nc)

        day["score"] = sum(
            day[nc].fillna(0) * fac_weights.get(f, 1.0)
            for f, nc in zip(active_factors, neutral_cols)
        )

        # ── 过滤不可交易 ──
        day = day.dropna(subset=["close"])
        day = day[(day["close"] >= 3) & (day["close"] <= 1000)]
        day = day[day["score"].notna()]

        if len(day) < 50:
            eq_rec = {"date": ds, "cash": cash,
                      "holdings_value": sum(
                          p["shares"] * p.get("current_price", p["buy_price"])
                          for p in positions.values()),
                      "n_positions": len(positions),
                      "daily_ret": 0}
            tv = eq_rec["cash"] + eq_rec["holdings_value"]
            eq_rec["total_value"] = tv
            eq_rec["peak"] = peak_value
            eq_rec["drawdown"] = (peak_value - tv) / peak_value if peak_value > 0 else 0
            equity_curve.append(eq_rec)
            continue

        # ── 更新持仓市值 ──
        price_map = dict(zip(day["code"], day["close"]))
        for pos_code in list(positions.keys()):
            cp = price_map.get(pos_code)
            if cp is not None:
                positions[pos_code]["current_price"] = cp

        # ── 检查退出信号（每日） — v2优化版 ──
        score_map = dict(zip(day["code"], day["score"]))
        for pos_code in list(positions.keys()):
            pos = positions[pos_code]
            if pos_code not in price_map:
                continue
            cur_price = price_map[pos_code]
            ret = cur_price / pos["buy_price"] - 1
            hold_days = (pd.Timestamp(ds) - pd.Timestamp(pos["buy_date"])).days

            # 优化1: 动态止损（大盘差时收紧）
            stop_loss = cfg["stop_loss"]
            if market_state == "down":
                stop_loss = max(stop_loss, -0.07)
            if ret <= stop_loss:
                shares = pos["shares"]
                cost = calc_trade_cost(cur_price, shares, is_buy=False)
                proceeds = cur_price * shares - cost
                cash += proceeds
                trades.append({
                    **pos, "sell_date": ds, "sell_price": cur_price,
                    "hold_days": hold_days,
                    "return_pct": ret, "return_net": cost_adjusted_return(ret),
                    "exit_reason": f"stoploss_{ret:.0%}"
                })
                total_trades += 1
                # 冷处理：止损后30天不再买入同一只
                cooldown_map[pos_code] = ds
                del positions[pos_code]
                continue

            # 止盈
            if ret >= cfg["take_profit"]:
                shares = pos["shares"]
                cost = calc_trade_cost(cur_price, shares, is_buy=False)
                proceeds = cur_price * shares - cost
                cash += proceeds
                trades.append({
                    **pos, "sell_date": ds, "sell_price": cur_price,
                    "hold_days": hold_days,
                    "return_pct": ret, "return_net": cost_adjusted_return(ret),
                    "exit_reason": f"takeprofit_{ret:.0%}"
                })
                total_trades += 1
                del positions[pos_code]
                continue

            # 优化2: 提前小亏退出（持有10天以上还亏>3%，砍）
            if hold_days >= 10 and ret < -0.03:
                shares = pos["shares"]
                cost = calc_trade_cost(cur_price, shares, is_buy=False)
                proceeds = cur_price * shares - cost
                cash += proceeds
                trades.append({
                    **pos, "sell_date": ds, "sell_price": cur_price,
                    "hold_days": hold_days,
                    "return_pct": ret, "return_net": cost_adjusted_return(ret),
                    "exit_reason": f"early_exit_{ret:.0%}"
                })
                total_trades += 1
                del positions[pos_code]
                continue

            # 优化3: 时间止损（持有>30天不赚钱就走）
            time_stop = 30
            if hold_days >= time_stop and ret < 0.01:
                shares = pos["shares"]
                cost = calc_trade_cost(cur_price, shares, is_buy=False)
                proceeds = cur_price * shares - cost
                cash += proceeds
                trades.append({
                    **pos, "sell_date": ds, "sell_price": cur_price,
                    "hold_days": hold_days,
                    "return_pct": ret, "return_net": cost_adjusted_return(ret),
                    "exit_reason": f"time_stop_{ret:.0%}"
                })
                total_trades += 1
                del positions[pos_code]
                continue

            # 信号弱化退出
            if pos_code in score_map:
                pos_score = pos["factor_score"]
                cur_score = score_map[pos_code]
                if pos_score > 0 and cur_score < pos_score * cfg["rank_drop_threshold"]:
                    shares = pos["shares"]
                    cost = calc_trade_cost(cur_price, shares, is_buy=False)
                    proceeds = cur_price * shares - cost
                    cash += proceeds
                    trades.append({
                        **pos, "sell_date": ds, "sell_price": cur_price,
                        "hold_days": hold_days,
                        "return_pct": ret, "return_net": cost_adjusted_return(ret),
                        "exit_reason": f"signal_{ret:.0%}"
                    })
                    total_trades += 1
                    del positions[pos_code]
                    continue

            # 最长持股天数
            if cfg["hold_max"] > 0:
                if hold_days >= cfg["hold_max"]:
                    shares = pos["shares"]
                    cost = calc_trade_cost(cur_price, shares, is_buy=False)
                    proceeds = cur_price * shares - cost
                    cash += proceeds
                    trades.append({
                        **pos, "sell_date": ds, "sell_price": cur_price,
                        "hold_days": hold_days,
                        "return_pct": ret, "return_net": cost_adjusted_return(ret),
                        "exit_reason": f"max_days_{cfg['hold_max']}"
                    })
                    total_trades += 1
                    del positions[pos_code]

        # ── 组合回撤风控 ──
        pos_val = sum(p["shares"] * p.get("current_price", p["buy_price"]) for p in positions.values())
        tv = cash + pos_val
        if tv > peak_value:
            peak_value = tv
        dd = (peak_value - tv) / peak_value if peak_value > 0 else 0
        if dd > cfg["max_drawdown"] and not liquidated:
            print(f"  ⚠ {ds}: 回撤{dd:.1%}超限，紧急清仓")
            for pos_code in list(positions.keys()):
                pos = positions[pos_code]
                cp = price_map.get(pos_code, pos["buy_price"])
                shares = pos["shares"]
                cost = calc_trade_cost(cp, shares, is_buy=False)
                cash += cp * shares - cost
                trades.append({
                    **pos, "sell_date": ds, "sell_price": cp,
                    "hold_days": (pd.Timestamp(ds) - pd.Timestamp(pos["buy_date"])).days,
                    "return_pct": cp / pos["buy_price"] - 1,
                    "return_net": cost_adjusted_return(cp / pos["buy_price"] - 1),
                    "exit_reason": "emergency_liquidate"
                })
                total_trades += 1
            positions.clear()
            liquidated = True
            pos_val = 0.0
            tv = cash

        # ── 大盘过滤：下跌市场减仓 ──
        if market_state in ("down", "down_mild"):
            max_pos = max(3, int(cfg["top_n"] * position_ratio))
            if len(positions) > max_pos:
                sorted_pos = sorted(positions.items(), key=lambda x: x[1]["buy_date"])
                for pc, _ in sorted_pos[max_pos:]:
                    if pc not in price_map:
                        continue
                    pos = positions[pc]
                    cp = price_map[pc]
                    shares = pos["shares"]
                    ret = cp / pos["buy_price"] - 1
                    cost = calc_trade_cost(cp, shares, is_buy=False)
                    cash += cp * shares - cost
                    trades.append({
                        **pos, "sell_date": ds, "sell_price": cp,
                        "hold_days": (pd.Timestamp(ds) - pd.Timestamp(pos["buy_date"])).days,
                        "return_pct": ret, "return_net": cost_adjusted_return(ret),
                        "exit_reason": f"market_del_{market_state}"
                    })
                    total_trades += 1
                    del positions[pc]

        # ── 换仓日：选股+买入 ──
        if date in rebalance_set and not liquidated:
            # 根据大盘调整目标持仓数
            effective_top = max(3, int(cfg["top_n"] * position_ratio))
            avail_val = cash * 0.95
            candidates = day.nlargest(effective_top, "score")
            target_codes = set(candidates["code"].tolist())
            current_codes = set(positions.keys())

            # 卖出不在候选中的
            for pos_code in list(current_codes - target_codes):
                if pos_code not in price_map:
                    continue
                pos = positions[pos_code]
                cp = price_map[pos_code]
                shares = pos["shares"]
                ret = cp / pos["buy_price"] - 1
                cost = calc_trade_cost(cp, shares, is_buy=False)
                cash += cp * shares - cost
                trades.append({
                    **pos, "sell_date": ds, "sell_price": cp,
                    "hold_days": (pd.Timestamp(ds) - pd.Timestamp(pos["buy_date"])).days,
                    "return_pct": ret, "return_net": cost_adjusted_return(ret),
                    "exit_reason": "rebalance_out"
                })
                total_trades += 1
                del positions[pos_code]

            # 买入候选股（补足到目标数量，排除冷处理票）
            new_codes = target_codes - current_codes
            # 过滤冷处理中的
            cooldown_active = {c for c in new_codes if c in cooldown_map and pd.Timestamp(ds) < pd.Timestamp(cooldown_map[c]) + pd.Timedelta(days=30)}
            new_codes = new_codes - cooldown_active
            if new_codes:
                n_new = len(new_codes)
                per_amount = avail_val / n_new
                for code in new_codes:
                    row = day[day["code"] == code]
                    if len(row) == 0:
                        continue
                    r = row.iloc[0]
                    price = r["close"]
                    score = r["score"]
                    cost = calc_trade_cost(price, 1, is_buy=True)
                    max_shares = int((per_amount - cost) / price)
                    if max_shares <= 0:
                        continue
                    amount = max_shares * price
                    total_cost = amount + cost
                    if total_cost > cash:
                        max_shares1 = int((cash - cost) / price)
                        if max_shares1 <= 0:
                            continue
                        max_shares = max_shares1
                        amount = max_shares * price
                        total_cost = amount + cost

                    cash -= total_cost
                    code_str = str(code)
                    if code_str not in name_cache:
                        name_cache[code_str] = get_stock_name(code_str)
                    positions[code] = {
                        "code": code_str,
                        "name": name_cache.get(code_str, ""),
                        "buy_date": ds,
                        "buy_price": price,
                        "shares": max_shares,
                        "weight": amount / tv if tv > 0 else 0,
                        "factor_score": score,
                        "current_price": price,
                    }

        # ── 每日净值记录 ──
        pos_val = sum(p["shares"] * p.get("current_price", p["buy_price"]) for p in positions.values())
        tv = cash + pos_val
        eq_rec = {
            "date": ds, "total_value": tv, "cash": cash,
            "holdings_value": pos_val, "peak": peak_value,
            "drawdown": (peak_value - tv) / peak_value if peak_value > 0 else 0,
            "n_positions": len(positions),
            "daily_ret": 0,
        }
        if len(equity_curve) >= 1:
            prev = equity_curve[-1]["total_value"]
            eq_rec["daily_ret"] = (tv - prev) / prev if prev > 0 else 0
        equity_curve.append(eq_rec)

        # ── 释放内存 ──
        del day
        gc.collect()

    # ══════════════════════════════════════
    # 回测结束 — 平仓
    # ══════════════════════════════════════
    last_ds = str(trade_dates[-1])[:10]
    for pos_code in list(positions.keys()):
        pos = positions[pos_code]
        cp = pos.get("current_price", pos["buy_price"])
        ret = cp / pos["buy_price"] - 1
        shares = pos["shares"]
        cost = calc_trade_cost(cp, shares, is_buy=False)
        cash += cp * shares - cost
        trades.append({
            **pos, "sell_date": last_ds, "sell_price": cp,
            "hold_days": (pd.Timestamp(last_ds) - pd.Timestamp(pos["buy_date"])).days,
            "return_pct": ret, "return_net": cost_adjusted_return(ret),
            "exit_reason": "end_of_backtest"
        })
        total_trades += 1

    # ── 生成报告 ──
    eq_df = pd.DataFrame(equity_curve)
    trades_df = pd.DataFrame(trades) if trades else pd.DataFrame()

    final_value = eq_df["total_value"].iloc[-1] if len(eq_df) > 0 else cash
    total_return = final_value / 10_000_000 - 1

    print(f"\n{'='*65}")
    print(f"  回测完成！")
    print(f"{'='*65}")
    print(f"  起始净值: 10,000,000")
    print(f"  最终净值: {final_value:,.0f}")
    print(f"  总收益:   {total_return:+.2%}")

    if len(trades_df) > 0:
        wins = trades_df[trades_df["return_pct"] > 0]
        losses = trades_df[trades_df["return_pct"] <= 0]
        win_rate = len(wins) / len(trades_df) if len(trades_df) > 0 else 0
        avg_win = wins["return_pct"].mean() if len(wins) > 0 else 0
        avg_loss = losses["return_pct"].mean() if len(losses) > 0 else 0
        avg_hold = trades_df["hold_days"].mean() if "hold_days" in trades_df.columns else 0
        print(f"  总交易:   {len(trades_df)} 笔")
        print(f"  胜率:     {win_rate:.1%}")
        print(f"  平均收益: {trades_df['return_pct'].mean():+.2%}")
        print(f"  平均盈利: {avg_win:+.2%} | 平均亏损: {avg_loss:.2%}")
        print(f"  平均持股: {avg_hold:.1f} 天")
        if losses["return_pct"].sum() != 0:
            pl_ratio = abs(wins["return_pct"].sum() / losses["return_pct"].sum())
            print(f"  盈亏比:   {pl_ratio:.2f}")

    # 最大回撤
    if len(eq_df) > 0:
        print(f"  最大回撤: {eq_df['drawdown'].min():.2%}")
        # 年化收益
        years = n_days / 252
        annualized = (1 + total_return) ** (1 / years) - 1 if years > 0 else 0
        print(f"  年化收益: {annualized:+.2%}")
        # 夏普（简化）
        daily_returns = eq_df["daily_ret"].values
        if len(daily_returns) > 1 and daily_returns.std() > 0:
            sharpe = (daily_returns.mean() / daily_returns.std()) * np.sqrt(252)
            print(f"  夏普比率: {sharpe:.2f}")
        # 卡玛
        max_dd = eq_df['drawdown'].min()
        if max_dd < 0:
            calmar = annualized / abs(max_dd) if max_dd != 0 else 0
            print(f"  卡玛比率: {calmar:.2f}")

    # ── 退出原因分布 ──
    if len(trades_df) > 0 and "exit_reason" in trades_df.columns:
        print(f"\n  退出原因分布:")
        for reason, cnt in trades_df["exit_reason"].value_counts().head(10).items():
            print(f"    {reason}: {cnt}")

    # ── 保存 ──
    timestamp = pd.Timestamp.now().strftime("%Y%m%d_%H%M%S")
    prefix = f"{OUTPUT_DIR}/bt_lowmem_{timestamp}"

    if len(eq_df) > 0:
        eq_df.to_csv(f"{prefix}_equity.csv", index=False)
        print(f"\n  💾 净值曲线: {prefix}_equity.csv")

    if len(trades_df) > 0:
        trades_df.to_csv(f"{prefix}_trades.csv", index=False)
        print(f"  💾 交易明细: {prefix}_trades.csv")

    # 因子使用
    if factor_usage:
        fu_df = pd.DataFrame([
            {"factor": f, "days_used": c}
            for f, c in sorted(factor_usage.items(), key=lambda x: -x[1])
        ])
        fu_df.to_csv(f"{prefix}_factor_usage.csv", index=False)
        print(f"  💾 因子使用: {prefix}_factor_usage.csv")

    # 配置
    with open(f"{prefix}_config.json", "w") as fp:
        json.dump(cfg, fp, indent=2)
    print(f"  💾 配置文件: {prefix}_config.json")

    elapsed = time.time() - t0
    print(f"\n⏱ 总耗时: {elapsed:.0f}s ({elapsed/60:.1f}min)")
    return eq_df, trades_df


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="低内存版全量回测")
    parser.add_argument("--start", default="2024-06-01")
    parser.add_argument("--end", default="2026-05-18")
    parser.add_argument("--top", type=int, default=20)
    parser.add_argument("--rebalance", type=int, default=5, help="换仓频率（天）")
    parser.add_argument("--hold-max", type=int, default=0, help="最大持股天数（0=不限）")
    parser.add_argument("--stop-loss", type=float, default=-0.10)
    parser.add_argument("--take-profit", type=float, default=0.25)
    parser.add_argument("--rank-drop", type=float, default=0.50)
    parser.add_argument("--max-drawdown", type=float, default=0.20)
    parser.add_argument("--max-alpha", type=int, default=20)
    parser.add_argument("--ic-window", type=int, default=60)
    args = parser.parse_args()

    cfg = {
        "start": args.start,
        "end": args.end,
        "top_n": args.top,
        "rebalance": args.rebalance,
        "hold_max": args.hold_max,
        "stop_loss": args.stop_loss,
        "take_profit": args.take_profit,
        "rank_drop_threshold": args.rank_drop,
        "max_drawdown": args.max_drawdown,
        "max_alpha": args.max_alpha,
        "ic_window": args.ic_window,
    }

    # 先跑一个 short warmup 验证不会 OOM
    eq_df, trades_df = run_backtest(cfg)

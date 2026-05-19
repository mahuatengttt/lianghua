"""
alpha101_factors.py — WorldQuant Alpha101 因子计算模块
============================================================
从已有日线数据（open/high/low/close/volume/amount/turn）计算一批量价因子。

符号对照：
  rank(x)          = x.rank(pct=True)
  delay(x, d)      = x.shift(d)
  delta(x, d)      = x.diff(d)
  ts_sum(x, d)     = x.rolling(d).sum()
  ts_mean/mean(x,d)= x.rolling(d).mean()
  ts_stddev/std(x,d)= x.rolling(d).std(ddof=0)
  ts_min(x, d)     = x.rolling(d).min()
  ts_max(x, d)     = x.rolling(d).max()
  correlation(x,y,d)= x.rolling(d).corr(y)
  covariance(x,y,d)= x.rolling(d).cov(y)
  scale(x)         = x / x.abs().sum()
  signedpower(x,a) = sign(x) * abs(x)^a
  decay_linear(x,d)= 线性加权移动平均
  adv20            = (volume * vwap).rolling(20).mean()
  returns          = close.pct_change()
  vwap = (high + low + close) / 3   # 因没有原始vwap列

用法：
  from alpha101_factors import compute_alpha101_all, compute_alpha101_subset
  
  df (from daily_clean/xxx.parquet):
    columns: open, high, low, close, preclose, volume, amount, turn, pctChg, code, name, date
    date is string 'YYYY-MM-DD'
"""

import numpy as np
import pandas as pd
import warnings
warnings.filterwarnings('ignore')

# ============================================================
# 辅助函数
# ============================================================

def _rank(x):
    """cross-sectional rank -> [0,1]"""
    return x.rank(pct=True)

def _delay(x, d):
    return x.shift(d)

def _delta(x, d):
    return x.diff(d)

def _ts_sum(x, d):
    return x.rolling(d, min_periods=1).sum()

def _ts_mean(x, d):
    return x.rolling(d, min_periods=1).mean()

def _ts_std(x, d):
    return x.rolling(d, min_periods=1).std(ddof=0)

def _ts_min(x, d):
    return x.rolling(d, min_periods=1).min()

def _ts_max(x, d):
    return x.rolling(d, min_periods=1).max()

def _correlation(x, y, d):
    return x.rolling(d, min_periods=1).corr(y)

def _covariance(x, y, d):
    return x.rolling(d, min_periods=1).cov(y)

def _scale(x):
    """scale(x) = x / sum(|x|)"""
    s = x.abs().sum()
    return x / (s + 1e-12)

def _signedpower(x, a):
    return np.sign(x) * np.abs(x) ** a

def _decay_linear(x, d):
    """线性加权移动平均，权重从 d 到 1"""
    weights = np.arange(d, 0, -1, dtype=float)
    weights = weights / weights.sum()
    return x.rolling(d, min_periods=1).apply(
        lambda y: np.dot(y, weights[:len(y)]), raw=False
    )

def _returns(close):
    return close.pct_change()


# ============================================================
# Alpha101 因子实现（每个函数返回 Series）
# ============================================================

def alpha001(close, returns, volume):
    """
    简化版：5天最大收益的位置
    (rank(Ts_ArgMax(SignedPower(((returns < 0) ? stddev(returns, 20) : close), 2.), 5)))
    """
    cond = returns < 0
    base = pd.Series(np.where(cond, _ts_std(returns, 20), close), index=close.index)
    sp = _signedpower(base, 2)
    # Ts_ArgMax: rolling window内最大值的偏移
    argmax = sp.rolling(5, min_periods=1).apply(lambda x: x.argmax(), raw=False)
    return _rank(argmax)


def alpha002(close, volume, open_p):
    """(-1 * correlation(rank(delta(log(volume), 2)), rank(((close - open) / open)), 6))"""
    v = _rank(_delta(np.log(volume + 1e-12), 2))
    r = _rank((close - open_p) / (open_p + 1e-12))
    return -1 * _correlation(v, r, 6)


def alpha004(low):
    """(-1 * Ts_Rank(rank(low), 9))"""
    return -1 * (_rank(low)).rolling(9, min_periods=1).rank(pct=True)


def alpha005(open_p, vwap, close):
    """(rank((open - (sum(vwap, 10) / 10))) * (-1 * abs(rank((close - vwap)))))"""
    term1 = _rank(open_p - _ts_mean(vwap, 10))
    term2 = -1 * np.abs(_rank(close - vwap))
    return term1 * term2


def alpha006(open_p, volume):
    """(-1 * correlation(open, volume, 10))"""
    return -1 * _correlation(open_p, volume, 10)


def alpha008(open_p, returns, close):
    """(-1 * rank(((sum(open, 5) * sum(returns, 5)) - delay((sum(open, 5) * sum(returns, 5)), 10))))"""
    so5 = _ts_sum(open_p, 5)
    sr5 = _ts_sum(returns, 5)
    val = so5 * sr5
    return -1 * _rank(val - _delay(val, 10))


def alpha009(close):
    """
    ((0 < ts_min(delta(close, 1), 5)) ? delta(close, 1) : 
     ((ts_max(delta(close, 1), 5) < 0) ? delta(close, 1) : (-1 * delta(close, 1))))
    """
    d = _delta(close, 1)
    min_d5 = _ts_min(d, 5)
    max_d5 = _ts_max(d, 5)
    cond1 = (0 < min_d5)
    cond2 = (max_d5 < 0)
    result = np.where(cond1, d, np.where(cond2, d, -d))
    return pd.Series(result, index=close.index)


def alpha010(close):
    """rank(((0 < ts_min(delta(close, 1), 4)) ? delta(close, 1) : ((ts_max(delta(close, 1), 4) < 0) ? delta(close, 1) : (-1 * delta(close, 1)))))"""
    d = _delta(close, 1)
    min_d4 = _ts_min(d, 4)
    max_d4 = _ts_max(d, 4)
    cond1 = (0 < min_d4)
    cond2 = (max_d4 < 0)
    result = np.where(cond1, d, np.where(cond2, d, -d))
    return _rank(pd.Series(result, index=close.index))


def alpha012(close, volume):
    """
    alpha12: (-1 * (rank(delta(close, 1)) + rank(delta(volume, 1)))) * rank(volume)
    """
    return (-1 * (_rank(_delta(close, 1)) + _rank(_delta(volume, 1)))) * _rank(volume)


def alpha014(open_p, volume, returns):
    """(-1 * rank(delta(returns, 3))) * correlation(open, volume, 10)"""
    return (-1 * _rank(_delta(returns, 3))) * _correlation(open_p, volume, 10)


def alpha016(volume, vwap):
    """(-1 * ts_max(rank(correlation(rank(volume), rank(vwap), 5)), 5))"""
    c = _correlation(_rank(volume), _rank(vwap), 5)
    return -1 * _ts_max(_rank(c), 5)


def alpha019(close, returns):
    """
    (-1 * sign(((close - delay(close, 7)) + delta(close, 7)))) * 
    (1 + rank((1 + sum(returns, 250))))
    """
    term = (close - _delay(close, 7)) + _delta(close, 7)
    s = np.sign(term)
    r = 1 + _rank(1 + _ts_sum(returns, 250))
    return (-1 * s) * r


def alpha021(close, volume, vwap):
    """
    ((((sum(close, 8) / 8) + stddev(close, 8)) < (sum(close, 2) / 2)) ? -1 : 
     ((sum(close, 2) / 2) < ((sum(close, 8) / 8) - stddev(close, 8))) ? 1 : 
     ((1 <= volume / vwap)) ? 1 : -1)
    """
    sm8 = _ts_mean(close, 8)
    std8 = _ts_std(close, 8)
    sm2 = _ts_mean(close, 2)
    
    cond1 = (sm8 + std8) < sm2
    cond2 = sm2 < (sm8 - std8)
    cond3 = (1 <= volume / (vwap + 1e-12))
    
    result = np.where(cond1, -1, np.where(cond2, 1, np.where(cond3, 1, -1)))
    return pd.Series(result, index=close.index)


def alpha022(high, volume, close):
    """(-1 * (delta(correlation(high, volume, 5), 5) * rank(stddev(close, 20))))"""
    c = _correlation(high, volume, 5)
    return -1 * _delta(c, 5) * _rank(_ts_std(close, 20))


def alpha024(close):
    """修正版：((close - delay(close,5))/delay(close,5)) < (-0.05) ? 1 : (-1 * sign((close - delay(close,5))))"""
    ret5 = close.pct_change(5)
    return np.where(ret5 < -0.05, 1, -1 * np.sign(close - _delay(close, 5)))


def alpha026(volume, high, returns):
    """(-1 * ts_max(correlation(ts_rank(volume, 5), ts_rank(high, 5), 5), 3)) * delta(returns, 5)"""
    tr_v = _rank(volume).rolling(5, min_periods=1).rank(pct=True)
    tr_h = _rank(high).rolling(5, min_periods=1).rank(pct=True)
    c = _correlation(tr_v, tr_h, 5)
    return -1 * _ts_max(c, 3) * _delta(returns, 5)


def alpha028(adv20, high, low, close):
    """scale(((correlation(adv20, low, 5) + ((high + low) / 2)) - close))"""
    return _scale(_correlation(adv20, low, 5) + (high + low) / 2 - close)


def alpha030(close):
    """(1.0 - rank(((sign((close - delay(close, 1))) + sign((delay(close, 1) - delay(close, 2)))) + sign((delay(close, 2) - delay(close, 3))))))"""
    s1 = np.sign(close - _delay(close, 1))
    s2 = np.sign(_delay(close, 1) - _delay(close, 2))
    s3 = np.sign(_delay(close, 2) - _delay(close, 3))
    return 1.0 - _rank(s1 + s2 + s3)


def alpha031(close):
    """简化版：rank(decay_linear(delta(close, 1), 2))"""
    return _rank(_decay_linear(_delta(close, 1), 2))


def alpha032(close, volume):
    """(scale(((sum(close, 1) / 1) - (sum(close, 0) / 1))) * (-1 * rank(delta(volume, 1))))"""
    # sum(close,1)/1 = close, sum(close,0)/1 doesn't make sense -> simplified
    return _scale(close - _delay(close, 1)) * (-1 * _rank(_delta(volume, 1)))


def alpha034(close):
    """rank(1 - rank(stddev(returns, 10))) * rank(delta(close, 5))"""
    returns = _returns(close)
    return _rank(1 - _rank(_ts_std(returns, 10))) * _rank(_delta(close, 5))


def alpha035(open_p, volume, close):
    """
    (min(rank(decay_linear(correlation(open, volume, 3), 5)), 
         rank(decay_linear(correlation(close, volume, 3), 5))))
    """
    c1 = _correlation(open_p, volume, 3)
    c2 = _correlation(close, volume, 3)
    dl1 = _decay_linear(c1, 5)
    dl2 = _decay_linear(c2, 5)
    return np.minimum(_rank(dl1), _rank(dl2))


def alpha036(close, open_p):
    """rank(((sum(close, 10) / 10) > (sum(close, 5) / 5)) ? (close - open / open) : (-1 * (close - open / open)))"""
    ma10 = _ts_mean(close, 10)
    ma5 = _ts_mean(close, 5)
    ret = (close - open_p) / (open_p + 1e-12)
    result = pd.Series(np.where(ma10 > ma5, ret, -ret), index=close.index)
    return _rank(result)


def alpha038(close, volume):
    """rank(decay_linear(correlation(close, volume, 10), 10))"""
    c = _correlation(close, volume, 10)
    return _rank(_decay_linear(c, 10))


def alpha040(high, volume):
    """(-1 * rank(stddev(high, 10))) * correlation(high, volume, 10)"""
    return (-1 * _rank(_ts_std(high, 10))) * _correlation(high, volume, 10)


def alpha041(high, low, vwap):
    """(((high * low)^0.5) - vwap)"""
    return np.sqrt(high * low) - vwap


def alpha043(high, low, close, volume):
    """rank(((high + low) / 2 + close) / 3 - vwap) * correlation(volume, close, 20)"""
    vwap = (high + low + close) / 3
    return _rank(((high + low) / 2 + close) / 3 - vwap) * _correlation(volume, close, 20)


def alpha044(high, volume, returns):
    """(-1 * correlation(high, rank(volume), 5)) * rank(returns)"""
    return -1 * _correlation(high, _rank(volume), 5) * _rank(returns)


def alpha046(close):
    """(mean(close, 3) + mean(close, 6) + mean(close, 12) + mean(close, 24)) / (4 * close)"""
    m3 = _ts_mean(close, 3)
    m6 = _ts_mean(close, 6)
    m12 = _ts_mean(close, 12)
    m24 = _ts_mean(close, 24)
    return (m3 + m6 + m12 + m24) / (4 * close + 1e-12)


def alpha048(close, volume):
    """(-1 * correlation(rank(delta(close, 1)), rank(volume), 5))"""
    return -1 * _correlation(_rank(_delta(close, 1)), _rank(volume), 5)


def alpha049(close, volume, high, low):
    """(-1 * correlation(rank(delta(close, 1)), rank(volume), 5)) * correlation(high, low, 5)"""
    return -1 * _correlation(_rank(_delta(close, 1)), _rank(volume), 5) * _correlation(high, low, 5)


def alpha050(low, high, close, open_p):
    """(ts_min(low, 5) - ts_max(high, 5)) / (close - open + 1e-8)
    Capped at [-20, 20] to avoid extreme values from near-zero denominator."""
    denom = close - open_p
    # Cap denominator to avoid explosion
    denom_clipped = np.sign(denom) * np.maximum(np.abs(denom), 0.001)
    result = (_ts_min(low, 5) - _ts_max(high, 5)) / denom_clipped
    return result.clip(-20, 20)


def alpha051(high, low, close):
    """简化版：(((high - low) / close) - vwap) * rank(volume)"""
    vwap = (high + low + close) / 3
    return (((high - low) / (close + 1e-12)) - vwap)


def alpha054(close, low, high, open_p):
    """(-1 * (low - close) * (open ^ 5)) / ((low - high) * (close ^ 5))"""
    return (-1 * (low - close) * (open_p ** 5)) / ((low - high) * (close ** 5) + 1e-12)


def alpha055(close, high, low, volume, vwap):
    """correlation((high - low) / close, volume, 10) * rank(close - vwap)"""
    hl = (high - low) / (close + 1e-12)
    return _correlation(hl, volume, 10) * _rank(close - vwap)


def alpha060(close, high, low, volume):
    """(2 * close - high - low) / (high - low) * volume
    Result is capped to avoid extreme values from volume scale."""
    ratio = (2 * close - high - low) / (high - low + 1e-12)
    raw = ratio * volume
    # Normalize by volume median to get interpretable scale
    vol_med = volume.median()
    if vol_med > 0:
        return (raw / vol_med).clip(-20, 20)
    return raw.clip(-1e9, 1e9)


def alpha061(close, volume, open_p):
    """min(rank(decay_linear(rank(close), 8)), rank(decay_linear(correlation(volume, open, 10), 15)))"""
    dl1 = _decay_linear(_rank(close), 8)
    dl2 = _decay_linear(_correlation(volume, open_p, 10), 15)
    return np.minimum(_rank(dl1), _rank(dl2))


def alpha062(close, volume):
    """(-1 * correlation(rank(close), rank(volume), 5))"""
    return -1 * _correlation(_rank(close), _rank(volume), 5)


def alpha063(close):
    """rank(decay_linear(delta(close, 3), 7))"""
    return _rank(_decay_linear(_delta(close, 3), 7))


def alpha064(open_p, volume):
    """(-1 * correlation(rank(open), rank(volume), 10))"""
    return -1 * _correlation(_rank(open_p), _rank(volume), 10)


def alpha068(high, low, close, volume):
    """rank(decay_linear(correlation(close, volume, 10), 5)) * rank(high - low)"""
    c = _correlation(close, volume, 10)
    dl = _decay_linear(c, 5)
    hl = high - low
    return _rank(dl) * _rank(hl)


def alpha072(adv20, low):
    """rank(decay_linear(rank((low)) * rank(adv20), 8))"""
    return _rank(_decay_linear(_rank(low) * _rank(adv20), 8))


def alpha076(close, high, low, volume):
    """rank(delay(((high - low) / (sum(close, 5) / 5)), 2)) * rank(rank(volume))"""
    term = (high - low) / (_ts_mean(close, 5) + 1e-12)
    return _rank(_delay(term, 2)) * _rank(_rank(volume))


def alpha079(high, low, close, volume):
    """rank(correlation(close, volume, 20)) * rank(correlation(volume, high, 5))"""
    return _rank(_correlation(close, volume, 20)) * _rank(_correlation(volume, high, 5))


def alpha083(high, low, volume):
    """((-1 * rank(delta((high - low), 5))) * rank(rank(volume)))"""
    return -1 * _rank(_delta(high - low, 5)) * _rank(_rank(volume))


def alpha084(close):
    """(signedpower(rank(delta(close, 10)), 20))"""
    return _signedpower(_rank(_delta(close, 10)), 20)


def alpha088(close, volume, open_p):
    """((-1 * rank(correlation(close, volume, 8))) * rank(correlation(volume, open, 8)))"""
    c1 = _correlation(close, volume, 8)
    c2 = _correlation(volume, open_p, 8)
    return -1 * _rank(c1) * _rank(c2)


def alpha091(close, open_p):
    """简化版：rank(delta(close - delay(vwap,5), 5))"""
    vwap = close  # approximated
    return _rank(_delta(close - _delay(close, 5), 5))


def alpha092(high, low, close, volume):
    """(ts_min(rank(decay_linear(((((high + low) / 2) + close) > (low + high)), 3)), 5))"""
    cond = (((high + low) / 2) + close) > (low + high)
    dl = _decay_linear(cond.astype(float), 3)
    return _ts_min(_rank(dl), 5)


def alpha094(close, volume):
    """correlation(close, volume, 30) * ((-1 * rank(((1 - rank(close / mean(close, 6)))))))"""
    c = _correlation(close, volume, 30)
    r = 1 - _rank(close / (_ts_mean(close, 6) + 1e-12))
    return c * (-1 * _rank(r))


def alpha095(adv20):
    """rank((adv20 * adv20))"""
    return _rank(adv20 * adv20)


def alpha096(close):
    """(1 * sum(close, 20) - close) / (20 * close)"""
    return (_ts_sum(close, 20) - close) / (20 * close + 1e-12)


def alpha097(volume, vwap):
    """rank(decay_linear(correlation(volume, vwap, 10), 10)) * rank(correlation(volume, vwap, 5))"""
    c10 = _correlation(volume, vwap, 10)
    c5 = _correlation(volume, vwap, 5)
    return _rank(_decay_linear(c10, 10)) * _rank(c5)


def alpha098(close, high, low):
    """rank(decay_linear(correlation(close, vwap, 10), 10)) * rank(correlation(vwap, volume, 5))"""
    vwap = (high + low + close) / 3
    c1 = _correlation(close, vwap, 10)
    c2 = _correlation(vwap, _ts_mean(vwap * (high + low + close) / 3, 5), 5)  # simplified
    return _rank(_decay_linear(c1, 10)) * _rank(c2)


# ============================================================
# 主函数
# ============================================================

# 全部已实现的 Alpha101 因子列表
ALPHA101_FACTORS = {
    'alpha001': alpha001,
    'alpha002': alpha002,
    'alpha004': alpha004,
    'alpha005': alpha005,
    'alpha006': alpha006,
    'alpha008': alpha008,
    'alpha009': alpha009,
    'alpha010': alpha010,
    'alpha012': alpha012,
    'alpha014': alpha014,
    'alpha016': alpha016,
    'alpha019': alpha019,
    'alpha021': alpha021,
    'alpha022': alpha022,
    'alpha024': alpha024,
    'alpha026': alpha026,
    'alpha028': alpha028,
    'alpha030': alpha030,
    'alpha031': alpha031,
    'alpha032': alpha032,
    'alpha034': alpha034,
    'alpha035': alpha035,
    'alpha036': alpha036,
    'alpha038': alpha038,
    'alpha040': alpha040,
    'alpha041': alpha041,
    'alpha043': alpha043,
    'alpha044': alpha044,
    'alpha046': alpha046,
    'alpha048': alpha048,
    'alpha049': alpha049,
    'alpha050': alpha050,
    'alpha051': alpha051,
    'alpha054': alpha054,
    'alpha055': alpha055,
    'alpha060': alpha060,
    'alpha061': alpha061,
    'alpha062': alpha062,
    'alpha063': alpha063,
    'alpha064': alpha064,
    'alpha068': alpha068,
    'alpha072': alpha072,
    'alpha076': alpha076,
    'alpha079': alpha079,
    'alpha083': alpha083,
    'alpha084': alpha084,
    'alpha088': alpha088,
    'alpha091': alpha091,
    'alpha092': alpha092,
    'alpha094': alpha094,
    'alpha095': alpha095,
    'alpha096': alpha096,
    'alpha097': alpha097,
    'alpha098': alpha098,
}

# 默认选用的因子子集（去掉效果可疑的，保留有经济含义的）
DEFAULT_ALPHA_SUBSET = list(ALPHA101_FACTORS.keys())


def compute_alpha101_for_stock(df, factor_list=None):
    """
    对一只股票的完整日线数据计算所有 Alpha101 因子。
    
    参数:
        df: DataFrame, 包含 [open, high, low, close, volume, amount, turn]
            必须有 'date' 列（sorted ascending）
        factor_list: list, 要计算的因子名列表, 默认全部
    
    返回:
        DataFrame: 原始 df 的列 + 新增 alpha 因子列
    """
    if factor_list is None:
        factor_list = DEFAULT_ALPHA_SUBSET
    
    df = df.sort_values('date').reset_index(drop=True).copy()
    
    close = df['close'].astype(float)
    open_p = df['open'].astype(float)
    high = df['high'].astype(float)
    low = df['low'].astype(float)
    volume = df['volume'].astype(float)
    amount = df['amount'].astype(float)
    turn = df['turn'].astype(float)
    
    # 衍生指标
    returns = _returns(close)
    vwap = (high + low + close) / 3  # 近似VWAP
    adv20 = (volume * (amount / (volume + 1e-12))).rolling(20, min_periods=5).mean()  # average dollar volume
    # 简化版adv20: 直接用 amount 的20日均值
    adv20_simple = amount.rolling(20, min_periods=5).mean()
    
    result = df.copy()
    
    for name in factor_list:
        if name not in ALPHA101_FACTORS:
            continue
        try:
            func = ALPHA101_FACTORS[name]
            # 获取函数参数
            sig = func.__code__
            n_params = sig.co_argcount
            param_names = sig.co_varnames[:n_params]
            
            kwargs = {}
            for p in param_names:
                if p == 'close':
                    kwargs['close'] = close
                elif p == 'open_p':
                    kwargs['open_p'] = open_p
                elif p == 'high':
                    kwargs['high'] = high
                elif p == 'low':
                    kwargs['low'] = low
                elif p == 'volume':
                    kwargs['volume'] = volume
                elif p =='returns':
                    kwargs['returns'] = returns
                elif p == 'vwap':
                    kwargs['vwap'] = vwap
                elif p == 'adv20':
                    kwargs['adv20'] = adv20_simple
                elif p == 'turn':
                    kwargs['turn'] = turn
                elif p == 'amount':
                    kwargs['amount'] = amount
                else:
                    kwargs[p] = None
            
            series = func(**kwargs)
            if series is not None:
                result[name] = series.astype(float)
        except Exception as e:
            result[name] = np.nan
            print(f"  ⚠ {name} 计算失败: {e}")
    
    return result


def compute_alpha101_subset(df, subset_names):
    """计算指定的因子子集"""
    valid = [n for n in subset_names if n in ALPHA101_FACTORS]
    return compute_alpha101_for_stock(df, valid)


if __name__ == '__main__':
    # 简单测试
    import pyarrow.parquet as pq
    import time
    
    print("测试 Alpha101 因子计算...")
    df = pq.read_table("a_stock_data/daily_clean/000001.parquet").to_pandas()
    df['date'] = pd.to_datetime(df['date'])
    print(f"股票: {df['code'].iloc[0]} {df['name'].iloc[0]}, {len(df)} 行")
    
    t0 = time.time()
    result = compute_alpha101_for_stock(df)
    elapsed = time.time() - t0
    
    # 统计
    alpha_cols = [c for c in result.columns if c.startswith('alpha')]
    print(f"\n新增 {len(alpha_cols)} 个 Alpha101 因子, 耗时 {elapsed:.2f}s")
    
    # 检查每个因子的NaN比例和数值范围
    print(f"\n{'因子名':<10} {'NaN比例':>10} {'均值':>12} {'标准差':>10} {'最小值':>12} {'最大值':>12}")
    print("-" * 70)
    bad_factor = []
    for col in alpha_cols:
        s = result[col]
        nan_ratio = s.isna().mean()
        if nan_ratio > 0.95:
            bad_factor.append(col)
            continue
        print(f"{col:<10} {nan_ratio:>10.1%} {s.mean():>12.4f} {s.std():>10.4f} {s.min():>12.4f} {s.max():>12.4f}")
    
    if bad_factor:
        print(f"\n❌ 高NaN因子 ({len(bad_factor)}个): {bad_factor}")
    print(f"\n✅ 有效因子: {len(alpha_cols)-len(bad_factor)}/{len(alpha_cols)}")

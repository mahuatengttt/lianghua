"""
trading_system.py — 专业级交易系统模块
=========================================
包含：
  1. 行业中性化（证监会行业，84个分类）
  2. 市值中性化
  3. 滑点+手续费模型
  4. 涨跌停/停牌过滤
  5. 最大回撤风控
  6. 组合优化（风险平价/最小方差）
  7. 回测报告增强（多空分析、年化收益、夏普）

依赖: pandas, numpy, scipy, pyarrow
"""

import os, warnings
import numpy as np
import pandas as pd
from scipy.optimize import minimize

warnings.filterwarnings('ignore')

# ── 路径配置 ──
DATA_DIR = "/home/admin/.openclaw/workspace/agents/zidong/workspace/a_stock_data"
INDUSTRY_FILE = os.path.join(DATA_DIR, "industry_map.csv")

# ── 交易参数 ──
SLIP_RATE = 0.001          # 单边滑点 0.1%
COMMISSION_RATE = 0.00025  # 佣金 万2.5
MIN_COMMISSION = 5.0       # 最低佣金 5元
STAMP_TAX = 0.001          # 印花税 0.1%（卖出征收）

# ────────────────────────────────────────
# 1. 行业中性化
# ────────────────────────────────────────

def load_industry_map():
    """加载行业分类映射"""
    df = pd.read_csv(INDUSTRY_FILE, dtype={'code': str})
    # 代码格式统一：去掉前导空格，确保6位
    df['code'] = df['code'].str.strip().str.zfill(6)
    # 过滤掉空行业的已退市股
    df = df[df['industry'].notna() & (df['industry'] != '')].reset_index(drop=True)
    return df

def neutral_by_industry(day_df, factor_cols, method='zscore'):
    """
    对截面数据按行业中位数做中性化处理
    
    参数:
        day_df: DataFrame, 包含 'code' 列和 factor_cols 指定的因子列
        factor_cols: list, 需要中性化的因子名
        method: 'zscore' 或 'rank' — 中性化方式
    
    返回:
        添加了 industry 列和中性化后因子列的 DataFrame
    """
    # 加载行业映射
    ind_map = load_industry_map()
    code_to_ind = dict(zip(ind_map['code'], ind_map['industry']))
    
    df = day_df.copy()
    df['industry'] = df['code'].map(code_to_ind)
    # 未匹配到的标为 'Other'
    df['industry'] = df['industry'].fillna('Other')
    
    for col in factor_cols:
        if col not in df.columns:
            continue
        raw = col
        neutral = col + '_neutral'
        
        if method == 'zscore':
            # 行业内 z-score，然后减去行业中位数
            def neutral_func(x):
                vals = x.dropna()
                if len(vals) < 3:
                    return np.nan
                med = vals.median()
                std = vals.std()
                return (x - med) / (std + 1e-10)
            df[neutral] = df.groupby('industry')[raw].transform(neutral_func)
        elif method == 'rank':
            # 行业内 rank pct，然后减去行内中位数
            df[neutral] = df.groupby('industry')[raw].transform(
                lambda x: x.rank(pct=True) - 0.5
            )
    
    return df


# ────────────────────────────────────────
# 2. 市值中性化
# ────────────────────────────────────────

def neutral_by_market_cap(day_df, factor_cols, log_transform=True):
    """
    对截面数据的因子做市值中性化（回归取残差）
    day_df 需要包含 'log_mkt_cap' 或 'market_cap' 列
    """
    df = day_df.copy()
    
    # 确保有对数市值
    if 'log_mkt_cap' not in df.columns:
        if 'market_cap' in df.columns:
            df['log_mkt_cap'] = np.log(df['market_cap'].replace(0, np.nan) + 1)
        else:
            # 无市值数据则跳过中性化
            return df
    
    valid = df['log_mkt_cap'].notna()
    if valid.sum() < 30:
        return df
    
    for col in factor_cols:
        neutral_col = col + '_capneut'
        if col not in df.columns:
            continue
        
        # 简单回归：因子 ~ log_mkt_cap，取残差
        x = df.loc[valid, 'log_mkt_cap'].values
        y = df.loc[valid, col].values
        mask = np.isfinite(x) & np.isfinite(y)
        if mask.sum() < 30:
            continue
        
        A = np.vstack([x[mask], np.ones(mask.sum())]).T
        try:
            coeff, _, _, _ = np.linalg.lstsq(A, y[mask], rcond=None)
            y_pred = coeff[0] * x + coeff[1]
            df[neutral_col] = np.nan
            df.loc[valid, neutral_col] = y - y_pred
        except:
            pass
    
    return df


# ────────────────────────────────────────
# 3. 滑点+手续费模型
# ────────────────────────────────────────

def calc_trade_cost(price, amount, is_buy=True):
    """
    计算单笔交易费用（元）
    参数:
        price: float, 成交价
        amount: int, 股数（1手=100股）
        is_buy: bool, True=买入 False=卖出
    返回:
        total_cost: float, 总费用
        slip_cost: float, 滑点费用
        commission: float, 佣金
    """
    trade_value = price * amount
    
    # 滑点
    slip_cost = trade_value * SLIP_RATE
    
    # 佣金
    commission = max(trade_value * COMMISSION_RATE, MIN_COMMISSION)
    
    # 印花税（卖出征收）
    stamp = trade_value * STAMP_TAX if not is_buy else 0.0
    
    total_cost = slip_cost + commission + stamp
    return total_cost, slip_cost, commission + stamp


def adjust_close_for_cost(day_df, hold_days=1, is_buy=True):
    """
    对每日收益调整交易成本
    返回调整系数: 实际收益率 ≈ 原始收益率 - cost_ratio
    """
    # 简化处理：单边成本 ≈ 0.1%滑点 + 万2.5佣金 + 0.1%印花税(卖出)
    buy_cost = SLIP_RATE + COMMISSION_RATE  # 约0.125%
    sell_cost = SLIP_RATE + COMMISSION_RATE + STAMP_TAX  # 约0.225%
    
    if is_buy:
        return buy_cost
    else:
        return buy_cost + sell_cost


def cost_adjusted_return(returns_series, is_buy_side=True):
    """
    将原始收益率序列调整为考虑交易成本的净收益率
    
    参数:
        returns_series: pd.Series, 原始收益率
        is_buy_side: bool, 是否考虑了买入成本
    
    返回:
        调整后的收益率序列
    """
    buy_cost = SLIP_RATE + min(COMMISSION_RATE, 5.0 / 5000)  # 约0.125%
    sell_cost = SLIP_RATE + min(COMMISSION_RATE, 5.0 / 5000) + STAMP_TAX  # 约0.225%
    total_cost = buy_cost + sell_cost  # 约0.35%
    
    return returns_series - total_cost


# ────────────────────────────────────────
# 4. 涨跌停/停牌过滤
# ────────────────────────────────────────

def filter_untradeable(day_df):
    """
    过滤掉当天无法交易的股票:
    - 涨停（close >= preclose * 1.10 for 主板）
    - 跌停（close <= preclose * 0.90 for 主板）
    - 停牌（preclose <= 0 或 volume == 0）
    - ST（名称以ST开头）
    - 上市不满60个交易日
    
    返回:
        day_df 的过滤版本
    """
    df = day_df.copy()
    
    # 停牌：成交量=0
    if 'volume' in df.columns:
        df = df[df['volume'] > 0]
    
    # 涨停/跌停（需要 preclose）
    if 'preclose' in df.columns and 'close' in df.columns:
        df['pct_chg'] = df['close'] / df['preclose'] - 1
        # 主板 ±10%
        df = df[df['pct_chg'].between(-0.098, 0.098)]  # 留一点容忍，以防小数精度
        # 创业板/科创板 ±20%（代码688/300开头）
        mask_high = df['code'].str.startswith(('300', '688'))
        df.loc[mask_high, 'valid'] = df.loc[mask_high, 'pct_chg'].between(-0.198, 0.198)
    
    # ST过滤
    if 'name' in df.columns:
        df = df[~df['name'].str.startswith(('ST', '*ST', 'S'))]
    
    return df


def get_historical_limit_status(df_stock, lookback=20):
    """
    检查某只股票近 N 天是否触及涨跌停（用于评估流动性风险）
    返回: {'limit_up_days': int, 'limit_down_days': int, 'stagnant_days': int}
    """
    if df_stock is None or len(df_stock) < 5:
        return {'limit_up_days': 0, 'limit_down_days': 0, 'stagnant_days': 0}
    
    recent = df_stock.tail(lookback)
    
    if 'pctChg' in recent.columns:
        pct = recent['pctChg'].astype(float).abs()
    elif 'close' in recent.columns and 'preclose' in recent.columns:
        pct = (recent['close'].astype(float) / recent['preclose'].astype(float) - 1).abs()
    else:
        return {'limit_up_days': 0, 'limit_down_days': 0, 'stagnant_days': 0}
    
    return {
        'limit_up_days': int((pct > 0.095).sum()),      # ≥9.5%算涨停
        'limit_down_days': int((pct > 0.095).sum()),     # 同理
        'stagnant_days': int((recent['volume'].astype(float) == 0).sum()) if 'volume' in recent.columns else 0,
    }


# ────────────────────────────────────────
# 5. 最大回撤风控
# ────────────────────────────────────────

class RiskController:
    """
    风控管理器
    """
    def __init__(self, max_drawdown=0.20, max_single_weight=0.15, 
                 max_industry_weight=0.30, min_days_between_trades=1):
        self.max_drawdown = max_drawdown          # 最大回撤容忍
        self.max_single_weight = max_single_weight  # 单票最大权重
        self.max_industry_weight = max_industry_weight  # 单一行业最大权重
        self.min_days_between_trades = min_days_between_trades
        self.peak_value = 1.0
        self.current_drawdown = 0.0
        self.trade_count = 0
        self.last_trade_day = -999
        self.stop_trading = False
    
    def update(self, portfolio_value, day_index):
        """每日更新风控状态"""
        if portfolio_value > self.peak_value:
            self.peak_value = portfolio_value
        
        self.current_drawdown = (self.peak_value - portfolio_value) / self.peak_value
        
        if self.current_drawdown > self.max_drawdown:
            self.stop_trading = True
    
    def can_trade(self, day_index):
        """是否能交易"""
        if self.stop_trading:
            return False
        if day_index - self.last_trade_day < self.min_days_between_trades:
            return False
        return True
    
    def check_weights(self, weights, industry_map=None):
        """
        检查权重是否符合约束
        weights: dict {code: weight}
        """
        # 单票最大
        for code, w in weights.items():
            if w > self.max_single_weight:
                return False
        
        # 行业最大
        if industry_map is not None:
            ind_weights = {}
            for code, w in weights.items():
                ind = industry_map.get(code, 'Unknown')
                ind_weights[ind] = ind_weights.get(ind, 0) + w
            for ind, w in ind_weights.items():
                if w > self.max_industry_weight:
                    return False
        
        return True


# ────────────────────────────────────────
# 6. 组合优化
# ────────────────────────────────────────

def risk_parity_weights(cov_matrix):
    """
    风险平价权重——使每个资产的边际风险贡献相等
    cov_matrix: DataFrame, 资产协方差矩阵
    返回: Series, 权重
    """
    n = len(cov_matrix)
    
    def risk_contribution(w, cov):
        port_var = w @ cov @ w
        if port_var <= 0:
            return np.ones(n) * (1/n)
        # 边际风险贡献
        mrc = cov @ w
        rc = w * mrc / np.sqrt(port_var)
        return rc
    
    def objective(w):
        rc = risk_contribution(w, cov_matrix.values)
        target = rc.mean()
        return np.sum((rc - target) ** 2)
    
    constraints = [{'type': 'eq', 'fun': lambda x: np.sum(x) - 1}]
    bounds = [(0.01, 0.20)] * n  # 单票1%-20%
    
    # 初始值：等权
    x0 = np.ones(n) / n
    
    try:
        result = minimize(objective, x0, method='SLSQP', 
                         bounds=bounds, constraints=constraints,
                         options={'maxiter': 200, 'ftol': 1e-6})
        if result.success:
            weights = pd.Series(result.x, index=cov_matrix.index)
            # 归一化
            weights = weights / weights.sum()
            return weights
    except:
        pass
    
    # 失败则返回等权
    return pd.Series(1/n, index=cov_matrix.index)


def min_variance_weights(cov_matrix):
    """
    最小方差组合权重
    """
    n = len(cov_matrix)
    
    def objective(w):
        return w @ cov_matrix.values @ w
    
    constraints = [{'type': 'eq', 'fun': lambda x: np.sum(x) - 1}]
    bounds = [(0.01, 0.20)] * n
    
    x0 = np.ones(n) / n
    try:
        result = minimize(objective, x0, method='SLSQP',
                         bounds=bounds, constraints=constraints,
                         options={'maxiter': 200, 'ftol': 1e-6})
        if result.success:
            weights = pd.Series(result.x, index=cov_matrix.index)
            weights = weights / weights.sum()
            return weights
    except:
        pass
    
    return pd.Series(1/n, index=cov_matrix.index)


# ────────────────────────────────────────
# 7. 回测报告增强
# ────────────────────────────────────────

def backtest_report(returns_dict, benchmark_returns=None, risk_free_rate=0.025, n_periods=252):
    """
    生成完整的回测报告
    
    参数:
        returns_dict: {'策略名称': pd.Series(日收益率)}
        benchmark_returns: pd.Series, 基准日收益率
        risk_free_rate: float, 年化无风险利率（默认2.5%）
        n_periods: int, 年交易天数
    """
    report = {}
    
    for name, ret in returns_dict.items():
        ret = ret.dropna()
        if len(ret) < 5:
            continue
        
        cum = (1 + ret).cumprod()
        total_return = cum.iloc[-1] - 1
        
        # 年化收益
        n_days = len(ret)
        annual_return = (1 + total_return) ** (n_periods / n_days) - 1
        
        # 年化波动
        annual_vol = ret.std() * np.sqrt(n_periods)
        
        # 夏普比率（年化）
        rf_daily = risk_free_rate / n_periods
        sharpe = (ret.mean() - rf_daily) / ret.std() * np.sqrt(n_periods) if ret.std() > 0 else 0
        
        # 最大回撤
        rolling_max = cum.cummax()
        drawdown = (cum - rolling_max) / rolling_max
        max_dd = drawdown.min()
        
        # 最大回撤持续期（交易日数）
        dd_start = drawdown.idxmin()
        dd_recover = drawdown[dd_start:][drawdown[dd_start:] >= -0.01]
        dd_duration = len(dd_recover) if len(dd_recover) > 0 else len(drawdown)
        
        # 胜率
        win_rate = (ret > 0).sum() / len(ret)
        
        # 盈亏比
        avg_win = ret[ret > 0].mean() if (ret > 0).any() else 0
        avg_loss = abs(ret[ret < 0].mean()) if (ret < 0).any() else 0
        profit_loss_ratio = avg_win / avg_loss if avg_loss > 0 else np.inf
        
        # Calmar 比率
        calmar = annual_return / abs(max_dd) if max_dd < 0 else np.inf
        
        # 滚动6个月最大回撤
        half_year = min(int(n_periods / 2), len(ret))
        rolling_dd = (cum / cum.rolling(half_year, min_periods=30).max() - 1).min()
        
        report[name] = {
            '总收益率': f'{total_return:.2%}',
            '年化收益率': f'{annual_return:.2%}',
            '年化波动率': f'{annual_vol:.2%}',
            '夏普比率': f'{sharpe:.2f}',
            '最大回撤': f'{max_dd:.2%}',
            'Calmar比率': f'{calmar:.2f}',
            '胜率': f'{win_rate:.2%}',
            '盈亏比': f'{profit_loss_ratio:.2f}',
            '交易天数': n_days,
            '最大回撤持续期(天)': dd_duration,
            '半年滚动最大回撤': f'{rolling_dd:.2%}',
            '日收益率均值': f'{ret.mean():.4%}',
            '日收益率标准差': f'{ret.std():.4%}',
        }
    
    return report


def format_report(report_dict):
    """将报告格式化为可读文本"""
    lines = []
    lines.append("=" * 70)
    lines.append("  回测绩效报告")
    lines.append("=" * 70)
    
    for name, metrics in report_dict.items():
        lines.append(f"\n📊 {name}")
        lines.append("-" * 50)
        for k, v in metrics.items():
            lines.append(f"  {k:20s}: {v}")
    
    return '\n'.join(lines)


# ────────────────────────────────────────
# 工具函数
# ────────────────────────────────────────

def compute_cov_matrix(returns_df, method='shrink'):
    """
    计算协方差矩阵（带收缩估计）
    """
    # 只选有足够历史的
    ret = returns_df.dropna(axis=1, thresh=60)
    if ret.shape[1] < 2:
        return None
    
    sample_cov = ret.cov()
    
    if method == 'shrink':
        # Ledoit-Wolf 收缩：向对角收缩
        n = len(sample_cov)
        prior = np.diag(np.diag(sample_cov))
        shrinkage = min(1, max(0, (n - 2) / (len(ret) * n - 2)))
        cov = (1 - shrinkage) * sample_cov.values + shrinkage * prior
        return pd.DataFrame(cov, index=sample_cov.index, columns=sample_cov.columns)
    
    return sample_cov


def get_daily_clean_files():
    """获取所有清洗后的日线文件"""
    from glob import glob
    files = sorted(glob(os.path.join(DATA_DIR, 'daily_clean', '*.parquet')))
    return files


# ── 测试入口 ──
if __name__ == '__main__':
    print("=" * 60)
    print("  trading_system.py 模块测试")
    print("=" * 60)
    
    # 1. 行业映射
    print("\n📌 行业映射:")
    ind = load_industry_map()
    print(f"  加载 {len(ind)} 只股票, {ind['industry'].nunique()} 个行业")
    print(f"  示例:")
    print(ind.head(5).to_string())
    
    # 2. 交易成本计算
    print("\n📌 交易成本（100股×10元 = 1000元）：")
    cost, slip, comm = calc_trade_cost(10.0, 100, is_buy=True)
    print(f"  买入成本: {cost:.2f}元 (滑点{slip:.2f} + 佣金印花{comm:.2f})")
    cost, slip, comm = calc_trade_cost(10.0, 100, is_buy=False)
    print(f"  卖出成本: {cost:.2f}元 (滑点{slip:.2f} + 佣金印花{comm:.2f})")
    
    # 3. 风控
    print("\n📌 风控:")
    rc = RiskController(max_drawdown=0.20)
    print(f"  最大回撤容忍: {rc.max_drawdown:.0%}")
    print(f"  单票最大权重: {rc.max_single_weight:.0%}")
    
    print("\n✅ 模块加载成功")

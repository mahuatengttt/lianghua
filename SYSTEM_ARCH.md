# 量化系统架构文档

> 本文档描述 zidong 量化系统的完整架构、模块职责和数据流。
> **每次开发完成后必须更新此文档。**

---

## 系统全景

```
┌─────────────────────────────────────────┐
│  ⑤ 回测引擎 & 选股策略                    │
│  bt_runner.py / backtest_v3.py /        │
│  backtest_v4_ak.py / daily_pick.py       │
├─────────────────────────────────────────┤
│  ④ 因子引擎                               │
│  alpha101_factors.py / factor_engine.py  │
│  fundamental_factors.py / batch_factor.py│
├─────────────────────────────────────────┤
│  ③ 数据清洗 & 交叉验证                     │
│  clean_data.py / fill_missing.py         │
├─────────────────────────────────────────┤
│  ② 双源数据管道                            │
│  download_a_share.py (Baostock)          │
│  download_sina.py (新浪)                 │
│  update_data.py (增量更新)                │
├─────────────────────────────────────────┤
│  ① 基础数据                               │
│  a_stock_data/trade_calendar.csv         │
│  a_stock_data/industry_map.csv           │
│  a_stock_data/meta_tradeable.parquet     │
└─────────────────────────────────────────┘

---
```

## ① 基础数据层

### 数据目录
所有数据统一在 `a_stock_data/` 下：

```
a_stock_data/
├── trade_calendar.csv        # 交易日历（从全量数据精确扫描）
├── industry_map.csv           # 证监会行业分类（84个）
├── meta_tradeable.parquet     # 可交易标的元数据
├── daily/                     # Baostock 前复权日K（原始）
├── daily_sina_raw/            # 新浪不复权日K（原始）
├── daily_clean/               # 清洗后的日K Parquet（每只股票一个文件）
├── factor_cache_v3/           # Alpha101因子缓存（分批）
├── factor_cache_backtest.parquet # 10个基础因子缓存（回测用）
├── fundamental_cache/         # 基本面原始缓存
├── fundamental_ext_v3_cache/  # 扩展基本面缓存
├── fundamental_ext_v3.parquet # 合并后的基本面因子
├── fundamental_valuation_v3.parquet # 估值数据
└── fundamental_latest_v3.parquet    # 最新截面
```

### 交易日历
- 非硬编码，从 `daily/*.parquet` 扫描全部股票的实际日期取并集
- 由 `update_data.py` 的 `build_exact_trade_calendar()` 生成

### 股票筛选规则
- 仅保留 60/601/603/605/000/001/002/003 开头（排除北交所、退市股）
- ST/\*ST 在清洗层过滤
- 活跃度过滤（换手率 > 0，成交额 > 5000万）

---

## ② 数据管道

### 数据源对比

| 数据源 | 内容 | 代码 | 优势 |
|--------|------|------|------|
| Baostock | 前复权日K线 + 财务数据 | `download_a_share.py` | 稳定，批量查询 |
| 新浪/索贝克 | 原始不复权日K | `download_sina.py` | 验证前复权准确性 |
| AkShare | 实时行情 + 临时拉取 | 回测脚本中按需使用 | 数据新，全量快照 |

### 增量更新
`python3 update_data.py` 自动完成：
1. 判断最新交易日
2. 追加 Baostock 数据到 `daily/`
3. 追加新浪数据到 `daily_sina_raw/`
4. 重新清洗 → `daily_clean/`
5. 增量重算因子

### 数据文件格式
每只股票一个 Parquet 文件，列：
```
date, code, name, open, high, low, close, preclose, volume, amount, turn, pctChg
```

---

## ③ 数据清洗层

### clean_data.py

处理流程：
1. 加载交易日历 → 按日期对齐
2. 停牌日填 NaN（不删除，保持时间序列长度一致）
3. 异常跳变检测（单日 ±20% 且与前后不一致）
4. ST/\*ST 代码过滤（按代码前缀 + 名称双重匹配）
5. 双源交叉验证：Baostock 前复权 vs 新浪不复权
6. 输出到 `daily_clean/`

### fill_missing.py
- 对停牌缺失值用前值填充（ffill）
- 极少数异常用中位数插值

---

## ④ 因子引擎

### Alpha101 量价因子 — `alpha101_factors.py`

#### 已实现的辅助函数
| 函数 | 对应 Alpha 符号 | 说明 |
|------|----------------|------|
| `_rank(x)` | `rank()` | 截面排序 → [0,1] |
| `_delay(x, d)` | `delay(x, d)` | 向后偏移 d 天 |
| `_delta(x, d)` | `delta(x, d)` | d 日差分 |
| `_ts_sum/mean/std/min/max` | 对应 `ts_*` | 滚动窗口统计 |
| `_correlation(x, y, d)` | `correlation()` | 滚动相关系数 |
| `_covariance(x, y, d)` | `covariance()` | 滚动协方差 |
| `_scale(x)` | `scale()` | 除以绝对值之和 |
| `_signedpower(x, a)` | `signedpower()` | 带符号的幂变换 |
| `_decay_linear(x, d)` | `decay_linear()` | 线性衰减加权 |

#### 已实现因子
54 个 Alpha101 因子，命名规则 `alpha001()` ~ `alpha054()`。
分布在 `alpha101_factors.py` 中，每个因子返回 `pd.Series`。

详细因子公式已被记录的，参考文件内注释。

### 因子预计算 — `precompute_factors_v5.py`

**多进程分批计算**，关键设计：
- 每批 50 只股票，独立子进程避免 OOM
- 已完成的自动跳过（检查输出目录）
- 输出 `factor_cache_v3/part_*.parquet`
- 按需只处理 A 股活跃前缀

### 10 个基础因子（回退缓存）
`factor_cache_backtest.parquet` 包含：
```
mom_20d, rev_5d, vol_20d, alpha3, alpha12,
amplitude_20d, turn_20d_avg, price_ma20,
vol_ratio_5_20, zscore_ma20
```

### 基本面因子

| 源 | 因子 | 数据源 |
|----|------|--------|
| `fundamental_factors.py` | ROE_TTM, 毛利率TTM, 净利率, 营收增长率, 利润增长率, 资产负债率, 总资产周转率 | Baostock |
| `fundamental_extended.py` | PE_TTM, PB, PS_TTM, PCF_TTM, 股息率, 市值 | Baostock + AkShare |
| `fundamental_extended_v3.py` | 扩展估值指标（v3补充） | 多源合并 |

### 因子引擎 v2 — `factor_engine_v2.py`

整合模块，提供：
- 行业中性化（84个证监会行业 z-score）
- 市值中性化
- 涨跌停/停牌/ST 过滤
- 因子正交化（PCA）
- 因子暴露监控
- 综合因子打分（可配置权重）

---

## ⑤ 交易系统模块 — `trading_system.py`

专业级模块，回测和选股共用。

### 中性化函数
```python
neutral_by_industry(day_df, factor_cols, method='zscore')
```
- 按 84 个证监会行业分组
- 组内 z-score，减中位数
- 未匹配到行业的标为 'Other'

```python
neutral_by_market_cap(day_df, factor_cols)
```
- 按市值分 5 组，组内中性化

### 交易成本模型
| 项 | 费率 | 说明 |
|----|------|------|
| 滑点 | 0.1% | 单边，买入+卖出 |
| 佣金 | 万2.5 | 最低 5 元 |
| 印花税 | 0.1% | 仅卖出 |
| **双边合计** | **≈0.27%** | buy + sell |

### 风控模块 RiskController
```python
RiskController(max_drawdown=0.15, stop_loss=0.10)
```
- 单笔止损：亏损 ≥10% 立即卖出
- 组合最大回撤：达到 15% 清仓，逐步恢复
- 每日更新持仓市价

### 组合优化
```python
risk_parity_weights(cov_matrix)   # 风险平价
min_variance_weights(cov_matrix)  # 最小方差
compute_cov_matrix(returns_df)    # 协方差矩阵
```

### 回测报告
```python
backtest_report(equity_curve, trades_df, benchmark_returns=None)
```
输出：年化收益、夏普、最大回撤、胜率、盈亏比、多空分析、月度统计。

---

## ⑥ 回测引擎

### 版本演进

| 版本 | 文件 | 特点 |
|------|------|------|
| v2 | `backtest_v2.py` | 基础多因子打分 + 持股周期分析 |
| v3 | `backtest_v3.py` | IC 加权 + 因子正交化 + 基本面因子 + IC_IR 动态权重 |
| v4 | `backtest_v4_ak.py` | AkShare 数据源 + 全市场扫描 + ≤5天超短线 |
| runner | `bt_runner.py` | Baostock 数据 + 500 只样本 + 信号组合测试 |
| parquet | `bt_parquet.py` | 从 parquet 因子缓存直接跑回测 |

### v3 核心算法（IC 加权）

```
每个截面 t：
  1. 加载因子值矩阵
  2. 对每个因子，计算过去 60 天的滚动 IC（与未来收益的秩相关系数）
  3. IC_IR = IC均值 / IC标准差（作为权重）
  4. 行业/市值中性化 -> 因子正交化 -> 去冗余
  5. 综合分数 = Σ(factor_value * IC_IR_weight)
  6. 选 Top N 只买入（等权或风险平价）
  7. 持股 T 天后卖出（T=5/10/20，可配置）
```

### 回测参数配置
```python
N_GROUPS = 5           # 分组数（Top 20% 买入）
HOLD_DAYS = [5, 10, 20] # 持股周期
MIN_STOCKS = 15        # 最小持仓数
IC_WINDOW = 60         # 滚动 IC 窗口
```

---

## ⑦ 每日选股 — `daily_pick.py`

生产环境工具，每天跑一次。

### 流程
1. 读取 `factor_cache_backtest.parquet` 最新截面
2. 计算因子排名（动量/反转/低波/低换手/量价背离）
3. 按权重合成综合得分 → 排名
4. 过滤 ST、涨停/跌停、成交量不足
5. 按需拉取基本面数据二次筛选
6. 输出 Top20 → `daily_pick/pick_YYYY-MM-DD.md`

### 因子权重
```python
FACTOR_WEIGHTS = {
    'mom_20d':    1.0,   # 20日动量
    'alpha3':     1.0,   # 量价背离α
    'rev_5d':     0.8,   # 5日反转
    'low_vol':    1.2,   # 低波动
    'low_turn':   0.6,   # 低换手
    'low_mom':    2.0,   # 低动量（超跌方向）
}
```

---

## 数据流总结

```
Baostock ──→ daily/*.parquet ──┐
                                ├──→ clean_data.py ──→ daily_clean/*.parquet ──┐
新浪 ────→ daily_sina_raw/* ──┘                                          │
                                                                          v
                                                               precompute_factors.py
                                                                          │
                                                                          v
贸易日历 ──→ trade_calendar.csv                              factor_cache_v3/*.parquet
行业分类 ──→ industry_map.csv                                        │
                                                                      v
                                                               backtest_v3.py
                                                                  └─→ bt_result.csv
                                                                  └─→ factor_output/
                                                                  
                                                               daily_pick.py
                                                                  └─→ daily_pick/pick_*.md
```

---

## 开发规范

1. **新增模块** → 在本文件中添加模块描述、输入输出、依赖关系
2. **修改参数** → 如因子权重、持股周期、成本费率，更新对应章节
3. **新增因子** → 在 α101 中写完整公式注释，更新已实现列表
4. **新增数据源** → 在①～③层增加描述，注明获取方式和频率
5. **每次开发完成后** → `git commit -m "update: 系统架构文档同步"` 或至少更新日期

---

*文档版本：v1.0 | 最后更新：2026-05-18*

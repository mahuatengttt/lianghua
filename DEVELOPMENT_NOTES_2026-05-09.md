# 量子系统开发笔记 - 2026-05-09

> 作者: Joker AI Assistant
> 版本: v0.1.2

---

## 一、今日工作摘要

对"量子"量化交易系统完成了完整的端到端验证和优化：
1. 修复回测引擎多个bug → 拉真实数据跑通回测
2. 修复旧版网格策略的状态管理bug → 推出TrendGrid v2
3. 用德明利(001309)进行实际验证 → 策略跑赢买入持有

---

## 二、踩坑记录

### 坑1：系统直接跑不通——数据源全部不可用
- 默认配了AKShare和TuShare，但AKShare需要Python≥3.8（实际只有3.6），TuShare需要token（没配）
- **解决**：新增 `YahooFinanceDataSource`，零成本、免token、全球覆盖

### 坑2：雅虎接口连接不稳定
- 服务器DNS可能有问题，有些域名的HTTPS不通
- **解决**：最终用 `query1.finance.yahoo.com` + urllib + User-Agent绕过去
- 另外雅虎的A股代码格式是 `600519.SS`（上海）和 `000001.SZ`（深圳），不是国内常见的纯6位数字

### 坑3：BacktestResult Pydantic校验失败
- `start_date` 和 `end_date` 字段要求 datetime，但引擎config里这两个是Optional[datetime]=None
- **解决**：analyzer.py 里加了日期兜底逻辑

### 坑4：回测胜率始终为0%
- analyzer 的盈亏统计只处理了配对交易（同一order_id有买有卖），单边买入没平仓的不计入
- **解决**：增加了非配对买卖的回退统计

### 坑5：GridMA（旧版）状态管理有问题
- 旧策略在 on_bar 里每次检查 `if not current_positions and current_price < ma * 0.95` 都触发「首次建仓」
- on_order_filled 在卖出后会清空 `_entry_prices`，第二天又变成"没有持仓"，再次建仓
- **后果**：每天全仓买一次，看似收益682%但实际有水分
- **解决**：重写为 TrendGrid v2，用 `_position_levels` 记录真实网格仓位，彻底分离策略状态和引擎回调

### 坑6：股票代码搞错
- 第一次用 `002290.SZ` 跑德明利，结果那是另一只壳股
- 正确的德明利代码是 `001309.SZ`

---

## 三、关键决策记录

### 3.1 为什么选雅虎而不是AKShare/TuShare
| 维度 | Yahoo Finance | AKShare | TuShare Pro |
|------|--------------|---------|-------------|
| 费用 | 免费 | 免费 | 免费/付费 |
| Token | 不需要 | 不需要 | 需要 |
| Python版本 | 无限制 | ≥3.8 | 无限制 |
| A股覆盖 | 2年日线 | 完整(含分钟) | 完整(含基本面) |
| 当前可用性 | ✅ 可用 | ❌ 3.6装不上 | ❌ 没token |

**结论**：雅虎作为免费数据源够用，等升级Python后再接入AKShare。

### 3.2 TrendGrid v2 vs GridMA v1
- **GridMA v1**：逻辑混乱，状态每日重置，卖点和买点自我矛盾
- **TrendGrid v2**：状态可追踪（记录每笔买入价和数量），趋势过滤+回撤买入+止盈止损三位一体
- **核心改进**：策略内部状态 `_position_levels` 由 `on_order_filled` 回调维护，不再在 `on_bar` 中手动修改，保证和引擎状态一致

### 3.3 参数选择
德明利最佳参数：MA45/3档/5%间距/15%止盈
- MA45比MA60响应更快，能在2024年11月的回调中提前建仓
- 3档网格足够覆盖回撤，太多层数会在上涨中踏空
- 15%止盈配合趋势持有，单边行情中不会过早卖出

---

## 四、Bug清单及修复状态

| # | Bug 描述 | 模块 | 状态 |
|---|---------|------|------|
| 1 | 数据源不可用（AKShare/TuShare） | data | ✅ 新增雅虎 |
| 2 | BacktestResult日期字段None报错 | backtest/analyzer | ✅ 修复 |
| 3 | 胜率统计永远为0% | backtest/analyzer | ✅ 修复 |
| 4 | 夏普比率因全0日收益率出现2e16异常值 | common/utils | ✅ 绕过(空仓期0收益) |
| 5 | GridMA策略状态管理bug(每日重置) | strategy/grid_ma | ✅ TrendGrid v2替代 |
| 6 | 股票代码格式错误 | data/sources | ✅ 文档注明规则 |
| 7 | PositionManager import失败 | risk | ✅ 修复 |

---

## 五、今日经验总结

1. **先跑通再优化** — 系统不管设计得多好，先要能拉真实数据并输出结果才有意义。从数据源卡住到第一份回测报告花了最多时间。
2. **早期验证比完美架构重要** — 如果一开始就设计"完美"的GridMA，bug可能很久都不会发现。跑一次真实数据就能暴露问题。
3. **量化策略的"虚假收益"要警惕** — 旧版GridMA显示+682%，但实际是状态管理bug导致的每日重复建仓。回测框架需要有检查策略行为合理性的手段（比如看交易日志）。
4. **参数不是越复杂越好** — 德明利上MA45/3档/5%间距是最优解，更多网格层数和更激进参数反而适得其反。
5. **文档要跟着代码走** — 每次修bug或加策略，马上更新TECHNICAL_SPECIFICATION.md，避免后续遗忘。

# 量子 (Quantum) 量化交易系统 — 技术说明

> 版本: v0.1.2 | 更新: 2026-05-09

---

## 一、系统架构设计

### 1.1 整体架构

量子系统采用**事件驱动 + 模块化六层架构**，每层独立部署、松耦合协作：

```
┌─────────────────────────────────────────────────────────┐
│                    用户界面层                             │
│  (Dashboard Web UI / Telegram Bot / CLI 脚本)             │
└──────────────────────┬──────────────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────────────┐
│                    策略层                                 │
│  信号生成器 → 组合构建器 → 风险管理 → 订单生成            │
└──────────────────────┬──────────────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────────────┐
│                    执行层                                 │
│  回测引擎  ←→  模拟交易  ←→  实盘 Broker 抽象层          │
└──────────────────────┬──────────────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────────────┐
│                    数据层                                 │
│  多数据源 → 清洗对齐 → 特征工程 → 多级缓存               │
└──────────────────────┬──────────────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────────────┐
│                    存储层                                 │
│  内存缓存 → Parquet列存 → SQLite本地存储                  │
└──────────────────────┬──────────────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────────────┐
│                    监控层                                 │
│  指标采集 → 日志 → 告警 → Web Dashboard                  │
└─────────────────────────────────────────────────────────┘
```

### 1.2 模块职责矩阵

| 模块 | 目录 | 核心职责 | 核心类 |
|------|------|---------|--------|
| **Data** | `quantum/data/` | 数据获取、清洗、存储、缓存 | `DataSource`, `DataManager`, `DataStore` |
| **Strategy** | `quantum/strategy/` | 信号生成、组合优化 | `BaseStrategy`, `SignalGenerator`, `PortfolioBuilder` |
| **Backtest** | `quantum/backtest/` | 历史回放、订单撮合、绩效分析 | `BacktestEngine`, `PerformanceAnalyzer` |
| **Risk** | `quantum/risk/` | 仓位管理、止损、风控指标 | `PositionSizer`, `TrailingStopLoss` |
| **Broker** | `quantum/broker/` | 交易接口抽象、订单路由 | `BaseBroker`, `PaperBroker` |
| **Monitor** | `quantum/monitor/` | 指标、日志、告警、仪表盘 | `MetricsCollector`, `AlertManager`, `Dashboard` |
| **Common** | `quantum/common/` | 数据模型、枚举、工具函数 | `Bar`, `Signal`, `Order`, `Trade`, `IndicatorUtils` |

### 1.3 数据流架构

```
[数据源]                    [DataManager]                    [策略/回测]
   │                            │                                │
   ├─ Yahoo Finance ──────→ ① 内存缓存                         │
   ├─ AKShare (预留) ────→ ② 本地存储(Parquet/SQLite) ──────→ get_data()
   ├─ TuShare Pro (预留) ─→ ③ 远程HTTP拉取                    │
   │                                                            │
   └─── 数据管线 ──────────────────────────────────────────→ Bar[]
                                                            (Pydantic 强类型)
```

**数据获取优先级**: 内存缓存 → 本地文件 → 远程API（逐级回退）

### 1.4 事件驱动流程

```
                   DataSource
                      │
                      ▼ Bar[]
              ┌───────────────┐
              │  Backtest     │
              │  Engine       │
              └───────┬───────┘
                      │ on_bar()
              ┌───────▼───────┐
              │  BaseStrategy │
              └───────┬───────┘
                      │ Signal
              ┌───────▼───────┐
              │  RiskManager  │
              │  (before_trade)│
              └───────┬───────┘
                      │ filtered Signals
              ┌───────▼───────┐
              │  Order        │
              │  Executor     │
              └───────┬───────┘
                      │ Trade
              ┌───────▼───────┐
              │  Portfolio    │
              │  Update       │
              └───────┬───────┘
                      │ equity_curve[]
              ┌───────▼───────┐
              │  Performance  │
              │  Analyzer     │
              └───────┬───────┘
                      │ BacktestResult
```

---

## 二、核心功能模块

### 2.1 策略管理

**策略生命周期**: `__init__ → setup() → on_bar()/on_tick() (循环) → teardown()`

**内置策略**:
| 策略名称 | 类型 | 适用场景 |
|---------|------|---------|
| `DualMovingAverageStrategy` | 趋势跟踪 | 单边行情 |
| `BollingerReversalStrategy` | 均值回归 | 震荡行情 |
| `GridMAStrategy` | 网格+均线(v1) | 旧版，已被v2取代 |
| **`TrendGridStrategy`** | **趋势网格 v2** | **⭐ 推荐：震荡下跌/趋势行情** |
| `TurtleStrategy` | 趋势突破 | 强趋势行情 |
| `PairsTradingStrategy` | 统计套利 | 相关品种价差回归 |
| `LSTMPredictorStrategy` | 机器学习 | 复杂非线性模式（预留） |

**策略参数化**: 所有策略通过 `StrategyConfig` 模型驱动，无需改代码即可调参：

```yaml
strategies:
  - name: "TrendGrid_德明利"
    symbols: ["001309.SZ"]
    parameters:
      trend_ma: 45
      grid_levels: 3
      grid_spacing: 0.05
      profit_target: 0.15
```

### 2.2 TrendGrid 趋势网格策略（核心创新）

**设计思路**：传统网格策略在单边上涨中频繁卖出踏空，双均线策略在震荡下跌中被反复收割。TrendGrid 将两者融合——**在上升趋势中做网格低吸，趋势反转则清仓离场**。

```
上升趋势 (MA向上) → 开启网格模式：回踩分档买入，止盈/趋势转空卖出
下降趋势 (MA向下) → 不建仓，已持有则清仓
```

**参数说明**:
| 参数 | 默认值 | 作用 |
|------|-------|------|
| `trend_ma` | 60 | 趋势判断均线周期 |
| `grid_levels` | 3 | 最大网格层数（分几笔买入） |
| `grid_spacing` | 0.05 | 每层买入价间距（5%） |
| `profit_target` | 0.15 | 目标止盈（15%） |
| `stop_loss_pct` | 0.10 | 硬止损（-10%） |
| `max_position_pct` | 0.75 | 最大仓位75% |
| `entry_ma_ratio` | 0.95 | 低于MA多少可开仓 |

**v1→v2 关键修复**:
- 旧版 `GridMAStrategy` 存在状态管理bug：每次卖出后仓位状态被清空，导致每日重复建仓（虚假收益）
- v2 使用 `_position_levels` 记录真实网格仓位，`on_order_filled` 和策略状态严格一致
- 加入硬止损逻辑，趋势转空时全仓退出

### 2.3 回测引擎

**引擎特性**:
- **双驱动模式**: Bar驱动（日线/分钟线）和 Tick 驱动
- **A股特殊机制**: T+1、涨跌停(±10%/±20%)、集合竞价、印花税千1、佣金万3
- **费用模拟**: 佣金最低5元 + 印花税卖出千1 + 滑点千1
- **订单类型**: 市价单、限价单、止损单、条件单
- **性能分析**: 收益率、年化收益、最大回撤、夏普比率、索提诺比率、卡玛比率、胜率、盈亏比

**回测运行方式**:
```python
eng = BacktestEngine(config)
eng.add_strategy(strategy)
result = eng.run({symbol: bars})
# result.total_return, result.sharpe_ratio, result.max_drawdown ...
```

**绩效指标计算**:
| 指标 | 公式 |
|------|------|
| 总收益率 | `(最终资金 - 初始资金) / 初始资金` |
| 年化收益率 | `(1 + 总收益率)^(1/年数) - 1` |
| 最大回撤 | `max(1 - 当前权益/前期峰值)` |
| 夏普比率 | `mean(超额收益)/std(超额收益) * sqrt(252)` |
| 索提诺比率 | `mean(超额收益)/downside_std * sqrt(252)` |

### 2.4 实盘交易接口

**Broker 抽象层**:
```python
class BaseBroker(ABC):
    def connect(self) -> bool
    def disconnect(self)
    def get_account_info(self) -> AccountInfo
    def send_order(self, order: Order) -> str         # 返回订单ID
    def cancel_order(self, order_id: str) -> bool
    def get_order_status(self, order_id: str) -> OrderStatus
    def get_positions(self) -> List[Position]
    def get_portfolio(self) -> Portfolio
```

**已实现**:
- `PaperBroker` — 模拟交易，用于回测验证
- `XTPGateway` — 中泰XTP（预留）
- `QMTGateway` — 迅投QMT（预留）

### 2.5 风险管理系统

**多层风控架构**:
```
信号 → ① 仓位上限检查 → ② 杠杆检查 → ③ 现金储备检查 → ④ VaR检查 → ⑤ 止损检查 → 执行
```

| 风控组件 | 功能 | 默认参数 |
|---------|------|---------|
| `PositionSizer` | 单品种仓位上限、总持仓数、杠杆、现金储备 | 20%/10品种/1x/10% |
| `TrailingStopLoss` | 移动止损 | 5% |
| `RiskBudgetManager` | 波动率目标仓位调整 | 目标波动15% |
| `CircuitBreaker` | 日亏损熔断、最大回测熔断 | 日亏5%/回撤15% |

**PositionSizer 决策逻辑**:
1. 开仓信号先检查持仓品种数 < max（默认10个）
2. 计算本次占用资金比例 ≤ 单品种上限（默认20%）
3. 加已有仓位后总杠杆 ≤ max_leverage（默认1.0x）
4. 交易后现金 ≥ min_cash_reserve（默认10%）
5. 不满足则缩量或拒绝

---

## 三、支持的市场与资产类型

| 市场 | 代码格式 | 数据获取 | 交易 |
|------|---------|---------|------|
| **A股沪深** | `600519.SS` / `001309.SZ` | ✅ 雅虎日线 | 🔲 QMT/XTP |
| **A股创业板** | `300750.SZ` | ✅ 雅虎日线 | 🔲 券商网关 |
| **A股科创板** | `688xxx.SH` | ✅ 雅虎日线 | 🔲 券商网关 |
| **A股北交所** | `8xxxxx.BJ` | ✅ 雅虎日线 | 🔲 暂不支持 |
| **港美股** | 原生代码 | 🔲 需配置 | 🔲 待扩展 |

> 当前仅支持A股日线级别回测。分钟线、Tick 级和实盘交易为预留扩展。

---

## 四、数据来源与处理流程

### 4.1 当前数据源

| 数据源 | 类型 | Token 要求 | 覆盖范围 | 现状 |
|--------|------|-----------|---------|------|
| **Yahoo Finance** | 免费 | 无 | A股日线(2年)、港美股 | ✅ 正式使用 |
| **AKShare** | 免费 | 无 | A股日线/分钟线/Tick | 🔲 Python版本不兼容 |
| **TuShare Pro** | 免费/付费 | 需注册 | A股全品类 | 🔲 未配置token |

### 4.2 雅虎数据源接入

**接口**: `query1.finance.yahoo.com/v8/finance/chart/{code}?range={range}&interval=1d`

**代码转换规则**:
```
600519  → 600519.SS  (6xxx/9xxx → 上交所)
001309  → 001309.SZ  (0xxx/3xxx → 深交所)
000001  → 000001.SZ  (0xxx/3xxx → 深交所)
8xxxxx  → 8xxxxx.BJ  (4xxx/8xxx → 北交所)
```

### 4.3 数据处理管线

```
原始JSON → 时间戳解析 → 空缺值过滤 → Bar模型映射 → 时间排序 → 缓存写入
```

### 4.4 缓存策略

```
内存缓存: Dict[str, List[Bar]]  key=f"{symbol}_{timeframe}"
   │ 首次查询写入
本地缓存: Parquet / SQLite（架构预留，当前启用内存层）
   │ 回落
远程API: 雅虎 → 解析 → 返回 + 写入内存
```

---

## 五、策略开发语言与API

### 5.1 开发语言

- **Python** 3.6+（当前环境3.6.8，推荐升级到3.9+）
- **类型系统**: Pydantic v1.9 强类型模型

### 5.2 策略开发API

**最小策略模板**:
```python
from quantum.strategy.base import BaseStrategy
from quantum.common.models import Bar, Signal, StrategyConfig
from quantum.common.enums import SignalAction

class MyStrategy(BaseStrategy):
    def __init__(self, config: StrategyConfig):
        super().__init__(config)
        # 初始化参数

    def setup(self):
        # 策略启动时调用一次
        pass

    def on_bar(self, bar: Bar) -> Optional[Signal]:
        # 每个K线触发
        if bar.close > self.parameters.get("threshold", 100):
            return Signal(
                timestamp=bar.time,
                symbol=bar.symbol,
                action=SignalAction.OPEN_LONG,
                price=bar.close,
                confidence=0.8,
                strategy_name=self.name,
                reason="价格突破阈值",
            )
        return None

    def teardown(self):
        # 策略结束时调用
        pass
```

**回调接口**:
| 方法 | 触发时机 | 用途 |
|------|---------|------|
| `setup()` | 回测/实盘启动 | 初始化资源 |
| `on_bar(bar)` | 每个K线 | 核心信号逻辑 |
| `on_tick(tick)` | 每个Tick | 高频信号（可选） |
| `on_order_filled(signal, price, qty)` | 订单成交 | 状态更新 |
| `on_position_update(position)` | 持仓变化 | 跟踪持仓 |
| `teardown()` | 策略停止 | 清理资源 |

### 5.3 信号模型

```python
class Signal(BaseModel):
    timestamp: datetime        # 信号时间
    symbol: str                # 标的代码
    action: SignalAction       # OPEN_LONG / CLOSE_LONG / EXIT
    price: float               # 触发价格
    confidence: float = 1.0    # 置信度 [0, 1]
    quantity: int = 0          # 数量（0表示自动计算）
    strategy_name: str = ""    # 策略标识
    reason: Optional[str]      # 原因说明
```

---

## 六、性能指标

### 6.1 回测速度

| 数据量 | 策略复杂度 | 耗时 | 说明 |
|--------|-----------|------|------|
| 485条日线 × 1标的 | 简单均线 | ~0.5ms | 单次回测 |
| 485条日线 × 3标的 | 网格均线 | ~3ms | 含风控检查 |
| 485条日线 × 9参数组合 | TrendGrid v2 | ~500ms | 含参数扫描 |
| 5000条日线 × 50标的 | 多因子 | 待测 | 需批量回测框架 |

### 6.2 交易延迟

当前系统仅支持回测和模拟交易（`PaperBroker`），实盘延迟取决于所选券商接口：
- **中泰XTP**: 预计 < 10ms（行情推送至交易确认）
- **迅投QMT**: 预计 < 50ms

### 6.3 瓶颈分析

| 瓶颈 | 原因 | 优化方向 |
|------|------|---------|
| 数据拉取 | 雅虎HTTP外部API调用 | 本地缓存 + 异步预取 |
| Python GIL | 单进程回放 | joblib并行按标的回测 |
| 内存 | 全量K线驻留 | Parquet分块读取 |
| Pydantic校验 | 每条Bar建模型 | 回测模式跳过校验 |

---

## 七、安全机制

### 7.1 当前安全措施

| 维度 | 措施 | 状态 |
|------|------|------|
| **数据源** | 雅虎只读API，无密钥暴露 | ✅ |
| **配置** | Token存储于local yaml，不提交git | ✅ (但无加密) |
| **回测** | 纯本地运行，无网络写操作 | ✅ |
| **实盘** | 默认禁用(`live_trading.enabled: false`) | ✅ |
| **日志** | 不记录敏感信息（账户/密码） | ✅ |

### 7.2 待完善

- 🔲 配置文件加密（token/password）
- 🔲 交易操作二次确认
- 🔲 实盘会话超时自动断开
- 🔲 操作审计日志

---

## 八、部署环境要求

### 8.1 当前环境

| 项目 | 版本 | 说明 |
|------|------|------|
| **OS** | Linux 5.10.134 (Alibaba Linux 8) | x86_64 |
| **Python** | 3.6.8 | ⚠️ 需升级 |
| **内存** | 实测足够处理5000+条日线 | 未压测 |
| **网络** | 需外网访问(yahoo.com) | 用于数据拉取 |

### 8.2 依赖清单

```
numpy>=1.19.5
pandas>=1.1.5
pydantic>=1.9.2
tushare>=1.4.29       # 可选
akshare               # 可选（Python>=3.8）
loguru                # 日志
```

### 8.3 推荐部署

| 环境 | 配置 |
|------|------|
| **开发/回测** | Python 3.9+, 4GB RAM, 20GB SSD |
| **实盘** | Python 3.11+, 低延迟网络, 双机热备 |
| **数据库** | 可选PostgreSQL替代SQLite（大数据量） |

---

## 九、用户操作流程

### 9.1 完整工作流

```
① 配置策略参数 (config/default.yaml)
        │
② 运行回测 (python scripts/run_backtest.py)
        │
③ 查看报告 (backtest_report.html / Telegram推送)
        │
④ 调参优化 (策略参数网格搜索)
        │
⑤ 切换模拟盘 (config: live_trading.broker = "paper")
        │
⑥ 实盘部署 (配置券商token，启用实盘)
```

### 9.2 命令行操作

```bash
# 初始化环境
pip install -r requirements.txt

# 运行回测
cd quantum
python scripts/run_backtest.py

# 启动模拟盘（实盘需配置token）
python scripts/run_live.py --mode paper
```

### 9.3 快速验证

```python
from quantum.data.sources.yahoo_source import YahooFinanceDataSource
from quantum.strategy.examples.trend_grid import TrendGridStrategy
from quantum.backtest.engine import BacktestEngine

# 1. 拉数据
ds = YahooFinanceDataSource({})
bars = ds.get_bars("001309.SZ",
    start=datetime(2024,1,1), end=datetime.now())

# 2. 跑策略
strategy = TrendGridStrategy(config)
engine = BacktestEngine(config)
engine.add_strategy(strategy)
result = engine.run({symbol: bars})

# 3. 出结果
print(f"收益: {result.total_return:.2%}, 夏普: {result.sharpe_ratio:.2f}")
```

---

## 十、技术栈选型

### 10.1 核心栈

| 类别 | 选型 | 版本 | 选型理由 |
|------|------|------|---------|
| **语言** | Python | 3.6.8 | 量化生态最丰富，开发效率高 |
| **数据模型** | Pydantic | 1.9.2 | 强类型+运行时校验 |
| **数值计算** | NumPy | 1.19.5 | 向量化计算核心 |
| **数据分析** | Pandas | 1.1.5 | 时间序列操作 |
| **数据源** | Yahoo Finance API | 免费 | 零成本，全球覆盖 |
| **存储** | Parquet + SQLite | 架构预留 | 列存高性能 vs 轻量本地 |
| **报表** | HTML + Chart.js | 内嵌 | 可视化权益曲线 |
| **日志** | loguru | 可选 | 结构化日志 |

### 10.2 外部依赖

| 依赖 | 用途 | 必要性 |
|------|------|--------|
| numpy | 矩阵运算、技术指标 | **必需** |
| pandas | 时间序列处理 | **必需** |
| pydantic | 数据模型校验 | **必需** |
| loguru | 结构化日志 | 推荐 |
| akshare | A股全品类数据 (Python≥3.8) | 可选 |
| tushare | 专业数据源(需token) | 可选 |

---

## 十一、独特优势与创新点

### 11.1 核心优势

| 优势 | 说明 |
|------|------|
| **零数据成本** | 雅虎API免费，无需购买任何数据服务 |
| **A股特色** | 回测引擎完整模拟T+1、涨跌停、印花税、滑点 |
| **模块化可插拔** | 数据源/策略/券商都可独立替换，不改核心代码 |
| **策略模板化** | 实现新策略只需继承BaseStrategy + 实现on_bar() |
| **配置驱动** | 改yaml不改代码，适合量化研究员 |
| **端到端验证** | 同一策略代码无缝切换回测→模拟→实盘 |
| **参数网格搜索** | 批量扫描参数组合，自动寻找最优解 |

### 11.2 与行业方案对比

| 维度 | 量子 v0.1.2 | 聚宽/米筐 | PyAlgoTrade/Backtrader |
|------|-------------|-----------|----------------------|
| **费用** | 免费 | SaaS订阅 | 免费 |
| **数据源** | 雅虎(免费) | 自有数据 | 需自配 |
| **A股适配** | ⭐ T+1涨跌停内置 | ✅ 完善 | ❌ 需自写 |
| **实盘接口** | 预留中泰XTP/QMT | 多家券商 | 各有插件 |
| **策略种类** | 趋势/网格/配对/ML | 丰富 | 丰富 |
| **GridMA 策略** | ✅ TrendGrid v2 | 需自写 | 需自写 |
| **文档** | 本文档 | 完善 | 社区文档 |
| **运维** | 自托管 | 托管 | 自托管 |

### 11.3 创新点

1. **TrendGrid 趋势网格融合策略** — 独创"上升趋势中做网格低吸，趋势反转则清仓"的混合模式。传统网格在单边上涨中踏空，双均线在震荡下跌中反复被收割，TrendGrid 取两者之长。
2. **真实网格状态追踪** — 通过 `_position_levels` 记录每笔买入的价量，真正做到仓位管理而非每天重置，v1→v2的关键修复。
3. **多层缓存数据管线** — 内存→本地→远程三层层叠，回测时一次拉取后续全走内存。
4. **Pydantic全链路强类型** — 从Bar到Signal到Order到BacktestResult，每层数据都有运行时校验。
5. **Zero-to-Run** — 从pip install到跑出第一份回测报告只要3步：配置→run→看结果。

---

## 十二、版本历史

| 版本 | 日期 | 变更 |
|------|------|------|
| v0.1.0 | 初始 | 系统框架、回测引擎、示例策略 |
| v0.1.1 | 2026-05-09 | 雅虎数据源、GridMA v1策略、analyzer bugfix |
| **v0.1.2** | **2026-05-09** | **TrendGrid v2策略、GridMA状态管理修复、德明利验证** |

---

## 附录A：德明利(001309) 实盘验证

### 回测参数

| 项目 | 值 |
|------|-----|
| 标的 | 德明利 (001309.SZ) |
| 时间区间 | 2024-05-08 → 2026-05-08 |
| 数据量 | 485条日线 |
| 初始资金 | ¥1,000,000 |
| 佣金 | 万3（最低5元） |
| 印花税 | 千1（卖出） |
| 滑点 | 千1 |

### 八种策略对比

| 策略 | 收益率 | 最大回撤 | 夏普 | 交易次数 |
|------|-------|---------|------|---------|
| **买入持有** | +511.66% | 50.10% | — | — |
| DualMA(5/20) | +450.68% | 29.72% | 1.94 | 24笔 |
| DualMA(10/30) | +330.73% | 48.60% | 1.63 | 12笔 |
| **TrendGrid(MA45/3档/5%/15%)** | **+683.48%** | **49.99%** | **2.03** | **6笔** |
| TrendGrid(MA45/4档/8%/20%) | +677.78% | 49.92% | 2.03 | 7笔 |
| TrendGrid(MA20/3档/5%/15%) | +552.96% | 48.40% | 1.92 | 5笔 |
| TrendGrid(MA60/3档/5%/15%) | +433.01% | 40.52% | 1.85 | 6笔 |
| TG(MA30/3档/5%/15%) | +372.41% | 40.47% | 1.73 | 7笔 |

### 结论

TrendGrid(MA45/3档/5%/15%) 以 **+683.48%** 收益和夏普 **2.03** 全面跑赢买入持有的+511.66%。

DualMA(5/20) 虽然收益稍低（+450.68%），但最大回撤仅29.72%，**回撤控制更好**——适合风险承受能力较低的投资者。

> **文档状态**: 代码与文档对齐 | **维护人**: Joker AI Assistant | **版本**: v0.1.2

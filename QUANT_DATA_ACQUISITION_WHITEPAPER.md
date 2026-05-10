# 量化交易系统数据获取白皮书

> 系统：量子 (Quantum) | 作者：Joker AI | 日期：2026-05-09

---

## 目录

1. [引言](#1-引言)
2. [数据源分类全景](#2-数据源分类全景)
3. [市场行情数据](#3-市场行情数据)
4. [基本面数据](#4-基本面数据)
5. [另类数据](#5-另类数据)
6. [数据获取技术方案详解](#6-数据获取技术方案详解)
7. [工程实践——量子系统的实现](#7-工程实践量子系统的实现)
8. [数据质量与一致性保障](#8-数据质量与一致性保障)
9. [总结与建议](#9-总结与建议)

---

## 1. 引言

量化交易的本质是用数学模型和计算机程序代替人工作出投资决策。而所有模型和策略的起点，都是**数据**。数据获取层的质量直接决定了整个量化系统的天花板——垃圾进，垃圾出。

一个好的数据获取架构需要在以下维度取得平衡：

| 维度 | 要求 | 典型矛盾 |
|------|------|---------|
| 覆盖面 | 多市场、多品种、多周期 | 全面 vs 成本 |
| 时效性 | 实时 or 准实时 | 延迟 vs 可用性 |
| 准确性 | 数据精确、复权正确 | 质量 vs 获取难度 |
| 一致性 | 不同来源的数据可对齐 | 标准化 vs 各源差异 |
| 可靠性 | 99.9%+ 可用率 | 冗余 vs 复杂度 |
| 成本 | 免费最好，付费要值 | 质量 vs 预算 |

基于量子系统的实践经验，本文将完整阐述全链路数据获取方案。

---

## 2. 数据源分类全景

量化交易的数据源可以从三个维度划分：

### 2.1 按数据类型划分

```
量化交易数据
├── 市场行情数据（Market Data）
│   ├── L1 行情（Level 1）
│   │   ├── 日线数据：Open/High/Low/Close/Volume
│   │   ├── 分钟线：1min/5min/15min/30min/60min
│   │   └── 实时快照：最新价、涨跌幅、五档盘口
│   └── L2 行情（Level 2）
│       ├── 十档深度行情
│       ├── 逐笔成交（Tick）
│       └── 逐笔委托
├── 基本面数据（Fundamental Data）
│   ├── 财务数据：三张报表、财务指标
│   ├── 公司行动：分红、送转、配股、增发
│   └── 宏观数据：GDP、CPI、PMI、利率
├── 因子数据（Factor Data）
│   ├── 价量因子：动量、反转、波动率、流动性
│   ├── 基本面因子：估值、成长、质量、红利
│   └── 另类因子：情绪、舆情、供应链
└── 另类数据（Alternative Data）
    ├── 舆情数据：新闻、社交媒体、研报
    ├── 供应链数据：供应商、客户、物流
    ├── 卫星数据：停车场、工地、农作物
    ├── 电商数据：销量、价格、评价
    └── 支付数据：信用卡流水、交易量
```

### 2.2 按时效性划分

| 类型 | 更新频率 | 延迟要求 | 典型用途 |
|------|---------|---------|---------|
| 实时行情 | 毫秒~秒级 | <100ms | 高频、算法交易 |
| 准实时 | 秒~分钟级 | <5min | 日内策略、盘中信号 |
| 日频 | 每日收盘后 | T+1 开盘前 | 中低频策略、因子更新 |
| 低频 | 周/月/季度 | 无硬性要求 | 基本面分析、宏观配置 |
| 历史快照 | 一次性 | N/A | 回测、研究 |

### 2.3 按收费模式划分

| 类型 | 代表来源 | 日均成本 | 适用场景 |
|------|---------|---------|---------|
| 完全免费 | Yahoo Finance, AKShare | ¥0 | 个人研究、原型验证 |
| 免费+限频 | TuShare Pro(基础分) | ¥0 | 小规模策略 |
| 按量付费 | Wind, 聚宽, TuShare(高级) | ¥100~1000 | 专业策略研发 |
| 机构订阅 | 万得终端, Bloomberg | ¥10000+ | 机构级实盘 |
| 交易所直连 | SSE/SZSE 行情 | ¥50000+/年+设备 | 高频交易 |

---

## 3. 市场行情数据

市场行情数据是量化交易的核心数据层，也是最基础、最不可或缺的数据。

### 3.1 Level 1 行情

#### 日线数据

标准OHLCV，每只股票每日一条记录。

**字段**：日期、开盘价、最高价、最低价、收盘价、成交量、成交额

**数据特征**：
- 数据量最小（A股~5000只 × 250交易日/年 ≈ 125万条/年）
- 适合长期策略、多因子模型、组合优化
- 历史可回溯时间长（A股>30年，美股>50年）

**复权问题**：
- 不复权：原始数据，存在分红除权缺口
- 前复权：调整历史价格，使价格连续，当前最新价为实际价格
- 后复权：调整历史价格，保持历史真实价格，但最新价失真

> ⚠️ 复权方式选择直接影响回测准确性。量子系统默认使用**前复权**，因为回测时从历史视角看当前，价格连续可比较。

#### 分钟线

1分钟、5分钟、15分钟、30分钟、60分钟K线。

**字段**：除OHLCV外，部分来源还提供成交笔数、资金流向

**数据特征**：
- 数据量：1分钟线约240条/天/只
- A股可回溯时间普遍短（多数仅2~3年免费数据）
- 对存储和IO性能有要求，需要高效压缩存储（Parquet格式）

#### 实时快照

当前时刻的行情快照：

```
{
  "最新价": 180.50,
  "涨跌幅": +2.15%,
  "成交量": 1250000,
  "成交额": 2.25亿,
  "最高": 182.00,
  "最低": 176.50,
  "今开": 177.00,
  "昨收": 176.70,
  "量比": 1.25,
  "换手率": 1.85%,
  "振幅": 3.11%,
  "流通市值": 135亿,
  "市盈率(动)": 25.3,
}
```

### 3.2 Level 2 行情（深度行情）

L2行情是专业交易的基础，提供更细粒度的市场微观结构信息。

#### 十档深度

标准五档（L1）之外的更完整委托队列，展示10档买卖挂单的价格和数量。

**用途**：
- 挂单厚度分析——支撑/压力位判断
- 买卖力道——买盘vs卖盘力度对比
- 大单监测——异常大单挂撤行为

#### 逐笔成交（Tick）

每一笔真实成交的记录，而非快照。

**字段**：成交时间、成交价、成交量、成交金额、买卖方向（主买/主卖/未知）

**用途**：
- 资金流向：主动买入vs主动卖出
- 大单追踪：单笔>50万的异动
- 微观结构分析：订单流不平衡
- 高频策略的原子输入

#### 逐笔委托

每一笔订单的挂单、撤单记录（最高级别数据）。

**用途**：
- 订单簿重构——精确还原订单簿状态变化
- 订单流毒性分析——识别知情交易
- 撤单率分析——真假挂单识别

### 3.3 各市场行情数据来源对比

| 数据源 | A股日线 | A股分钟 | A股实时 | L2/Tick | 美股行情 | 免费额度 |
|--------|---------|---------|---------|---------|---------|---------|
| Yahoo Finance | ✅ 2年 | ❌ | ❌ | ❌ | ✅ 完整 | 无限制 |
| AKShare | ✅ 完整 | ✅ 分钟 | ✅ | ✅ Tick | ❌ | 免费 |
| TuShare Pro | ✅ 完整 | ✅ 分钟 | ✅ | ✅ Tick | ❌ | 200次/分 |
| Wind | ✅ 完整 | ✅ 完整 | ✅ | ✅ 完整 | ✅ | ¥30K+/年 |
| JoinQuant/聚宽 | ✅ 完整 | ✅ 完整 | ✅ | ✅ Tick | ✅ | ¥0~付费 |
| 交易所直连 | ✅ | ✅ | ✅ | ✅ | ❌ | 机构级 |
| Polygon.io | ❌ | ❌ | ❌ | ❌ | ✅ 完整 | 免费套餐 |
| Alpha Vantage | ❌ | ❌ | ❌ | ❌ | ✅ 分钟 | 5次/分 |

---

## 4. 基本面数据

基本面数据是价值投资和多因子模型的核心输入。

### 4.1 财务数据

#### 三张报表

| 报表 | 核心科目 | 更新频率 | 披露时间 |
|------|---------|---------|---------|
| 资产负债表 | 总资产、负债、净资产、货币资金 | 季度 | 季末后1个月 |
| 利润表 | 营收、净利润、营业利润、扣非净利润 | 季度 | 季末后1个月 |
| 现金流量表 | 经营/投资/筹资现金流 | 季度 | 季末后1个月 |

#### 关键财务指标

从报表衍生出：ROE、ROA、毛利率、净利率、资产负债率、流动比率、速动比率、每股收益(EPS)、每股净资产(BVPS)

#### 一致性预期

券商分析师对未来的盈利预测，包括：
- 一致预期营收/净利润
- 一致预期EPS
- 预测评级的分布（买入/增持/中性/减持）

### 4.2 公司行动数据

直接影响价格和持仓的事件：

| 事件类型 | 数据字段 | 对策略的影响 |
|---------|---------|------------|
| 分红 | 每股分红金额、除权除息日 | 价格调整、股息策略 |
| 送转 | 送股比例、转增比例 | 股本变动、价格调整 |
| 增发/配股 | 增发价、增发数量 | 稀释效应、价格压力 |
| 回购 | 回购金额、回购均价 | 信号效应、基本面改善 |
| 股权激励 | 行权价、解锁时间表 | 管理层的利益绑定 |

### 4.3 宏观数据

| 数据 | 更新频率 | 来源 | 影响 |
|------|---------|------|------|
| GDP增速 | 季度 | 国家统计局 | 市场整体方向 |
| CPI/PPI | 月度 | 国家统计局 | 通胀预期、货币政策 |
| PMI | 月度 | 国家统计局 | 经济景气度 |
| LPR利率 | 月度 | 央行 | 资金成本 |
| M2/M1 | 月度 | 央行 | 流动性水平 |
| 社融规模 | 月度 | 央行 | 实体经济融资 |
| 外汇储备 | 月度 | 央行 | 汇率压力 |

### 4.4 基本面数据来源对比

| 数据源 | 财务数据 | 公司行动 | 宏观数据 | 一致预期 | 收费 |
|--------|---------|---------|---------|---------|------|
| AKShare | ✅ 完整 | ✅ | ✅ | ❌ | 免费 |
| TuShare Pro | ✅ 完整 | ✅ | ✅ | ✅ 部分 | 免费/付费 |
| Wind | ✅ 完整 | ✅ 完整 | ✅ 完整 | ✅ 完整 | ¥30K+/年 |
| Choice (东方财富) | ✅ 完整 | ✅ | ✅ | ✅ | ¥10K+/年 |
| iFinD (同花顺) | ✅ 完整 | ✅ | ✅ | ✅ | ¥10K+/年 |
| 巨潮资讯网(爬虫) | ✅ 免费 | ✅ 免费 | ❌ | ❌ | 免费 |

---

## 5. 另类数据

另类数据是当前量化领域的"军备竞赛"前沿——当主流因子被充分研究后，信息差的来源就是另类数据。

### 5.1 文本/舆情数据

| 数据来源 | 数据类型 | 分析方法 | 信号周期 |
|---------|---------|---------|---------|
| 新闻 | 公司新闻、行业新闻 | NLP情感分析 | 天~周 |
| 社交媒体 | 雪球、微博、股吧 | 情绪指数、热度 | 小时~天 |
| 研报 | 券商研究报告 | 关键词提取、评级 | 周~月 |
| 公告 | 上市公司公告 | 事件驱动分类 | 天 |
| 电话会议 | 管理层QA记录 | 语言情绪分析 | 季度 |

**实现方案**：
- 爬虫→自然语言处理(NLP/LLM)→情感因子化→纳入策略

### 5.2 供应链数据

- 上下游关系图谱（谁是供应商、谁是客户）
- 物流数据（发货量、货运频次）
- 库存数据（一些行业的库存周转可推断销量）
- 产业景气度（产能利用率、开工率）

### 5.3 卫星/遥感数据（最前沿）

- **零售停车场**：停车场车辆数 → 推断客流量 → 预测营收
- **农业监测**：作物面积/长势 → 商品期货价格预测
- **原油库存**：储油罐液位 → 全球供需平衡
- **工业活动**：工厂烟囱温度/排放 → 产能利用率

**代表供应商**：Orbital Insight、RS Metrics、SpaceKnow

### 5.4 其他另类数据

| 数据类型 | 数据内容 | 策略应用 |
|---------|---------|---------|
| 电商数据 | 商品销量、价格、评价 | 消费品牌预测 |
| APP下载量 | 应用商店排名、下载量 | 互联网公司活跃度 |
| 招聘数据 | 岗位数量、薪资 | 公司扩张/收缩 |
| 专利数据 | 申请量、引用量 | 创新活力 |
| 信用卡数据 | 消费类别、金额、频次 | 消费趋势 |
| 房产数据 | 成交价、挂牌量、看房热力 | 地产/周期 |
| 天气数据 | 温度、降雨、极端天气 | 商品/气候敏感行业 |

---

## 6. 数据获取技术方案详解

### 6.1 API 接口调用

最主流的数据获取方式。设计上需要处理限流、重试、认证等通用问题。

#### 通用API调用框架

```python
class BaseAPIClient:
    """通用API客户端基类"""
    
    def __init__(self, config):
        self.base_url = config["base_url"]
        self.token = config.get("token", "")
        self.max_retries = config.get("max_retries", 3)
        self.rate_limit = config.get("rate_limit", 0.5)  # 秒/请求
        self._last_request = 0.0
    
    def _request(self, method, path, params=None, data=None):
        """带限流和重试的HTTP请求"""
        # 1. 请求频率控制
        self._throttle()
        
        # 2. 构建请求
        url = f"{self.base_url}{path}"
        headers = self._build_headers()
        
        # 3. 带指数退避的重试
        for attempt in range(self.max_retries):
            try:
                resp = requests.request(
                    method, url, params=params, 
                    json=data, headers=headers, timeout=15
                )
                resp.raise_for_status()
                return resp.json()
            except requests.HTTPError as e:
                if e.response.status_code == 429:
                    # 被限流，等待 Retry-After
                    wait = int(e.response.headers.get(
                        "Retry-After", 2 ** attempt
                    ))
                    time.sleep(wait)
                elif e.response.status_code == 403:
                    raise AuthError(f"认证失败: {e}")
                elif e.response.status_code >= 500:
                    # 服务端错误，可以重试
                    if attempt < self.max_retries - 1:
                        time.sleep(2 ** attempt)
                        continue
                else:
                    raise DataError(f"请求失败: {e}")
            except requests.ConnectionError as e:
                if attempt < self.max_retries - 1:
                    time.sleep(2 ** attempt)
                    continue
                raise NetworkError(f"连接失败: {e}")
    
    def _throttle(self):
        """请求限速"""
        elapsed = time.time() - self._last_request
        if elapsed < self.rate_limit:
            time.sleep(self.rate_limit - elapsed)
        self._last_request = time.time()
```

#### 关键设计点

**限流算法**：
- 令牌桶（Token Bucket）：适合突发流量
- 滑动窗口（Sliding Window）：精确限频
- 简单退避（Simple Backoff）：够用就好的方案

**退避策略**：
```
第1次失败 → 等待2s
第2次失败 → 等待4s  
第3次失败 → 等待8s
...
最多MaxRetries次
```

**认证方式**：
| 方式 | 适用场景 |
|------|---------|
| API Key 请求头 | 最通用 |
| Bearer Token | OAuth2 兼容 |
| 签名认证 | 金融敏感接口 |
| 基本认证 | 旧系统兼容 |

### 6.2 数据库直接访问

#### SQL 模式（机构级）

部分大型金融机构直接采购交易所或资讯商的数据库，通过 SQL 直连访问。

```sql
-- 示例：万得 IPDB
SELECT 
    trade_date, sec_code, open_px, high_px, 
    low_px, close_px, volume, turnover
FROM wind.daily_quote
WHERE sec_code = '000001.SZ'
  AND trade_date BETWEEN '2024-01-01' AND '2024-12-31'
ORDER BY trade_date
```

**优点**：数据最全、实时性高、查询灵活
**缺点**：年费极高（数十万~百万级）、需要专职DBA

#### 对象存储 + Parquet（轻量方案）

量子系统的数据存储方案：

```
data/
├── daily/
│   ├── 000001.SZ.parquet
│   ├── 600519.SS.parquet
│   └── ...
├── minute/
│   ├── 000001.SZ.parquet
│   └── ...
└── fundamentals/
    └── ...
```

**Parquet 列式存储的优势**：
- 高压缩比（相比CSV节省5~10倍空间）
- 支持列投影（只读需要的列，IO极低）
- 天然与Pandas/PyArrow集成
- 支持分区，查询效率高

```python
# 写入
import pyarrow.parquet as pq

table = pa.Table.from_pandas(df)
pq.write_to_dataset(
    table, 
    root_path="data/daily/",
    partition_cols=["symbol"]
)

# 读取（只读部分列）
df = pd.read_parquet("data/daily/000001.SZ.parquet", 
                      columns=["time", "close", "volume"])
```

### 6.3 Web 爬虫

当目标数据没有API时，爬虫是最后的方案。

#### 典型爬虫场景

1. **巨潮资讯网**：上市公司PDF公告下载
2. **交易所网站**：融券余额、融资融券明细
3. **财经门户**：新闻内容抓取
4. **社交媒体**：股吧/雪球帖子采集

#### 爬虫技术栈

```
Requests + BeautifulSoup  → 轻量静态页面
Selenium / Playwright     → 动态JS渲染
Scrapy                    → 大规模分布式
Cloudscraper              → Cloudflare绕过
```

#### 防封策略

```
旋转User-Agent → 随机浏览器指纹
IP代理池       → 多出口切换
随机延时       → 人类行为模拟
Cookie管理     → Session持久化
无头浏览器     → JavaScript渲染
请求频率控制   → 不要比人还快
```

> ⚠️ **风险提示**：爬虫方式存在法律和合规风险。中国市场数据的知识产权归属复杂，商用前务必明确数据使用权。量产的爬虫方案建议配合专业法务审查。

### 6.4 数据订阅推送（WebSocket/Stream）

实时行情必须使用推送式连接：

```python
import asyncio
import websocket

class RealtimeDataStream:
    """WebSocket实时行情"""
    
    def __init__(self, symbols, callback):
        self.symbols = symbols
        self.callback = callback
        self.ws = None
        
    def connect(self):
        """连接行情推送服务器"""
        # 示例：东方财富 WebSocket
        ws_url = "wss://push.eastmoney.com/ws"
        
        self.ws = websocket.WebSocketApp(
            ws_url,
            on_message=self._on_message,
            on_error=self._on_error,
            on_close=self._on_close,
            on_open=self._on_open
        )
        # 启动长连接
        self.ws.run_forever()
    
    def _on_open(self, ws):
        """连接建立后订阅标的"""
        for symbol in self.symbols:
            subscribe_msg = {
                "action": "subscribe",
                "codes": [symbol],
                "fields": ["price", "volume", "amount"]
            }
            ws.send(json.dumps(subscribe_msg))
        log.info(f"已订阅 {len(self.symbols)} 只股票实时行情")
    
    def _on_message(self, ws, message):
        """收到实时数据"""
        data = json.loads(message)
        # 解析并回调
        bar = self._parse_tick(data)
        if bar:
            self.callback(bar)
    
    def _reconnect(self):
        """断线自动重连"""
        log.warning("行情连接断开，5秒后重连...")
        time.sleep(5)
        self.connect()
```

**心跳保活**：
```
客户端 → 服务器：Ping (每隔30s)
服务器 → 客户端：Pong
如果120s无响应 → 判定断开 → 触发重连
```

### 6.5 文件导入（离线数据）

当网络不可靠或需要历史全量数据时使用：

| 文件格式 | 适用场景 | 工具 |
|---------|---------|------|
| CSV | 通用表格数据 | pandas.read_csv |
| Parquet | 列式存储/分析 | pandas.read_parquet |
| HDF5 | 大规模时序 | pandas.HDFStore |
| Feather | 跨语言交互 | pandas.read_feather |
| Excel | 研究报告/分析 | pandas.read_excel |
| SQLite | 轻量数据库 | sqlite3 |

---

## 7. 工程实践——量子系统的实现

量子系统的数据获取层设计遵循**多级缓存 + 多数据源回退**的架构模式。

### 7.1 架构总览

```
                     ┌─────────────────────┐
                     │      DataManager     │
                     │  (统一数据入口、缓存)  │
                     └──────────┬──────────┘
                                │
          ┌─────────────────────┼─────────────────────┐
          ▼                     ▼                     ▼
┌──────────────────┐  ┌──────────────────┐  ┌──────────────────┐
│   DataSource A   │  │   DataSource B   │  │   DataSource C   │
│ (YahooFinance)   │  │   (AKShare)      │  │   (TuShare)      │
│ - 免费/无Token   │  │ - A股日线/分钟    │  │ - 专业/需Token   │
│ - 全球股/2年历史 │  │ - L2 Tick/实时   │  │ - 基本面/宏观    │
│ - 仅日线         │  │ - 仅A股          │  │ - 限频200次/分   │
└──────────────────┘  └──────────────────┘  └──────────────────┘
                                │
                      ┌─────────┴─────────┐
                      ▼                   ▼
               ┌────────────┐    ┌────────────┐
               │ DataStore 1 │    │ DataStore 2 │
               │  (Parquet)  │    │  (SQLite)   │
               └────────────┘    └────────────┘
```

### 7.2 多级缓存策略

```
内存缓存 (Memory Cache)
  ├── 最近访问的热数据
  ├── 过期策略：LRU / TTL
  └── 大小限制：~1GB

本地存储 (Local Store)
  ├── Parquet文件（主存储）
  │   ├── 按symbol分区
  │   ├── 列式压缩（~90%压缩率）
  │   └── 支持列投影查询
  ├── SQLite（小数据集）
  │   ├── 适合基本面/因子数据
  │   └── SQL灵活查询

远程数据源 (Remote Source)
  ├── 多源注册，按优先级回退
  ├── 使用优先：存储 > Yahoo > AKShare > TuShare
  └── 故障时自动切换
```

**查询路径**：
```
调用 get_data(symbol, start, end)
  ↓
① 检查内存缓存命中？
  ├─ 是 → 返回
  └─ 否 →
② 检查本地存储（Parquet/SQLite）？
  ├─ 是 → 加载到内存 → 返回
  └─ 否 →
③ 调用远程数据源（按优先级尝试）
  ├─ Yahoo → 有数据？→ 保存到本地 → 返回
  ├─ AKShare → 有数据？→ 保存到本地 → 返回
  └─ TuShare → 有数据？→ 保存到本地 → 返回
  ↓
④ 全失败 → 抛出 DataSourceError
```

### 7.3 Yahoo Finance 实现详解（量子系统的实际数据源）

量子系统最终主用的是 Yahoo Finance，因为它满足三个关键条件：**免费、无需Token、能用**。

#### 代码格式适配

A股代码需要转换成 Yahoo 格式：

```python
MARKET_MAP = {
    "6": ".SS",   # 600xxx-605xxx → 沪市主板
    "9": ".SS",   # 688xxx → 科创板
    "0": ".SZ",   # 000xxx-001xxx → 深市主板
    "3": ".SZ",   # 300xxx → 创业板
    "4": ".BJ",   # 4xxxxx → 北交所
    "8": ".BJ",   # 8xxxxx → 北交所
}

# 600519 → 600519.SS  (贵州茅台)
# 000001 → 000001.SZ  (平安银行)
# 001309 → 001309.SZ  (德明利)
```

#### 请求重试机制

```python
for attempt in range(max_retries):
    try:
        data = self._request(url)
        return self._parse_bars(data)
    except HTTPError as e:
        if e.code == 404:    # 标的不存在 → 直接失败
            raise
        elif e.code == 429:  # 限流 → 退避重试
            wait = retry_delay * (2 ** attempt)
            time.sleep(wait)
        else:
            raise
    except URLError:         # 网络问题 → 重试
        if attempt < max_retries - 1:
            time.sleep(retry_delay * (2 ** attempt))
            continue
        raise
```

**退避策略**：指数退避（Exponential Backoff），最多重试3次。

#### 限流保护

每次API请求后至少间隔500ms，避免触发 Yahoo 的限流。

### 7.4 数据统一模型

无论底层使用什么数据源，对外暴露统一的数据模型：

```python
class Bar(BaseModel):
    """K线数据——所有策略的数据消费接口"""
    symbol: str
    time: datetime
    timeframe: TimeFrame
    open: float
    high: float
    low: float
    close: float
    volume: float
    amount: float
```

策略层只依赖 `Bar` 模型，不关心数据来自 Yahoo 还是 AKShare。这种**接口隔离**使得更换数据源时无需修改任何策略代码。

### 7.5 全设备健康监控

```python
def health_check_all(self) -> Dict[str, bool]:
    """检查所有注册数据源的健康状态"""
    status = {}
    for name, source in self.sources.items():
        try:
            status[name] = source.health_check()
        except Exception:
            status[name] = False
    return status
```

---

## 8. 数据质量与一致性保障

### 8.1 常见数据质量问题

| 问题 | 表现 | 原因 | 影响 |
|------|------|------|------|
| 缺失值 | NaN/空行 | 停牌、数据源故障 | 指标计算错误 |
| 重复数据 | 相同时间戳多条 | 重复发送/采集 | 成交量翻倍 |
| 异常值 | 价格涨跌停外 | 数据源错误 | 回测收益假高 |
| 复权错误 | 价格跳跃不一致 | 数据源计算错误 | 策略逻辑偏差 |
| 时区错位 | 日期偏移一天 | 时区处理不当 | 信号错位 |
| 前后不一致 | 同一数据两份不一致 | 不同数据源天然差异 | 策略验证失效 |

### 8.2 数据清洗流程

```python
class DataCleaner:
    """数据清洗流水线"""
    
    def clean(self, bars: List[Bar]) -> List[Bar]:
        """清洗管道"""
        pipeline = [
            self.remove_duplicates,
            self.fill_missing,
            self.remove_outliers,
            self.adjust_for_actions,  # 复权
            self.sort_by_time,
        ]
        for step in pipeline:
            bars = step(bars)
        return bars
    
    def remove_duplicates(self, bars):
        """去重：保留最后一条"""
        seen = set()
        unique = []
        for bar in bars:
            key = (bar.symbol, bar.time, bar.timeframe)
            if key not in seen:
                seen.add(key)
                unique.append(bar)
        return unique
    
    def fill_missing(self, bars):
        """处理缺失值：前向填充"""
        # 交易日历填充 + 前向填充
        pass
    
    def remove_outliers(self, bars):
        """异常值检测（3σ 或涨跌停规则）"""
        filtered = []
        for bar in bars:
            daily_return = abs(bar.return_rate)
            # A股常规涨跌停限制
            if daily_return > 0.11:  # +-10% + 少许误差
                # 检查是否有除权/除息原因
                continue  # 标记为异常
            filtered.append(bar)
        return filtered
```

### 8.3 数据一致性校验

跨数据源交叉验证：

```python
class DataValidator:
    """数据交叉验证"""
    
    def validate(self, primary_bars, secondary_bars, tolerance=0.01):
        """验证两个数据源的数据是否一致"""
        # 对齐时间
        primary_map = {b.time: b for b in primary_bars}
        secondary_map = {b.time: b for b in secondary_bars}
        
        mismatches = []
        for time, pb in primary_map.items():
            sb = secondary_map.get(time)
            if not sb:
                continue
            
            # 收盘价差异 > 1%
            if abs(pb.close - sb.close) / sb.close > tolerance:
                mismatches.append({
                    "time": time,
                    "primary_close": pb.close,
                    "secondary_close": sb.close,
                    "diff_pct": abs(pb.close - sb.close) / sb.close
                })
        
        return {
            "total_bars": len(primary_map),
            "matched_bars": len(mismatches) == 0,
            "mismatch_count": len(mismatches),
            "mismatches": mismatches[:10]  # 只展示前10个
        }
```

### 8.4 数据版本管理

```python
# 每次数据更新记录元数据
{
    "version": "2026-05-09-v1",
    "source": "yahoo_finance",
    "symbols_count": 50,
    "date_range": ["2024-01-01", "2026-05-09"],
    "bars_count": 125000,
    "checksum": "sha256:a3f8b2c1...",
    "updated_at": "2026-05-09T23:30:00+08:00",
    "errors": ["600519.SS: 2 missing days (2024-02-08, 2024-02-09)"]
}
```

---

## 9. 总结与建议

### 9.1 不同阶段数据方案推荐

| 阶段 | 市场行情 | 基本面 | 费用 | 说明 |
|------|---------|--------|------|------|
| 个人研究/学习 | Yahoo + AKShare | AKShare | ¥0 | 足以跑通全流程 |
| 小团队回测 | AKShare + TuShare基础 | TuShare | ¥1K/年 | 覆盖A股全品种 |
| 专业策略开发 | 聚宽/优矿 + TuShare专业 | Wind/Choice | ¥10~30K/年 | 数据质量有保障 |
| 机构实盘 | 万得/交易所直连 | 万得+一致预期 | ¥30K+/年 | 低延迟+合规 |
| 高频/做市 | 交易所L2直连+FPGA | N/A | 百万+/年 | 微秒级延迟 |

### 9.2 量子系统现状

量子系统当前的数据获取层定位在**阶段1~2之间**：

```
✅ Yahoo Finance   — 开源、免费、全球股市日线（主要数据源）
✅ AKShare         — A股全品种（依赖Python≥3.8，当前环境受限）
✅ TuShare Pro     — 备选数据源（需要Token）
✅ 本地Parquet存储 — 本地缓存，减少重复请求
✅ 自动回退机制   — 数据源故障时自动切换
❌ WebSocket行情   — 尚未集成
❌ L2/逐笔成交     — 需要机构级合作
```

### 9.3 关键经验教训

**1. 先跑通再优化**
量子系统开发遇到的最大问题不是"哪个数据源质量最好"，而是"哪个数据源现在能用"。在 `python3.6` + 无Token 的环境下，唯一能用的就是 Yahoo Finance。**能用比好用在早期更重要**。

**2. 数据源适配器模式**
统一的数据抽象层（`BaseDataSource`）让切换数据源变成配置变更，而非代码重写。这是后期平滑升级的关键。

**3. 限流和重试是必修课**
任何免费API都有隐性限流（Yahoo 429、AKShare 503）。没有错误处理和退避机制的代码，在盘中批量拉数据时会全面崩溃。

**4. 数据存储不能忽略**
初始阶段可以"用完即抛"，但一旦策略开始迭代，没有本地缓存的代价是每次回测都要重新拉数据，既慢又不稳定。

**5. 股票代码解析是隐藏坑**
A股的代码在不同数据源中有完全不同的编码方式。没有统一的 code 适配层，换数据源意味着改写所有策略。

---

> 本文档基于"量子"(Quantum)量化交易系统的实际开发经验撰写。
> 系统地址：`/home/admin/.openclaw/workspace/quantum/`

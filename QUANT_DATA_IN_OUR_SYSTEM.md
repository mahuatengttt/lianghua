# 量子系统数据获取——完整实现分析

> 量子(Quantum) v0.1.2 | 基于真实系统代码 | 2026-05-09

---

**本文档仅分析量子系统内**实际已实现的代码结构和数据途径，不涉及行业中通用但在本系统中尚未集成的内容。

---

## 一、系统数据层架构

### 1.1 模块结构

```
quantum/data/
├── __init__.py              # 模块入口
├── base.py                  # 核心抽象层：DataSource + DataManager + DataStore
├── sources/                 # 数据源实现
│   ├── __init__.py
│   ├── yahoo_source.py      # Yahoo Finance（当前主力数据源）
│   ├── ak_source.py         # AKShare（A股全品种）
│   ├── tushare_source.py    # TuShare Pro（专业级）
│   └── local_source.py      # 本地文件（CSV/Parquet/SQLite）
└── processors/              # 数据预处理
    ├── __init__.py
    ├── cleaner.py            # 数据清洗
    ├── aligner.py            # 数据对齐
    └── resampler.py          # 周期转换
```

### 1.2 核心架构：多级缓存 + 自动回退

数据获取的最顶层入口是 **DataManager**，它的查询路径如下：

```
调用 get_data(symbol, start, end, timeframe)
   │
   ├─❶ 内存缓存（_bar_cache）
   │   └─ 命中 → 返回（O(1) 极快）
   │
   ├─❷ 本地存储（ParquetStore / SQLiteStore）
   │   └─ 命中 → 加载到内存缓存 → 返回
   │
   ├─❸ 远程数据源轮询（按配置顺序遍历）
   │   ├─ YahooFinanceDataSource    ← 当前主力
   │   ├─ AKShareDataSource        ← 备选（需要Python≥3.8）
   │   └─ TushareDataSource        ← 备选（需要Token）
   │   └─ 成功 → 保存到本地存储 → 缓存到内存 → 返回
   │
   └─❹ 全部失败 → 抛出 DataSourceError
```

**关键代码**（base.py）：

```python
def get_data(self, symbol, start, end, timeframe, source_name=None, use_cache=True):
    cache_key = f"{symbol}_{timeframe.value}"

    # 1. 内存缓存
    if use_cache and cache_key in self._bar_cache:
        filtered = [b for b in self._bar_cache[cache_key] if start <= b.time <= end]
        if filtered:
            return filtered

    # 2. 本地存储
    for store in self.stores.values():
        try:
            bars = store.load(symbol, start, end, timeframe)
            if bars:
                self._bar_cache[cache_key] = bars
                return bars
        except Exception:
            continue

    # 3. 远程数据源
    sources = [self.sources[source_name]] if source_name else list(self.sources.values())
    for source in sources:
        try:
            bars = source.get_bars(symbol, start, end, timeframe)
            if bars:
                self._bar_cache[cache_key] = bars
                for store in self.stores.values():
                    try:
                        store.save(bars)
                    except Exception:
                        pass
                return bars
        except Exception as e:
            raise DataSourceError(f"获取失败: {e}")

    raise DataSourceError(f"所有数据源均失败: {symbol}")
```

**设计要点**：
- **内存缓存**：用 dict[hash_key → List[Bar]]，key是 symbol+timeframe，避免重复拉取
- **本地持久化**：下载后自动落盘，下次启动不需要重拉
- **源切换零代码改动**：策略只依赖统一的 Bar 模型，数据源从配置切换

---

## 二、数据源实现详解

### 2.1 Yahoo Finance DataSource —— 当前主力

**文件**：`sources/yahoo_source.py`

#### 为什么选它作主力

量子系统跑在 Python 3.6 环境上，当时遇到了：

| 数据源 | 遭遇的问题 |
|--------|-----------|
| AKShare | 要求 Python ≥ 3.8，装不上 |
| TuShare Pro | 需要 Token，没有配置 |
| Yahoo Finance | **零依赖、免Token、直接HTTP请求就能跑** |

所以当时选了 Yahoo，核心逻辑就一个标准库能搞定的事：

```python
import urllib.request
import json
import ssl

# 唯一HTTP调用
req = urllib.request.Request(url, headers={"User-Agent": "..."})
resp = urllib.request.urlopen(req, context=ssl_ctx, timeout=15)
data = json.loads(resp.read())
```

不需要 requests、不需要 pandas——全部标准库完成。

#### 代码适配——A股代码的转换

雅虎需要特定的代码后缀，所以我写了一个 code 适配层：

```python
MARKET_MAP = {
    "6": ".SS",   # 600xxx-605xxx → 沪主板, 688xxx → 科创板
    "9": ".SS",
    "0": ".SZ",   # 000xxx → 深主板, 001xxx, 002xxx → 中小板
    "3": ".SZ",   # 300xxx → 创业板
    "4": ".BJ",   # 北交所
    "8": ".BJ",
}

def _normalize_symbol(self, symbol: str) -> str:
    code = symbol.strip()
    # 去掉已有前缀（SH/SZ -> .SS/.SZ）
    for prefix in ["SH", "SZ", "sh", "sz", "SH.", "SZ.", "."]:
        if code.startswith(prefix):
            code = code.replace(prefix, "", 1)
            break

    if "." in code:        # 已经有后缀，直接返回
        return code.upper()
    if code.isdigit() and len(code) == 6:
        suffix = self.MARKET_MAP.get(code[0], "")
        return f"{code}{suffix}"
    return code
```

接口上策略始终传 `"001309"` 或 `"001309.SZ"`，底层自动标准化再去调 API。

#### 请求限流与重试

```python
class YahooFinanceDataSource(DataSource):

    def _request(self, url):
        # 请求间隔控制
        self._throttle()

        for attempt in range(self.max_retries):  # 最多3次
            try:
                ...
                return json.loads(resp.read())
            except HTTPError as e:
                if e.code == 429:  # 被限流
                    time.sleep(self.retry_delay * (2 ** attempt))
                    continue
                elif e.code == 404:
                    raise DataSourceError(f"标的不存在")
            except URLError:
                if attempt < self.max_retries - 1:
                    time.sleep(2 ** attempt)
                    continue
                raise

    def _throttle(self):
        """两次请求之间至少间隔 rate_limit_delay 秒（默认0.5秒）"""
        elapsed = time.time() - self._last_request
        if elapsed < self.rate_limit_delay:
            time.sleep(self.rate_limit_delay - elapsed)
        self._last_request = time.time()
```

**限流策略**：固定间隔 500ms + 指数退避（失败后 2s → 4s → 8s）。

#### 数据范围计算

雅虎用 range 参数控制数据量，我动态计算：

```python
def _calc_range(self, start, end):
    days = (end - start).days
    if days <= 30:     return "1mo"
    elif days <= 90:   return "3mo"
    elif days <= 180:  return "6mo"
    elif days <= 365:  return "1y"
    elif days <= 730:  return "2y"
    else:              return "max"
```

#### 数据解析——从JSON到Bar对象

```python
def _get_daily_bars(self, symbol, yahoo_code, range_str):
    url = f"{BASE_URL}/{yahoo_code}?range={range_str}&interval=1d"
    data = self._request(url)

    result = data["chart"]["result"][0]
    timestamps = result["timestamp"]
    quotes = result["indicators"]["quote"][0]
    adjclose = result["indicators"].get("adjclose", [{}])[0]

    bars = []
    for i in range(len(timestamps)):
        o = quotes["open"][i]
        h = quotes["high"][i]
        l = quotes["low"][i]
        c = quotes["close"][i]
        v = quotes["volume"][i]

        if None in (o, h, l, c):
            continue    # 停牌日跳过

        bar_time = datetime.fromtimestamp(timestamps[i])
        bar = Bar(symbol=symbol, time=bar_time,
                  timeframe=DAILY,
                  open=float(o), high=float(h),
                  low=float(l), close=float(c),
                  volume=float(v or 0), amount=float(v * c if v else 0))
        bars.append(bar)

    return bars
```

解析中处理了三个坑：
- **停牌日**：o/h/l/c 全部为 None → 跳过
- **成交量有时为 None**：兜底为 0
- **时间戳**：Unix 时间戳转 Python datetime

#### 当前状态

```
✅ 日线数据（最多2年历史，实际够用）
✅ 实时行情快照（get_realtime_bar）
❌ 分钟线（雅虎限制，不支持）
❌ Tick数据（返回空列表）
```

### 2.2 AKShare DataSource —— A股原生数据源

**文件**：`sources/ak_source.py`

#### 定位

当环境升级到 Python ≥ 3.8 后，AKShare 应该是主力。它免费、免Token、覆盖完整。

#### 能力矩阵

| 数据类型 | AKShare 接口 | 当前可用 |
|---------|-------------|---------|
| A股日线（前复权） | `stock_zh_a_hist(period="daily", adjust="qfq")` | ❌ 3.6 不支持 |
| A股分钟线 | `stock_zh_a_hist_min_em()` | ❌ |
| Tick行情 | `stock_zh_a_tick_tx()` | ❌ |
| 实时行情全市场 | `stock_zh_a_spot_em()` | ❌ |
| 全部A股列表 | `stock_zh_a_spot_em()` → 代码列 | ❌ |

#### 代码结构

```python
class AKShareDataSource(DataSource):

    def __init__(self, config):
        super().__init__(config)
        self._akshare = None  # 延迟加载

    def _lazy_import(self):
        """首次使用时才导入，避免系统启动就报错"""
        if self._akshare is None:
            import akshare as ak
            self._akshare = ak

    def get_bars(self, symbol, start, end, timeframe):
        self._lazy_import()
        ak = self._akshare

        if timeframe == DAILY:
            df = ak.stock_zh_a_hist(
                symbol=code, period="daily",
                start_date=start_str, end_date=end_str,
                adjust="qfq"  # ✅ 前复权
            )
        elif timeframe in (MIN1, MIN5, MIN15, MIN30, MIN60):
            df = ak.stock_zh_a_hist_min_em(
                symbol=code,
                period=period_map[timeframe],
                adjust="qfq"
            )

        for _, row in df.iterrows():
            bar = Bar(
                symbol=symbol,
                time=row.get("日期") or row.get("时间"),
                open=float(row.get("开盘", 0)),
                high=float(row.get("最高", 0)),
                low=float(row.get("最低", 0)),
                close=float(row.get("收盘", 0)),
                volume=float(row.get("成交量", 0)),
                amount=float(row.get("成交额", 0)),
            )
            bars.append(bar)
```

**关键设计——列名统一**：AKShare 不同接口返回的列名不一致（"开盘"/"开盘价"/"open"），代码里用 `.get()` 多重兜底，保证解析的健壮性。

#### 全市场扫描能力

```python
def get_universe(self, category="all"):
    """获取全部A股股票代码列表"""
    self._lazy_import()
    df = self._akshare.stock_zh_a_spot_em()
    return df["代码"].tolist()  # 5000+只
```

这是雅虎做不到的——雅虎不支持批量列表。

### 2.3 Tushare DataSource —— 专业级备选

**文件**：`sources/tushare_source.py`

#### 定位

需要 Token 的专业数据源，覆盖最全。代码结构和 AKShare 基本一致，区别是使用 TuShare 的 pro_api。

#### 代码与AKShare的差异

```python
# AKShare
df = ak.stock_zh_a_hist(symbol=code, period="daily", ...)

# TuShare
df = pro.daily(ts_code=code, start_date=start_str, end_date=end_str)
```

**当前状态**：`config/default.yaml` 中 `tushare.enabled: false`，因为 Token 未配置。

### 2.4 LocalFileDataSource —— 离线/回退方案

**文件**：`sources/local_source.py`

读取本地 CSV 或 Parquet 文件中的数据，当成数据源使用。这为离线分析和数据导入提供了通道。

```python
class LocalFileDataSource(DataSource):
    def get_bars(self, symbol, start, end, timeframe):
        file_path = self.data_dir / f"{symbol}_{timeframe.value}.parquet"
        if not file_path.exists():
            return []  # 没有文件 → 返回空，不抛异常

        if self.format == "parquet":
            df = pd.read_parquet(file_path)
        else:
            df = pd.read_csv(file_path, parse_dates=["time"])

        df = df[(df["time"] >= start) & (df["time"] <= end)]
        return [Bar(**row.to_dict()) for _, row in df.iterrows()]
```

---

## 三、数据持久化——本地缓存层

**文件**：`sources/local_source.py`（ParquetStore + SQLiteStore）

### 3.1 ParquetStore（主存储）

```python
class ParquetStore(DataStore):
    """列式存储，高压缩比"""
    data_dir = "./data/parquet/"  # 600519.SS_daily.parquet

    def save(self, bars):
        symbol = bars[0].symbol
        timeframe = bars[0].timeframe
        file_path = self.data_dir / f"{symbol}_{timeframe.value}.parquet"

        df_new = pd.DataFrame([b.model_dump() for b in bars])

        if file_path.exists():
            # 合并 + 去重
            df_existing = pd.read_parquet(file_path)
            df_combined = pd.concat([df_existing, df_new])
            df_combined = df_combined.drop_duplicates(
                subset=["symbol", "time", "timeframe"]
            )
            df_combined = df_combined.sort_values("time")
        else:
            df_combined = df_new

        df_combined.to_parquet(file_path, index=False)

    def load(self, symbol, start, end, timeframe):
        file_path = self.data_dir / f"{symbol}_{timeframe.value}.parquet"
        if not file_path.exists():
            return None

        df = pd.read_parquet(file_path)
        df = df[(df["time"] >= start) & (df["time"] <= end)]
        return [Bar(...) for ... in df.iterrows()]
```

**为什么选 Parquet**：
- 列式压缩，行情数据的价格/成交量列压缩率可达10:1
- 只读需要的列，IO 极低
- 与 pandas 原生集成，零转换成本

### 3.2 SQLiteStore（副存储）

用于基本面/因子等结构化数据，SQL 查询灵活：

```python
class SQLiteStore(DataStore):
    db_path = "./data/quantum.db"

    # 建表
    CREATE TABLE IF NOT EXISTS bars (
        symbol TEXT NOT NULL,
        time TEXT NOT NULL,
        timeframe TEXT NOT NULL,
        open REAL, high REAL, low REAL,
        close REAL, volume REAL, amount REAL,
        PRIMARY KEY (symbol, time, timeframe)
    )

    # 写入（UPSERT）
    INSERT OR REPLACE INTO bars VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)

    # 查询
    SELECT * FROM bars
    WHERE symbol = ? AND timeframe = ?
      AND time >= ? AND time <= ?
    ORDER BY time ASC
```

---

## 四、数据预处理——清洗加工的流水线

**文件**：`processors/__init__.py`

### 4.1 DataCleaner

从数据源拿到原始数据后，先经过清洗管道：

```python
class DataCleaner:
    def clean_bars(self, bars):
        if not bars: return bars

        # 1. 去除异常值（价格非正、high<low、空值）
        bars = [b for b in bars if not self._is_invalid(b)]

        # 2. 统计去噪（价格突变超过5个标准差 → 剔除）
        bars = self._remove_outliers(bars, std_threshold=5.0)

        # 3. 去重
        bars = self._remove_duplicates(bars)

        # 4. 排序
        bars.sort(key=lambda b: b.time)

        # 5. 可选：填充缺失K线（最多5根）
        if self.config.get("fill_gaps", False):
            bars = self._fill_gaps(bars)

        return bars
```

**价格异常检测逻辑**：

```python
def _is_invalid(self, bar):
    return (
        np.isnan(bar.open) or bar.open <= 0 or
        bar.high < bar.low or            # 最高价比最低价还低 → 数据错乱
        bar.high < bar.open or bar.high < bar.close
    )
```

**异常价格移除**（5σ 规则）：

```python
def _remove_outliers(self, bars):
    closes = np.array([b.close for b in bars])
    returns = np.diff(closes) / closes[:-1]
    mean = np.nanmean(returns)
    std = np.nanstd(returns)

    if std == 0: return bars
    # 只保留收益率在 mean ± 5σ 内的
    clean = [bars[0]]
    for i in range(1, len(returns)):
        if abs(returns[i] - mean) <= 5 * std:
            clean.append(bars[i])
    clean.append(bars[-1])
    return clean
```

**缺失填充**：如果两根K线之间有不超过5根K线的缺口，用前一根收盘价填充。

### 4.2 DataAligner

多标的回测需要时间轴对齐——不同股票可能有不同的交易日期（停牌、节假日差异）：

```python
class DataAligner:
    @staticmethod
    def align_bars(bar_dict, method="ffill"):
        # 把所有标的时间轴合并为全集
        all_times = sorted(set(t for bars in bar_dict.values() for b in bars))
        full_index = pd.DatetimeIndex(all_times)

        for symbol, df in dfs.items():
            df = df.reindex(full_index)
            df = df.ffill() if method == "ffill" else df = df.bfill()
            df = df.dropna()
            ...
```

### 4.3 DataResampler

分钟线 → 小时线 → 日线 → 周线 → 月线 的双向转换：

```python
class DataResampler:
    @staticmethod
    def resample(bars, target):
        rule_map = {
            MIN5: "5min", MIN15: "15min",
            MIN30: "30min", MIN60: "60min",
            DAILY: "D", WEEKLY: "W", MONTHLY: "ME",
        }
        ohlc = {"open": "first", "high": "max",
                "low": "min", "close": "last",
                "volume": "sum", "amount": "sum"}
        resampled = df.resample(rule).agg(ohlc).dropna()
```

---

## 五、数据模型——统一的数据消费接口

**文件**：`common/models.py`

无论数据来自哪个源、经过什么预处理，最终策略层看到的只有**一个模型**：

```python
class Bar(BaseModel):
    """K线——全系统统一"""
    symbol: str
    time: datetime
    timeframe: TimeFrame
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0
    amount: float = 0.0
```

配套的数据类型还包括：

```python
class Tick(BaseModel):    # 逐笔成交（未使用）
class Order(BaseModel):   # 订单
class Trade(BaseModel):   # 成交
class Position(BaseModel):# 持仓
class Portfolio(BaseModel):# 组合
class Signal(BaseModel):  # 交易信号
```

**信号流向**：
```
数据源(DS) → Bar → 策略/Signal → Order → Broker → Trade/Portfolio
                 ↑
            DataCleaner 清洗
```

---

## 六、数据获取的实际执行链路

### 6.1 回测时的数据流

```python
# run_backtest.py
bars_dict = {}
for symbol in ["000001", "000002", "000858"]:
    # 数据模块自动走缓存链
    bars = data_manager.get_data(symbol, start, end, timeframe)
    bars_dict[symbol] = bars

# 经过清洗和清洗
cleaner = DataCleaner()
for symbol in bars_dict:
    bars_dict[symbol] = cleaner.clean_bars(bars_dict[symbol])

# 对齐多标的时间轴
bars_dict = DataAligner.align_bars(bars_dict)

result = engine.run(bars_dict)
```

### 6.2 当前配置状态

```yaml
# config/default.yaml
data:
  primary_source: "yahoo"     # 主力
  cache_enabled: true
  cache_dir: "./data/cache"
  sources:
    yahoo:
      enabled: true
      rate_limit_delay: 0.5   # 500ms请求间隔
      max_retries: 3
    akshare:
      enabled: false          # 等升级 Python 后开启
      rate_limit_delay: 0.5
    tushare:
      enabled: false          # 需配置 Token
      token: ""
```

### 6.3 实际运行中遇到的问题（已修复）

| # | 问题 | 根因 | 解决 |
|---|------|------|------|
| 1 | AKShare/TuShare 均不可用 | Python3.6 + 无Token | 新增 Yahoo |
| 2 | Yahoo 某些域名被墙 | DNS/HTTPS 证书问题 | 改用 `query1.finance.yahoo.com` + 跳过SSL |
| 3 | A股代码格式不正确 | Yah00用.SS/.SZ后缀 | 写标准化适配层 |
| 4 | 回测胜率总为0% | Analyzer只处理配对交易 | 增加单边回退统计 |
| 5 | 回测日期字段None报错 | config 中 start_date/end_date 含 Optional | 加兜底逻辑 |

---

## 七、当前局限与升级路线

### 7.1 当前局限

```
缺少实时推送服务（WebSocket）
  ├─ 当前：get_realtime_bar() 拉取最新日线快照（非实时行情）
  └─ 目标：WebSocket 推送实时 Tick 流

缺少分钟线数据
  ├─ Yahoo 不支持
  ├─ AKShare 支持但被 Python 版本限制
  └─ 回测引擎的 backtest_config.py 已有分钟线支持，缺数据

缺少基本面数据集成
  └─ 因子模型已写在 strategy/signals/ 下，缺财务数据管道

数据源的健康自动切换
  ├─ 当前：配置手动切换 primary_source
  └─ 目标：自动检测可用性，透明 fallback
```

### 7.2 升级路线

```
Phase 1（当前）
  主数据源：Yahoo Finance（日线）
  缓存层：Parquet 本地存储
  手动配置源选择

Phase 2（近期）
  升级环境 → 开启 AKShare
  打通 A股日线 + 分钟线 + Tick
  集成基本面数据

Phase 3（远期）
  集成 WebSocket 实时行情流
  多数据源自动切换+一致性校验
  L2 深度行情（需机构合作）
```

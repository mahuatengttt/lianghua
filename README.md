# 量子 (Quantum) 量化交易系统

## 系统概述

"量子"是一套面向A股市场设计的模块化量化交易系统，涵盖从数据获取、策略研发、回测验证、风险控制到实盘交易的全流程。系统采用事件驱动架构，支持多市场、多品种、多策略并行运行。

## 核心特性

- **模块化设计**：五大核心模块独立部署、松耦合协作
- **多策略支持**：趋势跟踪、均值回归、统计套利、机器学习、深度学习
- **完整回测引擎**：支持Tick/分钟/日线多周期回测，含滑点、手续费模拟
- **实时风控**：多层风险控制体系，支持动态止盈止损、仓位管理
- **实盘接口**：统一Broker抽象层，支持多家券商接入
- **性能监控**：实时监控系统状态、策略表现、交易信号

## 项目结构

```
quantum/
├── README.md                       # 项目文档
├── requirements.txt                # 依赖清单
├── config/                         # 配置文件
│   └── default.yaml                # 系统默认配置
├── quantum/                        # 主代码包
│   ├── __init__.py
│   ├── common/                     # 公共模块
│   │   ├── __init__.py
│   │   ├── enums.py                # 枚举定义
│   │   ├── exceptions.py           # 异常定义
│   │   ├── models.py               # 数据模型
│   │   └── utils.py                # 工具函数
│   ├── data/                       # 数据模块
│   │   ├── __init__.py
│   │   ├── base.py                 # 数据源抽象基类
│   │   ├── sources/                # 数据源实现
│   │   │   ├── __init__.py
│   │   │   ├── tushare_source.py   # Tushare数据源
│   │   │   ├── ak_source.py        # AKShare数据源
│   │   │   └── local_source.py     # 本地文件数据源
│   │   ├── storage/                # 数据存储
│   │   │   ├── __init__.py
│   │   │   ├── base.py             # 存储抽象
│   │   │   ├── parquet_store.py    # Parquet存储
│   │   │   └── sqlite_store.py     # SQLite存储
│   │   └── processors/             # 数据预处理
│   │       ├── __init__.py
│   │       ├── cleaner.py          # 数据清洗
│   │       ├── aligner.py          # 数据对齐
│   │       └── resampler.py        # 周期转换
│   ├── strategy/                   # 策略模块
│   │   ├── __init__.py
│   │   ├── base.py                 # 策略基类
│   │   ├── signals/                # 信号生成
│   │   │   ├── __init__.py
│   │   │   ├── trend.py            # 趋势跟踪信号
│   │   │   ├── mean_reversion.py   # 均值回归信号
│   │   │   ├── arbitrage.py        # 套利信号
│   │   │   └── ml_signal.py        # ML信号
│   │   ├── portfolio/              # 组合构建
│   │   │   ├── __init__.py
│   │   │   ├── mean_variance.py    # 均值方差优化
│   │   │   └── risk_parity.py      # 风险平价
│   │   └── examples/               # 示例策略
│   │       ├── __init__.py
│   │       ├── dual_moving_average.py
│   │       ├── bollinger_reversal.py
│   │       ├── pairs_trading.py
│   │       └── lstm_predictor.py
│   ├── backtest/                   # 回测模块
│   │   ├── __init__.py
│   │   ├── engine.py               # 回测引擎
│   │   ├── executor.py             # 订单执行模拟
│   │   ├── analyzer.py             # 绩效分析
│   │   └── report.py               # 报告生成
│   ├── risk/                       # 风控模块
│   │   ├── __init__.py
│   │   ├── base.py                 # 风控基类
│   │   ├── position_manager.py     # 仓位管理
│   │   ├── stop_loss.py            # 止损管理
│   │   ├── risk_metrics.py         # 风险指标
│   │   └── circuit_breaker.py      # 熔断机制
│   ├── broker/                     # 交易接口模块
│   │   ├── __init__.py
│   │   ├── base.py                 # Broker抽象基类
│   │   ├── paper_broker.py         # 模拟交易
│   │   └── gateways/               # 券商网关
│   │       ├── __init__.py
│   │       ├── xtp_gateway.py      # XTP中泰
│   │       └── qmt_gateway.py      # 迅投QMT
│   └── monitor/                    # 监控模块
│       ├── __init__.py
│       ├── metrics.py              # 指标收集
│       ├── logger.py               # 日志系统
│       ├── alerts.py               # 告警系统
│       └── dashboard.py           # 仪表盘
├── scripts/                        # 实用脚本
│   ├── install.sh                  # 安装脚本
│   ├── run_backtest.py             # 运行回测
│   └── run_live.py                 # 启动实盘
└── tests/                          # 测试
    ├── test_data.py
    ├── test_strategy.py
    ├── test_backtest.py
    └── test_risk.py
```

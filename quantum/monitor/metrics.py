"""
系统监控 - 指标收集器
"""

import time
import threading
from collections import deque
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional, Callable
from dataclasses import dataclass, field


@dataclass
class SystemMetrics:
    """系统性能指标"""
    timestamp: datetime
    cpu_percent: float = 0.0
    memory_mb: float = 0.0
    memory_percent: float = 0.0
    uptime_seconds: float = 0.0
    active_strategies: int = 0
    active_positions: int = 0
    total_capital: float = 0.0
    daily_pnl: float = 0.0
    orders_last_minute: int = 0
    signals_last_minute: int = 0
    trades_today: int = 0
    broker_latency_ms: float = 0.0


@dataclass
class StrategyMetrics:
    """策略性能指标"""
    name: str
    timestamp: datetime
    total_signals: int = 0
    total_trades: int = 0
    win_rate: float = 0.0
    profit_factor: float = 0.0
    sharpe_ratio: float = 0.0
    max_drawdown: float = 0.0
    current_drawdown: float = 0.0
    total_return: float = 0.0
    last_signal_time: Optional[datetime] = None


class MetricsCollector:
    """
    指标收集器
    多线程安全，支持Prometheus/Grafana输出
    """

    def __init__(self, max_history: int = 10000):
        self.max_history = max_history
        self.system_metrics: deque = deque(maxlen=max_history)
        self.strategy_metrics: Dict[str, deque] = {}
        self._lock = threading.Lock()
        self._callbacks: List[Callable] = []
        self._last_minute_signals = deque(maxlen=1000)
        self._last_minute_orders = deque(maxlen=1000)
        self._start_time = time.time()

    def record_system(self, metrics: SystemMetrics):
        with self._lock:
            self.system_metrics.append(metrics)

    def record_strategy(self, name: str, metrics: StrategyMetrics):
        with self._lock:
            if name not in self.strategy_metrics:
                self.strategy_metrics[name] = deque(maxlen=self.max_history)
            self.strategy_metrics[name].append(metrics)

    def record_signal(self, strategy_name: str):
        now = time.time()
        with self._lock:
            self._last_minute_signals.append(now)

    def record_order(self):
        now = time.time()
        with self._lock:
            self._last_minute_orders.append(now)

    def get_system_snapshot(self) -> Optional[SystemMetrics]:
        with self._lock:
            if self.system_metrics:
                return self.system_metrics[-1]
            return None

    def get_strategy_snapshot(self, name: str) -> Optional[StrategyMetrics]:
        with self._lock:
            if name in self.strategy_metrics and self.strategy_metrics[name]:
                return self.strategy_metrics[name][-1]
            return None

    def get_latest_metrics(self) -> Dict[str, Any]:
        """获取最新指标汇总"""
        now = time.time()

        # 计算每分钟速率
        one_min_ago = now - 60
        with self._lock:
            signals_per_min = sum(1 for t in self._last_minute_signals if t > one_min_ago)
            orders_per_min = sum(1 for t in self._last_minute_orders if t > one_min_ago)

        sys_snap = self.get_system_snapshot()

        strategy_data = {}
        with self._lock:
            for name, metrics_q in self.strategy_metrics.items():
                if metrics_q:
                    strategy_data[name] = {
                        "total_signals": metrics_q[-1].total_signals,
                        "total_trades": metrics_q[-1].total_trades,
                        "win_rate": metrics_q[-1].win_rate,
                        "total_return": metrics_q[-1].total_return,
                    }

        return {
            "system": {
                "uptime_hours": (now - self._start_time) / 3600,
                "signals_per_min": signals_per_min,
                "orders_per_min": orders_per_min,
                "capital": sys_snap.total_capital if sys_snap else 0,
                "daily_pnl": sys_snap.daily_pnl if sys_snap else 0,
                "positions": sys_snap.active_positions if sys_snap else 0,
                "strategies": sys_snap.active_strategies if sys_snap else 0,
            },
            "strategies": strategy_data,
        }

    def register_callback(self, callback: Callable):
        """注册指标更新回调"""
        self._callbacks.append(callback)

    def get_uptime(self) -> timedelta:
        return timedelta(seconds=time.time() - self._start_time)

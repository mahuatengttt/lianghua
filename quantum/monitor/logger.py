"""
日志系统和告警系统
"""

import os
import json
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any, Callable
from enum import Enum
from loguru import logger


class AlertLevel(Enum):
    """告警级别"""
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class AlertChannel(Enum):
    """告警通道"""
    LOG = "log"
    CONSOLE = "console"
    CALLBACK = "callback"


class Alert:
    """告警对象"""

    def __init__(
        self,
        title: str,
        message: str,
        level: AlertLevel = AlertLevel.INFO,
        source: str = "system",
        metadata: Dict = None,
    ):
        self.title = title
        self.message = message
        self.level = level
        self.source = source
        self.metadata = metadata or {}
        self.timestamp = datetime.now()
        self.alert_id = f"alert_{self.timestamp.strftime('%H%M%S')}"

    def to_dict(self) -> Dict:
        return {
            "id": self.alert_id,
            "title": self.title,
            "message": self.message,
            "level": self.level.value,
            "source": self.source,
            "timestamp": self.timestamp.isoformat(),
            "metadata": self.metadata,
        }


class AlertManager:
    """
    告警管理器
    支持多通道分发，频率限制
    """

    def __init__(self, config: Dict[str, Any] = None):
        self.config = config or {}
        self.channels: List[AlertChannel] = [AlertChannel.LOG]
        self.callbacks: Dict[str, List[Callable]] = {
            "info": [], "warning": [], "error": [], "critical": [],
        }
        self._cooldowns: Dict[str, datetime] = {}
        self.default_cooldown = timedelta(seconds=self.config.get("cooldown_seconds", 60))
        self.alert_history: List[Alert] = []

    def add_channel(self, channel: AlertChannel):
        if channel not in self.channels:
            self.channels.append(channel)

    def on_alert(self, level: str, callback: Callable):
        """注册告警回调"""
        if level in self.callbacks:
            self.callbacks[level].append(callback)

    def send(
        self,
        title: str,
        message: str,
        level: AlertLevel = AlertLevel.INFO,
        source: str = "system",
        metadata: Dict = None,
        dedup_key: str = None,
    ):
        """发送告警"""
        # 去重
        if dedup_key:
            if dedup_key in self._cooldowns:
                if datetime.now() - self._cooldowns[dedup_key] < self.default_cooldown:
                    return
            self._cooldowns[dedup_key] = datetime.now()

        alert = Alert(title, message, level, source, metadata)
        self.alert_history.append(alert)

        # 日志通道
        if AlertChannel.LOG in self.channels:
            log_method = getattr(logger, level.value, logger.info)
            log_method(f"[{source}] {title}: {message}")

        # 回调通道
        if AlertChannel.CALLBACK in self.channels:
            for cb in self.callbacks.get(level.value, []):
                try:
                    cb(alert)
                except Exception as e:
                    logger.error(f"告警回调失败: {e}")

    def info(self, title: str, message: str, **kwargs):
        self.send(title, message, AlertLevel.INFO, **kwargs)

    def warning(self, title: str, message: str, **kwargs):
        self.send(title, message, AlertLevel.WARNING, **kwargs)

    def error(self, title: str, message: str, **kwargs):
        self.send(title, message, AlertLevel.ERROR, **kwargs)

    def critical(self, title: str, message: str, **kwargs):
        self.send(title, message, AlertLevel.CRITICAL, **kwargs)


def setup_quantum_logger(name: str = "quantum", log_dir: str = "./logs"):
    """配置量子系统日志"""
    os.makedirs(log_dir, exist_ok=True)

    # 移除默认处理器
    logger.remove()

    # 控制台输出
    logger.add(
        lambda msg: print(msg),
        format="<green>{time:HH:mm:ss.SSS}</green> | <level>{level:8}</level> | <cyan>{name}</cyan> | <level>{message}</level>",
        level="INFO",
        colorize=True,
    )

    # 文件输出（按天轮转）
    logger.add(
        os.path.join(log_dir, "quantum_{time:YYYY-MM-DD}.log"),
        format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level:8} | {name} | {message}",
        level="DEBUG",
        rotation="00:00",
        retention="30 days",
        compression="gz",
    )

    # 错误日志独立文件
    logger.add(
        os.path.join(log_dir, "error_{time:YYYY-MM-DD}.log"),
        format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level:8} | {name} | {message}",
        level="ERROR",
        rotation="00:00",
        retention="90 days",
    )

    return logger

"""
本地文件数据源 & Parquet/SQLite存储实现
"""

import os
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Dict, Any

import pandas as pd

from ..common.models import Bar, Tick
from ..common.enums import TimeFrame
from ..common.exceptions import DataSourceError, StorageError
from .base import DataSource, DataStore


class LocalFileDataSource(DataSource):
    """本地CSV/Parquet文件数据源"""

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.data_dir = Path(config.get("data_dir", "./data"))
        self.format = config.get("format", "parquet")

    def get_bars(
        self,
        symbol: str,
        start: datetime,
        end: datetime,
        timeframe: TimeFrame = TimeFrame.DAILY,
        **kwargs
    ) -> List[Bar]:
        file_path = self.data_dir / f"{symbol}_{timeframe.value}.{self.format}"
        if not file_path.exists():
            return []

        try:
            if self.format == "parquet":
                df = pd.read_parquet(file_path)
            else:
                df = pd.read_csv(file_path, parse_dates=["time"])

            df = df[(df["time"] >= start) & (df["time"] <= end)]
            bars = []
            for _, row in df.iterrows():
                bars.append(Bar(**row.to_dict()))
            return bars
        except Exception as e:
            raise DataSourceError(f"读取本地文件失败: {e}")

    def get_tick(self, symbol: str, date: datetime) -> List[Tick]:
        return []

    def get_realtime_bar(self, symbol: str, timeframe: TimeFrame = TimeFrame.MIN1) -> Optional[Bar]:
        return None


class ParquetStore(DataStore):
    """Parquet 格式数据存储"""

    def __init__(self, data_dir: str = "./data/parquet"):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)

    def save(self, bars: List[Bar]) -> None:
        if not bars:
            return
        symbol = bars[0].symbol
        timeframe = bars[0].timeframe
        file_path = self.data_dir / f"{symbol}_{timeframe.value}.parquet"

        df_new = pd.DataFrame([b.model_dump() for b in bars])

        if file_path.exists():
            df_existing = pd.read_parquet(file_path)
            df_combined = pd.concat([df_existing, df_new])
            df_combined = df_combined.drop_duplicates(subset=["symbol", "time", "timeframe"])
            df_combined = df_combined.sort_values("time").reset_index(drop=True)
        else:
            df_combined = df_new

        df_combined.to_parquet(file_path, index=False)

    def load(
        self, symbol: str, start: datetime, end: datetime, timeframe: TimeFrame
    ) -> Optional[List[Bar]]:
        file_path = self.data_dir / f"{symbol}_{timeframe.value}.parquet"
        if not file_path.exists():
            return None

        df = pd.read_parquet(file_path)
        df = df[(df["time"] >= pd.Timestamp(start)) & (df["time"] <= pd.Timestamp(end))]

        if df.empty:
            return None

        return [Bar(**row) for row in df.to_dict(orient="records")]

    def exists(self, symbol: str, timeframe: TimeFrame) -> bool:
        file_path = self.data_dir / f"{symbol}_{timeframe.value}.parquet"
        return file_path.exists()


class SQLiteStore(DataStore):
    """SQLite 数据存储"""

    def __init__(self, db_path: str = "./data/quantum.db"):
        self.db_path = db_path
        self._init_db()

    def _get_connection(self):
        import sqlite3
        return sqlite3.connect(self.db_path)

    def _init_db(self):
        with self._get_connection() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS bars (
                    symbol TEXT NOT NULL,
                    time TEXT NOT NULL,
                    timeframe TEXT NOT NULL,
                    open REAL,
                    high REAL,
                    low REAL,
                    close REAL,
                    volume REAL,
                    amount REAL,
                    open_interest REAL,
                    PRIMARY KEY (symbol, time, timeframe)
                )
            """)
            conn.commit()

    def save(self, bars: List[Bar]) -> None:
        if not bars:
            return
        with self._get_connection() as conn:
            data = [
                (b.symbol, b.time.isoformat(), b.timeframe.value,
                 b.open, b.high, b.low, b.close, b.volume, b.amount, b.open_interest)
                for b in bars
            ]
            conn.executemany("""
                INSERT OR REPLACE INTO bars
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, data)
            conn.commit()

    def load(
        self, symbol: str, start: datetime, end: datetime, timeframe: TimeFrame
    ) -> Optional[List[Bar]]:
        with self._get_connection() as conn:
            cursor = conn.execute(
                """SELECT * FROM bars
                   WHERE symbol = ? AND timeframe = ?
                   AND time >= ? AND time <= ?
                   ORDER BY time ASC""",
                (symbol, timeframe.value, start.isoformat(), end.isoformat())
            )
            rows = cursor.fetchall()
            if not rows:
                return None
            return [Bar(
                symbol=r[0], time=datetime.fromisoformat(r[1]),
                timeframe=TimeFrame(r[2]),
                open=r[3], high=r[4], low=r[5], close=r[6],
                volume=r[7], amount=r[8], open_interest=r[9],
            ) for r in rows]

    def exists(self, symbol: str, timeframe: TimeFrame) -> bool:
        with self._get_connection() as conn:
            cursor = conn.execute(
                "SELECT COUNT(*) FROM bars WHERE symbol = ? AND timeframe = ?",
                (symbol, timeframe.value)
            )
            return cursor.fetchone()[0] > 0

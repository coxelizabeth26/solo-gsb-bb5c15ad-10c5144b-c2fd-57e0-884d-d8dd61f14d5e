"""SQLite 存储层。

表结构:
- seq         INTEGER PRIMARY KEY AUTOINCREMENT  —— 首次接收顺序(到达即分配,单调递增)
- event_id    TEXT UNIQUE                        —— 幂等键,重复 event_id 不会产生新记录
- occurred_at TEXT                               —— 事件发生时间,统一归一化为 UTC ISO-8601 字符串
- received_at TEXT                               —— 服务端接收时间,UTC ISO-8601
- payload     TEXT                               —— 原始 JSON 负载

时间统一存为同一偏移(+00:00)的 ISO-8601 字符串,可直接用字符串比较做范围查询。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    seq         INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id    TEXT NOT NULL UNIQUE,
    occurred_at TEXT NOT NULL,
    received_at TEXT NOT NULL,
    payload     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_occurred_at ON events (occurred_at, seq);
"""


def connect(db_path: str) -> sqlite3.Connection:
    """打开一个连接。每次请求独立连接,配合 WAL 模式支持并发读写。"""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def init_db(db_path: str) -> None:
    if db_path != ":memory:":
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = connect(db_path)
    try:
        conn.executescript(SCHEMA)
        conn.commit()
    finally:
        conn.close()

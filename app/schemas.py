"""Pydantic 请求/响应模型。"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class EventIn(BaseModel):
    """客户端上报的事件。occurred_at 为无时区信息时按 UTC 处理。"""

    event_id: str = Field(min_length=1, max_length=128)
    occurred_at: datetime
    payload: dict[str, Any] = Field(default_factory=dict)


class EventAccepted(BaseModel):
    """接收结果。created=False 表示该 event_id 已存在,本次为幂等命中。"""

    event_id: str
    seq: int
    created: bool


class EventRecord(BaseModel):
    seq: int
    event_id: str
    occurred_at: datetime
    received_at: datetime
    payload: dict[str, Any]


class EventPage(BaseModel):
    items: list[EventRecord]
    total: int
    limit: int
    offset: int


class ReplayRequest(BaseModel):
    event_ids: list[str] = Field(min_length=1, max_length=1000)


class ReplayItem(BaseModel):
    event_id: str
    status: Literal["success", "skipped"]
    seq: int | None = None
    occurred_at: datetime | None = None
    payload: dict[str, Any] | None = None


class ReplayResponse(BaseModel):
    results: list[ReplayItem]
    succeeded: int
    skipped: int

"""FastAPI 应用:Webhook 事件接收与重放。"""

from __future__ import annotations

import json
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException, Query, Response
from fastapi.concurrency import run_in_threadpool

from .db import connect, init_db
from .schemas import (
    EventAccepted,
    EventIn,
    EventPage,
    EventRecord,
    ReplayItem,
    ReplayRequest,
    ReplayResponse,
)

DEFAULT_DB_PATH = os.environ.get("EVENTS_DB", "events.db")


def _utc_iso(dt: datetime) -> str:
    """归一化为 UTC ISO-8601;无时区信息按 UTC 处理。"""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


def _row_to_record(row) -> EventRecord:
    return EventRecord(
        seq=row["seq"],
        event_id=row["event_id"],
        occurred_at=row["occurred_at"],
        received_at=row["received_at"],
        payload=json.loads(row["payload"]),
    )


def create_app(db_path: str = DEFAULT_DB_PATH) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        init_db(db_path)
        yield

    app = FastAPI(title="Webhook Event Ingest & Replay", version="1.0.0", lifespan=lifespan)

    @app.get("/healthz")
    async def healthz():
        return {"status": "ok"}

    @app.post("/events", response_model=EventAccepted)
    async def ingest(event: EventIn, response: Response):
        """幂等接收事件。event_id 已存在时不产生新记录,返回首次接收的 seq。"""

        def _insert() -> EventAccepted:
            received_at = datetime.now(timezone.utc).isoformat()
            conn = connect(db_path)
            try:
                cur = conn.execute(
                    "INSERT OR IGNORE INTO events (event_id, occurred_at, received_at, payload)"
                    " VALUES (?, ?, ?, ?)",
                    (
                        event.event_id,
                        _utc_iso(event.occurred_at),
                        received_at,
                        json.dumps(event.payload, ensure_ascii=False),
                    ),
                )
                conn.commit()
                if cur.rowcount == 1:
                    return EventAccepted(event_id=event.event_id, seq=cur.lastrowid, created=True)
                row = conn.execute(
                    "SELECT seq FROM events WHERE event_id = ?", (event.event_id,)
                ).fetchone()
                return EventAccepted(event_id=event.event_id, seq=row["seq"], created=False)
            finally:
                conn.close()

        result = await run_in_threadpool(_insert)
        response.status_code = 201 if result.created else 200
        return result

    @app.get("/events", response_model=EventPage)
    async def list_events(
        start: datetime | None = Query(default=None, description="occurred_at 下界(含)"),
        end: datetime | None = Query(default=None, description="occurred_at 上界(含)"),
        limit: int = Query(default=50, ge=1, le=500),
        offset: int = Query(default=0, ge=0),
    ):
        """按 occurred_at 时间范围分页查询,按发生时间(同刻按接收顺序)升序返回。"""
        if start is not None and end is not None and start > end:
            raise HTTPException(status_code=422, detail="start must not be after end")

        def _query() -> EventPage:
            where, params = [], []
            if start is not None:
                where.append("occurred_at >= ?")
                params.append(_utc_iso(start))
            if end is not None:
                where.append("occurred_at <= ?")
                params.append(_utc_iso(end))
            clause = f"WHERE {' AND '.join(where)}" if where else ""

            conn = connect(db_path)
            try:
                total = conn.execute(f"SELECT COUNT(*) AS c FROM events {clause}", params).fetchone()["c"]
                rows = conn.execute(
                    f"SELECT * FROM events {clause} ORDER BY occurred_at, seq LIMIT ? OFFSET ?",
                    (*params, limit, offset),
                ).fetchall()
                return EventPage(
                    items=[_row_to_record(r) for r in rows],
                    total=total,
                    limit=limit,
                    offset=offset,
                )
            finally:
                conn.close()

        return await run_in_threadpool(_query)

    @app.post("/replay", response_model=ReplayResponse)
    async def replay(req: ReplayRequest):
        """批量重放:按首次接收顺序(seq 升序)输出已存在的事件;未知 event_id 标记 skipped。

        重放只读取数据,不写入任何记录,重复调用结果一致。
        """

        def _replay() -> ReplayResponse:
            # 请求内去重,保证同一 event_id 只出现一次
            unique_ids = list(dict.fromkeys(req.event_ids))
            placeholders = ",".join("?" for _ in unique_ids)
            conn = connect(db_path)
            try:
                rows = conn.execute(
                    f"SELECT * FROM events WHERE event_id IN ({placeholders}) ORDER BY seq",
                    unique_ids,
                ).fetchall()
            finally:
                conn.close()

            found_ids = {row["event_id"] for row in rows}
            results: list[ReplayItem] = [
                ReplayItem(
                    event_id=row["event_id"],
                    status="success",
                    seq=row["seq"],
                    occurred_at=row["occurred_at"],
                    payload=json.loads(row["payload"]),
                )
                for row in rows  # 已按 seq(首次接收顺序)升序
            ]
            results.extend(
                ReplayItem(event_id=eid, status="skipped")
                for eid in unique_ids
                if eid not in found_ids
            )
            skipped = len(unique_ids) - len(found_ids)
            return ReplayResponse(
                results=results,
                succeeded=len(found_ids),
                skipped=skipped,
            )

        return await run_in_threadpool(_replay)

    return app


app = create_app()

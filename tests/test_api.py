import pytest
from fastapi.testclient import TestClient

from app.main import create_app


@pytest.fixture()
def client(tmp_path):
    app = create_app(str(tmp_path / "test.db"))
    with TestClient(app) as c:
        yield c


def make_event(event_id, occurred_at, **payload):
    return {"event_id": event_id, "occurred_at": occurred_at, "payload": payload}


def test_ingest_creates_record(client):
    r = client.post("/events", json=make_event("e1", "2026-09-18T10:00:00Z", a=1))
    assert r.status_code == 201
    body = r.json()
    assert body == {"event_id": "e1", "seq": 1, "created": True}


def test_ingest_is_idempotent(client):
    event = make_event("e1", "2026-09-18T10:00:00Z", a=1)
    assert client.post("/events", json=event).status_code == 201

    r = client.post("/events", json=event)
    assert r.status_code == 200
    assert r.json() == {"event_id": "e1", "seq": 1, "created": False}

    # 重复请求不产生重复记录
    page = client.get("/events").json()
    assert page["total"] == 1


def test_ingest_seq_tracks_arrival_order(client):
    client.post("/events", json=make_event("b", "2026-09-18T09:00:00Z"))
    client.post("/events", json=make_event("a", "2026-09-18T10:00:00Z"))
    page = client.get("/events").json()
    seqs = {item["event_id"]: item["seq"] for item in page["items"]}
    assert seqs == {"b": 1, "a": 2}


def test_list_events_time_range_and_pagination(client):
    for i, ts in enumerate(
        ["2026-09-18T08:00:00Z", "2026-09-18T09:00:00Z", "2026-09-18T10:00:00Z"]
    ):
        client.post("/events", json=make_event(f"e{i}", ts))

    r = client.get(
        "/events",
        params={"start": "2026-09-18T08:30:00Z", "end": "2026-09-18T10:00:00Z"},
    )
    assert r.status_code == 200
    page = r.json()
    assert page["total"] == 2
    assert [i["event_id"] for i in page["items"]] == ["e1", "e2"]

    # 分页
    page1 = client.get("/events", params={"limit": 2, "offset": 0}).json()
    page2 = client.get("/events", params={"limit": 2, "offset": 2}).json()
    assert page1["total"] == 3 and len(page1["items"]) == 2
    assert len(page2["items"]) == 1


def test_list_events_rejects_inverted_range(client):
    r = client.get(
        "/events",
        params={"start": "2026-09-18T10:00:00Z", "end": "2026-09-18T09:00:00Z"},
    )
    assert r.status_code == 422


def test_naive_occurred_at_treated_as_utc(client):
    client.post("/events", json=make_event("e1", "2026-09-18T10:00:00"))
    item = client.get("/events").json()["items"][0]
    assert item["occurred_at"] in ("2026-09-18T10:00:00Z", "2026-09-18T10:00:00+00:00")


def test_replay_outputs_in_first_received_order(client):
    # 乱序上报,接收顺序为 c, a, b
    for eid in ["c", "a", "b"]:
        client.post("/events", json=make_event(eid, "2026-09-18T10:00:00Z"))

    r = client.post("/replay", json={"event_ids": ["b", "missing", "a", "c"]})
    assert r.status_code == 200
    body = r.json()
    assert body["succeeded"] == 3
    assert body["skipped"] == 1
    # 已存在的事件按首次接收顺序输出,未知 id 标记 skipped
    assert [(i["event_id"], i["status"]) for i in body["results"]] == [
        ("c", "success"),
        ("a", "success"),
        ("b", "success"),
        ("missing", "skipped"),
    ]
    assert body["results"][0]["payload"] is not None
    assert body["results"][3]["seq"] is None


def test_replay_does_not_create_records(client):
    client.post("/events", json=make_event("e1", "2026-09-18T10:00:00Z"))
    client.post("/replay", json={"event_ids": ["e1", "ghost"]})
    client.post("/replay", json={"event_ids": ["e1", "ghost"]})
    assert client.get("/events").json()["total"] == 1


def test_replay_dedupes_request_ids(client):
    client.post("/events", json=make_event("e1", "2026-09-18T10:00:00Z"))
    body = client.post("/replay", json={"event_ids": ["e1", "e1"]}).json()
    assert body["succeeded"] == 1
    assert len(body["results"]) == 1

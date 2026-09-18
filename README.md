# Webhook 事件接收与重放 API

基于 FastAPI + SQLite 的 Webhook 事件服务:按 `event_id` 幂等接收事件并记录首次接收顺序,支持按时间范围分页查询和按事件批量重放。

## 一键启动(Docker)

```bash
docker build -t webhook-events . && docker run --rm -p 8000:8000 webhook-events
```

服务监听端口:**8000**(宿主机) → 8000(容器)。交互式文档:http://localhost:8000/docs

数据持久化(可选):挂载卷到 `/data`,数据库文件为 `/data/events.db`:

```bash
docker run --rm -p 8000:8000 -v webhook-data:/data webhook-events
```

本地开发(无需 Docker):

```bash
pip install -r requirements.txt
uvicorn app.main:app --port 8000
```

## API 与示例 curl

### 1. 接收事件 `POST /events`

幂等:`event_id` 为唯一键,重复上报返回 `200` + `created: false` 及首次接收的 `seq`,**不会产生重复记录**;首次创建返回 `201`。`occurred_at` 无时区时按 UTC 处理。

```bash
curl -i -X POST localhost:8000/events \
  -H 'Content-Type: application/json' \
  -d '{"event_id":"evt-001","occurred_at":"2026-09-18T10:00:00Z","payload":{"user":"alice","amount":99.5}}'
```

```json
{"event_id":"evt-001","seq":1,"created":true}
```

### 2. 按时间范围分页查询 `GET /events`

参数:`start` / `end`(对 `occurred_at` 闭区间过滤,ISO-8601)、`limit`(1–500,默认 50)、`offset`(默认 0)。按发生时间升序(同刻按接收顺序)返回,`total` 为范围内总数。

```bash
curl "localhost:8000/events?start=2026-09-18T10:00:00Z&end=2026-09-18T12:00:00Z&limit=50&offset=0"
```

```json
{"items":[{"seq":1,"event_id":"evt-001","occurred_at":"2026-09-18T10:00:00Z","received_at":"2026-09-18T10:00:01.123456Z","payload":{"user":"alice","amount":99.5}}],"total":1,"limit":50,"offset":0}
```

### 3. 批量重放 `POST /replay`

按**首次接收顺序**(`seq` 升序)输出命中的事件,与请求中 id 的顺序无关;未知 `event_id` 标记 `skipped`。重放只读不写,重复调用结果一致。

```bash
curl -X POST localhost:8000/replay \
  -H 'Content-Type: application/json' \
  -d '{"event_ids":["evt-003","evt-404","evt-001"]}'
```

```json
{
  "results":[
    {"event_id":"evt-001","status":"success","seq":1,"occurred_at":"2026-09-18T10:00:00Z","payload":{"user":"alice","amount":99.5}},
    {"event_id":"evt-404","status":"skipped","seq":null,"occurred_at":null,"payload":null}
  ],
  "succeeded":1,
  "skipped":1
}
```

### 健康检查 `GET /healthz`

返回 `{"status":"ok"}`。

## 设计要点

- **幂等**:`event_id` 为 SQLite `UNIQUE` 键,写入用 `INSERT OR IGNORE`,重复请求(含并发)只保留首条记录。
- **接收顺序**:`seq INTEGER PRIMARY KEY AUTOINCREMENT`,到达即分配,单调递增,即首次接收顺序。
- **时间存储**:`occurred_at` 统一归一化为 UTC ISO-8601 字符串,范围查询直接按字符串比较,建有 `(occurred_at, seq)` 索引。
- **并发**:每请求独立 SQLite 连接 + WAL 模式;数据库路径可用环境变量 `EVENTS_DB` 覆盖(容器内默认 `/data/events.db`)。

## 测试

```bash
pip install -r requirements-dev.txt
pytest tests/ -q
```

# Change: 测试库预置大数据模式（异步批量执行）

## Why

同步预置在万行以上会长时间占用 HTTP 请求，整表物化与单次 `executemany` 也会放大内存。模型字段、状态枚举、`bulk.py`、`iter_rows` 与延迟索引能力已经存在，但没有执行路径消费，导致配置与代码都是半成品。

## What Changes

- 总行数超过 `bulk_threshold` 时自动转异步：HTTP 只落 `PENDING` 记录，由独立 worker 执行
- 排队时固定快照与随机种子；提供进度、心跳、取消与失败/崩溃清理
- PostgreSQL 使用 `COPY FROM STDIN`，MySQL 优先 `LOAD DATA LOCAL INFILE`，索引与外键在数据写入后创建
- 新增取消接口，前端增加模式提示、进度列、轮询与取消入口
- 修正 `patch.sql` 中 `status` 默认值与模型、`pg_struct.sql` 不一致的问题

## Impact

- Affected specs: `test-db-provisioning`
- Affected code:
  - `src/apps/provisioning/`：`services.py`、`runner.py`（新增）、`bulk.py`、`data.py`、`models.py`、`views.py`、`serializers.py`
  - `src/jobs/provisioning_worker.py`（新增）、`src/start.sh`、`src/settings/gunicorn.py`
  - `src/config/conf.ini`、`src/sql/patch.sql`
  - `static/src/views/provisioning/index.vue`、`static/src/api/provisioning.js`
- 非破坏性：普通模式接口的入参与返回保持兼容，新增字段均为可选
- 归档顺序：先归档 `add-test-db-provisioning`，再归档本变更

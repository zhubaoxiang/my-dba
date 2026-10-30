# Change: 增加模型调用用量与耗时统计

## Why

模型调用（对话与嵌入）是目前唯一按次产生外部成本与延迟的环节，但系统没有任何记录：管理员无法回答「谁在用、用在哪、慢在哪、失败多少」，出问题时只能翻日志。首页总览展示了业务状态，却不覆盖模型这一层。

## What Changes

- 新增 `llm_usage` 表：每次模型调用记录一行——用户、功能、调用类型、模型、token、耗时、成功/失败、`request_id`、时间。
- 在 `build_chat_model()` 上挂 LangChain callback，并包装 `embed_documents` / `embed_query`，业务调用点零改动。
- 新增中间件按 URL 前缀映射功能，用 contextvars 传递 `username` / `feature` / `request_id`；后台任务记为 `system`。
- 新增 `GET /v1/llm-usage/summary`（汇总与分组）与 `GET /v1/llm-usage/calls`（明细分页），仅超级管理员可用。
- 记录保留 90 天，清理挂在现有 `jobs/metrics.py` 循环里执行（按项目规则软删除）。
- 前端 `/system` 占位页替换为带标签页的系统管理页，「模型用量」为第一个标签；不新增图表依赖，趋势先用 CSS 条形。
- **不记录** prompt 与回答正文；网关未返回 usage 时 token 留空，不估算。

## Impact

- Affected specs: `llm-usage`（新增）
- Affected code:
  - `src/apps/llmusage/`（模型、服务、中间件、序列化器、视图）
  - `src/apps/knowledge/llm.py`（挂 callback、包装嵌入）
  - `src/utils/custom_enum.py`（功能与调用类型枚举）
  - `src/sql/pg_struct.sql`（`llm_usage` 表）
  - `src/config/urls.py`（路由注册）、`settings/settings.py`（中间件）
  - `src/jobs/metrics.py`（保留期清理）
  - `static/src/views/system/`、`static/src/api/llmUsage.js`、`static/src/router/index.js`
  - `docs/architecture.md`、`docs/knowledge-qa.md`（模块与页面说明）

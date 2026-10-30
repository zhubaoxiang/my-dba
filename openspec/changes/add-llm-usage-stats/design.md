## Context

- 模型调用集中在 `apps/knowledge/llm.py`：`build_chat_model()` 被 SQL 解读、NL2SQL、问答 agent 复用；嵌入走 `embed_documents` / `embed_query`。
- 问答支持流式与 agent 多步工具调用，一次用户请求可能触发多次模型调用。
- 网关为 OpenAI 兼容端点，非流式响应通常带 usage；流式是否返回 usage 取决于网关，不能假定。
- 项目禁止 Django migration，DDL 写 `src/sql/pg_struct.sql`；模型必须继承 `AbstractTimeFiledModel`，删除一律软删除。
- 前端未引入图表库，`/system` 目前是 `views/development/index.vue` 占位页。

## Goals / Non-Goals

- Goals:
  - 管理员可按时间、用户、功能、模型查看调用次数、成功率、token 与平均耗时
  - 能下钻到单次失败调用与慢调用
  - 埋点不改变任何业务调用点，不阻断业务
- Non-Goals:
  - 不做 token 到金额的换算（价格随网关与模型变化）
  - 不记录 prompt / 回答正文
  - 不做实时告警、导出报表、按数据源或知识库维度统计
  - 不引入异步任务框架

## Decisions

### D1：原始记录 + 查询时聚合

每次调用写一行 `llm_usage`，查询时按时间范围 GROUP BY。相比按天预聚合，保留单次明细才能排查「哪一次慢、为什么失败」；内网规模下写入与存储成本可接受。

### D2：埋点收敛在 `llm.py`

- 对话：`build_chat_model()` 返回的模型实例挂一个 callbacks handler，`on_llm_end` 读取 `usage_metadata` 与耗时，`on_llm_error` 记录失败。agent 内部的多步调用与流式调用都会经过该 handler。
- 嵌入：在 `embed_documents` / `embed_query` 外层计时并记录，`units` 记文本块数。
- 记录动作调用 `apps/llmusage/services.py` 的 `record_call()`；跨模块只依赖对方的服务函数，不直接操作其 queryset。

### D3：归属信息用中间件 + contextvars

新增中间件：从 `request.user` 取 `username`，按 URL 前缀映射功能（`qa-session`→问答、`nl2sql`→NL2SQL、`sql-analysis`→SQL 解读、`ddl-review`→DDL 评审、`kb-document`→知识库摄入……），每个请求生成一个 `request_id`，写入 contextvars。未匹配的路径记为 `other` 并打日志，便于发现漏配。

后台任务没有请求上下文，`username` 记为 `system`，`feature` 按调用方显式传入或记为 `system`。映射表集中在中间件一处，业务调用点不用改。

### D4：token 可空，不估算

`prompt_tokens` / `completion_tokens` / `total_tokens` 均可空。流式调用或网关不返回 usage 时留 NULL，页面标注「该期间部分调用未返回用量」。估算值会污染成本判断，明确不做。

### D5：失败调用也记录，埋点失败吞异常

- 超时、连接失败、模型报错都写一行 `success=false`，`error_code` 记分类（`timeout` / `connection` / `bad_request` / `model_error`），不存原始堆栈。
- 记录写库失败只打日志，绝不向业务调用方抛异常——观测能力不能成为新的故障点。

### D6：新模块 `apps/llmusage`，接口 `SuperUserView`

- `GET /v1/llm-usage/summary?range=today|7d|30d&group_by=user|model|feature`：返回汇总卡片、按天趋势、分组行。
- `GET /v1/llm-usage/calls?range=...&success=&min_latency=&page=`：明细分页。
- 两接口仅超级管理员可用，普通用户返回 4005；聚合查询失败只把对应块标为不可用，不整页报错。

### D7：保留 90 天，按项目规则软删除

清理任务随 `jobs/metrics.py` 的常驻循环每天执行一次，把超期记录置 `is_deleted=True`；所有查询过滤 `is_deleted=False`。项目规则禁止物理删除，因此不写 DELETE。若未来量级成为问题，另行评审归档策略。

### D8：前端 `/system` 改为标签页

`/system` 保留 `adminOnly`，把占位页替换为 `el-tabs`，「模型用量」是第一个标签。页面包含时间范围、四张汇总卡片、按天趋势（CSS 条形）、按用户/模型/功能切换的分组表、失败与慢调用明细。不新增 echarts 依赖。

## Risks / Trade-offs

- **token 缺失**：流式调用可能拿不到 usage → 页面明确标注，不估算。
- **表增长**：每次调用一行，90 天软删除 → 内网规模可接受；`(create_time)`、`(username, create_time)`、`(feature, create_time)` 建索引。
- **中间件漏配新路由**：未匹配记 `other` 并打日志，测试覆盖现有全部 LLM 入口。
- **请求路径增加一次写库**：单条 INSERT，埋点失败吞掉，不影响响应。
- **用户改名或删除**：记录保存 `username` 快照，不建外键，历史数据仍可读。

## Migration Plan

- 在 `pg_struct.sql` 追加 `llm_usage` 建表语句，部署时执行增量 DDL。
- 无业务数据迁移；上线后从第一次模型调用开始产生数据。
- 回滚：删除表、回退代码；统计缺失不影响任何业务功能。

## Open Questions

无。

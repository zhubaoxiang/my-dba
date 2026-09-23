# Change: 知识问答（RAG + 通用大模型）

## Why

路线图里的第 2 项能力。使用者的诉求是「这个报错 / 概念 / 用法是什么？」——写代码或测功能时遇到不确定的数据库知识，现在只能自己翻文档或搜索。

本 change 提供问答入口：**优先从已摄入的文档知识库检索并据其作答（RAG）**；知识库未配置或检索无命中时**自动回退到通用大模型**，使用者无感。模型一律通过 API 接入。

## What Changes

- 新增**模型接入配置**：可配置多个 provider（OpenAI 兼容接口），含 base_url、对话模型名、嵌入模型名；API Key 可逆加密存储（复用 `utils/crypto.py`）
- 新增**文档知识库**：上传 PDF / Markdown / 纯文本 / 网页链接，切分后经嵌入模型向量化写入独立的 **Qdrant** 服务；支持多知识库、启用停用。**块正文存在库内**，Qdrant 只存向量与检索过滤字段（design.md D1b）
- 新增**向量检索**：top-k + 相似度阈值，低于阈值视为「无命中」；命中后按块 id 回库取正文
- 新增**问答会话**：多轮对话，历史持久化（会话 + 消息两张表）
- 新增**agent 编排**（langchain + langgraph，单 agent + 工具调用）：工具含 `search_knowledge_base`（检索知识库）与 `get_table_schema`（查已纳管数据源的表结构，复用 `metadata_snapshot`）
- 新增**回答模式**：默认自动（检索无命中则回退通用模型），允许强制指定「只用知识库」或「只问模型」；回答中标注实际使用的来源
- 新增前端问答页与知识库管理页
- 新增依赖：`langchain`、`langgraph`（会顶替 `requests` / `urllib3` / `idna` 的版本，见 design.md D7）

## Non-Goals

- **不做流式输出**：沿用项目统一响应格式 `{code, message, data}` 一次性返回。流式需要 SSE 且与统一响应格式冲突，另开 change
- 不做多知识库的自动路由（由使用者显式选择检索哪个库）
- 不做 RAG 评测集与自动化质量评分
- 不做模型微调、本地方案部署（模型一律走 API）
- 不把已纳管库的元数据整体入向量库——元数据通过 agent 工具实时查询（见 design.md D4）
- 不做多 agent / 规划-执行-反思等复杂编排

## 前置条件

**需要一个可用的 Qdrant 服务**：在 docker compose 中增加一个 Qdrant 容器与数据卷。单容器、零外部依赖，不引入 etcd/MinIO 之类要单独伺候的组件。

> 初版方案是「用 pgvector 复用现有 PG」，实施中被推翻：目标 PG 跑在 Alpine 容器（musl libc、uid 70），推 pgvector 的过程先是换镜像导致数据目录属主不匹配、**PostgreSQL 启动失败**，继而又暴露出 musl→glibc 会让排序规则**静默改变**（`datcollversion` 为 NULL，PG 不报 collation mismatch 警告）从而可能使文本索引失效。根因是「把向量能力塞进承载全部业务的生产关系库」。完整取舍见 design.md D1。

## Impact

- 受影响能力：**新增** `llm-provider`、`knowledge-base`、`knowledge-qa`；**修改** `datasource-management`（顺带修正基线里一处已失实的鉴权需求，见 design.md D8）
- 受影响代码：
  - 新增 `src/apps/knowledge/`（`models.py` 模型配置与知识库；`qa/` 问答子模块含 langgraph 图与工具；`ingest.py` 文档摄入；`retrieval.py` 向量检索；`serializers.py` / `views.py` / `services.py`）
  - `src/utils/crypto.py` 复用（不修改）
  - `src/config/conf.ini` 增加调用侧参数（超时、top-k 默认值等），**不含 API Key**
  - `src/sql/pg_struct.sql` 新增建表；`src/sql/patch.sql` 增加增量变更集；`CREATE EXTENSION vector` 写入 pg_struct.sql 并由 patch.sql 提供已有库的补丁
  - `src/settings/settings.py`：`INSTALLED_APPS` **不需要改**（单一 `apps` app）；`src/config/urls.py` 注册新路由
  - `src/requirements.txt`：新增 `langchain` / `langgraph`
  - 新增 `static/src/views/knowledge/*`、`static/src/api/knowledge.js`、路由注册
- **风险**：新增 37 个第三方包，并顶替 `requests`(2.24.0→2.34.2) / `urllib3`(1.25.11→2.8.0，跨大版本) / `idna`。经核查，`requests`/`urllib3` 目前**只被已标注为未使用的平台集成脚本引用**，无活代码调用，升级风险低。详见 design.md D7
- **部署影响**：模型走公有云 API，而服务部署在内网——**需要出网通道**，否则问答功能不可用。见 design.md D9

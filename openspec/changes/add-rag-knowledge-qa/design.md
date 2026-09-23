## Context

前置状态：

- 项目为「我的数据库管理专家」，**面向开发与测试人员**（不是 DBA 工具）。已实现能力：数据源纳管 + 库表元数据采集 + 健康问题分析
- 已归档能力见 `openspec/specs/`：`datasource-management`（5 条需求）、`metadata-catalog`（9 条需求）
- 另一个进行中的 change `add-analysis-rule-registry`（18/59）也在动 `apps/datasource`，两者不冲突但需注意 tasks 顺序
- 部署方式为 docker compose，服务部署在**内网**
- 无应用层鉴权，访问控制依赖网络隔离

已确定的外部决策：向量存储用**独立的 Qdrant 服务**（初版的 pgvector 方案在实施中被推翻，原因见 D1）；LLM 走公有云 API；知识来源为**外部文档**；问答模式为**默认自动回退 + 可强制**；langgraph 用到**单 agent + 工具调用**；块正文存库内、Qdrant 只做可重建索引（D1b）。

**实施期的环境事实**（供后续判断参考）：目标 PG 为 PostgreSQL 17.11，跑在 **Alpine 容器**（musl libc、postgres 用户 uid 70）；`pg_available_extensions` 中**没有 `vector`**，只有 `pg_trgm` 与 `unaccent`；库的 `datcollate = en_US.utf8` 但 `datcollversion` 为 **NULL**。

## Goals / Non-Goals

- Goals：让使用者能就数据库知识提问，优先据已有文档作答并可溯源；无知识库时仍可用；模型与知识库可运维、可切换
- Non-Goals：不做流式输出、不做多 agent 编排、不做 RAG 评测集、不做本地模型、不把元数据入向量库

## Decisions

### D1: 向量存储用独立的 Qdrant 服务

- **Decision**：向量存入独立部署的 **Qdrant** 服务（docker compose 增加一个容器），库内不再存任何向量。
- **背景（初版方案的推翻过程）**：初版决策是「用 pgvector 复用现有 PG」。实施时目标 PG 上缺 `vector` 扩展包，推动安装的过程暴露了两个真实问题：
  1. 该实例跑在 **Alpine 容器**（postgres 用户 uid **70**），而官方 pgvector 镜像基于 Debian（uid **999**）。换镜像后数据目录属主不匹配，**PostgreSQL 直接启动失败**——端口能连上但对任何输入都不作答
  2. Alpine 的 **musl 不实现 locale**：库虽记录 `datcollate = en_US.utf8`，实际按 C 字节序排序。换到 glibc 后排序规则会**真实改变**，而 `datcollversion` 为 **NULL** 意味着 PostgreSQL **不会报 collation mismatch 警告**——文本索引可能静默失效，表现为查询结果错乱且无任何提示

  两者都源于同一个根因：**把向量能力塞进承载全部业务的生产关系库**，让检索需求的变更波及整个数据库。风险与收益不成比例，故推翻。
- **Alternatives considered**：
  - **pgvector（初版方案，已否决）**：见上两点。此外向量检索还会把 PG 的资源占用与运维面进一步拉大
  - **Milvus**：空载即需 **2.5–4 GB** 内存，且依赖 etcd + MinIO（+Kafka）三个组件。当前规模（万级文档块）远用不上，被运维成本否决
  - **Chroma**：上手最快，但公开对比中内存占用更高（10 万向量约 1.2–1.8 GB），且明确不适合高并发生产
  - **嵌入式向量库（Chroma 嵌入式 / LanceDB / Milvus Lite）**：看着最省事，但本项目的 Django 是**多 gunicorn worker + 进程内后台任务**（摄入在 worker 内写、检索在任意 worker 内读），嵌入式库的多进程写入不安全。**必须用 server 模式**
  - **PG 全文检索（tsvector + pg_trgm）**：零外部依赖，但语义召回弱，且不解决「业务库与检索耦合」的根因
- **选 Qdrant 的理由**：单容器、**零外部依赖**；内存占用最低（10 万向量约 350–600 MB，且支持标量量化再省约 80%）；自带 Web UI 便于调试 RAG；`langchain-qdrant` 官方集成
- **配套**：docker compose 增加 Qdrant 服务与数据卷；集合维度须与 `models.EMBEDDING_DIMENSIONS` 及嵌入模型一致

### D1b: 块正文以库内为准，Qdrant 只做可重建的索引

- **Decision**：文档与块的**正文存库内**（`kb_document` / `kb_chunk.content`）；Qdrant 只存向量与检索过滤所需的最小字段（块 id、知识库 id），并在 `kb_chunk` 上回记 `qdrant_point_id`。检索时先从 Qdrant 取回命中的块 id，再按 id 回库取正文。
- **Why**：
  - 库内是**唯一事实来源**，软删除、审计、按内容检索 SQL 等既有约定全部照旧
  - Qdrant 侧数据丢失只是**重建索引**，不涉及正文——把不可替代的数据与可重建的索引分开
  - 与项目「模型继承 `AbstractTimeFiledModel`、显式 `db_table`、软删除」的约定一致；正文若搬到 Qdrant 的 payload 里就脱离了这套约定
- **Trade-off**：多一次按 id 回库的查询（一次 `WHERE id IN (...)`，可控）。换来的是数据归属清晰、索引可随时重建

### D2: langgraph 用于「单 agent + 工具调用」，不是线性 RAG 链

- **Decision**：用 langgraph 组一张 agent 图，由模型自主决定调用哪些工具、调用几次；而非固定「检索 → 组装提示 → 生成」的线性流程。
- **Why**：使用者的真实问法常常需要在多个动作间选择——「订单表里那个金额字段是什么类型」需要查表结构，「这个隔离级别怎么配」需要查文档，「帮我看看这条 SQL 为什么慢」两者都要。线性链只能无条件检索一次，无法按问题选择。
- **Alternatives considered**：
  - **线性 RAG 图**：行为完全可预测、调试容易，但回答不了「需要组合多个信息源」的问题，扩展时要重构
  - **多 agent 编排（规划-执行-反思）**：能力最强，但复杂度与调试成本陡增，当前只有一个 agent 的规模用不上
- **Trade-off**：agent 行为不确定（可能多轮调用工具、延迟更高、偶发不调工具直接作答）。缓解：设置最大步数上限、在回答中标注实际调用过的工具与来源，便于使用者判断可信度。

### D2b: 对接 OpenAI 兼容端点的两个实测坑

联调真实服务时踩到两处，都记在这里免得后人重走：

1. **`check_embedding_ctx_length` 必须关掉**。langchain-openai 默认开启，会先用 tiktoken 把文本切成 token id 再发；OpenAI 官方端点接受，但 OpenAI **兼容**端点（实测 SiliconFlow）要的是原文，直接返回 400「参数无效」。已关闭并有单测守住。
2. **`dimensions` 要显式传，值取系统约定维度**。起初以为「开源服务多不支持该参数」而去掉它，实测结论相反：该服务原生输出 **4096 维**，且支持用 `dimensions` 截到 1024/1536/2048。向量库集合的 size 固定，靠它截到 1024 可省约 4 倍内存与存储。不支持该参数的服务要么报错、要么忽略，两种都由 `_verify_dimensions` 兜住。

另外 `base_url` 要填**接口根**（`.../v1`），不要带 `/embeddings` 后缀——客户端会自己拼，带后缀会请求到 `/embeddings/embeddings` 而 404。

### D3: 模型配置落库，API Key 复用 `utils/crypto.py` 加密；对话与嵌入分开配置

- **Decision**：新增 `llm_provider` 表存 provider 类型、**用途（对话 / 嵌入）**、base_url、模型名、加密后的 API Key、启用状态。**一条配置只承载一种用途**，两类用途各自指定至多一条生效项（库上以 `(model_type)` 的部分唯一索引兜底）。**不写进 `conf.ini`。**
- **为什么分开（实施中修正）**：初版把对话模型名与嵌入模型名放在同一条记录里，且全表只能有一条生效。联调时发现**网关只提供对话模型、不提供嵌入**（`/models` 只返回对话模型、`/embeddings` 报 unsupported API name），而嵌入必然来自另一个服务。旧结构下要么没法同时配上两者，要么配上了也互相顶替。改为按用途分开后，**一端没配不拖累另一端**：没有嵌入时「仅通用模型」照常可用，需要检索时才报出可操作的配置提示。
- **Why**：
  - API Key 是凭据。`conf.ini` **已被 git 跟踪**（已知问题），再往里塞一个密钥等于公开
  - 模型与 base_url 属于会变的运维配置，落库可运行时切换、免重启（`CONF_ATTR` 在模块导入时载入，改 `conf.ini` 必须重启）
  - 项目已有 `utils/crypto.py`（Fernet 可逆加密）与 `datasource` 的凭据保护模式，直接复用，不引入新做法
- **Alternatives considered**：写在 `conf.ini`（见上，被拒）；环境变量（违反 `architecture.md` 的「禁止直接使用 `os.environ`」）
- **注意**：`utils/crypto.py` 的密钥目前也写在被跟踪的 `conf.ini` 里——**这是既有问题**，本 change 不解决，但它意味着 `llm_provider` 的加密强度上限受此限制

### D4: 元数据不入向量库，改为 agent 工具实时查询

- **Decision**：`get_table_schema` 工具直接查最近一次 `metadata_snapshot`，不做向量化。
- **Why**：表结构是**结构化且强时效**的数据——刚加了字段就该查到。入向量库会引入「快照过期」和「切分后语义失真」两个问题，而结构化查询本来就能精确回答「这张表有哪些字段」。
- **Alternatives considered**：把表/列描述入向量库做语义检索（适合「哪个字段存订单号」这类模糊问法）—— 有价值，但需在每次采集后重嵌入，成本与复杂度都上一个台阶，列入 Open Questions
- **依赖**：该工具读取 `apps/datasource` 的 `MetadataSnapshot`。属跨模块只读访问，按 `architecture.md`「禁止跨模块直接 import queryset」应通过该模块提供的接口/服务函数访问，不直接查它的 model

### D5: 回答模式默认自动回退，允许强制指定

- **Decision**：三种模式——`auto`（默认，先检索，命中则据文档作答，无命中回退通用模型）、`knowledge_only`（只用知识库，无命中时如实告知未找到）、`model_only`（跳过检索直接问模型）。回答中标注实际来源。
- **Why**：默认 `auto` 让使用者不必理解 RAG 概念就能用；但「检索不到却用模型常识作答」在技术问答里是**静默降级**，可能给出貌似有据实则无据的答案。因此必须允许强制 `knowledge_only`，且 `auto` 回退时要在回答里标明。
- **判定「无命中」**：以相似度阈值 + top-k 共同判定，阈值可配置。

### D6: 会话历史用项目自己的表，不用 langgraph checkpointer

- **Decision**：新增 `qa_session` + `qa_message` 两张表存会话与消息；每轮把历史消息作为状态喂给图。
- **Why**：langgraph 虽自带 checkpointer（`langgraph-checkpoint` 是传递依赖），但其存储格式是框架私有的，运维无法用 SQL 查询/清理，也不符合项目「模型继承 `AbstractTimeFiledModel`、软删除、显式 `db_table`」的约定。
- **Trade-off**：需要自己拼装历史、控制上下文长度（超长时截断或摘要）。当前问答场景轮次少，先做「保留最近 N 轮」，摘要压缩列入 Open Questions。

### D7: 依赖评估（`architecture.md` 要求）

- **Decision**：引入 `langchain` + `langgraph`，并接受它们对现有依赖的版本顶替。
- **实测影响**：新增 **37 个包**（langchain 1.4.2 / langchain-core 1.6.4 / langgraph 1.2.12 / pydantic 2.13.5 / langsmith / httpx / orjson / ormsgpack / zstandard / websockets / xxhash 等），顶替 3 个：`requests` 2.24.0→2.34.2、`urllib3` 1.25.11→**2.8.0**（跨大版本）、`idna` 2.10→3.20。体积新增约 **+19.5 MB**（site-packages 共 82 MB）
- **补充（实施时发现初版评估漏了两项，均已实测补入）**：
  - ~~`pgvector`（Python 包）~~ —— **已随 D1 的推翻而移除**，改用 `langchain-qdrant` 1.1.0 + `qdrant-client` 1.19.1，再拉入 `numpy`、`grpcio`、`protobuf`、`portalocker` 等 10 个包
  - `langchain-openai`（1.6.4）：**langchain 1.x 把各家 provider 拆成了独立包**，核心包不含任何模型实现。它再拉入 `openai 3.19.0`、`tiktoken`、`jiter`、`regex` 共 4 个包
  - 两项合计再增 6 个包，总数 **43 个**
- **风险评估**：`requests` 与 `urllib3` **只被 `src/utils/bsa.py` 与 `src/scripts/bsa_register_menu.py` 引用**，而这两个文件已标注为「当前未使用、无调用方」。升级不会碰到活代码。
- **必要性说明**（对应 `architecture.md`「新依赖必须先评估必要性」）：使用者明确要求 langchain + langgraph；且 agent + 工具调用 + 多模型适配自研成本高、易错。替代方案是只用各家 SDK 手写编排，被拒——会重复实现工具调用循环与消息抽象。
- **配套**：升级后必须重跑全部现有单测与 `scaffold_check.py`；`requirements.txt` 需明确锁定新版本，避免漂移。

### D8: 顺带修正基线里一处已失实的鉴权需求

- **Decision**：本 change 包含一条对 `datasource-management` 的 MODIFIED 需求，把「数据源接口鉴权交由 BSA 平台」改为「接口不做应用层鉴权，访问控制依赖网络隔离」。
- **Why**：该需求当前写在 `openspec/specs/` 基线里，但**描述与实际不符**——项目已改为 docker compose 部署，没有平台网关；本仓库也没有任何代码产出 `Token` 头。新问答接口沿用同一鉴权姿态，顺手把这条失实需求改对。
- **说明**：这是**搭车修改**，与本 change 的主功能无关。若评审认为应独立成 change，可拆出。

### D9: 内网部署 vs 公有云 API 的出网要求

- **Decision**：本 change 依赖服务能访问模型 API 的地址（公网或内网代理）。**这是部署前提，不由应用层解决。**
- **Why**：项目部署在内网（且无应用层鉴权，本就不该暴露）。若容器无出网通道，问答功能全部不可用，且表现为「调用超时」这种不易定位的故障。
- **配套**：`llm_provider` 记录 base_url，因此若内网有模型代理，配置指向代理即可，无需改代码；README 与部署说明需写明这一前提。

### D10: 不做流式输出

- **Decision**：一次性返回完整回答。
- **Why**：项目所有接口统一返回 `{code, message, data}`，而流式需要 SSE/分块传输，两者不兼容。为问答单独开一条不遵循统一格式的通道，代价大于收益。
- **Trade-off**：agent 可能多步调用工具，首字节等待时间较长（数秒到十几秒），体验不如流式。列入 Open Questions。

## Risks / Trade-offs

| 风险 | 影响 | 缓解 |
|------|------|------|
| **Qdrant 服务不可用** | 摄入与检索全部失败 | Qdrant 单容器零依赖，故障面小；检索侧给出明确错误而非空结果；正文在库内，**索引可重建** |
| **Qdrant 与库内不一致**（删文档时索引未同步） | 检索命中已删除的块 | 检索取回块 id 后回库校验 `is_deleted`；提供按知识库重建索引的入口 |
| 内网无出网通道 | 问答全部超时 | 开工前确认出网或内网模型代理；base_url 可配 |
| agent 行为不确定 | 延迟高、偶发不调工具、答案质量波动 | 最大步数上限；回答中标注调用的工具与来源；强制模式可绕过 agent |
| 引入 37 个包 | 镜像变大、依赖面变宽、升级 requests/urllib3 | 仅未使用的平台脚本引用旧版，风险低；升级后重跑全部检查 |
| 嵌入与对话模型不同源 | 向量维度/相似度不一致导致检索质量差 | 嵌入模型变更时必须重建向量；`llm_provider` 记录嵌入模型名，变更时提示重建 |
| 文档切分不当 | 检索召回的块缺上下文，答案断章取义 | 按标题层级 + 重叠窗口切分；块大小可配置 |
| 上下文超长 | 多轮后超出模型窗口 | 保留最近 N 轮 + 单轮检索块数上限；摘要压缩列入 Open Questions |
| 无应用层鉴权 | 知识库文档与模型 API Key 的配置接口可被任意读写 | 与基线一致依赖网络隔离；README 与部署说明重申只允许内网部署 |
| **模型把工具调用语法当正文吐出**（联调实测，deepseek 间歇性复现） | 使用者在界面上看到一堆标记语法 | 两处缓解：① 不绑定工具的场景改用不含工具语义的提示词（没有工具可调时提到工具会诱发模型编造调用语法）；② 输出侧按特征剥离这类内容，全被剥离时给出可读兜底 |
| **网关不提供嵌入模型**（实测：`/models` 只返回对话模型，`/embeddings` 报 unsupported API name） | RAG 的摄入与检索全部不可用 | 列为部署前提；嵌入模型缺失时按**配置问题**报错（code 4017 + 可操作提示），而不是报成服务器错误 |

## Migration Plan

1. **前置**：在 docker compose 中增加 Qdrant 服务与数据卷，确认服务可达（`/healthz`）
2. 执行 `src/sql/patch.sql`（已有库）或 `src/sql/pg_struct.sql`（新库）：建 `llm_provider` / `knowledge_base` / `kb_document` / `kb_chunk` / `qa_session` / `qa_message`，并为 `kb_chunk` 建向量列与索引
3. 更新 `requirements.txt` 并重装依赖；重跑全部单测与 `scaffold_check.py`，确认依赖升级未造成回归
4. 部署后端与前端
5. 在「模型配置」页添加一个 provider（填 base_url / 模型名 / API Key）并测试连通
6. 建一个知识库、上传一份文档，确认摄入成功且检索有结果
7. 回滚：回退代码与 `requirements.txt`；新表可保留（旧代码不读）；Qdrant 容器停掉即可，正文不受影响

## Open Questions

1. ~~嵌入模型用哪个？~~ **已定**：实际使用的嵌入服务输出 **1024 维**；`models.EMBEDDING_DIMENSIONS` 与 Qdrant 集合的 size 均按 1024 配置。换模型须同步改这两处并重建全部向量（见 `models.py` 的注释）
2. 是否需要把**已纳管库的元数据**也入向量库，以支持「哪个字段存订单号」这类模糊问法？（D4 选了不做）
3. 是否需要流式输出？（D10 选了不做）
4. 多轮上下文的压缩策略：保留最近 N 轮，还是超长后做摘要？N 取多少？
5. 文档摄入是否要支持定时增量同步（URL 来源的内容会变）？当前设计是手动上传/触发
6. 检索质量如何验证？是否需要建一套问答评测集（问题 + 期望命中的文档块）？
7. 换嵌入模型需要重建 Qdrant 的整个集合（维度变了则集合也要重建）——是否需要提供「按知识库重建索引」的入口？正文在库内，重建是纯计算，没有数据风险

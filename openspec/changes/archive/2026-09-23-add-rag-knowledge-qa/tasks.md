## 0. 前置条件确认（阻塞项，未确认前不要开工）

- [x] 0.1 ~~确认 pgvector 可安装~~ → **方案已推翻，改为独立 Qdrant 服务**（design.md D1）。原因：推 pgvector 的过程暴露了「向量能力塞进生产关系库」的两个真实代价——换官方镜像导致数据目录属主不匹配（Alpine uid 70 vs Debian uid 999）、**PostgreSQL 直接启动失败**；以及 musl→glibc 会让排序规则**静默改变**（`datcollversion` 为 NULL，PG 不报 mismatch 警告）
      → 待办：**在 docker compose 中增加 Qdrant 服务与数据卷**，确认 `/healthz` 可达
- [x] 0.2 **确认出网通道** —— 结论：**有出网 / 有内网代理**，模型 API 可达。`base_url` 可配，指向内网代理即可
- [x] 0.3 **确定嵌入模型** —— 结论：实际嵌入服务输出 **1024 维**；`models.EMBEDDING_DIMENSIONS` 与 Qdrant 集合 size 均按 1024 配置
      —— 期间修正一处隐患：原先给嵌入请求带上 `dimensions` 参数（OpenAI 文本嵌入 3 代的专有参数），开源嵌入服务不认它，会「被忽略后按原生维度返回」，拖到写向量库时才报维度不匹配。已改为不传该参数、拿到向量后立即显式校验
- [x] 0.4 确认 `add-analysis-rule-registry` 的进度 —— 该 change 停在第 2 节末（18/59），改动集中在 `apps/datasource` 的模型与规则，与本 change 只读快照不冲突

## 1. 依赖引入与回归验证（先做，尽早暴露风险）

- [x] 1.1 `src/requirements.txt` 新增 `langchain==1.4.2`、`langgraph==1.2.12`，并锁定解析出的具体版本
      —— 同时**必须**把 `requests==2.24.0` 改为 `requests==2.34.2`：langchain 要求更高的 requests，旧硬锁会让从文件安装直接解析失败
- [x] 1.2 重装依赖，确认版本顶替生效：`requests` 2.24.0→**2.34.2**、`urllib3` 1.25.11→**2.8.0**、`idna` 2.10→**3.20**；实际解析出 `langchain 1.4.2` / `langgraph 1.2.12` / `langchain-core 1.6.4` / `pydantic 2.13.5`
- [x] 1.3 **回归验证全绿**：`scaffold_check.py` ✅、34 项单测 ✅、`manage.py check` ✅（仅既有 `W042` 警告）、`ruff check`/`format` ✅
- [x] 1.4 `utils.bsa` 与 `scripts.bsa_register_menu` 在新版 `requests 2.34.2` / `urllib3 2.8.0` 下**仍可 import**，`bsa_client` 实例可构造 —— 虽然当前无调用方，但确认了升级没有留下坏文件
- [x] 1.5 体积影响：新增包合计 **+19.5 MB**（最大为 `pydantic_core` 5.1 MB、`langsmith` 3.1 MB、`langchain_core` 2.3 MB），site-packages 总计 82 MB

## 2. 模型接入配置（依赖 0.3）

- [x] 2.1 `utils/custom_enum.py` 新增 `ProviderTypeEnum`、`QaModeEnum`、`DocumentStatusEnum`、`DocumentSourceEnum`、`MessageRoleEnum`（`IntegerChoices`，禁止裸整数）
- [x] 2.2 `models.py` 新增 `LlmProvider`：名称、provider 类型、base_url、对话模型名、嵌入模型名、加密 API Key、是否启用、是否当前生效
- [x] 2.3 序列化器：`LlmProviderSerializer`（**API Key 不出参**）、创建/更新用普通 `Serializer` + `validate_name()`
- [x] 2.4 `LlmProviderView`（继承 `baseviews.AnyLogin`）：CRUD + `test` 连通性 + `activate` 设为生效
      —— `activate` 先取消原生效项再设新项（库上有部分唯一索引约束「至多一条生效」）；**首条配置自动生效**，避免出现「有配置但无生效项」导致问答直接失败
- [x] 2.5 连接测试发一次最小请求、带超时、**失败原因经 `_sanitize` 脱敏**（外部 SDK 报错内容不受控，兜底剔除 API Key）
- [x] 2.6 注册路由 `rf"{SYS_NAME}/v1/llm-provider"`，4 条路由（list/detail/test/activate）已用 resolver 确认匹配
- [x] 2.7 用真实 provider 验证：对 `aigw.inone.nsfocus.com` 的 `deepseek-flash` **真实调用成功**（1.4s，HTTP 200），`仅通用模型` 模式连续 3 次返回干净答案
      —— 过程中发现两个真实问题并修复（见 design.md 风险表）：模型会把工具调用语法当正文吐出；网关不提供嵌入模型
- [x] 2.8 嵌入模型已配置并**真实跑通**：`Qwen/Qwen3-VL-Embedding-8B` @ SiliconFlow，输出 **1024 维**
      —— 期间修正三处：`base_url` 多带了 `/embeddings` 后缀导致 404；`check_embedding_ctx_length` 默认开启使请求发的是 token id 而非原文，兼容端点返回 400；`dimensions` 参数**应保留**（实测该服务原生 4096 维，靠它截到 1024 可省约 4 倍内存）。详见 design.md D2b

## 3. 数据模型与 DDL（依赖 0.1、0.3）

- [x] 3.1 `models.py` 新增 `KnowledgeBase`、`KbDocument`、`KbChunk`、`QaSession`、`QaMessage`（均继承 `AbstractTimeFiledModel`，显式 `db_table`）；6 个模型已确认注册到 `apps` app
- [x] 3.2 `KbChunk` **不含向量列、也不存点标识**：正文留 `content`（库内是唯一事实来源），**点标识直接使用 `kb_chunk.id`**，因此索引可由正文纯函数式重建，且没有「向量写成功但标识回写失败」的漂移窗口（design.md D1b）。维度以 `models.EMBEDDING_DIMENSIONS`（当前 **1024**）表示，用于创建 Qdrant 集合，**不放配置文件**——它必须与集合 size 和嵌入模型的实际输出三方一致，写成常量更容易在评审时被看见
      —— 实施中先写成 `qdrant_point_id` 回记方案，随后推翻为「用块 id 当点标识」，更简单且更利于重建
- [x] 3.3 `src/sql/pg_struct.sql`：6 张新表与索引；**库内不含向量列、不依赖数据库向量扩展**
      —— 额外加了一条**数据库层兜底**：`uk_llm_provider_active` 部分唯一索引保证「至多一条生效配置」
- [x] 3.4 `src/sql/patch.sql` 追加同内容变更集
- [x] 3.5 `src/config/urls.py` 导入新模块视图（模型靠导入副作用注册，必须先注册路由）
- [x] 3.6 验证：`pg_struct.sql` 在全新 schema **65/65 通过**（去掉向量依赖后 100%，少 1 条是删掉了 `CREATE EXTENSION`）
      —— `patch.sql` 在**空库**上会失败于 `catalog_issue` 的 ALTER/COMMENT，这是**预期行为**：它是给「已有旧表的库」做增量用的，新库/空库应执行 `pg_struct.sql`。两种起始状态各用各的脚本，不混用
      —— `scaffold_check.py` 与 `manage.py check` 均已通过

## 4. 文档摄入（依赖 3）

- [x] 4.1 新增 `src/apps/knowledge/ingest.py`：Markdown / 文本 / PDF 解析、网页抓取
      —— 新增依赖 `pypdf` + `beautifulsoup4`（含 `soupsieve`，共 3 个纯 Python 包）。HTML 走 BeautifulSoup 去 script/style/nav 后取正文
- [x] 4.2 切分策略：Markdown 按标题层级组织（块携带标题路径）+ 重叠窗口，块大小与重叠从配置读取；超长段落硬切
- [x] 4.3 嵌入调用封装（批量、超时、重试 1 次），维度取自 `EMBEDDING_DIMENSIONS`
- [x] 4.3b 向量写入 Qdrant：集合按需创建（1024 维、余弦）；**点标识直接使用 `kb_chunk.id`**，点 payload 只放知识库 id 与文档 id（正文不进 Qdrant，D1b）
- [x] 4.4 摄入走 `simple-background-task` 异步执行（参考 `apps/datasource/services.py` 显式启动 worker）
- [x] 4.5 状态机：待处理 / 处理中 / 成功 / 失败；**先嵌入再落库**，嵌入失败一行不写；写向量失败则补偿删除刚写入的块
- [x] 4.6 重复来源检测：按**内容指纹**（非文件名/URL）在同一知识库内判重
      - 上传（同步）：内容当场可得，命中且使用者未表态时返回 4017 提示，并给出 `on_duplicate=1`（跳过）/ `2`（覆盖）两个选项
      - 链接导入（异步）：内容要抓取后才知道，检测只能在任务里做，那时无法回头问使用者，故退化为「跳过并写明原因」
      - 两种路径都**不静默**产生重复内容
      - 验证（真实库 + 真实 Qdrant）：未表态→4017；跳过→返回已有文档且不新建；覆盖→旧文档软删、新文档建立、总数不变；不同内容→正常新建
- [x] 4.7 验证（对着真实库与真实 Qdrant 跑，仅把嵌入函数替换为假实现）：
      - 切分：Markdown 标题层级正确产出 `标题一` / `标题一/子节` / `标题二`
      - 解析：不支持的格式、空文件、超大文件均给出可读原因
      - 摄入成功：状态=成功、库内 3 块、Qdrant 3 点
      - 摄入失败：嵌入抛异常 → 状态=失败记原因、**遗留块数 0**
      - 后续用**真实嵌入服务**复验：上传 Markdown → 摄入成功（3 块 / Qdrant 3 点，耗时 1s）→ 语义检索返回正确排序
      - **仍未验证**：PDF 与 URL 两条解析分支（手头没有 PDF 样本与可访问的外网页面）

## 5. 向量检索（依赖 3、4）

- [x] 5.1 新增 `src/apps/knowledge/retrieval.py`：向 Qdrant 按知识库（payload 过滤）+ top-k 检索，**再按块 id 回库取正文**（D1b）；另有 `vectorstore.py` 封装集合创建、写入、删除、检索
- [x] 5.2 返回结构含内容、文档标题、来源标识、标题路径、相似度分值
- [x] 5.3 Qdrant 不可达时抛 `VectorStoreError`（指明向量服务不可用），**不静默返回空**——否则上层会误判为「无命中」而回退到通用模型
- [x] 5.4 回库取正文时**过滤已软删除的块**，避免删了文档还能被检索命中
- [x] 5.5 验证（对着真实 Qdrant 与真实库跑）：阈值生效（0.9 滤掉 0.7459 的候选、1.0 返回空）、**跨库不泄漏**（kb=2 检索只返回自己的块）、增删幂等、**软删除的块不再被检索命中**

## 6. 问答编排（依赖 2、5）

- [x] 6.1 新增 `src/apps/knowledge/qa/`：`create_react_agent` 组成的单 agent（模型 ⇄ 工具循环），复杂度来自工具选择而非编排层级
- [x] 6.2 工具 `search_knowledge_base`：包装 5.1 的检索，并把命中的片段写入 `collected`
- [x] 6.3 工具 `get_table_schema`：经**新增的跨模块服务函数** `apps.datasource.services.list_snapshot_tables()` 访问快照，未直接 import 该模块的 model（`architecture.md` 跨模块约束）
- [x] 6.4 最大步数经 `recursion_limit` 限制；** agent 执行异常（含达上限）被兜住并返回可读提示**，不让整轮失败；工具自身失败返回可读信息给模型
- [x] 6.5 三种模式在进入 agent **之前**定下来：`model_only` 不检索；`knowledge_only` 无命中时如实告知且**不调模型**；`auto` 无命中才回退并标明
- [x] 6.6 历史消息组装 + 按配置轮数截断（不做摘要，避免引入额外模型调用）
- [x] 6.7 产出含：正文、**仅由实际检索产生的**来源、调用过的工具、是否回退、回退说明
- [x] 6.8 验证（单测，用替身隔离 langgraph 内部）：覆盖三种模式分支、无命中不调模型、无知识库回退并说明、向量服务故障**不被当作无命中**、来源不被编造、历史截断

## 7. 问答 API（依赖 6）

- [x] 7.1 `serializers.py`：模型配置 / 知识库 / 文档 / 会话 / 消息 / 提问参数
- [x] 7.2 `QaSessionView`：会话列表、详情（含消息与来源）、软删除
- [x] 7.3 提问接口 `ask`：入参含问题、会话、知识库、数据源、模式；**先答成功再落库**，失败不留空会话；非流式（design.md D10）
- [x] 7.4 `KnowledgeBaseView` / `KbDocumentView`：知识库 CRUD、文档列表与状态、文件上传、链接导入、重新摄入
      —— 删除知识库/文档时同步清理 Qdrant 向量；**向量清不掉不阻断业务删除**（库内是事实来源，残留向量在检索回库阶段会被 `is_deleted` 过滤）
      —— 文件来源不支持「重新摄入」（未落盘存储），返回明确提示要求重新上传
- [x] 7.5 注册路由 `knowledge-base` / `kb-document` / `qa-session`；列表接口统一 `pagination.paginate` 与 `baseviews.ResponseOK`
- [x] 7.6 验证（真实 HTTP 接口，仅模型与嵌入用替身）：
      - 建库 → 上传 Markdown → 异步摄入 → **状态成功、3 块、Qdrant 3 点**
      - 三种模式提问均返回 2000；`仅通用模型` 正确标记 `is_fallback=True` 并带「未经文档支撑」说明
      - 会话与消息落库正确（角色「提问/回答」）
      - **来源数为 0**：替身 agent 未真的调用检索工具，证明来源只由实际检索产生、不随模型措辞编造
      - 未验证：真实模型与真实嵌入调用（需 API Key）

## 8. 前端（依赖 7）

- [x] 8.1 `static/src/api/knowledge.js`，复用 `request.js`（当前含模型配置的 6 个接口，知识库/问答接口随后端推进补入）
- [x] 8.2 模型配置页：列表（标注生效项）+ 新建/编辑 + 测试连接 + 设为生效 + 软删除
      —— 编辑时 API Key 留空表示不更换；表单提示嵌入维度须与后端 `EMBEDDING_DIMENSIONS` 一致
- [x] 8.3 知识库页：左列知识库列表（新建/编辑/删除），右列文档列表与状态、上传文件、导入链接、重新摄入、失败原因展示
- [x] 8.4 问答页：会话列表 + 多轮对话 + 提问输入（Ctrl+Enter）+ 知识库选择 + 模式选择 + **来源展示** + **回退提示**
- [x] 8.5 路由注册（`meta.title` / `meta.icon`）—— `knowledge/qa`、`knowledge/bases`、`knowledge/provider` 三个页面
- [x] 8.6 `src/right_config.json` 增加「知识问答 → 问答 / 知识库 / 模型配置」菜单项
- [x] 8.7 验证：`npm run build` 通过（三个页面 chunk 均产出）；`ruff`、71 项单测、`scaffold_check.py` 全绿

## 9. 配置、文档与整体验证

- [x] 9.1 `src/config/conf.ini` 增加 `[knowledge]` 段：Qdrant 地址与集合名、模型超时、top-k、相似度阈值、切分块大小与重叠、agent 最大步数、历史轮数——**不含 API Key**（Key 在 `llm_provider` 表加密存储）
- [x] 9.2 更新 `README.md`：新增「已实现：知识问答」章节（数据表、API 清单、三种模式、重复来源检测、`[knowledge]` 配置项、**部署前提与已知限制**），并把路线图与目录结构同步
- [x] 9.3 更新 `openspec/project.md`：Domain Context 补充知识问答流程与**向量维度三方一致**的约束；External Dependencies 增加 Qdrant 与模型 API
- [x] 9.4 整体验证全绿：`scaffold_check.py` ✅、`manage.py test` **88 项** ✅、`ruff check`/`format` ✅、`npm run build` ✅
- [ ] 9.5 【**待你执行**】浏览器端到端走查：模型配置 → 知识库摄入 → 三种模式提问 → 来源可点开 → 回退提示可见。**这是人工确认项，自动化无法替代**
- [x] 9.6 规则自查：模型继承 `AbstractTimeFiledModel` ✅、`db_table` 全部显式 ✅、4 个 ViewSet 均继承 `baseviews.AnyLogin` ✅、路由全部注册 ✅；枚举走 `custom_enum`、DDL 写 `pg_struct.sql`/`patch.sql`、新依赖均先评估

## 依赖关系说明

- 关键路径：**0 → 1 → 2/3 → 4 → 5 → 6 → 7 → 8 → 9**
- **第 0 节是硬阻塞**：Qdrant 服务起不来则摄入与检索无法落地；出网不通则问答功能不可用
- 第 1 节刻意排在前面：37 个新包与 `requests`/`urllib3` 跨大版本顶替是**唯一会波及现有代码**的改动，先做先暴露
- 可并行：第 2 节（模型配置）与第 3 节（DDL）无耦合；第 8 节前端在第 7 节接口签名确定后即可开始
- 第 6.8 的单测不需要真实模型，是质量主要保障；7.6 才需要真实 provider 与 Qdrant 服务

## 破坏性 / 高风险项

- **依赖顶替**：`requests` 2.24.0→2.34.2、`urllib3` 1.25.11→2.8.0（跨大版本）、`idna` 2.10→3.20。仅未使用的平台集成脚本引用旧版，风险低但必须按 1.3 回归
- **DDL**：新增 6 张表与 1 个扩展。扩展需服务器侧安装，应用层无法自行解决
- 回滚：回退代码与 `requirements.txt`；新表可保留（旧代码不读），`vector` 扩展留着无害

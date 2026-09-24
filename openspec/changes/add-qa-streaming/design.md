## Context

前置状态：

- 问答已实现（归档于 `archive/2026-09-23-add-rag-knowledge-qa`），同步接口 `POST /qa-session/ask` 一次性返回 `{code, message, data}`
- 编排是 langgraph **单 agent + 工具调用**（该设计的 D2）：模型自主决定调哪些工具、调几次
- 归档设计的 **D10 明确选择了「不做流式输出」**，并把体验问题记入待办。本次推翻它
- 系统提示词要求回答标注引用编号，工具通过闭包收集「实际检索到的片段」作为来源
- `qa/agent.py` 有一处输出侧兜底 `_strip_tool_markup()`，**按行**剥离模型误当正文吐出的工具调用语法
- 部署为 docker compose + gunicorn：`worker_class` 未显式配置，`threads = 6 > 1`，经核对 gunicorn 源码（`config.py:98-108`）**会自动选用 gthread**
- 接口不做应用层鉴权（`AnyLogin`），访问控制依赖网络隔离

## Goals / Non-Goals

- **Goals**：回答生成过程中即可见；工具执行阶段不再是无反馈的空白；生成可中止且中断不丢已生成内容；同步接口与其既有行为完全不受影响
- **Non-Goals**：不做多 agent 编排；不做 reasoning/思维链输出的呈现；不做逐 token 落库；不引入心跳与断线重连；不改造同步接口；不新增第三方依赖

## Decisions

### D1: 新增流式接口，不改造同步接口

- **Decision**：新增 `POST {SYS_NAME}/v1/qa-session/ask-stream`。`/qa-session/ask` 原样保留，请求参数与响应结构都不动。
- **Why**：
  - 流式出问题时，同步接口可作为对照手段定位（是生成逻辑问题还是流式传输问题）
  - 脚本化调用仍需要「一次拿到完整结果」
  - `/ask` 的既有测试与调用方零改动，本次回归面收敛到新代码
- **Alternatives considered**：直接改造 `/ask` 为流式（只有一条路径，但既有契约变更、失去同步能力）；流式接口内部复用同步实现（当前 `answer()` 本就是同步的，反向包一层要引入队列与上下文传递，复杂度不降反升）
- **Trade-off**：两条路径需要同步维护。缓解见 D9——二者共享同一套模式判定。

### D2: 传输用 SSE 帧，客户端用 `fetch` 而非 `EventSource`

- **Decision**：响应体为 `text/event-stream`，用 SSE 的 `event:` / `data:` 帧格式承载事件。前端用 `fetch` + `ReadableStream` 读取并手写解析。
- **Why**：
  - `EventSource` **只支持 GET 且不能带自定义请求头**，而提问需要 POST 请求体、且项目约定认证走 `Token` 头，因此 `EventSource` 用不了
  - 有具名事件类型（`status` / `delta` / `done` / `error`），比裸 chunk 或 NDJSON 更易读、便于用 `curl` 与浏览器 DevTools 排查
- **Alternatives considered**：NDJSON 或裸文本 chunk（更简，但要自己约定类型字段，且丢失 SSE 的现成工具链）
- **配套**：附加 `Cache-Control: no-cache` 与 `X-Accel-Buffering: no`（后者为将来可能的前置代理预留）

### D3: 进度事件在**模型发起工具调用时**给出，不由工具回调上报

- **Decision**：工具名 → 进度文案的映射放在 `qa/tools.py`（`TOOL_STATUS` / `status_text()`）；编排层扫描分片流，一旦发现带 `tool_call_chunks` 的分片里出现了工具名，立刻推出一条 `status` 事件。**`build_tools()` 的签名不变。**
- **Why（实施中修正了初稿方案）**：初稿是给 `build_tools()` 加 `emit` 回调、由工具在自己开头调用。**实施时发现那样达不到目的**：回调只能把事件写进缓冲区，而缓冲区要等生成器下一次被 `next()` 才排空——那一刻工具已经跑完、模型也已吐出下一轮的第一个分片。于是「正在检索知识库…」会**和第一个答案 token 同时到达**，使用者仍要对着空白等完整个工具阶段，等于没有进度。
  改从模型的分片流取工具名，时机就提前到**工具真正执行之前**：模型必须先把调用意图吐完，图才会去执行工具。
- **Alternatives considered**：工具内回调（初稿方案，时机太晚，见上）；`astream_events` v2（事件语义最全，但要按 name/tag 过滤、事件量大且版本敏感）；从 `updates` 事件反推工具（只能拿到「已完成」，拿不到「正在进行」）
- **约束**：文案 MUST 面向使用者，MUST NOT 包含工具入参或原始返回内容。未登记的工具名给一句通用文案，不把工具名直接暴露给使用者。

### D4: 答案 token 的三重过滤条件

- **Decision**：以 `agent.stream(..., stream_mode="messages")` 取 `(chunk, metadata)`，仅在**同时满足**三个条件时才视为答案 token：

  1. `metadata["langgraph_node"] == "agent"`（排除工具节点产出的消息）
  2. `isinstance(chunk, AIMessageChunk)`
  3. **无** `tool_call_chunks`（排除模型决定调用工具的那一轮）

- **Why**：react agent 的中间轮次产出的同样是 `AIMessageChunk`，若不满足第 3 条，**模型为调用工具而生成的参数会被当成答案推给使用者**。这是本设计里最容易出错的一处。
- **配套**：三重条件写成独立谓词函数，配单测锁住。

### D5: 用行缓冲保留工具语法剥离，而不是放弃它

- **Decision**：token 先累积进 buffer，只把「到最后一个换行为止」的完整行交给过滤器输出，流结束时 flush 残余；若全部内容都被剥离，则使用与同步路径相同的兜底文案。
- **Why**：
  - 现有 `_strip_tool_markup()` 是**按行**判定的后处理，天然无法作用于实时 token 流。直接照搬不行，直接放弃则会让「模型把工具调用语法当正文吐出」这个**实测复现过的缺陷**在流式路径上复活
  - 行缓冲是保留该保证的最小代价方案：把现有的逐行判定谓词抽成 `_is_markup_line()`，同步与流式两条路径复用它，行为一致
- **Trade-off**：输出延迟最多增加一行。模型若吐出没有换行的超长标记块，会整段被缓冲——属可接受（该内容本就不该展示）。
- **Alternatives considered**：流式路径不做剥离（缺陷复活）；检测到特征就中止整轮（对使用者过于粗暴，且无法区分「正常回答里恰好含关键字」）
- **实施中踩到的坑（已由单测锁住）**：原 `message_text()` 对每个分片做 `strip()`，用于非流式无妨，但喂给行缓冲会把**换行逐块吃掉**——行边界永远不出现，整篇回答被并成一行；此时只要其中出现一个标记字样，**整篇正常回答都会被连坐丢弃**并替换成兜底文案。因此拆出 `content_text()`（不 strip）专供流式路径，`message_text()` 保持原语义供非流式使用。

### D6: 预检失败仍走统一响应格式

- **Decision**：参数非法、会话不存在、未配置对话/嵌入模型、向量服务不可用等**在写出第一个字节之前**就能判定的失败，仍返回统一格式 `{code, message, data}`（4000 / 4004 / 4017 / 5000）。只有**成功回答的正文**脱离统一格式。
- **更正（与初稿不符）**：初稿写的是「带对应 HTTP 状态码」。实施时核对 `baseviews.py` 发现，`FormatResponse.__init__` 里的 `FormatResponse.status_code = 200` 是赋在**类**上，因此本项目**所有接口的 HTTP 状态码恒为 200**，业务结果只由 body 里的 `code` 表达。所以前端不是按状态码、而是**按 `Content-Type`** 区分「预检失败的 JSON」与「已开流的事件流」。
- **Why**：
  - 未开流时错误仍带完整业务码与可操作文案，前端复用既有 axios 拦截器即可（它本来也只看 `code`）
  - 「接口不遵循统一规范」的范围被压到最小——只有正文流不遵循
- **Alternatives considered**：一律开流、错误也走事件帧（前端只有一条路径，但「未配置对话模型」这类配置问题会被埋进流里，且 body 里再也拿不到统一的 code）

### D7: 中断保留半截内容，用布尔字段标记完整性

- **Decision**：`qa_message` 增加 `is_complete`（`BooleanField`，默认 `True`）。生成被中止或异常断连时，落已收到内容并置 `is_complete=False`；正常结束置 `True`。
- **Why**：
  - 长回答快生成完时误关页面会很恼火，丢弃已生成内容对使用者是净损失
  - 用布尔而非枚举：状态只有「完整 / 不完整」两种，中断原因由 `error` 事件即时告知使用者，不需要持久化区分「主动停止」与「异常断连」
  - 满足项目规范（`AbstractTimeFiledModel` + 显式 `db_table`），不引入新表
- **Alternatives considered**：不落库（与现有「先答成功再落库」语义一致，但丢失内容）；用枚举字段区分三种状态（YAGNI，当前没有消费方需要区分）
- **配套**：「提问先于生成落库」——否则中断时连提问都丢了。

### D8: 生成期间不逐 token 落库

- **Decision**：生成过程中内容只进内存 buffer，仅在**正常结束**或**中断**时一次性写入。
- **Why**：逐 token 会产生数百次 `UPDATE`，代价远大于收益。
- **Trade-off**：进程在生成过程中被杀（重启、OOM）会丢失这半截内容，且**不会有任何痕迹**。这与项目既有的「进程内后台任务队列重启即丢」属同一量级，写入文档即可。
- **Alternatives considered**：按时间/长度节流落库（多一套节流状态，收益仅覆盖「进程被杀」这一小概率场景）

### D9: 流式与同步共享同一套模式判定

- **Decision**：把 `answer()` 拆成 `_prepare()`（预检 + 决定走哪条路径）与执行。`answer()` 与 `answer_stream()` 都从 `_prepare()` 的结果出发。
- **Why**：四种执行路径（仅通用模型 / 无知识库 / 无命中回退 / agent 编排）与三类预检错误如果各写一遍，**两条路径必然逐渐漂移**——最典型的是「回退必须标注」与「向量服务故障 ≠ 无命中」这两条约束，漏一条就是静默降级。
- **配套**：`_prepare()` 的分支写成可单测的纯逻辑。

### D10: 推翻归档设计的 D10

- **Decision**：`archive/2026-09-23-add-rag-knowledge-qa/design.md` 的 D10「不做流式输出」**作废**。
- **Why**：D10 的论据是「流式需要 SSE/分块传输，与统一响应格式不兼容，为问答单独开一条通道代价大于收益」。本次的实测结论是反向的：**agent 编排的首字节等待长达数秒到十几秒**（预检索 + 多轮工具调用），使用者对着空白等待的代价更大。且 D6 已把「不遵循统一格式」的范围压到只有成功正文，D1 又保住了同步接口，D10 当时的两个顾虑都已消解。
- **Alternatives considered**：保留非流式、改用「先返回检索到的来源，再返回答案」的两段式（减少等待感知但不解决生成阶段的等待）

### D11: 依赖评估（`architecture.md` 要求）

- **Decision**：**本次不引入任何新依赖。**
- **说明**：SSE 只是 `text/event-stream` 内容类型加纯文本帧格式，Django 的 `StreamingHttpResponse` 原生支持；前端用浏览器内置的 `fetch` + `TextDecoder` 手写解析。langgraph 的流式能力已在现有 `langgraph 1.2.12` 中，无需升级。

### D12: 用测试运行器把 DDL 灌入测试库，而不是补写 migration

- **Decision**：新增 `utils/test_runner.py`（`SqlSchemaTestRunner`），在建好测试库之后把 `sql/pg_struct.sql` 整份灌入；`settings/settings.py` 配置 `TEST_RUNNER` 指向它。**不写 migration。**
- **背景（实测发现）**：本项目禁止 Django migration，仓库中**没有任何 `migrations/` 目录**。而 Django 建测试库靠跑 `migrate`——实测结果是测试库里一张业务表都没有，任何 `TestCase` 都以 `relation "qa_message" does not exist` 失败。既有 88 项测试全为 `SimpleTestCase`（不碰数据库），因此这个问题此前从未暴露；而本次「中断保留已生成内容」这条需求**没有数据库就无法自动化验证**。
- **Why 这个方案**：`pg_struct.sql` 全是 `CREATE TABLE/INDEX IF NOT EXISTS`，无 psql 反斜杠元命令、无事务控制、无 `CONCURRENTLY`，可被 psycopg2 一次 `execute` 执行，因此不需要引入 psql 客户端或额外的 DDL 切分逻辑。
- **Alternatives considered**：
  - **补写 migration**：直接违反项目「禁止 Django migration」的硬性约定，且会让 DDL 出现两处事实来源
  - **每个 `TestCase` 在 `setUpClass` 里执行 DDL**：每个测试类都要重复，且无法覆盖将来新增的测试
  - **不测数据库层**：把落库逻辑压薄到只测入参组装——等于让「中断保留内容」这条需求失去自动化覆盖
- **必须在 `setup_databases` 里加守卫**：当测试套件不涉及数据库时（全部为 `SimpleTestCase`），Django **不会创建测试库**，此时连接仍指向 `conf.ini` 里配置的真实库。若无条件灌 DDL，就是在改真实库的结构。因此只在 `aliases` 非空时才灌。
- **附带收益**：`pg_struct.sql` 成为跑 DB 测试的前置条件——**改了表结构却忘记同步 `pg_struct.sql`，新写的 DB 测试会立刻失败**，形成一道防漂移的约束。
- **已验证**：`TestCase` 可读写业务表；既有 88 项不受影响；全 `SimpleTestCase` 时未建库、未灌 DDL，真实库结构原封不动。

## Risks / Trade-offs

| 风险 | 影响 | 缓解 |
|------|------|------|
| **中间轮次 token 被误当答案推出**（D4 第 3 条漏判） | 使用者看到工具调用参数 | 三重条件写成独立谓词 + 单测锁住；联调时先验证工具调用场景 |
| **流式连接长时间占用 worker 线程** | 并发提问数受限于 3 worker × 6 线程 = 18，超出即排队 | gunicorn 实际是 gthread（已核对源码），占用的是线程而非整个 worker；文档写明，必要时调 `workers` |
| **进程在生成中被杀** | 丢失该轮已生成内容，无痕迹 | 已知代价（D8），写入文档；高频场景再考虑节流落库 |
| **行缓冲带来输出延迟** | 首字延迟最多增加一行 | 仅影响按行交付的粒度；工具语法外泄的防护价值更高 |
| **中断落库依赖 `GeneratorExit`** | 若框架未如期关闭生成器，内容可能不落库 | `finally` 中兜底；测试覆盖主动停止与断连两种路径 |
| **两条路径落库时机不同** | `/ask` 失败不落库，`/ask-stream` 预检后即落用户消息 | 刻意为之（D7），在两个接口的文档中写明，避免被当成不一致缺陷 |
| **前端复杂度上升** | `qa.vue` 需处理 SSE 解析、中止、状态展示 | 解析逻辑全部收在 `qaApi.askStream()` 内，页面只接触回调 |
| 无应用层鉴权 + 长连接 | 流式接口同样可被任意调用，且更易占用资源 | 与基线一致依赖网络隔离，重申只允许内网部署 |

## Migration Plan

1. 执行 `src/sql/patch.sql`（已有库）或 `src/sql/pg_struct.sql`（新库）：为 `qa_message` 增加 `is_complete`
2. **无需数据回填**：新增列默认 `True`，既有回答都是完整生成的，语义正确
3. 部署后端与前端；同步接口无需改动，可灰度——前端切换前新接口不影响任何现有功能
4. 验收顺序：先验证「仅通用模型」模式的流式（不依赖 Qdrant）→ 再验证检索与工具进度 → 最后验证中断保留
5. **回滚**：回退代码即可。`is_complete` 列可保留（旧代码不读）；前端回退到调用 `/ask`

## Open Questions

1. ~~**测试库是否可用？**~~ **已解决**：测试库已建立（服务端 PostgreSQL 17.11，参数与 `dba` 一致），并补齐了 `SqlSchemaTestRunner`（见 D12）。`TestCase` 现已可用，中断落库可被自动化覆盖。**注意**：跑 DB 测试须用 `ENV_TYPE=test`，避免 Django 在 `local` 环境下建出 `test_dba` 之类的中间库。
2. 是否需要**心跳**？当前靠 `status` 与 `delta` 事件天然产生周期输出；若将来引入前置代理导致空闲超时断开，再补心跳事件。
3. 进度提示文案是否要可配置？当前硬编码在工具内，与工具实现同处，改起来成本低。
4. 是否要把流式接口的**最大生成时长**做成配置？当前依赖模型调用超时（`knowledge_llm_timeout`）与 agent 最大步数间接约束。

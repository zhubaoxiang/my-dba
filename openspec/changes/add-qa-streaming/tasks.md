## 1. 数据模型

- [ ] 1.1 `apps/knowledge/models.py`：`QaMessage` 增加 `is_complete`（BooleanField，默认 `True`）
- [ ] 1.2 `src/sql/pg_struct.sql`：同步 `qa_message` 建表语句
- [ ] 1.3 `src/sql/patch.sql`：追加幂等增量补丁（`ADD COLUMN IF NOT EXISTS`）
- [ ] 1.4 `apps/knowledge/serializers.py`：`QaMessageSerializer` 输出 `is_complete`

## 2. 后端：共享模式判定重构

- [ ] 2.1 把 `agent.answer()` 拆为 `_prepare()`（预检 + 决定执行路径：仅通用模型 / 无知识库 / 无命中回退 / agent 编排）与执行两部分
- [ ] 2.2 确认同步接口行为不变，既有 88 项测试全通过

## 3. 后端：流式生成

- [ ] 3.1 从 `_strip_tool_markup` 抽出逐行判定谓词 `_is_markup_line()`，原函数改为复用它（保证非流式行为逐字节不变）
- [ ] 3.2 实现行缓冲过滤器：token 累积进 buffer，只把「到最后一个换行为止」的完整行交付过滤，结束时 flush 残余
- [ ] 3.3 `qa/tools.py`：`build_tools()` 增加 `emit` 回调，各工具在开头上报进度文案
- [ ] 3.4 实现 `answer_stream()`，产出结构化事件（`status` / `delta` / `done` / `error`），**不在此层做 SSE 文本格式化**
- [ ] 3.5 token 来源：`agent.stream(..., stream_mode="messages")`，三重过滤——节点为 `agent`、是 `AIMessageChunk`、且无 `tool_call_chunks`
- [ ] 3.6 非 agent 路径（仅通用模型 / 无知识库 / 无命中回退）改用 `build_chat_model(...).stream()`，沿用 `_PLAIN_SYSTEM_PROMPT`
- [ ] 3.7 向量服务故障仍向上抛：预检阶段抛 → 统一格式 JSON；开流后抛 → `error` 事件

## 4. 后端：接口与持久化

- [ ] 4.1 `QaSessionView` 新增 `ask_stream` action（`@action(detail=False, methods=["POST"], url_path="ask-stream")`）
- [ ] 4.2 SSE 帧格式化抽成纯函数（便于无 DB 测试）
- [ ] 4.3 预检失败仍走 `baseviews.ResponseError` 系列，带对应 HTTP 状态码，**不落任何库**
- [ ] 4.4 开始生成前落**用户消息**；同时创建助手消息**占位行**
- [ ] 4.5 生成期间内容只进内存 buffer，**不逐 token 写库**
- [ ] 4.6 正常结束：一次性 update（content / sources / tools_used / is_fallback / note），`is_complete=True`
- [ ] 4.7 `finally` 处理中断（`GeneratorExit` / 客户端断开）：落已收内容，`is_complete=False`
- [ ] 4.8 响应头：`Content-Type: text/event-stream`、`Cache-Control: no-cache`、`X-Accel-Buffering: no`
- [ ] 4.9 确认路由可达（action 挂在已注册的 `QaSessionView` 上，无需改 `urls.py`）

## 5. 前端

- [ ] 5.1 `api/knowledge.js`：新增 `qaApi.askStream()`——`fetch` + `AbortController` + 手写 SSE 解析（`EventSource` 不支持 POST）
- [ ] 5.2 `views/knowledge/qa.vue`：推送占位气泡，`delta` 逐字追加并自动滚底
- [ ] 5.3 展示 `status` 进度提示，正文开始后清除
- [ ] 5.4 「停止生成」按钮（生成中替代发送按钮）
- [ ] 5.5 `is_complete === false` 时展示「已中断」标记（含会话回看）
- [ ] 5.6 `error` 事件弹出提示并标记该轮不完整

## 6. 测试基础设施

- [x] 6.1 建立 `test` 测试库（`conf.ini` 的 `[db_test]`，参数与 `dba` 一致）
- [x] 6.2 `src/utils/test_runner.py`：新增 `SqlSchemaTestRunner`——建好测试库后灌入 `sql/pg_struct.sql`
- [x] 6.3 `src/settings/settings.py`：配置 `TEST_RUNNER`
- [x] 6.4 验证守卫：全部为 `SimpleTestCase` 时**不建测试库、不灌 DDL**（真实库结构不受影响）
- [x] 6.5 验证 `TestCase` 可对业务表读写

## 7. 测试

- [ ] 7.1 SSE 帧格式化纯函数测试
- [ ] 7.2 行缓冲过滤器的增量行为测试（含工具语法外泄场景与全被剥离时的兜底）
- [ ] 7.3 `_prepare()` 各分支测试（四种执行路径 + 预检错误）
- [ ] 7.4 中断落库测试（`TestCase`，须以 `ENV_TYPE=test` 运行）
- [ ] 7.5 回归：既有 88 项全通过

## 8. 文档

- [ ] 8.1 `docs/knowledge-qa.md`：新增「流式输出」章节（事件协议、架构、限制）
- [ ] 8.2 `docs/knowledge-qa.md`：改写关键设计决策表中「不做流式输出」一条，及「已知限制」里对应两条
- [ ] 8.3 `docs/knowledge-qa.md`：API 表补入 `/qa-session/ask-stream`，并写明两个接口**落库时机不同**
- [ ] 8.4 `docs/architecture.md`：「接口约定」补充流式接口作为统一响应格式的**显式例外**及其边界
- [ ] 8.5 `docs/development.md`：测试与质量检查一节写明 DB 测试的跑法（`ENV_TYPE=test`）、`TEST_RUNNER` 的作用，并把 `utils/test_runner.py` 补入核心文件表
- [ ] 8.6 `docs/deployment.md`：写明流式的并发占用（gthread 线程被整条连接占住）

## 9. 校验

- [ ] 9.1 `python .ci/custom-checks/scaffold_check.py`
- [ ] 9.2 `ruff check src/ --config .ci/lint-rules/ruff.toml` 与 `ruff format --check`
- [ ] 9.3 `cd static && npm run build`
- [ ] 9.4 `openspec validate add-qa-streaming --strict --no-interactive`


## 1. 数据模型

- [x] 1.1 `apps/knowledge/models.py`：`QaMessage` 增加 `is_complete`（BooleanField，默认 `True`）
- [x] 1.2 `src/sql/pg_struct.sql`：同步 `qa_message` 建表语句与列注释
- [x] 1.3 `src/sql/patch.sql`：追加幂等增量补丁（`ADD COLUMN IF NOT EXISTS`）
- [x] 1.4 `QaMessageSerializer` 输出 `is_complete`（`exclude = ["update_time"]` 已自动包含，无需改动）

## 2. 后端：共享模式判定重构

- [x] 2.1 把 `agent.answer()` 拆为 `_prepare()`（预检 + 决定执行路径）与执行两部分，并抽出 `_decide_after_hits()` / `_agent_tools()` 供两条路径共用
- [x] 2.2 确认同步接口行为不变，既有 88 项测试全通过

## 3. 后端：流式生成

- [x] 3.1 从 `_strip_tool_markup` 抽出逐行判定谓词 `_is_markup_line()`，原函数改为复用它
- [x] 3.2 实现行缓冲过滤器 `_LineBuffer`：只交付到最后一个换行为止的完整行，结束时 flush 残余
- [x] 3.3 `qa/tools.py`：提供 `TOOL_STATUS` / `status_text()` 工具名→进度文案映射（**原计划的 `emit` 回调已否决**，见 design.md D3）
- [x] 3.4 实现 `stream_events()`，产出结构化事件（`status` / `delta` / `done`），不做 SSE 文本格式化
- [x] 3.5 token 三重过滤 `_is_answer_token()`：节点为 `agent`、是 `AIMessageChunk`、且无 `tool_call_chunks` / `tool_calls`
- [x] 3.6 非 agent 路径（仅通用模型 / 无知识库 / 无命中回退）走 `_stream_plain()`，沿用 `_PLAIN_SYSTEM_PROMPT`
- [x] 3.7 向量服务故障仍向上抛：由 `prepare_stream()` 在开流前抛出
- [x] 3.8 拆出 `content_text()`（不 strip）专供流式路径——`message_text()` 的逐块 strip 会吃掉换行，令整篇回答被行缓冲连坐丢弃（见 D5）

## 4. 后端：接口与持久化

- [x] 4.1 `QaSessionView.ask_stream`（`POST /qa-session/ask-stream`）
- [x] 4.2 `_sse()`：SSE 帧格式化抽成纯函数
- [x] 4.3 预检失败仍走 `baseviews` 统一格式，**不落任何库**
- [x] 4.4 开流前落用户消息，并创建助手消息占位行
- [x] 4.5 生成期间内容只进内存 buffer，不逐 token 写库
- [x] 4.6 正常结束一次性 update，`is_complete=True`
- [x] 4.7 `finally` 处理中断（`GeneratorExit`）：落已收内容，`is_complete=False`
- [x] 4.8 响应头：`Content-Type: text/event-stream`、`Cache-Control: no-cache`、`X-Accel-Buffering: no`
- [x] 4.9 路由可达（action 挂在已注册的 `QaSessionView` 上，无需改 `urls.py`；已由接口测试覆盖）

## 5. 前端

- [x] 5.1 `api/knowledge.js`：`askStream()`——`fetch` + `AbortController` + 手写 SSE 解析
- [x] 5.2 `views/knowledge/qa.vue`：占位气泡、`delta` 逐字追加、自动滚底
- [x] 5.3 展示 `status` 进度提示，正文开始后清除
- [x] 5.4 「停止生成」按钮（生成中替代发送按钮）
- [x] 5.5 `is_complete === false` 时展示「已中断」标记（含会话回看）
- [x] 5.6 `error` 事件弹出提示并标记该轮不完整

## 6. 测试基础设施

- [x] 6.1 建立 `test` 测试库（`conf.ini` 的 `[db_test]`，参数与 `dba` 一致）
- [x] 6.2 `src/utils/test_runner.py`：`SqlSchemaTestRunner`——建好测试库后灌入 `sql/pg_struct.sql`
- [x] 6.3 `src/settings/settings.py`：配置 `TEST_RUNNER`
- [x] 6.4 验证守卫：全部为 `SimpleTestCase` 时**不建测试库、不灌 DDL**
- [x] 6.5 验证 `TestCase` 可对业务表读写

## 7. 测试

- [x] 7.1 `SseFrameTests`：帧格式、中文不转义、载荷可解析
- [x] 7.2 `LineBufferTests`：增量放行、残余 flush、标记行丢弃、全被剥离、与同步路径结果一致
- [x] 7.3 `AnswerTokenFilterTests` + `ModeNormalizeTests`：三重过滤条件与模式归一
- [x] 7.4 `QaStreamTests`：事件序列、进度早于答案、中间轮次正文不外泄、进度不泄露入参
- [x] 7.5 `StreamPersistenceTests`（`TestCase`）：中断保留半截并标记不完整、完整回答不误标、SQL 两处都声明了新列
- [x] 7.6 `StreamEndpointTests`（`TransactionTestCase`）：路由、响应头、SSE 帧、落库、断连保留、提问先落库
- [x] 7.7 回归：全部 131 项通过

> 7.6 用 `TransactionTestCase` 是必须的：`TestCase` 把用例包在 atomic 里（autocommit=False），
> 测试客户端请求结束发 `request_finished` 会触发 `close_old_connections`，它见到 autocommit
> 与配置不符就关掉连接，导致流结束后的落库拿到已关闭的连接。生产环境请求周期内 autocommit
> 一致，不存在该问题。

## 8. 文档

- [x] 8.1 `docs/knowledge-qa.md`：新增「流式输出」章节（事件协议、架构、限制）
- [x] 8.2 `docs/knowledge-qa.md`：改写关键设计决策表中「不做流式输出」一条，及「已知限制」里对应两条
- [x] 8.3 `docs/knowledge-qa.md`：API 表补入 `/qa-session/ask-stream`，并写明两个接口**落库时机不同**
- [x] 8.4 `docs/architecture.md`：「接口约定」补充流式接口作为统一响应格式的**显式例外**及其边界
- [x] 8.5 `docs/development.md`：写明 DB 测试的跑法（`ENV_TYPE=test`）、`TEST_RUNNER` 的作用、`TransactionTestCase` 的注意事项，并把 `utils/test_runner.py` 补入核心文件表
- [x] 8.6 `docs/deployment.md`：写明流式的并发占用（gthread 线程被整条连接占住）

## 9. 校验

- [x] 9.1 `python .ci/custom-checks/scaffold_check.py`
- [x] 9.2 `ruff check src/ --config .ci/lint-rules/ruff.toml` 与 `ruff format --check`
- [x] 9.3 `cd static && npm run build`
- [x] 9.4 `openspec validate add-qa-streaming --strict --no-interactive`

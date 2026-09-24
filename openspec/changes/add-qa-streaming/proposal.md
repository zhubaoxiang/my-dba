# Change: 知识问答支持流式输出

## Why

当前问答接口一次性返回完整回答。而 agent 编排要经过「预检索 → 若干轮工具调用 → 生成」，实测首字节等待可达数秒到十几秒，使用者在整段结束前看不到任何反馈，只能对着空白等待。

归档设计（`archive/2026-09-23-add-rag-knowledge-qa/design.md` 的 D10）当初以「与统一响应格式冲突」为由放弃流式，把体验问题记为待办。本次推翻该决策：**让使用者看到回答正在生成，比让这个接口保持格式统一更重要**。

## What Changes

- 新增流式问答接口 `POST {SYS_NAME}/v1/qa-session/ask-stream`，边生成边输出回答文本
- 流式过程中同时输出**面向使用者的进度提示**（如「正在检索知识库…」），使工具执行阶段可见
- 新增**中止生成**能力；中止或异常断连时，**已生成的内容保留**并标记为不完整
- 新增 `qa_message.is_complete` 字段承载「是否完整」
- 前端问答页改为流式呈现，增加停止按钮与不完整标记
- **BREAKING（限本接口）**：流式接口成功回答时响应体**不遵循** `{code, message, data}` 统一响应格式。**预检失败**（参数非法、会话不存在、未配置模型、向量服务不可用）仍按统一格式返回并带对应 HTTP 状态
- 原有同步接口 `/qa-session/ask` **行为不变**，继续遵循统一响应格式

## Impact

- 受影响能力：`knowledge-qa`
- 受影响代码：
  - `src/apps/knowledge/models.py`（`is_complete` 字段）
  - `src/apps/knowledge/qa/agent.py`（拆出共享的模式判定，新增流式执行）
  - `src/apps/knowledge/qa/tools.py`（工具增加进度回调）
  - `src/apps/knowledge/views.py`（新增流式 action 与落库）
  - `src/apps/knowledge/serializers.py`（输出 `is_complete`）
  - `src/sql/pg_struct.sql` / `src/sql/patch.sql`
  - `static/src/api/knowledge.js` / `static/src/views/knowledge/qa.vue`
- 不影响：`datasource-management`、`metadata-catalog`、`llm-provider`、`knowledge-base`
- 无新增第三方依赖

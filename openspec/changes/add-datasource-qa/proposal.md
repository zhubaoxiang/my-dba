# Change: 增加按数据源范围的知识问答

## Why

当前问答页面只能选择知识库与回答模式。后端 agent 已有一个查询单个数据源表结构的工具，但前端没有数据源选择入口，也无法回答「这个环境有多少张表」「哪些表有明显问题」这类需要汇总最新快照与健康问题清单的问题。

## What Changes

- 问答页面在知识库、模式之外增加数据源选择；选中后，本次提问限定在该数据源范围内
- 会话持久化选中的数据源，重新打开会话时恢复；清除选择后不再沿用旧值
- 选中数据源时，agent 获得「数据源概况」与「问题清单」工具；「仅知识库」模式仍不访问数据源
- 只选数据源、不选知识库时直接进入 agent 用数据源作答，不再回退到通用模型
- 知识库检索无命中但已选数据源时，继续用数据源工具作答，不静默回退
- 回答的 `tools_used` 如实记录实际调用的数据源工具

## Impact

- Affected specs: `knowledge-qa`
- Affected code:
  - `src/apps/knowledge/models.py`、`views.py`、`serializers.py`
  - `src/apps/knowledge/qa/agent.py`、`qa/tools.py`
  - `src/apps/datasource/services.py`
  - `static/src/views/knowledge/qa.vue`
  - `src/sql/pg_struct.sql`、`src/sql/patch.sql`

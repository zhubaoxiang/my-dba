# Change: 增加 DDL 评审（建表 / 改表语句预检）

## Why

使用者在写 `CREATE TABLE` / `ALTER TABLE` 时，最容易踩的坑要到执行时才暴露：表没有主键、外键没有索引、加完字段变成宽表、删列会带走索引、非空表加 `NOT NULL` 直接失败。现有 12 条库表规则吃的是**采集快照**，还没有的快照表根本评不了；SQL 分析里的 DDL 规则只判语句本身（删表、锁表），看不到「这次改动会新引入什么结构问题」。

NL2SQL 已经能生成建表语句，但归档设计 `2026-10-12-add-nl2sql/design.md` 的 Open Question 1 明确写着：DDL 没有结构校验，可选方向正是「用 `sqlglot` 解析成结构，再喂给现有库表规则」。本变更把这个方向做实。

## What Changes

- 新增 **DDL 评审**能力：粘贴一条或多条 PostgreSQL DDL，可选绑定数据源，在**不执行、不连目标库**的前提下给出：
  - 本次改动**新引入或加重**的设计问题（复用现有库表规则）
  - 语句本身的操作风险（执行阻断、依赖影响、大表操作）
  - 逐条语句的「应用状态」与变更摘要
- 支持 `CREATE TABLE`、`ALTER TABLE`、`CREATE/DROP INDEX`、`DROP TABLE`、`DROP COLUMN`、`TRUNCATE`；多语句**按顺序**合并到内存快照。
- 给规则声明增加**作用域**：12 条库表规则中 8 条同时适用于 DDL 评审；仍遵守现有的启用 / 级别 / 阈值覆盖。
- 新增独立页面（SQL 组「DDL 评审」）；NL2SQL 生成 DDL 后提供「去 DDL 评审」的交接入口。
- **明确不支持**：MySQL、DML / 查询、视图 / 物化视图 / 序列 / 类型 / 函数、`CREATE TABLE AS/LIKE/INHERITS/PARTITION OF`。
- 不落库、不调用模型、不执行任何语句、不新增数据库表或 DDL。

## Impact

- 受影响规格：
  - **新增** `ddl-review`
  - **修改** `analysis-rule-registry`（规则声明增加作用域，规则清单可见）
  - **修改** `nl2sql`（DDL 结果可转到 DDL 评审；不改变「生成不执行」的边界）
- 受影响代码：
  - 新增 `src/apps/ddlreview/`（解析、顺序合并、语句风险、编排、视图、测试）
  - `src/apps/datasource/services.py`：新增完整快照读取与「对快照形态数据跑表规则」两个跨模块契约
  - `src/apps/sqlanalysis/services.py`：新增解析契约
  - `src/apps/datasource/rules/`：`RuleDefinition` 增加 `scopes`
  - `src/config/urls.py`：注册 `/v1/ddl-review`
  - `static/`：新增页面、路由、API 客户端；NL2SQL 页增加交接按钮
  - `docs/`：新增 `ddl-review.md`，更新 `architecture.md`、`datasource.md`、`nl2sql.md`、`README.md`
- 部署形态不变：无新依赖、无新表、无迁移；服务仍只允许部署在内网。

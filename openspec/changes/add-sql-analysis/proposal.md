# Change: SQL 规范与性能分析

## Why

产品路线四能力中的第 3 项（`openspec/project.md` 已规划为 `add-sql-analysis`）。使用者的诉求是「我这条 SQL 写得对吗？会不会慢？」——目前系统能让他看懂库表结构（分析数据库表）和查资料（知识问答），但**拿到一条 SQL 时仍然无从判断**：有没有语法问题、会不会全表扫描、这条 DELETE 会不会误删全表。

这是唯一能直接回答「这条语句有没有坑」的能力，且**风险与价值都集中在一处**——它能连真实库执行。

## What Changes

- 新增能力 `sql-analysis` 与模块 `src/apps/sqlanalysis/`
- **解析与格式化**：按目标方言解析 SQL，给出语法错误位置；输出美化后的语句
- **静态规则分析**：声明式规则在 AST 上判定，产出带稳定 `rule_code` 与级别的问题清单（DML/DDL/查询不限）
- **结构校验**：绑定数据源且有采集快照时，校验语句引用的表/列是否存在、类型是否匹配；**未绑定时跳过并明确说明**
- **只读试运行与执行计划**：**显式触发**。只读语句真跑（行数上限 + 语句超时 + 只读会话），DML/DDL **只出 `EXPLAIN`，绝不执行**
- **大模型解读**：解读规则命中的问题，并可补充规则之外的观察——**两块在响应结构上分区**，模型推测必须标注
- 新增前端页面：SQL 输入、数据源选择、问题清单、执行计划、解读分区
- 新增依赖 **sqlglot**（见 design.md D1 的必要性评估）
- **不落库**：分析是一次性的，不新增数据表

## Impact

- 受影响能力：新增 `sql-analysis`；**不改动**任何现有能力的既有需求
- 受影响代码：
  - `src/apps/sqlanalysis/`（**新增模块**：parse / formatting / rules / schema / explain / interpret / views / serializers）
  - `src/apps/base/baseviews.py`（新增无模型 ViewSet 基类，供只有自定义 action 的接口使用）
  - `src/config/urls.py`（注册路由）
  - `src/requirements.txt`（sqlglot）
  - `static/src/views/` / `static/src/api/`（新增页面与接口封装）
- 跨模块依赖：通过 `datasource.services.list_snapshot_tables()` 读采集快照（该函数是 datasource 模块声明的跨模块契约，不直接查其 model）
- 无数据表变更，无需 SQL 补丁
- 分期交付：① 静态层 → ② 需快照的规则 → ③ 执行层 → ④ 模型解读与前端
- **前置依赖**：本变更排在 `add-analysis-rule-registry` **之后**实施。使用者要求 SQL 规则纳入**统一的规则注册表**，界面上要有规则的统一展示与管理入口——而注册表、规则管理 API 与管理页正是该变更的交付物。接入时需给注册表增加一个 **scope 维度**（库表分析 / SQL 分析），使同一个页面承载两类规则；该步由本变更负责，见 design.md 的「前置依赖」

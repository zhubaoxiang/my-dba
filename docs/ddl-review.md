# DDL 评审（建表 / 改表语句预检）

使用者要在执行一条 `CREATE TABLE` / `ALTER TABLE` 之前，知道这次改动会不会失败、会不会锁表、会不会引入新的结构问题。本能力把 DDL 解析成表结构，**在最近一次采集快照上顺序预演**，然后给出结论——**不连接目标库、不执行任何语句、不落库、不调用模型**。

对应模块：`src/apps/ddlreview/`。

## 定位

| 做 | 不做 |
|----|------|
| 在快照上预演一条或多条 PostgreSQL DDL | 执行任何语句、连接目标库 |
| 报本次改动**新引入 / 加重**的设计问题 | 重复列出表上已有的老问题 |
| 报语句本身的操作风险（阻断 / 依赖 / 大表） | 判断业务语义、数据分布、应用代码依赖 |
| 支持 `CREATE` / `ALTER` / 索引 / 删除 / 清空 | 支持 MySQL、DML / 查询、视图 / 序列 / 函数 |
| 给建议 | 生成修复语句 |

**与 SQL 分析的分工**：SQL 分析回答「这条语句写得对不对、危不危险」；DDL 评审回答「这条变更会把表结构改成什么样、会不会失败」。两者都不执行。

## 流程

```
SQL（可多条）+ 可选 datasource_id
  → 方言校验（仅 PostgreSQL）
  → 解析（apps.sqlanalysis.services.parse_sql）
  → 取最近快照完整数据（apps.datasource.services.latest_snapshot_data）
  → 逐条顺序应用 DDL 到内存表结构
       ├─ 非 DDL → 标记「未参与评审」，继续
       ├─ 不支持的 DDL 形态 → 标记原因，停止后续
       └─ 阻断性风险 → 不应用，停止后续
  → 每条已应用语句：
       ├─ 表规则（DDL 作用域）在 before / after 各跑一次 → 只取新增 / 加重
       ├─ 语句风险（执行阻断 / 依赖影响 / 大表操作）
       └─ 变更摘要（增删改的列、索引、约束）
  → 汇总：按语句分组 + 总体结论 + 未评审说明
```

顺序语义是必要的：`CREATE TABLE` 之后才能 `ALTER`，`ADD COLUMN` 之后才能建索引。遇到无法应用或阻断性风险时**停止后续评审**，因为后续语句可能依赖这条失败语句造成的状态。

## 支持的 DDL

| 类别 | 支持 |
|------|------|
| `CREATE TABLE` | 列定义、主键、唯一、外键（含 `ON DELETE` / `ON UPDATE`）、CHECK、DEFAULT、NOT NULL、`IF NOT EXISTS` |
| `ALTER TABLE` | 加 / 删列（含 `IF NOT EXISTS`）、改类型、`SET/DROP NOT NULL`、`SET/DROP DEFAULT`、加 / 删约束（PK / UNIQUE / FK / CHECK）、改列名、改表名 |
| 索引 | `CREATE [UNIQUE] INDEX`（含 `CONCURRENTLY` / `IF NOT EXISTS`）、`DROP INDEX` |
| 破坏性操作 | `DROP TABLE`、`DROP COLUMN`、`TRUNCATE` |
| 多语句 | 按顺序应用；中途无法应用即停，并说明从第几条起未评审 |

**明确不支持**：`CREATE TABLE AS` / `LIKE` / `INHERITS` / `PARTITION OF`、视图 / 物化视图 / 序列 / 类型 / 函数、事务控制语句。非 DDL 语句会标记「未参与评审」；不支持的 DDL 形态会说明原因并停止后续。

## 输出口径

### 只报本次新引入 / 加重

设计问题由现有库表规则产出。系统在变更前、变更后各跑一次，按 `(rule_code, schema, table, column, fix_key)` 对齐：

- `after` 新出现 → 标「新增」
- `after` 级别升高 → 标「加重」
- `before` 已存在且没有变化 → **不列**，摘要只给「既有问题 N 条未重复列出」
- 宽表是唯一需要额外判断「加重但键不变」的规则：列数增加时标为「加重」

这样使用者看到的是**这次改动造成的东西**，而不是一份混杂着历史问题的完整清单。

### 语句风险与设计问题分区

| 分区 | 回答的问题 | 例子 |
|------|-----------|------|
| 设计问题 | 这次改动让表结构变差了什么 | 新表没有主键、加外键没索引、加列变宽表 |
| 语句风险 | 这条语句本身会不会失败、会不会锁表、影响谁 | 目标表不存在、删列有索引依赖、大表建索引未带 `CONCURRENTLY` |

语句风险分两类：

- **阻断**：语句会执行失败，例如目标表 / 列不存在、非空表加 `NOT NULL` 且无默认值、`DROP INDEX` 删到支撑主键的索引、依赖未处理的 `DROP` / `TRUNCATE`。
- **非阻断**：能执行但有代价，例如类型收窄、大表加外键 / 建索引、PostgreSQL 11 之前加带默认值的列、改名影响应用代码。

**阻断性风险同时阻止合并**：编排层不会把这条语句应用到内存快照，后续语句也不再评审。

### 结论不用「通过」

结论只有：

- `存在执行阻断`
- `部分未评审`
- `有高危风险，需确认` / `有风险，需确认` / `有轻微提示`
- `未发现新增问题`
- `无法评审`

**一条语句都没能应用时用「无法评审」**（例如单条 `ALTER` 的目标表不存在）；已有语句应用成功、后续被阻断时才用「存在执行阻断」——前者连结构都没能建起来，说「有阻断」会让人以为前面的部分已经评审过。

评审只覆盖结构与快照，不覆盖业务语义、数据分布与应用代码依赖。用「通过」会让人以为这条 DDL 已经完整验证过。

### v1 不生成修复语句

DDL 还没执行，给一条 `ALTER` 修复语句会让人以为可以直接跑；而且修复往往不是单条语句（宽表要拆表、类型收窄要先清数据）。设计问题只给规则原有的建议文案。

## 规则作用域

12 条库表规则中，8 条同时适用于 DDL 评审：

| 适用 | 规则 |
|------|------|
| ✅ | 无主键、外键缺索引、重复/冗余索引、可疑字段类型、浮点金额、长字符主键、宽表、级联删除链 |
| ❌ | 超大表、未使用索引、索引体积失衡（需要运行时行数 / 体积 / 使用统计）、孤立表（新建表天然孤立，报出来是噪音） |

作用域写在规则声明里（`RuleDefinition.scopes`），DDL 模块**不维护第二份适用规则清单**。规则仍遵守 `analysis_rule` 的启用 / 级别 / 阈值覆盖：停用一条规则，DDL 评审也跳过它，并在响应里列出被停用的规则。

## API

```
POST /my-dba/v1/ddl-review/review
```

| 字段 | 必填 | 说明 |
|------|------|------|
| `sql` | 是 | 一条或多条 DDL |
| `datasource_id` | 条件 | `CREATE TABLE` 可省略；`ALTER` / 索引 / 删除 / 清空必须提供，且该数据源必须已采集 |
| `dialect` | 否 | 可省略；默认 PostgreSQL，MySQL 明确返回 4017 |

响应要点：

```json
{
  "verdict": {"level": 1, "label": "存在执行阻断", "text": "..."},
  "syntax": {"ok": true, "statement_count": 2, "errors": []},
  "snapshot": {"performed": true, "table_count": 105, "collect_time": "2026-10-19 10:00:00", "note": ""},
  "summary": {
    "statements": 2, "applied": 1, "not_reviewed": 1,
    "design_issue_total": 2, "risk_total": 3, "blocking_total": 1,
    "existing_issue_total": 12, "disabled_rules": []
  },
  "statements": [
    {
      "index": 1, "sql": "...", "kind": "ALTER_TABLE", "applied": true, "apply_error": "",
      "change_summary": {"columns_added": ["remark"], "indexes_added": ["idx_orders_user"]},
      "design_issues": [{"change": "new", "rule_code": "fk_without_index", "issue_level": 2, "target": "...", "description": "...", "suggestion": "..."}],
      "risks": [{"code": "add_fk_scan", "level": 2, "blocking": false, "target": "...", "description": "...", "suggestion": "..."}]
    }
  ],
  "not_reviewed": [{"index": 2, "reason": "前一条语句存在执行阻断，后续未评审。", "partial": true}],
  "rules": {"evaluated": ["no_primary_key"], "disabled": []}
}
```

错误口径：

| 情形 | code |
|------|------|
| `sql` 为空 / 参数格式非法 | 4000 |
| 方言不是 PostgreSQL | 4017 |
| `ALTER` / 索引 / 删除 / 清空未选数据源 | 4017 |
| 数据源不存在 | 4004 |
| 数据源未采集 | 4017 |
| 解析失败 | 2000，结论 `无法评审`，带行列错误 |
| 目标表不在快照中 | 2000，逐语句标记无法应用；没有可评审语句时结论为 `无法评审` |

## 前端

- 侧边栏 SQL 组新增「DDL 评审」页。
- 数据源可选；页面常驻说明「仅支持 PostgreSQL、依据快照、不执行任何语句、只覆盖结构与快照」。
- 结果按语句分卡片：应用状态、变更摘要、新增/加重设计问题、语句风险；顶部给总体结论与快照时间。
- NL2SQL 生成 DDL 后出现「去 DDL 评审」按钮，通过 `sessionStorage`（键 `my-dba:ddl-review-draft`）交接 SQL 与数据源。
- 未绑定数据源时明确列出「级联删除链、外键目标、依赖影响未评审」，不显示成「没有问题」。

## 已知限制

- **依据快照**：不反映快照之后的结构变化；页面显示采集时间，建议评审前先重新采集。
- **依赖检查只覆盖快照**：视图、存储过程、触发器不在快照里，因此「谁依赖这张表」判不出来。
- **schema 歧义**：未限定 schema 时按唯一匹配 / `public` 解析；同名表跨 schema 且无法唯一确定时报错，不猜。
- **不支持 MySQL**：与修复语句只支持 PostgreSQL 的口径一致，不给出未验证的结论。
- **不判断业务语义**：加唯一约束是否会因重复值失败、改名是否影响应用代码，只能提示，不能验证。
- **不生成修复语句**：v1 只给建议。
- **大表阈值复用** `big_table` 的 `big_table_rows` / `big_table_size_mb`（默认 1000 万行 / 10 GB）：偏保守，千万行以下的表不报锁风险，与「宁漏不误」一致。

## 相关文件

| 文件 | 职责 |
|------|------|
| `apps/ddlreview/parser.py` | DDL → 语句类别与语法树；判定形态是否受支持 |
| `apps/ddlreview/types.py` | sqlglot 类型 → 快照 `data_type` 词表映射 |
| `apps/ddlreview/merge.py` | 顺序合并到内存表结构 |
| `apps/ddlreview/risks.py` | 语句级风险（阻断 / 依赖 / 大表） |
| `apps/ddlreview/services.py` | 编排、问题增量、结论 |
| `apps/ddlreview/views.py` | `POST /v1/ddl-review/review`（`StatelessView`） |
| `apps/datasource/services.py` | `latest_snapshot_data()` / `analyze_snapshot_data()` 跨模块契约 |
| `apps/sqlanalysis/services.py` | `parse_sql()` / `type_family()` 跨模块契约 |
| `static/src/views/ddlreview/index.vue` | 页面 |
| `static/src/api/ddlReview.js` | 前端 API |

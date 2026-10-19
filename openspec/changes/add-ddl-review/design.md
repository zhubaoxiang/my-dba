## Context

这个能力站在三块现成的东西上：

- `apps/datasource/`：库表规则、采集快照（含索引、外键动作、体积、`database_version`）
- `apps/sqlanalysis/parse.py`：按方言把 SQL 解析成 `sqlglot` 语法树，并区分只读 / DML / DDL
- `apps/sqlanalysis/schema.py`：类型归族（`numeric` / `string` / `temporal` …）

缺口是：库表规则只吃**已经存在**的采集快照，而使用者要评审的是**还没执行**的 DDL。`sqlglot` 已经能把 `CREATE TABLE` / `ALTER TABLE` / `CREATE INDEX` 解析成结构化节点（本设计前已用真实 AST 验证），因此可以把「DDL → 内存表结构 → 现有规则」接起来。

与既有基调对齐的地方：不落库、不执行、不调用模型、未做校验必须说明原因、宁可漏报不误报。

## Goals / Non-Goals

- **Goals**
  - 让使用者在执行 DDL 之前，看到这次改动**新引入**的设计问题与执行风险
  - 让 NL2SQL 生成的建表语句有一个确定的去处：转到 DDL 评审
  - 复用现有规则与快照，不新建第二套规则引擎
- **Non-Goals**
  - 不执行、不连目标库、不改生产库
  - 不支持 MySQL（明确报错，不静默降级）
  - 不评审 DML / 查询（交给 SQL 分析）
  - 不判断业务语义、应用代码依赖、数据分布
  - 不生成修复语句（v1）
  - 不做视图 / 存储过程 / 触发器依赖分析

## Decisions

### D1: 新模块 `apps/ddlreview`，不并进 `sqlanalysis` 或 `datasource`

- **Decision**：新增独立模块，跨模块只走服务函数。
- **Why**：它同时依赖解析（`sqlanalysis`）、快照与表规则（`datasource`）。放进任一边都会让那个模块的职责变宽；独立模块与 `nl2sql` / `sqlanalysis` / `knowledge` 的分法一致，也便于前端给一个清晰入口。
- **Alternatives considered**：
  - 并入 `sqlanalysis`：解析与 SQL 规则现成，但 DDL 评审要合并快照、做问题增量，和「贴一条 SQL 看结论」是两种工作流；页面与文档会被迫混在一起。
  - 并入 `datasource`：规则与快照现成，但解析能力在另一边；`datasource` 已经承担采集、分析、指标、关系，继续加会失控。

### D2: 在内存快照上**顺序应用**，遇到无法应用即停

- **Decision**：从最近一次快照深拷贝一份表结构，按语句顺序逐条应用。每条语句应用成功后记录 `before` / `after`；遇到无法应用（目标表不存在、列不存在、重复建表等）时记录原因并**停止**后续语句的评审。
- **Why**：迁移脚本天然是顺序语义——`CREATE TABLE` 之后才能 `ALTER`，`ADD COLUMN` 之后才能建索引。逐条独立评审会得出错误结论。停止而不是跳过，是因为后续语句可能依赖这条失败语句造成的状态；继续评审会给出看似完整、实则基于错误状态的结论。
- **非 DDL 语句**：标记为「未参与评审」并继续。它们不改变结构，不会影响后续 DDL 的状态；明确列出，不静默忽略。
- **Alternatives considered**：
  - 只接受单条语句：与 SQL 分析的边界一致，但迁移脚本要逐条贴，变更前后的关联被切断。
  - 多语句全部无条件应用：语义上更省事，但一条失败后后续状态不可信。

### D3: 只报**本次新引入 / 加重**的设计问题

- **Decision**：DDL 适用的表规则在 `before` 与 `after` 上各跑一次，按 `(rule_code, schema, table, column, fix_key)` 对齐；只上报 `after` 新出现、或 `after` 级别更高的条目。既有问题不重复列出，摘要里给数量。
- **Why**：一条 `ALTER TABLE` 背后可能已经有十几条老问题。全列出来，使用者分不清哪些是这次改出来的，清单会失去行动指向。
- **`fix_key` 必须进身份**：`duplicate_index` 的 `column_name` 可能相同而具体索引不同；不带 `fix_key` 会把两个不同的重复索引合成一条。
- **「加重但键不变」的特殊情况**：`wide_table` 在表已经超阈值后继续加列，键不变但描述中的列数变大。v1 对 `wide_table` 做一次列数比较，列数增加时标为「加重」；其余规则只按「新出现 / 级别升高」判定。这条限制写进文档。
- **停用的规则**：与库表分析共用 `analysis_rule` 覆盖项。规则被停用后，DDL 评审也不跑它，并在响应里列出被停用的规则。

### D4: 规则作用域是**声明**，不是 DDL 模块里的第二份清单

- **Decision**：`RuleDefinition` 增加 `scopes`，默认 `(SNAPSHOT,)`；8 条规则额外声明 `DDL`。注册表提供按作用域取规则的能力。
- **Why**：规则清单的唯一来源是代码声明。如果 DDL 模块自己维护一份「适用规则 code」列表，新增规则时必然会被忘记，两份清单迟早不一致。
- **8 条适用的规则**：`no_primary_key`、`fk_without_index`、`duplicate_index`、`suspicious_column_type`、`float_money_column`、`long_text_primary_key`、`wide_table`、`cascade_delete_chain`。
- **4 条不适用的规则及理由**：
  - `big_table` / `unused_index` / `index_larger_than_data`：需要运行时行数、体积或索引使用统计，DDL 还没有这些数据。
  - `isolated_table`：新建表天然没有入边外键，报出来是噪音；它回答的是「这张表是不是遗留表」，不是建表时的设计缺陷。

### D5: 语句风险与设计问题**分区**，不重复跑 SQL 规则

- **Decision**：DDL 评审自己实现一层语句级风险检查，不再对每条语句跑 `sqlanalysis` 的 18 条规则。
- **Why**：
  - SQL 规则的主语是「一条语句」，而 DDL 评审的风险需要**快照上下文**（这张表被谁引用、索引是否支撑约束、表有多大）。两边的输入不同。
  - 复用会把 `drop_table` / `truncate_table` 等告警与 DDL 风险重复报一遍；使用者会看到两条说同一件事的条目。
  - 多语句顺序合并后，SQL 规则需要的 schema 状态在变（`CREATE TABLE` 之后的 `ALTER` 才合法），直接复用还要为每条语句挑选 before/after 索引，边界反而更绕。
- **复用的部分**：只复用 **解析**（`sqlanalysis.parse`）与**类型归族**（`sqlanalysis.schema.type_family`），不复用规则。

### D6: v1 只支持 PostgreSQL，MySQL 明确报错

- **Decision**：方言校验不通过时返回 4017，并说明「DDL 评审目前只支持 PostgreSQL」。
- **Why**：与 `fixes.py` 只产出 PostgreSQL 语句同一条理由——类型词表、`ALTER` 语法、约束行为都有方言差异。本仓库的真机环境没有 MySQL，给未验证的结论比不给更糟。
- **Alternatives considered**：MySQL 跑不依赖方言的设计规则。无主键、宽表这类判据看似通用，但类型长度、`ALTER` 合并、大小写与 schema 语义都可能不准；一旦误报，会直接损害清单可信度。

### D7: 数据源是**条件必填**

- **Decision**：
  - 纯 `CREATE TABLE` 脚本可以不选数据源：只跑不依赖跨表上下文的规则，并明确说明「级联删除链、依赖影响未评审」。
  - 出现 `ALTER` / `INDEX` / `DROP` / `TRUNCATE` 时必须选数据源，且该数据源必须已采集；否则 4017。
  - 目标表不在最新快照中时 4017，并提示可能是采集之后新建的，先重新采集。
- **Why**：改表评审必须知道「改之前长什么样」。没有快照就只剩猜，而猜出来的结论一定是错的。这与 NL2SQL 的「没有结构就报错，不硬写」是同一条。
- **不静默降级**：未绑定数据源时，页面与响应都列出「未评审项」，不显示成「没有问题」。

### D8: v1 不给修复语句，只给建议

- **Decision**：设计问题沿用规则原有的 `description` / `suggestion`；语句风险给出可操作的提示；不生成 `ALTER` 修复语句。
- **Why**：DDL 还没执行，给一条「修复语句」会让人误以为可以直接跑。而且修复往往不是单条语句（例如宽表要拆表、类型收窄要先清数据），模板拼出来的东西不是确定的。现有「修复语句」机制服务的是**已存在**的问题，不适用于「尚未执行的变更」。

### D9: 类型归一化是最大的实现风险

- **Decision**：在 `ddlreview` 内做一层明确的 sqlglot 类型 → 快照 `data_type` 词表映射，并单测覆盖。
- **Why**：采集器存的是 `information_schema.columns.data_type`（`character varying` / `double precision` / `numeric` / `timestamp without time zone`），而 sqlglot 的 `DataType.Type` 是 `VARCHAR` / `DOUBLE` / `DECIMAL` / `TIMESTAMP`。规则按快照词表判定；映射错了会**静默漏报**，而不是报错。
- **映射范围**：PG 常用类型 + `serial` 家族（`smallserial` → `smallint`、`serial` → `integer`、`bigserial` → `bigint`，并补默认值语义）；未知类型保留小写原文，规则按 `unknown` 处理（不报）。
- **`length`**：`varchar(n)` 取 `n`；无长度上限取 `-1`（与采集器一致）；`numeric(p,s)` 的快照 `character_maximum_length` 为 `-1`，规则目前不使用精度，保持 `-1`。

### D10: 前端独立页面 + NL2SQL 交接，不内嵌评审

- **Decision**：新增「DDL 评审」页面；NL2SQL 生成 DDL 后显示「去 DDL 评审」按钮，通过 `sessionStorage` 的 `my-dba:ddl-review-draft` 键带上 SQL 与数据源；评审页读取后立即清除该键。
- **Why**：生成与评审是两件事。内嵌评审会让 NL2SQL 页面同时承担「生成」与「变更影响」两种结论，且评审结果无法在编辑后再看。独立页面允许使用者先改 SQL 再评审，也让多语句迁移脚本有地方贴。
- **为什么用 `sessionStorage`**：SQL 可能很长，放 query string 会超长；它是同一标签页内的一次性交接，不需要持久化。

### D11: 结论不用「通过」

- **Decision**：结论只有 `存在执行阻断` / `有风险，需确认` / `未发现新增问题` / `部分未评审` / `无法评审`。
- **Why**：「通过」会被读成「这条 DDL 是对的」。评审只覆盖结构与快照，不覆盖业务语义、数据分布与应用代码依赖。与 SQL 分析的「未发现问题」、NL2SQL 的「校验只覆盖结构」是同一条措辞纪律。

### D12: 大表阈值复用 `big_table` 的规则阈值

- **Decision**：`add_fk_scan` / `create_index_lock` / `add_column_default_rewrite` 的「大表」判定复用规则注册表里 `big_table` 的 `big_table_rows` / `big_table_size_mb`（默认 1000 万行 / 10 GB），即使 `big_table` 规则本身被停用也照用它的阈值。
- **Why**：这两个数本来就是系统对「大表」的业务定义，另起一套 `ddl_lock_risk_*` 阈值会让使用者在两处维护同一个概念。代价是偏保守——千万行以下的表不报锁风险；这与「宁漏不误」一致，漏报比在每次改表时刷一条「可能锁表」更便宜。
- **Alternatives considered**：为 DDL 单独增加更低的锁风险阈值。更灵敏，但需要论证「多少行算大」，且会与首页的「超大表」问题口径打架。

## 数据流

```
SQL（多条） + datasource_id?
  │
  ├─ 方言校验（PostgreSQL）
  ├─ sqlanalysis.services.parse_sql()
  ├─ datasource.services.latest_snapshot_data()   ← 可选；ALTER/INDEX/DROP 必填
  │
  ▼
逐条语句：
  ├─ 非 DDL → not_reviewed，继续
  ├─ 不支持的 DDL 形态 → not_reviewed，停止后续
  ├─ 应用失败 → apply_error，停止后续
  └─ 应用成功 → before / after 表结构
        │
        ├─ 表规则（DDL 作用域）before/after 各跑一次 → 增量
        ├─ 语句风险（执行阻断 / 依赖 / 大表）
        └─ 变更摘要（加/删/改的列、索引、约束）
  │
  ▼
按语句分组 + 总体结论 + 未评审说明
```

## API

```
POST /my-dba/v1/ddl-review/review
```

| 字段 | 必填 | 说明 |
|------|------|------|
| `sql` | 是 | 一条或多条 DDL |
| `datasource_id` | 条件 | `CREATE TABLE` 可省略；其余语句必填 |
| `dialect` | 否 | 可省略；有数据源时按库类型推断，默认 PostgreSQL |

响应要点：

```json
{
  "verdict": {"level": 1, "label": "存在执行阻断", "text": "第 2 条语句会因目标表不存在而失败。"},
  "snapshot": {"performed": true, "table_count": 105, "collect_time": "2026-10-19 10:00:00"},
  "summary": {
    "statements": 3,
    "applied": 2,
    "not_reviewed": 1,
    "design_issue_total": 2,
    "risk_total": 3,
    "blocking_total": 1,
    "existing_issue_total": 12,
    "disabled_rules": ["wide_table"]
  },
  "statements": [
    {
      "index": 1,
      "sql": "ALTER TABLE ...",
      "kind": "ALTER_TABLE",
      "applied": true,
      "apply_error": "",
      "change_summary": {"columns_added": ["remark"], "indexes_added": ["idx_orders_user"]},
      "design_issues": [
        {"change": "new", "rule_code": "fk_without_index", "issue_level": 2, "target": "app.orders.user_id", "description": "...", "suggestion": "..."}
      ],
      "risks": [
        {"code": "add_fk_scan", "level": 2, "blocking": false, "target": "app.orders", "description": "...", "suggestion": "..."}
      ]
    }
  ],
  "not_reviewed": [
    {"index": 3, "reason": "非 DDL，未参与评审"}
  ]
}
```

业务错误：

| 情形 | code |
|------|------|
| `sql` 为空 / 参数格式非法 | 4000 |
| 方言不是 PostgreSQL | 4017 |
| `ALTER` / `INDEX` / `DROP` 未选数据源 | 4017 |
| 数据源不存在 | 4004 |
| 数据源未采集 | 4017 |
| 目标表不在快照中 | 2000，逐语句标记无法应用；没有可评审语句时结论为 `无法评审` |
| 解析失败 | 2000，结论 `无法评审`，带行列错误 |

> **为什么目标表不在快照中不返回 4017**：多语句脚本里前面的语句可能已经可评审。把它做成整单失败，会让「第 1 条 CREATE 有问题」这类结论也拿不到。环境问题（没选数据源、没采集、方言不支持）才整单返回 4017。

## 语句风险清单（v1）

| code | 级别 | 触发 | 是否阻断 |
|------|------|------|---------|
| `target_table_exists` | 高 | `CREATE TABLE` 目标已存在且未带 `IF NOT EXISTS` | 是 |
| `target_table_missing` | 高 | `ALTER` / `DROP` / `INDEX` 目标不在快照中 | 是 |
| `target_column_exists` | 高 | `ADD COLUMN` 的列已存在，或 `CREATE TABLE` 中列重复定义 | 是 |
| `target_column_missing` | 高 | `DROP` / `ALTER` / 索引裸列不存在 | 是 |
| `target_index_exists` | 高 | `CREATE INDEX` 的索引名已存在 | 是 |
| `target_index_missing` | 高 | `DROP INDEX` 的索引不存在且未带 `IF EXISTS` | 是 |
| `target_constraint_exists` | 高 | `ADD PRIMARY KEY` 时表已有主键 | 是 |
| `target_constraint_missing` | 高 | `DROP CONSTRAINT` 的约束不存在 | 是 |
| `referenced_table_missing` | 高 | 外键引用的表不在快照中 | 是 |
| `referenced_column_missing` | 高 | 外键引用的列不在目标表 | 是 |
| `drop_index_constraint_backed` | 高 | 要删的索引支撑主键 / 唯一约束 | 是 |
| `drop_table_dependency` | 高 | 删表时仍有外键或约束引用 | 是（除非 `CASCADE`，仍提示） |
| `drop_column_dependency` | 高 | 删列时该列参与索引 / 主键 / 外键 | 是（除非 `CASCADE`，仍提示） |
| `truncate_dependency` | 高 | 清空的表被外键引用 | 视 `CASCADE` |
| `not_null_without_default` | 高 | 非空表加 `NOT NULL` 且无默认值 | 是 |
| `type_narrowing` | 中 | 类型族改变或字符长度 / 数值精度收窄 | 否（执行前需核对数据） |
| `add_fk_scan` | 中 | 大表加外键且未带 `NOT VALID` | 否 |
| `create_index_lock` | 中 | 大表建索引且未带 `CONCURRENTLY` | 否 |
| `add_column_default_rewrite` | 中 | PG < 11 上非空表加带默认值的列 | 否 |
| `rename_breaks_code` | 低 | 改表名 / 列名 | 否（应用代码依赖无法验证） |

大表判定复用规则注册表的 `big_table_rows` / `big_table_size_mb`（见 D12）；「非空表」直接取快照中的 `row_count > 0`。

**阻断性风险同时是合并门槛**：编排层在应用语句前先跑风险检查；只要出现 `blocking=True`，就不把这条语句合并进内存快照，后续语句也不再评审。应用成功后再检查外键目标等需要「变更后状态」的条件（当前实现把能前移的检查都放在了应用前）。

## Risks / Trade-offs

| 风险 | 影响 | 缓解 |
|------|------|------|
| **类型映射错误** | 规则静默漏报 | 映射表集中一处 + 单测覆盖常用类型；未知类型不判 |
| **快照过期** | 增量结论与实际不符 | 页面显示快照时间；响应带 `collect_time`；提示先重新采集 |
| **顺序合并语义不全** | 某些 DDL 无法应用 | 明确列出不支持的形态；无法应用即停，不猜 |
| **schema 歧义** | 改错表 | 未限定 schema 时按 `public` / 唯一匹配解析；歧义时明确报错 |
| **依赖检查只覆盖快照** | 视图、存储过程依赖看不到 | 文档与界面写明「依赖检查仅覆盖快照中的表 / 列 / 索引 / 约束」 |
| **多语句评审耗时** | 大库 × 多语句变慢 | 规则是纯 Python、单库百表量级毫秒级；先整体跑，后续按需优化 |
| **使用者把「未发现新增问题」读成「DDL 正确」** | 过度信任 | D11 的措辞纪律；界面常驻「只覆盖结构与快照」说明 |

## Migration Plan

- 无数据库变更：不新增表、不修改 DDL、不需要 `patch.sql`。
- 不新增依赖：`sqlglot` 已在 `requirements.txt`。
- 回滚：回退代码、路由与前端页面即可；没有数据需要清理。
- 上线后首次使用前，建议先对一个已采集的数据源跑一条 `CREATE TABLE` 与一条 `ALTER TABLE` 做真实页面核对。

## 已收敛的实现细节

- **`RENAME`**：`sqlglot` 对 `RENAME COLUMN` / `RENAME TO` 给出独立的 `RenameColumn` / `AlterRename` 节点（已用真实 AST 验证）；合并时必须同步更新索引、主键、外键里的旧名字，否则后续语句会基于旧名字判定。
- **改名不能让老问题变成「新增」**：问题身份含表名 / 列名；`RENAME` 后变更前问题的键会变。实现上先用本次的改名映射把 `before` 问题键改到新名字，再做增量对比。
- **首页数字带**：v1 不增加 DDL 评审条目。它是动作型能力，没有对应的持久规模；首页的五格是「规模一览」。

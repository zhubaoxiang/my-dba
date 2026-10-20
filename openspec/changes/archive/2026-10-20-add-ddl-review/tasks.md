## 1. 规则作用域

- [x] 1.1 `utils/custom_enum.py` 增加 `RuleScopeEnum`（`SNAPSHOT` / `DDL`）
- [x] 1.2 `apps/datasource/rules/definitions.py` 的 `RuleDefinition` 增加 `scopes` 字段，默认 `(SNAPSHOT,)`
- [x] 1.3 给 8 条适用规则声明 `(SNAPSHOT, DDL)`：`no_primary_key` / `fk_without_index` / `duplicate_index` / `suspicious_column_type` / `float_money_column` / `long_text_primary_key` / `wide_table` / `cascade_delete_chain`
- [x] 1.4 其余 4 条保持 `(SNAPSHOT,)`，**不写** DDL 作用域
- [x] 1.5 `rules/registry.py` 增加按作用域过滤的入口；`load_effective_rules()` 支持 `scope` / `codes` 过滤
- [x] 1.6 单测：默认作用域、8 条 DDL 规则、过滤后条数正确、停用规则不出现
- [x] 1.7 规则清单接口返回 `scopes`；规则页给 DDL 适用规则加标签

## 2. 跨模块服务契约

- [x] 2.1 `datasource/services.py` 增加 `latest_snapshot_data(datasource_id)`：返回完整快照数据（含 `database_version` / `tables` / `unavailable` / `collect_time`），**不含凭据**
- [x] 2.2 `datasource/services.py` 增加 `analyze_snapshot_data(data, rule_codes=None, scope=None)`：对快照形态数据跑表规则；不改查库行为
- [x] 2.3 单测：完整快照读取、未采集返回空、按 code / scope 过滤、既有 `list_snapshot_tables` 行为不变
- [x] 2.4 `sqlanalysis/services.py` 增加 `parse_sql(sql, dialect)` 解析契约（薄封装 `parse.parse_sql`）
- [x] 2.5 单测：解析契约对合法 / 非法 SQL 的返回与现有 `analyze_sql` 一致

## 3. DDL 解析与类型归一化

- [x] 3.1 `apps/ddlreview/parser.py`：把 `sqlglot` AST 转成内部操作（`CreateTable` / `AlterTable` / `CreateIndex` / `DropIndex` / `DropTable` / `DropColumn` / `Truncate` / `Comment`）
- [x] 3.2 支持 `CREATE TABLE` 的列定义、PK、UNIQUE、FK（含 `ON DELETE` / `ON UPDATE`）、CHECK、DEFAULT、NOT NULL、`IF NOT EXISTS`
- [x] 3.3 支持 `ALTER TABLE` 的 `ADD/DROP COLUMN`、`ALTER COLUMN TYPE`、`SET/DROP NOT NULL`、`SET/DROP DEFAULT`、`ADD/DROP CONSTRAINT`、`ADD PK/UNIQUE/FK`、`RENAME COLUMN`、`RENAME TO`
- [x] 3.4 支持 `CREATE [UNIQUE] INDEX` / `DROP INDEX`；索引列支持普通列与表达式
- [x] 3.5 不支持的形态（`CREATE TABLE AS/LIKE/INHERITS/PARTITION OF`、视图 / 物化视图 / 序列 / 类型 / 函数）返回明确原因，不静默跳过
- [x] 3.6 `apps/ddlreview/types.py`：sqlglot 类型 → 快照 `data_type` 词表映射；`serial` 家族归一；`length` 规则与采集器一致（无界 `-1`）
- [x] 3.7 单测：PG 常用类型、`varchar(n)` / `text` / `numeric(p,s)` / `serial` 家族 / 未知类型；与采集器样例对比
- [x] 3.8 单测：各类 DDL 的 AST → 操作结果；不支持的形态

## 4. 顺序合并

- [x] 4.1 `apps/ddlreview/merge.py`：深拷贝快照表结构，按语句顺序应用操作
- [x] 4.2 实现表 / 列 / 索引 / 主键 / 外键的增删改；`RENAME` 同步更新索引、主键、外键引用
- [x] 4.3 无法应用时记录原因并停止后续：目标表不存在、列不存在、重复表 / 列、外键目标不存在等
- [x] 4.4 非 DDL 语句标记 `not_reviewed` 并继续；不改变结构
- [x] 4.5 未限定 schema 的解析规则：唯一匹配 / `public` / 歧义报错
- [x] 4.6 单测：CREATE 后 ALTER、ADD COLUMN 后 CREATE INDEX、DROP 后 CREATE、RENAME 后 ALTER、中途失败即停、非 DDL 跳过

## 5. 设计问题增量

- [x] 5.1 `apps/ddlreview/services.py`：对每条应用成功的语句，在 `before` / `after` 上分别跑 DDL 作用域规则
- [x] 5.2 问题身份 `(rule_code, schema, table, column, fix_key)`；只取新出现 / 级别升高
- [x] 5.3 `wide_table` 特判：列数增加时标为「加重」
- [x] 5.4 既有问题不重复列出；摘要给出 `existing_issue_total`
- [x] 5.5 停用规则不参与；响应带 `disabled_rules`
- [x] 5.6 单测：新增无主键、加列变宽表、加外键缺索引、加索引变重复、删索引后外键缺索引、既有问题不重复、规则停用、宽表加重

## 6. 语句风险

- [x] 6.1 `apps/ddlreview/risks.py`：实现设计文档「语句风险清单」中的 16 条检查
- [x] 6.2 依赖检查使用快照中的外键、主键、唯一约束、索引；不解析视图 / 存储过程
- [x] 6.3 大表 / 非空判断使用快照 `row_count` / 体积；阈值集中定义并写进文档
- [x] 6.4 每条风险带 `code` / `level` / `blocking` / `target` / `description` / `suggestion`
- [x] 6.5 单测：每条风险的正例与反例；`IF NOT EXISTS` / `CASCADE` / `CONCURRENTLY` / `NOT VALID` 的例外
- [x] 6.6 单测：风险与设计问题不重复报同一件事

## 7. API 与编排

- [x] 7.1 `apps/ddlreview/services.py::review(sql, datasource_id=None, dialect=None)`：方言校验 → 解析 → 取快照 → 顺序合并 → 增量 + 风险 → 结论
- [x] 7.2 结论规则：`存在执行阻断` / `有风险，需确认` / `未发现新增问题` / `部分未评审` / `无法评审`
- [x] 7.3 `apps/ddlreview/views.py`：`DdlReviewView(StatelessView)` 的 `review` action
- [x] 7.4 `config/urls.py` 注册 `{SYS_NAME}/v1/ddl-review`
- [x] 7.5 错误口径：4000 / 4017 / 4004；解析失败仍 2000 + `无法评审`
- [x] 7.6 响应不含凭据；`snapshot.collect_time` 必带
- [x] 7.7 API 单测：无数据源建表、ALTER 缺数据源、未采集、表不在快照、MySQL、解析失败、多语句中途失败

## 8. 前端

- [x] 8.1 `static/src/api/ddlReview.js`：`review(payload)`
- [x] 8.2 `static/src/views/ddlreview/index.vue`：数据源选择（可选）、SQL 输入、评审按钮、结论区、逐语句卡片
- [x] 8.3 页面常驻说明：仅 PostgreSQL、依据快照、不执行任何语句、只覆盖结构与快照
- [x] 8.4 逐语句展示：应用状态、变更摘要、新增 / 加重设计问题、语句风险
- [x] 8.5 未评审项与快照时间可见；没有数据源时列出未评审内容，不显示成「没有问题」
- [x] 8.6 路由：SQL 组新增「DDL 评审」
- [x] 8.7 NL2SQL 生成 DDL 后显示「去 DDL 评审」按钮；`sessionStorage` 交接 SQL 与数据源
- [x] 8.8 后端给 NL2SQL 响应补 `is_ddl`（或等价的语句类别信息），前端据此显示按钮

## 9. 文档

- [x] 9.1 新增 `docs/ddl-review.md`：定位、流程、支持的 DDL、输出口径、API、限制
- [x] 9.2 `docs/architecture.md` 代码分层补 `apps/ddlreview/`
- [x] 9.3 `docs/datasource.md` 规则清单一节补「DDL 作用域」与 8 条适用规则
- [x] 9.4 `docs/nl2sql.md` 已知限制更新：DDL 仍不在 NL2SQL 内校验，但可转到 DDL 评审
- [x] 9.5 `README.md` 能力表与文档索引补 DDL 评审
- [x] 9.6 写明「只报本次新引入 / 加重」「不生成修复语句」「MySQL 不支持」三条口径

## 10. 校验与真机核对

- [x] 10.1 `python .ci/custom-checks/scaffold_check.py`
- [x] 10.2 `ruff check src/ --config .ci/lint-rules/ruff.toml` 与 `ruff format --check`
- [x] 10.3 `ENV_TYPE=test python manage.py test apps.ddlreview apps.datasource apps.sqlanalysis apps.nl2sql`
      —— 测试库连通后完整执行：**455 个测试通过**（含 `TestCase` 数据库组）
- [x] 10.4 **真实快照核对**：在 local 环境两个已采集的 PostgreSQL 快照（31 / 105 张表）上逐条跑过：
      - 新建表（PK + FK + 索引 + 浮点金额列）→ 判出 `float_money_column` 与 `fk_without_index`
      - 对已有「无主键」问题的表加列 → `no_primary_key` **没有**重复列出，`existing_issue_total=1`
      - 合法外键（跨表与自引用）→ 只报 `fk_without_index`，没有 `referenced_table_missing`
      - 在非主键索引上建同列索引 → 判出 `duplicate_index`
      - 删除被索引的列 → `drop_column_dependency` 阻断
      - 用已存在的表名建表 → `target_table_exists` 阻断
      - 干净的加列 → 无新增设计问题、无语句风险
      - 类型映射：`varchar(255)` 主键 → `long_text_primary_key`；`double precision` → `float_money_column`；
        `created_at varchar(32)` → `suspicious_column_type`
      - 级联链：真实快照只有 1 个 CASCADE 出边且没有二层链，`cascade_delete_chain` 为 0 是数据使然
- [x] 10.5 **误报核对**：上述真实快照场景没有观察到误报；既有问题不重复列出。
      初版探针在**主键索引**上建重复索引，`duplicate_index` 按设计不报（规则排除 primary），
      换成非主键索引后正常判出——这是取表问题，不是规则漏判
- [x] 10.6 **真实页面核对**：在 local 环境用 headless Chrome 实际点过：
      - 无数据源 `CREATE TABLE`：结果区显示结论、新增设计问题、未评审提示
      - 通过 `sessionStorage` 交接的 `ALTER`：SQL 与数据源预填，评审后显示「未发现新增问题」、快照时间、语句卡片与变更摘要
      - 窄屏（800px headless viewport）下设计问题表需横向滚动；桌面宽度可完整显示，未发现功能阻断
      - NL2SQL 交接的**接收端**已验证（预填 SQL 与数据源）；发送端按钮依赖真实模型生成，本次未触发
- [x] 10.7 `openspec validate add-ddl-review --strict --no-interactive`

## 11. 实现时要注意

- [x] 11.1 `RENAME COLUMN` / `RENAME TO` 必须同步更新快照中的索引、主键、外键引用，否则后续语句会基于旧名字判定
- [x] 11.2 表达式索引的列是 SQL 文本而非裸列名；`fk_without_index` 不应把表达式索引当成覆盖
- [x] 11.3 `CREATE TABLE` 的 `serial` 家族在快照词表里是 `bigint` / `integer`，但默认值语义不同；类型判定不要把它当成 `unknown`
- [x] 11.4 非 DDL 语句与不支持的 DDL 必须出现在 `not_reviewed` 中，不能只打日志
- [x] 11.5 快照过期是**正常情况**，不是错误；页面提示重采，不阻止评审
- [x] 11.6 不要为了 DDL 评审给 `analysis_rule` 表加记录或改 DDL；作用域来自代码声明

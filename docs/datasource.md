# 分析数据库表（数据源纳管与元数据采集）

把 PostgreSQL / MySQL 数据源接进来，**只读**采集库表元数据——表结构、列定义、索引、主键与外键关联、行数与占用体积。采集一次之后可随时回看，不用每次去连库翻 `information_schema`。

采集完由内置规则给出一份隐患清单，回答「这个库有没有坑」。

对应模块：`src/apps/datasource/`。

## 数据表

DDL 见 `src/sql/pg_struct.sql`（全量）与 `src/sql/patch.sql`（增量），**本项目禁止 Django migration**。

| 表 | 说明 |
|----|------|
| `datasource` | 被纳管的数据源配置，密码可逆加密存储 |
| `metadata_snapshot` | 每次采集生成的不可变快照，原始结果存 JSONB |
| `collect_task` | 采集任务与状态 |
| `catalog_issue` | 库表健康问题清单 |
| `analysis_rule` | 分析规则的可配置元数据（启用开关 / 级别 / 阈值，见文末「进行中」） |

## API

路径前缀 `{SYS_NAME}/v1`，当前 `SYS_NAME = 'my-dba'`。实现见 `apps/datasource/views.py` 的 `DatasourceView` 与 `CatalogView`。

### 数据源管理

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/datasource` | 数据源列表（分页） |
| POST | `/datasource` | 新建数据源 |
| GET | `/datasource/{id}` | 数据源详情 |
| PUT | `/datasource/{id}` | 修改（`password` 留空表示不修改） |
| DELETE | `/datasource/{id}` | 软删除 |
| POST | `/datasource/test` | 测试未保存的连接参数 |
| POST | `/datasource/{id}/test` | 测试已保存数据源的连通性 |
| POST | `/datasource/{id}/collect` | 触发采集，立即返回任务 |
| GET | `/datasource/{id}/tasks` | 采集任务列表 |

### 元数据查询

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/catalog/snapshots?datasource_id=` | 快照列表 |
| GET | `/catalog/latest-snapshot?datasource_id=` | 最新快照 |
| GET | `/catalog/summary?snapshot_id=` | 库表总览统计（表数量、各级问题数、未采集项、参与评估的规则数） |
| GET | `/catalog/tables?snapshot_id=&keyword=` | 表清单（分页、可搜索） |
| GET | `/catalog/table?snapshot_id=&table=&schema=` | 表详情（列 / 索引 / 主键 / 外键 / 体积） |
| GET | `/catalog/issues?datasource_id=&issue_level=` | 问题清单（按级别筛选） |
| GET | `/catalog/diff?snapshot_id=&compare_snapshot_id=` | 两个快照的结构差异 |

> ⚠️ 以上接口继承 `baseviews.AnyLogin`，**系统内不做任何认证与角色校验**。这意味着只要服务可达，任何人都能读写数据源配置（含目标库凭据）、触发采集、并借「连接测试」探测内网。**分析规则的写接口（`PUT` / `POST …/reset`）同样没有保护**——改一条规则会影响**所有使用者**看到的问题清单，例如把「无条件的 DELETE」这类高危规则关掉。**服务只能部署在内网**——完整说明见 [architecture.md 的「认证与鉴权」](architecture.md#认证与鉴权)。

## 健康分析规则

规则由 `src/apps/datasource/rules/` **声明式注册**：每条规则 = 一个判定函数 + 一条 `RuleDefinition`（`code` / 名称 / 默认级别 / 适用层级 / 默认阈值）。规则以稳定的字符串 `code` 标识。

| 规则 code | 名称 | 默认级别 | 触发条件 | 对你意味着什么 |
|-----------|------|---------|---------|--------------|
| `no_primary_key` | 无主键表 | 高 | 表没有主键 | 无法按行定位，写进重复数据不容易发现 |
| `fk_without_index` | 外键缺索引 | 中 | 外键列没有以它为前导列的索引 | 按它做关联查询会全表扫描 |
| `duplicate_index` | 重复或冗余索引 | 中 / 低 | 索引列完全相同；或非唯一索引是另一索引的前缀 | 白占空间、拖慢写入；上线前删掉即可 |
| `unused_index` | 疑似未使用索引 | 低 | 索引使用次数为 0 且表行数超过阈值 | 只增加写入成本，可评估删除 |
| `big_table` | 超大表 | 中 | 行数或占用空间超过阈值 | 查询、加字段、加索引都会明显变慢 |
| `suspicious_column_type` | 可疑字段类型 | 中 / 低 | 用字符类型存时间；超长 varchar；大对象类型 | 字符串比较用不上时间运算与范围索引；大字段拖慢整表扫描 |
| `isolated_table` | 孤立表 | 低 | 既无外键，也未被任何表引用 | 可能是遗留表，改表前先确认是否还有人用 |

分析是**纯计算**：输入是采集快照，不访问数据库，因此同一份快照可用不同阈值反复重算，也便于单测。

> **怎么看级别**：级别是按**规则类型**静态指定的，不随表体量或业务重要性变化——一张 5 行的字典表没有主键和一张 5 亿行的订单表没有主键，报出来都是「高」。所以看到「高」先结合自己知道的情况判断轻重，别当成必须立刻处理的告警。引入体量维度是后续待办。

## 配置

`src/config/conf.ini` 的 `[datasource]` 段：

| 配置项 | 说明 |
|--------|------|
| `secret_key` | 数据源密码的加密密钥，**上线前必须替换为随机值** |
| `connect_timeout` | 连接超时（秒） |
| `statement_timeout` | 语句超时（秒） |
| `exact_count` | 是否用 `COUNT(*)` 取精确行数，默认 `false`（改用估算值，避免拖垮大表） |
| `big_table_rows` / `big_table_size_mb` | 超大表判定阈值 |
| `varchar_max_length` | 可疑超长 varchar 的判定长度 |
| `unused_index_min_rows` | 判定未使用索引所需的最小表行数 |

> 阈值将在「分析规则注册表」改造完成后迁入规则表，`conf.ini` 只保留采集侧参数。改动 `conf.ini` **必须重启进程**（见 [architecture.md 的「配置层级」](architecture.md#配置层级)）。

## 安全说明

- 采集使用**独立只读短连接**，不复用 Django ORM 连接；PostgreSQL 连接显式 `set_session(readonly=True)`
- 采集只读系统目录，**不对目标库执行任何 DDL/DML**，也不支持在目标库上执行用户 SQL
- 数据源密码可逆加密存储（`utils/crypto.py`，Fernet），任何接口响应与日志都不含密码
- 加密密钥缺失时抛错，**不会回退为明文**

## 已知限制

- 采集任务走 `simple-background-task` 的**进程内内存队列**。进程重启后队列中的任务会丢失，需重新触发；任务状态以数据库 `collect_task` 为准（队列本身的说明见 [architecture.md 的「后台任务」](architecture.md#后台任务)）
- 多 worker 部署时，任务在接收该请求的 worker 进程内执行
- 「疑似未使用索引」依赖目标库的索引使用统计（PG `pg_stat_user_indexes`、MySQL `performance_schema`），取不到时跳过该规则并在快照的 `unavailable` 中留痕
- 单份快照的全部元数据存在一个 JSONB 单元格里，查询时整块载入并解析，**分页是"假分页"**。表数量上万后需要改为结构化明细表
- **快照没有保留策略**：每次采集整份复制，长期运行会持续占用存储
- MySQL 采集器尚未在真实 MySQL 上验证过（PostgreSQL 分支已端到端验证）

## 分析规则注册表

规则的**启用开关、严重级别、阈值**运行时可调，不用改代码发版。

**规则判定逻辑仍是代码**（不引入表达式引擎），只有元数据可配置：

```
代码声明（apps/datasource/rules/definitions.py）
  ├─ 规则清单、名称、说明、适用层级与默认值的唯一来源
  └─ 是「全集」——即使一条库记录都没有，规则照样生效
        ⊕ 叠加
analysis_rule 表
  └─ 只提供运行时改的三项：enabled / level / thresholds
```

**规则清单页也以代码声明为基底**：库里有记录只说明「这条被改过」，不决定它在不在清单里。所以清单**一打开就有内容**，与分析的取数口径一致——首页说 7 条，点进去也是 7 条。

库里**没有**某个 `code` 的记录时，分析用代码默认值；清单页展示默认值并把「恢复默认」置灰。第一次调整某条规则时后端为它顺手建行，**不需要任何前置动作**。

> 早期的「同步规则」按钮已随清单换基底一并移除：它原本的职责是「让页面有数据」，而这件事现在不再需要它。

**库里那三列（`name` / `description` / `object_level`）不再被读取。** 它们原是「同步」刷新的副本，同步移除后无人刷新；继续读它们，在代码里改过的名字就会**永远显示成旧的**（实测确认过）。所以清单里这三项一律取声明，只有 `enabled` / `level` / `thresholds` 取库里的行——**声明与副本冲突时，声明是对的**。

### 规则管理接口

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/analysis-rule` | 规则清单（分页），带级别/层级标签与「是否被调整过」 |
| PUT | `/analysis-rule/{code}` | 修改 `enabled` / `level` / `thresholds`（PUT 与 PATCH 同义）；库里没有则自动建行 |
| POST | `/analysis-rule/{code}/reset` | 恢复为代码声明的默认值；从未调整过的规则报参数错误 |

**规则以 `code` 定位，不用自增 id**：未落库的规则没有 id 可用，却必须可被修改。

两条刻意的约束：

- **不接受新建与删除**——规则来自代码声明，凭空建一条库记录没有对应实现；删掉库记录只会退回默认值，达不到「删除规则」的效果。要停用请改开关
- **名称、说明与适用层级不接受修改**——库里存的只是那三项覆盖值，改名字改说明无处生效

> SQL 分析规则在同一页的另一组里展示，但**只读**：它们没有覆盖机制，因此那一组不出现启用开关与级别下拉。接口见 [sql-analysis.md 的「规则清单」](sql-analysis.md#规则清单)。

### 规则集变化可被察觉

`metadata_snapshot.evaluated_rules` 记下**本次分析实际参与评估**的规则。与「当前启用数」不一致，即说明规则集在采集之后被改过——只比对当前配置是看不出来的。库表分析页会在两者不一致时给出提示。

> 停用一条规则**不影响历史数据**：已有快照及其问题清单保持不变，历史问题用自身行里快照下来的 `rule_name` 渲染，规则改名或停用后仍能正确展示。

### 阈值

阈值随规则走，存在 `analysis_rule.thresholds`（JSONB）。代码声明里的默认值与原先 `conf.ini` 的取值**逐一相同**（`10000000` / `10240` / `2000` / `10000`），行为不回归。

职责划分：采集参数是运行时基础设施配置，改完重启可以接受；分析阈值是业务策略，必须能运行时调整且可审计。

> **待办**：`conf.ini` 的 4 个分析阈值项尚未删除（`big_table_rows` / `big_table_size_mb` / `varchar_max_length` / `unused_index_min_rows`）。代码已不再读取它们，但留着会形成「改了不生效」的双源陷阱。

> **待办**：库结构补丁需要执行——`metadata_snapshot` 的 `evaluated_rules` 列尚未应用到 `dba` / `test`。执行方式：
>
> ```bash
> # 已有数据的库执行增量补丁（新库直接执行 pg_struct.sql 即可）
> psql "postgresql://<用户>:<密码>@<主机>:<端口>/<库名>" -f src/sql/patch.sql
> ```
>
> 补丁会给 `catalog_issue` 补 `rule_code` / `rule_name` / `object_level` / `schema_name` 并删除 `issue_type`，给 `metadata_snapshot` 补 `evaluated_rules`。旧问题行因 `rule_code` 为空属失效数据，确认可清空后手动执行 `DELETE FROM catalog_issue;`（该语句只写在 `patch.sql` 注释里，不会自动执行）。

> **待办**：`analysis_rule` 的 `name` / `description` / `object_level` 三列已无用途（不再被读取，只有建行时写一次），随下一次库结构补丁一并删除。删完 `analysis_rule` 就只剩 `code` / `enabled` / `level` / `thresholds`——一张没有任何副本、不会过期的覆盖表。**与上面 `evaluated_rules` 那条一起执行**，不单独占一次 DDL 往返。

> SQL 分析能力（`add-sql-analysis`）排在本变更之后，届时会给注册表增加 scope 维度，使同一个管理页承载语句级规则。

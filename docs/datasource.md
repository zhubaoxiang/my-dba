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

> ⚠️ 以上接口继承 `baseviews.AnyLogin`，**系统内不做任何认证与角色校验**。这意味着只要服务可达，任何人都能读写数据源配置（含目标库凭据）、触发采集、并借「连接测试」探测内网。**服务只能部署在内网**——完整说明见 [architecture.md 的「认证与鉴权」](architecture.md#认证与鉴权)。

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

## 进行中的分析规则注册表

对应 `openspec/changes/add-analysis-rule-registry`（18/59 任务）。目标：让规则的**启用开关、严重级别、阈值**运行时可调、无需改代码发版，并支持规则作用于库/模式/表/列不同层级。

设计边界：**规则判定逻辑仍是代码**（不引入表达式引擎），只有元数据可配置。

代码侧已完成（声明式规则、`analysis_rule` 模型、`catalog_issue` 以 `rule_code` 取代 `issue_type`），**数据库结构变更尚未执行**。落地步骤：

```bash
# 已有数据的库执行增量补丁（新库直接执行 pg_struct.sql 即可）
psql "postgresql://<用户>:<密码>@<主机>:<端口>/<库名>" -f src/sql/patch.sql
```

补丁会给 `catalog_issue` 补 `rule_code` / `rule_name` / `object_level` / `schema_name` 并删除 `issue_type`。旧行因 `rule_code` 为空属失效数据，确认可清空后手动执行 `DELETE FROM catalog_issue;`（该语句只写在 `patch.sql` 注释里，不会自动执行）。

规则管理 API、前端规则页、阈值从 `conf.ini` 迁出等尚待实现。

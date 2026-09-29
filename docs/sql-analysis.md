# 分析 SQL（规范与性能）

贴一条 SQL，就能知道它**语法对不对、规范与性能有没有坑、这条写操作危不危险**。绑定数据源后还能校验表/列是否存在，并**在不写入任何数据的前提下**拿到真实执行计划。

语句不限：查询、DML、DDL 都可以。

对应模块：`src/apps/sqlanalysis/`（**不落库**，分析是一次性的）。

## 能力

| 能力 | 说明 | 需要数据源 |
|------|------|-----------|
| 解析与格式化 | 按目标方言解析，语法错误给出**行列位置**；输出美化后的语句 | 否 |
| 规则分析 | 声明式规则在语法树上判定，产出带稳定 `rule_code` 的问题清单 | 否（部分规则需要） |
| 结构校验 | 校验语句引用的表/列是否存在、类型是否可比 | **是**（且需已采集） |
| 试运行 | 只读语句真跑拿行数与耗时；DML/DDL 只出执行计划 | **是** |
| 模型解读 | 解读规则命中的问题，并可补充规则之外的观察 | 需配置对话模型 |

## 执行边界

这是本能力唯一会碰到真实库的部分，边界划得很死：

| 语句 | 行为 |
|------|------|
| `SELECT` / `UNION` / `SHOW` / `EXPLAIN` / `DESCRIBE` | **真跑**（只读会话 + 流式游标只取前 N 行 + 语句超时） |
| `INSERT` / `UPDATE` / `DELETE` | **只出计划，绝不执行** |
| `CREATE` / `ALTER` / `DROP` / `TRUNCATE` | **只出计划，绝不执行** |
| `EXPLAIN ANALYZE` | 归入「其他」，**不执行** |
| 多语句 | 一次只接受**一条**，多语句直接拒绝 |

三条理由：

**① 判定用白名单，认不出来的一律非只读。** sqlglot 把 `SHOW` / `EXPLAIN` / `VACUUM` / `SET` 都归到兜底的 `Command` 节点，不能按节点类型放行——否则 `VACUUM`（会写统计信息）会被当成只读。方向上的保守是有意的：判错一边只是「少跑一条本来能跑的语句」，另一边是「在真实库上执行了不该执行的语句」。

**② `EXPLAIN ANALYZE` 单独挡住。** 它看着像 `EXPLAIN`，但在 PostgreSQL 上会**真执行**语句——连 DML 都会写入。

**③ 只读是服务端保证，不靠调用方自觉。** PostgreSQL 用 `set_session(readonly=True)`、MySQL 用 `SET SESSION TRANSACTION READ ONLY`。即便判定逻辑有一天出错放行了写语句，目标库自己也会拒绝。

> **多语句为什么直接拒绝**：多语句的执行语义（顺序、部分失败、其中哪一条会写）很容易看漏，直接不支持比小心处理更安全。

## 模型解读的分区

```
issues[]        规则判定      确定、可复现，同一输入必得同一结果
explanations[]  模型解读      针对上面每一条讲清「为什么」与「怎么改」
observations[]  模型推测      规则没覆盖到的观察，**未经规则验证**
```

三者在**响应里各占一个字段**，界面上也分区渲染。`observations` 带固定标注「未经规则验证」。

两条配套约束：

- **模型报出的 `rule_code` 若不在本次问题清单里，一律丢弃**——那是编造出处。与知识问答里「来源必须对得上实际检索结果」是同一条
- **未配置对话模型时不静默降级**：规则清单照常返回，解读部分说明原因并指出去哪里配

## 规则清单

18 条，`needs_schema` 的那 5 条在未绑定数据源时会被跳过，且**跳过原因会回给使用者**。

### 需要表结构

| 规则 code | 名称 | 级别 |
|---|---|---|
| `unknown_table` | 引用了不存在的表 | 高 |
| `unknown_column` | 引用了不存在的列 | 高 |
| `join_key_type_mismatch` | 连接键类型不一致 | 中 |
| `incomparable_types` | 不同类型的列直接比较 | 中 |
| `implicit_cast` | 隐式类型转换 | 中 |

### 纯语法即可判定

| 规则 code | 名称 | 级别 |
|---|---|---|
| `delete_without_where` | 无条件的 DELETE | 高 |
| `update_without_where` | 无条件的 UPDATE | 高 |
| `drop_table` | 删除表 | 高 |
| `drop_column` | 删除列 | 高 |
| `truncate_table` | 清空表 | 高 |
| `join_without_condition` | 无连接条件的 JOIN | 高 |
| `group_by_missing_column` | GROUP BY 遗漏非聚合列 | 中 |
| `function_on_column` | 列上套了函数或转换 | 中 |
| `select_star` | 使用 SELECT * | 中 |
| `insert_without_column_list` | INSERT 未列出列名 | 中 |
| `ddl_locks_table` | 会锁表的 DDL | 中 |
| `no_limit` | 无过滤也无行数限制 | 低 |
| `leading_wildcard_like` | 前缀通配的 LIKE | 低 |

几条刻意的克制——**误报会直接损害使用者对问题清单的信任**：

- **类型只比「族」不比具体类型**：`varchar(64)` 与 `text` 同族不报；`numeric` 与 `varchar` 跨族才报
- **时间列比字符串不报**：`created_at > '2024-01-01'` 是正常写法
- **`SELECT count(*)` 不报 `select_star`**：只看投影列表本身，不看嵌在函数里的星号
- **CTE 与子查询别名不当真实表**
- **多表且未限定列名时不判**：无法确定列属于哪张表

## API

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/sql-analysis/analyze` | 解析 + 格式化 + 规则 + 结构校验 + 模型解读 |
| POST | `/sql-analysis/execute` | **显式触发**的试运行 |

两个接口刻意分开：前者只读快照与内存，可以随用随调；后者会在**真实的、可能是生产的**库上执行语句，必须由使用者明确要求。

**方言**：显式指定 > 按数据源的库类型推断 > 默认 PostgreSQL。

**解析失败不是整体失败**：仍返回语法错误位置与原始语句，规则与结构校验标记为「未进行」并说明；**模型解读照常进行**，并把语法错误一并交给模型——不能解析恰恰是最需要帮助的时候。

## 配置

`src/config/conf.ini` 的 `[sqlanalysis]` 段：

| 配置项 | 说明 |
|--------|------|
| `max_rows` | 试运行最多返回的行数，默认 100 |
| `statement_timeout` | 试运行的语句超时（秒），默认 30 |

连接的建立、超时与只读会话由 `apps/datasource/services.open_readonly_connection()` 负责——本模块**不自己解密凭据、不直接读 datasource 的 model**。

## 跨模块依赖

| 依赖 | 用途 |
|------|------|
| `datasource.services.list_snapshot_tables()` | 读采集快照做结构校验 |
| `datasource.services.datasource_brief()` | 判断数据源是否存在、取它的库类型 |
| `datasource.services.open_readonly_connection()` | 试运行时的只读短连接 |
| `knowledge.llm` | 模型解读时构造对话模型 |

均走对方模块声明的服务函数，不直接查其 model。

## 已知限制

- **不落库**：分析结果不保存，无法对比改写前后的结果（需自行复制）
- **不做 SQL 改写**：只给建议，不自动改
- **多语句不支持试运行**：一次只能试运行一条
- **DDL 拿不到执行计划**：PostgreSQL 的 `EXPLAIN` 只支持 SELECT/INSERT/UPDATE/DELETE。这是目标库的能力边界，界面会如实说明，**不会把库的原始错误抛给使用者**——那会让人以为是自己写错了
- **`EXPLAIN ANALYZE` 不做**：它在 PG 上会真执行语句，边界需单独论证
- **未绑定数据源时**：5 条需快照的规则会被跳过（会在响应里列出跳过了哪些）
- **规则尚未纳入统一规则注册表**：当前规则清单只在代码里声明，启用/级别不可运行时调整。接入需给注册表增加 scope 维度，见 `openspec/changes/archive/2026-09-28-add-analysis-rule-registry/design.md`

## 相关文件

| 文件 | 职责 |
|------|------|
| `parse.py` | 按方言解析、语句分类（只读 / DML / DDL / 其他） |
| `formatting.py` | 从语法树重新生成排版 |
| `rules/definitions.py` | 18 条规则的声明与判定函数 |
| `rules/context.py` | 规则统一入参与产出入参 |
| `rules/registry.py` | 规则清单与生效规则 |
| `schema.py` | 表结构内存索引、类型归族 |
| `analyzer.py` | 遍历规则、注入上下文、汇总产出 |
| `explain.py` | 试运行与执行计划 |
| `interpret.py` | 模型解读（分区输出） |
| `serializers.py` / `views.py` | 接口 |

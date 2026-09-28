## Context

前置状态：

- 四个能力中的第 3 项（`openspec/project.md` 已规划为 `add-sql-analysis`），其余为「分析数据库表」（已实现）、「知识问答」（已实现）、「自然语言转 SQL」（未开始）
- 已归档能力见 `openspec/specs/`：`datasource-management`、`metadata-catalog`、`knowledge-base`、`knowledge-qa`、`llm-provider`
- 已有可复用的三块：**采集快照**（表/列/索引/类型，`metadata-catalog`）、**模型接入**（`llm_provider`，对话与嵌入分开配置）、**声明式规则模式**（`apps/datasource/rules/`）
- 采集器已确立「独立只读短连接、即用即关、不接入 ORM 多库路由」的做法（`apps/datasource/collectors/base.py`）
- 另一个进行中的 change `add-analysis-rule-registry`（18/59）在动 `apps/datasource`，两者不冲突
- 接口不做应用层鉴权（`AnyLogin`），访问控制依赖网络隔离

## Goals / Non-Goals

- **Goals**：拿到一条 SQL 就能知道「语法对不对、规范与性能有没有坑、这条写操作危不危险」；绑定数据源后能进一步校验表列是否存在、并**在不写入任何数据的前提下**拿到真实执行计划
- **Non-Goals**：不做 SQL 改写/自动修复；不做慢查询平台与历史趋势；不落库保存分析历史；不做自然语言转 SQL（那是另一个 change）；不让模型承担「判定有没有问题」的职责

## Decisions

### D1: 用 sqlglot 解析，而不是 Django 自带的 sqlparse

- **Decision**：引入 **sqlglot** 作为 SQL 解析与格式化引擎，按目标方言（postgres / mysql）解析成 AST，规则在 AST 上判定。
- **Why**：
  - 规则要按**语义**写而不是猜文本：「WHERE 的列上是否套了函数」「JOIN 有没有连接条件」「INSERT 是否列了列名」，这些在 AST 上是一等公民，在 token 流上只能靠模式匹配，**误报率高且跨方言不稳**
  - 方言感知：项目纳管 PG 与 MySQL，同一份 SQL 在两者下的合法性与语义不同
  - 一套库同时解决**解析 + 语法错误定位 + 美化**三件事
- **Alternatives considered**：
  - **sqlparse**（Django 3.2 自身的依赖，必然存在，零新增依赖）：只有词法分组、**没有语义 AST**，规则表达力不足；且它属传递依赖，直接依赖它本身也不规范
  - **把语法判定交给目标库**（`PREPARE` 让库报错）：最准，但**没绑数据源时完全不可用**，且规则仍需 AST 才能给出「为什么」
  - **自研解析**：SQL 语法面太宽，不可行
- **依赖评估（`architecture.md` 要求）**：纯 Python、无运行时依赖、维护活跃、支持 PG/MySQL 方言；体积小。列为 `requirements.txt` 显式依赖，不依赖传递引入

### D2: SQL 规则另建注册表，不复用 datasource 的规则注册表

- **Decision**：在 `apps/sqlanalysis/rules/` 下新建一套声明式规则（`context.py` / `definitions.py` / `registry.py`），**沿用** `apps/datasource/rules/` 的模式（`RuleDefinition` + 稳定 `code` + 统一产出入参），但**不共用**同一个注册表与上下文。
- **Why**：两者的**主语不同**。datasource 的 `RuleContext` 围绕库表快照构造（`tables` / `schemas` / `database_version` / `unavailable`），规则产出的是「哪张表/哪一列有问题」；SQL 规则的主语是**语句**，产出的是「这条语句的第几处有问题」。强行合并会让上下文承载两套无关数据，也让层级校验（库/模式/表/列）失去意义。
- **Trade-off**：两套注册表意味着规则管理界面将来要管两类。可接受——`add-analysis-rule-registry` 落地后，可再评估把 SQL 规则也纳入同一张配置表（列入 Open Questions）。
- **沿用而非重写**：`code` 稳定标识、级别语义、产出结构一致，使用者的体验不分裂。

### D3: 分析与执行拆成两个接口，执行必须显式触发

- **Decision**：
  - `POST {SYS_NAME}/v1/sql-analysis/analyze` —— 解析 + 格式化 + 规则 + 结构校验 + 模型解读。**安全，可随请求自动执行**
  - `POST {SYS_NAME}/v1/sql-analysis/execute` —— 试运行与执行计划。**必须由使用者明确要求**
- **Why**：两者的风险不对称。前者只读快照与内存计算；后者要在**真实的、可能是生产的**库上跑语句。合成一个接口意味着「点一下分析」就会连库执行，既可能误写，也会让重查询自动压到真实库上。
- **Alternatives considered**：单接口 + `execute: true` 参数（语义上更省一个接口，但「分析」这个动作本身变得有副作用，容易被误调）

### D4: 执行边界——只读真跑，DML/DDL 只 EXPLAIN

- **Decision**：
  - **只读语句**（`SELECT` / `UNION` / `SHOW` / `EXPLAIN` / `DESCRIBE`）真跑：在**只读会话**中执行，服务端游标只取前 N 行，带语句超时
  - **非只读语句**（`INSERT` / `UPDATE` / `DELETE` / `CREATE` / `ALTER` / `DROP` / `TRUNCATE` 等）**只出 `EXPLAIN`（不带 `ANALYZE`），绝不执行**
  - **多语句**（分号分隔）：按**最严格的一条**处理——只要其中有一条非只读，整批不执行，全部只出计划
- **Why**：
  - 「实际试运行」的真实价值在于拿到**真实行数与耗时**，而这只有 `SELECT` 需要
  - 用事务回滚来「安全地」跑 DML 是不可靠的：**MySQL 的 DDL 会隐式提交、回滚不掉**，且序列、触发器、外部副作用、`SET`/`VACUUM` 都不参与回滚。一旦发生就是改到了真实的库
  - `EXPLAIN`（不带 `ANALYZE`）在 PG 与 MySQL 上都**不执行语句**，只向优化器要计划，是安全的
- **Alternatives considered**：全部在事务里跑再回滚（见上，会真实改库）；一律只 `EXPLAIN`、SELECT 也不真跑（最保守，但拿不到真实行数与耗时，而「会不会慢」正需要它）
- **配套**：`EXPLAIN ANALYZE` **不在本次范围**——它在 PG 上会真执行语句（DML 在事务里虽可回滚，但代价与风险另说），列入 Open Questions

### D5: 模型解读与规则判定在响应结构上分区

- **Decision**：响应里三块**结构上分开**：
  - `issues[]` —— 规则判定，每条带稳定 `rule_code`、级别、位置、成因与建议。**可复现**
  - `explanation` —— 模型对上述问题的解读（为什么是问题、怎么改）
  - `observations[]` —— 模型提出的、**规则未覆盖**的观察，必须标注为未经规则验证
- **Why**：`issues` 由代码产出，同一输入必得同一结果，可单测；模型输出不可复现，可能不准。**两者混在一个列表里，使用者会把模型推测当成规则结论**——这与知识问答里「回退不得静默」「来源不许编造」是同一类约束：**不能让使用者误判结论的来源与确定性**。
- **配套**：未配置对话模型时 `issues` 照常产出，`explanation` / `observations` 缺席并给出可操作提示，**不静默降级**。

### D6: 不落库

- **Decision**：分析结果不持久化，不新增数据表。
- **Why**：这是一次性工具型能力——贴一条 SQL、看结论、改 SQL。没有回看需求，也不该像采集快照那样带来持续增长的存储与保留策略问题。将来若要历史，新增一张表即可，不影响本设计。
- **Trade-off**：无法对比改写前后的分析结果（使用者需自行复制）。

### D7: 给 baseviews 增加无模型 ViewSet 基类

- **Decision**：在 `apps/base/baseviews.py` 增加一个**无模型**的 ViewSet 基类（仅承载权限配置与统一响应），本模块的接口挂在它上面，只暴露自定义 action。
- **Why**：脚手架强制 ViewSet 继承 `baseviews` 的基类，而现有四个基类都是 `ModelViewSet` 子类。本模块**没有模型**，若直接继承会产生一套用不了的 list/detail 路由（访问即 500）。
- **约束**：只新增类、不改动现有基类，不影响任何既有接口。baseviews 的定位本就是「ViewSet 基类与统一响应封装」，扩展基类是它的预期用法。
- **Alternatives considered**：用函数视图（`{SYS_NAME}/api/{name}`，naming.md 允许）——但它与 ViewSet 的 `{SYS_NAME}/v1/{module}` 是两套路径约定，为一个接口破例会削弱一致性

### D8: 跨模块只读快照走 datasource 的服务契约；执行层新增一个只读连接服务

- **Decision**：
  - **结构校验**读快照：调用 `datasource.services.list_snapshot_tables(datasource_id)`（该函数的文档已声明它是跨模块契约，禁止直接查 `MetadataSnapshot`）。一次取全部表，在内存建索引后再校验语句里的表/列
  - **执行层**：在 datasource 模块**新增一个只读连接服务函数**，由它负责凭据解密、只读会话与超时配置，sqlanalysis 不自己解密、不直接读 datasource 的 model
- **Why**：现状是采集器需要调用方传入 `datasource` 实例与解密后的密码——两者都是 datasource 模块的内部物。若让 sqlanalysis 自己取，就等于跨模块碰它的 model 与凭据，违反 `architecture.md`。把「开一条只读连接」这个能力留在 datasource 侧，职责也更合理：**它才知道自己的方言、凭据与超时配置**。
- **配套**：sqlanalysis 侧只负责「这次执行取多少行、要不要 LIMIT」，连接与只读保证由 datasource 提供。

### D9: 解析失败要降级，不能整体失败

- **Decision**：若 sqlglot 无法解析（方言不支持、语法确实有错、或超出其覆盖范围），系统仍返回**语法错误信息（含位置）与原始语句**，规则判定与结构校验标记为**未能进行**并说明原因；模型解读**照常可用**（它对语法错误往往最有帮助）。
- **Why**：不能解析恰恰是最需要帮助的场景——使用者刚写完一条有语法错的 SQL。此时整体报「分析失败」等于在最需要的时候什么都不给。
- **Alternatives considered**：解析失败即整体失败（实现最简，但对使用者最没用）

### D10: 分阶段交付

- **Decision**：一个提案、四段任务，按此顺序实施与验收：① 静态层（解析/格式化/规则）→ ② 结构校验 → ③ 执行层 → ④ 模型解读 + 前端。
- **Why**：前三段各自都能独立交付价值，且风险递增。执行层是唯一碰真实库的部分，放在静态层与结构校验都被验证之后再上。模型解读放最后，因为它依赖前三者的产出结构定型。

## Risks / Trade-offs

| 风险 | 影响 | 缓解 |
|------|------|------|
| **sqlglot 解析不了某条 SQL** | 规则与结构校验无法进行 | 降级而非失败（D9）；模型解读仍可用；把解析不了的样本收集起来评估 |
| **只读真跑压到真实库** | 重查询占用目标库资源 | 必须显式触发（D3）；只读会话 + 语句超时 + 只取前 N 行；复用 datasource 已配置的超时项 |
| **`EXPLAIN` 在部分语句上仍可能报错或代价高** | 拿到的是错误而非计划 | 按「拿不到计划」如实返回并说明，不当成分析失败 |
| **模型推测被误当成规则结论** | 使用者按不可靠的结论改 SQL | 响应结构分区（D5）+ 前端分区渲染 + 明确标注 |
| **规则误报** | 使用者被噪音干扰、失去信任 | 每条规则 MUST 配正例与反例单测；级别按规则类型静态指定（与 datasource 一致，不随体量变化） |
| **新增第三方依赖** | 依赖面变宽 | 纯 Python、无运行时依赖、体积小（D1）；显式写进 `requirements.txt` 锁定版本 |
| **两套规则注册表** | 将来规则管理分散 | 记入 Open Questions，等 `add-analysis-rule-registry` 落地后统一评估 |
| **无应用层鉴权** | 任意人可让服务连真实库执行只读语句 | 与基线一致依赖网络隔离；执行接口只在内网可用；重申只允许内网部署 |

## Migration Plan

1. 新增依赖并安装：`requirements.txt` 增加 sqlglot → 重跑全部现有测试确认无回归
2. 新增模块与路由；`config/urls.py` 注册
3. **无数据表变更**，不需要执行任何 SQL 补丁
4. 验收顺序：静态层（纯内存，可先对一批真实 SQL 跑一遍看误报）→ 结构校验（对一个已采集的数据源）→ 执行层（**先对测试库**，再对任何真实库）→ 模型解读与前端
5. **回滚**：回退代码与 `requirements.txt` 即可；无数据迁移，无残留

## Open Questions

1. **规则清单是否要按团队实际踩过的坑替换？** 当前 12 条是按通用最佳实践拟的，团队若有更痛的场景（如某类 JOIN 写法、某张表的特定陷阱），替换掉泛泛的条目价值更高。
2. **是否提供 `EXPLAIN ANALYZE`？** 它在 PG 上会真执行语句；DML 可在事务里回滚，但代价与边界需单独论证。当前不做。
3. **SQL 规则是否纳入统一的规则注册表？** 等 `add-analysis-rule-registry` 落地后评估——若纳入，规则的可配置项（开关/级别/阈值）就能在同一个界面管理。
4. **结构校验的深度**：当前只校验表/列是否存在与类型是否可比较；是否要做「JOIN 的列类型是否匹配」「GROUP BY 是否遗漏非聚合列」这类更深的结构判定？
5. **是否需要「方言自动识别」**？未绑数据源时没有任何线索，当前做法是由调用方显式指定或默认 PG。

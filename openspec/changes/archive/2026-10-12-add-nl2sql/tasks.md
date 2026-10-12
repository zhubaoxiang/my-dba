## 1. 后端：sqlanalysis 服务层加对外契约

- [x] 1.1 ~~新增 `load_schema_index()` 契约~~ —— **不需要**：`datasource.services.list_snapshot_tables()` 本来就是对外契约且已给出表/列/主键/外键，`nl2sql` 直接用它取结构，不经过 sqlanalysis
- [x] 1.2 新增 `analyze_sql(sql, dialect, datasource_id)`：解析 + 跑规则 + 结构校验，返回与 `/sql-analysis/analyze` 同形的结果
- [x] 1.3 视图层改调该契约——`/analyze` **与 `/interpret` 都改走了它**（后者原先自己重跑一遍解析与规则，那段重复逻辑随之消失）；`_syntax` 提升为公开的 `syntax_of()`，供 `format` 与 `analyze` 共用
- [x] 1.4 单测：现有 sql-analysis 接口行为不回归（291 项全绿）
- [x] 1.5 两处测试的打桩目标由 `views.analyzer` 改为 `analyzer`（同一模块对象，打桩效果一致）

## 2. 后端：nl2sql 模块

- [x] 2.1 建 `apps/nl2sql/`（`generator.py` / `services.py` / `serializers.py` / `views.py`）—— ~~注册进 `INSTALLED_APPS`~~ **不需要**：`INSTALLED_APPS` 里是 `"apps"` 整包，子模块自动在内，且本模块没有模型
- [x] 2.2 `generator.py`：结构渲染、提示词（含方言提示）、调模型、抽出 SQL
- [x] 2.3 `generator.py` + `services.py`：**自修一轮**，返回 `attempts` / `repair`
- [x] 2.4 结构超出上限时抛 `StructureTooLarge`（**不截断**）
- [x] 2.5 模型不可用 / 调用失败 / 输出无可用语句 → 降级结果，不返回半成品
- [x] 2.6 单测：生成成功；首次无问题**不触发**自修；首次有问题自修成功；自修后仍失败如实返回；**自修只调用一次**（硬上限）
- [x] 2.7 单测：模型未配置 / 调用失败各自如实报错；自修失败时保留首次结果
- [x] 2.8 单测：结构超限时抛错，接口层断言**报错响应里不含任何生成结果**

> **实现时的一处收窄**（比设计更保守）：触发自修的问题**只认 `unknown_table` 与 `unknown_column`**，而不是全部"需表结构"的规则。理由：这两条是「表名/列名编错了」的直接证据，自修能确定性改善；`join_key_type_mismatch` / `incomparable_types` / `implicit_cast` 多是对写法选择的提示，让模型去改有把正确部分改坏的风险，且不影响能否执行。

## 2b. 修订：放开到「什么都能生成，什么都不执行」（2026-10-10）

**起因**：实测输入「更新研判状态是未研判的告警，将状态置为已通报」——一条明确的 `UPDATE`——生成出来的却是一条 `SELECT`。根因是**提示词要求「只写查询」**：模型读懂了 `UPDATE`，按指令不能写，于是勉强编了一条 `SELECT` 交差，**且不说明它没按使用者要求的做**。安静的错答比明确的拒绝糟得多。

- [x] 2b.1 `generator.py` 两处提示词：去掉「只写查询」，改为「使用者要什么就给什么」，并明说「硬写成查询只会让使用者以为你按他说的做了」；自修提示词补「不要借机改变语句类型」
- [x] 2b.2 `services.py` 新增 `_modifies_data()`：按 `sql-analysis` 的**同一判法**（按最严格的一条算、认不出的当会改动）算出 `modifies_data`，随响应返回，供界面标明「不会被执行」
- [x] 2b.3 前端：结果为写语句时显示「该语句会修改数据或结构；系统不会执行它，试运行也不会」；**试运行的 tip 也随之改变**（对写语句讲「会在真实库上执行」是错的）
- [x] 2b.4 单测：写语句 / DDL 的 `modifies_data` 为真，`SELECT` 为假
- [x] 2b.5 单测：**高危告警不触发自修**——「把所有用户的昵称都改掉」本就无 `WHERE`，让它加一个 `WHERE` 等于擅自改变语义；但危险必须报出来
- [x] 2b.6 规格：需求 1 由「按需求生成查询语句」改为「按需求生成 SQL」，新增「修改需求给出修改语句」「生成的写语句不被执行」两个场景
- [x] 2b.7 `docs/nl2sql.md` / `README.md` / `openspec/project.md` 同步；文档记下「曾经只做 SELECT 与为什么改」

> **保留的限制**：建表语句**没有结构校验**——表还不存在、没有快照可查，它的正确性无从自动验证。已写进文档，不能让使用者把 DDL 的「校验通过」当成「这张表设计对了」。

## 2c. 修订：真机核对后的两处（2026-10-10）

拿 `ENV_TYPE=local` 的真实库（105 张表）真跑了一遍，发现两处：

- [x] 2c.1 **上限太低**：105 张表 = 51288 字符，而写死的上限是 40000 → 该库**不选表就必然报错**，每一次都得先手动缩范围。改为 `conf.ini` 的 `[nl2sql] max_structure_chars`，默认 **120000**（不同模型上下文差得多，本来就该可调）
- [x] 2c.2 **结构不够时会硬写**：提示词原写「挑最接近的写法，不要编」——问 `alert_rule` 却只给 `alarm_whitelist` 时，模型没编表名，**但拿 `alarm_whitelist` 编了一条格式完全正常、校验通过的 SQL**，答的是另一个问题。改为让模型回 `NOT_ENOUGH_SCHEMA` 标记、接口转成「结构不足」（结论标签与「未能生成」分开）
- [x] 2c.3 单测：标记识别（含被代码块包裹的情形）、结构不足的两种文案、`SchemaInsufficient` 与 `GenerateError` 的处置分开
- [x] 2c.4 规格新增「结构不足以表达需求时如实说明」；`docs/nl2sql.md` 补该机制与「曾经硬写」的由来
- [x] 2c.5 真机复验：105 张表**能生成**；写需求得到正确的 `UPDATE` 且 `modifies_data=true`；**故意只给一张表问另一张表 → 正确回「结构不足」**；同一批表问 schema 答得出的维度 → SQL 正确

> **仍未真机验证的**：**自修一轮**。真机上模型没有编造过表名列名（提示词禁止 + 结构就在眼前），因此那条路径没被触发过——它只有单测覆盖。这是防守层，正常情况下不该响。

## 3. 后端：接口

- [x] 3.1 `POST {SYS_NAME}/v1/nl2sql/generate`：入参 `datasource_id`（必填）、`question`（必填）、`tables`（可选）、`dialect`（可选）
- [x] 3.2 响应含 `sql` / `attempts` / `repair` / `issues` / `verdict` / `schema_check` / `structure` / `generation`
- [x] 3.3 路由注册进 `src/config/urls.py`
- [x] 3.4 单测：缺必填 / 空白 question → 4000；数据源不存在 → 4004
- [x] 3.5 单测：响应不含凭据
- [x] 3.6 单测：结构校验的 `performed` / `table_count` / `note` 如实反映

## 4. 前端：页面与菜单

- [x] 4.1 `static/src/views/nl2sql/index.vue`：数据源（必选）+ 表范围（可选、可搜索多选）+ 需求输入 + 生成
- [x] 4.2 结果区：SQL（可复制）、校验结论、问题清单、**自修说明**
- [x] 4.3 结果区常驻说明：**校验只覆盖结构，不覆盖业务语义**
- [x] 4.4「试运行」按钮 → 调既有的 `/sql-analysis/execute`，显式触发
- [x] 4.5 表范围下方提示建议缩小；超限的后端报错文案自带「请缩小表范围」
- [x] 4.6 `static/src/api/nl2sql.js` + 菜单项「SQL 生成」；生成超时放宽到 150 秒（单次 `llm_timeout` 60 秒 × 最多两次调用）
- [x] 4.7 `npm run build` 通过

> **一处需要你知道的事实**：复用的 `/sql-analysis/execute` **只返回「能否执行 / 行数 / 耗时 / EXPLAIN 计划」，不返回数据行**。所以「试运行」证明的是「这条语句跑得通、多大代价」，不是「结果对不对」。要改的话得动 sqlanalysis 的执行契约，超出本次范围——已如实写进 `docs/nl2sql.md`。

## 5. 文档

- [x] 5.1 新增 `docs/nl2sql.md`：机制、为什么只认两条规则、为什么一轮为限、**校验只覆盖结构**、表范围取舍、边界、降级、API、已知限制
- [x] 5.2 `README.md`：路线图该行改为「查询语句已实现；建表语句未开始」；文档索引加一行
- [x] 5.3 `docs/sql-analysis.md`：补「本模块对外提供」的契约表，写明 `nl2sql` 用它校验
- [x] 5.4 `openspec/project.md`：**整张路线图表都已过期**（1/3/4 早已实现却写着「未开始」），一并更正；同段里 RAG 向量存储的 pgvector 也过时（实际是 Qdrant）

## 6. 校验

- [x] 6.1 `python .ci/custom-checks/scaffold_check.py`
- [x] 6.2 `ruff check` 与 `ruff format --check`（带 `.ci/lint-rules/ruff.toml`）
- [x] 6.3 `cd static && npm run build`
- [x] 6.4 `ENV_TYPE=test python manage.py test apps.nl2sql apps.sqlanalysis apps.datasource apps.overview apps.knowledge`（410 项）
- [x] 6.5 **真实接口核对**（`ENV_TYPE=local`，105 张表的真实库）：能生成；写需求得到正确的 `UPDATE` 且 `modifies_data=true`；结构超限与结构不足两种情形都如实报出且**不带任何 SQL**；同一批表问 schema 答得出的维度，SQL 正确。**唯一没跑到的是「自修一轮」**——真机上模型没编造过表名列名，那条路径没被触发（只有单测覆盖），详见 2c
- [ ] 6.6 **真实页面核对**：菜单项可见、结果区含「只覆盖结构」的说明、试运行是显式触发
- [x] 6.7 `openspec validate add-nl2sql --strict --no-interactive`

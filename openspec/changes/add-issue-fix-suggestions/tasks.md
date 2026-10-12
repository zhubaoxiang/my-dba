## 1. 数据结构：让问题能定位到索引

- [x] 1.1 `CatalogIssue` 加 `fix_key`（`varchar(128)`，`NOT NULL DEFAULT ''`）；DDL 写进 `sql/pg_struct.sql` 与 `sql/patch.sql`
- [x] 1.2 `rules/context.py` 的 `issue()` 支持 `fix_key`；索引类规则（重复 / 冗余 / 未使用）填索引名
- [x] 1.3 `services._save_issues()` 落这个字段
- [x] 1.4 单测：索引类问题的 `fix_key` 是索引名；其他规则为空串

## 2. 模板生成

- [x] 2.1 新增 `apps/datasource/fixes.py`：按 `rule_code` 分发模板，输入是问题的结构化字段，输出 `{sql, caveat}`
- [x] 2.2 覆盖 7 个分支：建索引、删索引（重复/冗余/未使用）、删表、超长 varchar 收紧、字符存时间改 timestamp、加自增主键
- [x] 2.3 对象名缺失时**不硬拼**（返回空，由调用方决定怎么展示）
- [x] 2.4 每条语句带 `caveat`（删索引＝确认无依赖；类型变更＝脏数据会失败；加主键＝若确无业务唯一列）
- [x] 2.5 超大表 / 大对象拆表**不给模板**，标记为「需模型参考」
- [x] 2.6 单测：7 个分支各自的语句与前提；对象名缺失时不产出语句

## 3. 接口

- [x] 3.1 问题清单接口的每条问题带上 `fix`：`{sql, caveat, source: "rule"}` 或 `{source: "model_required"}`
- [x] 3.2 新增参考语句接口（调 `nl2sql` 的模型接入）：入参为问题标识，返回 `{available, sql, premise, note}`
- [x] 3.3 未配置对话模型时如实说明，模板部分不受影响
- [x] 3.4 单测：清单带模板语句；无对话模型时模板照常、参考语句如实说明
- [x] 3.5 单测：响应不含凭据

## 4. 前端

- [x] 4.1 `issues.vue`：每条问题下展示修复语句（可复制）+ 风险前提
- [x] 4.2 顶部「复制全部修复语句」——**只含模板生成的**，并按来源注明
- [x] 4.3「需你决策」的两条给按钮，点了才生成；结果单独陈列，标「无唯一答案，仅供参考」并写明前提
- [x] 4.4 语句区标「先在测试库验证；系统不会执行」
- [x] 4.5 `npm run build` 通过

## 5. 文档

- [x] 5.1 `docs/datasource.md`：问题清单新增修复语句一节——两类来源、为什么模板为主、`fix_key` 的作用、不执行
- [x] 5.2 补「别存成品语句」这条决定的由来（与「声明副本」那次教训同源）

## 6. 校验

- [x] 6.1 `python .ci/custom-checks/scaffold_check.py`
- [x] 6.2 `ruff check` 与 `ruff format --check`（带 `.ci/lint-rules/ruff.toml`）
- [x] 6.3 `cd static && npm run build`
- [x] 6.4 `ENV_TYPE=test python manage.py test apps.datasource apps.nl2sql apps.overview`
- [x] 6.5 **真实接口核对**（`ENV_TYPE=local`，142 + 30 条问题）：模板语句正确、来源标注正确；**并据此抓到一处危害**——见 8.1
- [x] 6.6 **真实页面核对**（已通过）：语句与前提可见、两类分区陈列、「复制全部」只含模板的
- [ ] 6.7 `openspec validate add-issue-fix-suggestions --strict --no-interactive`

## 7. 实现时的三处偏差（与上面写的不同）

- [x] 7.1 **字段名 `object_name` → `fix_key`**：索引类规则要填的是「索引名」，但**可疑字段类型那条规则有三个分支**（字符存时间 / 超长 varchar / 大对象），可修的那两个语句形态完全不同，而表里没有 `data_type` 可分——那一列存的其实是**命中的分支**。叫 `object_name` 是撒谎
- [x] 7.2 **只支持 PostgreSQL 方言**：`ALTER COLUMN … TYPE` / `bigserial` 在 MySQL 上是另一套写法，而本仓库没有可验证的 MySQL 环境。非 PostgreSQL 数据源标 `unsupported` 并说明原因，**不静默降级为模型建议**——那会让 MySQL 用户以为拿到的东西与 PostgreSQL 用户同等可靠
- [x] 7.3 **参考语句复用 `nl2sql.services.generate_sql()`**，不另起一套提示词与输出解析；**前提由后端给**（「分区键与归档周期取决于你的查询与保留策略」这句是恒定的，模型说不如我们说）。`datasource.services` 延迟导入 `nl2sql.services`——后者依赖前者的 `list_snapshot_tables`，模块级导入会成环
- [x] 7.4 **「复制本页修复语句」而不是「复制全部」**：跨页拼一份看起来像完整脚本的东西，反而容易被整段执行；而且那要多发一次请求

## 8. 真机核对抓到的一处**危害**（本次改动里最要紧的一条）

- [x] 8.1 **`isolated_table` 不再产出语句。** 真机核对时 `覆羽51环境` 的 30 条问题里 **23 条**都是「孤立表」→ 每条都给了一条 `DROP TABLE`。那个库**根本不建外键**，于是每张表都被判成孤立，包括 `tb_cases_project` / `tb_interface` / `tb_env` 这些显然是业务表的——而「复制本页」按钮会把它们**拼成一段脚本**，**一次点击就是一个删库脚本**，且拼接时风险前提全被丢掉。改为 `advisory`：不给语句，并说明为什么不给
- [x] 8.2 **批量复制带上前提。** 每段前面补问题说明与风险前提作为 SQL 注释——前提是贴着语句的，拼接时丢掉它就等于产出一段裸 DDL
- [x] 8.3 规格补两条场景：「判定依据不足时不给出语句」「批量复制的语句必须带上各自的前提」；`docs/datasource.md` 记下这次判断与由来
- [x] 8.4 复验：两库全部语句里 **`DROP TABLE` 为 0** ✓

> **一处已知的迁移后果（已复验）**：库里现存的问题是**旧规则**存的，`fix_key` 是空串，因此索引类与字段类型类的修复语句暂时落在「需模型参考」。**重新采集一次即正常**——这是加列方案的正常代价，写在了 design 的 Migration Plan 里。

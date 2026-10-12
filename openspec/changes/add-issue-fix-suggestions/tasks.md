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
- [ ] 6.5 **真实接口核对**：对一个已采集的数据源取问题清单，逐条核对模板语句正确；核对 2 个不给模板的分支如实标记为「需模型参考」
- [ ] 6.6 **真实页面核对**：语句与前提可见、两类分区陈列、「复制全部」只含模板的
- [ ] 6.7 `openspec validate add-issue-fix-suggestions --strict --no-interactive`

## 7. 实现时的三处偏差（与上面写的不同）

- [x] 7.1 **字段名 `object_name` → `fix_key`**：索引类规则要填的是「索引名」，但**可疑字段类型那条规则有三个分支**（字符存时间 / 超长 varchar / 大对象），可修的那两个语句形态完全不同，而表里没有 `data_type` 可分——那一列存的其实是**命中的分支**。叫 `object_name` 是撒谎
- [x] 7.2 **只支持 PostgreSQL 方言**：`ALTER COLUMN … TYPE` / `bigserial` 在 MySQL 上是另一套写法，而本仓库没有可验证的 MySQL 环境。非 PostgreSQL 数据源标 `unsupported` 并说明原因，**不静默降级为模型建议**——那会让 MySQL 用户以为拿到的东西与 PostgreSQL 用户同等可靠
- [x] 7.3 **参考语句复用 `nl2sql.services.generate_sql()`**，不另起一套提示词与输出解析；**前提由后端给**（「分区键与归档周期取决于你的查询与保留策略」这句是恒定的，模型说不如我们说）。`datasource.services` 延迟导入 `nl2sql.services`——后者依赖前者的 `list_snapshot_tables`，模块级导入会成环
- [x] 7.4 **「复制本页修复语句」而不是「复制全部」**：跨页拼一份看起来像完整脚本的东西，反而容易被整段执行；而且那要多发一次请求

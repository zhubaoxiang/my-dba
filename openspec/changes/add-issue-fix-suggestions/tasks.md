## 1. 数据结构：让问题能定位到索引

- [ ] 1.1 `CatalogIssue` 加 `object_name`（`varchar(256)`，`NOT NULL DEFAULT ''`）；DDL 写进 `sql/pg_struct.sql` 与 `sql/patch.sql`
- [ ] 1.2 `rules/context.py` 的 `issue()` 支持 `object_name`；索引类规则（重复 / 冗余 / 未使用）填索引名
- [ ] 1.3 `services._save_issues()` 落这个字段
- [ ] 1.4 单测：索引类问题的 `object_name` 是索引名；其他规则为空串

## 2. 模板生成

- [ ] 2.1 新增 `apps/datasource/fixes.py`：按 `rule_code` 分发模板，输入是问题的结构化字段，输出 `{sql, caveat}`
- [ ] 2.2 覆盖 7 个分支：建索引、删索引（重复/冗余/未使用）、删表、超长 varchar 收紧、字符存时间改 timestamp、加自增主键
- [ ] 2.3 对象名缺失时**不硬拼**（返回空，由调用方决定怎么展示）
- [ ] 2.4 每条语句带 `caveat`（删索引＝确认无依赖；类型变更＝脏数据会失败；加主键＝若确无业务唯一列）
- [ ] 2.5 超大表 / 大对象拆表**不给模板**，标记为「需模型参考」
- [ ] 2.6 单测：7 个分支各自的语句与前提；对象名缺失时不产出语句

## 3. 接口

- [ ] 3.1 问题清单接口的每条问题带上 `fix`：`{sql, caveat, source: "rule"}` 或 `{source: "model_required"}`
- [ ] 3.2 新增参考语句接口（调 `nl2sql` 的模型接入）：入参为问题标识，返回 `{available, sql, premise, note}`
- [ ] 3.3 未配置对话模型时如实说明，模板部分不受影响
- [ ] 3.4 单测：清单带模板语句；无对话模型时模板照常、参考语句如实说明
- [ ] 3.5 单测：响应不含凭据

## 4. 前端

- [ ] 4.1 `issues.vue`：每条问题下展示修复语句（可复制）+ 风险前提
- [ ] 4.2 顶部「复制全部修复语句」——**只含模板生成的**，并按来源注明
- [ ] 4.3「需你决策」的两条给按钮，点了才生成；结果单独陈列，标「无唯一答案，仅供参考」并写明前提
- [ ] 4.4 语句区标「先在测试库验证；系统不会执行」
- [ ] 4.5 `npm run build` 通过

## 5. 文档

- [ ] 5.1 `docs/datasource.md`：问题清单新增修复语句一节——两类来源、为什么模板为主、`object_name` 的作用、不执行
- [ ] 5.2 补「别存成品语句」这条决定的由来（与「声明副本」那次教训同源）

## 6. 校验

- [ ] 6.1 `python .ci/custom-checks/scaffold_check.py`
- [ ] 6.2 `ruff check` 与 `ruff format --check`（带 `.ci/lint-rules/ruff.toml`）
- [ ] 6.3 `cd static && npm run build`
- [ ] 6.4 `ENV_TYPE=test python manage.py test apps.datasource apps.nl2sql apps.overview`
- [ ] 6.5 **真实接口核对**：对一个已采集的数据源取问题清单，逐条核对模板语句正确；核对 2 个不给模板的分支如实标记为「需模型参考」
- [ ] 6.6 **真实页面核对**：语句与前提可见、两类分区陈列、「复制全部」只含模板的
- [ ] 6.7 `openspec validate add-issue-fix-suggestions --strict --no-interactive`

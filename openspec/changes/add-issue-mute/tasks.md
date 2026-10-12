## 1. 数据模型

- [x] 1.1 新增 `IssueMute`（继承 `AbstractTimeFiledModel`，`db_table = "issue_mute"`）：`datasource_id` / `rule_code` / `table_name` / `column_name`；DDL 写进 `sql/pg_struct.sql` 与 `sql/patch.sql`
- [x] 1.2 部分唯一索引：未删除范围内 `(datasource_id, rule_code, table_name, column_name)` 唯一（与 `analysis_rule` 同款写法，避免软删除后无法重建）
- [x] 1.3 单测：同键重复静音不产生第二行；软删除后可重新静音

## 2. 后端：统一口径

- [x] 2.1 `services.py` 新增**唯一一处**静音键提取与过滤逻辑（问题行 → 静音键），供问题清单与问题统计**共用**
- [x] 2.2 问题清单：匹配静音并在响应里标 `muted`；默认不返回已静音的，`include_muted=true` 时才带上
- [x] 2.3 `list_datasource_status()` 的分级问题数**排除静音**——复用 2.1 的同一函数
- [x] 2.4 `overview/services.py` 的问题合计随之正确（它取自 2.3）
- [x] 2.5 单测：**同一处被静音后，问题清单条数与统计数字同时减 1**（直接断言两者一致，防口径分叉）
- [x] 2.6 单测：静音只影响那一处——同表其他问题、他表同类问题照常报出

## 3. 后端：接口

- [x] 3.1 `POST` 静音一处；`DELETE` 取消静音（幂等：重复静音返回成功而不报错）
- [x] 3.2 已静音条目的清单查询（供「查看已静音」）
- [x] 3.3 入参校验：`rule_code` 必须是声明里存在的规则，否则 4000
- [x] 3.4 单测：静音 / 取消 / 重复静音 / 不存在的规则 code / 响应不含凭据

## 4. 前端

- [x] 4.1 `issues.vue`：每条问题一个「静音」，顶部「已静音 N 条 · 查看」
- [x] 4.2 查看已静音的列表，可「取消静音」
- [x] 4.3 静音动作的说明文案要和「停用规则」讲清区别（**只影响这一处** vs **全局**）
- [x] 4.4 措辞用「静音」而非「已处理/已修复」——**它不表示问题消失了**
- [x] 4.5 首页与库卡片的问题数说明补一句「不含已静音」（否则数字变小会让人以为问题解决了）
- [x] 4.6 `npm run build` 通过

## 5. 文档

- [x] 5.1 `docs/datasource.md`：静音一节——为什么必须独立表、粒度、与停用规则的区别、口径一致
- [x] 5.2 `docs/overview.md`：问题数口径补「排除已静音」

## 6. 校验

- [x] 6.1 `python .ci/custom-checks/scaffold_check.py`
- [x] 6.2 `ruff check` 与 `ruff format --check`（带 `.ci/lint-rules/ruff.toml`）
- [x] 6.3 `cd static && npm run build`
- [x] 6.4 `ENV_TYPE=test python manage.py test apps.datasource apps.overview apps.nl2sql`
- [x] 6.5 **真实接口核对**（：清单 142 → 141、首页该库合计 142 → 141，**两个数字同时动**；已静音列表可用；取消后回到 142）。「重新采集后仍生效」由单测覆盖：静音一处 → 清单少一条、首页合计少一条（**两个数字同时动**）；重新采集后静音仍生效；取消静音后回来
- [ ] 6.6 **真实页面核对**：静音入口与「查看已静音」可用、措辞不误导、首页说明可见
- [x] 6.7 `openspec validate add-issue-mute --strict --no-interactive`

## 7. 实现时抓到的两个 bug（都由测试逼出来）

- [x] 7.1 **空 `Q()` 会把问题数清零。** `muted_filter()` 原本在「没有任何静音项」时返回空 `Q()`——而空 `Q()` 在 Django 里是**中性**的，`.filter(Q())` 匹配**全部**行。拿它去做「减去已静音」的减法，会把所有问题一起减掉、**首页数字归零**。改为返回 `None`，调用方必须显式判空；并补了一条用例钉住它
- [x] 7.2 **`owner_id` 取到了过滤后的 queryset。** 我用**已被静音过滤**的结果去取 `datasource_id`——整页全被静音时取不到值，连带 `muted_total` 也算成 0，界面上就再也看不到「已静音几条」。改为在过滤**之前**取

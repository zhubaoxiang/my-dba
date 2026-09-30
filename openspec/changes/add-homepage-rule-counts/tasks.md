## 1. 后端：跨模块取数契约

- [x] 1.1 `apps/datasource/services.py`：`rule_counts()` —— 库表分析规则的条目数
- [x] 1.2 `apps/sqlanalysis/services.py`（**新增**）：`rule_counts()` —— SQL 分析规则的条目数
- [x] 1.3 两处都取**代码声明**的长度，不数 `analysis_rule` 表的行数（未同步时会数成 0）
- [x] 1.4 单测：两个返回值分别是 7 与 18，且在**没有任何 `analysis_rule` 行**时仍然如此

## 2. 后端：首页接口

- [x] 2.1 `apps/overview/services.py`：新增 `rules` 块，纳入分块兜底
- [x] 2.2 取不到时两项记 `None`，**不是 0**（0 是「一条规则都没有」，含义完全不同）
- [x] 2.3 单测：正常返回条数准确、规则块失败时其余照常返回、失败时是 `None` 而非 0
- [x] 2.4 单测：响应仍不含任何凭据

## 3. 前端

- [x] 3.1 知识库之后、页脚之前新增「分析规则」块，并列两个条目数
- [x] 3.2 库表规则那格给到 `/datasource/rules` 的入口；SQL 规则暂无清单页，只给数（design D5）
- [x] 3.3 取不到时显示占位符而非 0
- [x] 3.4 `npm run build` 通过

## 4. 文档

- [x] 4.1 `docs/overview.md`：页面结构图补该块、取数口径补两行、写明 **SQL 规则暂无清单页**这一不对称
- [x] 4.2 `docs/overview.md` 的「明确不做的」核对一遍，把「启用状态」记为推迟项

## 5. 校验

- [x] 5.1 `python .ci/custom-checks/scaffold_check.py`
- [x] 5.2 `ruff check` 与 `ruff format --check`
- [x] 5.3 `cd static && npm run build`
- [x] 5.4 `ENV_TYPE=test python manage.py test apps.datasource apps.knowledge apps.overview apps.sqlanalysis`
- [x] 5.5 **真实接口核对**：`GET /my-dba/v1/overview` 返回 `rules` 块且条数为 7 / 18
- [x] 5.6 `openspec validate add-homepage-rule-counts --strict --no-interactive`

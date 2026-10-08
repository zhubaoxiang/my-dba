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

- [x] 3.1 概览数字带新增两格（库表规则 / SQL 规则），各一个大数字 + 独立标签
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

## 6. 修订：规则概况由「并成一格」改为「并排两格」

初版把两个规则数并进数字带第 4 格（`7 · 18` + 标签「分析规则 · 库表 / SQL」）。实测两个数被读成一个怪数字、标签过密，**太不明显**——诉求「让系统的判断标准可见」没达成。改为第 4、5 两格。

- [x] 6.1 `home/index.vue`：第 4 格拆成两个 `.stat`，标签分别为「库表规则」「SQL 规则」
- [x] 6.2 `.summary` 栅格 `repeat(4, …)` → `repeat(5, …)`
- [x] 6.3 `docs/overview.md`：结构图改 5 格，「分析规则」整段改述
- [x] 6.4 design.md D2 改写为「并排两格」
- [x] 6.5 校验：`npm run build`、`python .ci/custom-checks/scaffold_check.py`、`ruff check`、`openspec validate add-homepage-rule-counts --strict --no-interactive`
- [ ] 6.6 真实页面核对：数字带 5 格；库表规则 7 带入口、SQL 规则 18 不带

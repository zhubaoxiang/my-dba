## 1. 后端：库表规则清单换基底

- [x] 1.1 `AnalysisRuleView.list()` 改为遍历 `registry.all_rules()`，逐条叠加 `analysis_rule` 中同 `code` 的行
- [x] 1.2 未落库的规则 `id` 返回 `null`，字段取代码默认值，`is_overridden` 为 false
- [x] 1.3 序列化支持「无库行」的规则（用未落库的模型实例，序列化器无需改动）
- [x] 1.4 单测：**库里一行都没有**时清单仍返回 7 条，且值等于代码默认
- [x] 1.5 单测：某条被人工调整后，清单该条展示调整值且 `is_overridden` 为 true
- [x] 1.6 单测：库中的孤儿 code（不在代码声明里）不出现在清单中

## 2. 后端：按 code 定位 + 自动建行 + 去同步

- [x] 2.1 规则定位改为按 `code`（`lookup_field`，`PUT/PATCH …/{code}`）
- [x] 2.2 `update`：库中无该 code 时先建行再写覆盖（upsert），无需任何前置动作
- [x] 2.3 `reset` 改为按 `code` 定位；未落库的规则返回参数错误（没有可恢复的覆盖）
- [x] 2.4 删除 `sync` action 及其路由痕迹
- [x] 2.5 保留 `create` / `destroy` 的拒绝语义（规则来自代码声明）
- [x] 2.6 单测：对未落库的规则 PUT 级别 → 建行且生效；再 PUT 启用 → 改同一行
- [x] 2.7 单测：不存在的 `code` → 4004
- [x] 2.8 单测：`sync` action 已不存在

## 3. 后端：SQL 规则清单接口

- [x] 3.1 `apps/sqlanalysis/services.py` 新增 `list_rules()`（读 `rules/registry.all_rules()`）
- [x] 3.2 `SqlAnalysisView` 新增 `GET rules` action，返回 code / name / description / level / level_label / needs_schema
- [x] 3.3 单测：返回 18 条，其中 `needs_schema` 为 true 的 5 条
- [x] 3.4 单测：响应不含凭据；且一个数据源都没有时照常返回

> 附带一处基础设施改动：`baseviews.StatelessView` 由裸 `ViewSet` 改为 `GenericViewSet`，
> 使无模型的 action 也能用 `pagination.paginate()`（不新增任何资源路由）。

## 4. 前端：规则页两个 Tab

- [x] 4.1 `rules.vue` 改为两个 Tab（库表规则 / SQL 规则），标签带条数
- [x] 4.2 库表 Tab：保留现有列与交互，改调按 `code` 定位的新接口
- [x] 4.3 SQL Tab：只读四列（标识 / 名称 / 级别 / 需表结构），**无启用开关、无级别下拉**
- [x] 4.4 去掉「同步规则」按钮
- [x] 4.5「恢复默认」对未调整过的规则置灰（依 `is_overridden`）
- [x] 4.6 两个 Tab 都加一列「说明」（`show-overflow-tooltip`）；原先只有 SQL 那组给名称挂了 tooltip，库表那组一条说明都看不到，属不对称
- [x] 4.7 单测锁住 `description` 字段确实返回且非空
- [x] 4.8 库表组：启用开关并入「操作」列（开关 + 恢复默认同一格），列数由 9 降到 8

## 5. 前端：首页 SQL 那格补入口

- [x] 5.1 SQL 规则那格加上「查看规则清单」，与库表那格对称
- [x] 5.2 两格各自深链到对应那组（`?tab=datasource` / `?tab=sql`），点 SQL 那格不会落在库表 Tab 上

## 6. 文档

- [x] 6.1 `docs/datasource.md`：清单基底改为代码声明、移除同步、按 code 定位、指向 SQL 那组
- [x] 6.2 `docs/sql-analysis.md`：补 `GET /sql-analysis/rules` 与「界面上看得到」的说明
- [x] 6.3 `docs/overview.md`：两格都带入口，「SQL 规则暂无清单页」这一不对称改为已消除（并从「明确不做的」移除）

## 7. 校验

- [x] 7.1 `python .ci/custom-checks/scaffold_check.py`
- [x] 7.2 `ruff check` 与 `ruff format --check`（**带项目配置** `.ci/lint-rules/ruff.toml`）
- [x] 7.3 `cd static && npm run build`
- [x] 7.4 `ENV_TYPE=test python manage.py test apps.datasource apps.knowledge apps.overview apps.sqlanalysis`（386 passed）
- [x] 7.5 **真实接口核对**：库为空时 `GET /v1/analysis-rule` 返回 7 条（`id` 为 null）；`GET /v1/sql-analysis/rules` 返回 18 条（5 条 `needs_schema`）；PUT 未落库规则自动建行且复用同一行；reset 未调整规则报 4000；未知 code 报 4004
- [x] 7.6 **真实页面核对**：两个 Tab 可见且标签带条数；SQL Tab 无任何可操作控件；「说明」列两 Tab 都有；库表组「操作」列为 `[开关] 恢复默认`
- [x] 7.7 `openspec validate add-unified-rule-catalog --strict --no-interactive`

## 8. 修订：库中的副本不再参与读取

`analysis_rule` 的 `name` / `description` / `object_level` 是「同步」刷新的声明副本；删掉同步时漏了停止读它们，导致**代码里改过名的规则在清单里永远显示旧名字**（起服务实测确认）。

- [x] 8.1 `registry._declared_view()`：名称、说明、适用层级一律取声明，只有 `enabled` / `level` / `thresholds` 取库里的行
- [x] 8.2 `_defaults()` 注明那三列已不再被读取
- [x] 8.3 回归测试：把库里副本改乱，清单仍显示声明的值，而级别仍取覆盖值
- [x] 8.4 `docs/datasource.md` 写明「声明与副本冲突时声明是对的」，并挂上删列的待办
- [ ] 8.5 删那三列 —— **不在本变更内**，随下一次库结构补丁与 `evaluated_rules` 一起执行

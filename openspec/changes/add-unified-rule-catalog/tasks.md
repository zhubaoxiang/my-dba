## 1. 后端：库表规则清单换基底

- [ ] 1.1 `AnalysisRuleView.list()` 改为遍历 `registry.all_rules()`，逐条叠加 `analysis_rule` 中同 `code` 的行
- [ ] 1.2 未落库的规则 `id` 返回 `null`，字段取代码默认值，`is_overridden` 为 false
- [ ] 1.3 序列化支持「无库行」的规则（现有 `AnalysisRuleSerializer` 绑的是 Model 实例）
- [ ] 1.4 单测：**库里一行都没有**时清单仍返回 7 条，且值等于代码默认
- [ ] 1.5 单测：某条被人工调整后，清单该条展示调整值且 `is_overridden` 为 true
- [ ] 1.6 单测：库中的孤儿 code（不在代码声明里）不出现在清单中

## 2. 后端：按 code 定位 + 自动建行 + 去同步

- [ ] 2.1 规则定位改为按 `code`（`PUT/PATCH …/{code}`）
- [ ] 2.2 `update`：库中无该 code 时先建行再写覆盖（upsert），无需任何前置动作
- [ ] 2.3 `reset` 改为按 `code` 定位；未落库的规则返回参数错误（没有可恢复的覆盖）
- [ ] 2.4 删除 `sync` action 及其路由痕迹
- [ ] 2.5 保留 `create` / `destroy` 的拒绝语义（规则来自代码声明）
- [ ] 2.6 单测：对未落库的规则 PUT 级别 → 建行且生效；再 PUT 启用 → 改同一行
- [ ] 2.7 单测：不存在的 `code` → 4004
- [ ] 2.8 单测：`/sync` 路由已不存在

## 3. 后端：SQL 规则清单接口

- [ ] 3.1 `apps/sqlanalysis/services.py` 新增 `list_rules()`（读 `rules/registry.all_rules()`）
- [ ] 3.2 `SqlAnalysisView` 新增 `GET rules` action，返回 code / name / description / level / needs_schema
- [ ] 3.3 单测：返回 18 条，其中 `needs_schema` 为 true 的 5 条
- [ ] 3.4 单测：响应不含凭据

## 4. 前端：规则页两个 Tab

- [ ] 4.1 `rules.vue` 改为两个 Tab（库表规则 / SQL 规则），标签带条数
- [ ] 4.2 库表 Tab：保留现有列与交互，改调按 `code` 定位的新接口
- [ ] 4.3 SQL Tab：只读四列（标识 / 名称 / 级别 / 需表结构），**无启用开关、无级别下拉**
- [ ] 4.4 去掉「同步规则」按钮
- [ ] 4.5「恢复默认」对未调整过的规则置灰（依 `is_overridden`）

## 5. 前端：首页 SQL 那格补入口

- [ ] 5.1 SQL 规则那格加上「查看规则清单」，与库表那格对称

## 6. 文档

- [ ] 6.1 `docs/datasource.md`：清单改两 Tab、基底改为代码声明、移除同步、按 code 定位
- [ ] 6.2 `docs/sql-analysis.md`：补 SQL 规则清单接口
- [ ] 6.3 `docs/overview.md`：两格都带入口，「SQL 规则暂无清单页」这一不对称改为已消除

## 7. 校验

- [ ] 7.1 `python .ci/custom-checks/scaffold_check.py`
- [ ] 7.2 `ruff check` 与 `ruff format --check`
- [ ] 7.3 `cd static && npm run build`
- [ ] 7.4 `ENV_TYPE=test python manage.py test apps.datasource apps.sqlanalysis apps.overview`
- [ ] 7.5 **真实接口核对**：库为空时 `GET /v1/analysis-rule` 返回 7 条；`GET /v1/sql-analysis/rules` 返回 18 条
- [ ] 7.6 **真实页面核对**：两个 Tab 可见，SQL Tab 无任何可操作控件
- [ ] 7.7 `openspec validate add-unified-rule-catalog --strict --no-interactive`

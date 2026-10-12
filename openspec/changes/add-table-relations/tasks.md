## 1. 后端：关系推导

- [x] 1.1 `datasource/services.py` 新增 `table_relations(datasource_id, schema, table)`：遍历快照全部表，返回 `{"references": [...], "referenced_by": [...]}`
- [x] 1.2 每项含：对方表的 `schema` / `name`、本侧列名、外键名；被引用侧同样
- [x] 1.3 同名表跨模式时按 `schema + name` 对齐，不错配
- [x] 1.4 表不在快照中 / 快照不可用时**如实说明原因**，不返回空关系
- [x] 1.5 单测（纯计算为主）：两侧都有的情形；本表无外键但被引用（**关键用例**）；两侧皆空；同名跨模式；指向快照里不存在的表

## 2. 后端：接口

- [x] 2.1 `catalog/table` 的响应带上 `relations`
- [x] 2.2 单测：接口返回的关系与 1.1 一致；快照不可用时 `relations` 带原因而非空
- [x] 2.3 单测：响应不含凭据

## 3. 前端

- [x] 3.1 `table-detail.vue` 新增「关系」区：两段列表（引用了哪些表 / 被哪些表引用），带列名与外键名
- [x] 3.2 对方表在同一快照时可点跳转到它的详情；不在时只显示名字
- [x] 3.3 两侧皆空时明确说明「没有任何外键关系」，**不留白**
- [x] 3.4 `npm run build` 通过

## 4. 文档

- [x] 4.1 `docs/datasource.md`：表详情补「关系」一节；写明「被引用必须扫全库」这条与原因
- [x] 4.2 记下「不画 ER 图」的决定与理由（不引图表库 · 百来张表不可读 · 实问的是单表关系）

## 5. 校验

- [x] 5.1 `python .ci/custom-checks/scaffold_check.py`
- [x] 5.2 `ruff check` 与 `ruff format --check`（带 `.ci/lint-rules/ruff.toml`）
- [x] 5.3 `cd static && npm run build`
- [x] 5.4 `ENV_TYPE=test python manage.py test apps.datasource`
- [x] 5.5 **真实接口核对**（：ds=1 的 auth_* 表两个方向都对；**反向一致性逐条核对无误**；auth_permission 自身有外键且被 2 张表引用；ds=2 整库无外键 → 关系为空列表而非缺失）：对一个已采集的数据源取某张有外键的表，核对「引用了谁」；再取一张被引用的表，核对「被谁引用」两侧对得上
- [ ] 5.6 **真实页面核对**：关系区可见、可跳转、无关系时如实说明
- [x] 5.7 `openspec validate add-table-relations --strict --no-interactive`

## 6. 实现时补的一处（测试逼出来的）

- [x] 6.1 **抽屉已开着时 `@open` 不会再触发。** 表详情是个抽屉，靠 `@open="load"` 首次加载；而从「关系」里跳到另一张表时抽屉**是开着的**，`@open` 不会再来一次——点了没反应。补了 `watch(() => props.table)` 重新加载，并由 `@navigate` 把目标表交给父组件

## 1. 后端：sqlanalysis 服务层加对外契约

- [ ] 1.1 `apps/sqlanalysis/services.py` 新增 `load_schema_index(datasource_id)`：把 `schema.load_schema_index` 的调用下沉到服务层，返回 `(index, 说明)`，拿不到时说明原因
- [ ] 1.2 新增 `analyze_sql(sql, datasource_id=None, dialect=None)`：解析 + 跑规则 + 结构校验，返回与 `/sql-analysis/analyze` 同形的结果（issues / evaluated_rules / skipped_rules / verdict）
- [ ] 1.3 视图层改调这两个契约，去掉对 `schema` / `analyzer` 的直接调用（**行为不变**，只换调用路径）
- [ ] 1.4 单测：现有 sql-analysis 接口行为不回归（既有用例全绿即可）
- [ ] 1.5 单测：`load_schema_index` 在无快照时返回说明原因而非抛异常

## 2. 后端：nl2sql 模块

- [ ] 2.1 建 `apps/nl2sql/`（`__init__.py` / `services.py` / `serializers.py` / `views.py` / `generator.py`），注册进 `settings.py` 的 `INSTALLED_APPS`
- [ ] 2.2 `generator.py`：提示词（含表结构 + 使用者需求）、取模型的 JSON/文本输出、抽出 SQL
- [ ] 2.3 `generator.py`：**自修一轮** —— 把结构问题与真实表名回给模型重生成一次；返回 `(sql, attempts, repair)`
- [ ] 2.4 结构超出可承载范围时抛出可识别的错误（供接口如实报错），**不截断**
- [ ] 2.5 模型不可用（未配置 / 调用失败 / 输出解析不了）时返回可识别的失败，不返回半成品
- [ ] 2.6 单测（模型调用打桩）：生成成功；首次无问题不触发自修；首次有问题自修成功；自修后仍失败如实返回
- [ ] 2.7 单测：模型未配置 / 超时 / 输出非 SQL 时各自如实报错
- [ ] 2.8 单测：结构超限时报错且**不含**任何基于残缺结构的生成结果

## 3. 后端：接口

- [ ] 3.1 `POST {SYS_NAME}/v1/nl2sql/generate`：入参 `datasource_id`（必填）、`question`（必填）、`tables`（可选）、`dialect`（可选）
- [ ] 3.2 响应含 `sql` / `attempts` / `repair`（是否自修、修了哪些问题）/ `issues` / `verdict` / `schema_check` / `generation`
- [ ] 3.3 路由注册进 `src/config/urls.py`
- [ ] 3.4 单测：缺 `datasource_id` 或 `question` → 4000；数据源不存在 → 4004
- [ ] 3.5 单测：响应不含任何凭据
- [ ] 3.6 单测：无采集快照时 `schema_check.performed` 为 false 且 **note 说明原因**

## 4. 前端：页面与菜单

- [ ] 4.1 `static/src/views/nl2sql/index.vue`：数据源（必选）+ 表范围（可选、可搜索多选）+ 需求输入 + 生成
- [ ] 4.2 结果区：SQL（可复制）、校验结论、问题清单、**自修说明**（第几次、改了什么）
- [ ] 4.3 结果区固定说明：**校验只覆盖结构，不覆盖业务语义**（design D6）
- [ ] 4.4「试运行」按钮 → 调既有的 `/sql-analysis/execute`，显式触发（**不自动执行**）
- [ ] 4.5 表多时提示建议缩小范围；结构超限时报错文案要能指回「请缩小表范围」
- [ ] 4.6 `static/src/api/nl2sql.js` + `router/index.js` 菜单项「SQL 生成」
- [ ] 4.7 `npm run build` 通过

## 5. 文档

- [ ] 5.1 新增 `docs/nl2sql.md`：能力与边界、**生成 → 校验 → 自修一轮**的机制、校验只覆盖结构这一限定、API、已知限制
- [ ] 5.2 `README.md`：路线图该行改为已实现并链文档；文档索引加一行
- [ ] 5.3 `docs/sql-analysis.md`：说明服务层新增的两个契约，及其服务对象
- [ ] 5.4 `openspec/project.md`：路线图状态更新

## 6. 校验

- [ ] 6.1 `python .ci/custom-checks/scaffold_check.py`
- [ ] 6.2 `ruff check` 与 `ruff format --check`（**带 `.ci/lint-rules/ruff.toml`**）
- [ ] 6.3 `cd static && npm run build`
- [ ] 6.4 `ENV_TYPE=test python manage.py test apps.nl2sql apps.sqlanalysis apps.datasource`
- [ ] 6.5 **真实接口核对**：绑一个已采集的数据源，说一句需求 → 拿到 SQL、看到校验结论；故意说一个不存在的表名，确认自修一轮生效
- [ ] 6.6 **真实页面核对**：菜单项可见、结果区含「只覆盖结构」的说明、试运行是显式触发
- [ ] 6.7 `openspec validate add-nl2sql --strict --no-interactive`

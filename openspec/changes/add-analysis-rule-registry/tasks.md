## 1. 规则声明机制（不改变现有行为）

- [x] 1.1 新增 `src/apps/datasource/rules/__init__.py`
- [x] 1.2 新增 `src/apps/datasource/rules/context.py`：定义 `RuleContext`（持有 `tables`、`schemas`、`database_version`、`unavailable`、`thresholds`、`rule_code`）
- [x] 1.3 在 `context.py` 提供产出方法，统一产出结构（`rule_code` / `rule_name` / `object_level` / `target` / `schema_name` / `table_name` / `column_name` / `description` / `suggestion`，可选 `issue_level` / `object_level`）
      —— 方法名定为 `issue()` 而非 `make_issue()`，更贴合调用处 `ctx.issue(...)` 的读法
- [x] 1.4 新增 `src/apps/datasource/rules/definitions.py`：定义 `RuleDefinition`（`code` / `name` / `description` / `default_level` / `object_level` / `default_thresholds` / `handler`）
- [x] 1.5 在 `utils/custom_enum.py` 新增 `ObjectLevelEnum`（DATABASE / SCHEMA / TABLE / COLUMN），使用 `custom_enum.IntegerChoices`
- [x] 1.6 把现有 7 个 `_check_*` 方法从 `analyzer.py` 迁到 `definitions.py`，改为接收 `RuleContext` 并返回 issue 列表
- [x] 1.7 每条规则补 `RuleDefinition` 声明，`code` 取值：`no_primary_key` / `fk_without_index` / `duplicate_index` / `unused_index` / `big_table` / `suspicious_column_type` / `isolated_table`
- [x] 1.8 保留规则内部的级别细分：`duplicate_index` 与 `suspicious_column_type` 在 issue 上显式给出 `issue_level`，其余由框架按规则级别赋值
- [x] 1.9 新增 `src/apps/datasource/rules/registry.py`：导出全部 `RuleDefinition` 的只读清单，并提供按 `code` 查找
- [x] 1.10 验证：`python manage.py test apps.datasource` 中现有 **21 项分析器测试全绿，断言一行未改**——这是重构不回归的安全网
- [x] 1.11 **额外验证（比计划更强）**：用库里由重构前分析器产出的**真实快照**做黄金比对。首次比对出现 30 条差异，逐条追查确认**与本次重构无关**——差异全为 `text`/`jsonb` 列，是早先 `_LARGE_OBJECT_TYPES` 收窄的预期效果；把类型集合还原为收窄前版本后，新分析器输出与库中旧结果 **60 条逐条完全一致**

## 2. 模型与 DDL

- [x] 2.1 `utils/custom_enum.py` 移除 `IssueTypeEnum`（已被 `rule_code` 取代），确认无残留引用（全仓 grep 为 0）
- [x] 2.2 `models.py` 新增 `AnalysisRule`（继承 `AbstractTimeFiledModel`，`db_table = "analysis_rule"`）：`code`、`name`、`description`、`level`、`object_level`、`enabled`、`thresholds`（JSONField）
      —— `code` **未使用** `unique=True`：唯一性改由部分唯一索引（未删除范围内唯一）保证，与 `Datasource.name` 一致，避免软删除后同 code 无法重建
- [x] 2.3 `models.py` 调整 `CatalogIssue`：新增 `rule_code`、`rule_name`、`object_level`、`schema_name`；移除 `issue_type`
- [x] 2.4 `src/sql/pg_struct.sql` 新增 `analysis_rule` 建表语句（含 `code` 部分唯一索引、注释）
- [x] 2.5 `src/sql/pg_struct.sql` 调整 `catalog_issue`：新增四列、删除 `issue_type`，并更新列注释
- [x] 2.6 增量变更写入 `src/sql/patch.sql`（按项目约定：`pg_struct.sql` 放全量建表、`patch.sql` 放增量）
      —— 每个变更集一个区块、幂等可重复执行；破坏性数据操作（`DELETE`）只写在注释里由人工确认执行
- [x] 2.7 验证：
      - `patch.sql` 打在**已有旧结构**的真实库：**18/18 条通过**
      - `pg_struct.sql` 打在**全新 schema**（模拟新库）：**31/31 条通过**
      - 均在单一大事务 + 内层 SAVEPOINT 中执行，验证后整体回滚，库中零残留
      - 另：`scaffold_check.py`、`manage.py check`、`ruff check`/`format`、34 项单测全部通过

## 3. 规则注册表加载与覆盖合并（依赖 1、2）

- [ ] 3.1 `registry.py` 增加 `load_effective_rules()`：以代码声明为全集，`analysis_rule` 为覆盖层，返回每条规则生效的 `enabled` / `level` / `thresholds`
- [ ] 3.2 库中无对应 `code` 时用代码默认值；查询时一次性取全部规则，避免逐条查库
- [ ] 3.3 `analyzer.py` 的 `analyze()` 改为遍历生效规则并调用其 `handler`，替换现有 7 行硬编码调用
- [ ] 3.4 停用的规则直接跳过，不调用 handler
- [ ] 3.5 校验产出的 issue 层级与规则声明的 `object_level` 一致；不一致时记日志并跳过该条 issue（不静默接受）
- [ ] 3.6 `CatalogAnalyzer` 记录本次实际参与评估的规则 `code` 列表，供总览统计返回
- [ ] 3.7 `services.py` 写库时填充 `rule_code` / `rule_name` / `object_level` / `schema_name`
- [ ] 3.8 验证：单测覆盖「停用规则不产出」「库中无记录走默认值」「阈值按规则独立」「层级不一致被跳过」

## 4. 规则管理 API（依赖 3）

- [ ] 4.1 `serializers.py` 新增 `AnalysisRuleSerializer`（`ModelSerializer`，查询用）
- [ ] 4.2 `serializers.py` 新增 `AnalysisRuleUpdateSerializer`（普通 `Serializer` + `validate()`：级别须为合法枚举值、阈值须为对象结构）
- [ ] 4.3 `views.py` 新增 `AnalysisRuleView`（继承 `baseviews.AnyLogin`，与基线一致）：`list`（分页）、`partial_update`（启用/级别/阈值）
- [ ] 4.4 实现 `sync` action：按 `code` 幂等 upsert，只更新 `name` / `description` / `object_level`
- [ ] 4.5 实现 `reset` action（detail）：把单条规则的可覆盖项恢复为代码默认值
- [ ] 4.6 不存在的 `code` 返回 4004；非法级别/阈值返回 4000
- [ ] 4.7 `CatalogView.summary` 返回值增加本次参与评估的规则数与规则 `code` 列表
- [ ] 4.8 `src/config/urls.py` 注册 `rf"{SYS_NAME}/v1/analysis-rule"`
- [ ] 4.9 验证：真实请求跑通清单/停用/改级别/改阈值/恢复默认/同步；同步不覆盖人工修改；同步幂等（连续两次结果一致）

## 5. 问题清单接口适配（依赖 3、4）

- [ ] 5.1 `CatalogIssueSerializer` 改为输出 `rule_code` / `rule_name` / `object_level`，移除 `issue_type` / `issue_type_label`
- [ ] 5.2 `CatalogView.issues` 增加按 `rule_code` 筛选参数
- [ ] 5.3 保持按 `issue_level` 筛选与排序行为不变
- [ ] 5.4 验证：历史问题在规则停用/改名后仍能正确展示名称

## 6. 前端（依赖 4、5）

- [ ] 6.1 `static/src/api/datasource.js` 增加规则清单、修改、恢复默认、同步四个接口
- [ ] 6.2 新增 `static/src/views/datasource/rules.vue`：规则清单 + 启用开关 + 级别调整 + 阈值编辑 + 恢复默认 + 触发同步
- [ ] 6.3 问题清单页 `issues.vue`：`issue_type_label` → `rule_name`；增加「问题类型」筛选下拉（选项来自规则清单接口）
- [ ] 6.4 `issues.vue` 与 `tables.vue` 中硬编码的 1/2/3 **保持不动**（级别仍为三档）
- [ ] 6.5 库表分析页 `tables.vue` 展示「本次参与评估的规则数 / 启用规则数」，规则集变化可被察觉
- [ ] 6.6 `static/src/router/index.js` 注册规则管理路由（配置 `meta.title`、`meta.icon`）
- [ ] 6.7 `src/right_config.json` 的 `children` 增加规则管理菜单项
- [ ] 6.8 验证：`npm run build` 通过

## 7. 配置迁移与文档（依赖 3）

- [ ] 7.1 `src/config/conf.ini` 移除 4 个分析阈值项（`big_table_rows` / `big_table_size_mb` / `varchar_max_length` / `unused_index_min_rows`），保留 `connect_timeout` / `statement_timeout` / `exact_count`
- [ ] 7.2 确认代码声明里的默认阈值等于被移除的 conf.ini 原值（`10000000` / `10240` / `2000` / `10000`），**行为不回归**
- [ ] 7.3 确认 `analyzer.py` 的 `load_thresholds()` 与 `_int_config` 已无引用后删除
- [ ] 7.4 更新 `README.md`：规则注册表说明、规则管理接口、阈值迁移说明（原配置项 → 规则注册表）
- [ ] 7.5 更新 `openspec/project.md`：Domain Context 或约束中补充「分析规则由规则注册表驱动，阈值不在 conf.ini」

## 8. 整体验证

- [ ] 8.1 `python ../.ci/custom-checks/scaffold_check.py` 通过
- [ ] 8.2 `python manage.py test apps.datasource` 全绿；确认新增单测覆盖 3.8 / 5.4 列出的场景
- [ ] 8.3 `ruff check` 与 `ruff format --check` 通过
- [ ] 8.4 `npm run build` 通过
- [ ] 8.5 真实库端到端：重新采集一次，比对重构前后同一份快照的问题集合一致（仅表现形式由 `issue_type` 变为 `rule_code`）
- [ ] 8.6 真实库端到端：停用一条规则 → 重新采集 → 确认该规则不再产出；恢复 → 再采集 → 确认恢复产出
- [ ] 8.7 对照 `.ai-harness/rules/` 逐条自查（模型继承、ViewSet 基类、响应格式、分页、软删除、枚举、路由注册、db_table、Serializer 类型、DDL 管理）

## 依赖关系说明

- 关键路径：1 → 2 → 3 → 4 → 5 → 6 → 7 → 8
- **第 1 节必须先于第 3 节完成且测试全绿**——先纯重构、行为不变，再接入规则表。否则重构与功能变更混在一起，测试挂了分不清是哪边的错
- 可并行：第 2 节（模型与 DDL）与第 1 节（规则声明）无耦合，可同时推进
- 可并行：第 6 节前端在第 4、5 节接口签名确定后即可开始；第 7 节文档可在第 3 节完成后随时做
- 阻塞项：第 2.7 与第 8.5–8.6 需要可用的 PostgreSQL 实例

## 破坏性变更与回滚

- `catalog_issue.issue_type` 删除、`IssueTypeEnum` 移除。迁移前该表仅有验证数据，迁移步骤为：`DELETE FROM catalog_issue;` → 执行改列 DDL
- 回滚：回退代码 + 恢复 `conf.ini` 的 4 个阈值项。`analysis_rule` 表保留不影响旧代码运行；`catalog_issue` 的 `rule_code` 等列留着也无害（旧代码不读）

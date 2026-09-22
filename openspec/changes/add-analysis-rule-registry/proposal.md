# Change: 分析规则注册表

## Why

当前 7 条分析规则的判定逻辑、严重级别、启用与否全部硬编码在 `analyzer.py` 的 `analyze()` 里，`analyze()` 的 7 行方法调用顺序就是规则清单。后果：

- **加一条规则**要改 6 处（`analyze()`、新增 `_check_*`、`custom_enum`、pg_struct.sql 注释、spec、单测）
- **调一条规则的级别**必须改代码并重新部署
- **临时停用一条规则**（例如某库的「孤立表」噪音太大）做不到，只能改代码
- 规则清单没有单一事实来源，级别作为字面量散落在 7 个方法里

运维侧的真实需求是「不改代码、不发版就能调节分析与开关规则」。本 change 把规则改为**声明式注册**：规则逻辑仍是代码（保证可控、可测、可读），但规则的启用、级别、阈值落库可配，运行时生效。

## What Changes

- 新增 `analysis_rule` 表：每条规则的 `code`、名称、描述、严重级别、适用层级、启用开关、阈值（JSONB）
- 新增**规则声明机制**：每条规则以 `RuleDefinition`（code / name / level / object_level / thresholds / handler）在代码中声明，作为默认值来源
- 新增 `analysis-rule-registry` 能力：查询规则清单、修改启用/级别/阈值、恢复默认、从代码声明同步
- 重构 `analyzer.py`：`analyze()` 由「硬编码 7 次调用」改为「遍历启用规则并执行」；引入 `RuleContext` 作为统一入参，使规则可作用于库/模式/表/列不同层级
- `catalog_issue` 表调整：以 `rule_code` + `rule_name` 取代数字 `issue_type`，新增 `object_level`、`schema_name` 以承载非表级问题
- 分析阈值从 `conf.ini` **迁入规则注册表**（每条规则自带 thresholds）；`conf.ini` 只保留采集侧参数
- 新增前端规则管理页：规则清单、启用开关、级别调整、阈值编辑
- 问题清单页增加「问题类型」筛选（数据来自规则清单）

## Non-Goals

- **规则判定逻辑不做配置化**：不引入表达式引擎 / CEL / JSONLogic / 脚本。规则逻辑一律是代码里的 Python 函数，加新规则仍需写代码
- **严重级别不扩展**：仍为高/中/低三档，本 change 只让「规则 → 级别」的归属可配置。新增第四档会牵动前端 3 处硬编码，另开 change
- 不做规则的版本管理与回滚
- 不做按数据源差异化的规则配置（同一套规则对所有数据源生效），见 Open Questions
- 不做快照保留策略、不把元数据明细结构化落表（属独立的扩容 change）

## Impact

- 受影响能力：**新增** `analysis-rule-registry`；**修改** `metadata-catalog`
- 受影响代码：
  - 新增 `src/apps/datasource/rules/`（`context.py`、`definitions.py` 规则声明、`registry.py` 加载与覆盖合并）
  - 重构 `src/apps/datasource/analyzer.py`（7 个 `_check_*` 方法迁至 `rules/definitions.py` 并改为接收 `RuleContext`）
  - `src/apps/datasource/models.py`：新增 `AnalysisRule`；调整 `CatalogIssue`
  - `src/apps/datasource/serializers.py` / `views.py`：新增 `AnalysisRuleView`
  - `src/sql/pg_struct.sql`：新增 `analysis_rule`；调整 `catalog_issue`
  - `src/utils/custom_enum.py`：新增 `ObjectLevelEnum`；移除不再使用的 `IssueTypeEnum`
  - `src/config/conf.ini`：移除 4 个分析阈值项
  - `src/config/urls.py`：注册 `rf"{SYS_NAME}/v1/analysis-rule"`
  - 新增 `static/src/views/datasource/rules.vue`、`static/src/api/datasource.js` 增加规则接口、路由注册
- **破坏性变更**：`catalog_issue.issue_type`（smallint）被 `rule_code`（varchar）取代。该表当前仅有验证数据、无生产数据，迁移方式见 design.md
- 依赖：本 change 建立在已归档的 `add-datasource-catalog`（`openspec/specs/metadata-catalog`）之上

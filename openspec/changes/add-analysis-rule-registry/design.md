## Context

前置状态（`add-datasource-catalog` 已归档，构成基线）：

- 7 条规则以 `_check_*` 方法形式存在于 `src/apps/datasource/analyzer.py`，由 `analyze()` 逐一调用
- 级别作为字面量写在每个方法的 `self._issue(...)` 调用处（`analyzer.py:280` 的 `_issue` 签名第一个参数）
- 判定阈值有 4 项，读 `conf.ini` 的 `[datasource]` 段，由 `load_thresholds()` 组装
- `catalog_issue` 以 `issue_level` + `issue_type`（均为 smallint）标识问题，只有 `table_name`/`column_name`，无法表达库级/模式级问题
- `CONF_ATTR` 在模块导入时一次性载入（`utils/configure.py:73`），**改 conf.ini 必须重启进程**

已确定的外部决策：规则逻辑留在代码（不做表达式引擎）；可覆盖项落数据库表；严重级别固定三档，只让归属可配置。

## Goals / Non-Goals

- Goals：新增一条规则从「改 6 处」降到「加 1 个函数 + 1 条声明」；启用/级别/阈值运行时可调且可审计；规则支持库/模式/表/列层级
- Non-Goals：不追求「不写代码就能加规则」；不扩展级别档位；不做按数据源的差异化配置

## Decisions

### D1: 规则身份用字符串 `code`，取代数字枚举

- **Decision**：每条规则有稳定字符串 `code`（如 `no_primary_key`、`fk_without_index`）。`catalog_issue` 存 `rule_code` + `rule_name` 两个字段，废弃 `issue_type`（smallint）与 `IssueTypeEnum`。
- **Why**：字符串 code 在配置界面与日志里可读；加规则不涉及数字重新编号；避免「枚举 + 数字」这层不必要的间接。
- **`rule_name` 写入时快照**：`catalog_issue` 同时存下分析那一刻的规则名称。规则日后改名或被停用，历史问题仍能正确渲染——与「快照不可变」的既有原则一致。
- **Alternatives considered**：保留 `issue_type` 数字枚举当主键，另加 code 字段 —— 被拒，两个标识表达同一件事是冗余；直接用 `analysis_rule.id` 外键 —— 被拒，规则被删后历史问题会失去标识。
- **破坏性影响**：`catalog_issue.issue_type` 列删除。该表当前只有验证数据，无生产数据。

### D2: 适用层级是声明式元数据，不驱动调用

- **Decision**：新增 `ObjectLevelEnum`（DATABASE / SCHEMA / TABLE / COLUMN）。每条规则声明自己的 `object_level`；handler 统一接收 `RuleContext`，**自己决定遍历什么**。框架只做一件事：校验产出的 issue 层级与规则声明一致。
- **Why**：规则形态差异大。表级规则（无主键、超大表）需要逐表判断，但跨表规则（孤立表）无法用单表入参表达。若让框架按层级驱动调用，框架会变复杂且限制表达力。
- **Alternatives considered**：框架按 `object_level` 决定对每张表调用还是全局调用一次 —— 被拒，跨表规则表达不了，且需要为每种层级维护一套调用约定。
- **Trade-off**：层级一致性靠约定 + 一条运行时校验，而非类型系统保证。校验失败按框架缺陷处理（记日志并跳过该条 issue），不静默。

### D3: 代码是默认值来源，库是运行时真值；同步幂等且不覆盖用户改动

- **Decision**：
  - 分析时以**代码声明为全集**，`analysis_rule` 作为覆盖层。库里没有该 code 的记录 → 用代码默认值（启用、默认级别、默认阈值）；有 → 用库里的 `enabled` / `level` / `thresholds`
  - 提供 `sync` 接口：按 code 幂等 upsert 代码声明的规则，**只更新** `name` / `description` / `object_level`，**不触碰** `enabled` / `level` / `thresholds` 这三项用户可改的字段
  - **不在进程启动时自动写库**：多 worker 会并发写，且启动路径不应有写库副作用。分析本身不依赖 sync 完成
- **Why**：这样「没同步过」也不会影响功能（默认值兜底），sync 只负责让规则出现在管理界面上；同时保证运维改过的开关不会被一次部署冲掉。
- **Alternatives considered**：启动时自动同步 —— 被拒（并发写、启动副作用）；规则全部手工录入、代码不留声明 —— 被拒（默认值要维护两份，且新部署的库缺规则时分析会静默少跑规则）。

### D4: 阈值迁入规则注册表，conf.ini 只留采集参数

- **Decision**：每条规则自带 `thresholds`（JSONB）。代码声明里给出默认值（沿用现有 conf.ini 的数值），库表可覆盖。`conf.ini` 的 `[datasource]` 段移除 4 个分析阈值项，只保留 `connect_timeout` / `statement_timeout` / `exact_count` 这三个采集侧参数。
- **Why**：职责分离——「采集参数」是运行时基础设施配置，改完需重启可以接受；「分析阈值」是业务策略，必须能运行时调整且可审计。
- **Alternatives considered**：阈值留在 `conf.ini`、只有启用与级别落库 —— 被拒，同一个「按规则配置」的概念被拆到两个配置源，解释成本高于收益；且 `conf.ini` 已被 git 跟踪（存在凭据泄露问题），不适合承载业务策略。
- **Migration**：现有 `conf.ini` 的值作为代码默认值写入，**数值不变**，行为不回归。

### D5: 级别由框架赋值，规则可显式覆盖

- **Decision**：handler 不再自己传级别。框架按 `analysis_rule.level` 统一赋值。但 handler MAY 在返回的 issue 里显式给出 `issue_level`，此时以 handler 为准。
- **Why**：现状是级别散落在 7 个方法的 `_issue(level, ...)` 调用里，这是「级别要改代码」的根因。改为框架注入后，调整归属只需改库表一行。同时保留显式覆盖是为了不丢失现有能力——「重复或冗余索引」与「可疑字段类型」两条规则内部有级别细分（列完全相同=中 / 前缀冗余=低；字符类型存时间=中 / 超长 varchar=低），一刀切会降低信息量。
- **Alternatives considered**：彻底禁止规则内部指定级别、拆成两条独立规则 —— 被拒，会让规则数量膨胀且两者共享大部分判定逻辑。

### D6: 停用规则不影响历史数据

- **Decision**：停用的规则不产出**新**问题；已有快照及其问题清单**保持不变**（沿用快照不可变原则）。历史问题通过自身快照里的 `rule_name` 渲染，不依赖规则表当前状态。
- **Why**：避免「停用一条规则导致历史问题凭空消失」，那会破坏快照的审计价值。

### D7: 前端不因级别而改，但需要新增规则管理页

- **Decision**：级别三档固定，`issues.vue` 与 `tables.vue` 里 3 处硬编码的 1/2/3 保持不动。新增规则管理页承载开关、级别、阈值编辑；问题清单页增加「问题类型」筛选下拉（选项来自规则清单接口）。
- **Why**：没有管理页，本 change 的核心收益「按规则开关」只有开发者能用 curl 享受，运维用不上。

## Risks / Trade-offs

| 风险 | 影响 | 缓解 |
|------|------|------|
| 规则 `code` 改名 | 历史 `catalog_issue.rule_code` 变成孤儿值 | 写入时快照 `rule_name` 保证可渲染；文档标注 code 为稳定标识、改名属破坏性操作 |
| 运维误操作把规则大面积停用 | 分析结果突然变少且不易察觉 | 提供「恢复默认」接口；`summary` 返回本次实际参与评估的规则数，与启用规则数不一致时可被发现 |
| `RuleContext` 结构变更 | 影响全部 7 条规则 | 重构前先补齐单测（现有 21 项分析器测试先转绿再重构），保证行为不回归 |
| 阈值双源残留 | 改了一处不生效，难以排查 | `conf.ini` 的 4 项显式删除，README 与 `openspec/project.md` 同步更新 |
| `catalog_issue` 删列 | 现有验证数据丢失 | 该表仅含验证数据；迁移脚本先清表再改列，并在 tasks 中显式标注 |
| 规则管理接口被任意调用 | 全局分析行为被篡改 | 沿用 `AnyLogin`（与基线一致）；**访问控制依赖网络隔离**，服务只允许内网部署；若需要收紧见 Open Questions |

## Migration Plan

1. 执行 `src/sql/patch.sql`（增量补丁，幂等）：`catalog_issue` 增加 `rule_code` / `rule_name` / `object_level` / `schema_name` 并删除 `issue_type`；新建 `analysis_rule`
   - **已有库跑 `patch.sql`，新建库跑 `pg_struct.sql`**，两者不混用
   - `ADD COLUMN` 会给旧行补默认值，这些行找不回对应规则（`rule_code = ''`），确认可清空后执行 `DELETE FROM catalog_issue;`（脚本中只写在注释里，不自动执行）
2. 部署后端，进入规则管理页或调用一次 `sync` 接口，让 7 条规则出现在界面上
3. 逐条确认启用状态与级别符合预期（默认值 ＝ 重构前的行为）
4. 重新采集一次，确认问题清单与重构前一致（同库同快照应产出同样的 7 类问题，只有 `issue_type` → `rule_code` 的表现形式变化）
5. 回滚：回退代码 + 恢复 `conf.ini` 的 4 个阈值项即可；`analysis_rule` 表留着不影响旧代码运行

## Open Questions

1. 是否需要**按数据源差异化**配置规则（A 库开「孤立表」、B 库关）？当前设计是全局一套。若需要，`analysis_rule` 需增加「数据源覆盖」子表，本 change 不覆盖
2. 规则被停用后，是否需要提供「用新规则集重新分析已有快照」的入口？否则历史快照仍保留旧规则集产出的问题
3. 阈值是否也需要按数据源覆盖？（不同规模的库对大表的定义不同）
4. 规则管理接口的权限：沿用 `AnyLogin`（与基线一致，依赖网络隔离），还是因为「会改动全局分析行为」而收紧到管理员？收紧会引入基线中不存在的 Token 依赖问题（参见 `add-datasource-catalog` 的 D7）
5. 是否需要「规则试运行」——用改动后的配置对指定快照重跑分析、预览结果但不落库？

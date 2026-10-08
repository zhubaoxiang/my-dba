# Change: 规则清单统一呈现，并让清单不再为空

## Why

规则清单页（`/datasource/rules`）在全新部署下**一条数据都没有**。

它读的是 `analysis_rule` 表，而这张表**只有点过「同步规则」之后才有行**，仓库里没有任何自动同步。表的注释自己写着「库中无记录时分析使用代码声明的默认值」——也就是说**分析**早就按代码声明走，只有**这个页面**还在读库。结果是首页显示「库表规则 7 条」，点进去清单是空的：同一个系统里两处口径打架。这正是 design D3 警告过的「把『没同步』说成了『没有规则』」，只不过首页按 D3 修好了，清单页没修。

同时，**SQL 分析的 18 条规则在界面上任何地方都列不出来**：SQL 分析页展示的是分析结果，不是规则清单；后端也没有 SQL 规则的清单接口。首页那格因此只能给数、不给入口（`add-homepage-rule-counts` 的 D5）。

本变更让规则清单**一打开就有内容**，并把两条能力线的规则放到同一页。

## What Changes

- 规则清单页改为**两个 Tab**：库表规则（7 条，可调）、SQL 规则（18 条，只读）
- 库表规则清单改为「**以代码声明为基底，叠加库中的覆盖项**」返回——未落库的规则也出现在清单里，展示代码默认值
- 规则定位从数据库自增 `id` 改为规则 `code`（**BREAKING**）
- **移除「同步规则」**：第一次修改某条规则时由该次修改自动落库，使用者不再需要任何前置动作
- 新增 `GET {SYS_NAME}/v1/sql-analysis/rules`：SQL 规则的只读清单（标识、名称、级别、是否需要表结构）
- 首页 SQL 规则那格补上「查看规则清单」入口，两格对称——推翻 `add-homepage-rule-counts` 的 D5（其理由「没有清单页可去」本次消失）

## Impact

- 受影响能力：
  - **修改** `analysis-rule-registry`（清单基底、定位方式、移除同步）
  - **修改** `system-overview`（两类规则都给清单入口）
  - **新增** `sql-analysis` 的「SQL 规则清单可见」能力
- 受影响代码：
  - `src/apps/datasource/views.py` —— `AnalysisRuleView`：list 换基底、定位换 code、删 sync、update 变 upsert
  - `src/apps/sqlanalysis/views.py` —— 新增 `rules` action
  - `static/src/views/datasource/rules.vue` —— 两个 Tab
  - `static/src/api/` —— 新增 SQL 规则清单调用；调整规则接口的定位方式
  - `static/src/views/home/index.vue` —— SQL 那格补入口
  - `docs/datasource.md`、`docs/sql-analysis.md`、`docs/overview.md`
- 无数据模型变更、无 DDL、无新依赖、无部署形态变化
- 接口仍为 `{SYS_NAME}/v1/analysis-rule` 与 `{SYS_NAME}/v1/sql-analysis`，**响应不含任何凭据**

## 明确不做

- **不做自定义规则（DSL）**：规则判定逻辑仍然全部是代码（`analysis-rule-registry` 的「规则的代码声明」不变）。DSL 化已决定不做，理由见 [design.md](design.md#规则-dsl已决定不做2026-10-08)
- 不做规则试运行
- **不改动任何一条规则的判定逻辑**，一行都不动
- 不迁移内置规则进库

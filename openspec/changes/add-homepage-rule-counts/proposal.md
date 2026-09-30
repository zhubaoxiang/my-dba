# Change: 首页新增「分析规则」块

## Why

首页展示了库与知识库的状态，但**系统的「判断标准」在首页完全没有位置**：

- **库表分析规则**的清单页（`/datasource/rules`）只能从侧边菜单找到，首页没有任何指向它的线索
- **SQL 分析的 18 条规则在界面上任何地方都列不出来**——`sql-analysis` 页面展示的是分析结果（问题清单），不是规则清单；`analysis_rule` 表只存库表规则的覆盖项

结果是使用者看得到「判出了什么问题」，看不到「**系统到底在替我检查什么**」。把两条能力线的规则条目数摆到首页，这个缺口就补上了，顺带给规则清单一个入口。

## What Changes

- 首页新增「分析规则」块：**库表分析规则 7 条**、**SQL 分析规则 18 条**
- 新增跨模块只读契约：
  - `apps/datasource/services.py` → `rule_counts()`
  - `apps/sqlanalysis/services.py`（**新增**，该模块目前没有服务层）→ `rule_counts()`
- `apps/overview/services.py`：新增 `rules` 块，纳入**分块兜底**
- 顺带修正 `system-overview` 里一处**与已归档设计相矛盾的旧规格**：见下

### 顺带修正：规格里「列表在限定高度内滚动」已过时

`system-overview` 的「总览数据一次取回」有一条场景要求「列表 MUST 在限定高度内滚动，MUST NOT 把首屏撑得过长」。但已归档的 `add-homepage-overview` 的 **D8 明确决定「页面接受滚动」**，理由是「一屏不滚」在多库时必然做不到，为它把卡片压扁等于长期为一个偶发约束付代价。实现按 D8 走（整页滚动、卡片网格），规格没跟着改。

本变更在修改「总览数据一次取回」以纳入规则概况的同时，把这条场景一并改成与 D8 一致。**这不是新的决定，是把规格补齐到已批准的设计上。**

## Impact

- 受影响能力：**修改** `system-overview`
- 受影响代码：
  - `src/apps/sqlanalysis/services.py`（**新增**：规则条数的跨模块读契约）
  - `src/apps/datasource/services.py`（新增 `rule_counts()`）
  - `src/apps/overview/services.py`（新增 `rules` 块）
  - `static/src/views/home/index.vue`（新增块）
  - `docs/overview.md`（位置、取数口径、SQL 规则暂无清单页这一不对称）
- 无新数据模型、无 DDL、无新依赖、无部署形态变化
- 接口仍为 `GET {SYS_NAME}/v1/overview`，响应新增一个 `rules` 键，**不含凭据**

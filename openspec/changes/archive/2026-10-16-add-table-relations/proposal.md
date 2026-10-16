# Change: 表的外键关系可见

## Why

README 把「看懂库表结构」列为第一个使用者的诉求，但现在界面只做到**单表**——表清单、表详情（列 / 索引 / 主键 / 外键 / 体积）。**「这个库的表怎么串起来的」答不出来。**

而数据早就齐了：采集阶段 `_foreign_keys()` 一直在采，`datasource.services.list_snapshot_tables()` 也把 `foreign_keys` 暴露出来了——**只是没有一处用它**。

## What Changes

- 表详情新增「关系」区：**引用了哪些表** / **被哪些表引用**，带列名与外键名，可点跳到对方表
- 「被引用」由该数据源快照内的**全部表**推导（遍历所有表的外键找指向本表的）

## Impact

- 受影响能力：**修改** `metadata-catalog`
- 受影响代码：
  - `src/apps/datasource/services.py`：新增关系推导（纯计算，跨全快照）
  - `src/apps/datasource/serializers.py`、`views.py`：表详情带上关系
  - `static/src/views/datasource/table-detail.vue`
  - `docs/datasource.md`
- 无新依赖、无 DDL、无部署形态变化

## 明确不做

- **不画 ER 图**：项目一直不引图表库（首页的火花线都是自绘的，理由写在代码里：「为一条 100×34 的小线引入几百 KB 的依赖不成比例」）。而全库 ER 图在百来张表时本就不可读，小范围关系图则仍需绘图能力——**「这张表跟谁有关」用列表答更好**
- **不做跨数据源的关系**：外键只在单个库内有意义
- **不做「孤立表」的判定**：那是既有规则的事（`isolated_table`），不在这里重复

# Change: 快照结构对比

## Why

`differ.diff_snapshots()` 与 `GET /catalog/diff` 早已完成，但前端 `catalogApi.diff` **没有任何页面调用**——能力存在、入口缺失。开发与测试最常问的「测试库和生产库结构差在哪」「这次采集改了什么」目前只能逐张表人工核对。

## What Changes

- 新增「结构对比」页面：左右各选数据源与快照，展示新增 / 删除 / 变更表
- `GET /catalog/diff` 响应扩展：两侧快照元信息、紧凑摘要、警告（跨方言 / 未采集项 / 按表名对齐）
- 新增 `ignore_schema` 选项：按表名对齐；任一侧存在同名表时**拒绝比较**，不悄悄合并
- 采集完成后自动与上一次快照对比，把紧凑摘要写入 `metadata_snapshot.change_summary`
- 采集任务列表展示结构变化摘要
- 首页数据源卡片提示「结构有变化」，可点入对比页
- 新增 DDL（`change_summary` 列）与相关文档

## Impact

- 受影响能力：**修改** `metadata-catalog`、**新增** `system-overview` 中的结构变化提示
- 受影响代码：
  - `src/apps/datasource/{differ,services,views,serializers,models}.py`
  - `src/apps/overview/services.py`、`src/apps/overview/tests.py`
  - `src/sql/pg_struct.sql`、`src/sql/patch.sql`
  - `static/src/views/datasource/diff.vue`（新增）、`router/index.js`、`views/datasource/index.vue`、`views/home/index.vue`
  - `docs/datasource.md`、`docs/overview.md`
- 无新依赖、无部署形态变化

## 明确不做

- **不做跨方言类型归一化**：两侧 db_type 不同时只警告「类型名称天然不同、差异仅供参考」，不把 `varchar` 与 `character varying` 强行视为相同——归一化可能抹掉真实差异
- **不做数据差异**：本期只比结构
- **不做变更历史时间线 / ER 图**
- **不做采集任务自动轮询**：沿用现有手动刷新

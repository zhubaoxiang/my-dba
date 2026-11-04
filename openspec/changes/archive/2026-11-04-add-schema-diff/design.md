# 设计：快照结构对比

## Context

`differ.diff_snapshots()` 是纯计算，输入两份 `metadata_snapshot.raw_data`；`CatalogView.diff` 接受任意两个快照 id。前端已有 `catalogApi.diff` 封装但从未调用。采集编排 `run_collect_task()` 目前不读取上一次快照。

## Goals / Non-Goals

- Goals：把 diff 能力接到界面；采集后可察觉结构漂移；首页能提示漂移
- Non-Goals：跨方言类型归一化、数据 diff、变更历史、自动轮询

## Decisions

### 摘要存 `metadata_snapshot.change_summary`

首页与任务列表都需要「这个快照相对上一次改了什么」，但不能在每次请求时加载两份 `raw_data` 做 diff。摘要存快照，查询只读小字段。

- 不在 `collect_task` 上存：任务是过程记录，快照才是结果；且首页按「最新快照」取数更自然
- 不即时计算：首页与任务列表会因此承担全量 diff 的代价
- 在 `objects.create()` **之前**算好并一并写入，保持快照不可变

摘要只存计数：`comparable` / `base_snapshot_id` / `base_collect_time` / 三个计数 / `has_changes`。完整差异仍按需走 `/catalog/diff`。

### 首次采集与计算失败必须显式不可比

首次采集没有上一次快照；摘要计算异常也不能让采集失败。两种情况都记 `{comparable: false, reason: ...}`，前端据此显示原因，**绝不显示成「无结构变化」**。

历史快照的 `change_summary` 为 `{}`，API 以 `null` 暴露为「未记录」——与「无变化」区分。

### `/catalog/diff` 响应带两侧元信息与警告

前端需要展示数据源名、采集时间、数据库类型，并判断是否跨方言。响应扩展为 `{base, target, diff, summary, warnings}`。现有前端没有消费者，可安全调整。

### `ignore_schema` 与同名表碰撞

跨库对比时 schema 名可能不同（PG 的 `public` vs MySQL 的库名）。`ignore_schema=true` 时按表名对齐；但若任一侧在多个 schema 下存在同名表，按表名对齐会 silently 合并，必须拒绝并列出冲突表名（返回 4000）。

### 跨方言不归一化

类型名在 PG / MySQL 间天然不同（`character varying` vs `varchar`）。做归一会掩盖真实差异，不做归一会产生假差异。选择后者 + 明确警告，符合项目「取不到 ≠ 没有」「误报比漏报贵」的一贯口径——这里改为「不把不确定的差异说成确定」。

## Risks / Trade-offs

| 风险 | 处理 |
|------|------|
| 快照不可变被破坏 | 摘要在 create 前计算并传入 |
| `list_datasource_status` 漏字段导致 N+1 | `.only()` 明确包含 `change_summary` |
| 跨方言假差异 | 响应返回 warnings，页面显式提示「仅供参考」 |
| 同名表被悄悄合并 | `TableKeyCollision` → 4000，列出冲突表 |
| 摘要与页面口径不同 | 摘要固定同源、schema 精确模式；页面勾选 `ignore_schema` 时提示口径不同 |
| 旧快照无摘要被读成无变化 | `{}` → API `null` → 前端不显示提示 |

## Migration Plan

1. 执行 `src/sql/patch.sql` 的增量 DDL
2. 部署后端与前端
3. 历史快照无需回填；下一次采集起产生摘要

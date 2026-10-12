# Change: 问题可被静音

## Why

问题清单**每次采集全量重算**，所以「这个我知道，别再报」无处安放。同一批问题会在每次采集后原样重现，把新问题淹掉——**而「清单里全是噪音就再也不看了」正是本项目一直在防的事**。

现有的「停用规则」解决不了这个：它是**全局**的，关掉一条规则等于这个库、那张表上的同类问题也一起不报了，代价过大。

## What Changes

- 新增**跨快照**的静音表：按 `数据源 + 规则 + 表 + 列` 记住「这一处不再报」
- 问题清单读时匹配并默认隐藏，顶部给出「已静音 N 条」与查看入口，可取消
- **各处口径一致**：静音的问题在问题清单与首页的问题统计中**都不再计入**（详见受影响能力）

## Impact

- 受影响能力：**修改** `metadata-catalog`（问题可静音）；**修改** `system-overview`（问题统计排除静音）
- 受影响代码：
  - `src/apps/datasource/models.py` + `sql/pg_struct.sql` + `sql/patch.sql`：新表 `issue_mute`
  - `src/apps/datasource/services.py`：问题清单与问题统计的静音过滤（**两处走同一函数**，避免口径分叉）
  - `src/apps/datasource/views.py`、`serializers.py`：静音与取消静音的接口
  - `src/apps/overview/services.py`：问题合计排除静音
  - `static/src/views/datasource/issues.vue`
  - `docs/datasource.md`、`docs/overview.md`
- 无新依赖、无部署形态变化

## 明确不做

- **不做「静音整条规则」**：那是现有的「停用规则」能力，已经有了，不该在这里再做一遍
- **不做静音的批量管理页**：先看实际用不用得上
- **不静音「库离线」这类采集状态**：静音只作用于分析判出的问题

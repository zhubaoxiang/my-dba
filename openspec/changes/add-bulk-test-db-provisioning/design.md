## Context

`add-test-db-provisioning` 的同步链路已完成，但大数据基础件未接线：

- `provisioned_database` 已有 `snapshot_id` / `seed` / `bulk` / `total_rows` / `processed_rows` / `current_table` / `database_created` / `cancel_requested` / `worker_id` / `heartbeat_time`
- `bulk.load_table`、`data.iter_rows`、`KeyPool`、`bulk_risks`、`ddl.build_plan(defer_indexes=True)` 已实现
- 没有 worker、没有取消/进度接口；`patch.sql` 与模型的状态默认值不一致

## Goals / Non-Goals

- Goals：超阈值自动异步；固定快照可复现；进度/取消/失败清理；复用现有批量写入能力
- Non-Goals：多 worker 横向扩展、引入调度器或消息队列、改造已创建测试库的同步维护接口

## Decisions

- **模式判定**：`总行数 > bulk_threshold` 转异步，否则同步。同步模式的有效上限即 `bulk_threshold`，避免出现两种模式都拒绝的区间；阈值按 `bulk_max_total_rows` 归一化。
- **执行进程**：在 `start.sh` 中守护独立 worker，与 gunicorn 分离；`sh` 增加 SIGTERM 转发，容器停止宽限 60 秒。理由：不新增 compose 服务，仍避免多 gunicorn worker 各跑一份。
- **任务领取**：`select_for_update(skip_locked=True)` 原子领取 `PENDING`；同一数据源唯一活跃任务由既有部分唯一索引保证。`IntegrityError` 在 `transaction.atomic()` 内回滚后转成可读错误。
- **快照固定**：预览返回 `snapshot_id`，执行时原样带回并写入记录；worker 按 `snapshot_data_by_id()` 重建结构，排队期间新采集不影响任务。
- **写入顺序**：建表 → 流式生成 + COPY/LOAD DATA → 后置索引/外键。唯一索引冲突会在后置阶段暴露，按失败整库清理；`bulk_risks` 只做容量预检。
- **取消语义**：`PENDING` 直接 `CANCELLED`；`RUNNING` 只置 `cancel_requested`，worker 在批边界退出并删库。`bulk.load_table` 的取消检查从逐行改为批边界。
- **失败/中断**：异常、SIGTERM、心跳超时统一按 `database_created` 删库并落 `FAILED`/`CANCELLED`；清理前校验库名格式，避免删到任意库。
- **连接参数**：MySQL bulk 连接开启 `local_infile=True`，读写超时取 `bulk_timeout_seconds`；PG 数据阶段设置 `statement_timeout`，后置建索引阶段设为 0。
- **进度**：列表字段 `processed_rows / total_rows / current_table / heartbeat_time` 已由序列化器自动返回，前端每 5 秒轮询，不新增进度接口。

## Risks / Trade-offs

| 风险 | 处理 |
|------|------|
| 同步模式可跑到阈值，HTTP 耗时接近 300s | gunicorn `timeout=300`；文档说明按目标库吞吐配置阈值 |
| 建库成功与 `database_created` 落库之间的崩溃窗口 | 接受极短窗口，最坏残留空库人工清理 |
| MySQL `local_infile` 未开启 | 自动回退多行 INSERT，吞吐下降但功能可用 |
| 后置建索引耗时很长 | 不设语句超时，依赖运维观察；任务心跳持续更新 |
| 单 worker 吞吐受限 | 默认单实例；`skip_locked` 为将来多实例留出边界 |

## Migration Plan

1. 执行 `patch.sql` 中 `add-bulk-test-db-provisioning` 段（列与索引已存在，仅校正状态默认值）
2. 更新 `conf.ini` 的 bulk / worker 配置
3. 重建后端镜像并重启容器，`start.sh` 会拉起 worker
4. 回滚：停止 worker、改回同步限制；已有异步记录按状态人工处理

## Open Questions

- 是否需要在多实例部署时限制 worker 数量：当前依赖单实例假设，后续可加 advisory lock

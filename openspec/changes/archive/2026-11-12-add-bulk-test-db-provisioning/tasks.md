## 1. 配置与数据模型

- [x] 1.1 `conf.ini` 恢复 bulk 默认值，新增 `bulk_pool_rows` / `bulk_timeout_seconds` / `bulk_tmp_dir` / worker 轮询、心跳、stale 配置
- [x] 1.2 `services._config()` 读取新键，并对 `bulk_threshold` 与 bulk 上限做归一化
- [x] 1.3 `patch.sql` 把 `status` 默认值改回 1，与模型和 `pg_struct.sql` 一致

## 2. 模式判定与快照固定

- [x] 2.1 `Prepared` 执行上下文与 `_validate_preset_limits`：总行数超过阈值即进入 bulk
- [x] 2.2 `datasource.services` 快照函数返回 `snapshot_id` / `datasource_id`
- [x] 2.3 `preview` 返回模式、快照 id、后置语句与风险警告
- [x] 2.4 `provision` 支持可选 `snapshot_id`，异步路径只落 `PENDING` 记录

## 3. 批量生成与写入

- [x] 3.1 `data.pool_targets` / `build_pools` / `track_pools`：边生成边维护父键池
- [x] 3.2 bulk 模式接入 `bulk_risks` 预检
- [x] 3.3 `bulk.load_table` 取消检查改到批边界，异常时回滚
- [x] 3.4 MySQL bulk 连接开启 `local_infile`，超时取 bulk 配置

## 4. worker 与任务编排

- [x] 4.1 `runner.py`：原子领取、stale 恢复、执行编排、进度/心跳、取消检查、失败清理
- [x] 4.2 `jobs/provisioning_worker.py`：常驻循环、信号处理、`close_old_connections`
- [x] 4.3 `start.sh` 守护 worker 并转发 SIGTERM；compose 增加 `stop_grace_period`
- [x] 4.4 gunicorn `timeout=300`，与前端执行超时对齐

## 5. 接口与前端

- [x] 5.1 `POST /test-db/{id}/cancel`；`provision` 透传 `snapshot_id`
- [x] 5.2 删除守卫：`PENDING` 直接删除、`RUNNING` 拒绝、终态删库
- [x] 5.3 前端行数上限、模式提示、进度列、5 秒轮询、取消按钮、状态配色与删除限制
- [x] 5.4 `api/provisioning.js` 增加 `cancel`

## 6. 测试与文档

- [x] 6.1 新增模式判定、入队、取消、删除守卫、批量写入、runner、恢复、API 测试
- [x] 6.2 `python manage.py test apps.provisioning`（101 项通过）
- [x] 6.3 `ruff check` / `ruff format --check` 通过
- [x] 6.4 `npm run build` 通过
- [x] 6.5 更新 `docs/test-db-provisioning.md`、`docs/architecture.md`、`docs/deployment.md`、`docs/development.md`
- [x] 6.6 `openspec validate add-bulk-test-db-provisioning --strict --no-interactive`
- [ ] 6.7 手工核对：真实测试库上执行一次大数据任务，验证进度、取消与崩溃恢复（需真实测试库与写账号）

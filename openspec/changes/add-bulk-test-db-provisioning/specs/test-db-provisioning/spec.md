## ADDED Requirements

### Requirement: 大数据模式自动判定与上限

系统 SHALL 根据总行数自动选择执行模式：总行数超过 `bulk_threshold` 时进入异步大数据模式，否则同步执行。同步模式的有效上限为 `bulk_threshold`；大数据模式 MUST 同时受单表上限与总量上限约束。

#### Scenario: 超过阈值进入异步

- **WHEN** 请求的总行数大于 `bulk_threshold`
- **THEN** 系统 MUST 创建 `pending` 记录并立即返回，MUST NOT 在请求线程内连接目标库

#### Scenario: 未超过阈值保持同步

- **WHEN** 请求的总行数小于或等于 `bulk_threshold`
- **THEN** 系统 MUST 按同步模式执行并返回完成后的记录

#### Scenario: 超过大数据上限被拒绝

- **WHEN** 单表或总行数超过 `bulk_max_rows_per_table` / `bulk_max_total_rows`
- **THEN** 系统 MUST 拒绝并说明具体限制

### Requirement: 固定快照与执行上下文

异步任务 MUST 固定预览时的快照与随机种子，排队期间的数据源采集 MUST NOT 改变任务结构。执行时 MUST 校验快照仍存在且属于该数据源。

#### Scenario: 排队期间发生新采集

- **WHEN** 任务排队后数据源完成了一次新的元数据采集
- **THEN** worker MUST 继续使用记录中固定的快照执行

#### Scenario: 快照被删除

- **WHEN** worker 执行时固定快照已不存在
- **THEN** 任务 MUST 失败并保留可读原因，MUST NOT 改用最新快照

### Requirement: 异步任务状态与并发控制

系统 SHALL 使用 `pending` / `running` / `ready` / `failed` / `cancelled` 表示异步任务状态。同一数据源 MUST 同时最多存在一个排队或执行中的任务；worker MUST 原子领取任务。

#### Scenario: 重复提交被拒绝

- **WHEN** 同一数据源已有排队或执行中的任务
- **THEN** 新的异步预置请求 MUST 被拒绝并提示等待或取消

#### Scenario: 取消排队任务

- **WHEN** 创建者或管理员取消 `pending` 任务
- **THEN** 记录 MUST 立即变为 `cancelled`，MUST NOT 连接目标库

#### Scenario: 取消执行中任务

- **WHEN** 创建者或管理员取消 `running` 任务
- **THEN** 系统 MUST 置取消标志，worker MUST 在批次边界停止、删除本次创建的库并把记录置为 `cancelled`

#### Scenario: 执行中不可删除

- **WHEN** 对 `running` 记录调用删除
- **THEN** 系统 MUST 拒绝并提示先取消

### Requirement: 大数据 worker 与中断恢复

系统 SHALL 由独立于 gunicorn 的常驻 worker 执行异步任务。worker MUST 周期性写入心跳与进度；启动时 MUST 回收心跳超时的 `running` 任务。

#### Scenario: worker 崩溃后恢复

- **WHEN** worker 进程中断且心跳超过 `worker_stale_seconds`
- **THEN** 重启后的 worker MUST 把该任务置为 `failed`（或 `cancelled`，若已请求取消）
- **AND** MUST 按 `database_created` 清理半成品数据库

#### Scenario: 进度可见

- **WHEN** 任务执行中查询记录列表
- **THEN** 响应 MUST 包含 `processed_rows`、`total_rows`、`current_table` 与 `heartbeat_time`

### Requirement: 大数据批量写入与后置约束

异步执行 SHALL 使用流式生成与批量写入：PostgreSQL 使用 `COPY FROM STDIN`，MySQL 优先 `LOAD DATA LOCAL INFILE`，不可用时回退多行 `INSERT`。唯一索引、普通索引与外键 MUST 在数据写入完成后创建。

#### Scenario: 批量写入成功

- **WHEN** worker 执行一个大表任务
- **THEN** 数据 MUST 分批写入并按 `bulk_commit_rows` 提交
- **AND** 全部表写入完成后 MUST 创建索引与外键，最后置为 `ready`

#### Scenario: 唯一约束冲突

- **WHEN** 后置创建唯一索引时发现重复值
- **THEN** 任务 MUST 失败，本次创建的数据库 MUST 被清理

#### Scenario: MySQL 不支持本地文件导入

- **WHEN** `LOAD DATA LOCAL INFILE` 因 `local_infile` 被拒绝
- **THEN** worker MUST 对当前及后续批次回退多行 `INSERT`，任务 MUST 继续执行

### Requirement: 大数据模式界面

测试库页面 MUST 在预览中展示执行方式；异步任务 MUST 在列表中展示进度与当前表，并支持取消。

#### Scenario: 预览显示异步

- **WHEN** 预览的总行数超过 `bulk_threshold`
- **THEN** 页面 MUST 显示「异步排队」提示，并说明提交后可在列表查看进度

#### Scenario: 列表轮询进度

- **WHEN** 列表中存在 `pending` 或 `running` 记录
- **THEN** 页面 MUST 定时刷新并展示进度百分比与当前表
- **AND** MUST 为创建者/管理员提供取消入口

#### Scenario: 未完成记录不可维护

- **WHEN** 记录状态为排队、执行中、失败或已取消
- **THEN** 页面 MUST NOT 提供追加数据、清空、新增表或删表入口

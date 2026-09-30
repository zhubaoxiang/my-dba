# 数据源指标采集

首页要回答的第一个问题是「**这个库现在能不能用**」。元数据快照回答不了它——采集是手动触发的，快照可能几天前；库掉了、连接数满了，首页却还显示着几天前的表数与问题分级。

指标采集就是补上这一块：周期性地连一下每个库，把**数据库自己报出来的**可用性与负载记下来。

对应模块：`src/apps/datasource/metrics.py`（探测与写库）、`src/jobs/metrics.py`（常驻进程），展示在首页（[overview.md](overview.md)）。

> **边界**：这是「**这个库能不能用**」的可用性信息，服务的是原有诉求（连不上就没法分析库表），**不是运维监控**。「不做监控」的边界声明不变——不做告警、不做阈值通知、不做 SLA。

## 采什么

| 指标 | PG 取数 | MySQL 取数 | 说明 |
|------|---------|-----------|------|
| 是否可连接 | 连接是否成功 | 同左 | 连不上时**只记失败原因**，其余项留空 |
| 当前连接数 | `pg_stat_activity` 计数 | `Threads_connected` | |
| 连接数上限 | `max_connections` | `max_connections` | 与上一项组成 `9/100` |
| 数据库大小 | `pg_database_size(current_database())` | `information_schema.tables` 求和 | |
| 缓存命中计数 | `pg_stat_database.blks_hit` | `Innodb_buffer_pool_read_requests` | 存**原始计数**，见下 |
| 缓存未命中计数 | `pg_stat_database.blks_read` | `Innodb_buffer_pool_reads` | 同上 |
| 读 / 写字节数 | `pg_stat_io` 的 `reads / writes × op_bytes` | `Innodb_data_read / written` | PG 16 以下没有该视图 → 留空（见下） |
| 未能采集到的项 | 记进 `unavailable` | 同左 | 前端据此说明「该项取不到」 |

**存原始计数而不是比率**：`pg_stat_database` 的命中率是「自服务启动以来」的累计平均值，几乎不随近期变化而变动，看不出问题。因此表里存累计计数，由**相邻两次采样的差值**算区间命中率（`services.list_datasource_metrics()`）。目标库重启会让计数器归零，差值为负时退回累计值——不能算出负命中率。

## 怎么采

```
start.sh
├── gunicorn（web，3 worker）
└── while true; do python jobs/metrics.py; sleep 5; done &   ← 采集进程
        └── 每 5 分钟一轮：collect_all() → 逐个探测写库 → purge_expired()
```

### 为什么是独立进程，而不是随 web 启动

gunicorn 跑 **3 个 worker**（`settings/gunicorn.py`）。任何随 web 进程启动的周期性任务都会**每个 worker 各跑一份**，同一个库被采三次、数据重复。独立进程从根上避开这件事。

### 为什么用外层 `while true` 守护

容器的 `restart: always` **只管容器、不管容器里的单个进程**——采集进程崩了，容器还活着，没人会发现指标停更了。外面套一层 `while true; ...; sleep 5` 让它在崩溃后 5 秒内自动拉起，且**不引入任何新依赖**（`apscheduler` / `django-q` 都没装，系统 cron 在容器里也没有）。

循环内还**兜住所有异常**：采某个库出错绝不该让这个进程退出，退出就再也没人采了。

> **已知限制**：多实例部署时**每个实例都会跑一份采集**。当前是单实例部署；需要时可用数据库锁或单独的调度容器解决。

### 配置

`src/config/conf.ini` 的 `[datasource_metrics]` 段：

| 键 | 默认 | 含义 |
|----|------|------|
| `interval` | `300` | 采集间隔（秒） |
| `retention_days` | `30` | 指标历史保留天数 |
| `probe_timeout` | `5` | 单个数据源的探测超时（秒） |

读不到或值非法时回落到默认值（`metrics.interval()` 等），不会因为少配一项就采不了。

## 保留与清理

每 5 分钟一轮、保留 30 天 → 每库约 **8,640 行**。清理放在采集轮次结束时顺带做（`purge_expired()`），**不额外引入第二个定时任务**。

这里用的是**物理删除**（不是项目惯用的软删除）：这是一张纯时间序列表，行没有业务含义、也不被任何东西引用，留着 `is_deleted=True` 的行只会让表和索引白白变大。

## 为什么不做机器资源（CPU / 内存 / 磁盘）

技术上**可行**：采集账号是超级用户，`pg_read_file('/proc/meminfo')` 能读到内存总量、负载、CPU 累计时间。但**明确否决**，理由三条：

1. **需要超级用户**。数据源采集的定位是「只读采集」，为此把采集账号提权到能读服务器上任意文件，权限放得过大
2. **它本质是「任意文件读」能力**。功能一旦存在，代码里就有读目标服务器本地文件的路径；若日后被改成「读使用者指定的路径」，即是文件泄露漏洞。这是本项目引入的第一项此类能力
3. **强绑 Linux 且 MySQL 走不通**；容器内读到的还是宿主机数据，多租户下会越界

**磁盘容量即便走 `/proc` 也拿不到**——那里没有文件系统容量，只能执行 `df`。

代价（明确接受）：首页**没有**机器 CPU / 内存 / 磁盘。指标只包含「数据库自己知道」的东西。

## 已知限制

| 限制 | 影响 | 说明 |
|------|------|------|
| **MySQL 分支未在真实 MySQL 上验证** | 可能取不到值 | 照 PG 对称实现；取不到时该项留空并在 `unavailable` 里标注，**不报错、不记 0**。与元数据采集器是同样的已知限制 |
| **在线状态最多陈旧 5 分钟** | 间隔内的掉线看不出来 | 首页标注采集时刻；需要实时判断时直接看「测试连通性」 |
| **PG 16 以下没有 `pg_stat_io`** | 读写量留空 | 不记 0——「没有 IO 数据」与「IO 为零」是两回事 |

> **实测修正**：`design.md` 里「目标 PG 为 17.11」说的是**本服务自己的库**；真正被纳管的库实测是
> **PG 11.1 与 PG 9.6.2**，两者都没有 `pg_stat_io`。也就是说**读写量目前在所有已纳管的库上都取不到**，
> 卡片上这一项恒为空、`unavailable` 恒含 `io`。这是预期行为（如实留空），不是故障；
> 等接了 PG 16+ 的库它就会自动出现。

## 相关文件

| 文件 | 职责 |
|------|------|
| `src/apps/datasource/metrics.py` | 按方言的指标探测、写库、过期清理 |
| `src/apps/datasource/services.py` | `list_datasource_metrics()`：最新值 + 24 小时趋势（降采样），跨模块读契约 |
| `src/jobs/metrics.py` | 常驻采集循环（独立于 web 进程） |
| `src/start.sh` | 启动并守护采集进程 |
| `src/sql/pg_struct.sql` / `patch.sql` | `datasource_metric` 表与两条索引 |

## 索引与取数代价

`list_datasource_metrics()` 每次首页加载都会跑，因此它只回两类行，都受控：

| 查询 | 索引 | 每库回多少行 |
|------|------|-------------|
| 最新值（`DISTINCT ON (datasource_id)`） | `idx_datasource_metric_ds (datasource_id, create_time DESC)` | **1 行** |
| 最近 24 小时采样（趋势 + 区间命中率） | `idx_datasource_metric_time (create_time DESC)` | ≤ 288 行 |

**两条索引各管一件事**：跨全部数据源按时间过滤的查询，首列是 `datasource_id` 的复合索引带不动
（实测 15 库 × 30 天时退化为全表扫描，130k 行；加上 `create_time` 索引后只读 72 个堆块）。

> **曾经的坑**：早先的实现把整张指标表 `order_by(...)` 捞进 Python 再按库取前两条。3 个库 30 天
> 就是 2.6 万行 × 约 700 字节 ≈ **每次打开首页传 18MB**。现在客户端只回每库一行 + 24 小时的点。
>
> 剩余代价：`DISTINCT ON` 仍需扫一遍索引（服务端 shared_buffers 内，不回传），行数随「库数 × 保留天数」
> 线性增长。接了几十个库之后若成为瓶颈，改成按库 `LIMIT 1` 的 LATERAL 连接即可。

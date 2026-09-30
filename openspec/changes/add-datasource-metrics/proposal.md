# Change: 数据源指标采集与首页展示

## Why

首页现在只显示「表数 + 问题分级 + 采集时间」，都是**采集那一刻的静态结果**。使用者看不到库**现在**的状态：

- **连不上就什么都做不了**——数据源掉了、网络断了，首页却还显示着几天前的快照，看不出异常
- **连接数满了使用者就写不进数据**，而这件事只有连上去查才知道
- 库大小与缓存命中率的变化，是判断「这个库最近是不是不对劲」最直接的线索

这些都只需要**已有的只读采集通道**就能拿到，不需要任何新的部署组件或高权限账号。

> **边界说明**：这块是「**这个库能不能用**」的可用性信息，服务的是原有诉求（连不上就没法分析库表），**不是运维监控**。因此「不做监控」的边界声明不变；机器层面的 CPU / 内存 / 磁盘**不做**（需读服务器 `/proc`，要用超级用户，代价与收益不成比例）。

## What Changes

- 新增能力 `datasource-metrics` 与模块 `src/apps/datasource/metrics.py`
- 新增 `datasource_metric` 表：一行 = 一个库的一次探测（在线、连接数与上限、库大小、缓存命中率、读写量）
- 新增 `src/jobs/metrics.py` 常驻采集进程，由 `start.sh` 以 `while true` 守护启动；**每 5 分钟**一轮，历史**保留 30 天**
- 首页库卡片铺开：概览数字带（库数 / 在线数 / 问题合计 / 知识库数）、每库的指标格、**连接数 24 小时趋势图**
- 首页知识库卡片铺开：检索块数、文档状态分布条、问答次数、最近更新
- 明确**不做**：机器 CPU / 内存 / 磁盘；指标的历史范围切换（先只画 24 小时）

## Impact

- 受影响能力：**新增** `datasource-metrics`；**修改** `system-overview`（首页展示指标与趋势、知识库卡片铺开）
- 受影响代码：
  - `src/apps/datasource/metrics.py`（**新增**：按方言的指标探测）
  - `src/apps/datasource/services.py`（新增跨模块只读契约 `list_datasource_metrics()`）
  - `src/apps/knowledge/services.py`（扩充 `list_knowledge_base_status()`）
  - `src/apps/overview/services.py`（接入指标与趋势）
  - `src/jobs/metrics.py`（**新增**：常驻采集进程）
  - `src/start.sh`（启动采集进程并守护）
  - `src/sql/pg_struct.sql` / `src/sql/patch.sql`（新增 `datasource_metric` 表）
  - `static/src/views/home/index.vue`（铺开）
- **部署形态变化**：容器内多一个常驻进程（不需新增容器）
- 无新依赖

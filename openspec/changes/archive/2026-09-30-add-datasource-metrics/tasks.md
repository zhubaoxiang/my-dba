## 1. 数据模型

- [x] 1.1 `apps/datasource/models.py`：新增 `DatasourceMetric`（继承 `AbstractTimeFiledModel`，显式 `db_table="datasource_metric"`）
- [x] 1.2 `src/sql/pg_struct.sql`：新增建表语句与索引（`datasource_id + create_time` 复合索引，供取最新值与趋势）
- [x] 1.3 `src/sql/patch.sql`：追加幂等增量补丁
- [x] 1.4 `utils/custom_enum.py`：如需要，补指标相关的枚举（当前预计不需要）

## 2. 后端：指标探测

- [x] 2.1 `apps/datasource/metrics.py`：`probe(datasource_id) -> dict`，用**已有的只读连接**取数
- [x] 2.2 PostgreSQL 分支：连接数、`max_connections`、库大小、缓存命中率、读写量（`pg_stat_io`，PG 16+ 缺失时留空）
- [x] 2.3 MySQL 分支：`Threads_connected`、`max_connections`、`information_schema` 求和、`Innodb_buffer_pool_read*`、`Innodb_data_*`
- [x] 2.4 连不上时返回 `is_online=False` + 失败原因，MUST NOT 抛异常
- [x] 2.5 **不读目标服务器本地文件、不需要超级用户**（在模块 docstring 里写明这条边界与理由）
- [x] 2.6 某项指标该方言取不到时留空并说明，MUST NOT 记为 0
- [x] 2.7 单测：PG 正常取值、连接失败、某项缺失三种情形（mock 连接）

## 3. 后端：采集任务

- [x] 3.1 `apps/datasource/services.py`：`collect_all_metrics()`——遍历未删除数据源逐个探测并写库
- [x] 3.2 单个数据源失败被兜住，继续采集其余；整体记日志
- [x] 3.3 保留期清理：删除超过 30 天的记录（配置项控制）
- [x] 3.4 `src/jobs/metrics.py`：常驻循环，间隔取配置（默认 5 分钟）；**循环内兜住所有异常，绝不退出**
- [x] 3.5 `src/start.sh`：以 `while true; do python jobs/metrics.py; sleep 5; done &` 启动并守护
- [x] 3.6 `config/conf.ini` 新增 `[datasource_metrics]` 段：采集间隔、保留天数、探测超时
- [x] 3.7 单测：多数据源逐个采集、单个失败不影响其余、清理只删过期数据

## 4. 后端：跨模块契约与聚合

- [x] 4.1 `apps/datasource/services.py`：`list_datasource_metrics()`——每个数据源的最新指标 + 24 小时趋势点（**降采样到最多 48 点**）
- [x] 4.2 趋势点按时间升序，缺失的时段跳过（不做插值，避免画出假数据）
- [x] 4.3 无任何指标记录时返回「尚未采集」，供前端显示「指标采集中」
- [x] 4.4 `apps/knowledge/services.py`：`list_knowledge_base_status()` 扩充——检索块总数、各状态文档数、问答次数、最近更新时间
- [x] 4.5 三组统计各用**一次聚合查询**（`GROUP BY knowledge_base_id`），不逐个知识库查
- [x] 4.6 单测：趋势降采样点数、无数据、知识库各项统计准确

## 5. 后端：首页接口

- [x] 5.1 `apps/overview/services.py`：接入指标与趋势；新增概览数字（库数 / 在线数 / 问题合计 / 知识库概况）
- [x] 5.2 指标块同样纳入**分块兜底**，取不到时标不可用而非返回 0
- [x] 5.3 响应仍**不含任何凭据**
- [x] 5.4 单测：含指标的正常返回、指标块失败时其余仍返回、概览数字准确

## 6. 前端

- [x] 6.1 概览数字带：库数 / 在线数（`2/3` 形式）/ 问题合计（含高危）/ 知识库与文档数
- [x] 6.2 库卡片网格（2 列）：标题区（状态点带光晕 + 库名 + 类型标签）
- [x] 6.3 指标格（2×2）：当前连接（`9/100`，按占上限比例分档着色）、数据库大小、缓存命中率（低时着色）、表数量
- [x] 6.4 趋势区：**自绘 SVG**（渐变填充 + polyline），最近 24 小时连接数；Y 轴自适应、颜色按占上限比例分档
- [x] 6.5 页脚：问题分级 + 采集时间；动作按状态给（看问题 / 重新采集 / 去采集）
- [x] 6.6 离线卡片：整卡换红底红边，**完整显示失败原因**（不截断），不显示过期指标、不画趋势
- [x] 6.7 指标历史不足时显示「指标采集中…」，不留白、不画假线
- [x] 6.8 知识库卡片：文档数与检索块数、文档状态堆叠条（成功 / 待处理 / 失败）、问答次数、最近更新、描述一行省略
- [x] 6.9 空知识库卡：说明「问答时检索不到内容」，不只显示 0
- [x] 6.10 空状态：无库 / 无知识库的虚线引导卡
- [x] 6.11 窄屏（`< lg`）退化为单列
- [x] 6.12 `npm run build` 通过

## 7. 文档

- [x] 7.1 新增 `docs/datasource-metrics.md`：采什么、怎么采（独立进程 + 守护）、保留策略、**为什么不做机器资源**（含 `/proc` 方案的否决理由）、MySQL 未验证
- [x] 7.2 `docs/overview.md`：补充指标与趋势的取数口径、Y 轴与配色的设计意图、离线与「采集中」的呈现
- [x] 7.3 `README.md`：文档索引补入
- [x] 7.4 `docs/architecture.md`：目录树补 `jobs/metrics.py`；部署一节补常驻进程
- [x] 7.5 `docs/deployment.md`：写明容器内多一个常驻进程及其守护方式

## 8. 校验

- [x] 8.1 `python .ci/custom-checks/scaffold_check.py`
- [x] 8.2 `ruff check` 与 `ruff format --check`
- [x] 8.3 `cd static && npm run build`
- [x] 8.4 `ENV_TYPE=test python manage.py test apps.datasource apps.knowledge apps.overview apps.sqlanalysis`
- [x] 8.5 **真实数据核对**：对一个真实库跑一轮采集，逐项比对指标与实际（连接数、库大小、命中率），并确认离线库被正确标出
- [x] 8.6 确认 `datasource_metric` 表实际建出且索引生效
- [x] 8.7 `openspec validate add-datasource-metrics --strict --no-interactive`

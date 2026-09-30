# datasource-metrics Specification

## Purpose
TBD - created by archiving change add-datasource-metrics. Update Purpose after archive.
## Requirements
### Requirement: 指标采集范围

系统 SHALL 周期采集每个已纳管数据源的**可用性与负载指标**。采集 MUST 只通过只读连接取数，MUST NOT 采集目标服务器本机的资源信息，MUST NOT 要求超出只读采集所需的权限。

#### Scenario: 采集可用性与连接情况

- **WHEN** 一轮采集执行
- **THEN** MUST 记录该数据源是否可连接
- **AND** 可连接时 MUST 记录当前连接数与配置的连接数上限
- **AND** 不可连接时 MUST 记录失败原因

#### Scenario: 采集库自身的容量与命中情况

- **WHEN** 数据源可连接
- **THEN** MUST 记录数据库占用大小、缓存命中率与读写量

#### Scenario: 不采集目标服务器本机资源

- **WHEN** 采集任一数据源的指标
- **THEN** MUST NOT 读取目标服务器上的本地文件
- **AND** MUST NOT 采集该机器的 CPU、内存或磁盘信息
- **AND** MUST NOT 需要超级用户权限

### Requirement: 采集调度

系统 SHALL 由**独立于 web 进程**的常驻任务执行周期采集。同一部署实例内 MUST NOT 因 web 进程数量而重复采集。

#### Scenario: 独立于 web 进程运行

- **WHEN** web 服务以多个 worker 启动
- **THEN** 周期采集 MUST 只在单个进程中执行，MUST NOT 每个 worker 各跑一份

#### Scenario: 采集进程异常退出后可自行恢复

- **WHEN** 采集进程因异常退出
- **THEN** MUST 能自动重新启动，MUST NOT 需要人工介入

#### Scenario: 单个数据源失败不影响其余

- **WHEN** 某个数据源探测失败
- **THEN** MUST 记录该数据源的失败结果，并 MUST 继续采集其余数据源
- **AND** MUST NOT 因单个数据源失败而中断整轮采集

### Requirement: 指标保留

系统 SHALL 保留有限时长的指标历史并自动清理，MUST NOT 无限增长。

#### Scenario: 历史保留期

- **WHEN** 指标被写入
- **THEN** MUST 至少保留既定的保留时长，供展示趋势使用

#### Scenario: 自动清理过期指标

- **WHEN** 存在超过保留时长的指标记录
- **THEN** MUST 被自动清理，MUST NOT 需要人工执行

### Requirement: 指标采集的已知限制须可见

系统 SHALL 在指标不可用时如实呈现，MUST NOT 让使用者把「取不到」误读为「正常」或「没有问题」。

#### Scenario: 数据源离线

- **WHEN** 某数据源在最近一轮采集中不可连接
- **THEN** 该数据源 MUST 被标记为离线并显示失败原因
- **AND** MUST NOT 继续展示上一次成功采集的指标而不加区分

#### Scenario: 某种方言取不到某项指标

- **WHEN** 某数据源类型不支持某项指标
- **THEN** 该项 MUST 留空并说明，MUST NOT 记为 0 或直接省略而不加说明


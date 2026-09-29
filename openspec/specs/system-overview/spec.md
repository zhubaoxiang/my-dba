# system-overview Specification

## Purpose
TBD - created by archiving change add-homepage-overview. Update Purpose after archive.
## Requirements
### Requirement: 系统就绪状态提示

系统 SHALL 在首页提示模型配置的就绪状态，使使用者能判断「某个功能用不了」是不是因为还没配置。提示 MUST 只依据本地配置判断，MUST NOT 因探测外部服务而阻塞首页加载。

#### Scenario: 配置齐全

- **WHEN** 对话模型与嵌入模型均已配置生效
- **THEN** 首页 MUST 给出简短的就绪说明，MUST NOT 占据显著版面

#### Scenario: 缺少对话模型

- **WHEN** 没有生效中的对话模型
- **THEN** 首页 MUST 说明受影响的**具体功能**（知识问答、SQL 分析的 AI 解读），并给出去哪里配置的入口

#### Scenario: 缺少嵌入模型

- **WHEN** 没有生效中的嵌入模型
- **THEN** 首页 MUST 说明受影响的**具体功能**（知识库检索与文档摄入），并给出配置入口

#### Scenario: 两项都缺

- **WHEN** 两类模型都没有配置
- **THEN** 首页 MUST 同时列出两项，MUST NOT 只显示其一

#### Scenario: 不探测外部服务

- **WHEN** 首页加载
- **THEN** 系统 MUST NOT 因探测向量服务或模型接口的可达性而等待
- **AND** 外部服务不可达的情况 MUST 由实际使用时的错误提示暴露，MUST NOT 由首页承担

### Requirement: 已纳管库的状态总览

系统 SHALL 在首页逐条展示每个已纳管数据源的状态，包含关键指标与可执行的下一步动作。

#### Scenario: 已采集的库显示问题分级

- **WHEN** 某数据源有采集快照
- **THEN** MUST 显示其表数量、按严重级别的问题数量，以及该快照的采集时间
- **AND** 问题数 MUST 基于**最近一次快照**统计，MUST NOT 累计历史快照

#### Scenario: 未采集或采集失败的库不显示问题分级

- **WHEN** 某数据源没有可用快照（尚未采集、采集失败或采集进行中）
- **THEN** MUST 显示其采集状态，MUST NOT 显示问题数量
- **AND** MUST NOT 让使用者把「还没有数据」误读为「没有问题」

#### Scenario: 每个库提供下一步动作

- **WHEN** 使用者查看某个库
- **THEN** 该行 MUST 提供与它当前状态相符的动作（查看问题 / 触发采集 / 重新采集）

#### Scenario: 需要处理的库排在前面

- **WHEN** 首页展示多个库
- **THEN** 采集失败与未采集的库 MUST 排在已采集的库之前
- **AND** 已采集的库 MUST 按高危问题数量降序排列

### Requirement: 知识库的状态总览

系统 SHALL 在首页逐条展示每个知识库的状态，包含文档数量、摄入状态与可执行的下一步动作。

#### Scenario: 显示文档数与摄入状态

- **WHEN** 首页展示某个知识库
- **THEN** MUST 显示其未删除的文档数量与摄入状态概要

#### Scenario: 摄入失败必须显著标出

- **WHEN** 某知识库存在摄入失败的文档
- **THEN** 该知识库 MUST 被显著标出，并 MUST 提供查看失败原因的入口
- **AND** MUST NOT 把摄入失败混在「文档总数」里让使用者看不出来

#### Scenario: 每个知识库提供下一步动作

- **WHEN** 使用者查看某个知识库
- **THEN** 该行 MUST 提供提问与管理文档的入口

### Requirement: 总览数据一次取回

系统 SHALL 通过**单个**接口返回首页所需的全部数据，使首页只发一次请求。

#### Scenario: 单次请求取回全部总览数据

- **WHEN** 首页加载
- **THEN** 系统 MUST 通过一个接口返回就绪状态、库列表与知识库列表，MUST NOT 要求前端分别请求多次

#### Scenario: 列表过长时可滚动

- **WHEN** 库或知识库的数量超出首屏可容纳的范围
- **THEN** 列表 MUST 在限定高度内滚动，MUST NOT 把首屏撑得过长

### Requirement: 总览的部分失败处理

系统 SHALL 在单个数据来源出错时仍返回其余部分，MUST NOT 因一块数据取不到而使整个总览失败。

#### Scenario: 某块数据取不到时其余照常返回

- **WHEN** 某个模块的数据查询失败
- **THEN** 该块 MUST 返回「暂不可用」，其余部分 MUST 照常返回

#### Scenario: 取不到的数据不得伪装成没有数据

- **WHEN** 某块数据因失败而没有取到
- **THEN** 界面 MUST 如实标注该块不可用，MUST NOT 显示成「没有数据」

### Requirement: 首页的空状态与刷新

系统 SHALL 在没有任何数据时给出引导，并提供可预期的刷新方式。

#### Scenario: 还没有接入数据库

- **WHEN** 一个数据源都没有接入
- **THEN** 该栏 MUST 给出接入的引导入口，MUST NOT 留白

#### Scenario: 还没有知识库

- **WHEN** 一个知识库都没有
- **THEN** 该栏 MUST 给出创建的引导入口

#### Scenario: 进入时加载并提供手动刷新

- **WHEN** 使用者进入首页
- **THEN** 系统 MUST 加载一次数据，并 MUST 提供手动刷新且显示最近刷新时间

#### Scenario: 不自动轮询

- **WHEN** 首页停留不动
- **THEN** 系统 MUST NOT 周期性自动请求


## ADDED Requirements

### Requirement: 模型调用记录采集

系统 SHALL 在每次对话模型或嵌入模型调用时写入一条用量记录，包含用户、功能、模型、耗时与结果。对话调用 MUST 在模型返回 usage 时记录 token 数；未返回时 MUST 留空，MUST NOT 估算。系统 MUST NOT 记录 prompt 或回答正文。

#### Scenario: 对话调用成功

- **WHEN** 业务调用对话模型并成功返回
- **THEN** 系统 MUST 写入一条 `call_type=chat` 的记录
- **AND** 记录 MUST 包含用户、功能、模型、耗时与 `success=true`
- **AND** 模型返回 usage 时 MUST 记录 prompt / completion / total token

#### Scenario: 流式调用未返回用量

- **WHEN** 流式调用结束但网关未返回 usage
- **THEN** 系统 MUST 仍记录调用次数与耗时
- **AND** token 字段 MUST 为空，MUST NOT 用估算值填充

#### Scenario: 嵌入调用

- **WHEN** 知识库摄入或检索调用嵌入模型
- **THEN** 系统 MUST 写入一条 `call_type=embedding` 的记录
- **AND** 记录 MUST 包含耗时与处理的文本块数

#### Scenario: 调用失败

- **WHEN** 模型调用超时、连接失败或返回错误
- **THEN** 系统 MUST 写入一条 `success=false` 的记录
- **AND** 记录 MUST 包含错误分类码

#### Scenario: 记录不影响业务

- **WHEN** 用量记录写库失败
- **THEN** 系统 MUST 只记录日志
- **AND** MUST NOT 使原业务调用失败或返回错误

#### Scenario: 不记录内容

- **WHEN** 任何模型调用完成
- **THEN** 用量记录 MUST NOT 包含 prompt、回答正文或文档内容

### Requirement: 调用归属

系统 SHALL 为每次调用记录发起用户、功能与请求标识。一次用户请求内的多次模型调用 MUST 共享同一个 `request_id`；后台任务 MUST 记为 `system`。

#### Scenario: 用户请求归属

- **WHEN** 已登录用户通过业务接口触发模型调用
- **THEN** 记录 MUST 包含该用户的 username、对应功能与 `request_id`

#### Scenario: 一次请求多次调用

- **WHEN** 问答 agent 在一次请求中多次调用模型
- **THEN** 这些记录 MUST 共享同一个 `request_id`

#### Scenario: 后台任务

- **WHEN** 没有用户请求上下文的进程调用模型
- **THEN** 记录的用户 MUST 为 `system`

#### Scenario: 未识别的功能

- **WHEN** 调用来源无法映射到已知功能
- **THEN** 记录的功能 MUST 标记为 `other`
- **AND** 系统 MUST 记录日志以便补齐映射

### Requirement: 用量查询接口

系统 SHALL 提供汇总与明细查询接口，仅超级管理员可用。汇总接口 MUST 支持时间范围与按用户/模型/功能分组；明细接口 MUST 支持分页与按成功状态、最小耗时过滤。

#### Scenario: 汇总查询

- **WHEN** 超级管理员按时间范围查询汇总
- **THEN** 系统 MUST 返回调用次数、成功率、token 合计与平均耗时
- **AND** MUST 返回按天趋势与所选维度的分组结果

#### Scenario: 明细查询

- **WHEN** 超级管理员查询调用明细
- **THEN** 系统 MUST 分页返回单次调用记录
- **AND** MUST 支持按成功状态与最小耗时过滤

#### Scenario: 普通用户访问

- **WHEN** 普通用户调用用量接口
- **THEN** 系统 MUST 返回业务码 4005
- **AND** MUST NOT 返回任何用量数据

#### Scenario: 聚合部分失败

- **WHEN** 汇总的某一块查询失败
- **THEN** 系统 MUST 把该块标记为不可用
- **AND** MUST NOT 使整个响应失败

### Requirement: 用量数据保留

系统 SHALL 保留最近 90 天的用量记录，超期记录 MUST 物理删除。

#### Scenario: 超期清理

- **WHEN** 清理任务运行且存在超过 90 天的记录
- **THEN** 系统 MUST 物理删除这些记录
- **AND** 查询 MUST NOT 再返回它们

#### Scenario: 未超期记录不受影响

- **WHEN** 清理任务运行
- **THEN** 90 天内的记录 MUST 全部保留

### Requirement: 系统管理页面

系统 SHALL 在「系统管理」页提供「模型用量」标签页，仅超级管理员可见。页面 MUST 展示汇总卡片、按天趋势、分组表与失败/慢调用明细；数据缺失 MUST 明确标注，MUST NOT 显示为 0。

#### Scenario: 管理员查看用量

- **WHEN** 超级管理员进入系统管理页
- **THEN** 页面 MUST 展示「模型用量」标签页
- **AND** 支持切换时间范围与分组维度

#### Scenario: 普通用户不可见

- **WHEN** 普通用户登录
- **THEN** 侧边栏 MUST NOT 展示「系统管理」
- **AND** 直接访问 `/system` MUST 被重定向回首页

#### Scenario: 用量缺失

- **WHEN** 所选时间范围内部分调用未返回 token 用量
- **THEN** 页面 MUST 明确标注用量不可用
- **AND** MUST NOT 把缺失显示为 0

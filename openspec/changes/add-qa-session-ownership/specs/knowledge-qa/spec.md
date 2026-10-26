## ADDED Requirements

### Requirement: 问答会话归属

问答会话 MUST 归属于创建它的登录用户。会话列表 MUST 只返回当前用户的会话；会话详情、删除、继续提问 MUST 只允许操作当前用户的会话。

#### Scenario: 列表只返回自己的会话

- **WHEN** 用户查询问答会话列表
- **THEN** 系统 MUST 只返回该用户创建的会话
- **AND** MUST NOT 返回其他用户的会话

#### Scenario: 不能读取他人的会话

- **WHEN** 用户请求一个属于其他用户的会话 id
- **THEN** 系统 MUST 返回「会话不存在」
- **AND** MUST NOT 暴露该会话是否存在

#### Scenario: 不能删除他人的会话

- **WHEN** 用户删除一个属于其他用户的会话 id
- **THEN** 系统 MUST 返回「会话不存在」
- **AND** MUST NOT 软删除该会话

#### Scenario: 不能续问他人的会话

- **WHEN** 用户在提问或流式提问中指定一个属于其他用户的会话 id
- **THEN** 系统 MUST 返回「会话不存在」
- **AND** MUST NOT 读取该会话历史或写入新消息

#### Scenario: 自己的会话正常可用

- **WHEN** 用户访问、提问或删除自己创建的会话
- **THEN** 系统 MUST 按既有行为正常处理

#### Scenario: 不能直接创建或修改会话

- **WHEN** 用户直接调用会话的创建、修改或部分修改接口
- **THEN** 系统 MUST 拒绝该请求
- **AND** MUST NOT 创建可伪造 `creator` 的会话，MUST NOT 修改任何会话

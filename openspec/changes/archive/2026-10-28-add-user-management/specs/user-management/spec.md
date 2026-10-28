## ADDED Requirements

### Requirement: 本地用户管理

系统 SHALL 提供本地 `auth_user` 用户的管理接口，支持列表、详情、创建、修改与删除。接口 MUST 仅对超级管理员开放，MUST NOT 管理 LDAP 用户。

#### Scenario: 用户列表

- **WHEN** 超级管理员查询用户列表
- **THEN** 系统 MUST 分页返回本地用户
- **AND** 每项 MUST 包含用户名、邮箱、角色、启用状态、创建时间与最后登录

#### Scenario: 创建用户

- **WHEN** 超级管理员提交用户名、密码、角色与启用状态
- **THEN** 系统 MUST 创建本地用户，并按角色设置 `is_superuser` / `is_staff`
- **AND** 密码 MUST 经过哈希存储

#### Scenario: 修改用户

- **WHEN** 超级管理员修改邮箱、角色、启用状态或密码
- **THEN** 系统 MUST 更新对应用户
- **AND** 密码留空时 MUST 保持原密码不变
- **AND** 用户名 MUST NOT 被修改

#### Scenario: 删除用户

- **WHEN** 超级管理员删除一个本地用户
- **THEN** 系统 MUST 物理删除该 `auth_user` 记录
- **AND** 删除后该用户 MUST NOT 再能登录

#### Scenario: 不管理 LDAP 用户

- **WHEN** 用户通过 LDAP 登录
- **THEN** 该用户 MUST NOT 出现在本地用户列表中
- **AND** 用户管理接口 MUST NOT 创建或修改 LDAP 账号

### Requirement: 两级角色与访问控制

系统 SHALL 支持两种角色：超级管理员与普通用户。用户管理接口 MUST 仅超级管理员可用；普通用户 MUST 可以使用全部业务功能。

#### Scenario: 超级管理员访问

- **WHEN** 超级管理员调用用户管理接口
- **THEN** 系统 MUST 正常处理

#### Scenario: 普通用户访问用户管理

- **WHEN** 普通用户调用用户管理接口
- **THEN** 系统 MUST 返回业务码 4005
- **AND** MUST NOT 执行任何增删改查

#### Scenario: 普通用户使用业务功能

- **WHEN** 普通用户访问数据源、库表分析、SQL 分析、DDL 评审、知识问答等业务接口
- **THEN** 系统 MUST 按既有权限正常处理，MUST NOT 因角色为普通用户而拒绝

#### Scenario: 未登录与无权限可区分

- **WHEN** 请求未携带有效 JWT
- **THEN** 系统 MUST 返回 4003
- **WHEN** 请求已登录但权限不足
- **THEN** 系统 MUST 返回 4005
- **AND** 前端 MUST NOT 因 4005 清理登录态

### Requirement: 账户安全约束

系统 SHALL 保护管理员账号，避免误操作导致无法管理。账号密码 MUST 使用与本地登录一致的哈希方式存储。

#### Scenario: 不能删除自己

- **WHEN** 超级管理员尝试删除自己的账号
- **THEN** 系统 MUST 拒绝该操作

#### Scenario: 不能移除最后一个超级管理员

- **WHEN** 删除、禁用或降级操作会导致没有可用的超级管理员
- **THEN** 系统 MUST 拒绝该操作

#### Scenario: 不能修改自己的角色和状态

- **WHEN** 超级管理员修改自己的账号
- **THEN** 系统 MUST 允许修改邮箱和密码
- **AND** MUST 拒绝修改自己的角色与启用状态

#### Scenario: 登录时间可追溯

- **WHEN** 本地账号登录成功
- **THEN** 系统 MUST 更新该账号的 `last_login`

### Requirement: 用户管理界面

系统 SHALL 提供用户管理页面，仅超级管理员可见。页面 MUST 支持用户列表、创建、编辑与删除，普通用户 MUST NOT 看到入口，也 MUST NOT 通过直接访问路由进入。

#### Scenario: 管理员使用页面

- **WHEN** 超级管理员进入用户管理页
- **THEN** 页面 MUST 展示本地用户列表，并提供新增、编辑、启用/停用、删除操作

#### Scenario: 普通用户看不到入口

- **WHEN** 普通用户登录
- **THEN** 侧边栏 MUST NOT 展示「用户管理」与「系统管理」
- **AND** 直接访问 `/user` 或 `/system` MUST 被重定向回首页

#### Scenario: 删除前确认

- **WHEN** 超级管理员点击删除用户
- **THEN** 页面 MUST 二次确认后再提交删除请求

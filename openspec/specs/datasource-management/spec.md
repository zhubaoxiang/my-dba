# datasource-management Specification

## Purpose
TBD - created by archiving change add-datasource-catalog. Update Purpose after archive.
## Requirements
### Requirement: 数据源配置管理

系统 SHALL 提供数据库数据源的配置管理能力，支持创建、分页查询、查看详情、修改与软删除。每个数据源 MUST 记录名称、数据库类型、主机、端口、库名、用户名、加密后的密码、描述与启用状态。

#### Scenario: 创建数据源

- **WHEN** 用户提交合法的数据源配置（名称、类型、主机、端口、库名、用户名、密码）
- **THEN** 系统创建该数据源并以加密形式存储密码
- **AND** 返回统一成功响应（code 2000），响应体中不含密码明文或密文

#### Scenario: 分页查询数据源列表

- **WHEN** 用户请求数据源列表
- **THEN** 系统仅返回 `is_deleted=False` 的数据源
- **AND** 返回结构由 `pagination.paginate` 生成，包含 `total` 字段

#### Scenario: 数据源名称重复

- **WHEN** 用户创建或修改数据源时使用了已存在（未删除）的名称
- **THEN** 系统拒绝操作并返回参数错误（code 4000），提示名称重复

#### Scenario: 软删除数据源

- **WHEN** 用户删除一个数据源
- **THEN** 系统将其 `is_deleted` 置为 True，不物理删除记录
- **AND** 该数据源不再出现在列表查询结果中
- **AND** 其历史元数据快照保留，可供审计查询

### Requirement: 数据源凭据保护

系统 MUST 以可逆对称加密方式存储数据源密码，加密密钥 MUST 从配置读取，禁止硬编码。任何 API 响应与日志输出 MUST NOT 包含密码明文。

#### Scenario: 密码不出现在响应中

- **WHEN** 用户查询数据源列表或详情
- **THEN** 响应中的密码字段为空或掩码值，不含明文与密文

#### Scenario: 密码不出现在日志中

- **WHEN** 数据源连接测试或元数据采集失败并记录日志
- **THEN** 日志内容不包含数据源密码

#### Scenario: 加密密钥缺失

- **WHEN** 配置中缺少数据源加密密钥
- **THEN** 涉及数据源加解密的操作返回服务端错误（code 5000）
- **AND** 系统 MUST NOT 回退为明文存储或明文读取

### Requirement: 数据源连接测试

系统 SHALL 提供连接测试能力，在不落库的前提下验证目标库可达性与凭据有效性，且 MUST 设置连接超时。

#### Scenario: 连接成功

- **WHEN** 用户对一份数据源配置发起连接测试，且目标库可达、凭据有效
- **THEN** 返回成功响应，并包含目标库版本等基础信息

#### Scenario: 连接失败

- **WHEN** 目标库不可达、端口错误或凭据无效
- **THEN** 返回失败响应并给出可读的失败原因
- **AND** 失败原因中不含密码

#### Scenario: 连接超时

- **WHEN** 目标库在超时时间内无响应
- **THEN** 测试在超时后终止并返回超时原因，不得无限阻塞请求

### Requirement: 数据源接口鉴权交由 BSA 平台

数据源接口 SHALL 继承 `baseviews.AnyLogin`，系统内部 MUST NOT 做认证与角色校验；访问控制由 BSA 底座平台通过菜单与路由权限实现。

#### Scenario: 系统内不因缺少 Token 而拒绝请求

- **WHEN** 请求到达数据源接口且未携带 `Token` 请求头
- **THEN** 系统不返回 4003，而是正常执行业务逻辑并返回统一响应结构

#### Scenario: 停用的数据源不可采集

- **WHEN** 对 `is_enabled = False` 的数据源触发采集
- **THEN** 系统返回业务失败提示（code 4017），不执行采集

#### Scenario: 非法请求仍被参数校验拦截

- **WHEN** 请求提交的数据源配置缺少必填字段或格式非法
- **THEN** 系统返回参数错误（code 4000），且数据不发生变更

### Requirement: 数据源管理界面

系统 SHALL 提供前端页面用于数据源配置管理，包含列表展示与新建、编辑、删除、连接测试、触发采集入口。

#### Scenario: 数据源页面可用

- **WHEN** 用户进入数据源管理页面
- **THEN** 页面展示数据源列表，并提供新建、编辑、删除、连接测试与触发采集的操作入口

#### Scenario: 连接测试结果可见

- **WHEN** 用户在页面上对某数据源点击连接测试
- **THEN** 页面展示测试成功或失败的结果提示及失败原因


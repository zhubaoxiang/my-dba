# auth Specification

## Purpose
TBD - created by archiving change add-login-auth. Update Purpose after archive.
## Requirements
### Requirement: 登录与 JWT 签发

系统 SHALL 提供用户名 + 密码登录接口。认证成功后 MUST 签发包含用户标识、用户名、角色与过期时间的 JWT，并返回给客户端；客户端后续通过 `Token` 请求头携带该 JWT。

#### Scenario: 本地账号登录成功

- **WHEN** 本地 Django 用户提交正确的用户名与密码
- **THEN** 系统返回 JWT 与用户信息，用户角色按 `is_staff` / `is_superuser` 映射为 `admin`，否则为 `user`

#### Scenario: 登录失败不泄露细节

- **WHEN** 用户名不存在或密码错误
- **THEN** 系统返回业务失败（code 4003），且不区分「用户不存在」与「密码错误」

#### Scenario: 登录参数为空

- **WHEN** 用户名或密码为空
- **THEN** 系统返回参数错误（code 4000），不发起认证

#### Scenario: JWT 包含过期时间

- **WHEN** 系统签发 JWT
- **THEN** token 中 MUST 包含 `exp`，有效期取配置项 `[auth] token_ttl_hours`（默认 8 小时）

### Requirement: 本地账号优先与 LDAP 兜底

系统 SHALL 先检查本地 `auth_user` 是否存在该用户。存在时 MUST 只校验本地密码；不存在时 SHALL 尝试 LDAP。本地存在同名用户但密码错误时 MUST NOT 再用 LDAP 密码尝试。

#### Scenario: 本地账号认证成功

- **WHEN** 本地 `auth_user` 存在该用户且密码正确
- **THEN** 系统签发 JWT，角色按 `is_staff` / `is_superuser` 映射为 `admin`，否则为 `user`

#### Scenario: 本地账号密码错误

- **WHEN** 本地存在该用户但密码错误
- **THEN** 系统返回 4003，MUST NOT 再尝试 LDAP

#### Scenario: 本地不存在该用户时走 LDAP

- **WHEN** 本地没有该用户且 LDAP 已配置
- **THEN** 系统使用 `username@domain` 做 UPN bind

#### Scenario: LDAP 认证成功

- **WHEN** 本地没有该用户，且 `username@domain` 与密码 bind 成功
- **THEN** 系统签发 JWT，角色为 `user`，不创建本地 Django 用户

#### Scenario: LDAP 密码错误

- **WHEN** 本地没有该用户，且 LDAP bind 因密码错误失败
- **THEN** 系统返回 4003

#### Scenario: LDAP 服务器不可达

- **WHEN** 本地没有该用户，且 LDAP 已配置但连接异常或超时
- **THEN** 系统返回 4017，说明 LDAP 不可达

#### Scenario: LDAP 未配置且本地无该用户

- **WHEN** 本地没有该用户且 `[auth] ldap_server` 为空
- **THEN** 系统返回 4003

#### Scenario: LDAP 配置不完整

- **WHEN** `ldap_server` 有值但 `ldap_domain` 为空
- **THEN** 系统返回业务失败（code 4017），说明 LDAP 配置不完整，不静默当作未配置

#### Scenario: 空密码不做匿名 bind

- **WHEN** 密码为空
- **THEN** 系统 MUST NOT 发起 LDAP bind，直接按认证失败处理

### Requirement: 认证失败与 token 过期

系统 SHALL 将缺失、无效或过期的 JWT 统一识别为未认证，并返回业务失败（code 4003），MUST NOT 返回 5000。

#### Scenario: 未携带 token

- **WHEN** 请求受保护接口且未携带 `Token` 请求头
- **THEN** 系统返回 4003，不执行业务逻辑

#### Scenario: token 过期

- **WHEN** 请求携带已过期的 JWT
- **THEN** 系统返回 4003，并提示登录已过期

#### Scenario: token 无效

- **WHEN** 请求携带无法解码或签名不匹配的 JWT
- **THEN** 系统返回 4003，并提示登录凭证无效

#### Scenario: JWT 密钥未配置

- **WHEN** `[auth] jwt_secret` 为空时尝试登录
- **THEN** 系统返回 4017，明确说明需要配置密钥，MUST NOT 用空密钥签发 token

### Requirement: 受保护接口

除登录接口外，所有业务接口 SHALL 要求登录。登录接口 MUST 公开；`me` 与 `logout` MUST 要求有效 JWT。

#### Scenario: 业务接口要求登录

- **WHEN** 未登录客户端访问数据源、库表、问题、知识库、SQL 分析、NL2SQL、DDL 评审或首页接口
- **THEN** 系统返回 4003，不返回业务数据

#### Scenario: 登录后可访问

- **WHEN** 客户端携带有效 JWT 访问业务接口
- **THEN** 系统正常返回统一响应结构

#### Scenario: 登录接口公开

- **WHEN** 未登录客户端调用 `POST /v1/auth/login`
- **THEN** 系统 MUST 正常处理登录请求，不要求 `Token`

#### Scenario: 当前用户接口

- **WHEN** 已登录客户端调用 `GET /v1/auth/me`
- **THEN** 系统返回当前用户名、角色与是否 staff

### Requirement: 登录界面与登录态保持

系统 SHALL 提供登录页面，并在未登录访问业务页面时跳转到登录页；登录成功后跳回原页面或首页。退出登录 MUST 清除客户端 token 并回到登录页。

#### Scenario: 未登录访问业务页

- **WHEN** 浏览器没有有效 token 时访问业务路由
- **THEN** 前端 MUST 跳转到 `/login`

#### Scenario: 登录成功跳转

- **WHEN** 登录成功
- **THEN** 前端保存 token 与用户信息，并跳转到原目标页或首页

#### Scenario: 刷新保持登录

- **WHEN** 已登录用户刷新页面
- **THEN** 前端从本地存储恢复 token，并通过 `me` 接口恢复用户信息

#### Scenario: 退出登录

- **WHEN** 用户点击退出登录
- **THEN** 前端清除 token 与用户信息，并跳转到登录页

#### Scenario: 认证失效回登录页

- **WHEN** 业务请求返回 code 4003
- **THEN** 前端清除登录态并跳转登录页；登录请求本身的 4003 MUST NOT 触发跳转，只展示错误


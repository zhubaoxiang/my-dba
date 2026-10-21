# Change: 增加登录认证（JWT + LDAP）

## Why

当前系统没有任何应用层认证：所有 ViewSet 继承 `AnyLogin` / `StatelessView`，`permission_classes` 为空，只要服务可达，任何人都能读写数据源凭据、模型 API Key、知识库文档，并借连接测试探测内网。README 与架构文档都要求「只允许内网部署」正是因为这个缺口。

现在需要在不依赖网络隔离的前提下，让使用者先登录再访问系统，并复用现有的域账号（LDAP）。

## What Changes

- 新增登录能力：
  - `POST /v1/auth/login`：用户名 + 密码，签发 JWT
  - `GET /v1/auth/me`：当前登录用户
  - `POST /v1/auth/logout`：客户端丢弃 token（JWT 无服务端会话）
- 认证策略：
  - **LDAP 优先**：配置了 LDAP 时，先用 `username@domain` 做 UPN bind
  - **本地兜底**：LDAP 未配置或服务器不可达时，走 Django `authenticate()`
  - **密码错误不回落**：LDAP 明确拒绝密码时直接失败，避免用本地密码绕过
  - LDAP 用户 `role=user`；本地 `is_staff` / `is_superuser` 用户 `role=admin`
  - v1 所有登录用户权限相同，不做细粒度 RBAC
- 所有现有业务接口改为要求登录：`AnyLogin` 与 `StatelessView` 的权限从「无」改为 `IsAuthenticated`。
- 前端新增登录页、路由守卫、用户信息与退出登录；请求拦截器在认证失效时清 token 并跳转登录页。
- 配置新增 `[auth]`：JWT 密钥、有效期、LDAP 地址/端口/域名/超时。
- 新增依赖 `ldap3`。
- 新增最小 `auth_user` 表 DDL（本地账号用）；不引入 Django admin / 权限表。

## Impact

- 受影响规格：
  - **新增** `auth`
  - **修改** `datasource-management`：删除「接口不做应用层鉴权」，改为要求登录
- 受影响代码：
  - 新增 `src/apps/accounts/`（LDAP、登录服务、JWT 签发、视图、测试）
  - `src/utils/authentication.py`（过期处理、角色）
  - `src/apps/base/baseviews.py`（`AnyLogin` / `StatelessView` 改为 `IsAuthenticated`）
  - `src/settings/settings.py`、`src/config/conf.ini`、`src/requirements.txt`
  - `src/sql/pg_struct.sql`、`src/sql/patch.sql`（`auth_user`）
  - 前端 `router` / `request` / `store/user` / `layout`，新增登录页
  - 现有 API 测试需要带上登录态
- 部署注意：生产环境必须设置 `[auth] jwt_secret`；未设置时登录接口会明确报配置缺失，不会用空密钥签发 token。

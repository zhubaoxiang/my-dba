## Context

系统现在完全没有应用层认证：`baseviews.AnyLogin` 与 `StatelessView` 都把 `permission_classes` 设为空，访问控制依赖网络隔离。`utils/authentication.py` 里已经有一个未启用的 `JwtAuthentication` 与前端 localStorage token 的雏形，但没有签发 token 的登录接口。

需求是：用 Django 的认证能力做本地账号登录，同时支持 LDAP 域账号；登录后所有业务接口要求认证。

## Goals / Non-Goals

- **Goals**
  - 用户名 + 密码登录，签发 JWT
  - LDAP 优先，本地 Django 账号兜底
  - 所有现有业务接口要求登录
  - 前端登录页、路由守卫、用户信息与退出
- **Non-Goals**
  - 不做细粒度 RBAC / LDAP 组映射（v1 所有登录用户同权限）
  - 不做服务端 token 吊销 / 刷新机制（token 到期重新登录）
  - 不做 Django admin、权限表、用户管理 CRUD
  - 不改数据源凭据、模型 API Key 的加密方式

## Decisions

### D1: 用 JWT，不用 Django session

- **Decision**：登录成功后签发 JWT，前端继续用 `Token` 请求头；`JwtAuthentication` 作为 DRF 默认认证。
- **Why**：前端已有 localStorage token 与 `Token` 头；JWT 无 CSRF 配置负担，也适合前后端分离。Django 只用于本地账号的 `authenticate()` 与密码校验。
- **Alternatives considered**：Django session（需要 CSRF、session 表、cookie 配置）。已在需求确认阶段否决。

### D2: LDAP 优先，本地兜底；密码错误不回落

- **Decision**：
  1. `[auth] ldap_server` 非空且 `ldap_domain` 非空 → LDAP 已配置。
  2. 先做 UPN bind：`username@domain`。
  3. bind 成功 → 登录成功（`role=user`）。
  4. bind 明确失败（`LDAPBindError`）→ 直接返回「用户名或密码错误」，**不尝试本地密码**。
  5. LDAP 未配置，或连接异常（`LDAPException` / 超时）→ 回落到 Django `authenticate()`。
- **Why**：LDAP 密码错误时不回落实现在于避免「本地同名账号密码绕过域密码」；LDAP 不可达时允许本地管理员应急登录。
- **配置不完整**：`ldap_server` 有值但 `ldap_domain` 为空 → 返回 4017「LDAP 配置不完整」，不静默当作未配置。

### D3: LDAP 用户不落 `auth_user`

- **Decision**：LDAP 登录成功后只签发 JWT，payload 为 `{id: username, name: username, role: "user"}`；不创建本地 Django 用户。
- **Why**：JWT 认证返回 `AuthedUser`，`IsAuthenticated` 只要求 `is_authenticated=True`，不需要数据库用户。避免为每个域账号维护一份本地记录，也避免域账号删除后的残留。
- **代价**：无法用 Django `User` 外键关联 LDAP 用户的业务数据；当前没有这类需求。

### D4: 所有登录用户同权限

- **Decision**：v1 不做 RBAC。本地 `is_staff` / `is_superuser` 映射为 `role=admin`，LDAP 用户为 `role=user`；现有接口只要求 `IsAuthenticated`，不按角色拦截。
- **Why**：当前系统没有角色/审批概念；先补登录，把权限模型留到有明确需求时再做。`role` 只用于前端展示与后续扩展。

### D5: 用基类统一改权限，登录接口单独放行

- **Decision**：
  - `baseviews.AnyLogin`：`permission_classes = (IsAuthenticated,)`，使用默认 `JwtAuthentication`。
  - `baseviews.StatelessView`：同样要求登录。
  - 新增 `apps/accounts/views.AuthView`，`login` action 用 `permission_classes=()` 放行；`me` / `logout` 要求登录。
- **Why**：现有 7 个 `AnyLogin` 与 4 个 `StatelessView` 无需逐个改；只有登录接口是公开的。`StatelessView` 的 docstring 与文档同步更新，避免继续声称「不做应用层鉴权」。
- **注意**：现有 API 测试都直接 `APIClient()`，改权限后会得到 4003；测试统一加 `force_authenticate` 或 JWT。

### D6: JWT 配置与过期

- **Decision**：`JWT_SECRET` 从 `conf.ini` 的 `[auth] jwt_secret` 读取；`token_ttl_hours` 默认 8。密钥为空时登录返回 4017，**不使用空密钥签发**。`JwtAuthentication` 对 `ExpiredSignatureError` / `InvalidTokenError` 返回 4003。
- **Why**：空密钥签发的 JWT 任何人都能伪造；宁可登录不可用，也不能静默降级。过期必须被识别为「登录已过期」，而不是 5000。
- **测试**：`settings/test.py` 设置测试专用密钥；生产由部署方配置。

### D7: 只补最小 `auth_user` DDL

- **Decision**：`pg_struct.sql` / `patch.sql` 只补 Django 3.2 的 `auth_user` 表；不补 `auth_permission` / `auth_group` / `content_type` / `django_session`。
- **Why**：本地认证只需要 `authenticate()`（查 `auth_user` + `check_password` + `is_active`），JWT 不需要 session 表；项目不使用 Django admin 与权限系统。
- **代价（明确接受）**：将来若启用 Django admin 或 `user.has_perm()`，需要补齐完整 auth 表。文档写明。

### D8: 前端登录与失效处理

- **Decision**：
  - 新增 `/login` 页面（Layout 外）。
  - 路由守卫：无 token → `/login`；已登录访问 `/login` → `/home`。
  - 请求拦截器：业务码 4003 时清 token 并跳登录页（登录请求本身除外，避免密码错误时循环跳转）。
  - 顶栏用户名来自登录响应/`/auth/me`；退出登录清 token 并回 `/login`。
- **Why**：与现有 `request.js` 的 `Token` 头、`userStore` 的 token 持久化保持一致；不需要改后端统一响应格式。

## 数据流

```
登录页 username/password
  → POST /v1/auth/login
      ├─ LDAP 已配置 → UPN bind
      │    ├─ 成功 → JWT(role=user)
      │    ├─ 密码错误 → 4003（不回落）
      │    └─ 不可达 → 本地 authenticate()
      └─ LDAP 未配置 → 本地 authenticate()
  → {token, user}
  → 前端存 token / user
  → 后续请求带 Token 头 → JwtAuthentication → AuthedUser → IsAuthenticated
```

## API

| 方法 | 路径 | 认证 | 说明 |
|------|------|------|------|
| POST | `/my-dba/v1/auth/login` | 否 | 登录，返回 token 与 user |
| GET | `/my-dba/v1/auth/me` | 是 | 当前用户 |
| POST | `/my-dba/v1/auth/logout` | 是 | 无服务端状态，客户端丢弃 token |

登录失败口径：

| 情形 | code |
|------|------|
| 用户名/密码为空 | 4000 |
| 用户名或密码错误 | 4003 |
| LDAP 已配置但配置不完整 / 不可达且本地也失败 | 4017 |
| JWT 密钥未配置 | 4017 |
| token 过期/无效 | 4003 |

## Risks / Trade-offs

| 风险 | 影响 | 缓解 |
|------|------|------|
| **JWT 密钥为空或泄露** | token 可被伪造 | 空密钥拒绝签发；密钥放 `conf.ini`，部署时替换随机值；文档强调 |
| **本地兜底被用来绕过 LDAP** | 域密码策略失效 | LDAP 密码错误不回落；只有 LDAP 不可达/未配置才走本地 |
| **所有用户同权限** | 任一登录用户可改数据源/规则 | v1 明确接受；后续按角色收口，接口已统一走 `IsAuthenticated` |
| **现有测试全部无 token** | 改权限后大量测试 4003 | 测试统一加 `force_authenticate`；另测真实 JWT 登录 |
| **auth_user DDL 不完整** | 启用 admin/权限系统时缺表 | 文档写明；当前只支持登录，不启用 admin |
| **token 无法服务端吊销** | 退出后旧 token 在有效期内仍可用 | 8 小时有效期；如需要，后续加黑名单/短 token+刷新 |

## Migration Plan

1. 依赖：`requirements.txt` 加 `ldap3`，部署时安装。
2. 配置：`conf.ini` 增加 `[auth]`；生产必须设置 `jwt_secret`；LDAP 可选。
3. DDL：新库执行 `pg_struct.sql`；已有库执行 `patch.sql`（补 `auth_user`）。
4. 本地管理员：`python manage.py createsuperuser`（或将已有 auth_user 行的 `is_staff`/`is_superuser` 置 true）。
5. 前端重新构建。
6. 回滚：恢复 `permission_classes` 与前端路由；JWT 不涉及数据迁移。

## Open Questions

无。v1 的范围与边界已在上文确定。

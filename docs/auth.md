# 登录与认证（JWT + LDAP）

系统使用 **JWT** 做登录态：登录成功后签发 token，前端放在 `Token` 请求头里；所有业务接口要求有效 token，只有登录接口公开。

域账号通过 LDAP 的 UPN（`username@domain`）认证；LDAP 未配置或服务器不可达时，回落到本地 Django 账号。

对应模块：`src/apps/accounts/`。

## 登录流程

```
登录页 username / password
  → POST /my-dba/v1/auth/login
      ├─ LDAP 已配置 → username@domain bind
      │    ├─ 成功 → 签发 JWT（role=user）
      │    ├─ 密码错误 → 4003（不回落本地）
      │    └─ 不可达 → 回落本地 Django authenticate()
      └─ LDAP 未配置 → 本地 Django authenticate()
  → {token, user}
  → 前端存 token / userInfo
  → 后续请求带 Token → JwtAuthentication → AuthedUser → IsAuthenticated
```

## LDAP 策略

| 情形 | 行为 |
|------|------|
| `ldap_server` 为空 | 未启用 LDAP，只走本地 Django 账号 |
| `ldap_server` 有值、`ldap_domain` 为空 | 返回 4017，明确报配置不完整，不静默当作未配置 |
| LDAP bind 成功 | 登录成功，角色 `user`；不创建本地 `auth_user` 记录 |
| LDAP 明确拒绝密码 | 返回 4003，**不再尝试本地密码**，避免本地同名账号绕过域密码 |
| LDAP 连接异常 / 超时 | 记告警并回落本地 Django 认证 |
| 空密码 | 不发起 LDAP bind，直接失败，避免匿名 bind |

LDAP 只做 UPN bind，不读取组属性；v1 不做 LDAP 组到角色的映射。

## 角色与权限

- LDAP 用户：`role=user`
- 本地 `is_staff` / `is_superuser` 用户：`role=admin`
- **v1 所有登录用户权限相同**：现有接口只要求 `IsAuthenticated`，不按角色拦截。`role` 目前只用于展示与后续扩展。

## JWT

- 请求头：`Token: <jwt>`（不是 `Authorization: Bearer`）
- payload：`id` / `name` / `role` / `exp`
- 有效期：`[auth] token_ttl_hours`（默认 8 小时）
- 过期或无效：统一返回业务码 4003，前端清 token 并回登录页
- **无服务端会话 / 吊销**：退出登录只清客户端 token；有效期内旧 token 仍可用。需要更强控制时再引入短 token + 刷新或黑名单。

## 配置

`src/config/conf.ini` 的 `[auth]` 段：

| 配置项 | 说明 |
|--------|------|
| `jwt_secret` | **必填**，随机值；为空时登录返回 4017，不会用空密钥签发 |
| `token_ttl_hours` | token 有效期（小时），默认 8 |
| `ldap_server` | LDAP 地址；为空表示不启用 LDAP |
| `ldap_port` | 默认 389 |
| `ldap_domain` | UPN 后缀，例如 `corp.example.com`；启用 LDAP 时必填 |
| `ldap_connect_timeout` | 连接超时（秒），默认 10 |

改动 `conf.ini` 必须重启进程（`CONF_ATTR` 在模块导入时载入）。

## 本地账号

本地密码只用于兜底。首次部署可以用 Django 自带命令创建管理员：

```bash
cd src
python manage.py createsuperuser
```

`auth_user` 表已经写进 `sql/pg_struct.sql` 与 `sql/patch.sql`。这是**最小表**：系统不使用 Django admin / 权限系统，因此没有 `auth_group` / `auth_permission` / `content_type` / `django_session`。如果将来启用 Django admin 或 `has_perm()`，需要补齐完整 auth 表。

LDAP 用户不落 `auth_user`，因此本地表里不会出现域账号。

## API

| 方法 | 路径 | 认证 | 说明 |
|------|------|------|------|
| POST | `/my-dba/v1/auth/login` | 否 | 登录，返回 token 与用户信息 |
| GET | `/my-dba/v1/auth/me` | 是 | 当前用户 |
| POST | `/my-dba/v1/auth/logout` | 是 | 无服务端状态，客户端丢弃 token |

失败口径：

| 情形 | code |
|------|------|
| 用户名 / 密码为空 | 4000 |
| 用户名或密码错误 | 4003 |
| LDAP 配置不完整 / 不可达且本地也失败 | 4017 |
| JWT 密钥未配置 | 4017 |
| token 过期 / 无效 | 4003 |

## 前端

- `/login` 页面（Layout 外）：用户名 + 密码
- 路由守卫：无 token → `/login`；已登录访问 `/login` → `/home`
- `request.js`：自动带 `Token`；业务码 4003 清 token 并跳登录页（登录请求本身不跳，避免密码错误循环）
- 顶栏显示用户名；刷新后拉一次 `/auth/me` 恢复用户信息；退出登录清 token 回登录页

## 已知限制

- **v1 不做 RBAC**：任一登录用户都能改数据源、规则、知识库等；需要按角色收口时另行设计。
- **token 不能服务端吊销**：退出后旧 token 在有效期内仍可用。
- **LDAP 只做 UPN bind**：不读组、不做管理员组映射；域账号删除后，已签发 token 仍可用至过期。
- **本地兜底只在 LDAP 不可达/未配置时生效**：LDAP 密码错误不会回落到本地。
- **`auth_user` 是最小表**：不启用 Django admin / 权限系统。

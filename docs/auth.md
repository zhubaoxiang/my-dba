# 登录与认证（JWT + LDAP）

系统使用 **JWT** 做登录态：登录成功后签发 token，前端放在 `Token` 请求头里；所有业务接口要求有效 token，只有登录接口公开。

**本地 Django 账号优先**；只有本地不存在该用户时，才走 LDAP 的 UPN（`username@domain`）认证。

对应模块：`src/apps/accounts/`。

## 登录流程

```
登录页 username / password
  → POST /my-dba/v1/auth/login
      ├─ 本地存在该用户 → Django authenticate()
      │    ├─ 成功 → 签发 JWT（role=admin/user）
      │    └─ 失败 → 4003（不再尝试 LDAP）
      └─ 本地不存在该用户
           ├─ LDAP 已配置 → username@domain bind
           │    ├─ 成功 → 签发 JWT（role=user）
           │    ├─ 密码错误 → 4003
           │    └─ 不可达 → 4017
           └─ LDAP 未配置 → 4003
  → {token, user}
  → 前端存 token / userInfo
  → 后续请求带 Token → JwtAuthentication → AuthedUser → IsAuthenticated
```

## LDAP 策略

| 情形 | 行为 |
|------|------|
| **本地存在该用户** | 只校验本地 Django 密码；成功登录，失败直接 4003，**不再尝试 LDAP** |
| **本地不存在该用户** | 尝试 LDAP（若已配置） |
| LDAP bind 成功 | 登录成功，角色 `user`；不创建本地 `auth_user` 记录 |
| LDAP 明确拒绝密码 | 返回 4003 |
| LDAP 未配置 | 返回 4003（本地也没有该用户） |
| LDAP 连接异常 / 超时 | 返回 4017，说明 LDAP 不可达 |
| `ldap_server` 有值、`ldap_domain` 为空 | 返回 4017，明确报配置不完整，不静默当作未配置 |
| 空密码 | 不发起 LDAP bind，直接失败，避免匿名 bind |

**本地账号优先是刻意的**：本地和 LDAP 同名时，本地密码优先；本地密码错误不会再用 LDAP 密码尝试。这样两套账号体系不会在同一个用户名上产生歧义，代价是本地账号忘记密码时需要先由管理员重置，不能靠 LDAP 密码绕过。

LDAP 只做 UPN bind，不读取组属性；v1 不做 LDAP 组到角色的映射。

## 角色与权限

- LDAP 用户：`role=user`
- 本地 `is_staff` / `is_superuser` 用户：`role=admin`
- **超级管理员**：可以使用全部功能，并且是唯一能进入「用户管理」的角色
- **普通用户**：可以使用全部业务功能（数据源、库表分析、SQL 分析/生成、DDL 评审、知识问答等），但看不到也不能调用「用户管理」与「系统管理」

用户管理接口挂在 `SuperUserView`（`IsAdminUser`）上；普通用户访问返回业务码 **4005**。4003 表示未登录/登录失效，4005 表示已登录但无权限——前端不会因为 4005 清理登录态。

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
| `ldap_use_ssl` | LDAPS（636 端口）设为 `true`；普通 389 设为 `false`（默认） |

登录框支持三种账号写法：短账号（`alice`）、完整 UPN（`alice@corp.example.com`）、域前缀（`CORP\alice`）。短账号会自动拼成 `alice@<ldap_domain>`；完整 UPN 不会重复拼后缀。

改动 `conf.ini` 必须重启进程（`CONF_ATTR` 在模块导入时载入）。

## 本地账号

本地密码只用于兜底。首次部署可以用 Django 自带命令创建管理员：

```bash
cd src
python manage.py createsuperuser
```

`auth_user` 表已经写进 `sql/pg_struct.sql` 与 `sql/patch.sql`。这是**最小表**：系统不使用 Django admin / 权限系统，因此没有 `auth_group` / `auth_permission` / `content_type` / `django_session`。如果将来启用 Django admin 或 `has_perm()`，需要补齐完整 auth 表。

LDAP 用户不落 `auth_user`，因此本地表里不会出现域账号。

### 用户管理

超级管理员可以在「用户管理」页面维护本地账号：

- 列表、创建、编辑、删除
- 角色：超级管理员 / 普通用户
- 可重置密码、禁用账号
- 用户名创建后不可修改（会话与各类记录的 `creator` 引用用户名）
- 删除为物理删除；不能删除自己，也不能删除/禁用/降级最后一个可用的超级管理员
- 管理员不能修改自己的角色和启用状态，避免把自己锁在外面

删除用户使用参数化 `DELETE FROM auth_user`：本库没有 `django_admin_log` 等 Django 关联表，不能走 ORM 的级联删除。

## API

| 方法 | 路径 | 认证 | 说明 |
|------|------|------|------|
| POST | `/my-dba/v1/auth/login` | 否 | 登录，返回 token 与用户信息 |
| GET | `/my-dba/v1/auth/me` | 是 | 当前用户 |
| POST | `/my-dba/v1/auth/logout` | 是 | 无服务端状态，客户端丢弃 token |
| GET | `/my-dba/v1/user` | 管理员 | 本地用户列表（分页，可按用户名/邮箱搜索） |
| GET | `/my-dba/v1/user/{id}` | 管理员 | 本地用户详情 |
| POST | `/my-dba/v1/user` | 管理员 | 创建本地用户 |
| PUT | `/my-dba/v1/user/{id}` | 管理员 | 修改邮箱、角色、启用状态、密码（密码留空不修改） |
| DELETE | `/my-dba/v1/user/{id}` | 管理员 | 物理删除本地用户 |

失败口径：

| 情形 | code |
|------|------|
| 用户名 / 密码为空 | 4000 |
| 用户名或密码错误 | 4003 |
| LDAP 配置不完整 / 不可达且本地也失败 | 4017 |
| JWT 密钥未配置 | 4017 |
| token 过期 / 无效 | 4003 |
| 已登录但无权限（如普通用户访问用户管理） | 4005 |

## 前端

- `/login` 页面（Layout 外）：用户名 + 密码
- 路由守卫：无 token → `/login`；已登录访问 `/login` → `/home`
- `/user`、`/system` 标记 `adminOnly`：普通用户菜单不显示，直接访问会被重定向回首页
- `request.js`：自动带 `Token`；业务码 4003 清 token 并跳登录页（登录请求本身不跳，避免密码错误循环）；4005 只提示，不清登录态
- 顶栏显示用户名；刷新后拉一次 `/auth/me` 恢复用户信息；退出登录清 token 回登录页

## 已知限制

- **只区分两种角色**：超级管理员与普通用户；普通用户仍可使用全部业务功能，没有更细的写权限划分。
- **token 不能服务端吊销**：退出、删除用户或降级管理员后，旧 token 在有效期内仍可用（默认 8 小时）。需要立即失效时要引入 token 版本/黑名单。
- **LDAP 只做 UPN bind**：不读组、不做管理员组映射；LDAP 用户不在本地用户列表中，也无需本地密码。
- **本地账号优先**：本地存在同名用户时不会再用 LDAP 密码尝试；本地账号密码错误需由管理员重置。
- **`auth_user` 是最小表**：不启用 Django admin / 权限系统；删除用户不能走 ORM 级联，使用参数化 SQL。

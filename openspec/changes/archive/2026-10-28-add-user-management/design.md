## Context

- 本地账号存放在最小化的 `auth_user` 表，登录走 Django `authenticate()`/`check_password`
- JWT 里已经携带 `role`（`admin` / `user`），`AuthedUser.is_staff` 由 role 推导
- 现有 `SuperUserView`（`IsAdminUser`）可以直接用于管理员接口，但此前没有使用
- 数据库缺少 `django_admin_log`、`auth_user_groups` 等 Django 关联表，不能依赖 `User.delete()` 的级联收集
- 前端已有基于 `meta.showInMenu` 的菜单生成，但还没有按角色过滤

## Goals / Non-Goals

- Goals:
  - 超级管理员可以完整管理本地用户
  - 普通用户不能进入用户管理，也不能调用用户管理接口
  - 不引入新的用户表或权限框架
- Non-Goals:
  - 不管理 LDAP 用户（他们不在本地 `auth_user`）
  - 不做细粒度的按钮/字段级权限
  - 不做用户自助注册、找回密码、个人中心

## Decisions

### D1：复用 `auth_user` 与 `is_superuser` / `is_staff`

角色映射：

| 角色 | is_superuser | is_staff |
|------|--------------|----------|
| 超级管理员 | true | true |
| 普通用户 | false | false |

登录已经按 `is_staff or is_superuser` 映射为 `admin`，因此不需要改认证链路，也不需要新增角色列。

### D2：用户管理接口使用 `SuperUserView`

`/v1/user` 提供列表、详情、创建、修改、删除。`SuperUserView` 的 `IsAdminUser` 检查 `request.user.is_staff`，而 `AuthedUser.is_staff` 由 JWT 的 role 推导，正好对应超级管理员。

### D3：新增 4005 区分「无权限」与「未登录」

目前 `PermissionDenied` 和认证失败都返回 4003，前端拦截器把 4003 当成登录失效并登出。管理员接口需要区分：

- 4003：未登录、token 无效/过期
- 4005：已登录，但没有该操作权限

前端对 4005 只提示，不清理登录态。

### D4：用户名创建后不可修改

会话的 `creator`、各类记录的 `creator` 都是用户名字符串。允许改名会让历史归属和会话隔离失效。用户管理只允许修改邮箱、角色、启用状态和密码。

### D5：物理删除使用参数化 SQL

`auth_user` 没有 `django_admin_log` 等 Django 关联表，`User.delete()` / `QuerySet.delete()` 的级联收集会访问不存在的表。删除使用参数化语句：

```sql
DELETE FROM auth_user WHERE id = %s
```

删除前做两项保护：

1. 不能删除自己
2. 不能删除最后一个可用的超级管理员

### D6：管理员不能修改自己的角色和启用状态

避免把自己降级或禁用后锁死。管理员可以修改自己的邮箱和密码。

### D7：前端 `meta.adminOnly`

路由 `/user`、`/system` 标记 `adminOnly`：

- 菜单生成时按当前用户角色过滤
- 路由守卫拦截非管理员，跳回首页
- 后端接口仍是最终防线

### D8：登录成功更新 `last_login`

本地账号认证成功后写入 `last_login`，用户管理列表才能展示「最后登录」。LDAP 用户没有本地行，不记录。

## Risks / Trade-offs

- JWT 无服务端会话：管理员被降级后，旧 token 在过期前仍是管理员。当前 token TTL 8 小时；本次不引入 token 版本/黑名单，避免超出范围。
- 物理删除不可恢复；通过「禁止删自己 / 禁止删最后一个管理员」降低锁死风险，删除前前端二次确认。
- 被删除用户的历史数据仍保留 `creator` 字符串；这是审计所需，不随账号删除而消失。

## Migration Plan

- 无数据库变更，复用 `auth_user`
- 现有 `admin` 用户（`is_superuser=True`、`is_staff=True`）自动成为超级管理员
- 回滚只需回退代码；已创建/删除的账号数据需人工处理

## Open Questions

无。

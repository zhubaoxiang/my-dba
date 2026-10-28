# Change: 增加本地用户管理与两级权限

## Why

用户管理菜单目前是占位页，本地 `auth_user` 账号只能通过 `createsuperuser` 维护。同时系统只有「登录/未登录」的区分，没有管理员与普通用户的权限边界。

## What Changes

- 增加本地用户管理：列表、详情、创建、修改、删除、启用/禁用
- 支持两种角色：超级管理员、普通用户
- 用户管理接口仅超级管理员可用；普通用户仍可使用全部业务功能
- 「用户管理」「系统管理」菜单仅超级管理员可见，路由对普通用户拦截
- 新增业务码 4005 表示「已登录但无权限」，与 4003「未登录/登录失效」区分

## Impact

- Affected specs: `user-management`（新增）
- Affected code:
  - `src/apps/accounts/`（用户管理接口）
  - `src/utils/exception.py`、`src/apps/base/baseviews.py`（4005）
  - `static/src/views/user/`、`static/src/api/user.js`
  - `static/src/router/index.js`、`static/src/layout/index.vue`

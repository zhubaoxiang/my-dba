## 1. 依赖、配置与 DDL

- [x] 1.1 `src/requirements.txt` 增加 `ldap3==2.9.1`，并在 venv 中安装
- [x] 1.2 `src/config/conf.ini` 增加 `[auth]`：`jwt_secret`（留空）、`token_ttl_hours=8`、`ldap_server`、`ldap_port=389`、`ldap_domain`、`ldap_connect_timeout=10`
- [x] 1.3 `settings/settings.py` 的 `JWT_SECRET` 改为从 `CONF_ATTR` 读取
- [x] 1.4 `settings/test.py` 设置测试专用 `JWT_SECRET`
- [x] 1.5 `sql/pg_struct.sql` 补最小 `auth_user` 表（Django 3.2 字段）
- [x] 1.6 `sql/patch.sql` 追加幂等的 `auth_user` 补丁
- [x] 1.7 单测：配置缺失时登录返回 4017；测试密钥可签发/解码

## 2. 后端登录与 LDAP

- [x] 2.1 新增 `src/apps/accounts/` 模块（`__init__.py` / `services.py` / `serializers.py` / `views.py` / `tests.py`）
- [x] 2.2 `services.ldap_config()`：读取并校验 server / domain；server 为空返回 None，server 有值但 domain 为空报配置不完整
- [x] 2.3 `services.ldap_authenticate()`：`ldap3` UPN bind；空密码直接失败；`LDAPBindError` → False；`LDAPException` → None
- [x] 2.4 `services.login()`：LDAP 优先；密码错误不回落；未配置/不可达回落 Django `authenticate`
- [x] 2.5 本地用户映射 `role=admin`（is_staff/superuser），LDAP 用户 `role=user`
- [x] 2.6 JWT 签发包含 `id` / `name` / `role` / `exp`；`token_ttl_hours` 可配
- [x] 2.7 `AuthView`：login / me / logout；login 公开，me/logout 要求登录
- [x] 2.8 `config/urls.py` 注册 `/v1/auth`
- [x] 2.9 单测：本地成功/失败、LDAP 成功、LDAP 密码错误不回落、LDAP 不可达回落、空密码、配置不完整
- [x] 2.10 API 单测：login 返回 token；me 无 token 4003；me 带 token 2000；错误密码 4003

## 3. 全局认证与现有测试

- [x] 3.1 `baseviews.AnyLogin` 改为 `permission_classes=(IsAuthenticated,)`，使用默认 `JwtAuthentication`
- [x] 3.2 `baseviews.StatelessView` 同样要求登录；更新 docstring
- [x] 3.3 `JwtAuthentication` 处理 `ExpiredSignatureError` / `InvalidTokenError`，返回 4003 而不是 5000
- [x] 3.4 现有 API 测试统一加登录态（`force_authenticate` 或测试 JWT）
- [x] 3.5 新增「未登录访问受保护接口返回 4003、带 token 返回 2000」的回归测试
- [x] 3.6 确认 `SuperUserView` 的 `is_staff` 仍由 `AuthedUser.role` 正确映射

## 4. 前端

- [x] 4.1 `static/src/api/auth.js`：login / me / logout
- [x] 4.2 `static/src/store/user.js`：token + userInfo；login / fetchMe / logout
- [x] 4.3 `static/src/views/login/index.vue`：登录页（Layout 外）
- [x] 4.4 `router/index.js`：`/login` 路由 + `beforeEach` 守卫
- [x] 4.5 `api/request.js`：业务码 4003 时清 token 并跳登录页；登录请求本身不跳
- [x] 4.6 `layout/index.vue`：显示真实用户名；退出登录清 token 并回 `/login`；刷新后拉取 `/auth/me`
- [x] 4.7 `npm run build` 通过

## 5. 文档

- [x] 5.1 新增 `docs/auth.md`：登录流程、LDAP 策略、JWT、配置、API、限制
- [x] 5.2 更新 `docs/architecture.md` 的「认证与鉴权」一节
- [x] 5.3 更新 `README.md` 的部署说明（必须配置 `jwt_secret`；LDAP 可选）
- [x] 5.4 delta spec 已写：将「接口不做应用层鉴权」改为「接口要求登录」（归档时应用到基线）

## 6. 校验

- [x] 6.1 `python .ci/custom-checks/scaffold_check.py`
- [x] 6.2 `ruff check` / `ruff format --check`
- [x] 6.3 `ENV_TYPE=test python manage.py test apps.accounts apps.datasource apps.sqlanalysis apps.nl2sql apps.ddlreview`
      —— **483 个测试通过**（含数据库 `TestCase`）
- [x] 6.4 真实页面：登录失败提示、登录成功跳首页、刷新保持登录、退出回登录页、未登录访问业务页被拦截
      —— 已用 headless Chrome 在 local 环境验证：登录成功跳 `/home`、顶栏显示用户名、刷新保持登录、
      退出回 `/login` 且 token 清空、错误密码留在登录页并提示、未登录访问业务页被守卫拦截；
      无 token 调 `/overview` 返回 4003「未登录或登录已失效」
- [x] 6.5 `openspec validate add-login-auth --strict --no-interactive`

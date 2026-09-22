# Project Context

## Purpose

我的数据库管理专家：面向**开发与测试人员**的数据库助手。使用者不是 DBA——它不做备份、容量规划、主从运维，而是帮写代码和测功能的人看懂库表、写出正确的 SQL、随时问数据库相关问题。

能力围绕三条使用者诉求展开：**分析数据库表**（库表结构、索引与字段隐患）、**知识问答**（RAG）、**分析 SQL**（规范与性能）。后续功能演进一律往这个方向走，不要引入偏 DBA 运维的能力。

产品路线为 4 个能力，各自独立成一个 openspec change：

| # | 能力 | 状态 |
|---|------|------|
| 1 | RAG 知识问答（无配置时回退通用大模型） | 未开始（规划 `add-rag-knowledge-qa`） |
| 2 | 自然语言转建表语句 / SQL | 未开始（规划 `add-nl2sql`） |
| 3 | SQL 规范与性能分析 | 未开始（规划 `add-sql-analysis`） |
| 4 | 数据源对接与库表分析 | 已提案（`add-datasource-catalog`） |

已确定的外部技术选型：数据源支持 PostgreSQL + MySQL；RAG 向量存储用 pgvector（复用现有 PG）；LLM 走公有云 API。

## Tech Stack

**后端**（`src/`）

- Python 3.11.9（由 uv 管理，与 Dockerfile 基础镜像一致）
- Django 3.2.25 + Django REST Framework 3.15.1 + django-filter 21.1
- PostgreSQL（psycopg2-binary 2.9.9）、Redis 3.5.3
- JWT 认证（PyJWT 1.7.1）、gunicorn 20.0.4
- 异步任务：simple-background-task（注意：README 提到的 django-q **并未安装**）

**前端**（`static/`）

- Vue 3.5 + Vite 5 + Element Plus 2.8 + Pinia 2.2 + Vue Router 4.4

**工程化**

- uv 管理 Python 与虚拟环境；npm 管理前端依赖
- ruff 做 lint 与 format；pre-commit 做提交门禁；自定义脚手架合规检查

## Project Conventions

### Code Style

- PEP 8，由 `ruff check` / `ruff format` 强制，配置在 `.ci/lint-rules/ruff.toml`
- 注释精简（不超过 3 行），不写描述 WHAT 的注释，只在 WHY 非显而易见时写
- 不写向后兼容 hack（`unused _vars`、`// removed` 之类），确信无用则直接删除
- YAGNI：不做需求之外的抽象与重构，三行相似代码胜过过早抽象
- 异常处理只在系统边界（用户输入、外部 API），内部代码信任框架保证
- Import 顺序：标准库 → 第三方库 → 本项目模块

详见 [code-style.md](../.ai-harness/rules/code-style.md)。

### Architecture Patterns

- 业务模块放 `src/apps/{module}/`，包含 `__init__.py`、`models.py`、`serializers.py`、`filters.py`（无过滤需求可留空）、`views.py`
- 通用能力放 `src/utils/`，扁平结构不建子目录；禁止业务模块自建工具类
- **所有业务模块挂在单一 `apps` Django app 下**：`INSTALLED_APPS` 中只有 `"apps"` 一项。新增模块**不需要**修改 `INSTALLED_APPS`，**不要**为模块新建 AppConfig。模型会自动获得 `app_label = "apps"`
- **模块的模型靠导入副作用注册**：模块的 `models.py` 只有在被导入时才注册到 Django app registry，而导入发生在 `config/urls.py` 引用该模块 `views` 时。因此新模块必须先在 `config/urls.py` 中 import，其模型才可见；`apps.get_app_configs()` 在启动时对 `apps` 报 0 个模型属正常现象
- 禁止跨模块直接 import queryset；跨模块数据交互必须通过 API / Serializer
- 配置读取统一走 `utils/configure.py` 的 `Configure` 单例，禁止直接使用 `os.environ`（环境变量 `ENV_TYPE` 除外）
- 认证统一走 `utils/authentication.py` 的 `JwtAuthentication`，禁止自建认证
- 配置层级：`settings/settings.py`（基础）→ `settings/{local|dev|prod|test}.py`（环境覆盖 DEBUG 与渲染器）→ `config/conf.ini`（外部配置，由 `ENV_TYPE` 决定 section）
- BSA 底座对接：`utils/bsa.py` 的 `BsaClient` 单例；生命周期入口 `hooks/install.py` / `hooks/uninstall.py`

详见 [architecture.md](../.ai-harness/rules/architecture.md) 与 [scaffold.md](../.ai-harness/rules/scaffold.md)。

### Testing Strategy

- 单元测试：`python manage.py test`（Django 测试运行器）
- 脚手架合规：`python .ci/custom-checks/scaffold_check.py`
- 代码规范：`ruff check src/ --config .ci/lint-rules/ruff.toml`、`ruff format --check src/ --config .ci/lint-rules/ruff.toml`
- 上述三项由 `.ci/pre-commit-config.yaml` 的 pre-commit 钩子统一触发，提交前必须全部通过
- 新增业务模块需自带 `tests.py`，覆盖核心分支

### Git Workflow

- 主干开发，默认分支 `main`（同时是 PR 的目标分支）
- 提交信息：Conventional Commits 风格前缀 + 中文描述，如 `feat:`、`fix:`、`docs:`、`build:`、`refactor:`、`init:`
- 示例：`feat: api-test 完成 CLI 接线与报告`、`build: Dockerfile 分层 + 阿里云归档源修复`
- 生产打包在打包机上执行 `python3 package.py`（先 `--clean`，再 `git pull`，再打包）

## Domain Context

- **BSA 底座平台**：本系统运行于 BSA 平台之上。平台侧概念包括服务包、菜单权限、组件信息
- **服务包**：`service.json` 定义 BSA Chart 包 —— 服务元信息、镜像、资源限制、探针、生命周期钩子、端口与路径映射。版本发布时修改
- **菜单注册**：`src/right_config.json` 声明本系统在 BSA 平台中的菜单树；`hooks/install.py` 调用 `scripts/bsa_register_menu.py`（经 Kong 网关）完成注册，`hooks/uninstall.py` 注销
- **URL 前缀**：`SYS_NAME`（定义在 `src/config/urls.py`）是系统在网关上的路径前缀，当前为 `my-dba`。约定 ViewSet 路由为 `{SYS_NAME}/v1/{module}`，函数视图为 `{SYS_NAME}/api/{name}`
- **响应封装**：所有接口返回 `{code, message, data}`。2000 成功、4000 参数错误、4003 无权限、4004 不存在、4017 业务条件未满足、5000 服务端错误
- **认证载体**：JWT Token 通过自定义请求头 `Token` 传递，**不是**标准的 `Authorization: Bearer`

## Important Constraints

以下为不可协商的脚手架硬约束，违反任一条即不符合规范，必须在继续之前修复：

- **禁止 Django migration**：DDL 手写进 `src/sql/pg_struct.sql`，初始数据 `pg_data.sql`，增量变更 `patch.sql`
- **模型必须继承** `apps.base.models.AbstractTimeFiledModel`（提供 `create_time`、`update_time`、`creator`、`is_deleted`），禁止直接继承 `models.Model`
- **软删除**：删除一律置 `is_deleted=True`，查询一律过滤 `is_deleted=False`，禁止物理删除
- **ViewSet 必须继承** `apps.base.baseviews` 的基类（`AnyLogin` / `BaseView` / `OperatorView` / `SuperUserView`），禁止直接用 `ModelViewSet`
- **响应必须用** `baseviews.ResponseOK` / `ResponseError` / `ResponseBadRequest` / `ResponseForbidden` / `ResponseNotFound` / `ResponseExpectationFailed`，禁止裸 `Response()`
- **列表接口必须分页**：`pagination.paginate(self, queryset)`
- **枚举使用** `utils/custom_enum.py` 的 `IntegerChoices`，禁止裸整数
- **`db_table` 显式指定**，禁止依赖自动生成
- **Serializer 分工**：查询用 `ModelSerializer`，创建/更新用普通 `Serializer` + `validate()`
- **路由注册**在 `src/config/urls.py` 通过 `router.register()`

速查表见 [CLAUDE.md](../CLAUDE.md)，完整规则见 [.ai-harness/rules/](../.ai-harness/rules/)。

## External Dependencies

| 依赖 | 用途 | 配置位置 |
|------|------|---------|
| PostgreSQL | 业务数据库 | `src/config/conf.ini` 的 `[db_{ENV_TYPE}]` 段 |
| Redis | 缓存 | 依赖已声明；`settings.py` 当前用 LocMemCache |
| BSA 底座平台 | 菜单注册、服务组件信息查询 | `conf.ini` 中的 BSA 地址与认证信息，经 `utils/bsa.py` 访问 |
| Kong 网关 | 菜单注册的调用链路 | `scripts/bsa_register_menu.py` |

## Known Issues

已知问题，供后续处理，不属于当前任何 change 的范围：

- `src/config/conf.ini` **已被 git 跟踪且含真实数据库地址与口令**，违反 [security.md](../.ai-harness/rules/security.md) 的凭据管理要求。应改为提交 `conf.ini.example`、将 `conf.ini` 加入 `.gitignore`，并轮换已泄露的口令
- `settings/settings.py` 中 `SECRET_KEY` 硬编码（security.md 已标注为已知问题），`JWT_SECRET` 为空字符串
- `src/apps/test/` 没有 `tests.py`。CLAUDE.md 中的 `GoodsTestCase` 与 README 中的 `apps/test/tests.py` 均不存在，属过期文档
- `src/apps/test/models.py` 的 `MavenEnforcerRules` 未显式声明主键类型，触发 `models.W042` 警告
- `manage.py check` 的输出**不稳定**：由于模型靠导入副作用注册（见上文），Django 的 `apps.get_models()` 结果可能缓存于模型被导入之前，导致同样的代码有时报 `no issues`、有时报 1 条 `models.W042`。判定 CI 是否通过时不要依赖该命令的输出条数
- `package.py:24` 的注释中含一段 GitLab OAuth2 令牌明文（`oauth2:zRddW7Z9hhGfEkSkqrhG@...`），已进入 git 历史。虽为注释，仍应视为凭据泄露并轮换
- `docker-compose.yml` 仅用于本地容器编排，其镜像名需与 `service.json` 的 `imageProjectName`/`imageRepoName` 保持一致

# 架构

跨模块的骨架：技术栈、配置层级、模块注册机制、接口约定、认证与后台任务。各能力模块自身的架构见 [datasource.md](datasource.md) 与 [knowledge-qa.md](knowledge-qa.md)。

## 技术栈与依赖

| 包 | 版本 | 用途 |
|----|------|------|
| Django | 3.2.25 | Web 框架 |
| djangorestframework | 3.15.1 | REST API 框架 |
| django-filter | 21.1 | 查询过滤 |
| django-cors-headers | 3.4.0 | CORS 跨域 |
| psycopg2-binary | 2.9.9 | PostgreSQL 驱动 |
| PyMySQL | 1.2.3 | MySQL 驱动（纯 Python，免编译） |
| cryptography | 50.0.1 | 凭据可逆加密（Fernet） |
| PyJWT | 1.7.1 | JWT 认证 |
| simple-background-task | 0.1.0 | 后台任务 |
| gunicorn | 20.0.4 | WSGI 生产服务器 |
| requests | 2.34.2 | HTTP 客户端 |
| redis | 3.5.3 | Redis 客户端 |
| django-redis-cache | 2.1.0 | Redis 缓存 |
| pyyaml | 6.0.1 | YAML 解析 |
| langchain / langchain-openai / langgraph | 1.4.2 / 1.6.4 / 1.2.12 | 问答 agent 与模型接入 |
| langchain-qdrant / qdrant-client | 1.1.0 / 1.19.1 | 向量库客户端 |
| pypdf / beautifulsoup4 | 6.19.0 / 4.15.0 | 文档解析（PDF / HTML） |

> `requests` 与 `urllib3` 因 langchain 生态顶替而跨大版本升级（`urllib3` 1.x → 2.x）。旧版只被 `utils/bsa.py` 引用，而该文件**当前无调用方**（见 [deployment.md](deployment.md) 附录），升级不触碰活代码。

## 配置层级

```
settings/settings.py          基础配置（DB、DRF、中间件、缓存、INSTALLED_APPS）
      ↓
settings/{local|dev|test|prod}.py   按 ENV_TYPE 分化
      ↓
config/conf.ini               外部配置（数据库连接、各模块参数）
      ↓
utils/configure.py            Configure 单例 → CONF_ATTR
```

通过环境变量 `ENV_TYPE` 切换环境（默认 `local`）：

| `ENV_TYPE` | 配置文件 |
|-----------|---------|
| `local` | `settings/local.py` |
| `dev` | `settings/dev.py` |
| `test` | `settings/test.py` |
| `prod` | `settings/prod.py` |

`conf.ini` 按 `[名称_环境]` 分段（如 `[db_local]` / `[db_dev]`），业务参数段（`[datasource]` / `[knowledge]`）不带环境后缀。**禁止直接使用 `os.environ`**（读 `ENV_TYPE` 除外）。

> ⚠️ `CONF_ATTR` 在**模块导入时一次性载入**，改动 `conf.ini` **必须重启进程**才生效。这也是「模型配置为什么落库而不是写 conf.ini」的原因之一（见 [knowledge-qa.md](knowledge-qa.md)）。

## 模块注册机制

**所有业务模块挂在单一 `apps` Django app 下**：`INSTALLED_APPS` 中只有 `"apps"` 一项。

- 新增模块**不需要**、也**不要**把 `apps.your_module` 加进 `INSTALLED_APPS`
- **不要**为模块新建 `AppConfig`
- 模型的 `app_label` 是 `"apps"`，不是模块名
- 模型的注册靠**导入副作用**：只有在 `config/urls.py` 引用了该模块的 `views` 之后，其 `models` 才会注册到 Django。因此新模块**必须先在 `urls.py` 注册**，否则模型不可见

> 副作用：`manage.py check` 的输出条数不稳定（`apps.get_models()` 的结果可能缓存在模型被导入之前）。判断 CI 是否通过不要依赖该命令的输出条数。

## 代码分层与目录

```
src/                          # 后端代码根目录
├── manage.py
├── requirements.txt / requirements-dev.txt
├── right_config.json         # 平台菜单注册配置（未使用）
├── start.sh                  # 生产启动脚本（gunicorn）
│
├── config/                   # Django 项目配置包
│   ├── urls.py               # 根 URL 路由（注册所有 ViewSet，含 SYS_NAME）
│   ├── conf.ini              # 外部配置（数据库、数据源密钥等）
│   └── wsgi.py / asgi.py
│
├── settings/                 # 多环境配置
│   ├── settings.py           # 基础配置（DB、DRF、中间件、缓存）
│   ├── local.py / dev.py / test.py / prod.py
│   └── gunicorn.py
│
├── apps/                     # 业务模块（单一 app，见「模块注册机制」）
│   ├── base/                 # 基类：baseviews（ViewSet 基类 + 统一响应）、models（抽象基 Model）
│   ├── datasource/           # 数据源纳管与元数据采集 → docs/datasource.md
│   │   ├── models.py         #   数据源 / 快照 / 采集任务 / 问题清单 / 分析规则
│   │   ├── serializers.py
│   │   ├── views.py          #   DatasourceView / CatalogView
│   │   ├── services.py       #   采集编排与异步任务
│   │   ├── analyzer.py       #   分析驱动（遍历规则、注入上下文、校验产出）
│   │   ├── rules/            #   规则声明式注册
│   │   │   ├── context.py    #     RuleContext（规则统一入参 + 产出方法）
│   │   │   ├── definitions.py#     RuleDefinition 与各条规则的判定函数
│   │   │   └── registry.py   #     规则清单、查找、层级校验、阈值解析
│   │   ├── collectors/       #   方言采集器（base / postgres / mysql）
│   │   ├── differ.py         #   快照结构差异对比
│   │   └── tests.py
│   ├── knowledge/            # 知识问答（RAG + 通用大模型）→ docs/knowledge-qa.md
│   │   ├── models.py         #   模型配置 / 知识库 / 文档 / 块 / 会话 / 消息
│   │   ├── llm.py            #   模型接入（对话与嵌入分开取）
│   │   ├── vectorstore.py    #   Qdrant 封装（集合 / 写入 / 删除 / 检索）
│   │   ├── ingest.py         #   解析 → 切分 → 嵌入 → 写库写向量
│   │   ├── retrieval.py      #   检索 + 回库取正文
│   │   ├── qa/               #   langgraph 单 agent 与工具
│   │   ├── serializers.py / views.py
│   │   └── tests.py
│   └── test/                 # 脚手架示例模块（见 development.md 附录）
│
├── utils/                    # 通用能力（扁平结构，禁止业务模块自建工具类）
│   ├── authentication.py     # JWT 认证（JwtAuthentication、AuthedUser）
│   ├── bsa.py                # 平台底座客户端（未使用）
│   ├── common.py             # 密码哈希、DRF 错误格式化、DB 连接清理装饰器
│   ├── background.py         # 后台任务提交（worker 启动与入队）
│   ├── configure.py          # INI 配置解析（CONF_ATTR 单例）
│   ├── crypto.py             # 凭据可逆加解密（Fernet）
│   ├── custom_enum.py        # 枚举定义（IntegerChoices）
│   ├── exception.py          # DRF 全局异常处理
│   ├── logger.py             # 统一日志（get_logger，按天轮转保留 30 天）
│   ├── middleware.py         # 自定义中间件（当前为空实现）
│   └── pagination.py         # 分页（StandardPagination + paginate）
│
├── hooks/                    # 平台生命周期钩子（未使用）
├── jobs/                     # 定时任务目录（预留）
├── scripts/                  # 运维脚本（SQL 初始化）
│
└── sql/                      # 数据库 SQL
    ├── pg_struct.sql         # 全量表结构（新建库执行）
    ├── patch.sql             # 增量补丁（已有库执行）
    ├── pg_data.sql           # 初始数据
    └── init_user_db.sh       # 创建数据库角色与授权
```

**跨模块调用**：禁止直接 import 别的模块的 queryset / model 去查询。跨模块只读访问要走对方模块提供的服务函数——例如 `apps/knowledge/qa/tools.py` 查表结构时调用的是 `datasource_services.list_snapshot_tables()`，不是直接查 `MetadataSnapshot`。

## 接口约定

### 统一响应格式

所有 API 必须返回统一结构，**禁止裸 `Response()`**：

```json
{ "code": 2000, "message": "success", "data": { } }
```

| 类 | code | 含义 |
|----|------|------|
| `ResponseOK` | 2000 | 成功 |
| `ResponseError` | 5000 | 服务器错误 |
| `ResponseBadRequest` | 4000 | 参数错误 |
| `ResponseForbidden` | 4003 | 无权限 |
| `ResponseNotFound` | 4004 | 资源不存在 |
| `ResponseExpectationFailed` | 4017 | 业务条件未满足 |

```python
return baseviews.ResponseOK({"key": "value"})
return baseviews.ResponseOK(pagination.paginate(self, qs))
return baseviews.ResponseBadRequest("参数不合法")
return baseviews.ResponseExpectationFailed("该数据源已有采集任务在执行中")
```

> 4017 是「可预期的业务拒绝」的落点：凭据测试失败、重复来源、配置缺失等**使用者能自己解决**的情况用它，而不是报 5000。

### 分页

列表接口必须使用 `pagination.paginate(self, qs)`，返回结构含 `count` / `total` / `results`。分页参数：`page`、`page_size`（默认 10）。

### URL 路径规则

`SYS_NAME` 定义在 `config/urls.py`，当前为 `'my-dba'`：

- ViewSet 路由：`{SYS_NAME}/v1/xxx`
- 函数视图：`{SYS_NAME}/api/xxx`

各模块的具体接口清单见其模块文档。

## 认证与鉴权

- JWT Token 通过请求头 **`Token`** 字段传递（非标准的 `Authorization: Bearer`）
- 解码后用户信息在 `request.user`（`AuthedUser`，含 `user_id` / `username` / `role`；`is_staff = (role == "admin")`）
- `JWT_SECRET` 在 `settings/settings.py`，生产环境必须设置

ViewSet 基类（`apps/base/baseviews.py`）按权限分三档：

| 基类 | 权限 | 适用场景 |
|------|------|---------|
| `AnyLogin` | 无认证、无权限 | **本项目统一采用**（原因见下） |
| `BaseView` / `OperatorView` | `IsAuthenticated` | 常规业务接口 |
| `SuperUserView` | `IsAdminUser` | 系统管理接口 |

> ⚠️ **本项目所有接口继承 `AnyLogin`，系统内不做任何认证与角色校验。**
>
> 原因：**本仓库没有任何代码会产出 `Token` 头**——`utils/middleware.py` 的 `CustomMiddleware` 是空实现，前端无登录页、从不调用 `setToken`。因此 `IsAuthenticated` / `IsAdminUser` 会让接口恒返回 4003。
>
> **代价**：只要服务可达，任何人都能读写数据源配置（含目标库凭据）、模型 API Key、知识库文档，并借「连接测试」探测内网。
>
> **因此：服务只能部署在内网，不要直接暴露到公网或不可信网络。** 访问控制依赖网络隔离，不依赖应用层。若要对外开放，必须先补齐登录 / token 机制（属独立的变更）。
>
> 决策背景：`openspec/changes/archive/2026-09-22-add-datasource-catalog/design.md` 的 D7。

## 后台任务

- 异步任务使用 **`simple-background-task`**（`django-q` 与 `apscheduler` **并未安装**，勿照抄脚手架文档）
- 任务函数必须加 `@clean_db_connections_decorator`（`utils/common.py`），防止线程内数据库连接丢失
- 该库的 worker 线程**不会自动启动**，且每次构造 `BackgroundTask()` 都会重建内部队列——应只构造一次并显式 `start()`，不要用它的 `defer()`

参考实现：`apps/datasource/services.py`（`utils/background.py` 是提交入口）。

> 该队列是**进程内内存**队列：进程重启后未执行的任务会丢失，多 worker 部署时任务在接收请求的那个 worker 进程内执行。任务状态以数据库表为准（`collect_task` / `kb_document.status`），不要依赖队列本身。各模块的限制见其模块文档。

## 代码规范入口

脚手架硬性约束在 `.ai-harness/rules/`（scaffold / security / naming / architecture / code-style），**违反任一条即不符合规范，必须在继续之前修复**。日常开发流程、测试与质量检查见 [development.md](development.md)。

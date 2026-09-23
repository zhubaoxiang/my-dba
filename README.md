# 我的数据库管理专家（my-dba）

**面向开发与测试人员**的数据库助手。把你要用的库接进来，就能看懂表结构、发现设计隐患、写出正确的 SQL，并随时问数据库相关的问题。

后端 Django 3.2 + DRF，前端 Vue 3 + Element Plus，数据库 PostgreSQL。**通过 docker compose 部署**（对外路径前缀 `my-dba`）。

> 本文档描述**当前代码状态**。`openspec/changes/` 下的变更提案尚未落地的部分会明确标注。

## 目标使用者与边界

**使用者是写代码和测功能的人，不是 DBA。**

典型场景：接了需求要动某张表，先看看它长什么样、有哪些索引、字段类型有没有坑；自己写的 SQL 到底对不对、会不会慢；遇到一个数据库概念或报错想快速弄清楚。

| 做 | 不做 |
|----|------|
| 帮使用者**看懂**库表结构与隐患 | 备份恢复、容量规划、主从运维 |
| 帮使用者**写出**正确的建表语句与 SQL | 数据库巡检、值班告警、SLA 管理 |
| 帮使用者**判断**自己 SQL 的规范与性能 | 慢查询平台、SQL 上线审批流 |
| 解答数据库相关的问题 | 生产库变更执行 |

> **演进方向**：后续功能一律往「帮开发/测试自助解决问题」这个方向走。偏 DBA 运维的能力（监控、备份、容量、审批）不要引入——那是另一类产品的职责。

## 能力与路线图

四条能力围绕使用者的三条诉求展开，另有 1 项支撑性改造：

| 能力 | 使用者的诉求 | 状态 |
|------|-------------|------|
| **分析数据库表** | 「这个库/表长什么样？有没有坑？」 | ✅ 已实现并归档（`add-datasource-catalog`） |
| **知识问答**（RAG） | 「这个报错 / 概念 / 用法是什么？」 | ⬜ 未开始 |
| **分析 SQL** | 「我这条 SQL 写得对吗？会不会慢？」 | ⬜ 未开始 |
| **自然语言转建表语句 / SQL** | 「按我说的建张表 / 写条查询」 | ⬜ 未开始 |
| 分析规则注册表（支撑） | 让分析规则可配置、可开关 | 🚧 进行中（`add-analysis-rule-registry`，见下文） |

已确定的技术选型：数据源支持 PostgreSQL + MySQL；RAG 向量存储拟用 pgvector（复用现有 PG）；LLM 走公有云 API。

## 已实现：分析数据库表

把 PostgreSQL / MySQL 数据源接进来，**只读**采集库表元数据——表结构、列定义、索引、主键与外键关联、行数与占用体积。采集一次之后可随时回看，不用每次去连库翻 `information_schema`。

采集完由内置规则给出一份隐患清单，回答「这个库有没有坑」。

### 数据表

DDL 见 `src/sql/pg_struct.sql`（全量）与 `src/sql/patch.sql`（增量），**本项目禁止 Django migration**。

| 表 | 说明 |
|----|------|
| `datasource` | 被纳管的数据源配置，密码可逆加密存储 |
| `metadata_snapshot` | 每次采集生成的不可变快照，原始结果存 JSONB |
| `collect_task` | 采集任务与状态 |
| `catalog_issue` | 库表健康问题清单 |

### API

路径前缀 `{SYS_NAME}/v1`，当前 `SYS_NAME = 'my-dba'`。

数据源管理：

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/datasource` | 数据源列表（分页） |
| POST | `/datasource` | 新建数据源 |
| GET | `/datasource/{id}` | 数据源详情 |
| PUT | `/datasource/{id}` | 修改（`password` 留空表示不修改） |
| DELETE | `/datasource/{id}` | 软删除 |
| POST | `/datasource/test` | 测试未保存的连接参数 |
| POST | `/datasource/{id}/test` | 测试已保存数据源的连通性 |
| POST | `/datasource/{id}/collect` | 触发采集，立即返回任务 |
| GET | `/datasource/{id}/tasks` | 采集任务列表 |

元数据查询：

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/catalog/snapshots?datasource_id=` | 快照列表 |
| GET | `/catalog/latest-snapshot?datasource_id=` | 最新快照 |
| GET | `/catalog/summary?snapshot_id=` | 库表总览统计（表数量、各级问题数、未采集项、参与评估的规则数） |
| GET | `/catalog/tables?snapshot_id=&keyword=` | 表清单（分页、可搜索） |
| GET | `/catalog/table?snapshot_id=&table=&schema=` | 表详情（列 / 索引 / 主键 / 外键 / 体积） |
| GET | `/catalog/issues?datasource_id=&issue_level=` | 问题清单（按级别筛选） |
| GET | `/catalog/diff?snapshot_id=&compare_snapshot_id=` | 两个快照的结构差异 |

> ⚠️ **鉴权风险**：以上接口继承 `baseviews.AnyLogin`，**系统内不做任何认证与角色校验**，且本仓库没有任何代码会产出 `Token` 头（见「认证与鉴权」一节）。这意味着**只要服务可达，任何人都能读写数据源配置（含目标库凭据）、触发采集、并借「连接测试」探测内网**。
>
> **因此：服务只能部署在内网，不要直接暴露到公网或不可信网络。** 访问控制依赖网络隔离，不依赖应用层。—— 决策背景见 `openspec/changes/archive/2026-09-22-add-datasource-catalog/design.md` 的 D7。

### 健康分析规则

规则由 `src/apps/datasource/rules/` **声明式注册**：每条规则 = 一个判定函数 + 一条 `RuleDefinition`（`code` / 名称 / 默认级别 / 适用层级 / 默认阈值）。规则以稳定的字符串 `code` 标识。

| 规则 code | 名称 | 默认级别 | 触发条件 | 对你意味着什么 |
|-----------|------|---------|---------|--------------|
| `no_primary_key` | 无主键表 | 高 | 表没有主键 | 无法按行定位，写进重复数据不容易发现 |
| `fk_without_index` | 外键缺索引 | 中 | 外键列没有以它为前导列的索引 | 按它做关联查询会全表扫描 |
| `duplicate_index` | 重复或冗余索引 | 中 / 低 | 索引列完全相同；或非唯一索引是另一索引的前缀 | 白占空间、拖慢写入；上线前删掉即可 |
| `unused_index` | 疑似未使用索引 | 低 | 索引使用次数为 0 且表行数超过阈值 | 只增加写入成本，可评估删除 |
| `big_table` | 超大表 | 中 | 行数或占用空间超过阈值 | 查询、加字段、加索引都会明显变慢 |
| `suspicious_column_type` | 可疑字段类型 | 中 / 低 | 用字符类型存时间；超长 varchar；大对象类型 | 字符串比较用不上时间运算与范围索引；大字段拖慢整表扫描 |
| `isolated_table` | 孤立表 | 低 | 既无外键，也未被任何表引用 | 可能是遗留表，改表前先确认是否还有人用 |

分析是**纯计算**：输入是采集快照，不访问数据库，因此同一份快照可用不同阈值反复重算，也便于单测。

> **怎么看级别**：级别是按**规则类型**静态指定的，不随表体量或业务重要性变化——一张 5 行的字典表没有主键和一张 5 亿行的订单表没有主键，报出来都是「高」。所以看到「高」先结合自己知道的情况判断轻重，别当成必须立刻处理的告警。引入体量维度是后续待办。

### 配置（`src/config/conf.ini` 的 `[datasource]` 段）

| 配置项 | 说明 |
|--------|------|
| `secret_key` | 数据源密码的加密密钥，**上线前必须替换为随机值** |
| `connect_timeout` | 连接超时（秒） |
| `statement_timeout` | 语句超时（秒） |
| `exact_count` | 是否用 `COUNT(*)` 取精确行数，默认 `false`（改用估算值，避免拖垮大表） |
| `big_table_rows` / `big_table_size_mb` | 超大表判定阈值 |
| `varchar_max_length` | 可疑超长 varchar 的判定长度 |
| `unused_index_min_rows` | 判定未使用索引所需的最小表行数 |

> 阈值将在「分析规则注册表」改造完成后迁入规则表，`conf.ini` 只保留采集侧参数。改动 `conf.ini` **必须重启进程**（`CONF_ATTR` 在模块导入时一次性载入）。

### 安全说明

- 采集使用**独立只读短连接**，不复用 Django ORM 连接；PostgreSQL 连接显式 `set_session(readonly=True)`
- 采集只读系统目录，**不对目标库执行任何 DDL/DML**，也不支持在目标库上执行用户 SQL
- 数据源密码可逆加密存储（`utils/crypto.py`，Fernet），任何接口响应与日志都不含密码
- 加密密钥缺失时抛错，**不会回退为明文**

### 已知限制

- 采集任务走 `simple-background-task` 的**进程内内存队列**，其 worker 线程不会自动启动（已在 `services.py` 中显式处理）。进程重启后队列中的任务会丢失，需重新触发；任务状态以数据库 `collect_task` 为准
- 多 worker 部署时，任务在接收该请求的 worker 进程内执行
- 「疑似未使用索引」依赖目标库的索引使用统计（PG `pg_stat_user_indexes`、MySQL `performance_schema`），取不到时跳过该规则并在快照的 `unavailable` 中留痕
- 单份快照的全部元数据存在一个 JSONB 单元格里，查询时整块载入并解析，**分页是"假分页"**。表数量上万后需要改为结构化明细表
- **快照没有保留策略**：每次采集整份复制，长期运行会持续占用存储
- MySQL 采集器尚未在真实 MySQL 上验证过（PostgreSQL 分支已端到端验证）

## 🚧 进行中：分析规则注册表

对应 `openspec/changes/add-analysis-rule-registry`（18/59 任务）。目标：让规则的**启用开关、严重级别、阈值**运行时可调、无需改代码发版，并支持规则作用于库/模式/表/列不同层级。

设计边界：**规则判定逻辑仍是代码**（不引入表达式引擎），只有元数据可配置。

代码侧已完成（声明式规则、`analysis_rule` 模型、`catalog_issue` 以 `rule_code` 取代 `issue_type`），**数据库结构变更尚未执行**。落地步骤：

```bash
# 已有数据的库执行增量补丁（新库直接执行 pg_struct.sql 即可）
psql "postgresql://<用户>:<密码>@<主机>:<端口>/<库名>" -f src/sql/patch.sql
```

补丁会给 `catalog_issue` 补 `rule_code` / `rule_name` / `object_level` / `schema_name` 并删除 `issue_type`。旧行因 `rule_code` 为空属失效数据，确认可清空后手动执行 `DELETE FROM catalog_issue;`（该语句只写在 `patch.sql` 注释里，不会自动执行）。

规则管理 API、前端规则页、阈值从 `conf.ini` 迁出等尚待实现。

## 快速开始

### 环境准备

运行环境由 `uv` 统一管理：Python 3.11.9 与虚拟环境自动下载/创建，本机无需预装 Python、无需管理员权限。前端需 Node ≥ 18。

### 后端

```bash
# 1. 安装依赖（uv 创建的 venv 不含 pip，请用 uv pip）
uv python install 3.11.9
uv venv venv --python 3.11.9
cd src
uv pip install -r requirements.txt
uv pip install -r requirements-dev.txt

# 2. 初始化数据库结构（二选一）
#    新建库：psql "<连接串>" -f sql/pg_struct.sql
#    已有库：psql "<连接串>" -f sql/patch.sql

# 3. 配置数据库连接：src/config/conf.ini 的 [db_<ENV_TYPE>] 段

# 4. 启动（ENV_TYPE 默认 local）
cd src && ../venv/Scripts/python.exe manage.py runserver    # Windows
cd src && ../venv/bin/python manage.py runserver            # macOS / Linux
```

### 前端

```bash
cd static
npm install --registry=https://registry.npmmirror.com   # 国内网络加速；官方源亦可
npm run dev                                             # http://localhost:5173，已代理到 Django 后端
```

## 目录结构

```
my-dba/
├── Dockerfile                    # Docker 镜像构建
├── docker-compose.yml            # 本地容器编排
├── package.py                    # BSA dat 包打包脚本（未使用）
├── service.json                  # BSA Chart 包配置（未使用）
├── service-mgr-tool/             # Chart 打包工具（未使用）
│
├── openspec/                     # 变更提案与规格基线
│   ├── project.md                # 项目上下文（约定、约束、外部依赖、已知问题）
│   ├── specs/                    # 已归档能力的规格（当前真值）
│   └── changes/                  # 待实施提案 / archive 已归档提案
│
├── .ai-harness/                  # AI 开发约束（规则 / 技能 / 提示词）
├── .ci/                          # lint 规则与脚手架合规检查
├── wiki/                         # 项目知识库（Obsidian vault）
│
├── static/                       # 前端工程（Vue3 + Vite 源码，构建产物 static/dist/）
│
└── src/                          # 后端代码根目录
    ├── manage.py
    ├── requirements.txt / requirements-dev.txt
    ├── right_config.json         # BSA 平台菜单注册配置（未使用）
    ├── start.sh                  # 生产启动脚本（gunicorn）
    │
    ├── config/                   # Django 项目配置包
    │   ├── urls.py               # 根 URL 路由（注册所有 ViewSet，含 SYS_NAME）
    │   ├── conf.ini              # 外部配置（数据库、数据源密钥等）
    │   ├── wsgi.py / asgi.py
    │
    ├── settings/                 # 多环境配置
    │   ├── settings.py           # 基础配置（DB、DRF、中间件、缓存）
    │   ├── local.py / dev.py / test.py / prod.py
    │   └── gunicorn.py
    │
    ├── apps/                     # 业务模块（见下方「模块注册机制」）
    │   ├── base/                 # 基类：baseviews（ViewSet 基类 + 统一响应）、models（抽象基 Model）
    │   ├── datasource/           # 数据源纳管与元数据采集
    │   │   ├── models.py         # 数据源 / 快照 / 采集任务 / 问题清单 / 分析规则
    │   │   ├── serializers.py
    │   │   ├── views.py          # DatasourceView / CatalogView
    │   │   ├── services.py       # 采集编排与异步任务
    │   │   ├── analyzer.py       # 分析驱动（遍历规则、注入上下文、校验产出）
    │   │   ├── rules/            # 规则声明式注册
    │   │   │   ├── context.py    #   RuleContext（规则统一入参 + 产出方法）
    │   │   │   ├── definitions.py#   RuleDefinition 与 7 条规则的判定函数
    │   │   │   └── registry.py   #   规则清单、查找、层级校验、阈值解析
    │   │   ├── collectors/       # 方言采集器（base / postgres / mysql）
    │   │   ├── differ.py         # 快照结构差异对比
    │   │   └── tests.py
    │   └── test/                 # 脚手架示例模块（见附录）
    │
    ├── utils/                    # 通用能力（扁平结构，禁止业务模块自建工具类）
    │   ├── authentication.py     # JWT 认证（JwtAuthentication、AuthedUser）
    │   ├── bsa.py                # BSA 底座客户端（未使用）
    │   ├── common.py             # 密码哈希、DRF 错误格式化、DB 连接清理装饰器
    │   ├── configure.py          # INI 配置解析（CONF_ATTR 单例）
    │   ├── crypto.py             # 数据源凭据可逆加解密（Fernet）
    │   ├── custom_enum.py        # 枚举定义（IntegerChoices）
    │   ├── exception.py          # DRF 全局异常处理
    │   ├── logger.py             # 统一日志（get_logger，按天轮转保留 30 天）
    │   ├── middleware.py         # 自定义中间件（当前为空实现）
    │   └── pagination.py         # 分页（StandardPagination + paginate）
    │
    ├── hooks/                    # BSA 生命周期钩子（未使用）
    ├── jobs/                     # 定时任务目录（预留）
    ├── scripts/                  # 运维脚本（BSA 菜单注册 SDK（未使用）、SQL 初始化）
    │
    └── sql/                      # 数据库 SQL
        ├── pg_struct.sql         # 全量表结构（新建库执行）
        ├── patch.sql             # 增量补丁（已有库执行）
        ├── pg_data.sql           # 初始数据
        └── init_user_db.sh       # 创建数据库角色与授权
```

## 开发规范

### 1. 新增业务模块

每个业务模块在 `src/apps/` 下创建独立子目录，必须包含：

```
apps/your_module/
├── __init__.py          # 包标识（空文件）
├── models.py            # 数据模型，必须继承 AbstractTimeFiledModel
├── serializers.py       # DRF 序列化器
├── filters.py           # 过滤类（无过滤需求可留空）
└── views.py             # ViewSet，必须继承 baseviews 中的基类
```

#### 模块注册机制（重要，易踩坑）

**所有业务模块挂在单一 `apps` Django app 下**：`INSTALLED_APPS` 中只有 `"apps"` 一项。

- 新增模块**不需要**、也**不要**把 `apps.your_module` 加进 `INSTALLED_APPS`
- **不要**为模块新建 `AppConfig`
- 模型的 `app_label` 是 `"apps"`，不是模块名
- 模型的注册靠**导入副作用**：只有在 `config/urls.py` 引用了该模块的 `views` 之后，其 `models` 才会注册到 Django。因此新模块**必须先在 `urls.py` 注册**，否则模型不可见

> 副作用：`manage.py check` 的输出条数不稳定（`apps.get_models()` 的结果可能缓存在模型被导入之前）。判断 CI 是否通过不要依赖该命令的输出条数。

#### Model 规范

所有 Model 必须继承 `AbstractTimeFiledModel`，自动获得 `create_time`、`update_time`、`creator`、`is_deleted` 字段。使用软删除（过滤 `is_deleted=False`），**禁止物理删除记录**。必须显式指定 `db_table`。

```python
from apps.base.models import AbstractTimeFiledModel
from utils import custom_enum


class YourModel(AbstractTimeFiledModel):
    name = models.CharField(max_length=64, verbose_name="名称")
    status = models.SmallIntegerField(choices=custom_enum.YourEnum.choices, verbose_name="状态")

    class Meta:
        db_table = "your_table"
```

枚举类在 `utils/custom_enum.py` 中用 `IntegerChoices` 定义，**禁止裸整数**：

```python
class YourEnum(models.IntegerChoices):
    TYPE_A = 1, "类型A"
    TYPE_B = 2, "类型B"
```

#### ViewSet 规范

| 基类 | 权限 | 适用场景 |
|------|------|---------|
| `AnyLogin` | 无认证、无权限 | **本项目采用**：服务仅在内网部署，访问控制依赖网络隔离（见「部署」一节的鉴权风险） |
| `BaseView` / `OperatorView` | 需登录（`IsAuthenticated`） | 常规业务接口 |
| `SuperUserView` | 需管理员（`IsAdminUser`） | 系统管理接口 |

```python
from apps.base import baseviews
from utils import pagination


class YourView(baseviews.AnyLogin):
    queryset = models.YourModel.objects.all()
    serializer_class = serializers.YourSerializer
    pagination_class = pagination.StandardPagination

    def list(self, request, **kwargs):
        queryset = self.get_queryset().filter(is_deleted=False).order_by("-id")
        result = pagination.paginate(self, queryset)
        return baseviews.ResponseOK(result)
```

自定义 Action 用 DRF 的 `@action`：

```python
@action(detail=False, methods=["POST"], url_path="custom-action")
def custom_action(self, request, **kwargs):
    return baseviews.ResponseOK(data)
```

#### Serializer 规范

- 查询 / 列表用 `ModelSerializer`
- 创建 / 更新用普通 `Serializer`，在 `validate()` / `validate_<field>()` 中做校验
- 校验失败用 `ToolUtil.format_drf_error()` 格式化错误信息

```python
class YourSerializer(serializers.ModelSerializer):
    create_time = serializers.DateTimeField(format="%Y-%m-%d %H:%M:%S")

    class Meta:
        model = models.YourModel
        exclude = ["update_time"]


class YourCreateSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=64)

    def validate(self, attrs):
        return attrs
```

#### URL 注册

在 `src/config/urls.py` 中注册：

```python
from apps.your_module import views as your_views

router.register(rf"{SYS_NAME}/v1/your_module", your_views.YourView, basename="your_module")
```

路径规则（`SYS_NAME` 定义在 `src/config/urls.py`）：

- ViewSet 路由：`{SYS_NAME}/v1/xxx`
- 函数视图：`{SYS_NAME}/api/xxx`

### 2. 统一响应格式

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

### 3. 分页规范

列表接口必须使用 `pagination.paginate()`，返回结构含 `count` / `total` / `results`。分页参数：`page`、`page_size`（默认 10）。

### 4. 认证与鉴权

- JWT Token 通过请求头 **`Token`** 字段传递（非标准的 `Authorization: Bearer`）
- 解码后用户信息在 `request.user`（`AuthedUser`，含 `user_id` / `username` / `role`；`is_staff = (role == "admin")`）
- `JWT_SECRET` 在 `settings/settings.py`，生产环境必须设置

> ⚠️ **本仓库当前没有任何代码会产出 `Token` 头**（`CustomMiddleware` 是空实现，前端无登录页、从不调用 `setToken`）。因此 `IsAuthenticated` / `IsAdminUser` 会让接口恒返回 4003。
>
> 本项目因此统一使用 `AnyLogin`，**访问控制依靠网络隔离而非应用层鉴权**——服务只允许部署在内网。若要对外开放，必须先补齐登录/token 机制（属独立的变更）。

### 5. 环境配置规范

通过环境变量 `ENV_TYPE` 切换（默认 `local`）：

| `ENV_TYPE` | 配置文件 |
|-----------|---------|
| `local` | `settings/local.py` |
| `dev` | `settings/dev.py` |
| `test` | `settings/test.py` |
| `prod` | `settings/prod.py` |

外部配置在 `config/conf.ini` 中按 `[名称_环境]` 分段，由 `utils/configure.py` 解析为 `CONF_ATTR`。**禁止直接使用 `os.environ`**（`ENV_TYPE` 除外）。注意 `CONF_ATTR` 在模块导入时载入，改配置需重启。

### 6. BSA 平台集成规范（当前未使用，保留备查）

> 本项目**不在 BSA 底座上部署**，改用 docker compose。以下能力与相关文件目前**未接入、未被调用**，保留仅为将来可能的平台化部署备查。修改业务代码时不要依赖它们。

- 菜单配置：`src/right_config.json` 的 `children` 数组
- 安装钩子：`hooks/install.py` 执行菜单注册与 SQL 初始化
- 卸载钩子：`hooks/uninstall.py` 执行菜单注销与数据清理
- 菜单注册 SDK：`scripts/bsa_register_menu.py`（原设计经 Kong 网关调用 BSA 权限服务）

### 7. 后台任务规范

- 异步任务使用 **`simple-background-task`**（`django-q` 与 `apscheduler` **并未安装**，勿照抄脚手架文档）
- 任务函数必须加 `@clean_db_connections_decorator`（`utils/common.py`），防止线程内数据库连接丢失
- 该库的 worker 线程**不会自动启动**，且每次构造 `BackgroundTask()` 都会重建内部队列——应只构造一次并显式 `start()`，不要用它的 `defer()`。参考 `apps/datasource/services.py`

### 8. 日志规范

必须使用 `utils/logger.py` 的 `get_logger()`：

```python
from utils.logger import get_logger

LOGGER = get_logger("your_module.log")
LOGGER.info("操作成功")
```

日志落在 `src/logs/`，按天轮转、保留 30 天，同时输出文件与终端。**禁止在日志中输出密码、token 等敏感信息。**

### 9. BSA 平台客户端规范（当前未使用，保留备查）

> 同 §6：项目不在 BSA 底座上部署，`utils/bsa.py` 目前无调用方。

```python
from utils.bsa import bsa_client

info = bsa_client.get_service_component_info("b-vuln-backend")
```

原设计：认证信息从 `CONF_ATTR` 读取 `common_sessionid` 与 `common_csrftoken`，构造请求头代理到 BSA 平台。

### 10. SQL 规范

- **全量表结构**：`sql/pg_struct.sql` —— 新建库执行本文件一次到位
- **增量补丁**：`sql/patch.sql` —— **已有数据的库做结构变更只改这里**，按变更集追加、写成幂等；同时回头同步 `pg_struct.sql` 的建表语句，保持新库能一次建全
- 初始数据：`sql/pg_data.sql`
- 数据库角色创建：`sql/init_user_db.sh`
- 破坏性数据操作（`DELETE` / 改类型等）只写在 `patch.sql` 的注释里，由人工确认后手动执行

### 11. 代码风格

PEP 8，由 `ruff check` / `ruff format` 强制，配置在 `.ci/lint-rules/ruff.toml`。注释精简（≤3 行），只在 WHY 非显而易见时写；不做需求之外的抽象（YAGNI）；异常处理只在系统边界。

## 核心文件说明

| 文件 | 作用 | 是否需修改 |
|------|------|-----------|
| `config/urls.py` | URL 路由注册，新增模块**必须**在此注册（否则模型不可见） | **必须** |
| `apps/base/baseviews.py` | ViewSet 基类与统一响应封装 | 仅扩展基类时 |
| `apps/base/models.py` | 抽象 Model 基类 | 仅扩展字段时 |
| `utils/configure.py` | INI 配置解析 | 一般不修改 |
| `utils/authentication.py` | JWT 认证实现 | 一般不修改 |
| `utils/crypto.py` | 数据源凭据可逆加解密 | 一般不修改 |
| `utils/pagination.py` | 分页工具 | 一般不修改 |
| `utils/logger.py` | 统一日志 | 一般不修改 |
| `utils/exception.py` | 全局异常处理 | 一般不修改 |
| `utils/bsa.py` | BSA 底座客户端（**当前未使用**） | 一般不修改 |
| `settings/settings.py` | Django 核心配置（**注意：不需要改 `INSTALLED_APPS`**） | 极少 |
| `config/conf.ini` | 外部配置（数据库、数据源密钥） | 新环境时 |
| `right_config.json` | BSA 菜单注册结构（**当前未使用**） | 接入平台时 |
| `service.json` | Chart 包服务定义（**当前未使用**，仅镜像名与 `docker-compose.yml` 对齐） | 接入平台时 |
| `docker-compose.yml` / `Dockerfile` | **实际部署方式** | 部署形态变更时 |
| `sql/pg_struct.sql` / `sql/patch.sql` | 全量结构 / 增量补丁 | 表结构变更时 |

## 测试与质量检查

提交前必须全部通过（`.ci/pre-commit-config.yaml` 已配置为 pre-commit 钩子）：

```bash
# 脚手架合规检查
python .ci/custom-checks/scaffold_check.py

# 单元测试（当前 34 项，均为 SimpleTestCase，不需要数据库）
cd src && python manage.py test apps.datasource

# 代码规范
ruff check src/ --config .ci/lint-rules/ruff.toml
ruff format --check src/ --config .ci/lint-rules/ruff.toml

# 前端构建
cd static && npm run build
```

## 部署

**部署方式：docker compose。**

```bash
# 1. 构建镜像（Dockerfile 已做分层：依赖层在前，改代码只重建代码层）
docker build -t my-dba/my-dba:V1.0R01F00 .

# 2. 启动（network_mode: host，容器内监听 8080）
docker compose up -d

# 3. 查看日志 / 停止
docker compose logs -f
docker compose down
```

`docker-compose.yml` 使用 `network_mode: "host"`，`restart: always`。镜像名需与 `service.json` 的 `imageProjectName` / `imageRepoName` 保持一致。

> ⚠️ **只在内网部署**：数据源接口无应用层鉴权（见上文「鉴权风险」），宿主网络必须可信。

### 附：BSA dat 包打包（当前未使用）

`package.py` 与 `service-mgr-tool/` 是原 BSA 平台化部署的打包链路，现已不用。如将来需要：

```bash
python3 package.py --clean
git pull origin main
python3 package.py
```

## 依赖清单（核心）

| 包 | 版本 | 用途 |
|----|------|------|
| Django | 3.2.25 | Web 框架 |
| djangorestframework | 3.15.1 | REST API 框架 |
| django-filter | 21.1 | 查询过滤 |
| django-cors-headers | 3.4.0 | CORS 跨域 |
| psycopg2-binary | 2.9.9 | PostgreSQL 驱动 |
| PyMySQL | 1.2.3 | MySQL 驱动（纯 Python，免编译） |
| cryptography | 50.0.1 | 数据源凭据可逆加密（Fernet） |
| PyJWT | 1.7.1 | JWT 认证 |
| simple-background-task | 0.1.0 | 后台任务 |
| gunicorn | 20.0.4 | WSGI 生产服务器 |
| requests | 2.24.0 | HTTP 客户端 |
| redis | 3.5.3 | Redis 客户端 |
| django-redis-cache | 2.1.0 | Redis 缓存 |
| pyyaml | 6.0.1 | YAML 解析 |

## AI 开发约束加载流程

用 Claude Code 开发时，每次对话自动加载以下约束：

```
CLAUDE.md（项目根目录）
  ├── 项目概述、常用命令、架构概要
  ├── 脚手架检查清单摘要
  └── .ai-harness/rules/ 详细规则
        ├── scaffold.md     → 脚手架规范（模型继承、ViewSet 基类、响应格式、分页、软删除、枚举、路由注册）
        ├── security.md     → 安全约束（凭据管理、输入验证、认证要求、日志安全、密码存储）
        ├── naming.md       → 命名规范（类名、表名、ViewSet、Serializer、URL、枚举）
        ├── architecture.md → 架构约束（模块位置、通用能力、跨模块调用、配置读取、依赖引入）
        └── code-style.md   → 代码风格（格式化、注释、YAGNI、异常处理）
```

**违规即阻断**：违反 `.ai-harness/rules/` 中任何规则即不符合脚手架规范，必须在继续之前修复。

| Skill | 触发场景 | 作用 |
|-------|---------|------|
| `scaffold` | 新增模块、修改模型/视图/序列化器 | 强制执行脚手架规范 |
| `module-creator` | 创建新业务模块 | 生成符合规范的 models/serializers/views/URL |
| `review` | 代码变更审查 | 检查安全、架构、脚手架合规性 |
| `api-test` | API 契约测试 | 自动化测试全部接口并生成报告 |

**变更流程走 openspec**：规划 / 提案 / 规格变更用 `openspec/`（`/openspec:proposal` → `/openspec:apply` → `/openspec:archive`）。`openspec/project.md` 是项目上下文的事实来源，写提案前先读它。

**知识库**：项目根目录 `wiki/`（Obsidian vault），用 `/wiki-ingest` 摄入、`/wiki-update` 同步、`/wiki-query` 检索。

## 已知问题

以下为已知问题，尚未处理：

- **`src/config/conf.ini` 已被 git 跟踪且含真实数据库地址与口令**，违反 `.ai-harness/rules/security.md` 的凭据管理要求。应改为提交 `conf.ini.example`、将 `conf.ini` 加入 `.gitignore`，并轮换已泄露口令。**数据源加密密钥也写在该文件中，同样存在泄露风险**
- `package.py` 的注释中含一段 GitLab OAuth2 令牌明文，已进入 git 历史，应轮换
- `settings/settings.py` 的 `SECRET_KEY` 硬编码，`JWT_SECRET` 为空字符串
- `apps/test/` 缺少 `tests.py` 与 `filters.py`，且 `MavenEnforcerRules` 直接继承 `models.Model`（违反脚手架规则）。作为示例模块它本身不合规，请勿照抄
- 所有模型未显式声明主键类型，`manage.py check` 会报 `models.W042` 警告

## 附录：脚手架示例模块 `apps/test`

`apps/test/` 是脚手架自带的示例，演示各层协作方式，可作为参考——但注意上文「已知问题」中它的两处不合规。

| 层 | 类 | 说明 |
|----|----|------|
| Model | `MavenEnforcerRules` | 数据模型 |
| Serializer | `MavenEnforcerRuleSerializer` | `ModelSerializer`，列表查询、格式化时间、枚举 label |
| Serializer | `CreateBannedDependenciesSerializer` | 普通 `Serializer`，含 `validate()` 跨字段校验 |
| ViewSet | `TestView` | 继承 `AnyLogin`，标准列表（分页 + 软删除过滤）+ 自定义 Action |

注册的接口（`SYS_NAME = 'my-dba'`）：

- `GET  /my-dba/api/v1/test` — 列表
- `POST /my-dba/api/v1/test` — 创建
- `GET  /my-dba/api/v1/test/{id}` — 详情
- `POST /my-dba/api/v1/test/banned` — 自定义 Action

# 开发规范

改这个仓库时要遵守的约定。跨模块的架构（配置层级、模块注册机制、统一响应、认证、后台任务）在 [architecture.md](architecture.md)，这里讲**具体怎么写**。

> 硬性约束的完整清单在 `.ai-harness/rules/`：scaffold / security / naming / architecture / code-style。**违反任一条即不符合脚手架规范，必须在继续之前修复。**

## 1. 新增业务模块

每个业务模块在 `src/apps/` 下创建独立子目录，必须包含：

```
apps/your_module/
├── __init__.py          # 包标识（空文件）
├── models.py            # 数据模型，必须继承 AbstractTimeFiledModel
├── serializers.py       # DRF 序列化器
├── filters.py           # 过滤类（无过滤需求可留空）
└── views.py             # ViewSet，必须继承 baseviews 中的基类
```

**注册方式见 [architecture.md 的「模块注册机制」](architecture.md#模块注册机制)**——不要往 `INSTALLED_APPS` 里加东西，必须先在 `config/urls.py` 注册，否则模型不可见。

### Model 规范

所有 Model 必须继承 `AbstractTimeFiledModel`，自动获得 `create_time`、`update_time`、`creator`、`is_deleted` 字段。使用软删除（过滤 `is_deleted=False`），**禁止物理删除记录**。必须显式指定 `db_table`。

```python
from django.db import models

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

### ViewSet 规范

基类按权限选，本项目统一用 `AnyLogin`（原因见 [architecture.md 的「认证与鉴权」](architecture.md#认证与鉴权)）：

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

响应格式与分页规则见 [architecture.md 的「接口约定」](architecture.md#接口约定)。

### Serializer 规范

- 查询 / 列表用 `ModelSerializer`
- 创建 / 更新用普通 `Serializer`，在 `validate()` / `validate_<field>()` 中做校验
- 校验失败用 `ToolUtil.format_drf_error()`（`utils/common.py`）格式化错误信息

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

### URL 注册

在 `src/config/urls.py` 中注册：

```python
from apps.your_module import views as your_views

router.register(rf"{SYS_NAME}/v1/your_module", your_views.YourView, basename="your_module")
```

路径规则见 [architecture.md 的「URL 路径规则」](architecture.md#url-路径规则)。

## 2. 日志规范

必须使用 `utils/logger.py` 的 `get_logger()`：

```python
from utils.logger import get_logger

LOGGER = get_logger("your_module.log")
LOGGER.info("操作成功")
```

日志落在 `src/logs/`，按天轮转、保留 30 天，同时输出文件与终端。

> **禁止在日志中输出密码、token 等敏感信息。** 凭据相关的约束见 `.ai-harness/rules/security.md`。

## 3. SQL 规范

本项目**禁止 Django migration**，DDL 一律手写在 SQL 文件里：

- **全量表结构**：`src/sql/pg_struct.sql` —— 新建库执行本文件一次到位
- **增量补丁**：`src/sql/patch.sql` —— **已有数据的库做结构变更只改这里**，按变更集追加、写成幂等；同时回头同步 `pg_struct.sql` 的建表语句，保持新库能一次建全
- 初始数据：`src/sql/pg_data.sql`
- 数据库角色创建：`src/sql/init_user_db.sh`

**破坏性数据操作（`DELETE` / 改类型等）只写在 `patch.sql` 的注释里**，由人工确认后手动执行，不自动跑。

## 4. 代码风格

PEP 8，由 `ruff check` / `ruff format` 强制，配置在 `.ci/lint-rules/ruff.toml`。

- 注释精简（≤3 行），只在 WHY 非显而易见时写
- 不做需求之外的抽象（YAGNI）
- 异常处理只在系统边界

## 5. 测试与质量检查

提交前必须全部通过（`.ci/pre-commit-config.yaml` 已配置为 pre-commit 钩子）：

```bash
# 脚手架合规检查
python .ci/custom-checks/scaffold_check.py

# 单元测试（当前 131 项）
cd src && ENV_TYPE=test python manage.py test apps.datasource apps.knowledge

# 代码规范
ruff check src/ --config .ci/lint-rules/ruff.toml
ruff format --check src/ --config .ci/lint-rules/ruff.toml

# 前端构建
cd static && npm run build
```

### 跑测试要知道的三件事

**1. 用 `ENV_TYPE=test`**，它指向 `conf.ini` 的 `[db_test]`（库名 `test`）。不指定时会按 `local` 走，Django 建出的是 `test_dba` 之类的中间库。

**2. 绝大多数用例是 `SimpleTestCase`，不碰数据库**；只有知识问答的少数几个用到：`StreamPersistenceTests`（`TestCase`）与 `StreamEndpointTests`（`TransactionTestCase`）。

**3. 测试库的表结构由 `utils/test_runner.py` 灌入。** 本项目禁止 Django migration，仓库里没有任何 `migrations/`，而 Django 建测试库靠跑 `migrate`——不干预的话测试库里一张业务表都不会有，`TestCase` 会直接以 `relation "xxx" does not exist` 失败。`SqlSchemaTestRunner` 在建好测试库之后把 `sql/pg_struct.sql` 整份灌进去。

> 它**只在测试套件确实涉及数据库时才灌**。全部为 `SimpleTestCase` 时 Django 不会创建测试库，连接仍指向 `conf.ini` 配置的真实库——那时无条件灌 DDL 等于改真实库的结构。
>
> 附带收益：**改了表结构却忘记同步 `pg_struct.sql`，DB 测试会立刻失败**，形成一道防漂移的约束。

> **为什么接口级测试要用 `TransactionTestCase`**：`TestCase` 把每个用例包在 atomic 里（autocommit=False），而测试客户端每个请求结束发的 `request_finished` 信号会触发 `close_old_connections`——它见到 autocommit 与配置不符就关闭连接，于是流式响应结束后那段落库代码拿到的是一条已关闭的连接。生产环境请求周期内 autocommit 一致，不存在该问题。

## 6. 核心文件说明

| 文件 | 作用 | 是否需修改 |
|------|------|-----------|
| `config/urls.py` | URL 路由注册，新增模块**必须**在此注册（否则模型不可见） | **必须** |
| `config/conf.ini` | 外部配置（数据库、各模块参数） | 新环境时 |
| `apps/base/baseviews.py` | ViewSet 基类与统一响应封装 | 仅扩展基类时 |
| `apps/base/models.py` | 抽象 Model 基类 | 仅扩展字段时 |
| `settings/settings.py` | Django 核心配置（**注意：不需要改 `INSTALLED_APPS`**） | 极少 |
| `utils/configure.py` | INI 配置解析 | 一般不修改 |
| `utils/authentication.py` | JWT 认证实现 | 一般不修改 |
| `utils/crypto.py` | 凭据可逆加解密 | 一般不修改 |
| `utils/pagination.py` | 分页工具 | 一般不修改 |
| `utils/logger.py` | 统一日志 | 一般不修改 |
| `utils/exception.py` | 全局异常处理 | 一般不修改 |
| `utils/background.py` | 后台任务提交入口 | 一般不修改 |
| `utils/test_runner.py` | 测试运行器：把 `sql/pg_struct.sql` 灌进测试库（本项目无 migration） | 表结构管理方式变更时 |
| `utils/bsa.py` | 平台底座客户端（**当前未使用**，见 [deployment.md](deployment.md) 附录） | 一般不修改 |
| `sql/pg_struct.sql` / `sql/patch.sql` | 全量结构 / 增量补丁 | 表结构变更时 |
| `docker-compose.yml` / `Dockerfile` | **实际部署方式**（见 [deployment.md](deployment.md)） | 部署形态变更时 |
| `right_config.json` | 平台菜单注册配置（**当前未使用**） | 接入平台时 |

## 7. AI 开发约束加载流程

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

## 8. 已知问题

以下为已知问题，尚未处理：

- **`src/config/conf.ini` 已被 git 跟踪且含真实数据库地址与口令**，违反 `.ai-harness/rules/security.md` 的凭据管理要求。应改为提交 `conf.ini.example`、将 `conf.ini` 加入 `.gitignore`，并轮换已泄露口令。**数据源加密密钥与模型 API Key 的加密密钥也写在该文件中，同样存在泄露风险**
- `package.py` 的注释中含一段 GitLab OAuth2 令牌明文，已进入 git 历史，应轮换
- `settings/settings.py` 的 `SECRET_KEY` 硬编码，`JWT_SECRET` 为空字符串
- `apps/test/` 缺少 `tests.py` 与 `filters.py`，且 `MavenEnforcerRules` 直接继承 `models.Model`（违反脚手架规则）。作为示例模块它本身不合规，请勿照抄
- 所有模型未显式声明主键类型，`manage.py check` 会报 12 条 `models.W042` 警告

## 附录：脚手架示例模块 `apps/test`

`apps/test/` 是脚手架自带的示例，演示各层协作方式，可作为参考——但注意上文「已知问题」中它的两处不合规。

| 层 | 类 | 说明 |
|----|----|------|
| Model | `MavenEnforcerRules` | 数据模型 |
| Serializer | `MavenEnforcerRuleSerializer` | `ModelSerializer`，列表查询、格式化时间、枚举 label |
| Serializer | `CreateBannedDependenciesSerializer` | 普通 `Serializer`，含 `validate()` 跨字段校验 |
| ViewSet | `TestView` | 继承 `AnyLogin`，标准列表（分页 + 软删除过滤）+ 自定义 Action |

注册的接口（`SYS_NAME = 'my-dba'`，注意这个模块用的是 `api/v1` 而非 `v1`）：

- `GET  /my-dba/api/v1/test` — 列表
- `POST /my-dba/api/v1/test` — 创建
- `GET  /my-dba/api/v1/test/{id}` — 详情
- `POST /my-dba/api/v1/test/banned` — 自定义 Action
- `GET  /my-dba/api/test` — 函数视图示例

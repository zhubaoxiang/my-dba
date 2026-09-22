<!-- OPENSPEC:START -->
# OpenSpec Instructions

These instructions are for AI assistants working in this project.

Always open `@/openspec/AGENTS.md` when the request:
- Mentions planning or proposals (words like proposal, spec, change, plan)
- Introduces new capabilities, breaking changes, architecture shifts, or big performance/security work
- Sounds ambiguous and you need the authoritative spec before coding

Use `@/openspec/AGENTS.md` to learn:
- How to create and apply change proposals
- Spec format and conventions
- Project structure and guidelines

Keep this managed block so 'openspec update' can refresh the instructions.

<!-- OPENSPEC:END -->

# CLAUDE.md

This file provides guidance to Claude Code when working with code in this repository.

## 协作约定

- Skill 文件（`.ai-harness/skills/*/SKILL.md`）必须以中文撰写，包括 frontmatter 的 `description` 字段和代码示例中的注释
- Harness 规则文件（`.ai-harness/rules/*.md`）和 Prompt 文件（`.ai-harness/prompts/*.md`）同样以中文为主
- 搜索所有文件时，必须限定在当前项目根目录下（`./.ai-harness/rules/`），禁止跨项目引用其他工作目录中的规则
- 项目知识库为根目录 `wiki/`，用 wiki 系 skill 维护：`wiki-init` 初始化（project-init 自动）、`wiki-ingest`/`wiki-update` 摄入、`wiki-query` 检索

## 项目概述

我的数据库管理专家后端服务，面向**开发与测试人员**（不是 DBA 运维工具）：帮其看懂被管理库的表结构与隐患、用自然语言完成建表与查询、审查 SQL 的规范与性能、解答数据库相关问题。Django 3.2 + DRF，对接 BSA 底座平台。PostgreSQL 数据库，JWT 认证，自定义响应格式，软删除模型。

## 常用命令

```bash
# 启动开发服务器（默认使用 settings.local）
python manage.py runserver

# 指定环境启动
ENV_TYPE=dev python manage.py runserver
ENV_TYPE=prod python manage.py runserver
ENV_TYPE=test python manage.py runserver

# 运行测试
python manage.py test
python manage.py test apps.test.tests.GoodsTestCase

# DDL 更新（禁止 Django migration，DDL 写 sql/pg_struct.sql）
# 新建/修改表结构 → 在 sql/pg_struct.sql 中编写 DDL

# 生产部署
gunicorn -c settings/gunicorn.py config.wsgi:application
```

## 架构概要

- 配置层级：`settings/settings.py` → `{local|dev|prod|test}.py` → `conf.ini`（`Configure` 单例）
- 认证：`JwtAuthentication`（`utils/authentication.py`），填充 `AuthedUser`
- 响应：`baseviews.ResponseOK/ResponseError/ResponseBadRequest/ResponseForbidden/ResponseNotFound/ResponseExpectationFailed`（code 2000/5000/4000/4003/4004/0017）
- 模型：继承 `AbstractTimeFiledModel`（`apps/base/models.py`），软删除 `is_deleted`
- 分页：`pagination.paginate(self, queryset)` → `baseviews.ResponseOK(result)`
- BSA 对接：`BsaClient` 单例 + `hooks/install.py`

## Harness 规则引用

所有详细规则在 `.ai-harness/rules/` 中，以下为入口链接：

- [scaffold.md](.ai-harness/rules/scaffold.md) — 脚手架规范（模型继承、ViewSet基类、响应格式、分页、软删除、枚举、路由注册）
- [security.md](.ai-harness/rules/security.md) — 安全约束（凭据管理、输入验证、认证要求、日志安全、密码存储）
- [naming.md](.ai-harness/rules/naming.md) — 命名规范（类名、表名、ViewSet、Serializer、URL、枚举）
- [architecture.md](.ai-harness/rules/architecture.md) — 架构约束（模块位置、通用能力、跨模块调用、配置读取、依赖引入）
- [code-style.md](.ai-harness/rules/code-style.md) — 代码风格（格式化、注释、YAGNI、异常处理）

**违反 `.ai-harness/rules/` 中任何规则即不符合脚手架规范，必须在继续之前修复。**

## 脚手架检查清单摘要

| 检查项 | 规则 |
|--------|------|
| 模型继承 | `AbstractTimeFiledModel`，禁止 `models.Model` |
| ViewSet 继承 | `baseviews` 基类，禁止 `ModelViewSet` |
| 响应方式 | `baseviews.ResponseOK/etc`，禁止 `Response()` |
| 列表分页 | `pagination.paginate(self, queryset)` |
| 软删除 | `is_deleted=False` |
| 枚举 | `custom_enum.IntegerChoices`，禁止裸整数 |
| 路由注册 | `src/config/urls.py` 通过 `router.register()` |
| 已安装应用 | `settings/settings.py` `INSTALLED_APPS` |
| db_table | 显式指定，禁止自动生成 |
| Serializer | 查询用 `ModelSerializer`；创建用 `Serializer`+`validate()` |
| DDL 管理 | 禁止 Django migration，DDL 写 `src/sql/pg_struct.sql` |

详细规则见 [scaffold.md](.ai-harness/rules/scaffold.md)。

## ViewSet 基类选择

| 类 | 权限 | 适用场景 |
|---|------|---------|
| `AnyLogin` | 无认证 | 公开接口 |
| `BaseView` / `OperatorView` | `IsAuthenticated` | 需登录 |
| `SuperUserView` | `IsAdminUser` | 管理员 |

## 新模块模板代码

### models.py

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

### serializers.py

```python
from rest_framework import serializers
from . import models


class YourSerializer(serializers.ModelSerializer):
    create_time = serializers.DateTimeField(format='%Y-%m-%d %H:%M:%S')

    class Meta:
        model = models.YourModel
        exclude = ['update_time']


class YourCreateSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=64)

    def validate(self, attrs):
        return attrs
```

### views.py

```python
from apps.base import baseviews
from utils import pagination
from . import models, serializers


class YourView(baseviews.BaseView):
    queryset = models.YourModel.objects.all()
    serializer_class = serializers.YourSerializer
    pagination_class = pagination.StandardPagination

    def list(self, request, **kwargs):
        queryset = self.get_queryset().filter(is_deleted=False).order_by('-id')
        result = pagination.paginate(self, queryset)
        return baseviews.ResponseOK(result)
```

### URL 注册（config/urls.py）

```python
from apps.your_module import views as your_views

router.register(rf'{SYS_NAME}/v1/your_module', your_views.YourView, basename='your_module')
```
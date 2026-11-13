<div align="center">

# 我的数据库管理专家（my-dba）

**面向开发与测试人员的数据库助手**

[![Python](https://img.shields.io/badge/Python-3.11.9-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![Django](https://img.shields.io/badge/Django-3.2-092E20?logo=django&logoColor=white)](https://www.djangoproject.com/)
[![Vue](https://img.shields.io/badge/Vue-3-4FC08D?logo=vuedotjs&logoColor=white)](https://vuejs.org/)
[![Element Plus](https://img.shields.io/badge/Element%20Plus-2.8-409EFF)](https://element-plus.org/)
[![Docker Compose](https://img.shields.io/badge/Docker%20Compose-2496ED?logo=docker&logoColor=white)](https://docs.docker.com/compose/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

把你要用的库接进来，就能看懂表结构、发现设计隐患、写出正确的 SQL，并随时问数据库相关的问题。

<img width="2756" height="1268" alt="首页截图" src="https://github.com/user-attachments/assets/66edbc66-8ebc-4336-a4d6-9f1b8fb21c10" />

</div>

> 本文档只讲**项目定位、路线图与上手步骤**。各模块的架构、API、配置与限制见 [文档索引](#文档索引)。

## 目录

- [目标使用者与边界](#目标使用者与边界)
- [技术栈](#技术栈)
- [能力与路线图](#能力与路线图)
- [快速开始](#快速开始)
- [目录结构](#目录结构)
- [文档索引](#文档索引)
- [License](#license)

## 技术栈

| 层 | 选型 |
|----|------|
| 后端 | Python 3.11 · Django 3.2 · Django REST Framework |
| 前端 | Vue 3 · Vite 5 · Element Plus 2 · Pinia |
| 数据库 | PostgreSQL（业务库）· Qdrant（向量检索） |
| 部署 | Docker Compose（前端 nginx + gunicorn + PostgreSQL + Qdrant） |
| 工程化 | uv · ruff · OpenSpec |

## 目标使用者与边界

**主要使用者是写代码和测功能的人；DBA 也可以拿它做辅助**——例如看一张不熟悉的表长什么样、快速验一条 SQL 的规范问题。

典型场景：接了需求要动某张表，先看看它长什么样、有哪些索引、字段类型有没有坑；自己写的 SQL 到底对不对、会不会慢；遇到一个数据库概念或报错想快速弄清楚。

| 做 | 不做 |
|----|------|
| 帮使用者**看懂**库表结构与隐患 | 备份恢复、容量规划、主从运维 |
| 帮使用者**写出**正确的建表语句与 SQL | 数据库巡检、值班告警、SLA 管理 |
| 帮使用者**判断**自己 SQL 的规范与性能 | 慢查询平台、SQL 上线审批流 |
| 在**测试环境**新建独立测试库并造数（限自己创建的数据源，生产禁用） | 写入任何已有数据库 |
| 解答数据库相关的问题 | 生产库变更执行 |

## 能力与路线图

四条能力围绕使用者的三条诉求展开，另有 2 项支撑性能力：

| 能力 | 使用者的诉求 | 状态 |
|------|-------------|------|
| **分析数据库表** | 「这个库/表长什么样？有没有坑？」 | ✅ 已实现（[文档](docs/datasource.md)） |
| **知识问答**（RAG） | 「这个报错 / 概念 / 用法是什么？」 | ✅ 已实现（[文档](docs/knowledge-qa.md)） |
| **分析 SQL** | 「我这条 SQL 写得对吗？会不会慢？」 | ✅ 已实现（[文档](docs/sql-analysis.md)） |
| **自然语言转建表语句 / SQL** | 「按我说的建张表 / 写条查询」 | ✅ 已实现（[文档](docs/nl2sql.md)） |
| 分析规则注册表（支撑） | 让分析规则可配置、可开关 | 🚧 进行中（[文档](docs/datasource.md#分析规则注册表)） |
| **DDL 评审**（支撑） | 「这条建表 / 改表语句能不能安全执行？」 | ✅ 已实现（[文档](docs/ddl-review.md)） |
| **造数**（支撑） | 「给我一套能直接用的测试数据」 | ✅ 已实现（[测试库](docs/test-db-provisioning.md) / [数据文件](docs/test-data-export.md)；可选手填表结构） |

已确定的技术选型：数据源支持 PostgreSQL + MySQL；RAG 向量存储用独立的 Qdrant 服务；LLM 走公有云 API。
<img width="3840" height="1890" alt="image" src="https://github.com/user-attachments/assets/504869e7-da23-4175-9dcd-7d69dc5091ac" />

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

# 2. 初始化数据库结构（二选一，本项目禁止 Django migration）
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

### 登录配置

所有业务接口要求登录。上线前必须在 `src/config/conf.ini` 的 `[auth]` 段设置随机 `jwt_secret`；本地账号可用 `python manage.py createsuperuser` 创建，域账号可通过 LDAP 登录。详见 [docs/auth.md](docs/auth.md)。

### 用上知识问答还需两步

知识问答依赖两个外部条件，**不满足时其余功能照常可用**：

1. **Qdrant 服务**（`docker-compose.yml` 已含）——向量检索的载体
2. **模型 API 可达 + 一条生效的模型配置**——在「模型配置」页添加对话模型与嵌入模型

细节见 [knowledge-qa.md 的「部署前提」](docs/knowledge-qa.md#部署前提)。

## 目录结构

```
my-dba/
├── README.md                     # 本文件
├── docs/                         # 模块文档（见下方索引）
│
├── Dockerfile                    # 后端镜像（依赖层 / 代码层分开）
├── docker-compose.yml            # 容器编排（frontend / web / postgres / qdrant）
├── package.py                    # 前后端镜像构建脚本（镜像名取自 compose）
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
├── static/                       # 前端工程（Vue3 + Vite；Dockerfile + nginx 配置，产物在镜像内构建）
└── src/                          # 后端代码根目录
```

`src/` 的内部结构（`apps/` 各模块、`utils/` 各通用能力）见 [architecture.md 的「代码分层与目录」](docs/architecture.md#代码分层与目录)。

## 文档索引

| 文档 | 内容 |
|------|------|
| [docs/roadmap.md](docs/roadmap.md) | 功能候选方向：优先级、价值判断、现状基础、边界与明确不建议项（**候选清单，非承诺排期**） |
| [docs/overview.md](docs/overview.md) | 首页：定位与页面结构、**取数口径**（问题数取最新快照、没有数据 ≠ 没有问题）、指标与趋势的呈现、就绪检查的边界、排序与分块兜底 |
| [docs/datasource-metrics.md](docs/datasource-metrics.md) | 数据源指标采集：采什么、怎么采（独立进程 + 守护）、保留策略、取数代价、已知限制 |
| [docs/architecture.md](docs/architecture.md) | 技术栈与依赖、配置层级、**模块注册机制**、代码分层、接口约定（统一响应 / 分页）、认证与鉴权、后台任务 |
| [docs/datasource.md](docs/datasource.md) | 分析数据库表：元数据采集、健康分析规则、API、配置、安全说明、已知限制 |
| [docs/test-db-provisioning.md](docs/test-db-provisioning.md) | 造数 · 测试库：安全边界、近似结构与造数、简单语义推断、API、配置、已知限制 |
| [docs/test-data-export.md](docs/test-data-export.md) | 造数 · 数据文件：生成可下载的 SQL 数据文件、格式、限制与已知边界 |
| [docs/knowledge-qa.md](docs/knowledge-qa.md) | 知识问答：**架构与数据流**、摄入链路、问答链路、关键设计决策、数据模型、API、配置、实测踩坑 |
| [docs/sql-analysis.md](docs/sql-analysis.md) | 分析 SQL：能力与规则清单、**执行边界**（只读真跑 / DML·DDL 只 EXPLAIN）、模型解读分区、API、配置 |
| [docs/nl2sql.md](docs/nl2sql.md) | 说人话 → SQL 语句：**生成 → 校验 → 自修一轮**的机制、**生成与执行是两件事**、校验只覆盖结构这一限定、表范围的取舍、边界与降级、API |
| [docs/ddl-review.md](docs/ddl-review.md) | DDL 评审：顺序预演、**只报本次新引入/加重**、语句风险分区、规则作用域、支持范围、API、已知限制 |
| [docs/auth.md](docs/auth.md) | 登录与认证：JWT、本地账号优先 + LDAP 兜底、角色、配置、API、已知限制 |
| [docs/development.md](docs/development.md) | 开发规范：新增模块、Model/ViewSet/Serializer/URL、日志、SQL、代码风格、测试与质量检查、已知问题 |
| [docs/deployment.md](docs/deployment.md) | 部署：镜像构建、compose 编排的两个坑、数据库初始化、生产运行方式、平台化部署备查 |

## License

本项目采用 [MIT License](LICENSE) 开源。


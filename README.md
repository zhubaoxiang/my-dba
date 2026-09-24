# 我的数据库管理专家（my-dba）

**面向开发与测试人员**的数据库助手。把你要用的库接进来，就能看懂表结构、发现设计隐患、写出正确的 SQL，并随时问数据库相关的问题。

后端 Django 3.2 + DRF，前端 Vue 3 + Element Plus，数据库 PostgreSQL。**通过 docker compose 部署**（对外路径前缀 `my-dba`）。

> 本文档只讲**项目定位、路线图与上手步骤**。各模块的架构、API、配置与限制见 [文档索引](#文档索引)。

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
| **分析数据库表** | 「这个库/表长什么样？有没有坑？」 | ✅ 已实现（[文档](docs/datasource.md)） |
| **知识问答**（RAG） | 「这个报错 / 概念 / 用法是什么？」 | ✅ 已实现（[文档](docs/knowledge-qa.md)） |
| **分析 SQL** | 「我这条 SQL 写得对吗？会不会慢？」 | ⬜ 未开始 |
| **自然语言转建表语句 / SQL** | 「按我说的建张表 / 写条查询」 | ⬜ 未开始 |
| 分析规则注册表（支撑） | 让分析规则可配置、可开关 | 🚧 进行中（[文档](docs/datasource.md#进行中的分析规则注册表)） |

已确定的技术选型：数据源支持 PostgreSQL + MySQL；RAG 向量存储用独立的 Qdrant 服务；LLM 走公有云 API。

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
├── Dockerfile                    # 镜像构建（依赖层 / 代码层分开）
├── docker-compose.yml            # 容器编排（web / postgres / qdrant）
├── package.py / service.json / service-mgr-tool/   # 平台化打包链路（未使用）
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
└── src/                          # 后端代码根目录
```

`src/` 的内部结构（`apps/` 各模块、`utils/` 各通用能力）见 [architecture.md 的「代码分层与目录」](docs/architecture.md#代码分层与目录)。

## 文档索引

| 文档 | 内容 |
|------|------|
| [docs/architecture.md](docs/architecture.md) | 技术栈与依赖、配置层级、**模块注册机制**、代码分层、接口约定（统一响应 / 分页）、认证与鉴权、后台任务 |
| [docs/datasource.md](docs/datasource.md) | 分析数据库表：元数据采集、健康分析规则、API、配置、安全说明、已知限制 |
| [docs/knowledge-qa.md](docs/knowledge-qa.md) | 知识问答：**架构与数据流**、摄入链路、问答链路、关键设计决策、数据模型、API、配置、实测踩坑 |
| [docs/development.md](docs/development.md) | 开发规范：新增模块、Model/ViewSet/Serializer/URL、日志、SQL、代码风格、测试与质量检查、已知问题 |
| [docs/deployment.md](docs/deployment.md) | 部署：镜像构建、compose 编排的两个坑、数据库初始化、生产运行方式、平台化部署备查 |

配套约定：开发硬性约束在 [`.ai-harness/rules/`](.ai-harness/rules/)（**违反即阻断**）；变更流程走 [`openspec/`](openspec/)（`/openspec:proposal` → `/openspec:apply` → `/openspec:archive`）。


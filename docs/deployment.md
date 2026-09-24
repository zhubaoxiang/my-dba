# 部署

**部署方式：docker compose。**

> ⚠️ **只能部署在内网。** 所有接口继承 `AnyLogin`，系统内不做任何认证与角色校验，**服务可达即可读写数据源凭据、模型 API Key 与知识库文档，并借「连接测试」探测内网**。宿主网络必须可信，不要直接暴露到公网或不可信网络。完整说明见 [architecture.md 的「认证与鉴权」](architecture.md#认证与鉴权)。

## 镜像构建

```bash
# Dockerfile 已做分层：依赖层在前，改代码只重建代码层，依赖不重装
docker build -t my-dba/my-dba:V1.0R01F00 .
```

几个需要知道的点：

- **基础镜像 `python:3.11.9-slim-bullseye`**，apt 与 pip 均走阿里云镜像。注意 bullseye 已归档，源必须写 `debian-archive/debian` 并放宽 `Check-Valid-Until` 校验，否则 `apt-get update` 直接失败
- **`ENV_TYPE=test` 在镜像里写死**，因此容器运行时读的是 `conf.ini` 的 `[db_test]` 段。改成部署环境对应的段落需要重建镜像或覆盖该环境变量
- 编译类工具（`build-essential` 等）仅在 pip 构建 wheel 时需要，装完即卸以压体积
- 构建上下文由 `.dockerignore` 排除 `venv` / `node_modules` / `.git` 等（约 220M）
- 镜像名与 tag 由 `docker-compose.yml` 的 `image:` 决定，构建时用同一个名字打 tag

## 启动

```bash
docker compose up -d       # 启动
docker compose logs -f     # 查看日志
docker compose down        # 停止
```

`docker-compose.yml` 里三个服务的要点：

| 服务 | 说明 |
|------|------|
| `web` | `network_mode: "host"`，`restart: always`。容器内由 gunicorn 监听 **8080** |
| `postgres` | 业务库。用 `network_mode: "host"`，应用侧连 `127.0.0.1:5432` |
| `qdrant` | 向量库（知识问答的检索载体）。REST 6333 / gRPC 6334 |

### 两个「不要照抄」的坑

**1. 数据卷必须引用已有的，不能新建**

`pg_data` 用的是 `external: true` + `${PG_VOLUME_NAME}`。compose 只引用不创建——这是刻意的，避免手滑起出一个空库。上线前先：

```bash
docker volume ls                          # 找到当前 PG 实际在用的卷名
docker inspect <现有容器>                  # 核对 image 完整 tag、卷名、POSTGRES_DB/USER、端口
```

**2. 基础镜像 tag 必须显式写死，且必须是 alpine 变体**

数据目录是 Alpine/musl 的：

- Alpine 的 `postgres` 用户是 **uid 70**、Debian 是 **uid 999**。换基础镜像会让数据目录属主不匹配，**PostgreSQL 直接启动失败**（端口能连上但对任何输入都不作答）
- musl 与 glibc 的**排序规则不同**。库虽记录 `datcollate = en_US.utf8`，实际按 C 字节序排序；换到 glibc 后排序规则会真实改变，而 `datcollversion` 为 NULL 意味着 PostgreSQL **不会报 collation mismatch 警告**——文本索引可能静默失效，表现为查询结果错乱且无任何提示

历史上这里用过默认的 `latest`/`alpine` 变动导致过故障，因此现在显式写 `postgres:17-alpine`。同理 `qdrant:latest` **上线前也要固定到具体版本**。

### 必需的环境变量

| 变量 | 用途 |
|------|------|
| `POSTGRES_PASSWORD` | 业务库口令。**不要写进 compose 文件**，在 `.env` 或部署环境变量里给 |
| `PG_VOLUME_NAME` | 已存在的 PG 数据卷名 |

## 依赖的外部服务

| 依赖 | 要求 | 缺失时的表现 |
|------|------|-------------|
| **出网通道** | 模型走公有云 API，服务需能访问；内网有模型代理则把 `base_url` 指向代理 | 问答功能全部超时（不易定位） |
| **Qdrant** | compose 已含；应用侧连 `127.0.0.1:6333` | 摄入与检索失败（有明确报错） |
| **嵌入模型** | 需单独配置。不少网关只提供对话模型 | 「仅通用模型」仍可用，检索时报可操作的配置提示 |

详见 [knowledge-qa.md 的「部署前提」](knowledge-qa.md#部署前提)。元数据采集不需要出网。

## 数据库结构初始化

**本项目禁止 Django migration**，结构变更走 SQL 文件：

```bash
# 新建库：一次建全
psql "postgresql://<用户>:<密码>@<主机>:<端口>/<库名>" -f src/sql/pg_struct.sql

# 已有库：执行增量补丁
psql "postgresql://<用户>:<密码>@<主机>:<端口>/<库名>" -f src/sql/patch.sql
```

规则见 [development.md 的「SQL 规范」](development.md#3-sql-规范)。

## 生产运行方式

容器内 `src/start.sh` 启动 gunicorn（配置在 `src/settings/gunicorn.py`）：

- 绑定 `0.0.0.0:8080`，**3 个 worker**，每 worker 6 线程
- 访问日志与错误日志共用 `src/logs/gunicorn.log`，按天切割保留 7 天
- 脚本末尾 `tail -f` 让容器保持前台运行

> **多 worker 的后果**：后台任务是**进程内内存队列**（见 [architecture.md 的「后台任务」](architecture.md#后台任务)）。采集与文档摄入的任务会在**接收该请求的那个 worker 进程**内执行；进程重启后队列中未执行的任务会丢失，需重新触发。任务状态以数据库为准。

---

## 附录：平台化部署（当前未使用）

本项目**不在原平台底座上部署**，已改用 docker compose。以下为平台相关资产：`package.py` 是**仍在使用的统一编译打包脚本**（构建镜像 → 生成 chart 包 → 生成 dat 包），其余目前**未接入、未被调用**，保留仅为将来可能的平台化部署备查。**修改业务代码时不要依赖它们。**

| 文件 / 目录 | 用途 |
|------------|------|
| `package.py` | **统一编译打包脚本**。取件路径（`service-mgr-tool/`、`service.json`）写在脚本开头 |
| `src/right_config.json` | 菜单注册配置（`children` 数组） |
| `src/hooks/install.py` / `uninstall.py` | 安装/卸载钩子：菜单注册与注销、SQL 初始化与数据清理。二者以子进程方式调用已删除的 `scripts/bsa_register_menu.py`，将来若启用平台化部署需连同该脚本一并恢复 |
| `src/utils/bsa.py` | 平台底座客户端 |

打包流程：

```bash
python3 package.py --clean
git pull origin main
python3 package.py
```

底座客户端用法（原设计：认证信息从 `CONF_ATTR` 读 `common_sessionid` 与 `common_csrftoken`，构造请求头代理到平台）：

```python
from utils.bsa import bsa_client

info = bsa_client.get_service_component_info("b-vuln-backend")
```

> ⚠️ `package.py` 的注释中含一段 GitLab OAuth2 令牌明文，已进入 git 历史，应轮换（见 [development.md 的「已知问题」](development.md#8-已知问题)）。

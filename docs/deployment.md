# 部署

**部署方式：docker compose。** 前端由独立 nginx 容器托管，后端 gunicorn 只提供 API。

> **登录与配置**：所有业务接口要求有效 JWT；服务仍建议只部署在内网或受控网络。上线前必须在 `conf.ini` 的 `[auth]` 段设置随机 `jwt_secret`；LDAP 可选。详见 [auth.md](auth.md)。

## 部署形态

```
浏览器 :8765（纯 HTTP，始终可用）
        或 :443（HTTPS；80 自动跳转，受 NGINX_HTTPS 控制）
  → frontend（nginx）
      ├─ /static/*、/login、/home …  → 前端静态资源
      └─ /my-dba/*                   → 127.0.0.1:8080
  → web（gunicorn）:8080
      ├─ PostgreSQL :5432
      └─ Qdrant :6333
```

| 服务 | 网络 | 端口 | 说明 |
|------|------|------|------|
| `frontend` | host | 80 / 443 / 8765 | 前端 nginx；80 跳 443（受 `NGINX_HTTPS` 控制），8765 为常驻 HTTP 入口，静态资源 + `/my-dba` 反代 |
| `web` | host | 8080 | Django + gunicorn；同时启动指标采集进程 |
| `postgres` | host | 5432 | 业务库；数据卷为已有卷（迁移）或新建卷（首次部署） |
| `qdrant` | bridge | 6333 / 6334 | 知识问答向量库 |

> `frontend` / `web` / `postgres` 都使用 host 网络，所以 nginx 直接反代 `127.0.0.1:8080`，应用直接连 `127.0.0.1:5432`。

## 一、准备配置

### 1. `conf.ini` 的生产段

在 `src/config/conf.ini` 填写生产库：

```ini
[db_prod]
name = <生产库名>
address = <生产库地址>
username = <生产库用户>
password = <生产库口令>
port = 5432
```

并确认：

```ini
[auth]
jwt_secret = <随机值>
token_ttl_hours = 8
ldap_server = <LDAP 地址，可留空>
ldap_port = 389
ldap_domain = <UPN 后缀，可留空>
ldap_connect_timeout = 10
ldap_use_ssl = false
```

生成 `jwt_secret`：

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

> `conf.ini` 已通过 compose 只读挂载到 `web` 容器：`./src/config/conf.ini:/home/src/config/conf.ini:ro`。
> 改完执行 `docker compose restart web` 即可，不需要重建镜像。
> **生产库口令、LDAP 地址与 JWT 密钥不要提交到 git；提交前确认工作区只包含占位值。**

### 2. 环境变量

复制模板并按实际环境填写（模板见根目录 `.env.example`）：

```bash
cp .env.example .env
```

```ini
POSTGRES_PASSWORD=<与 [db_prod] 的 password 一致>
PG_VOLUME_NAME=<已有 PG 数据卷名>
POSTGRES_DB=<与 [db_prod] 的 name 一致>
POSTGRES_USER=<与 [db_prod] 的 username 一致>
ENV_TYPE=prod
NGINX_HTTPS=auto
```

`.env` 已被 `.gitignore` 与 `.dockerignore` 排除，不会进仓库或镜像。`ENV_TYPE=prod` 决定后端读取 `conf.ini` 的 `[db_prod]` 段。

> `POSTGRES_PASSWORD` 只在**首次初始化空卷**时生效；已有数据卷的密码以库内实际值为准，`[db_prod].password` 必须填实际口令。

> **首次部署**没有已有数据卷时，先手工建一个空卷，再把它填进 `PG_VOLUME_NAME`：
>
> ```bash
> docker volume create my-dba-pg-data
> ```
>
> 从已有部署**迁移**时，用 `docker volume ls` / `docker inspect <现有容器>` 找到实际卷名，切勿新建空卷。

## 二、构建镜像

在项目根目录执行，一次构建前后端镜像：

```bash
python package.py
```

镜像名与 tag 取自 `docker-compose.yml` 的 `web` / `frontend` 服务，默认：

- 后端 `my-dba-backend:1.0.0`，上下文为项目根目录
- 前端 `my-dba-frontend:1.0.0`，上下文为 `static/`

只重建其中一端：

```bash
python package.py --only backend
python package.py --only frontend
```

前端镜像在 Node 阶段执行 `npm ci && npm run build`，再由 nginx 托管；**不依赖宿主机上的 `static/dist`**。

> 认证改动新增了 `ldap3` 依赖和登录代码，升级时必须重新构建后端镜像；`docker compose up -d` 不会自动重建。

## 三、启动

**首次部署**（空数据卷）：先只启动数据库，等 `pg_isready` 通过后按第四节导入表结构，再回来启动全栈。

```bash
docker compose up -d postgres
docker compose logs -f postgres
```

导入完成后：

```bash
docker compose up -d
```

**已有数据库**（迁移 / 升级）可直接启动全部服务：

```bash
docker compose up -d
docker compose logs -f web postgres frontend
```

compose 已配置：

- `postgres` 有 `pg_isready` 健康检查
- `web` 等 `postgres` 健康后再启动
- `frontend` 等 `web` 健康后再启动

访问 `http://<服务器>:8765/`；启用 HTTPS 时访问 `https://<服务器>/`。

> 如果服务器的 80 / 443 端口已被其他 nginx 占用，二选一：停掉原服务，或修改 `static/nginx/conf/conf.d/http.conf` / `https.conf` 的 `listen` 端口并重新构建前端镜像。也可以保留宿主机 nginx，直接让它托管 `static/dist` 并反代 `/my-dba` 到 `127.0.0.1:8080`。

## 四、数据库初始化（手动）

compose 的 postgres 服务**不会自动执行**项目 SQL。数据库健康后进入容器执行，宿主机不需要安装 `psql`：

```bash
# 确认库已存在（POSTGRES_DB 在首次初始化时自动创建，库名需与 [db_prod].name 一致）
docker exec -it my-dba-postgres psql -U <POSTGRES_USER> -l

# 库名与 POSTGRES_DB 不一致时才需要手工建库
docker exec -it my-dba-postgres createdb -U <POSTGRES_USER> <库名>

# 全新库：一次建全
docker exec -i my-dba-postgres psql -v ON_ERROR_STOP=1 -U <POSTGRES_USER> -d <库名> < src/sql/pg_struct.sql

# 已有库：执行增量补丁
docker exec -i my-dba-postgres psql -v ON_ERROR_STOP=1 -U <POSTGRES_USER> -d <库名> < src/sql/patch.sql
```

> `-v ON_ERROR_STOP=1` 不能省：默认 psql 遇到错误会继续执行，最后仍返回 0。
>
> Windows 的 PowerShell/cmd 不支持 `<` 重定向时，改为先复制再执行：
>
> ```bash
> docker cp src/sql/pg_struct.sql my-dba-postgres:/tmp/pg_struct.sql
> docker exec -it my-dba-postgres psql -v ON_ERROR_STOP=1 -U <POSTGRES_USER> -d <库名> -f /tmp/pg_struct.sql
> ```

容器内通过 Unix socket 连接，默认 `trust`，不需要再输口令。

本次登录认证新增了 `auth_user` 表，已有库务必执行 `patch.sql`，否则本地账号登录和 `createsuperuser` 都会失败。

创建本地管理员：

```bash
docker exec -e DJANGO_SETTINGS_MODULE=settings.prod -it my-dba-backend \
  sh -lc "cd /home/src && python manage.py createsuperuser"
```

LDAP 用户不需要本地账号；本地账号优先，只有本地不存在该用户时才走 LDAP。

## 五、前端与 nginx

前端镜像基于 `static/Dockerfile`，nginx 配置在 `static/nginx/conf/`：

- `nginx.conf`：全局配置，加载 `conf.d/*.conf`
- `conf.d/http.conf`：8765 纯 HTTP 入口，始终启用
- `conf.d/https.conf`：80 跳 443 与 443 SSL，可关闭
- `snippets/locations.inc`：HTTP / HTTPS 共用的路由

共用路由包括：

- `/` 与前端路由（`/login`、`/home`）回落到 `index.html`
- `/static/*` 直接返回 Vite 产物并缓存 30 天
- `/my-dba/*` 反代 `127.0.0.1:8080`
- 知识问答 SSE 单独关闭了 `proxy_buffering`，避免流式回答被攒住
- 上传限制 `client_max_body_size 25m`

HTTPS 由 compose 的 `NGINX_HTTPS` 控制：

| 值 | 行为 |
|----|------|
| `auto`（默认） | 证书存在才启用 443 与 80 跳转 |
| `on` | 强制启用（缺证书会启动失败） |
| `off` | 关闭 443 与 80，仅保留 8765 的 HTTP |

证书放在宿主机，compose 只读挂载到容器的 `/etc/nginx/cert`：

```text
static/nginx/cert/server.crt   # 完整证书链
static/nginx/cert/server.key   # 私钥，权限 600
```

- 证书目录被 `static/.gitignore` 忽略，不会进 git；也被 `.dockerignore` 排除，不会进镜像
- `auto` 下缺证书不会导致 nginx 退出，只是不监听 443/80
- 如果不用容器做 TLS，可以设置 `NGINX_HTTPS=off`，改由宿主机已有 nginx 终止 TLS

## 六、运行与运维

容器内 `src/start.sh` 启动 gunicorn（配置在 `src/settings/gunicorn.py`）：

- 监听 `0.0.0.0:8080`，3 个 worker，每 worker 6 线程；单请求超时 300 秒（同步预置可跑到 `bulk_threshold`）
- 访问日志与错误日志在 `src/logs/gunicorn.log`，按天切割保留 7 天
- 同时启动独立指标采集进程，日志在 `src/logs/metrics.log`
- 同时启动大数据预置 worker（`jobs/provisioning_worker.py`），日志在 `src/logs/provisioning_worker.log`；`sh` 会转发 SIGTERM，容器停止宽限 60 秒
- 大数据模式使用 MySQL `LOAD DATA LOCAL INFILE` 时需要服务端开启 `local_infile`；否则自动回退多行 INSERT

常用命令：

```bash
docker compose ps
docker compose logs -f web
docker compose restart web          # 改 conf.ini 后
docker compose down                 # 停止
```

> 多 worker 下后台任务是进程内内存队列，重启会丢未执行任务；任务状态以数据库为准。
> 流式问答占用 gunicorn 线程，并发上限约为 `3 × 6 = 18`，超出排队。

## 七、升级

```bash
git pull
python package.py
docker compose up -d
```

升级后按变更需要执行 `src/sql/patch.sql`。本次认证升级还需要确认 `[auth] jwt_secret` 已配置、`auth_user` 表已创建。

## 八、已知坑

### 数据卷：迁移引用已有卷，首次部署新建卷

`pg_data` 使用 `external: true` + `${PG_VOLUME_NAME}`，compose 不会自动创建：

- **首次部署**：先 `docker volume create <名字>`，再把名字填进 `PG_VOLUME_NAME`
- **从已有部署迁移**：用下面命令找到实际卷名，避免起出一个空库

```bash
docker volume ls
docker inspect <现有容器>
```

### PostgreSQL 必须用 alpine 变体且固定 tag

数据目录是 Alpine/musl 的：Alpine 的 `postgres` 用户是 uid 70、Debian 是 uid 999；换基础镜像会导致数据目录属主不匹配，PostgreSQL 启动失败。musl 与 glibc 的排序规则也不同，混用可能让文本索引静默失效。因此固定使用 `postgres:17-alpine`。

### qdrant 固定版本

`docker-compose.yml` 使用 `qdrant/qdrant:v1.19.1`；升级前先验证新版本的存储格式兼容性，不要改用 `latest`。

### 安全项

- `conf.ini` 仍被 git 跟踪；生产口令、LDAP 地址、`jwt_secret` 不应进入仓库
- `settings/settings.py` 的 `SECRET_KEY` 仍是硬编码
- `ALLOWED_HOSTS=["*"]`、`CORS_ORIGIN_ALLOW_ALL=True`，生产建议收窄
- `jwt_secret` 一旦进入 git 历史，共享仓库场景应轮换

---

## 附录：原平台化部署（当前未使用）

本项目**不在原平台底座上部署**，统一使用 docker compose。原 chart/dat 打包链路依赖的 `service.json`、`service-mgr-tool/`、`scripts/bsa_register_menu.py` 已删除，`package.py` 现在只负责构建前后端镜像。

> ⚠️ `package.py` 历史版本的注释中含一段 GitLab OAuth2 令牌明文，已进入 git 历史，应轮换（见 [development.md 的「已知问题」](development.md#8-已知问题)）。

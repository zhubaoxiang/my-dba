# 部署

**部署方式：docker compose。** 前端由独立 nginx 容器托管，后端 gunicorn 只提供 API。

> **登录与配置**：所有业务接口要求有效 JWT；服务仍建议只部署在内网或受控网络。上线前必须在 `conf.ini` 的 `[auth]` 段设置随机 `jwt_secret`；LDAP 可选。详见 [auth.md](auth.md)。

## 部署形态

```
浏览器 :443（80 自动跳转）
  → frontend（nginx）
      ├─ /static/*、/login、/home …  → 前端静态资源
      └─ /my-dba/*                   → 127.0.0.1:8080
  → web（gunicorn）:8080
      ├─ PostgreSQL :5432
      └─ Qdrant :6333
```

| 服务 | 网络 | 端口 | 说明 |
|------|------|------|------|
| `frontend` | host | 80 / 443 | 前端 nginx；80 跳 443，静态资源 + `/my-dba` 反代 |
| `web` | host | 8080 | Django + gunicorn；同时启动指标采集进程 |
| `postgres` | host | 5432 | 业务库；使用已有数据卷 |
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

在部署目录创建 `.env`：

```ini
POSTGRES_PASSWORD=<与 [db_prod] 的 password 一致>
PG_VOLUME_NAME=<已有 PG 数据卷名>
POSTGRES_DB=<与 [db_prod] 的 name 一致>
POSTGRES_USER=<与 [db_prod] 的 username 一致>
ENV_TYPE=prod
```

> `POSTGRES_PASSWORD` 只在**首次初始化空卷**时生效；已有数据卷的密码以库内实际值为准，`[db_prod].password` 必须填实际口令。

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

```bash
docker compose up -d
docker compose logs -f web postgres frontend
```

compose 已配置：

- `postgres` 有 `pg_isready` 健康检查
- `web` 等 `postgres` 健康后再启动
- `frontend` 等 `web` 健康后再启动

访问 `http://<服务器>/`。

> 如果服务器的 80 / 443 端口已被其他 nginx 占用，二选一：停掉原服务，或修改 `static/nginx/conf/nginx.conf` 的 `listen` 端口并重新构建前端镜像。也可以保留宿主机 nginx，直接让它托管 `static/dist` 并反代 `/my-dba` 到 `127.0.0.1:8080`。

## 四、数据库初始化（手动）

compose 的 postgres 服务**不会自动执行**项目 SQL。数据库健康后手动执行：

```bash
# 新库：一次建全
psql "postgresql://<用户>:<口令>@<主机>:<端口>/<库名>" -f src/sql/pg_struct.sql

# 已有库：执行增量补丁
psql "postgresql://<用户>:<口令>@<主机>:<端口>/<库名>" -f src/sql/patch.sql
```

本次登录认证新增了 `auth_user` 表，已有库务必执行 `patch.sql`，否则本地账号登录和 `createsuperuser` 都会失败。

创建本地管理员：

```bash
docker exec -e DJANGO_SETTINGS_MODULE=settings.prod -it my-dba-backend \
  sh -lc "cd /home/src && python manage.py createsuperuser"
```

LDAP 用户不需要本地账号；本地账号优先，只有本地不存在该用户时才走 LDAP。

## 五、前端与 nginx

前端镜像基于 `static/Dockerfile`，nginx 配置在 `static/nginx/conf/nginx.conf`：

- `/` 与前端路由（`/login`、`/home`）回落到 `index.html`
- `/static/*` 直接返回 Vite 产物并缓存 30 天
- `/my-dba/*` 反代 `127.0.0.1:8080`
- 知识问答 SSE 单独关闭了 `proxy_buffering`，避免流式回答被攒住
- 上传限制 `client_max_body_size 25m`

nginx 监听 80（跳转）和 443（SSL），证书放在宿主机：

```text
static/nginx/cert/server.crt   # 完整证书链
static/nginx/cert/server.key   # 私钥，权限 600
```

compose 已把该目录只读挂载到容器的 `/etc/nginx/cert`。注意：

- 证书目录被 `static/.gitignore` 忽略，不会进 git；也被 `.dockerignore` 排除，不会进镜像
- **启动前必须放好证书**，否则 nginx 会因加载证书失败而退出
- 如果不用容器做 TLS，可以去掉 compose 的证书挂载并调整 nginx 配置，改由宿主机已有 nginx 终止 TLS

## 六、运行与运维

容器内 `src/start.sh` 启动 gunicorn（配置在 `src/settings/gunicorn.py`）：

- 监听 `0.0.0.0:8080`，3 个 worker，每 worker 6 线程
- 访问日志与错误日志在 `src/logs/gunicorn.log`，按天切割保留 7 天
- 同时启动独立指标采集进程，日志在 `src/logs/metrics.log`

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

### 数据卷必须引用已有的

`pg_data` 使用 `external: true` + `${PG_VOLUME_NAME}`，避免手滑起出空库：

```bash
docker volume ls
docker inspect <现有容器>
```

### PostgreSQL 必须用 alpine 变体且固定 tag

数据目录是 Alpine/musl 的：Alpine 的 `postgres` 用户是 uid 70、Debian 是 uid 999；换基础镜像会导致数据目录属主不匹配，PostgreSQL 启动失败。musl 与 glibc 的排序规则也不同，混用可能让文本索引静默失效。因此固定使用 `postgres:17-alpine`。

### qdrant 不要长期使用 latest

`docker-compose.yml` 目前是 `qdrant/qdrant:latest`；上线前请固定到已验证的版本。

### 安全项

- `conf.ini` 仍被 git 跟踪；生产口令、LDAP 地址、`jwt_secret` 不应进入仓库
- `settings/settings.py` 的 `SECRET_KEY` 仍是硬编码
- `ALLOWED_HOSTS=["*"]`、`CORS_ORIGIN_ALLOW_ALL=True`，生产建议收窄
- `jwt_secret` 一旦进入 git 历史，共享仓库场景应轮换

---

## 附录：原平台化部署（当前未使用）

本项目**不在原平台底座上部署**，统一使用 docker compose。原 chart/dat 打包链路依赖的 `service.json`、`service-mgr-tool/`、`scripts/bsa_register_menu.py` 已删除，`package.py` 现在只负责构建前后端镜像。

> ⚠️ `package.py` 历史版本的注释中含一段 GitLab OAuth2 令牌明文，已进入 git 历史，应轮换（见 [development.md 的「已知问题」](development.md#8-已知问题)）。

## Context

「数据库智能体」共 4 个能力，本次只做第 4 个（数据源对接与库表分析），其余拆为独立 change：

| # | 能力 | 规划 change-id | 依赖本次 |
|---|------|---------------|---------|
| 1 | RAG 知识问答 | `add-rag-knowledge-qa` | 否 |
| 2 | 自然语言转建表语句/SQL | `add-nl2sql` | 是 |
| 3 | SQL 规范与性能分析 | `add-sql-analysis` | 是（性能分析部分） |
| 4 | 数据源对接与库表分析 | `add-datasource-catalog`（本次） | — |

已确定的外部决策：数据源类型 = PostgreSQL + MySQL；RAG 向量存储 = pgvector（本次不涉及）；LLM 接入 = 公有云 API（本次不涉及）。

约束条件：

- 技术栈固定 Django 3.2 + DRF + PostgreSQL；DDL 手写进 `src/sql/pg_struct.sql`，**禁止 Django migration**
- 必须遵守 `.ai-harness/rules/` 的脚手架、命名、安全、架构规范
- 目标库可能是生产库，采集不得造成压力，更不得写入
- 现有代码库中**没有任何** LLM / 向量 / 元数据采集相关代码，属全新能力

## Goals / Non-Goals

- Goals：把「配一个数据源 → 采集 → 看库表问题」这条链路跑通并可交付；产出结构化元数据，供后续 NL2SQL 与 SQL 性能分析复用
- Non-Goals：不做 SQL 生成、不做 SQL 性能分析、不做 RAG、不支持 PG/MySQL 之外的库、不做定时采集

## Decisions

### D1: 数据源凭据采用可逆加密（对现有安全规则的有意偏离）

- **Decision**：新增 `src/utils/crypto.py`，用对称加密（Fernet / AES-GCM）加密数据源密码，密钥从 `Configure` 读取。密钥缺失时**抛错**，绝不回退明文。密钥的存放位置受 D9 约束。
- **背景**：`security.md` 规定「密码存储必须使用 `utils/common.py` 的 `CommonUtils.password_encrypt`」，但该函数实现为 **MD5 单向哈希**（`utils/common.py:89`）。数据源密码需要还原出明文才能回连目标库，MD5 在功能上不可用。
- **Alternatives considered**：
  - 明文存储 — 拒绝，直接违反安全规则
  - 复用 MD5 — 不可逆，功能不成立
  - 平台 KMS — 当前无此依赖，列入 Open Questions
- **结论**：本条需评审确认。配套动作：在 `security.md` 中区分「用户登录密码 → 单向哈希」与「需回连的凭据 → 可逆加密」，避免后续 AI 生成的代码误用 MD5 存数据源密码。
- **Risks**：密钥泄露等同全部数据源凭据泄露 → 密钥不入库、日志脱敏、预留轮换能力。

### D2: 采集使用独立短连接，不接入 Django ORM 多库路由

- **Decision**：collectors 用各驱动直连（psycopg2 / PyMySQL），连接用完即关。
- **Why**：ORM 多库路由会把目标库连接纳入 Django 连接生命周期（长连接、请求结束不释放），且 ORM 允许写入，存在误写生产库的风险；短连接更容易施加超时与只读约束。
- **Alternatives considered**：动态注册 `DATABASES` + `inspectdb` — 被拒，原因见上。

### D3: 方言抽象为 `BaseCollector` + 每库实现

- **Decision**：定义统一采集接口（连接测试、模式、表、列、索引、约束、体积），PG 走 `information_schema` + `pg_catalog`，MySQL 走 `information_schema`。
- **MySQL 驱动选 PyMySQL**：纯 Python，避免 `mysqlclient` 的编译依赖与 Windows 构建问题。
- **Trade-off**：抽象层带来一层间接；但后续增加库类型（如 Oracle）只需新增实现，符合「先抽象」的投入产出。

### D4: 行数与体积优先取估算值

- **Decision**：PG 用 `pg_class.reltuples` / `pg_stat_user_tables.n_live_tup` 与 `pg_total_relation_size`；MySQL 用 `information_schema.TABLES` 的 `TABLE_ROWS` 与 `DATA_LENGTH + INDEX_LENGTH`。精确 `COUNT(*)` 作为配置开关，**默认关闭**且必须带超时。
- **Why**：大表 `COUNT(*)` 在生产库上代价高昂，采集工具不应成为故障源。

### D5: 快照存 JSONB，问题项结构化落表

- **Decision**：`metadata_snapshot` 用 JSONField 保存原始采集结果；`catalog_issue` 结构化落表（需按级别/类型筛选与分页）。
- **Trade-off**：表/列级明细存在 JSONB 中，无法直接 SQL join 检索；本期表数量预期在千级以内，Python 侧解析可接受。若后续需要跨库列级检索，再引入结构化表（列入后续演进）。

### D6: 异步沿用项目已有的 simple-background-task

- **Decision**：用 `simple-background-task` + `@clean_db_connections_decorator`。
- **Why**：`requirements.txt` 已含该依赖；README 提到的 `django-q` 实际未安装，不为此新增框架。

### D7: 接口鉴权交由 BSA 平台，系统内使用 AnyLogin

- **Decision**：`DatasourceView` 与 `CatalogView` 均继承 `baseviews.AnyLogin`，系统内不做认证与角色校验；访问控制由 BSA 平台的菜单与路由权限实现。
- **Why**：项目 [README](../README.md) 明确「`AnyLogin` — 无认证、无权限 — 公开接口，**BSA底座时默认使用此基类，接口权限由BSA平台控制**」，且全项目唯一另一个 ViewSet（`TestView`）用的就是 `AnyLogin`。本 change 是 BSA 底座上的业务模块，应遵循同一约定。
- **Alternatives considered**：
  - **读 `IsAuthenticated` + 写 `IsAdminUser`（本 change 初版方案，已废弃）**：勘察发现本仓库**没有任何代码会产出 `Token` 头** —— `CustomMiddleware` 是空实现（`process_request` 仅 `pass`）、`utils/bsa.py` 的 sessionid/csrftoken 是**出方向**调用（我们调 BSA）、前端无登录页且从不调用 `setToken`。该方案会让所有接口恒返回 4003，功能完全不可用。这是初版设计未对照项目既有约定的失误。
  - **保留写操作的管理员校验**：写操作仍需 Token，本地开发依旧不可用，且同样缺乏 Token 来源。
- **Security note（风险提示）**：数据源接口涉及目标库凭据，放开系统内鉴权意味着**访问控制完全依赖 BSA 平台**。若本服务被直接暴露（绕过平台网关），数据源配置可被任意读写、并可借「连接测试」探测内网。部署时必须确保服务仅经平台网关对外。

### D8: 降级而非失败

- **Decision**：某项分析依赖的统计信息取不到时（如 MySQL 5.7 无 `sys` schema 视图、PG 未开启 `pg_stat_statements`），跳过该规则并在结果中标记「未评估」，其余规则照常产出。
- **Why**：采集对象是异构的生产库，任何一项缺失都不应让整次分析失败。

### D9: 加密密钥不能落在被 git 跟踪的 conf.ini 里（需前置修复）

- **Decision**：本 change 实施前，先把 `src/config/conf.ini` 从 git 跟踪中移除，改为提交 `conf.ini.example` 并把 `conf.ini` 加入 `.gitignore`；加密密钥写入各环境本地的 `conf.ini`。
- **背景**（勘察发现）：`src/config/conf.ini` **当前已被 git 跟踪，且含真实数据库地址与口令**（如 `Admin123#`）。若按 D1 把数据源加密密钥写进这个文件并继续提交，等于把密钥和它保护的凭据一起公开，加密形同虚设。
- **Alternatives considered**：
  - 密钥走环境变量 — 被拒，`architecture.md` 明确「禁止直接使用 `os.environ` 读取配置（环境变量 `env_type` 除外）」
  - 引入 KMS — 当前无此依赖，见 Open Questions
- **配套动作**：轮换 `conf.ini` 中已泄露的口令（该文件在 git 历史中已存在，仅删除当前文件不足以消除泄露，需改密或清理历史）。
- **注意**：这是**既有安全问题**，不属于本 change 的功能范围，但本 change 的凭据加密方案依赖它先被解决，故列为前置条件。

## Risks / Trade-offs

| 风险 | 影响 | 缓解 |
|------|------|------|
| 大库采集耗时长、对生产库有压力 | 影响被管理业务 | 异步执行、强制只读账号、行数取估算、语句超时、同数据源并发互斥 |
| 加密密钥管理不善 | 全部数据源凭据泄露 | 密钥不入 git、日志脱敏、支持轮换、缺失时报错不回退明文 |
| 目标库版本差异（PG 9.x / MySQL 5.7） | 系统目录字段缺失导致采集失败 | 采集器按版本降级，缺失项标记未采集而非整体失败（D8） |
| 索引使用统计权限不足 | 「未使用索引」规则失效 | D8 降级策略 |
| 快照无限增长 | 存储膨胀 | 每数据源保留最近 N 个快照（N 可配置），超出清理 —— 待确认 |
| 采集器到目标库网络不可达 | 功能不可用 | 待确认是否需要 SSH 隧道/跳板（见 Open Questions） |

## Migration Plan

新增能力，无数据迁移。

上线步骤：

1. 在目标 PG 执行 `src/sql/pg_struct.sql` 新增的 4 张表 DDL
2. 在 `src/config/conf.ini` 配置数据源加密密钥与分析阈值（各环境段）—— **前置动作见 D9**
3. 部署后端（**无需**改 `INSTALLED_APPS`：所有业务模块挂在单一 `apps` app 下，`settings.py` 已含 `"apps"`，新模块自动被识别），注册 BSA 菜单（`right_config.json` + `hooks/install.py`）
4. 构建并部署前端

回滚：下线菜单、停止采集任务即可；4 张表数据可保留，无外部依赖。

## Open Questions

1. 数据源可见性是否需要按用户/角色隔离？（当前设计：不做系统内隔离，完全依赖 BSA 平台的菜单/路由权限，见 D7；因本仓库无 Token 来源，系统内无法表达用户身份）
2. 是否需要定时自动采集及采集频率配置？（当前：仅手动触发）
3. 目标库网络是否可达？是否需要 SSH 隧道/跳板机支持？
4. 加密密钥是否必须走平台 KMS？若必须，本期以「未跟踪的 conf.ini」作为过渡方案是否可接受？
5. 快照保留策略：保留最近多少个？是否需要手动清理入口？
6. 「疑似未使用索引」在 MySQL 5.7（无 `sys.schema_unused_indexes`）下如何处理——跳过，还是改用 `performance_schema` 自行计算？
7. D9 的前置修复（`conf.ini` 移出 git + 轮换口令）由谁在什么时间点完成？若不先做，D1 的加密方案价值有限。

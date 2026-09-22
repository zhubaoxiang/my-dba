## 1. 数据模型与 DDL

- [x] 1.1 在 `utils/custom_enum.py` 新增枚举：`DbTypeEnum`（POSTGRESQL/MYSQL）、`CollectTaskStatusEnum`、`IssueLevelEnum`、`IssueTypeEnum`，使用 `custom_enum.IntegerChoices`，禁止裸整数
- [x] 1.2 新增 `src/apps/datasource/models.py`，定义 `Datasource`（继承 `AbstractTimeFiledModel`，显式 `db_table = "datasource"`）
- [x] 1.3 定义 `MetadataSnapshot`（`db_table = "metadata_snapshot"`，原始采集结果用 `JSONField`，含采集时间、表数量等摘要）
- [x] 1.4 定义 `CollectTask`（`db_table = "collect_task"`，状态、起止时间、失败原因）
- [x] 1.5 定义 `CatalogIssue`（`db_table = "catalog_issue"`，级别、类型、对象定位、说明、建议）
- [x] 1.6 在 `src/sql/pg_struct.sql` 追加上述 4 张表的 DDL（**禁止 Django migration**）
- [x] 1.7 确认无需修改 `INSTALLED_APPS`（所有业务模块挂在单一 `apps` app 下，`settings.py` 已含 `"apps"`）；仅在 `src/config/urls.py` 增加新模块视图的导入
- [x] 1.8 验证：`python ../.ci/custom-checks/scaffold_check.py` 与 `python manage.py check` 均通过

## 2. 凭据可逆加密（依赖 1）

- [x] 2.1 新增 `src/utils/crypto.py`，提供 `encrypt(plain) -> str` / `decrypt(cipher) -> str` 对称加解密
- [ ] 2.2 【**未执行，用户决定暂不处理**】把 `src/config/conf.ini` 移出 git 跟踪。密钥当前仍写在**被 git 跟踪**的 conf.ini 中，属已接受的风险（design.md D9）
- [x] 2.3 密钥从 `utils/configure.py` 的 `Configure` 单例读取；在本地 `src/config/conf.ini` 各环境段增加密钥配置项，`conf.ini.example` 中只留占位符
      —— 实际做法：在 conf.ini 新增 `[datasource]` 段并写入随机密钥；因 2.2 未执行，**未创建** `conf.ini.example`
- [x] 2.4 密钥缺失或非法时抛出明确异常，**禁止回退明文**
- [x] 2.5 更新 `.ai-harness/rules/security.md`：区分「用户登录密码 → MD5 单向哈希」与「需回连的凭据 → 可逆加密」（对应 design.md D1）
- [x] 2.6 验证：单测覆盖 加密→解密往返一致、密钥缺失报错、密文不含明文片段

## 3. 采集器抽象与方言实现（依赖 1、2）

- [x] 3.1 新增 `src/apps/datasource/collectors/base.py`：定义 `BaseCollector` 接口（连接测试、模式清单、表清单、列、索引、约束/外键、体积与行数），统一返回 dict 结构
- [x] 3.2 实现 `collectors/postgres.py`：基于 `information_schema` + `pg_catalog`
- [x] 3.3 实现 `collectors/mysql.py`：基于 `information_schema`
- [x] 3.4 采集器统一使用独立短连接（连接 → 采集 → 关闭），不使用 Django ORM 多库路由；设置连接超时与语句超时
- [x] 3.5 行数与体积取估算值（PG `reltuples`/`n_live_tup`、MySQL `TABLE_ROWS`）；精确 `COUNT(*)` 作为配置开关且默认关闭（对应 design.md D4）
- [x] 3.6 取值缺失时按项降级并标记未采集，不抛错中断整次采集（对应 design.md D8）
- [x] 3.7 `src/requirements.txt` 增加 `PyMySQL`
- [x] 3.8 对真实 PostgreSQL 采集并核对：表数 4=4、列总数 51=51、索引总数 8=8，与 `information_schema` 直接统计一致
      —— **发现并修复一个真实 bug**：`pg_get_indexdef(oid, k, true)` 中的 `k` 来自 `generate_subscripts(indkey)`，而 `indkey` 是 `int2vector`（下标从 0 起），导致 k=0 返回整条索引定义、索引列名全部错误，进而让「外键缺索引」规则全线误报。已改为 `k + 1` 并在真实库上验证（误报 5 条 → 0 条，剩余 2 条为真问题）。MySQL 采集器仍**未在真实 MySQL 上验证**

## 4. 数据源配置 API（依赖 1、2、3）

- [x] 4.1 `serializers.py`：`DatasourceSerializer`（`ModelSerializer`，查询用，密码字段不出参）、`DatasourceCreateSerializer` / `DatasourceUpdateSerializer`（普通 `Serializer` + `validate_name()`）
- [x] 4.2 `views.py`：`DatasourceView` 实现 `list` / `create` / `retrieve` / `update` / `destroy`
- [x] 4.3 `DatasourceView` / `CatalogView` 继承 `baseviews.AnyLogin`，鉴权交由 BSA 平台（对应 design.md D7；初版的按 action 区分权限方案已废弃）
- [x] 4.4 实现连接测试 action（不落库、带超时、失败原因不含密码）
- [x] 4.5 名称唯一性校验（`is_deleted=False` 范围内）；删除走软删除
- [x] 4.6 所有列表接口使用 `pagination.paginate(self, queryset)`，响应统一 `baseviews.ResponseOK` / `ResponseBadRequest` / `ResponseForbidden`
- [x] 4.7 对真实库逐条调用接口验证：创建/列表/详情/修改/软删除、未保存参数连接测试、已保存数据源连接测试、同名重复被拒（4000）、分页结构含 `count/total/results`、响应无 `password` 字段、错误口令返回非 2000 且不泄露口令

## 5. 采集服务与异步任务（依赖 3、4）

- [x] 5.1 新增 `src/apps/datasource/services.py`：封装「校验数据源 → 建任务 → 采集 → 写快照 → 跑分析 → 更新任务状态」
- [x] 5.2 采集任务函数加 `@clean_db_connections_decorator`，用 `simple-background-task` 异步执行
      —— 实测该库的 worker 线程不会自动启动、且每次构造都会重建内部队列，故 `services.py` 只构造一次 worker 并显式 `start()`
- [x] 5.3 失败时任务置失败并记录原因，**不写入成功快照**
- [x] 5.4 同一数据源存在执行中任务时拒绝重复触发（返回 code 4017）
- [x] 5.5 真实库验证：触发接口立即返回 `task_id` 且状态为「待执行」；成功路径任务转「成功」并产出快照；错误口令路径任务转「失败」且 `fail_reason` 非空、不含密码、**不产生快照**；执行中重复触发返回 4017；停用数据源触发返回 4017

## 6. 健康问题分析（依赖 5）

- [x] 6.1 新增 `src/apps/datasource/analyzer.py`：输入快照，输出问题项列表
- [x] 6.2 实现规则：无主键表、外键缺索引、重复/冗余索引、疑似未使用索引、超大表、可疑字段类型、孤立表
- [x] 6.3 判定阈值从 `Configure` 读取，**禁止硬编码**
- [x] 6.4 依赖的统计信息缺失时跳过该规则并标记「未评估」（对应 design.md D8）
- [x] 6.5 验证：单测用构造的快照数据覆盖每条规则的正例与反例，并确认无问题时返回空清单

## 7. 元数据查询 API 与路由（依赖 5、6）

- [x] 7.1 实现快照列表 / 最新快照查询 action
- [x] 7.2 实现表清单查询（分页，返回表名、注释、行数、体积）
- [x] 7.3 实现表详情查询（列定义、索引、主键、外键关联、体积）
- [x] 7.4 实现问题清单查询（支持按严重级别筛选，分页）
- [x] 7.5 实现两个快照的结构差异对比 action（`differ.py`，含单测）
- [x] 7.6 在 `src/config/urls.py` 通过 `router.register` 注册 `rf"{SYS_NAME}/v1/datasource"` 与 `rf"{SYS_NAME}/v1/catalog"`
- [x] 7.7 在 `src/right_config.json` 的 `children` 增加数据源管理菜单项（含库表分析、问题清单子菜单）
- [x] 7.8 对真实库验证：快照列表（不含 `raw_data`）、最新快照、总览统计（`issue_counts`/`unavailable`）、表清单分页、表详情含 `columns/indexes/primary_key/foreign_keys`、问题清单分页与按级别筛选、快照自比对无差异

## 8. 前端页面（依赖 7）

- [x] 8.1 新增 `static/src/api/datasource.js`，复用 `request.js` 实例
- [x] 8.2 新增数据源管理页：列表 + 新建/编辑弹窗 + 连接测试 + 触发采集 + 任务状态
- [x] 8.3 新增库表总览页：表清单（分页、可搜索）+ 统计卡片 + 未采集项提示
- [x] 8.4 新增表详情组件 `table-detail.vue`：以抽屉形式展示列定义、索引、外键关联、体积
      —— 实现方式由「独立页面」调整为「抽屉组件」，跳转路径 `/datasource/tables?table=x` 仍可用
- [x] 8.5 新增问题清单页：按严重级别筛选，可跳转表详情
- [x] 8.6 在 `static/src/router/index.js` 注册路由（配置 `meta.title`、`meta.icon`）
- [x] 8.7 `npm run build` 通过（新页面 chunk 均产出）；「新建数据源 → 测试连接 → 采集 → 查看问题」闭环的**全部后端接口**已用真实请求跑通。**未做**的是人工在浏览器里点一遍前端渲染

## 9. 文档与整体验证

- [x] 9.1 更新 `README.md`，增加数据源与元数据模块说明（数据表、API 清单、分析规则、配置项、安全说明、已知限制）
- [x] 9.2 更新 `src/sql/pg_data.sql` —— 已评估：不需要预置示例数据，跳过
- [x] 9.3 整体验证：`scaffold_check.py` ✅、`python manage.py test` ✅（33 项通过）、`npm run build` ✅；另加 ruff check ✅ / ruff format --check ✅（pre-commit 配置的两项）
- [x] 9.4 对照 `.ai-harness/rules/` 逐条自查（模型继承、ViewSet 基类、响应格式、分页、软删除、枚举、路由注册、db_table、Serializer 类型、DDL 管理）

## 依赖关系说明

- 关键路径：1 → 2 → 3 → 4 → 5 → 6 → 7 → 8 → 9
- 可并行：第 2 节（加密工具）与第 3 节（采集器）在 1 完成后可并行推进；第 8 节前端可在第 7 节接口签名确定后与 6 并行开发
- 阻塞项：第 3 节需要可用的 PG 与 MySQL 测试实例；第 1 节的 DDL 需确认目标 PG 版本

## 完成情况小结

- 已完成 **57 / 58** 项，未完成 **1** 项：
  - `2.2` conf.ini 移出 git 跟踪 —— 用户明确选择「暂不处理，接受风险」，**不再计划执行**
- 遗留的未验证点：MySQL 采集器未在真实 MySQL 上跑过（`3.8` 只验证了 PostgreSQL 分支）；前端未做人工浏览器走查（`8.7`）

### 真实验证发现的问题（已修复）

| 问题 | 影响 | 状态 |
|------|------|------|
| `pg_get_indexdef(oid, k, true)` 的 `k` 取自 `generate_subscripts(indkey)`，而 `indkey` 是 `int2vector`（0 起） | 取到的是整条索引定义而非列名 → **「外键缺索引」规则全线误报**，「重复/冗余索引」规则失效，表详情列名显示错误 | 已改为 `k + 1`，真实库验证：误报 5 条 → 0 条 |
| `text` / `jsonb` 被列为「大对象类型」 | 在正常库上产生大量噪音（PG 中 `text` 是推荐字符串类型） | 已收窄为仅 `blob/bytea/longtext/mediumtext` |
| `collect_task.snapshot_id`、`catalog_issue.datasource_id` 缺前导索引 | 真实的外键缺索引 | 已在本模块分析规则发现后补进 `pg_struct.sql`（**需重新执行 DDL 生效**） |

> 这三项均无法被纯逻辑单测发现，是任务 `3.8` 对着真实数据库跑才暴露出来的。

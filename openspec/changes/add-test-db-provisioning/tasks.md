## 1. 数据模型与配置

- [x] 1.1 `src/apps/datasource/models.py`：`env_type`（默认 production）、`write_username`、`write_password`
- [x] 1.2 `src/utils/custom_enum.py`：环境类型枚举、预置状态枚举
- [x] 1.3 `src/apps/provisioning/models.py`：`provisioned_database` 表（数据源、库名、表、行数、状态、失败原因、创建人）
- [x] 1.4 `src/sql/pg_struct.sql` / `patch.sql`：数据源新列与 `provisioned_database` 增量 DDL
- [x] 1.5 `src/config/conf.ini`：`[provisioning]` 段（前缀、表数、行数、超时、快照最大年龄）
- [x] 1.6 数据源创建/编辑支持 `env_type` / 写账号字段（创建者与管理员），列表返回 `env_type` / `env_type_label` / `write_username` / `has_write_credential`，写密码永不回传

## 2. 预置服务

- [x] 2.1 `datasource.services`：环境校验、写账号读取（加密串）、最新快照读取的跨模块契约
- [x] 2.2 库名生成与校验：前缀 + 数据源 id + 时间戳 + 随机后缀；格式校验
- [x] 2.3 DDL 生成器（PG / MySQL）：列、主键、唯一索引、普通索引；仅两端都被选中的外键
- [x] 2.4 不支持的类型（数组、自定义枚举、空间类型等）在预览阶段拒绝并说明
- [x] 2.5 造数生成器：行数按表指定；固定种子、类型/长度/`NOT NULL`、主键与唯一索引、外键取值
- [x] 2.5.1 简单语义推断：按列名/注释识别邮箱、手机、姓名、金额、编号、URL、IP、状态等；类型不兼容时回退
- [x] 2.6 外键拓扑排序；循环外键与不可空自引用在预览阶段拒绝
- [x] 2.7 预置编排：写 `creating` 记录 → 建库 → 建表 → 造数 → `ready`；失败置 `failed` 并尝试删库
- [x] 2.8 删除：只接受 `provisioned_database` 中的记录；删除后置 `dropped`
- [x] 2.9 限制与审计：表数/行数/超时/快照年龄；写 `provisioning.log`（不含凭据）

## 3. 接口

- [x] 3.1 `ProvisioningView(BaseView)`：`preview` / `provision` / `list` / `delete`；表入参包含每表行数；普通用户按数据源创建者与记录创建者鉴权，管理员可操作全部
- [x] 3.2 环境类型与写账号随数据源创建/修改接口提交；普通用户提交这些字段返回 4005
- [x] 3.3 执行接口要求 `confirm=true`，并重新校验环境、凭据、名称与限制
- [x] 3.4 响应不含任何密码；错误文案不泄露连接串
- [x] 3.5 `config/urls.py` 注册路由

## 4. 前端

- [x] 4.1 `api/provisioning.js`
- [x] 4.2 `views/provisioning/index.vue`：选择测试数据源、表，**逐表设置行数**，可选种子
- [x] 4.3 预览区：库名、DDL、行数、警告；「近似结构」提示常驻
- [x] 4.4 确认执行与结果展示；已创建测试库列表与删除
- [x] 4.5 数据源创建/编辑表单：环境类型与写账号字段（创建者与管理员可见）；预置页不再提供配置入口
- [x] 4.6 路由 `/test-db` 放在「库表」组、不设 `adminOnly`；`npm run build`

## 5. 测试与文档

- [x] 5.1 单测：DDL 生成（两种方言）、逐表行数、造数约束、唯一性、语义推断与类型不兼容回退、外键顺序、循环拒绝、库名格式、限制
- [x] 5.2 接口测试：生产数据源拒绝、无写账号拒绝、预览无副作用、失败清理、删除只接受记录库、响应无凭据；普通用户只能操作自己创建的数据源与记录，管理员可操作全部
- [x] 5.3 新增 `docs/test-db-provisioning.md`：安全边界、近似结构清单、API、配置、限制
- [x] 5.4 更新 `docs/datasource.md`（环境类型与写账号）、`docs/architecture.md`（新模块）、README 边界说明
- [x] 5.5 `python src/manage.py test apps`（748 项通过，其中 provisioning 33 项）
- [x] 5.6 `python .ci/custom-checks/scaffold_check.py`、`ruff check`、`ruff format --check`
- [x] 5.7 `openspec validate add-test-db-provisioning --strict --no-interactive`
- [ ] 5.8 手工核对：真实测试库上预览、执行、失败清理、删除；生产数据源确认没有入口（需真实测试库与写账号）

## 6. 已创建测试库的后续维护

- [x] 6.1 追加数据：只限记录中的表；外键从测试库父表读取；唯一冲突整体回滚；行数上限
- [x] 6.2 清空数据：`DELETE` 不级联；被外键引用时返回可读错误
- [x] 6.3 新增表：从最新快照建表造数；外键只指向已存在表；MySQL 失败清理新表
- [x] 6.4 删除表：只允许记录中的表；`DROP TABLE` 不级联
- [x] 6.5 操作后按实际行数刷新记录的表明细与总行数
- [x] 6.6 接口：`POST/DELETE /test-db/{id}/rows`、`POST/DELETE /test-db/{id}/tables`；归属与状态校验
- [x] 6.7 前端：已创建记录可展开表清单；追加/清空/新增表/删表入口与确认
- [x] 6.9 新建测试库弹框：默认不选数据源、库表分页 5 条/页、预览与执行分步
- [x] 6.10 已创建记录列表支持按库名/数据源搜索；普通用户搜索仍只返回自己的记录
- [x] 6.8 测试：权限、状态、表归属、追加/清空/加表/删表流程（57 项 provisioning 测试通过）

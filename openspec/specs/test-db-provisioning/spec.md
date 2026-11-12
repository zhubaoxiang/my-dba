# test-db-provisioning Specification

## Purpose
TBD - created by archiving change add-test-db-provisioning. Update Purpose after archive.
## Requirements
### Requirement: 测试库预置安全边界

系统 SHALL 提供测试库预置能力：从最新元数据快照生成结构并写入测试数据。该能力对**所有登录用户开放**，但普通用户**只能操作自己创建的数据源**；管理员可操作全部数据源。能力**仅适用于标记为测试环境的数据源**。

预置 MUST 只新建一个由服务端命名的独立 database，MUST NOT 修改、删除或写入任何已有数据库。库名 MUST 符合固定格式（前缀 + 数据源标识 + 时间戳 + 随机后缀），且创建前 MUST 校验同名库不存在。

系统 MUST NOT 复制源库的任何业务数据，只读取快照中的结构元数据。

#### Scenario: 生产数据源不可预置

- **WHEN** 使用者对生产环境数据源发起预置
- **THEN** 服务端 MUST 拒绝（code 4017），MUST NOT 连接目标库或创建任何 database

#### Scenario: 只新建独立库

- **WHEN** 预置执行成功
- **THEN** 目标服务器 MUST 新增一个符合命名规则的独立 database
- **AND** 该服务器上已有 database 的内容 MUST 保持不变

#### Scenario: 同名库不覆盖

- **WHEN** 生成的库名在目标服务器上已存在
- **THEN** 创建 MUST 失败并返回可读错误，MUST NOT 删除或覆盖已有库

#### Scenario: 普通用户可预置自己的数据源

- **WHEN** 普通用户对自己创建的数据源发起预览或执行
- **THEN** 系统 MUST 正常处理

#### Scenario: 普通用户不能预置他人的数据源

- **WHEN** 普通用户对他人的数据源发起预览或执行
- **THEN** 系统 MUST 返回无权限（code 4005），且 MUST NOT 连接目标库

#### Scenario: 管理员可预置任意数据源

- **WHEN** 管理员对任意测试环境数据源发起预览或执行
- **THEN** 系统 MUST 正常处理

#### Scenario: 未配置写账号不可执行

- **WHEN** 测试数据源没有独立的预置写账号
- **THEN** 预置 MUST 被拒绝，并提示先由数据源创建者或管理员配置

### Requirement: 近似结构与造数

系统 SHALL 从数据源**最新快照**读取所选表的结构，生成与数据源同方言的 DDL，并按外键拓扑顺序生成测试数据。

DDL MUST 覆盖：列定义（类型、长度、`NOT NULL`、可解析默认值）、主键、唯一索引、普通索引；外键 MUST 仅在**两端表都被选中**时创建。

每张表的行数 MUST 支持独立指定，并同时受每表上限与总行数上限约束。

系统 SHALL 按**列名与注释**做简单语义推断（如邮箱、手机号、姓名、金额、编号、URL、IP、状态），仅在类型兼容时生效；类型不兼容或无法识别时 MUST 回退到类型默认生成。语义推断是启发式，MUST NOT 被描述为业务正确的数据，MUST NOT 推断业务规则或跨表关联。

快照未包含的 CHECK 约束、触发器、视图、序列、扩展、权限、分区定义 MUST NOT 被声称已还原；界面与响应 MUST 明确提示**近似结构**。

不支持可靠生成的值类型（数组、自定义枚举、空间类型等）MUST 在预览阶段拒绝该表并说明原因，MUST NOT 猜测生成。

生成的数据 MUST 满足：类型与长度、`NOT NULL`、主键唯一、唯一索引、外键引用有效。循环外键或不可空的自引用外键 MUST 在预览阶段被拒绝并说明原因。

生成 MUST 支持固定随机种子以便复现；未提供时由服务端生成并返回。

#### Scenario: 生成近似 DDL

- **WHEN** 预览选中若干表
- **THEN** 响应 MUST 包含将执行的建表 DDL，并列出未还原的结构项
- **AND** 只包含使用者选中的表

#### Scenario: 外键依赖未选中的表

- **WHEN** 选中的表引用了未选中的表
- **THEN** 该外键 MUST NOT 被创建，并在警告中明确说明

#### Scenario: 不支持的类型被拒绝

- **WHEN** 选中表的列使用快照无法可靠还原的类型（如数组、自定义枚举）
- **THEN** 预览 MUST 拒绝该表并说明原因，MUST NOT 生成可能错误的结构

#### Scenario: 按表指定行数

- **WHEN** 使用者为不同表设置不同的行数
- **THEN** 每张表 MUST 按各自的行数生成数据，且总量不得超过配置上限

#### Scenario: 简单语义推断

- **WHEN** 列名或注释匹配邮箱、手机号、金额等常见语义，且类型兼容
- **THEN** 生成值 MUST 采用对应的语义格式（如邮箱、手机号、两位小数金额）

#### Scenario: 类型不兼容时回退

- **WHEN** 列名匹配某种语义但类型不兼容（如在数值列上匹配到「邮箱」）
- **THEN** 系统 MUST 回退到类型默认生成，MUST NOT 生成不兼容的值

#### Scenario: 造数满足约束

- **WHEN** 执行预置并写入测试数据
- **THEN** 主键唯一、唯一索引、`NOT NULL` 与外键引用 MUST 均成立

#### Scenario: 循环外键被拒绝

- **WHEN** 选中的表之间存在循环外键，或自引用外键所在列不可空
- **THEN** 预览 MUST 拒绝并说明无法确定插入顺序

#### Scenario: 种子可复现

- **WHEN** 使用同一数据源、同一表集合、同一行数与同一种子重新生成
- **THEN** 生成的数据 MUST 一致

### Requirement: 预置执行与清理

系统 SHALL 提供预览与执行两个阶段。预览 MUST 只生成计划（库名、DDL、行数、警告），MUST NOT 连接目标库或创建任何 database。执行 MUST 要求显式确认，并在服务端重新校验环境类型、写账号、库名格式与限制。

系统 MUST 对表数、每表行数、总行数、执行超时施加可配置上限；超出时 MUST 拒绝。

执行失败时 MUST 尝试删除已创建但未完成的 database，并把记录置为失败、保留失败原因；清理失败时 MUST 保留库名供人工处理。删除接口 MUST 只接受系统记录过的预置库，MUST NOT 接受任意库名。

#### Scenario: 预览无副作用

- **WHEN** 调用预览接口
- **THEN** 目标服务器 MUST NOT 新增或修改任何 database

#### Scenario: 未确认不执行

- **WHEN** 调用执行接口但没有显式确认
- **THEN** 系统 MUST 拒绝执行

#### Scenario: 超出限制被拒绝

- **WHEN** 请求的表数或行数超过配置上限
- **THEN** 系统 MUST 拒绝并说明具体限制

#### Scenario: 失败自动清理

- **WHEN** 建表或写入数据过程中失败
- **THEN** 系统 MUST 尝试删除本次创建的 database
- **AND** 预置记录 MUST 标记为失败并保留原因

#### Scenario: 只删除系统创建的库

- **WHEN** 记录创建者或管理员删除一条预置记录
- **THEN** 系统 MUST 只删除该记录对应的 database，并标记为已删除
- **AND** MUST NOT 允许删除未记录或已删除的库

### Requirement: 已创建测试库的后续维护

系统 SHALL 允许记录创建者或管理员对**状态为可用**的预置记录继续维护：向已创建的表追加数据、清空表数据、从最新快照新增表、删除本功能创建的表。

维护 MUST 只作用于该记录对应的测试库，MUST NOT 接受任意库名或任意 SQL。追加 / 清空 / 删表 MUST 只针对记录中存在的表；新增表 MUST 来自最新快照。

追加数据 MUST 满足类型、`NOT NULL`、唯一与外键约束；外键值 MUST 从测试库现有父表读取，父表没有数据时 MUST 拒绝。唯一冲突 MUST 整体回滚，MUST NOT 留下部分数据。清空与删表 MUST NOT 使用 `CASCADE`；被外键引用时 MUST 失败并说明原因。新增表的外键 MUST 只指向测试库中已存在的表。操作成功后 MUST 更新记录中的表明细与总行数。

#### Scenario: 追加数据

- **WHEN** 记录创建者或管理员对记录中的表追加若干行
- **THEN** 新行 MUST 写入该测试库的表，记录的该表行数与总行数 MUST 更新

#### Scenario: 清空表数据

- **WHEN** 对记录中的表执行清空数据
- **THEN** 该表数据 MUST 被删除，记录的行数 MUST 更新
- **AND** MUST NOT 级联删除其他表的数据

#### Scenario: 新增表

- **WHEN** 从最新快照选择一张尚未在记录中的表新增
- **THEN** 系统 MUST 在测试库中建表、造数，并把该表加入记录的表明细
- **AND** 外键 MUST 只指向测试库中已存在的表

#### Scenario: 删除表

- **WHEN** 对记录中的表执行删除
- **THEN** 该表 MUST 从测试库删除并从记录中移除
- **AND** MUST NOT 删除记录之外的表

#### Scenario: 只操作记录内的表

- **WHEN** 请求对不在记录中的表追加 / 清空 / 删除
- **THEN** 系统 MUST 拒绝并说明该表不是本功能创建的

#### Scenario: 唯一冲突整体回滚

- **WHEN** 追加数据时违反唯一约束
- **THEN** 本次追加 MUST 整体回滚，MUST NOT 留下部分行

#### Scenario: 父表无数据拒绝追加

- **WHEN** 追加的数据含外键，但测试库中的父表没有可引用的行
- **THEN** 系统 MUST 拒绝并说明父表没有数据

#### Scenario: 外键引用导致清空或删表失败

- **WHEN** 被其他表外键引用的表被清空或删除
- **THEN** 目标库会拒绝，系统 MUST 返回可读错误，MUST NOT 自动级联删除

#### Scenario: 非可用记录不可维护

- **WHEN** 记录状态不是「可用」或操作者不是记录创建者/管理员
- **THEN** 系统 MUST 拒绝（code 4005 或 4017）

### Requirement: 预置记录与审计

系统 SHALL 为每次预置保存记录，至少包含：发起人、数据源、库名、选中的表、总行数、状态、失败原因与时间。

记录 MUST 在创建 database 之前写入，状态至少覆盖 `creating` / `ready` / `failed` / `dropped`。系统 MUST 写审计日志，记录发起人、数据源、库名、表数、行数与结果；日志 MUST NOT 包含任何密码。

普通用户 MUST 只能查看与删除自己创建的预置记录；管理员 MAY 查看与删除全部记录。

#### Scenario: 成功记录

- **WHEN** 预置成功完成
- **THEN** 记录 MUST 包含库名、表、行数、发起人与完成时间，状态为 `ready`

#### Scenario: 失败记录保留原因

- **WHEN** 预置执行失败
- **THEN** 记录 MUST 保留失败原因与已生成的库名，状态为 `failed`

#### Scenario: 普通用户记录隔离

- **WHEN** 普通用户查询预置记录列表
- **THEN** 系统 MUST 只返回该用户创建的记录，MUST NOT 返回他人的记录
- **AND** 删除他人的记录 MUST 返回无权限（code 4005）

#### Scenario: 管理员可查看全部记录

- **WHEN** 管理员查询预置记录列表
- **THEN** 系统 MUST 返回全部记录，并允许删除任意记录

#### Scenario: 日志不含凭据

- **WHEN** 预置过程写审计日志
- **THEN** 日志 MUST NOT 包含读写账号的密码或完整连接串

### Requirement: 测试库预置界面

系统 SHALL 提供所有登录用户可见的「测试库」页面，位于**「造数」主菜单**下的「测试库」子菜单，支持选择测试数据源、选择表、**逐表设置行数**、设置可选种子、预览、确认执行，并展示已创建的测试库列表。

页面默认展示已创建的测试库记录列表；右上角提供「新建测试库」入口，点击后打开创建弹框。已创建记录 MUST 可展开查看其表清单，并对状态为可用的记录提供追加数据、清空数据、新增表与删除表入口。

已创建记录列表 MUST 支持按库名或数据源名称搜索；普通用户的列表与搜索结果 MUST 只包含自己创建的记录。

创建弹框 MUST 先让使用者选择数据源（默认不选中），选择后才加载库表；库表列表 MUST 分页展示（每页 5 条）并支持按表名/注释搜索。表选择完成后进入预览步骤，确认后再执行。

页面 MUST 常驻提示「近似结构，未包含 CHECK / 触发器 / 视图 / 序列 / 扩展等」，并展示后端返回的全部警告。

环境类型与预置写账号 MUST 在**数据源创建/编辑页**由数据源创建者或管理员设置；预置页 MUST NOT 提供单独的环境/写账号配置入口，并 MUST 在不可用时引导使用者去数据源管理页配置。

#### Scenario: 普通用户可见但只列自己的数据源

- **WHEN** 普通用户进入预置页面
- **THEN** 菜单与页面 MUST 可见
- **AND** 数据源下拉 MUST 只列出该用户创建的数据源
- **AND** 预置记录列表 MUST 只显示该用户创建的记录

#### Scenario: 搜索已创建记录

- **WHEN** 使用者在已创建记录列表输入库名或数据源名称关键字
- **THEN** 列表 MUST 只显示匹配的记录
- **AND** 普通用户的搜索结果 MUST 仍限于自己创建的记录

#### Scenario: 默认展示已创建记录

- **WHEN** 使用者进入「造数 → 测试库」
- **THEN** 页面 MUST 默认展示已创建的测试库列表
- **AND** 新建入口 MUST 位于页面右上角

#### Scenario: 先选数据源再加载库表

- **WHEN** 打开新建测试库弹框
- **THEN** 数据源 MUST 默认为空
- **AND** 选择数据源后才加载并分页展示库表（每页 5 条）

#### Scenario: 预览后确认

- **WHEN** 使用者选择表与行数并进入预览步骤
- **THEN** 页面 MUST 展示库名、表清单、DDL、行数与警告，并要求再次确认后才执行

#### Scenario: 近似结构提示常驻

- **WHEN** 使用者进入预置页面
- **THEN** 页面 MUST 常驻显示未还原的结构项提示

#### Scenario: 结果与清理入口

- **WHEN** 预置成功
- **THEN** 页面 MUST 展示新库名与行数，并在列表中提供删除入口

### Requirement: 大数据模式自动判定与上限

系统 SHALL 根据总行数自动选择执行模式：总行数超过 `bulk_threshold` 时进入异步大数据模式，否则同步执行。同步模式的有效上限为 `bulk_threshold`；大数据模式 MUST 同时受单表上限与总量上限约束。

#### Scenario: 超过阈值进入异步

- **WHEN** 请求的总行数大于 `bulk_threshold`
- **THEN** 系统 MUST 创建 `pending` 记录并立即返回，MUST NOT 在请求线程内连接目标库

#### Scenario: 未超过阈值保持同步

- **WHEN** 请求的总行数小于或等于 `bulk_threshold`
- **THEN** 系统 MUST 按同步模式执行并返回完成后的记录

#### Scenario: 超过大数据上限被拒绝

- **WHEN** 单表或总行数超过 `bulk_max_rows_per_table` / `bulk_max_total_rows`
- **THEN** 系统 MUST 拒绝并说明具体限制

### Requirement: 固定快照与执行上下文

异步任务 MUST 固定预览时的快照与随机种子，排队期间的数据源采集 MUST NOT 改变任务结构。执行时 MUST 校验快照仍存在且属于该数据源。

#### Scenario: 排队期间发生新采集

- **WHEN** 任务排队后数据源完成了一次新的元数据采集
- **THEN** worker MUST 继续使用记录中固定的快照执行

#### Scenario: 快照被删除

- **WHEN** worker 执行时固定快照已不存在
- **THEN** 任务 MUST 失败并保留可读原因，MUST NOT 改用最新快照

### Requirement: 异步任务状态与并发控制

系统 SHALL 使用 `pending` / `running` / `ready` / `failed` / `cancelled` 表示异步任务状态。同一数据源 MUST 同时最多存在一个排队或执行中的任务；worker MUST 原子领取任务。

#### Scenario: 重复提交被拒绝

- **WHEN** 同一数据源已有排队或执行中的任务
- **THEN** 新的异步预置请求 MUST 被拒绝并提示等待或取消

#### Scenario: 取消排队任务

- **WHEN** 创建者或管理员取消 `pending` 任务
- **THEN** 记录 MUST 立即变为 `cancelled`，MUST NOT 连接目标库

#### Scenario: 取消执行中任务

- **WHEN** 创建者或管理员取消 `running` 任务
- **THEN** 系统 MUST 置取消标志，worker MUST 在批次边界停止、删除本次创建的库并把记录置为 `cancelled`

#### Scenario: 执行中不可删除

- **WHEN** 对 `running` 记录调用删除
- **THEN** 系统 MUST 拒绝并提示先取消

### Requirement: 大数据 worker 与中断恢复

系统 SHALL 由独立于 gunicorn 的常驻 worker 执行异步任务。worker MUST 周期性写入心跳与进度；启动时 MUST 回收心跳超时的 `running` 任务。

#### Scenario: worker 崩溃后恢复

- **WHEN** worker 进程中断且心跳超过 `worker_stale_seconds`
- **THEN** 重启后的 worker MUST 把该任务置为 `failed`（或 `cancelled`，若已请求取消）
- **AND** MUST 按 `database_created` 清理半成品数据库

#### Scenario: 进度可见

- **WHEN** 任务执行中查询记录列表
- **THEN** 响应 MUST 包含 `processed_rows`、`total_rows`、`current_table` 与 `heartbeat_time`

### Requirement: 大数据批量写入与后置约束

异步执行 SHALL 使用流式生成与批量写入：PostgreSQL 使用 `COPY FROM STDIN`，MySQL 优先 `LOAD DATA LOCAL INFILE`，不可用时回退多行 `INSERT`。唯一索引、普通索引与外键 MUST 在数据写入完成后创建。

#### Scenario: 批量写入成功

- **WHEN** worker 执行一个大表任务
- **THEN** 数据 MUST 分批写入并按 `bulk_commit_rows` 提交
- **AND** 全部表写入完成后 MUST 创建索引与外键，最后置为 `ready`

#### Scenario: 唯一约束冲突

- **WHEN** 后置创建唯一索引时发现重复值
- **THEN** 任务 MUST 失败，本次创建的数据库 MUST 被清理

#### Scenario: MySQL 不支持本地文件导入

- **WHEN** `LOAD DATA LOCAL INFILE` 因 `local_infile` 被拒绝
- **THEN** worker MUST 对当前及后续批次回退多行 `INSERT`，任务 MUST 继续执行

### Requirement: 大数据模式界面

测试库页面 MUST 在预览中展示执行方式；异步任务 MUST 在列表中展示进度与当前表，并支持取消。

#### Scenario: 预览显示异步

- **WHEN** 预览的总行数超过 `bulk_threshold`
- **THEN** 页面 MUST 显示「异步排队」提示，并说明提交后可在列表查看进度

#### Scenario: 列表轮询进度

- **WHEN** 列表中存在 `pending` 或 `running` 记录
- **THEN** 页面 MUST 定时刷新并展示进度百分比与当前表
- **AND** MUST 为创建者/管理员提供取消入口

#### Scenario: 未完成记录不可维护

- **WHEN** 记录状态为排队、执行中、失败或已取消
- **THEN** 页面 MUST NOT 提供追加数据、清空、新增表或删表入口


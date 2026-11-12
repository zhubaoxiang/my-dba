# test-data-export Specification

## Purpose
TBD - created by archiving change add-test-data-export. Update Purpose after archive.
## Requirements
### Requirement: 测试数据文件导出

系统 SHALL 根据数据源最新快照或使用者手动输入的表结构，生成可下载的 SQL 数据文件。导出 MUST 只读取快照/表结构元数据，MUST NOT 连接目标库、读取源库业务数据或执行任何语句。

导出 SHALL 接收：数据源或手动表结构（二选一）、选中的表与每表行数、可选随机种子、是否包含建表语句。数据源模式下，普通用户 MUST 只能选择自己创建的数据源；管理员 MAY 选择全部。导出 MUST NOT 要求数据源为测试环境，也 MUST NOT 要求配置预置写账号。

文件 MUST 包含按外键拓扑顺序排列的 `INSERT` 语句；选择合适的行数、类型/长度/`NOT NULL`/唯一约束由生成器保证。所选表的外键引用未选中的表时，响应与文件 MUST 给出明确警告。

文件 MUST 使用目标方言的标识符引用与字符串转义；MUST 以 `.sql` 文件名下载。行数 MUST 受配置的表数、单表行数与总行数上限约束。

#### Scenario: 生成并下载数据文件

- **WHEN** 使用者选择自己创建的数据源、表与行数并触发生成
- **THEN** 系统 MUST 返回 `.sql` 文件名与文件内容
- **AND** 内容 MUST 包含对应表的 `INSERT` 语句

#### Scenario: 不连接目标库

- **WHEN** 执行导出
- **THEN** 系统 MUST NOT 建立到目标数据库的连接
- **AND** MUST NOT 执行任何语句

#### Scenario: 可选建表语句

- **WHEN** 使用者勾选「包含建表语句」
- **THEN** 文件 MUST 在数据前包含所选表的建表/索引/外键语句
- **AND** MUST NOT 包含 `CREATE DATABASE`

#### Scenario: 外键顺序与警告

- **WHEN** 同时选中父表与子表
- **THEN** 文件中的父表 `INSERT` MUST 排在子表之前
- **WHEN** 子表的外键引用了未选中的表
- **THEN** 响应与文件 MUST 警告该外键值可能无法通过目标库校验

#### Scenario: 非创建者被拒绝

- **WHEN** 普通用户选择他人创建的数据源导出
- **THEN** 系统 MUST 返回无权限（code 4005）

#### Scenario: 未登录被拒绝

- **WHEN** 未登录请求导出
- **THEN** 系统 MUST 返回未登录/登录失效（code 4003）

#### Scenario: 行数上限

- **WHEN** 请求的表数或行数超过配置上限
- **THEN** 系统 MUST 拒绝并说明限制

#### Scenario: 同种子可复现

- **WHEN** 使用同一数据源（或同一手动表结构）、表集合、行数与种子再次生成
- **THEN** 文件的数据部分 MUST 一致

### Requirement: 手动表结构造数

系统 SHALL 允许使用者不关联数据源，直接提供表结构（DDL）进行造数并生成 SQL 文件。

系统 MUST 仅解析 `CREATE TABLE` / `CREATE INDEX` / `ALTER TABLE` 作为结构定义；注释类语句 MAY 忽略并提示；`DROP` / `TRUNCATE` / 视图等其他语句 MUST 被拒绝。

解析结果 SHALL 用于选择表、逐表行数并生成与数据源模式相同格式的文件。手动模式 MUST NOT 要求数据源归属，任何登录用户 MAY 使用，但仍 MUST 受表数/行数上限约束。

未限定 schema 的外键 MUST 按子表所在 schema 归一，使父表/子表的拓扑顺序正确。

#### Scenario: 解析手动表结构

- **WHEN** 使用者粘贴合法的 `CREATE TABLE` 语句并选择数据库类型
- **THEN** 系统 MUST 返回解析出的表清单与列数

#### Scenario: 无数据源生成文件

- **WHEN** 使用者仅提供表结构、表与行数并生成
- **THEN** 系统 MUST 生成 `.sql` 文件，MUST NOT 要求 datasource_id

#### Scenario: 非法或不支持的 DDL

- **WHEN** DDL 解析失败，或包含 `DROP` / `TRUNCATE` / 视图等语句
- **THEN** 系统 MUST 拒绝并说明原因，MUST NOT 生成文件

#### Scenario: 手动模式仍受上限约束

- **WHEN** 手动模式的表数或行数超过配置上限
- **THEN** 系统 MUST 拒绝并说明限制

### Requirement: 造数菜单与数据文件界面

系统 SHALL 在侧边栏提供顶层「造数」菜单，位于「SQL」菜单之后，包含「数据文件」与「测试库」两个子菜单，**数据文件在上**。

「数据文件」页面 SHALL 支持两种模式：**从数据源**与**手动输入表结构**，二者可切换。数据源模式支持选择数据源；手动模式支持选择数据库类型、粘贴 DDL 并解析表结构。两种模式都支持勾选表、逐表设置行数、设置可选随机种子与是否包含建表语句；生成后展示文件名、总行数、警告，并提供下载按钮。表列表 MUST 支持按表名/注释模糊搜索。

普通用户的页面数据源下拉 MUST 只列出自己创建的数据源；管理员 MAY 看到全部。手动模式不展示数据源下拉。

#### Scenario: 菜单结构

- **WHEN** 登录用户查看侧边栏
- **THEN** 「造数」主菜单 MUST 位于「SQL」之后
- **AND** 展开后 MUST 有两个子菜单「数据文件」「测试库」，数据文件排在前面

#### Scenario: 普通用户只看到自己的数据源

- **WHEN** 普通用户进入「数据文件」页面
- **THEN** 数据源下拉 MUST 只包含该用户创建的数据源

#### Scenario: 模式切换

- **WHEN** 使用者在「从数据源」与「手动输入表结构」之间切换
- **THEN** 页面 MUST 清空上一次的表选择与生成结果，MUST NOT 混用两种来源

#### Scenario: 手动模式解析后生成

- **WHEN** 使用者在手动模式粘贴 DDL、选择数据库类型并点击解析
- **THEN** 页面 MUST 展示解析出的表清单
- **AND** 勾选表与行数后可生成并下载 `.sql` 文件

#### Scenario: 生成并下载

- **WHEN** 使用者选择表与行数并点击生成
- **THEN** 页面 MUST 展示文件名、总行数与警告
- **AND** 点击下载 MUST 得到 `.sql` 文件

#### Scenario: 表名搜索

- **WHEN** 表数量较多时输入表名或注释关键字
- **THEN** 表列表 MUST 只显示匹配项，且已勾选的表不因过滤丢失


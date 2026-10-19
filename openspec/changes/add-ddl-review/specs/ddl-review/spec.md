## ADDED Requirements

### Requirement: DDL 评审的输入与方言边界

系统 SHALL 接受一条或多条 DDL 语句作为评审输入，并在**不执行**的前提下给出评审结果。第一版 MUST 只支持 PostgreSQL；其他方言 MUST 明确说明暂不支持，MUST NOT 静默按 PostgreSQL 处理。

#### Scenario: 评审一条 CREATE TABLE

- **WHEN** 使用者提交一条 `CREATE TABLE` 语句
- **THEN** 系统返回该语句的评审结果，包含设计问题、语句风险与变更摘要
- **AND** 目标库上 MUST NOT 执行任何语句

#### Scenario: 评审多条 DDL

- **WHEN** 使用者提交多条 DDL 语句
- **THEN** 系统按语句顺序逐条评审，并为每条语句给出独立结果

#### Scenario: MySQL 明确不支持

- **WHEN** 数据源类型为 MySQL，或显式指定的方言为 MySQL
- **THEN** 系统返回业务失败（code 4017），说明 DDL 评审目前只支持 PostgreSQL
- **AND** MUST NOT 按 PostgreSQL 解析后给出可能错误的结论

#### Scenario: 非 DDL 语句不参与评审

- **WHEN** 输入中混有 `SELECT` / `INSERT` / `UPDATE` / `DELETE` 等非 DDL 语句
- **THEN** 该语句 MUST 被标记为「未参与评审」并出现在响应的未评审列表中
- **AND** MUST NOT 静默忽略，也 MUST NOT 让使用者误以为它被评审过

### Requirement: 基于快照的顺序合并

系统 SHALL 以数据源最近一次采集快照为基线，按语句顺序把 DDL 应用到内存表结构。出现 `ALTER` / `INDEX` / `DROP` / `TRUNCATE` 时 MUST 要求提供数据源，且该数据源 MUST 已有采集快照；纯 `CREATE TABLE` 脚本 MAY 不绑定数据源。

#### Scenario: 改表必须绑定已采集的数据源

- **WHEN** 输入包含 `ALTER TABLE` 且未提供数据源
- **THEN** 系统返回业务失败（code 4017），说明改表评审必须先选择数据源
- **AND** MUST NOT 在缺少表结构的情况下猜测结论

#### Scenario: 数据源尚未采集

- **WHEN** 提供了数据源，但该数据源没有任何采集快照
- **THEN** 系统返回业务失败（code 4017），说明请先执行采集

#### Scenario: 目标表不在快照中

- **WHEN** DDL 要修改或删除的表不在最近一次快照中
- **THEN** 该语句 MUST 被标记为无法应用，说明该表可能是采集之后新建的，请先重新采集
- **AND** 若因此没有任何语句可评审，总体结论 MUST 为「无法评审」
- **AND** 其他环境问题（未选数据源、未采集、方言不支持）MUST 单独按业务失败处理

#### Scenario: 顺序应用生效

- **WHEN** 同一输入中先 `CREATE TABLE` 再 `ALTER TABLE` 该表
- **THEN** 第二条语句 MUST 基于第一条语句创建后的内存表结构进行评审
- **AND** MUST NOT 把第一条语句创建的表判为「不存在」

#### Scenario: 无法应用即停止后续

- **WHEN** 某条语句无法合并到当前内存表结构（例如目标表不存在、列不存在）
- **THEN** 该语句 MUST 被标记为无法应用并说明原因
- **AND** 后续语句 MUST 标记为未评审，MUST NOT 基于不可信的状态继续给出结论

### Requirement: 只报本次新引入或加重的设计问题

系统 SHALL 在变更前与变更后的表结构上分别运行适用于 DDL 的库表规则，且只上报本次变更**新出现**或**级别升高**的设计问题。变更前已存在的问题 MUST NOT 重复列出，但摘要 MUST 给出这类问题的数量。

#### Scenario: 新增无主键表

- **WHEN** 评审一条创建后没有主键的 `CREATE TABLE`
- **THEN** 结果中 MUST 出现「无主键表」问题

#### Scenario: 加列导致宽表

- **WHEN** 一条 `ALTER TABLE ADD COLUMN` 使表的列数超过宽表阈值
- **THEN** 结果中 MUST 出现「宽表」问题，并标记为本次新增

#### Scenario: 已有问题不重复

- **WHEN** 被修改的表在变更前已经存在「无主键」问题，本次变更没有触及主键
- **THEN** 该问题 MUST NOT 出现在本次评审的问题清单中
- **AND** 摘要 MUST 说明该表已有问题未重复列出

#### Scenario: 规则级别升高算加重

- **WHEN** 某条问题在变更前已存在，本次变更使其严重级别升高
- **THEN** 该问题 MUST 以「加重」标记出现在结果中

#### Scenario: 停用的规则不参与

- **WHEN** 某条适用于 DDL 的库表规则在规则注册表中被停用
- **THEN** DDL 评审 MUST NOT 产出该规则的问题
- **AND** 响应 MUST 列出被停用的规则，使使用者知道这次少跑了什么

#### Scenario: 规则作用域由声明决定

- **WHEN** 一条库表规则没有声明 DDL 作用域
- **THEN** DDL 评审 MUST NOT 运行它
- **AND** 规则清单接口 MUST 能看出它是否适用于 DDL 评审

### Requirement: 语句操作风险

系统 SHALL 独立于设计问题给出语句级操作风险，至少覆盖：执行阻断、依赖影响与大表操作。每条风险 MUST 带稳定标识、严重级别、是否阻断、对象定位、说明与建议。

#### Scenario: 删表仍有依赖

- **WHEN** `DROP TABLE` 的目标表仍被其他表的外键引用
- **THEN** 系统 MUST 列出引用它的表与约束，并说明未带 `CASCADE` 时会执行失败

#### Scenario: 删列参与索引或约束

- **WHEN** `DROP COLUMN` 的列参与索引、主键或外键
- **THEN** 系统 MUST 列出这些依赖，并说明删除的影响

#### Scenario: 非空表加 NOT NULL 且无默认值

- **WHEN** 对一个已有数据的表 `ADD COLUMN ... NOT NULL` 且未提供默认值
- **THEN** 系统 MUST 标记为执行阻断，说明该语句会失败

#### Scenario: 大表建索引未带 CONCURRENTLY

- **WHEN** 对达到大表体量的表执行 `CREATE INDEX` 且未带 `CONCURRENTLY`
- **THEN** 系统 MUST 提示该操作会阻塞写入，并给出体量依据

#### Scenario: 大表加外键未带 NOT VALID

- **WHEN** 对达到大表体量的表 `ADD CONSTRAINT ... FOREIGN KEY` 且未带 `NOT VALID`
- **THEN** 系统 MUST 提示该操作会扫描全表并持锁，并给出体量依据

#### Scenario: 修改列类型收窄

- **WHEN** `ALTER COLUMN ... TYPE` 改变了类型族，或收窄了字符长度 / 数值精度
- **THEN** 系统 MUST 提示可能丢失数据，并说明执行前需要核对现有数据

### Requirement: 未评审内容必须如实说明

系统 MUST 在结论与响应中说明本次**没有**评审的内容及其原因，MUST NOT 把「未评审」呈现成「没有问题」。

#### Scenario: 未绑定数据源

- **WHEN** 纯建表脚本没有绑定数据源
- **THEN** 系统 MUST 说明依赖整库上下文的检查（如级联删除链、依赖影响）未进行

#### Scenario: 快照过期

- **WHEN** 评审依据的快照采集时间距当前较久
- **THEN** 系统 MUST 显示快照采集时间，并提示可重新采集以获得更准确的结论
- **AND** MUST NOT 因快照过期而拒绝评审

#### Scenario: 解析失败

- **WHEN** DDL 无法解析
- **THEN** 结论 MUST 为「无法评审」，并给出带行列位置的语法错误
- **AND** MUST NOT 呈现为空问题清单或「未发现新增问题」

#### Scenario: 不支持的 DDL 形态

- **WHEN** 输入包含 `CREATE TABLE AS` / `LIKE` / `INHERITS` / `PARTITION OF`、视图、序列、类型或函数等不支持的形态
- **THEN** 该语句 MUST 出现在未评审列表中并说明原因

### Requirement: 不执行、不落库、不调用模型

DDL 评审 MUST 只基于采集快照做纯计算：MUST NOT 连接目标库执行任何语句，MUST NOT 持久化评审结果，MUST NOT 调用大模型，MUST NOT 修改数据源、快照或规则配置。

#### Scenario: 不连接目标库

- **WHEN** 使用者发起 DDL 评审
- **THEN** 目标库上 MUST NOT 产生任何连接或查询

#### Scenario: 不加可覆盖项

- **WHEN** 规则注册表中没有任何 DDL 相关记录
- **THEN** 评审 MUST 照常使用代码声明的默认值运行
- **AND** MUST NOT 因为评审而写入 `analysis_rule`

#### Scenario: 评审结果不保存

- **WHEN** 使用者离开页面后再次进入
- **THEN** 系统 MUST NOT 提供此前评审记录的查询入口

### Requirement: DDL 评审界面

系统 SHALL 提供独立页面用于 DDL 评审，页面 MUST 让使用者选择可选的数据源、输入一条或多条 DDL，并按语句展示评审结果。

#### Scenario: 页面可用

- **WHEN** 使用者进入 DDL 评审页
- **THEN** 页面提供数据源选择、SQL 输入与评审入口

#### Scenario: 结果按语句呈现

- **WHEN** 评审完成
- **THEN** 页面按语句展示应用状态、变更摘要、新增或加重的设计问题与语句风险
- **AND** 页面展示总体结论与快照采集时间

#### Scenario: 不使用「通过」措辞

- **WHEN** 本次没有发现新增问题
- **THEN** 结论 MUST 使用「未发现新增问题」之类的措辞
- **AND** MUST NOT 使用「通过」「正确」等表示 DDL 已被完整验证的说法

#### Scenario: 未评审内容可见

- **WHEN** 存在未参与评审或无法应用的内容
- **THEN** 页面 MUST 明确列出它们及原因

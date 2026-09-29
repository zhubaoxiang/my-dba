## ADDED Requirements

### Requirement: SQL 解析与格式化

系统 SHALL 接受任意 SQL 语句（查询、DML、DDL 不限），按指定方言解析为语法树并给出美化后的语句。解析失败时 MUST 指出错误位置与原因，MUST NOT 仅返回「解析失败」。

#### Scenario: 按方言解析

- **WHEN** 用户提交一条 SQL 并指定方言
- **THEN** 系统 MUST 按该方言解析，MUST NOT 用其他方言的规则判定合法性

#### Scenario: 语法错误指出位置

- **WHEN** 提交的 SQL 存在语法错误
- **THEN** 系统 MUST 返回错误位置与可读原因，MUST 让使用者知道该改哪里

#### Scenario: 输出美化后的语句

- **WHEN** 提交的 SQL 语法正确
- **THEN** 系统 MUST 返回格式化后的语句，且 MUST NOT 改变原语句的语义

#### Scenario: 解析失败仍给出有用信息

- **WHEN** 提交的 SQL 无法被解析（语法确实有错，或超出解析器的覆盖范围）
- **THEN** 系统 MUST 仍返回语法错误信息与原始语句，并把规则分析与结构校验标记为「未能进行」并说明原因
- **AND** MUST NOT 因解析失败而整体报错、什么也不给

### Requirement: SQL 静态规则分析

系统 SHALL 用**声明式规则**在语法树上判定语句的规范与性能问题，产出带稳定规则标识与严重级别的问题清单。同一输入 MUST 产出同一结果。

#### Scenario: 问题清单带稳定标识

- **WHEN** 规则判定出问题
- **THEN** 每条问题 MUST 带稳定的规则 `code`、级别、成因说明与改进建议，MUST NOT 只有一句模糊提示

#### Scenario: 结果可复现

- **WHEN** 对同一条 SQL 重复分析
- **THEN** 问题清单 MUST 完全一致（判定不依赖模型，不受随机性影响）

#### Scenario: 危险的写操作必须报出

- **WHEN** 提交的语句包含无 `WHERE` 的 `UPDATE`/`DELETE`、破坏性 DDL（`DROP`/`TRUNCATE`）等高风险写法
- **THEN** 系统 MUST 以最高级别报出，并说明后果

#### Scenario: 覆盖各类语句

- **WHEN** 提交的是查询、DML 或 DDL
- **THEN** 系统 MUST 都做规则判定，MUST NOT 只支持查询

#### Scenario: 未绑定数据源也能判定

- **WHEN** 未指定任何数据源
- **THEN** 基于语法本身即可判定的规则 MUST 照常产出
- **AND** 需要表结构才能判定的规则 MUST 被跳过并说明原因

#### Scenario: JOIN 的判定

- **WHEN** 语句包含 JOIN
- **THEN** 系统 MUST 判定是否存在无连接条件的 JOIN，并 MUST 在能取得表结构时判定两侧连接键的类型是否匹配

#### Scenario: GROUP BY 的判定

- **WHEN** 语句包含 GROUP BY
- **THEN** 系统 MUST 判定是否遗漏了未聚合的列，并 MUST 判定 GROUP BY 的列与 SELECT 中的非聚合列是否一致

### Requirement: 已纳管库的结构校验

系统 SHALL 在指定数据源且该数据源已有采集快照时，校验语句引用的表与列是否存在、类型是否可用。无法校验时 MUST 说明原因，MUST NOT 静默跳过。

#### Scenario: 校验表与列是否存在

- **WHEN** 指定了已采集的数据源
- **THEN** 系统 MUST 校验语句引用的表与列在该库中确实存在，并指出不存在的对象

#### Scenario: 指出类型不匹配

- **WHEN** 语句中对列的比较或运算存在明显的类型不匹配
- **THEN** 系统 MUST 报出，并说明涉及的列与类型

#### Scenario: 指出连接键类型不匹配

- **WHEN** JOIN 两侧连接键的列类型不一致
- **THEN** 系统 MUST 报出并指出涉及的列与类型，说明可能带来的隐式转换或索引失效

#### Scenario: 未指定数据源时说明跳过原因

- **WHEN** 未指定数据源
- **THEN** 系统 MUST 明确告知结构校验未进行及原因，MUST NOT 让使用者误以为已校验过

#### Scenario: 数据源尚未采集时说明

- **WHEN** 指定的数据源还没有采集快照
- **THEN** 系统 MUST 告知需要先采集，MUST NOT 报成语句的错误

### Requirement: 只读试运行与执行计划

系统 SHALL 在**使用者明确要求**且指定了数据源时执行试运行：**只读语句真跑**并返回受限的行数与耗时，**非只读语句只返回执行计划、绝不执行**。系统 MUST NOT 在分析请求中自动执行任何语句。

#### Scenario: 必须显式触发

- **WHEN** 用户只请求分析、未请求试运行
- **THEN** 系统 MUST NOT 连接目标库执行任何语句

#### Scenario: 只读语句真跑

- **WHEN** 用户请求试运行且语句为只读
- **THEN** 系统 MUST 在只读会话中执行，返回实际行数与耗时，且 MUST 限制返回行数

#### Scenario: 非只读语句只出计划

- **WHEN** 用户请求试运行且语句为 DML 或 DDL
- **THEN** 系统 MUST 只返回执行计划，MUST NOT 执行该语句
- **AND** MUST 明确告知该语句未被执行及其原因

#### Scenario: 多语句按最严格的一条处理

- **WHEN** 提交的 SQL 含多条语句且其中至少一条非只读
- **THEN** 系统 MUST NOT 执行其中任何一条，全部只返回计划

#### Scenario: 执行受超时与行数限制

- **WHEN** 执行试运行
- **THEN** 语句 MUST 受超时约束，返回行数 MUST 有上限，MUST NOT 因一条语句拖垮目标库

#### Scenario: 拿不到计划时如实说明

- **WHEN** 目标库拒绝或无法为某条语句生成计划
- **THEN** 系统 MUST 如实说明未能取得计划，MUST NOT 把它伪装成分析失败或空结果

### Requirement: 大模型解读与规则判定的分区

系统 SHALL 用大模型解读规则命中的问题，并 MAY 补充规则未覆盖的观察。模型产出的内容 MUST 与规则判定的问题清单在响应结构中**分开**，模型推测 MUST 被标注为未经规则验证。

#### Scenario: 两类结论结构上分开

- **WHEN** 一次分析同时产出规则判定与模型解读
- **THEN** 二者 MUST 位于响应中不同的字段，MUST NOT 混在同一个列表里

#### Scenario: 模型推测必须标注

- **WHEN** 模型提出了规则未覆盖的观察
- **THEN** 该观察 MUST 被标注为未经规则验证，使用者 MUST NOT 将其误认为规则结论

#### Scenario: 未配置对话模型时不静默降级

- **WHEN** 没有生效中的对话模型
- **THEN** 规则判定结果 MUST 照常返回，解读部分 MUST 说明未配置模型并指出去哪里配置
- **AND** MUST NOT 让使用者以为解读已包含在内

### Requirement: SQL 分析界面

系统 SHALL 提供前端页面：输入 SQL、选择方言与数据源、查看格式化结果、问题清单、结构校验结果、执行计划与解读分区，并支持复制格式化后的语句。

#### Scenario: 页面可用

- **WHEN** 用户进入 SQL 分析页面
- **THEN** 页面 MUST 提供 SQL 输入、方言与数据源选择，并能发起分析

#### Scenario: 格式化结果可复制

- **WHEN** 分析完成
- **THEN** 页面 MUST 展示格式化后的语句，并 MUST 提供复制手段

#### Scenario: 问题清单分级展示

- **WHEN** 规则判定出问题
- **THEN** 页面 MUST 按严重级别展示，并给出成因与改进建议

#### Scenario: 试运行需显式操作

- **WHEN** 用户希望试运行
- **THEN** 页面 MUST 要求用户主动触发，MUST NOT 在分析时自动执行

#### Scenario: 解读与判定分区呈现

- **WHEN** 页面同时展示规则判定与模型产出
- **THEN** 两者 MUST 在视觉上分区，模型推测 MUST 带明确标注

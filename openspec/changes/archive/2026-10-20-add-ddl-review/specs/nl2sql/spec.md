## ADDED Requirements

### Requirement: DDL 生成结果可转到 DDL 评审

当生成结果是 DDL 时，系统 SHALL 在结果区提供转到 DDL 评审的入口，并在交接时带上生成的语句与当前选择的数据源。NL2SQL 本身 MUST NOT 声称已经对 DDL 做过结构校验；它也 MUST NOT 在交接过程中执行任何语句。

#### Scenario: DDL 结果显示评审入口

- **WHEN** 生成结果是一条或多条 DDL
- **THEN** 结果区 MUST 提供「去 DDL 评审」入口
- **AND** 非 DDL 结果 MUST NOT 显示该入口

#### Scenario: 交接保留输入上下文

- **WHEN** 使用者点击「去 DDL 评审」
- **THEN** DDL 评审页 MUST 带入刚才生成的语句与当时选择的数据源
- **AND** 使用者 MAY 在评审前继续编辑语句

#### Scenario: 不把生成结果说成已校验

- **WHEN** 生成结果是 DDL
- **THEN** NL2SQL 的结论与校验说明 MUST 继续如实说明其校验只覆盖结构，或没有结构校验
- **AND** MUST NOT 因为提供了评审入口就暗示 DDL 已经通过验证

#### Scenario: 交接不执行语句

- **WHEN** 使用者从 NL2SQL 转到 DDL 评审
- **THEN** 目标库上 MUST NOT 执行任何语句

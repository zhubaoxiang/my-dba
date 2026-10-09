## MODIFIED Requirements

### Requirement: 分析规则规模可见

系统 SHALL 在首页展示两条分析能力各自的**规则条目数**，使「系统在替使用者检查什么、检查得多细」可见。规则条目数 SHALL 取自规则注册表的**代码声明**，MUST NOT 依赖规则是否已同步到库。条目数取不到时 MUST 如实标注，MUST NOT 显示成 0。

#### Scenario: 展示两类规则的条目数

- **WHEN** 首页加载完成
- **THEN** MUST 展示库表分析规则与 SQL 分析规则各自的条目数
- **AND** 条目数 MUST 反映代码声明里实际存在的规则条数，MUST NOT 因尚未同步到库而显示为 0

#### Scenario: 两类规则都提供清单入口

- **WHEN** 首页加载完成
- **THEN** 两类规则各自的条目数旁 MUST 都提供进入规则清单页的入口

#### Scenario: 条目数取不到时如实标注

- **WHEN** 规则条目数因查询失败而取不到
- **THEN** MUST 标注为不可用，MUST NOT 显示成「0 条」

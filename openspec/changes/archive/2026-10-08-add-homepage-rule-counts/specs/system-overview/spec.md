## ADDED Requirements

### Requirement: 分析规则规模可见

系统 SHALL 在首页展示两条分析能力各自的**规则条目数**，使「系统在替使用者检查什么、检查得多细」可见。规则条目数 SHALL 取自规则注册表的**代码声明**，MUST NOT 依赖规则是否已同步到库。条目数取不到时 MUST 如实标注，MUST NOT 显示成 0。

#### Scenario: 展示两类规则的条目数

- **WHEN** 首页加载完成
- **THEN** MUST 展示库表分析规则与 SQL 分析规则各自的条目数
- **AND** 条目数 MUST 反映代码声明里实际存在的规则条数，MUST NOT 因尚未同步到库而显示为 0

#### Scenario: 提供规则清单入口

- **WHEN** 某类规则存在可供查看与调整的清单页
- **THEN** 该条目数旁 MUST 提供进入该清单页的入口

#### Scenario: 条目数取不到时如实标注

- **WHEN** 规则条目数因查询失败而取不到
- **THEN** MUST 标注为不可用，MUST NOT 显示成「0 条」

## MODIFIED Requirements

### Requirement: 总览数据一次取回

系统 SHALL 通过**单个**接口返回首页所需的全部数据，使首页只发一次请求。

#### Scenario: 单次请求取回全部总览数据

- **WHEN** 首页加载
- **THEN** 系统 MUST 通过一个接口返回就绪状态、概览数字、库列表、指标、知识库列表与规则概况，MUST NOT 要求前端分别请求多次

#### Scenario: 内容超出即滚动

- **WHEN** 库、知识库或规则的数量超出首屏可容纳的范围
- **THEN** 页面 MUST 整体纵向滚动即可，MUST NOT 为了「一屏放下」压缩每一条信息的呈现

> 本条替换原先「列表 MUST 在限定高度内滚动，MUST NOT 把首屏撑得过长」。原写法与已归档的 `add-homepage-overview` 的 D8（页面接受滚动，不为「一屏放下」压缩信息）相矛盾，实现一直按 D8 走。

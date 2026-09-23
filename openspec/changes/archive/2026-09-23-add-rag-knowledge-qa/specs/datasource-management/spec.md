## RENAMED Requirements

- FROM: `### Requirement: 数据源接口鉴权交由 BSA 平台`
- TO: `### Requirement: 接口不做应用层鉴权`

## MODIFIED Requirements

### Requirement: 接口不做应用层鉴权

业务接口 SHALL 继承 `baseviews.AnyLogin`，系统内部 MUST NOT 做认证与角色校验。原因：本仓库没有任何代码会产出 `Token` 请求头，任何基于 `IsAuthenticated` / `IsAdminUser` 的校验都会让接口恒返回 4003。访问控制**依赖网络隔离**——服务只允许部署在内网，MUST NOT 直接暴露到公网或不可信网络。

#### Scenario: 系统内不因缺少 Token 而拒绝请求

- **WHEN** 请求到达业务接口且未携带 `Token` 请求头
- **THEN** 系统不返回 4003，而是正常执行业务逻辑并返回统一响应结构

#### Scenario: 停用的数据源不可采集

- **WHEN** 对 `is_enabled = False` 的数据源触发采集
- **THEN** 系统返回业务失败提示（code 4017），不执行采集

#### Scenario: 非法请求仍被参数校验拦截

- **WHEN** 请求提交的数据源配置缺少必填字段或格式非法
- **THEN** 系统返回参数错误（code 4000），且数据不发生变更

#### Scenario: 部署边界被明确记录

- **WHEN** 查阅项目文档或部署说明
- **THEN** MUST 能查到「接口无应用层鉴权，访问控制依赖网络隔离，只允许内网部署」这一约束及其后果说明

## REMOVED Requirements

### Requirement: 接口不做应用层鉴权

**Reason**: 系统已增加 JWT 登录认证，业务接口改为要求有效登录，不再把网络隔离作为唯一访问控制。

**Migration**: 所有业务接口改为要求有效 JWT；`POST /v1/auth/login` 公开。部署时配置 `[auth] jwt_secret`，LDAP 可选。

## ADDED Requirements

### Requirement: 接口要求登录

业务接口 SHALL 要求有效的 JWT 认证；未携带、无效或过期的 `Token` 请求头 MUST 返回业务失败（code 4003），MUST NOT 执行业务逻辑。登录接口本身 MUST 公开。访问控制不再依赖网络隔离；服务仍建议只部署在内网或受控网络。

#### Scenario: 未登录被拒绝

- **WHEN** 请求到达业务接口且未携带 `Token` 请求头
- **THEN** 系统返回 4003，不返回业务数据

#### Scenario: 有效 token 可访问

- **WHEN** 请求携带有效 JWT
- **THEN** 系统正常执行业务逻辑并返回统一响应结构

#### Scenario: 登录接口公开

- **WHEN** 未登录客户端调用 `POST /v1/auth/login`
- **THEN** 系统正常处理登录请求，不要求 `Token`

#### Scenario: 停用的数据源不可采集

- **WHEN** 对 `is_enabled = False` 的数据源触发采集
- **THEN** 系统返回业务失败提示（code 4017），不执行采集

#### Scenario: 非法请求仍被参数校验拦截

- **WHEN** 请求提交的数据源配置缺少必填字段或格式非法
- **THEN** 系统返回参数错误（code 4000），且数据不发生变更

#### Scenario: 部署边界被明确记录

- **WHEN** 查阅项目文档或部署说明
- **THEN** MUST 能查到「接口要求登录；服务仍建议只部署在内网或受控网络」这一约束

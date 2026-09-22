# Change: 数据源对接与库表元数据采集分析

## Why

数据库管理平台当前没有任何数据库实例纳管能力，用户无法了解被管理库的表结构、索引、数据量与关联状况。这同时是「数据库智能体」四个能力中 SQL 规范/性能分析与 NL2SQL 的共同前置——缺少 schema 与索引信息时，性能分析只能停留在静态语法层，无法判断索引缺失、关联设计等问题。

## What Changes

- 新增**数据源配置管理**：数据源 CRUD、连接测试、凭据可逆加密存储、软删除
- 新增**元数据采集**：通过只读独立连接采集模式/表清单、列定义、主键、唯一约束、外键关联、索引、行数与体积
- 新增**元数据快照**：每次采集生成不可变快照，支持历史快照查询与两快照结构差异对比
- 新增**库表健康问题分析**：无主键表、外键缺索引、重复/冗余索引、疑似未使用索引、超大表、可疑字段类型、孤立表
- 新增**异步采集任务**与任务状态/失败原因查询
- 新增**前端页面**：数据源管理、库表总览、表详情、问题清单
- 新增依赖 PyMySQL（MySQL 方言驱动）

## Non-Goals

本次**不做**以下内容（拆为后续独立 change）：

| 能力 | 规划 change-id | 与本次关系 |
|------|---------------|-----------|
| RAG 知识问答 | `add-rag-knowledge-qa` | 无依赖，可并行 |
| 自然语言转建表语句/SQL | `add-nl2sql` | 依赖本次的元数据 |
| SQL 规范与性能分析 | `add-sql-analysis` | 依赖本次的元数据（性能分析部分） |

其他明确的非目标：

- 不对目标库执行任何 DDL/DML，不支持在目标库上执行用户自定义 SQL
- 不引入 pgvector 与向量检索（属 RAG change）
- 不支持 PostgreSQL / MySQL 之外的数据库类型
- 不做定时自动采集（本期仅手动触发）

## Impact

- 受影响能力（均为新增）：`datasource-management`、`metadata-catalog`
- 受影响代码：
  - 新增 `src/apps/datasource/`（`models.py`、`serializers.py`、`views.py`、`services.py`、`analyzer.py`、`collectors/`）
  - 新增 `src/utils/crypto.py`（可逆加密工具）
  - 修改 `src/config/urls.py`（注册两个 ViewSet 路由）
  - 修改 `src/config/conf.ini`（加密密钥、分析阈值配置项）
  - 修改 `src/sql/pg_struct.sql`（新增 4 张表 DDL）
  - 修改 `src/requirements.txt`（PyMySQL）
  - 修改 `src/right_config.json`（BSA 菜单项）
  - 新增 `static/src/api/datasource.js`、`static/src/views/datasource/*`、修改 `static/src/router/index.js`
- **不需要**修改 `src/settings/settings.py`：所有业务模块挂在单一 `apps` Django app 下，`INSTALLED_APPS` 只有 `"apps"` 一项，新增模块会自动被识别
- **与脚手架规则的偏差（需评审确认）**：数据源密码需要**可逆**加密，与 `security.md`「必须使用 `CommonUtils.password_encrypt`」冲突（该函数为 MD5 单向哈希，无法回连目标库）。详见 design.md D1。

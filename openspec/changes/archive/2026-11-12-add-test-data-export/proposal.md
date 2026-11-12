# Change: 造数菜单与测试数据文件导出

## Why

测试人员有时无法直接把数据写进目标环境（权限、网络或环境审批限制），需要一份可下载的数据文件自己导入。现有「测试库预置」只负责在测试数据源上新建库，没有提供「只生成数据文件」的出口；同时它被放在「库表」组，入口位置也和「造数」的定位不符。

## What Changes

- 侧边栏新增**顶层「造数」菜单**，位于「SQL」组下方，包含两个子菜单：
  - **测试库**：现有测试库预置能力
  - **测试数据**：生成可下载的 SQL 数据文件
- 新增测试数据文件导出，支持两种表结构来源：
  - **数据源**：选择自己创建的数据源（管理员可选全部），使用最新快照
  - **手动输入**：粘贴 `CREATE TABLE` / `CREATE INDEX` / `ALTER TABLE` 语句，不依赖数据源
  - 两种方式都支持表选择、逐表行数、可选随机种子与「包含建表语句」
  - 外键按拓扑顺序生成；未选中的被引用表给出明确警告
  - 复用现有语义推断、类型/长度/唯一/非空约束处理与行数上限
  - **不连接目标库、不执行任何语句**；文件由使用者下载后手动导入
- 新增前端「数据文件」页面与下载功能

## Impact

- 受影响能力：**新增** `test-data-export`；`test-db-provisioning` 的页面菜单位置调整为「造数 → 测试库」
- 受影响代码：
  - `src/apps/provisioning/services.py`：导出生成、SQL 字面量渲染、手动 DDL 解析
  - `src/apps/provisioning/serializers.py`、`views.py`：解析与文件导出接口
  - 复用 `apps/ddlreview` 的纯解析/合并能力（不连接、不执行）
  - `src/config/urls.py`：`/v1/test-data`
  - `static/src/views/provisioning/test-data.vue`、`api/provisioning.js`、`router/index.js`、`layout/index.vue`
  - `docs/test-data-export.md`（新增）、`docs/test-db-provisioning.md`、README
- 无新依赖、无 DDL、无部署形态变化

## 明确不做

- **不执行、不连接目标库**：只生成文件；导入由使用者完成
- **不自动导入**：不提供远程执行、不写任何目标库
- **不接受任意 SQL 执行**：手动输入只解析 `CREATE TABLE` / `CREATE INDEX` / `ALTER TABLE` 作为结构定义；`DROP` / `TRUNCATE` / 视图等直接拒绝
- **不依赖数据源的手动模式不做归属限制**：任何登录用户都可使用，只生成文件
- **不复制源库数据**：只读取快照元数据，生成的是合成数据
- **不保证完整结构**：与测试库一致，CHECK / 触发器 / 视图 / 序列等不还原

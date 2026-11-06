# 测试数据文件导出

按**数据源最新快照**或**手动输入的表结构**生成一份可下载的 `.sql` 数据文件，由使用者自行导入目标环境。系统**只读元数据、只生成文件**：不连接目标库、不执行任何语句、不读取源库业务数据。

对应模块：`src/apps/provisioning/`（`TestDataView`）；菜单位于**「造数 → 数据文件」**（在「测试库」上方）。

## 与「测试库」的分工

| | 测试库 | 测试数据 |
|---|---|---|
| 是否连接目标库 | 是（建库、写数据） | **否** |
| 是否需要测试环境 | 是 | **否** |
| 是否需要预置写账号 | 是 | **否** |
| 输出 | 目标环境上的独立测试库 | 可下载的 SQL 文件 |
| 导入方式 | 系统执行 | 使用者手动导入 |

「测试库」的安全边界见 [test-db-provisioning.md](test-db-provisioning.md)；本能力只负责生成文件。

## 权限

- 登录用户可用；普通用户只能选择**自己创建的数据源**，管理员可选全部。
- 不要求数据源为测试环境，也不要求配置预置写账号——只读快照，不写入任何库。
- 未登录返回 4003，越权返回 4005。

## 两种表结构来源

页面顶部可切换：

- **从数据源**：选择自己创建的数据源（管理员可选全部），使用最新快照
- **手动输入表结构**：选择 PostgreSQL / MySQL，粘贴 `CREATE TABLE` / `CREATE INDEX` / `ALTER TABLE`，点击「解析表结构」

手动模式**不依赖数据源**，任何登录用户都可以使用；它只解析结构，不会连接或执行。支持范围：

- `CREATE TABLE`（列、主键、唯一、外键、NOT NULL、默认值）
- `CREATE INDEX`
- `ALTER TABLE` 中受 DDL 评审支持的变更动作（用于补充结构定义）
- 注释类语句会被忽略并提示；`DROP` / `TRUNCATE` / 视图等其他语句直接拒绝

> 手写 DDL 的列/表注释不会被解析，语义推断只能依据列名。

## 使用流程

1. 进入「造数 → 数据文件」，选择「从数据源」或「手动输入表结构」
2. 数据源模式选择数据源；手动模式粘贴 DDL 并点击「解析表结构」
3. 可选设置随机种子与「包含建表语句」
4. 勾选表，逐表设置行数（支持按表名/注释模糊搜索）
5. 点击「生成 SQL 文件」
6. 查看文件名、总行数与警告，点击「下载 SQL 文件」
7. 自行确认目标环境后导入

### 随机种子

随机种子用于**复现**：数据源/表结构、表集合、行数与种子都相同时，生成的数据相同；不填则每次随机。想复现上一次结果时，把当时的种子填回来即可。

## 文件内容

```sql
-- 由 my-dba 生成的测试数据文件
-- 数据源：<名称>（PostgreSQL / MySQL）
-- 快照采集时间：2026-11-01 10:00:00
-- 表数量：2，总行数：200
-- 系统不会执行本文件；请确认目标环境后再手动导入。
-- 警告：...
-- ===== DDL =====            （仅在勾选「包含建表语句」时出现）
CREATE TABLE ...;
-- ===== 数据 =====
INSERT INTO "public"."orders" ("id", "user_id") VALUES
(1, 1),
(2, 2);
```

- 数据按**外键拓扑顺序**生成，父表 INSERT 在子表之前
- 每 100 行一条多行 `INSERT`
- 字符串单引号转义；MySQL 额外转义反斜杠；布尔、日期时间、JSON、二进制按方言渲染
- 可选 DDL 复用测试库的近似建表逻辑，**不包含** `CREATE DATABASE`
- MySQL 文件头提示先 `USE` 目标数据库
- 文件不自动包 `BEGIN/COMMIT`：MySQL DDL 不可回滚，统一包事务会误导

## 近似结构与限制

与测试库共用快照，因此：

- **不会还原** CHECK 约束、触发器、视图、序列、扩展、权限、分区定义、自定义类型、数值精度
- 生成值是合成数据；语义推断（邮箱、手机、金额等）是启发式的，不保证业务正确
- 所选表的外键引用**未选中的表**时，生成的外键值可能无法通过目标库校验，响应与文件会明确警告
- 一次只支持同方言：PG 快照生成 PG 风格 SQL，MySQL 生成 MySQL 风格 SQL
- 行数受 `[provisioning]` 的 `max_tables` / `max_rows_per_table` / `max_total_rows` 限制
- 文件由使用者自行导入；系统不提供远程执行或自动导入

## API

前缀 `{SYS_NAME}/v1/test-data`，要求登录。

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/parse` | 解析手动输入的表结构，返回表清单摘要 |
| POST | `/file` | 生成文件内容（JSON 返回文件名与内容，前端转成下载） |

数据源模式请求示例：

```json
{
  "datasource_id": 1,
  "tables": [
    {"schema": "public", "name": "orders", "rows": 100},
    {"schema": "public", "name": "users", "rows": 20}
  ],
  "seed": 123456,
  "include_ddl": false
}
```

手动表结构模式请求示例（`datasource_id` 与 `ddl` 二选一）：

```json
{
  "db_type": 1,
  "ddl": "CREATE TABLE users (id integer PRIMARY KEY, email varchar(64) NOT NULL);",
  "tables": [{"schema": "public", "name": "users", "rows": 20}],
  "seed": 123456,
  "include_ddl": false
}
```

响应 `data` 包含：`filename`、`content`、`total_rows`、`tables`、`warnings`、`snapshot_collect_time` 等。前端用 `Blob` 下载，不在服务端落盘。

## 配置

沿用 `src/config/conf.ini` 的 `[provisioning]` 段（`max_tables`、`max_rows_per_table`、`max_total_rows` 等），见 [test-db-provisioning.md 的配置](test-db-provisioning.md#配置)。

## 已知限制

- 生成文件是**近似结构 + 合成数据**，不替代真实脱敏数据
- 手动表结构只支持 `CREATE TABLE` / `CREATE INDEX` / `ALTER TABLE`；注释不解析，`DROP` / `TRUNCATE` / 视图等会被拒绝
- 「包含建表语句」只适合空目标库；目标表已存在时请只生成数据
- MySQL 的字符集由目标库决定；文件头提示先 `USE` 目标数据库
- 文件内容较大时接口返回 JSON，受浏览器内存与请求超时限制；当前行数上限下可用

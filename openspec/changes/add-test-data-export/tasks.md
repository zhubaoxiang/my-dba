## 1. 后端

- [x] 1.1 `services.export_test_data()`：按快照选表、逐表行数、种子与限制；只读、不连接目标库
- [x] 1.2 SQL 字面量渲染与分批 `INSERT`；按外键拓扑顺序输出
- [x] 1.3 可选建表语句；未选中父表给出警告
- [x] 1.4 归属校验：普通用户限自己创建的数据源，管理员可全部；不要求测试环境/写账号
- [x] 1.5 `TestDataGenerateSerializer` 与 `TestDataView`（`StatelessView`），注册 `/v1/test-data`
- [x] 1.6 单测与接口测试：归属、文件内容、DDL 开关、外键顺序/警告、种子复现、限制、未登录 4003
- [x] 1.7 手动表结构：复用 `ddlreview` 解析 CREATE TABLE / INDEX / ALTER TABLE；拒绝 DROP/TRUNCATE；未限定 schema 的外键归一
- [x] 1.8 `POST /v1/test-data/parse` 解析表结构；`/file` 支持 `datasource_id` 或 `db_type+ddl` 二选一
- [x] 1.9 测试：解析成功/失败、手动导出、外键顺序、模式二选一校验

## 2. 前端

- [x] 2.1 新增 `views/provisioning/test-data.vue`：数据源 / 手动表结构两种模式
- [x] 2.2 手动模式：DB 类型、DDL 输入、解析表结构、表选择与逐表行数
- [x] 2.3 表名模糊搜索、已选数量、生成结果与警告
- [x] 2.4 下载 SQL 文件（Blob）
- [x] 2.5 菜单重构：顶层「造数」位于 SQL 下方，子菜单「数据文件」「测试库」（数据文件在上）
- [x] 2.6 `npm run build`

## 3. 文档与校验

- [x] 3.1 新增 `docs/test-data-export.md`
- [x] 3.2 更新 `docs/test-db-provisioning.md`（菜单位置）、`docs/architecture.md`、README
- [x] 3.3 `python src/manage.py test apps`
- [x] 3.4 `ruff check` / `ruff format --check` / 脚手架检查
- [x] 3.5 `openspec validate add-test-data-export --strict --no-interactive`
- [ ] 3.6 手工核对：真实快照上生成并下载文件；在目标环境导入验证（需目标环境）

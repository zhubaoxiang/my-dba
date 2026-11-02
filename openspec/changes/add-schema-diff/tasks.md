## 1. DDL 与模型

- [x] 1.1 `src/sql/pg_struct.sql`：`metadata_snapshot` 增加 `change_summary jsonb`
- [x] 1.2 `src/sql/patch.sql`：追加增量 DDL 与列注释
- [x] 1.3 `src/apps/datasource/models.py`：`MetadataSnapshot.change_summary`

## 2. 差异计算

- [x] 2.1 `differ.py` 增加 `TableKeyCollision`、`ignore_schema` 参数
- [x] 2.2 忽略 schema 时检测同名表碰撞并拒绝
- [x] 2.3 增加 `summarize_result()` / `summarize_diff()`
- [x] 2.4 纯函数单测：对齐、碰撞、摘要计数；原有用例不变

## 3. 采集摘要

- [x] 3.1 `run_collect_task()` 取上一次快照并计算摘要
- [x] 3.2 `_build_change_summary()`：首次采集 / 正常 / 异常降级
- [x] 3.3 `_save_snapshot()` 在 create 时写入摘要
- [x] 3.4 单测：三条路径

## 4. 接口

- [x] 4.1 `/catalog/diff` 响应扩展 `{base, target, diff, summary, warnings}`
- [x] 4.2 解析 `ignore_schema`；碰撞返回 4000
- [x] 4.3 `CollectTaskSerializer` 暴露 `change_summary`，任务列表 `select_related("snapshot")`
- [x] 4.4 接口测试：元信息、跨方言警告、ignore_schema、碰撞、缺参、404

## 5. 首页数据

- [x] 5.1 `list_datasource_status()` 带上 `change_summary`（`.only()` 同步）
- [x] 5.2 overview 测试确认字段透传

## 6. 前端

- [x] 6.1 新增 `views/datasource/diff.vue` 与路由
- [x] 6.2 采集任务弹窗展示结构变化摘要
- [x] 6.3 首页卡片「结构有变化」提示与跳转
- [x] 6.4 `npm run build` 通过

## 7. 文档与校验

- [x] 7.1 `docs/datasource.md`：接口契约、跨源口径、摘要、页面说明
- [x] 7.2 `docs/overview.md`：结构变化提示口径
- [x] 7.3 `python src/manage.py test apps.datasource apps.overview`（237 项通过）
- [x] 7.4 `python .ci/custom-checks/scaffold_check.py`
- [x] 7.5 `ruff check` 通过；改动文件已 `ruff format`
- [x] 7.6 `openspec validate add-schema-diff --strict --no-interactive`
- [ ] 7.7 手工核对：两次采集摘要、跨方言警告、ignore_schema、首页提示（需运行环境）

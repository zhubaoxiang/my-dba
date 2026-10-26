## 1. 数据模型与接口

- [x] 1.1 `qa_session` 增加可空 `datasource_id`：更新 `src/sql/pg_struct.sql` 与追加 `src/sql/patch.sql`
- [x] 1.2 `QaSession` 模型增加 `datasource_id` 整数字段，不建跨模块外键
- [x] 1.3 `QaSessionSerializer` 输出 `datasource_id` 与 `datasource_name`
- [x] 1.4 `ask` / `ask-stream` 解析数据源：请求显式携带时以请求为准并更新会话，未携带时沿用会话默认值
- [x] 1.5 新建会话时保存 `datasource_id`

## 2. 数据源读取能力

- [x] 2.1 在 `datasource/services.py` 增加数据源概况读取：表数量、模式数量、问题分布、采集时间
- [x] 2.2 增加问题清单读取：支持按表名与级别过滤，统一走 `muted_filter()`，限制返回条数与文本长度
- [x] 2.3 未采集、数据源不存在时返回可读结果而非抛异常

## 3. Agent 与工具

- [x] 3.1 新增 `get_datasource_summary` 工具并登记进度文案
- [x] 3.2 新增 `list_catalog_issues` 工具并登记进度文案
- [x] 3.3 调整 `_prepare` 路由：只选数据源时进入 agent；自动模式下知识库无命中但选了数据源时仍进入 agent
- [x] 3.4 `prepare_stream` 仅在存在知识库时做预检索
- [x] 3.5 有数据源时向系统提示词补充「必须用工具查询、不得编造」的约束
- [x] 3.6 工具调用改为显式收集到 `used_tools`，同步与流式都如实返回 `tools_used`

## 4. 前端

- [x] 4.1 问答页增加数据源下拉，加载数据源列表并标记停用项
- [x] 4.2 提问时显式提交 `datasource_id`（未选为 `null`）
- [x] 4.3 打开会话时恢复数据源选择
- [x] 4.4 调整顶部控件布局，容纳三个选择器

## 5. 测试与验证

- [x] 5.1 覆盖路由：只选数据源、知识库无命中但有数据源、仅知识库模式不挂数据源工具
- [x] 5.2 覆盖工具：概况、问题清单、静音过滤、未采集、`tools_used`
- [x] 5.3 覆盖接口：会话持久化与恢复、清除数据源、非法数据源
- [x] 5.4 运行 `python manage.py test` 与前端构建
- [x] 5.5 运行 `openspec validate add-datasource-qa --strict --no-interactive`

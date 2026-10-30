## 1. 后端

- [x] 1.1 `src/sql/pg_struct.sql` 新增 `llm_usage` 表与索引（`create_time`、`username+create_time`、`feature+create_time`）
- [x] 1.2 `utils/custom_enum.py` 增加功能枚举与调用类型枚举（对话/嵌入）
- [x] 1.3 新增 `apps/llmusage` 模块：模型、`record_call()` 服务、聚合查询服务
- [x] 1.4 实现 LangChain callback handler，接入 `build_chat_model()`；包装 `embed_documents` / `embed_query`
- [x] 1.5 新增归属中间件（URL 前缀映射功能、写入 contextvars、生成 `request_id`），在 `settings.py` 注册
- [x] 1.6 新增 `LlmUsageView(SuperUserView)`：`summary` 与 `calls`，在 `config/urls.py` 注册
- [x] 1.7 保留期清理接入 `jobs/metrics.py` 循环，超期记录软删除
- [x] 1.8 补测试：记录成功/失败/无 usage、埋点失败不影响业务、归属与 request_id、权限 4005、聚合正确、清理只处理超期数据

## 2. 前端

- [x] 2.1 新增 `static/src/api/llmUsage.js`
- [x] 2.2 新增 `static/src/views/system/index.vue`：`el-tabs`，首个标签为「模型用量」
- [x] 2.3 用量页：时间范围、汇总卡片、按天趋势（CSS 条形）、分组切换表、失败/慢调用明细
- [x] 2.4 路由 `/system` 指向新页面，保留 `adminOnly`；删除不再被引用的占位页 `views/development/index.vue`
- [x] 2.5 缺失数据标注为「不可用」，不显示为 0

## 3. 文档

- [x] 3.1 更新 `docs/architecture.md`（新增模块与系统管理页）
- [x] 3.2 更新 `docs/knowledge-qa.md`（模型调用观测说明）

## 4. 验证

- [x] 4.1 `python manage.py test`（`test apps`：687 个用例全部通过，其中 llmusage 22 个）
- [ ] 4.2 `ruff check src/ --config .ci/lint-rules/ruff.toml`、`ruff format --check src/ --config .ci/lint-rules/ruff.toml`（新增/修改文件全部通过；全仓库仍存在 HEAD 上即有的 1 个 I001（`knowledge/tests.py`）与 9 个格式化差异，属既有问题，未扩大改动）
- [x] 4.3 `python .ci/custom-checks/scaffold_check.py`
- [x] 4.4 `static/` 下 `npm run build`
- [x] 4.5 `openspec validate add-llm-usage-stats --strict --no-interactive`

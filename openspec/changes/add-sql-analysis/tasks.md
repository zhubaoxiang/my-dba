## 1. 准备

- [ ] 1.1 `requirements.txt` 增加 sqlglot 并安装；重跑既有 131 项测试确认无回归
- [ ] 1.2 新建模块骨架 `src/apps/sqlanalysis/`（`__init__.py` / `views.py` / `serializers.py` / `filters.py`）
- [ ] 1.3 `apps/base/baseviews.py` 增加**无模型 ViewSet 基类**（只承载权限与统一响应，供只有自定义 action 的接口用）
- [ ] 1.4 `src/config/urls.py` 注册 `{SYS_NAME}/v1/sql-analysis`
- [ ] 1.5 `utils/custom_enum.py` 增加所需枚举（方言、语句类别、问题级别复用既有）

## 2. 静态层：解析与格式化

- [ ] 2.1 `parse.py`：按方言解析，返回语法树 + 语句类别（只读 / DML / DDL）
- [ ] 2.2 `parse.py`：语法错误转为带**位置**的可读信息
- [ ] 2.3 `formatting.py`：输出美化语句（保持语义不变）
- [ ] 2.4 `parse.py`：解析失败时的降级结果（错误 + 原始语句 + 后续步骤标记为未进行）
- [ ] 2.5 单测：各方言正例、语法错误定位、美化幂等（格式化两次结果一致）、降级路径

## 3. 静态层：规则分析

- [ ] 3.1 `rules/context.py`：`SqlRuleContext`——持有语句列表、原始 SQL、方言与可选的表结构索引，产出统一问题项
- [ ] 3.2 `rules/definitions.py`：`RuleDefinition` 与第一批规则判定函数
- [ ] 3.3 `rules/registry.py`：规则清单、查找、按语句类别过滤
- [ ] 3.4 危险写操作规则：`delete_without_where`、`update_without_where`、`drop_table`、`drop_column`、`truncate_table`、`cartesian_join`
- [ ] 3.5 性能规则：`function_on_column`、`implicit_cast`、`select_star`、`no_limit`
- [ ] 3.6 规范规则：`insert_without_column_list`、`ddl_locks_table`
- [ ] 3.7 `analyzer.py`：遍历规则、注入上下文、校验产出（沿用 `datasource/analyzer.py` 的做法）
- [ ] 3.8 单测：**每条规则 MUST 配正例与反例**；同输入结果可复现；规则不依赖数据源也能跑

## 4. 结构校验

- [ ] 4.1 `schema.py`：调 `datasource.services.list_snapshot_tables()` 取快照，在内存建表/列索引
- [ ] 4.2 `schema.py`：校验语句引用的表、列是否存在，指出不存在的对象
- [ ] 4.3 `schema.py`：校验列类型是否可比较/可运算
- [ ] 4.4 未指定数据源、数据源未采集、快照为空三种情况的**明确说明**（不得静默跳过）
- [ ] 4.5 单测：mock 快照覆盖命中、缺表、缺列、类型不匹配与三种跳过情形
- [ ] 4.6 人工验证：对一个已采集的真实数据源跑若干语句

## 5. 执行层：只读试运行与执行计划

- [ ] 5.1 `datasource/services.py` 新增**只读连接服务函数**：负责凭据解密、只读会话、超时配置（sqlanalysis 不自己解密、不读其 model）
- [ ] 5.2 `explain.py`：只读语句在只读会话中真跑，服务端游标只取前 N 行，返回行数与耗时
- [ ] 5.3 `explain.py`：非只读语句只出 `EXPLAIN`（不带 `ANALYZE`），不执行
- [ ] 5.4 `explain.py`：多语句按**最严格的一条**处理（含任一非只读则整批不执行）
- [ ] 5.5 `explain.py`：拿不到计划时如实说明，不伪装成失败或空结果
- [ ] 5.6 行数上限与超时取自配置（新增配置项或复用 `[datasource]` 既有项）
- [ ] 5.7 单测：语句类别判定、多语句取最严、mock 连接下的执行与不执行分支
- [ ] 5.8 人工验证：**先在测试库**上验证只跑 SELECT、DML 只出计划；确认无写入

## 6. 模型解读

- [ ] 6.1 `interpret.py`：把规则命中清单交给对话模型，产出逐条解读
- [ ] 6.2 `interpret.py`：模型可补充规则之外的观察，**单独成字段**并标注未经规则验证
- [ ] 6.3 未配置对话模型时：规则结果照常返回，解读部分给出可操作提示，**不静默降级**
- [ ] 6.4 复用 `knowledge.llm` 的模型构造与 `llm_provider` 的生效配置读取（不重复实现）
- [ ] 6.5 单测：分区字段不混、未配置模型时的降级、模型输出异常时的兜底

## 7. 接口

- [ ] 7.1 `serializers.py`：`analyze` 与 `execute` 的入参校验（SQL 非空与长度上限、方言合法、数据源存在）
- [ ] 7.2 `views.py`：`POST /sql-analysis/analyze`——解析 + 规则 + 结构校验 + 解读
- [ ] 7.3 `views.py`：`POST /sql-analysis/execute`——显式触发的试运行
- [ ] 7.4 响应结构：`formatted` / `syntax` / `issues[]` / `schema_check` / `execution` / `explanation` / `observations[]` **互相分开**
- [ ] 7.5 单测：接口级端到端（mock 解析与模型），确认识别失败走统一格式且不开执行

## 8. 前端

- [ ] 8.1 `api/sqlAnalysis.js`：`analyze()` 与 `execute()` 封装
- [ ] 8.2 页面：SQL 输入、方言与数据源选择、分析按钮
- [ ] 8.3 展示格式化结果并提供**复制**
- [ ] 8.4 问题清单按级别分组，展示成因与建议
- [ ] 8.5 结构校验结果展示，跳过时显示原因
- [ ] 8.6 「试运行」按钮（**显式触发**）与执行计划 / 行数耗时展示
- [ ] 8.7 解读区与「模型推测」区**视觉分区**，推测带标注
- [ ] 8.8 路由与菜单注册

## 9. 文档

- [ ] 9.1 新增 `docs/sql-analysis.md`：能力说明、接口、规则清单、执行边界（只读真跑 / DML 只 EXPLAIN 的理由）、已知限制
- [ ] 9.2 `README.md`：路线图该能力状态改为已实现，文档索引补入
- [ ] 9.3 `docs/architecture.md`：技术栈补 sqlglot；如新增了无模型基类，在「代码分层」处说明
- [ ] 9.4 `docs/development.md`：如新增依赖或基类，补入核心文件表

## 10. 校验

- [ ] 10.1 `python .ci/custom-checks/scaffold_check.py`
- [ ] 10.2 `ruff check src/ --config .ci/lint-rules/ruff.toml` 与 `ruff format --check`
- [ ] 10.3 `cd static && npm run build`
- [ ] 10.4 `ENV_TYPE=test python manage.py test apps.sqlanalysis apps.datasource apps.knowledge`
- [ ] 10.5 `openspec validate add-sql-analysis --strict --no-interactive`

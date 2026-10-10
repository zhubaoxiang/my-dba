# Change: 说人话 → 查询语句

## Why

路线图上最后一项未开始的能力（`README` 的「自然语言转建表语句 / SQL」）。使用者的诉求是「按我说的写条查询」——不必先翻清表结构、不必记住表名列名。

这条能力与别处不同的地方在于：**它自带验证手段**。项目的 18 条 SQL 规则里，`unknown_table` / `unknown_column` 那几条吃的就是真实采集快照，正好用来判断生成结果里的表名列名**到底存不存在**。于是这个能力不是「模型猜一条 SQL 给你」，而是「模型猜完，项目自己验一遍，验出问题让它改一次」。

## What Changes

- 新增接口 `POST {SYS_NAME}/v1/nl2sql/generate`：入参为数据源（必填）、一句需求（必填）、表范围（可选）→ 返回一条 `SELECT` + 校验结论 + 问题清单 + 是否发生过自修
- **生成 → 校验 → 自修一轮**：用系统自己的规则校验；发现结构问题就把问题与真实表名回给模型重生成**一次**；仍有问题如实展示，不再重试
- **表范围可选**：不选则用全库结构；结构超出模型上下文时**如实报错并提示缩小范围**，不截断后继续
- 新增页面与菜单项「SQL 生成」
- 复用已有的试运行（前端直接调 `POST {SYS_NAME}/v1/sql-analysis/execute`），**不新增执行接口**
- `apps/sqlanalysis/services.py` 增加两处对外只读契约（结构索引、规则校验），供本模块调用

## Impact

- 受影响能力：**新增** `nl2sql`
- 受影响代码：
  - `src/apps/nl2sql/`（**新增模块**）：`generator.py`（提示词与自修循环）、`services.py`、`serializers.py`、`views.py`
  - `src/apps/sqlanalysis/services.py`：新增 `load_schema_index()` 与 `analyze_sql()` 两个契约
  - `src/config/urls.py`、`src/settings/settings.py`（注册路由与应用）
  - `static/src/views/nl2sql/index.vue`（**新增页面**）、`static/src/api/nl2sql.js`、`static/src/router/index.js`（菜单项）
  - `docs/nl2sql.md`（**新增**）、`README.md`（路线图与文档索引）、`docs/sql-analysis.md`（服务层新增契约的说明）
- 无新数据模型、无 DDL、无新依赖（`langchain_openai` 已在用）、无部署形态变化
- 接口响应**不含凭据**

## 明确不做

- **不做「说人话 → 建表语句」**：那是同一路线图的另一半，但它有一处本质困难——表还不存在，没有快照可查，**生成结果无从自动验证**，质量全看模型。属独立变更
- **不做多轮追问**：单轮生成，不满意就改描述重来。与 `sql-analysis` 的「贴一条、看结论、改完就走」一致
- **不保存生成历史**
- **不做自动粗筛选表**：从描述里抽词匹配表名看似省事，但筛错就把确定性的失败变成了看不懂的失败
- **不改动任何已有规则**
- **不让模型承担判定职责**：有没有问题是规则说了算，模型只负责生成

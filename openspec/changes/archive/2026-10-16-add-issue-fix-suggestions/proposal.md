# Change: 从库表问题生成修复语句

## Why

库表分析能判出「表缺主键」「外键缺索引」「用 varchar 存时间」，`catalog_issue` 里也有 `suggestion` 字段——但那个字段存的是**一句文字**：

```
「为 (user_id) 建立索引。」        ← 表、列都在规则手里
「确认无其他依赖后删除索引 idx_dup，保留 idx_uniq。」  ← 索引名也在
```

于是现在的流程是「**分析告诉你哪里有问题，然后就没有然后了**」——使用者还得自己把这句话翻译成 SQL。

而这次的有利条件是：**规则已经拿着对象名了**。7 条库表规则（9 个判定分支）逐个过一遍，**其中 7 个能直接给出确定语句**，只有 2 个（超大表的分区/归档策略、大对象字段拆表）没有唯一答案。

## What Changes

- 库表分析判出的每条问题，附带一条**可执行的修复语句**
- **两类分开呈现、各自标明来源**：
  - **由规则对象直接得出**（对象确定的 7 个分支）——确定性模板生成，随问题清单一并返回，**不需要模型**
  - **无唯一答案、需你决策**（超大表、大对象拆表）——按需由模型给出 AI 修复语句，写明前提
- `catalog_issue` 新增 `fix_key`：存**生成修复语句所需的额外标识**，由各规则自己填——索引类规则填**索引名**（现在它只存在于描述文字里，`DROP INDEX` 无法从库中读出），可疑字段类型填**命中的分支**（该规则有三个分支，语句形态不同）
- **只支持 PostgreSQL 方言**：`ALTER COLUMN … TYPE` / `bigserial` 在 MySQL 上是另一套写法，而本仓库没有可验证的 MySQL 环境；非 PostgreSQL 数据源明确说明不支持，**不静默降级**
- 问题清单页直接展示语句 + 「复制全部修复语句」；「需你决策」那两条给按钮按需生成

## Impact

- 受影响能力：**修改** `metadata-catalog`
- 受影响代码：
  - `src/apps/datasource/fixes.py`（**新增**）：模板生成
  - `src/apps/datasource/rules/context.py`、`rules/definitions.py`：索引类规则填 `object_name`
  - `src/apps/datasource/models.py` + `sql/pg_struct.sql` + `sql/patch.sql`：`catalog_issue.object_name`
  - `src/apps/datasource/serializers.py`、`services.py`、`views.py`：问题清单带上修复语句
  - `src/apps/nl2sql/`（**复用**其模型接入做「AI 修复语句」）
  - `static/src/views/datasource/issues.vue`
  - `docs/datasource.md`
- 无新依赖、无部署形态变化

## 明确不做

- **不执行任何修复语句**：与 README 的「不做生产库变更执行」一致
- **不让模型承担模板能做的事**：对象名已在手上，交模型只会引入幻觉，且它看不到库内依赖（`DROP INDEX` 很可能提错）
- **不自动判断「该不该修」**：只给语句，改不改由使用者决定

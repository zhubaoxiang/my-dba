# Change: 库表分析的表清单增加问题数

## Why

库表分析页当前只展示行数、体积、列数、索引数等基础信息。使用者在浏览表清单时看不到哪些表有健康问题，必须逐表打开详情或跳到问题清单再筛选，无法快速定位需要处理的表。

## What Changes

- `/catalog/tables` 返回的每张表增加 `issue_counts`（高/中/低）与 `issue_total`
- 问题数按最近一次快照汇总，排除已静音项，与问题清单、首页统计同口径
- 库表分析页增加「问题数」列，用高/中/低标签展示
- 表清单改为操作列：点击「详情」打开表详情，点击「问题」跳到问题清单并带上数据源、模式与表名条件
- 问题清单接口支持按模式与表名过滤，页面展示当前表条件并允许清除

## Impact

- Affected specs: `metadata-catalog`
- Affected code:
  - `src/apps/datasource/views.py`（`CatalogView.tables`）
  - `src/apps/datasource/serializers.py`（`CatalogTableSerializer`）
  - `static/src/views/datasource/tables.vue`

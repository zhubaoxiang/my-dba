## 1. 后端

- [x] 1.1 `CatalogView.tables` 按 `(schema, table, issue_level)` 一次分组汇总问题数
- [x] 1.2 汇总走 `muted_filter()`，排除已静音项
- [x] 1.3 `CatalogTableSerializer` 增加 `issue_counts` 与 `issue_total`
- [x] 1.4 表清单按高 → 中 → 低问题数降序排列，同分按模式/表名稳定排序
- [x] 1.5 问题清单接口支持按模式与表名过滤

## 2. 前端

- [x] 2.1 库表分析表清单增加「问题数」列
- [x] 2.2 用高/中/低标签展示，零问题显示占位符
- [x] 2.3 表清单增加操作列（详情 / 问题），取消整行点击打开详情
- [x] 2.4 问题清单增加可搜索的表筛选下拉，接收跳转条件并支持清除

## 3. 测试

- [x] 3.1 覆盖按级别汇总、静音排除、同名表不混淆、按模式与表名过滤
- [x] 3.2 运行 `python manage.py test`
- [x] 3.3 运行前端构建
- [x] 3.4 运行 `openspec validate add-table-issue-counts --strict --no-interactive`

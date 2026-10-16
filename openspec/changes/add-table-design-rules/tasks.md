## 1. 采集扩展：外键补「删除/更新动作」

- [x] 1.1 `collectors/postgres.py`：`_FOREIGN_KEYS_SQL` 补 `con.confdeltype` / `con.confupdtype`，映射成 `ON DELETE` / `ON UPDATE` 的动作名（`a`=NO ACTION / `r`=RESTRICT / `c`=CASCADE / `n`=SET NULL / `d`=SET DEFAULT）
- [x] 1.2 `collectors/mysql.py`：取 `REFERENTIAL_CONSTRAINTS` 的 `DELETE_RULE` / `UPDATE_RULE`，输出同样的字段名
- [x] 1.3 两边字段名对齐（`on_delete` / `on_update`），使分析侧不必分方言
- [x] 1.4 单测：两处方言的映射（含 `NO ACTION` 与取不到值时的降级——**取不到不能当成 CASCADE**）
- [x] 1.5 老快照的外键没有这两个字段 → 分析侧按「判不了」跳过，**不当作 NO ACTION**

## 2. 常量与词表

- [x] 2.1 `rules/definitions.py` 新增 `_FLOAT_TYPES`（`real` / `double precision` / `float` / `double` / `float4` / `float8`）
- [x] 2.2 新增金额语义词表 `_MONEY_NAME_PATTERN`（`amount` / `price` / `fee` / `cost` / `balance` / `money` / 金额 / 价格 / 费用 / 余额）
- [x] 2.3 单测：词表命中与不命中各几例（尤其 `total_count` 这类**不该**命中的）

## 3. 五条规则

- [x] 3.1 `float_money_column`（高 / 列级）：浮点类型 **且** 名字或注释含金额语义
- [x] 3.2 `long_text_primary_key`（中 / 列级）：主键列是字符类型且长度无界或超 `primary_key_max_length`（默认 64）
- [x] 3.3 `wide_table`（中 / 表级）：列数超 `wide_table_max_columns`（默认 50）
- [x] 3.4 `index_larger_than_data`（中 / 表级）：索引占用 > 数据占用且 `row_count > 0`
- [x] 3.5 `cascade_delete_chain`（中 / 表级）：本表的 `ON DELETE CASCADE` 指向的表**自己也有** CASCADE 出边
- [x] 3.6 每条规则的说明与建议按现有风格写：**说明问题本身与后果**，不写「建议做 X」的空话
- [x] 3.7 单测（各规则的**正例与反例**都写）：
  - 浮点 + 金额名 → 报；浮点 + 非金额名（如 `score`）→ **不报**；`numeric(19,4)` 存金额 → **不报**
  - 主键 `varchar(255)` → 报；`varchar(32)` → 不报；`int8` → 不报；`text`（无界）→ 报
  - 51 列 → 报；49 列 → 不报；阈值调成 10 后 11 列 → 报
  - 索引 20MB / 数据 10MB → 报；空表（`row_count=0`）索引略大 → **不报**
  - **A→B 单层级联 → 不报**；**A→B→C 跨两层 → 报**；**自引用级联 → 不报**；外键动作字段缺失（老快照）→ **不报**

## 4. 与修复语句对接

- [x] 4.1 `fixes.py`：`float_money_column` 给模板 —— `ALTER TABLE … ALTER COLUMN … TYPE numeric(19, 4);`
- [x] 4.2 它的前提写两条：**精度与标度按业务定**（示例取 19,4）；**浮点已经丢掉的精度找不回来**，先核对现网数据
- [x] 4.3 `long_text_primary_key` / `wide_table` / `cascade_delete_chain` 归到「需模型参考」（动作明确，但要不要做是业务决定）
- [x] 4.4 `index_larger_than_data` 归到「不给语句」，并在说明里指向「重复或冗余索引」与「疑似未使用索引」那两条
- [x] 4.5 单测：五条各自的 `source` 与模板内容

## 5. 文档

- [x] 5.1 `docs/datasource.md` 的规则清单表补 5 行；采集范围一节补上「外键含动作」
- [x] 5.2 写明**每条为什么这么设计**——尤其 D1 的漏报取舍、D4 与逐索引两条的关系、D6 为什么只报跨表链
- [x] 5.3 「明确不做的」补上：命名规范、注释覆盖率、可空列占比

## 6. 校验

- [x] 6.1 `python .ci/custom-checks/scaffold_check.py`
- [x] 6.2 `ruff check` 与 `ruff format --check`（带 `.ci/lint-rules/ruff.toml`）
- [x] 6.3 `ENV_TYPE=test python manage.py test apps.datasource apps.overview`
- [x] 6.4 **真实接口核对**（见第 8 组）：重新采集一个数据源 → 核对 5 条新规则产出的问题；
       **逐条看有没有误报**（这是本次最主要的核对目标——新规则的代价全在误报上）
- [ ] 6.5 **真实页面核对**：规则清单页能看到 12 条；新规则的修复语句与来源标注正确
- [x] 6.6 `openspec validate add-table-design-rules --strict --no-interactive`

## 7. 实现时的两处

- [x] 7.1 **测试抓到我一个真 bug**：级联链那段的 `for t, name in downstream` 把 `(schema, table)` 元组**拆成了两个变量**，于是 `t in cascades` 拿 schema 去比整张表的键，永远不成立——链永远为空、规则永不触发。改成 `for target in downstream` 后正常。**写单测的价值就在这**：这个错误读代码时看不出来，跑一遍立刻现形
- [x] 7.2 MySQL 侧的 `REFERENTIAL_CONSTRAINTS` 关联查询**只在 PostgreSQL 上验证过**（本条记进 `docs/datasource.md` 的已知限制）

## 8. 6.4 真机核对抓到的误报（本次最主要的核对目标）

对一个已被采集的数据源重新采集（31 张表），逐条看 5 条新规则的产出。

- [x] 8.1 **抓到 6 条误报**：`index_larger_than_data` 报出来的全是 `data=8192` 字节、1~42 行的小表，措辞还显示成「索引占用 0 MB，超过数据本身的 0 MB」。根因是判据只比大小、**没有体量下限**——而小表的 `index > data` 是**必然形态**（索引有固定的页开销）
- [x] 8.2 修复：加阈值 `index_overweight_min_data_mb`（默认 100），数据占用不到这个体量就不判；补一条小表的反例单测
- [x] 8.3 复验：同一份真实快照重跑，该规则 **6 → 0 条**
- [x] 8.4 **确认其余三条的 0 条是数据使然**，不是规则静默失效：库里**一条浮点列都没有**（金额那条无从触发）、最大表 **19 列**（阈值 50）、唯一的字符主键是 `varchar(40)`（阈值 64，正好卡在阈值内）
- [x] 8.5 **确认 `cascade_delete_chain` 的 0 条是判对了**：库里唯一那条 CASCADE 指向的表自己没有任何外键（单层级联，不该报）
- [x] 8.6 **顺带验证了新采集 SQL**：重采成功（`status=3`），说明新加的 `confdeltype` / `confupdtype` 取数在真实 PostgreSQL 上跑得通；采到 9 条外键，动作分布 `NO ACTION 7 / CASCADE 1 / SET NULL 1`

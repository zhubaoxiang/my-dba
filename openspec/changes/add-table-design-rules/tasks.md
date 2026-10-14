## 1. 常量与词表

- [ ] 1.1 `rules/definitions.py` 新增 `_FLOAT_TYPES`（`real` / `double precision` / `float` / `double` / `float4` / `float8`）
- [ ] 1.2 新增金额语义词表 `_MONEY_NAME_PATTERN`（`amount` / `price` / `fee` / `cost` / `balance` / `money` / 金额 / 价格 / 费用 / 余额）
- [ ] 1.3 单测：词表命中与不命中各几例（尤其 `total_count` 这类**不该**命中的）

## 2. 四条规则

- [ ] 2.1 `float_money_column`（高 / 列级）：浮点类型 **且** 名字或注释含金额语义
- [ ] 2.2 `long_text_primary_key`（中 / 列级）：主键列是字符类型且长度无界或超 `primary_key_max_length`（默认 64）
- [ ] 2.3 `wide_table`（中 / 表级）：列数超 `wide_table_max_columns`（默认 50）
- [ ] 2.4 `index_larger_than_data`（中 / 表级）：索引占用 > 数据占用且 `row_count > 0`
- [ ] 2.5 每条规则的说明与建议按现有风格写：**说明问题本身与后果**，不写「建议做 X」的空话
- [ ] 2.6 单测（各规则的**正例与反例**都写）：
  - 浮点 + 金额名 → 报；浮点 + 非金额名（如 `score`）→ **不报**；`numeric(19,4)` 存金额 → **不报**
  - 主键 `varchar(255)` → 报；主键 `varchar(32)` → 不报；主键 `int8` → 不报；主键 `text`（无界）→ 报
  - 51 列 → 报；49 列 → 不报；阈值调成 10 后 11 列 → 报
  - 索引 20MB / 数据 10MB → 报；空表（`row_count=0`）索引略大 → **不报**

## 3. 与修复语句对接

- [ ] 3.1 `fixes.py`：`float_money_column` 给模板 —— `ALTER TABLE … ALTER COLUMN … TYPE numeric(19, 4);`
- [ ] 3.2 它的前提要写两条：**精度与标度按业务定**（示例取 19,4）；**浮点已经丢掉的精度找不回来**，先核对现网数据
- [ ] 3.3 `long_text_primary_key` / `wide_table` 归到「需模型参考」（改主键与拆表都要业务判断，无唯一答案）
- [ ] 3.4 `index_larger_than_data` 归到「不给语句」，并在说明里指向「重复或冗余索引」与「疑似未使用索引」那两条
- [ ] 3.5 单测：四条各自的 `source` 与模板内容

## 4. 文档

- [ ] 4.1 `docs/datasource.md` 的规则清单表补 4 行；写明**每条为什么这么设计**（尤其 D1 的漏报取舍、D4 与逐索引两条的关系）
- [ ] 4.2 `docs/datasource.md` 的「明确不做的」补上：命名规范、注释覆盖率、可空列占比、`ON DELETE CASCADE`（后者需先扩采集）

## 5. 校验

- [ ] 5.1 `python .ci/custom-checks/scaffold_check.py`
- [ ] 5.2 `ruff check` 与 `ruff format --check`（带 `.ci/lint-rules/ruff.toml`）
- [ ] 5.3 `ENV_TYPE=test python manage.py test apps.datasource apps.overview`
- [ ] 5.4 **真实接口核对**：对一个已采集的数据源重新采集 → 核对新规则产出的问题；
       **逐条看有没有误报**（这是本次最主要的核对目标——新规则的代价全在误报上）
- [ ] 5.5 **真实页面核对**：规则清单页能看到 11 条；新规则的修复语句与来源标注正确
- [ ] 5.6 `openspec validate add-table-design-rules --strict --no-interactive`

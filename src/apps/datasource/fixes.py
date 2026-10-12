"""
库表问题的修复语句（模板）

只覆盖**对象已经确定**的分支——规则手里有表名、列名、索引名，语句就是填空。这一类
不需要模型：确定性、可单测、且不依赖模型可用性。

「无唯一答案」的两类（超大表的分区/归档策略、大对象字段拆表）在这里**故意没有模板**：
它们要先决定策略，模板拼不出来。那两条由模型给 AI 修复语句。

**只支持 PostgreSQL 方言。** 生成出来的语句是要贴到目标库上跑的，而
`ALTER COLUMN … TYPE` / `ADD COLUMN … bigserial` 这些在 MySQL 上是另一套写法；
本仓库的真机环境没有 MySQL，写一套没在真机上验证过的 DDL 给人执行，恰恰是这个项目
一直在防的事。非 PostgreSQL 的数据源一律标 `unsupported` 并说明原因，**不静默降级**。

**这里只产出语句，绝不执行**（README 的边界：不做生产库变更执行）。
"""

from apps.datasource.rules import registry
from utils import custom_enum

# 语句的来源，界面靠它分区陈列
SOURCE_RULE = "rule"  # 由规则对象直接得出——确定的
SOURCE_MODEL_REQUIRED = "model_required"  # 无唯一答案，需模型给参考
SOURCE_UNSUPPORTED = "unsupported"  # 方言不支持，明确说明
SOURCE_ADVISORY = "advisory"  # 判定依据不足，**不给出语句**

_POSTGRES = custom_enum.DbTypeEnum.POSTGRESQL.value

# PostgreSQL 的标识符上限是 63 字节
_IDENTIFIER_LIMIT = 63

# 可疑字段类型的「分支」标识，由规则填进 fix_key（该规则有三个分支，语句形态不同）
_BRANCH_CHAR_TIME = "char_time"
_BRANCH_LONG_VARCHAR = "long_varchar"

# 取值都走这里：既支持 `CatalogIssue` 实例，也支持单测里造的普通对象——
# 模板是纯计算，不该为了单测去起数据库
_MISSING = object()


def _of(issue, name: str) -> str:
    value = getattr(issue, name, _MISSING)
    return "" if value is _MISSING or value is None else str(value)


def _quote(name: str) -> str:
    """
    标识符引用，统一用双引号（本模块只产出 PostgreSQL 语句）
    """
    return f'"{name}"' if name else name


def _qualified(schema: str, table: str) -> str:
    """
    模式.表；模式为空或 public 时省略——public 是 PostgreSQL 的默认，写上反而啰嗦
    """
    table = _quote(table)
    if schema and schema.lower() != "public":
        return f"{_quote(schema)}.{table}"
    return table


def _index_name(table: str, columns: list) -> str:
    """
    按「idx_表_列」自动拼一个索引名

    超长要截断（PostgreSQL 标识符上限 63），**截断是不理想的**（有撞名可能），
    所以前提里写明名字可按团队规范改。
    """
    parts = ["idx", table, *columns]
    name = "_".join(part.strip() for part in parts if part and part.strip())
    return name.replace(",", "_").replace(" ", "_")[:_IDENTIFIER_LIMIT]


def _create_index(issue) -> tuple:
    columns = [item for item in _of(issue, "column_name").split(",") if item]
    if not columns:
        return None
    table_name = _of(issue, "table_name")
    name = _index_name(table_name, columns)
    target = _qualified(_of(issue, "schema_name"), table_name)
    rendered = ", ".join(_quote(item) for item in columns)
    return (
        f"CREATE INDEX {_quote(name)} ON {target} ({rendered});",
        f"索引名 {name} 由「表_列」自动拼出，可按团队命名规范调整；执行前请确认该列组合确实缺少可用索引。",
    )


def _drop_index(issue, caveat: str) -> tuple:
    index = _of(issue, "fix_key")
    if not index:
        return None
    target = _quote(index)
    schema = _of(issue, "schema_name")
    if schema:
        target = f"{_quote(schema)}.{target}"
    return (f"DROP INDEX {target};", caveat)


def _add_surrogate_key(issue) -> tuple:
    target = _qualified(_of(issue, "schema_name"), _of(issue, "table_name"))
    return (
        f"ALTER TABLE {target} ADD COLUMN id bigserial PRIMARY KEY;",
        "代理键列名默认取 id；若表中已有同名列，请另行指定。仅在确实没有业务唯一列时才使用代理键。",
    )


def _char_to_timestamp(issue) -> tuple:
    column = _of(issue, "column_name")
    if not column:
        return None
    target = _qualified(_of(issue, "schema_name"), _of(issue, "table_name"))
    rendered = _quote(column)
    return (
        f"ALTER TABLE {target} ALTER COLUMN {rendered} TYPE timestamp USING {rendered}::timestamp;",
        "转换会逐行解析现有文本；任何一行不是合法时间，整条语句都会失败。建议先用 SELECT 找出这些行。",
    )


def _shrink_varchar(issue, limit: int) -> tuple:
    column = _of(issue, "column_name")
    if not column:
        return None
    target = _qualified(_of(issue, "schema_name"), _of(issue, "table_name"))
    return (
        f"ALTER TABLE {target} ALTER COLUMN {_quote(column)} TYPE varchar({limit});",
        f"目标长度 {limit} 取自当前阈值 varchar_max_length；现有数据有超长的行时整条语句会失败。",
    )


def varchar_limit() -> int:
    """
    取 `suspicious_column_type` **当前生效**的阈值（代码声明 ⊕ 库中覆盖）

    修复语句里要写具体长度，写死代码默认值会与使用者调过的阈值脱钩。
    """
    for rule in registry.load_effective_rules():
        if rule.code == "suspicious_column_type":
            return int(rule.thresholds.get("varchar_max_length", 2000))
    return 2000


def _template(issue, limit: int):
    """
    按规则 code 给出一条修复语句；给不出时返回 None
    """
    code = _of(issue, "rule_code")
    if code == "fk_without_index":
        return _create_index(issue)
    if code == "duplicate_index":
        return _drop_index(issue, "执行前确认没有视图、约束或外键依赖它；若被依赖，先处理依赖方。")
    if code == "unused_index":
        return _drop_index(issue, "需先观察一个完整业务周期（含月末、季末等周期场景）确认确实无用，再执行。")
    if code == "no_primary_key":
        return _add_surrogate_key(issue)
    if code == "suspicious_column_type":
        branch = _of(issue, "fix_key")
        if branch == _BRANCH_CHAR_TIME:
            return _char_to_timestamp(issue)
        if branch == _BRANCH_LONG_VARCHAR:
            return _shrink_varchar(issue, limit)
    # big_table 与可疑字段类型里的「大对象」分支：没有唯一答案，交模型
    return None


def _unsupported() -> dict:
    return {
        "sql": "",
        "caveat": "",
        "source": SOURCE_UNSUPPORTED,
        "note": "当前只支持 PostgreSQL 的修复语句生成；这类数据源的写法不同，未在真机验证前不给出语句。",
    }


def _advisory(note: str) -> dict:
    return {"sql": "", "caveat": "", "source": SOURCE_ADVISORY, "note": note}


# **这一条规则刻意不产出语句**，理由值得写下来：
#
# `isolated_table` 判的是「既没有外键，也没有被任何表引用」。但**大量正常库根本不建外键**——
# 真机核对时遇到过：一个不建外键的库，23 张表**全被**判成「孤立」，包括那些显然是业务表的。
#
# 「没有外键」与「没人用」之间隔着一整个业务上下文。把它变成一条可执行的 `DROP TABLE`，
# 是**把一个弱证据伪装成确定结论**；而这条语句又是**不可逆**的。宁可只留排查建议。
_ADVISORY_RULES = {
    "isolated_table": "「孤立」仅表示该表没有外键关系，不代表没有业务在使用；因此不自动给出语句，请按排查建议人工确认。",
}


def fixes_for(issues: list, db_type, limit: int = None) -> list:
    """
    给一批问题各配一条修复语句，与输入**等长同序**

    :param limit: 超长 varchar 的目标长度。留空时按**当前生效的阈值**取（会读一次库）；
        显式传入则完全不碰数据库——模板是纯计算，单测不必为它起数据库

    每条的结构：
    - `{"sql", "caveat", "source": "rule"}` —— 由规则对象直接得出，确定的
    - `{"sql": "", "caveat": "", "source": "model_required"}` —— 无唯一答案，需模型给参考
    - `{"sql": "", "caveat": "", "source": "unsupported", "note": …}` —— 方言不支持
    """
    if db_type != _POSTGRES:
        return [_unsupported() for _ in issues]

    # 阈值只读一次，不逐条查库
    limit = varchar_limit() if limit is None else limit
    result = []
    for issue in issues:
        advisory = _ADVISORY_RULES.get(_of(issue, "rule_code"))
        if advisory:
            result.append(_advisory(advisory))
            continue
        template = _template(issue, limit)
        if template is None:
            result.append({"sql": "", "caveat": "", "source": SOURCE_MODEL_REQUIRED})
        else:
            sql, caveat = template
            result.append({"sql": sql, "caveat": caveat, "source": SOURCE_RULE})
    return result

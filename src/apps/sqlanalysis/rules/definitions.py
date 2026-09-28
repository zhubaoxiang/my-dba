"""
SQL 规则声明

每条规则 = 一个判定函数 + 一条 `SqlRuleDefinition`。规则逻辑一律是代码，
不通过表达式或配置动态求值（与库表分析同一约定）。

`needs_schema=True` 的规则还要读采集快照里的表结构，未绑定数据源时由框架跳过并说明原因。

判定函数自己决定看哪些语句（框架不驱动调用）——规则形态差异大，强行统一会让框架复杂且限制表达力。
"""

import dataclasses
from collections.abc import Callable

from sqlglot import exp

from utils import custom_enum

_HIGH = custom_enum.IssueLevelEnum.HIGH
_MEDIUM = custom_enum.IssueLevelEnum.MEDIUM
_LOW = custom_enum.IssueLevelEnum.LOW

# 需要「列上套了东西」才算失效的场景，只看比较类节点——
# 把普通列引用也扫进来会在任何一条 SQL 上都报出噪音
_COMPARISONS = (exp.EQ, exp.NEQ, exp.GT, exp.GTE, exp.LT, exp.LTE, exp.Like, exp.ILike)

# ALTER TABLE 里会重写表或长时间持锁的动作
_LOCKING_ACTIONS = (exp.ColumnDef, exp.AlterColumn)


@dataclasses.dataclass(frozen=True)
class SqlRuleDefinition:
    """
    一条 SQL 规则的声明

    - `code` 为稳定标识，不随规则增删改变（改名属破坏性操作）
    - `needs_schema` 声明该规则是否需要表结构；框架据此在未绑定数据源时跳过
    """

    code: str
    name: str
    description: str
    default_level: "custom_enum.IssueLevelEnum"
    handler: Callable
    needs_schema: bool = False


# ----------------------------------------------------------------------
# 小工具
# ----------------------------------------------------------------------


def _table_name(node) -> str:
    """
    取节点的目标表名。DROP 与 TRUNCATE 把对象放在 `tables` / `expressions`，其余在 `this`
    """
    target = node.args.get("this")
    if isinstance(target, exp.Schema):
        target = target.this
    if isinstance(target, exp.Table):
        return target.name or ""
    for key in ("tables", "expressions"):
        for item in node.args.get(key) or []:
            if isinstance(item, exp.Table):
                return item.name or ""
    return ""


def _wrapped_columns(side):
    """
    取表达式里**被函数或类型转换包住**的列

    纯列引用（`WHERE id = 1`）不算；`WHERE f(id) = 1` 与 `WHERE id::text = '1'` 才算——
    前者让优化器用不上索引，后者引入隐式转换。
    """
    if isinstance(side, exp.Column):
        return []
    if side.find(exp.Column) is None:
        return []
    if side.find(exp.Func) is None and side.find(exp.Cast) is None:
        return []
    return list(side.find_all(exp.Column))


def _has_aggregate(node) -> bool:
    return next(iter(node.find_all(exp.AggFunc)), None) is not None


# ----------------------------------------------------------------------
# 危险写操作
# ----------------------------------------------------------------------


def _check_delete_without_where(ctx):
    issues = []
    for statement in ctx.statements:
        for node in statement.expression.find_all(exp.Delete):
            if node.args.get("where") is not None:
                continue
            table = _table_name(node)
            issues.append(
                ctx.issue(
                    description=f"这条 DELETE 没有 WHERE 条件，会删除{f' {table}' if table else ''}的全部数据。",
                    suggestion="补上 WHERE 并先用相同条件 SELECT 确认影响范围；确实要清空整表请改用 TRUNCATE（更快，且会重置自增）。",
                    statement=statement,
                    target=table,
                )
            )
    return issues


def _check_update_without_where(ctx):
    issues = []
    for statement in ctx.statements:
        for node in statement.expression.find_all(exp.Update):
            if node.args.get("where") is not None:
                continue
            table = _table_name(node)
            issues.append(
                ctx.issue(
                    description=f"这条 UPDATE 没有 WHERE 条件，会更新{f' {table}' if table else ''}的全部行。",
                    suggestion="补上 WHERE 并先用相同条件 SELECT 确认影响范围。",
                    statement=statement,
                    target=table,
                )
            )
    return issues


def _check_drop_table(ctx):
    issues = []
    for statement in ctx.statements:
        for node in statement.expression.find_all(exp.Drop):
            if str(node.args.get("kind") or "").upper() != "TABLE":
                continue
            table = _table_name(node)
            issues.append(
                ctx.issue(
                    description=f"DROP TABLE{f' {table}' if table else ''} 会连同表内全部数据与索引一起删除，且不可回滚。",
                    suggestion="先确认该表确实无人使用；生产库上的删表应走变更流程，并确保有可恢复的备份。",
                    statement=statement,
                    target=table,
                )
            )
    return issues


def _check_drop_column(ctx):
    issues = []
    for statement in ctx.statements:
        for action in statement.expression.find_all(exp.Drop):
            if str(action.args.get("kind") or "").upper() != "COLUMN":
                continue
            columns = [item.name for item in action.args.get("tables") or [] if isinstance(item, exp.Column)]
            table = ""
            alter = action.parent
            while alter is not None and not isinstance(alter, exp.Alter):
                alter = alter.parent
            if alter is not None:
                table = _table_name(alter)
            name = "、".join(columns) or "该列"
            issues.append(
                ctx.issue(
                    description=f"删除列 {name}{f'（表 {table}）' if table else ''} 会永久丢失该列数据。",
                    suggestion="确认无代码与报表依赖后，先将列改为可空并停用一段时间，再执行删除。",
                    statement=statement,
                    target=f"{table}.{name}" if table else name,
                )
            )
    return issues


def _check_truncate_table(ctx):
    issues = []
    for statement in ctx.statements:
        for node in statement.expression.find_all(exp.TruncateTable):
            table = _table_name(node)
            issues.append(
                ctx.issue(
                    description=f"TRUNCATE{f' {table}' if table else ''} 会清空全表且不可回滚（比 DELETE 更快，但同样不留痕迹）。",
                    suggestion="确认这是有意清空；需要保留部分数据请改用带 WHERE 的 DELETE。",
                    statement=statement,
                    target=table,
                )
            )
    return issues


# ----------------------------------------------------------------------
# JOIN 与 GROUP BY
# ----------------------------------------------------------------------


def _check_join_without_condition(ctx):
    issues = []
    for statement in ctx.statements:
        for join in statement.expression.find_all(exp.Join):
            if join.args.get("on") is not None or join.args.get("using"):
                continue
            kind = str(join.args.get("kind") or "").upper()
            right = join.args.get("this")
            name = right.name if isinstance(right, exp.Table) else (right.sql()[:64] if right is not None else "")
            label = f"CROSS JOIN {name}" if kind == "CROSS" else f"JOIN {name}"
            issues.append(
                ctx.issue(
                    description=f"{label} 没有连接条件，两侧会做笛卡尔积——行数是两张表的乘积。",
                    suggestion="补上 ON 条件；确实需要笛卡尔积时请显式写 CROSS JOIN 并在注释里说明用途。",
                    statement=statement,
                    target=name,
                )
            )
    return issues


def _check_group_by_missing_column(ctx):
    """
    SELECT 里的非聚合列必须出现在 GROUP BY 中

    PostgreSQL 会直接报错，MySQL 在关闭 ONLY_FULL_GROUP_BY 时会**静默返回随机值**——
    后者才是真正危险的情况，因此这条对两种方言都有价值。
    """
    issues = []
    for statement in ctx.statements:
        for select in statement.expression.find_all(exp.Select):
            group = select.args.get("group")
            if group is None:
                continue
            group_columns = list(group.expressions)
            # GROUP BY 1 这类位置引用没法与列名比对，宁可不报也不误报
            if any(not isinstance(item, exp.Column) for item in group_columns):
                continue
            grouped = {item.name.lower() for item in group_columns}
            for item in select.expressions:
                if _has_aggregate(item):
                    continue
                for column in item.find_all(exp.Column):
                    if column.name.lower() in grouped:
                        continue
                    issues.append(
                        ctx.issue(
                            description=f"SELECT 中的列 {column.name} 未出现在 GROUP BY 里，也不是聚合结果，"
                            f"它的取值不确定（MySQL 关闭 ONLY_FULL_GROUP_BY 时会静默返回任意一行的值）。",
                            suggestion=f"把 {column.name} 加入 GROUP BY，或对它套一个聚合函数。",
                            statement=statement,
                            target=column.name,
                        )
                    )
    return issues


# ----------------------------------------------------------------------
# 性能与规范
# ----------------------------------------------------------------------


def _check_function_on_column(ctx):
    issues = []
    for statement in ctx.statements:
        for where in statement.expression.find_all(exp.Where):
            for comparison in where.find_all(exp.Binary):
                if not isinstance(comparison, _COMPARISONS):
                    continue
                for side in (comparison.left, comparison.right):
                    for column in _wrapped_columns(side):
                        issues.append(
                            ctx.issue(
                                description=f"WHERE 里对列 {column.name} 套了函数或类型转换，"
                                f"优化器无法再用该列上的索引定位，通常退化为全表扫描。",
                                suggestion=f"把运算挪到等号另一侧（例如改成 {column.name} >= 某个值 的范围条件），"
                                f"或为该表达式建函数索引。",
                                statement=statement,
                                target=column.name,
                            )
                        )
    return issues


def _check_select_star(ctx):
    issues = []
    for statement in ctx.statements:
        for select in statement.expression.find_all(exp.Select):
            stars = list(select.find_all(exp.Star))
            if not stars:
                continue
            issues.append(
                ctx.issue(
                    description="用了 SELECT *：取回了不需要的列，大字段会明显增加传输与内存开销；"
                    "表结构变更时也可能让依赖列序的代码出错。",
                    suggestion="只列出真正需要的列。",
                    statement=statement,
                    target="SELECT *",
                )
            )
    return issues


def _check_insert_without_column_list(ctx):
    issues = []
    for statement in ctx.statements:
        for node in statement.expression.find_all(exp.Insert):
            target = node.args.get("this")
            if isinstance(target, exp.Schema):
                continue  # 已显式列出列名
            table = _table_name(node)
            issues.append(
                ctx.issue(
                    description=f"INSERT 没有列出列名{f'（表 {table}）' if table else ''}，"
                    f"取值顺序完全依赖表的列顺序——表加列或调序后会静默写错位置。",
                    suggestion="显式写出列名：INSERT INTO t (col1, col2) VALUES (...)。",
                    statement=statement,
                    target=table,
                )
            )
    return issues


def _check_ddl_locks_table(ctx):
    issues = []
    for statement in ctx.statements:
        for alter in statement.expression.find_all(exp.Alter):
            locks = [action for action in alter.args.get("actions") or [] if isinstance(action, _LOCKING_ACTIONS)]
            if not locks:
                continue
            table = _table_name(alter)
            issues.append(
                ctx.issue(
                    description=f"这条 DDL 会重写表或长时间持有排他锁{f'（表 {table}）' if table else ''}，"
                    f"执行期间该表的读写会被阻塞；大表上可能持续数分钟。",
                    suggestion="选低峰期执行；大表加列可用「先加可空列、回填、再加约束」的分步做法。",
                    statement=statement,
                    target=table,
                )
            )
    return issues


def _check_no_limit(ctx):
    """
    既无 WHERE 又无 LIMIT 的查询会返回整表

    只在两者都缺时报出：有 WHERE 的查询未必需要 LIMIT，单看 LIMIT 会产生大量噪音。
    """
    issues = []
    for statement in ctx.statements:
        for select in statement.expression.find_all(exp.Select):
            if select.args.get("limit") is not None or select.args.get("where") is not None:
                continue
            if _has_aggregate(select):
                continue  # 聚合查询本来就只返回一行
            issues.append(
                ctx.issue(
                    description="这条查询既没有 WHERE 也没有 LIMIT，会返回整张表的数据。",
                    suggestion="补上过滤条件；确实要看全量时请加 LIMIT 分段取，或确认表规模可接受。",
                    statement=statement,
                    target="",
                )
            )
    return issues


def _check_leading_wildcard_like(ctx):
    issues = []
    for statement in ctx.statements:
        for node in statement.expression.find_all(exp.Like, exp.ILike):
            pattern = node.expression
            if not isinstance(pattern, exp.Literal) or not pattern.is_string:
                continue
            if not str(pattern.this).startswith("%"):
                continue
            column = node.this.name if isinstance(node.this, exp.Column) else ""
            issues.append(
                ctx.issue(
                    description=f"LIKE 以 % 开头{f'（列 {column}）' if column else ''}，无法用 B 树索引定位，只能全表扫描。",
                    suggestion="改用前缀匹配（把 % 放到末尾）；确实需要全文检索时用目标库的全文索引能力。",
                    statement=statement,
                    target=column,
                )
            )
    return issues


# ----------------------------------------------------------------------
# 规则清单
# ----------------------------------------------------------------------

RULES = (
    SqlRuleDefinition(
        code="delete_without_where",
        name="无条件的 DELETE",
        description="DELETE 没有 WHERE，会删除整表数据",
        default_level=_HIGH,
        handler=_check_delete_without_where,
    ),
    SqlRuleDefinition(
        code="update_without_where",
        name="无条件的 UPDATE",
        description="UPDATE 没有 WHERE，会更新整表数据",
        default_level=_HIGH,
        handler=_check_update_without_where,
    ),
    SqlRuleDefinition(
        code="drop_table",
        name="删除表",
        description="DROP TABLE 会连同数据与索引一起删除且不可回滚",
        default_level=_HIGH,
        handler=_check_drop_table,
    ),
    SqlRuleDefinition(
        code="drop_column",
        name="删除列",
        description="DROP COLUMN 会永久丢失该列数据",
        default_level=_HIGH,
        handler=_check_drop_column,
    ),
    SqlRuleDefinition(
        code="truncate_table",
        name="清空表",
        description="TRUNCATE 清空全表且不可回滚",
        default_level=_HIGH,
        handler=_check_truncate_table,
    ),
    SqlRuleDefinition(
        code="join_without_condition",
        name="无连接条件的 JOIN",
        description="JOIN 没有 ON/USING，两侧做笛卡尔积",
        default_level=_HIGH,
        handler=_check_join_without_condition,
    ),
    SqlRuleDefinition(
        code="group_by_missing_column",
        name="GROUP BY 遗漏非聚合列",
        description="SELECT 中的非聚合列未出现在 GROUP BY，取值不确定",
        default_level=_MEDIUM,
        handler=_check_group_by_missing_column,
    ),
    SqlRuleDefinition(
        code="function_on_column",
        name="列上套了函数或转换",
        description="WHERE 里对列做运算，导致索引用不上",
        default_level=_MEDIUM,
        handler=_check_function_on_column,
    ),
    SqlRuleDefinition(
        code="select_star",
        name="使用 SELECT *",
        description="取回全部列，增加传输与内存开销",
        default_level=_MEDIUM,
        handler=_check_select_star,
    ),
    SqlRuleDefinition(
        code="insert_without_column_list",
        name="INSERT 未列出列名",
        description="取值顺序依赖表的列顺序，加列或调序后会写错位置",
        default_level=_MEDIUM,
        handler=_check_insert_without_column_list,
    ),
    SqlRuleDefinition(
        code="ddl_locks_table",
        name="会锁表的 DDL",
        description="重写表或长时间持有排他锁，阻塞该表读写",
        default_level=_MEDIUM,
        handler=_check_ddl_locks_table,
    ),
    SqlRuleDefinition(
        code="no_limit",
        name="无过滤也无行数限制",
        description="既没有 WHERE 也没有 LIMIT，会返回整表",
        default_level=_LOW,
        handler=_check_no_limit,
    ),
    SqlRuleDefinition(
        code="leading_wildcard_like",
        name="前缀通配的 LIKE",
        description="LIKE 以 % 开头，无法使用索引",
        default_level=_LOW,
        handler=_check_leading_wildcard_like,
    ),
)

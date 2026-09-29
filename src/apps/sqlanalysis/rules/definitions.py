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

from apps.sqlanalysis.schema import SchemaIndex, families_conflict, type_family
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

    **没有 GROUP BY 时每一个非聚合列都算遗漏**——这不是更宽松的情况，而是更严重的：
    PostgreSQL 会直接报错（column must appear in the GROUP BY clause），
    MySQL 在关闭 ONLY_FULL_GROUP_BY 时会**静默返回随机值**。

    每条 SELECT 只报一条（把所有遗漏的列列在一起）：它们对应的是同一个修法。
    """
    issues = []
    for statement in ctx.statements:
        for select in statement.expression.find_all(exp.Select):
            group = select.args.get("group")
            if group is None:
                if not _has_aggregate(select):
                    continue  # 没有聚合就谈不上分组
                grouped = set()
            else:
                group_columns = list(group.expressions)
                # GROUP BY 1 这类位置引用没法与列名比对，宁可不报也不误报
                if any(not isinstance(item, exp.Column) for item in group_columns):
                    continue
                grouped = {item.name.lower() for item in group_columns}

            missing, seen = [], set()
            for item in select.expressions:
                if _has_aggregate(item):
                    continue
                # 有别名时，别名本身就是它在 GROUP BY 里可能出现的形式：
                # `SELECT name AS n ... GROUP BY n` 完全合法，不该报
                alias = (item.alias or "").strip().lower() if isinstance(item, exp.Alias) else ""
                if alias and alias in grouped:
                    continue
                for column in item.find_all(exp.Column):
                    key = column.name.lower()
                    if key in grouped or key in seen:
                        continue
                    seen.add(key)
                    missing.append(column.name)
            if not missing:
                continue

            columns = "、".join(missing)
            if group is None:
                description = (
                    f"这条 SELECT 里既有聚合函数、又有未聚合的列（{columns}），却没有 GROUP BY，"
                    f"两者的组合是无效的——PostgreSQL 会直接报错，"
                    f"MySQL 在关闭 ONLY_FULL_GROUP_BY 时会静默返回任意一行的值。"
                )
                suggestion = f"补上 GROUP BY 并把这些列都列进去（GROUP BY {columns}），或对它们套聚合函数。"
            else:
                description = (
                    f"SELECT 中的列 {columns} 未出现在 GROUP BY 里，也不是聚合结果，"
                    f"它们的取值不确定（MySQL 关闭 ONLY_FULL_GROUP_BY 时会静默返回任意一行的值）。"
                )
                suggestion = f"把 {columns} 加入 GROUP BY，或对它们套一个聚合函数。"
            issues.append(
                ctx.issue(description=description, suggestion=suggestion, statement=statement, target=columns)
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


def _has_top_level_star(select) -> bool:
    """
    只看投影列表本身，不看嵌在函数里的星号

    `SELECT count(*)` 是完全正常的写法。若用 `find_all(Star)` 一扫到底，会把它也报成
    「用了 SELECT *」——这类误报最伤使用者对问题清单的信任，实测在真实库上就踩到了。
    """
    for item in select.expressions:
        if isinstance(item, exp.Star):
            return True
        if isinstance(item, exp.Column) and isinstance(item.this, exp.Star):
            return True
    return False


def _check_select_star(ctx):
    issues = []
    for statement in ctx.statements:
        for select in statement.expression.find_all(exp.Select):
            if not _has_top_level_star(select):
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


def _is_nested(select) -> bool:
    """
    该 SELECT 是否嵌在子查询或 CTE 里

    嵌在里面的不是使用者最终要看的结果集，对它报「会返回整张表」是噪音——
    真正该看的是最外层那条。实测在含 CTE 的正确 SQL 上误报过。
    """
    node = select.parent
    while node is not None:
        if isinstance(node, exp.Subquery | exp.CTE):
            return True
        node = node.parent
    return False


def _check_no_limit(ctx):
    """
    既无 WHERE 又无 LIMIT 的查询会返回整表

    只在两者都缺时报出：有 WHERE 的查询未必需要 LIMIT，单看 LIMIT 会产生大量噪音。
    每条语句**只报一次**：`SELECT ... UNION SELECT ...` 两个分支都无过滤时，
    那是同一件事，报两遍只是噪音。
    """
    issues = []
    for statement in ctx.statements:
        for select in statement.expression.find_all(exp.Select):
            if _is_nested(select):
                continue
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
            break  # 每条语句只报一次
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
# 需快照的规则
#
# 这几条要读采集快照里的表结构。未绑定数据源时由框架跳过并记录，不会静默少跑。
# ----------------------------------------------------------------------


def _local_names(statement) -> set:
    """
    语句内自定义的名字（CTE 名、子查询别名）——它们不是真实表，不能拿去快照里找
    """
    names = set()
    for node in statement.expression.find_all(exp.CTE, exp.Subquery):
        alias = node.args.get("alias")
        name = getattr(alias, "name", "") or ""
        if name:
            names.add(name.lower())
    return names


def _alias_map(statement) -> dict:
    """
    语句里能对应到真实表的引用：别名或表名 -> (表名, 模式)
    """
    local = _local_names(statement)
    mapping = {}
    for table in statement.expression.find_all(exp.Table):
        name = table.name or ""
        if not name or name.lower() in local:
            continue
        mapping[(table.alias_or_name or name).lower()] = (name, table.db or None)
    return mapping


def _select_aliases(statement) -> set:
    """
    语句里 SELECT 起的输出别名

    这些名字可以合法地出现在 `ORDER BY` / `GROUP BY` 里：
    `SELECT count(*) AS cnt ... ORDER BY cnt`。它们**不是表里的列**，不能拿去快照里找——
    实测在真实 SQL 上误报过（`GROUP BY event_id ORDER BY domain_cnt` 把 domain_cnt 报成不存在的列）。
    """
    names = set()
    for alias in statement.expression.find_all(exp.Alias):
        name = (alias.alias or "").strip()
        if name:
            names.add(name.lower())
    return names


def _single_target(statement, aliases):
    """
    整条语句只引用一张真实表、且没有 CTE/子查询别名时，未限定的列名才能确定归属；
    其余情况无法判断，宁可漏报也不误报
    """
    local = _local_names(statement)
    real_tables = [
        table
        for table in statement.expression.find_all(exp.Table)
        if (table.name or "") and (table.name or "").lower() not in local
    ]
    if len(real_tables) == 1 and len(aliases) == 1 and not local:
        return next(iter(aliases.values()))
    return None


def _resolve_column(ctx, aliases, column, single=None):
    """
    把列解析成 (表名, 列名, 类型族)；解析不了返回 None

    比较表达式的两侧不一定是列（可能是字面量、函数调用），先挡掉。
    """
    if not isinstance(column, exp.Column):
        return None
    qualifier = (column.table or "").lower()
    target = aliases.get(qualifier) if qualifier else single
    if target is None:
        return None
    table_name, schema = target
    data_type = ctx.schema.column_type(table_name, column.name, schema)
    if not data_type:
        return None
    return table_name, column.name, type_family(data_type)


def _check_unknown_table(ctx):
    if not ctx.has_schema:
        return []
    issues = []
    for statement in ctx.statements:
        local = _local_names(statement)
        seen = set()
        for table in statement.expression.find_all(exp.Table):
            name = table.name or ""
            if not name or name.lower() in local:
                continue
            key = (name.lower(), (table.db or "").lower())
            if key in seen:
                continue
            seen.add(key)
            if ctx.schema.find_table(name, table.db or None) is not None:
                continue
            issues.append(
                ctx.issue(
                    description=f"语句引用了表 {name}，但它不在最近一次采集的快照里"
                    f"（表名可能写错，或该表是采集之后新建的、快照已过期）。",
                    suggestion="核对表名；若该表确实存在，重新采集一次该数据源以刷新快照。",
                    statement=statement,
                    target=name,
                )
            )
    return issues


def _check_unknown_column(ctx):
    """
    注意不能复用 `_resolve_column`：那个函数是**为取类型**设计的，列不存在或类型取不到时
    返回 None 表示「判不了」；而这条规则要判的恰恰是「列不存在」本身，得单独解析表再查列。
    """
    if not ctx.has_schema:
        return []
    issues = []
    for statement in ctx.statements:
        aliases = _alias_map(statement)
        if not aliases:
            continue
        single = _single_target(statement, aliases)
        output_aliases = _select_aliases(statement)
        for column in statement.expression.find_all(exp.Column):
            if column.name.lower() in output_aliases:
                continue  # 引用的是 SELECT 的输出别名，不是表里的列
            qualifier = (column.table or "").lower()
            target = aliases.get(qualifier) if qualifier else single
            if target is None:
                continue
            table_name, schema_name = target
            table = ctx.schema.find_table(table_name, schema_name)
            if table is None:
                continue  # 表本身就不存在，由 unknown_table 报，不在这里重复
            if SchemaIndex.find_column(table, column.name) is not None:
                continue
            issues.append(
                ctx.issue(
                    description=f"列 {column.name} 在表 {table_name} 中不存在（依最近一次采集的快照）。",
                    suggestion="核对列名拼写；若该列是采集之后新增的，重新采集一次该数据源。",
                    statement=statement,
                    target=f"{table_name}.{column.name}",
                )
            )
    return issues


def _report_type_conflict(ctx, statement, left, right, left_name, right_name, extra: str = ""):
    return ctx.issue(
        description=f"{left_name} 是 {left[2]} 类型、{right_name} 是 {right[2]} 类型，两种类型不同族，"
        f"比较时会发生隐式转换{extra}，通常也用不上索引。",
        suggestion="把两侧的类型对齐（改字段类型，或显式转换其中一侧）；跨类型关联在大表上代价很高。",
        statement=statement,
        target=f"{left_name} × {right_name}",
    )


def _check_join_key_type_mismatch(ctx):
    """
    JOIN 连接键类型不一致——跨类型关联是慢查询的常见根因
    """
    if not ctx.has_schema:
        return []
    issues = []
    for statement in ctx.statements:
        aliases = _alias_map(statement)
        for join in statement.expression.find_all(exp.Join):
            condition = join.args.get("on")
            if condition is None:
                continue
            for comparison in condition.find_all(exp.EQ):
                left = _resolve_column(ctx, aliases, comparison.left)
                right = _resolve_column(ctx, aliases, comparison.right)
                if left is None or right is None or not families_conflict(left[2], right[2]):
                    continue
                issues.append(
                    _report_type_conflict(
                        ctx,
                        statement,
                        left,
                        right,
                        f"{left[0]}.{left[1]}",
                        f"{right[0]}.{right[1]}",
                        extra="，连接键上的转换会让索引失效",
                    )
                )
    return issues


def _check_incomparable_types(ctx):
    """
    WHERE 里两个不同类型的列直接比较

    只判限定到具体表的列；未限定的列无法确定归属，宁可不报。
    """
    if not ctx.has_schema:
        return []
    issues = []
    for statement in ctx.statements:
        aliases = _alias_map(statement)
        single = _single_target(statement, aliases)
        for where in statement.expression.find_all(exp.Where):
            for comparison in where.find_all(exp.EQ, exp.NEQ, exp.GT, exp.GTE, exp.LT, exp.LTE):
                left = _resolve_column(ctx, aliases, comparison.left, single)
                right = _resolve_column(ctx, aliases, comparison.right, single)
                if left is None or right is None or not families_conflict(left[2], right[2]):
                    continue
                issues.append(
                    _report_type_conflict(ctx, statement, left, right, f"{left[0]}.{left[1]}", f"{right[0]}.{right[1]}")
                )
    return issues


def _check_implicit_cast(ctx):
    """
    列与字面量的类型不同族

    只报「数值列比字符串」与「字符串列比数值」两种——时间列比字符串（`created_at > '2024-01-01'`）
    是正常写法，报了就是噪音。
    """
    if not ctx.has_schema:
        return []
    issues = []
    for statement in ctx.statements:
        aliases = _alias_map(statement)
        single = _single_target(statement, aliases)
        for comparison in statement.expression.find_all(exp.EQ, exp.NEQ, exp.GT, exp.GTE, exp.LT, exp.LTE):
            for column, literal in ((comparison.left, comparison.right), (comparison.right, comparison.left)):
                if not isinstance(column, exp.Column) or not isinstance(literal, exp.Literal):
                    continue
                resolved = _resolve_column(ctx, aliases, column, single)
                if resolved is None:
                    continue
                table_name, column_name, family = resolved
                if family == "numeric" and literal.is_string:
                    detail = "数值列与字符串字面量比较，会触发隐式转换，索引用不上"
                elif family == "string" and not literal.is_string:
                    detail = "字符串列与数值字面量比较，会触发隐式转换，索引用不上"
                else:
                    continue
                issues.append(
                    ctx.issue(
                        description=f"{table_name}.{column_name} 是 {family} 类型，却与字面量 {literal.sql()[:32]} 比较：{detail}。",
                        suggestion="把字面量写成与列相同的类型（字符串加引号、数值不加），避免隐式转换。",
                        statement=statement,
                        target=f"{table_name}.{column_name}",
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
    # ---- 以下需要表结构，未绑定数据源时由框架跳过并记录原因 ----
    SqlRuleDefinition(
        code="unknown_table",
        name="引用了不存在的表",
        description="语句引用的表不在最近一次采集的快照里",
        default_level=_HIGH,
        handler=_check_unknown_table,
        needs_schema=True,
    ),
    SqlRuleDefinition(
        code="unknown_column",
        name="引用了不存在的列",
        description="语句引用的列在对应表里不存在",
        default_level=_HIGH,
        handler=_check_unknown_column,
        needs_schema=True,
    ),
    SqlRuleDefinition(
        code="join_key_type_mismatch",
        name="连接键类型不一致",
        description="JOIN 两侧连接键类型不同族，转换会让索引失效",
        default_level=_MEDIUM,
        handler=_check_join_key_type_mismatch,
        needs_schema=True,
    ),
    SqlRuleDefinition(
        code="incomparable_types",
        name="不同类型的列直接比较",
        description="WHERE 里两个列类型不同族",
        default_level=_MEDIUM,
        handler=_check_incomparable_types,
        needs_schema=True,
    ),
    SqlRuleDefinition(
        code="implicit_cast",
        name="隐式类型转换",
        description="列与字面量类型不同族，会触发隐式转换",
        default_level=_MEDIUM,
        handler=_check_implicit_cast,
        needs_schema=True,
    ),
)

"""
DDL 解析

只负责把 SQL 解析成语句、判定形态是否受支持；把语句合并进表结构是 `merge.py` 的事。
解析本身复用 `apps.sqlanalysis.services.parse_sql()`（跨模块只读契约）。
"""

import dataclasses

from sqlglot import exp

from apps.sqlanalysis import services as sqlanalysis_services


@dataclasses.dataclass(frozen=True)
class ParsedStatement:
    """
    一条输入语句：在原输入中的序号、原始 SQL、类别、语法树与是否可评审
    """

    index: int
    sql: str
    kind: str
    expression: object
    supported: bool
    reason: str = ""


@dataclasses.dataclass(frozen=True)
class ParseResult:
    statements: tuple = ()
    errors: tuple = ()

    @property
    def ok(self) -> bool:
        return not self.errors


def _alter_action(action) -> tuple:
    if isinstance(action, exp.ColumnDef | exp.AlterColumn | exp.RenameColumn | exp.AlterRename):
        return True, ""
    if isinstance(action, exp.AddConstraint):
        return True, ""
    if isinstance(action, exp.Drop):
        kind = str(action.args.get("kind") or "").upper()
        if kind in ("COLUMN", "CONSTRAINT"):
            return True, ""
        return False, f"ALTER 动作 {kind or 'DROP'} 暂不支持"
    return False, f"ALTER 动作 {type(action).__name__} 暂不支持"


def _classify(expression) -> tuple:
    """
    返回 `(kind, supported, reason)`
    """
    if isinstance(expression, exp.Create):
        kind = str(expression.args.get("kind") or "").upper()
        if kind == "TABLE":
            schema = expression.this
            has_columns = isinstance(schema, exp.Schema) and any(
                isinstance(item, exp.ColumnDef) for item in schema.expressions or ()
            )
            if not has_columns:
                return "CREATE_TABLE", False, "CREATE TABLE AS / LIKE / INHERITS / PARTITION OF 暂不支持"
            return "CREATE_TABLE", True, ""
        if kind == "INDEX":
            return "CREATE_INDEX", True, ""
        return kind or "CREATE", False, f"暂不支持 CREATE {kind}"

    if isinstance(expression, exp.Alter):
        kind = str(expression.args.get("kind") or "").upper()
        if kind != "TABLE":
            return "ALTER_TABLE", False, f"暂不支持 ALTER {kind}"
        for action in expression.args.get("actions") or ():
            supported, reason = _alter_action(action)
            if not supported:
                return "ALTER_TABLE", False, reason
        return "ALTER_TABLE", True, ""

    if isinstance(expression, exp.Drop):
        kind = str(expression.args.get("kind") or "").upper()
        if kind in ("TABLE", "INDEX"):
            return f"DROP_{kind}", True, ""
        return f"DROP_{kind or 'OBJECT'}", False, f"暂不支持 DROP {kind}"

    if isinstance(expression, exp.TruncateTable):
        return "TRUNCATE", True, ""

    if isinstance(expression, exp.Comment):
        return "COMMENT", False, "注释变更不影响结构评审"

    return "OTHER", False, "非 DDL，未参与评审"


def parse_ddl(sql: str, dialect=None) -> ParseResult:
    """
    解析一条或多条 DDL

    解析失败不抛异常——返回带行列位置的错误，由上层呈现为「无法评审」。
    """
    text = (sql or "").strip()
    if not text:
        return ParseResult(errors=({"description": "SQL 不能为空", "line": None, "col": None, "highlight": ""},))

    result = sqlanalysis_services.parse_sql(text, dialect)
    if not result.ok:
        return ParseResult(errors=tuple(result.errors))

    statements = []
    for item in result.statements:
        kind, supported, reason = _classify(item.expression)
        statements.append(
            ParsedStatement(
                index=item.index,
                sql=item.sql,
                kind=kind,
                expression=item.expression,
                supported=supported,
                reason=reason,
            )
        )
    return ParseResult(statements=tuple(statements))

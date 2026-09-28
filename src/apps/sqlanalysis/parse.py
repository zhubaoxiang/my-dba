"""
SQL 解析与语句分类

按目标方言把 SQL 文本解析为语法树，逐条判定语句类别——**类别决定能不能真跑**：
只有只读语句允许在目标库上实际执行，其余一律只出执行计划（design.md D4）。

分类采用**白名单**：只把明确无写入副作用的节点视为只读，认不出来的一律归为「其他」
并按非只读处理。方向上的保守是有意的——判错一边是「少跑一条本来能跑的语句」，
判错另一边是「在真实库上执行了不该执行的语句」。
"""

import dataclasses

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError

from utils import custom_enum

# 明确只读：真跑不改变目标库的状态。ANALYZE 不在其列——它会写统计信息
_READ_ONLY_NODES = (exp.Select, exp.Union, exp.Except, exp.Intersect, exp.Describe)
_DML_NODES = (exp.Insert, exp.Update, exp.Delete, exp.Merge)
_DDL_NODES = (exp.Create, exp.Drop, exp.Alter, exp.TruncateTable, exp.Grant, exp.Revoke, exp.Comment)

# sqlglot 认不出的语句（SHOW / EXPLAIN / VACUUM / SET…）统一落成 Command，只能按关键字二次判定
_COMMAND_READ_ONLY = ("SHOW", "EXPLAIN")
# EXPLAIN ANALYZE 会**真执行**语句（PG 上连 DML 都会写入），不在本次范围，按非只读挡住
_ANALYZE_MARKER = "ANALYZE"
_ANALYZE_SCAN_CHARS = 40

DEFAULT_DIALECT = custom_enum.DbTypeEnum.POSTGRESQL


@dataclasses.dataclass(frozen=True)
class Statement:
    """
    一条语句：语法树 + 类别 + 在原始输入中的序号
    """

    index: int
    sql: str
    kind: "custom_enum.StatementKindEnum"
    expression: object

    @property
    def is_read_only(self) -> bool:
        return self.kind == custom_enum.StatementKindEnum.READ_ONLY


@dataclasses.dataclass(frozen=True)
class ParseResult:
    """
    一次解析的结果：要么拿到语句，要么拿到语法错误

    两者不会同时为空，也可能同时非空（多语句里部分是坏的），由调用方决定怎么呈现。
    """

    statements: tuple = ()
    errors: tuple = ()

    @property
    def ok(self) -> bool:
        return not self.errors

    @property
    def has_multiple(self) -> bool:
        return len(self.statements) > 1


def dialect_of(dialect) -> "custom_enum.DbTypeEnum":
    """
    归一化方言取值，缺省为 PostgreSQL
    """
    if dialect is None or dialect == "":
        return DEFAULT_DIALECT
    if isinstance(dialect, custom_enum.DbTypeEnum):
        return dialect
    try:
        return custom_enum.DbTypeEnum(int(dialect))
    except (TypeError, ValueError):
        return DEFAULT_DIALECT


def _dialect_name(dialect) -> str:
    return "postgres" if dialect_of(dialect) == custom_enum.DbTypeEnum.POSTGRESQL else "mysql"


def _command_kind(node) -> "custom_enum.StatementKindEnum":
    """
    `Command` 是 sqlglot 的兜底节点，SHOW / EXPLAIN / VACUUM / SET 都在里面，只能看关键字
    """
    keyword = str(node.this or "").upper()
    if keyword not in _COMMAND_READ_ONLY:
        return custom_enum.StatementKindEnum.OTHER
    if keyword == "EXPLAIN":
        head = str(node.expression or "")[:_ANALYZE_SCAN_CHARS].upper()
        if _ANALYZE_MARKER in head:
            return custom_enum.StatementKindEnum.OTHER
    return custom_enum.StatementKindEnum.READ_ONLY


def classify(expression) -> "custom_enum.StatementKindEnum":
    """
    判定单条语句的类别
    """
    if isinstance(expression, _READ_ONLY_NODES):
        return custom_enum.StatementKindEnum.READ_ONLY
    if isinstance(expression, _DML_NODES):
        return custom_enum.StatementKindEnum.DML
    if isinstance(expression, _DDL_NODES):
        return custom_enum.StatementKindEnum.DDL
    if isinstance(expression, exp.Command):
        return _command_kind(expression)
    return custom_enum.StatementKindEnum.OTHER


def _error_items(exc: ParseError) -> list:
    """
    把解析异常转成带位置的可读条目

    sqlglot 的 `errors` 已经带 line / col / highlight，直接透出比只给一句 message 有用得多
    """
    items = []
    for raw in exc.errors or []:
        items.append(
            {
                "description": str(raw.get("description") or exc),
                "line": raw.get("line"),
                "col": raw.get("col"),
                "highlight": raw.get("highlight") or "",
            }
        )
    if not items:
        items.append({"description": str(exc), "line": None, "col": None, "highlight": ""})
    return items


def parse_sql(sql: str, dialect=None) -> ParseResult:
    """
    解析 SQL 文本

    解析失败不抛异常：返回带位置错误的 `ParseResult`，由上层降级为
    「只给语法错误与原始语句」（design.md D9）——不能解析恰恰是最需要帮助的时候。
    """
    text = (sql or "").strip()
    if not text:
        return ParseResult(errors=({"description": "SQL 不能为空", "line": None, "col": None, "highlight": ""},))

    name = _dialect_name(dialect)
    try:
        expressions = sqlglot.parse(text, read=name)
    except ParseError as exc:
        return ParseResult(errors=tuple(_error_items(exc)))

    statements = tuple(
        Statement(index=index, sql=expr.sql(dialect=name), kind=classify(expr), expression=expr)
        for index, expr in enumerate((item for item in expressions if item is not None), start=1)
    )
    if not statements:
        return ParseResult(errors=({"description": "未能解析出任何语句", "line": None, "col": None, "highlight": ""},))
    return ParseResult(statements=statements)


def overall_kind(statements) -> "custom_enum.StatementKindEnum":
    """
    整批语句的类别：**取最严格的一条**

    只要有一条非只读，整批就按非只读处理。多语句里混着一条 INSERT 时
    绝不能因为前面几条是 SELECT 就把整批跑掉。
    """
    kinds = [item.kind for item in statements]
    for candidate in (
        custom_enum.StatementKindEnum.DDL,
        custom_enum.StatementKindEnum.DML,
        custom_enum.StatementKindEnum.OTHER,
        custom_enum.StatementKindEnum.READ_ONLY,
    ):
        if candidate in kinds:
            return candidate
    return custom_enum.StatementKindEnum.OTHER

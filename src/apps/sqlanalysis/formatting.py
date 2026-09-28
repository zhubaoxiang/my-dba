"""
SQL 美化

从语法树重新生成排版，**只改格式不改语义**。

注意 sqlglot 是重新生成而非原地排版，因此**注释会丢失**。这是可接受的取舍：
使用者要的是「看清这条语句的结构」，而保留注释需要另一套基于原文的排版实现；
若将来确实需要，再单独做。无法解析时原样返回——美化失败不该盖住语法错误的提示。
"""

import sqlglot
from sqlglot.errors import ParseError

from apps.sqlanalysis.parse import _dialect_name


def format_sql(sql: str, dialect=None) -> str:
    """
    返回美化后的 SQL；解析不了就原样返回
    """
    text = (sql or "").strip()
    if not text:
        return ""
    name = _dialect_name(dialect)
    try:
        expressions = sqlglot.parse(text, read=name)
    except ParseError:
        return text
    pretty = [expr.sql(dialect=name, pretty=True) for expr in expressions if expr is not None]
    return ";\n\n".join(pretty) + ";\n" if pretty else text

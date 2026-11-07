"""
测试库 DDL 生成

输入快照里的表结构，输出与数据源同方言的建库 / 建表 / 索引 / 外键语句。
不连接目标库；标识符按方言转义，默认值只接受可安全还原的表达式。
"""

import re

from utils import custom_enum

POSTGRESQL = custom_enum.DbTypeEnum.POSTGRESQL.value
MYSQL = custom_enum.DbTypeEnum.MYSQL.value

_UNSUPPORTED_PG = {"ARRAY", "USER-DEFINED"}
_UNSUPPORTED_MYSQL = {
    "enum",
    "set",
    "geometry",
    "point",
    "linestring",
    "polygon",
    "multipoint",
    "multilinestring",
    "multipolygon",
    "geometrycollection",
}
_SAFE_ACTIONS = {"CASCADE", "SET NULL", "SET DEFAULT", "RESTRICT", "NO ACTION"}
_PG_INDEX_TYPES = {"btree", "hash", "gin", "gist", "brin", "spgist"}
_SAFE_DEFAULT_PATTERNS = (
    re.compile(r"^[-+]?\d+(\.\d+)?$"),
    re.compile(r"^'(?:[^']|'')*'(?:::[A-Za-z0-9_ .\[\]\"]+)?$"),
    re.compile(r"^(true|false)$", re.I),
    re.compile(r"^(now\(\)|current_timestamp(?:\(\d+\))?|current_date|current_time(?:\(\d+\))?)$", re.I),
    re.compile(r"^NULL$", re.I),
)


class DdlError(Exception):
    """
    快照结构无法可靠还原
    """


def quote_ident(db_type: int, name) -> str:
    name = str(name)
    if db_type == POSTGRESQL:
        return '"' + name.replace('"', '""') + '"'
    return "`" + name.replace("`", "``") + "`"


def qualified_table(db_type: int, table: dict) -> str:
    if db_type == POSTGRESQL:
        return f"{quote_ident(db_type, table.get('schema') or 'public')}.{quote_ident(db_type, table['name'])}"
    return quote_ident(db_type, table["name"])


def create_database_sql(db_type: int, db_name: str) -> str:
    if db_type == MYSQL:
        # 生成数据含中文，不依赖目标服务器的默认字符集
        return f"CREATE DATABASE {quote_ident(db_type, db_name)} CHARACTER SET utf8mb4"
    return f"CREATE DATABASE {quote_ident(db_type, db_name)}"


def build_single_table(db_type: int, table: dict, existing_tables: list) -> tuple:
    """
    在已有测试库中新增一张表：只生成该表及其索引/外键，外键只指向已存在的表
    """
    warnings = []
    statements = []
    if db_type == POSTGRESQL:
        schema = table.get("schema") or "public"
        if schema != "public":
            statements.append(f"CREATE SCHEMA IF NOT EXISTS {quote_ident(db_type, schema)}")
    statements.append(_create_table(db_type, table, warnings))
    statements.extend(_create_indexes(db_type, table, warnings))

    selected = {(item.get("schema"), item.get("name")): item for item in existing_tables}
    selected[(table.get("schema"), table.get("name"))] = table
    statements.extend(_create_foreign_keys(db_type, table, selected, warnings))
    return statements, warnings


def build_plan(db_type: int, db_name: str, ordered_tables: list, *, defer_indexes: bool = False) -> dict:
    """
    生成建库之后的语句（PG 的 CREATE DATABASE 单独返回）

    `defer_indexes=True` 时只返回建 schema/表语句，索引与外键放进 `post_statements`，
    供大数据模式先灌数据、后建索引与外键。
    """
    warnings = []
    statements = []

    if db_type == POSTGRESQL:
        schemas = sorted({t.get("schema") or "public" for t in ordered_tables})
        for schema in schemas:
            if schema != "public":
                statements.append(f"CREATE SCHEMA IF NOT EXISTS {quote_ident(db_type, schema)}")

    for table in ordered_tables:
        statements.append(_create_table(db_type, table, warnings))

    index_statements = []
    for table in ordered_tables:
        index_statements.extend(_create_indexes(db_type, table, warnings))

    selected = {(t.get("schema"), t.get("name")): t for t in ordered_tables}
    fk_statements = []
    for table in ordered_tables:
        fk_statements.extend(_create_foreign_keys(db_type, table, selected, warnings))

    post_statements = []
    if defer_indexes:
        post_statements = index_statements + fk_statements
    else:
        statements.extend(index_statements)
        statements.extend(fk_statements)

    return {
        "create_database_sql": create_database_sql(db_type, db_name),
        "statements": statements,
        "post_statements": post_statements,
        "warnings": warnings,
    }


def _create_table(db_type: int, table: dict, warnings: list) -> str:
    columns = table.get("columns") or []
    if not columns:
        raise DdlError(f"表 {table.get('schema')}.{table.get('name')} 没有列定义，无法建表")

    definitions = [_column_definition(db_type, column, warnings) for column in columns]
    primary_key = table.get("primary_key") or {}
    primary_columns = primary_key.get("columns") or []
    if primary_columns:
        name = primary_key.get("name") or f"pk_{table['name']}"
        quoted = ", ".join(quote_ident(db_type, column) for column in primary_columns)
        definitions.append(f"CONSTRAINT {quote_ident(db_type, name)} PRIMARY KEY ({quoted})")

    body = ",\n    ".join(definitions)
    return f"CREATE TABLE {qualified_table(db_type, table)} (\n    {body}\n)"


def _column_definition(db_type: int, column: dict, warnings: list) -> str:
    name = column.get("name")
    if not name:
        raise DdlError("快照中存在没有列名的列，无法建表")
    data_type = _column_type(db_type, column)
    sql = f"{quote_ident(db_type, name)} {data_type}"

    default = _safe_default(db_type, column, warnings)
    if default is not None:
        sql += f" DEFAULT {default}"
    if not column.get("nullable", True):
        sql += " NOT NULL"
    return sql


def _column_type(db_type: int, column: dict) -> str:
    name = column.get("name")
    data_type = (column.get("data_type") or "").strip()
    if not data_type:
        raise DdlError(f"列 {name} 缺少类型，无法建表")

    lowered = data_type.lower()
    if db_type == POSTGRESQL:
        if data_type.upper() in _UNSUPPORTED_PG or "[]" in data_type or lowered.startswith(("geometry", "geography")):
            raise DdlError(f"列 {name} 的类型 {data_type} 无法可靠还原，请缩小表范围")
    else:
        if lowered in _UNSUPPORTED_MYSQL:
            raise DdlError(f"列 {name} 的类型 {data_type} 无法可靠还原，请缩小表范围")

    length = column.get("length")
    if length not in (None, -1) and "char" in lowered:
        try:
            return f"{data_type}({int(length)})"
        except (TypeError, ValueError):
            return data_type
    return data_type


def _safe_default(db_type: int, column: dict, warnings: list):
    default = str(column.get("default") or "").strip()
    if not default:
        return None
    if default.lower().startswith("nextval("):
        warnings.append(f"列 {column.get('name')} 的序列默认值不还原，值由造数提供")
        return None
    if not any(pattern.match(default) for pattern in _SAFE_DEFAULT_PATTERNS):
        warnings.append(f"列 {column.get('name')} 的默认值无法安全还原，已跳过")
        return None
    data_type = (column.get("data_type") or "").lower()
    if db_type == MYSQL and any(token in data_type for token in ("text", "blob")):
        warnings.append(f"MySQL 的 {data_type} 列不支持默认值，已跳过 {column.get('name')} 的默认值")
        return None
    return default


def _create_indexes(db_type: int, table: dict, warnings: list) -> list:
    statements = []
    column_types = {
        column.get("name"): (column.get("data_type") or "").lower() for column in table.get("columns") or []
    }
    for position, index in enumerate(table.get("indexes") or []):
        if index.get("primary"):
            continue
        columns = index.get("columns") or []
        if not columns:
            warnings.append(f"表 {table.get('name')} 的索引 {index.get('name')} 没有列信息，已跳过")
            continue

        name = index.get("name") or f"idx_{table['name']}_{position}"
        quoted_columns = ", ".join(_index_column(db_type, column, column_types) for column in columns)
        target = qualified_table(db_type, table)
        index_type = (index.get("index_type") or "").lower()

        if db_type == MYSQL and index_type == "fulltext":
            statements.append(f"CREATE FULLTEXT INDEX {quote_ident(db_type, name)} ON {target} ({quoted_columns})")
            continue

        if db_type == POSTGRESQL and index_type and index_type != "btree":
            if index_type not in _PG_INDEX_TYPES:
                warnings.append(f"索引 {name} 的类型 {index_type} 不支持，按普通索引创建")
                index_type = ""
        else:
            index_type = ""

        using = f" USING {index_type.upper()}" if index_type else ""
        unique = "UNIQUE " if index.get("unique") else ""
        statements.append(f"CREATE {unique}INDEX {quote_ident(db_type, name)} ON {target}{using} ({quoted_columns})")
    return statements


def _index_column(db_type: int, column: str, column_types: dict) -> str:
    """
    MySQL 的 TEXT / BLOB 列建索引必须带前缀长度；快照没有前缀信息，统一取 191
    """
    quoted = quote_ident(db_type, column)
    if db_type == MYSQL:
        data_type = column_types.get(column, "")
        if any(token in data_type for token in ("text", "blob")):
            return f"{quoted}(191)"
    return quoted


def _create_foreign_keys(db_type: int, table: dict, selected: dict, warnings: list) -> list:
    statements = []
    child = qualified_table(db_type, table)
    for foreign_key in table.get("foreign_keys") or []:
        ref_key = (foreign_key.get("ref_schema"), foreign_key.get("ref_table"))
        parent = selected.get(ref_key)
        if parent is None:
            warnings.append(
                f"表 {table.get('name')} 的外键 {foreign_key.get('name')} 引用了未选中的表 "
                f"{foreign_key.get('ref_table')}，已跳过"
            )
            continue
        columns = foreign_key.get("columns") or []
        ref_columns = foreign_key.get("ref_columns") or []
        if not columns or len(columns) != len(ref_columns):
            warnings.append(f"表 {table.get('name')} 的外键 {foreign_key.get('name')} 列信息不完整，已跳过")
            continue

        name = foreign_key.get("name") or f"fk_{table['name']}_{len(statements)}"
        quoted_columns = ", ".join(quote_ident(db_type, column) for column in columns)
        quoted_ref = ", ".join(quote_ident(db_type, column) for column in ref_columns)
        sql = (
            f"ALTER TABLE {child} ADD CONSTRAINT {quote_ident(db_type, name)} "
            f"FOREIGN KEY ({quoted_columns}) REFERENCES {qualified_table(db_type, parent)} ({quoted_ref})"
        )
        action = _action(foreign_key.get("on_delete"))
        if action:
            sql += f" ON DELETE {action}"
        action = _action(foreign_key.get("on_update"))
        if action:
            sql += f" ON UPDATE {action}"
        statements.append(sql)
    return statements


def _action(value) -> str:
    action = str(value or "").strip().upper()
    return action if action in _SAFE_ACTIONS else ""

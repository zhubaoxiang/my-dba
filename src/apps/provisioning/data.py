"""
测试数据生成

只依赖快照元数据与已生成的父表行，不读取源库业务数据。
所有值都是合成数据；按列名/注释做简单语义映射，类型不兼容时回退。
"""

import json
import uuid
from datetime import date, datetime, time, timedelta
from decimal import Decimal

_STRING = "string"
_INT = "int"
_NUMBER = "number"
_BOOL = "bool"
_UUID = "uuid"
_JSON = "json"
_DATETIME = "datetime"
_DATE = "date"
_TIME = "time"
_BINARY = "binary"
_OTHER = "other"

# 顺序有意义：username 必须先于 name，否则 user_name 会被识别成姓名
_SEMANTIC_RULES = (
    ("username", ("username", "user_name", "用户名", "账号")),
    ("email", ("email", "mail", "邮箱")),
    ("phone", ("phone", "mobile", "手机", "电话")),
    ("amount", ("amount", "price", "money", "金额", "价格", "费用")),
    ("code", ("code", "编号", "编码", "单号")),
    ("url", ("url", "链接", "网址")),
    ("ip", ("ip", "ip地址")),
    ("status", ("status", "状态")),
    ("title", ("title", "标题")),
    ("address", ("address", "地址")),
    ("remark", ("remark", "description", "备注", "描述")),
    ("name", ("name", "姓名", "名称")),
)

_STRING_SEMANTICS = {"username", "email", "phone", "code", "url", "ip", "title", "address", "remark", "name"}


class DataError(Exception):
    """
    生成的数据无法满足约束
    """


def type_family(data_type) -> str:
    lowered = (data_type or "").lower()
    if not lowered:
        return _OTHER
    if "timestamp" in lowered or "datetime" in lowered:
        return _DATETIME
    if lowered.startswith("date"):
        return _DATE
    if "time" in lowered:
        return _TIME
    if "bool" in lowered:
        return _BOOL
    if "interval" in lowered:
        return _OTHER
    if "int" in lowered or "serial" in lowered:
        return _INT
    if any(token in lowered for token in ("numeric", "decimal", "real", "double", "float", "money")):
        return _NUMBER
    if "uuid" in lowered:
        return _UUID
    if "json" in lowered:
        return _JSON
    if any(token in lowered for token in ("char", "text", "clob", "string")):
        return _STRING
    if any(token in lowered for token in ("bytea", "blob", "binary")):
        return _BINARY
    return _OTHER


def semantic_of(column: dict):
    text = f"{column.get('name') or ''} {column.get('comment') or ''}".lower()
    for semantic, keywords in _SEMANTIC_RULES:
        if any(keyword in text for keyword in keywords):
            return semantic
    return None


def describe_semantics(table: dict) -> list:
    """
    预览用：列出识别到的语义列，以及是否因类型不兼容而回退
    """
    notes = []
    for column in table.get("columns") or []:
        semantic = semantic_of(column)
        if not semantic:
            continue
        family = type_family(column.get("data_type"))
        notes.append(
            {
                "column": column.get("name"),
                "semantic": semantic,
                "applied": _compatible(semantic, family),
            }
        )
    return notes


def generate_rows(table: dict, row_count: int, generated: dict, selected_keys: set, rng, start_index: int = 0) -> list:
    """
    生成一张表的数据；父表数据必须已在 `generated` 中

    `start_index` 用于向已有表追加数据：让按行号生成的语义值避开已有行的编号区间。
    """
    columns = table.get("columns") or []
    fk_map = _fk_map(table, selected_keys)
    rows = []

    for offset in range(row_count):
        index = start_index + offset
        row = {}
        for column in columns:
            name = column.get("name")
            if name in fk_map:
                foreign_key = fk_map[name]
                if foreign_key is None:
                    row[name] = None
                    continue
                parent_key = (foreign_key.get("ref_schema"), foreign_key.get("ref_table"))
                parents = generated.get(parent_key) or []
                if not parents:
                    raise DataError(f"表 {table.get('name')} 的外键 {foreign_key.get('name')} 找不到父表数据")
                position = list(foreign_key.get("columns") or []).index(name)
                ref_column = (foreign_key.get("ref_columns") or [])[position]
                parent = parents[rng.randrange(len(parents))]
                row[name] = parent.get(ref_column)
                continue

            family = type_family(column.get("data_type"))
            semantic = semantic_of(column)
            if semantic and _compatible(semantic, family):
                value = _semantic_value(semantic, index)
            else:
                value = _generic_value(family, index, rng, column)
            if value is None and not column.get("nullable", True):
                raise DataError(f"列 {column.get('name')} 的类型 {column.get('data_type')} 无法生成数据，请缩小表范围")
            row[name] = _fit_length(value, column.get("length"))
        _check_not_null(table, row)
        rows.append(row)

    _validate_unique(table, rows)
    return rows


def _fk_map(table: dict, selected_keys: set) -> dict:
    """
    列名 → 外键定义；自引用外键映射为 None（生成 NULL）
    """
    mapping = {}
    self_key = (table.get("schema"), table.get("name"))
    for foreign_key in table.get("foreign_keys") or []:
        key = (foreign_key.get("ref_schema"), foreign_key.get("ref_table"))
        if key not in selected_keys:
            continue
        columns = foreign_key.get("columns") or []
        ref_columns = foreign_key.get("ref_columns") or []
        if len(columns) != len(ref_columns):
            # DDL 生成阶段已跳过信息不完整的约束，这里同样不映射
            continue
        if key == self_key:
            for column in foreign_key.get("columns") or []:
                mapping[column] = None
            continue
        for column in foreign_key.get("columns") or []:
            mapping[column] = foreign_key
    return mapping


def _compatible(semantic: str, family: str) -> bool:
    if semantic == "amount":
        return family == _NUMBER
    if semantic == "status":
        return family == _INT
    if semantic in _STRING_SEMANTICS:
        return family == _STRING
    return False


def _semantic_value(semantic: str, index: int):
    number = index + 1
    if semantic == "email":
        return f"test{number}@example.com"
    if semantic == "phone":
        return f"138{number:08d}"
    if semantic == "username":
        return f"test_user_{number}"
    if semantic == "name":
        return f"测试用户{number}"
    if semantic == "amount":
        return Decimal(number) + Decimal("0.25")
    if semantic == "code":
        return f"CODE{number:08d}"
    if semantic == "url":
        return f"https://example.com/{number}"
    if semantic == "ip":
        return f"192.0.2.{index % 254 + 1}"
    if semantic == "status":
        return index % 3
    if semantic == "title":
        return f"测试标题{number}"
    if semantic == "address":
        return f"测试地址{number}号"
    if semantic == "remark":
        return f"测试备注{number}"
    return None


def _generic_value(family: str, index: int, rng, column: dict):
    number = index + 1
    if family == _INT:
        return number
    if family == _NUMBER:
        return Decimal(number) + Decimal("0.5")
    if family == _STRING:
        return f"{column.get('name')}_{number}"
    if family == _BOOL:
        return index % 2 == 0
    if family == _UUID:
        return str(uuid.UUID(int=rng.getrandbits(128)))
    if family == _JSON:
        # 统一给 JSON 字符串：psycopg2 不会自动适配 dict
        return json.dumps({"row": number}, ensure_ascii=False)
    if family == _DATETIME:
        return datetime(2020, 1, 1, 9, 0, 0) + timedelta(minutes=index)
    if family == _DATE:
        return date(2020, 1, 1) + timedelta(days=index % 3650)
    if family == _TIME:
        return time(9, index % 60)
    if family == _BINARY:
        return b"test"
    return None


def _fit_length(value, length):
    if not isinstance(value, str) or length in (None, -1):
        return value
    try:
        limit = int(length)
    except (TypeError, ValueError):
        return value
    if limit > 0 and len(value) > limit:
        return value[:limit]
    return value


def _check_not_null(table: dict, row: dict):
    for column in table.get("columns") or []:
        if not column.get("nullable", True) and row.get(column.get("name")) is None:
            raise DataError(f"表 {table.get('name')} 的列 {column.get('name')} 不可为空，但无法生成值")


def _validate_unique(table: dict, rows: list):
    keys = []
    primary_key = table.get("primary_key") or {}
    if primary_key.get("columns"):
        keys.append(tuple(primary_key["columns"]))
    for index in table.get("indexes") or []:
        if index.get("unique") and not index.get("primary"):
            columns = tuple(index.get("columns") or [])
            if columns:
                keys.append(columns)

    for columns in keys:
        seen = set()
        for row in rows:
            values = tuple(row.get(column) for column in columns)
            # PG 与 MySQL 的唯一约束都允许多个 NULL
            if any(value is None for value in values):
                continue
            if values in seen:
                raise DataError(
                    f"表 {table.get('name')} 的唯一约束 ({', '.join(columns)}) 无法满足，请减少行数或调整表选择"
                )
            seen.add(values)

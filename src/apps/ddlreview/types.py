"""
DDL 类型归一化

sqlglot 的类型词表与采集器存的 `information_schema.data_type` **不是同一套**：
`VARCHAR` / `DOUBLE` / `DECIMAL` 对应 `character varying` / `double precision` / `numeric`。
规则按后者判定；映射错了会静默漏报，而不是报错。这里是唯一映射点。
"""

from sqlglot import exp

# 字符类型的长度有意义（采集器存 character_maximum_length）；其余类型保持 -1
_CHAR_TOKENS = {"CHAR", "NCHAR", "VARCHAR", "NVARCHAR", "BPCHAR", "CHARACTER", "CHARACTER VARYING"}

_SIMPLE = {
    "INT": "integer",
    "INTEGER": "integer",
    "INT4": "integer",
    "BIGINT": "bigint",
    "INT8": "bigint",
    "SMALLINT": "smallint",
    "INT2": "smallint",
    "SERIAL": "integer",
    "BIGSERIAL": "bigint",
    "SMALLSERIAL": "smallint",
    "BOOLEAN": "boolean",
    "BOOL": "boolean",
    "TEXT": "text",
    "UUID": "uuid",
    "JSON": "json",
    "JSONB": "jsonb",
    "BYTEA": "bytea",
    "DATE": "date",
    "REAL": "real",
    "FLOAT4": "real",
    "DOUBLE": "double precision",
    "FLOAT": "double precision",
    "FLOAT8": "double precision",
    "VARCHAR": "character varying",
    "CHARACTER VARYING": "character varying",
    "CHAR": "character",
    "CHARACTER": "character",
    "BPCHAR": "character",
    "NCHAR": "character",
    "NVARCHAR": "character varying",
    "NUMERIC": "numeric",
    "DECIMAL": "numeric",
    "DEC": "numeric",
    "TIMESTAMP": "timestamp without time zone",
    "TIMESTAMPTZ": "timestamp with time zone",
    "TIMESTAMP WITH TIME ZONE": "timestamp with time zone",
    "TIMESTAMP WITHOUT TIME ZONE": "timestamp without time zone",
    "TIME": "time without time zone",
    "TIMETZ": "time with time zone",
    "TIME WITH TIME ZONE": "time with time zone",
    "TIME WITHOUT TIME ZONE": "time without time zone",
    "INTERVAL": "interval",
    "BIT": "bit",
    "VARBIT": "bit varying",
}


def _token(data_type) -> str:
    node = getattr(data_type, "this", None)
    return str(getattr(node, "value", node) or "").upper()


def _length(data_type, token: str) -> int:
    if token not in _CHAR_TOKENS:
        return -1
    for param in data_type.expressions or ():
        literal = param.this if isinstance(param, exp.DataTypeParam) else param
        if isinstance(literal, exp.Literal) and not literal.is_string:
            try:
                return int(literal.this)
            except (TypeError, ValueError):
                return -1
    return -1


def normalize_type(data_type) -> tuple:
    """
    sqlglot `DataType` → `(data_type, length)`，词表与采集器一致

    未知类型保留小写原文（规则按 `unknown` 族处理，不报）；`USER-DEFINED` 取类型名本身。
    """
    if data_type is None:
        return "", -1

    token = _token(data_type)
    length = _length(data_type, token)

    if token == "ARRAY":
        return "array", -1
    if token == "USER-DEFINED":
        raw = data_type.args.get("kind")
        return (str(raw).strip().lower() if raw is not None else ""), -1
    if token in _SIMPLE:
        return _SIMPLE[token], length

    rendered = (data_type.sql(dialect="postgres") or "").split("(")[0].strip().lower()
    return rendered, length

"""
表结构索引

绑定数据源且该数据源已有采集快照时，用它判定语句引用的表/列是否存在、类型是否可比。

跨模块只读走 `datasource.services` 声明的契约（`datasource_brief` / `list_snapshot_tables`），
不直接查 datasource 的 model（architecture.md）。索引在内存里建一次，供本模块全部需快照的规则共用。

类型比较只比「族」不比具体类型：`varchar(64)` 与 `text` 同族，比较它们没有问题；
`integer` 与 `varchar` 跨族，比较会触发隐式转换、通常也用不上索引。
"""

from apps.datasource import services as datasource_services

_NUMERIC = {
    "smallint",
    "integer",
    "int",
    "int2",
    "int4",
    "int8",
    "bigint",
    "smallserial",
    "serial",
    "bigserial",
    "decimal",
    "numeric",
    "real",
    "double precision",
    "float",
    "float4",
    "float8",
    "double",
    "tinyint",
    "mediumint",
    "dec",
    "fixed",
}
_STRING = {
    "char",
    "varchar",
    "character",
    "character varying",
    "text",
    "tinytext",
    "mediumtext",
    "longtext",
    "nchar",
    "nvarchar",
    "bpchar",
    "enum",
    "set",
    "clob",
    "citext",
    "name",
    "uuid",
}
_TEMPORAL = {
    "date",
    "time",
    "timestamp",
    "timestamptz",
    "datetime",
    "year",
    "interval",
    "timestamp with time zone",
    "timestamp without time zone",
    "time with time zone",
    "time without time zone",
}
_BOOLEAN = {"boolean", "bool", "bit"}
_JSON = {"json", "jsonb"}
_BINARY = {"bytea", "blob", "tinyblob", "mediumblob", "longblob", "binary", "varbinary"}

_FAMILIES = (
    ("numeric", _NUMERIC),
    ("string", _STRING),
    ("temporal", _TEMPORAL),
    ("boolean", _BOOLEAN),
    ("json", _JSON),
    ("binary", _BINARY),
)

# 跨这些族比较才值得报：字符串与时间比较是常见且正常的写法，不该报
_COMPARABLE_CROSS = {frozenset({"numeric", "string"}), frozenset({"numeric", "temporal"})}


class SchemaUnavailable(Exception):
    """
    没有可用的表结构

    消息会被原样转达给使用者——「未做结构校验」必须说明原因，不能静默跳过。
    """


def type_family(data_type: str) -> str:
    """
    把具体类型归到「族」；空的或认不出的返回 `unknown`，调用方据此放弃判定

    先剥掉长度与精度（`varchar(64)` → `varchar`），再归族。
    """
    name = (data_type or "").strip().lower()
    if not name:
        return "unknown"
    name = name.split("(")[0].strip()
    if name.endswith("[]"):
        return "array"
    for family, names in _FAMILIES:
        if name in names:
            return family
    return "unknown"


def families_conflict(left: str, right: str) -> bool:
    """
    两个类型族是否构成「不该直接比较」的组合

    任一侧认不出来就不报——宁可漏报也不误报，误报会让使用者不再信任问题清单
    """
    if "unknown" in (left, right) or "array" in (left, right) or left == right:
        return False
    return frozenset({left, right}) in _COMPARABLE_CROSS


class SchemaIndex:
    """
    采集快照里表/列的内存索引
    """

    def __init__(self, tables: list):
        self.tables = list(tables or ())
        self._by_name = {}
        for table in self.tables:
            self._by_name.setdefault((table.get("name") or "").lower(), []).append(table)

    def find_table(self, name: str, schema: str = None):
        """
        按表名查找；同名跨模式时指定了模式就按模式挑，否则取第一个
        """
        candidates = self._by_name.get((name or "").lower()) or []
        if not candidates:
            return None
        if schema:
            for item in candidates:
                if (item.get("schema") or "").lower() == schema.lower():
                    return item
        return candidates[0]

    @staticmethod
    def find_column(table, name: str):
        wanted = (name or "").lower()
        for column in table.get("columns") or []:
            if (column.get("name") or "").lower() == wanted:
                return column
        return None

    def column_type(self, table_name: str, column_name: str, schema: str = None) -> str:
        """
        取列的类型；表或列不存在时返回空串
        """
        table = self.find_table(table_name, schema)
        if table is None:
            return ""
        column = self.find_column(table, column_name)
        return (column or {}).get("data_type") or ""


def load_schema_index(datasource_id) -> SchemaIndex:
    """
    按数据源 id 取最近一次采集快照并建索引

    三种「拿不到结构」的情形各自给出可读原因，而不是笼统地返回空——使用者需要知道
    是没选数据源、选错了，还是选了但还没采集。
    """
    if not datasource_id:
        raise SchemaUnavailable("未指定数据源，本次未做表结构校验。")

    brief = datasource_services.datasource_brief(datasource_id)
    if brief is None:
        raise SchemaUnavailable(f"数据源 {datasource_id} 不存在或已删除，本次未做表结构校验。")

    tables = datasource_services.list_snapshot_tables(datasource_id)
    if not tables:
        raise SchemaUnavailable(f"数据源「{brief['name']}」尚未采集，本次未做表结构校验；请先执行一次采集。")

    return SchemaIndex(tables)

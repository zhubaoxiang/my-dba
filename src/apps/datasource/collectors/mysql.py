"""
MySQL 元数据采集器

取数来源：information_schema（+ performance_schema 取索引使用统计，取不到则降级）。
驱动选 PyMySQL：纯 Python，避免 mysqlclient 的编译依赖与 Windows 构建问题。
"""

import pymysql

from apps.datasource.collectors.base import BaseCollector
from utils import custom_enum
from utils.logger import get_logger

LOGGER = get_logger("datasource.log")

_SYSTEM_SCHEMAS = ("information_schema", "mysql", "performance_schema", "sys")

_SCHEMA_FILTER = "TABLE_SCHEMA NOT IN ('information_schema', 'mysql', 'performance_schema', 'sys')"

_TABLES_SQL = f"""
SELECT TABLE_SCHEMA AS schema_name,
       TABLE_NAME AS table_name,
       COALESCE(TABLE_COMMENT, '') AS table_comment,
       COALESCE(TABLE_ROWS, 0) AS row_count,
       COALESCE(DATA_LENGTH, 0) AS data_size,
       COALESCE(INDEX_LENGTH, 0) AS index_size,
       COALESCE(DATA_LENGTH, 0) + COALESCE(INDEX_LENGTH, 0) AS total_size
FROM information_schema.TABLES
WHERE TABLE_TYPE = 'BASE TABLE' AND {_SCHEMA_FILTER}
ORDER BY TABLE_SCHEMA, TABLE_NAME
"""

_COLUMNS_SQL = f"""
SELECT TABLE_SCHEMA AS schema_name,
       TABLE_NAME AS table_name,
       COLUMN_NAME AS column_name,
       ORDINAL_POSITION AS ordinal,
       DATA_TYPE AS data_type,
       COALESCE(CHARACTER_MAXIMUM_LENGTH, -1) AS length,
       (IS_NULLABLE = 'YES') AS nullable,
       COALESCE(COLUMN_DEFAULT, '') AS default_value,
       COALESCE(COLUMN_COMMENT, '') AS column_comment
FROM information_schema.COLUMNS
WHERE {_SCHEMA_FILTER}
ORDER BY TABLE_SCHEMA, TABLE_NAME, ORDINAL_POSITION
"""

_INDEXES_SQL = f"""
SELECT TABLE_SCHEMA AS schema_name,
       TABLE_NAME AS table_name,
       INDEX_NAME AS index_name,
       NON_UNIQUE AS non_unique,
       INDEX_TYPE AS index_type,
       SEQ_IN_INDEX AS seq_in_index,
       COLUMN_NAME AS column_name
FROM information_schema.STATISTICS
WHERE {_SCHEMA_FILTER}
ORDER BY TABLE_SCHEMA, TABLE_NAME, INDEX_NAME, SEQ_IN_INDEX
"""

_PRIMARY_KEYS_SQL = f"""
SELECT TABLE_SCHEMA AS schema_name,
       TABLE_NAME AS table_name,
       CONSTRAINT_NAME AS constraint_name,
       COLUMN_NAME AS column_name,
       ORDINAL_POSITION AS ordinal
FROM information_schema.KEY_COLUMN_USAGE
WHERE CONSTRAINT_NAME = 'PRIMARY' AND {_SCHEMA_FILTER}
ORDER BY TABLE_SCHEMA, TABLE_NAME, ORDINAL_POSITION
"""

_FOREIGN_KEYS_SQL = f"""
SELECT TABLE_SCHEMA AS schema_name,
       TABLE_NAME AS table_name,
       CONSTRAINT_NAME AS constraint_name,
       COLUMN_NAME AS column_name,
       ORDINAL_POSITION AS ordinal,
       REFERENCED_TABLE_SCHEMA AS ref_schema,
       REFERENCED_TABLE_NAME AS ref_table,
       REFERENCED_COLUMN_NAME AS ref_column
FROM information_schema.KEY_COLUMN_USAGE
WHERE REFERENCED_TABLE_NAME IS NOT NULL AND {_SCHEMA_FILTER}
ORDER BY TABLE_SCHEMA, TABLE_NAME, CONSTRAINT_NAME, ORDINAL_POSITION
"""

# 索引使用统计：MySQL 5.7+ / 8.0 可用；performance_schema 关闭或无权限时降级
_INDEX_USAGE_SQL = """
SELECT OBJECT_SCHEMA AS schema_name,
       OBJECT_NAME AS table_name,
       INDEX_NAME AS index_name,
       COUNT_STAR AS scans
FROM performance_schema.table_io_waits_summary_by_index_usage
WHERE INDEX_NAME IS NOT NULL
"""


class MySQLCollector(BaseCollector):
    """
    MySQL 元数据采集器
    """

    db_type = custom_enum.DbTypeEnum.MYSQL.value

    def connect(self):
        self._conn = pymysql.connect(
            host=self.datasource.host,
            port=self.datasource.port,
            user=self.datasource.username,
            password=self.password(),
            database=self.datasource.db_name or None,
            connect_timeout=self.config.get("connect_timeout", 5),
            read_timeout=self.config.get("statement_timeout", 30),
            charset="utf8mb4",
            cursorclass=pymysql.cursors.DictCursor,
            autocommit=True,
        )
        self._set_readonly()
        return self._conn

    def _set_readonly(self):
        """
        尽力把会话设为只读；老版本或权限不足时忽略（采集语句本身全是读）
        """
        with self._conn.cursor() as cur:
            for stmt in (
                f"SET SESSION MAX_EXECUTION_TIME = {int(self.config.get('statement_timeout', 30)) * 1000}",
                "SET SESSION TRANSACTION READ ONLY",
            ):
                try:
                    cur.execute(stmt)
                except Exception as exc:  # noqa: BLE001 版本或权限不支持时降级
                    LOGGER.warning("MySQL 会话设置跳过 stmt=%s err=%s", stmt, exc)

    def _rows(self, sql: str) -> list:
        with self._conn.cursor() as cur:
            cur.execute(sql)
            return [dict(row) for row in cur.fetchall()]

    def _server_version(self) -> str:
        rows = self._rows("SELECT VERSION() AS version")
        return rows[0]["version"] if rows else ""

    def _schemas(self) -> list:
        placeholders = ", ".join(["%s"] * len(_SYSTEM_SCHEMAS))
        rows = self._rows(
            f"SELECT SCHEMA_NAME FROM information_schema.SCHEMATA WHERE SCHEMA_NAME NOT IN ({placeholders})"
        )
        return [row["SCHEMA_NAME"] for row in rows]

    def _tables(self) -> list:
        return [
            {
                "schema": row["schema_name"],
                "name": row["table_name"],
                "comment": row["table_comment"],
                "row_count": int(row["row_count"] or 0),
                "row_count_exact": False,
                "data_size": int(row["data_size"] or 0),
                "index_size": int(row["index_size"] or 0),
                "total_size": int(row["total_size"] or 0),
            }
            for row in self._rows(_TABLES_SQL)
        ]

    def _columns(self) -> list:
        return [
            {
                "schema": row["schema_name"],
                "table": row["table_name"],
                "name": row["column_name"],
                "ordinal": int(row["ordinal"]),
                "data_type": row["data_type"],
                "length": int(row["length"]),
                "nullable": bool(row["nullable"]),
                "default": str(row["default_value"] or ""),
                "comment": row["column_comment"],
            }
            for row in self._rows(_COLUMNS_SQL)
        ]

    def _indexes(self) -> list:
        usage = self._index_usage()
        grouped = {}
        for row in self._rows(_INDEXES_SQL):
            key = (row["schema_name"], row["table_name"], row["index_name"])
            entry = grouped.get(key)
            if entry is None:
                scans = usage.get(key)
                if scans is not None:
                    self._index_usage_available = True
                entry = {
                    "schema": row["schema_name"],
                    "table": row["table_name"],
                    "name": row["index_name"],
                    "unique": int(row["non_unique"]) == 0,
                    "primary": row["index_name"] == "PRIMARY",
                    "index_type": row["index_type"],
                    "columns": [],
                    "scans": scans,
                }
                grouped[key] = entry
            entry["columns"].append(row["column_name"])
        return list(grouped.values())

    def _index_usage(self) -> dict:
        """
        performance_schema 不可用时返回空字典，索引使用统计将被标记为未采集
        """
        try:
            return {
                (row["schema_name"], row["table_name"], row["index_name"]): int(row["scans"] or 0)
                for row in self._rows(_INDEX_USAGE_SQL)
            }
        except Exception as exc:  # noqa: BLE001 performance_schema 关闭或无权限
            LOGGER.warning("MySQL 索引使用统计不可用，将降级跳过该规则 err=%s", exc)
            return {}

    def _primary_keys(self) -> list:
        grouped = {}
        for row in self._rows(_PRIMARY_KEYS_SQL):
            key = (row["schema_name"], row["table_name"])
            entry = grouped.setdefault(key, {"name": row["constraint_name"], "columns": []})
            entry["columns"].append(row["column_name"])
        return [
            {"schema": schema, "table": table, "name": item["name"], "columns": item["columns"]}
            for (schema, table), item in grouped.items()
        ]

    def _foreign_keys(self) -> list:
        grouped = {}
        for row in self._rows(_FOREIGN_KEYS_SQL):
            key = (row["schema_name"], row["table_name"], row["constraint_name"])
            entry = grouped.get(key)
            if entry is None:
                entry = {
                    "schema": row["schema_name"],
                    "table": row["table_name"],
                    "name": row["constraint_name"],
                    "columns": [],
                    "ref_schema": row["ref_schema"],
                    "ref_table": row["ref_table"],
                    "ref_columns": [],
                }
                grouped[key] = entry
            entry["columns"].append(row["column_name"])
            entry["ref_columns"].append(row["ref_column"])
        return list(grouped.values())

    def _exact_row_count(self, schema, table) -> int:
        with self._conn.cursor() as cur:
            cur.execute(f"SELECT COUNT(*) AS cnt FROM `{schema}`.`{table}`")
            return int(cur.fetchone()["cnt"])

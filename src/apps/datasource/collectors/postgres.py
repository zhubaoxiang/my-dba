"""
PostgreSQL 元数据采集器

取数来源：information_schema + pg_catalog。
行数与体积优先取估算值（reltuples / n_live_tup / pg_*_size），避免在生产库上做全表扫描。
"""

import psycopg2
import psycopg2.extras

from apps.datasource.collectors.base import BaseCollector
from utils import custom_enum

_EXCLUDED_SCHEMAS = ("pg_catalog", "information_schema")

_SCHEMA_FILTER = """
    n.nspname NOT IN ('pg_catalog', 'information_schema')
    AND n.nspname NOT LIKE 'pg\\_temp%'
    AND n.nspname NOT LIKE 'pg\\_toast%'
"""

_TABLES_SQL = f"""
SELECT n.nspname                                   AS schema_name,
       c.relname                                   AS table_name,
       COALESCE(obj_description(c.oid, 'pg_class'), '') AS table_comment,
       COALESCE(s.n_live_tup, c.reltuples)::bigint AS row_count,
       pg_table_size(c.oid)::bigint                AS data_size,
       pg_indexes_size(c.oid)::bigint              AS index_size,
       pg_total_relation_size(c.oid)::bigint       AS total_size
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
LEFT JOIN pg_stat_user_tables s ON s.relid = c.oid
WHERE c.relkind IN ('r', 'p') AND {_SCHEMA_FILTER}
ORDER BY n.nspname, c.relname
"""

_COLUMNS_SQL = """
SELECT c.table_schema                                   AS schema_name,
       c.table_name                                     AS table_name,
       c.column_name                                    AS column_name,
       c.ordinal_position                               AS ordinal,
       c.data_type                                      AS data_type,
       COALESCE(c.character_maximum_length, -1)         AS length,
       (c.is_nullable = 'YES')                          AS nullable,
       COALESCE(c.column_default, '')                   AS default_value,
       COALESCE(pg_catalog.col_description(pc.oid, c.ordinal_position), '') AS column_comment
FROM information_schema.columns c
JOIN pg_catalog.pg_namespace pn ON pn.nspname = c.table_schema
JOIN pg_catalog.pg_class pc ON pc.relname = c.table_name AND pc.relnamespace = pn.oid
WHERE c.table_schema NOT IN ('pg_catalog', 'information_schema')
ORDER BY c.table_schema, c.table_name, c.ordinal_position
"""

_INDEXES_SQL = f"""
SELECT n.nspname      AS schema_name,
       t.relname      AS table_name,
       i.relname      AS index_name,
       ix.indisunique AS is_unique,
       ix.indisprimary AS is_primary,
       am.amname      AS index_type,
       -- indkey 是 int2vector，下标从 0 起（不同于普通数组），故列号需 +1；
       -- 若直接传 k，k=0 会被 pg_get_indexdef 当作「返回整条索引定义」
       ARRAY(SELECT pg_get_indexdef(ix.indexrelid, k + 1, true)
             FROM generate_subscripts(ix.indkey, 1) AS k ORDER BY k) AS index_columns,
       s.idx_scan     AS scans
FROM pg_index ix
JOIN pg_class i ON i.oid = ix.indexrelid
JOIN pg_class t ON t.oid = ix.indrelid
JOIN pg_namespace n ON n.oid = t.relnamespace
JOIN pg_am am ON am.oid = i.relam
LEFT JOIN pg_stat_user_indexes s ON s.indexrelid = ix.indexrelid
WHERE t.relkind IN ('r', 'p') AND {_SCHEMA_FILTER}
ORDER BY n.nspname, t.relname, i.relname
"""

_PRIMARY_KEYS_SQL = f"""
SELECT n.nspname AS schema_name,
       t.relname AS table_name,
       con.conname AS constraint_name,
       ARRAY(SELECT a.attname
             FROM unnest(con.conkey) WITH ORDINALITY AS k(attnum, ord)
             JOIN pg_attribute a ON a.attrelid = con.conrelid AND a.attnum = k.attnum
             ORDER BY k.ord) AS pk_columns
FROM pg_constraint con
JOIN pg_class t ON t.oid = con.conrelid
JOIN pg_namespace n ON n.oid = t.relnamespace
WHERE con.contype = 'p' AND t.relkind IN ('r', 'p') AND {_SCHEMA_FILTER}
"""

_FOREIGN_KEYS_SQL = f"""
SELECT n.nspname  AS schema_name,
       t.relname  AS table_name,
       con.conname AS constraint_name,
       ARRAY(SELECT a.attname
             FROM unnest(con.conkey) WITH ORDINALITY AS k(attnum, ord)
             JOIN pg_attribute a ON a.attrelid = con.conrelid AND a.attnum = k.attnum
             ORDER BY k.ord) AS fk_columns,
       rn.nspname AS ref_schema,
       rt.relname AS ref_table,
       ARRAY(SELECT a.attname
             FROM unnest(con.confkey) WITH ORDINALITY AS k(attnum, ord)
             JOIN pg_attribute a ON a.attrelid = con.confrelid AND a.attnum = k.attnum
             ORDER BY k.ord) AS ref_columns
FROM pg_constraint con
JOIN pg_class t ON t.oid = con.conrelid
JOIN pg_namespace n ON n.oid = t.relnamespace
JOIN pg_class rt ON rt.oid = con.confrelid
JOIN pg_namespace rn ON rn.oid = rt.relnamespace
WHERE con.contype = 'f' AND t.relkind IN ('r', 'p') AND {_SCHEMA_FILTER}
"""


class PostgresCollector(BaseCollector):
    """
    PostgreSQL 元数据采集器
    """

    db_type = custom_enum.DbTypeEnum.POSTGRESQL.value

    def connect(self):
        self._conn = psycopg2.connect(
            host=self.datasource.host,
            port=self.datasource.port,
            dbname=self.datasource.db_name,
            user=self.datasource.username,
            password=self.password(),
            connect_timeout=self.config.get("connect_timeout", 5),
            application_name="my-dba-metadata-collector",
        )
        self._conn.set_session(readonly=True, autocommit=True)
        with self._conn.cursor() as cur:
            cur.execute("SET statement_timeout = %s", (int(self.config.get("statement_timeout", 30)) * 1000,))
        return self._conn

    def _rows(self, sql: str) -> list:
        with self._conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql)
            return [dict(row) for row in cur.fetchall()]

    def _server_version(self) -> str:
        rows = self._rows("SELECT current_setting('server_version') AS version")
        return rows[0]["version"] if rows else ""

    def _schemas(self) -> list:
        rows = self._rows(
            f"""
            SELECT n.nspname AS schema_name
            FROM pg_namespace n
            WHERE {_SCHEMA_FILTER}
            ORDER BY n.nspname
            """
        )
        return [row["schema_name"] for row in rows]

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
                "default": row["default_value"],
                "comment": row["column_comment"],
            }
            for row in self._rows(_COLUMNS_SQL)
        ]

    def _indexes(self) -> list:
        result = []
        for row in self._rows(_INDEXES_SQL):
            scans = row["scans"]
            if scans is not None:
                self._index_usage_available = True
            result.append(
                {
                    "schema": row["schema_name"],
                    "table": row["table_name"],
                    "name": row["index_name"],
                    "unique": bool(row["is_unique"]),
                    "primary": bool(row["is_primary"]),
                    "index_type": row["index_type"],
                    "columns": list(row["index_columns"]),
                    "scans": None if scans is None else int(scans),
                }
            )
        return result

    def _primary_keys(self) -> list:
        return [
            {
                "schema": row["schema_name"],
                "table": row["table_name"],
                "name": row["constraint_name"],
                "columns": list(row["pk_columns"]),
            }
            for row in self._rows(_PRIMARY_KEYS_SQL)
        ]

    def _foreign_keys(self) -> list:
        return [
            {
                "schema": row["schema_name"],
                "table": row["table_name"],
                "name": row["constraint_name"],
                "columns": list(row["fk_columns"]),
                "ref_schema": row["ref_schema"],
                "ref_table": row["ref_table"],
                "ref_columns": list(row["ref_columns"]),
            }
            for row in self._rows(_FOREIGN_KEYS_SQL)
        ]

    def _exact_row_count(self, schema, table) -> int:
        with self._conn.cursor() as cur:
            cur.execute(f'SELECT COUNT(*) FROM "{schema}"."{table}"')
            return int(cur.fetchone()[0])

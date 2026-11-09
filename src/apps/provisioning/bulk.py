"""
大数据批量写入

PostgreSQL 使用 COPY FROM STDIN；MySQL 使用 LOAD DATA LOCAL INFILE，失败时回退多行 INSERT。
所有值都由生成器产出，不拼接原始 SQL 字符串（LOAD DATA 的临时文件由服务端生成）。
"""

import contextlib
import io
import os
import tempfile
from datetime import date, datetime, time
from decimal import Decimal

from apps.provisioning import ddl
from utils import custom_enum

POSTGRESQL = custom_enum.DbTypeEnum.POSTGRESQL.value
MYSQL = custom_enum.DbTypeEnum.MYSQL.value

_PG_ESCAPE = {
    "\\": "\\\\",
    "\t": "\\t",
    "\n": "\\n",
    "\r": "\\r",
    "\b": "\\b",
    "\f": "\\f",
    "\v": "\\v",
}
_MYSQL_ESCAPE = {
    "\\": "\\\\",
    "\t": "\\t",
    "\n": "\\n",
    "\r": "\\r",
    "\0": "\\0",
}


class BulkCancelled(Exception):
    """
    用户在批次边界请求取消
    """


def format_pg_field(value) -> str:
    if value is None:
        return "\\N"
    if isinstance(value, bool):
        return "t" if value else "f"
    if isinstance(value, bytes):
        return "\\\\x" + value.hex()
    if isinstance(value, datetime):
        return value.isoformat(sep=" ")
    if isinstance(value, date | time):
        return value.isoformat()
    if isinstance(value, int | float | Decimal):
        return str(value)
    text = str(value)
    return "".join(_PG_ESCAPE.get(char, char) for char in text)


def format_mysql_field(value) -> str:
    if value is None:
        return "\\N"
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, bytes):
        # 生成器目前只产生 b"test"；二进制列的表在服务层强制走 INSERT
        return value.decode("utf-8", "replace")
    if isinstance(value, datetime):
        return value.isoformat(sep=" ")
    if isinstance(value, date | time):
        return value.isoformat()
    if isinstance(value, int | float | Decimal):
        return str(value)
    text = str(value)
    return "".join(_MYSQL_ESCAPE.get(char, char) for char in text)


def copy_batch(cursor, table: dict, columns: list, batch: list) -> None:
    lines = ["\t".join(format_pg_field(row.get(column)) for column in columns) for row in batch]
    text = "\n".join(lines) + "\n"
    quoted = ", ".join(ddl.quote_ident(POSTGRESQL, column) for column in columns)
    sql = (
        f"COPY {ddl.qualified_table(POSTGRESQL, table)} ({quoted}) "
        "FROM STDIN WITH (FORMAT text, DELIMITER E'\\t', NULL E'\\\\N')"
    )
    cursor.copy_expert(sql, io.StringIO(text))


def load_data_batch(conn, table: dict, columns: list, batch: list, tmp_dir: str = "") -> None:
    fd, path = tempfile.mkstemp(prefix="my-dba-bulk-", suffix=".tsv", dir=tmp_dir or None)
    os.close(fd)
    try:
        with open(path, "w", encoding="utf-8", newline="") as stream:
            for row in batch:
                stream.write("\t".join(format_mysql_field(row.get(column)) for column in columns))
                stream.write("\n")
        quoted = ", ".join(ddl.quote_ident(MYSQL, column) for column in columns)
        sql = (
            f"LOAD DATA LOCAL INFILE %s INTO TABLE {ddl.qualified_table(MYSQL, table)} "
            "CHARACTER SET utf8mb4 "
            "FIELDS TERMINATED BY '\\t' ESCAPED BY '\\\\' "
            f"LINES TERMINATED BY '\\n' ({quoted})"
        )
        with conn.cursor() as cursor:
            cursor.execute(sql, [path])
    finally:
        with contextlib.suppress(OSError):
            os.remove(path)


def insert_batch(cursor, table: dict, columns: list, batch: list, chunk: int = 1000) -> None:
    if not batch:
        return
    quoted = ", ".join(ddl.quote_ident(MYSQL, column) for column in columns)
    target = ddl.qualified_table(MYSQL, table)
    for start in range(0, len(batch), chunk):
        part = batch[start : start + chunk]
        placeholders = ", ".join("(" + ", ".join(["%s"] * len(columns)) + ")" for _ in part)
        sql = f"INSERT INTO {target} ({quoted}) VALUES {placeholders}"
        params = [row.get(column) for row in part for column in columns]
        cursor.execute(sql, params)


def local_infile_error(exc) -> bool:
    code = exc.args[0] if getattr(exc, "args", None) else None
    return code in (1148, 3948, 2068) or "local_infile" in str(exc).lower()


def load_table(
    conn,
    db_type: int,
    table: dict,
    row_iter,
    *,
    batch_rows: int,
    commit_rows: int,
    on_progress,
    cancel_check,
    tmp_dir: str = "",
    allow_load_data: bool = True,
    insert_chunk: int = 1000,
) -> int:
    """
    消费 row_iter 并分批写入；返回写入行数

    `on_progress(processed, table_label)` 供服务层做进度与心跳；`cancel_check()`
    在每批开始前检查用户是否请求取消。
    """
    columns = [column.get("name") for column in table.get("columns") or []]
    label = f"{table.get('schema')}.{table.get('name')}"
    processed = 0
    since_commit = 0
    batch = []

    def flush():
        nonlocal processed, since_commit, allow_load_data
        if not batch:
            return
        if db_type == POSTGRESQL:
            with conn.cursor() as cursor:
                copy_batch(cursor, table, columns, batch)
        elif allow_load_data and not _has_binary(table):
            try:
                load_data_batch(conn, table, columns, batch, tmp_dir)
            except Exception as exc:  # noqa: BLE001 只对 local_infile 相关错误回退
                if not local_infile_error(exc):
                    raise
                allow_load_data = False
                with conn.cursor() as cursor:
                    insert_batch(cursor, table, columns, batch, insert_chunk)
        else:
            with conn.cursor() as cursor:
                insert_batch(cursor, table, columns, batch, insert_chunk)
        processed += len(batch)
        since_commit += len(batch)
        batch.clear()
        if since_commit >= commit_rows:
            conn.commit()
            since_commit = 0
        on_progress(processed, label)

    try:
        for row in row_iter:
            batch.append(row)
            if len(batch) >= batch_rows:
                if cancel_check():
                    raise BulkCancelled()
                flush()
        if cancel_check():
            raise BulkCancelled()
        flush()
        if since_commit:
            conn.commit()
    except Exception:
        # 已提交批次无法回滚，调用方通过删除目标库完成清理
        with contextlib.suppress(Exception):
            conn.rollback()
        raise
    on_progress(processed, label)
    return processed


def _has_binary(table: dict) -> bool:
    for column in table.get("columns") or []:
        data_type = (column.get("data_type") or "").lower()
        if any(token in data_type for token in ("bytea", "blob", "binary")):
            return True
    return False

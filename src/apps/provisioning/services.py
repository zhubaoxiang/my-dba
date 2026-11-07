"""
测试库预置编排

安全约束（与 openspec add-test-db-provisioning 一致）：
- 仅测试环境数据源
- 仅使用独立的预置写账号（与只读采集账号分离）
- 只新建唯一命名的独立 database，绝不写入已有库
- 失败尽力删库，记录保留；删除只接受本功能记录过的库
"""

import random
import re
import secrets
from datetime import date, datetime, time, timedelta
from decimal import Decimal

import psycopg2
import pymysql
from psycopg2 import sql as pg_sql

from apps.datasource import services as datasource_services
from apps.provisioning import data as data_gen
from apps.provisioning import ddl, models
from utils import crypto, custom_enum
from utils.configure import CONF_ATTR
from utils.logger import get_logger

LOGGER = get_logger("provisioning.log")

POSTGRESQL = custom_enum.DbTypeEnum.POSTGRESQL.value
MYSQL = custom_enum.DbTypeEnum.MYSQL.value
TEST_ENV = custom_enum.DatasourceEnvEnum.TEST.value


class ProvisionError(Exception):
    """
    可预期的预置失败（环境、权限、限制、约束）
    """


class ProvisionPermissionError(ProvisionError):
    """
    不是数据源或预置记录的创建者
    """


def _int_config(key: str, default: int) -> int:
    try:
        return int(CONF_ATTR.get(key))
    except (TypeError, ValueError):
        return default


def _config() -> dict:
    return {
        "db_prefix": (CONF_ATTR.get("provisioning_db_prefix") or "dba_test").strip() or "dba_test",
        "max_tables": _int_config("provisioning_max_tables", 20),
        "max_rows_per_table": _int_config("provisioning_max_rows_per_table", 1000),
        "max_total_rows": _int_config("provisioning_max_total_rows", 5000),
        "timeout_seconds": _int_config("provisioning_timeout_seconds", 120),
        "snapshot_max_age_days": _int_config("provisioning_snapshot_max_age_days", 7),
        "bulk_threshold": _int_config("provisioning_bulk_threshold", 100000),
        "bulk_max_rows_per_table": _int_config("provisioning_bulk_max_rows_per_table", 100000000),
        "bulk_max_total_rows": _int_config("provisioning_bulk_max_total_rows", 100000000),
        "bulk_batch_rows": _int_config("provisioning_bulk_batch_rows", 20000),
        "bulk_commit_rows": _int_config("provisioning_bulk_commit_rows", 1000000),
    }


def preview(datasource_id: int, tables: list, seed=None, actor: str = "", is_admin: bool = True) -> dict:
    """
    只生成计划，不连接目标库、不创建任何 database
    """
    config = _config()
    target, snapshot, ordered, rows_map, seed, warnings = _prepare(datasource_id, tables, seed, config, actor, is_admin)
    db_name = _generate_db_name(config["db_prefix"], target["id"])
    payload = _plan_payload(target, snapshot, ordered, rows_map, db_name, seed, warnings)
    LOGGER.info("测试库预置预览 datasource_id=%s tables=%s rows=%s", target["id"], len(ordered), payload["total_rows"])
    return payload


def provision(
    datasource_id: int,
    tables: list,
    db_name: str,
    seed=None,
    confirm: bool = False,
    creator: str = "",
    is_admin: bool = True,
):
    """
    执行预置；返回 (记录, 警告)
    """
    if not confirm:
        raise ProvisionError("请先确认在测试环境创建独立数据库")

    config = _config()
    target, snapshot, ordered, rows_map, seed, warnings = _prepare(
        datasource_id, tables, seed, config, creator, is_admin
    )
    _validate_db_name(db_name, config["db_prefix"], target["id"], target["db_type"])
    if models.ProvisionedDatabase.objects.filter(
        datasource_id=target["id"], db_name=db_name, is_deleted=False
    ).exists():
        raise ProvisionError("该库名已有预置记录，请重新预览")
    try:
        exists = _database_exists(target, db_name)
    except Exception as exc:  # noqa: BLE001 连接目标库失败要转成可读业务错误
        raise ProvisionError(f"连接目标库失败：{_error_text(exc, _safe_secret(target))}") from exc
    if exists:
        raise ProvisionError("目标服务器上已存在同名数据库，请重新预览")

    payload = _plan_payload(target, snapshot, ordered, rows_map, db_name, seed, warnings)
    record = models.ProvisionedDatabase.objects.create(
        datasource_id=target["id"],
        db_name=db_name,
        status=custom_enum.ProvisionStatusEnum.CREATING.value,
        tables=[
            {"schema": item.get("schema"), "name": item.get("name"), "rows": rows_map[_key(item)]} for item in ordered
        ],
        row_count=0,
        creator=creator,
    )

    created = False
    try:
        _create_database(target, db_name)
        created = True
        row_count = _create_tables_and_data(target, db_name, payload["statements"][1:], ordered, rows_map, seed, config)
        record.status = custom_enum.ProvisionStatusEnum.READY.value
        record.row_count = row_count
        record.error = ""
        record.save(update_fields=["status", "row_count", "error", "update_time"])
        LOGGER.info(
            "测试库预置成功 datasource_id=%s db_name=%s tables=%s rows=%s creator=%s",
            target["id"],
            db_name,
            len(ordered),
            row_count,
            creator,
        )
        return record, payload["warnings"]
    except Exception as exc:  # noqa: BLE001 失败必须落记录并尽量清理
        message = _error_text(exc, _safe_secret(target))
        cleanup_error = ""
        if created:
            try:
                _drop_database(target, db_name)
            except Exception as drop_exc:  # noqa: BLE001 清理失败不能盖住原始错误
                cleanup_error = _error_text(drop_exc, _safe_secret(target))
                LOGGER.error("测试库清理失败 datasource_id=%s db_name=%s err=%s", target["id"], db_name, cleanup_error)
        record.status = custom_enum.ProvisionStatusEnum.FAILED.value
        record.error = (message + (f"；清理失败：{cleanup_error}" if cleanup_error else ""))[:1024]
        record.save(update_fields=["status", "error", "update_time"])
        LOGGER.error("测试库预置失败 datasource_id=%s db_name=%s err=%s", target["id"], db_name, message)
        raise ProvisionError(message) from exc


def delete_record(record, actor: str = "", is_admin: bool = True) -> None:
    """
    删除本功能创建过的测试库；只接受记录里的库名
    """
    if not is_admin and record.creator != actor:
        raise ProvisionPermissionError("只能删除自己创建的测试库")
    if record.status == custom_enum.ProvisionStatusEnum.DROPPED.value:
        raise ProvisionError("该测试库已删除")
    target = datasource_services.provisioning_target(record.datasource_id)
    if target is None:
        raise ProvisionError("数据源不存在，无法删除该测试库")
    try:
        _drop_database(target, record.db_name)
    except Exception as exc:  # noqa: BLE001 连接或删除失败要给出可读原因
        raise ProvisionError(f"删除测试库失败：{_error_text(exc, _safe_secret(target))}") from exc
    record.status = custom_enum.ProvisionStatusEnum.DROPPED.value
    record.error = ""
    record.save(update_fields=["status", "error", "update_time"])
    LOGGER.info("测试库已删除 datasource_id=%s db_name=%s", record.datasource_id, record.db_name)


# ----------------------------------------------------------------------
# 测试数据文件导出（只读快照，不连接目标库）
# ----------------------------------------------------------------------


def export_test_data(
    datasource_id: int,
    tables: list,
    seed=None,
    include_ddl: bool = False,
    actor: str = "",
    is_admin: bool = True,
) -> dict:
    """
    按快照结构生成 INSERT SQL 文件内容，供使用者下载后自行导入

    只读元数据，不连接目标库、不执行任何语句；普通用户限自己创建的数据源。
    """
    target = datasource_services.provisioning_target(datasource_id)
    if target is None:
        raise ProvisionError("数据源不存在")
    if not is_admin and target.get("creator") != actor:
        raise ProvisionPermissionError("只能使用自己创建的数据源")

    snapshot = datasource_services.latest_snapshot_data(datasource_id)
    if snapshot is None:
        raise ProvisionError("该数据源尚未采集，无法生成测试数据")
    return _build_export(
        db_type=target["db_type"],
        datasource_id=target["id"],
        datasource_name=target["name"],
        snapshot_time=snapshot.get("collect_time", ""),
        all_tables=snapshot.get("tables") or [],
        selection=tables,
        seed=seed,
        include_ddl=include_ddl,
        actor=actor,
    )


def parse_table_structure(db_type: int, ddl_text: str) -> dict:
    """
    解析手写的 CREATE TABLE / CREATE INDEX / ALTER TABLE，返回表结构摘要
    """
    tables, warnings = _parse_ddl_tables(db_type, ddl_text)
    return {
        "db_type": db_type,
        "db_type_label": custom_enum.DbTypeEnum(db_type).label,
        "tables": [
            {
                "schema": table.get("schema"),
                "name": table.get("name"),
                "column_count": len(table.get("columns") or []),
            }
            for table in tables
        ],
        "warnings": warnings,
    }


def export_test_data_from_ddl(
    db_type: int,
    ddl_text: str,
    tables: list,
    seed=None,
    include_ddl: bool = False,
    actor: str = "",
) -> dict:
    """
    基于手动输入的表结构生成数据文件；不依赖数据源，因此没有归属限制
    """
    parsed_tables, parse_warnings = _parse_ddl_tables(db_type, ddl_text)
    payload = _build_export(
        db_type=db_type,
        datasource_id=None,
        datasource_name="手动输入的表结构",
        snapshot_time="",
        all_tables=parsed_tables,
        selection=tables,
        seed=seed,
        include_ddl=include_ddl,
        actor=actor,
    )
    payload["warnings"] = parse_warnings + payload["warnings"]
    return payload


def _parse_ddl_tables(db_type: int, ddl_text: str) -> tuple:
    """
    解析手写 DDL 为快照形态的表结构

    只接受 CREATE TABLE / CREATE INDEX / ALTER TABLE；注释类语句忽略并给出提示，
    其余（DROP / TRUNCATE / 视图等）直接拒绝，避免把「删表」当成结构定义。
    """
    from apps.ddlreview import merge as ddl_merge
    from apps.ddlreview import parser as ddl_parser

    parsed = ddl_parser.parse_ddl(ddl_text, db_type)
    if not parsed.ok:
        messages = "；".join(str(item.get("description") or "解析失败") for item in parsed.errors[:3])
        raise ProvisionError(f"表结构解析失败：{messages}")

    tables = []
    warnings = []
    for statement in parsed.statements:
        if statement.kind == "COMMENT" or (not statement.supported and statement.kind == "OTHER"):
            warnings.append(f"第 {statement.index} 条语句已忽略：{statement.reason}")
            continue
        if statement.kind not in {"CREATE_TABLE", "CREATE_INDEX", "ALTER_TABLE"}:
            raise ProvisionError(
                f"仅支持 CREATE TABLE / CREATE INDEX / ALTER TABLE，"
                f"暂不支持第 {statement.index} 条语句（{statement.kind}）"
            )
        if not statement.supported:
            raise ProvisionError(f"第 {statement.index} 条语句不支持：{statement.reason}")
        applied = ddl_merge.apply(tables, statement.expression)
        if not applied["applied"]:
            raise ProvisionError(f"第 {statement.index} 条语句无法应用：{applied['error']}")
    if not tables:
        raise ProvisionError("没有解析到任何表结构")
    # 手写 DDL 里未限定 schema 的外键，按子表所在 schema 归一，否则拓扑排序匹配不到父表
    for table in tables:
        for foreign_key in table.get("foreign_keys") or []:
            if not foreign_key.get("ref_schema"):
                foreign_key["ref_schema"] = table.get("schema") or "public"
    return tables, warnings


def _build_export(
    db_type: int,
    datasource_id,
    datasource_name: str,
    snapshot_time: str,
    all_tables: list,
    selection: list,
    seed,
    include_ddl: bool,
    actor: str,
) -> dict:
    config = _config()
    _validate_limits(selection, config)

    index = {(table.get("schema"), table.get("name")): table for table in all_tables}
    selected = []
    rows_map = {}
    for item in selection:
        table = _resolve_table(index, item)
        selected.append(table)
        rows_map[_key(table)] = item["rows"]

    warnings = []
    ordered = _ordered_tables(selected, warnings)
    selected_keys = {_key(table) for table in ordered}
    for table in ordered:
        for foreign_key in table.get("foreign_keys") or []:
            ref = (foreign_key.get("ref_schema"), foreign_key.get("ref_table"))
            if ref not in selected_keys:
                warnings.append(
                    f"表 {table.get('name')} 的外键 {foreign_key.get('name')} 引用了未选中的表 "
                    f"{foreign_key.get('ref_table')}，生成的外键值可能无法通过目标库校验"
                )

    if seed is None:
        seed = secrets.randbelow(2**31)

    ddl_statements = []
    if include_ddl:
        plan = ddl.build_plan(db_type, "export", ordered)
        ddl_statements = plan["statements"]
        warnings.extend(plan["warnings"])

    rng = random.Random(seed)
    generated = {}
    insert_statements = []
    total_rows = 0
    for table in ordered:
        key = _key(table)
        rows = data_gen.generate_rows(table, rows_map[key], generated, selected_keys, rng)
        insert_statements.extend(_insert_statements(db_type, table, rows))
        generated[key] = rows
        total_rows += len(rows)

    db_type_label = custom_enum.DbTypeEnum(db_type).label
    lines = [
        "-- 由 my-dba 生成的测试数据文件",
        (
            f"-- 数据源：{datasource_name}（{db_type_label}）"
            if datasource_id is not None
            else f"-- 表结构来源：手动输入（{db_type_label}）"
        ),
    ]
    if snapshot_time:
        lines.append(f"-- 快照采集时间：{snapshot_time}")
    lines.extend(
        [
            f"-- 表数量：{len(ordered)}，总行数：{total_rows}",
            f"-- 生成时间：{datetime.now():%Y-%m-%d %H:%M:%S}",
            "-- 系统不会执行本文件；请确认目标环境后再手动导入。",
        ]
    )
    if db_type == MYSQL:
        lines.append("-- MySQL 导入前请先 USE 目标数据库。")
    for warning in warnings:
        lines.append(f"-- 警告：{warning}")
    lines.append("")
    if ddl_statements:
        lines.append("-- ===== DDL =====")
        lines.extend(f"{statement};" for statement in ddl_statements)
        lines.append("")
    if insert_statements:
        lines.append("-- ===== 数据 =====")
        lines.extend(insert_statements)

    source = datasource_id if datasource_id is not None else "manual"
    filename = f"test_data_{source}_{datetime.now():%Y%m%d%H%M%S}.sql"
    LOGGER.info(
        "导出测试数据文件 source=%s tables=%s rows=%s actor=%s",
        source,
        len(ordered),
        total_rows,
        actor,
    )
    return {
        "filename": filename,
        "content": "\n".join(lines) + "\n",
        "datasource_id": datasource_id,
        "datasource_name": datasource_name,
        "db_type": db_type,
        "db_type_label": db_type_label,
        "snapshot_collect_time": snapshot_time,
        "tables": [
            {"schema": item.get("schema"), "name": item.get("name"), "rows": rows_map[_key(item)]} for item in ordered
        ],
        "total_rows": total_rows,
        "warnings": warnings,
    }


def _insert_statements(db_type: int, table: dict, rows: list) -> list:
    columns = [column.get("name") for column in table.get("columns") or []]
    if not columns or not rows:
        return []
    target = ddl.qualified_table(db_type, table)
    quoted_columns = ", ".join(ddl.quote_ident(db_type, column) for column in columns)
    statements = []
    for start in range(0, len(rows), 100):
        batch = rows[start : start + 100]
        values = ",\n".join(
            "(" + ", ".join(_sql_literal(row.get(column), db_type) for column in columns) + ")" for row in batch
        )
        statements.append(f"INSERT INTO {target} ({quoted_columns}) VALUES\n{values};")
    return statements


def _sql_literal(value, db_type: int) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        if db_type == POSTGRESQL:
            return "TRUE" if value else "FALSE"
        return "1" if value else "0"
    if isinstance(value, int | float | Decimal):
        return str(value)
    if isinstance(value, datetime):
        return f"'{value.isoformat(sep=' ')}'"
    if isinstance(value, date | time):
        return f"'{value.isoformat()}'"
    if isinstance(value, bytes):
        encoded = value.hex()
        return f"E'\\\\x{encoded}'::bytea" if db_type == POSTGRESQL else f"0x{encoded}"
    text = str(value).replace("'", "''")
    if db_type == MYSQL:
        text = text.replace("\\", "\\\\")
    return f"'{text}'"


# ----------------------------------------------------------------------
# 已创建测试库的后续维护
# ----------------------------------------------------------------------


def append_rows(
    record,
    schema: str,
    name: str,
    rows: int,
    seed=None,
    actor: str = "",
    is_admin: bool = True,
):
    """
    向本功能创建的表追加数据；父表外键从测试库现有数据中取
    """
    target = _writable_record_target(record, actor, is_admin)
    config = _config()
    if rows > config["max_rows_per_table"]:
        raise ProvisionError(f"单表行数不能超过 {config['max_rows_per_table']}")
    if (record.row_count or 0) + rows > config["max_total_rows"]:
        raise ProvisionError(f"总行数不能超过 {config['max_total_rows']}")
    if _find_record_table(record, schema, name) is None:
        raise ProvisionError("该表不是本功能创建的，不能追加数据")
    table = _snapshot_table_for(record.datasource_id, schema, name)
    warnings = []
    conn = _connect_target(target, record.db_name)
    try:
        _set_manual_transaction(conn, target["db_type"])
        with conn.cursor() as cursor:
            if not _table_exists(cursor, target["db_type"], schema, name):
                raise ProvisionError("该表在测试库中已不存在")
            external = _external_parents(cursor, target["db_type"], table, _record_keys(record), warnings)
            rng = random.Random(seed if seed is not None else secrets.randbelow(2**31))
            # 追加时用较大的起始编号，避开创建时的 1..N，降低唯一键冲突概率
            start_index = 100000 + secrets.randbelow(800000)
            generated = data_gen.generate_rows(
                table, rows, external, _record_keys(record), rng, start_index=start_index
            )
            _insert_rows(cursor, target["db_type"], table, generated)
        conn.commit()
        try:
            _refresh_record_counts(conn, target["db_type"], record)
        except Exception as exc:  # noqa: BLE001 数据已写入，行数统计失败不应报成写入失败
            warnings.append(f"数据已写入，但行数刷新失败：{_error_text(exc, _safe_secret(target))}")
        LOGGER.info(
            "测试库追加数据 db_name=%s table=%s.%s rows=%s actor=%s",
            record.db_name,
            schema,
            name,
            rows,
            actor,
        )
        return record, warnings
    except Exception as exc:  # noqa: BLE001 追加失败必须回滚并给出可读原因
        conn.rollback()
        raise ProvisionError(_error_text(exc, _safe_secret(target))) from exc
    finally:
        conn.close()


def clear_rows(record, schema: str, name: str, actor: str = "", is_admin: bool = True):
    """
    清空本功能创建的表的数据（不做 CASCADE；被外键引用时由目标库拒绝）
    """
    target = _writable_record_target(record, actor, is_admin)
    if _find_record_table(record, schema, name) is None:
        raise ProvisionError("该表不是本功能创建的，不能清空数据")
    conn = _connect_target(target, record.db_name)
    try:
        _set_manual_transaction(conn, target["db_type"])
        with conn.cursor() as cursor:
            if not _table_exists(cursor, target["db_type"], schema, name):
                raise ProvisionError("该表在测试库中已不存在")
            cursor.execute(f"DELETE FROM {ddl.qualified_table(target['db_type'], {'schema': schema, 'name': name})}")
        conn.commit()
        try:
            _refresh_record_counts(conn, target["db_type"], record)
        except Exception as exc:  # noqa: BLE001 数据已清空，行数统计失败不应报成操作失败
            LOGGER.warning("清空后刷新行数失败 db_name=%s err=%s", record.db_name, _error_text(exc))
        LOGGER.info("测试库清空数据 db_name=%s table=%s.%s actor=%s", record.db_name, schema, name, actor)
    except Exception as exc:  # noqa: BLE001
        conn.rollback()
        raise ProvisionError(_error_text(exc, _safe_secret(target))) from exc
    finally:
        conn.close()


def add_table(
    record,
    schema: str,
    name: str,
    rows: int,
    seed=None,
    actor: str = "",
    is_admin: bool = True,
):
    """
    从最新快照向已创建的测试库新增一张表并造数
    """
    target = _writable_record_target(record, actor, is_admin)
    config = _config()
    if rows > config["max_rows_per_table"]:
        raise ProvisionError(f"单表行数不能超过 {config['max_rows_per_table']}")
    if len(record.tables or []) >= config["max_tables"]:
        raise ProvisionError(f"最多只能有 {config['max_tables']} 张表")
    if (record.row_count or 0) + rows > config["max_total_rows"]:
        raise ProvisionError(f"总行数不能超过 {config['max_total_rows']}")
    if _find_record_table(record, schema, name) is not None:
        raise ProvisionError("该表已经在测试库记录中")
    table = _snapshot_table_for(record.datasource_id, schema, name)
    existing_tables = _snapshot_tables_for_record(record)
    statements, warnings = ddl.build_single_table(target["db_type"], table, existing_tables)

    conn = _connect_target(target, record.db_name)
    ddl_done = False
    try:
        _set_manual_transaction(conn, target["db_type"])
        with conn.cursor() as cursor:
            if _table_exists(cursor, target["db_type"], schema, name):
                raise ProvisionError("测试库中已存在同名表")
            for statement in statements:
                cursor.execute(statement)
                # MySQL 的 DDL 逐条提交：CREATE TABLE 成功后即使后续索引/外键失败也要能清理
                if statement.lstrip().upper().startswith("CREATE TABLE"):
                    ddl_done = True
            external = _external_parents(cursor, target["db_type"], table, _record_keys(record), warnings)
            rng = random.Random(seed if seed is not None else secrets.randbelow(2**31))
            generated = data_gen.generate_rows(table, rows, external, _record_keys(record) | {(schema, name)}, rng)
            _insert_rows(cursor, target["db_type"], table, generated)
        conn.commit()
    except Exception as exc:  # noqa: BLE001
        conn.rollback()
        # MySQL 的 DDL 不可回滚：表可能已经建出来了，尽力清掉
        if ddl_done and target["db_type"] == MYSQL:
            try:
                with conn.cursor() as cursor:
                    cursor.execute(
                        f"DROP TABLE IF EXISTS {ddl.qualified_table(MYSQL, {'schema': schema, 'name': name})}"
                    )
                conn.commit()
            except Exception as drop_exc:  # noqa: BLE001 清理失败不能盖住原始错误
                LOGGER.error("新增表清理失败 db_name=%s table=%s.%s err=%s", record.db_name, schema, name, drop_exc)
        raise ProvisionError(_error_text(exc, _safe_secret(target))) from exc
    finally:
        conn.close()

    record.tables = list(record.tables or []) + [{"schema": schema, "name": name, "rows": rows}]
    record.save(update_fields=["tables", "update_time"])
    try:
        refresh_conn = _connect_target(target, record.db_name)
        try:
            _refresh_record_counts(refresh_conn, target["db_type"], record)
        finally:
            refresh_conn.close()
    except Exception as exc:  # noqa: BLE001 表已建好，行数统计失败不应报成操作失败
        warnings.append(f"表已新增，但行数刷新失败：{_error_text(exc, _safe_secret(target))}")
    LOGGER.info("测试库新增表 db_name=%s table=%s.%s rows=%s actor=%s", record.db_name, schema, name, rows, actor)
    return record, warnings


def drop_table(record, schema: str, name: str, actor: str = "", is_admin: bool = True):
    """
    删除本功能创建的表（不做 CASCADE；被其他表外键引用时由目标库拒绝）
    """
    target = _writable_record_target(record, actor, is_admin)
    if _find_record_table(record, schema, name) is None:
        raise ProvisionError("该表不是本功能创建的，不能删除")
    conn = _connect_target(target, record.db_name)
    try:
        _set_manual_transaction(conn, target["db_type"])
        with conn.cursor() as cursor:
            if not _table_exists(cursor, target["db_type"], schema, name):
                raise ProvisionError("该表在测试库中已不存在")
            cursor.execute(f"DROP TABLE {ddl.qualified_table(target['db_type'], {'schema': schema, 'name': name})}")
        conn.commit()
    except Exception as exc:  # noqa: BLE001
        conn.rollback()
        raise ProvisionError(_error_text(exc, _safe_secret(target))) from exc
    finally:
        conn.close()

    record.tables = [
        item
        for item in record.tables or []
        if not (item.get("name") == name and (not schema or item.get("schema") == schema))
    ]
    record.save(update_fields=["tables", "update_time"])
    try:
        refresh_conn = _connect_target(target, record.db_name)
        try:
            _refresh_record_counts(refresh_conn, target["db_type"], record)
        finally:
            refresh_conn.close()
    except Exception as exc:  # noqa: BLE001 表已删除，行数统计失败不应报成操作失败
        LOGGER.warning("删表后刷新行数失败 db_name=%s err=%s", record.db_name, _error_text(exc))
    LOGGER.info("测试库删除表 db_name=%s table=%s.%s actor=%s", record.db_name, schema, name, actor)


def _writable_record_target(record, actor: str, is_admin: bool) -> dict:
    if not is_admin and record.creator != actor:
        raise ProvisionPermissionError("只能操作自己创建的测试库")
    if record.status != custom_enum.ProvisionStatusEnum.READY.value:
        raise ProvisionError("只有可用的测试库才能继续操作")
    target = _target_or_error(record.datasource_id, actor, is_admin)
    if not _database_exists(target, record.db_name):
        record.status = custom_enum.ProvisionStatusEnum.FAILED.value
        record.error = "测试库已不存在"
        record.save(update_fields=["status", "error", "update_time"])
        raise ProvisionError("测试库已不存在，无法继续操作")
    return target


def _find_record_table(record, schema: str, name: str):
    for item in record.tables or []:
        if item.get("name") == name and (not schema or item.get("schema") == schema):
            return item
    return None


def _record_keys(record) -> set:
    return {(item.get("schema"), item.get("name")) for item in record.tables or []}


def _snapshot_table_for(datasource_id: int, schema: str, name: str) -> dict:
    snapshot = datasource_services.latest_snapshot_data(datasource_id)
    if snapshot is None:
        raise ProvisionError("该数据源尚未采集，无法读取表结构")
    for table in snapshot.get("tables") or []:
        if table.get("name") == name and (not schema or table.get("schema") == schema):
            return table
    raise ProvisionError(f"源快照中找不到表 {name}，请重新采集")


def _snapshot_tables_for_record(record) -> list:
    snapshot = datasource_services.latest_snapshot_data(record.datasource_id)
    if snapshot is None:
        return []
    index = {(table.get("schema"), table.get("name")): table for table in snapshot.get("tables") or []}
    return [index[key] for key in _record_keys(record) if key in index]


def _set_manual_transaction(conn, db_type: int) -> None:
    if db_type == POSTGRESQL:
        conn.autocommit = False
    else:
        conn.autocommit(False)


def _table_exists(cursor, db_type: int, schema: str, name: str) -> bool:
    if db_type == POSTGRESQL:
        cursor.execute(
            "SELECT 1 FROM information_schema.tables WHERE table_schema = %s AND table_name = %s",
            [schema or "public", name],
        )
    else:
        cursor.execute(
            "SELECT 1 FROM information_schema.tables WHERE table_schema = DATABASE() AND table_name = %s",
            [name],
        )
    return cursor.fetchone() is not None


def _external_parents(cursor, db_type: int, table: dict, db_keys: set, warnings: list) -> dict:
    """
    从测试库现有父表读取外键可引用的值；读不到时明确报错，不生成无效外键
    """
    needed = {}
    self_key = _key(table)
    for foreign_key in table.get("foreign_keys") or []:
        ref = (foreign_key.get("ref_schema"), foreign_key.get("ref_table"))
        if ref not in db_keys or ref == self_key:
            continue
        columns = foreign_key.get("ref_columns") or []
        if columns:
            needed.setdefault(ref, set()).update(columns)

    result = {}
    for ref, columns in needed.items():
        parent = {"schema": ref[0], "name": ref[1]}
        ordered = sorted(columns)
        quoted = ", ".join(ddl.quote_ident(db_type, column) for column in ordered)
        try:
            cursor.execute(f"SELECT DISTINCT {quoted} FROM {ddl.qualified_table(db_type, parent)} LIMIT 1000")
            rows = [dict(zip(ordered, row, strict=True)) for row in cursor.fetchall()]
        except Exception as exc:  # noqa: BLE001
            raise ProvisionError(f"读取父表 {ref[1]} 失败：{_error_text(exc)}") from exc
        if not rows:
            raise ProvisionError(f"父表 {ref[1]} 没有数据，无法生成外键引用")
        result[ref] = rows
    return result


def _refresh_record_counts(conn, db_type: int, record) -> None:
    tables = []
    total = 0
    with conn.cursor() as cursor:
        for item in record.tables or []:
            table = {"schema": item.get("schema"), "name": item.get("name")}
            try:
                cursor.execute(f"SELECT COUNT(*) FROM {ddl.qualified_table(db_type, table)}")
                count = int(cursor.fetchone()[0])
            except Exception:  # noqa: BLE001 单表统计失败不影响其他表
                count = 0
            tables.append({**item, "rows": count})
            total += count
    record.tables = tables
    record.row_count = total
    record.save(update_fields=["tables", "row_count", "update_time"])


# ----------------------------------------------------------------------
# 准备与校验
# ----------------------------------------------------------------------


def _prepare(datasource_id: int, tables: list, seed, config: dict, actor: str, is_admin: bool):
    target = _target_or_error(datasource_id, actor, is_admin)
    _validate_limits(tables, config)
    snapshot = datasource_services.latest_snapshot_data(datasource_id)
    if snapshot is None:
        raise ProvisionError("该数据源尚未采集，无法预置")

    index = {(table.get("schema"), table.get("name")): table for table in snapshot.get("tables") or []}
    selected = []
    rows_map = {}
    for item in tables:
        table = _resolve_table(index, item)
        key = (table.get("schema"), table.get("name"))
        selected.append(table)
        rows_map[key] = item["rows"]

    warnings = []
    ordered = _ordered_tables(selected, warnings)
    if seed is None:
        seed = secrets.randbelow(2**31)
    return target, snapshot, ordered, rows_map, seed, warnings


def _target_or_error(datasource_id: int, actor: str, is_admin: bool) -> dict:
    target = datasource_services.provisioning_target(datasource_id)
    if target is None:
        raise ProvisionError("数据源不存在")
    if not is_admin and target.get("creator") != actor:
        raise ProvisionPermissionError("只能使用自己创建的数据源")
    if target["env_type"] != TEST_ENV:
        raise ProvisionError("只有测试环境的数据源可以预置测试库")
    if not target["is_enabled"]:
        raise ProvisionError("数据源已停用，无法预置")
    if not target["has_write_credential"]:
        raise ProvisionError("请先配置预置写账号")
    return target


def _validate_limits(tables: list, config: dict) -> None:
    if len(tables) > config["max_tables"]:
        raise ProvisionError(f"最多只能选择 {config['max_tables']} 张表")
    seen = set()
    total = 0
    for item in tables:
        key = (item.get("schema") or "", item["name"])
        if key in seen:
            raise ProvisionError(f"表 {item['name']} 被重复选择")
        seen.add(key)
        if item["rows"] > config["max_rows_per_table"]:
            raise ProvisionError(f"单表行数不能超过 {config['max_rows_per_table']}")
        total += item["rows"]
    if total > config["max_total_rows"]:
        raise ProvisionError(f"总行数不能超过 {config['max_total_rows']}")


def _resolve_table(index: dict, item: dict) -> dict:
    if item.get("schema"):
        table = index.get((item["schema"], item["name"]))
    else:
        matches = [table for (_, name), table in index.items() if name == item["name"]]
        if len(matches) > 1:
            raise ProvisionError(f"表 {item['name']} 存在于多个 schema，请指定 schema")
        table = matches[0] if matches else None
    if table is None:
        raise ProvisionError(f"快照中找不到表 {item['name']}，请重新采集")
    return table


def _ordered_tables(selected: list, warnings: list) -> list:
    """
    按外键依赖做拓扑排序；循环外键拒绝，可空自引用以 NULL 生成
    """
    selected_keys = [_key(table) for table in selected]
    selected_set = set(selected_keys)
    edges = {key: set() for key in selected_keys}
    indegree = {key: 0 for key in selected_keys}

    for table in selected:
        key = _key(table)
        nullable = {column.get("name"): column.get("nullable", True) for column in table.get("columns") or []}
        for foreign_key in table.get("foreign_keys") or []:
            ref = (foreign_key.get("ref_schema"), foreign_key.get("ref_table"))
            if ref not in selected_set:
                # DDL 生成阶段会统一给出「外键被跳过」的警告，这里不重复
                continue
            if ref == key:
                columns = foreign_key.get("columns") or []
                if any(not nullable.get(column, True) for column in columns):
                    raise ProvisionError(f"表 {table.get('name')} 的自引用外键列不可为空，无法生成数据")
                warnings.append(f"表 {table.get('name')} 的自引用外键将以 NULL 生成")
                continue
            if key not in edges[ref]:
                edges[ref].add(key)
                indegree[key] += 1

    order = sorted(key for key in selected_keys if indegree[key] == 0)
    result = []
    while order:
        key = order.pop(0)
        result.append(key)
        for child in sorted(edges[key]):
            indegree[child] -= 1
            if indegree[child] == 0:
                order.append(child)
        order.sort()

    if len(result) != len(selected_keys):
        raise ProvisionError("选中的表之间存在循环外键，无法确定插入顺序")
    by_key = {_key(table): table for table in selected}
    return [by_key[key] for key in result]


def _plan_payload(target: dict, snapshot: dict, ordered: list, rows_map: dict, db_name: str, seed: int, warnings: list):
    config = _config()
    warnings = list(warnings)
    age_warning = _snapshot_age_warning(snapshot, config)
    if age_warning:
        warnings.append(age_warning)
    unavailable = snapshot.get("unavailable") or []
    if unavailable:
        warnings.append(f"快照存在未采集项（{'、'.join(str(item) for item in unavailable)}），结构可能不完整")

    try:
        plan = ddl.build_plan(target["db_type"], db_name, ordered)
    except ddl.DdlError as exc:
        raise ProvisionError(str(exc)) from exc
    warnings.extend(plan["warnings"])

    table_payload = []
    total_rows = 0
    for table in ordered:
        key = _key(table)
        rows = rows_map[key]
        total_rows += rows
        table_payload.append(
            {
                "schema": table.get("schema"),
                "name": table.get("name"),
                "rows": rows,
                "semantic_columns": data_gen.describe_semantics(table),
            }
        )

    return {
        "datasource_id": target["id"],
        "datasource_name": target["name"],
        "db_type": target["db_type"],
        "db_type_label": custom_enum.DbTypeEnum(target["db_type"]).label,
        "db_name": db_name,
        "seed": seed,
        "snapshot_collect_time": snapshot.get("collect_time", ""),
        "tables": table_payload,
        "total_rows": total_rows,
        "create_database_sql": plan["create_database_sql"],
        "statements": [plan["create_database_sql"]] + plan["statements"],
        "warnings": warnings,
    }


def _snapshot_age_warning(snapshot: dict, config: dict) -> str:
    raw = snapshot.get("collect_time")
    if not raw:
        return ""
    try:
        collected = datetime.strptime(raw, "%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError):
        return ""
    days = config["snapshot_max_age_days"]
    if datetime.now() - collected > timedelta(days=days):
        return f"快照采集于 {raw}，已超过 {days} 天，结构可能已变化"
    return ""


def _generate_db_name(prefix: str, datasource_id: int) -> str:
    return f"{prefix}_{datasource_id}_{datetime.now():%Y%m%d%H%M%S}_{secrets.token_hex(2)}"


def _validate_db_name(db_name: str, prefix: str, datasource_id: int, db_type: int) -> None:
    pattern = rf"^{re.escape(prefix)}_{datasource_id}_\d{{14}}_[0-9a-f]{{4}}$"
    if not re.fullmatch(pattern, db_name):
        raise ProvisionError("库名不合法，请重新预览")
    limit = 63 if db_type == POSTGRESQL else 64
    if len(db_name) > limit:
        raise ProvisionError("库名过长")


# ----------------------------------------------------------------------
# 连接与执行
# ----------------------------------------------------------------------


def _write_password(target: dict) -> str:
    try:
        return crypto.decrypt(target["write_password"])
    except crypto.CryptoKeyError as exc:
        raise ProvisionError(str(exc)) from exc


def _safe_secret(target: dict) -> str:
    """
    错误脱敏用；解密失败时返回空串，绝不把异常带进异常处理里
    """
    try:
        return crypto.decrypt(target["write_password"])
    except Exception:  # noqa: BLE001
        return ""


def _connect_admin(target: dict):
    password = _write_password(target)
    if target["db_type"] == POSTGRESQL:
        return psycopg2.connect(
            host=target["host"],
            port=target["port"],
            dbname=target["db_name"],
            user=target["write_username"],
            password=password,
            connect_timeout=10,
        )
    return pymysql.connect(
        host=target["host"],
        port=target["port"],
        user=target["write_username"],
        password=password,
        connect_timeout=10,
        read_timeout=30,
        write_timeout=30,
        charset="utf8mb4",
    )


def _connect_target(target: dict, db_name: str):
    password = _write_password(target)
    if target["db_type"] == POSTGRESQL:
        return psycopg2.connect(
            host=target["host"],
            port=target["port"],
            dbname=db_name,
            user=target["write_username"],
            password=password,
            connect_timeout=10,
        )
    return pymysql.connect(
        host=target["host"],
        port=target["port"],
        user=target["write_username"],
        password=password,
        database=db_name,
        connect_timeout=10,
        read_timeout=120,
        write_timeout=120,
        charset="utf8mb4",
    )


def _database_exists(target: dict, db_name: str) -> bool:
    conn = _connect_admin(target)
    try:
        with conn.cursor() as cursor:
            if target["db_type"] == POSTGRESQL:
                cursor.execute("SELECT 1 FROM pg_database WHERE datname = %s", [db_name])
            else:
                cursor.execute("SELECT SCHEMA_NAME FROM information_schema.SCHEMATA WHERE SCHEMA_NAME = %s", [db_name])
            return cursor.fetchone() is not None
    finally:
        conn.close()


def _create_database(target: dict, db_name: str) -> None:
    conn = _connect_admin(target)
    try:
        if target["db_type"] == POSTGRESQL:
            conn.autocommit = True
            with conn.cursor() as cursor:
                cursor.execute(pg_sql.SQL("CREATE DATABASE {}").format(pg_sql.Identifier(db_name)))
        else:
            conn.autocommit(True)
            with conn.cursor() as cursor:
                cursor.execute(ddl.create_database_sql(MYSQL, db_name))
    finally:
        conn.close()


def _drop_database(target: dict, db_name: str) -> None:
    conn = _connect_admin(target)
    try:
        if target["db_type"] == POSTGRESQL:
            conn.autocommit = True
            with conn.cursor() as cursor:
                try:
                    cursor.execute(
                        "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                        "WHERE datname = %s AND pid <> pg_backend_pid()",
                        [db_name],
                    )
                except Exception as exc:  # noqa: BLE001 无权限时仍然尝试删除
                    LOGGER.warning("终止测试库连接失败 db_name=%s err=%s", db_name, _error_text(exc))
                try:
                    cursor.execute(pg_sql.SQL("DROP DATABASE {}").format(pg_sql.Identifier(db_name)))
                except psycopg2.Error as exc:
                    # 3D000 = invalid_catalog_name：库已经不存在，视为已删除
                    if getattr(exc, "pgcode", None) == "3D000":
                        return
                    raise
        else:
            conn.autocommit(True)
            with conn.cursor() as cursor:
                cursor.execute(f"DROP DATABASE IF EXISTS {ddl.quote_ident(MYSQL, db_name)}")
    finally:
        conn.close()


def _create_tables_and_data(
    target: dict, db_name: str, statements: list, ordered: list, rows_map: dict, seed: int, config: dict
) -> int:
    db_type = target["db_type"]
    rng = random.Random(seed)
    generated = {}
    selected_keys = {_key(table) for table in ordered}
    conn = _connect_target(target, db_name)
    try:
        if db_type == POSTGRESQL:
            conn.autocommit = False
            with conn.cursor() as cursor:
                cursor.execute("SET statement_timeout = %s", [config["timeout_seconds"] * 1000])
                for statement in statements:
                    cursor.execute(statement)
                total = _generate_and_insert(cursor, db_type, ordered, rows_map, generated, selected_keys, rng)
            conn.commit()
            return total
        conn.autocommit(False)
        with conn.cursor() as cursor:
            for statement in statements:
                cursor.execute(statement)
            total = _generate_and_insert(cursor, db_type, ordered, rows_map, generated, selected_keys, rng)
        conn.commit()
        return total
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _generate_and_insert(cursor, db_type, ordered, rows_map, generated, selected_keys, rng) -> int:
    total = 0
    for table in ordered:
        key = _key(table)
        rows = data_gen.generate_rows(table, rows_map[key], generated, selected_keys, rng)
        _insert_rows(cursor, db_type, table, rows)
        generated[key] = rows
        total += len(rows)
    return total


def _insert_rows(cursor, db_type: int, table: dict, rows: list) -> None:
    if not rows:
        return
    columns = [column.get("name") for column in table.get("columns") or []]
    placeholders = ", ".join(["%s"] * len(columns))
    quoted_columns = ", ".join(ddl.quote_ident(db_type, column) for column in columns)
    sql = f"INSERT INTO {ddl.qualified_table(db_type, table)} ({quoted_columns}) VALUES ({placeholders})"
    values = [tuple(row.get(column) for column in columns) for row in rows]
    cursor.executemany(sql, values)


def _key(table: dict) -> tuple:
    return (table.get("schema"), table.get("name"))


def _error_text(exc: Exception, secret: str = "") -> str:
    text = str(exc)
    if secret:
        text = text.replace(secret, "***")
    return text[:1000]

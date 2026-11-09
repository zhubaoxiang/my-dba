"""
大数据预置任务执行

worker 从 provisioned_database 领取 PENDING 任务，按固定快照重建结构，
流式生成数据后用 COPY / LOAD DATA 写入，索引与外键在数据完成后创建。
"""

import contextlib
import logging
import random
from datetime import datetime, timedelta

from django.db import transaction

from apps.datasource import services as datasource_services
from apps.provisioning import bulk, ddl, models, services
from apps.provisioning import data as data_gen
from utils import custom_enum
from utils.logger import get_logger

LOGGER = get_logger("provisioning_worker.log", logging.INFO)

PENDING = custom_enum.ProvisionStatusEnum.PENDING.value
RUNNING = custom_enum.ProvisionStatusEnum.RUNNING.value
READY = custom_enum.ProvisionStatusEnum.READY.value
FAILED = custom_enum.ProvisionStatusEnum.FAILED.value
CANCELLED = custom_enum.ProvisionStatusEnum.CANCELLED.value


class WorkerStopped(Exception):
    """
    worker 收到停止信号，或任务记录已被移出 RUNNING
    """


def claim_next(worker_id: str):
    """
    原子领取最早的一条排队任务
    """
    with transaction.atomic():
        record = (
            models.ProvisionedDatabase.objects.select_for_update(skip_locked=True)
            .filter(is_deleted=False, status=PENDING)
            .order_by("id")
            .first()
        )
        if record is None:
            return None
        now = datetime.now()
        record.status = RUNNING
        record.worker_id = worker_id
        record.start_time = now
        record.heartbeat_time = now
        record.save(update_fields=["status", "worker_id", "start_time", "heartbeat_time", "update_time"])
        return record


def recover_stale(stale_seconds: int) -> int:
    """
    把心跳超时的 RUNNING 任务判为中断，并按 database_created 清理半成品库
    """
    cutoff = datetime.now() - timedelta(seconds=stale_seconds)
    stale = list(models.ProvisionedDatabase.objects.filter(is_deleted=False, status=RUNNING, heartbeat_time__lt=cutoff))
    for record in stale:
        if record.cancel_requested:
            _cleanup(record, CANCELLED, "已取消")
        else:
            _cleanup(record, FAILED, "执行进程心跳超时")
        LOGGER.warning("回收中断任务 id=%s db=%s", record.id, record.db_name)
    return len(stale)


def run(record, stop_event=None) -> None:
    """
    执行一条 RUNNING 任务；异常不会向 worker 主循环抛出，状态与清理都在这里落地
    """
    config = services._config()
    target = None
    conn = None
    created = False
    on_progress = _progress_callback(record.id, config)
    cancel_check = _cancel_checker(record.id, config, stop_event)
    try:
        target, ordered, rows_map, plan = _load_context(record, config)
        if services._database_exists(target, record.db_name):
            raise services.ProvisionError("目标服务器上已存在同名数据库")
        services._create_database(target, record.db_name)
        created = True
        _update(record.id, database_created=True)
        conn = services._connect_target(target, record.db_name, bulk=True, timeout=config["bulk_timeout_seconds"])
        services._set_manual_transaction(conn, target["db_type"])
        _execute_statements(conn, target, plan["statements"], config["bulk_timeout_seconds"])
        total = _execute_data(conn, target, record, ordered, rows_map, config, on_progress, cancel_check)
        if _cancel_requested(record.id):
            raise bulk.BulkCancelled()
        if not _update(record.id, current_table="建立索引与外键", heartbeat_time=datetime.now()):
            raise WorkerStopped()
        _execute_statements(conn, target, plan["post_statements"], 0)
        conn.commit()
        if _cancel_requested(record.id):
            raise bulk.BulkCancelled()
        _finalize(record.id, total)
        LOGGER.info("大数据任务完成 id=%s db=%s rows=%s", record.id, record.db_name, total)
    except bulk.BulkCancelled:
        _rollback(conn)
        _cleanup(record, CANCELLED, "已取消", database_created=created)
    except WorkerStopped:
        _rollback(conn)
        _cleanup(record, FAILED, "执行进程停止", database_created=created)
    except Exception as exc:  # noqa: BLE001 任务失败必须落状态并清理
        _rollback(conn)
        message = services._error_text(exc, services._safe_secret(target or {}))
        _cleanup(record, FAILED, message, database_created=created)
        LOGGER.error("大数据任务失败 id=%s db=%s err=%s", record.id, record.db_name, message)
    finally:
        if conn is not None:
            with contextlib.suppress(Exception):
                conn.close()


def _load_context(record, config: dict):
    target = services._target_or_error(record.datasource_id, record.creator, True)
    snapshot = datasource_services.snapshot_data_by_id(record.snapshot_id) if record.snapshot_id else None
    if snapshot is None:
        raise services.ProvisionError("固定快照不存在或已删除，请重新提交")
    if snapshot.get("datasource_id") != target["id"]:
        raise services.ProvisionError("快照不属于该数据源，请重新提交")
    index = {(table.get("schema"), table.get("name")): table for table in snapshot.get("tables") or []}
    selected = []
    rows_map = {}
    for item in record.tables or []:
        key = (item.get("schema"), item.get("name"))
        table = index.get(key)
        if table is None:
            raise services.ProvisionError(f"快照中找不到表 {item.get('name')}，请重新提交")
        selected.append(table)
        rows_map[key] = int(item.get("rows") or 0)
    warnings = []
    ordered = services._ordered_tables(selected, warnings)
    total = sum(rows_map.values())
    if total > config["bulk_max_total_rows"]:
        raise services.ProvisionError("任务行数超过当前配置的总量上限")
    for table in ordered:
        rows = rows_map[services._key(table)]
        if rows > config["bulk_max_rows_per_table"]:
            raise services.ProvisionError(f"单表行数超过当前配置上限：{table.get('name')}")
        for risk in data_gen.bulk_risks(table, rows):
            if risk["level"] == "error":
                raise services.ProvisionError(risk["message"])
    try:
        plan = ddl.build_plan(target["db_type"], record.db_name, ordered, defer_indexes=True)
    except ddl.DdlError as exc:
        raise services.ProvisionError(str(exc)) from exc
    return target, ordered, rows_map, plan


def _execute_statements(conn, target: dict, statements: list, statement_timeout: int) -> None:
    if not statements:
        return
    with conn.cursor() as cursor:
        if target["db_type"] == services.POSTGRESQL:
            cursor.execute("SET statement_timeout = %s", [statement_timeout * 1000])
        for statement in statements:
            cursor.execute(statement)


def _execute_data(conn, target, record, ordered, rows_map, config, on_progress, cancel_check) -> int:
    rng = random.Random(record.seed)
    selected_keys = {services._key(table) for table in ordered}
    targets = data_gen.pool_targets(ordered, selected_keys)
    pools = data_gen.build_pools(targets, config["bulk_pool_rows"])
    processed_total = 0
    for table in ordered:
        key = services._key(table)
        label = f"{table.get('schema')}.{table.get('name')}"
        on_progress(processed_total, label)
        row_iter = data_gen.iter_rows(table, rows_map[key], pools, selected_keys, rng)
        tracked = data_gen.track_pools(key, row_iter, targets, pools)
        written = bulk.load_table(
            conn,
            target["db_type"],
            table,
            tracked,
            batch_rows=config["bulk_batch_rows"],
            commit_rows=config["bulk_commit_rows"],
            on_progress=lambda count, table_label, base=processed_total: on_progress(base + count, table_label),
            cancel_check=cancel_check,
            tmp_dir=config["bulk_tmp_dir"],
        )
        processed_total += written
        on_progress(processed_total, label)
    return processed_total


def _progress_callback(record_id: int, config: dict):
    """
    进度与心跳写库；按 worker_heartbeat_seconds 节流，避免每批都写
    """
    state = {"time": None, "label": None}

    def callback(processed: int, label: str) -> None:
        now = datetime.now()
        if (
            state["time"] is not None
            and state["label"] == label
            and (now - state["time"]).total_seconds() < config["worker_heartbeat_seconds"]
        ):
            return
        state["time"] = now
        state["label"] = label
        if not _update(record_id, processed_rows=processed, current_table=label, heartbeat_time=now):
            raise WorkerStopped()

    return callback


def _cancel_checker(record_id: int, config: dict, stop_event):
    """
    批边界调用；取消标志按 heartbeat 间隔刷新，不逐行查库
    """
    state = {"time": None, "cancel": False}

    def check() -> bool:
        if stop_event is not None and stop_event.is_set():
            raise WorkerStopped()
        now = datetime.now()
        if state["time"] is None or (now - state["time"]).total_seconds() >= config["worker_heartbeat_seconds"]:
            state["time"] = now
            state["cancel"] = _cancel_requested(record_id)
        return state["cancel"]

    return check


def _cancel_requested(record_id: int) -> bool:
    return models.ProvisionedDatabase.objects.filter(id=record_id, cancel_requested=True).exists()


def _rollback(conn) -> None:
    if conn is None:
        return
    with contextlib.suppress(Exception):
        conn.rollback()


def _finalize(record_id: int, total: int) -> None:
    now = datetime.now()
    models.ProvisionedDatabase.objects.filter(id=record_id, status=RUNNING).update(
        status=READY,
        row_count=total,
        processed_rows=total,
        current_table="",
        error="",
        end_time=now,
        update_time=now,
    )


def _update(record_id: int, **fields) -> int:
    fields["update_time"] = datetime.now()
    return models.ProvisionedDatabase.objects.filter(id=record_id, status=RUNNING).update(**fields)


def _cleanup(record, status: int, message: str, database_created: bool | None = None) -> None:
    created = record.database_created if database_created is None else database_created
    cleanup_error = ""
    if created:
        target = datasource_services.provisioning_target(record.datasource_id)
        if target is None:
            cleanup_error = "；数据源不存在，无法清理目标库"
        else:
            try:
                config = services._config()
                services._validate_db_name(record.db_name, config["db_prefix"], record.datasource_id, target["db_type"])
                services._drop_database(target, record.db_name)
            except Exception as exc:  # noqa: BLE001 清理失败要保留原因供人工处理
                cleanup_error = f"；清理失败：{services._error_text(exc, services._safe_secret(target))}"
    now = datetime.now()
    models.ProvisionedDatabase.objects.filter(id=record.id).update(
        status=status,
        error=(message + cleanup_error)[:1024],
        end_time=now,
        update_time=now,
    )

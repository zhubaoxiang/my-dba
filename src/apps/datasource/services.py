"""
数据源采集服务

编排「校验数据源 → 建任务 → 采集 → 写快照 → 跑分析 → 更新任务状态」。
采集异步执行，触发接口立即返回任务标识。

注意（已知限制）：simple-background-task 用的是进程内内存队列，且其 worker 线程
不会自动启动，进程重启后队列中的任务会丢失。因此任务状态以数据库中的 CollectTask
为准，重启后可重新触发。多 worker 部署时任务在接收请求的那个 worker 内执行。
"""

import threading
from datetime import datetime

from simple_background_task import BackgroundTask
from simple_background_task.task import Task

from apps.datasource import analyzer, models
from apps.datasource.collectors import get_collector
from utils import common, custom_enum
from utils.configure import CONF_ATTR
from utils.logger import get_logger

LOGGER = get_logger("datasource.log")

_TRUE_VALUES = ("1", "true", "yes", "on")


class CollectRejected(Exception):
    """
    采集请求被拒绝（数据源停用、已有任务在执行等）
    """


def _int_config(key: str, default: int) -> int:
    try:
        return int(CONF_ATTR.get(key))
    except (TypeError, ValueError):
        return default


def load_collect_config() -> dict:
    """
    采集参数，全部来自 conf.ini
    """
    return {
        "connect_timeout": _int_config("datasource_connect_timeout", 5),
        "statement_timeout": _int_config("datasource_statement_timeout", 30),
        "exact_count": str(CONF_ATTR.get("datasource_exact_count", "false")).strip().lower() in _TRUE_VALUES,
    }


# ----------------------------------------------------------------------
# 后台 worker
#
# BackgroundTask 是单例，但每次构造都会重建内部队列，因此这里只构造一次并直接持有，
# 不再调用 defer()（它内部会重新构造，导致已入队任务丢失）。
# ----------------------------------------------------------------------

_worker = BackgroundTask()
_worker.daemon = True
_worker_lock = threading.Lock()
_worker_started = False


def _ensure_worker():
    global _worker_started
    if _worker_started:
        return
    with _worker_lock:
        if _worker_started:
            return
        _worker.start()
        _worker_started = True


def _submit(func, **kwargs):
    _ensure_worker()
    _worker.put(Task(func, **kwargs))


# ----------------------------------------------------------------------
# 对外入口
# ----------------------------------------------------------------------


def trigger_collect(datasource, creator: str = "") -> models.CollectTask:
    """
    触发一次采集，立即返回任务记录
    """
    if not datasource.is_enabled:
        raise CollectRejected("数据源已停用，无法采集")
    running = models.CollectTask.objects.filter(
        datasource_id=datasource.id,
        is_deleted=False,
        status__in=(custom_enum.CollectTaskStatusEnum.PENDING.value, custom_enum.CollectTaskStatusEnum.RUNNING.value),
    )
    if running.exists():
        raise CollectRejected("该数据源已有采集任务在执行中，请稍后再试")

    task = models.CollectTask.objects.create(
        datasource_id=datasource.id,
        status=custom_enum.CollectTaskStatusEnum.PENDING.value,
        creator=creator,
    )
    _submit(run_collect_task, task_id=task.id)
    return task


@common.clean_db_connections_decorator
def run_collect_task(task_id: int):
    """
    后台执行采集；任何失败都落到任务状态上，不向上抛
    """
    task = models.CollectTask.objects.filter(id=task_id, is_deleted=False).first()
    if task is None:
        LOGGER.warning("采集任务不存在 task_id=%s", task_id)
        return

    datasource = models.Datasource.objects.filter(id=task.datasource_id, is_deleted=False).first()
    if datasource is None:
        _finish(task, custom_enum.CollectTaskStatusEnum.FAILED, "数据源不存在或已删除")
        return

    task.status = custom_enum.CollectTaskStatusEnum.RUNNING.value
    task.start_time = datetime.now()
    task.save(update_fields=["status", "start_time", "update_time"])

    try:
        data = get_collector(datasource, load_collect_config()).collect()
        snapshot = _save_snapshot(datasource, data, task.creator)
        issues = analyzer.CatalogAnalyzer(data).analyze()
        _save_issues(datasource, snapshot, issues, task.creator)
        task.snapshot_id = snapshot.id
        _finish(task, custom_enum.CollectTaskStatusEnum.SUCCESS, "")
        LOGGER.info(
            "采集完成 datasource_id=%s snapshot_id=%s tables=%s issues=%s",
            datasource.id,
            snapshot.id,
            snapshot.table_count,
            len(issues),
        )
    except Exception as exc:  # noqa: BLE001 采集失败必须落到任务状态，不中断 worker
        LOGGER.error("采集失败 datasource_id=%s err=%s", datasource.id, exc)
        _finish(task, custom_enum.CollectTaskStatusEnum.FAILED, str(exc))


def _finish(task: models.CollectTask, status, fail_reason: str):
    task.status = status.value
    task.fail_reason = (fail_reason or "")[:1024]
    task.end_time = datetime.now()
    task.save(update_fields=["status", "fail_reason", "end_time", "snapshot_id", "update_time"])


def _save_snapshot(datasource, data: dict, creator: str) -> models.MetadataSnapshot:
    return models.MetadataSnapshot.objects.create(
        datasource_id=datasource.id,
        database_version=data.get("database_version", ""),
        schema_count=len(data.get("schemas") or []),
        table_count=len(data.get("tables") or []),
        collect_time=datetime.now(),
        raw_data=data,
        unavailable=data.get("unavailable") or [],
        creator=creator,
    )


def _save_issues(datasource, snapshot, issues: list, creator: str):
    if not issues:
        return
    models.CatalogIssue.objects.bulk_create(
        [
            models.CatalogIssue(
                datasource_id=datasource.id,
                snapshot_id=snapshot.id,
                issue_level=item["issue_level"],
                rule_code=item["rule_code"][:64],
                rule_name=item["rule_name"][:128],
                object_level=item["object_level"],
                target=item["target"][:512],
                schema_name=item["schema_name"][:128],
                table_name=item["table_name"][:128],
                column_name=item["column_name"][:128],
                description=item["description"][:1024],
                suggestion=item["suggestion"][:1024],
                creator=creator,
            )
            for item in issues
        ]
    )


def latest_snapshot(datasource_id: int):
    return (
        models.MetadataSnapshot.objects.filter(datasource_id=datasource_id, is_deleted=False)
        .order_by("-collect_time", "-id")
        .first()
    )

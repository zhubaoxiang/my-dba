"""
数据源采集服务

编排「校验数据源 → 建任务 → 采集 → 写快照 → 跑分析 → 更新任务状态」。
采集异步执行，触发接口立即返回任务标识。

注意（已知限制）：simple-background-task 用的是进程内内存队列，且其 worker 线程
不会自动启动，进程重启后队列中的任务会丢失。因此任务状态以数据库中的 CollectTask
为准，重启后可重新触发。多 worker 部署时任务在接收请求的那个 worker 内执行。
"""

from datetime import datetime, timedelta

import psycopg2
import pymysql
from django.db.models import Count

from apps.datasource import analyzer, models
from apps.datasource.collectors import get_collector
from apps.datasource.rules import registry as rule_registry
from utils import background, common, crypto, custom_enum
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
    background.submit(run_collect_task, task_id=task.id)
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
        # 先分析再落快照：快照要记下本次实际参与评估的规则，否则「规则集在采集之后被改过」无从察觉
        runner = analyzer.CatalogAnalyzer(data)
        issues = runner.analyze()
        snapshot = _save_snapshot(datasource, data, runner.evaluated_rules, task.creator)
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


def _save_snapshot(datasource, data: dict, evaluated_rules: list, creator: str) -> models.MetadataSnapshot:
    return models.MetadataSnapshot.objects.create(
        datasource_id=datasource.id,
        database_version=data.get("database_version", ""),
        schema_count=len(data.get("schemas") or []),
        table_count=len(data.get("tables") or []),
        collect_time=datetime.now(),
        raw_data=data,
        unavailable=data.get("unavailable") or [],
        evaluated_rules=list(evaluated_rules or []),
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


class DatasourceUnavailable(Exception):
    """
    数据源不存在或不可用
    """


class ReadonlyConnection:
    """
    目标库的**只读短连接**，用作上下文管理器

    只读是**服务端保证**（PostgreSQL 的 `set_session(readonly=True)`、MySQL 的
    `SET SESSION TRANSACTION READ ONLY`）：即便语句里带了写操作，目标库也会拒绝执行，
    而不是靠调用方自觉。

    PostgreSQL 连接是**非 autocommit** 的：服务端游标（`DECLARE`）必须在事务里才能用，
    而只有服务端游标才能在取到 N 行后停下——否则 `SELECT *` 会先把整表读进内存。
    调用方用完请 `rollback()`（只读会话，回滚无副作用），`close()` 时会自动兜底。
    """

    def __init__(self, connection, db_type: int):
        self.connection = connection
        self.db_type = db_type

    def cursor(self, *args, **kwargs):
        return self.connection.cursor(*args, **kwargs)

    def probe_cursor(self, name: str = None):
        """
        流式游标：逐批取数，**不会把整个结果集读进内存**

        这是试运行能安全跑 `SELECT *` 的前提——普通游标会在 execute 时把全部行拉回来。
        方言差异留在这里，调用方只管用。
        """
        if self.db_type == custom_enum.DbTypeEnum.POSTGRESQL.value:
            return self.connection.cursor(name=name or "my_dba_probe")
        return self.connection.cursor(pymysql.cursors.SSCursor)

    def rollback(self):
        """
        结束当前只读事务；只读会话里回滚没有副作用
        """
        try:
            self.connection.rollback()
        except Exception:  # noqa: BLE001 回滚失败不影响已取得的结果
            LOGGER.warning("回滚只读事务失败")

    def close(self):
        try:
            self.connection.close()
        except Exception:  # noqa: BLE001 关闭失败不影响已取得的结果
            LOGGER.warning("关闭只读连接失败")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False


def open_readonly_connection(datasource_id: int, statement_timeout: int = None) -> ReadonlyConnection:
    """
    按数据源 id 开一条只读短连接，供 SQL 试运行使用

    **跨模块请调用本函数，不要自己解密凭据、也不要直接读 Datasource**（architecture.md）。
    凭据解密、只读会话与超时设置都在这里完成。调用方负责关闭，建议用 `with`。
    """
    row = models.Datasource.objects.filter(id=datasource_id, is_deleted=False).first()
    if row is None:
        raise DatasourceUnavailable(f"数据源 {datasource_id} 不存在或已删除")

    config = load_collect_config()
    timeout = int(config["statement_timeout"] if statement_timeout is None else statement_timeout)
    credential = crypto.decrypt(row.password)
    timeout_ms = timeout * 1000

    if row.db_type == custom_enum.DbTypeEnum.POSTGRESQL.value:
        connection = psycopg2.connect(
            host=row.host,
            port=row.port,
            dbname=row.db_name,
            user=row.username,
            password=credential,
            connect_timeout=config["connect_timeout"],
            application_name="my-dba-sql-analysis",
        )
        connection.set_session(readonly=True, autocommit=False)
        with connection.cursor() as cursor:
            cursor.execute("SET statement_timeout = %s", (timeout_ms,))
    else:
        connection = pymysql.connect(
            host=row.host,
            port=row.port,
            database=row.db_name,
            user=row.username,
            password=credential,
            connect_timeout=config["connect_timeout"],
            charset="utf8mb4",
            autocommit=True,
            program_name="my-dba-sql-analysis",
        )
        with connection.cursor() as cursor:
            cursor.execute("SET SESSION TRANSACTION READ ONLY")
            cursor.execute("SET SESSION MAX_EXECUTION_TIME = %s", (timeout_ms,))

    return ReadonlyConnection(connection, row.db_type)


def datasource_brief(datasource_id: int):
    """
    按 id 取数据源的简要信息，不存在返回 None

    **跨模块请调用本函数做存在性判断与类型取用，不要直接查 Datasource**
    （architecture.md 跨模块约束）。不带出任何凭据字段。
    """
    row = models.Datasource.objects.filter(id=datasource_id, is_deleted=False).first()
    if row is None:
        return None
    return {"id": row.id, "name": row.name, "db_type": row.db_type, "is_enabled": row.is_enabled}


def latest_snapshot(datasource_id: int):
    return (
        models.MetadataSnapshot.objects.filter(datasource_id=datasource_id, is_deleted=False)
        .order_by("-collect_time", "-id")
        .first()
    )


def list_snapshot_tables(datasource_id: int, keyword: str = "") -> list:
    """
    取某数据源最近快照中的表摘要

    **跨模块请调用本函数，不要直接查 MetadataSnapshot**（architecture.md 跨模块约束）。
    返回结构经裁剪，只含调用方通常需要的字段。
    """
    snapshot = latest_snapshot(datasource_id)
    if snapshot is None:
        return []
    keyword = (keyword or "").strip().lower()
    result = []
    for table in snapshot.raw_data.get("tables") or []:
        if keyword and keyword not in (table.get("name") or "").lower():
            continue
        result.append(
            {
                "schema": table.get("schema") or "",
                "name": table.get("name") or "",
                "comment": table.get("comment") or "",
                "row_count": int(table.get("row_count") or 0),
                "columns": [
                    {
                        "name": column.get("name") or "",
                        "data_type": column.get("data_type") or "",
                        "nullable": bool(column.get("nullable")),
                        "comment": column.get("comment") or "",
                    }
                    for column in table.get("columns") or []
                ],
                "primary_key": (table.get("primary_key") or {}).get("columns") or [],
                "foreign_keys": [
                    {"columns": fk.get("columns") or [], "ref_table": fk.get("ref_table") or ""}
                    for fk in table.get("foreign_keys") or []
                ],
            }
        )
    return result


def list_datasource_metrics() -> dict:
    """
    每个数据源的**最新指标**与近期连接数趋势

    **跨模块请调用本函数**：调用方不要自己查 `DatasourceMetric`。

    只回两类行，都受控：每库**一行**最新值，加上最近 24 小时的采样序列。指标表是
    时间序列，30 天能攒到八千多行一库——整表捞出来在首页每次加载时跑一遍是不行的。

    趋势**降采样到最多 48 个点**：画一条 100px 宽的小线用不着 288 个点。
    """
    samples = _recent_online_samples()

    items = []
    for datasource_id, latest in _latest_samples().items():
        series = samples.get(datasource_id, [])
        items.append(
            {
                "datasource_id": datasource_id,
                "is_online": latest.is_online,
                "fail_reason": latest.fail_reason,
                "connection_count": latest.connection_count,
                "max_connections": latest.max_connections,
                "database_size": latest.database_size,
                # 离线时不给命中率——前端本来也不展示过期指标，给了反而容易被误用
                "cache_hit_ratio": _hit_ratio(series) if latest.is_online else None,
                "io_read_bytes": latest.io_read_bytes,
                "io_write_bytes": latest.io_write_bytes,
                "unavailable": latest.unavailable,
                "collected_at": latest.create_time.strftime("%Y-%m-%d %H:%M:%S"),
                "trend": _downsample(
                    [row["connection_count"] for row in series if row["connection_count"] is not None]
                ),
            }
        )
    return {"items": items}


_TREND_HOURS = 24
_TREND_MAX_POINTS = 48


def _latest_samples() -> dict:
    """
    每库最近的一条采样，`{datasource_id: row}`

    **不按时间截断**：采集进程停掉时，「最新一条」可能是几天前的，但界面正是靠它带着的
    采集时刻把「陈旧」暴露出来。用 `DISTINCT ON` 让数据库每库只回一行——`ORDER BY`
    与索引 `(datasource_id, create_time DESC)` 同序，走索引即可，无需扫全表。
    """
    rows = (
        models.DatasourceMetric.objects.filter(is_deleted=False)
        .order_by("datasource_id", "-create_time", "-id")
        .distinct("datasource_id")
    )
    return {row.datasource_id: row for row in rows}


def _recent_online_samples() -> dict:
    """
    最近 24 小时**在线**的采样，按数据源分组、按时间升序

    只要趋势与间隔命中率用得上的几列。离线那几轮的指标全是空的，混进来只会把趋势
    画歪，因此直接在库里滤掉。
    """
    deadline = datetime.now() - timedelta(hours=_TREND_HOURS)
    rows = (
        models.DatasourceMetric.objects.filter(is_deleted=False, is_online=True, create_time__gte=deadline)
        .order_by("datasource_id", "create_time", "id")
        .values_list("datasource_id", "connection_count", "cache_hit_count", "cache_read_count")
    )
    grouped = {}
    for datasource_id, connection_count, hit_count, read_count in rows:
        grouped.setdefault(datasource_id, []).append(
            {"connection_count": connection_count, "cache_hit_count": hit_count, "cache_read_count": read_count}
        )
    return grouped


def _downsample(values: list, limit: int = _TREND_MAX_POINTS) -> list:
    """
    等间隔抽样到最多 limit 个点，**末尾一点一定保留**——最新的变化最该被看到
    """
    total = len(values)
    if total <= limit:
        return values
    step = total / limit
    picked = [values[min(int(index * step), total - 1)] for index in range(limit)]
    picked[-1] = values[-1]
    return picked


def _hit_ratio(series: list):
    """
    缓存命中率（百分数，保留两位），入参是**按时间升序**的采样序列

    优先用**相邻两次采样的差值**算区间命中率：累计比率是「自服务启动以来」的平均值，
    几乎不随近期变化而变动，看不出问题。只有一条记录时退回累计值。

    计数器可能因目标库重启而归零，差值为负时同样退回累计值——不能算出负命中率。
    """
    if not series:
        return None
    latest = series[-1]
    if latest["cache_hit_count"] is None or latest["cache_read_count"] is None:
        return None

    hits, reads = latest["cache_hit_count"], latest["cache_read_count"]
    if len(series) > 1:
        previous = series[-2]
        delta_hits = hits - (previous["cache_hit_count"] or 0)
        delta_reads = reads - (previous["cache_read_count"] or 0)
        if delta_hits >= 0 and delta_reads >= 0 and delta_hits + delta_reads > 0:
            hits, reads = delta_hits, delta_reads

    total = hits + reads
    return round(hits * 100.0 / total, 2) if total > 0 else None


def rule_counts() -> dict:
    """
    库表分析规则的条目数

    **跨模块请调用本函数**，不要直接 import 规则注册表。

    取**代码声明**的长度，不是 `analysis_rule` 表的行数：那张表只有同步过之后才有行，
    数它会在「还没点过同步」时得到 0——而那时规则有 7 条、分析也照常跑（代码声明是
    全集与默认值来源，库只是覆盖层）。**数出 0 是把「没同步」说成了「没有规则」。**
    """
    return {"total": len(rule_registry.all_rules())}


def list_datasource_status() -> list:
    """
    每个数据源的采集状态与**最近快照**的问题分级统计

    **跨模块请调用本函数**（architecture.md）：调用方不要自己查
    `Datasource` / `MetadataSnapshot` / `CollectTask` / `CatalogIssue`。

    问题数**只统计最近一次快照**，不累计历史——同一处问题每次采集都会重新产出，
    累加会让数字随采集次数虚增、且不指向任何动作（首页提案的 D4）。
    """
    datasources = models.Datasource.objects.filter(is_deleted=False).order_by("id")

    latest_snapshot = {}
    for snapshot in models.MetadataSnapshot.objects.filter(is_deleted=False).order_by(
        "datasource_id", "-collect_time", "-id"
    ):
        latest_snapshot.setdefault(snapshot.datasource_id, snapshot)

    latest_task = {}
    for task in models.CollectTask.objects.filter(is_deleted=False).order_by("datasource_id", "-id"):
        latest_task.setdefault(task.datasource_id, task)

    counts = {}
    snapshot_ids = [snapshot.id for snapshot in latest_snapshot.values()]
    if snapshot_ids:
        rows = (
            models.CatalogIssue.objects.filter(is_deleted=False, snapshot_id__in=snapshot_ids)
            .values("snapshot_id", "issue_level")
            .annotate(total=Count("id"))
        )
        counts = {(row["snapshot_id"], row["issue_level"]): row["total"] for row in rows}

    items = []
    for datasource in datasources:
        snapshot = latest_snapshot.get(datasource.id)
        task = latest_task.get(datasource.id)
        issue_counts = {level.value: 0 for level in custom_enum.IssueLevelEnum}
        if snapshot is not None:
            for level in custom_enum.IssueLevelEnum:
                issue_counts[level.value] = counts.get((snapshot.id, level.value), 0)

        items.append(
            {
                "id": datasource.id,
                "name": datasource.name,
                "db_type": datasource.db_type,
                "is_enabled": datasource.is_enabled,
                "collect_status": _collect_status(snapshot, task),
                "table_count": snapshot.table_count if snapshot is not None else 0,
                "collect_time": snapshot.collect_time.strftime("%Y-%m-%d %H:%M:%S") if snapshot else "",
                # 没有快照时问题数一律为 0，并由调用方按 collect_status 决定**不展示**它们——
                # 「还没有数据」不能让使用者读成「没有问题」
                "issue_counts": issue_counts if snapshot is not None else {},
                "fail_reason": (
                    task.fail_reason if task and task.status == custom_enum.CollectTaskStatusEnum.FAILED.value else ""
                ),
            }
        )

    # 把需要处理的浮上来：这个列表的用途是回答「我该关注哪个库」
    items.sort(
        key=lambda item: (_STATUS_RANK.get(item["collect_status"], 9), -item["issue_counts"].get(1, 0), item["name"])
    )
    return items


def _collect_status(snapshot, task) -> str:
    """
    采集状态。有快照就算「已采集」——即便最近一次采集失败，旧数据仍然可用，
    失败通过 `fail_reason` 单独带出，不必让状态本身变得含糊
    """
    if task is not None and task.status == custom_enum.CollectTaskStatusEnum.RUNNING.value:
        return "running"
    if snapshot is not None:
        return "collected"
    if task is not None and task.status == custom_enum.CollectTaskStatusEnum.FAILED.value:
        return "failed"
    return "never"


# 排序权重：需要处理的在前
_STATUS_RANK = {"failed": 0, "never": 1, "running": 2, "collected": 3}

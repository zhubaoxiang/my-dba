"""
数据源指标探测

只采**数据库自身报出来的东西**：可用性、连接数、容量与命中情况。

**刻意不采目标服务器本机的 CPU / 内存 / 磁盘。** 那些要读服务器上的本地文件
（`pg_read_file('/proc/meminfo')` 之类），实测**可行**——采集账号是超级用户。但否决：

1. 需要**超级用户**，而数据源采集的定位是「只读采集」，权限放得过大
2. 本质是引入「**任意文件读**」能力，是本项目的第一项此类能力
3. 强绑 Linux，且 MySQL 走不通

详见 `openspec/changes/add-datasource-metrics/design.md` 的 D1。**不要在这里加读文件的代码。**

探测走的是**已有的只读采集连接**（`services.open_readonly_connection`），不需要额外权限，
也不复用 Django 的 ORM 连接。
"""

from datetime import datetime, timedelta

from apps.datasource import models
from utils import custom_enum
from utils.configure import CONF_ATTR
from utils.logger import get_logger

LOGGER = get_logger("datasource.log")

_DEFAULT_INTERVAL = 300
_DEFAULT_RETENTION_DAYS = 30
_DEFAULT_PROBE_TIMEOUT = 5

# 单条探测语句取不到值时的归类名，会记进 `unavailable`
_CACHE = "cache"
_IO = "io"


def _int_config(key: str, default: int) -> int:
    try:
        return int(CONF_ATTR.get(key))
    except (TypeError, ValueError):
        return default


def interval() -> int:
    """
    采集间隔（秒）
    """
    return _int_config("datasource_metrics_interval", _DEFAULT_INTERVAL)


def retention_days() -> int:
    """
    指标历史保留天数
    """
    return _int_config("datasource_metrics_retention_days", _DEFAULT_RETENTION_DAYS)


def probe_timeout() -> int:
    """
    单个数据源的探测超时（秒）
    """
    return _int_config("datasource_metrics_probe_timeout", _DEFAULT_PROBE_TIMEOUT)


# ----------------------------------------------------------------------
# 各方言的取数
# ----------------------------------------------------------------------


def _probe_postgres(connection) -> dict:
    metrics, unavailable = {}, []

    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT (SELECT count(*) FROM pg_stat_activity) AS connection_count,"
            "       current_setting('max_connections')::int AS max_connections,"
            "       pg_database_size(current_database()) AS database_size"
        )
        row = cursor.fetchone()
        metrics["connection_count"] = int(row[0]) if row and row[0] is not None else None
        metrics["max_connections"] = int(row[1]) if row and row[1] is not None else None
        metrics["database_size"] = int(row[2]) if row and row[2] is not None else None

        cursor.execute("SELECT blks_hit, blks_read FROM pg_stat_database WHERE datname = current_database()")
        row = cursor.fetchone()
        if row:
            metrics["cache_hit_count"] = int(row[0] or 0)
            metrics["cache_read_count"] = int(row[1] or 0)
        else:
            unavailable.append(_CACHE)

    # pg_stat_io 是 PG 16 才有的；PG 16 以下没有该视图，取不到就如实标注而不是记 0——
    # 「没有 IO 数据」与「IO 为零」是两回事。
    #
    # 注意它**没有** read_bytes / write_bytes 列：字节数要用 `次数 × op_bytes` 算，
    # op_bytes 是每次 I/O 的字节数（实测稳定为 8192）。
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT COALESCE(sum(reads * op_bytes), 0), COALESCE(sum(writes * op_bytes), 0) FROM pg_stat_io"
            )
            row = cursor.fetchone()
            metrics["io_read_bytes"] = int(row[0] or 0)
            metrics["io_write_bytes"] = int(row[1] or 0)
    except Exception as exc:  # noqa: BLE001 视图缺失（PG 16 以下）或取数失败都按「没采到」处理
        # 不写「该库不支持」——失败也可能是别的原因，断言成因会把日志变成误导
        LOGGER.warning("IO 指标采集失败，跳过 datasource_id=%s err=%s", getattr(connection, "name", ""), exc)
        connection.rollback()  # 语句失败会中断事务，必须回滚后才能继续
        unavailable.append(_IO)

    return {**metrics, "unavailable": unavailable}


def _mysql_status(cursor, name: str):
    """
    取一个 MySQL 全局状态变量；不存在时返回 None（SHOW 不会报错，只是没有行）
    """
    cursor.execute(f"SHOW GLOBAL STATUS LIKE '{name}'")
    row = cursor.fetchone()
    return int(row[1]) if row else None


def _probe_mysql(connection) -> dict:
    metrics, unavailable = {}, []

    with connection.cursor() as cursor:
        cursor.execute("SHOW STATUS LIKE 'Threads_connected'")
        row = cursor.fetchone()
        metrics["connection_count"] = int(row[1]) if row else None

        cursor.execute("SHOW VARIABLES LIKE 'max_connections'")
        row = cursor.fetchone()
        metrics["max_connections"] = int(row[1]) if row else None

        cursor.execute(
            "SELECT COALESCE(SUM(data_length + index_length), 0) FROM information_schema.tables"
            " WHERE table_schema = DATABASE()"
        )
        row = cursor.fetchone()
        metrics["database_size"] = int(row[0]) if row and row[0] is not None else None

        hits = _mysql_status(cursor, "Innodb_buffer_pool_read_requests")
        reads = _mysql_status(cursor, "Innodb_buffer_pool_reads")
        if hits is None or reads is None:
            unavailable.append(_CACHE)  # 非 InnoDB 引擎下没有这两个变量
        else:
            metrics["cache_hit_count"], metrics["cache_read_count"] = hits, reads

        io_read = _mysql_status(cursor, "Innodb_data_read")
        io_write = _mysql_status(cursor, "Innodb_data_written")
        if io_read is None or io_write is None:
            unavailable.append(_IO)
        else:
            metrics["io_read_bytes"], metrics["io_write_bytes"] = io_read, io_write

    return {**metrics, "unavailable": unavailable}


_PROBES = {
    custom_enum.DbTypeEnum.POSTGRESQL.value: _probe_postgres,
    custom_enum.DbTypeEnum.MYSQL.value: _probe_mysql,
}


# ----------------------------------------------------------------------
# 对外入口
# ----------------------------------------------------------------------


def _offline(exc: Exception) -> dict:
    """
    连不上时**只记失败原因，不记任何指标**

    记 0 会让人以为「连上了只是没负载」，那是完全不同的一回事。
    """
    return {
        "is_online": False,
        "fail_reason": f"{type(exc).__name__}: {exc}"[:1024],
        "unavailable": [],
    }


def probe(datasource_id: int, timeout: int = None) -> dict:
    """
    探测一个数据源的指标

    **不抛异常**：连不上、某项取不到都如实反映在返回值里——一轮采集不该因为
    一个库出问题而整体中断。
    """
    from apps.datasource import services  # 延迟导入：services 是上层编排，避免循环引用

    try:
        connection = services.open_readonly_connection(datasource_id, probe_timeout() if timeout is None else timeout)
    except Exception as exc:  # noqa: BLE001 连接失败是本函数的正常返回之一
        return _offline(exc)

    try:
        with connection:
            try:
                collected = _PROBES[connection.db_type](connection)
            finally:
                connection.rollback()  # 只读事务用完即回滚，不留下悬挂事务
    except Exception as exc:  # noqa: BLE001 取数中途失败同样按「本轮没采到」处理
        LOGGER.warning("指标探测失败 datasource_id=%s err=%s", datasource_id, exc)
        return _offline(exc)

    return {"is_online": True, "fail_reason": "", **collected}


def collect_all(creator: str = "metrics") -> dict:
    """
    采集全部未删除数据源并写库，返回 `{written, failed}`

    单个数据源出问题绝不影响其余：`probe()` 本身不抛异常，写库也逐条兜住。
    """
    datasource_ids = list(models.Datasource.objects.filter(is_deleted=False).values_list("id", flat=True))
    written = failed = 0
    for datasource_id in datasource_ids:
        try:
            models.DatasourceMetric.objects.create(datasource_id=datasource_id, creator=creator, **probe(datasource_id))
            written += 1
        except Exception as exc:  # noqa: BLE001 写库失败也不该中断整轮
            failed += 1
            LOGGER.error("指标写库失败 datasource_id=%s err=%s", datasource_id, exc)

    LOGGER.info("指标采集完成 written=%s failed=%s", written, failed)
    return {"written": written, "failed": failed}


def purge_expired() -> int:
    """
    清理超过保留期的指标，返回删除条数

    放在采集循环里顺带做，不额外引入第二个定时任务。
    """
    deadline = datetime.now() - timedelta(days=retention_days())
    deleted, _ = models.DatasourceMetric.objects.filter(create_time__lt=deadline).delete()
    return deleted

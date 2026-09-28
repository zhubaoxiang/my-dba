"""
SQL 试运行与执行计划

**只有只读语句会被真正执行**（design.md D4）：

- 只读语句：在目标库的只读会话里执行，用**流式游标**只取前 N 行，返回实际行数与耗时
- 非只读语句：只取执行计划（`EXPLAIN`，**不带 ANALYZE**），语句本身不会被执行
- 多语句：一次试运行只接受**一条**语句。多语句的执行语义（顺序、部分失败、其中哪一条会写）
  很容易看漏，直接不支持比小心处理更安全

`EXPLAIN ANALYZE` 不在本次范围——它在 PostgreSQL 上会**真执行**语句。
"""

import time

from apps.datasource import services as datasource_services
from utils import custom_enum
from utils.configure import CONF_ATTR
from utils.logger import get_logger

LOGGER = get_logger("sqlanalysis.log")

_DEFAULT_MAX_ROWS = 100
_DEFAULT_TIMEOUT = 30

_KIND_LABEL = {
    custom_enum.StatementKindEnum.DML: "写操作（DML）",
    custom_enum.StatementKindEnum.DDL: "结构变更（DDL）",
    custom_enum.StatementKindEnum.OTHER: "非只读语句",
}


class ExecutionRejected(Exception):
    """
    试运行请求被拒绝（不可执行、缺数据源、取不到计划等），消息会直接回给使用者
    """


def _int_config(key: str, default: int) -> int:
    try:
        return int(CONF_ATTR.get(key))
    except (TypeError, ValueError):
        return default


def default_max_rows() -> int:
    return _int_config("sqlanalysis_max_rows", _DEFAULT_MAX_ROWS)


def default_timeout() -> int:
    return _int_config("sqlanalysis_statement_timeout", _DEFAULT_TIMEOUT)


def _kind_label(kind) -> str:
    return _KIND_LABEL.get(kind, "非只读语句")


def _explain(connection, statement) -> str:
    """
    取执行计划。**不带 ANALYZE**，因此语句不会被真正执行
    """
    with connection.cursor() as cursor:
        cursor.execute(f"EXPLAIN {statement.sql}")
        rows = cursor.fetchall()
    return "\n".join(" ".join(str(item) for item in row) for row in rows)


def _run_readonly(connection, statement, limit: int) -> dict:
    """
    在只读会话里真跑一条只读语句，最多取 limit 行

    多取一行用于判断「是否被截断」——只报取到的行数而不说被截断，使用者会以为结果就这么多。
    """
    started = time.monotonic()
    cursor = connection.probe_cursor()
    try:
        cursor.execute(statement.sql)
        rows = cursor.fetchmany(limit + 1)
    finally:
        cursor.close()
    duration_ms = int((time.monotonic() - started) * 1000)
    truncated = len(rows) > limit
    return {
        "row_count": min(len(rows), limit),
        "duration_ms": duration_ms,
        "truncated": truncated,
    }


def execute(parse_result, datasource_id: int, max_row_count: int = None, timeout: int = None) -> dict:
    """
    试运行

    :raises ExecutionRejected: 语句不可执行、缺少数据源、或目标库取不到计划
    """
    statements = parse_result.statements
    if not statements:
        raise ExecutionRejected("没有可执行的语句——请先解决语法错误。")
    if len(statements) > 1:
        raise ExecutionRejected("试运行一次只支持一条语句。多语句里若混有写操作很容易看漏，请分开执行。")

    statement = statements[0]
    limit = default_max_rows() if max_row_count is None else int(max_row_count)
    wait = default_timeout() if timeout is None else int(timeout)

    try:
        connection = datasource_services.open_readonly_connection(datasource_id, wait)
    except datasource_services.DatasourceUnavailable as exc:
        raise ExecutionRejected(str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 连不上目标库属可预期结果
        raise ExecutionRejected(f"连接目标库失败：{exc}") from exc

    with connection:
        plan = ""
        plan_error = ""
        try:
            plan = _explain(connection, statement)
        except Exception as exc:  # noqa: BLE001 目标库拒绝为某些语句生成计划属可预期结果
            plan_error = str(exc)
            LOGGER.info("未能取得执行计划 kind=%s err=%s", statement.kind.name, exc)

        if not statement.is_read_only:
            connection.rollback()
            note = f"该语句是{_kind_label(statement.kind)}，**未被执行**。"
            if plan_error:
                # DDL 生成不了计划是目标库的能力边界，不是使用者的 SQL 有问题——
                # 若把库返回的原始错误（"syntax error at or near DROP"）直接抛出去，
                # 使用者会以为是自己写错了
                note += "目标库也不支持为这类语句生成执行计划，因此本次只有这一条说明。"
            return {
                "executed": False,
                "plan": plan,
                "row_count": 0,
                "duration_ms": 0,
                "truncated": False,
                "note": note,
            }

        if plan_error:
            connection.rollback()
            raise ExecutionRejected(f"未能取得执行计划：{plan_error}")

        try:
            result = _run_readonly(connection, statement, limit)
        except Exception as exc:  # noqa: BLE001 执行失败属可预期结果，要如实回给使用者
            connection.rollback()
            raise ExecutionRejected(f"执行失败：{exc}") from exc
        connection.rollback()

    note = f"仅返回前 {limit} 行，结果已被截断。" if result["truncated"] else ""
    return {"executed": True, "plan": plan, **result, "note": note}

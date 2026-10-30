"""
模型用量：记录、聚合与清理

**记录动作不向调用方抛异常**：观测能力不能成为新的故障点。
"""

from datetime import datetime, time, timedelta

from django.db.models import Avg, Count, Q, Sum
from django.db.models.functions import TruncDate

from apps.llmusage import context, models
from utils import custom_enum
from utils.logger import get_logger

LOGGER = get_logger("llm_usage.log")

RETENTION_DAYS = 90
_RANGE_DAYS = {"today": 1, "7d": 7, "30d": 30}
_GROUP_FIELDS = {"user": "username", "model": "model_name", "feature": "feature"}


def record_call(
    *,
    call_type,
    provider=None,
    model_name="",
    latency_ms=0,
    success=True,
    error_code="",
    prompt_tokens=None,
    completion_tokens=None,
    total_tokens=None,
    units=None,
    feature=None,
):
    """
    写入一条调用记录；任何异常只记日志，不影响原始调用
    """
    try:
        ctx = context.get_context()
        username = ctx["username"] or "system"
        resolved_feature = feature if feature is not None else ctx["feature"]
        if resolved_feature == custom_enum.LlmFeatureEnum.OTHER.value:
            LOGGER.info("模型调用来源未识别 path=%s", ctx["path"])
        models.LlmUsage.objects.create(
            username=username,
            feature=resolved_feature,
            call_type=call_type,
            provider_id=getattr(provider, "id", None),
            model_name=model_name or getattr(provider, "model_name", ""),
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            units=units,
            latency_ms=latency_ms,
            success=success,
            error_code=error_code,
            request_id=ctx["request_id"],
            creator=username[:32],
        )
    except Exception as exc:  # noqa: BLE001 统计失败不能影响业务调用
        LOGGER.error("模型用量记录失败: %s", exc)


def classify_error(error) -> str:
    """
    错误只记分类，不存原始堆栈或响应正文
    """
    text = f"{type(error).__name__} {error}".lower()
    if "timeout" in text or "timedout" in text:
        return "timeout"
    if "connect" in text:
        return "connection"
    if "400" in text or "bad request" in text:
        return "bad_request"
    return "model_error"


def purge_expired() -> int:
    """
    删除超过保留期的记录

    用量是纯观测数据，过期后没有恢复价值；与 `datasource_metrics` 的清理一致，
    这里物理删除，不保留软删除记录。
    """
    deadline = datetime.now() - timedelta(days=RETENTION_DAYS)
    deleted, _ = models.LlmUsage.objects.filter(create_time__lt=deadline).delete()
    return deleted


def _base_queryset(range_key: str):
    days = _RANGE_DAYS[range_key]
    start = datetime.combine(datetime.now().date() - timedelta(days=days - 1), time.min)
    return models.LlmUsage.objects.filter(is_deleted=False, create_time__gte=start)


def _block(name: str, loader, unavailable: dict) -> dict:
    """
    逐块取数：某一块失败只标记该块，不整页报错
    """
    try:
        return {"available": True, **loader()}
    except Exception as exc:  # noqa: BLE001 聚合失败不应拖垮整个页面
        LOGGER.error("模型用量聚合失败 block=%s err=%s", name, exc)
        return {"available": False, **unavailable}


def _summary(qs) -> dict:
    agg = qs.aggregate(
        calls=Count("id"),
        success_calls=Count("id", filter=Q(success=True)),
        total_tokens=Sum("total_tokens"),
        avg_latency=Avg("latency_ms"),
    )
    calls = agg["calls"] or 0
    return {
        "calls": calls,
        "success_calls": agg["success_calls"] or 0,
        # 没有调用时成功率是「无数据」而不是 0%
        "success_rate": round(agg["success_calls"] / calls, 4) if calls else None,
        "total_tokens": agg["total_tokens"],
        "avg_latency_ms": round(agg["avg_latency"]) if agg["avg_latency"] is not None else None,
    }


def _trend(qs) -> dict:
    rows = (
        qs.annotate(day=TruncDate("create_time"))
        .values("day")
        .annotate(
            calls=Count("id"),
            success_calls=Count("id", filter=Q(success=True)),
            total_tokens=Sum("total_tokens"),
            avg_latency=Avg("latency_ms"),
        )
        .order_by("day")
    )
    return {
        "items": [
            {
                "date": row["day"].strftime("%Y-%m-%d"),
                "calls": row["calls"],
                "success_calls": row["success_calls"],
                "total_tokens": row["total_tokens"],
                "avg_latency_ms": round(row["avg_latency"]) if row["avg_latency"] is not None else None,
            }
            for row in rows
        ]
    }


def _group_label(group_by: str, key):
    if group_by == "feature":
        return dict(custom_enum.LlmFeatureEnum.choices).get(key, str(key))
    return key or "（空）"


def _groups(qs, group_by: str) -> dict:
    field = _GROUP_FIELDS[group_by]
    rows = (
        qs.values(field)
        .annotate(
            calls=Count("id"),
            success_calls=Count("id", filter=Q(success=True)),
            total_tokens=Sum("total_tokens"),
            avg_latency=Avg("latency_ms"),
        )
        .order_by("-calls")
    )
    items = []
    for row in rows:
        calls = row["calls"] or 0
        items.append(
            {
                "key": row[field],
                "label": _group_label(group_by, row[field]),
                "calls": calls,
                "success_calls": row["success_calls"],
                "success_rate": round(row["success_calls"] / calls, 4) if calls else None,
                "total_tokens": row["total_tokens"],
                "avg_latency_ms": round(row["avg_latency"]) if row["avg_latency"] is not None else None,
            }
        )
    return {"items": items}


def build_usage(range_key: str, group_by: str) -> dict:
    """
    汇总、趋势与分组各自兜底，某一块失败不影响其他块
    """
    qs = _base_queryset(range_key)
    return {
        "range": range_key,
        "group_by": group_by,
        "summary": _block(
            "summary",
            lambda: _summary(qs),
            {
                "calls": None,
                "success_calls": None,
                "success_rate": None,
                "total_tokens": None,
                "avg_latency_ms": None,
            },
        ),
        "trend": _block("trend", lambda: _trend(qs), {"items": []}),
        "groups": _block("groups", lambda: _groups(qs, group_by), {"items": []}),
    }


def calls_queryset(range_key: str, success=None, min_latency=None):
    qs = _base_queryset(range_key)
    if success is not None:
        qs = qs.filter(success=success)
    if min_latency is not None:
        qs = qs.filter(latency_ms__gte=min_latency)
    return qs.order_by("-create_time", "-id")

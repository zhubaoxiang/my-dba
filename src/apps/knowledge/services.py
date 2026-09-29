"""
知识模块的跨模块只读契约

首页要展示知识库的文档与摄入状态，但**不能直接查本模块的 model**（`architecture.md`
跨模块约束）。对外可用的读取入口集中在这里，与 datasource 模块的 services 同一形状。
"""

from django.db import models as django_models

from apps.knowledge import models
from utils import custom_enum


def list_knowledge_base_status() -> list:
    """
    每个知识库的文档数与摄入状态统计

    **跨模块请调用本函数**：调用方不要自己查 `KnowledgeBase` / `KbDocument`。

    排序把需要处理的浮上来：有摄入失败的在前，其次有未处理完的，再按名称。
    """
    bases = models.KnowledgeBase.objects.filter(is_deleted=False).order_by("id")

    # 一次把所有文档按 (知识库, 状态) 聚合出来，避免每个知识库查一遍
    rows = (
        models.KbDocument.objects.filter(is_deleted=False)
        .values("knowledge_base_id", "status")
        .annotate(total=django_models.Count("id"))
    )
    stats = {}
    for row in rows:
        stats.setdefault(row["knowledge_base_id"], {})[row["status"]] = row["total"]

    pending_statuses = (
        custom_enum.DocumentStatusEnum.PENDING.value,
        custom_enum.DocumentStatusEnum.PROCESSING.value,
    )

    items = []
    for base in bases:
        by_status = stats.get(base.id, {})
        items.append(
            {
                "id": base.id,
                "name": base.name,
                "description": base.description,
                "is_enabled": base.is_enabled,
                "document_count": sum(by_status.values()),
                "failed_count": by_status.get(custom_enum.DocumentStatusEnum.FAILED.value, 0),
                "pending_count": sum(by_status.get(status, 0) for status in pending_statuses),
            }
        )

    items.sort(
        key=lambda item: (
            0 if item["failed_count"] else 1,
            0 if item["pending_count"] else 1,
            item["name"],
        )
    )
    return items

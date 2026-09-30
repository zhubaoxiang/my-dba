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
    每个知识库的规模、摄入状态与使用情况

    **跨模块请调用本函数**：调用方不要自己查 `KnowledgeBase` / `KbDocument` / `QaSession`。

    排序把需要处理的浮上来：有摄入失败的在前，其次有未处理完的，再按名称。
    """
    bases = models.KnowledgeBase.objects.filter(is_deleted=False).order_by("id")
    documents = models.KbDocument.objects.filter(is_deleted=False)

    # 三组统计**各一次聚合查询**，不逐个知识库查
    rows = documents.values("knowledge_base_id", "status").annotate(
        total=django_models.Count("id"), chunks=django_models.Sum("chunk_count")
    )
    stats = {}
    for row in rows:
        bucket = stats.setdefault(row["knowledge_base_id"], {"by_status": {}, "chunks": 0})
        bucket["by_status"][row["status"]] = row["total"]
        bucket["chunks"] += row["chunks"] or 0

    updated = dict(documents.values_list("knowledge_base_id").annotate(latest=django_models.Max("update_time")))
    sessions = dict(
        models.QaSession.objects.filter(is_deleted=False, knowledge_base_id__isnull=False)
        .values_list("knowledge_base_id")
        .annotate(total=django_models.Count("id"))
    )

    pending_statuses = (
        custom_enum.DocumentStatusEnum.PENDING.value,
        custom_enum.DocumentStatusEnum.PROCESSING.value,
    )

    items = []
    for base in bases:
        bucket = stats.get(base.id) or {"by_status": {}, "chunks": 0}
        by_status = bucket["by_status"]
        latest = updated.get(base.id)
        items.append(
            {
                "id": base.id,
                "name": base.name,
                "description": base.description,
                "is_enabled": base.is_enabled,
                "document_count": sum(by_status.values()),
                # 「32 篇文档」是观感，检索块数才是**实际能搜到多少内容**：
                # 一篇 200 页的 PDF 只切出 3 个块，光看文档数看不出来
                "chunk_count": bucket["chunks"],
                "success_count": by_status.get(custom_enum.DocumentStatusEnum.SUCCESS.value, 0),
                "failed_count": by_status.get(custom_enum.DocumentStatusEnum.FAILED.value, 0),
                "pending_count": sum(by_status.get(status, 0) for status in pending_statuses),
                # 「建了 10 个知识库、9 个没人问过」是常见情况，值得显出来
                "qa_session_count": sessions.get(base.id, 0),
                "last_updated": latest.strftime("%Y-%m-%d %H:%M:%S") if latest else "",
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

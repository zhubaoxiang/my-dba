"""
向量检索与回库取正文

Qdrant 只做索引，正文以库内为准（design.md D1b）：先从 Qdrant 取回命中的块标识与相似度，
再按标识回库取正文，并过滤已软删除的块。因此索引丢失不影响正文，重建也只是重算向量。
"""

from apps.knowledge import vectorstore
from apps.knowledge.models import KbChunk
from utils.configure import CONF_ATTR

_DEFAULT_TOP_K = 5
_DEFAULT_THRESHOLD = 0.35


def _num_config(key: str, default, cast):
    try:
        return cast(CONF_ATTR.get(key))
    except (TypeError, ValueError):
        return default


def default_top_k() -> int:
    return _num_config("knowledge_retrieval_top_k", _DEFAULT_TOP_K, int)


def default_threshold() -> float:
    return _num_config("knowledge_similarity_threshold", _DEFAULT_THRESHOLD, float)


def retrieve(
    knowledge_base_id: int,
    query_vector: list,
    top_k: int = None,
    score_threshold: float = None,
) -> list:
    """
    检索指定知识库，返回按相似度降序的片段。

    每个片段含：chunk_id、content、heading_path、score、document_id、document_title、source。
    无命中时返回空列表；**向量服务不可用会抛 VectorStoreError**——两者必须区分，
    否则上层会把「服务挂了」误判成「没查到」而静默回退到通用模型。
    """
    hits = vectorstore.search(
        knowledge_base_id=knowledge_base_id,
        vector=query_vector,
        top_k=top_k if top_k is not None else default_top_k(),
        score_threshold=score_threshold if score_threshold is not None else default_threshold(),
    )
    if not hits:
        return []

    scores = {chunk_id: score for chunk_id, score in hits}
    chunks = KbChunk.objects.filter(id__in=scores.keys(), is_deleted=False).select_related("document")

    # 回库后按 Qdrant 给出的相似度重排；库中已删或缺失的块自然被丢掉
    ordered = sorted(chunks, key=lambda c: scores.get(c.id, 0.0), reverse=True)
    return [
        {
            "chunk_id": chunk.id,
            "content": chunk.content,
            "heading_path": chunk.heading_path,
            "score": round(scores.get(chunk.id, 0.0), 4),
            "document_id": chunk.document_id,
            "document_title": chunk.document.title,
            "source": chunk.document.source,
        }
        for chunk in ordered
    ]

"""
Qdrant 向量存储

正文存在业务库里（唯一事实来源），本模块只管向量侧的索引：建集合、写入、删除、检索。
点标识**直接使用 `kb_chunk.id`**，因此索引可由库内正文纯函数式重建，无需存储映射，
也不存在「向量写成功但标识回写失败」的漂移窗口（design.md D1b）。
"""

from qdrant_client import QdrantClient
from qdrant_client.http import models as qmodels

from apps.knowledge.models import EMBEDDING_DIMENSIONS
from utils.configure import CONF_ATTR

_DEFAULT_URL = "http://127.0.0.1:6333"
_DEFAULT_COLLECTION = "my_dba_kb"
_DEFAULT_TIMEOUT = 15

# 余弦距离，与写入时的嵌入模型语义匹配
_DISTANCE = qmodels.Distance.COSINE

# payload 里只放检索过滤所需的最小字段，正文不进 Qdrant
_PAYLOAD_KB = "knowledge_base_id"
_PAYLOAD_DOCUMENT = "document_id"


class VectorStoreError(Exception):
    """
    向量服务不可用或操作失败。

    单独定义是为了让上层能把「向量服务挂了」与「检索无命中」区分开——
    后者是正常的空结果，前者必须报错，否则会被误判成无命中而静默回退到通用模型。
    """


def collection_name() -> str:
    return str(CONF_ATTR.get("knowledge_qdrant_collection") or _DEFAULT_COLLECTION)


def _timeout() -> float:
    try:
        return float(CONF_ATTR.get("knowledge_llm_timeout"))
    except (TypeError, ValueError):
        return float(_DEFAULT_TIMEOUT)


def _client() -> QdrantClient:
    """
    构造 Qdrant 客户端。

    **`trust_env=False`**：向量服务是内网组件，不该受开发机/服务器上的系统代理影响。
    实测踩过一次——本机开着代理时，httpx 会把发往内网地址的请求也丢给代理，
    代理连不通内网便回 502，现象与「向量服务挂了」完全一样，很难定位。
    注意这只关掉向量客户端的环境探测；模型调用走的是公网，那边仍需要代理。
    """
    url = str(CONF_ATTR.get("knowledge_qdrant_url") or _DEFAULT_URL)
    return QdrantClient(url=url, timeout=_timeout(), trust_env=False)


def ensure_collection() -> None:
    """
    集合不存在则创建。维度取自 EMBEDDING_DIMENSIONS，换嵌入模型等于重建整个集合。
    """
    try:
        client = _client()
        name = collection_name()
        if not client.collection_exists(name):
            client.create_collection(
                collection_name=name,
                vectors_config=qmodels.VectorParams(size=EMBEDDING_DIMENSIONS, distance=_DISTANCE),
            )
    except Exception as exc:  # noqa: BLE001 外部服务，按边界处理
        raise VectorStoreError(f"初始化向量集合失败: {exc}") from exc


def health() -> dict:
    """
    供连通性检查与运维查看
    """
    try:
        client = _client()
        name = collection_name()
        exists = client.collection_exists(name)
        points = client.count(collection_name=name).count if exists else 0
        return {"collection": name, "exists": exists, "points": points, "dimensions": EMBEDDING_DIMENSIONS}
    except Exception as exc:  # noqa: BLE001
        raise VectorStoreError(f"向量服务不可用: {exc}") from exc


def upsert_chunks(chunks: list) -> None:
    """
    写入向量。chunks 为 [(chunk_id, vector, knowledge_base_id, document_id), ...]

    点标识即 chunk_id，所以重复写入同一批是幂等的覆盖。
    """
    if not chunks:
        return
    points = [
        qmodels.PointStruct(
            id=int(chunk_id),
            vector=vector,
            payload={_PAYLOAD_KB: int(kb_id), _PAYLOAD_DOCUMENT: int(doc_id)},
        )
        for chunk_id, vector, kb_id, doc_id in chunks
    ]
    try:
        _client().upsert(collection_name=collection_name(), points=points, wait=True)
    except Exception as exc:  # noqa: BLE001
        raise VectorStoreError(f"写入向量失败: {exc}") from exc


def delete_chunks(chunk_ids: list) -> None:
    """
    按块标识删除向量（软删除文档时同步调用）
    """
    ids = [int(i) for i in chunk_ids if i]
    if not ids:
        return
    try:
        _client().delete(
            collection_name=collection_name(),
            points_selector=qmodels.PointIdsList(points=ids),
            wait=True,
        )
    except Exception as exc:  # noqa: BLE001
        raise VectorStoreError(f"删除向量失败: {exc}") from exc


def search(knowledge_base_id: int, vector: list, top_k: int, score_threshold: float) -> list:
    """
    在指定知识库内检索，返回 [(chunk_id, score), ...]

    按 knowledge_base_id 过滤，保证不跨库泄漏；低于阈值的候选由服务端丢弃。
    """
    try:
        result = _client().query_points(
            collection_name=collection_name(),
            query=vector,
            query_filter=qmodels.Filter(
                must=[qmodels.FieldCondition(key=_PAYLOAD_KB, match=qmodels.MatchValue(value=int(knowledge_base_id)))]
            ),
            limit=int(top_k),
            score_threshold=float(score_threshold),
            with_payload=False,
            with_vectors=False,
        )
    except Exception as exc:  # noqa: BLE001
        raise VectorStoreError(f"向量检索失败: {exc}") from exc
    return [(int(p.id), float(p.score)) for p in result.points]

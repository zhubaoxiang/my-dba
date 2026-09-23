"""
大模型接入

从 LlmProvider 配置构造 langchain 的模型对象。API Key 在此解密，只在内存中传递，
不落日志、不出接口。
"""

from langchain_openai import ChatOpenAI, OpenAIEmbeddings

from apps.knowledge import models
from utils import crypto, custom_enum
from utils.configure import CONF_ATTR

_DEFAULT_TIMEOUT = 60


def _timeout() -> float:
    try:
        return float(CONF_ATTR.get("knowledge_llm_timeout"))
    except (TypeError, ValueError):
        return float(_DEFAULT_TIMEOUT)


def active_provider(model_type=None):
    """
    取某一用途下当前生效且启用中的配置，没有则返回 None。

    对话与嵌入分开取：一端没配不该影响另一端（现实里两者常来自不同服务）。
    """
    model_type = model_type if model_type is not None else custom_enum.ModelTypeEnum.CHAT
    value = model_type.value if hasattr(model_type, "value") else int(model_type)
    return models.LlmProvider.objects.filter(
        is_deleted=False, is_enabled=True, is_active=True, model_type=value
    ).first()


def active_chat_provider():
    return active_provider(custom_enum.ModelTypeEnum.CHAT)


def active_embedding_provider():
    return active_provider(custom_enum.ModelTypeEnum.EMBEDDING)


def build_chat_model(provider, temperature: float = 0):
    """
    构造对话模型。base_url 可指向内网代理，无需改代码。
    """
    return ChatOpenAI(
        model=provider.model_name,
        base_url=provider.base_url,
        api_key=crypto.decrypt(provider.api_key),
        temperature=temperature,
        timeout=_timeout(),
        max_retries=1,
    )


def build_embeddings(provider):
    """
    构造嵌入模型。

    **显式传 `dimensions`**，把输出维度对齐到系统约定的 `EMBEDDING_DIMENSIONS`：
    向量库集合的 size 是固定的，而不少嵌入模型的**原生维度很大**
    （实测 Qwen3-VL-Embedding-8B 原生 4096），靠 Matryoshka 截到 1024 能省约 4 倍内存与存储。
    支持该参数的服务（OpenAI 3 代、Qwen3-Embedding 等）会照此返回；
    不支持的服务要么报错、要么忽略——两种都由 `_verify_dimensions` 兜住，
    不会带着错误的维度一路走到写库才失败。
    """
    if provider.model_type != custom_enum.ModelTypeEnum.EMBEDDING.value:
        raise ValueError("传入的不是嵌入模型的配置，无法生成向量")
    return OpenAIEmbeddings(
        model=provider.model_name,
        base_url=provider.base_url,
        api_key=crypto.decrypt(provider.api_key),
        dimensions=models.EMBEDDING_DIMENSIONS,
        # 必须关掉：默认开启时 langchain 会先用 tiktoken 把文本切成 token id 再发，
        # 而 OpenAI 兼容端点（实测 SiliconFlow）要的是原文，会直接返回 400 参数无效。
        check_embedding_ctx_length=False,
        timeout=_timeout(),
        max_retries=1,
    )


def _verify_dimensions(provider, vectors: list):
    """
    维度三方必须一致：嵌入模型的实际输出、`models.EMBEDDING_DIMENSIONS`、
    以及 Qdrant 集合的 size。不一致时在这里就报清楚，而不是等写库时才失败。
    """
    for vector in vectors:
        size = len(vector)
        if size != models.EMBEDDING_DIMENSIONS:
            raise ValueError(
                f"嵌入维度不匹配：模型「{provider.model_name}」返回 {size} 维，"
                f"而系统按 {models.EMBEDDING_DIMENSIONS} 维配置（见 models.EMBEDDING_DIMENSIONS 与 Qdrant 集合的 size）。"
                f"换嵌入模型需同步改这两处并重建全部向量。"
            )


def embed_documents(provider, texts: list) -> list:
    """
    批量嵌入并校验维度
    """
    vectors = build_embeddings(provider).embed_documents(texts)
    _verify_dimensions(provider, vectors)
    return vectors


def embed_query(provider, text: str) -> list:
    """
    单条嵌入并校验维度
    """
    vector = build_embeddings(provider).embed_query(text)
    _verify_dimensions(provider, [vector])
    return vector


def test_connection(provider) -> dict:
    """
    连通性测试：按配置的用途发一次最小请求，不落库任何问答内容
    """
    if provider.model_type == custom_enum.ModelTypeEnum.EMBEDDING.value:
        vector = embed_query(provider, "ping")
        return {"model_name": provider.model_name, "dimensions": len(vector)}

    reply = build_chat_model(provider).invoke("ping")
    content = getattr(reply, "content", "") or ""
    return {"model_name": provider.model_name, "reply_length": len(content)}

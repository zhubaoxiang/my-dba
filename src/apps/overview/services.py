"""
首页总览的聚合

把数据源与知识库两条能力线的状态汇到一处，供首页一次取回。

**分块兜底**：任何一块取数失败时只把那一块标记为不可用，其余照常返回。首页是入口页，
部分数据取不到时整页报错是最糟的结果——使用者连能用的部分也看不到了。取不到的那块
由前端如实标注，**不能伪装成「没有数据」**。

跨模块只读走对方模块声明的服务函数，不直接查其 model（`architecture.md`）。
"""

from apps.datasource import services as datasource_services
from apps.knowledge import llm
from apps.knowledge import services as knowledge_services
from utils.logger import get_logger

LOGGER = get_logger("overview.log")

# 缺哪类模型会影响哪些功能。写在后端而不是前端：这是后端的领域知识，
# 前端各存一份会在加功能时漏改。
_CHAT_AFFECTED = "知识问答、SQL 分析的 AI 解读"
_EMBEDDING_AFFECTED = "知识库检索与文档摄入"


def _readiness() -> dict:
    """
    就绪状态：**只读本地配置**，不发起任何外部调用

    特别是不探测 Qdrant——它的客户端超时取的是 `knowledge_llm_timeout`（默认 60 秒），
    向量服务一挂会把整个首页拖死。服务不可达属于「使用时才暴露」的问题，
    实际调用时已有明确报错，不该由首页承担。
    """
    missing = []
    if llm.active_chat_provider() is None:
        missing.append({"item": "对话模型", "affected": _CHAT_AFFECTED})
    if llm.active_embedding_provider() is None:
        missing.append({"item": "嵌入模型", "affected": _EMBEDDING_AFFECTED})
    return {"available": True, "ready": not missing, "missing": missing}


def _block(name: str, loader, unavailable: dict) -> dict:
    """
    逐块取数：失败只影响这一块，并记日志便于定位
    """
    try:
        return {"available": True, "items": loader()}
    except Exception as exc:  # noqa: BLE001 外部模块的查询失败不该拖垮整个首页
        LOGGER.error("首页总览取数失败 block=%s err=%s", name, exc)
        return unavailable


def build_overview() -> dict:
    """
    组装首页所需的全部数据
    """
    return {
        "readiness": _block("readiness", _readiness, {"available": False, "ready": False, "missing": []}),
        "datasources": _block(
            "datasources",
            datasource_services.list_datasource_status,
            {"available": False, "items": []},
        ),
        "knowledge_bases": _block(
            "knowledge_bases",
            knowledge_services.list_knowledge_base_status,
            {"available": False, "items": []},
        ),
    }

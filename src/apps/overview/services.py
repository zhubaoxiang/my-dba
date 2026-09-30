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
from apps.sqlanalysis import services as sqlanalysis_services
from utils import custom_enum
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
    return {"ready": not missing, "missing": missing}


def _block(name: str, loader, unavailable: dict) -> dict:
    """
    逐块取数：失败只影响这一块，并记日志便于定位

    各块的 `loader` 返回**同样形状的字典**，本函数只负责补上 `available`——
    不能假定各块都是列表：就绪状态是个对象，硬塞进 `items` 会让前端取不到它。
    """
    try:
        return {"available": True, **loader()}
    except Exception as exc:  # noqa: BLE001 外部模块的查询失败不该拖垮整个首页
        LOGGER.error("首页总览取数失败 block=%s err=%s", name, exc)
        return unavailable


def _summary(datasources: dict, metrics: dict, knowledge_bases: dict) -> dict:
    """
    顶部总览数字：由**已经取回的块**算出来，不额外查库

    这样它与下面的卡片必然一致——分别取数的话，两次查询之间的变化会让
    「在线 2/3」和卡片上的状态对不上。

    取不到的项记 `None`（前端显示占位符），**不是 0**：0 是「数过了，一个也没有」，
    与「没数成」是两回事。
    """
    datasource_items = datasources["items"] if datasources["available"] else None
    knowledge_items = knowledge_bases["items"] if knowledge_bases["available"] else None

    issue_total = issue_high_total = None
    if datasource_items is not None:
        # 没有快照的库 issue_counts 是空字典，不会被算进去——「还没有数据」不是「没有问题」
        counts = [item["issue_counts"] or {} for item in datasource_items]
        issue_total = sum(sum(bucket.values()) for bucket in counts)
        issue_high_total = sum(bucket.get(custom_enum.IssueLevelEnum.HIGH.value, 0) for bucket in counts)

    return {
        "datasource_total": len(datasource_items) if datasource_items is not None else None,
        # 指标取不到时线上数量是未知的，不能当成 0 个在线
        "online_total": (sum(1 for item in metrics["items"] if item["is_online"]) if metrics["available"] else None),
        "issue_total": issue_total,
        "issue_high_total": issue_high_total,
        "knowledge_base_total": len(knowledge_items) if knowledge_items is not None else None,
        "document_total": (
            sum(item["document_count"] for item in knowledge_items) if knowledge_items is not None else None
        ),
    }


def _rules() -> dict:
    """
    两条能力线各自的规则条目数

    这是「系统在替使用者检查什么」的规模，不是状态——它只在有人改代码时才变
    （design.md D2）。两个数都由**代码常量**算出、不查库，因此不需要像别处那样
    逐项兜底：要么一起取到，要么一起取不到，由分块兜底整块标为不可用。
    """
    return {
        "datasource_total": datasource_services.rule_counts()["total"],
        "sql_total": sqlanalysis_services.rule_counts()["total"],
    }


def build_overview() -> dict:
    """
    组装首页所需的全部数据

    各块先分别取，再算总览数字——总览数字是它们的汇总，必须排在后面。
    """
    readiness = _block("readiness", _readiness, {"available": False, "ready": False, "missing": []})
    datasources = _block(
        "datasources",
        lambda: {"items": datasource_services.list_datasource_status()},
        {"available": False, "items": []},
    )
    metrics = _block(
        "metrics",
        datasource_services.list_datasource_metrics,
        {"available": False, "items": []},
    )
    knowledge_bases = _block(
        "knowledge_bases",
        lambda: {"items": knowledge_services.list_knowledge_base_status()},
        {"available": False, "items": []},
    )
    rules = _block(
        "rules",
        _rules,
        # 取不到时记 None 而不是 0：0 是「一条规则都没有」，与「没取到」是两回事
        {"available": False, "datasource_total": None, "sql_total": None},
    )

    return {
        "readiness": readiness,
        "summary": _summary(datasources, metrics, knowledge_bases),
        "datasources": datasources,
        "metrics": metrics,
        "knowledge_bases": knowledge_bases,
        "rules": rules,
    }

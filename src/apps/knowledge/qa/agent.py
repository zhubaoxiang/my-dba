"""
问答编排

langchain + langgraph 组成的**单 agent**：模型自主决定调用哪些工具、调用几次。
图本身是线性的（模型 ⇄ 工具循环），复杂度来自工具选择而非编排层级。

三种模式在进入 agent **之前**就把「要不要用知识库」定下来：
- `model_only` 直接问模型
- `knowledge_only` 先检索，无命中就如实告知且**不调模型**（否则等于静默回退）
- `auto` 先检索，无命中才回退到模型，并在结果里标明这次没用到知识库
"""

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langgraph.prebuilt import create_react_agent

from apps.knowledge import llm, retrieval
from apps.knowledge.qa import tools as qa_tools
from utils import custom_enum
from utils.configure import CONF_ATTR
from utils.logger import get_logger

LOGGER = get_logger("knowledge.log")

NO_HIT_MESSAGE = "未在知识库中找到相关内容。可以换一种问法，或先在知识库中补充相关文档。"
FALLBACK_NOTE = "未在知识库中找到相关资料，以下回答来自通用模型，未经文档支撑。"
NO_KB_NOTE = "当前没有可用的知识库，以下回答来自通用模型，未经文档支撑。"
NO_EMBEDDING_MESSAGE = (
    "尚未配置生效中的嵌入模型，无法生成向量，知识库检索不可用。"
    "请在「模型配置」中新增一条「嵌入模型」的配置并设为生效，或改用「仅通用模型」模式提问。"
)
NO_CHAT_MESSAGE = "尚未配置生效中的对话模型，请先在「模型配置」中添加一条「对话模型」的配置并设为生效。"

_SYSTEM_PROMPT = """你是面向开发与测试人员的数据库助手。回答数据库相关的概念、用法、报错与设计问题。

要求：
1. 优先依据工具检索到的资料作答。资料能回答的部分，不要用你自己的记忆替换或补充细节。
2. 引用资料时用 [1]、[2] 这样的编号对应检索结果，不要编造编号之外的来源。
3. 资料没有覆盖的部分，明确说明「资料中未涉及」，再给出你自己的判断，并让使用者知道这是推断。
4. 用中文回答，直接给结论，不要复述问题。
"""

# 不绑定工具时用的提示词。实测 deepseek 在「提示词提到工具、但实际没有工具可调」时，
# 偶尔会把工具调用语法当正文吐出来，因此这两处必须分开。
_PLAIN_SYSTEM_PROMPT = """你是面向开发与测试人员的数据库助手。用中文简洁回答问题，直接给结论。

当前没有可用的资料检索工具，请基于你自己的知识回答；不确定的地方明确说明是推测，
不要编造出处、引用编号，也不要输出任何函数或工具调用语法。
"""

# 兜底剥离：模型偶尔会把工具调用语法当正文返回（已观测到 deepseek 如此）。
# 这类内容对使用者毫无意义，必须清掉再落库与展示。
_TOOL_MARKUP_HINTS = ("invoke", "function_calls", "DSML", "antml", "parameter name=")


class QaError(Exception):
    """
    可预期的问答失败（未配置模型、模式参数不合法等），消息会直接回给使用者
    """


def _int_config(key: str, default: int) -> int:
    try:
        return int(CONF_ATTR.get(key))
    except (TypeError, ValueError):
        return default


def max_steps() -> int:
    return _int_config("knowledge_agent_max_steps", 6)


def history_rounds() -> int:
    return _int_config("knowledge_history_rounds", 6)


def _to_messages(question: str, history: list, system: str = "") -> list:
    """
    组装消息：保留最近若干轮，超出预算的历史直接截断（不做摘要，避免引入额外模型调用）
    """
    messages = [SystemMessage(content=system or _SYSTEM_PROMPT)]
    rounds = history_rounds()
    for item in (history or [])[-rounds * 2 :]:
        content = (item.get("content") or "").strip()
        if not content:
            continue
        if item.get("role") == custom_enum.MessageRoleEnum.USER.value:
            messages.append(HumanMessage(content=content))
        else:
            messages.append(AIMessage(content=content))
    messages.append(HumanMessage(content=question))
    return messages


def message_text(message) -> str:
    """
    取出消息的纯文本。

    langchain 1.x 的 `content` 可能是字符串，也可能是内容块列表（多模态），
    直接当字符串用会在列表上崩掉。
    """
    content = getattr(message, "content", "")
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and isinstance(block.get("text"), str):
                parts.append(block["text"])
        return "\n".join(parts).strip()
    return ""


def _strip_tool_markup(text: str) -> str:
    """
    清掉模型误当正文吐出的工具调用语法。仅在命中特征时才动手，正常回答原样返回。
    """
    if not any(hint in text for hint in _TOOL_MARKUP_HINTS):
        return text
    kept = [
        line
        for line in text.splitlines()
        if not line.strip().startswith("<") and not any(hint in line for hint in _TOOL_MARKUP_HINTS)
    ]
    cleaned = "\n".join(kept).strip()
    return cleaned or "模型返回了无法展示的内容（疑似工具调用语法），请重试。"


def _ask_model(provider, question: str, history: list) -> str:
    """
    不检索、直接问模型。用不含工具语义的提示词——没有工具可调时提到工具会诱发模型编造调用语法。
    """
    reply = llm.build_chat_model(provider).invoke(_to_messages(question, history, system=_PLAIN_SYSTEM_PROMPT))
    return _strip_tool_markup(message_text(reply))


def _presearch(embedding_provider, knowledge_base_id: int, question: str) -> list:
    """
    先检索一次，用来判定「有没有命中」。

    VectorStoreError 会向上抛：向量服务不可用与「没查到」必须区分开，
    否则会被误判成无命中而静默回退到通用模型。
    """
    vector = llm.embed_query(embedding_provider, question)
    return retrieval.retrieve(knowledge_base_id, vector)


def _run_agent(provider, question: str, history: list, tools: list, collected: list) -> tuple:
    """
    跑 agent，返回 (回答文本, 调用过的工具名)
    """
    agent = create_react_agent(llm.build_chat_model(provider), tools, prompt=_SYSTEM_PROMPT)
    steps = max_steps()
    try:
        result = agent.invoke(
            {"messages": _to_messages(question, history, system="")},
            config={"recursion_limit": steps * 2 + 2},
        )
    except Exception as exc:  # noqa: BLE001 含 GraphRecursionError：达到步数上限不该让整轮失败
        LOGGER.warning("agent 执行中断（可能达到步数上限）: %s", exc)
        return f"（本次回答在达到最大工具调用步数前中断：{exc}）", _tool_names(collected)

    messages = result.get("messages") or []
    content = ""
    for message in reversed(messages):
        if isinstance(message, AIMessage):
            text = message_text(message)
            if text:
                content = _strip_tool_markup(text)
                break
    return content or "（模型没有返回内容）", _tool_names(collected)


def _tool_names(collected: list) -> list:
    """
    从收集到的片段反推调用过的工具——目前只有检索类工具会写入 collected
    """
    return ["search_knowledge_base"] if collected else []


def _sources(collected: list) -> list:
    """
    去重后的来源清单。只包含本次**实际检索返回**的片段，不由模型生成。
    """
    seen, result = set(), []
    for hit in collected:
        key = (hit["document_id"], hit["chunk_id"])
        if key in seen:
            continue
        seen.add(key)
        result.append(
            {
                "chunk_id": hit["chunk_id"],
                "document_id": hit["document_id"],
                "document_title": hit["document_title"],
                "source": hit["source"],
                "heading_path": hit["heading_path"],
                "score": hit["score"],
            }
        )
    return result


def answer(
    question: str,
    knowledge_base_id: int = None,
    datasource_id: int = None,
    mode=None,
    history: list = None,
) -> dict:
    """
    回答一个问题，返回 content / sources / tools_used / is_fallback / note
    """
    question = (question or "").strip()
    if not question:
        raise QaError("问题不能为空")
    mode = mode or custom_enum.QaModeEnum.AUTO
    if isinstance(mode, int) and mode not in [m.value for m in custom_enum.QaModeEnum]:
        raise QaError("回答模式不合法")

    chat = llm.active_chat_provider()
    if chat is None:
        raise QaError(NO_CHAT_MESSAGE)
    hist = history or []

    if mode == custom_enum.QaModeEnum.MODEL_ONLY:
        return _result(_ask_model(chat, question, hist), [], [], is_fallback=True, note=FALLBACK_NOTE)

    if not knowledge_base_id:
        if mode == custom_enum.QaModeEnum.KNOWLEDGE_ONLY:
            raise QaError("「仅知识库」模式必须指定知识库")
        return _result(_ask_model(chat, question, hist), [], [], is_fallback=True, note=NO_KB_NOTE)

    # 嵌入模型缺失是配置问题而非服务故障，必须给出可操作的提示，
    # 否则会被当成「服务器错误」而使用者不知道该去改哪里
    embedding = llm.active_embedding_provider()
    if embedding is None:
        raise QaError(NO_EMBEDDING_MESSAGE)

    hits = _presearch(embedding, knowledge_base_id, question)
    if not hits:
        if mode == custom_enum.QaModeEnum.KNOWLEDGE_ONLY:
            return _result(NO_HIT_MESSAGE, [], [], is_fallback=False)
        return _result(_ask_model(chat, question, hist), [], [], is_fallback=True, note=FALLBACK_NOTE)

    collected = []
    if mode == custom_enum.QaModeEnum.KNOWLEDGE_ONLY:
        tools = qa_tools.build_tools(knowledge_base_id=knowledge_base_id, collected=collected)
    else:
        tools = qa_tools.build_tools(
            knowledge_base_id=knowledge_base_id, datasource_id=datasource_id, collected=collected
        )
    if not tools:
        return _result(_ask_model(chat, question, hist), [], [], is_fallback=True, note=FALLBACK_NOTE)

    content, used = _run_agent(chat, question, hist, tools, collected)
    return _result(content, _sources(collected), used, is_fallback=False)


def _result(content: str, sources: list, tools_used: list, is_fallback: bool, note: str = "") -> dict:
    return {
        "content": content,
        "sources": sources,
        "tools_used": tools_used,
        "is_fallback": is_fallback,
        "note": note,
    }

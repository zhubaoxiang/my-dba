"""
问答编排

langchain + langgraph 组成的**单 agent**：模型自主决定调用哪些工具、调用几次。
图本身是线性的（模型 ⇄ 工具循环），复杂度来自工具选择而非编排层级。

三种模式在进入 agent **之前**就把「要不要用知识库」定下来：
- `model_only` 直接问模型
- `knowledge_only` 先检索，无命中就如实告知且**不调模型**（否则等于静默回退）
- `auto` 先检索，无命中才回退到模型，并在结果里标明这次没用到知识库

编排分两层，同步与流式**共用**（见 design.md D9）：

1. `prepare_stream()` / `_prepare()` —— 预检与预检索，**不产出任何内容**，可预期失败直接抛
2. `answer()`（同步，一次性返回）与 `stream_events()`（流式，逐块产出）

两条路径共用同一套模式判定，否则「回退必须标注」「服务故障 ≠ 无命中」这类约束
会在两条路径上各写一遍并逐渐漂移。
"""

import dataclasses

from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage, SystemMessage
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

# 模型什么都没返回，与「返回了但全是不可展示内容」是两回事，分开报
NO_CONTENT_MESSAGE = "（模型没有返回内容）"
MARKUP_FALLBACK_MESSAGE = "模型返回了无法展示的内容（疑似工具调用语法），请重试。"
INTERRUPTED_TEMPLATE = "（本次回答在达到最大工具调用步数前中断：{}）"

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

# 执行路径
_PATH_MODEL_ONLY = "model_only"
_PATH_NO_KB = "no_kb"
_PATH_AGENT = "agent"


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


def content_text(message) -> str:
    """
    取出消息的文本，**不做 strip**

    langchain 1.x 的 `content` 可能是字符串，也可能是内容块列表（多模态），
    直接当字符串用会在列表上崩掉。

    流式路径必须用这个而不是 `message_text()`：`_LineBuffer` 靠换行切分块，
    逐块 strip 会把行边界抹掉，导致整篇回答被并成一行、任何一个标记字样都能
    把全文连坐丢弃。
    """
    content = getattr(message, "content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and isinstance(block.get("text"), str):
                parts.append(block["text"])
        return "\n".join(parts)
    return ""


def message_text(message) -> str:
    """
    取出消息的纯文本（已 strip），非流式路径用这个
    """
    return content_text(message).strip()


# ----------------------------------------------------------------------
# 工具调用语法的剥离（同步与流式共用同一套逐行判定）
# ----------------------------------------------------------------------


def _is_markup_line(line: str) -> bool:
    """
    判定一行是否属于模型误当正文吐出的工具调用语法
    """
    return line.strip().startswith("<") or any(hint in line for hint in _TOOL_MARKUP_HINTS)


def _strip_tool_markup(text: str) -> str:
    """
    清掉模型误当正文吐出的工具调用语法。仅在命中特征时才动手，正常回答原样返回。
    """
    if not any(hint in text for hint in _TOOL_MARKUP_HINTS):
        return text
    kept = [line for line in text.splitlines() if not _is_markup_line(line)]
    cleaned = "\n".join(kept).strip()
    return cleaned or MARKUP_FALLBACK_MESSAGE


class _LineBuffer:
    """
    流式路径上的逐行剥离缓冲

    `_strip_tool_markup()` 是整段文本的后处理，无法作用于实时 token 流；直接放弃则会让
    「模型把工具调用语法当正文吐出」这个实测缺陷在流式路径上复活。因此改为**只放行到
    最后一个换行为止的完整行**，残余留在缓冲里等后续分片补齐。

    代价是输出最多滞后一行，换来的是与非流式一致的保证。
    """

    def __init__(self):
        self._buf = ""
        self.raw_len = 0
        self.has_content = False

    def feed(self, text: str) -> str:
        """
        送入一个分片，返回本次可放行的文本（可能为空）
        """
        if not text:
            return ""
        self.raw_len += len(text)
        self._buf += text
        cut = self._buf.rfind("\n")
        if cut < 0:
            return ""
        complete, self._buf = self._buf[: cut + 1], self._buf[cut + 1 :]
        return self._keep(complete)

    def flush(self) -> str:
        """
        流结束，放行残余
        """
        rest, self._buf = self._buf, ""
        return self._keep(rest)

    def _keep(self, chunk: str) -> str:
        if not chunk:
            return ""
        kept = "\n".join(line for line in chunk.split("\n") if not _is_markup_line(line))
        if kept.strip():
            self.has_content = True
        return kept


def _tail_events(buffer: _LineBuffer):
    """
    流结束时的兜底：一个字都没能放行时给出可读文案

    区分「模型没返回」与「返回了但全是不可展示内容」，与同步路径的判定一致。
    """
    if buffer.has_content:
        return
    yield _ev_delta(MARKUP_FALLBACK_MESSAGE if buffer.raw_len else NO_CONTENT_MESSAGE)


# ----------------------------------------------------------------------
# 事件
# ----------------------------------------------------------------------


def _ev_status(text: str) -> dict:
    return {"type": "status", "text": text}


def _ev_delta(text: str) -> dict:
    return {"type": "delta", "text": text}


def _done_event(prep: "_Prep", collected: list, is_fallback: bool, note: str = "") -> dict:
    return {
        "type": "done",
        "sources": _sources(collected),
        "tools_used": _tool_names(collected),
        "is_fallback": is_fallback,
        "note": note,
        "mode": prep.mode,
    }


# ----------------------------------------------------------------------
# 预检与路径判定
# ----------------------------------------------------------------------


@dataclasses.dataclass
class _Prep:
    """
    一次提问的执行计划：预检结论 + 走哪条路径
    """

    path: str
    mode: int
    chat: object
    history: list
    knowledge_base_id: int = None
    datasource_id: int = None
    embedding: object = None
    hits: list = dataclasses.field(default_factory=list)


def _normalize_mode(mode) -> int:
    if mode is None:
        return custom_enum.QaModeEnum.AUTO.value
    try:
        value = int(mode)
    except (TypeError, ValueError):
        raise QaError("回答模式不合法") from None
    if value not in custom_enum.QaModeEnum.values:
        raise QaError("回答模式不合法")
    return value


def _prepare(question: str, knowledge_base_id=None, datasource_id=None, mode=None, history=None) -> _Prep:
    """
    预检并决定执行路径。可预期的失败抛 QaError。

    本函数**不访问外部服务**，因此同步与流式都能安全地先跑它。
    """
    question = (question or "").strip()
    if not question:
        raise QaError("问题不能为空")

    mode = _normalize_mode(mode)
    chat = llm.active_chat_provider()
    if chat is None:
        raise QaError(NO_CHAT_MESSAGE)

    base = {
        "mode": mode,
        "chat": chat,
        "history": history or [],
        "knowledge_base_id": knowledge_base_id,
        "datasource_id": datasource_id,
    }

    if mode == custom_enum.QaModeEnum.MODEL_ONLY.value:
        return _Prep(path=_PATH_MODEL_ONLY, **base)
    if not knowledge_base_id:
        if mode == custom_enum.QaModeEnum.KNOWLEDGE_ONLY.value:
            raise QaError("「仅知识库」模式必须指定知识库")
        return _Prep(path=_PATH_NO_KB, **base)

    # 嵌入模型缺失是配置问题而非服务故障，必须给出可操作的提示，
    # 否则会被当成「服务器错误」而使用者不知道该去改哪里
    embedding = llm.active_embedding_provider()
    if embedding is None:
        raise QaError(NO_EMBEDDING_MESSAGE)
    return _Prep(path=_PATH_AGENT, embedding=embedding, **base)


def _presearch(embedding_provider, knowledge_base_id: int, question: str) -> list:
    """
    先检索一次，用来判定「有没有命中」。

    VectorStoreError 会向上抛：向量服务不可用与「没查到」必须区分开，
    否则会被误判成无命中而静默回退到通用模型。
    """
    vector = llm.embed_query(embedding_provider, question)
    return retrieval.retrieve(knowledge_base_id, vector)


def _decide_after_hits(prep: _Prep, hits: list) -> str:
    """
    预检索之后的去向：进 agent / 如实告知无命中 / 回退通用模型

    抽成独立函数是为了让同步与流式走同一套判定，「无命中不得静默回退」只需在一处保证。
    """
    if hits:
        return "agent"
    if prep.mode == custom_enum.QaModeEnum.KNOWLEDGE_ONLY.value:
        return "no_hit"
    return "fallback"


def _agent_tools(prep: _Prep, collected: list) -> list:
    """
    构造工具集

    「仅知识库」模式不挂表结构工具——该模式承诺只用知识库作答。
    """
    datasource_id = None if prep.mode == custom_enum.QaModeEnum.KNOWLEDGE_ONLY.value else prep.datasource_id
    return qa_tools.build_tools(
        knowledge_base_id=prep.knowledge_base_id, datasource_id=datasource_id, collected=collected
    )


# ----------------------------------------------------------------------
# 同步执行
# ----------------------------------------------------------------------


def _ask_model(provider, question: str, history: list) -> str:
    """
    不检索、直接问模型。用不含工具语义的提示词——没有工具可调时提到工具会诱发模型编造调用语法。
    """
    reply = llm.build_chat_model(provider).invoke(_to_messages(question, history, system=_PLAIN_SYSTEM_PROMPT))
    return _strip_tool_markup(message_text(reply))


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
        return INTERRUPTED_TEMPLATE.format(exc), _tool_names(collected)

    messages = result.get("messages") or []
    content = ""
    for message in reversed(messages):
        if isinstance(message, AIMessage):
            text = message_text(message)
            if text:
                content = _strip_tool_markup(text)
                break
    return content or NO_CONTENT_MESSAGE, _tool_names(collected)


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


def _result(content: str, sources: list, tools_used: list, is_fallback: bool, note: str = "") -> dict:
    return {
        "content": content,
        "sources": sources,
        "tools_used": tools_used,
        "is_fallback": is_fallback,
        "note": note,
    }


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
    prep = _prepare(question, knowledge_base_id, datasource_id, mode, history)
    question = (question or "").strip()

    if prep.path == _PATH_MODEL_ONLY:
        return _result(_ask_model(prep.chat, question, prep.history), [], [], True, FALLBACK_NOTE)
    if prep.path == _PATH_NO_KB:
        return _result(_ask_model(prep.chat, question, prep.history), [], [], True, NO_KB_NOTE)

    hits = _presearch(prep.embedding, prep.knowledge_base_id, question)
    decision = _decide_after_hits(prep, hits)
    if decision == "no_hit":
        return _result(NO_HIT_MESSAGE, [], [], False)
    if decision == "fallback":
        return _result(_ask_model(prep.chat, question, prep.history), [], [], True, FALLBACK_NOTE)

    collected = []
    tools = _agent_tools(prep, collected)
    if not tools:
        return _result(_ask_model(prep.chat, question, prep.history), [], [], True, FALLBACK_NOTE)

    content, used = _run_agent(prep.chat, question, prep.history, tools, collected)
    return _result(content, _sources(collected), used, False)


# ----------------------------------------------------------------------
# 流式执行
# ----------------------------------------------------------------------


def prepare_stream(
    question: str,
    knowledge_base_id: int = None,
    datasource_id: int = None,
    mode=None,
    history: list = None,
) -> _Prep:
    """
    流式提问在**写出第一个字节之前**要做的全部判定：预检 + 预检索。

    这两步都要访问外部服务，失败必须还能改回统一格式的 JSON 错误——一旦开始推流，
    HTTP 状态码就已经发出去了，之后出错只能走流内错误事件（见 design.md D6）。
    因此本函数刻意不是生成器：生成器的函数体要到首次迭代才执行，那时响应头已发出。
    """
    prep = _prepare(question, knowledge_base_id, datasource_id, mode, history)
    if prep.path == _PATH_AGENT:
        # VectorStoreError 在此向上抛：视图据此返回 5000，而不是让它看起来像「无命中」
        prep.hits = _presearch(prep.embedding, prep.knowledge_base_id, (question or "").strip())
    return prep


def _is_answer_token(chunk, metadata) -> bool:
    """
    判定一个分片是否属于**最终答案**的正文

    三个条件缺一不可。react agent 的中间轮次产出的同样是 `AIMessageChunk`，
    若不排除「带工具调用」的分片，**模型为调用工具而生成的参数会被当成答案推给使用者**。
    """
    if metadata.get("langgraph_node") != "agent":
        return False
    if not isinstance(chunk, AIMessageChunk):
        return False
    if getattr(chunk, "tool_call_chunks", None):
        return False
    if getattr(chunk, "tool_calls", None):
        return False
    return True


def _tool_names_in(chunk, metadata) -> list:
    """
    取出模型**正在发起**的工具名

    模型先把工具调用的参数流式吐出来，图随后才执行工具，因此据它给出的进度提示
    出现在工具执行**之前**——这正是「工具阶段可见」所需要的。若改为在工具函数内部
    上报，提示要等工具跑完、模型吐出下一个分片时才能送达，等于没有提示。
    """
    if metadata.get("langgraph_node") != "agent":
        return []
    names = []
    for item in getattr(chunk, "tool_call_chunks", None) or []:
        name = item.get("name") if isinstance(item, dict) else getattr(item, "name", None)
        if name:
            names.append(name)
    return names


def _stream_plain(prep: _Prep, question: str, note: str):
    """
    不检索、直接问模型；流式版本。用不含工具语义的提示词，理由同 `_ask_model`。
    """
    buffer = _LineBuffer()
    chunks = llm.build_chat_model(prep.chat).stream(_to_messages(question, prep.history, system=_PLAIN_SYSTEM_PROMPT))
    for chunk in chunks:
        out = buffer.feed(content_text(chunk))
        if out:
            yield _ev_delta(out)
    tail = buffer.flush()
    if tail:
        yield _ev_delta(tail)
    yield from _tail_events(buffer)
    yield _done_event(prep, [], is_fallback=True, note=note)


def _stream_agent(prep: _Prep, question: str, tools: list, collected: list):
    """
    跑 agent 并逐块产出答案；工具进度在模型发起调用时给出
    """
    agent = create_react_agent(llm.build_chat_model(prep.chat), tools, prompt=_SYSTEM_PROMPT)
    steps = max_steps()
    buffer = _LineBuffer()
    announced = set()

    try:
        stream = agent.stream(
            {"messages": _to_messages(question, prep.history, system="")},
            config={"recursion_limit": steps * 2 + 2},
            stream_mode="messages",
        )
        for chunk, metadata in stream:
            for name in _tool_names_in(chunk, metadata):
                if name in announced:
                    continue
                announced.add(name)
                yield _ev_status(qa_tools.status_text(name))
            if not _is_answer_token(chunk, metadata):
                continue
            out = buffer.feed(content_text(chunk))
            if out:
                yield _ev_delta(out)
    except Exception as exc:  # noqa: BLE001 含 GraphRecursionError：达到步数上限不该让整轮失败
        LOGGER.warning("流式回答中断（可能达到步数上限）: %s", exc)
        # 已经吐出去的内容收不回来，只能把中断说明接在后面；与同步路径给同一句话
        prefix = "\n\n" if buffer.has_content else ""
        yield _ev_delta(f"{prefix}{INTERRUPTED_TEMPLATE.format(exc)}")
        yield _done_event(prep, collected, is_fallback=False)
        return

    tail = buffer.flush()
    if tail:
        yield _ev_delta(tail)
    yield from _tail_events(buffer)
    yield _done_event(prep, collected, is_fallback=False)


def stream_events(prep: _Prep, question: str):
    """
    产出流式事件：`status` / `delta` / `done`

    调用前必须先跑 `prepare_stream()`——它已把「开流前可判定」的失败全部抛掉，
    因此本函数内不会再出现配置类错误。开流之后才发生的异常由调用方转成错误事件。
    """
    question = (question or "").strip()

    if prep.path == _PATH_MODEL_ONLY:
        yield from _stream_plain(prep, question, FALLBACK_NOTE)
        return
    if prep.path == _PATH_NO_KB:
        yield from _stream_plain(prep, question, NO_KB_NOTE)
        return

    decision = _decide_after_hits(prep, prep.hits)
    if decision == "no_hit":
        # 如实告知，不调模型——「仅知识库」模式不得静默改用通用模型
        yield _ev_delta(NO_HIT_MESSAGE)
        yield _done_event(prep, [], is_fallback=False)
        return
    if decision == "fallback":
        yield from _stream_plain(prep, question, FALLBACK_NOTE)
        return

    collected = []
    tools = _agent_tools(prep, collected)
    if not tools:
        yield from _stream_plain(prep, question, FALLBACK_NOTE)
        return
    yield from _stream_agent(prep, question, tools, collected)

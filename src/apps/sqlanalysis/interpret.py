"""
大模型解读

解读**规则已命中的问题**，并可补充规则未覆盖的观察（design.md D5）。三者关系：

```
issues[]       规则判定     确定、可复现，同一输入必得同一结果
explanations[] 模型解读     针对上面每一条讲清「为什么」与「怎么改」
observations[] 模型推测     规则没覆盖到的观察，**未经规则验证**
```

`observations` 单独成字段并带固定标注，前端照此分区渲染——不让模型推测有机会混进规则结论。
这与知识问答里「回退不得静默」「来源不许编造」是同一类约束：使用者不能误判结论的来源与确定性。

未配置对话模型时，规则清单照常返回，这里给出可操作提示，**不静默降级**。
"""

import json
import re

from apps.knowledge import llm
from utils import custom_enum
from utils.logger import get_logger

LOGGER = get_logger("sqlanalysis.log")

NO_CHAT_MODEL_MESSAGE = (
    "尚未配置生效中的对话模型，本次没有大模型解读（规则判定部分不受影响）。"
    "请在「模型配置」中添加一条「对话模型」的配置并设为生效。"
)

# 模型推测的固定标注。放在响应里而不是只写在前端，后端也要能自证这条边界
OBSERVATIONS_NOTE = "以下为模型推测，未经规则验证，请自行判断后再采纳。"

_LEVEL_LABEL = {
    custom_enum.IssueLevelEnum.HIGH.value: "高",
    custom_enum.IssueLevelEnum.MEDIUM.value: "中",
    custom_enum.IssueLevelEnum.LOW.value: "低",
}

# 方言标签：喂给模型，避免它拿另一种方言的规则来评判（实测踩到过）
_DIALECT_LABEL = {
    custom_enum.DbTypeEnum.POSTGRESQL.value: "PostgreSQL",
    custom_enum.DbTypeEnum.MYSQL.value: "MySQL",
}

_SYSTEM_PROMPT = """你是数据库专家，负责解读 SQL 静态分析的结果。

**必须按用户给出的目标数据库方言来评判**，不要把另一种方言的写法套过来。最容易搞错的几处：
- 标识符引用：PostgreSQL 用双引号 "col"，MySQL 用反引号 `col`；用错的那个在对方方言里
  根本不是合法语法，不要建议使用者改成另一种
- 字符串字面量一律用单引号，两种方言都一样
- 分页：PostgreSQL 用 LIMIT/OFFSET，MySQL 用 LIMIT offset, size

如果某处写法在**目标方言下本来就是正确的**，就不要把它说成问题。

严格按下面三件事做：
1. **先给总体判断**，放进 summary：这条 SQL 有没有明显问题、能不能直接执行、优先改哪一处。
   两三句话，直接给结论，**不要复述问题清单**；没问题就直说没问题。
2. 对给出的**每一条**问题，用一两句话说明「为什么是问题」以及「具体怎么改」，放进 explanations。
   rule_code 用给出的规则标识（也可用规则名）。
3. 如果你发现了规则**没有覆盖到**的其他风险，放进 observations；没有就留空数组。
   不要为了凑数而编造。

只输出 JSON，不要任何解释性文字、不要代码块之外的任何内容。格式：
{"summary": "总体判断与建议", "explanations": [{"rule_code": "规则标识", "text": "解读"}], "observations": ["观察"]}
"""


class InterpretError(Exception):
    """
    可预期的解读失败
    """


def _reply_text(reply) -> str:
    """
    取模型回复的纯文本

    langchain 1.x 的 content 可能是字符串也可能是内容块列表，直接当字符串用会在列表上崩掉。
    """
    content = getattr(reply, "content", "")
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


def _extract_json(text: str) -> dict:
    """
    从模型回复里取出 JSON 对象

    模型常把 JSON 包在代码块里或前后带一句话，因此退而求其次：截取第一个 `{` 到最后一个 `}`。
    """
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```[a-zA-Z]*\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start < 0 or end <= start:
        raise InterpretError("模型输出里没有 JSON 对象")
    try:
        return json.loads(cleaned[start : end + 1])
    except json.JSONDecodeError as exc:
        raise InterpretError(f"模型输出的 JSON 无法解析：{exc}") from exc


def _code_index(issues: list) -> dict:
    """
    本次问题清单里可接受的「指代」→ 规则 code

    **code 与规则名都认**：提示词里两者都出现了（`- [中] select_star（使用 SELECT *）`），
    模型回显中文名是完全合理的，只认 code 会把它的解读整批丢掉。
    """
    index = {}
    for item in issues:
        code = str(item["rule_code"]).strip()
        index[code.lower()] = code
        name = str(item.get("rule_name") or "").strip()
        if name:
            index.setdefault(name.lower(), code)
    return index


def _normalize(payload: dict, issues: list) -> tuple:
    """
    规整模型输出，返回 (解读, 推测, 被丢弃的解读条数)

    **只接受能对应到本次问题清单的解读**——模型若报了一个不存在的规则，那就是编造的出处，
    与知识问答里「来源必须对得上实际检索结果」是同一条约束。

    「丢弃」要计数并回传：界面上必须能区分「规则没判出问题」与「模型有输出但没对上」，
    否则前者的话术会把后者的故障盖住。
    """
    index = _code_index(issues)
    raw_items = payload.get("explanations")
    # 兼容模型把 explanations 写成 {code: text} 的字典形式
    if isinstance(raw_items, dict):
        raw_items = [{"rule_code": key, "text": value} for key, value in raw_items.items()]

    explanations, dropped, seen = [], 0, set()
    for item in raw_items or []:
        if not isinstance(item, dict):
            dropped += 1
            continue
        key = str(item.get("rule_code") or item.get("code") or item.get("rule") or "").strip()
        text = str(item.get("text") or item.get("explanation") or "").strip()
        code = index.get(key.lower())
        if not code or not text or code in seen:
            dropped += 1
            continue
        seen.add(code)
        explanations.append({"rule_code": code, "text": text})

    observations = []
    for item in payload.get("observations") or []:
        text = str(item).strip()
        if text:
            observations.append(text)

    summary = str(payload.get("summary") or "").strip()
    return explanations, observations, dropped, summary


def _dialect_label(dialect) -> str:
    """
    方言标签；认不出来就说「未知」，并让模型别乱套另一种方言的规则
    """
    try:
        return _DIALECT_LABEL.get(int(dialect), "") if dialect is not None else ""
    except (TypeError, ValueError):
        return ""


def _build_messages(sql: str, issues: list, context_note: str = "", dialect_label: str = "") -> list:
    from langchain_core.messages import HumanMessage, SystemMessage

    lines = [
        f"- [{_LEVEL_LABEL.get(item['issue_level'], '?')}] {item['rule_code']}"
        f"（{item['target'] or '整条语句'}）：{item['description']}"
        for item in issues
    ]
    body = "规则命中的问题：\n" + ("\n".join(lines) if lines else "（无，规则未判出问题）")
    # 方言必须告诉模型：不说的话它会自己猜，实测猜成 MySQL 后把 PostgreSQL 里完全正确的
    # 双引号标识符说成「错误写法」，并建议改成 PG 根本不支持的反引号
    header = f"目标数据库：{dialect_label}\n\n" if dialect_label else ""
    extra = f"\n\n补充信息：{context_note}" if context_note else ""
    return [
        SystemMessage(content=_SYSTEM_PROMPT),
        HumanMessage(content=f"{header}SQL：\n{sql}\n\n{body}{extra}"),
    ]


def _unavailable(note: str) -> dict:
    """
    降级结果。字段与正常返回**完全一致**，前端不必到处判空
    """
    return {
        "available": False,
        "note": note,
        "summary": "",
        "explanations": [],
        "observations": [],
        "observations_note": "",
        "dropped_explanations": 0,
    }


def interpret(sql: str, issues: list, dialect=None, context_note: str = "") -> dict:
    """
    解读规则命中的问题，并可补充规则之外的观察

    :param context_note: 附带给模型的补充信息，例如「解析失败，语法错误在 …」。
        SQL 解析不了时规则一条都跑不了，而模型对语法错误往往**最有帮助**（design.md D9）。

    任何失败都降级为「本次没有解读」并说明原因，**不影响规则判定部分的返回**。
    """
    provider = llm.active_chat_provider()
    if provider is None:
        return _unavailable(NO_CHAT_MODEL_MESSAGE)

    try:
        reply = llm.build_chat_model(provider).invoke(
            _build_messages(sql, issues, context_note, _dialect_label(dialect))
        )
        payload = _extract_json(_reply_text(reply))
        explanations, observations, dropped, summary = _normalize(payload, issues)
        if dropped:
            # 回原始输出便于定位：模型用了什么键名、写了什么指代，只有原文能说清
            LOGGER.warning("模型解读有 %s 条无法对应到问题清单，原始输出：%s", dropped, _reply_text(reply)[:1000])
    except InterpretError as exc:
        LOGGER.warning("模型解读输出异常: %s", exc)
        return _unavailable(f"大模型解读未能完成：{exc}")
    except Exception as exc:  # noqa: BLE001 外部模型调用属边界
        LOGGER.warning("模型解读失败: %s", exc)
        return _unavailable(f"大模型解读失败：{exc}")

    return {
        "available": True,
        "note": "",
        "summary": summary,
        "explanations": explanations,
        "observations": observations,
        # 只要给出了推测就带上标注；没有推测则不必显示
        "observations_note": OBSERVATIONS_NOTE if observations else "",
        # 让界面能区分「规则没判出问题」与「模型有输出但没对上」
        "dropped_explanations": dropped,
    }

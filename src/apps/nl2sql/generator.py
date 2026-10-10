"""
自然语言 → 查询语句的生成

只做三件事：**渲染表结构文本、拼提示词、调模型并抽出 SQL**。编排（取结构、校验、
自修、组装响应）在 `services.py`——分开是为了让提示词与输出解析能脱离数据库单测。

模型**只负责生成**，不负责判断生成得对不对：判定由 `apps/sqlanalysis` 的规则承担
（design.md D2）。这条分开得很硬，别让模型在提示词里被要求「自查」。
"""

import re

from langchain_core.messages import HumanMessage, SystemMessage

from apps.knowledge import llm

_SYSTEM_PROMPT = """你是数据库专家，负责按使用者的描述写出一条 SQL 语句。

**只输出一条 SQL，不要解释、不要代码块标记、不要分号之后的任何内容。**

硬性约束：
- 只能使用下面给出的表与列。**不要凭空造表名或列名**——库里没有的东西写出来就是错的。
- **如果给出的结构不足以表达这个需求**（要查的东西不在里面），**不要挑一张相近的表硬写**——
  那等于在一条看起来完全正常的 SQL 里回答了另一个问题，使用者分辨不出来。这种情况**只回复**：
  NOT_ENOUGH_SCHEMA
- **使用者要什么就给什么**：要查询就给 SELECT，要改数据就给 UPDATE / DELETE / INSERT，
  要改结构就给 DDL。**不要**因为「这条语句会被执行」而把修改需求改写成一条 SELECT——
  它不会被自动执行，硬写成查询只会让使用者以为你按他说的做了。
- 只写**一条**语句。就是要改多张表也不要拆成多条。
- 按目标数据库的方言写。最容易搞错的是标识符引用：PostgreSQL 用双引号，MySQL 用反引号。
"""

_REPAIR_SYSTEM_PROMPT = """你是数据库专家。你上一次写的语句有结构问题，请修正后重新给出。

**只输出修正后的那一条 SQL**，不要解释、不要代码块标记、不要复述问题。

只修改被指出的问题，其余部分保持不变——**不要把本来正确的地方一并改掉**，也不要借机
改变语句的类型（SELECT 就还是 SELECT，UPDATE 就还是 UPDATE）。
仍然只能使用下面给出的表与列。
"""

_DIALECT_NOTE = {
    "postgresql": "目标方言是 **PostgreSQL**：标识符需引用时用双引号。",
    "mysql": "目标方言是 **MySQL**：标识符需引用时用反引号。",
}


# 结构不足时模型按约定回这个标记。**必须比「返回里有没有 SQL」先判**——否则这个标记
# 会被当成一条 SQL 送进解析器，报一个使用者看不懂的语法错误
_SCHEMA_INSUFFICIENT = "NOT_ENOUGH_SCHEMA"


class GenerateError(Exception):
    """
    可预期的生成失败（模型不可用、调用超时、输出里没有可用语句）
    """


class SchemaInsufficient(GenerateError):
    """
    模型判定给出的表结构表达不了这个需求

    单独一个类型而不是笼统的 `GenerateError`：调用方要给的**处置完全不同**——这不是失败，
    是「给的范围不对」，要提示使用者扩大表范围，而不是说「生成失败，请重试」。
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


def extract_sql(text: str) -> str:
    """
    从模型回复里取出 SQL

    模型常把 SQL 包在代码块里，或前后带一句解释。这里剥掉代码块标记与结尾分号。
    **不在这里判断「是不是多条语句」**——那是解析器的事，且判断错了代价更高
    （字符串字面量里也可能有分号）。多语句会由调用方按解析结果如实拒绝。
    """
    cleaned = (text or "").strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```[a-zA-Z]*\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    cleaned = cleaned.strip().rstrip(";").strip()
    if _SCHEMA_INSUFFICIENT in cleaned.upper():
        raise SchemaInsufficient("模型判定现有表结构不足以表达该需求")
    if not cleaned:
        raise GenerateError("模型未返回内容")
    return cleaned


def build_structure_text(tables: list) -> str:
    """
    把表结构渲染成提示词里的紧凑文本

    只保留「写对这条查询」所需要的：表名、说明、列名、类型、是否可空、主键、外键。
    **不塞行数之类的统计**——它们与写查询无关，只会白占上下文。
    """
    blocks = []
    for table in tables:
        name = table.get("name") or ""
        schema = table.get("schema") or ""
        qualified = f"{schema}.{name}" if schema and schema != "public" else name
        comment = (table.get("comment") or "").strip()
        lines = [f"表 {qualified}" + (f"（{comment}）" if comment else "")]

        primary_key = set(table.get("primary_key") or [])
        for column in table.get("columns") or []:
            marks = []
            if column.get("name") in primary_key:
                marks.append("主键")
            suffix = f" [{' '.join(marks)}]" if marks else ""
            column_comment = (column.get("comment") or "").strip()
            note = f"  -- {column_comment}" if column_comment else ""
            lines.append(f"  {column.get('name')} {column.get('data_type')}{suffix}{note}")

        for foreign_key in table.get("foreign_keys") or []:
            ref_table = foreign_key.get("ref_table") or ""
            columns = ", ".join(foreign_key.get("columns") or [])
            if ref_table and columns:
                lines.append(f"  外键：{columns} -> {ref_table}")

        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def _dialect_note(dialect_label: str) -> str:
    return _DIALECT_NOTE.get((dialect_label or "").strip().lower(), "")


def _user_prompt(structure_text: str, question: str, dialect_label: str) -> str:
    parts = [f"可用的表结构：\n\n{structure_text}", f"使用者的需求：{question}"]
    note = _dialect_note(dialect_label)
    if note:
        parts.append(note)
    parts.append("请给出这一条查询语句。")
    return "\n\n".join(parts)


def generate(provider, structure_text: str, question: str, dialect_label: str = "") -> str:
    """
    生成一条查询语句

    任何失败都抛 `GenerateError`，由调用方如实呈现——**不返回半成品**。
    """
    messages = [
        SystemMessage(content=_SYSTEM_PROMPT),
        HumanMessage(content=_user_prompt(structure_text, question, dialect_label)),
    ]
    try:
        reply = llm.build_chat_model(provider).invoke(messages)
    except Exception as exc:  # noqa: BLE001 外部模型调用属边界
        raise GenerateError(f"模型调用失败：{exc}") from exc
    return extract_sql(_reply_text(reply))


def repair(provider, structure_text: str, question: str, sql: str, problems: list, dialect_label: str = "") -> str:
    """
    带着结构问题重新生成一次

    `problems` 是规则报出的问题项（含 `target` 与 `description`），原样给模型——
    转述会丢信息，而模型需要知道**具体是哪个表/列**不存在。
    """
    lines = ["你上一次生成的语句是：", sql, "", "它有以下结构问题："]
    for item in problems:
        target = item.get("target") or ""
        description = item.get("description") or ""
        lines.append(f"- {target}：{description}" if target else f"- {description}")
    lines.append("")
    lines.append(_user_prompt(structure_text, question, dialect_label))
    lines.append("请只输出修正后的那一条 SQL。")

    messages = [
        SystemMessage(content=_REPAIR_SYSTEM_PROMPT),
        HumanMessage(content="\n".join(lines)),
    ]
    try:
        reply = llm.build_chat_model(provider).invoke(messages)
    except Exception as exc:  # noqa: BLE001 同上
        raise GenerateError(f"模型调用失败：{exc}") from exc
    return extract_sql(_reply_text(reply))

"""
agent 可调用的工具

工具按请求构造（知识库与数据源随请求而变），并通过闭包收集「本次实际检索到的片段」——
回答里给出的来源必须对得上实际检索结果，不允许由模型自行编造。
"""

from langchain_core.tools import tool

from apps.datasource import services as datasource_services
from apps.knowledge import llm, retrieval

# 工具回给模型的内容上限，避免一次检索把上下文塞满
_MAX_SNIPPET_CHARS = 700
_MAX_TABLE_CHARS = 1200

# 工具名 → 面向使用者的进度文案。
#
# 流式回答在**模型开始发起工具调用时**就据它给出提示，因此提示出现在工具执行之前，
# 使用者不会对着空白等待。文案 MUST NOT 包含入参或工具返回内容。
TOOL_STATUS = {
    "search_knowledge_base": "正在检索知识库…",
    "get_table_schema": "正在查询表结构…",
}


def status_text(tool_name: str) -> str:
    """
    取某个工具的进度文案；未登记的工具给一个通用说法，不暴露内部细节
    """
    return TOOL_STATUS.get(tool_name) or "正在查询…"


def build_tools(knowledge_base_id: int | None = None, datasource_id: int | None = None, collected: list | None = None):
    """
    构造工具集。collected 用于收集本次实际检索到的片段，作为回答的来源依据。
    """
    collected = collected if collected is not None else []
    tools = []

    if knowledge_base_id:

        @tool
        def search_knowledge_base(query: str) -> str:
            """检索知识库中与查询相关的资料片段。问及概念、用法、报错、规范时先用它。"""
            provider = llm.active_embedding_provider()
            if provider is None:
                return "检索失败：尚未配置生效中的嵌入模型，无法生成查询向量。"
            vector = llm.embed_query(provider, query)
            try:
                hits = retrieval.retrieve(knowledge_base_id, vector)
            except Exception as exc:  # noqa: BLE001 工具失败要回给模型而不是中断整轮
                return f"检索失败：{exc}"

            if not hits:
                return "知识库中没有找到相关内容。"
            for hit in hits:
                collected.append(hit)
            return "\n\n".join(
                f"[{index}] 出自《{hit['document_title']}》{('· ' + hit['heading_path']) if hit['heading_path'] else ''}\n"
                f"{hit['content'][:_MAX_SNIPPET_CHARS]}"
                for index, hit in enumerate(hits, start=1)
            )

        tools.append(search_knowledge_base)

    if datasource_id:

        @tool
        def get_table_schema(table_name: str = "") -> str:
            """查询已纳管数据库的表结构。传表名看该表的字段与索引，留空列出所有表的名称。"""
            try:
                tables = datasource_services.list_snapshot_tables(datasource_id, table_name)
            except Exception as exc:  # noqa: BLE001
                return f"查询表结构失败：{exc}"

            if not tables:
                return (
                    f"数据源中没有找到匹配 {table_name!r} 的表，或该数据源尚未采集。"
                    if table_name
                    else "该数据源尚未采集。"
                )

            if not table_name:
                listing = "\n".join(
                    f"- {t['schema']}.{t['name']}（{t['comment'] or '无注释'}，约 {t['row_count']} 行，{len(t['columns'])} 列）"
                    for t in tables[:50]
                )
                return f"共 {len(tables)} 张表：\n{listing}"

            table = tables[0]
            columns = "\n".join(
                f"  - {c['name']} {c['data_type']}{'' if c['nullable'] else ' NOT NULL'}"
                f"{('  # ' + c['comment']) if c['comment'] else ''}"
                for c in table["columns"]
            )
            text = (
                f"{table['schema']}.{table['name']}（{table['comment'] or '无注释'}，约 {table['row_count']} 行）\n"
                f"主键：{', '.join(table['primary_key']) or '无'}\n"
                f"列：\n{columns}"
            )
            return text[:_MAX_TABLE_CHARS]

        tools.append(get_table_schema)

    return tools

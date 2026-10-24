"""
agent 可调用的工具

工具按请求构造（知识库与数据源随请求而变），并通过闭包收集「本次实际检索到的片段」——
回答里给出的来源必须对得上实际检索结果，不允许由模型自行编造。

数据源工具只读最近一次元数据快照与问题表，不连接被纳管数据库。
"""

from langchain_core.tools import tool

from apps.datasource import services as datasource_services
from apps.knowledge import llm, retrieval
from utils import custom_enum

# 工具回给模型的内容上限，避免一次检索把上下文塞满
_MAX_SNIPPET_CHARS = 700
_MAX_TABLE_CHARS = 1200
_MAX_ISSUE_CHARS = 300
_MAX_ISSUE_ROWS = 20

# 工具名 → 面向使用者的进度文案。
#
# 流式回答在**模型开始发起工具调用时**就据它给出提示，因此提示出现在工具执行之前，
# 使用者不会对着空白等待。文案 MUST NOT 包含入参或工具返回内容。
TOOL_STATUS = {
    "search_knowledge_base": "正在检索知识库…",
    "get_table_schema": "正在查询表结构…",
    "get_datasource_summary": "正在查看数据源概况…",
    "list_catalog_issues": "正在查询库表问题…",
}

_ISSUE_LEVEL_ALIASES = {
    "high": custom_enum.IssueLevelEnum.HIGH.value,
    "1": custom_enum.IssueLevelEnum.HIGH.value,
    "高": custom_enum.IssueLevelEnum.HIGH.value,
    "medium": custom_enum.IssueLevelEnum.MEDIUM.value,
    "2": custom_enum.IssueLevelEnum.MEDIUM.value,
    "中": custom_enum.IssueLevelEnum.MEDIUM.value,
    "low": custom_enum.IssueLevelEnum.LOW.value,
    "3": custom_enum.IssueLevelEnum.LOW.value,
    "低": custom_enum.IssueLevelEnum.LOW.value,
}


def status_text(tool_name: str) -> str:
    """
    取某个工具的进度文案；未登记的工具给一个通用说法，不暴露内部细节
    """
    return TOOL_STATUS.get(tool_name) or "正在查询…"


def build_tools(
    knowledge_base_id: int | None = None,
    datasource_id: int | None = None,
    collected: list | None = None,
    used_tools: list | None = None,
):
    """
    构造工具集。

    - `collected` 收集本次实际检索到的片段，作为回答的来源依据
    - `used_tools` 记录实际执行过的工具名，供回答如实标注
    """
    collected = collected if collected is not None else []
    used_tools = used_tools if used_tools is not None else []
    tools = []

    if knowledge_base_id:

        @tool
        def search_knowledge_base(query: str) -> str:
            """检索知识库中与查询相关的资料片段。问及概念、用法、报错、规范时先用它。"""
            used_tools.append("search_knowledge_base")
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
            used_tools.append("get_table_schema")
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

        @tool
        def get_datasource_summary() -> str:
            """查询当前数据源的整体概况：表数量、模式数量、问题高/中/低分布、最近采集时间。
            问「有多少张表」「整体健康状况怎么样」时先用它。"""
            used_tools.append("get_datasource_summary")
            try:
                summary = datasource_services.datasource_summary(datasource_id)
            except Exception as exc:  # noqa: BLE001
                return f"查询数据源概况失败：{exc}"

            if not summary["collected"]:
                return "该数据源尚未完成采集，无法提供概况。请先在数据源页面触发采集。"

            counts = summary["issue_counts"]
            return (
                f"数据源概况（最近采集：{summary['collect_time']}，"
                f"数据库版本：{summary['database_version'] or '未知'}）\n"
                f"- 模式：{summary['schema_count']} 个\n"
                f"- 表：{summary['table_count']} 张\n"
                f"- 问题：共 {summary['issue_total']} 个"
                f"（高 {counts.get(custom_enum.IssueLevelEnum.HIGH.value, 0)} / "
                f"中 {counts.get(custom_enum.IssueLevelEnum.MEDIUM.value, 0)} / "
                f"低 {counts.get(custom_enum.IssueLevelEnum.LOW.value, 0)}）"
            )

        tools.append(get_datasource_summary)

        @tool
        def list_catalog_issues(table_name: str = "", issue_level: str = "") -> str:
            """查询当前数据源的库表健康问题。可按表名（包含匹配）和严重级别过滤，
            issue_level 取 high/medium/low。问「哪些表有明显问题」「某张表有什么隐患」时用它。"""
            used_tools.append("list_catalog_issues")
            level = None
            if issue_level:
                level = _ISSUE_LEVEL_ALIASES.get(str(issue_level).strip().lower())
                if level is None:
                    return "严重级别不合法，只能是 high、medium 或 low。"

            try:
                result = datasource_services.list_snapshot_issues(
                    datasource_id, issue_level=level, table_name=table_name, limit=_MAX_ISSUE_ROWS
                )
            except Exception as exc:  # noqa: BLE001
                return f"查询库表问题失败：{exc}"

            if not result["collected"]:
                return "该数据源尚未完成采集，无法查询问题。请先在数据源页面触发采集。"
            if not result["issues"]:
                scope = f"表名包含 {table_name!r} 的" if table_name else (f"{issue_level} 级别的" if issue_level else "")
                return f"没有查询到{scope}问题（已排除已静音项）。"

            lines = [f"共 {result['total']} 个问题，以下展示前 {len(result['issues'])} 个："]
            for item in result["issues"]:
                target = item["target"] or ".".join(
                    part for part in (item["schema"], item["table"], item["column"]) if part
                )
                line = f"- [{item['issue_level_label']}] {target}（{item['rule_name']}）：{item['description'][:_MAX_ISSUE_CHARS]}"
                if item["suggestion"]:
                    line += f" 建议：{item['suggestion'][:_MAX_ISSUE_CHARS]}"
                lines.append(line)
            return "\n".join(lines)

        tools.append(list_catalog_issues)

    return tools

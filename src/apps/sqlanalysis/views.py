"""
SQL 分析视图

分析是**一次性**的、不落库，因此挂在 `baseviews.StatelessView` 上，只暴露自定义 action。

两类接口刻意分开（design.md D3）：

- `analyze`：只读快照与内存计算，可以随请求自动执行
- `execute`：会在**真实的、可能是生产的**库上跑语句，**必须由使用者显式触发**

同基线：不做应用层鉴权，访问控制依赖网络隔离。
"""

from rest_framework.decorators import action

from apps.base import baseviews
from apps.datasource import services as datasource_services
from apps.sqlanalysis import analyzer, explain, formatting, interpret, parse, schema, serializers
from utils import common
from utils.logger import get_logger

LOGGER = get_logger("sqlanalysis.log")


class SqlAnalysisView(baseviews.StatelessView):
    """
    SQL 分析：解析与格式化、规范与性能规则、结构校验、模型解读，以及显式触发的试运行
    """

    def _resolve_datasource(self, datasource_id):
        """
        校验数据源并取它的库类型；未指定数据源时返回 (None, None)
        """
        if not datasource_id:
            return None, None
        return datasource_services.datasource_brief(datasource_id), None

    @staticmethod
    def _syntax(result) -> dict:
        return {
            "ok": result.ok,
            "statement_count": len(result.statements),
            "statements": [
                {"index": item.index, "kind": item.kind.value, "kind_label": item.kind.label, "sql": item.sql}
                for item in result.statements
            ],
            "errors": [dict(item) for item in result.errors],
        }

    @staticmethod
    def _schema_check(index, skipped_reason: str = "") -> dict:
        """
        结构校验的结果说明

        **未做校验时必须说明原因**——让使用者以为校验过了比不校验更糟。
        """
        if index is None:
            return {"performed": False, "note": skipped_reason, "table_count": 0}
        return {"performed": True, "note": "", "table_count": len(index.tables)}

    def _load_index(self, datasource_id):
        """
        取表结构索引；拿不到时返回 (None, 说明)
        """
        try:
            return schema.load_schema_index(datasource_id), ""
        except schema.SchemaUnavailable as exc:
            return None, str(exc)

    @action(detail=False, methods=["POST"], url_path="analyze")
    def analyze(self, request):
        """
        解析、格式化、规则判定、结构校验与模型解读

        解析失败**不是整体失败**：仍返回语法错误与原始语句，规则与结构校验标记为未进行。
        """
        serializer = serializers.SqlAnalyzeSerializer(data=request.data)
        if not serializer.is_valid():
            return baseviews.ResponseBadRequest(common.ToolUtil.format_drf_error(serializer.errors))
        data = serializer.validated_data

        datasource_id = data.get("datasource_id")
        brief, _ = self._resolve_datasource(datasource_id)
        if datasource_id and brief is None:
            return baseviews.ResponseNotFound("数据源不存在")

        # 方言：显式指定的优先，其次按数据源的库类型推断，都没有则用默认
        dialect = data.get("dialect") or (brief or {}).get("db_type")

        sql = data["sql"]
        result = parse.parse_sql(sql, dialect)
        issues = []
        runner = None

        if result.ok:
            index, skipped = self._load_index(datasource_id)
            runner = analyzer.SqlAnalyzer(result, sql=sql, dialect=dialect, schema=index)
            issues = runner.analyze()
            schema_check = self._schema_check(index, skipped)
        else:
            # 解析不了，规则一条都跑不了；但模型对语法错误往往最有帮助，解读照常进行
            schema_check = self._schema_check(None, "SQL 未能解析，本次未做结构校验。")

        context_note = ""
        if not result.ok:
            first = result.errors[0]
            where = f"第 {first['line']} 行第 {first['col']} 列" if first.get("line") else "位置未知"
            context_note = f"该 SQL 解析失败（{where}）：{first['description']}"

        return baseviews.ResponseOK(
            {
                "sql": sql,
                "dialect": dialect or "",
                "formatted": formatting.format_sql(sql, dialect),
                "syntax": self._syntax(result),
                # 结论由后端按规则产出算出来，**不依赖模型**——「有没有明显问题」
                # 任何时候都要有答案
                "verdict": analyzer.verdict_of(issues),
                "issues": issues,
                "schema_check": schema_check,
                "interpretation": interpret.interpret(sql, issues, dialect, context_note=context_note),
                "evaluated_rules": list(runner.evaluated_rules) if runner else [],
                "skipped_rules": list(runner.skipped_rules) if runner else [],
            }
        )

    @action(detail=False, methods=["POST"], url_path="execute")
    def execute(self, request):
        """
        试运行：只读语句真跑，DML/DDL 只出执行计划、**绝不执行**

        必须由使用者显式触发——这个接口会在真实库上执行语句。
        """
        serializer = serializers.SqlExecuteSerializer(data=request.data)
        if not serializer.is_valid():
            return baseviews.ResponseBadRequest(common.ToolUtil.format_drf_error(serializer.errors))
        data = serializer.validated_data

        if datasource_services.datasource_brief(data["datasource_id"]) is None:
            return baseviews.ResponseNotFound("数据源不存在")

        result = parse.parse_sql(data["sql"], data.get("dialect"))
        try:
            execution = explain.execute(result, data["datasource_id"], data.get("max_rows"))
        except explain.ExecutionRejected as exc:
            return baseviews.ResponseExpectationFailed(str(exc))
        except Exception as exc:  # noqa: BLE001 外部依赖属边界，失败要如实回给使用者
            LOGGER.error("试运行失败 datasource_id=%s err=%s", data["datasource_id"], exc)
            return baseviews.ResponseError(f"试运行失败：{exc}")

        return baseviews.ResponseOK(execution)

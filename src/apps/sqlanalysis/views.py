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
from apps.sqlanalysis import explain, formatting, interpret, parse, serializers, services
from utils import common, pagination
from utils.logger import get_logger

LOGGER = get_logger("sqlanalysis.log")


class SqlAnalysisView(baseviews.StatelessView):
    """
    SQL 分析：解析与格式化、规范与性能规则、结构校验、模型解读，以及显式触发的试运行
    """

    pagination_class = pagination.StandardPagination

    @action(detail=False, methods=["GET"], url_path="rules")
    def rules(self, request):
        """
        SQL 规则清单（只读）

        SQL 规则没有覆盖机制、也不落库，这里就是代码声明的直接呈现——目的是让使用者
        **不用贴一条 SQL** 也能看到系统会检查什么。「需要表结构」的规则一并标出，
        使未绑定数据源时被跳过的那些不至于被误以为在跑。
        """
        self.serializer_class = serializers.SqlRuleSerializer
        return baseviews.ResponseOK(pagination.paginate(self, services.list_rules()))

    def _resolve_datasource(self, datasource_id):
        """
        校验数据源并取它的库类型；未指定数据源时返回 (None, None)
        """
        if not datasource_id:
            return None, None
        return datasource_services.datasource_brief(datasource_id), None

    @staticmethod
    def _syntax_note(syntax: dict) -> str:
        """
        解析失败时给模型的补充信息

        规则一条都跑不了，而模型对语法错误往往**最有帮助**，所以哪怕没判出问题也要把位置告诉它。
        """
        if syntax.get("ok") or not syntax.get("errors"):
            return ""
        first = syntax["errors"][0]
        where = f"第 {first['line']} 行第 {first['col']} 列" if first.get("line") else "位置未知"
        return f"该 SQL 解析失败（{where}）：{first['description']}"

    @action(detail=False, methods=["POST"], url_path="format")
    def format_sql(self, request):
        """
        只做格式化

        与分析分开：格式化是独立动作，不该顺带触发规则判定与模型调用——那既慢又费额度。
        解析不了时原样返回，由调用方按 `syntax.ok` 决定怎么提示。
        """
        serializer = serializers.SqlTextSerializer(data=request.data)
        if not serializer.is_valid():
            return baseviews.ResponseBadRequest(common.ToolUtil.format_drf_error(serializer.errors))
        data = serializer.validated_data

        sql = data["sql"]
        dialect = data.get("dialect")
        result = parse.parse_sql(sql, dialect)
        return baseviews.ResponseOK(
            {
                "sql": sql,
                "dialect": dialect or "",
                "formatted": formatting.format_sql(sql, dialect),
                "syntax": services.syntax_of(result),
            }
        )

    @action(detail=False, methods=["POST"], url_path="analyze")
    def analyze(self, request):
        """
        解析、规则判定与结构校验

        解析失败**不是整体失败**：仍返回语法错误与原始语句，规则与结构校验标记为未进行。
        结果由 `services.analyze_sql` 产出——本视图只做入参校验与数据源解析。
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

        return baseviews.ResponseOK(services.analyze_sql(data["sql"], dialect=dialect, datasource_id=datasource_id))

    @action(detail=False, methods=["POST"], url_path="interpret")
    def interpret_sql(self, request):
        """
        只做模型解读

        与 analyze 分开的理由是**耗时差了几个数量级**：规则判定是毫秒级的纯计算，模型调用是
        十几秒的外部服务。绑在一起时使用者要等模型才看得到问题清单，前端的超时也卡不住
        （实测撞过 15 秒超时）。

        入参与 analyze 相同：这里**重新跑一遍解析与规则**，保证解读对应的就是规则判出的那批问题，
        也免去把问题清单在前后端之间来回传。
        """
        serializer = serializers.SqlAnalyzeSerializer(data=request.data)
        if not serializer.is_valid():
            return baseviews.ResponseBadRequest(common.ToolUtil.format_drf_error(serializer.errors))
        data = serializer.validated_data

        datasource_id = data.get("datasource_id")
        brief, _ = self._resolve_datasource(datasource_id)
        if datasource_id and brief is None:
            return baseviews.ResponseNotFound("数据源不存在")

        dialect = data.get("dialect") or (brief or {}).get("db_type")
        sql = data["sql"]
        analysis = services.analyze_sql(sql, dialect=dialect, datasource_id=datasource_id)

        return baseviews.ResponseOK(
            interpret.interpret(
                sql,
                analysis["issues"],
                dialect,
                context_note=self._syntax_note(analysis["syntax"]),
            )
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

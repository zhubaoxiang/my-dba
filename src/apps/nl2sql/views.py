"""
说人话 → 查询语句

一次性、不落库，因此挂在 `baseviews.StatelessView` 上，只暴露自定义 action。

同基线：不做应用层鉴权，访问控制依赖网络隔离。
"""

from rest_framework.decorators import action

from apps.base import baseviews
from apps.nl2sql import serializers, services
from utils import common
from utils.logger import get_logger

LOGGER = get_logger("nl2sql.log")


class Nl2SqlView(baseviews.StatelessView):
    """
    按自然语言描述生成查询语句
    """

    @action(detail=False, methods=["POST"], url_path="generate")
    def generate(self, request):
        """
        生成一条查询语句，并用系统自己的规则校验它

        与既有边界一致：**这里只生成，不执行**。跑不跑由使用者在前端显式触发，
        走既有的 `/sql-analysis/execute`（design.md D4）。

        「模型不可用」「模型没写出可用语句」不在这里报错——那是使用者需要看到的正常结论，
        由 `services` 返回降级结果。这里只拦「数据源不存在」与「拿不到表结构」这类
        输入/环境问题。
        """
        serializer = serializers.Nl2SqlGenerateSerializer(data=request.data)
        if not serializer.is_valid():
            return baseviews.ResponseBadRequest(common.ToolUtil.format_drf_error(serializer.errors))
        data = serializer.validated_data

        try:
            result = services.generate_sql(
                data["datasource_id"],
                data["question"],
                tables=data.get("tables"),
                dialect=data.get("dialect"),
            )
        except services.DatasourceNotFound as exc:
            return baseviews.ResponseNotFound(str(exc))
        except (services.StructureUnavailable, services.StructureTooLarge) as exc:
            return baseviews.ResponseBadRequest(str(exc))

        return baseviews.ResponseOK(result)

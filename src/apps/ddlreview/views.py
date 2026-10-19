"""
DDL 评审视图

评审是**一次性**的、不落库、不执行，因此挂在 `baseviews.StatelessView` 上，只暴露自定义 action。
同基线：不做应用层鉴权，访问控制依赖网络隔离。
"""

from rest_framework.decorators import action

from apps.base import baseviews
from apps.ddlreview import serializers, services
from utils import common


class DdlReviewView(baseviews.StatelessView):
    """
    在采集快照上预演一条或多条 DDL，给出设计问题增量与语句风险
    """

    @action(detail=False, methods=["POST"], url_path="review")
    def review(self, request):
        serializer = serializers.DdlReviewSerializer(data=request.data)
        if not serializer.is_valid():
            return baseviews.ResponseBadRequest(common.ToolUtil.format_drf_error(serializer.errors))
        data = serializer.validated_data

        try:
            result = services.review(
                data["sql"],
                datasource_id=data.get("datasource_id"),
                dialect=data.get("dialect"),
            )
        except services.DdlReviewError as exc:
            if exc.code == 4004:
                return baseviews.ResponseNotFound(str(exc))
            if exc.code == 4017:
                return baseviews.ResponseExpectationFailed(str(exc))
            return baseviews.ResponseBadRequest(str(exc))
        return baseviews.ResponseOK(result)

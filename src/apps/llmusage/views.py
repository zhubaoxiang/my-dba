"""
模型用量接口（仅超级管理员）

只读：`GET /v1/llm-usage` 返回汇总、趋势与分组，`GET /v1/llm-usage/calls`
返回调用明细。写入由 `services.record_call()` 在模型调用时完成。
"""

from rest_framework.decorators import action

from apps.base import baseviews
from apps.llmusage import models, serializers, services
from utils import common, pagination


class LlmUsageView(baseviews.SuperUserView):
    """
    模型调用用量

    `http_method_names` 只留 GET：这个资源不提供增删改，记录只由调用回调写入。
    """

    queryset = models.LlmUsage.objects.filter(is_deleted=False)
    serializer_class = serializers.LlmUsageSerializer
    pagination_class = pagination.StandardPagination
    http_method_names = ["get"]

    def list(self, request, **kwargs):
        # 用普通 dict：QueryDict 会被 DRF 当作 HTML 表单，缺失的布尔字段会被解析成 False
        serializer = serializers.UsageSummaryQuerySerializer(data=request.query_params.dict())
        if not serializer.is_valid():
            return baseviews.ResponseBadRequest(common.ToolUtil.format_drf_error(serializer.errors))
        data = serializer.validated_data
        return baseviews.ResponseOK(services.build_usage(data["range"], data["group_by"]))

    @action(detail=False, methods=["get"], url_path="calls")
    def calls(self, request, **kwargs):
        # 同上：缺失的 success 不能被解析成 False
        serializer = serializers.UsageCallsQuerySerializer(data=request.query_params.dict())
        if not serializer.is_valid():
            return baseviews.ResponseBadRequest(common.ToolUtil.format_drf_error(serializer.errors))
        data = serializer.validated_data
        queryset = services.calls_queryset(data["range"], data.get("success"), data.get("min_latency"))
        return baseviews.ResponseOK(pagination.paginate(self, queryset))

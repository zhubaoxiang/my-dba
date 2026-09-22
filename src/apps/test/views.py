"""
test views
author: zhubao
"""

import logging

from django.http import HttpResponse
from rest_framework.decorators import action

from apps.base import baseviews
from apps.test import models, serializers
from utils import common, custom_enum, pagination

LOGGER = logging.getLogger(__name__)


def index(request):
    return HttpResponse("Hello World!")


class TestView(baseviews.AnyLogin):
    """
    视图
    """

    queryset = models.MavenEnforcerRules.objects.all()
    serializer_class = serializers.MavenEnforcerRuleSerializer
    pagination_class = pagination.StandardPagination

    def list(self, request, **kwargs):
        queryset = self.get_queryset().filter(is_deleted=False).order_by("-id")
        result = pagination.paginate(self, queryset)
        return baseviews.ResponseOK(result)

    def create(self, request, **kwargs):
        print(kwargs)
        return baseviews.ResponseOK(kwargs)

    @action(detail=False, methods=["POST"], url_path="banned")
    def create_banned_dependency_rule(self, request, **kwargs):
        params = request.data
        serializer = serializers.CreateBannedDependenciesSerializer(data=params)
        if not serializer.is_valid():
            return baseviews.ResponseBadRequest(common.ToolUtil.format_drf_error(serializer.errors))
        data = {"type": custom_enum.TaskTypeEnum.HOST.value}
        return baseviews.ResponseOK(data)

    @action(detail=False, methods=["POST"], permission_classes=[])
    def addition(self, request):
        """
        规则组装
        :param request:
        :return:
        """
        file_instance = request.FILES.get("file")
        file_data = file_instance.read()

        response = HttpResponse(file_data)
        response["Content-Type"] = "application/octet-stream"
        response["Content-Disposition"] = "attachment; filename=pom.xml"
        return response

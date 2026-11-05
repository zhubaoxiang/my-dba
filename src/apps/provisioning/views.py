"""
测试库预置接口（仅管理员）

预览只生成计划；执行会真正在测试环境新建独立 database，因此全部接口走 SuperUserView。
"""

from rest_framework.decorators import action

from apps.base import baseviews
from apps.provisioning import models, serializers, services
from utils import common, pagination


class ProvisioningView(baseviews.BaseView):
    """
    测试库预置记录、预览、执行与删除

    登录用户可用，但只能操作**自己创建的数据源**与自己的预置记录；管理员可操作全部。
    """

    queryset = models.ProvisionedDatabase.objects.filter(is_deleted=False)
    serializer_class = serializers.ProvisionedDatabaseSerializer
    pagination_class = pagination.StandardPagination

    @staticmethod
    def _creator(request) -> str:
        return getattr(getattr(request, "user", None), "username", "") or ""

    @staticmethod
    def _is_admin(request) -> bool:
        return bool(getattr(getattr(request, "user", None), "is_staff", False))

    def list(self, request, **kwargs):
        queryset = self.get_queryset().order_by("-id")
        if not self._is_admin(request):
            queryset = queryset.filter(creator=self._creator(request))
        return baseviews.ResponseOK(pagination.paginate(self, queryset))

    def create(self, request, *args, **kwargs):
        return baseviews.ResponseBadRequest("请使用预览与执行接口")

    def update(self, request, *args, **kwargs):
        return baseviews.ResponseBadRequest("预置记录不可修改")

    def partial_update(self, request, *args, **kwargs):
        return baseviews.ResponseBadRequest("预置记录不可修改")

    def destroy(self, request, *args, **kwargs):
        record = self.get_queryset().filter(id=kwargs.get("pk")).first()
        if record is None:
            return baseviews.ResponseNotFound("预置记录不存在")
        try:
            services.delete_record(record, actor=self._creator(request), is_admin=self._is_admin(request))
        except services.ProvisionPermissionError as exc:
            return baseviews.ResponsePermissionDenied(str(exc))
        except services.ProvisionError as exc:
            return baseviews.ResponseExpectationFailed(str(exc))
        return baseviews.ResponseOK(None)

    @action(detail=False, methods=["POST"], url_path="preview")
    def preview(self, request):
        serializer = serializers.ProvisionPreviewSerializer(data=request.data)
        if not serializer.is_valid():
            return baseviews.ResponseBadRequest(common.ToolUtil.format_drf_error(serializer.errors))
        data = serializer.validated_data
        try:
            plan = services.preview(
                data["datasource_id"],
                data["tables"],
                data.get("seed"),
                actor=self._creator(request),
                is_admin=self._is_admin(request),
            )
        except services.ProvisionPermissionError as exc:
            return baseviews.ResponsePermissionDenied(str(exc))
        except services.ProvisionError as exc:
            return baseviews.ResponseExpectationFailed(str(exc))
        return baseviews.ResponseOK(plan)

    def _record_or_none(self, kwargs):
        return self.get_queryset().filter(id=kwargs.get("pk")).first()

    @action(detail=True, methods=["POST", "DELETE"], url_path="rows")
    def rows(self, request, **kwargs):
        """
        POST 追加数据；DELETE 清空表数据
        """
        record = self._record_or_none(kwargs)
        if record is None:
            return baseviews.ResponseNotFound("预置记录不存在")

        if request.method == "POST":
            serializer = serializers.ManageRowsSerializer(data=request.data)
            if not serializer.is_valid():
                return baseviews.ResponseBadRequest(common.ToolUtil.format_drf_error(serializer.errors))
            data = serializer.validated_data
            try:
                record, warnings = services.append_rows(
                    record,
                    data["schema"],
                    data["name"],
                    data["rows"],
                    seed=data.get("seed"),
                    actor=self._creator(request),
                    is_admin=self._is_admin(request),
                )
            except services.ProvisionPermissionError as exc:
                return baseviews.ResponsePermissionDenied(str(exc))
            except services.ProvisionError as exc:
                return baseviews.ResponseExpectationFailed(str(exc))
            payload = serializers.ProvisionedDatabaseSerializer(record).data
            payload["warnings"] = warnings
            return baseviews.ResponseOK(payload)

        serializer = serializers.TableRefSerializer(data=request.query_params.dict())
        if not serializer.is_valid():
            return baseviews.ResponseBadRequest(common.ToolUtil.format_drf_error(serializer.errors))
        data = serializer.validated_data
        try:
            services.clear_rows(
                record, data["schema"], data["name"], actor=self._creator(request), is_admin=self._is_admin(request)
            )
        except services.ProvisionPermissionError as exc:
            return baseviews.ResponsePermissionDenied(str(exc))
        except services.ProvisionError as exc:
            return baseviews.ResponseExpectationFailed(str(exc))
        return baseviews.ResponseOK(serializers.ProvisionedDatabaseSerializer(record).data)

    @action(detail=True, methods=["POST", "DELETE"], url_path="tables")
    def tables(self, request, **kwargs):
        """
        POST 新增表；DELETE 删除表
        """
        record = self._record_or_none(kwargs)
        if record is None:
            return baseviews.ResponseNotFound("预置记录不存在")

        if request.method == "POST":
            serializer = serializers.ManageRowsSerializer(data=request.data)
            if not serializer.is_valid():
                return baseviews.ResponseBadRequest(common.ToolUtil.format_drf_error(serializer.errors))
            data = serializer.validated_data
            try:
                record, warnings = services.add_table(
                    record,
                    data["schema"],
                    data["name"],
                    data["rows"],
                    seed=data.get("seed"),
                    actor=self._creator(request),
                    is_admin=self._is_admin(request),
                )
            except services.ProvisionPermissionError as exc:
                return baseviews.ResponsePermissionDenied(str(exc))
            except services.ProvisionError as exc:
                return baseviews.ResponseExpectationFailed(str(exc))
            payload = serializers.ProvisionedDatabaseSerializer(record).data
            payload["warnings"] = warnings
            return baseviews.ResponseOK(payload)

        serializer = serializers.TableRefSerializer(data=request.query_params.dict())
        if not serializer.is_valid():
            return baseviews.ResponseBadRequest(common.ToolUtil.format_drf_error(serializer.errors))
        data = serializer.validated_data
        try:
            services.drop_table(
                record, data["schema"], data["name"], actor=self._creator(request), is_admin=self._is_admin(request)
            )
        except services.ProvisionPermissionError as exc:
            return baseviews.ResponsePermissionDenied(str(exc))
        except services.ProvisionError as exc:
            return baseviews.ResponseExpectationFailed(str(exc))
        return baseviews.ResponseOK(serializers.ProvisionedDatabaseSerializer(record).data)

    @action(detail=False, methods=["POST"], url_path="provision")
    def provision(self, request):
        serializer = serializers.ProvisionExecuteSerializer(data=request.data)
        if not serializer.is_valid():
            return baseviews.ResponseBadRequest(common.ToolUtil.format_drf_error(serializer.errors))
        data = serializer.validated_data
        if not data["confirm"]:
            return baseviews.ResponseBadRequest("请先确认在测试环境创建独立数据库")
        try:
            record, warnings = services.provision(
                data["datasource_id"],
                data["tables"],
                data["db_name"],
                seed=data.get("seed"),
                confirm=True,
                creator=self._creator(request),
                is_admin=self._is_admin(request),
            )
        except services.ProvisionPermissionError as exc:
            return baseviews.ResponsePermissionDenied(str(exc))
        except services.ProvisionError as exc:
            return baseviews.ResponseExpectationFailed(str(exc))
        payload = serializers.ProvisionedDatabaseSerializer(record).data
        payload["warnings"] = warnings
        return baseviews.ResponseOK(payload)

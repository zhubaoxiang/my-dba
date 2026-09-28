"""
数据源与元数据视图
"""

from rest_framework.decorators import action

from apps.base import baseviews
from apps.datasource import differ, models, serializers, services
from apps.datasource.collectors import get_collector
from apps.datasource.rules import registry
from utils import common, crypto, custom_enum, pagination
from utils.logger import get_logger

LOGGER = get_logger("datasource.log")

_MUTABLE_FIELDS = ("name", "db_type", "host", "port", "db_name", "username", "description", "is_enabled")


def _to_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


class DatasourceView(baseviews.AnyLogin):
    """
    数据源配置管理

    本类不做认证与角色校验：本仓库没有任何代码会产出 `Token` 头，任何基于
    `IsAuthenticated` / `IsAdminUser` 的校验都会让接口恒返回 4003。
    因此访问控制**依赖网络隔离**——服务只允许部署在内网，不要直接暴露到公网。
    """

    queryset = models.Datasource.objects.all()
    serializer_class = serializers.DatasourceSerializer
    pagination_class = pagination.StandardPagination

    @staticmethod
    def _creator(request) -> str:
        return getattr(getattr(request, "user", None), "username", "") or ""

    def _get_instance(self, kwargs):
        return models.Datasource.objects.filter(is_deleted=False, id=kwargs.get("pk")).first()

    def list(self, request, **kwargs):
        queryset = self.get_queryset().filter(is_deleted=False).order_by("-id")
        return baseviews.ResponseOK(pagination.paginate(self, queryset))

    def retrieve(self, request, *args, **kwargs):
        instance = self._get_instance(kwargs)
        if instance is None:
            return baseviews.ResponseNotFound("数据源不存在")
        return baseviews.ResponseOK(self.get_serializer(instance).data)

    def create(self, request, **kwargs):
        serializer = serializers.DatasourceCreateSerializer(data=request.data)
        if not serializer.is_valid():
            return baseviews.ResponseBadRequest(common.ToolUtil.format_drf_error(serializer.errors))
        data = serializer.validated_data
        instance = models.Datasource.objects.create(
            name=data["name"],
            db_type=data["db_type"],
            host=data["host"],
            port=data["port"],
            db_name=data["db_name"],
            username=data["username"],
            password=crypto.encrypt(data["password"]),
            description=data.get("description", ""),
            is_enabled=data.get("is_enabled", True),
            creator=self._creator(request),
        )
        return baseviews.ResponseOK(self.get_serializer(instance).data)

    def update(self, request, *args, **kwargs):
        instance = self._get_instance(kwargs)
        if instance is None:
            return baseviews.ResponseNotFound("数据源不存在")
        serializer = serializers.DatasourceUpdateSerializer(data=request.data, context={"instance": instance})
        if not serializer.is_valid():
            return baseviews.ResponseBadRequest(common.ToolUtil.format_drf_error(serializer.errors))
        data = serializer.validated_data
        for field in _MUTABLE_FIELDS:
            setattr(instance, field, data[field])
        if data.get("password"):
            instance.password = crypto.encrypt(data["password"])
        instance.save()
        return baseviews.ResponseOK(self.get_serializer(instance).data)

    def destroy(self, request, *args, **kwargs):
        instance = self._get_instance(kwargs)
        if instance is None:
            return baseviews.ResponseNotFound("数据源不存在")
        instance.is_deleted = True
        instance.save(update_fields=["is_deleted", "update_time"])
        return baseviews.ResponseOK(None)

    @action(detail=True, methods=["POST"], url_path="test")
    def test_saved(self, request, **kwargs):
        """
        测试已保存数据源的连通性
        """
        instance = self._get_instance(kwargs)
        if instance is None:
            return baseviews.ResponseNotFound("数据源不存在")
        return self._run_test(instance)

    @action(detail=False, methods=["POST"], url_path="test")
    def test_unsaved(self, request):
        """
        测试尚未保存的连接参数
        """
        serializer = serializers.DatasourceTestSerializer(data=request.data)
        if not serializer.is_valid():
            return baseviews.ResponseBadRequest(common.ToolUtil.format_drf_error(serializer.errors))
        data = serializer.validated_data
        stub = models.Datasource(
            db_type=data["db_type"],
            host=data["host"],
            port=data["port"],
            db_name=data["db_name"],
            username=data["username"],
            password="",
        )
        return self._run_test(stub, plain_password=data["password"])

    @action(detail=True, methods=["POST"], url_path="collect")
    def collect(self, request, **kwargs):
        """
        触发一次元数据采集，立即返回任务
        """
        instance = self._get_instance(kwargs)
        if instance is None:
            return baseviews.ResponseNotFound("数据源不存在")
        try:
            task = services.trigger_collect(instance, creator=self._creator(request))
        except services.CollectRejected as exc:
            return baseviews.ResponseExpectationFailed(str(exc))
        return baseviews.ResponseOK(serializers.CollectTaskSerializer(task).data)

    @action(detail=True, methods=["GET"], url_path="tasks")
    def tasks(self, request, **kwargs):
        """
        采集任务列表
        """
        queryset = models.CollectTask.objects.filter(datasource_id=kwargs.get("pk"), is_deleted=False).order_by("-id")
        self.serializer_class = serializers.CollectTaskSerializer
        return baseviews.ResponseOK(pagination.paginate(self, queryset))

    def _run_test(self, datasource, plain_password: str = None):
        try:
            info = get_collector(datasource, services.load_collect_config(), plain_password).test_connection()
        except crypto.CryptoKeyError as exc:
            return baseviews.ResponseError(str(exc))
        except Exception as exc:  # noqa: BLE001 连接失败属预期结果，转为业务响应
            LOGGER.warning("数据源连接测试失败 host=%s err=%s", datasource.host, exc)
            return baseviews.ResponseExpectationFailed(f"连接失败: {exc}")
        return baseviews.ResponseOK(info)


class CatalogView(baseviews.AnyLogin):
    """
    元数据查询：快照、库表总览、表详情、问题清单、版本差异

    同 DatasourceView，不做应用层鉴权，访问控制依赖网络隔离。
    """

    queryset = models.MetadataSnapshot.objects.all()
    serializer_class = serializers.SnapshotSerializer
    pagination_class = pagination.StandardPagination

    def _resolve_snapshot(self, request):
        """
        按 snapshot_id 或数据源最新快照定位目标快照，返回 (snapshot, 错误响应)
        """
        snapshot_id = request.query_params.get("snapshot_id")
        datasource_id = request.query_params.get("datasource_id")
        if snapshot_id:
            snapshot = models.MetadataSnapshot.objects.filter(is_deleted=False, id=_to_int(snapshot_id)).first()
        elif datasource_id:
            parsed = _to_int(datasource_id)
            if parsed is None:
                return None, baseviews.ResponseBadRequest("datasource_id 必须为整数")
            snapshot = services.latest_snapshot(parsed)
        else:
            return None, baseviews.ResponseBadRequest("缺少参数 snapshot_id 或 datasource_id")
        if snapshot is None:
            return None, baseviews.ResponseNotFound("快照不存在或该数据源尚未采集")
        return snapshot, None

    @action(detail=False, methods=["GET"], url_path="snapshots")
    def snapshots(self, request):
        """
        快照列表
        """
        queryset = models.MetadataSnapshot.objects.filter(is_deleted=False)
        datasource_id = request.query_params.get("datasource_id")
        if datasource_id:
            parsed = _to_int(datasource_id)
            if parsed is None:
                return baseviews.ResponseBadRequest("datasource_id 必须为整数")
            queryset = queryset.filter(datasource_id=parsed)
        queryset = queryset.order_by("-collect_time", "-id")
        return baseviews.ResponseOK(pagination.paginate(self, queryset))

    @action(detail=False, methods=["GET"], url_path="latest-snapshot")
    def latest_snapshot(self, request):
        """
        数据源的最新快照
        """
        datasource_id = _to_int(request.query_params.get("datasource_id"))
        if datasource_id is None:
            return baseviews.ResponseBadRequest("缺少参数 datasource_id")
        snapshot = services.latest_snapshot(datasource_id)
        if snapshot is None:
            return baseviews.ResponseNotFound("该数据源尚未采集")
        return baseviews.ResponseOK(self.get_serializer(snapshot).data)

    @action(detail=False, methods=["GET"], url_path="tables")
    def tables(self, request):
        """
        表清单（分页，可按表名或注释搜索）
        """
        snapshot, error = self._resolve_snapshot(request)
        if error:
            return error
        keyword = (request.query_params.get("keyword") or "").strip().lower()
        items = []
        for table in snapshot.raw_data.get("tables") or []:
            if (
                keyword
                and keyword not in (table.get("name") or "").lower()
                and keyword not in (table.get("comment") or "").lower()
            ):
                continue
            items.append(
                {
                    "schema": table.get("schema") or "",
                    "name": table.get("name") or "",
                    "comment": table.get("comment") or "",
                    "row_count": int(table.get("row_count") or 0),
                    "data_size": int(table.get("data_size") or 0),
                    "index_size": int(table.get("index_size") or 0),
                    "total_size": int(table.get("total_size") or 0),
                    "column_count": len(table.get("columns") or []),
                    "index_count": len(table.get("indexes") or []),
                }
            )
        self.serializer_class = serializers.CatalogTableSerializer
        return baseviews.ResponseOK(pagination.paginate(self, items))

    @action(detail=False, methods=["GET"], url_path="table")
    def table_detail(self, request):
        """
        表详情：列定义、索引、主键、外键关联与体积
        """
        snapshot, error = self._resolve_snapshot(request)
        if error:
            return error
        table_name = request.query_params.get("table")
        schema = request.query_params.get("schema")
        if not table_name:
            return baseviews.ResponseBadRequest("缺少参数 table")
        for table in snapshot.raw_data.get("tables") or []:
            if table.get("name") == table_name and (not schema or table.get("schema") == schema):
                return baseviews.ResponseOK(table)
        return baseviews.ResponseNotFound("表不存在")

    @action(detail=False, methods=["GET"], url_path="issues")
    def issues(self, request):
        """
        问题清单，可按严重级别筛选
        """
        queryset = models.CatalogIssue.objects.filter(is_deleted=False)
        snapshot_id = request.query_params.get("snapshot_id")
        datasource_id = request.query_params.get("datasource_id")
        if snapshot_id:
            queryset = queryset.filter(snapshot_id=_to_int(snapshot_id))
        elif datasource_id:
            parsed = _to_int(datasource_id)
            if parsed is None:
                return baseviews.ResponseBadRequest("datasource_id 必须为整数")
            snapshot = services.latest_snapshot(parsed)
            if snapshot is None:
                return baseviews.ResponseNotFound("该数据源尚未采集")
            queryset = queryset.filter(snapshot_id=snapshot.id)
        else:
            return baseviews.ResponseBadRequest("缺少参数 snapshot_id 或 datasource_id")

        issue_level = _to_int(request.query_params.get("issue_level"))
        if issue_level is not None:
            queryset = queryset.filter(issue_level=issue_level)
        # 按规则筛选（前端的「问题类型」下拉用它）。规则以稳定 code 标识，不受改名影响
        rule_code = (request.query_params.get("rule_code") or "").strip()
        if rule_code:
            queryset = queryset.filter(rule_code=rule_code)
        queryset = queryset.order_by("issue_level", "rule_code", "id")
        self.serializer_class = serializers.CatalogIssueSerializer
        return baseviews.ResponseOK(pagination.paginate(self, queryset))

    @action(detail=False, methods=["GET"], url_path="summary")
    def summary(self, request):
        """
        库表总览：表数量、各级别问题数量、未采集项
        """
        snapshot, error = self._resolve_snapshot(request)
        if error:
            return error
        counts = {level.value: 0 for level in custom_enum.IssueLevelEnum}
        rows = (
            models.CatalogIssue.objects.filter(snapshot_id=snapshot.id, is_deleted=False)
            .values_list("issue_level")
            .order_by()
        )
        for (level,) in rows:
            counts[level] = counts.get(level, 0) + 1
        return baseviews.ResponseOK(
            {
                "snapshot_id": snapshot.id,
                "datasource_id": snapshot.datasource_id,
                "database_version": snapshot.database_version,
                "collect_time": snapshot.collect_time.strftime("%Y-%m-%d %H:%M:%S"),
                "schema_count": snapshot.schema_count,
                "table_count": snapshot.table_count,
                "issue_counts": counts,
                "issue_total": sum(counts.values()),
                "unavailable": snapshot.unavailable,
                # 本次采集时实际参与评估的规则。与 enabled_rule_count 不一致，
                # 即说明规则集在采集之后被改过——否则「停用了一条规则」不会被察觉
                "evaluated_rules": snapshot.evaluated_rules,
                "evaluated_rule_count": len(snapshot.evaluated_rules or []),
                "enabled_rule_count": sum(1 for item in registry.load_effective_rules() if item.enabled),
            }
        )

    @action(detail=False, methods=["GET"], url_path="diff")
    def diff(self, request):
        """
        两个快照的结构差异
        """
        base_id = _to_int(request.query_params.get("snapshot_id"))
        target_id = _to_int(request.query_params.get("compare_snapshot_id"))
        if base_id is None or target_id is None:
            return baseviews.ResponseBadRequest("缺少参数 snapshot_id 或 compare_snapshot_id")
        base = models.MetadataSnapshot.objects.filter(is_deleted=False, id=base_id).first()
        target = models.MetadataSnapshot.objects.filter(is_deleted=False, id=target_id).first()
        if base is None or target is None:
            return baseviews.ResponseNotFound("快照不存在")
        return baseviews.ResponseOK(differ.diff_snapshots(base.raw_data, target.raw_data))


class AnalysisRuleView(baseviews.AnyLogin):
    """
    分析规则注册表

    规则清单与默认值来自代码声明（`apps/datasource/rules/`），本接口只暴露**运行时可改**的部分：
    启用开关、严重级别、阈值。名称与适用层级随同步更新，不接受直接修改。

    同基线：不做应用层鉴权，访问控制依赖网络隔离。
    """

    queryset = models.AnalysisRule.objects.all()
    serializer_class = serializers.AnalysisRuleSerializer
    pagination_class = pagination.StandardPagination

    @staticmethod
    def _creator(request) -> str:
        return getattr(getattr(request, "user", None), "username", "") or ""

    def _get_instance(self, kwargs):
        return models.AnalysisRule.objects.filter(is_deleted=False, id=kwargs.get("pk")).first()

    def list(self, request, **kwargs):
        queryset = self.get_queryset().filter(is_deleted=False).order_by("object_level", "code")
        return baseviews.ResponseOK(pagination.paginate(self, queryset))

    def retrieve(self, request, *args, **kwargs):
        instance = self._get_instance(kwargs)
        if instance is None:
            return baseviews.ResponseNotFound("规则不存在")
        return baseviews.ResponseOK(self.get_serializer(instance).data)

    def update(self, request, *args, **kwargs):
        """
        修改启用开关、级别或阈值。PUT 与 PATCH 同义——三项都是可选的
        """
        instance = self._get_instance(kwargs)
        if instance is None:
            return baseviews.ResponseNotFound("规则不存在")
        serializer = serializers.AnalysisRuleUpdateSerializer(
            data=request.data, context={"rule": registry.get_rule(instance.code)}
        )
        if not serializer.is_valid():
            return baseviews.ResponseBadRequest(common.ToolUtil.format_drf_error(serializer.errors))
        data = serializer.validated_data
        for field in ("enabled", "level", "thresholds"):
            if field in data:
                setattr(instance, field, data[field])
        instance.save()
        return baseviews.ResponseOK(self.get_serializer(instance).data)

    def partial_update(self, request, *args, **kwargs):
        return self.update(request, *args, **kwargs)

    def create(self, request, **kwargs):
        # 凭空建一条库记录没有对应的代码实现，分析时会被忽略，不如明确拒绝
        return baseviews.ResponseBadRequest("规则来自代码声明，请调用同步接口，而不是新建")

    def destroy(self, request, *args, **kwargs):
        # 删掉库记录只会退回代码默认值，达不到「删除规则」的效果
        return baseviews.ResponseBadRequest("规则来自代码声明，不支持删除；如需停用请改启用开关")

    @action(detail=False, methods=["POST"], url_path="sync")
    def sync(self, request):
        """
        从代码声明同步规则清单

        按 `code` 幂等 upsert，**只更新** name / description / object_level。
        enabled / level / thresholds 由使用者调整，同步不碰——否则一次部署就会冲掉调过的开关。
        """
        creator = self._creator(request)
        existing = {row.code: row for row in models.AnalysisRule.objects.filter(is_deleted=False)}
        created = updated = 0
        for rule in registry.all_rules():
            row = existing.get(rule.code)
            if row is None:
                models.AnalysisRule.objects.create(
                    code=rule.code,
                    name=rule.name,
                    description=rule.description,
                    level=rule.default_level.value,
                    object_level=rule.object_level.value,
                    enabled=True,
                    thresholds=dict(rule.default_thresholds),
                    creator=creator,
                )
                created += 1
                continue
            if (
                row.name != rule.name
                or row.description != rule.description
                or row.object_level != rule.object_level.value
            ):
                row.name = rule.name
                row.description = rule.description
                row.object_level = rule.object_level.value
                row.save(update_fields=["name", "description", "object_level", "update_time"])
                updated += 1
        return baseviews.ResponseOK({"created": created, "updated": updated, "total": len(registry.all_rules())})

    @action(detail=True, methods=["POST"], url_path="reset")
    def reset(self, request, **kwargs):
        """
        把可覆盖项恢复为代码声明的默认值
        """
        instance = self._get_instance(kwargs)
        if instance is None:
            return baseviews.ResponseNotFound("规则不存在")
        declared = registry.get_rule(instance.code)
        if declared is None:
            return baseviews.ResponseExpectationFailed(f"规则 {instance.code} 已不在代码声明中，无法恢复默认值")
        instance.enabled = True
        instance.level = declared.default_level.value
        instance.thresholds = dict(declared.default_thresholds)
        instance.save(update_fields=["enabled", "level", "thresholds", "update_time"])
        return baseviews.ResponseOK(self.get_serializer(instance).data)

"""
数据源与元数据视图
"""

from django.db.models import Count
from rest_framework.decorators import action

from apps.base import baseviews
from apps.datasource import differ, fixes, models, serializers, services
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

    @staticmethod
    def _issue_counts(snapshot) -> dict:
        """
        按 (schema, table) 汇总问题数量，排除已静音项

        一次分组查询算完全部表，避免在表清单循环里逐表查库。问题与清单、统计
        走同一个 `muted_filter()`，保证「表上的问题数」和「问题清单」口径一致。
        """
        counts = {}
        queryset = models.CatalogIssue.objects.filter(
            snapshot_id=snapshot.id, is_deleted=False
        ).exclude(table_name="")
        muted = services.muted_filter()
        if muted is not None:
            queryset = queryset.exclude(muted)
        rows = (
            queryset.values("schema_name", "table_name", "issue_level")
            .annotate(total=Count("id"))
            .order_by()
        )
        for row in rows:
            key = ((row["schema_name"] or "").lower(), (row["table_name"] or "").lower())
            counts.setdefault(key, {})[row["issue_level"]] = row["total"]
        return counts

    @action(detail=False, methods=["GET"], url_path="tables")
    def tables(self, request):
        """
        表清单（分页，可按表名或注释搜索）
        """
        snapshot, error = self._resolve_snapshot(request)
        if error:
            return error
        keyword = (request.query_params.get("keyword") or "").strip().lower()
        issue_counts = self._issue_counts(snapshot)
        items = []
        for table in snapshot.raw_data.get("tables") or []:
            if (
                keyword
                and keyword not in (table.get("name") or "").lower()
                and keyword not in (table.get("comment") or "").lower()
            ):
                continue
            key = ((table.get("schema") or "").lower(), (table.get("name") or "").lower())
            counts = issue_counts.get(key, {})
            by_level = {level.value: counts.get(level.value, 0) for level in custom_enum.IssueLevelEnum}
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
                    "issue_counts": by_level,
                    "issue_total": sum(by_level.values()),
                }
            )
        # 问题多的排前面：高 → 中 → 低逐级比较；同分按 schema/表名，保证分页顺序稳定
        items.sort(
            key=lambda item: (
                -item["issue_counts"][custom_enum.IssueLevelEnum.HIGH.value],
                -item["issue_counts"][custom_enum.IssueLevelEnum.MEDIUM.value],
                -item["issue_counts"][custom_enum.IssueLevelEnum.LOW.value],
                item["schema"].lower(),
                item["name"].lower(),
            )
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
                # 浅拷贝一份再挂关系：快照里的对象是缓存着的，就地改会污染后续请求
                payload = dict(table)
                payload["relations"] = services.table_relations(
                    snapshot.raw_data.get("tables") or [], table.get("schema"), table.get("name")
                )
                return baseviews.ResponseOK(payload)
        return baseviews.ResponseNotFound("表不存在")

    @action(detail=False, methods=["GET"], url_path="issues")
    def issues(self, request):
        """
        问题清单，可按严重级别筛选

        每条问题附带一条**修复语句**（`fix`）：对象确定的那几类由模板给出（纯计算，
        见 `apps/datasource/fixes.py`），无唯一答案的标为需模型参考。

        分页在此**手动完成**而非用 `pagination.paginate`：修复语句要挂在**分页后的这一页**
        上，而那个封装内部就把序列化做完了，插不进去。
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
        # 从库表分析「问题」按钮跳转进来时按表定位；同名表可能出现在不同模式，模式一并带上
        table_name = (request.query_params.get("table_name") or "").strip()
        if table_name:
            queryset = queryset.filter(table_name__iexact=table_name)
        schema_name = (request.query_params.get("schema_name") or "").strip()
        if schema_name:
            queryset = queryset.filter(schema_name__iexact=schema_name)
        # 本次问题所属的数据源。**必须在静音过滤之前取**——否则整页问题全被静音时
        # 这里取不到值，连带下面的 muted_total 也变成 0，界面上就再也看不到「已静音几条」
        owner_id = queryset.values_list("datasource_id", flat=True).first()

        # 静音的问题**默认不出现**——它们的存在意义就是让清单只剩新问题。
        # 与问题统计走同一个 `muted_filter()`，两处口径必须一致
        include_muted = (request.query_params.get("include_muted") or "").lower() in ("1", "true", "yes")
        muted = services.muted_filter()
        if not include_muted and muted is not None:
            queryset = queryset.exclude(muted)

        queryset = queryset.order_by("issue_level", "rule_code", "id")

        page = self.paginate_queryset(queryset)
        items = list(page) if page is not None else list(queryset)
        brief = services.datasource_brief(owner_id) if owner_id else None
        db_type = (brief or {}).get("db_type")
        for item, fix in zip(items, fixes.fixes_for(items, db_type), strict=False):
            item.fix = fix

        self.serializer_class = serializers.CatalogIssueSerializer
        serializer = self.get_serializer(items, many=True)
        # 「已静音 N 条」：界面上要常驻这个数——静音会让数字变小，不说明会被当成问题解决了
        muted_total = services.count_mutes(owner_id) if owner_id else 0
        if page is None:
            return baseviews.ResponseOK(
                {"results": serializer.data, "total": len(serializer.data), "muted_total": muted_total}
            )
        result = self.get_paginated_response(serializer.data).data
        result["total"] = result["count"]
        result["muted_total"] = muted_total
        return baseviews.ResponseOK(result)

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

    @action(detail=False, methods=["POST"], url_path="issues/mute")
    def mute_issue(self, request):
        """
        静音一处问题（幂等）

        **只影响这一处**——要让整条规则不再运行，那是「停用规则」，影响面差一个数量级。
        """
        datasource_id = _to_int(request.data.get("datasource_id"))
        rule_code = (request.data.get("rule_code") or "").strip()
        if datasource_id is None or not rule_code:
            return baseviews.ResponseBadRequest("缺少参数 datasource_id 或 rule_code")
        try:
            row = services.mute_issue(
                datasource_id,
                rule_code,
                table_name=(request.data.get("table_name") or "").strip(),
                column_name=(request.data.get("column_name") or "").strip(),
                creator=getattr(getattr(request, "user", None), "username", "") or "",
            )
        except services.InvalidMuteKey as exc:
            return baseviews.ResponseBadRequest(str(exc))
        except services.DatasourceUnavailable as exc:
            return baseviews.ResponseNotFound(str(exc))
        return baseviews.ResponseOK(serializers.IssueMuteSerializer(row).data)

    @action(detail=False, methods=["DELETE"], url_path=r"issues/mute/(?P<mute_id>\d+)")
    def unmute_issue(self, request, mute_id=None):
        """
        取消静音
        """
        datasource_id = _to_int(request.query_params.get("datasource_id"))
        if datasource_id is None:
            return baseviews.ResponseBadRequest("缺少参数 datasource_id")
        try:
            services.unmute_issue(datasource_id, int(mute_id))
        except services.DatasourceUnavailable as exc:
            return baseviews.ResponseNotFound(str(exc))
        return baseviews.ResponseOK({"id": int(mute_id)})

    @action(detail=False, methods=["GET"], url_path="issues/muted")
    def muted_issues(self, request):
        """
        已静音的条目（供「查看已静音」与取消静音）
        """
        datasource_id = _to_int(request.query_params.get("datasource_id"))
        if datasource_id is None:
            return baseviews.ResponseBadRequest("缺少参数 datasource_id")
        rows = services.list_mutes(datasource_id)
        self.serializer_class = serializers.IssueMuteSerializer
        return baseviews.ResponseOK({"results": self.get_serializer(rows, many=True).data, "total": len(rows)})

    @action(detail=False, methods=["POST"], url_path="issues/reference")
    def issue_reference(self, request):
        """
        给一条「无唯一答案」的问题生成**AI 修复语句**

        **按需触发**——它要调模型，有延迟与成本，且只在那两条上用得着（超大表、
        大对象拆表）。模型不可用时返回 `available: false` 与原因，不是接口错误。
        """
        datasource_id = _to_int(request.data.get("datasource_id"))
        issue_id = _to_int(request.data.get("issue_id"))
        if datasource_id is None or issue_id is None:
            return baseviews.ResponseBadRequest("缺少参数 datasource_id 或 issue_id")

        try:
            return baseviews.ResponseOK(services.reference_fix(datasource_id, issue_id))
        except services.DatasourceUnavailable as exc:
            return baseviews.ResponseNotFound(str(exc))


class AnalysisRuleView(baseviews.AnyLogin):
    """
    分析规则注册表

    规则清单与默认值来自代码声明（`apps/datasource/rules/`），本接口只暴露**运行时可改**的部分：
    启用开关、严重级别、阈值。名称、说明与适用层级随代码声明，不接受直接修改。

    清单以**代码声明为基底、叠加库中的覆盖项**返回，从未落库的规则也会出现并展示代码默认值
    （design D1）——库里有没有记录只决定「这条被改过没有」，不决定它在不在清单里。
    规则以 `code` 定位而非自增主键：未落库的规则没有主键可用（design D2）。

    同基线：不做应用层鉴权，访问控制依赖网络隔离。
    """

    queryset = models.AnalysisRule.objects.all()
    serializer_class = serializers.AnalysisRuleSerializer
    pagination_class = pagination.StandardPagination
    lookup_field = "code"

    @staticmethod
    def _creator(request) -> str:
        return getattr(getattr(request, "user", None), "username", "") or ""

    @staticmethod
    def _defaults(declared, creator: str) -> dict:
        """
        为「库里还没有」的规则建行时的初值：一律取代码声明的默认值

        表里只存可覆盖的三项——名称、说明与适用层级不落库，那是代码声明的事。
        """
        return {
            "level": declared.default_level.value,
            "enabled": True,
            "thresholds": dict(declared.default_thresholds),
            "creator": creator,
        }

    def list(self, request, **kwargs):
        rows = registry.declared_with_overrides()
        rows.sort(key=lambda row: (row.object_level, row.code))
        return baseviews.ResponseOK(pagination.paginate(self, rows))

    def retrieve(self, request, *args, **kwargs):
        row = registry.declared_row(kwargs.get("code"))
        if row is None:
            return baseviews.ResponseNotFound("规则不存在")
        return baseviews.ResponseOK(self.get_serializer(row).data)

    def update(self, request, *args, **kwargs):
        """
        修改启用开关、级别或阈值。PUT 与 PATCH 同义——三项都是可选的

        库里没有这条规则时**顺手建行**：清单改以代码声明为基底之后，使用者不再需要任何
        「同步」之类的前置动作（design D3）。
        """
        code = kwargs.get("code")
        declared = registry.get_rule(code)
        if declared is None:
            return baseviews.ResponseNotFound("规则不存在")
        serializer = serializers.AnalysisRuleUpdateSerializer(data=request.data, context={"rule": declared})
        if not serializer.is_valid():
            return baseviews.ResponseBadRequest(common.ToolUtil.format_drf_error(serializer.errors))
        data = serializer.validated_data
        instance, _ = models.AnalysisRule.objects.get_or_create(
            code=code,
            is_deleted=False,
            defaults=self._defaults(declared, self._creator(request)),
        )
        for field in ("enabled", "level", "thresholds"):
            if field in data:
                setattr(instance, field, data[field])
        instance.save()
        # 走「声明 ⊕ 覆盖」的统一出口：名称与说明不在表里，得由注册表注入后序列化器才拿得到
        return baseviews.ResponseOK(self.get_serializer(registry.declared_row(code)).data)

    def partial_update(self, request, *args, **kwargs):
        return self.update(request, *args, **kwargs)

    def create(self, request, **kwargs):
        # 凭空建一条库记录没有对应的代码实现，分析时会被忽略，不如明确拒绝
        return baseviews.ResponseBadRequest("规则来自代码声明，请改用修改接口调整某条已有规则")

    def destroy(self, request, *args, **kwargs):
        # 删掉库记录只会退回代码默认值，达不到「删除规则」的效果
        return baseviews.ResponseBadRequest("规则来自代码声明，不支持删除；如需停用请改启用开关")

    @action(detail=True, methods=["POST"], url_path="reset")
    def reset(self, request, **kwargs):
        """
        把可覆盖项恢复为代码声明的默认值

        从未被调整过的规则库里没有记录，也就没有可恢复的东西——如实报错，而不是悄悄
        建一行再抹掉，那会让「恢复默认」看起来生效了。
        """
        code = kwargs.get("code")
        declared = registry.get_rule(code)
        if declared is None:
            return baseviews.ResponseNotFound("规则不存在")
        instance = models.AnalysisRule.objects.filter(is_deleted=False, code=code).first()
        if instance is None:
            return baseviews.ResponseBadRequest("该规则从未被调整过，没有可恢复的默认值")
        instance.enabled = True
        instance.level = declared.default_level.value
        instance.thresholds = dict(declared.default_thresholds)
        instance.save(update_fields=["enabled", "level", "thresholds", "update_time"])
        return baseviews.ResponseOK(self.get_serializer(registry.declared_row(code)).data)

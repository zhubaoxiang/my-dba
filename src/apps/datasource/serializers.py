"""
数据源与元数据序列化器
"""

from rest_framework import serializers

from apps.datasource import models
from apps.datasource.rules import registry
from utils import custom_enum


class DatasourceSerializer(serializers.ModelSerializer):
    """
    数据源查询序列化，密码不出参
    """

    create_time = serializers.DateTimeField(format="%Y-%m-%d %H:%M:%S")
    db_type_label = serializers.SerializerMethodField(label="数据库类型")

    class Meta:
        model = models.Datasource
        exclude = ["update_time", "password"]

    def get_db_type_label(self, obj):
        return custom_enum.DbTypeEnum(obj.db_type).label


class DatasourceBaseSerializer(serializers.Serializer):
    """
    数据源参数校验基类
    """

    name = serializers.CharField(max_length=64)
    db_type = serializers.ChoiceField(choices=custom_enum.DbTypeEnum.choices)
    host = serializers.CharField(max_length=128)
    port = serializers.IntegerField(min_value=1, max_value=65535)
    db_name = serializers.CharField(max_length=128)
    username = serializers.CharField(max_length=64)
    description = serializers.CharField(max_length=255, required=False, allow_blank=True, default="")
    is_enabled = serializers.BooleanField(default=True)

    def validate_name(self, value):
        queryset = models.Datasource.objects.filter(is_deleted=False, name=value)
        instance = self.context.get("instance")
        if instance is not None:
            queryset = queryset.exclude(id=instance.id)
        if queryset.exists():
            raise serializers.ValidationError("数据源名称已存在")
        return value


class DatasourceCreateSerializer(DatasourceBaseSerializer):
    """
    创建数据源
    """

    password = serializers.CharField(max_length=256, write_only=True)


class DatasourceUpdateSerializer(DatasourceBaseSerializer):
    """
    修改数据源，密码留空表示不修改
    """

    password = serializers.CharField(max_length=256, write_only=True, required=False, allow_blank=True, default="")


class DatasourceTestSerializer(serializers.Serializer):
    """
    未落库的连接测试参数
    """

    db_type = serializers.ChoiceField(choices=custom_enum.DbTypeEnum.choices)
    host = serializers.CharField(max_length=128)
    port = serializers.IntegerField(min_value=1, max_value=65535)
    db_name = serializers.CharField(max_length=128)
    username = serializers.CharField(max_length=64)
    password = serializers.CharField(max_length=256, write_only=True)


class SnapshotSerializer(serializers.ModelSerializer):
    """
    元数据快照，raw_data 体积大，列表与详情都不出参
    """

    create_time = serializers.DateTimeField(format="%Y-%m-%d %H:%M:%S")
    collect_time = serializers.DateTimeField(format="%Y-%m-%d %H:%M:%S")

    class Meta:
        model = models.MetadataSnapshot
        exclude = ["update_time", "raw_data"]


class CollectTaskSerializer(serializers.ModelSerializer):
    """
    采集任务
    """

    create_time = serializers.DateTimeField(format="%Y-%m-%d %H:%M:%S")
    status_label = serializers.SerializerMethodField(label="状态")

    class Meta:
        model = models.CollectTask
        exclude = ["update_time"]

    def get_status_label(self, obj):
        return custom_enum.CollectTaskStatusEnum(obj.status).label


class CatalogIssueSerializer(serializers.ModelSerializer):
    """
    库表问题
    """

    create_time = serializers.DateTimeField(format="%Y-%m-%d %H:%M:%S")
    issue_level_label = serializers.SerializerMethodField(label="严重级别")
    object_level_label = serializers.SerializerMethodField(label="适用层级")

    class Meta:
        model = models.CatalogIssue
        exclude = ["update_time"]

    def get_issue_level_label(self, obj):
        return custom_enum.IssueLevelEnum(obj.issue_level).label

    def get_object_level_label(self, obj):
        return custom_enum.ObjectLevelEnum(obj.object_level).label


class AnalysisRuleSerializer(serializers.ModelSerializer):
    """
    规则查询序列化

    级别与层级同时给出值、标签与「是否与代码声明不一致」，前端不必再维护一份枚举映射，
    也能据此判断「恢复默认」是否有意义。
    """

    create_time = serializers.DateTimeField(format="%Y-%m-%d %H:%M:%S")
    level_label = serializers.SerializerMethodField(label="严重级别")
    object_level_label = serializers.SerializerMethodField(label="适用层级")
    is_overridden = serializers.SerializerMethodField(label="可覆盖项是否被改过")

    class Meta:
        model = models.AnalysisRule
        exclude = ["update_time"]

    def get_level_label(self, obj):
        return custom_enum.IssueLevelEnum(obj.level).label

    def get_object_level_label(self, obj):
        return custom_enum.ObjectLevelEnum(obj.object_level).label

    def get_is_overridden(self, obj):
        declared = registry.get_rule(obj.code)
        if declared is None:
            return False
        return (
            not obj.enabled
            or obj.level != declared.default_level.value
            or (obj.thresholds or {}) != declared.default_thresholds
        )


class AnalysisRuleUpdateSerializer(serializers.Serializer):
    """
    修改规则的可覆盖项

    只接受**启用、级别、阈值**三项。名称、说明与适用层级来自代码声明——在库里改它们
    会让声明与库长期不一致，且下一次同步就被覆盖回来，不如不给改。
    """

    enabled = serializers.BooleanField(required=False)
    level = serializers.ChoiceField(choices=custom_enum.IssueLevelEnum.choices, required=False)
    thresholds = serializers.DictField(required=False)

    def validate_thresholds(self, value):
        """
        阈值必须落在该规则声明的键上且为数字

        写错键名不会报错、只会静默不生效，是这类配置最难排查的一类问题，故在此拦下。
        """
        declared = self.context.get("rule")
        defaults = declared.default_thresholds if declared is not None else {}
        for key, raw in value.items():
            if defaults and key not in defaults:
                raise serializers.ValidationError(f"未知的阈值项：{key}")
            if isinstance(raw, bool) or not isinstance(raw, int | float):
                raise serializers.ValidationError(f"阈值 {key} 必须是数字")
        return value

    def validate(self, attrs):
        if not attrs:
            raise serializers.ValidationError("至少要提供 enabled、level、thresholds 之一")
        return attrs


class CatalogTableSerializer(serializers.Serializer):
    """
    快照中的表摘要（数据来自 JSON，非模型）
    """

    schema = serializers.CharField()
    name = serializers.CharField()
    comment = serializers.CharField()
    row_count = serializers.IntegerField()
    data_size = serializers.IntegerField()
    index_size = serializers.IntegerField()
    total_size = serializers.IntegerField()
    column_count = serializers.IntegerField()
    index_count = serializers.IntegerField()

"""
数据源与元数据序列化器
"""

from rest_framework import serializers

from apps.datasource import models
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

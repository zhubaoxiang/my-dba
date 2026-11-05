"""
测试库预置的入参与会话序列化
"""

from rest_framework import serializers

from apps.datasource import services as datasource_services
from apps.provisioning import models
from utils import custom_enum


class ProvisionTableSerializer(serializers.Serializer):
    """
    选中的表与行数
    """

    schema = serializers.CharField(max_length=128, required=False, allow_blank=True, default="")
    name = serializers.CharField(max_length=128)
    rows = serializers.IntegerField(min_value=1, max_value=100000)


class ProvisionPreviewSerializer(serializers.Serializer):
    datasource_id = serializers.IntegerField()
    tables = ProvisionTableSerializer(many=True, allow_empty=False)
    seed = serializers.IntegerField(required=False, allow_null=True, min_value=0, max_value=2**31 - 1)


class ProvisionExecuteSerializer(ProvisionPreviewSerializer):
    db_name = serializers.CharField(max_length=128)
    confirm = serializers.BooleanField(default=False)


class ManageRowsSerializer(serializers.Serializer):
    """
    向已创建的表追加数据
    """

    schema = serializers.CharField(max_length=128, required=False, allow_blank=True, default="")
    name = serializers.CharField(max_length=128)
    rows = serializers.IntegerField(min_value=1, max_value=100000)
    seed = serializers.IntegerField(required=False, allow_null=True, min_value=0, max_value=2**31 - 1)


class TableRefSerializer(serializers.Serializer):
    """
    定位已创建测试库中的一张表
    """

    schema = serializers.CharField(max_length=128, required=False, allow_blank=True, default="")
    name = serializers.CharField(max_length=128)


class ProvisionedDatabaseSerializer(serializers.ModelSerializer):
    """
    预置记录；含数据源名称与状态文案，不含任何凭据
    """

    create_time = serializers.DateTimeField(format="%Y-%m-%d %H:%M:%S")
    status_label = serializers.SerializerMethodField(label="状态")
    datasource_name = serializers.SerializerMethodField(label="数据源")

    class Meta:
        model = models.ProvisionedDatabase
        exclude = ["update_time", "is_deleted"]

    def get_status_label(self, obj):
        return custom_enum.ProvisionStatusEnum(obj.status).label

    def get_datasource_name(self, obj):
        brief = datasource_services.datasource_brief(obj.datasource_id)
        return brief["name"] if brief else ""

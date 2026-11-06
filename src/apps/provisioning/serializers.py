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


class TestDataParseSerializer(serializers.Serializer):
    """
    手动输入表结构：解析 CREATE TABLE / CREATE INDEX / ALTER TABLE
    """

    db_type = serializers.ChoiceField(choices=custom_enum.DbTypeEnum.choices)
    ddl = serializers.CharField(max_length=200000, trim_whitespace=False)


class TestDataGenerateSerializer(serializers.Serializer):
    """
    生成测试数据文件：数据源模式或手动表结构模式，二选一

    两种模式都只读结构/快照，不连接目标库。
    """

    datasource_id = serializers.IntegerField(required=False, allow_null=True)
    db_type = serializers.ChoiceField(choices=custom_enum.DbTypeEnum.choices, required=False)
    ddl = serializers.CharField(max_length=200000, required=False, allow_blank=True, trim_whitespace=False)
    tables = ProvisionTableSerializer(many=True, allow_empty=False)
    seed = serializers.IntegerField(required=False, allow_null=True, min_value=0, max_value=2**31 - 1)
    include_ddl = serializers.BooleanField(default=False)

    def validate(self, attrs):
        has_datasource = attrs.get("datasource_id") is not None
        has_ddl = bool((attrs.get("ddl") or "").strip())
        if has_datasource == has_ddl:
            raise serializers.ValidationError("请且只提供 datasource_id 或 ddl")
        if has_ddl and attrs.get("db_type") is None:
            raise serializers.ValidationError("手动提供表结构时必须选择数据库类型")
        return attrs


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

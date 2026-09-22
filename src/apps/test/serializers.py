"""
serializers
"""

from rest_framework import serializers

from apps.test import models
from utils import custom_enum


class MavenEnforcerRuleSerializer(serializers.ModelSerializer):
    """
    二方包规则序列化
    """

    create_time = serializers.DateTimeField(format="%Y-%m-%d %H:%M:%S")
    rule_type = serializers.SerializerMethodField(label="规则类型")

    class Meta:
        model = models.MavenEnforcerRules
        exclude = ["update_time"]

    def get_rule_type(self, obj):
        return custom_enum.TaskTypeEnum(obj.rule_type).label


class CreateBannedDependenciesSerializer(serializers.Serializer):
    """
    创建禁止依赖包序列化
    """

    title = serializers.CharField(max_length=128)
    message = serializers.CharField(max_length=128)
    group = serializers.CharField(max_length=32)
    package = serializers.CharField(max_length=32, required=False, allow_blank=True)
    version = serializers.CharField(max_length=32, required=False, allow_blank=True)
    operator = serializers.CharField(max_length=32, required=False, allow_blank=True)
    transitive = serializers.BooleanField(default=True)

    def create(self, validated_data):
        pass

    def update(self, instance, validated_data):
        pass

    def validate(self, attrs):
        if attrs.get("operator") == "in" and "," not in attrs.get("version") and "，" not in attrs.get("version"):
            raise serializers.ValidationError("当选择in时version需要逗号分隔")
        return attrs

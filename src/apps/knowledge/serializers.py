"""
知识问答序列化器
"""

from rest_framework import serializers

from apps.knowledge import models
from utils import custom_enum


class LlmProviderSerializer(serializers.ModelSerializer):
    """
    模型配置查询序列化，API Key 不出参
    """

    create_time = serializers.DateTimeField(format="%Y-%m-%d %H:%M:%S")
    provider_type_label = serializers.SerializerMethodField(label="接口类型")
    model_type_label = serializers.SerializerMethodField(label="用途")

    class Meta:
        model = models.LlmProvider
        exclude = ["update_time", "api_key"]

    def get_provider_type_label(self, obj):
        return custom_enum.ProviderTypeEnum(obj.provider_type).label

    def get_model_type_label(self, obj):
        return custom_enum.ModelTypeEnum(obj.model_type).label


class LlmProviderBaseSerializer(serializers.Serializer):
    """
    模型配置参数校验基类

    一条配置只承载一种用途：`model_type` 决定它是对话模型还是嵌入模型。
    """

    name = serializers.CharField(max_length=64)
    provider_type = serializers.ChoiceField(choices=custom_enum.ProviderTypeEnum.choices)
    model_type = serializers.ChoiceField(
        choices=custom_enum.ModelTypeEnum.choices, default=custom_enum.ModelTypeEnum.CHAT
    )
    base_url = serializers.CharField(max_length=255)
    model_name = serializers.CharField(max_length=128)
    is_enabled = serializers.BooleanField(default=True)

    def validate_name(self, value):
        queryset = models.LlmProvider.objects.filter(is_deleted=False, name=value)
        instance = self.context.get("instance")
        if instance is not None:
            queryset = queryset.exclude(id=instance.id)
        if queryset.exists():
            raise serializers.ValidationError("配置名称已存在")
        return value


class LlmProviderCreateSerializer(LlmProviderBaseSerializer):
    """
    创建模型配置
    """

    api_key = serializers.CharField(max_length=512, write_only=True)


class LlmProviderUpdateSerializer(LlmProviderBaseSerializer):
    """
    修改模型配置，API Key 留空表示不更换
    """

    api_key = serializers.CharField(max_length=512, write_only=True, required=False, allow_blank=True, default="")


class KnowledgeBaseSerializer(serializers.ModelSerializer):
    """
    知识库查询序列化
    """

    create_time = serializers.DateTimeField(format="%Y-%m-%d %H:%M:%S")
    document_count = serializers.SerializerMethodField(label="文档数")

    class Meta:
        model = models.KnowledgeBase
        exclude = ["update_time"]

    def get_document_count(self, obj):
        return models.KbDocument.objects.filter(knowledge_base_id=obj.id, is_deleted=False).count()


class KnowledgeBaseCreateSerializer(serializers.Serializer):
    """
    创建/修改知识库
    """

    name = serializers.CharField(max_length=64)
    description = serializers.CharField(max_length=255, required=False, allow_blank=True, default="")
    is_enabled = serializers.BooleanField(default=True)

    def validate_name(self, value):
        queryset = models.KnowledgeBase.objects.filter(is_deleted=False, name=value)
        instance = self.context.get("instance")
        if instance is not None:
            queryset = queryset.exclude(id=instance.id)
        if queryset.exists():
            raise serializers.ValidationError("知识库名称已存在")
        return value


class KbDocumentSerializer(serializers.ModelSerializer):
    """
    文档查询序列化
    """

    create_time = serializers.DateTimeField(format="%Y-%m-%d %H:%M:%S")
    status_label = serializers.SerializerMethodField(label="状态")
    source_type_label = serializers.SerializerMethodField(label="来源类型")

    class Meta:
        model = models.KbDocument
        exclude = ["update_time"]

    def get_status_label(self, obj):
        return custom_enum.DocumentStatusEnum(obj.status).label

    def get_source_type_label(self, obj):
        return custom_enum.DocumentSourceEnum(obj.source_type).label


class KbImportUrlSerializer(serializers.Serializer):
    """
    通过链接导入文档
    """

    knowledge_base_id = serializers.IntegerField(min_value=1)
    url = serializers.CharField(max_length=1024)
    # 链接的内容要抓取后才知道是否重复，故处理方式需随请求带进异步任务
    on_duplicate = serializers.ChoiceField(choices=custom_enum.DuplicateActionEnum.choices, required=False)

    def validate_url(self, value):
        if not value.strip().lower().startswith(("http://", "https://")):
            raise serializers.ValidationError("链接必须以 http:// 或 https:// 开头")
        return value.strip()


class QaMessageSerializer(serializers.ModelSerializer):
    """
    问答消息
    """

    create_time = serializers.DateTimeField(format="%Y-%m-%d %H:%M:%S")
    role_label = serializers.SerializerMethodField(label="角色")

    class Meta:
        model = models.QaMessage
        exclude = ["update_time"]

    def get_role_label(self, obj):
        return custom_enum.MessageRoleEnum(obj.role).label


class QaSessionSerializer(serializers.ModelSerializer):
    """
    问答会话
    """

    create_time = serializers.DateTimeField(format="%Y-%m-%d %H:%M:%S")
    knowledge_base_name = serializers.SerializerMethodField(label="知识库")
    message_count = serializers.SerializerMethodField(label="消息数")

    class Meta:
        model = models.QaSession
        exclude = ["update_time"]

    def get_knowledge_base_name(self, obj):
        return getattr(obj.knowledge_base, "name", "") if obj.knowledge_base_id else ""

    def get_message_count(self, obj):
        return models.QaMessage.objects.filter(session_id=obj.id, is_deleted=False).count()


class QaAskSerializer(serializers.Serializer):
    """
    提问参数
    """

    question = serializers.CharField(max_length=4000)
    session_id = serializers.IntegerField(min_value=1, required=False, allow_null=True)
    knowledge_base_id = serializers.IntegerField(min_value=1, required=False, allow_null=True)
    datasource_id = serializers.IntegerField(min_value=1, required=False, allow_null=True)
    mode = serializers.ChoiceField(choices=custom_enum.QaModeEnum.choices, required=False)

    def validate_question(self, value):
        value = value.strip()
        if not value:
            raise serializers.ValidationError("问题不能为空")
        return value

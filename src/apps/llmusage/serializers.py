from rest_framework import serializers

from apps.llmusage import models

RANGE_CHOICES = ("today", "7d", "30d")
GROUP_CHOICES = ("user", "model", "feature")


class LlmUsageSerializer(serializers.ModelSerializer):
    create_time = serializers.DateTimeField(format="%Y-%m-%d %H:%M:%S")
    feature_label = serializers.CharField(source="get_feature_display", read_only=True)
    call_type_label = serializers.CharField(source="get_call_type_display", read_only=True)

    class Meta:
        model = models.LlmUsage
        fields = [
            "id",
            "username",
            "feature",
            "feature_label",
            "call_type",
            "call_type_label",
            "model_name",
            "prompt_tokens",
            "completion_tokens",
            "total_tokens",
            "units",
            "latency_ms",
            "success",
            "error_code",
            "request_id",
            "create_time",
        ]


class UsageSummaryQuerySerializer(serializers.Serializer):
    range = serializers.ChoiceField(choices=RANGE_CHOICES, default="7d")
    group_by = serializers.ChoiceField(choices=GROUP_CHOICES, default="user")


class UsageCallsQuerySerializer(serializers.Serializer):
    range = serializers.ChoiceField(choices=RANGE_CHOICES, default="7d")
    success = serializers.BooleanField(required=False)
    min_latency = serializers.IntegerField(required=False, min_value=0)

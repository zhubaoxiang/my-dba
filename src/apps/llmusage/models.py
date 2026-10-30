"""
模型调用用量

每次调用一行，查询时聚合。token 列可空：网关未返回 usage 时留空，不估算。
"""

from django.db import models

from apps.base.models import AbstractTimeFiledModel
from utils import custom_enum


class LlmUsage(AbstractTimeFiledModel):
    """
    一次模型调用的记录

    **不存 prompt 与回答正文**：统计只需要元数据，正文会带来隐私与体积问题。
    `username` 是快照而非外键：账号删除或改名后，历史记录仍要能按原归属查看。
    """

    username = models.CharField(max_length=150, default="", verbose_name="用户")
    feature = models.SmallIntegerField(
        choices=custom_enum.LlmFeatureEnum.choices,
        default=custom_enum.LlmFeatureEnum.OTHER,
        verbose_name="来源功能",
    )
    call_type = models.SmallIntegerField(choices=custom_enum.LlmCallTypeEnum.choices, verbose_name="调用类型")
    provider_id = models.IntegerField(null=True, blank=True, verbose_name="模型配置")
    model_name = models.CharField(max_length=128, default="", blank=True, verbose_name="模型名")
    prompt_tokens = models.IntegerField(null=True, blank=True, verbose_name="输入 token")
    completion_tokens = models.IntegerField(null=True, blank=True, verbose_name="输出 token")
    total_tokens = models.IntegerField(null=True, blank=True, verbose_name="总 token")
    units = models.IntegerField(null=True, blank=True, verbose_name="嵌入文本块数")
    latency_ms = models.IntegerField(default=0, verbose_name="耗时（毫秒）")
    success = models.BooleanField(default=True, verbose_name="是否成功")
    error_code = models.CharField(max_length=32, default="", blank=True, verbose_name="错误分类")
    request_id = models.CharField(max_length=36, default="", blank=True, verbose_name="请求标识")

    class Meta:
        db_table = "llm_usage"

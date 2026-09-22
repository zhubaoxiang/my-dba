from django.db import models

from utils import custom_enum


class MavenEnforcerRules(models.Model):
    """
    二方包规则表
    """

    title = models.CharField(default="", max_length=128, verbose_name="规则标题")
    create_time = models.DateTimeField(auto_now_add=True, verbose_name="创建时间")
    update_time = models.DateTimeField(auto_now=True, verbose_name="更新时间")
    rule_type = models.SmallIntegerField(choices=custom_enum.TaskTypeEnum.choices, verbose_name="规则类型")
    rules = models.JSONField(default=dict, verbose_name="规则详情")
    status = models.CharField(
        default=custom_enum.TaskTypeEnum.HOST,
        choices=custom_enum.TaskTypeEnum.choices,
        max_length=32,
        verbose_name="规则状态",
    )
    creator = models.CharField(default="", max_length=32, verbose_name="创建人")
    is_deleted = models.BooleanField(default=False, verbose_name="是否删除")

    class Meta:
        db_table = "maven_enforcer_rules"

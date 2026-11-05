"""
测试库预置记录

`datasource_id` 只存 id，不建跨模块外键（与 qa_session 同口径）。
"""

from django.db import models

from apps.base.models import AbstractTimeFiledModel
from utils import custom_enum


class ProvisionedDatabase(AbstractTimeFiledModel):
    """
    由测试库预置功能创建的独立数据库
    """

    datasource_id = models.IntegerField(verbose_name="数据源 id")
    db_name = models.CharField(max_length=128, verbose_name="数据库名")
    status = models.SmallIntegerField(
        choices=custom_enum.ProvisionStatusEnum.choices,
        default=custom_enum.ProvisionStatusEnum.CREATING.value,
        verbose_name="状态",
    )
    tables = models.JSONField(default=list, verbose_name="选中的表与行数")
    row_count = models.IntegerField(default=0, verbose_name="写入行数")
    error = models.CharField(max_length=1024, default="", blank=True, verbose_name="失败原因")

    class Meta:
        db_table = "provisioned_database"

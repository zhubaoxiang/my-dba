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
    # 大数据模式的执行上下文：固定快照与种子，worker 在独立进程中复现
    snapshot_id = models.IntegerField(null=True, blank=True, verbose_name="快照 id")
    seed = models.IntegerField(null=True, blank=True, verbose_name="随机种子")
    bulk = models.BooleanField(default=False, verbose_name="是否大数据模式")
    total_rows = models.BigIntegerField(default=0, verbose_name="计划总行数")
    processed_rows = models.BigIntegerField(default=0, verbose_name="已写入行数")
    current_table = models.CharField(max_length=256, default="", blank=True, verbose_name="当前表")
    database_created = models.BooleanField(default=False, verbose_name="目标库是否已创建")
    cancel_requested = models.BooleanField(default=False, verbose_name="是否请求取消")
    worker_id = models.CharField(max_length=128, default="", blank=True, verbose_name="执行 worker")
    heartbeat_time = models.DateTimeField(null=True, blank=True, verbose_name="worker 心跳")
    start_time = models.DateTimeField(null=True, blank=True, verbose_name="开始时间")
    end_time = models.DateTimeField(null=True, blank=True, verbose_name="结束时间")

    class Meta:
        db_table = "provisioned_database"

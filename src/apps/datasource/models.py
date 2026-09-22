"""
数据源纳管与元数据采集模型
author: zhubao
"""

from django.db import models

from apps.base.models import AbstractTimeFiledModel
from utils import custom_enum


class Datasource(AbstractTimeFiledModel):
    """
    被纳管的数据库数据源
    """

    name = models.CharField(max_length=64, verbose_name="数据源名称")
    db_type = models.SmallIntegerField(choices=custom_enum.DbTypeEnum.choices, verbose_name="数据库类型")
    host = models.CharField(max_length=128, verbose_name="主机地址")
    port = models.IntegerField(verbose_name="端口")
    db_name = models.CharField(max_length=128, verbose_name="数据库名")
    username = models.CharField(max_length=64, verbose_name="用户名")
    password = models.CharField(max_length=512, verbose_name="加密后的密码")
    description = models.CharField(max_length=255, default="", blank=True, verbose_name="描述")
    is_enabled = models.BooleanField(default=True, verbose_name="是否启用")

    class Meta:
        db_table = "datasource"


class MetadataSnapshot(AbstractTimeFiledModel):
    """
    一次采集产生的元数据快照，生成后不可变
    """

    datasource = models.ForeignKey(
        Datasource, on_delete=models.DO_NOTHING, db_column="datasource_id", verbose_name="数据源"
    )
    database_version = models.CharField(max_length=128, default="", blank=True, verbose_name="数据库版本")
    schema_count = models.IntegerField(default=0, verbose_name="模式数量")
    table_count = models.IntegerField(default=0, verbose_name="表数量")
    collect_time = models.DateTimeField(verbose_name="采集时间")
    raw_data = models.JSONField(default=dict, verbose_name="原始采集结果")
    unavailable = models.JSONField(default=list, verbose_name="未采集到的项")

    class Meta:
        db_table = "metadata_snapshot"


class CollectTask(AbstractTimeFiledModel):
    """
    元数据采集任务
    """

    datasource = models.ForeignKey(
        Datasource, on_delete=models.DO_NOTHING, db_column="datasource_id", verbose_name="数据源"
    )
    snapshot = models.ForeignKey(
        MetadataSnapshot,
        on_delete=models.DO_NOTHING,
        db_column="snapshot_id",
        null=True,
        blank=True,
        verbose_name="产出快照",
    )
    status = models.SmallIntegerField(
        choices=custom_enum.CollectTaskStatusEnum.choices,
        default=custom_enum.CollectTaskStatusEnum.PENDING,
        verbose_name="状态",
    )
    start_time = models.DateTimeField(null=True, blank=True, verbose_name="开始时间")
    end_time = models.DateTimeField(null=True, blank=True, verbose_name="结束时间")
    fail_reason = models.CharField(max_length=1024, default="", blank=True, verbose_name="失败原因")

    class Meta:
        db_table = "collect_task"


class CatalogIssue(AbstractTimeFiledModel):
    """
    库表健康问题
    """

    datasource = models.ForeignKey(
        Datasource, on_delete=models.DO_NOTHING, db_column="datasource_id", verbose_name="数据源"
    )
    snapshot = models.ForeignKey(
        MetadataSnapshot, on_delete=models.DO_NOTHING, db_column="snapshot_id", verbose_name="所属快照"
    )
    issue_level = models.SmallIntegerField(choices=custom_enum.IssueLevelEnum.choices, verbose_name="严重级别")
    rule_code = models.CharField(max_length=64, verbose_name="规则标识")
    rule_name = models.CharField(max_length=128, default="", blank=True, verbose_name="规则名称快照")
    object_level = models.SmallIntegerField(choices=custom_enum.ObjectLevelEnum.choices, verbose_name="适用层级")
    target = models.CharField(max_length=512, default="", blank=True, verbose_name="对象定位")
    schema_name = models.CharField(max_length=128, default="", blank=True, verbose_name="模式名")
    table_name = models.CharField(max_length=128, default="", blank=True, verbose_name="表名")
    column_name = models.CharField(max_length=128, default="", blank=True, verbose_name="列名")
    description = models.CharField(max_length=1024, default="", blank=True, verbose_name="问题说明")
    suggestion = models.CharField(max_length=1024, default="", blank=True, verbose_name="改进建议")

    class Meta:
        db_table = "catalog_issue"


class AnalysisRule(AbstractTimeFiledModel):
    """
    分析规则的可覆盖项

    规则清单与默认值来自代码声明（apps/datasource/rules/），本表只存运行时可改的部分：
    启用开关、严重级别、阈值。库中无对应 code 的记录时，分析使用代码声明的默认值。
    """

    # 唯一性由 db 的部分唯一索引保证（未删除范围内唯一），与 Datasource.name 一致，
    # 避免软删除后同 code 无法重建
    code = models.CharField(max_length=64, verbose_name="规则标识")
    name = models.CharField(max_length=128, default="", blank=True, verbose_name="规则名称")
    description = models.CharField(max_length=512, default="", blank=True, verbose_name="规则说明")
    level = models.SmallIntegerField(choices=custom_enum.IssueLevelEnum.choices, verbose_name="严重级别")
    object_level = models.SmallIntegerField(choices=custom_enum.ObjectLevelEnum.choices, verbose_name="适用层级")
    enabled = models.BooleanField(default=True, verbose_name="是否启用")
    thresholds = models.JSONField(default=dict, verbose_name="阈值")

    class Meta:
        db_table = "analysis_rule"

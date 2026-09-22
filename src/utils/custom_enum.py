"""
应用用枚举类

@author: zhubao
"""

from django.db import models


class TaskTypeEnum(models.IntegerChoices):
    """
    任务类型
    """

    HOST = 1, "主机扫描"
    WEB = 2, "WEB扫描"

    @classmethod
    def get_value_by_label(cls, label):
        data_dict = {}
        for choice in cls.choices:
            data_dict[choice[1]] = choice[0]
        return data_dict.get(label)


class DbTypeEnum(models.IntegerChoices):
    """
    数据源数据库类型
    """

    POSTGRESQL = 1, "PostgreSQL"
    MYSQL = 2, "MySQL"


class CollectTaskStatusEnum(models.IntegerChoices):
    """
    元数据采集任务状态
    """

    PENDING = 1, "待执行"
    RUNNING = 2, "执行中"
    SUCCESS = 3, "成功"
    FAILED = 4, "失败"


class IssueLevelEnum(models.IntegerChoices):
    """
    库表问题严重级别
    """

    HIGH = 1, "高"
    MEDIUM = 2, "中"
    LOW = 3, "低"


class ObjectLevelEnum(models.IntegerChoices):
    """
    规则的适用层级
    """

    DATABASE = 1, "库"
    SCHEMA = 2, "模式"
    TABLE = 3, "表"
    COLUMN = 4, "列"

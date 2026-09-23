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


class ProviderTypeEnum(models.IntegerChoices):
    """
    大模型接入的接口类型
    """

    OPENAI_COMPATIBLE = 1, "OpenAI 兼容接口"


class ModelTypeEnum(models.IntegerChoices):
    """
    模型用途

    对话与嵌入分开配置：现实里两者常来自不同服务（例如对话走网关、嵌入走内网
    的 bge 服务），各自独立选「生效」互不影响，一端没配不该拖累另一端。
    """

    CHAT = 1, "对话模型"
    EMBEDDING = 2, "嵌入模型"


class QaModeEnum(models.IntegerChoices):
    """
    问答模式
    """

    AUTO = 1, "自动（先检索，无命中回退通用模型）"
    KNOWLEDGE_ONLY = 2, "仅知识库"
    MODEL_ONLY = 3, "仅通用模型"


class DocumentStatusEnum(models.IntegerChoices):
    """
    文档摄入状态
    """

    PENDING = 1, "待处理"
    PROCESSING = 2, "处理中"
    SUCCESS = 3, "成功"
    FAILED = 4, "失败"


class DocumentSourceEnum(models.IntegerChoices):
    """
    文档来源类型
    """

    FILE = 1, "上传文件"
    URL = 2, "网页链接"


class MessageRoleEnum(models.IntegerChoices):
    """
    问答消息角色
    """

    USER = 1, "提问"
    ASSISTANT = 2, "回答"

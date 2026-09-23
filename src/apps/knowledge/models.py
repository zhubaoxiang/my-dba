"""
知识问答：模型接入配置、文档知识库、问答会话

author: zhubao
"""

from django.db import models

from apps.base.models import AbstractTimeFiledModel
from utils import custom_enum

# 向量维度必须与所选嵌入模型的实际输出一致，也必须与 Qdrant 集合的 size 一致。
#
# 当前值对应实际使用的嵌入服务（1024 维）。改它等于换嵌入模型，必须：
#   1. 改这里的值
#   2. 删掉并重建 Qdrant 集合（集合的 size 不可改）
#   3. 重新摄入全部文档
# 正文都在库内，第 3 步只是重算向量，没有数据风险。
# 刻意不放进配置文件：三处不一致会表现为难以定位的检索异常，写成常量更容易在评审时看见。
EMBEDDING_DIMENSIONS = 1024


class LlmProvider(AbstractTimeFiledModel):
    """
    模型接入配置，一律通过 API 接入

    **一条记录只承载一种用途**（`model_type`）：对话与嵌入分开配置，各自独立选「生效」。
    同一个网关若同时提供两类模型，就建两条记录指向它——重复的是两行配置，
    换来的是两端互不牵制。
    """

    name = models.CharField(max_length=64, verbose_name="配置名称")
    provider_type = models.SmallIntegerField(choices=custom_enum.ProviderTypeEnum.choices, verbose_name="接口类型")
    model_type = models.SmallIntegerField(
        choices=custom_enum.ModelTypeEnum.choices, default=custom_enum.ModelTypeEnum.CHAT, verbose_name="用途"
    )
    base_url = models.CharField(max_length=255, verbose_name="接口地址")
    model_name = models.CharField(max_length=128, verbose_name="模型名")
    api_key = models.CharField(max_length=512, verbose_name="加密后的 API Key")
    is_enabled = models.BooleanField(default=True, verbose_name="是否启用")
    is_active = models.BooleanField(default=False, verbose_name="是否当前生效（同类中唯一）")

    class Meta:
        db_table = "llm_provider"


class KnowledgeBase(AbstractTimeFiledModel):
    """
    知识库
    """

    name = models.CharField(max_length=64, verbose_name="知识库名称")
    description = models.CharField(max_length=255, default="", blank=True, verbose_name="描述")
    is_enabled = models.BooleanField(default=True, verbose_name="是否启用")

    class Meta:
        db_table = "knowledge_base"


class KbDocument(AbstractTimeFiledModel):
    """
    知识库中的一份文档
    """

    knowledge_base = models.ForeignKey(
        KnowledgeBase, on_delete=models.DO_NOTHING, db_column="knowledge_base_id", verbose_name="所属知识库"
    )
    title = models.CharField(max_length=255, verbose_name="标题")
    source_type = models.SmallIntegerField(choices=custom_enum.DocumentSourceEnum.choices, verbose_name="来源类型")
    source = models.CharField(max_length=1024, verbose_name="来源（文件名或 URL）")
    content_hash = models.CharField(max_length=64, default="", blank=True, verbose_name="内容指纹")
    status = models.SmallIntegerField(
        choices=custom_enum.DocumentStatusEnum.choices,
        default=custom_enum.DocumentStatusEnum.PENDING,
        verbose_name="状态",
    )
    fail_reason = models.CharField(max_length=1024, default="", blank=True, verbose_name="失败原因")
    chunk_count = models.IntegerField(default=0, verbose_name="切分块数")

    class Meta:
        db_table = "kb_document"


class KbChunk(AbstractTimeFiledModel):
    """
    文档切分后的块

    **正文以本表为准**，Qdrant 只存向量与检索所需的少量过滤字段，是可随时重建的索引。
    这样软删除、审计、按内容检索 SQL 等既有约定都照旧；Qdrant 侧数据丢失也只是重建索引，
    不涉及正文。

    本表**不存 Qdrant 的点标识**：直接以 `id` 作为点标识，因此索引可由本表纯函数式重建，
    也不存在「向量写成功但标识回写失败」的漂移窗口。

    向量在写入 Qdrant 之前已生成，嵌入失败时整份文档不入库，不留半成品。
    """

    knowledge_base = models.ForeignKey(
        KnowledgeBase, on_delete=models.DO_NOTHING, db_column="knowledge_base_id", verbose_name="所属知识库"
    )
    document = models.ForeignKey(
        KbDocument, on_delete=models.DO_NOTHING, db_column="document_id", verbose_name="所属文档"
    )
    ordinal = models.IntegerField(verbose_name="块序号")
    heading_path = models.CharField(max_length=512, default="", blank=True, verbose_name="标题路径")
    content = models.TextField(verbose_name="块内容")

    class Meta:
        db_table = "kb_chunk"


class QaSession(AbstractTimeFiledModel):
    """
    一次问答会话
    """

    title = models.CharField(max_length=128, default="", blank=True, verbose_name="会话标题")
    knowledge_base = models.ForeignKey(
        KnowledgeBase,
        on_delete=models.DO_NOTHING,
        db_column="knowledge_base_id",
        null=True,
        blank=True,
        verbose_name="默认知识库",
    )
    mode = models.SmallIntegerField(
        choices=custom_enum.QaModeEnum.choices, default=custom_enum.QaModeEnum.AUTO, verbose_name="默认模式"
    )

    class Meta:
        db_table = "qa_session"


class QaMessage(AbstractTimeFiledModel):
    """
    会话中的一条消息

    回答侧同时记录来源、调用过的工具与是否回退——「不许凭空编造来源」与
    「回退不得静默」两条约束的落点。
    """

    session = models.ForeignKey(QaSession, on_delete=models.DO_NOTHING, db_column="session_id", verbose_name="所属会话")
    role = models.SmallIntegerField(choices=custom_enum.MessageRoleEnum.choices, verbose_name="角色")
    mode = models.SmallIntegerField(
        choices=custom_enum.QaModeEnum.choices, default=custom_enum.QaModeEnum.AUTO, verbose_name="使用的模式"
    )
    content = models.TextField(verbose_name="内容")
    sources = models.JSONField(default=list, verbose_name="引用来源")
    tools_used = models.JSONField(default=list, verbose_name="调用过的工具")
    is_fallback = models.BooleanField(default=False, verbose_name="是否由通用模型回退产生")

    class Meta:
        db_table = "qa_message"

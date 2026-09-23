"""
知识问答视图
"""

from django.db import transaction
from rest_framework.decorators import action

from apps.base import baseviews
from apps.knowledge import ingest, llm, models, serializers, vectorstore
from apps.knowledge.qa import agent
from utils import background, common, crypto, custom_enum, pagination
from utils.logger import get_logger

LOGGER = get_logger("knowledge.log")

_MUTABLE_FIELDS = ("name", "provider_type", "model_type", "base_url", "model_name", "is_enabled")
_KB_MUTABLE_FIELDS = ("name", "description", "is_enabled")


def _sanitize(message: str, provider) -> str:
    """
    兜底：确保失败原因里不出现 API Key（外部 SDK 的报错内容不受我们控制）
    """
    try:
        secret = crypto.decrypt(provider.api_key)
    except Exception:  # noqa: BLE001 解密失败时没什么可清理的
        return message
    return message.replace(secret, "***") if secret else message


class LlmProviderView(baseviews.AnyLogin):
    """
    模型接入配置管理

    同基线：不做应用层鉴权，访问控制依赖网络隔离。
    """

    queryset = models.LlmProvider.objects.all()
    serializer_class = serializers.LlmProviderSerializer
    pagination_class = pagination.StandardPagination

    @staticmethod
    def _creator(request) -> str:
        return getattr(getattr(request, "user", None), "username", "") or ""

    def _get_instance(self, kwargs):
        return models.LlmProvider.objects.filter(is_deleted=False, id=kwargs.get("pk")).first()

    def list(self, request, **kwargs):
        queryset = self.get_queryset().filter(is_deleted=False).order_by("-is_active", "-id")
        return baseviews.ResponseOK(pagination.paginate(self, queryset))

    def retrieve(self, request, *args, **kwargs):
        instance = self._get_instance(kwargs)
        if instance is None:
            return baseviews.ResponseNotFound("模型配置不存在")
        return baseviews.ResponseOK(self.get_serializer(instance).data)

    def create(self, request, **kwargs):
        serializer = serializers.LlmProviderCreateSerializer(data=request.data)
        if not serializer.is_valid():
            return baseviews.ResponseBadRequest(common.ToolUtil.format_drf_error(serializer.errors))
        data = serializer.validated_data
        instance = models.LlmProvider.objects.create(
            name=data["name"],
            provider_type=data["provider_type"],
            model_type=data["model_type"],
            base_url=data["base_url"],
            model_name=data["model_name"],
            api_key=crypto.encrypt(data["api_key"]),
            is_enabled=data.get("is_enabled", True),
            creator=self._creator(request),
        )
        # 该类用途下若还没有生效项，首条自动生效——避免出现「有配置但无生效项」而直接失败。
        # 注意是按用途分别判断：新加一条嵌入配置不该顶掉已有的对话配置。
        if not models.LlmProvider.objects.filter(
            is_deleted=False, is_active=True, model_type=instance.model_type
        ).exists():
            instance.is_active = True
            instance.save(update_fields=["is_active", "update_time"])
        return baseviews.ResponseOK(self.get_serializer(instance).data)

    def update(self, request, *args, **kwargs):
        instance = self._get_instance(kwargs)
        if instance is None:
            return baseviews.ResponseNotFound("模型配置不存在")
        serializer = serializers.LlmProviderUpdateSerializer(data=request.data, context={"instance": instance})
        if not serializer.is_valid():
            return baseviews.ResponseBadRequest(common.ToolUtil.format_drf_error(serializer.errors))
        data = serializer.validated_data
        for field in _MUTABLE_FIELDS:
            setattr(instance, field, data[field])
        if data.get("api_key"):
            instance.api_key = crypto.encrypt(data["api_key"])
        instance.save()
        return baseviews.ResponseOK(self.get_serializer(instance).data)

    def destroy(self, request, *args, **kwargs):
        instance = self._get_instance(kwargs)
        if instance is None:
            return baseviews.ResponseNotFound("模型配置不存在")
        instance.is_deleted = True
        instance.is_active = False
        instance.save(update_fields=["is_deleted", "is_active", "update_time"])
        return baseviews.ResponseOK(None)

    @action(detail=True, methods=["POST"], url_path="activate")
    def activate(self, request, **kwargs):
        """
        设为当前生效配置
        """
        instance = self._get_instance(kwargs)
        if instance is None:
            return baseviews.ResponseNotFound("模型配置不存在")
        if not instance.is_enabled:
            return baseviews.ResponseExpectationFailed("已停用的配置不能设为生效")

        # 只取消**同一用途**下原有的生效项：库上的部分唯一索引按 model_type 约束「每类至多一条」
        with transaction.atomic():
            models.LlmProvider.objects.filter(is_deleted=False, is_active=True, model_type=instance.model_type).exclude(
                id=instance.id
            ).update(is_active=False)
            instance.is_active = True
            instance.save(update_fields=["is_active", "update_time"])
        return baseviews.ResponseOK(self.get_serializer(instance).data)

    @action(detail=True, methods=["POST"], url_path="test")
    def test_connection(self, request, **kwargs):
        """
        连通性测试：发一次最小请求，不落库问答内容
        """
        instance = self._get_instance(kwargs)
        if instance is None:
            return baseviews.ResponseNotFound("模型配置不存在")
        try:
            info = llm.test_connection(instance)
        except crypto.CryptoKeyError as exc:
            return baseviews.ResponseError(str(exc))
        except Exception as exc:  # noqa: BLE001 外部接口失败属预期结果，转为业务响应
            reason = _sanitize(str(exc), instance)
            LOGGER.warning("模型连通性测试失败 provider_id=%s err=%s", instance.id, reason)
            return baseviews.ResponseExpectationFailed(f"连接失败: {reason}")
        return baseviews.ResponseOK(info)


class KnowledgeBaseView(baseviews.AnyLogin):
    """
    知识库管理

    同基线：不做应用层鉴权，访问控制依赖网络隔离。
    """

    queryset = models.KnowledgeBase.objects.all()
    serializer_class = serializers.KnowledgeBaseSerializer
    pagination_class = pagination.StandardPagination

    @staticmethod
    def _creator(request) -> str:
        return getattr(getattr(request, "user", None), "username", "") or ""

    def _get_instance(self, kwargs):
        return models.KnowledgeBase.objects.filter(is_deleted=False, id=kwargs.get("pk")).first()

    def list(self, request, **kwargs):
        queryset = self.get_queryset().filter(is_deleted=False).order_by("-id")
        return baseviews.ResponseOK(pagination.paginate(self, queryset))

    def retrieve(self, request, *args, **kwargs):
        instance = self._get_instance(kwargs)
        if instance is None:
            return baseviews.ResponseNotFound("知识库不存在")
        return baseviews.ResponseOK(self.get_serializer(instance).data)

    def create(self, request, **kwargs):
        serializer = serializers.KnowledgeBaseCreateSerializer(data=request.data)
        if not serializer.is_valid():
            return baseviews.ResponseBadRequest(common.ToolUtil.format_drf_error(serializer.errors))
        data = serializer.validated_data
        instance = models.KnowledgeBase.objects.create(
            name=data["name"],
            description=data.get("description", ""),
            is_enabled=data.get("is_enabled", True),
            creator=self._creator(request),
        )
        # 集合按需创建，让使用者建完库就能立刻上传
        try:
            vectorstore.ensure_collection()
        except vectorstore.VectorStoreError as exc:
            LOGGER.warning("创建向量集合失败，摄入时会再试: %s", exc)
        return baseviews.ResponseOK(self.get_serializer(instance).data)

    def update(self, request, *args, **kwargs):
        instance = self._get_instance(kwargs)
        if instance is None:
            return baseviews.ResponseNotFound("知识库不存在")
        serializer = serializers.KnowledgeBaseCreateSerializer(data=request.data, context={"instance": instance})
        if not serializer.is_valid():
            return baseviews.ResponseBadRequest(common.ToolUtil.format_drf_error(serializer.errors))
        for field in _KB_MUTABLE_FIELDS:
            setattr(instance, field, serializer.validated_data[field])
        instance.save()
        return baseviews.ResponseOK(self.get_serializer(instance).data)

    def destroy(self, request, *args, **kwargs):
        """
        软删除知识库，并清理其文档、块与 Qdrant 中的向量
        """
        instance = self._get_instance(kwargs)
        if instance is None:
            return baseviews.ResponseNotFound("知识库不存在")
        _purge_chunks(models.KbChunk.objects.filter(knowledge_base_id=instance.id))
        models.KbChunk.objects.filter(knowledge_base_id=instance.id).update(is_deleted=True)
        models.KbDocument.objects.filter(knowledge_base_id=instance.id).update(is_deleted=True)
        instance.is_deleted = True
        instance.save(update_fields=["is_deleted", "update_time"])
        return baseviews.ResponseOK(None)


class KbDocumentView(baseviews.AnyLogin):
    """
    知识库文档：列表、上传、链接导入、重新摄入、软删除
    """

    queryset = models.KbDocument.objects.all()
    serializer_class = serializers.KbDocumentSerializer
    pagination_class = pagination.StandardPagination

    @staticmethod
    def _creator(request) -> str:
        return getattr(getattr(request, "user", None), "username", "") or ""

    def _get_instance(self, kwargs):
        return models.KbDocument.objects.filter(is_deleted=False, id=kwargs.get("pk")).first()

    @staticmethod
    def _knowledge_base(knowledge_base_id):
        return models.KnowledgeBase.objects.filter(is_deleted=False, id=knowledge_base_id).first()

    def list(self, request, **kwargs):
        queryset = self.get_queryset().filter(is_deleted=False)
        knowledge_base_id = request.query_params.get("knowledge_base_id")
        if knowledge_base_id:
            queryset = queryset.filter(knowledge_base_id=knowledge_base_id)
        return baseviews.ResponseOK(pagination.paginate(self, queryset.order_by("-id")))

    def create(self, request, **kwargs):
        """
        上传文件摄入。文件内容随任务驻留内存，不落盘存储
        """
        upload = request.FILES.get("file")
        if not upload:
            return baseviews.ResponseBadRequest("请选择要上传的文件")
        base = self._knowledge_base(request.data.get("knowledge_base_id"))
        if base is None:
            return baseviews.ResponseNotFound("知识库不存在")

        raw = upload.read()
        try:
            title, _ = ingest.parse_file(upload.name, raw)
        except ingest.IngestError as exc:
            return baseviews.ResponseBadRequest(str(exc))

        document = models.KbDocument.objects.create(
            knowledge_base_id=base.id,
            title=title,
            source_type=custom_enum.DocumentSourceEnum.FILE.value,
            source=upload.name[:1024],
            status=custom_enum.DocumentStatusEnum.PENDING.value,
            creator=self._creator(request),
        )
        background.submit(ingest.run_ingest, document_id=document.id, raw=raw)
        return baseviews.ResponseOK(self.get_serializer(document).data)

    @action(detail=False, methods=["POST"], url_path="import-url")
    def import_url(self, request):
        """
        通过链接导入文档
        """
        serializer = serializers.KbImportUrlSerializer(data=request.data)
        if not serializer.is_valid():
            return baseviews.ResponseBadRequest(common.ToolUtil.format_drf_error(serializer.errors))
        data = serializer.validated_data
        base = self._knowledge_base(data["knowledge_base_id"])
        if base is None:
            return baseviews.ResponseNotFound("知识库不存在")

        document = models.KbDocument.objects.create(
            knowledge_base_id=base.id,
            title=data["url"][:255],
            source_type=custom_enum.DocumentSourceEnum.URL.value,
            source=data["url"][:1024],
            status=custom_enum.DocumentStatusEnum.PENDING.value,
            creator=self._creator(request),
        )
        background.submit(ingest.run_ingest, document_id=document.id, url=data["url"])
        return baseviews.ResponseOK(self.get_serializer(document).data)

    @action(detail=True, methods=["POST"], url_path="reingest")
    def reingest(self, request, **kwargs):
        """
        重新摄入：先清掉旧的块与向量，再重跑一次
        """
        document = self._get_instance(kwargs)
        if document is None:
            return baseviews.ResponseNotFound("文档不存在")
        if document.source_type != custom_enum.DocumentSourceEnum.URL.value:
            # 上传的文件没有落盘，无法重新解析
            return baseviews.ResponseExpectationFailed("文件来源的文档无法重新摄入，请重新上传该文件")

        _reset_document(document)
        background.submit(ingest.run_ingest, document_id=document.id, url=document.source)
        return baseviews.ResponseOK(self.get_serializer(document).data)

    def destroy(self, request, *args, **kwargs):
        document = self._get_instance(kwargs)
        if document is None:
            return baseviews.ResponseNotFound("文档不存在")
        _reset_document(document)
        document.is_deleted = True
        document.save(update_fields=["is_deleted", "update_time"])
        return baseviews.ResponseOK(None)


def _purge_chunks(chunk_queryset):
    """
    删除一批块对应的向量。

    向量清不掉不该挡住业务侧删除——库内数据是事实来源，残留的向量在「检索后回库取正文」
    时会被过滤掉，只影响检索的候选集大小。
    """
    chunk_ids = list(chunk_queryset.values_list("id", flat=True))
    if not chunk_ids:
        return
    try:
        vectorstore.delete_chunks(chunk_ids)
    except vectorstore.VectorStoreError as exc:
        LOGGER.warning("清理向量失败，仅影响检索候选集: %s", exc)


def _reset_document(document):
    """
    清掉一份文档的块与向量，并把状态复位为待处理
    """
    _purge_chunks(models.KbChunk.objects.filter(document_id=document.id))
    models.KbChunk.objects.filter(document_id=document.id).delete()
    document.chunk_count = 0
    document.status = custom_enum.DocumentStatusEnum.PENDING.value
    document.fail_reason = ""
    document.save(update_fields=["chunk_count", "status", "fail_reason", "update_time"])


class QaSessionView(baseviews.AnyLogin):
    """
    问答会话：会话列表、消息回看、提问、软删除
    """

    queryset = models.QaSession.objects.all()
    serializer_class = serializers.QaSessionSerializer
    pagination_class = pagination.StandardPagination

    @staticmethod
    def _creator(request) -> str:
        return getattr(getattr(request, "user", None), "username", "") or ""

    def _get_instance(self, kwargs):
        return models.QaSession.objects.filter(is_deleted=False, id=kwargs.get("pk")).first()

    def list(self, request, **kwargs):
        queryset = self.get_queryset().filter(is_deleted=False).order_by("-id")
        return baseviews.ResponseOK(pagination.paginate(self, queryset))

    def retrieve(self, request, *args, **kwargs):
        """
        会话详情，带全部消息（含来源与是否回退）
        """
        instance = self._get_instance(kwargs)
        if instance is None:
            return baseviews.ResponseNotFound("会话不存在")
        messages = models.QaMessage.objects.filter(session_id=instance.id, is_deleted=False).order_by("id")
        data = self.get_serializer(instance).data
        data["messages"] = serializers.QaMessageSerializer(messages, many=True).data
        return baseviews.ResponseOK(data)

    def destroy(self, request, *args, **kwargs):
        instance = self._get_instance(kwargs)
        if instance is None:
            return baseviews.ResponseNotFound("会话不存在")
        models.QaMessage.objects.filter(session_id=instance.id).update(is_deleted=True)
        instance.is_deleted = True
        instance.save(update_fields=["is_deleted", "update_time"])
        return baseviews.ResponseOK(None)

    @action(detail=False, methods=["POST"], url_path="ask")
    def ask(self, request):
        """
        提问。**先答成功再落库**，避免失败时留下一个空会话
        """
        serializer = serializers.QaAskSerializer(data=request.data)
        if not serializer.is_valid():
            return baseviews.ResponseBadRequest(common.ToolUtil.format_drf_error(serializer.errors))
        data = serializer.validated_data

        session = None
        if data.get("session_id"):
            session = self._get_instance({"pk": data["session_id"]})
            if session is None:
                return baseviews.ResponseNotFound("会话不存在")

        knowledge_base_id = data.get("knowledge_base_id") or (session.knowledge_base_id if session else None)
        mode = data.get("mode") or (session.mode if session else custom_enum.QaModeEnum.AUTO.value)
        history = _session_history(session) if session else []

        try:
            result = agent.answer(
                question=data["question"],
                knowledge_base_id=knowledge_base_id,
                datasource_id=data.get("datasource_id"),
                mode=mode,
                history=history,
            )
        except agent.QaError as exc:
            return baseviews.ResponseExpectationFailed(str(exc))
        except vectorstore.VectorStoreError as exc:
            # 与「无命中」必须区分：这是服务故障，不能让它看起来像查无结果
            LOGGER.error("问答失败（向量服务）: %s", exc)
            return baseviews.ResponseError(f"向量服务不可用：{exc}")
        except Exception as exc:  # noqa: BLE001 模型调用等外部依赖
            LOGGER.error("问答失败: %s", exc)
            return baseviews.ResponseError(f"问答失败：{exc}")

        creator = self._creator(request)
        if session is None:
            session = models.QaSession.objects.create(
                title=data["question"][:128],
                knowledge_base_id=knowledge_base_id,
                mode=mode,
                creator=creator,
            )
        models.QaMessage.objects.create(
            session_id=session.id,
            role=custom_enum.MessageRoleEnum.USER.value,
            mode=mode,
            content=data["question"],
            creator=creator,
        )
        answer_message = models.QaMessage.objects.create(
            session_id=session.id,
            role=custom_enum.MessageRoleEnum.ASSISTANT.value,
            mode=mode,
            content=result["content"],
            sources=result["sources"],
            tools_used=result["tools_used"],
            is_fallback=result["is_fallback"],
            creator=creator,
        )
        return baseviews.ResponseOK(
            {
                "session_id": session.id,
                "message_id": answer_message.id,
                "content": result["content"],
                "sources": result["sources"],
                "tools_used": result["tools_used"],
                "is_fallback": result["is_fallback"],
                "note": result["note"],
                "mode": mode,
            }
        )


def _session_history(session) -> list:
    """
    取会话历史供 agent 组装上下文。截断由 agent 按配置负责。
    """
    return [
        {"role": message.role, "content": message.content}
        for message in models.QaMessage.objects.filter(session_id=session.id, is_deleted=False).order_by("id")
    ]

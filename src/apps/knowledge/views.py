"""
知识问答视图
"""

import json

from django.db import transaction
from django.http import StreamingHttpResponse
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
        ingest.purge_chunks(models.KbChunk.objects.filter(knowledge_base_id=instance.id))
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

        上传是同步的、内容当时就能拿到，所以重复来源在这里就能判定——
        命中且使用者没表态时**返回提示让前端问一次**，而不是自作主张。
        """
        upload = request.FILES.get("file")
        if not upload:
            return baseviews.ResponseBadRequest("请选择要上传的文件")
        base = self._knowledge_base(request.data.get("knowledge_base_id"))
        if base is None:
            return baseviews.ResponseNotFound("知识库不存在")

        raw = upload.read()
        try:
            title, text = ingest.parse_file(upload.name, raw)
        except ingest.IngestError as exc:
            return baseviews.ResponseBadRequest(str(exc))

        digest = ingest.content_hash(text)
        action = _parse_duplicate_action(request.data.get("on_duplicate"))
        duplicate = ingest.find_duplicate(base.id, digest)
        if duplicate is not None:
            if action is None:
                return baseviews.ResponseExpectationFailed(
                    f"该知识库中已存在内容相同的文档《{duplicate.title}》(id={duplicate.id})。"
                    f"覆盖请带 on_duplicate={custom_enum.DuplicateActionEnum.OVERWRITE.value}，"
                    f"跳过请带 on_duplicate={custom_enum.DuplicateActionEnum.SKIP.value}"
                )
            if action == custom_enum.DuplicateActionEnum.SKIP:
                data = self.get_serializer(duplicate).data
                data["duplicated"] = True
                return baseviews.ResponseOK(data)
            ingest.reset_document(duplicate)
            duplicate.is_deleted = True
            duplicate.save(update_fields=["is_deleted", "update_time"])

        document = models.KbDocument.objects.create(
            knowledge_base_id=base.id,
            title=title,
            source_type=custom_enum.DocumentSourceEnum.FILE.value,
            source=upload.name[:1024],
            content_hash=digest,
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
        action = data.get("on_duplicate")
        background.submit(
            ingest.run_ingest,
            document_id=document.id,
            url=data["url"],
            on_duplicate=action.value if action else None,
        )
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

        ingest.reset_document(document)
        background.submit(ingest.run_ingest, document_id=document.id, url=document.source)
        return baseviews.ResponseOK(self.get_serializer(document).data)

    def destroy(self, request, *args, **kwargs):
        document = self._get_instance(kwargs)
        if document is None:
            return baseviews.ResponseNotFound("文档不存在")
        ingest.reset_document(document)
        document.is_deleted = True
        document.save(update_fields=["is_deleted", "update_time"])
        return baseviews.ResponseOK(None)


def _parse_duplicate_action(raw):
    """
    解析重复处理方式；未提供或非法都返回 None，由调用方决定默认行为
    """
    if raw in (None, ""):
        return None
    try:
        return custom_enum.DuplicateActionEnum(int(raw))
    except (TypeError, ValueError):
        return None


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

    @action(detail=False, methods=["POST"], url_path="ask-stream")
    def ask_stream(self, request):
        """
        流式提问，边生成边推送

        与同步接口的两点差别（见 design.md D6/D7）：

        1. **开流前的失败仍走统一响应格式**——那时 HTTP 状态码还有意义；开流之后
           只能走流内 `error` 事件，因为状态码已经发出去了
        2. **先落库再生成**——否则生成被中止时，提问与半截回答都会丢
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

        # 预检与预检索都在这里做完，因此它**不是**生成器：生成器的函数体要到首次迭代
        # 才执行，那时响应头已经发出，失败就没法再改回统一格式的 JSON 了
        try:
            prep = agent.prepare_stream(
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
            LOGGER.error("流式问答失败（向量服务）: %s", exc)
            return baseviews.ResponseError(f"向量服务不可用：{exc}")

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
            content="",
            creator=creator,
        )

        response = StreamingHttpResponse(
            _stream_body(answer_message, prep, data["question"]),
            content_type="text/event-stream; charset=utf-8",
        )
        response["Cache-Control"] = "no-cache"
        response["X-Accel-Buffering"] = "no"
        return response


def _sse(event: str, payload: dict) -> str:
    """
    SSE 帧。data 用 JSON，便于前端按事件类型解析
    """
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _persist_stream_message(message, content: str, done: dict, completed: bool):
    """
    流结束时一次性写入。**不逐 token 写库**——那会产生数百次 UPDATE。

    代价是进程在生成过程中被杀会丢失这半截内容（与「进程内后台任务重启即丢」同级），
    见 design.md D8。
    """
    message.content = content
    message.sources = done.get("sources") or []
    message.tools_used = done.get("tools_used") or []
    message.is_fallback = bool(done.get("is_fallback"))
    message.is_complete = completed
    message.save(update_fields=["content", "sources", "tools_used", "is_fallback", "is_complete", "update_time"])


def _stream_body(answer_message, prep, question: str):
    """
    产出 SSE 帧，并在结束时落库

    正常结束由 `done` 事件标志；客户端断开时 Django 关闭本生成器（GeneratorExit），
    此时不能再 yield，只能靠 `finally` 把已收到的内容存下来并标记为不完整。
    """
    text_parts, done, completed = [], {}, False
    try:
        for event in agent.stream_events(prep, question):
            kind = event["type"]
            if kind == "status":
                yield _sse("status", {"text": event["text"]})
            elif kind == "delta":
                text_parts.append(event["text"])
                yield _sse("delta", {"text": event["text"]})
            elif kind == "done":
                done, completed = event, True
                yield _sse(
                    "done",
                    {
                        "session_id": answer_message.session_id,
                        "message_id": answer_message.id,
                        "sources": event["sources"],
                        "tools_used": event["tools_used"],
                        "is_fallback": event["is_fallback"],
                        "note": event["note"],
                        "mode": event["mode"],
                        # 收到 done 即完整；流意外结束（无 done）由前端判定为中断
                        "interrupted": False,
                    },
                )
    except Exception as exc:  # noqa: BLE001 开流后失败只能走流内错误事件
        LOGGER.error("流式问答中断: %s", exc)
        yield _sse("error", {"message": f"问答中断：{exc}"})
    finally:
        try:
            _persist_stream_message(answer_message, "".join(text_parts), done, completed)
        except Exception as exc:  # noqa: BLE001 落库失败不该盖住已经推给使用者的内容
            LOGGER.error("流式回答落库失败 message_id=%s err=%s", answer_message.id, exc)


def _session_history(session) -> list:
    """
    取会话历史供 agent 组装上下文。截断由 agent 按配置负责。
    """
    return [
        {"role": message.role, "content": message.content}
        for message in models.QaMessage.objects.filter(session_id=session.id, is_deleted=False).order_by("id")
    ]

"""
知识问答模块单元测试

全部为 SimpleTestCase（**不需要数据库**）：覆盖不依赖存储的部分——凭据保护、失败信息脱敏、
向量存储迁移完整性、摄入的切分与解析、以及三种问答模式的分支。

涉及真实数据库与 Qdrant 的链路（摄入落库、检索回库取正文、问答接口）不放进这里，
否则跑单测就得依赖 PG 与 Qdrant 同时在线；那部分以脚本对着真实服务验证。
"""

from pathlib import Path
from unittest import mock

from django.test import SimpleTestCase

from apps.knowledge import ingest, llm, models, serializers, views
from apps.knowledge.qa import agent
from utils import crypto, custom_enum

_SQL_STRUCT = Path(__file__).resolve().parents[2] / "sql" / "pg_struct.sql"
_SQL_PATCH = Path(__file__).resolve().parents[2] / "sql" / "patch.sql"


class FakeProvider:
    """
    仅提供被调用属性的替身，避免为了构造 ORM 实例而触碰数据库
    """

    def __init__(self, api_key_cipher="", model_type=None, model_name="gpt-4o-mini"):
        self.api_key = api_key_cipher
        self.model_type = (model_type or custom_enum.ModelTypeEnum.CHAT).value
        self.model_name = model_name
        self.base_url = "http://example.invalid/v1"
        self.id = 1


class ProviderSerializerTests(SimpleTestCase):
    """
    模型配置序列化不得带出 API Key
    """

    def test_api_key_excluded_from_output(self):
        cipher = crypto.encrypt("sk-super-secret")
        instance = models.LlmProvider(
            name="内部代理",
            provider_type=custom_enum.ProviderTypeEnum.OPENAI_COMPATIBLE.value,
            model_type=custom_enum.ModelTypeEnum.CHAT.value,
            base_url="http://example.invalid/v1",
            model_name="gpt-4o-mini",
            api_key=cipher,
            is_enabled=True,
            is_active=True,
        )
        data = serializers.LlmProviderSerializer(instance).data
        self.assertNotIn("api_key", data)
        self.assertNotIn("sk-super-secret", str(data))
        self.assertNotIn(cipher, str(data))
        self.assertEqual(data["provider_type_label"], "OpenAI 兼容接口")
        self.assertEqual(data["model_type_label"], "对话模型")

    def test_create_serializer_rejects_duplicate_name(self):
        serializer = serializers.LlmProviderCreateSerializer(
            data={
                "name": "dup",
                "provider_type": custom_enum.ProviderTypeEnum.OPENAI_COMPATIBLE.value,
                "model_type": custom_enum.ModelTypeEnum.CHAT.value,
                "base_url": "http://example.invalid/v1",
                "model_name": "gpt-4o-mini",
                "api_key": "sk-x",
            }
        )
        with mock.patch.object(serializers.models.LlmProvider.objects, "filter") as fake_filter:
            fake_filter.return_value.exclude.return_value.exists.return_value = True
            self.assertFalse(serializer.is_valid())

    def test_model_type_must_be_a_known_choice(self):
        serializer = serializers.LlmProviderCreateSerializer(
            data={
                "name": "x",
                "provider_type": custom_enum.ProviderTypeEnum.OPENAI_COMPATIBLE.value,
                "model_type": 99,
                "base_url": "http://example.invalid/v1",
                "model_name": "m",
                "api_key": "sk-x",
            }
        )
        # 名称唯一性校验会查库，SimpleTestCase 不能碰数据库，这里一并替换掉
        with mock.patch.object(serializers.models.LlmProvider.objects, "filter") as fake_filter:
            fake_filter.return_value.exclude.return_value.exists.return_value = False
            self.assertFalse(serializer.is_valid())
        self.assertIn("model_type", serializer.errors)


class FailureMessageSanitizeTests(SimpleTestCase):
    """
    「失败原因中不含 API Key」——外部 SDK 的报错内容不受我们控制，必须有兜底
    """

    def test_key_is_scrubbed_from_message(self):
        provider = FakeProvider(api_key_cipher=crypto.encrypt("sk-super-secret"))
        message = "401 Unauthorized: invalid key sk-super-secret provided"
        cleaned = views._sanitize(message, provider)
        self.assertNotIn("sk-super-secret", cleaned)
        self.assertIn("***", cleaned)

    def test_message_untouched_when_key_absent(self):
        provider = FakeProvider(api_key_cipher=crypto.encrypt("sk-super-secret"))
        message = "connection timed out"
        self.assertEqual(views._sanitize(message, provider), message)

    def test_undecryptable_key_does_not_break_sanitize(self):
        provider = FakeProvider(api_key_cipher="not-a-valid-cipher")
        message = "some failure"
        self.assertEqual(views._sanitize(message, provider), message)


class EmbeddingBuilderTests(SimpleTestCase):
    """
    用途必须对得上：拿一条「对话模型」的配置去构造嵌入会返回错误结果，
    因此在解密之前就得拦下，避免把「配置用错了」误报成「密钥有问题」
    """

    def test_chat_provider_rejected_by_embedding_builder(self):
        provider = FakeProvider(api_key_cipher="not-a-valid-cipher", model_type=custom_enum.ModelTypeEnum.CHAT)
        with self.assertRaises(ValueError):
            llm.build_embeddings(provider)

    def test_dimension_mismatch_reports_both_sizes_and_model(self):
        """
        维度不一致要在**拿到向量的那一刻**就报清楚。
        否则会拖到写 Qdrant 时才以「维度不匹配」失败，或者更糟——静默写进去检索全乱。
        """
        provider = FakeProvider(model_type=custom_enum.ModelTypeEnum.EMBEDDING, model_name="some-embedding-model")
        # 相对常量构造「错误维度」，避免把当前值写死进测试（换维度后测试自己会失效）
        wrong = models.EMBEDDING_DIMENSIONS * 4
        with mock.patch.object(llm, "build_embeddings") as fake:
            fake.return_value.embed_query.return_value = [0.0] * wrong
            with self.assertRaises(ValueError) as ctx:
                llm.embed_query(provider, "文本")
        message = str(ctx.exception)
        self.assertIn(str(wrong), message)
        self.assertIn(str(models.EMBEDDING_DIMENSIONS), message)
        self.assertIn("some-embedding-model", message)

    def test_matching_dimension_passes(self):
        provider = FakeProvider(model_type=custom_enum.ModelTypeEnum.EMBEDDING, model_name="ok")
        with mock.patch.object(llm, "build_embeddings") as fake:
            fake.return_value.embed_documents.return_value = [[0.0] * models.EMBEDDING_DIMENSIONS]
            self.assertEqual(len(llm.embed_documents(provider, ["x"])[0]), models.EMBEDDING_DIMENSIONS)

    def test_dimensions_param_is_sent_with_the_system_value(self):
        """
        必须显式指定输出维度：向量库集合的 size 固定，而不少嵌入模型原生维度很大
        （实测某个模型原生 4096），靠该参数截到系统约定值可省数倍内存与存储。
        """
        provider = FakeProvider(api_key_cipher=crypto.encrypt("k"), model_type=custom_enum.ModelTypeEnum.EMBEDDING)
        embeddings = llm.build_embeddings(provider)
        self.assertEqual(embeddings._invocation_params.get("dimensions"), models.EMBEDDING_DIMENSIONS)

    def test_ctx_length_check_is_disabled(self):
        """
        默认开启时 langchain 会把文本切成 token id 再发，OpenAI 兼容端点要的是原文，
        会直接 400。这条守住那个开关不被改回去。
        """
        provider = FakeProvider(api_key_cipher=crypto.encrypt("k"), model_type=custom_enum.ModelTypeEnum.EMBEDDING)
        self.assertFalse(llm.build_embeddings(provider).check_embedding_ctx_length)

    def test_provider_lookup_is_per_model_type(self):
        """
        两类用途各自独立取生效项——这是「嵌入与对话分开」的核心
        """
        from apps.knowledge import llm as llm_module

        self.assertTrue(callable(llm_module.active_chat_provider))
        self.assertTrue(callable(llm_module.active_embedding_provider))
        with mock.patch.object(llm_module.models.LlmProvider.objects, "filter") as fake_filter:
            llm_module.active_embedding_provider()
            kwargs = fake_filter.call_args.kwargs
            self.assertEqual(kwargs.get("model_type"), custom_enum.ModelTypeEnum.EMBEDDING.value)


class VectorStoreMigrationTests(SimpleTestCase):
    """
    向量存储已从库内 pgvector 切到独立的 Qdrant 服务。

    这几条是**防止迁移只做一半**：库内既留残余的向量列/扩展，应用又去连 Qdrant，
    会出现两处都有、两处都不完整的难查状态。
    """

    def test_sql_has_no_inline_vector_column(self):
        for path in (_SQL_STRUCT, _SQL_PATCH):
            with self.subTest(sql=path.name):
                sql = path.read_text(encoding="utf-8")
                self.assertNotRegex(sql, r"vector\s*\(\s*\d+\s*\)", f"{path.name} 仍存在内联向量列定义")
                self.assertNotIn("CREATE EXTENSION IF NOT EXISTS vector", sql, f"{path.name} 仍在启用 vector 扩展")

    def test_no_stored_point_id_mapping(self):
        """
        点标识直接用 kb_chunk.id，库内不存映射——这样索引可由正文纯函数式重建，
        也不存在「向量写成功但标识回写失败」的漂移窗口
        """
        field_names = {f.name for f in models.KbChunk._meta.get_fields()}
        self.assertNotIn("qdrant_point_id", field_names)
        for path in (_SQL_STRUCT, _SQL_PATCH):
            with self.subTest(sql=path.name):
                self.assertNotIn("qdrant_point_id", path.read_text(encoding="utf-8"))

    def test_chunk_model_has_no_embedding_field(self):
        self.assertNotIn("embedding", {f.name for f in models.KbChunk._meta.get_fields()})

    def test_chunk_content_is_the_source_of_truth(self):
        """
        正文必须留在库内：Qdrant 侧是可重建的索引，丢了不该丢正文
        """
        self.assertIn("content", {f.name for f in models.KbChunk._meta.get_fields()})

    def test_no_pgvector_import_left(self):
        source = (Path(__file__).resolve().parent / "models.py").read_text(encoding="utf-8")
        self.assertNotIn("pgvector", source, "models.py 仍在引用 pgvector")


class IngestSplitTests(SimpleTestCase):
    """
    文档切分：按标题层级组织并保留重叠，块脱离上下文会让答案断章取义
    """

    def test_markdown_keeps_heading_path(self):
        md = "# 一\n甲\n\n## 一甲\n乙\n\n# 二\n丙\n"
        got = ingest.split_text(md, True)
        self.assertEqual([p for p, _ in got], ["一", "一/一甲", "二"])

    def test_plain_text_has_no_heading_path(self):
        got = ingest.split_text("甲\n\n乙", False)
        self.assertTrue(all(p == "" for p, _ in got))

    def test_long_text_is_split_and_chunks_respect_size(self):
        size = ingest.chunk_size()
        text = "\n\n".join("第%d段" % i + "内容" * 60 for i in range(40))
        chunks = ingest.split_text(text, False)
        self.assertGreater(len(chunks), 1)
        # 硬切只可能发生在单个超长段落上，正常段落拼接不超上限
        self.assertTrue(all(len(body) <= max(size, 2000) for _, body in chunks))

    def test_empty_text_yields_no_chunks(self):
        """
        空内容返回空列表（两种格式一致），由调用方报「切分后没有可用内容」
        """
        self.assertEqual(ingest.split_text("", False), [])
        self.assertEqual(ingest.split_text("", True), [])


class IngestParseTests(SimpleTestCase):
    """
    解析失败必须给出可读原因——尤其纯扫描件，否则使用者会以为摄入成功了
    """

    def test_unsupported_suffix_rejected(self):
        with self.assertRaises(ingest.IngestError):
            ingest.parse_file("a.docx", b"x")

    def test_empty_file_rejected(self):
        with self.assertRaises(ingest.IngestError):
            ingest.parse_file("a.txt", b"")

    def test_oversize_file_rejected(self):
        with self.assertRaises(ingest.IngestError):
            ingest.parse_file("a.txt", b"x" * (ingest.MAX_UPLOAD_BYTES + 1))

    def test_text_file_parsed_with_title_from_name(self):
        title, text = ingest.parse_file("数据库手册.md", "# 标题\n正文".encode())
        self.assertEqual(title, "数据库手册")
        self.assertIn("正文", text)

    def test_gbk_file_decoded(self):
        title, text = ingest.parse_file("gbk.txt", "中文内容".encode("gbk"))
        self.assertIn("中文内容", text)

    def test_url_must_be_http(self):
        with self.assertRaises(ingest.IngestError):
            ingest.parse_url("file:///etc/passwd")

    def test_content_hash_is_stable_and_distinguishing(self):
        self.assertEqual(ingest.content_hash("甲"), ingest.content_hash("甲"))
        self.assertNotEqual(ingest.content_hash("甲"), ingest.content_hash("乙"))


class _StubChat:
    """替身对话模型：只回一句固定文本"""

    def __init__(self, text="模型回答"):
        self.text = text
        self.calls = 0

    def invoke(self, messages, **kwargs):
        self.calls += 1
        from langchain_core.messages import AIMessage

        return AIMessage(content=self.text)


class _StubEmbeddings:
    def embed_query(self, text):
        return [0.1] * models.EMBEDDING_DIMENSIONS


class QaModeBranchTests(SimpleTestCase):
    """
    三种模式必须在进入 agent **之前**就定下来：「向量服务不可用」「知识库里没查到」
    「本来就不打算用知识库」是三件事，混在一起就会出现静默回退——
    使用者以为答案有文档支撑，实际是模型自己编的。
    """

    def setUp(self):
        self.chat_provider = FakeProvider(api_key_cipher=crypto.encrypt("k"), model_type=custom_enum.ModelTypeEnum.CHAT)
        self.embedding_provider = FakeProvider(
            api_key_cipher=crypto.encrypt("k"), model_type=custom_enum.ModelTypeEnum.EMBEDDING, model_name="emb"
        )
        self.chat = _StubChat()
        self.retrieved = []
        self.agent_calls = []

        for name, value in (
            # 对话与嵌入分开取：两条生效配置
            ("active_chat_provider", lambda: self.chat_provider),
            ("active_embedding_provider", lambda: self.embedding_provider),
            ("build_chat_model", lambda p, **kw: self.chat),
            ("embed_query", lambda p, text: [0.1] * models.EMBEDDING_DIMENSIONS),
        ):
            patcher = mock.patch.object(agent.llm, name, side_effect=value)
            patcher.start()
            self.addCleanup(patcher.stop)

        patcher = mock.patch.object(agent.retrieval, "retrieve", side_effect=lambda *a, **kw: list(self.retrieved))
        patcher.start()
        self.addCleanup(patcher.stop)

        # 注意：闭包里不能直接用 self——嵌套类的方法会把 self 遮蔽掉
        case = self

        def fake_agent(model, tools, prompt=""):
            class _A:
                def invoke(self, payload, config=None):
                    case.agent_calls.append([t.name for t in tools])
                    from langchain_core.messages import AIMessage

                    return {"messages": [AIMessage(content="依据资料的回答")]}

            return _A()

        patcher = mock.patch.object(agent, "create_react_agent", side_effect=fake_agent)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _hit(self, chunk_id=1):
        return {
            "chunk_id": chunk_id,
            "content": "片段内容",
            "heading_path": "第一节",
            "score": 0.9,
            "document_id": 7,
            "document_title": "手册",
            "source": "manual.md",
        }

    def test_no_chat_provider_raises(self):
        with (
            mock.patch.object(agent.llm, "active_chat_provider", return_value=None),
            self.assertRaises(agent.QaError) as ctx,
        ):
            agent.answer("问题", knowledge_base_id=1)
        self.assertIn("对话模型", str(ctx.exception))

    def test_empty_question_raises(self):
        with self.assertRaises(agent.QaError):
            agent.answer("   ", knowledge_base_id=1)

    def test_model_only_never_touches_knowledge_base(self):
        self.retrieved = [self._hit()]
        result = agent.answer("问题", knowledge_base_id=1, mode=custom_enum.QaModeEnum.MODEL_ONLY)
        self.assertTrue(result["is_fallback"])
        self.assertEqual(self.agent_calls, [], "仅通用模型模式不该启动 agent")
        self.assertEqual(self.chat.calls, 1)

    def test_auto_without_knowledge_base_falls_back_and_says_so(self):
        result = agent.answer("问题", knowledge_base_id=None, mode=custom_enum.QaModeEnum.AUTO)
        self.assertTrue(result["is_fallback"])
        self.assertIn("未经文档支撑", result["note"])

    def test_knowledge_only_requires_knowledge_base(self):
        with self.assertRaises(agent.QaError):
            agent.answer("问题", knowledge_base_id=None, mode=custom_enum.QaModeEnum.KNOWLEDGE_ONLY)

    def test_knowledge_only_without_hit_does_not_call_model(self):
        """
        规格要求：仅知识库模式无命中时如实告知，**不得**改用模型作答
        """
        self.retrieved = []
        result = agent.answer("问题", knowledge_base_id=1, mode=custom_enum.QaModeEnum.KNOWLEDGE_ONLY)
        self.assertEqual(result["content"], agent.NO_HIT_MESSAGE)
        self.assertFalse(result["is_fallback"])
        self.assertEqual(self.chat.calls, 0, "无命中时不该调用模型")
        self.assertEqual(self.agent_calls, [])

    def test_auto_without_hit_falls_back_with_note(self):
        self.retrieved = []
        result = agent.answer("问题", knowledge_base_id=1, mode=custom_enum.QaModeEnum.AUTO)
        self.assertTrue(result["is_fallback"])
        self.assertIn("未经文档支撑", result["note"])
        self.assertEqual(self.chat.calls, 1)

    def test_auto_with_hit_runs_agent(self):
        self.retrieved = [self._hit()]
        result = agent.answer("问题", knowledge_base_id=1, mode=custom_enum.QaModeEnum.AUTO)
        self.assertFalse(result["is_fallback"])
        self.assertEqual(len(self.agent_calls), 1)
        self.assertIn("search_knowledge_base", self.agent_calls[0])

    def test_sources_come_from_retrieval_not_from_the_model(self):
        """
        规格要求「不得凭空编造来源」：模型嘴上说依据资料，但本次若没有工具真的检索过，
        来源就必须是空的——来源只能由实际检索结果产生
        """
        self.retrieved = [self._hit()]
        result = agent.answer("问题", knowledge_base_id=1, mode=custom_enum.QaModeEnum.AUTO)
        self.assertIn("资料", result["content"])
        self.assertEqual(result["sources"], [], "未经工具检索就不该有任何来源")

    def test_vector_store_failure_is_not_treated_as_no_hit(self):
        """
        向量服务不可用必须抛出去，不能被当成「没查到」而回退到通用模型
        """
        with (
            mock.patch.object(agent.retrieval, "retrieve", side_effect=RuntimeError("向量服务不可用")),
            self.assertRaises(RuntimeError),
        ):
            agent.answer("问题", knowledge_base_id=1, mode=custom_enum.QaModeEnum.AUTO)

    def test_sources_only_contain_actually_retrieved_chunks(self):
        collected = [self._hit(1), self._hit(2), self._hit(1)]
        sources = agent._sources(collected)
        self.assertEqual([s["chunk_id"] for s in sources], [1, 2], "来源应去重且只含实际检索到的块")


class AnswerRobustnessTests(SimpleTestCase):
    """
    实测发现的模型侧问题：deepseek 在「提示词提到工具、但没有工具可调」时，
    偶尔把工具调用语法当正文吐出来。使用者不该在界面上看到这类内容。
    """

    def test_message_text_handles_plain_string(self):
        from langchain_core.messages import AIMessage

        self.assertEqual(agent.message_text(AIMessage(content="  普通回答  ")), "普通回答")

    def test_message_text_handles_content_blocks(self):
        """
        langchain 1.x 的 content 可能是内容块列表，直接当字符串用会崩
        """
        from langchain_core.messages import AIMessage

        message = AIMessage(content=[{"type": "text", "text": "第一段"}, {"type": "text", "text": "第二段"}])
        self.assertEqual(agent.message_text(message), "第一段\n第二段")

    def test_message_text_ignores_non_text_blocks(self):
        from langchain_core.messages import AIMessage

        message = AIMessage(content=[{"type": "image_url", "image_url": {"url": "x"}}, "纯文本"])
        self.assertEqual(agent.message_text(message), "纯文本")

    def test_plain_answer_untouched(self):
        text = "PostgreSQL 默认隔离级别是读已提交。"
        self.assertEqual(agent._strip_tool_markup(text), text)

    def test_tool_markup_lines_are_stripped(self):
        text = (
            "先说明一下：\n"
            "<function_calls>\n"
            '<invoke name="search_docs">\n'
            '<parameter name="query">MVCC</parameter>\n'
            "</invoke>\n"
            "</function_calls>\n"
            "结论是使用多版本并发控制。"
        )
        cleaned = agent._strip_tool_markup(text)
        self.assertIn("先说明一下", cleaned)
        self.assertIn("结论是", cleaned)
        self.assertNotIn("invoke", cleaned)
        self.assertNotIn("function_calls", cleaned)

    def test_pure_markup_gets_a_readable_fallback(self):
        text = '<function_calls>\n<invoke name="x">\n</invoke>\n</function_calls>'
        cleaned = agent._strip_tool_markup(text)
        self.assertNotIn("invoke", cleaned)
        self.assertTrue(cleaned.strip(), "不该返回空内容")


class MissingEmbeddingTests(SimpleTestCase):
    """
    嵌入模型缺失是**配置问题**，不是服务故障：必须给出可操作的提示，
    而不是报成「服务器错误」让使用者不知道该改哪里
    """

    def test_missing_embedding_provider_raises_qa_error(self):
        """
        没配嵌入模型时，只用对话模型能跑，但一旦要检索就必须给出可操作的提示
        """
        chat = FakeProvider(api_key_cipher=crypto.encrypt("k"), model_type=custom_enum.ModelTypeEnum.CHAT)
        with (
            mock.patch.object(agent.llm, "active_chat_provider", return_value=chat),
            mock.patch.object(agent.llm, "active_embedding_provider", return_value=None),
            self.assertRaises(agent.QaError) as ctx,
        ):
            agent.answer("问题", knowledge_base_id=1, mode=custom_enum.QaModeEnum.AUTO)
        self.assertIn("嵌入模型", str(ctx.exception))

    def test_model_only_does_not_need_embedding(self):
        """
        仅通用模型模式不检索，因此缺嵌入模型不该拦住它——这正是「分开配置」的意义
        """
        chat = FakeProvider(api_key_cipher=crypto.encrypt("k"), model_type=custom_enum.ModelTypeEnum.CHAT)
        with (
            mock.patch.object(agent.llm, "active_chat_provider", return_value=chat),
            mock.patch.object(agent.llm, "active_embedding_provider", return_value=None),
            mock.patch.object(agent.llm, "build_chat_model", lambda p, **kw: _StubChat("模型回答")),
        ):
            result = agent.answer("问题", mode=custom_enum.QaModeEnum.MODEL_ONLY)
        self.assertEqual(result["content"], "模型回答")


class QaHistoryTests(SimpleTestCase):
    """
    多轮上下文：保留最近若干轮，超出直接截断（不引入额外的摘要模型调用）
    """

    def test_history_is_trimmed_to_configured_rounds(self):
        history = [
            {"role": custom_enum.MessageRoleEnum.USER.value, "content": f"问题{i}"}
            for i in range(agent.history_rounds() * 3)
        ]
        messages = agent._to_messages("当前问题", history)
        # 1 条 system + 截断后的历史 + 1 条当前问题
        self.assertEqual(len(messages), agent.history_rounds() * 2 + 2)

    def test_blank_history_entries_are_skipped(self):
        history = [{"role": custom_enum.MessageRoleEnum.USER.value, "content": "   "}]
        self.assertEqual(len(agent._to_messages("问题", history)), 2)


class DuplicateActionTests(SimpleTestCase):
    """
    重复来源的处理方式：要么跳过、要么明确覆盖，**不允许静默产生重复内容**
    """

    def test_parses_valid_actions(self):
        self.assertEqual(
            views._parse_duplicate_action(str(custom_enum.DuplicateActionEnum.SKIP.value)),
            custom_enum.DuplicateActionEnum.SKIP,
        )
        self.assertEqual(
            views._parse_duplicate_action(str(custom_enum.DuplicateActionEnum.OVERWRITE.value)),
            custom_enum.DuplicateActionEnum.OVERWRITE,
        )

    def test_absent_or_invalid_returns_none(self):
        for raw in (None, "", "abc", 99):
            with self.subTest(raw=raw):
                self.assertIsNone(views._parse_duplicate_action(raw), "未表态时必须返回 None，由调用方去问使用者")

    def test_enum_has_exactly_two_actions(self):
        self.assertEqual([m.name for m in custom_enum.DuplicateActionEnum], ["SKIP", "OVERWRITE"])


class QaModeTests(SimpleTestCase):
    """
    问答模式与规格约定一致：自动 / 仅知识库 / 仅通用模型
    """

    def test_exactly_three_modes(self):
        self.assertEqual(
            [m.name for m in custom_enum.QaModeEnum],
            ["AUTO", "KNOWLEDGE_ONLY", "MODEL_ONLY"],
        )

    def test_auto_is_the_first_and_default(self):
        self.assertEqual(custom_enum.QaModeEnum.AUTO.value, 1)

"""
知识问答模块单元测试

绝大多数为 SimpleTestCase（**不需要数据库**）：覆盖不依赖存储的部分——凭据保护、失败信息脱敏、
向量存储迁移完整性、摄入的切分与解析、三种问答模式的分支、以及流式的事件与分片过滤。

`StreamPersistenceTests` 是唯一的 TestCase：流式中断要把已收到的内容落库并标记不完整，
这件事离开数据库测不了。它依赖 `TEST_RUNNER`（`utils/test_runner.py`）把 `sql/pg_struct.sql`
灌进测试库——本项目禁止 Django migration，Django 默认建不出业务表。

涉及真实 Qdrant 与模型 API 的链路（摄入落库、检索回库取正文）不放进这里，
那部分以脚本对着真实服务验证。
"""

from pathlib import Path
from unittest import mock

from django.test import SimpleTestCase, TestCase, TransactionTestCase
from rest_framework.test import APIClient

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


# ----------------------------------------------------------------------
# 流式输出（openspec: add-qa-streaming）
# ----------------------------------------------------------------------


class SseFrameTests(SimpleTestCase):
    """
    SSE 帧：`event:` 行 + JSON 载荷，中文不转义（前端直接展示）
    """

    def test_frame_has_event_line_then_data_line(self):
        frame = views._sse("delta", {"text": "你好"})
        self.assertTrue(frame.startswith("event: delta\n"))
        self.assertIn("data: ", frame)
        self.assertTrue(frame.endswith("\n\n"))

    def test_chinese_is_not_ascii_escaped(self):
        frame = views._sse("status", {"text": "正在检索知识库…"})
        self.assertIn("正在检索知识库…", frame)
        self.assertNotIn("\\u", frame)

    def test_payload_is_json_parseable(self):
        import json

        frame = views._sse("done", {"sources": [], "is_fallback": False})
        payload = json.loads(frame.split("data: ", 1)[1].strip())
        self.assertEqual(payload["sources"], [])
        self.assertFalse(payload["is_fallback"])


class LineBufferTests(SimpleTestCase):
    """
    流式路径必须给出与同步路径**相同**的保证：工具调用语法不得出现在正文里。

    `_strip_tool_markup()` 是整段文本的后处理，无法作用于实时 token 流，因此流式改为
    只放行到最后一个换行为止的完整行。实测缺陷（deepseek 会把调用语法当正文吐出）
    不能因为改成流式就复活。
    """

    def test_incomplete_line_is_held_back(self):
        buffer = agent._LineBuffer()
        self.assertEqual(buffer.feed("第一行还没写完"), "")
        self.assertEqual(buffer.feed("，现在写完了\n"), "第一行还没写完，现在写完了\n")

    def test_tail_without_newline_is_released_on_flush(self):
        buffer = agent._LineBuffer()
        self.assertEqual(buffer.feed("结尾没有换行"), "")
        self.assertEqual(buffer.flush(), "结尾没有换行")

    def test_markup_line_is_dropped(self):
        buffer = agent._LineBuffer()
        out = buffer.feed('正常内容\n<invoke name="search">\n')
        self.assertIn("正常内容", out)
        self.assertNotIn("invoke", out)

    def test_all_markup_leaves_no_content(self):
        buffer = agent._LineBuffer()
        buffer.feed("<function_calls>\n</function_calls>\n")
        self.assertTrue(buffer.raw_len)
        self.assertFalse(buffer.has_content)
        self.assertEqual(buffer.flush(), "")

    def test_plain_text_passes_through_unchanged(self):
        buffer = agent._LineBuffer()
        text = "第一行\n第二行\n"
        self.assertEqual(buffer.feed(text), text)

    def test_agrees_with_sync_path_on_the_same_text(self):
        text = '开头\n<invoke name="a">\nx\n中间正常\n'
        buffer = agent._LineBuffer()
        streamed = buffer.feed(text) + buffer.flush()
        self.assertEqual(streamed.strip(), agent._strip_tool_markup(text))

    def test_empty_feed_is_noop(self):
        buffer = agent._LineBuffer()
        self.assertEqual(buffer.feed(""), "")
        self.assertEqual(buffer.raw_len, 0)


class AnswerTokenFilterTests(SimpleTestCase):
    """
    中间轮次的分片绝不能当答案推出。

    react agent 的每一轮产出的都是 `AIMessageChunk`，模型为调用工具而生成的参数
    若不滤掉，会直接出现在使用者的回答里——这是流式路径最容易出错的一处。
    """

    @staticmethod
    def _chunk(content="", tool_call_chunks=None, tool_calls=None):
        from langchain_core.messages import AIMessageChunk

        return AIMessageChunk(content=content, tool_call_chunks=tool_call_chunks or [], tool_calls=tool_calls or [])

    def test_plain_agent_chunk_is_answer(self):
        self.assertTrue(agent._is_answer_token(self._chunk("依据资料的回答"), {"langgraph_node": "agent"}))

    def test_chunk_carrying_a_tool_call_is_not_answer(self):
        chunk = self._chunk("", [{"name": "search_knowledge_base", "args": "", "id": "1", "index": 0}])
        self.assertFalse(agent._is_answer_token(chunk, {"langgraph_node": "agent"}))

    def test_chunk_with_parsed_tool_calls_is_not_answer(self):
        chunk = self._chunk("", tool_calls=[{"name": "search_knowledge_base", "args": {}, "id": "1"}])
        self.assertFalse(agent._is_answer_token(chunk, {"langgraph_node": "agent"}))

    def test_chunk_from_another_node_is_not_answer(self):
        self.assertFalse(agent._is_answer_token(self._chunk("工具返回"), {"langgraph_node": "tools"}))

    def test_missing_node_metadata_is_not_answer(self):
        self.assertFalse(agent._is_answer_token(self._chunk("x"), {}))

    def test_tool_name_is_read_from_call_chunks(self):
        chunk = self._chunk("", [{"name": "get_table_schema", "args": "", "id": "1", "index": 0}])
        self.assertEqual(agent._tool_names_in(chunk, {"langgraph_node": "agent"}), ["get_table_schema"])

    def test_tool_name_ignored_outside_agent_node(self):
        chunk = self._chunk("", [{"name": "get_table_schema", "args": "", "id": "1", "index": 0}])
        self.assertEqual(agent._tool_names_in(chunk, {"langgraph_node": "tools"}), [])


class ModeNormalizeTests(SimpleTestCase):
    """
    模式归一：缺省为自动，非法值必须在**任何外部调用之前**被拒
    """

    def test_none_falls_back_to_auto(self):
        self.assertEqual(agent._normalize_mode(None), custom_enum.QaModeEnum.AUTO.value)

    def test_enum_member_is_accepted(self):
        self.assertEqual(
            agent._normalize_mode(custom_enum.QaModeEnum.KNOWLEDGE_ONLY),
            custom_enum.QaModeEnum.KNOWLEDGE_ONLY.value,
        )

    def test_unknown_value_is_rejected(self):
        with self.assertRaises(agent.QaError):
            agent._normalize_mode(99)

    def test_non_numeric_is_rejected(self):
        with self.assertRaises(agent.QaError):
            agent._normalize_mode("自动")


class _StubStreamingChat:
    """替身对话模型：把预设分片逐段吐出"""

    def __init__(self, pieces=None):
        self.pieces = pieces if pieces is not None else ["模型回答\n"]
        self.calls = 0

    def stream(self, messages, **kwargs):
        from langchain_core.messages import AIMessageChunk

        self.calls += 1
        for piece in self.pieces:
            yield AIMessageChunk(content=piece)


class _StubStreamingAgent:
    """替身 agent：按预设的 (分片, metadata) 序列产出"""

    def __init__(self, events):
        self.events = events

    def stream(self, payload, config=None, stream_mode=None):
        yield from self.events


class QaStreamTests(SimpleTestCase):
    """
    流式问答的事件序列、进度提示时机，以及「服务故障 ≠ 无命中」在流式路径上同样成立
    """

    def setUp(self):
        self.chat_provider = FakeProvider(api_key_cipher=crypto.encrypt("k"), model_type=custom_enum.ModelTypeEnum.CHAT)
        self.embedding_provider = FakeProvider(
            api_key_cipher=crypto.encrypt("k"), model_type=custom_enum.ModelTypeEnum.EMBEDDING, model_name="emb"
        )
        self.chat = _StubStreamingChat()
        self.retrieved = []
        self.agent_events = []

        for name, value in (
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

        patcher = mock.patch.object(
            agent, "create_react_agent", side_effect=lambda *a, **kw: _StubStreamingAgent(self.agent_events)
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    @staticmethod
    def _chunk(content="", tool_call_chunks=None):
        from langchain_core.messages import AIMessageChunk

        return AIMessageChunk(content=content, tool_call_chunks=tool_call_chunks or [])

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

    def _run(self, question="问题", **kwargs):
        prep = agent.prepare_stream(question, **kwargs)
        return list(agent.stream_events(prep, question))

    @staticmethod
    def _deltas(events):
        return "".join(e["text"] for e in events if e["type"] == "delta")

    def test_agent_path_streams_text_then_done(self):
        self.retrieved = [self._hit()]
        self.agent_events = [
            (self._chunk("依据资料"), {"langgraph_node": "agent"}),
            (self._chunk("的回答\n"), {"langgraph_node": "agent"}),
        ]
        events = self._run(knowledge_base_id=1)
        self.assertEqual(events[-1]["type"], "done")
        self.assertEqual(self._deltas(events).strip(), "依据资料的回答")
        self.assertFalse(events[-1]["is_fallback"])

    def test_tool_progress_arrives_before_the_answer(self):
        self.retrieved = [self._hit()]
        self.agent_events = [
            (
                self._chunk("", [{"name": "search_knowledge_base", "args": "", "id": "1", "index": 0}]),
                {"langgraph_node": "agent"},
            ),
            (self._chunk("答案\n"), {"langgraph_node": "agent"}),
        ]
        events = self._run(knowledge_base_id=1)
        statuses = [e["text"] for e in events if e["type"] == "status"]
        self.assertEqual(statuses, ["正在检索知识库…"])
        first_delta = next(i for i, e in enumerate(events) if e["type"] == "delta")
        self.assertLess(events.index({"type": "status", "text": statuses[0]}), first_delta)

    def test_intermediate_turn_text_is_not_shown(self):
        """带工具调用的分片，其正文属于中间轮次，不得当答案推出"""
        self.retrieved = [self._hit()]
        self.agent_events = [
            (
                self._chunk("我这就去查一下", [{"name": "search_knowledge_base", "args": "", "id": "1", "index": 0}]),
                {"langgraph_node": "agent"},
            ),
            (self._chunk("真正的答案\n"), {"langgraph_node": "agent"}),
        ]
        text = self._deltas(self._run(knowledge_base_id=1))
        self.assertNotIn("我这就去查一下", text)
        self.assertIn("真正的答案", text)

    def test_progress_text_does_not_leak_arguments(self):
        self.retrieved = [self._hit()]
        self.agent_events = [
            (
                self._chunk(
                    "", [{"name": "get_table_schema", "args": '{"table_name":"orders"}', "id": "1", "index": 0}]
                ),
                {"langgraph_node": "agent"},
            ),
            (self._chunk("答案\n"), {"langgraph_node": "agent"}),
        ]
        events = self._run(knowledge_base_id=1, datasource_id=2)
        statuses = [e["text"] for e in events if e["type"] == "status"]
        self.assertEqual(statuses, ["正在查询表结构…"])
        self.assertNotIn("orders", "".join(statuses))

    def test_knowledge_only_without_hit_does_not_call_model(self):
        self.retrieved = []
        events = self._run(knowledge_base_id=1, mode=custom_enum.QaModeEnum.KNOWLEDGE_ONLY)
        self.assertEqual(self.chat.calls, 0)
        self.assertIn("未在知识库中找到", self._deltas(events))
        self.assertFalse(events[-1]["is_fallback"])

    def test_auto_without_hit_streams_a_fallback_and_says_so(self):
        self.retrieved = []
        self.chat.pieces = ["模型自己答的\n"]
        events = self._run(knowledge_base_id=1)
        self.assertEqual(self.chat.calls, 1)
        self.assertTrue(events[-1]["is_fallback"])
        self.assertIn("未经文档支撑", events[-1]["note"])
        self.assertEqual(self._deltas(events).strip(), "模型自己答的")

    def test_model_only_skips_retrieval(self):
        events = self._run(mode=custom_enum.QaModeEnum.MODEL_ONLY)
        self.assertTrue(events[-1]["is_fallback"])
        # 不检索、直接问模型，与同步路径同一条分支
        self.assertEqual(self.chat.calls, 1)
        self.assertEqual(events[-1]["sources"], [])

    def test_sources_stay_empty_when_no_tool_actually_ran(self):
        """规格要求「不得凭空编造来源」：工具没真的检索过，来源就必须是空的"""
        self.retrieved = [self._hit()]
        self.agent_events = [(self._chunk("依据资料的回答\n"), {"langgraph_node": "agent"})]
        events = self._run(knowledge_base_id=1)
        self.assertEqual(events[-1]["sources"], [])

    def test_model_markup_never_reaches_the_client(self):
        """流式路径同样要挡住「模型把工具调用语法当正文吐出」"""
        self.retrieved = [self._hit()]
        self.agent_events = [
            (self._chunk("正常内容\n"), {"langgraph_node": "agent"}),
            (self._chunk('<invoke name="search">\n'), {"langgraph_node": "agent"}),
            (self._chunk("继续正常\n"), {"langgraph_node": "agent"}),
        ]
        text = self._deltas(self._run(knowledge_base_id=1))
        self.assertNotIn("invoke", text)
        self.assertIn("正常内容", text)
        self.assertIn("继续正常", text)

    def test_vector_store_failure_propagates_from_presearch(self):
        """预检索阶段的故障必须抛出去，不能在流式路径上退化成「无命中」"""
        from apps.knowledge import vectorstore

        with (
            mock.patch.object(agent.retrieval, "retrieve", side_effect=vectorstore.VectorStoreError("向量服务不可用")),
            self.assertRaises(vectorstore.VectorStoreError),
        ):
            agent.prepare_stream("问题", knowledge_base_id=1)

    def test_prepare_stream_does_not_start_a_generator(self):
        """
        预检必须**立即**执行：一旦它变成生成器，函数体就要等到首次迭代才跑，
        那时响应头已经发出，未配置模型这类错误就再也回不到统一格式的 JSON 了
        """
        with (
            mock.patch.object(agent.llm, "active_chat_provider", return_value=None),
            self.assertRaises(agent.QaError),
        ):
            agent.prepare_stream("问题", knowledge_base_id=1)


class StreamPersistenceTests(TestCase):
    """
    流式中断保留：已收到的内容必须落库，并标记为不完整

    这是本模块唯一的 TestCase——离开数据库测不了。它依赖 `utils/test_runner.py`
    把 `sql/pg_struct.sql` 灌进测试库（本项目禁止 Django migration）。
    """

    def setUp(self):
        self.session = models.QaSession.objects.create(title="会话", mode=custom_enum.QaModeEnum.AUTO.value)
        self.message = models.QaMessage.objects.create(
            session_id=self.session.id,
            role=custom_enum.MessageRoleEnum.ASSISTANT.value,
            mode=custom_enum.QaModeEnum.AUTO.value,
            content="",
        )

    def test_interrupted_content_is_kept_and_marked_incomplete(self):
        views._persist_stream_message(self.message, "半截回答", {}, completed=False)
        self.message.refresh_from_db()
        self.assertEqual(self.message.content, "半截回答")
        self.assertFalse(self.message.is_complete)

    def test_completed_answer_is_marked_complete(self):
        done = {
            "sources": [{"chunk_id": 1}],
            "tools_used": ["search_knowledge_base"],
            "is_fallback": False,
        }
        views._persist_stream_message(self.message, "完整回答", done, completed=True)
        self.message.refresh_from_db()
        self.assertTrue(self.message.is_complete)
        self.assertEqual(self.message.tools_used, ["search_knowledge_base"])
        self.assertEqual(self.message.sources, [{"chunk_id": 1}])

    def test_fallback_flag_is_persisted(self):
        views._persist_stream_message(self.message, "回退回答", {"is_fallback": True}, completed=True)
        self.message.refresh_from_db()
        self.assertTrue(self.message.is_fallback)

    def test_is_complete_defaults_to_true_for_existing_rows(self):
        """新增列对既有数据必须是「完整」，否则历史回答会被误标为中断"""
        existing = models.QaMessage.objects.create(
            session_id=self.session.id,
            role=custom_enum.MessageRoleEnum.ASSISTANT.value,
            mode=custom_enum.QaModeEnum.AUTO.value,
            content="历史回答",
        )
        existing.refresh_from_db()
        self.assertTrue(existing.is_complete)

    def test_schema_sql_declares_the_new_column(self):
        """列必须同时出现在全量结构与增量补丁里——两者都不更新，新库与旧库就会不一致"""
        struct_sql = _SQL_STRUCT.read_text(encoding="utf-8")
        patch_sql = _SQL_PATCH.read_text(encoding="utf-8")
        self.assertIn("is_complete", struct_sql, "pg_struct.sql 缺 is_complete，新库建不出来")
        self.assertIn("is_complete", patch_sql, "patch.sql 缺 is_complete，已有库升不上来")


class StreamEndpointTests(TransactionTestCase):
    """
    流式接口的端到端：路由、响应类型、SSE 帧、落库、以及断连保留

    用 `TransactionTestCase` 而非 `TestCase` 是必须的：`TestCase` 会把每个用例包在
    atomic 里（autocommit=False），而测试客户端每个请求结束发的 `request_finished`
    信号会触发 `close_old_connections`——它见到 autocommit 与配置不符就关掉连接，
    于是流结束后那段落库代码拿到的是一条已关闭的连接。生产环境请求周期内 autocommit
    一致，不存在这个问题。

    注意本项目的统一响应封装把 **HTTP 状态码固定为 200**，业务结果只由 body 里的 `code`
    表达（`baseviews.FormatResponse`）。因此「预检失败」与「开流成功」都是 200，
    靠 `Content-Type` 区分——这正是前端判定的依据（见 design.md D6）。
    """

    URL = "/my-dba/v1/qa-session/ask-stream"

    def setUp(self):
        self.client = APIClient()
        self.base = models.KnowledgeBase.objects.create(name="知识库")
        self.chat_provider = FakeProvider(api_key_cipher=crypto.encrypt("k"))
        self.embedding_provider = FakeProvider(
            api_key_cipher=crypto.encrypt("k"), model_type=custom_enum.ModelTypeEnum.EMBEDDING, model_name="emb"
        )
        self.chat = _StubStreamingChat(pieces=["答案第一段\n", "答案第二段\n"])
        self.retrieved = []
        self.agent_events = []

        for name, value in (
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

        patcher = mock.patch.object(
            agent, "create_react_agent", side_effect=lambda *a, **kw: _StubStreamingAgent(self.agent_events)
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def _post(self, payload):
        return self.client.post(self.URL, payload, format="json")

    @staticmethod
    def _body(response):
        return b"".join(response.streaming_content).decode("utf-8")

    def _messages(self, session_id):
        return list(models.QaMessage.objects.filter(session_id=session_id).order_by("id"))

    def test_preflight_failure_does_not_start_a_stream(self):
        """未配置对话模型必须还能回统一格式的 JSON——一旦开流就再也改不回去了"""
        with mock.patch.object(agent.llm, "active_chat_provider", return_value=None):
            response = self._post({"question": "问题"})
        self.assertFalse(response.streaming)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["code"], 4017)
        self.assertIn("对话模型", response.json()["message"])
        self.assertFalse(models.QaMessage.objects.exists(), "预检失败不该留下任何消息")

    def test_empty_question_is_rejected_before_streaming(self):
        response = self._post({"question": "   "})
        self.assertFalse(response.streaming)
        self.assertEqual(response.json()["code"], 4000)

    def test_model_only_streams_sse_frames_and_persists_a_complete_answer(self):
        response = self._post({"question": "问题", "mode": custom_enum.QaModeEnum.MODEL_ONLY.value})
        self.assertEqual(response["Content-Type"], "text/event-stream; charset=utf-8")
        self.assertEqual(response["Cache-Control"], "no-cache")
        body = self._body(response)
        self.assertIn("event: delta", body)
        self.assertIn("event: done", body)
        self.assertIn("答案第一段", body)

        session = models.QaSession.objects.get()
        messages = self._messages(session.id)
        self.assertEqual(
            [m.role for m in messages],
            [custom_enum.MessageRoleEnum.USER.value, custom_enum.MessageRoleEnum.ASSISTANT.value],
        )
        self.assertEqual(messages[0].content, "问题")
        self.assertIn("答案第一段", messages[1].content)
        self.assertTrue(messages[1].is_complete)

    def test_agent_path_emits_tool_progress(self):
        self.retrieved = [
            {
                "chunk_id": 1,
                "content": "片段",
                "heading_path": "",
                "score": 0.9,
                "document_id": 7,
                "document_title": "手册",
                "source": "manual.md",
            }
        ]
        self.agent_events = [
            (
                QaStreamTests._chunk("", [{"name": "search_knowledge_base", "args": "", "id": "1", "index": 0}]),
                {"langgraph_node": "agent"},
            ),
            (QaStreamTests._chunk("依据资料的回答\n"), {"langgraph_node": "agent"}),
        ]
        body = self._body(self._post({"question": "问题", "knowledge_base_id": self.base.id}))
        self.assertIn("event: status", body)
        self.assertIn("正在检索知识库…", body)
        self.assertIn("依据资料的回答", body)

    def test_client_disconnect_keeps_partial_content(self):
        """客户端断开时已推出去的内容必须留下，并标记为不完整"""
        self.chat.pieces = ["第一段\n", "第二段\n", "第三段\n"]
        response = self._post({"question": "问题", "mode": custom_enum.QaModeEnum.MODEL_ONLY.value})
        iterator = iter(response.streaming_content)
        next(iterator)  # 只消费一帧就断开

        response.close()  # Django 会把生成器的 close 注册为资源清理器，这里触发 GeneratorExit

        message = models.QaMessage.objects.get(role=custom_enum.MessageRoleEnum.ASSISTANT.value)
        self.assertIn("第一段", message.content)
        self.assertNotIn("第三段", message.content, "断开之后的内容不该出现")
        self.assertFalse(message.is_complete, "被中断的回答必须标记为不完整")

    def test_user_question_is_persisted_before_generation(self):
        """提问先于生成落库：否则中断时连问题都丢了"""
        self.chat.pieces = ["第一段\n", "第二段\n"]
        response = self._post({"question": "这个问题不能丢", "mode": custom_enum.QaModeEnum.MODEL_ONLY.value})
        iterator = iter(response.streaming_content)
        next(iterator)
        response.close()

        user_message = models.QaMessage.objects.get(role=custom_enum.MessageRoleEnum.USER.value)
        self.assertEqual(user_message.content, "这个问题不能丢")

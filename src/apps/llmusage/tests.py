"""
模型用量单元测试

覆盖记录、回调、归属中间件、查询接口与保留期清理。
"""

from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest import mock

from django.test import RequestFactory, TestCase
from rest_framework.test import APIClient

from apps.llmusage import callbacks, context, middleware, models, services
from utils import custom_enum
from utils.authentication import AuthedUser, JwtOperate

USAGE = "/my-dba/v1/llm-usage"
CALLS = f"{USAGE}/calls"

ADMIN = AuthedUser(1, "root", "admin")
NORMAL = AuthedUser(2, "alice", "user")


def _reset_context():
    context.set_context("", custom_enum.LlmFeatureEnum.OTHER.value, "", "")


def _row(**kwargs):
    data = {
        "username": "alice",
        "feature": custom_enum.LlmFeatureEnum.QA,
        "call_type": custom_enum.LlmCallTypeEnum.CHAT,
        "model_name": "test-model",
        "latency_ms": 100,
        "success": True,
    }
    data.update(kwargs)
    return models.LlmUsage.objects.create(**data)


def _client(user=None):
    client = APIClient()
    if user is not None:
        client.force_authenticate(user=user)
    return client


class RecordCallTests(TestCase):
    def setUp(self):
        context.set_context("alice", custom_enum.LlmFeatureEnum.QA.value, "req-1", "/my-dba/v1/qa-session")

    def tearDown(self):
        _reset_context()

    def test_records_context_and_tokens(self):
        provider = SimpleNamespace(id=7, model_name="m1")
        services.record_call(
            call_type=custom_enum.LlmCallTypeEnum.CHAT,
            provider=provider,
            prompt_tokens=10,
            completion_tokens=5,
            total_tokens=15,
            latency_ms=42,
        )
        row = models.LlmUsage.objects.get()
        self.assertEqual(row.username, "alice")
        self.assertEqual(row.feature, custom_enum.LlmFeatureEnum.QA.value)
        self.assertEqual(row.provider_id, 7)
        self.assertEqual(row.model_name, "m1")
        self.assertEqual(row.total_tokens, 15)
        self.assertEqual(row.request_id, "req-1")
        self.assertTrue(row.success)

    def test_failure_records_error_code(self):
        services.record_call(
            call_type=custom_enum.LlmCallTypeEnum.CHAT,
            success=False,
            error_code="timeout",
        )
        row = models.LlmUsage.objects.get()
        self.assertFalse(row.success)
        self.assertEqual(row.error_code, "timeout")

    def test_record_failure_does_not_raise(self):
        with mock.patch("apps.llmusage.models.LlmUsage.objects.create", side_effect=RuntimeError("db down")):
            services.record_call(call_type=custom_enum.LlmCallTypeEnum.CHAT)
        self.assertEqual(models.LlmUsage.objects.count(), 0)


class CallbackTests(TestCase):
    def setUp(self):
        context.set_context("alice", custom_enum.LlmFeatureEnum.QA.value, "req-2", "/my-dba/v1/qa-session")
        self.provider = SimpleNamespace(id=1, model_name="chat-model")

    def tearDown(self):
        _reset_context()

    def test_records_usage_from_llm_output(self):
        handler = callbacks.LlmUsageCallbackHandler(self.provider)
        handler.on_chat_model_start({}, [], run_id="run-1")
        response = SimpleNamespace(
            llm_output={"token_usage": {"prompt_tokens": 3, "completion_tokens": 4, "total_tokens": 7}},
            generations=[],
        )
        handler.on_llm_end(response, run_id="run-1")

        row = models.LlmUsage.objects.get()
        self.assertEqual((row.prompt_tokens, row.completion_tokens, row.total_tokens), (3, 4, 7))
        self.assertTrue(row.success)

    def test_usage_missing_leaves_tokens_null(self):
        handler = callbacks.LlmUsageCallbackHandler(self.provider)
        handler.on_llm_start({}, ["hi"], run_id="run-2")
        response = SimpleNamespace(
            llm_output={},
            generations=[[SimpleNamespace(message=SimpleNamespace(usage_metadata={}))]],
        )
        handler.on_llm_end(response, run_id="run-2")

        row = models.LlmUsage.objects.get()
        self.assertIsNone(row.prompt_tokens)
        self.assertIsNone(row.completion_tokens)
        self.assertIsNone(row.total_tokens)

    def test_streaming_usage_metadata_is_read(self):
        handler = callbacks.LlmUsageCallbackHandler(self.provider)
        handler.on_llm_start({}, ["hi"], run_id="run-3")
        response = SimpleNamespace(
            llm_output=None,
            generations=[
                [
                    SimpleNamespace(
                        message=SimpleNamespace(
                            usage_metadata={"input_tokens": 2, "output_tokens": 6, "total_tokens": 8}
                        )
                    )
                ]
            ],
        )
        handler.on_llm_end(response, run_id="run-3")

        row = models.LlmUsage.objects.get()
        self.assertEqual(row.total_tokens, 8)

    def test_error_is_classified(self):
        handler = callbacks.LlmUsageCallbackHandler(self.provider)
        handler.on_llm_start({}, ["hi"], run_id="run-4")
        handler.on_llm_error(TimeoutError("timed out"), run_id="run-4")

        row = models.LlmUsage.objects.get()
        self.assertFalse(row.success)
        self.assertEqual(row.error_code, "timeout")


class MiddlewareTests(TestCase):
    def tearDown(self):
        _reset_context()

    def _run(self, path, token=None):
        request = RequestFactory().get(path)
        if token:
            request.META["HTTP_TOKEN"] = token
        middleware.LlmUsageContextMiddleware(lambda req: None)(request)
        return context.get_context()

    def test_feature_from_path(self):
        ctx = self._run("/my-dba/v1/qa-session")
        self.assertEqual(ctx["feature"], custom_enum.LlmFeatureEnum.QA.value)
        self.assertTrue(ctx["request_id"])

    def test_username_from_token(self):
        token = JwtOperate().encode({"id": 1, "name": "alice", "role": "user"})
        ctx = self._run("/my-dba/v1/nl2sql", token=token)
        self.assertEqual(ctx["username"], "alice")
        self.assertEqual(ctx["feature"], custom_enum.LlmFeatureEnum.NL2SQL.value)

    def test_unknown_path_is_other(self):
        ctx = self._run("/my-dba/v1/datasource")
        self.assertEqual(ctx["feature"], custom_enum.LlmFeatureEnum.OTHER.value)

    def test_invalid_token_keeps_anonymous(self):
        ctx = self._run("/my-dba/v1/qa-session", token="not-a-token")
        self.assertEqual(ctx["username"], "")


class UsageApiTests(TestCase):
    def test_summary_aggregates(self):
        _row()
        _row(success=False, latency_ms=300, error_code="timeout")
        payload = _client(ADMIN).get(USAGE, {"range": "today"}).json()

        self.assertEqual(payload["code"], 2000)
        summary = payload["data"]["summary"]
        self.assertTrue(summary["available"])
        self.assertEqual(summary["calls"], 2)
        self.assertEqual(summary["success_calls"], 1)
        self.assertEqual(summary["success_rate"], 0.5)

    def test_summary_group_by_feature(self):
        _row()
        _row()
        _row(feature=custom_enum.LlmFeatureEnum.NL2SQL)
        payload = _client(ADMIN).get(USAGE, {"range": "today", "group_by": "feature"}).json()

        groups = payload["data"]["groups"]["items"]
        self.assertEqual(groups[0]["label"], "知识问答")
        self.assertEqual(groups[0]["calls"], 2)

    def test_normal_user_gets_4005(self):
        payload = _client(NORMAL).get(USAGE).json()
        self.assertEqual(payload["code"], 4005)

    def test_anonymous_gets_4003(self):
        payload = APIClient().get(USAGE).json()
        self.assertEqual(payload["code"], 4003)

    def test_calls_filter_by_success(self):
        _row(success=False, latency_ms=500, error_code="timeout")
        _row(latency_ms=10)
        payload = _client(ADMIN).get(CALLS, {"range": "today", "success": "false"}).json()

        self.assertEqual(payload["code"], 2000)
        self.assertEqual(payload["data"]["total"], 1)
        self.assertEqual(payload["data"]["results"][0]["error_code"], "timeout")

    def test_calls_without_success_returns_all(self):
        # 回归：QueryDict 会让缺失的布尔参数被解析成 False
        _row(success=False)
        _row()
        payload = _client(ADMIN).get(CALLS, {"range": "today"}).json()
        self.assertEqual(payload["data"]["total"], 2)

    def test_calls_filter_by_min_latency(self):
        _row(latency_ms=10)
        _row(latency_ms=500)
        payload = _client(ADMIN).get(CALLS, {"range": "today", "min_latency": 100}).json()
        self.assertEqual(payload["data"]["total"], 1)

    def test_deleted_rows_are_excluded(self):
        row = _row()
        row.is_deleted = True
        row.save(update_fields=["is_deleted"])
        payload = _client(ADMIN).get(USAGE, {"range": "today"}).json()
        self.assertEqual(payload["data"]["summary"]["calls"], 0)

    def test_invalid_range_is_rejected(self):
        payload = _client(ADMIN).get(USAGE, {"range": "bad"}).json()
        self.assertEqual(payload["code"], 4000)

    def test_partial_failure_marks_block_unavailable(self):
        with mock.patch.object(services, "_summary", side_effect=RuntimeError("boom")):
            data = services.build_usage("7d", "user")
        self.assertFalse(data["summary"]["available"])
        self.assertTrue(data["trend"]["available"])


class RetentionTests(TestCase):
    def test_purge_soft_deletes_expired_rows_only(self):
        expired = _row()
        expired.create_time = datetime.now() - timedelta(days=services.RETENTION_DAYS + 1)
        models.LlmUsage.objects.filter(id=expired.id).update(create_time=expired.create_time)
        fresh = _row()

        self.assertEqual(services.purge_expired(), 1)

        expired.refresh_from_db()
        fresh.refresh_from_db()
        self.assertTrue(expired.is_deleted)
        self.assertFalse(fresh.is_deleted)
        # 软删除：行仍在表里
        self.assertEqual(models.LlmUsage.objects.count(), 2)

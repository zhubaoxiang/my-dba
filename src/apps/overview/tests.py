"""
首页总览模块单元测试

总览是跨模块的只读聚合，取数口径与「取不到时怎么办」是它的全部要点，因此这里
围绕这两件事覆盖。依赖数据库，用 `TestCase`。
"""

from datetime import datetime, timedelta
from unittest import mock

from django.test import TestCase
from rest_framework.test import APIClient

from apps.datasource import models as ds_models
from apps.knowledge import models as kb_models
from apps.overview import services
from utils import crypto, custom_enum

HIGH = custom_enum.IssueLevelEnum.HIGH.value
MEDIUM = custom_enum.IssueLevelEnum.MEDIUM.value
LOW = custom_enum.IssueLevelEnum.LOW.value


def _datasource(name, enabled=True):
    return ds_models.Datasource.objects.create(
        name=name,
        db_type=custom_enum.DbTypeEnum.POSTGRESQL.value,
        host="127.0.0.1",
        port=5432,
        db_name="d",
        username="u",
        password=crypto.encrypt("p"),
        is_enabled=enabled,
        creator="test",
    )


def _snapshot(datasource, table_count=10, minutes_ago=0):
    return ds_models.MetadataSnapshot.objects.create(
        datasource_id=datasource.id,
        collect_time=datetime.now() - timedelta(minutes=minutes_ago),
        raw_data={"tables": []},
        table_count=table_count,
    )


def _issue(datasource, snapshot, level):
    return ds_models.CatalogIssue.objects.create(
        datasource_id=datasource.id,
        snapshot_id=snapshot.id,
        issue_level=level,
        rule_code="some_rule",
        rule_name="某规则",
        object_level=custom_enum.ObjectLevelEnum.TABLE.value,
    )


def _task(datasource, status, fail_reason=""):
    return ds_models.CollectTask.objects.create(
        datasource_id=datasource.id,
        status=status,
        fail_reason=fail_reason,
        creator="test",
    )


def _metric(datasource, minutes_ago=0, **kwargs):
    fields = {"is_online": True, "creator": "test", **kwargs}
    row = ds_models.DatasourceMetric.objects.create(datasource_id=datasource.id, **fields)
    if minutes_ago:
        ds_models.DatasourceMetric.objects.filter(id=row.id).update(
            create_time=datetime.now() - timedelta(minutes=minutes_ago)
        )
        row.refresh_from_db()
    return row


class ReadinessTests(TestCase):
    """
    就绪状态只读本地配置，**不发起任何外部调用**
    """

    def build(self, chat=True, embedding=True):
        with (
            mock.patch.object(services.llm, "active_chat_provider", return_value=object() if chat else None),
            mock.patch.object(services.llm, "active_embedding_provider", return_value=object() if embedding else None),
        ):
            return services.build_overview()["readiness"]

    def test_ready_when_both_configured(self):
        readiness = self.build()
        self.assertTrue(readiness["ready"])
        self.assertEqual(readiness["missing"], [])

    def test_missing_chat_model_names_the_affected_features(self):
        readiness = self.build(chat=False)
        self.assertFalse(readiness["ready"])
        self.assertEqual([item["item"] for item in readiness["missing"]], ["对话模型"])
        self.assertIn("知识问答", readiness["missing"][0]["affected"])
        self.assertIn("AI 解读", readiness["missing"][0]["affected"])

    def test_missing_embedding_model_names_the_affected_features(self):
        readiness = self.build(embedding=False)
        self.assertEqual([item["item"] for item in readiness["missing"]], ["嵌入模型"])
        self.assertIn("检索", readiness["missing"][0]["affected"])

    def test_both_missing_are_both_listed(self):
        readiness = self.build(chat=False, embedding=False)
        self.assertEqual([item["item"] for item in readiness["missing"]], ["对话模型", "嵌入模型"])

    def test_does_not_probe_external_services(self):
        """
        探测 Qdrant 尤其危险：它的客户端超时取的是 knowledge_llm_timeout（默认 60 秒），
        向量服务一挂会把整个首页拖死。这条测试确保总览压根不去连它。
        """
        with mock.patch("qdrant_client.QdrantClient", side_effect=AssertionError("不该探测向量服务")):
            result = services.build_overview()
        self.assertTrue(result["readiness"]["available"])


class DatasourceStatusTests(TestCase):
    """
    库的状态：问题数取**最新快照**，没有快照就不给问题数
    """

    def item(self, name):
        return next(item for item in services.datasource_services.list_datasource_status() if item["name"] == name)

    def test_collected_datasource_reports_counts_from_latest_snapshot(self):
        datasource = _datasource("prod")
        old = _snapshot(datasource, table_count=5, minutes_ago=60)
        latest = _snapshot(datasource, table_count=12, minutes_ago=1)
        _issue(datasource, old, HIGH)
        _issue(datasource, latest, MEDIUM)
        _issue(datasource, latest, LOW)

        item = self.item("prod")
        self.assertEqual(item["collect_status"], "collected")
        self.assertEqual(item["table_count"], 12)
        self.assertEqual(item["issue_counts"], {HIGH: 0, MEDIUM: 1, LOW: 1})

    def test_never_collected_datasource_has_no_issue_counts(self):
        """「还没有数据」不能让使用者读成「没有问题」"""
        _datasource("fresh")
        item = self.item("fresh")
        self.assertEqual(item["collect_status"], "never")
        self.assertEqual(item["issue_counts"], {})
        self.assertEqual(item["table_count"], 0)

    def test_failed_datasource_reports_reason(self):
        datasource = _datasource("broken")
        _task(datasource, custom_enum.CollectTaskStatusEnum.FAILED.value, "连接被拒绝")
        item = self.item("broken")
        self.assertEqual(item["collect_status"], "failed")
        self.assertIn("连接被拒绝", item["fail_reason"])

    def test_running_datasource(self):
        datasource = _datasource("busy")
        _task(datasource, custom_enum.CollectTaskStatusEnum.RUNNING.value)
        self.assertEqual(self.item("busy")["collect_status"], "running")

    def test_running_outranks_an_existing_snapshot(self):
        """正在采集时该显示「采集中」，而不是停在上一份快照上"""
        datasource = _datasource("recoll")
        _snapshot(datasource)
        _task(datasource, custom_enum.CollectTaskStatusEnum.RUNNING.value)
        self.assertEqual(self.item("recoll")["collect_status"], "running")

    def test_datasources_needing_attention_come_first(self):
        """这个列表回答的是「我该关注哪个库」，有问题的要在前面"""
        healthy = _datasource("aaa_healthy")
        _snapshot(healthy, minutes_ago=1)
        broken = _datasource("zzz_broken")
        _task(broken, custom_enum.CollectTaskStatusEnum.FAILED.value, "连不上")
        _datasource("mmm_fresh")

        names = [item["name"] for item in services.datasource_services.list_datasource_status()]
        self.assertEqual(names.index("zzz_broken"), 0, "采集失败的排最前")
        self.assertEqual(names.index("mmm_fresh"), 1, "未采集的次之")
        self.assertEqual(names.index("aaa_healthy"), 2)

    def test_more_high_issues_sorts_first_among_collected(self):
        few = _datasource("few")
        _issue(few, _snapshot(few), HIGH)
        many = _datasource("many")
        many_snapshot = _snapshot(many)
        for _ in range(3):
            _issue(many, many_snapshot, HIGH)

        names = [item["name"] for item in services.datasource_services.list_datasource_status()]
        self.assertLess(names.index("many"), names.index("few"))

    def test_soft_deleted_datasource_is_excluded(self):
        datasource = _datasource("gone")
        datasource.is_deleted = True
        datasource.save(update_fields=["is_deleted"])
        self.assertEqual([item["name"] for item in services.datasource_services.list_datasource_status()], [])


class KnowledgeBaseStatusTests(TestCase):
    """
    知识库状态：文档数与摄入状态。失败必须能看出来。
    """

    def make_base(self, name):
        return kb_models.KnowledgeBase.objects.create(name=name, creator="test")

    def make_document(self, base, status, title="doc", chunk_count=0):
        return kb_models.KbDocument.objects.create(
            knowledge_base_id=base.id,
            title=title,
            source_type=custom_enum.DocumentSourceEnum.FILE.value,
            source=f"{title}.md",
            status=status,
            chunk_count=chunk_count,
            creator="test",
        )

    def test_counts_by_status(self):
        base = self.make_base("手册")
        self.make_document(base, custom_enum.DocumentStatusEnum.SUCCESS.value, "a")
        self.make_document(base, custom_enum.DocumentStatusEnum.SUCCESS.value, "b")
        self.make_document(base, custom_enum.DocumentStatusEnum.FAILED.value, "c")
        self.make_document(base, custom_enum.DocumentStatusEnum.PROCESSING.value, "d")

        item = services.knowledge_services.list_knowledge_base_status()[0]
        self.assertEqual(item["document_count"], 4)
        self.assertEqual(item["failed_count"], 1)
        self.assertEqual(item["pending_count"], 1)

    def test_bases_with_failures_come_first(self):
        self.make_base("aaa_ok")
        broken = self.make_base("zzz_broken")
        self.make_document(broken, custom_enum.DocumentStatusEnum.FAILED.value)

        names = [item["name"] for item in services.knowledge_services.list_knowledge_base_status()]
        self.assertEqual(names, ["zzz_broken", "aaa_ok"])

    def test_empty_base_reports_zero(self):
        self.make_base("空库")
        item = services.knowledge_services.list_knowledge_base_status()[0]
        self.assertEqual(item["document_count"], 0)
        self.assertEqual(item["failed_count"], 0)
        self.assertEqual(item["chunk_count"], 0)

    def test_chunk_count_separates_scale_from_retrievable_content(self):
        """
        一篇 200 页的 PDF 可能只切出 3 个块——「1 篇文档」与「1,204 个检索块」
        说的不是一件事，光看文档数看不出来
        """
        base = self.make_base("厚文档")
        self.make_document(base, custom_enum.DocumentStatusEnum.SUCCESS.value, "a", chunk_count=1200)
        self.make_document(base, custom_enum.DocumentStatusEnum.SUCCESS.value, "b", chunk_count=4)

        item = services.knowledge_services.list_knowledge_base_status()[0]
        self.assertEqual(item["document_count"], 2)
        self.assertEqual(item["chunk_count"], 1204)
        self.assertEqual(item["success_count"], 2)

    def test_chunk_count_ignores_soft_deleted_documents(self):
        base = self.make_base("手册")
        self.make_document(base, custom_enum.DocumentStatusEnum.SUCCESS.value, "a", chunk_count=10)
        gone = self.make_document(base, custom_enum.DocumentStatusEnum.SUCCESS.value, "b", chunk_count=99)
        gone.is_deleted = True
        gone.save(update_fields=["is_deleted"])

        self.assertEqual(services.knowledge_services.list_knowledge_base_status()[0]["chunk_count"], 10)

    def test_qa_session_count_shows_whether_anyone_uses_it(self):
        """「建了 10 个知识库、9 个没人问过」是常见情况，值得显出来"""
        used = self.make_base("常用")
        self.make_base("没人用")
        for _ in range(3):
            kb_models.QaSession.objects.create(knowledge_base_id=used.id, title="t", creator="test")

        by_name = {item["name"]: item for item in services.knowledge_services.list_knowledge_base_status()}
        self.assertEqual(by_name["常用"]["qa_session_count"], 3)
        self.assertEqual(by_name["没人用"]["qa_session_count"], 0)

    def test_last_updated_is_the_newest_document(self):
        base = self.make_base("手册")
        old = self.make_document(base, custom_enum.DocumentStatusEnum.SUCCESS.value, "a")
        kb_models.KbDocument.objects.filter(id=old.id).update(update_time=datetime.now() - timedelta(days=2))
        newest = self.make_document(base, custom_enum.DocumentStatusEnum.SUCCESS.value, "b")

        item = services.knowledge_services.list_knowledge_base_status()[0]
        self.assertEqual(item["last_updated"], newest.update_time.strftime("%Y-%m-%d %H:%M:%S"))

    def test_last_updated_is_empty_without_documents(self):
        """没有文档时给空串而不是当前时间——不能让「从没更新过」看起来像刚更新过"""
        self.make_base("空库")
        self.assertEqual(services.knowledge_services.list_knowledge_base_status()[0]["last_updated"], "")


class SummaryTests(TestCase):
    """
    顶部总览数字：由已取回的块算出来，取不到的项是 None 而不是 0
    """

    def summary(self):
        return services.build_overview()["summary"]

    def test_counts_datasources_online_and_issues(self):
        up = _datasource("up")
        down = _datasource("down")
        _datasource("unprobed")
        snapshot = _snapshot(up)
        _issue(up, snapshot, HIGH)
        _issue(up, snapshot, LOW)
        _metric(up, connection_count=3, max_connections=100)
        _metric(down, is_online=False, fail_reason="连接被拒绝")

        summary = self.summary()
        self.assertEqual(summary["datasource_total"], 3)
        self.assertEqual(summary["online_total"], 1)
        self.assertEqual(summary["issue_total"], 2)
        self.assertEqual(summary["issue_high_total"], 1)

    def test_unprobed_datasource_is_not_counted_as_offline(self):
        """还没采到指标的库是「不知道」，不能混进「已知离线」里"""
        _datasource("fresh")
        summary = self.summary()
        self.assertEqual(summary["datasource_total"], 1)
        self.assertEqual(summary["online_total"], 0)

    def test_unavailable_block_gives_none_not_zero(self):
        """
        0 是「数过了，一个也没有」，None 是「没数成」——混为一谈会让使用者
        以为「一个库都没掉线」
        """
        _datasource("ds")
        _metric(_datasource("other"))
        with mock.patch.object(
            services.datasource_services, "list_datasource_metrics", side_effect=RuntimeError("查询炸了")
        ):
            summary = self.summary()
        self.assertIsNone(summary["online_total"])
        self.assertEqual(summary["datasource_total"], 2, "其余部分仍要算出来")

    def test_knowledge_summary_counts_bases_and_documents(self):
        base = kb_models.KnowledgeBase.objects.create(name="手册", creator="test")
        for title in ("a", "b"):
            kb_models.KbDocument.objects.create(
                knowledge_base_id=base.id,
                title=title,
                source_type=custom_enum.DocumentSourceEnum.FILE.value,
                source=f"{title}.md",
                status=custom_enum.DocumentStatusEnum.SUCCESS.value,
                creator="test",
            )

        summary = self.summary()
        self.assertEqual(summary["knowledge_base_total"], 1)
        self.assertEqual(summary["document_total"], 2)

    def test_summary_is_consistent_with_the_cards(self):
        """数字与卡片来自同一份数据，不能各取一次而对不上"""
        datasource = _datasource("ds")
        _snapshot(datasource)
        _metric(datasource, connection_count=1, max_connections=100)

        overview = services.build_overview()
        self.assertEqual(overview["summary"]["datasource_total"], len(overview["datasources"]["items"]))
        self.assertEqual(
            overview["summary"]["online_total"],
            sum(1 for item in overview["metrics"]["items"] if item["is_online"]),
        )


class MetricsBlockTests(TestCase):
    """
    指标块：随库卡片一起返回，且与库卡片各自独立兜底
    """

    def test_metrics_are_returned_per_datasource(self):
        datasource = _datasource("ds")
        _metric(datasource, connection_count=9, max_connections=100, database_size=1024)
        _metric(datasource, minutes_ago=5, connection_count=4, max_connections=100)

        block = services.build_overview()["metrics"]
        self.assertTrue(block["available"])
        item = next(item for item in block["items"] if item["datasource_id"] == datasource.id)
        self.assertEqual(item["connection_count"], 9)
        self.assertEqual(item["trend"], [4, 9])
        self.assertTrue(item["collected_at"])

    def test_offline_datasource_carries_the_reason(self):
        datasource = _datasource("down")
        _metric(datasource, is_online=False, fail_reason="连接被拒绝")

        item = services.build_overview()["metrics"]["items"][0]
        self.assertFalse(item["is_online"])
        self.assertEqual(item["fail_reason"], "连接被拒绝")
        self.assertIsNone(item["connection_count"])

    def test_metrics_failure_leaves_the_datasource_cards_intact(self):
        """
        指标取不到只是少一块——库的名称、采集状态与问题数仍然要显示，
        否则使用者连「哪个库有问题」都看不到
        """
        datasource = _datasource("ds")
        _snapshot(datasource, table_count=7)
        with mock.patch.object(
            services.datasource_services, "list_datasource_metrics", side_effect=RuntimeError("查询炸了")
        ):
            overview = services.build_overview()

        self.assertFalse(overview["metrics"]["available"])
        self.assertEqual(overview["metrics"]["items"], [])
        self.assertTrue(overview["datasources"]["available"])
        self.assertEqual(overview["datasources"]["items"][0]["table_count"], 7)
        self.assertTrue(overview["summary"]["datasource_total"] is not None)


class OverviewApiTests(TestCase):
    """
    接口：一次取回全部，某块失败不拖垮整页，且不含任何凭据
    """

    URL = "/my-dba/v1/overview"

    def setUp(self):
        self.client = APIClient()

    def get(self):
        return self.client.get(self.URL).json()

    def test_returns_every_block(self):
        payload = self.get()
        self.assertEqual(payload["code"], 2000)
        for key in ("readiness", "summary", "datasources", "metrics", "knowledge_bases"):
            self.assertIn(key, payload["data"])
        for key in ("readiness", "datasources", "metrics", "knowledge_bases"):
            self.assertIn("available", payload["data"][key])

    def test_a_failing_block_does_not_break_the_rest(self):
        """
        首页是入口页，部分数据取不到时整页报错是最糟的结果——使用者连能用的
        部分也看不到了
        """
        _datasource("ds")
        with mock.patch.object(
            services.datasource_services, "list_datasource_status", side_effect=RuntimeError("查询炸了")
        ):
            data = self.get()["data"]
        self.assertFalse(data["datasources"]["available"])
        self.assertEqual(data["datasources"]["items"], [])
        self.assertTrue(data["knowledge_bases"]["available"], "其余部分必须照常返回")
        self.assertTrue(data["readiness"]["available"])

    def test_response_carries_no_credentials(self):
        datasource = _datasource("ds")
        _snapshot(datasource)
        body = self.client.get(self.URL).content.decode("utf-8")
        self.assertNotIn("password", body)
        self.assertNotIn(crypto.encrypt("p"), body)
        self.assertNotIn("api_key", body)

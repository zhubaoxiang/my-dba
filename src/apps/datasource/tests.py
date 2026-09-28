"""
数据源模块单元测试

crypto / analyzer / differ 均为纯计算，用 SimpleTestCase 即可，不需要数据库。
规则注册表的**覆盖合并**需读 `analysis_rule` 表，那一组用 TestCase
（依赖 `utils/test_runner.py` 把 sql/pg_struct.sql 灌进测试库）。
"""

from datetime import datetime
from unittest import mock

from django.test import SimpleTestCase, TestCase
from rest_framework.test import APIClient

from apps.datasource import analyzer, differ, models, serializers
from apps.datasource.rules import registry
from utils import crypto, custom_enum


class _FakeConf:
    """
    替换 Configure 单例，用于构造密钥缺失/不匹配的场景
    """

    def __init__(self, data):
        self._data = data

    def get(self, key, default=None):
        return self._data.get(key, default)


class CryptoTests(SimpleTestCase):
    """
    数据源凭据可逆加解密
    """

    def test_round_trip(self):
        cipher = crypto.encrypt("Passw0rd!机密")
        self.assertNotEqual(cipher, "Passw0rd!机密")
        self.assertEqual(crypto.decrypt(cipher), "Passw0rd!机密")

    def test_cipher_does_not_leak_plaintext(self):
        cipher = crypto.encrypt("Passw0rd!")
        self.assertNotIn("Passw0rd", cipher)

    def test_empty_value(self):
        self.assertEqual(crypto.encrypt(""), "")
        self.assertEqual(crypto.decrypt(""), "")

    def test_missing_key_raises_and_never_falls_back_to_plaintext(self):
        with mock.patch.object(crypto, "CONF_ATTR", _FakeConf({})), self.assertRaises(crypto.CryptoKeyError):
            crypto.encrypt("secret")

    def test_blank_key_raises(self):
        with (
            mock.patch.object(crypto, "CONF_ATTR", _FakeConf({"datasource_secret_key": "   "})),
            self.assertRaises(crypto.CryptoKeyError),
        ):
            crypto.encrypt("secret")

    def test_wrong_key_raises(self):
        with mock.patch.object(crypto, "CONF_ATTR", _FakeConf({"datasource_secret_key": "key-one"})):
            cipher = crypto.encrypt("secret")
        with (
            mock.patch.object(crypto, "CONF_ATTR", _FakeConf({"datasource_secret_key": "key-two"})),
            self.assertRaises(crypto.CryptoKeyError),
        ):
            crypto.decrypt(cipher)


class DatasourceSerializerTests(SimpleTestCase):
    """
    查询序列化不得带出密码
    """

    def test_password_excluded_from_output(self):
        instance = models.Datasource(
            name="prod-db",
            db_type=custom_enum.DbTypeEnum.POSTGRESQL.value,
            host="10.0.0.1",
            port=5432,
            db_name="app",
            username="reader",
            password=crypto.encrypt("Passw0rd!"),
            description="生产只读",
            is_enabled=True,
        )
        data = serializers.DatasourceSerializer(instance).data
        self.assertNotIn("password", data)
        self.assertNotIn("Passw0rd", str(data))

    def test_create_serializer_rejects_duplicate_name(self):
        serializer = serializers.DatasourceCreateSerializer(
            data={
                "name": "dup",
                "db_type": custom_enum.DbTypeEnum.POSTGRESQL.value,
                "host": "127.0.0.1",
                "port": 5432,
                "db_name": "app",
                "username": "reader",
                "password": "Passw0rd!",
            }
        )
        with mock.patch.object(serializers.models.Datasource.objects, "filter") as fake_filter:
            fake_filter.return_value.exclude.return_value.exists.return_value = True
            self.assertFalse(serializer.is_valid())


def _column(name, data_type="integer", length=-1, nullable=False):
    return {
        "schema": "public",
        "table": "t",
        "name": name,
        "ordinal": 1,
        "data_type": data_type,
        "length": length,
        "nullable": nullable,
        "default": "",
        "comment": "",
    }


def _index(name, columns, unique=False, primary=False, scans=None):
    return {
        "schema": "public",
        "table": "t",
        "name": name,
        "unique": unique,
        "primary": primary,
        "index_type": "btree",
        "columns": columns,
        "scans": scans,
    }


def _table(name, **overrides):
    table = {
        "schema": "public",
        "name": name,
        "comment": "",
        "row_count": 100,
        "row_count_exact": False,
        "data_size": 8192,
        "index_size": 0,
        "total_size": 8192,
        "columns": [],
        "indexes": [],
        "primary_key": {"name": f"pk_{name}", "columns": ["id"]},
        "foreign_keys": [],
    }
    table.update(overrides)
    return table


def _snapshot(tables, unavailable=None):
    return {"database_version": "16.0", "schemas": ["public"], "tables": tables, "unavailable": unavailable or []}


# 按规则 code 注入阈值（等值于原扁平阈值的语义，便于构造小体量的测试数据）
_RULE_OVERRIDES = {
    "unused_index": {"thresholds": {"unused_index_min_rows": 100}},
    "big_table": {"thresholds": {"big_table_rows": 1000, "big_table_size_mb": 1}},
    "suspicious_column_type": {"thresholds": {"varchar_max_length": 255}},
}


class AnalyzerTests(SimpleTestCase):
    """
    健康分析规则，每条规则覆盖正例与反例
    """

    def analyze(self, tables, unavailable=None):
        return analyzer.CatalogAnalyzer(_snapshot(tables, unavailable), rule_overrides=_RULE_OVERRIDES).analyze()

    def types_of(self, issues):
        return [issue["rule_code"] for issue in issues]

    def test_no_primary_key_detected(self):
        issues = self.analyze([_table("no_pk", primary_key=None)])
        self.assertIn("no_primary_key", self.types_of(issues))

    def test_table_with_primary_key_has_no_no_pk_issue(self):
        issues = self.analyze([_table("with_pk", indexes=[_index("pk_with_pk", ["id"], unique=True, primary=True)])])
        self.assertNotIn("no_primary_key", self.types_of(issues))

    def test_foreign_key_without_index(self):
        table = _table(
            "child",
            foreign_keys=[
                {
                    "name": "fk_parent",
                    "columns": ["parent_id"],
                    "ref_schema": "public",
                    "ref_table": "parent",
                    "ref_columns": ["id"],
                }
            ],
            indexes=[_index("pk_child", ["id"], unique=True, primary=True)],
        )
        issues = self.analyze([table])
        self.assertIn("fk_without_index", self.types_of(issues))

    def test_foreign_key_with_leading_index_is_clean(self):
        table = _table(
            "child",
            foreign_keys=[
                {
                    "name": "fk_parent",
                    "columns": ["parent_id"],
                    "ref_schema": "public",
                    "ref_table": "parent",
                    "ref_columns": ["id"],
                }
            ],
            indexes=[_index("idx_parent_id", ["parent_id"])],
        )
        self.assertNotIn("fk_without_index", self.types_of(self.analyze([table])))

    def test_duplicate_index(self):
        table = _table("dup", indexes=[_index("idx_a", ["x"]), _index("idx_b", ["x"])])
        issues = self.analyze([table])
        self.assertIn("duplicate_index", self.types_of(issues))

    def test_prefix_covered_index_is_redundant(self):
        table = _table("prefix", indexes=[_index("idx_a", ["x"]), _index("idx_ab", ["x", "y"])])
        issues = self.analyze([table])
        self.assertIn("duplicate_index", self.types_of(issues))

    def test_unused_index_detected(self):
        table = _table("unused", row_count=5000, indexes=[_index("idx_a", ["x"], scans=0)])
        issues = self.analyze([table])
        self.assertIn("unused_index", self.types_of(issues))

    def test_used_index_is_clean(self):
        table = _table("used", row_count=5000, indexes=[_index("idx_a", ["x"], scans=42)])
        self.assertNotIn("unused_index", self.types_of(self.analyze([table])))

    def test_unused_index_skipped_when_stats_unavailable(self):
        """
        design.md D8：统计信息缺失时跳过该规则，其余规则照常
        """
        table = _table("unused", row_count=5000, primary_key=None, indexes=[_index("idx_a", ["x"], scans=None)])
        issues = self.analyze([table], unavailable=["index_usage"])
        types = self.types_of(issues)
        self.assertNotIn("unused_index", types)
        self.assertIn("no_primary_key", types)

    def test_big_table_by_rows(self):
        issues = self.analyze([_table("big", row_count=99999)])
        self.assertIn("big_table", self.types_of(issues))

    def test_small_table_is_clean(self):
        self.assertNotIn("big_table", self.types_of(self.analyze([_table("small", row_count=10)])))

    def test_suspicious_time_stored_as_varchar(self):
        table = _table("t1", columns=[_column("create_time", "varchar", 32)])
        self.assertIn("suspicious_column_type", self.types_of(self.analyze([table])))

    def test_proper_timestamp_is_clean(self):
        table = _table("t2", columns=[_column("create_time", "timestamp")])
        self.assertNotIn("suspicious_column_type", self.types_of(self.analyze([table])))

    def test_overlong_varchar(self):
        table = _table("t3", columns=[_column("remark", "varchar", 4000)])
        self.assertIn("suspicious_column_type", self.types_of(self.analyze([table])))

    def test_large_object_column(self):
        table = _table("t4", columns=[_column("payload", "bytea")])
        self.assertIn("suspicious_column_type", self.types_of(self.analyze([table])))

    def test_text_and_json_are_not_flagged_as_large_objects(self):
        """
        text 是 PG 推荐字符串类型、jsonb 是结构化可索引类型，报为可疑会在正常库上产生大量噪音
        """
        table = _table("t5", columns=[_column("remark", "text"), _column("payload", "jsonb")])
        self.assertNotIn("suspicious_column_type", self.types_of(self.analyze([table])))

    def test_isolated_table(self):
        issues = self.analyze([_table("lonely")])
        self.assertIn("isolated_table", self.types_of(issues))

    def test_referenced_table_is_not_isolated(self):
        parent = _table("parent")
        child = _table(
            "child",
            foreign_keys=[
                {
                    "name": "fk",
                    "columns": ["parent_id"],
                    "ref_schema": "public",
                    "ref_table": "parent",
                    "ref_columns": ["id"],
                }
            ],
            indexes=[_index("idx_pid", ["parent_id"])],
        )
        types = self.types_of(self.analyze([parent, child]))
        self.assertNotIn("isolated_table", types)

    def test_clean_snapshot_returns_empty_list(self):
        clean = _table(
            "clean",
            columns=[_column("id", "integer"), _column("remark", "varchar", 64)],
            indexes=[_index("pk_clean", ["id"], unique=True, primary=True)],
            foreign_keys=[
                {
                    "name": "fk",
                    "columns": ["parent_id"],
                    "ref_schema": "public",
                    "ref_table": "other",
                    "ref_columns": ["id"],
                }
            ],
        )
        other = _table("other")
        clean["indexes"].append(_index("idx_pid", ["parent_id"]))
        issues = self.analyze([clean, other])
        self.assertEqual(issues, [])

    def test_no_tables_returns_empty_list(self):
        self.assertEqual(self.analyze([]), [])

    def test_issue_payload_shape(self):
        issues = self.analyze([_table("no_pk", primary_key=None)])
        issue = next(item for item in issues if item["rule_code"] == "no_primary_key")
        self.assertEqual(issue["issue_level"], custom_enum.IssueLevelEnum.HIGH.value)
        self.assertEqual(issue["table_name"], "no_pk")
        self.assertEqual(issue["target"], "public.no_pk")
        self.assertTrue(issue["description"])
        self.assertTrue(issue["suggestion"])


class RuleRegistryTests(SimpleTestCase):
    """
    规则注册表的覆盖合并（注入路径，**不查库**）

    分析器的既有测试是 `SimpleTestCase`（不碰数据库），靠 `rule_overrides` 注入覆盖项。
    这条路径必须完全绕开数据库，否则整组分析器测试都会被 Django 判为「数据库访问不允许」。
    """

    def runner(self, tables, overrides):
        return analyzer.CatalogAnalyzer(_snapshot(tables), rule_overrides=overrides)

    def codes(self, issues):
        return [item["rule_code"] for item in issues]

    def test_empty_injection_means_all_rules_enabled_with_code_defaults(self):
        effective = registry.load_effective_rules({})
        self.assertEqual([item.code for item in effective], registry.rule_codes())
        self.assertTrue(all(item.enabled for item in effective))

    def test_disabled_rule_is_skipped_and_not_counted(self):
        runner = self.runner([_table("no_pk", primary_key=None)], {"no_primary_key": {"enabled": False}})
        self.assertNotIn("no_primary_key", self.codes(runner.analyze()))
        self.assertNotIn("no_primary_key", runner.evaluated_rules, "停用的规则不该计入本次评估")

    def test_disabling_one_rule_does_not_affect_others(self):
        runner = self.runner([_table("no_pk", primary_key=None)], {"big_table": {"enabled": False}})
        self.assertIn("no_primary_key", self.codes(runner.analyze()))

    def test_configured_level_is_applied(self):
        """级别由框架按生效配置赋值——这正是「改级别不用改代码」的落点"""
        runner = self.runner([_table("no_pk", primary_key=None)], {"no_primary_key": {"level": 3}})
        issue = next(item for item in runner.analyze() if item["rule_code"] == "no_primary_key")
        self.assertEqual(issue["issue_level"], custom_enum.IssueLevelEnum.LOW.value)

    def test_rule_internal_level_subdivision_still_wins(self):
        """
        规则内部有级别细分时，handler 显式给出的级别优先于注册表配置（design.md D5）。
        前缀冗余索引恒为「低」，不因注册表把整条规则调成「高」而改变。
        """
        tables = [
            _table(
                "t",
                columns=[_column("a", "integer"), _column("b", "integer")],
                indexes=[_index("i_a", ["a"]), _index("i_ab", ["a", "b"])],
            )
        ]
        runner = self.runner(tables, {"duplicate_index": {"level": custom_enum.IssueLevelEnum.HIGH.value}})
        issues = [item for item in runner.analyze() if item["rule_code"] == "duplicate_index"]
        self.assertTrue(issues, "前缀冗余索引应当被判出")
        self.assertTrue(
            all(item["issue_level"] == custom_enum.IssueLevelEnum.LOW.value for item in issues),
            "规则显式给出的级别不该被注册表配置覆盖",
        )

    def test_exact_duplicate_follows_the_configured_level(self):
        """
        与上一条互补：列完全相同的重复索引**不显式给级别**，因此跟随注册表配置。
        两条一起界定了 D5 的实际语义——配置改的是规则的默认级别，不是规则内部的细分
        """
        tables = [_table("t", columns=[_column("a", "integer")], indexes=[_index("i1", ["a"]), _index("i2", ["a"])])]
        runner = self.runner(tables, {"duplicate_index": {"level": custom_enum.IssueLevelEnum.HIGH.value}})
        issues = [item for item in runner.analyze() if item["rule_code"] == "duplicate_index"]
        self.assertTrue(issues, "完全重复的索引应当被判出")
        self.assertTrue(all(item["issue_level"] == custom_enum.IssueLevelEnum.HIGH.value for item in issues))

    def test_partial_thresholds_merge_over_defaults(self):
        """库中只配了部分阈值时，漏配的键仍取代码默认值，不会因少写一项而失效"""
        declared = registry.get_rule("big_table").default_thresholds
        effective = next(
            item
            for item in registry.load_effective_rules({"big_table": {"thresholds": {"big_table_rows": 5}}})
            if item.code == "big_table"
        )
        self.assertEqual(effective.thresholds["big_table_rows"], 5)
        for key, value in declared.items():
            if key != "big_table_rows":
                self.assertEqual(effective.thresholds[key], value)

    def test_override_for_unknown_code_is_ignored(self):
        effective = registry.load_effective_rules({"not_a_real_rule": {"enabled": False}})
        self.assertEqual([item.code for item in effective], registry.rule_codes())

    def test_issue_coarser_than_declared_level_is_skipped(self):
        """
        规则产出比声明更粗的层级，说明声明写错了：记日志并跳过该条，
        MUST NOT 静默接受（design.md D2）
        """

        class _BadRule:
            code = "bad_rule"
            name = "层级写错的规则"
            object_level = custom_enum.ObjectLevelEnum.TABLE
            default_level = custom_enum.IssueLevelEnum.LOW
            default_thresholds = {}

            @staticmethod
            def handler(ctx):
                return [
                    ctx.issue(
                        "越级的问题",
                        "不该被接受",
                        target="whatever",
                        object_level=custom_enum.ObjectLevelEnum.DATABASE,
                    )
                ]

        with mock.patch.object(registry, "RULES", (_BadRule,)):
            issues = analyzer.CatalogAnalyzer(_snapshot([]), rule_overrides={}).analyze()
        self.assertEqual(issues, [], "越级的产出必须被框架挡下")


class RuleRegistryDbTests(TestCase):
    """
    规则注册表从 `analysis_rule` 表读覆盖项

    这是本模块唯一的 `TestCase`：覆盖合并离开数据库测不了。
    依赖 `utils/test_runner.py` 把 `sql/pg_struct.sql` 灌进测试库。
    """

    def effective(self, code):
        return next(item for item in registry.load_effective_rules() if item.code == code)

    def make_rule(self, **kwargs):
        defaults = {
            "code": "big_table",
            "name": "超大表",
            "level": custom_enum.IssueLevelEnum.MEDIUM.value,
            "object_level": custom_enum.ObjectLevelEnum.TABLE.value,
            "enabled": True,
        }
        return models.AnalysisRule.objects.create(**{**defaults, **kwargs})

    def test_no_rows_falls_back_to_code_defaults(self):
        """库里一条都没有时，全部规则按代码默认值生效——「没同步过」不影响功能"""
        effective = {item.code: item for item in registry.load_effective_rules()}
        self.assertEqual(sorted(effective), sorted(registry.rule_codes()))
        for code, item in effective.items():
            self.assertTrue(item.enabled)
            self.assertEqual(item.level, registry.get_rule(code).default_level)

    def test_row_overrides_are_applied(self):
        self.make_rule(enabled=False, level=custom_enum.IssueLevelEnum.LOW.value, thresholds={"big_table_rows": 7})
        item = self.effective("big_table")
        self.assertFalse(item.enabled)
        self.assertEqual(item.level, custom_enum.IssueLevelEnum.LOW)
        self.assertEqual(item.thresholds["big_table_rows"], 7)
        declared = registry.get_rule("big_table").default_thresholds
        self.assertEqual(item.thresholds["big_table_size_mb"], declared["big_table_size_mb"])

    def test_soft_deleted_row_is_ignored(self):
        self.make_rule(enabled=False, is_deleted=True)
        self.assertTrue(self.effective("big_table").enabled, "软删除的覆盖项不该生效")

    def test_invalid_level_in_db_falls_back_to_declared_default(self):
        self.make_rule(level=99)
        self.assertEqual(self.effective("big_table").level, registry.get_rule("big_table").default_level)

    def test_disabled_rule_is_skipped_by_the_analyzer(self):
        self.make_rule(code="no_primary_key", name="无主键表", enabled=False)
        runner = analyzer.CatalogAnalyzer(_snapshot([_table("no_pk", primary_key=None)]))
        self.assertNotIn("no_primary_key", self.codes(runner.analyze()))
        self.assertNotIn("no_primary_key", runner.evaluated_rules)

    @staticmethod
    def codes(issues):
        return [item["rule_code"] for item in issues]


class AnalysisRuleApiTests(TestCase):
    """
    规则管理接口：清单、同步、改覆盖项、恢复默认

    规则注册表的**可配置**部分离开数据库测不了，故这一组用 TestCase。
    """

    URL = "/my-dba/v1/analysis-rule"
    CATALOG = "/my-dba/v1/catalog"

    def setUp(self):
        self.client = APIClient()

    def sync(self):
        return self.client.post(f"{self.URL}/sync", {}, format="json").json()

    def rule_id(self, code):
        return models.AnalysisRule.objects.get(code=code, is_deleted=False).id

    def test_sync_creates_every_declared_rule(self):
        payload = self.sync()
        self.assertEqual(payload["code"], 2000)
        self.assertEqual(payload["data"]["created"], len(registry.all_rules()))
        self.assertEqual(payload["data"]["total"], len(registry.all_rules()))

    def test_sync_is_idempotent(self):
        self.sync()
        payload = self.sync()
        self.assertEqual(payload["data"]["created"], 0)
        self.assertEqual(payload["data"]["updated"], 0)
        self.assertEqual(models.AnalysisRule.objects.filter(is_deleted=False).count(), len(registry.all_rules()))

    def test_sync_does_not_overwrite_user_changes(self):
        """同步只更新名称/说明/层级，不碰开关、级别与阈值——否则一次部署就把调过的冲掉了"""
        self.sync()
        rid = self.rule_id("big_table")
        self.client.put(
            f"{self.URL}/{rid}", {"enabled": False, "level": 3, "thresholds": {"big_table_rows": 7}}, format="json"
        )
        self.sync()
        row = models.AnalysisRule.objects.get(id=rid)
        self.assertFalse(row.enabled)
        self.assertEqual(row.level, 3)
        self.assertEqual(row.thresholds["big_table_rows"], 7)

    def test_list_returns_rules_with_labels(self):
        self.sync()
        results = self.client.get(f"{self.URL}?page=1&page_size=100").json()["data"]["results"]
        self.assertEqual(len(results), len(registry.all_rules()))
        for key in ("code", "name", "level", "level_label", "object_level_label", "is_overridden", "thresholds"):
            self.assertIn(key, results[0])

    def test_update_level(self):
        self.sync()
        payload = self.client.put(f"{self.URL}/{self.rule_id('big_table')}", {"level": 3}, format="json").json()
        self.assertEqual(payload["data"]["level"], 3)
        self.assertTrue(payload["data"]["is_overridden"], "改过之后应当标记为已偏离默认值")

    def test_reset_restores_defaults(self):
        self.sync()
        rid = self.rule_id("big_table")
        self.client.put(
            f"{self.URL}/{rid}", {"enabled": False, "level": 3, "thresholds": {"big_table_rows": 7}}, format="json"
        )
        payload = self.client.post(f"{self.URL}/{rid}/reset").json()
        declared = registry.get_rule("big_table")
        self.assertTrue(payload["data"]["enabled"])
        self.assertEqual(payload["data"]["level"], declared.default_level.value)
        self.assertEqual(payload["data"]["thresholds"], declared.default_thresholds)
        self.assertFalse(payload["data"]["is_overridden"])

    def test_unknown_threshold_key_is_rejected(self):
        """写错键名不会报错、只会静默不生效，是这类配置最难排查的问题，必须拦下"""
        self.sync()
        payload = self.client.put(
            f"{self.URL}/{self.rule_id('big_table')}", {"thresholds": {"big_table_row": 7}}, format="json"
        ).json()
        self.assertEqual(payload["code"], 4000)
        self.assertIn("big_table_row", payload["message"])

    def test_non_numeric_threshold_is_rejected(self):
        self.sync()
        payload = self.client.put(
            f"{self.URL}/{self.rule_id('big_table')}", {"thresholds": {"big_table_rows": "很多"}}, format="json"
        ).json()
        self.assertEqual(payload["code"], 4000)

    def test_invalid_level_is_rejected(self):
        self.sync()
        payload = self.client.put(f"{self.URL}/{self.rule_id('big_table')}", {"level": 99}, format="json").json()
        self.assertEqual(payload["code"], 4000)

    def test_empty_body_is_rejected(self):
        self.sync()
        payload = self.client.put(f"{self.URL}/{self.rule_id('big_table')}", {}, format="json").json()
        self.assertEqual(payload["code"], 4000)

    def test_unknown_rule_returns_not_found(self):
        payload = self.client.put(f"{self.URL}/999999", {"level": 3}, format="json").json()
        self.assertEqual(payload["code"], 4004)

    def test_create_is_rejected(self):
        payload = self.client.post(self.URL, {"code": "x"}, format="json").json()
        self.assertEqual(payload["code"], 4000)

    def test_destroy_is_rejected(self):
        self.sync()
        payload = self.client.delete(f"{self.URL}/{self.rule_id('big_table')}").json()
        self.assertEqual(payload["code"], 4000)
        self.assertTrue(models.AnalysisRule.objects.filter(code="big_table", is_deleted=False).exists())

    def test_disabling_a_rule_through_the_api_stops_it_from_producing_issues(self):
        """端到端：接口停用一条规则 → 分析不再产出它。这是本变更的核心收益"""
        self.sync()
        self.client.put(f"{self.URL}/{self.rule_id('no_primary_key')}", {"enabled": False}, format="json")
        runner = analyzer.CatalogAnalyzer(_snapshot([_table("no_pk", primary_key=None)]))
        self.assertNotIn("no_primary_key", [item["rule_code"] for item in runner.analyze()])

    def test_summary_reports_evaluated_and_enabled_rule_counts(self):
        """两者不一致才说明规则集在采集之后被改过——只报一个数就看不出变化"""
        self.sync()
        datasource = models.Datasource.objects.create(
            name="ds",
            db_type=custom_enum.DbTypeEnum.POSTGRESQL.value,
            host="127.0.0.1",
            port=5432,
            db_name="d",
            username="u",
            password="p",
        )
        snapshot = models.MetadataSnapshot.objects.create(
            datasource_id=datasource.id,
            collect_time=datetime.now(),
            raw_data={"tables": []},
            evaluated_rules=["no_primary_key", "big_table"],
        )
        payload = self.client.get(f"{self.CATALOG}/summary?snapshot_id={snapshot.id}").json()
        self.assertEqual(payload["data"]["evaluated_rule_count"], 2)
        self.assertEqual(payload["data"]["evaluated_rules"], ["no_primary_key", "big_table"])
        self.assertEqual(payload["data"]["enabled_rule_count"], len(registry.all_rules()))


class CatalogIssueApiTests(TestCase):
    """
    问题清单接口：按规则筛选、历史问题的名称渲染

    依赖 `catalog_issue` 的 `rule_code` / `rule_name`，离开数据库测不了。
    """

    CATALOG = "/my-dba/v1/catalog"

    def setUp(self):
        self.client = APIClient()
        self.datasource = models.Datasource.objects.create(
            name="ds",
            db_type=custom_enum.DbTypeEnum.POSTGRESQL.value,
            host="127.0.0.1",
            port=5432,
            db_name="d",
            username="u",
            password="p",
        )
        self.snapshot = models.MetadataSnapshot.objects.create(
            datasource_id=self.datasource.id, collect_time=datetime.now(), raw_data={"tables": []}
        )

    def add_issue(self, rule_code, rule_name, level=custom_enum.IssueLevelEnum.HIGH.value):
        return models.CatalogIssue.objects.create(
            datasource_id=self.datasource.id,
            snapshot_id=self.snapshot.id,
            issue_level=level,
            rule_code=rule_code,
            rule_name=rule_name,
            object_level=custom_enum.ObjectLevelEnum.TABLE.value,
            table_name="t",
            description="说明",
            suggestion="建议",
        )

    def test_filter_by_rule_code(self):
        self.add_issue("no_primary_key", "无主键表")
        self.add_issue("big_table", "超大表", level=custom_enum.IssueLevelEnum.MEDIUM.value)
        payload = self.client.get(f"{self.CATALOG}/issues?snapshot_id={self.snapshot.id}&rule_code=big_table").json()
        results = payload["data"]["results"]
        self.assertEqual([item["rule_code"] for item in results], ["big_table"])

    def test_filter_by_issue_level_still_works(self):
        self.add_issue("no_primary_key", "无主键表")
        self.add_issue("big_table", "超大表", level=custom_enum.IssueLevelEnum.MEDIUM.value)
        payload = self.client.get(f"{self.CATALOG}/issues?snapshot_id={self.snapshot.id}&issue_level=1").json()
        self.assertEqual([item["rule_code"] for item in payload["data"]["results"]], ["no_primary_key"])

    def test_serializer_exposes_rule_fields_and_drops_issue_type(self):
        self.add_issue("no_primary_key", "无主键表")
        item = self.client.get(f"{self.CATALOG}/issues?snapshot_id={self.snapshot.id}").json()["data"]["results"][0]
        for key in ("rule_code", "rule_name", "object_level", "object_level_label", "issue_level_label"):
            self.assertIn(key, item)
        self.assertNotIn("issue_type", item)
        self.assertNotIn("issue_type_label", item)

    def test_historical_issue_renders_its_own_rule_name(self):
        """
        快照不可变：历史问题用自身行里快照下来的 `rule_name` 渲染，
        规则停用或改名后仍能正确展示（design.md D6）
        """
        self.add_issue("no_primary_key", "无主键表（旧名）")
        models.AnalysisRule.objects.create(
            code="no_primary_key",
            name="改了名的规则",
            level=custom_enum.IssueLevelEnum.LOW.value,
            object_level=custom_enum.ObjectLevelEnum.TABLE.value,
            enabled=False,
        )
        item = self.client.get(f"{self.CATALOG}/issues?snapshot_id={self.snapshot.id}").json()["data"]["results"][0]
        self.assertEqual(item["rule_name"], "无主键表（旧名）")


class DifferTests(SimpleTestCase):
    """
    快照差异对比
    """

    def test_table_and_column_changes(self):
        base = _snapshot([_table("kept", columns=[_column("id")]), _table("dropped")])
        target = _snapshot([_table("kept", columns=[_column("id"), _column("extra")]), _table("added")])
        result = differ.diff_snapshots(base, target)

        self.assertEqual(result["added_tables"], ["public.added"])
        self.assertEqual(result["removed_tables"], ["public.dropped"])
        self.assertEqual(len(result["changed_tables"]), 1)
        changed = result["changed_tables"][0]
        self.assertEqual(changed["table"], "public.kept")
        self.assertEqual(changed["added_columns"], ["extra"])

    def test_column_type_change(self):
        base = _snapshot([_table("t", columns=[_column("c", "varchar", 32)])])
        target = _snapshot([_table("t", columns=[_column("c", "varchar", 64)])])
        result = differ.diff_snapshots(base, target)
        changed = result["changed_tables"][0]["changed_columns"][0]
        self.assertEqual(changed["name"], "c")
        self.assertIn("32", changed["before"])
        self.assertIn("64", changed["after"])

    def test_index_changes(self):
        base = _snapshot([_table("t", indexes=[_index("idx_old", ["a"])])])
        target = _snapshot([_table("t", indexes=[_index("idx_new", ["a"])])])
        result = differ.diff_snapshots(base, target)
        changed = result["changed_tables"][0]
        self.assertEqual(changed["added_indexes"], ["idx_new"])
        self.assertEqual(changed["removed_indexes"], ["idx_old"])

    def test_identical_snapshots_have_no_changes(self):
        table = _table("t", columns=[_column("id")])
        result = differ.diff_snapshots(_snapshot([table]), _snapshot([table]))
        self.assertEqual(result["added_tables"], [])
        self.assertEqual(result["removed_tables"], [])
        self.assertEqual(result["changed_tables"], [])

    def test_empty_snapshots(self):
        result = differ.diff_snapshots({}, {})
        self.assertEqual(result, {"added_tables": [], "removed_tables": [], "changed_tables": []})

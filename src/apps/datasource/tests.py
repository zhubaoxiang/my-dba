"""
数据源模块单元测试

crypto / analyzer / differ 均为纯计算，用 SimpleTestCase 即可，不需要数据库。
规则注册表的**覆盖合并**需读 `analysis_rule` 表，那一组用 TestCase
（依赖 `utils/test_runner.py` 把 sql/pg_struct.sql 灌进测试库）。
"""

from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest import mock

from django.test import SimpleTestCase, TestCase
from rest_framework.test import APIClient

from apps.datasource import analyzer, differ, fixes, models, serializers, services, views
from apps.datasource import metrics as ds_metrics
from apps.datasource import services as ds_services
from apps.datasource.rules import registry
from apps.nl2sql import services as nl2sql_services
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

    def test_missing_override_does_not_log_a_warning(self):
        """
        「库里没有该规则的记录」是正常路径，不该每轮分析都为每条规则刷一条警告——
        那会把真正需要排查的脏数据淹掉
        """
        with mock.patch.object(registry.LOGGER, "warning") as warning:
            registry.load_effective_rules({})
        warning.assert_not_called()

    def test_invalid_level_logs_a_warning(self):
        """有值但不合法才是要排查的情况，必须留痕"""
        with mock.patch.object(registry.LOGGER, "warning") as warning:
            registry.load_effective_rules({"big_table": {"level": "高"}})
        warning.assert_called_once()

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
            "level": custom_enum.IssueLevelEnum.MEDIUM.value,
            "enabled": True,
        }
        return models.AnalysisRule.objects.create(**{**defaults, **kwargs})

    def test_no_rows_falls_back_to_code_defaults(self):
        """库里一条都没有时，全部规则按代码默认值生效——「一条都没调整过」不影响功能"""
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
        self.make_rule(code="no_primary_key", enabled=False)
        runner = analyzer.CatalogAnalyzer(_snapshot([_table("no_pk", primary_key=None)]))
        self.assertNotIn("no_primary_key", self.codes(runner.analyze()))
        self.assertNotIn("no_primary_key", runner.evaluated_rules)

    @staticmethod
    def codes(issues):
        return [item["rule_code"] for item in issues]


class AnalysisRuleApiTests(TestCase):
    """
    规则管理接口：清单（代码声明为基底）、改覆盖项、恢复默认

    规则注册表的**可配置**部分离开数据库测不了，故这一组用 TestCase。
    """

    URL = "/my-dba/v1/analysis-rule"
    CATALOG = "/my-dba/v1/catalog"

    def setUp(self):
        self.client = APIClient()

    def catalog(self):
        return self.client.get(f"{self.URL}?page=1&page_size=100").json()["data"]["results"]

    # ------------------------------------------------------------------
    # 清单：代码声明为基底 ⊕ 库中覆盖
    # ------------------------------------------------------------------

    def test_catalog_is_not_empty_without_any_db_row(self):
        """全新部署：库里一行都没有，清单仍须给出全部规则——首页说 7 条，点进来不该是空表"""
        self.assertEqual(models.AnalysisRule.objects.count(), 0)
        results = self.catalog()
        self.assertEqual(len(results), len(registry.all_rules()))
        for item in results:
            declared = registry.get_rule(item["code"])
            self.assertIsNone(item["id"], "未落库的规则没有自增 id")
            self.assertEqual(item["level"], declared.default_level.value)
            self.assertTrue(item["enabled"])
            self.assertFalse(item["is_overridden"])

    def test_catalog_returns_labels(self):
        first = self.catalog()[0]
        for key in (
            "code",
            "name",
            "description",
            "level",
            "level_label",
            "object_level_label",
            "is_overridden",
            "thresholds",
        ):
            self.assertIn(key, first)
        self.assertTrue(first["description"], "说明要真的给出内容，前端那一列才有意义")

    def test_catalog_reflects_overrides(self):
        self.client.put(f"{self.URL}/big_table", {"level": 3}, format="json")
        item = next(row for row in self.catalog() if row["code"] == "big_table")
        self.assertEqual(item["level"], 3)
        self.assertTrue(item["is_overridden"])

    def test_catalog_metadata_comes_from_the_declaration(self):
        """
        名称、说明与适用层级一律取代码声明；级别 / 启用 / 阈值取库里的覆盖值

        那三列**已不在表里**（随 DDL 删除），由注册表注入实例后交给序列化器。这条用例
        同时守住「序列化器必须显式声明 name / description」——模型没这两个字段，
        ModelSerializer 不显式声明就不会输出它们，「说明」列会整列空掉。
        """
        self.client.put(f"{self.URL}/big_table", {"level": 3}, format="json")
        item = next(row for row in self.catalog() if row["code"] == "big_table")
        declared = registry.get_rule("big_table")
        self.assertEqual(item["name"], declared.name)
        self.assertEqual(item["description"], declared.description)
        self.assertEqual(item["object_level_label"], declared.object_level.label)
        self.assertEqual(item["level"], 3, "级别是覆盖项，必须取库里的值")

    def test_update_response_carries_the_declared_name_and_description(self):
        """PUT 的响应也走「声明 ⊕ 覆盖」出口：名称与说明不在表里，漏了注入响应就会缺字段"""
        payload = self.client.put(f"{self.URL}/big_table", {"level": 3}, format="json").json()
        declared = registry.get_rule("big_table")
        self.assertEqual(payload["data"]["name"], declared.name)
        self.assertEqual(payload["data"]["description"], declared.description)

    def test_orphan_code_is_not_listed(self):
        """库里留着、已不在代码声明中的规则不进清单（design D6）"""
        models.AnalysisRule.objects.create(
            code="removed_rule",
            level=custom_enum.IssueLevelEnum.LOW.value,
        )
        self.assertNotIn("removed_rule", [row["code"] for row in self.catalog()])
        self.assertTrue(models.AnalysisRule.objects.filter(code="removed_rule").exists(), "孤儿行保留，不做删除")

    # ------------------------------------------------------------------
    # 修改：按 code 定位；库里没有时自动建行
    # ------------------------------------------------------------------

    def test_update_creates_the_row_on_first_change(self):
        payload = self.client.put(f"{self.URL}/big_table", {"level": 3}, format="json").json()
        self.assertEqual(payload["code"], 2000)
        self.assertEqual(payload["data"]["level"], 3)
        self.assertTrue(payload["data"]["is_overridden"])
        self.assertEqual(models.AnalysisRule.objects.filter(code="big_table").count(), 1)

    def test_second_update_reuses_the_same_row(self):
        self.client.put(f"{self.URL}/big_table", {"level": 3}, format="json")
        self.client.put(f"{self.URL}/big_table", {"enabled": False}, format="json")
        rows = models.AnalysisRule.objects.filter(code="big_table", is_deleted=False)
        self.assertEqual(rows.count(), 1)
        self.assertEqual(rows.first().level, 3)
        self.assertFalse(rows.first().enabled)

    def test_retrieve_by_code(self):
        payload = self.client.get(f"{self.URL}/big_table").json()
        self.assertEqual(payload["code"], 2000)
        self.assertEqual(payload["data"]["code"], "big_table")

    def test_reset_restores_defaults(self):
        self.client.put(
            f"{self.URL}/big_table", {"enabled": False, "level": 3, "thresholds": {"big_table_rows": 7}}, format="json"
        )
        payload = self.client.post(f"{self.URL}/big_table/reset").json()
        declared = registry.get_rule("big_table")
        self.assertTrue(payload["data"]["enabled"])
        self.assertEqual(payload["data"]["level"], declared.default_level.value)
        self.assertEqual(payload["data"]["thresholds"], declared.default_thresholds)
        self.assertFalse(payload["data"]["is_overridden"])

    def test_reset_on_untouched_rule_is_rejected(self):
        """没有可恢复的覆盖项时如实报错，而不是悄悄建一行再抹掉"""
        payload = self.client.post(f"{self.URL}/big_table/reset").json()
        self.assertEqual(payload["code"], 4000)
        self.assertFalse(models.AnalysisRule.objects.exists())

    # ------------------------------------------------------------------
    # 校验与拒绝
    # ------------------------------------------------------------------

    def test_unknown_threshold_key_is_rejected(self):
        """写错键名不会报错、只会静默不生效，是这类配置最难排查的问题，必须拦下"""
        payload = self.client.put(f"{self.URL}/big_table", {"thresholds": {"big_table_row": 7}}, format="json").json()
        self.assertEqual(payload["code"], 4000)
        self.assertIn("big_table_row", payload["message"])

    def test_non_numeric_threshold_is_rejected(self):
        payload = self.client.put(
            f"{self.URL}/big_table", {"thresholds": {"big_table_rows": "很多"}}, format="json"
        ).json()
        self.assertEqual(payload["code"], 4000)

    def test_invalid_level_is_rejected(self):
        payload = self.client.put(f"{self.URL}/big_table", {"level": 99}, format="json").json()
        self.assertEqual(payload["code"], 4000)

    def test_empty_body_is_rejected(self):
        payload = self.client.put(f"{self.URL}/big_table", {}, format="json").json()
        self.assertEqual(payload["code"], 4000)

    def test_unknown_rule_returns_not_found(self):
        payload = self.client.put(f"{self.URL}/no_such_rule", {"level": 3}, format="json").json()
        self.assertEqual(payload["code"], 4004)

    def test_create_is_rejected(self):
        payload = self.client.post(self.URL, {"code": "x"}, format="json").json()
        self.assertEqual(payload["code"], 4000)

    def test_destroy_is_rejected(self):
        self.client.put(f"{self.URL}/big_table", {"level": 3}, format="json")
        payload = self.client.delete(f"{self.URL}/big_table").json()
        self.assertEqual(payload["code"], 4000)
        self.assertTrue(models.AnalysisRule.objects.filter(code="big_table", is_deleted=False).exists())

    def test_sync_action_is_removed(self):
        """「同步」已随清单换基底一并移除：它不再是查看规则的前置动作"""
        self.assertFalse(hasattr(views.AnalysisRuleView, "sync"))

    # ------------------------------------------------------------------
    # 端到端
    # ------------------------------------------------------------------

    def test_disabling_a_rule_through_the_api_stops_it_from_producing_issues(self):
        """端到端：接口停用一条规则 → 分析不再产出它。这是本次改动的核心收益"""
        self.client.put(f"{self.URL}/no_primary_key", {"enabled": False}, format="json")
        runner = analyzer.CatalogAnalyzer(_snapshot([_table("no_pk", primary_key=None)]))
        self.assertNotIn("no_primary_key", [item["rule_code"] for item in runner.analyze()])

    def test_summary_reports_evaluated_and_enabled_rule_counts(self):
        """两者不一致才说明规则集在采集之后被改过——只报一个数就看不出变化"""
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

    def add_issue(self, rule_code, rule_name, level=custom_enum.IssueLevelEnum.HIGH.value, **extra):
        defaults = {
            "datasource_id": self.datasource.id,
            "snapshot_id": self.snapshot.id,
            "issue_level": level,
            "rule_code": rule_code,
            "rule_name": rule_name,
            "object_level": custom_enum.ObjectLevelEnum.TABLE.value,
            "table_name": "t",
            "description": "说明",
            "suggestion": "建议",
        }
        return models.CatalogIssue.objects.create(**{**defaults, **extra})

    def test_filter_by_rule_code(self):
        self.add_issue("no_primary_key", "无主键表")
        self.add_issue("big_table", "超大表", level=custom_enum.IssueLevelEnum.MEDIUM.value)
        payload = self.client.get(f"{self.CATALOG}/issues?snapshot_id={self.snapshot.id}&rule_code=big_table").json()
        results = payload["data"]["results"]
        self.assertEqual([item["rule_code"] for item in results], ["big_table"])

    def test_issues_carry_fix_statements(self):
        """修复语句随问题清单一并返回——对象确定的那几条由模板给出，不需要模型"""
        self.add_issue("fk_without_index", "外键缺索引", column_name="user_id")
        item = self.client.get(f"{self.CATALOG}/issues?snapshot_id={self.snapshot.id}").json()["data"]["results"][0]
        self.assertEqual(item["fix"]["source"], "rule")
        self.assertIn("CREATE INDEX", item["fix"]["sql"])
        self.assertTrue(item["fix"]["caveat"], "每条语句都要带风险前提")

    def test_issues_mark_the_ones_needing_the_model(self):
        """无唯一答案的**必须与模板生成的分开标**——不能让人同等信任"""
        self.add_issue("big_table", "超大表")
        item = self.client.get(f"{self.CATALOG}/issues?snapshot_id={self.snapshot.id}").json()["data"]["results"][0]
        self.assertEqual(item["fix"]["source"], "model_required")
        self.assertEqual(item["fix"]["sql"], "")

    def test_fix_key_is_exposed(self):
        """索引名必须能从接口读到——这是修复语句的原料"""
        self.add_issue("unused_index", "疑似未使用索引", fix_key="idx_old")
        item = self.client.get(f"{self.CATALOG}/issues?snapshot_id={self.snapshot.id}").json()["data"]["results"][0]
        self.assertEqual(item["fix_key"], "idx_old")
        self.assertIn("idx_old", item["fix"]["sql"])

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
        # 快照里记的是「无主键表（旧名）」，而代码声明现在叫「无主键表」——规则的名称只存在于
        # 代码声明（库里已无 name 列），这两者的差值就代表「规则改过名」。
        self.add_issue("no_primary_key", "无主键表（旧名）")
        models.AnalysisRule.objects.create(
            code="no_primary_key",
            level=custom_enum.IssueLevelEnum.LOW.value,
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


class _FakeCursor:
    """
    按 SQL 片段匹配返回值的替身游标；命中 failures 的片段则抛错
    """

    def __init__(self, rows_by_marker, failures=()):
        self.rows_by_marker = rows_by_marker
        self.failures = tuple(failures)
        self.executed = []
        self._current = []

    def execute(self, sql, *args):
        self.executed.append(sql)
        for marker in self.failures:
            if marker in sql:
                raise RuntimeError(f"不支持: {marker}")
        for marker, rows in self.rows_by_marker.items():
            if marker in sql:
                self._current = list(rows)
                return
        self._current = []

    def fetchone(self):
        return self._current[0] if self._current else None

    def fetchall(self):
        return self._current

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeConn:
    def __init__(self, cursor, db_type=None):
        self.db_type = db_type if db_type is not None else custom_enum.DbTypeEnum.POSTGRESQL.value
        self.cursor_obj = cursor
        self.rolled_back = 0

    def cursor(self, *args, **kwargs):
        return self.cursor_obj

    def rollback(self):
        self.rolled_back += 1

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


# PG 侧三条查询的返回值，按 marker 匹配
_PG_ROWS = {
    "pg_stat_activity": [(11, 100, 8951475)],
    "pg_stat_database": [(775521, 37)],
    "pg_stat_io": [(63684608, 38780928)],
}


class MetricProbeTests(SimpleTestCase):
    """
    指标探测：连不上、某项取不到都要如实反映，且**不抛异常**——一轮采集不该
    因为一个库出问题而整体中断。
    """

    def probe(self, cursor, db_type=None):
        connection = _FakeConn(cursor, db_type)
        # metrics.probe() 是函数内延迟导入 services 的，所以补丁打在 services 模块上
        with mock.patch.object(services, "open_readonly_connection", return_value=connection):
            return ds_metrics.probe(1), connection

    def test_connection_failure_is_reported_without_metrics(self):
        """连不上时只记原因，**不记任何指标**——记 0 会让人以为「连上了只是没负载」"""
        with mock.patch.object(services, "open_readonly_connection", side_effect=RuntimeError("连接被拒绝")):
            result = ds_metrics.probe(1)
        self.assertFalse(result["is_online"])
        self.assertIn("连接被拒绝", result["fail_reason"])
        self.assertNotIn("connection_count", result)

    def test_postgres_metrics_are_collected(self):
        result, _ = self.probe(_FakeCursor(_PG_ROWS))
        self.assertTrue(result["is_online"])
        self.assertEqual(result["connection_count"], 11)
        self.assertEqual(result["max_connections"], 100)
        self.assertEqual(result["database_size"], 8951475)
        self.assertEqual(result["cache_hit_count"], 775521)
        self.assertEqual(result["io_read_bytes"], 63684608)
        self.assertEqual(result["unavailable"], [])

    def test_missing_pg_stat_io_is_marked_unavailable(self):
        """PG 16 以下没有 pg_stat_io：留空并标注，MUST NOT 记 0"""
        cursor = _FakeCursor(_PG_ROWS, failures=("pg_stat_io",))
        result, connection = self.probe(cursor)
        self.assertNotIn("io_read_bytes", result)
        self.assertIn("io", result["unavailable"])
        self.assertEqual(result["connection_count"], 11, "一项取不到不该影响其余")
        self.assertGreaterEqual(connection.rolled_back, 1, "语句失败后必须回滚，否则后续查询全废")

    def test_mysql_metrics_are_collected(self):
        rows = {
            "Threads_connected": [("Threads_connected", "7")],
            "max_connections": [("max_connections", "151")],
            "information_schema": [(123456,)],
            "Innodb_buffer_pool_read_requests": [("Innodb_buffer_pool_read_requests", "900")],
            "Innodb_buffer_pool_reads": [("Innodb_buffer_pool_reads", "10")],
            "Innodb_data_read": [("Innodb_data_read", "2048")],
            "Innodb_data_written": [("Innodb_data_written", "4096")],
        }
        result, _ = self.probe(_FakeCursor(rows), db_type=custom_enum.DbTypeEnum.MYSQL.value)
        self.assertEqual(result["connection_count"], 7)
        self.assertEqual(result["max_connections"], 151)
        self.assertEqual(result["cache_hit_count"], 900)
        self.assertEqual(result["io_write_bytes"], 4096)
        self.assertEqual(result["unavailable"], [])

    def test_mysql_missing_status_variables_are_marked_unavailable(self):
        """非 InnoDB 引擎下没有那几个状态变量：SHOW 不报错、只是没有行，必须标为未采到"""
        rows = {
            "Threads_connected": [("Threads_connected", "7")],
            "max_connections": [("max_connections", "151")],
            "information_schema": [(123456,)],
        }
        result, _ = self.probe(_FakeCursor(rows), db_type=custom_enum.DbTypeEnum.MYSQL.value)
        self.assertEqual(result["connection_count"], 7, "一项取不到不该影响其余")
        self.assertIn("cache", result["unavailable"])
        self.assertIn("io", result["unavailable"])


class MetricCollectTests(TestCase):
    """
    采集与保留：单个数据源失败不影响其余；清理只删过期的
    """

    def make(self, name):
        return models.Datasource.objects.create(
            name=name,
            db_type=custom_enum.DbTypeEnum.POSTGRESQL.value,
            host="127.0.0.1",
            port=5432,
            db_name="d",
            username="u",
            password="p",
            creator="test",
        )

    def test_one_failure_does_not_stop_the_rest(self):
        first = self.make("a")
        second = self.make("b")
        calls = []

        def fake_probe(datasource_id, timeout=None):
            calls.append(datasource_id)
            if datasource_id == first.id:
                raise RuntimeError("这个库炸了")
            return {"is_online": True, "fail_reason": "", "unavailable": []}

        with mock.patch.object(ds_metrics, "probe", side_effect=fake_probe):
            summary = ds_metrics.collect_all()

        self.assertEqual(calls, [first.id, second.id], "第一个失败后仍要继续采第二个")
        self.assertEqual(summary, {"written": 1, "failed": 1})
        self.assertEqual(models.DatasourceMetric.objects.filter(datasource_id=second.id).count(), 1)

    def test_purge_only_removes_expired(self):
        datasource = self.make("c")
        old = models.DatasourceMetric.objects.create(datasource_id=datasource.id, is_online=True, creator="t")
        models.DatasourceMetric.objects.filter(id=old.id).update(
            create_time=datetime.now() - timedelta(days=ds_metrics.retention_days() + 1)
        )
        fresh = models.DatasourceMetric.objects.create(datasource_id=datasource.id, is_online=True, creator="t")

        ds_metrics.purge_expired()

        self.assertFalse(models.DatasourceMetric.objects.filter(id=old.id).exists())
        self.assertTrue(models.DatasourceMetric.objects.filter(id=fresh.id).exists())

    def test_soft_deleted_datasource_is_not_collected(self):
        datasource = self.make("gone")
        datasource.is_deleted = True
        datasource.save(update_fields=["is_deleted"])
        with mock.patch.object(ds_metrics, "probe") as probe:
            ds_metrics.collect_all()
        probe.assert_not_called()


class DownsampleTests(SimpleTestCase):
    """
    趋势降采样是纯函数，单测盯住它的两个要害：点数受控、**末尾一点不丢**
    """

    def test_short_series_is_returned_as_is(self):
        self.assertEqual(ds_services._downsample([1, 2, 3]), [1, 2, 3])

    def test_long_series_is_capped(self):
        self.assertEqual(len(ds_services._downsample(list(range(288)))), 48)

    def test_last_point_is_always_kept(self):
        """末尾是最新的值，丢了就看不到「刚刚发生了什么」"""
        self.assertEqual(ds_services._downsample(list(range(288)))[-1], 287)

    def test_order_is_preserved(self):
        picked = ds_services._downsample(list(range(288)))
        self.assertEqual(picked, sorted(picked))


class MetricQueryTests(TestCase):
    """
    指标查询契约：最新值、趋势只算在线点、区间命中率
    """

    def make(self, name="m"):
        return models.Datasource.objects.create(
            name=name,
            db_type=custom_enum.DbTypeEnum.POSTGRESQL.value,
            host="127.0.0.1",
            port=5432,
            db_name="d",
            username="u",
            password="p",
            creator="test",
        )

    def add(self, datasource, minutes_ago=0, **kwargs):
        fields = {"is_online": True, "creator": "t", **kwargs}
        row = models.DatasourceMetric.objects.create(datasource_id=datasource.id, **fields)
        if minutes_ago:
            models.DatasourceMetric.objects.filter(id=row.id).update(
                create_time=datetime.now() - timedelta(minutes=minutes_ago)
            )
            row.refresh_from_db()
        return row

    def item(self, datasource_id):
        return next(
            item for item in services.list_datasource_metrics()["items"] if item["datasource_id"] == datasource_id
        )

    def test_latest_sample_wins(self):
        datasource = self.make()
        self.add(datasource, minutes_ago=30, connection_count=3)
        self.add(datasource, minutes_ago=1, connection_count=17)
        self.assertEqual(self.item(datasource.id)["connection_count"], 17)

    def test_trend_excludes_offline_samples(self):
        """离线那几轮没有连接数可言，混进趋势会把线画歪"""
        datasource = self.make()
        self.add(datasource, minutes_ago=20, connection_count=5)
        self.add(datasource, minutes_ago=10, connection_count=None, is_online=False)
        self.add(datasource, minutes_ago=1, connection_count=7)
        self.assertEqual(self.item(datasource.id)["trend"], [5, 7])

    def test_trend_excludes_old_samples(self):
        datasource = self.make()
        self.add(datasource, minutes_ago=60 * 30, connection_count=99)  # 30 小时前
        self.add(datasource, minutes_ago=1, connection_count=4)
        self.assertEqual(self.item(datasource.id)["trend"], [4])

    def test_no_samples_means_empty(self):
        self.assertEqual(services.list_datasource_metrics(), {"items": []})

    def test_hit_ratio_uses_the_interval_between_samples(self):
        """
        用相邻两次的差值算，而不是累计值——累计比率是「自服务启动以来」的平均值，
        几乎不随近期变化而变动
        """
        datasource = self.make()
        self.add(datasource, minutes_ago=5, cache_hit_count=1000, cache_read_count=1000)
        self.add(datasource, minutes_ago=1, cache_hit_count=1900, cache_read_count=1100)
        # 区间：命中 900、未命中 100 → 90%
        self.assertEqual(self.item(datasource.id)["cache_hit_ratio"], 90.0)

    def test_hit_ratio_falls_back_when_counters_reset(self):
        """目标库重启会让计数器归零，差值为负时必须退回累计值，不能算出负命中率"""
        datasource = self.make()
        self.add(datasource, minutes_ago=5, cache_hit_count=9000, cache_read_count=1000)
        self.add(datasource, minutes_ago=1, cache_hit_count=90, cache_read_count=10)
        self.assertEqual(self.item(datasource.id)["cache_hit_ratio"], 90.0)

    def test_hit_ratio_is_none_when_unavailable(self):
        datasource = self.make()
        self.add(datasource, minutes_ago=1, connection_count=1)
        self.assertIsNone(self.item(datasource.id)["cache_hit_ratio"])

    def test_hit_ratio_skips_offline_samples(self):
        """
        离线那一轮没有计数器，不能当成「上一轮」；间隔要跨过它，接到上一个在线点
        """
        datasource = self.make()
        self.add(datasource, minutes_ago=30, cache_hit_count=1000, cache_read_count=1000)
        self.add(datasource, minutes_ago=20, is_online=False, fail_reason="连接被拒绝")
        self.add(datasource, minutes_ago=1, cache_hit_count=1900, cache_read_count=1100)
        self.assertEqual(self.item(datasource.id)["cache_hit_ratio"], 90.0)

    def test_latest_sample_is_reported_even_when_old(self):
        """
        最新值**不按时间截断**：采集进程停掉时，界面要靠这一行带着的采集时刻
        把「陈旧」暴露出来，而不是显示成「指标采集中」——那会让人以为再等一会就有了
        """
        datasource = self.make()
        self.add(datasource, minutes_ago=60 * 24 * 10, connection_count=42)  # 10 天前
        item = self.item(datasource.id)
        self.assertEqual(item["connection_count"], 42)
        self.assertEqual(item["trend"], [], "超出 24 小时的点不进趋势")


class FixTemplateTests(SimpleTestCase):
    """
    修复语句模板：纯计算，**不碰数据库**（阈值显式传入）

    最要紧的一条分界：模板生成的是「算出来的」，需模型参考的是「一种建议」——
    两者必须由 `source` 分得开，否则使用者会同等信任。
    """

    POSTGRES = custom_enum.DbTypeEnum.POSTGRESQL.value
    MYSQL = custom_enum.DbTypeEnum.MYSQL.value

    def issue(self, **kwargs):
        defaults = {
            "rule_code": "fk_without_index",
            "schema_name": "public",
            "table_name": "orders",
            "column_name": "user_id",
            "fix_key": "",
        }
        return SimpleNamespace(**{**defaults, **kwargs})

    def fix(self, issue, db_type=None):
        return fixes.fixes_for([issue], db_type or self.POSTGRES, limit=2000)[0]

    def test_foreign_key_index(self):
        fix = self.fix(self.issue())
        self.assertEqual(fix["source"], fixes.SOURCE_RULE)
        self.assertIn("CREATE INDEX", fix["sql"])
        self.assertIn('"orders"', fix["sql"])
        self.assertIn('"user_id"', fix["sql"])
        self.assertTrue(fix["caveat"], "索引名是拼出来的，前提里要说可以改")

    def test_duplicate_index_drops_by_name(self):
        fix = self.fix(self.issue(rule_code="duplicate_index", fix_key="idx_dup"))
        self.assertIn("DROP INDEX", fix["sql"])
        self.assertIn("idx_dup", fix["sql"])
        self.assertIn("依赖", fix["caveat"])

    def test_unused_index_caveat_tells_you_to_wait_a_cycle(self):
        fix = self.fix(self.issue(rule_code="unused_index", fix_key="idx_old"))
        self.assertIn("idx_old", fix["sql"])
        self.assertIn("业务周期", fix["caveat"])

    def test_isolated_table_gets_no_statement(self):
        """
        **刻意不给语句**：`isolated_table` 判的是「没有外键」，而大量正常库根本不建外键
        ——真机核对时某库 23 张表全被判成孤立，包括显然是业务表的那些。
        「没有外键」与「没人用」之间隔着一整个业务上下文，而 `DROP TABLE` 不可逆。
        """
        fix = self.fix(self.issue(rule_code="isolated_table", column_name=""))
        self.assertEqual(fix["sql"], "")
        self.assertEqual(fix["source"], fixes.SOURCE_ADVISORY)
        self.assertIn("没有业务在用", fix["note"])

    def test_no_primary_key_adds_surrogate(self):
        fix = self.fix(self.issue(rule_code="no_primary_key", column_name=""))
        self.assertIn("bigserial PRIMARY KEY", fix["sql"])
        self.assertIn("代理键", fix["caveat"])

    def test_char_time_uses_using_clause(self):
        fix = self.fix(self.issue(rule_code="suspicious_column_type", fix_key="char_time", column_name="created_at"))
        self.assertIn("TYPE timestamp", fix["sql"])
        self.assertIn("USING", fix["sql"])

    def test_long_varchar_uses_the_given_limit(self):
        fix = self.fix(self.issue(rule_code="suspicious_column_type", fix_key="long_varchar", column_name="remark"))
        self.assertIn("varchar(2000)", fix["sql"])

    def test_big_table_has_no_template(self):
        fix = self.fix(self.issue(rule_code="big_table", column_name=""))
        self.assertEqual(fix["sql"], "")
        self.assertEqual(fix["source"], fixes.SOURCE_MODEL_REQUIRED)

    def test_large_object_has_no_template(self):
        fix = self.fix(self.issue(rule_code="suspicious_column_type", fix_key="large_object"))
        self.assertEqual(fix["source"], fixes.SOURCE_MODEL_REQUIRED)

    def test_missing_object_name_gives_no_statement(self):
        """对象名缺失时**不硬拼**——宁可给不出，也不给一条猜的"""
        fix = self.fix(self.issue(rule_code="duplicate_index", fix_key=""))
        self.assertEqual(fix["sql"], "")
        self.assertEqual(fix["source"], fixes.SOURCE_MODEL_REQUIRED)

    def test_non_public_schema_is_qualified(self):
        fix = self.fix(self.issue(schema_name="reporting"))
        self.assertIn('"reporting"."orders"', fix["sql"])

    def test_public_schema_is_omitted(self):
        """public 是 PostgreSQL 的默认模式，写上反而啰嗦"""
        self.assertNotIn("public", self.fix(self.issue(schema_name="public"))["sql"])

    def test_mysql_is_unsupported_with_a_reason(self):
        """**方言不支持时必须明说，不静默降级**——MySQL 的写法不同且未在真机验证"""
        fix = self.fix(self.issue(), db_type=self.MYSQL)
        self.assertEqual(fix["source"], fixes.SOURCE_UNSUPPORTED)
        self.assertEqual(fix["sql"], "")
        self.assertIn("PostgreSQL", fix["note"])

    def test_fixes_align_with_issues(self):
        issues = [self.issue(), self.issue(rule_code="big_table", column_name="")]
        result = fixes.fixes_for(issues, self.POSTGRES, limit=2000)
        self.assertEqual(len(result), len(issues), "等长同序——错位会让语句挂到别的问题上")
        self.assertEqual(result[0]["source"], fixes.SOURCE_RULE)
        self.assertEqual(result[1]["source"], fixes.SOURCE_MODEL_REQUIRED)


class ReferenceFixTests(TestCase):
    """
    无唯一答案那类问题的**参考语句**：走 nl2sql 的模型接入（打桩）

    与模板生成的分开标 `source`——模板是算出来的，这里是建议。
    """

    def setUp(self):
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
            datasource_id=self.datasource.id,
            collect_time=datetime.now(),
            raw_data={"tables": []},
        )
        self.issue = models.CatalogIssue.objects.create(
            datasource_id=self.datasource.id,
            snapshot_id=self.snapshot.id,
            issue_level=custom_enum.IssueLevelEnum.MEDIUM.value,
            rule_code="big_table",
            rule_name="超大表",
            object_level=custom_enum.ObjectLevelEnum.TABLE.value,
            table_name="orders",
            description="这张表很大",
        )

    def stub(self, **result):
        return mock.patch.object(nl2sql_services, "generate_sql", return_value=result)

    def test_returns_sql_with_a_backend_supplied_caveat(self):
        with self.stub(sql="CREATE TABLE orders_new ...", generation={"available": True}) as call:
            data = services.reference_fix(self.datasource.id, self.issue.id)
        self.assertTrue(data["available"])
        self.assertIn("CREATE TABLE", data["sql"])
        # 前提由后端给，不依赖模型自己声明——那句是恒定的
        self.assertIn("策略", data["caveat"])
        # 问题的描述要说给模型，它才知道要修什么
        self.assertIn("这张表很大", call.call_args.args[1])

    def test_model_unavailable_is_reported_not_hidden(self):
        with self.stub(sql="", generation={"available": False, "note": "尚未配置对话模型，无法生成查询语句；…"}):
            data = services.reference_fix(self.datasource.id, self.issue.id)
        self.assertFalse(data["available"])
        self.assertIn("对话模型", data["note"])

    def test_unknown_issue_is_rejected(self):
        with self.assertRaises(services.DatasourceUnavailable):
            services.reference_fix(self.datasource.id, 999999)

    def test_issue_of_another_datasource_is_rejected(self):
        other = models.Datasource.objects.create(
            name="other",
            db_type=custom_enum.DbTypeEnum.POSTGRESQL.value,
            host="127.0.0.1",
            port=5432,
            db_name="d",
            username="u",
            password="p",
        )
        with self.assertRaises(services.DatasourceUnavailable):
            services.reference_fix(other.id, self.issue.id)


class IssueMuteTests(TestCase):
    """
    静音：**跨快照生效**，且**清单与统计同口径**

    后者是这次最容易做错的地方——只在清单里隐藏、首页照算，会立刻造出
    「首页 7 条、清单 5 条」这种两处打架。
    """

    def setUp(self):
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

    def add_issue(self, rule_code="no_primary_key", table_name="t", column_name="c", snapshot=None):
        return models.CatalogIssue.objects.create(
            datasource_id=self.datasource.id,
            snapshot_id=(snapshot or self.snapshot).id,
            issue_level=custom_enum.IssueLevelEnum.HIGH.value,
            rule_code=rule_code,
            rule_name="规则",
            object_level=custom_enum.ObjectLevelEnum.TABLE.value,
            table_name=table_name,
            column_name=column_name,
        )

    def listed(self, include_muted=False):
        queryset = models.CatalogIssue.objects.filter(is_deleted=False)
        muted = services.muted_filter()
        if not include_muted and muted is not None:
            queryset = queryset.exclude(muted)
        return queryset.count()

    def counted(self):
        counts = services.list_datasource_status()[0]["issue_counts"]
        return sum(counts.values())

    def test_mute_is_idempotent(self):
        first = services.mute_issue(self.datasource.id, "no_primary_key", "t", "c")
        second = services.mute_issue(self.datasource.id, "no_primary_key", "t", "c")
        self.assertEqual(first.id, second.id)
        self.assertEqual(models.IssueMute.objects.filter(is_deleted=False).count(), 1)

    def test_unknown_rule_is_rejected(self):
        with self.assertRaises(services.InvalidMuteKey):
            services.mute_issue(self.datasource.id, "no_such_rule")

    def test_mute_drops_both_the_list_and_the_count_by_one(self):
        """**两个数字必须同时动**——这是本变更最容易做错的地方"""
        self.add_issue()
        self.add_issue(rule_code="isolated_table", table_name="u")
        before_listed, before_counted = self.listed(), self.counted()
        self.assertEqual((before_listed, before_counted), (2, 2))

        services.mute_issue(self.datasource.id, "no_primary_key", "t", "c")

        after_listed, after_counted = self.listed(), self.counted()
        self.assertEqual(after_listed, before_listed - 1, "清单要少一条")
        self.assertEqual(after_counted, before_counted - 1, "统计也要少一条，不能只动一边")

    def test_no_mutes_does_not_zero_the_counts(self):
        """
        没有静音项时**一个数都不能少**

        空 `Q()` 在 Django 里是中性的，`.filter(Q())` 匹配全部行——拿它去做减法会把
        所有问题一起减掉、首页数字归零。这条用例就是钉住那个坑。
        """
        self.add_issue()
        self.assertIsNone(services.muted_filter(), "没有静音项时应当返回 None 而不是空 Q")
        self.assertEqual(self.counted(), 1)
        self.assertEqual(self.listed(), 1)

    def test_mute_survives_recollect(self):
        """问题行每次采集都会重建，静音**必须跨快照生效**"""
        self.add_issue()
        services.mute_issue(self.datasource.id, "no_primary_key", "t", "c")

        # 模拟重新采集：旧问题随旧快照一起作废，新快照产出同键的新问题
        new_snapshot = models.MetadataSnapshot.objects.create(
            datasource_id=self.datasource.id, collect_time=datetime.now(), raw_data={"tables": []}
        )
        models.CatalogIssue.objects.filter(snapshot_id=self.snapshot.id).update(is_deleted=True)
        self.add_issue(snapshot=new_snapshot)

        self.assertEqual(self.listed(), 0, "重新采集后静音仍应生效")
        self.assertEqual(self.counted(), 0)

    def test_mute_only_affects_that_one_place(self):
        """**只影响这一处**：同表其他问题、他表同类问题照常报"""
        self.add_issue(column_name="c")  # 被静音的这处
        self.add_issue(column_name="d")  # 同表、同规则、别的列
        self.add_issue(table_name="u", column_name="c")  # 他表、同规则
        services.mute_issue(self.datasource.id, "no_primary_key", "t", "c")
        self.assertEqual(self.listed(), 2)

    def test_unmute_brings_it_back(self):
        self.add_issue()
        row = services.mute_issue(self.datasource.id, "no_primary_key", "t", "c")
        self.assertEqual(self.listed(), 0)
        services.unmute_issue(self.datasource.id, row.id)
        self.assertEqual(self.listed(), 1)

    def test_can_mute_again_after_unmute(self):
        """软删除 + 部分唯一索引：同一处取消后还能重新静音"""
        row = services.mute_issue(self.datasource.id, "no_primary_key", "t", "c")
        services.unmute_issue(self.datasource.id, row.id)
        again = services.mute_issue(self.datasource.id, "no_primary_key", "t", "c")
        self.assertNotEqual(again.id, row.id)
        self.assertEqual(models.IssueMute.objects.filter(is_deleted=False).count(), 1)

    def test_unmute_rejects_another_datasources_record(self):
        other = models.Datasource.objects.create(
            name="other",
            db_type=custom_enum.DbTypeEnum.POSTGRESQL.value,
            host="127.0.0.1",
            port=5432,
            db_name="d",
            username="u",
            password="p",
        )
        row = services.mute_issue(self.datasource.id, "no_primary_key", "t", "c")
        with self.assertRaises(services.DatasourceUnavailable):
            services.unmute_issue(other.id, row.id)

    def test_mute_rule_name_comes_from_the_declaration(self):
        """规则名不存副本——库里的副本会「规则改名后永远显示旧名字」"""
        row = services.mute_issue(self.datasource.id, "no_primary_key")
        data = serializers.IssueMuteSerializer(row).data
        self.assertEqual(data["rule_name"], registry.get_rule("no_primary_key").name)


class IssueMuteApiTests(TestCase):
    """
    静音的接口：幂等、权限边界、口径一致
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
        models.CatalogIssue.objects.create(
            datasource_id=self.datasource.id,
            snapshot_id=self.snapshot.id,
            issue_level=custom_enum.IssueLevelEnum.HIGH.value,
            rule_code="no_primary_key",
            rule_name="无主键表",
            object_level=custom_enum.ObjectLevelEnum.TABLE.value,
            table_name="t",
            column_name="c",
        )

    def mute(self, **extra):
        body = {"datasource_id": self.datasource.id, "rule_code": "no_primary_key", "table_name": "t"}
        return self.client.post(f"{self.CATALOG}/issues/mute", {**body, **extra}, format="json").json()

    def issues(self):
        return self.client.get(f"{self.CATALOG}/issues?datasource_id={self.datasource.id}").json()["data"]

    def test_mute_hides_the_issue_and_reports_the_muted_count(self):
        self.assertEqual(self.issues()["total"], 1)
        self.assertEqual(self.mute(column_name="c")["code"], 2000)
        data = self.issues()
        self.assertEqual(data["total"], 0, "静音的默认不出现")
        self.assertEqual(data["muted_total"], 1, "但要给出数量，否则静音就成了无声的遗忘")

    def test_mute_is_idempotent_over_the_api(self):
        self.mute(column_name="c")
        self.assertEqual(self.mute(column_name="c")["code"], 2000)
        self.assertEqual(self.mute(column_name="c")["code"], 2000)
        self.assertEqual(models.IssueMute.objects.filter(is_deleted=False).count(), 1)

    def test_include_muted_shows_them_again(self):
        self.mute(column_name="c")
        payload = self.client.get(
            f"{self.CATALOG}/issues?datasource_id={self.datasource.id}&include_muted=true"
        ).json()["data"]
        self.assertEqual(payload["total"], 1)

    def test_muted_list_and_unmute(self):
        row_id = self.mute(column_name="c")["data"]["id"]
        listed = self.client.get(f"{self.CATALOG}/issues/muted?datasource_id={self.datasource.id}").json()["data"]
        self.assertEqual(listed["total"], 1)
        self.assertEqual(listed["results"][0]["rule_name"], "无主键表")

        payload = self.client.delete(f"{self.CATALOG}/issues/mute/{row_id}?datasource_id={self.datasource.id}").json()
        self.assertEqual(payload["code"], 2000)
        self.assertEqual(self.issues()["total"], 1, "取消后要回来")

    def test_unknown_rule_is_rejected(self):
        self.assertEqual(self.mute(rule_code="no_such_rule")["code"], 4000)

    def test_missing_parameters_are_rejected(self):
        self.assertEqual(self.client.post(f"{self.CATALOG}/issues/mute", {}, format="json").json()["code"], 4000)

    def test_unknown_datasource_returns_not_found(self):
        payload = self.client.post(
            f"{self.CATALOG}/issues/mute",
            {"datasource_id": 999999, "rule_code": "no_primary_key"},
            format="json",
        ).json()
        self.assertEqual(payload["code"], 4004)

    def test_response_does_not_leak_credentials(self):
        self.mute(column_name="c")
        rendered = str(self.client.get(f"{self.CATALOG}/issues/muted?datasource_id={self.datasource.id}").json())
        for key in ("password", "api_key"):
            self.assertNotIn(key, rendered)


class TableRelationTests(SimpleTestCase):
    """
    表的外键关系：纯计算

    最关键的用例是**本表一个外键都没有、却被别的表引用**——外键是单向声明的，
    只读本表会得出「无关系」这个**错误**结论。
    """

    def table(self, name, schema="public", foreign_keys=None):
        return {"schema": schema, "name": name, "columns": [], "foreign_keys": foreign_keys or []}

    def fk(self, columns, ref_table, ref_schema="public", ref_columns=None):
        return {
            "name": f"fk_{ref_table}",
            "columns": columns,
            "ref_schema": ref_schema,
            "ref_table": ref_table,
            "ref_columns": ref_columns or ["id"],
        }

    def test_both_directions(self):
        tables = [
            self.table("orders", foreign_keys=[self.fk(["user_id"], "app_user")]),
            self.table("app_user"),
            self.table("order_items", foreign_keys=[self.fk(["order_id"], "orders")]),
        ]
        rel = services.table_relations(tables, "public", "orders")
        self.assertEqual([item["table"] for item in rel["references"]], ["app_user"])
        self.assertEqual([item["table"] for item in rel["referenced_by"]], ["order_items"])

    def test_referenced_by_comes_from_the_whole_snapshot(self):
        """本表**没有任何外键**，却被引用——只看本表会得到「无关系」这个错误结论"""
        tables = [
            self.table("app_user"),
            self.table("orders", foreign_keys=[self.fk(["user_id"], "app_user")]),
        ]
        rel = services.table_relations(tables, "public", "app_user")
        self.assertEqual(rel["references"], [])
        self.assertEqual([item["table"] for item in rel["referenced_by"]], ["orders"])

    def test_columns_travel_with_the_relation(self):
        """要能显示「orders.user_id → app_user.id」，两侧列名都得带上"""
        tables = [
            self.table(
                "orders",
                foreign_keys=[self.fk(["user_id", "tenant_id"], "app_user", ref_columns=["id", "tenant_id"])],
            ),
            self.table("app_user"),
        ]
        item = services.table_relations(tables, "public", "orders")["references"][0]
        self.assertEqual(item["columns"], ["user_id", "tenant_id"])
        self.assertEqual(item["ref_columns"], ["id", "tenant_id"])

    def test_no_relations_at_all(self):
        rel = services.table_relations([self.table("lonely")], "public", "lonely")
        self.assertEqual(rel, {"references": [], "referenced_by": []})

    def test_case_is_ignored(self):
        tables = [self.table("App_User"), self.table("orders", foreign_keys=[self.fk(["u"], "APP_USER")])]
        self.assertEqual(len(services.table_relations(tables, "public", "app_user")["referenced_by"]), 1)

    def test_same_name_across_schemas_is_not_confused(self):
        """同名表跨模式必须按「模式 + 表名」对齐，否则会指错表"""
        tables = [
            self.table("orders", schema="shop"),
            self.table("orders", schema="archive"),
            self.table("items", schema="shop", foreign_keys=[self.fk(["order_id"], "orders", ref_schema="shop")]),
        ]
        self.assertEqual(services.table_relations(tables, "archive", "orders")["referenced_by"], [])
        self.assertEqual(len(services.table_relations(tables, "shop", "orders")["referenced_by"]), 1)

    def test_relation_to_a_table_outside_the_snapshot_is_marked_unresolvable(self):
        """指向快照里没有的表：名字照给，但标成不可跳转——否则点过去会落空"""
        tables = [self.table("orders", foreign_keys=[self.fk(["u"], "gone", ref_schema="other")])]
        item = services.table_relations(tables, "public", "orders")["references"][0]
        self.assertEqual(item["table"], "gone")
        self.assertFalse(item["resolvable"])

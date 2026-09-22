"""
数据源模块单元测试

crypto / analyzer / differ 均为纯计算，用 SimpleTestCase 即可，不需要数据库。
"""

from unittest import mock

from django.test import SimpleTestCase

from apps.datasource import analyzer, differ, models, serializers
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

"""
测试库预置单元测试

DDL / 造数是纯计算；接口与编排用 TestCase + mock 连接，不对真实数据库执行任何操作。
"""

import random
from datetime import datetime
from decimal import Decimal
from unittest import mock

from django.test import SimpleTestCase, TestCase
from rest_framework.test import APIClient

from apps.datasource import models as ds_models
from apps.provisioning import data, ddl, models, services
from utils import crypto, custom_enum
from utils.authentication import AuthedUser

POSTGRESQL = custom_enum.DbTypeEnum.POSTGRESQL.value
MYSQL = custom_enum.DbTypeEnum.MYSQL.value
TEST_ENV = custom_enum.DatasourceEnvEnum.TEST.value
PROD_ENV = custom_enum.DatasourceEnvEnum.PRODUCTION.value
READY = custom_enum.ProvisionStatusEnum.READY.value
FAILED = custom_enum.ProvisionStatusEnum.FAILED.value
DROPPED = custom_enum.ProvisionStatusEnum.DROPPED.value


def _column(name, data_type="integer", length=-1, nullable=False, comment="", default=""):
    return {
        "name": name,
        "data_type": data_type,
        "length": length,
        "nullable": nullable,
        "comment": comment,
        "default": default,
    }


def _table(name, schema="public", columns=None, primary_key=None, indexes=None, foreign_keys=None):
    return {
        "schema": schema,
        "name": name,
        "columns": columns or [_column("id")],
        "primary_key": primary_key if primary_key is not None else {"name": f"pk_{name}", "columns": ["id"]},
        "indexes": indexes or [],
        "foreign_keys": foreign_keys or [],
    }


def _foreign_key(name, columns, ref_table, ref_columns):
    return {
        "name": name,
        "columns": columns,
        "ref_schema": "public",
        "ref_table": ref_table,
        "ref_columns": ref_columns,
        "on_delete": "",
        "on_update": "",
    }


def _datasource(name, env_type=TEST_ENV, write=True, db_type=POSTGRESQL, creator="owner"):
    return ds_models.Datasource.objects.create(
        name=name,
        db_type=db_type,
        host="127.0.0.1",
        port=5432,
        db_name="d",
        username="reader",
        password=crypto.encrypt("read"),
        env_type=env_type,
        write_username="writer" if write else "",
        write_password=crypto.encrypt("write") if write else "",
        creator=creator,
    )


def _snapshot(datasource, tables):
    return ds_models.MetadataSnapshot.objects.create(
        datasource_id=datasource.id,
        collect_time=datetime.now(),
        raw_data={"tables": tables},
        table_count=len(tables),
    )


class DdlPlanTests(SimpleTestCase):
    def test_postgres_create_statements(self):
        table = _table(
            "orders",
            columns=[_column("id"), _column("email", "character varying", 64, comment="邮箱")],
            indexes=[
                {"name": "idx_email", "unique": True, "primary": False, "index_type": "btree", "columns": ["email"]}
            ],
        )

        plan = ddl.build_plan(POSTGRESQL, "dba_test_1_20260101000000_abcd", [table])

        self.assertIn('CREATE DATABASE "dba_test_1_20260101000000_abcd"', plan["create_database_sql"])
        self.assertTrue(any('CREATE TABLE "public"."orders"' in statement for statement in plan["statements"]))
        self.assertTrue(any("character varying(64)" in statement for statement in plan["statements"]))
        self.assertTrue(any('CREATE UNIQUE INDEX "idx_email"' in statement for statement in plan["statements"]))

    def test_mysql_create_statements(self):
        table = _table("orders", schema="d", columns=[_column("id"), _column("name", "varchar", 32)])

        plan = ddl.build_plan(MYSQL, "dba_test_1_20260101000000_abcd", [table])

        self.assertIn("CREATE DATABASE `dba_test_1_20260101000000_abcd`", plan["create_database_sql"])
        self.assertTrue(any("CREATE TABLE `orders`" in statement for statement in plan["statements"]))
        self.assertTrue(any("varchar(32)" in statement for statement in plan["statements"]))

    def test_foreign_key_only_when_both_tables_selected(self):
        parent = _table("users")
        child = _table(
            "orders",
            columns=[_column("id"), _column("user_id")],
            foreign_keys=[_foreign_key("fk_user", ["user_id"], "users", ["id"])],
        )

        both = ddl.build_plan(POSTGRESQL, "x", [parent, child])
        only_child = ddl.build_plan(POSTGRESQL, "x", [child])

        self.assertTrue(
            any("ADD CONSTRAINT" in statement and "FOREIGN KEY" in statement for statement in both["statements"])
        )
        self.assertFalse(any("FOREIGN KEY" in statement for statement in only_child["statements"]))
        self.assertTrue(any("未选中的表" in warning for warning in only_child["warnings"]))

    def test_mysql_text_index_uses_prefix(self):
        table = _table(
            "t",
            schema="d",
            columns=[_column("id"), _column("body", "text")],
            indexes=[
                {"name": "idx_body", "unique": False, "primary": False, "index_type": "btree", "columns": ["body"]}
            ],
        )

        plan = ddl.build_plan(MYSQL, "x", [table])

        self.assertTrue(any("`body`(191)" in statement for statement in plan["statements"]))

    def test_unsupported_type_rejected(self):
        pg_table = _table("t", columns=[_column("payload", "ARRAY")])
        mysql_table = _table("t", columns=[_column("status", "enum")])

        with self.assertRaises(ddl.DdlError):
            ddl.build_plan(POSTGRESQL, "x", [pg_table])
        with self.assertRaises(ddl.DdlError):
            ddl.build_plan(MYSQL, "x", [mysql_table])

    def test_sequence_default_is_skipped_with_warning(self):
        table = _table("t", columns=[_column("id", default="nextval('t_id_seq'::regclass)")])

        plan = ddl.build_plan(POSTGRESQL, "x", [table])

        self.assertFalse(any("DEFAULT" in statement for statement in plan["statements"]))
        self.assertTrue(any("序列默认值" in warning for warning in plan["warnings"]))


class DataGenerateTests(SimpleTestCase):
    def test_semantic_email_and_amount(self):
        table = _table(
            "users",
            columns=[
                _column("id"),
                _column("email", "character varying", 64, comment="邮箱"),
                _column("amount", "numeric", comment="金额"),
            ],
        )
        rows = data.generate_rows(table, 3, {}, {("public", "users")}, random.Random(1))

        self.assertEqual(rows[0]["email"], "test1@example.com")
        self.assertEqual(rows[0]["amount"], Decimal("1.25"))
        self.assertEqual(rows[2]["email"], "test3@example.com")

    def test_semantic_falls_back_when_type_incompatible(self):
        table = _table("t", columns=[_column("id"), _column("email")])
        notes = data.describe_semantics(table)
        rows = data.generate_rows(table, 2, {}, {("public", "t")}, random.Random(1))

        self.assertFalse(notes[0]["applied"])
        self.assertIsInstance(rows[0]["email"], int)

    def test_foreign_key_uses_parent_rows(self):
        parent = _table("users", columns=[_column("id")])
        child = _table(
            "orders",
            columns=[_column("id"), _column("user_id")],
            foreign_keys=[_foreign_key("fk_user", ["user_id"], "users", ["id"])],
        )
        parent_rows = data.generate_rows(parent, 3, {}, {("public", "users")}, random.Random(1))
        generated = {("public", "users"): parent_rows}

        child_rows = data.generate_rows(
            child, 5, generated, {("public", "users"), ("public", "orders")}, random.Random(2)
        )

        parent_ids = {row["id"] for row in parent_rows}
        self.assertTrue(all(row["user_id"] in parent_ids for row in child_rows))

    def test_same_seed_is_reproducible(self):
        table = _table("t", columns=[_column("id"), _column("uid", "uuid")])

        first = data.generate_rows(table, 3, {}, {("public", "t")}, random.Random(7))
        second = data.generate_rows(table, 3, {}, {("public", "t")}, random.Random(7))

        self.assertEqual(first, second)

    def test_unique_constraint_violation_raises(self):
        table = _table(
            "t",
            columns=[_column("flag", "boolean")],
            primary_key=None,
            indexes=[{"name": "uq_flag", "unique": True, "primary": False, "columns": ["flag"]}],
        )

        with self.assertRaises(data.DataError):
            data.generate_rows(table, 3, {}, {("public", "t")}, random.Random(1))

    def test_unable_to_generate_not_null_column_raises(self):
        table = _table("t", columns=[_column("span", "interval")], primary_key=None)

        with self.assertRaises(data.DataError):
            data.generate_rows(table, 1, {}, {("public", "t")}, random.Random(1))


class ProvisionPreviewTests(TestCase):
    def setUp(self):
        self.datasource = _datasource("test-ds")
        self.tables = [
            _table("users", columns=[_column("id"), _column("email", "character varying", 64, comment="邮箱")])
        ]
        _snapshot(self.datasource, self.tables)
        self.selection = [{"schema": "public", "name": "users", "rows": 5}]

    def test_preview_returns_plan_without_connecting(self):
        with mock.patch.object(services, "_connect_admin", side_effect=AssertionError("不应连接目标库")):
            plan = services.preview(self.datasource.id, self.selection, seed=1)

        self.assertTrue(plan["db_name"].startswith("dba_test_"))
        self.assertEqual(plan["total_rows"], 5)
        self.assertIn("CREATE DATABASE", plan["create_database_sql"])
        self.assertEqual(plan["seed"], 1)
        self.assertTrue(any(item["column"] == "email" for item in plan["tables"][0]["semantic_columns"]))

    def test_owner_can_preview_own_datasource(self):
        plan = services.preview(self.datasource.id, self.selection, seed=1, actor="owner", is_admin=False)

        self.assertTrue(plan["db_name"].startswith("dba_test_"))

    def test_other_user_cannot_preview_datasource(self):
        with self.assertRaisesMessage(services.ProvisionPermissionError, "自己创建的数据源"):
            services.preview(self.datasource.id, self.selection, actor="someone", is_admin=False)

    def test_production_datasource_is_rejected(self):
        prod = _datasource("prod-ds", env_type=PROD_ENV)
        _snapshot(prod, self.tables)

        with self.assertRaisesMessage(services.ProvisionError, "测试环境"):
            services.preview(prod.id, self.selection)

    def test_missing_write_credential_is_rejected(self):
        datasource = _datasource("no-write", write=False)
        _snapshot(datasource, self.tables)

        with self.assertRaisesMessage(services.ProvisionError, "预置写账号"):
            services.preview(datasource.id, self.selection)

    def test_missing_snapshot_is_rejected(self):
        datasource = _datasource("never-collected")

        with self.assertRaisesMessage(services.ProvisionError, "尚未采集"):
            services.preview(datasource.id, self.selection)

    def test_row_limit_is_enforced(self):
        with self.assertRaisesMessage(services.ProvisionError, "单表行数"):
            services.preview(self.datasource.id, [{"schema": "public", "name": "users", "rows": 1001}])

    def test_cyclic_foreign_keys_are_rejected(self):
        a = _table(
            "a",
            columns=[_column("id"), _column("b_id")],
            foreign_keys=[_foreign_key("fk_a_b", ["b_id"], "b", ["id"])],
        )
        b = _table(
            "b",
            columns=[_column("id"), _column("a_id")],
            foreign_keys=[_foreign_key("fk_b_a", ["a_id"], "a", ["id"])],
        )
        datasource = _datasource("cycle-ds")
        _snapshot(datasource, [a, b])

        with self.assertRaisesMessage(services.ProvisionError, "循环外键"):
            services.preview(
                datasource.id,
                [
                    {"schema": "public", "name": "a", "rows": 2},
                    {"schema": "public", "name": "b", "rows": 2},
                ],
            )


class ProvisionExecuteTests(TestCase):
    def setUp(self):
        self.datasource = _datasource("exec-ds")
        _snapshot(self.datasource, [_table("users", columns=[_column("id")])])
        self.selection = [{"schema": "public", "name": "users", "rows": 3}]
        self.db_name = services._generate_db_name(services._config()["db_prefix"], self.datasource.id)

    def test_requires_confirmation(self):
        with self.assertRaisesMessage(services.ProvisionError, "确认"):
            services.provision(self.datasource.id, self.selection, self.db_name, confirm=False)

    def test_invalid_db_name_is_rejected(self):
        with self.assertRaisesMessage(services.ProvisionError, "库名不合法"):
            services.provision(self.datasource.id, self.selection, "bad_name", confirm=True)

    def test_happy_path_marks_record_ready(self):
        with (
            mock.patch.object(services, "_database_exists", return_value=False),
            mock.patch.object(services, "_create_database"),
            mock.patch.object(services, "_create_tables_and_data", return_value=3),
        ):
            record, warnings = services.provision(
                self.datasource.id, self.selection, self.db_name, seed=1, confirm=True, creator="tester"
            )

        self.assertEqual(record.status, READY)
        self.assertEqual(record.row_count, 3)
        self.assertEqual(record.creator, "tester")
        self.assertIsInstance(warnings, list)

    def test_failure_marks_record_and_tries_cleanup(self):
        with (
            mock.patch.object(services, "_database_exists", return_value=False),
            mock.patch.object(services, "_create_database"),
            mock.patch.object(services, "_create_tables_and_data", side_effect=RuntimeError("boom")),
            mock.patch.object(services, "_drop_database") as drop,
            self.assertRaisesMessage(services.ProvisionError, "boom"),
        ):
            services.provision(self.datasource.id, self.selection, self.db_name, confirm=True)

        drop.assert_called_once()
        record = models.ProvisionedDatabase.objects.get(datasource_id=self.datasource.id)
        self.assertEqual(record.status, FAILED)
        self.assertIn("boom", record.error)

    def test_create_database_failure_does_not_drop_existing_db(self):
        with (
            mock.patch.object(services, "_database_exists", return_value=False),
            mock.patch.object(services, "_create_database", side_effect=RuntimeError("create failed")),
            mock.patch.object(services, "_drop_database") as drop,
            self.assertRaises(services.ProvisionError),
        ):
            services.provision(self.datasource.id, self.selection, self.db_name, confirm=True)

        drop.assert_not_called()

    def test_existing_database_is_rejected(self):
        with (
            mock.patch.object(services, "_database_exists", return_value=True),
            self.assertRaisesMessage(services.ProvisionError, "同名数据库"),
        ):
            services.provision(self.datasource.id, self.selection, self.db_name, confirm=True)

    def test_owner_can_delete_own_record(self):
        record = models.ProvisionedDatabase.objects.create(
            datasource_id=self.datasource.id,
            db_name=self.db_name,
            status=READY,
            tables=self.selection,
            row_count=3,
            creator="owner",
        )
        with mock.patch.object(services, "_drop_database"):
            services.delete_record(record, actor="owner", is_admin=False)

        record.refresh_from_db()
        self.assertEqual(record.status, DROPPED)

    def test_non_owner_cannot_delete_record(self):
        record = models.ProvisionedDatabase.objects.create(
            datasource_id=self.datasource.id,
            db_name=self.db_name,
            status=READY,
            tables=self.selection,
            row_count=3,
            creator="owner",
        )
        with self.assertRaisesMessage(services.ProvisionPermissionError, "自己创建的测试库"):
            services.delete_record(record, actor="someone", is_admin=False)

    def test_delete_record_only_accepts_recorded_database(self):
        record = models.ProvisionedDatabase.objects.create(
            datasource_id=self.datasource.id,
            db_name=self.db_name,
            status=READY,
            tables=self.selection,
            row_count=3,
            creator="tester",
        )
        with mock.patch.object(services, "_drop_database") as drop:
            services.delete_record(record)

        drop.assert_called_once()
        record.refresh_from_db()
        self.assertEqual(record.status, DROPPED)
        with self.assertRaisesMessage(services.ProvisionError, "已删除"):
            services.delete_record(record)


class TestDataExportTests(TestCase):
    """
    测试数据文件导出：只读快照，不需要测试环境或写账号
    """

    def setUp(self):
        # 故意用生产环境且不配置写账号：导出只读元数据，应当可用
        self.datasource = _datasource("export-ds", env_type=PROD_ENV, write=False, creator="owner")
        _snapshot(
            self.datasource,
            [
                _table(
                    "users",
                    columns=[_column("id"), _column("email", "character varying", 64, comment="邮箱")],
                ),
                _table(
                    "orders",
                    columns=[_column("id"), _column("user_id")],
                    foreign_keys=[_foreign_key("fk_user", ["user_id"], "users", ["id"])],
                ),
            ],
        )

    def test_non_owner_is_denied(self):
        with self.assertRaises(services.ProvisionPermissionError):
            services.export_test_data(
                self.datasource.id,
                [{"schema": "public", "name": "users", "rows": 1}],
                actor="other",
                is_admin=False,
            )

    def test_export_generates_sql_without_connecting(self):
        with (
            mock.patch.object(services, "_connect_admin", side_effect=AssertionError("不应连接目标库")),
            mock.patch.object(services, "_connect_target", side_effect=AssertionError("不应连接目标库")),
        ):
            payload = services.export_test_data(
                self.datasource.id,
                [{"schema": "public", "name": "users", "rows": 2}],
                seed=1,
                actor="owner",
                is_admin=False,
            )

        self.assertTrue(payload["filename"].endswith(".sql"))
        self.assertEqual(payload["total_rows"], 2)
        self.assertIn('INSERT INTO "public"."users"', payload["content"])
        self.assertIn("test1@example.com", payload["content"])
        self.assertNotIn("CREATE TABLE", payload["content"])

    def test_include_ddl_adds_create_table(self):
        payload = services.export_test_data(
            self.datasource.id,
            [{"schema": "public", "name": "users", "rows": 1}],
            include_ddl=True,
            actor="owner",
            is_admin=False,
        )

        self.assertIn('CREATE TABLE "public"."users"', payload["content"])

    def test_parent_rows_are_inserted_before_children(self):
        payload = services.export_test_data(
            self.datasource.id,
            [
                {"schema": "public", "name": "orders", "rows": 2},
                {"schema": "public", "name": "users", "rows": 2},
            ],
            seed=1,
            actor="owner",
            is_admin=False,
        )

        parent = payload["content"].index('INSERT INTO "public"."users"')
        child = payload["content"].index('INSERT INTO "public"."orders"')
        self.assertLess(parent, child)

    def test_unselected_fk_is_warned(self):
        payload = services.export_test_data(
            self.datasource.id,
            [{"schema": "public", "name": "orders", "rows": 1}],
            actor="owner",
            is_admin=False,
        )

        self.assertTrue(any("未选中的表" in warning for warning in payload["warnings"]))

    def test_same_seed_produces_same_data_section(self):
        tables = [{"schema": "public", "name": "users", "rows": 2}]
        first = services.export_test_data(self.datasource.id, tables, seed=7, actor="owner", is_admin=False)
        second = services.export_test_data(self.datasource.id, tables, seed=7, actor="owner", is_admin=False)

        first_data = first["content"].split("-- ===== 数据 =====")[1]
        second_data = second["content"].split("-- ===== 数据 =====")[1]
        self.assertEqual(first_data, second_data)

    def test_row_limit_is_enforced(self):
        with self.assertRaisesMessage(services.ProvisionError, "单表行数"):
            services.export_test_data(
                self.datasource.id,
                [{"schema": "public", "name": "users", "rows": 1001}],
                actor="owner",
                is_admin=False,
            )

    DDL = "CREATE TABLE users (id integer PRIMARY KEY, email varchar(64) NOT NULL);"

    def test_parse_table_structure(self):
        result = services.parse_table_structure(POSTGRESQL, self.DDL)

        self.assertEqual(result["tables"][0]["name"], "users")
        self.assertEqual(result["tables"][0]["column_count"], 2)

    def test_parse_rejects_drop(self):
        with self.assertRaisesMessage(services.ProvisionError, "仅支持"):
            services.parse_table_structure(POSTGRESQL, "DROP TABLE users;")

    def test_export_from_ddl(self):
        with (
            mock.patch.object(services, "_connect_admin", side_effect=AssertionError("不应连接目标库")),
            mock.patch.object(services, "_connect_target", side_effect=AssertionError("不应连接目标库")),
        ):
            payload = services.export_test_data_from_ddl(
                POSTGRESQL,
                self.DDL,
                [{"schema": "public", "name": "users", "rows": 2}],
                seed=1,
            )

        self.assertIsNone(payload["datasource_id"])
        self.assertIn("manual", payload["filename"])
        self.assertIn('INSERT INTO "public"."users"', payload["content"])
        self.assertEqual(payload["total_rows"], 2)

    def test_export_from_ddl_include_ddl(self):
        payload = services.export_test_data_from_ddl(
            POSTGRESQL,
            self.DDL,
            [{"schema": "public", "name": "users", "rows": 1}],
            include_ddl=True,
        )

        self.assertIn('CREATE TABLE "public"."users"', payload["content"])

    def test_export_from_ddl_orders_parent_first(self):
        ddl = (
            "CREATE TABLE users (id integer PRIMARY KEY);"
            "CREATE TABLE orders (id integer PRIMARY KEY, user_id integer REFERENCES users(id));"
        )
        payload = services.export_test_data_from_ddl(
            POSTGRESQL,
            ddl,
            [
                {"schema": "public", "name": "orders", "rows": 2},
                {"schema": "public", "name": "users", "rows": 2},
            ],
            seed=1,
        )

        parent = payload["content"].index('INSERT INTO "public"."users"')
        child = payload["content"].index('INSERT INTO "public"."orders"')
        self.assertLess(parent, child)


class TestDataApiTests(TestCase):
    URL = "/my-dba/v1/test-data/file"

    def setUp(self):
        self.datasource = _datasource("export-api", creator="owner")
        _snapshot(self.datasource, [_table("users", columns=[_column("id")])])
        self.tables = [{"schema": "public", "name": "users", "rows": 1}]

    def test_owner_can_generate_file(self):
        client = APIClient()
        client.force_authenticate(user=AuthedUser(3, "owner", "user"))

        payload = client.post(
            self.URL,
            {"datasource_id": self.datasource.id, "tables": self.tables, "seed": 1},
            format="json",
        ).json()

        self.assertEqual(payload["code"], 2000)
        self.assertIn("INSERT INTO", payload["data"]["content"])

    def test_non_owner_is_denied(self):
        client = APIClient()
        client.force_authenticate(user=AuthedUser(2, "user", "user"))

        payload = client.post(
            self.URL,
            {"datasource_id": self.datasource.id, "tables": self.tables},
            format="json",
        ).json()

        self.assertEqual(payload["code"], 4005)

    def test_unauthenticated_is_rejected(self):
        payload = (
            APIClient()
            .post(
                self.URL,
                {"datasource_id": self.datasource.id, "tables": self.tables},
                format="json",
            )
            .json()
        )

        self.assertEqual(payload["code"], 4003)

    def test_parse_api(self):
        client = APIClient()
        client.force_authenticate(user=AuthedUser(3, "owner", "user"))

        payload = client.post(
            "/my-dba/v1/test-data/parse",
            {"db_type": POSTGRESQL, "ddl": "CREATE TABLE users (id integer PRIMARY KEY);"},
            format="json",
        ).json()

        self.assertEqual(payload["code"], 2000)
        self.assertEqual(payload["data"]["tables"][0]["name"], "users")

    def test_generate_from_ddl_api(self):
        client = APIClient()
        client.force_authenticate(user=AuthedUser(3, "owner", "user"))

        payload = client.post(
            self.URL,
            {
                "db_type": POSTGRESQL,
                "ddl": "CREATE TABLE users (id integer PRIMARY KEY);",
                "tables": [{"schema": "public", "name": "users", "rows": 1}],
                "seed": 1,
            },
            format="json",
        ).json()

        self.assertEqual(payload["code"], 2000)
        self.assertIn("INSERT INTO", payload["data"]["content"])

    def test_generate_requires_exactly_one_source(self):
        client = APIClient()
        client.force_authenticate(user=AuthedUser(3, "owner", "user"))

        missing = client.post(self.URL, {"tables": self.tables}, format="json").json()
        both = client.post(
            self.URL,
            {
                "datasource_id": self.datasource.id,
                "db_type": POSTGRESQL,
                "ddl": "CREATE TABLE users (id integer PRIMARY KEY);",
                "tables": self.tables,
            },
            format="json",
        ).json()

        self.assertEqual(missing["code"], 4000)
        self.assertEqual(both["code"], 4000)


class _FakeCursor:
    def __init__(self):
        self.statements = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, sql, params=None):
        self.statements.append((sql, params))

    def fetchall(self):
        return []

    def fetchone(self):
        return (0,)


class _FakeConnection:
    def __init__(self):
        self.autocommit = True

    def cursor(self):
        return _FakeCursor()

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        pass


class ManageDatabaseTests(TestCase):
    """
    已创建测试库的后续维护：权限、状态、表归属与基本流程
    """

    def setUp(self):
        self.datasource = _datasource("manage-ds", creator="owner")
        _snapshot(
            self.datasource,
            [
                _table("users", columns=[_column("id")]),
                _table("orders", columns=[_column("id")]),
            ],
        )
        self.db_name = services._generate_db_name(services._config()["db_prefix"], self.datasource.id)
        self.record = models.ProvisionedDatabase.objects.create(
            datasource_id=self.datasource.id,
            db_name=self.db_name,
            status=READY,
            tables=[{"schema": "public", "name": "users", "rows": 3}],
            row_count=3,
            creator="owner",
        )

    def test_non_owner_cannot_modify(self):
        with self.assertRaises(services.ProvisionPermissionError):
            services.append_rows(self.record, "public", "users", 1, actor="other", is_admin=False)

    def test_only_ready_record_can_be_modified(self):
        self.record.status = DROPPED
        self.record.save(update_fields=["status"])

        with self.assertRaisesMessage(services.ProvisionError, "可用"):
            services.append_rows(self.record, "public", "users", 1, actor="owner", is_admin=False)

    def test_append_rejects_table_not_in_record(self):
        with (
            mock.patch.object(services, "_database_exists", return_value=True),
            self.assertRaisesMessage(services.ProvisionError, "不是本功能创建"),
        ):
            services.append_rows(self.record, "public", "orders", 1, actor="owner", is_admin=False)

    def test_append_rows_happy_path(self):
        with (
            mock.patch.object(services, "_database_exists", return_value=True),
            mock.patch.object(services, "_connect_target", return_value=_FakeConnection()),
            mock.patch.object(services, "_table_exists", return_value=True),
            mock.patch.object(services, "_external_parents", return_value={}),
            mock.patch.object(data, "generate_rows", return_value=[{"id": 100}]),
            mock.patch.object(services, "_insert_rows"),
            mock.patch.object(services, "_refresh_record_counts"),
        ):
            record, warnings = services.append_rows(self.record, "public", "users", 1, actor="owner", is_admin=False)

        self.assertEqual(record.id, self.record.id)
        self.assertIsInstance(warnings, list)

    def test_add_table_rejects_existing_record_table(self):
        with (
            mock.patch.object(services, "_database_exists", return_value=True),
            self.assertRaisesMessage(services.ProvisionError, "已经在测试库记录中"),
        ):
            services.add_table(self.record, "public", "users", 1, actor="owner", is_admin=False)

    def test_add_table_happy_path(self):
        with (
            mock.patch.object(services, "_database_exists", return_value=True),
            mock.patch.object(services, "_connect_target", return_value=_FakeConnection()),
            mock.patch.object(services, "_table_exists", return_value=False),
            mock.patch.object(services, "_external_parents", return_value={}),
            mock.patch.object(ddl, "build_single_table", return_value=(["CREATE TABLE t (id int)"], [])),
            mock.patch.object(data, "generate_rows", return_value=[{"id": 1}]),
            mock.patch.object(services, "_insert_rows"),
            mock.patch.object(services, "_refresh_record_counts"),
        ):
            record, warnings = services.add_table(self.record, "public", "orders", 1, actor="owner", is_admin=False)

        self.assertTrue(any(item["name"] == "orders" for item in record.tables))
        self.assertIsInstance(warnings, list)

    def test_clear_rows_happy_path(self):
        with (
            mock.patch.object(services, "_database_exists", return_value=True),
            mock.patch.object(services, "_connect_target", return_value=_FakeConnection()),
            mock.patch.object(services, "_table_exists", return_value=True),
            mock.patch.object(services, "_refresh_record_counts"),
        ):
            services.clear_rows(self.record, "public", "users", actor="owner", is_admin=False)

    def test_drop_table_happy_path(self):
        with (
            mock.patch.object(services, "_database_exists", return_value=True),
            mock.patch.object(services, "_connect_target", return_value=_FakeConnection()),
            mock.patch.object(services, "_table_exists", return_value=True),
            mock.patch.object(services, "_refresh_record_counts"),
        ):
            services.drop_table(self.record, "public", "users", actor="owner", is_admin=False)

        self.record.refresh_from_db()
        self.assertEqual(self.record.tables, [])


class ProvisioningApiTests(TestCase):
    URL = "/my-dba/v1/test-db"

    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(user=AuthedUser(1, "admin", "admin"))
        self.datasource = _datasource("api-ds")
        _snapshot(self.datasource, [_table("users", columns=[_column("id")])])
        self.tables = [{"schema": "public", "name": "users", "rows": 3}]
        self.db_name = services._generate_db_name(services._config()["db_prefix"], self.datasource.id)

    def test_user_sees_only_own_records(self):
        own = models.ProvisionedDatabase.objects.create(
            datasource_id=self.datasource.id, db_name=self.db_name, creator="user"
        )
        other = models.ProvisionedDatabase.objects.create(
            datasource_id=self.datasource.id, db_name=f"{self.db_name}_x", creator="other"
        )
        self.client.force_authenticate(user=AuthedUser(2, "user", "user"))

        payload = self.client.get(self.URL).json()

        self.assertEqual(payload["code"], 2000)
        names = [item["db_name"] for item in payload["data"]["results"]]
        self.assertIn(own.db_name, names)
        self.assertNotIn(other.db_name, names)

    def test_user_cannot_preview_datasource_of_others(self):
        self.client.force_authenticate(user=AuthedUser(2, "user", "user"))

        payload = self.client.post(
            f"{self.URL}/preview",
            {"datasource_id": self.datasource.id, "tables": self.tables},
            format="json",
        ).json()

        self.assertEqual(payload["code"], 4005)

    def test_user_can_preview_own_datasource(self):
        datasource = _datasource("user-ds", creator="user")
        _snapshot(datasource, [_table("users", columns=[_column("id")])])
        self.client.force_authenticate(user=AuthedUser(2, "user", "user"))

        payload = self.client.post(
            f"{self.URL}/preview",
            {"datasource_id": datasource.id, "tables": self.tables},
            format="json",
        ).json()

        self.assertEqual(payload["code"], 2000)

    def test_user_cannot_delete_record_of_others(self):
        record = models.ProvisionedDatabase.objects.create(
            datasource_id=self.datasource.id, db_name=self.db_name, creator="owner"
        )
        self.client.force_authenticate(user=AuthedUser(2, "user", "user"))

        payload = self.client.delete(f"{self.URL}/{record.id}").json()

        self.assertEqual(payload["code"], 4005)

    def test_preview_api(self):
        payload = self.client.post(
            f"{self.URL}/preview",
            {"datasource_id": self.datasource.id, "tables": self.tables, "seed": 1},
            format="json",
        ).json()

        self.assertEqual(payload["code"], 2000)
        self.assertTrue(payload["data"]["db_name"].startswith("dba_test_"))
        self.assertEqual(payload["data"]["total_rows"], 3)

    def test_provision_requires_confirm(self):
        payload = self.client.post(
            f"{self.URL}/provision",
            {
                "datasource_id": self.datasource.id,
                "tables": self.tables,
                "db_name": self.db_name,
                "confirm": False,
            },
            format="json",
        ).json()

        self.assertEqual(payload["code"], 4000)
        self.assertFalse(models.ProvisionedDatabase.objects.exists())

    def test_datasource_serializer_hides_write_password(self):
        payload = self.client.get("/my-dba/v1/datasource?page=1&page_size=10").json()

        item = payload["data"]["results"][0]
        self.assertNotIn("write_password", item)
        self.assertEqual(item["write_username"], "writer")
        self.assertEqual(item["env_type"], TEST_ENV)
        self.assertTrue(item["has_write_credential"])

    def _datasource_payload(self, **overrides):
        payload = {
            "name": "api-created",
            "db_type": POSTGRESQL,
            "host": "127.0.0.1",
            "port": 5432,
            "db_name": "d",
            "username": "reader",
            "password": "read",
        }
        payload.update(overrides)
        return payload

    def test_admin_can_set_provisioning_fields_on_create(self):
        payload = self.client.post(
            "/my-dba/v1/datasource",
            self._datasource_payload(env_type=TEST_ENV, write_username="writer", write_password="secret"),
            format="json",
        ).json()

        self.assertEqual(payload["code"], 2000)
        self.assertEqual(payload["data"]["env_type"], TEST_ENV)
        self.assertEqual(payload["data"]["write_username"], "writer")
        self.assertTrue(payload["data"]["has_write_credential"])
        self.assertNotIn("write_password", payload["data"])

    def test_user_can_set_provisioning_fields_on_own_create(self):
        self.client.force_authenticate(user=AuthedUser(2, "user", "user"))

        payload = self.client.post(
            "/my-dba/v1/datasource",
            self._datasource_payload(env_type=TEST_ENV, write_username="writer", write_password="secret"),
            format="json",
        ).json()

        self.assertEqual(payload["code"], 2000)
        self.assertEqual(payload["data"]["env_type"], TEST_ENV)
        self.assertTrue(payload["data"]["has_write_credential"])

    def test_owner_can_set_provisioning_fields_on_own_update(self):
        self.client.force_authenticate(user=AuthedUser(3, "owner", "user"))
        payload = self._datasource_payload(
            name=self.datasource.name, env_type=PROD_ENV, write_username="", write_password=""
        )

        response = self.client.put(f"/my-dba/v1/datasource/{self.datasource.id}", payload, format="json").json()

        self.assertEqual(response["code"], 2000)
        self.assertEqual(response["data"]["env_type"], PROD_ENV)
        self.assertFalse(response["data"]["has_write_credential"])

    def test_admin_update_keeps_password_when_blank(self):
        payload = self._datasource_payload(
            name=self.datasource.name,
            env_type=TEST_ENV,
            write_username="writer",
            write_password="",
        )

        response = self.client.put(f"/my-dba/v1/datasource/{self.datasource.id}", payload, format="json").json()

        self.assertEqual(response["code"], 2000)
        self.assertTrue(response["data"]["has_write_credential"])

    def test_admin_can_clear_write_account(self):
        payload = self._datasource_payload(
            name=self.datasource.name, env_type=TEST_ENV, write_username="", write_password=""
        )

        response = self.client.put(f"/my-dba/v1/datasource/{self.datasource.id}", payload, format="json").json()

        self.assertEqual(response["code"], 2000)
        self.assertFalse(response["data"]["has_write_credential"])

    def test_non_admin_cannot_set_provisioning_fields_on_update(self):
        self.client.force_authenticate(user=AuthedUser(2, "user", "user"))
        payload = self._datasource_payload(name=self.datasource.name, env_type=TEST_ENV)

        response = self.client.put(f"/my-dba/v1/datasource/{self.datasource.id}", payload, format="json").json()

        self.assertEqual(response["code"], 4005)

    def test_delete_record_api(self):
        record = models.ProvisionedDatabase.objects.create(
            datasource_id=self.datasource.id,
            db_name=self.db_name,
            status=READY,
            tables=self.tables,
            row_count=3,
            creator="admin",
        )
        with mock.patch.object(services, "_drop_database"):
            payload = self.client.delete(f"{self.URL}/{record.id}").json()

        self.assertEqual(payload["code"], 2000)
        record.refresh_from_db()
        self.assertEqual(record.status, DROPPED)

    def test_delete_unknown_record(self):
        payload = self.client.delete(f"{self.URL}/999999").json()
        self.assertEqual(payload["code"], 4004)

    def _ready_record(self, creator="admin"):
        return models.ProvisionedDatabase.objects.create(
            datasource_id=self.datasource.id,
            db_name=f"{self.db_name}_{creator}",
            status=READY,
            tables=[{"schema": "public", "name": "users", "rows": 3}],
            row_count=3,
            creator=creator,
        )

    def test_append_rows_api(self):
        record = self._ready_record()
        with mock.patch.object(services, "append_rows", return_value=(record, [])):
            payload = self.client.post(
                f"{self.URL}/{record.id}/rows",
                {"schema": "public", "name": "users", "rows": 1},
                format="json",
            ).json()

        self.assertEqual(payload["code"], 2000)
        self.assertEqual(payload["data"]["id"], record.id)

    def test_clear_rows_api(self):
        record = self._ready_record()
        with mock.patch.object(services, "clear_rows"):
            payload = self.client.delete(f"{self.URL}/{record.id}/rows?schema=public&name=users").json()

        self.assertEqual(payload["code"], 2000)

    def test_add_table_api(self):
        record = self._ready_record()
        with mock.patch.object(services, "add_table", return_value=(record, [])):
            payload = self.client.post(
                f"{self.URL}/{record.id}/tables",
                {"schema": "public", "name": "orders", "rows": 1},
                format="json",
            ).json()

        self.assertEqual(payload["code"], 2000)

    def test_drop_table_api(self):
        record = self._ready_record()
        with mock.patch.object(services, "drop_table"):
            payload = self.client.delete(f"{self.URL}/{record.id}/tables?schema=public&name=users").json()

        self.assertEqual(payload["code"], 2000)

    def test_search_records_by_db_name(self):
        match = models.ProvisionedDatabase.objects.create(
            datasource_id=self.datasource.id, db_name="dba_search_match", creator="admin"
        )
        other = models.ProvisionedDatabase.objects.create(
            datasource_id=self.datasource.id, db_name="dba_other", creator="admin"
        )

        payload = self.client.get(f"{self.URL}?keyword=search_match").json()

        names = [item["db_name"] for item in payload["data"]["results"]]
        self.assertIn(match.db_name, names)
        self.assertNotIn(other.db_name, names)

    def test_search_records_by_datasource_name(self):
        record = models.ProvisionedDatabase.objects.create(
            datasource_id=self.datasource.id, db_name=self.db_name, creator="admin"
        )

        payload = self.client.get(f"{self.URL}?keyword=api-ds").json()

        names = [item["db_name"] for item in payload["data"]["results"]]
        self.assertIn(record.db_name, names)

    def test_user_search_only_returns_own_records(self):
        own = models.ProvisionedDatabase.objects.create(
            datasource_id=self.datasource.id, db_name=f"{self.db_name}_own", creator="user"
        )
        other = models.ProvisionedDatabase.objects.create(
            datasource_id=self.datasource.id, db_name=f"{self.db_name}_other", creator="other"
        )
        self.client.force_authenticate(user=AuthedUser(2, "user", "user"))

        payload = self.client.get(f"{self.URL}?keyword={self.db_name}").json()

        names = [item["db_name"] for item in payload["data"]["results"]]
        self.assertIn(own.db_name, names)
        self.assertNotIn(other.db_name, names)

    def test_manage_endpoints_deny_non_owner(self):
        record = self._ready_record(creator="owner")
        self.client.force_authenticate(user=AuthedUser(2, "user", "user"))

        payload = self.client.post(
            f"{self.URL}/{record.id}/rows",
            {"schema": "public", "name": "users", "rows": 1},
            format="json",
        ).json()

        self.assertEqual(payload["code"], 4005)

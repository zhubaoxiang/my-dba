"""
DDL 评审单元测试

解析、类型归一化与内存合并都是纯计算，`SimpleTestCase` 即可。
编排与接口要读快照，另用 `TestCase`（依赖 `utils/test_runner.py` 灌 DDL）。
"""

from unittest import mock

from django.test import SimpleTestCase
from rest_framework.test import APIClient

from apps.datasource import services as datasource_services
from apps.ddlreview import merge, parser, risks, services, types
from utils import custom_enum


def _column(name, data_type="bigint", length=-1, nullable=True, default="", comment=""):
    return {
        "name": name,
        "data_type": data_type,
        "length": length,
        "nullable": nullable,
        "default": default,
        "comment": comment,
    }


def _table(name, columns, schema="public", primary_key=None, foreign_keys=None, indexes=None, row_count=0):
    return {
        "schema": schema,
        "name": name,
        "comment": "",
        "row_count": row_count,
        "data_size": 0,
        "index_size": 0,
        "total_size": 0,
        "columns": columns,
        "indexes": indexes or [],
        "primary_key": primary_key,
        "foreign_keys": foreign_keys or [],
    }


def _parse(sql):
    result = parser.parse_ddl(sql, custom_enum.DbTypeEnum.POSTGRESQL)
    assert result.ok, result.errors
    return result.statements[0]


class TypeNormalizeTests(SimpleTestCase):
    """
    类型词表必须与采集器的 `information_schema.data_type` 对齐，否则规则会静默漏报
    """

    def normalize(self, sql_type):
        statement = _parse(f"CREATE TABLE t (c {sql_type})")
        column = statement.expression.this.expressions[0]
        return types.normalize_type(column.args["kind"])

    def test_character_varying_keeps_length(self):
        self.assertEqual(self.normalize("varchar(64)"), ("character varying", 64))

    def test_text_is_unbounded(self):
        self.assertEqual(self.normalize("text"), ("text", -1))

    def test_numeric_ignores_precision(self):
        """采集器只存 character_maximum_length；numeric 的精度不影响现有规则"""
        self.assertEqual(self.normalize("numeric(19,4)"), ("numeric", -1))

    def test_serial_family_maps_to_integer_types(self):
        self.assertEqual(self.normalize("bigserial"), ("bigint", -1))
        self.assertEqual(self.normalize("serial"), ("integer", -1))
        self.assertEqual(self.normalize("smallserial"), ("smallint", -1))

    def test_double_precision(self):
        self.assertEqual(self.normalize("double precision"), ("double precision", -1))

    def test_timestamp_with_time_zone(self):
        self.assertEqual(self.normalize("timestamp with time zone"), ("timestamp with time zone", -1))

    def test_unknown_type_is_lowercased(self):
        self.assertEqual(self.normalize("my_enum"), ("my_enum", -1))


class ParserTests(SimpleTestCase):
    def test_create_table_supported(self):
        statement = _parse("CREATE TABLE orders (id bigint PRIMARY KEY, user_id bigint)")
        self.assertEqual(statement.kind, "CREATE_TABLE")
        self.assertTrue(statement.supported)

    def test_alter_table_supported(self):
        statement = _parse("ALTER TABLE orders ADD COLUMN remark text")
        self.assertEqual(statement.kind, "ALTER_TABLE")
        self.assertTrue(statement.supported)

    def test_create_index_supported(self):
        statement = _parse("CREATE UNIQUE INDEX idx_orders_user ON orders (user_id)")
        self.assertEqual(statement.kind, "CREATE_INDEX")
        self.assertTrue(statement.supported)

    def test_drop_table_supported(self):
        statement = _parse("DROP TABLE orders")
        self.assertEqual(statement.kind, "DROP_TABLE")
        self.assertTrue(statement.supported)

    def test_truncate_supported(self):
        statement = _parse("TRUNCATE TABLE orders")
        self.assertEqual(statement.kind, "TRUNCATE")
        self.assertTrue(statement.supported)

    def test_create_table_as_is_not_supported(self):
        statement = _parse("CREATE TABLE orders AS SELECT 1")
        self.assertEqual(statement.kind, "CREATE_TABLE")
        self.assertFalse(statement.supported)
        self.assertIn("AS", statement.reason)

    def test_non_ddl_is_marked_not_reviewed(self):
        statement = _parse("SELECT 1")
        self.assertFalse(statement.supported)
        self.assertIn("非 DDL", statement.reason)

    def test_syntax_error_is_returned(self):
        result = parser.parse_ddl("CREATE TABLE", custom_enum.DbTypeEnum.POSTGRESQL)
        self.assertFalse(result.ok)
        self.assertTrue(result.errors)


class CreateTableMergeTests(SimpleTestCase):
    def test_create_table_builds_snapshot_shape(self):
        tables = []
        statement = _parse(
            """
            CREATE TABLE orders (
                id bigserial PRIMARY KEY,
                user_id bigint NOT NULL REFERENCES app_user(id) ON DELETE CASCADE,
                amount double precision,
                remark varchar(255),
                CONSTRAINT uk_orders_user_remark UNIQUE (user_id, remark)
            )
            """
        )
        result = merge.apply(tables, statement.expression)
        self.assertTrue(result["applied"], result["error"])
        self.assertEqual(len(tables), 1)
        table = tables[0]
        self.assertEqual((table["schema"], table["name"]), ("public", "orders"))
        self.assertEqual([column["name"] for column in table["columns"]], ["id", "user_id", "amount", "remark"])
        self.assertEqual(table["columns"][0]["data_type"], "bigint")
        self.assertFalse(table["columns"][0]["nullable"], "主键隐含 NOT NULL")
        self.assertEqual(table["columns"][1]["data_type"], "bigint")
        self.assertEqual(table["primary_key"], {"name": "pk_orders", "columns": ["id"]})
        self.assertEqual(table["foreign_keys"][0]["ref_table"], "app_user")
        self.assertEqual(table["foreign_keys"][0]["on_delete"], "CASCADE")
        self.assertIn("id", [column for index in table["indexes"] for column in index["columns"]])
        unique = [index for index in table["indexes"] if index.get("unique") and not index.get("primary")]
        self.assertEqual(unique[0]["columns"], ["user_id", "remark"])

    def test_duplicate_table_is_an_apply_error(self):
        tables = [_table("orders", [_column("id")])]
        statement = _parse("CREATE TABLE orders (id bigint)")
        result = merge.apply(tables, statement.expression)
        self.assertFalse(result["applied"])
        self.assertIn("已存在", result["error"])

    def test_if_not_exists_duplicate_is_a_noop(self):
        tables = [_table("orders", [_column("id")])]
        statement = _parse("CREATE TABLE IF NOT EXISTS orders (id bigint)")
        result = merge.apply(tables, statement.expression)
        self.assertTrue(result["applied"])
        self.assertTrue(result.get("noop"))


class AlterTableMergeTests(SimpleTestCase):
    def setUp(self):
        self.tables = [
            _table(
                "orders",
                [_column("id"), _column("user_id"), _column("remark")],
                primary_key={"name": "pk_orders", "columns": ["id"]},
                indexes=[{"name": "pk_orders", "columns": ["id"], "unique": True, "primary": True}],
            )
        ]

    def apply(self, sql):
        statement = _parse(sql)
        return merge.apply(self.tables, statement.expression)

    def test_add_column(self):
        result = self.apply("ALTER TABLE orders ADD COLUMN note text NOT NULL DEFAULT ''")
        self.assertTrue(result["applied"], result["error"])
        column = next(item for item in self.tables[0]["columns"] if item["name"] == "note")
        self.assertFalse(column["nullable"])
        self.assertEqual(column["default"], "''")

    def test_add_duplicate_column_is_an_apply_error(self):
        result = self.apply("ALTER TABLE orders ADD COLUMN remark text")
        self.assertFalse(result["applied"])
        self.assertIn("已存在", result["error"])

    def test_add_column_if_not_exists_is_a_noop(self):
        result = self.apply("ALTER TABLE orders ADD COLUMN IF NOT EXISTS remark text")
        self.assertTrue(result["applied"])

    def test_drop_column_removes_dependent_index(self):
        self.tables[0]["indexes"].append({"name": "idx_orders_user", "columns": ["user_id"], "unique": False})
        result = self.apply("ALTER TABLE orders DROP COLUMN user_id")
        self.assertTrue(result["applied"], result["error"])
        self.assertNotIn("user_id", [column["name"] for column in self.tables[0]["columns"]])
        self.assertNotIn("idx_orders_user", [index["name"] for index in self.tables[0]["indexes"]])

    def test_alter_column_type(self):
        result = self.apply("ALTER TABLE orders ALTER COLUMN remark TYPE varchar(128)")
        self.assertTrue(result["applied"], result["error"])
        column = next(item for item in self.tables[0]["columns"] if item["name"] == "remark")
        self.assertEqual((column["data_type"], column["length"]), ("character varying", 128))

    def test_set_not_null(self):
        result = self.apply("ALTER TABLE orders ALTER COLUMN remark SET NOT NULL")
        self.assertTrue(result["applied"], result["error"])
        column = next(item for item in self.tables[0]["columns"] if item["name"] == "remark")
        self.assertFalse(column["nullable"])

    def test_add_foreign_key(self):
        result = self.apply(
            "ALTER TABLE orders ADD CONSTRAINT fk_orders_user FOREIGN KEY (user_id) "
            "REFERENCES app_user(id) ON DELETE CASCADE"
        )
        self.assertTrue(result["applied"], result["error"])
        self.assertEqual(self.tables[0]["foreign_keys"][0]["name"], "fk_orders_user")
        self.assertEqual(self.tables[0]["foreign_keys"][0]["on_delete"], "CASCADE")

    def test_add_primary_key(self):
        self.tables[0]["primary_key"] = None
        result = self.apply("ALTER TABLE orders ADD PRIMARY KEY (id)")
        self.assertTrue(result["applied"], result["error"])
        self.assertEqual(self.tables[0]["primary_key"]["columns"], ["id"])

    def test_rename_column_updates_indexes(self):
        self.tables[0]["indexes"].append({"name": "idx_orders_remark", "columns": ["remark"], "unique": False})
        result = self.apply("ALTER TABLE orders RENAME COLUMN remark TO note")
        self.assertTrue(result["applied"], result["error"])
        self.assertNotIn("remark", [column["name"] for column in self.tables[0]["columns"]])
        index = next(item for item in self.tables[0]["indexes"] if item["name"] == "idx_orders_remark")
        self.assertEqual(index["columns"], ["note"])

    def test_rename_column_updates_self_referencing_foreign_key(self):
        self.tables[0]["foreign_keys"].append(
            {
                "name": "fk_parent",
                "columns": ["id"],
                "ref_schema": "public",
                "ref_table": "orders",
                "ref_columns": ["id"],
                "on_delete": "NO ACTION",
            }
        )
        result = self.apply("ALTER TABLE orders RENAME COLUMN id TO node_id")
        self.assertTrue(result["applied"], result["error"])
        foreign_key = self.tables[0]["foreign_keys"][0]
        self.assertEqual(foreign_key["columns"], ["node_id"])
        self.assertEqual(foreign_key["ref_columns"], ["node_id"])

    def test_rename_table_updates_self_referencing_foreign_key(self):
        self.tables[0]["foreign_keys"].append(
            {
                "name": "fk_parent",
                "columns": ["id"],
                "ref_schema": "public",
                "ref_table": "orders",
                "ref_columns": ["id"],
                "on_delete": "NO ACTION",
            }
        )
        result = self.apply("ALTER TABLE orders RENAME TO orders_archive")
        self.assertTrue(result["applied"], result["error"])
        self.assertEqual(self.tables[0]["foreign_keys"][0]["ref_table"], "orders_archive")

    def test_rename_table_updates_other_tables_foreign_keys(self):
        other = _table(
            "order_items",
            [_column("order_id")],
            foreign_keys=[
                {
                    "name": "fk_items_order",
                    "columns": ["order_id"],
                    "ref_schema": "public",
                    "ref_table": "orders",
                    "ref_columns": ["id"],
                    "on_delete": "CASCADE",
                }
            ],
        )
        self.tables.append(other)
        result = self.apply("ALTER TABLE orders RENAME TO orders_archive")
        self.assertTrue(result["applied"], result["error"])
        self.assertEqual(self.tables[0]["name"], "orders_archive")
        self.assertEqual(other["foreign_keys"][0]["ref_table"], "orders_archive")

    def test_drop_constraint(self):
        self.tables[0]["foreign_keys"].append(
            {
                "name": "fk_orders_user",
                "columns": ["user_id"],
                "ref_schema": "public",
                "ref_table": "app_user",
                "ref_columns": ["id"],
                "on_delete": "",
            }
        )
        result = self.apply("ALTER TABLE orders DROP CONSTRAINT fk_orders_user")
        self.assertTrue(result["applied"], result["error"])
        self.assertEqual(self.tables[0]["foreign_keys"], [])

    def test_missing_table_is_an_apply_error(self):
        result = self.apply("ALTER TABLE missing ADD COLUMN note text")
        self.assertFalse(result["applied"])
        self.assertIn("不在", result["error"])

    def test_ambiguous_table_name_without_schema_is_an_error(self):
        tables = [
            _table("orders", [_column("id")], schema="a"),
            _table("orders", [_column("id")], schema="b"),
        ]
        result = merge.apply(tables, _parse("ALTER TABLE orders ADD COLUMN note text").expression)
        self.assertFalse(result["applied"])
        self.assertIn("多个模式", result["error"])


class IndexMergeTests(SimpleTestCase):
    def setUp(self):
        self.tables = [_table("orders", [_column("id"), _column("user_id")])]

    def test_create_index(self):
        statement = _parse("CREATE INDEX idx_orders_user ON orders (user_id)")
        result = merge.apply(self.tables, statement.expression)
        self.assertTrue(result["applied"], result["error"])
        self.assertEqual(self.tables[0]["indexes"][0]["name"], "idx_orders_user")
        self.assertEqual(self.tables[0]["indexes"][0]["columns"], ["user_id"])

    def test_create_unique_index(self):
        statement = _parse("CREATE UNIQUE INDEX idx_orders_user ON orders (user_id)")
        merge.apply(self.tables, statement.expression)
        self.assertTrue(self.tables[0]["indexes"][0]["unique"])

    def test_drop_index(self):
        self.tables[0]["indexes"].append({"name": "idx_orders_user", "columns": ["user_id"], "unique": False})
        statement = _parse("DROP INDEX idx_orders_user")
        result = merge.apply(self.tables, statement.expression)
        self.assertTrue(result["applied"], result["error"])
        self.assertEqual(self.tables[0]["indexes"], [])

    def test_drop_missing_index_is_an_apply_error(self):
        statement = _parse("DROP INDEX idx_missing")
        result = merge.apply(self.tables, statement.expression)
        self.assertFalse(result["applied"])

    def test_drop_index_if_exists_is_a_noop(self):
        statement = _parse("DROP INDEX IF EXISTS idx_missing")
        result = merge.apply(self.tables, statement.expression)
        self.assertTrue(result["applied"])
        self.assertTrue(result.get("noop"))

    def test_create_index_if_not_exists_is_a_noop(self):
        self.tables[0]["indexes"].append({"name": "idx_orders_user", "columns": ["user_id"], "unique": False})
        statement = _parse("CREATE INDEX IF NOT EXISTS idx_orders_user ON orders (user_id)")
        result = merge.apply(self.tables, statement.expression)
        self.assertTrue(result["applied"])
        self.assertTrue(result.get("noop"))


class DropTableMergeTests(SimpleTestCase):
    def test_drop_table(self):
        tables = [_table("orders", [_column("id")]), _table("app_user", [_column("id")])]
        statement = _parse("DROP TABLE orders")
        result = merge.apply(tables, statement.expression)
        self.assertTrue(result["applied"], result["error"])
        self.assertEqual([table["name"] for table in tables], ["app_user"])

    def test_drop_missing_table_is_an_apply_error(self):
        result = merge.apply([], _parse("DROP TABLE orders").expression)
        self.assertFalse(result["applied"])

    def test_drop_table_if_exists_is_a_noop(self):
        result = merge.apply([], _parse("DROP TABLE IF EXISTS orders").expression)
        self.assertTrue(result["applied"])
        self.assertTrue(result.get("noop"))


class RiskTests(SimpleTestCase):
    """
    语句风险：执行阻断要挡住，操作风险只提示
    """

    CONTEXT = {"database_version": "PostgreSQL 16.2", "thresholds": {"big_table_rows": 100, "big_table_size_mb": 1}}

    def evaluate(self, sql, tables, context=None):
        statement = _parse(sql)
        return risks.evaluate(statement, tables, context or self.CONTEXT)

    def codes(self, items):
        return {item["code"] for item in items}

    def blocking(self, items):
        return {item["code"] for item in items if item["blocking"]}

    def test_create_existing_table_is_blocking(self):
        tables = [_table("orders", [_column("id")])]
        result = self.evaluate("CREATE TABLE orders (id bigint)", tables)
        self.assertIn("target_table_exists", self.blocking(result))

    def test_if_not_exists_is_not_blocking(self):
        tables = [_table("orders", [_column("id")])]
        result = self.evaluate("CREATE TABLE IF NOT EXISTS orders (id bigint)", tables)
        self.assertEqual(self.blocking(result), set())

    def test_create_index_if_not_exists_is_not_blocking(self):
        tables = [
            _table(
                "orders",
                [_column("id"), _column("user_id")],
                indexes=[{"name": "idx_orders_user", "columns": ["user_id"], "unique": False}],
            )
        ]
        result = self.evaluate("CREATE INDEX IF NOT EXISTS idx_orders_user ON orders (user_id)", tables)
        self.assertNotIn("target_index_exists", self.blocking(result))

    def test_add_column_if_not_exists_is_not_blocking(self):
        tables = [_table("orders", [_column("id")])]
        result = self.evaluate("ALTER TABLE orders ADD COLUMN IF NOT EXISTS id bigint", tables)
        self.assertNotIn("target_column_exists", self.blocking(result))

    def test_alter_missing_table_is_blocking(self):
        result = self.evaluate("ALTER TABLE missing ADD COLUMN c int", [])
        self.assertIn("target_table_missing", self.blocking(result))

    def test_add_duplicate_column_is_blocking(self):
        tables = [_table("orders", [_column("id")])]
        result = self.evaluate("ALTER TABLE orders ADD COLUMN id bigint", tables)
        self.assertIn("target_column_exists", self.blocking(result))

    def test_drop_missing_column_is_blocking(self):
        tables = [_table("orders", [_column("id")])]
        result = self.evaluate("ALTER TABLE orders DROP COLUMN missing", tables)
        self.assertIn("target_column_missing", self.blocking(result))

    def test_foreign_key_to_missing_table_is_blocking(self):
        tables = [_table("orders", [_column("id"), _column("user_id")])]
        result = self.evaluate(
            "ALTER TABLE orders ADD CONSTRAINT fk_user FOREIGN KEY (user_id) REFERENCES app_user(id)", tables
        )
        self.assertIn("referenced_table_missing", self.blocking(result))

    def test_foreign_key_to_missing_column_is_blocking(self):
        tables = [
            _table("orders", [_column("id"), _column("user_id")]),
            _table("app_user", [_column("id")]),
        ]
        result = self.evaluate(
            "ALTER TABLE orders ADD CONSTRAINT fk_user FOREIGN KEY (user_id) REFERENCES app_user(missing)", tables
        )
        self.assertIn("referenced_column_missing", self.blocking(result))

    def test_drop_table_with_dependency_is_blocking(self):
        tables = [
            _table("orders", [_column("id")]),
            _table(
                "order_items",
                [_column("order_id")],
                foreign_keys=[
                    {
                        "name": "fk_items_order",
                        "columns": ["order_id"],
                        "ref_schema": "public",
                        "ref_table": "orders",
                        "ref_columns": ["id"],
                        "on_delete": "NO ACTION",
                    }
                ],
            ),
        ]
        result = self.evaluate("DROP TABLE orders", tables)
        self.assertIn("drop_table_dependency", self.blocking(result))

    def test_drop_table_cascade_is_not_blocking(self):
        tables = [
            _table("orders", [_column("id")]),
            _table(
                "order_items",
                [_column("order_id")],
                foreign_keys=[
                    {
                        "name": "fk_items_order",
                        "columns": ["order_id"],
                        "ref_schema": "public",
                        "ref_table": "orders",
                        "ref_columns": ["id"],
                        "on_delete": "NO ACTION",
                    }
                ],
            ),
        ]
        result = self.evaluate("DROP TABLE orders CASCADE", tables)
        self.assertNotIn("drop_table_dependency", self.blocking(result))

    def test_drop_self_referenced_column_is_blocking(self):
        tables = [
            _table(
                "tree",
                [_column("id"), _column("parent_id")],
                foreign_keys=[
                    {
                        "name": "fk_parent",
                        "columns": ["parent_id"],
                        "ref_schema": "public",
                        "ref_table": "tree",
                        "ref_columns": ["id"],
                        "on_delete": "NO ACTION",
                    }
                ],
            )
        ]
        result = self.evaluate("ALTER TABLE tree DROP COLUMN id", tables)
        self.assertIn("drop_column_dependency", self.blocking(result))

    def test_drop_column_with_index_dependency_is_blocking(self):
        tables = [
            _table(
                "orders",
                [_column("id"), _column("user_id")],
                indexes=[{"name": "idx_orders_user", "columns": ["user_id"], "unique": False}],
            )
        ]
        result = self.evaluate("ALTER TABLE orders DROP COLUMN user_id", tables)
        self.assertIn("drop_column_dependency", self.blocking(result))

    def test_truncate_with_dependency_is_blocking(self):
        tables = [
            _table("orders", [_column("id")]),
            _table(
                "order_items",
                [_column("order_id")],
                foreign_keys=[
                    {
                        "name": "fk_items_order",
                        "columns": ["order_id"],
                        "ref_schema": "public",
                        "ref_table": "orders",
                        "ref_columns": ["id"],
                        "on_delete": "NO ACTION",
                    }
                ],
            ),
        ]
        result = self.evaluate("TRUNCATE TABLE orders", tables)
        self.assertIn("truncate_dependency", self.blocking(result))

    def test_drop_primary_index_is_blocking(self):
        tables = [
            _table(
                "orders",
                [_column("id", nullable=False)],
                primary_key={"name": "pk_orders", "columns": ["id"]},
                indexes=[{"name": "pk_orders", "columns": ["id"], "unique": True, "primary": True}],
            )
        ]
        result = self.evaluate("DROP INDEX pk_orders", tables)
        self.assertIn("drop_index_constraint_backed", self.blocking(result))

    def test_not_null_without_default_on_non_empty_table_is_blocking(self):
        tables = [_table("orders", [_column("id")], row_count=10)]
        result = self.evaluate("ALTER TABLE orders ADD COLUMN note text NOT NULL", tables)
        self.assertIn("not_null_without_default", self.blocking(result))

    def test_not_null_with_default_is_not_blocking(self):
        tables = [_table("orders", [_column("id")], row_count=10)]
        result = self.evaluate("ALTER TABLE orders ADD COLUMN note text NOT NULL DEFAULT ''", tables)
        self.assertNotIn("not_null_without_default", self.blocking(result))

    def test_type_narrowing_is_reported_but_not_blocking(self):
        tables = [_table("orders", [_column("remark", "character varying", 255)])]
        result = self.evaluate("ALTER TABLE orders ALTER COLUMN remark TYPE varchar(16)", tables)
        self.assertIn("type_narrowing", self.codes(result))
        self.assertNotIn("type_narrowing", self.blocking(result))

    def test_create_index_on_large_table_is_reported(self):
        tables = [_table("orders", [_column("id"), _column("user_id")], row_count=100)]
        result = self.evaluate("CREATE INDEX idx_orders_user ON orders (user_id)", tables)
        self.assertIn("create_index_lock", self.codes(result))
        self.assertNotIn("create_index_lock", self.blocking(result))

    def test_create_index_concurrently_is_clean(self):
        tables = [_table("orders", [_column("id"), _column("user_id")], row_count=100)]
        result = self.evaluate("CREATE INDEX CONCURRENTLY idx_orders_user ON orders (user_id)", tables)
        self.assertNotIn("create_index_lock", self.codes(result))

    def test_add_foreign_key_on_large_table_without_not_valid_is_reported(self):
        tables = [
            _table("orders", [_column("id"), _column("user_id")], row_count=100),
            _table("app_user", [_column("id")]),
        ]
        result = self.evaluate(
            "ALTER TABLE orders ADD CONSTRAINT fk_user FOREIGN KEY (user_id) REFERENCES app_user(id)", tables
        )
        self.assertIn("add_fk_scan", self.codes(result))
        self.assertNotIn("add_fk_scan", self.blocking(result))

    def test_add_foreign_key_with_not_valid_is_clean(self):
        tables = [
            _table("orders", [_column("id"), _column("user_id")], row_count=100),
            _table("app_user", [_column("id")]),
        ]
        result = self.evaluate(
            "ALTER TABLE orders ADD CONSTRAINT fk_user FOREIGN KEY (user_id) REFERENCES app_user(id) NOT VALID",
            tables,
        )
        self.assertNotIn("add_fk_scan", self.codes(result))

    def test_rename_is_advisory(self):
        tables = [_table("orders", [_column("id")])]
        result = self.evaluate("ALTER TABLE orders RENAME TO orders_archive", tables)
        self.assertIn("rename_breaks_code", self.codes(result))
        self.assertNotIn("rename_breaks_code", self.blocking(result))

    def test_duplicate_column_in_create_is_blocking(self):
        result = self.evaluate("CREATE TABLE orders (id bigint, id bigint)", [])
        self.assertIn("target_column_exists", self.blocking(result))

    def test_add_primary_key_column_when_pk_exists_is_blocking(self):
        tables = [
            _table(
                "orders",
                [_column("id")],
                primary_key={"name": "pk_orders", "columns": ["id"]},
            )
        ]
        result = self.evaluate("ALTER TABLE orders ADD COLUMN code bigint PRIMARY KEY", tables)
        self.assertIn("target_constraint_exists", self.blocking(result))

    def test_expression_index_does_not_require_a_column_named_like_the_expression(self):
        tables = [_table("orders", [_column("id"), _column("name", "character varying", 32)])]
        result = self.evaluate("CREATE INDEX idx_orders_name ON orders (lower(name))", tables)
        self.assertNotIn("target_column_missing", self.blocking(result))

    def test_clean_create_has_no_risks(self):
        result = self.evaluate("CREATE TABLE orders (id bigint PRIMARY KEY)", [])
        self.assertEqual(result, [])


class ReviewServiceTests(SimpleTestCase):
    """
    编排：方言、数据源条件必填、顺序合并、问题增量、结论
    """

    def review(self, sql, tables=None, datasource_id=None, rule_overrides=None):
        if datasource_id is None:
            return services.review(sql, rule_overrides=rule_overrides or {})
        snapshot = {
            "database_version": "PostgreSQL 16.2",
            "tables": tables or [],
            "collect_time": "2026-10-19 10:00:00",
        }
        with (
            mock.patch.object(
                datasource_services,
                "datasource_brief",
                return_value={"id": datasource_id, "name": "ds", "db_type": custom_enum.DbTypeEnum.POSTGRESQL.value},
            ),
            mock.patch.object(datasource_services, "latest_snapshot_data", return_value=snapshot),
        ):
            return services.review(sql, datasource_id=datasource_id, rule_overrides=rule_overrides or {})

    def codes(self, result):
        return {item["rule_code"] for statement in result["statements"] for item in statement["design_issues"]}

    def risk_codes(self, result):
        return {item["code"] for statement in result["statements"] for item in statement["risks"]}

    def test_create_table_without_datasource(self):
        result = self.review("CREATE TABLE orders (id bigint, amount double precision)")
        self.assertTrue(result["statements"][0]["applied"])
        self.assertIn("no_primary_key", self.codes(result))
        self.assertFalse(result["snapshot"]["performed"])
        self.assertIn("级联删除链", "".join(item["reason"] for item in result["not_reviewed"]))

    def test_create_table_with_primary_key_is_clean(self):
        result = self.review("CREATE TABLE orders (id bigint PRIMARY KEY)")
        self.assertEqual(self.codes(result), set())
        self.assertEqual(result["verdict"]["label"], "未发现新增问题")

    def test_alter_without_datasource_is_rejected(self):
        with self.assertRaises(services.DdlReviewError) as raised:
            services.review("ALTER TABLE orders ADD COLUMN note text", rule_overrides={})
        self.assertEqual(raised.exception.code, 4017)

    def test_multi_statement_is_applied_in_order(self):
        result = self.review(
            "CREATE TABLE orders (id bigint PRIMARY KEY); ALTER TABLE orders ADD COLUMN user_id bigint",
            datasource_id=1,
        )
        self.assertEqual([item["applied"] for item in result["statements"]], [True, True])
        self.assertEqual(result["summary"]["applied"], 2)

    def test_existing_issue_is_not_repeated(self):
        tables = [_table("orders", [_column("id")])]
        result = self.review("ALTER TABLE orders ADD COLUMN note text", tables=tables, datasource_id=1)
        statement = result["statements"][0]
        self.assertNotIn("no_primary_key", self.codes(result))
        self.assertEqual(statement["existing_issue_total"], 1)

    def test_disabled_rule_is_not_run(self):
        result = self.review(
            "CREATE TABLE orders (id bigint)",
            rule_overrides={"no_primary_key": {"enabled": False}},
        )
        self.assertNotIn("no_primary_key", self.codes(result))
        self.assertIn("no_primary_key", result["summary"]["disabled_rules"])

    def test_alter_add_foreign_key_introduces_missing_index_issue(self):
        tables = [
            _table("orders", [_column("id", nullable=False)], primary_key={"name": "pk_orders", "columns": ["id"]})
        ]
        tables[0]["indexes"] = [{"name": "pk_orders", "columns": ["id"], "unique": True, "primary": True}]
        result = self.review(
            "ALTER TABLE orders ADD COLUMN user_id bigint REFERENCES app_user(id)",
            tables=tables
            + [
                _table(
                    "app_user", [_column("id", nullable=False)], primary_key={"name": "pk_app_user", "columns": ["id"]}
                )
            ],
            datasource_id=1,
        )
        self.assertIn("fk_without_index", self.codes(result))

    def test_blocking_risk_stops_subsequent_statements(self):
        tables = [_table("orders", [_column("id")])]
        result = self.review(
            "ALTER TABLE missing ADD COLUMN note text; CREATE TABLE other (id bigint PRIMARY KEY)",
            tables=tables,
            datasource_id=1,
        )
        self.assertFalse(result["statements"][0]["applied"])
        self.assertIn("target_table_missing", self.risk_codes(result))
        self.assertIn("后续未评审", "".join(item["reason"] for item in result["not_reviewed"]))
        self.assertEqual(result["verdict"]["label"], "无法评审")

    def test_blocking_after_applied_statement_is_execution_block(self):
        tables = [_table("orders", [_column("id")])]
        result = self.review(
            "CREATE TABLE other (id bigint PRIMARY KEY); ALTER TABLE missing ADD COLUMN note text",
            tables=tables,
            datasource_id=1,
        )
        self.assertTrue(result["statements"][0]["applied"])
        self.assertEqual(result["verdict"]["label"], "存在执行阻断")

    def test_unsupported_statement_is_not_reviewed_and_stops(self):
        result = self.review("CREATE TABLE orders AS SELECT 1; CREATE TABLE other (id bigint PRIMARY KEY)")
        self.assertFalse(result["statements"][0]["applied"])
        self.assertIn("CREATE TABLE AS", result["statements"][0]["apply_error"])
        self.assertIn("后续未评审", "".join(item["reason"] for item in result["not_reviewed"]))

    def test_mysql_is_rejected(self):
        with (
            mock.patch.object(
                datasource_services,
                "datasource_brief",
                return_value={"id": 1, "name": "ds", "db_type": custom_enum.DbTypeEnum.MYSQL.value},
            ),
            mock.patch.object(datasource_services, "latest_snapshot_data", return_value={"tables": []}),
            self.assertRaises(services.DdlReviewError) as raised,
        ):
            services.review("CREATE TABLE orders (id bigint PRIMARY KEY)", datasource_id=1, rule_overrides={})
        self.assertEqual(raised.exception.code, 4017)

    def test_parse_error_is_unreviewable(self):
        result = self.review("CREATE TABLE")
        self.assertEqual(result["verdict"]["label"], "无法评审")
        self.assertEqual(result["statements"], [])
        self.assertTrue(result["syntax"]["errors"])

    def test_rename_table_does_not_repeat_existing_issue(self):
        tables = [_table("orders", [_column("id")])]
        result = self.review("ALTER TABLE orders RENAME TO orders_archive", tables=tables, datasource_id=1)
        self.assertNotIn("no_primary_key", self.codes(result))

    def test_rename_column_does_not_repeat_existing_issue(self):
        tables = [_table("orders", [_column("created_at", "character varying", 32)])]
        result = self.review(
            "ALTER TABLE orders RENAME COLUMN created_at TO created_on",
            tables=tables,
            datasource_id=1,
        )
        self.assertNotIn("suspicious_column_type", self.codes(result))

    def test_wide_table_worsening_is_marked(self):
        columns = [_column(f"c{i}") for i in range(51)]
        tables = [_table("orders", columns, primary_key={"name": "pk_orders", "columns": ["c0"]})]
        result = self.review(
            "ALTER TABLE orders ADD COLUMN c51 bigint",
            tables=tables,
            datasource_id=1,
            rule_overrides={"wide_table": {"thresholds": {"wide_table_max_columns": 50}}},
        )
        wide = [
            item
            for statement in result["statements"]
            for item in statement["design_issues"]
            if item["rule_code"] == "wide_table"
        ]
        self.assertEqual(len(wide), 1)
        self.assertEqual(wide[0]["change"], "worsened")


class DdlReviewApiTests(SimpleTestCase):
    """
    接口契约：统一响应格式、错误码与解析失败的降级
    """

    URL = "/my-dba/v1/ddl-review/review"

    def setUp(self):
        self.client = APIClient()

    def post(self, payload):
        # 接口测试不依赖 `analysis_rule` 表：规则清单与增量在这里打桩，编排另有专门测试
        with (
            mock.patch.object(services, "_effective_rules", return_value=([], {})),
            mock.patch.object(datasource_services, "analyze_snapshot_data", return_value=[]),
        ):
            return self.client.post(self.URL, payload, format="json").json()

    def test_create_table_without_datasource(self):
        payload = self.post({"sql": "CREATE TABLE orders (id bigint PRIMARY KEY)"})
        self.assertEqual(payload["code"], 2000)
        self.assertTrue(payload["data"]["statements"][0]["applied"])

    def test_empty_sql_is_bad_request(self):
        payload = self.post({"sql": "  "})
        self.assertEqual(payload["code"], 4000)

    def test_alter_without_datasource_is_expectation_failed(self):
        payload = self.post({"sql": "ALTER TABLE orders ADD COLUMN note text"})
        self.assertEqual(payload["code"], 4017)
        self.assertIn("数据源", payload["message"])

    def test_mysql_datasource_is_expectation_failed(self):
        with mock.patch.object(
            datasource_services,
            "datasource_brief",
            return_value={"id": 1, "name": "ds", "db_type": custom_enum.DbTypeEnum.MYSQL.value},
        ):
            payload = self.post({"sql": "CREATE TABLE orders (id bigint)", "datasource_id": 1})
        self.assertEqual(payload["code"], 4017)

    def test_missing_datasource_is_not_found(self):
        with mock.patch.object(datasource_services, "datasource_brief", return_value=None):
            payload = self.post({"sql": "CREATE TABLE orders (id bigint)", "datasource_id": 99})
        self.assertEqual(payload["code"], 4004)

    def test_parse_error_still_returns_2000_with_unreviewable_verdict(self):
        payload = self.post({"sql": "CREATE TABLE"})
        self.assertEqual(payload["code"], 2000)
        self.assertEqual(payload["data"]["verdict"]["label"], "无法评审")
        self.assertTrue(payload["data"]["syntax"]["errors"])

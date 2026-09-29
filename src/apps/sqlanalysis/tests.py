"""
SQL 分析模块单元测试

解析、语句分类与美化都是纯计算，`SimpleTestCase` 即可，不需要数据库。
「哪些语句允许真跑」是本模块最要紧的一条判定，单独一组重点覆盖。
"""

import json
from unittest import mock

from django.test import SimpleTestCase, TestCase
from rest_framework.test import APIClient

from apps.datasource import models
from apps.sqlanalysis import analyzer, explain, formatting, interpret, parse, schema, views
from apps.sqlanalysis.rules import registry
from apps.sqlanalysis.schema import SchemaIndex, SchemaUnavailable, families_conflict, type_family
from utils import custom_enum

READ_ONLY = custom_enum.StatementKindEnum.READ_ONLY
DML = custom_enum.StatementKindEnum.DML
DDL = custom_enum.StatementKindEnum.DDL
OTHER = custom_enum.StatementKindEnum.OTHER


class StatementClassifyTests(SimpleTestCase):
    """
    语句类别决定「能不能在真实库上跑」，判错一边就是在别人库上执行了不该执行的语句
    """

    def kind(self, sql, dialect=None):
        result = parse.parse_sql(sql, dialect)
        self.assertTrue(result.ok, f"应能解析：{sql} -> {result.errors}")
        return result.statements[0].kind

    def test_select_is_read_only(self):
        self.assertEqual(self.kind("SELECT 1"), READ_ONLY)
        self.assertEqual(self.kind("SELECT * FROM t WHERE id = 1"), READ_ONLY)

    def test_cte_select_is_read_only(self):
        self.assertEqual(self.kind("WITH x AS (SELECT 1) SELECT * FROM x"), READ_ONLY)

    def test_set_operations_are_read_only(self):
        self.assertEqual(self.kind("SELECT 1 UNION SELECT 2"), READ_ONLY)

    def test_describe_is_read_only(self):
        self.assertEqual(self.kind("DESCRIBE t"), READ_ONLY)

    def test_show_is_read_only(self):
        self.assertEqual(self.kind("SHOW TABLES"), READ_ONLY)

    def test_explain_without_analyze_is_read_only(self):
        """EXPLAIN 不带 ANALYZE 时只向优化器要计划，不会执行语句"""
        self.assertEqual(self.kind("EXPLAIN SELECT 1"), READ_ONLY)

    def test_explain_analyze_is_not_read_only(self):
        """
        **EXPLAIN ANALYZE 会真执行语句**（PG 上连 DML 都会写入），
        绝不能因为「它看起来是 EXPLAIN」就放行
        """
        self.assertNotEqual(self.kind("EXPLAIN ANALYZE SELECT 1"), READ_ONLY)

    def test_explain_with_analyze_option_is_not_read_only(self):
        self.assertNotEqual(self.kind("EXPLAIN (ANALYZE, BUFFERS) SELECT 1"), READ_ONLY)

    def test_dml_classified(self):
        self.assertEqual(self.kind("INSERT INTO t VALUES (1)"), DML)
        self.assertEqual(self.kind("UPDATE t SET a = 1"), DML)
        self.assertEqual(self.kind("DELETE FROM t"), DML)

    def test_ddl_classified(self):
        for sql in ("CREATE TABLE t (a int)", "ALTER TABLE t ADD COLUMN b int", "DROP TABLE t", "TRUNCATE t"):
            self.assertEqual(self.kind(sql), DDL, sql)

    def test_unrecognised_command_is_not_read_only(self):
        """
        分类是白名单：sqlglot 的 Command 兜底节点里既有 SHOW / EXPLAIN 也有 VACUUM / SET，
        认不出来的一律按非只读处理——VACUUM 会写统计信息，不是只读操作
        """
        for sql in ("VACUUM", "SET search_path = public", "BEGIN", "GRANT SELECT ON t TO u"):
            self.assertNotEqual(self.kind(sql), READ_ONLY, sql)

    def test_statement_index_is_one_based(self):
        result = parse.parse_sql("SELECT 1; SELECT 2")
        self.assertEqual([item.index for item in result.statements], [1, 2])

    def test_multi_statement_takes_the_strictest(self):
        """
        多语句里混着一条写操作时，绝不能因为前面几条是 SELECT 就把整批跑掉
        """
        result = parse.parse_sql("SELECT 1; INSERT INTO t VALUES (1)")
        self.assertEqual(parse.overall_kind(result.statements), DML)

        result = parse.parse_sql("SELECT 1; SELECT 2")
        self.assertEqual(parse.overall_kind(result.statements), READ_ONLY)

        result = parse.parse_sql("SELECT 1; DROP TABLE t")
        self.assertEqual(parse.overall_kind(result.statements), DDL)

    def test_has_multiple_flag(self):
        self.assertFalse(parse.parse_sql("SELECT 1").has_multiple)
        self.assertTrue(parse.parse_sql("SELECT 1; SELECT 2").has_multiple)


class ParseErrorTests(SimpleTestCase):
    """
    解析失败**不是整体失败**：不能解析恰恰是最需要帮助的时候（design.md D9）
    """

    def test_syntax_error_reports_position(self):
        result = parse.parse_sql("SELECT id,\n  FROM users\nWHERE")
        self.assertFalse(result.ok)
        self.assertEqual(result.statements, ())
        error = result.errors[0]
        self.assertIsNotNone(error["line"], "必须给出出错行，只说「解析失败」等于什么也没说")
        self.assertIsNotNone(error["col"])
        self.assertTrue(error["description"])

    def test_error_keeps_highlight(self):
        result = parse.parse_sql("SELECT 1 FROM")
        self.assertFalse(result.ok)
        self.assertTrue(any(item["description"] for item in result.errors))

    def test_empty_sql_is_rejected(self):
        for sql in ("", "   ", None):
            result = parse.parse_sql(sql)
            self.assertFalse(result.ok)
            self.assertIn("不能为空", result.errors[0]["description"])

    def test_parse_does_not_raise(self):
        """解析失败必须返回结果对象而不是抛异常，否则上层无法降级"""
        self.assertIsInstance(parse.parse_sql("这不是 SQL"), parse.ParseResult)


class DialectTests(SimpleTestCase):
    """
    方言缺省为 PostgreSQL，可显式指定
    """

    def test_defaults_to_postgres(self):
        self.assertEqual(parse.dialect_of(None), custom_enum.DbTypeEnum.POSTGRESQL)
        self.assertEqual(parse.dialect_of(""), custom_enum.DbTypeEnum.POSTGRESQL)
        self.assertEqual(parse.dialect_of(99), custom_enum.DbTypeEnum.POSTGRESQL)

    def test_explicit_dialect(self):
        self.assertEqual(parse.dialect_of(custom_enum.DbTypeEnum.MYSQL), custom_enum.DbTypeEnum.MYSQL)
        self.assertEqual(parse.dialect_of(custom_enum.DbTypeEnum.MYSQL.value), custom_enum.DbTypeEnum.MYSQL)

    def test_mysql_specific_syntax_parses_under_mysql(self):
        result = parse.parse_sql("SELECT `id` FROM `t` LIMIT 10", custom_enum.DbTypeEnum.MYSQL)
        self.assertTrue(result.ok)


class FormatTests(SimpleTestCase):
    """
    美化只改排版，不改语义
    """

    def test_formats_a_select(self):
        formatted = formatting.format_sql("select id,name from users where id=1")
        self.assertIn("SELECT", formatted)
        self.assertIn("\n", formatted, "美化后应当有换行，否则等于没美化")

    def test_format_is_idempotent(self):
        """格式化两次结果一致——否则每次保存都在漂移"""
        once = formatting.format_sql("select id,name from users where id=1")
        twice = formatting.format_sql(once)
        self.assertEqual(once, twice)

    def test_format_preserves_semantics(self):
        """语义不变：重新解析后应与原文解析出的语法树等价"""
        original = "select a from t where b = 1 order by c limit 5"
        formatted = formatting.format_sql(original)
        self.assertEqual(
            parse.parse_sql(original).statements[0].expression.sql(),
            parse.parse_sql(formatted).statements[0].expression.sql(),
        )

    def test_unparseable_sql_is_returned_as_is(self):
        """美化失败不该盖住语法错误的提示"""
        broken = "SELECT FROM WHERE"
        self.assertEqual(formatting.format_sql(broken), broken)

    def test_empty_returns_empty(self):
        self.assertEqual(formatting.format_sql(""), "")
        self.assertEqual(formatting.format_sql(None), "")


class RuleTests(SimpleTestCase):
    """
    每条规则配正例与反例

    只有正例的规则会在真实使用中变成噪音源——报了一堆不该报的，使用者就不再看了。
    """

    def analyze(self, sql, dialect=None, schema=None):
        result = parse.parse_sql(sql, dialect)
        self.assertTrue(result.ok, f"测试用例本身要能解析：{sql} -> {result.errors}")
        return analyzer.SqlAnalyzer(result, sql=sql, dialect=dialect, schema=schema).analyze()

    def codes(self, sql, **kwargs):
        return [item["rule_code"] for item in self.analyze(sql, **kwargs)]

    def levels(self, sql, code, **kwargs):
        return [item["issue_level"] for item in self.analyze(sql, **kwargs) if item["rule_code"] == code]

    # ---- 危险写操作 ----

    def test_delete_without_where(self):
        self.assertIn("delete_without_where", self.codes("DELETE FROM users"))
        self.assertNotIn("delete_without_where", self.codes("DELETE FROM users WHERE id = 1"))

    def test_update_without_where(self):
        self.assertIn("update_without_where", self.codes("UPDATE users SET name = 'x'"))
        self.assertNotIn("update_without_where", self.codes("UPDATE users SET name = 'x' WHERE id = 1"))

    def test_drop_table(self):
        self.assertIn("drop_table", self.codes("DROP TABLE users"))
        self.assertNotIn("drop_table", self.codes("DROP INDEX idx_users"))

    def test_drop_column(self):
        self.assertIn("drop_column", self.codes("ALTER TABLE users DROP COLUMN email"))
        self.assertNotIn("drop_column", self.codes("ALTER TABLE users ADD COLUMN email varchar(64)"))

    def test_truncate_table(self):
        self.assertIn("truncate_table", self.codes("TRUNCATE users"))

    def test_dangerous_rules_are_high_level(self):
        for sql, code in (
            ("DELETE FROM t", "delete_without_where"),
            ("UPDATE t SET a = 1", "update_without_where"),
            ("DROP TABLE t", "drop_table"),
        ):
            self.assertEqual(self.levels(sql, code), [custom_enum.IssueLevelEnum.HIGH.value], f"{sql} 应当报为高危")

    # ---- JOIN 与 GROUP BY ----

    def test_join_without_condition(self):
        self.assertIn("join_without_condition", self.codes("SELECT * FROM a JOIN b"))
        self.assertNotIn("join_without_condition", self.codes("SELECT * FROM a JOIN b ON a.id = b.id"))

    def test_join_with_using_is_accepted(self):
        self.assertNotIn("join_without_condition", self.codes("SELECT * FROM a JOIN b USING (id)"))

    def test_group_by_missing_column(self):
        sql = "SELECT dept, count(*) FROM emp GROUP BY dept"
        self.assertNotIn("group_by_missing_column", self.codes(sql))
        bad = "SELECT dept, name, count(*) FROM emp GROUP BY dept"
        self.assertIn("group_by_missing_column", self.codes(bad))

    def test_group_by_with_aggregate_only_is_accepted(self):
        self.assertNotIn("group_by_missing_column", self.codes("SELECT count(*) FROM emp GROUP BY dept"))

    def test_group_by_positional_reference_is_not_judged(self):
        """GROUP BY 1 这类位置引用没法与列名比对，宁可不报也不误报"""
        self.assertNotIn("group_by_missing_column", self.codes("SELECT dept, count(*) FROM emp GROUP BY 1"))

    # ---- 性能与规范 ----

    def test_function_on_column(self):
        self.assertIn("function_on_column", self.codes("SELECT 1 FROM t WHERE upper(name) = 'X'"))
        self.assertNotIn("function_on_column", self.codes("SELECT 1 FROM t WHERE name = 'X'"))

    def test_cast_on_column_is_reported(self):
        self.assertIn("function_on_column", self.codes("SELECT 1 FROM t WHERE id::text = '1'"))

    def test_function_outside_where_is_not_reported(self):
        """只判 WHERE 里的列运算——SELECT 列表里套函数是正常写法"""
        self.assertNotIn("function_on_column", self.codes("SELECT upper(name) FROM t WHERE id = 1"))

    def test_select_star(self):
        self.assertIn("select_star", self.codes("SELECT * FROM t"))
        self.assertIn("select_star", self.codes("SELECT t.* FROM t"))
        self.assertIn("select_star", self.codes("SELECT id, * FROM t"))
        self.assertNotIn("select_star", self.codes("SELECT id, name FROM t"))

    def test_count_star_is_not_select_star(self):
        """
        `SELECT count(*)` 是正常写法。实测在真实库上，用 find_all(Star) 一扫到底会把它
        报成「用了 SELECT *」——这类误报最伤使用者对问题清单的信任
        """
        self.assertNotIn("select_star", self.codes("SELECT count(*) FROM t"))
        self.assertNotIn("select_star", self.codes("SELECT count(*) AS n FROM t WHERE id = 1"))

    def test_insert_without_column_list(self):
        self.assertIn("insert_without_column_list", self.codes("INSERT INTO t VALUES (1, 2)"))
        self.assertNotIn("insert_without_column_list", self.codes("INSERT INTO t (a, b) VALUES (1, 2)"))

    def test_ddl_locks_table(self):
        self.assertIn("ddl_locks_table", self.codes("ALTER TABLE t ADD COLUMN c int"))
        self.assertIn("ddl_locks_table", self.codes("ALTER TABLE t ALTER COLUMN c TYPE varchar(50)"))
        self.assertNotIn("ddl_locks_table", self.codes("ALTER TABLE t RENAME TO t2"))

    def test_no_limit(self):
        self.assertIn("no_limit", self.codes("SELECT id FROM t"))
        self.assertNotIn("no_limit", self.codes("SELECT id FROM t WHERE id > 10"))
        self.assertNotIn("no_limit", self.codes("SELECT id FROM t LIMIT 10"))
        self.assertNotIn("no_limit", self.codes("SELECT count(*) FROM t"), "聚合查询本就只返回一行")

    def test_leading_wildcard_like(self):
        self.assertIn("leading_wildcard_like", self.codes("SELECT 1 FROM t WHERE name LIKE '%abc'"))
        self.assertNotIn("leading_wildcard_like", self.codes("SELECT 1 FROM t WHERE name LIKE 'abc%'"))

    def test_ilike_is_covered(self):
        self.assertIn("leading_wildcard_like", self.codes("SELECT 1 FROM t WHERE name ILIKE '%abc'"))

    # ---- 分析器本身 ----

    def test_dangerous_write_is_reported_on_the_right_statement(self):
        """多语句时问题要落在出事的那一条上，而不是笼统报给整条 SQL"""
        issues = self.analyze("SELECT 1;\nDELETE FROM t")
        issue = next(item for item in issues if item["rule_code"] == "delete_without_where")
        self.assertEqual(issue["statement_index"], 2)

    def test_result_is_reproducible(self):
        sql = "SELECT * FROM a JOIN b"
        self.assertEqual(self.analyze(sql), self.analyze(sql))

    def test_issues_are_ordered_by_level(self):
        levels = [item["issue_level"] for item in self.analyze("SELECT * FROM a JOIN b")]
        self.assertEqual(levels, sorted(levels))

    def test_clean_sql_produces_nothing(self):
        self.assertEqual(self.analyze("SELECT id, name FROM users WHERE id = 1 LIMIT 1"), [])

    def test_needs_schema_rules_are_skipped_without_schema(self):
        """需快照的规则在未绑定数据源时跳过，且**跳过必须可解释**——不能静默少跑"""
        result = parse.parse_sql("SELECT id FROM users WHERE id = 1 LIMIT 1")
        runner = analyzer.SqlAnalyzer(result, sql="")
        runner.analyze()
        declared = [rule.code for rule in registry.schema_rules()]
        self.assertEqual(sorted(runner.skipped_rules), sorted(declared))
        for code in declared:
            self.assertNotIn(code, runner.evaluated_rules, "跳过的不该同时算作已评估")

    def test_schema_rules_run_when_schema_is_available(self):
        """有表结构时不应有任何规则被跳过"""
        result = parse.parse_sql("SELECT id FROM users WHERE id = 1 LIMIT 1")
        runner = analyzer.SqlAnalyzer(result, sql="", schema=SchemaIndex([]))
        runner.analyze()
        self.assertEqual(runner.skipped_rules, [])

    def test_disabled_rule_is_skipped(self):
        result = parse.parse_sql("SELECT * FROM t")
        runner = analyzer.SqlAnalyzer(result, sql="", rule_overrides={"select_star": {"enabled": False}})
        self.assertNotIn("select_star", [item["rule_code"] for item in runner.analyze()])
        self.assertNotIn("select_star", runner.evaluated_rules)


def _table(name, *columns, schema_name="public"):
    return {
        "schema": schema_name,
        "name": name,
        "comment": "",
        "row_count": 0,
        "columns": [{"name": n, "data_type": t, "nullable": True, "comment": ""} for n, t in columns],
        "primary_key": [],
        "foreign_keys": [],
    }


# 造一份最小快照：users 与 orders 通过 user_id（integer）关联
_TEST_INDEX = SchemaIndex(
    [
        _table("users", ("id", "integer"), ("name", "varchar"), ("created_at", "timestamp")),
        _table("orders", ("id", "integer"), ("user_id", "integer"), ("code", "varchar")),
    ]
)


class SchemaRuleTests(SimpleTestCase):
    """
    需快照的规则：判定表/列是否存在、类型是否可比

    误报会直接损害使用者对问题清单的信任，因此这几条一律「能确定才报」。
    """

    def codes(self, sql, index=_TEST_INDEX):
        result = parse.parse_sql(sql)
        self.assertTrue(result.ok, result.errors)
        runner = analyzer.SqlAnalyzer(result, sql=sql, schema=index)
        return [item["rule_code"] for item in runner.analyze()]

    def test_unknown_table(self):
        self.assertIn("unknown_table", self.codes("SELECT id FROM nope WHERE id = 1"))
        self.assertNotIn("unknown_table", self.codes("SELECT id FROM users WHERE id = 1"))

    def test_cte_name_is_not_treated_as_a_table(self):
        """CTE 是语句内自定义的名字，不是真实表，不能拿去快照里找"""
        sql = "WITH recent AS (SELECT id FROM users) SELECT id FROM recent"
        self.assertNotIn("unknown_table", self.codes(sql))

    def test_subquery_alias_is_not_treated_as_a_table(self):
        sql = "SELECT x.id FROM (SELECT id FROM users) x"
        self.assertNotIn("unknown_table", self.codes(sql))

    def test_unknown_column(self):
        self.assertIn("unknown_column", self.codes("SELECT nope FROM users"))
        self.assertNotIn("unknown_column", self.codes("SELECT name FROM users"))

    def test_unknown_column_with_qualifier(self):
        self.assertIn("unknown_column", self.codes("SELECT u.nope FROM users u"))
        self.assertNotIn("unknown_column", self.codes("SELECT u.name FROM users u"))

    def test_unknown_column_not_judged_when_ambiguous(self):
        """多表且未限定列名时无法确定它属于哪张表，宁可不报"""
        sql = "SELECT name FROM users u JOIN orders o ON u.id = o.user_id"
        self.assertNotIn("unknown_column", self.codes(sql))

    def test_join_key_type_mismatch(self):
        sql = "SELECT 1 FROM users u JOIN orders o ON u.name = o.user_id"
        self.assertIn("join_key_type_mismatch", self.codes(sql))

    def test_join_key_same_type_is_accepted(self):
        sql = "SELECT 1 FROM users u JOIN orders o ON u.id = o.user_id"
        self.assertNotIn("join_key_type_mismatch", self.codes(sql))

    def test_incomparable_types_in_where(self):
        sql = "SELECT 1 FROM users u JOIN orders o ON u.id = o.user_id WHERE u.name = o.user_id"
        self.assertIn("incomparable_types", self.codes(sql))

    def test_implicit_cast_numeric_column_against_string_literal(self):
        self.assertIn("implicit_cast", self.codes("SELECT 1 FROM users WHERE id = '1'"))
        self.assertNotIn("implicit_cast", self.codes("SELECT 1 FROM users WHERE id = 1"))

    def test_implicit_cast_string_column_against_number(self):
        self.assertIn("implicit_cast", self.codes("SELECT 1 FROM users WHERE name = 1"))

    def test_temporal_against_string_is_not_reported(self):
        """时间列比字符串是正常写法，报了就是噪音"""
        self.assertNotIn("implicit_cast", self.codes("SELECT 1 FROM users WHERE created_at > '2024-01-01'"))

    def test_schema_rules_are_skipped_without_index(self):
        codes = self.codes("SELECT id FROM nope WHERE nope = 1", index=None)
        for code in ("unknown_table", "unknown_column", "implicit_cast", "join_key_type_mismatch"):
            self.assertNotIn(code, codes)


class TypeFamilyTests(SimpleTestCase):
    """
    类型归族：只比族不比具体类型，避免把 varchar(64) 与 text 这种同族差异也报出来
    """

    def test_length_and_precision_are_stripped(self):
        self.assertEqual(type_family("varchar(64)"), "string")
        self.assertEqual(type_family("numeric(10,2)"), "numeric")
        self.assertEqual(type_family("  INTEGER  "), "numeric")

    def test_known_families(self):
        self.assertEqual(type_family("text"), "string")
        self.assertEqual(type_family("timestamp with time zone"), "temporal")
        self.assertEqual(type_family("jsonb"), "json")
        self.assertEqual(type_family("bytea"), "binary")

    def test_unknown_types_are_marked_unknown(self):
        self.assertEqual(type_family(""), "unknown")
        self.assertEqual(type_family("some_custom_type"), "unknown")
        self.assertEqual(type_family("integer[]"), "array")

    def test_conflicts_only_for_cross_family_candidates(self):
        self.assertTrue(families_conflict("numeric", "string"))
        self.assertFalse(families_conflict("string", "string"))
        self.assertFalse(families_conflict("string", "temporal"), "时间与字符串比较是常见写法")
        self.assertFalse(families_conflict("unknown", "string"), "认不出来的类型不该报")
        self.assertFalse(families_conflict("numeric", "json"))


class SchemaUnavailableTests(SimpleTestCase):
    """
    三种「拿不到表结构」的情形各自给出可读原因——未做结构校验必须说明为什么
    """

    def test_no_datasource(self):
        with self.assertRaises(SchemaUnavailable) as ctx:
            schema.load_schema_index(None)
        self.assertIn("未指定数据源", str(ctx.exception))

    def test_datasource_not_found(self):
        with (
            mock.patch.object(schema.datasource_services, "datasource_brief", return_value=None),
            self.assertRaises(SchemaUnavailable) as ctx,
        ):
            schema.load_schema_index(7)
        self.assertIn("不存在", str(ctx.exception))

    def test_datasource_not_collected(self):
        with (
            mock.patch.object(schema.datasource_services, "datasource_brief", return_value={"id": 7, "name": "本地库"}),
            mock.patch.object(schema.datasource_services, "list_snapshot_tables", return_value=[]),
            self.assertRaises(SchemaUnavailable) as ctx,
        ):
            schema.load_schema_index(7)
        self.assertIn("尚未采集", str(ctx.exception))

    def test_loads_index_when_available(self):
        with (
            mock.patch.object(schema.datasource_services, "datasource_brief", return_value={"id": 7, "name": "本地库"}),
            mock.patch.object(
                schema.datasource_services,
                "list_snapshot_tables",
                return_value=[_table("users", ("id", "integer"))],
            ),
        ):
            index = schema.load_schema_index(7)
        self.assertEqual(index.column_type("users", "id"), "integer")


class _FakeCursor:
    def __init__(self, rows, fail_on=()):
        self.rows = list(rows or [])
        self.fail_on = tuple(fail_on)
        self.executed = []

    def execute(self, sql, *args):
        self.executed.append(sql)
        # 用 startswith 而不是 in：`EXPLAIN SELECT ...` 里也含 SELECT，
        # 用 in 会让「只让真执行那一步失败」的用例变成 EXPLAIN 就失败
        for marker in self.fail_on:
            if sql.lstrip().startswith(marker):
                raise RuntimeError("目标库拒绝执行")

    def fetchall(self):
        return self.rows

    def fetchmany(self, size):
        return self.rows[:size]

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeConnection:
    """
    替身只读连接：记录所有真正发出去的语句，供断言「写操作有没有被执行」
    """

    def __init__(self, rows=None, fail_on=()):
        self.db_type = custom_enum.DbTypeEnum.POSTGRESQL.value
        self.cursors = []
        self.rolled_back = 0
        self.closed = False
        self._rows = rows or []
        self._fail_on = fail_on

    def _make(self):
        cursor = _FakeCursor(self._rows, self._fail_on)
        self.cursors.append(cursor)
        return cursor

    def cursor(self, *args, **kwargs):
        return self._make()

    def probe_cursor(self, name=None):
        return self._make()

    def rollback(self):
        self.rolled_back += 1

    def close(self):
        self.closed = True

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False

    def issued_sql(self):
        return [sql for cursor in self.cursors for sql in cursor.executed]


class ExplainTests(SimpleTestCase):
    """
    试运行：**只有只读语句会被真正执行**，其余一律只出计划

    这是整个模块唯一会碰到真实库的地方，判错了就是在别人的库上执行了不该执行的语句。
    """

    def run_with(self, sql, rows=None, fail_on=(), **kwargs):
        connection = _FakeConnection(rows, fail_on)
        with mock.patch.object(explain.datasource_services, "open_readonly_connection", return_value=connection):
            return explain.execute(parse.parse_sql(sql), datasource_id=1, **kwargs), connection

    def test_read_only_statement_is_executed(self):
        result, connection = self.run_with("SELECT id FROM users", rows=[(1,), (2,)])
        self.assertTrue(result["executed"])
        self.assertEqual(result["row_count"], 2)
        self.assertTrue(any(sql.startswith("EXPLAIN") for sql in connection.issued_sql()))
        self.assertIn("SELECT id FROM users", connection.issued_sql()[-1])

    def test_delete_is_not_executed(self):
        result, connection = self.run_with("DELETE FROM users")
        self.assertFalse(result["executed"])
        self.assertEqual(result["row_count"], 0)
        self.assertIn("未被执行", result["note"])
        issued = connection.issued_sql()
        self.assertEqual(len(issued), 1, "除了 EXPLAIN 不该再发出任何语句")
        self.assertTrue(issued[0].startswith("EXPLAIN"), "唯一发出的是 EXPLAIN")

    def test_ddl_is_not_executed(self):
        result, connection = self.run_with("DROP TABLE users")
        self.assertFalse(result["executed"])
        self.assertEqual(len(connection.issued_sql()), 1)
        self.assertIn("结构变更", result["note"])

    def test_update_is_not_executed(self):
        result, connection = self.run_with("UPDATE users SET name = 'x'")
        self.assertFalse(result["executed"])
        self.assertEqual(len(connection.issued_sql()), 1)

    def test_explain_analyze_is_not_executed(self):
        """EXPLAIN ANALYZE 会真执行语句，绝不能因为「看着像 EXPLAIN」就放行"""
        result, connection = self.run_with("EXPLAIN ANALYZE SELECT id FROM users")
        self.assertFalse(result["executed"])
        self.assertEqual(len(connection.issued_sql()), 1)

    def test_multi_statement_is_rejected(self):
        """多语句里若混有写操作很容易看漏，直接不支持"""
        with self.assertRaises(explain.ExecutionRejected) as ctx:
            explain.execute(parse.parse_sql("SELECT 1; SELECT 2"), datasource_id=1)
        self.assertIn("一条语句", str(ctx.exception))

    def test_truncated_result_is_reported(self):
        """只报取到的行数而不说被截断，使用者会以为结果就这么多"""
        result, _ = self.run_with("SELECT id FROM users LIMIT 10", rows=[(i,) for i in range(6)], max_row_count=3)
        self.assertTrue(result["truncated"])
        self.assertEqual(result["row_count"], 3)
        self.assertIn("截断", result["note"])

    def test_untruncated_result_has_no_note(self):
        result, _ = self.run_with("SELECT id FROM users", rows=[(1,)], max_row_count=10)
        self.assertFalse(result["truncated"])
        self.assertEqual(result["note"], "")

    def test_plan_failure_on_a_write_statement_is_not_an_error(self):
        """
        DDL 生成不了执行计划是目标库的能力边界（PostgreSQL 的 EXPLAIN 只支持
        SELECT/INSERT/UPDATE/DELETE），不是使用者的 SQL 有问题——不能因此报错
        """
        result, _ = self.run_with("DROP TABLE users", fail_on=("EXPLAIN",))
        self.assertFalse(result["executed"])
        self.assertEqual(result["plan"], "")
        self.assertIn("未被执行", result["note"])
        self.assertIn("不支持为这类语句生成执行计划", result["note"])

    def test_plan_failure_on_a_read_only_statement_is_an_error(self):
        """只读语句拿不到计划才是真失败，要如实说明，不能伪装成空结果"""
        with self.assertRaises(explain.ExecutionRejected) as ctx:
            self.run_with("SHOW TABLES", fail_on=("EXPLAIN",))
        self.assertIn("未能取得执行计划", str(ctx.exception))

    def test_execution_failure_is_reported(self):
        with self.assertRaises(explain.ExecutionRejected) as ctx:
            self.run_with("SELECT id FROM users", fail_on=("SELECT",))
        self.assertIn("执行失败", str(ctx.exception))

    def test_datasource_unavailable_is_reported(self):
        with (
            mock.patch.object(
                explain.datasource_services,
                "open_readonly_connection",
                side_effect=explain.datasource_services.DatasourceUnavailable("数据源 9 不存在或已删除"),
            ),
            self.assertRaises(explain.ExecutionRejected) as ctx,
        ):
            explain.execute(parse.parse_sql("SELECT 1"), datasource_id=9)
        self.assertIn("不存在", str(ctx.exception))

    def test_syntax_error_means_nothing_to_execute(self):
        """解析不出语句时根本没有东西可试运行，必须明确拒绝而不是去连库"""
        broken = "SELECT id,\n  FROM users\nWHERE"
        self.assertFalse(parse.parse_sql(broken).ok)
        with (
            mock.patch.object(explain.datasource_services, "open_readonly_connection") as opener,
            self.assertRaises(explain.ExecutionRejected) as ctx,
        ):
            explain.execute(parse.parse_sql(broken), datasource_id=1)
        self.assertIn("没有可执行的语句", str(ctx.exception))
        opener.assert_not_called()

    def test_connection_is_rolled_back_and_closed(self):
        """只读事务用完即回滚，不留下悬挂事务"""
        _, connection = self.run_with("SELECT id FROM users", rows=[(1,)])
        self.assertGreaterEqual(connection.rolled_back, 1)
        self.assertTrue(connection.closed)


class _StubChat:
    """替身对话模型"""

    def __init__(self, reply_text="", raises=None):
        self.reply_text = reply_text
        self.raises = raises
        self.calls = 0

    def invoke(self, messages, **kwargs):
        self.calls += 1
        if self.raises is not None:
            raise self.raises
        from langchain_core.messages import AIMessage

        return AIMessage(content=self.reply_text)


class InterpretTests(SimpleTestCase):
    """
    模型解读：与规则判定分区，且不许编造出处

    「回归不得静默」「来源不许编造」是项目在知识问答里定下的两条约束，这里同样适用：
    使用者必须能分辨哪些是规则判定的确定结论、哪些是模型的推测。
    """

    ISSUES = [
        {
            "rule_code": "delete_without_where",
            "issue_level": custom_enum.IssueLevelEnum.HIGH.value,
            "target": "users",
            "description": "这条 DELETE 没有 WHERE 条件",
        }
    ]

    def run_interpret(self, reply_text="", raises=None, with_provider=True, issues=None):
        chat = _StubChat(reply_text, raises)
        provider = object() if with_provider else None
        with (
            mock.patch.object(interpret.llm, "active_chat_provider", return_value=provider),
            mock.patch.object(interpret.llm, "build_chat_model", return_value=chat),
        ):
            return (
                interpret.interpret("DELETE FROM users", self.ISSUES if issues is None else issues),
                chat,
            )

    def test_no_chat_provider_degrades_with_actionable_hint(self):
        result, chat = self.run_interpret(with_provider=False)
        self.assertFalse(result["available"])
        self.assertIn("对话模型", result["note"])
        self.assertIn("模型配置", result["note"], "必须告诉使用者去哪里配")
        self.assertEqual(result["explanations"], [])
        self.assertEqual(chat.calls, 0, "没配模型就不该发起调用")

    def test_explanations_and_observations_land_in_separate_fields(self):
        payload = json.dumps(
            {
                "explanations": [{"rule_code": "delete_without_where", "text": "会删光整表"}],
                "observations": ["这个表可能还有触发器"],
            }
        )
        result, _ = self.run_interpret(payload)
        self.assertTrue(result["available"])
        self.assertEqual([item["rule_code"] for item in result["explanations"]], ["delete_without_where"])
        self.assertEqual(result["observations"], ["这个表可能还有触发器"])

    def test_observations_carry_the_disclaimer(self):
        payload = json.dumps({"explanations": [], "observations": ["建议确认锁竞争"]})
        result, _ = self.run_interpret(payload)
        self.assertIn("未经规则验证", result["observations_note"])

    def test_no_observations_means_no_disclaimer(self):
        payload = json.dumps({"explanations": [], "observations": []})
        result, _ = self.run_interpret(payload)
        self.assertEqual(result["observations_note"], "")
        self.assertTrue(result["available"])

    def test_json_wrapped_in_a_code_fence_is_accepted(self):
        payload = "```json\n" + json.dumps({"explanations": [], "observations": ["x"]}) + "\n```"
        result, _ = self.run_interpret(payload)
        self.assertTrue(result["available"])
        self.assertEqual(result["observations"], ["x"])

    def test_fabricated_rule_code_is_dropped(self):
        """模型报了一个不存在的规则就是编造出处，必须丢弃——与「来源不许编造」同一条约束"""
        payload = json.dumps(
            {
                "explanations": [
                    {"rule_code": "delete_without_where", "text": "真的"},
                    {"rule_code": "made_up_rule", "text": "编的"},
                ],
                "observations": [],
            }
        )
        result, _ = self.run_interpret(payload)
        self.assertEqual([item["rule_code"] for item in result["explanations"]], ["delete_without_where"])

    def test_malformed_output_degrades_with_a_reason(self):
        result, _ = self.run_interpret("我觉得这条 SQL 还行")
        self.assertFalse(result["available"])
        self.assertIn("未能完成", result["note"])

    def test_model_failure_degrades_with_a_reason(self):
        result, _ = self.run_interpret(raises=RuntimeError("模型超时"))
        self.assertFalse(result["available"])
        self.assertIn("失败", result["note"])

    def test_clean_sql_still_lets_the_model_look_for_observations(self):
        """规则没判出问题时模型仍可提观察——这正是「补充规则之外」的价值所在"""
        payload = json.dumps({"explanations": [], "observations": ["建议确认该表的锁竞争"]})
        result, _ = self.run_interpret(payload, issues=[])
        self.assertTrue(result["available"])
        self.assertEqual(result["observations"], ["建议确认该表的锁竞争"])

    def test_degraded_result_keeps_the_same_shape(self):
        """降级结果与正常结果的字段必须一致，前端才不用到处判空"""
        degraded, _ = self.run_interpret(with_provider=False)
        normal, _ = self.run_interpret(json.dumps({"explanations": [], "observations": []}))
        self.assertEqual(sorted(degraded.keys()), sorted(normal.keys()))


class SqlAnalysisApiTests(TestCase):
    """
    接口级端到端：字段分区、解析失败降级、试运行的拒绝路径

    模型调用一律替换掉（不依赖外部服务）；数据库是真的——数据源存在性、是否已采集
    这类判定离开库反而测不准，而它们正是「未做校验必须说明原因」的落点。
    """

    URL = "/my-dba/v1/sql-analysis"

    def setUp(self):
        self.client = APIClient()
        self.interpretation = {
            "available": False,
            "note": "（测试替身）",
            "explanations": [],
            "observations": [],
            "observations_note": "",
        }
        patcher = mock.patch.object(views.interpret, "interpret", return_value=self.interpretation)
        patcher.start()
        self.addCleanup(patcher.stop)

    def post(self, path, payload):
        return self.client.post(f"{self.URL}/{path}", payload, format="json").json()

    def make_datasource(self, name="ds"):
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

    def test_analyze_returns_every_section_separately(self):
        """规则判定、结构校验、模型解读在响应里各占一个字段，前端才好分区渲染"""
        data = self.post("analyze", {"sql": "SELECT * FROM users"})["data"]
        for key in (
            "sql",
            "dialect",
            "formatted",
            "syntax",
            "issues",
            "schema_check",
            "interpretation",
            "evaluated_rules",
            "skipped_rules",
        ):
            self.assertIn(key, data)
        self.assertTrue(data["syntax"]["ok"])
        self.assertIn("select_star", [item["rule_code"] for item in data["issues"]])

    def test_analyze_says_schema_check_was_skipped_and_why(self):
        data = self.post("analyze", {"sql": "SELECT * FROM users"})["data"]
        self.assertFalse(data["schema_check"]["performed"])
        self.assertIn("未指定数据源", data["schema_check"]["note"])
        self.assertIn("unknown_table", data["skipped_rules"], "跳过的规则要能解释")

    def test_analyze_formats_the_sql(self):
        data = self.post("analyze", {"sql": "select id from users where id=1 limit 1"})["data"]
        self.assertIn("SELECT", data["formatted"])
        self.assertIn("\n", data["formatted"])

    def test_analyze_reports_the_dialect(self):
        self.assertEqual(self.post("analyze", {"sql": "SELECT 1", "dialect": 2})["data"]["dialect"], 2)

    def test_analyze_degrades_on_syntax_error(self):
        """解析失败不是整体失败：仍返回语法错误与原始语句，并说明后续步骤未进行"""
        data = self.post("analyze", {"sql": "SELECT id,\n  FROM users\nWHERE"})["data"]
        self.assertFalse(data["syntax"]["ok"])
        self.assertIsNotNone(data["syntax"]["errors"][0]["line"], "必须给出出错位置")
        self.assertEqual(data["issues"], [])
        self.assertFalse(data["schema_check"]["performed"])
        self.assertIn("未能解析", data["schema_check"]["note"])
        self.assertEqual(data["evaluated_rules"], [])

    def test_analyze_rejects_empty_sql(self):
        self.assertEqual(self.post("analyze", {"sql": "   "})["code"], 4000)

    def test_analyze_rejects_unknown_datasource(self):
        self.assertEqual(self.post("analyze", {"sql": "SELECT 1", "datasource_id": 999999})["code"], 4004)

    def test_analyze_explains_an_uncollected_datasource(self):
        datasource = self.make_datasource()
        data = self.post("analyze", {"sql": "SELECT 1", "datasource_id": datasource.id})["data"]
        self.assertFalse(data["schema_check"]["performed"])
        self.assertIn("尚未采集", data["schema_check"]["note"])

    def test_execute_requires_a_datasource(self):
        self.assertEqual(self.post("execute", {"sql": "SELECT 1"})["code"], 4000)

    def test_execute_rejects_unknown_datasource(self):
        self.assertEqual(self.post("execute", {"sql": "SELECT 1", "datasource_id": 999999})["code"], 4004)

    def test_execute_rejects_multi_statement_in_unified_format(self):
        """多语句被拒时走统一格式，且**不会去连库**"""
        datasource = self.make_datasource()
        with mock.patch.object(views.explain.datasource_services, "open_readonly_connection") as opener:
            payload = self.post("execute", {"sql": "SELECT 1; SELECT 2", "datasource_id": datasource.id})
        self.assertEqual(payload["code"], 4017)
        self.assertIn("一条语句", payload["message"])
        opener.assert_not_called()

    def test_execute_does_not_run_a_write_statement(self):
        datasource = self.make_datasource()
        connection = _FakeConnection()
        with mock.patch.object(views.explain.datasource_services, "open_readonly_connection", return_value=connection):
            data = self.post("execute", {"sql": "DELETE FROM users", "datasource_id": datasource.id})["data"]
        self.assertFalse(data["executed"])
        self.assertEqual(len(connection.issued_sql()), 1, "除了 EXPLAIN 不该发出任何语句")

    def test_execute_runs_a_read_only_statement(self):
        datasource = self.make_datasource()
        connection = _FakeConnection(rows=[(1,), (2,)])
        with mock.patch.object(views.explain.datasource_services, "open_readonly_connection", return_value=connection):
            data = self.post("execute", {"sql": "SELECT id FROM users", "datasource_id": datasource.id})["data"]
        self.assertTrue(data["executed"])
        self.assertEqual(data["row_count"], 2)

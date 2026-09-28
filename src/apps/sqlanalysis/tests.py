"""
SQL 分析模块单元测试

解析、语句分类与美化都是纯计算，`SimpleTestCase` 即可，不需要数据库。
「哪些语句允许真跑」是本模块最要紧的一条判定，单独一组重点覆盖。
"""

from django.test import SimpleTestCase

from apps.sqlanalysis import analyzer, formatting, parse
from apps.sqlanalysis.rules import registry
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
        self.assertNotIn("select_star", self.codes("SELECT id, name FROM t"))

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
        runner = analyzer.SqlAnalyzer(result, sql="", schema=object())
        runner.analyze()
        self.assertEqual(runner.skipped_rules, [])

    def test_disabled_rule_is_skipped(self):
        result = parse.parse_sql("SELECT * FROM t")
        runner = analyzer.SqlAnalyzer(result, sql="", rule_overrides={"select_star": {"enabled": False}})
        self.assertNotIn("select_star", [item["rule_code"] for item in runner.analyze()])
        self.assertNotIn("select_star", runner.evaluated_rules)

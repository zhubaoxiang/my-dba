"""
说人话 → 查询语句的单元测试

生成器（结构渲染与输出解析）是纯计算，用 `SimpleTestCase`。编排与接口要读快照，
用 `TestCase`。

**模型调用一律打桩，其余链路走真代码**——取结构、跑规则、判定要不要自修、组装响应
都是本项目自己的逻辑，正是最该被测的部分。
"""

from datetime import datetime
from unittest import mock

from django.test import SimpleTestCase, TestCase
from rest_framework.test import APIClient

from apps.datasource import models
from apps.nl2sql import generator, services
from utils import custom_enum


def _column(name, data_type="varchar(64)", nullable=True, comment=""):
    return {"name": name, "data_type": data_type, "nullable": nullable, "comment": comment}


def _table(name, columns, schema="public", comment="", primary_key=None, foreign_keys=None):
    """
    快照里的表结构形状（`list_snapshot_tables` 会把它转成对外契约的形状）
    """
    return {
        "schema": schema,
        "name": name,
        "comment": comment,
        "row_count": 0,
        "columns": columns,
        "primary_key": {"columns": primary_key or []},
        "foreign_keys": foreign_keys or [],
    }


def _contract_table(name, columns, schema="public", comment="", primary_key=None, foreign_keys=None):
    """
    `datasource.services.list_snapshot_tables` 返回的形状——`primary_key` 已是列名**列表**。
    直接测 `build_structure_text` 时用它，因为那个函数的入参就是这个形状。
    """
    return {
        "schema": schema,
        "name": name,
        "comment": comment,
        "columns": columns,
        "primary_key": primary_key or [],
        "foreign_keys": foreign_keys or [],
    }


class _FakeProvider:
    model_name = "test-model"


class StructureTextTests(SimpleTestCase):
    """
    结构渲染与输出解析：纯计算
    """

    def test_renders_table_columns_keys_and_comments(self):
        text = generator.build_structure_text(
            [
                _contract_table(
                    "app_user",
                    [_column("id", "bigint", nullable=False), _column("nickname", comment="昵称")],
                    comment="用户表",
                    primary_key=["id"],
                    foreign_keys=[{"columns": ["role_id"], "ref_table": "roles"}],
                )
            ]
        )
        self.assertIn("表 app_user（用户表）", text)
        self.assertIn("id bigint [主键]", text)
        self.assertIn("nickname varchar(64)  -- 昵称", text)
        self.assertIn("外键：role_id -> roles", text)

    def test_public_schema_is_not_prefixed(self):
        text = generator.build_structure_text([_contract_table("t", [_column("id")], schema="public")])
        self.assertIn("表 t", text)
        self.assertNotIn("public.t", text)

    def test_non_public_schema_is_prefixed(self):
        text = generator.build_structure_text([_contract_table("t", [_column("id")], schema="reporting")])
        self.assertIn("表 reporting.t", text)

    def test_extract_sql_strips_code_fence_and_semicolon(self):
        self.assertEqual(generator.extract_sql("```sql\nSELECT 1;\n```"), "SELECT 1")
        self.assertEqual(generator.extract_sql("SELECT 1;"), "SELECT 1")
        self.assertEqual(generator.extract_sql("  解释一句\nSELECT 1  "), "解释一句\nSELECT 1")

    def test_extract_sql_rejects_empty_output(self):
        with self.assertRaises(generator.GenerateError):
            generator.extract_sql("   ")

    def test_extract_sql_surfaces_the_insufficient_marker(self):
        """
        结构不足的标记必须单独识别出来，不能当成一条 SQL 送进解析器——
        那会报一个使用者看不懂的语法错误
        """
        with self.assertRaises(generator.SchemaInsufficient):
            generator.extract_sql("NOT_ENOUGH_SCHEMA")
        with self.assertRaises(generator.SchemaInsufficient):
            generator.extract_sql("```\nnot_enough_schema\n```")

    def test_dialect_note_covers_both_supported_dialects(self):
        """
        方言必须写进提示词：不说的话模型会自己猜，实测猜成 MySQL 之后把 PostgreSQL 里
        完全正确的双引号标识符说成「错误写法」
        """
        self.assertIn("PostgreSQL", generator._dialect_note("PostgreSQL"))
        self.assertIn("MySQL", generator._dialect_note("MySQL"))

    def test_dialect_note_is_silent_when_unknown(self):
        """认不出来就不提方言，由模型自己判断——而不是崩掉给一段错提示"""
        self.assertEqual(generator._dialect_note(""), "")
        self.assertEqual(generator._dialect_note("SQLite"), "")


class Nl2SqlGenerateTests(TestCase):
    """
    编排：取结构 → 生成 → 校验 → 自修一轮 → 组装

    模型层打桩，其余全部走真代码（包括那 18 条规则）。
    """

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
        self._collect(
            [
                _table("app_user", [_column("id", "bigint", nullable=False), _column("nickname")], primary_key=["id"]),
                _table("orders", [_column("id", "bigint", nullable=False), _column("user_id", "bigint")]),
            ]
        )

    def _collect(self, tables, datasource=None):
        return models.MetadataSnapshot.objects.create(
            datasource_id=(datasource or self.datasource).id,
            collect_time=datetime.now(),
            raw_data={"tables": tables},
        )

    def _stub_model(self, generated="SELECT id FROM app_user", repaired=None, generate_error=None):
        """
        替换掉「调模型」这一处。返回 (generate 的桩, repair 的桩)
        """
        generate_kwargs = {"side_effect": generate_error} if generate_error else {"return_value": generated}
        repair_kwargs = {"return_value": repaired if repaired is not None else generated}
        p_provider = mock.patch.object(services.llm, "active_chat_provider", return_value=_FakeProvider())
        p_generate = mock.patch.object(services.generator, "generate", **generate_kwargs)
        p_repair = mock.patch.object(services.generator, "repair", **repair_kwargs)
        started = []
        for patcher in (p_provider, p_generate, p_repair):
            started.append(patcher.start())
            self.addCleanup(patcher.stop)
        return started[1], started[2]

    # ------------------------------------------------------------------
    # 自修
    # ------------------------------------------------------------------

    def test_no_repair_when_the_first_attempt_is_clean(self):
        _, repair_call = self._stub_model(generated="SELECT id FROM app_user")
        data = services.generate_sql(self.datasource.id, "查用户 id")
        self.assertEqual(data["attempts"], 1)
        self.assertFalse(data["repair"]["applied"])
        self.assertEqual(data["repair"]["note"], "")
        repair_call.assert_not_called()

    def test_repairs_once_when_the_first_attempt_has_unknown_table(self):
        # user 不存在（真实表名是 app_user），自修后改对
        _, repair_call = self._stub_model(
            generated="SELECT id FROM user",
            repaired="SELECT id FROM app_user",
        )
        data = services.generate_sql(self.datasource.id, "查用户 id")
        self.assertEqual(data["attempts"], 2)
        self.assertTrue(data["repair"]["applied"])
        self.assertEqual(data["repair"]["before_sql"], "SELECT id FROM user")
        self.assertEqual(data["sql"], "SELECT id FROM app_user")
        self.assertIn("已自动修正", data["repair"]["note"])
        repair_call.assert_called_once()

    def test_repair_is_capped_at_one_round(self):
        """自修一轮是硬上限——修完仍有问题就如实说，不再重试"""
        _, repair_call = self._stub_model(
            generated="SELECT id FROM user",
            repaired="SELECT id FROM user",  # 修完还是错的
        )
        data = services.generate_sql(self.datasource.id, "查用户 id")
        self.assertEqual(repair_call.call_count, 1)
        self.assertTrue(data["repair"]["applied"])
        self.assertIn("仍有", data["repair"]["note"])
        self.assertTrue(data["issues"], "剩余问题必须如实展示")

    def test_repair_failure_is_reported_not_hidden(self):
        p_provider = mock.patch.object(services.llm, "active_chat_provider", return_value=_FakeProvider())
        p_generate = mock.patch.object(services.generator, "generate", return_value="SELECT id FROM user")
        p_repair = mock.patch.object(services.generator, "repair", side_effect=generator.GenerateError("模型超时"))
        for patcher in (p_provider, p_generate, p_repair):
            patcher.start()
            self.addCleanup(patcher.stop)

        data = services.generate_sql(self.datasource.id, "查用户 id")
        self.assertFalse(data["repair"]["applied"])
        self.assertIn("模型调用失败", data["repair"]["note"])
        self.assertEqual(data["sql"], "SELECT id FROM user", "自修失败时保留首次结果，不返回空")

    # ------------------------------------------------------------------
    # 降级与前置条件
    # ------------------------------------------------------------------

    def test_missing_chat_model_degrades_with_a_reason(self):
        with mock.patch.object(services.llm, "active_chat_provider", return_value=None):
            data = services.generate_sql(self.datasource.id, "查用户 id")
        self.assertFalse(data["generation"]["available"])
        self.assertEqual(data["sql"], "")
        self.assertIn("对话模型", data["generation"]["note"])
        self.assertEqual(data["verdict"]["label"], "未能生成")
        # 没生成语句也就没做结构校验——但必须说明原因，空 note 会被读成「校验过了、没问题」
        self.assertFalse(data["schema_check"]["performed"])
        self.assertTrue(data["schema_check"]["note"])

    def test_model_failure_degrades_with_a_reason(self):
        self._stub_model(generate_error=generator.GenerateError("模型调用失败：超时"))
        data = services.generate_sql(self.datasource.id, "查用户 id")
        self.assertFalse(data["generation"]["available"])
        self.assertIn("超时", data["generation"]["note"])

    def test_schema_insufficient_is_reported_as_scope_not_failure(self):
        """
        结构不足时**不硬写**：明说是范围问题，而不是给一条答另一个问题的 SQL。
        结论标签也要与「生成失败」分开——处置不同。
        """
        self._stub_model(generate_error=generator.SchemaInsufficient("结构不足"))
        data = services.generate_sql(self.datasource.id, "查告警规则", tables=["app_user"])
        self.assertEqual(data["sql"], "")
        self.assertEqual(data["verdict"]["label"], "结构不足")
        self.assertIn("仅提供了 1 张表", data["generation"]["note"])
        self.assertIn("扩大表范围", data["generation"]["note"])

    def test_schema_insufficient_without_a_table_limit_points_at_collection(self):
        self._stub_model(generate_error=generator.SchemaInsufficient("结构不足"))
        data = services.generate_sql(self.datasource.id, "查告警规则")
        self.assertIn("是否已被采集", data["generation"]["note"])

    def test_unknown_datasource_raises(self):
        with self.assertRaises(services.DatasourceNotFound):
            services.generate_sql(999999, "查用户 id")

    def test_datasource_without_snapshot_is_rejected(self):
        empty = models.Datasource.objects.create(
            name="empty",
            db_type=custom_enum.DbTypeEnum.POSTGRESQL.value,
            host="127.0.0.1",
            port=5432,
            db_name="d",
            username="u",
            password="p",
        )
        with self.assertRaises(services.StructureUnavailable) as ctx:
            services.generate_sql(empty.id, "查用户 id")
        self.assertIn("尚未采集", str(ctx.exception))

    def test_selected_tables_that_do_not_exist_are_rejected(self):
        with self.assertRaises(services.StructureUnavailable) as ctx:
            services.generate_sql(self.datasource.id, "查用户 id", tables=["no_such_table"])
        self.assertIn("均不存在", str(ctx.exception))

    def test_selected_tables_limit_the_structure(self):
        _, _ = self._stub_model()
        data = services.generate_sql(self.datasource.id, "查订单", tables=["orders"])
        self.assertEqual(data["structure"]["table_count"], 1)
        self.assertTrue(data["structure"]["limited"])

    def test_structure_over_limit_reports_instead_of_truncating(self):
        """超限必须报错——截断后继续会安静地产出基于残缺结构的语句"""
        self._stub_model()
        with (
            mock.patch.object(services, "_max_structure_chars", return_value=10),
            self.assertRaises(services.StructureTooLarge) as ctx,
        ):
            services.generate_sql(self.datasource.id, "查用户 id")
        self.assertIn("超出可承载范围", str(ctx.exception))

    def test_dialect_label_comes_from_the_datasource_type(self):
        """
        数据源的 `db_type` 是枚举**整数**，不是字符串——曾因直接当字符串用而崩在 `.strip()` 上。
        提示词要的是可读名，解析器要的是原始值，两者必须分开。
        """
        generate_call, _ = self._stub_model()
        data = services.generate_sql(self.datasource.id, "查用户 id")
        self.assertEqual(data["dialect"], "PostgreSQL")
        self.assertEqual(generate_call.call_args.args[3], "PostgreSQL")

    def test_mysql_datasource_gets_the_mysql_dialect(self):
        mysql = models.Datasource.objects.create(
            name="my",
            db_type=custom_enum.DbTypeEnum.MYSQL.value,
            host="127.0.0.1",
            port=3306,
            db_name="d",
            username="u",
            password="p",
        )
        self._collect([_table("app_user", [_column("id")])], datasource=mysql)
        generate_call, _ = self._stub_model()
        services.generate_sql(mysql.id, "查用户 id")
        self.assertEqual(generate_call.call_args.args[3], "MySQL")

    def test_explicit_dialect_overrides_the_datasource_type(self):
        generate_call, _ = self._stub_model()
        services.generate_sql(self.datasource.id, "查用户 id", dialect=custom_enum.DbTypeEnum.MYSQL.value)
        self.assertEqual(generate_call.call_args.args[3], "MySQL")

    def test_write_statement_is_flagged_as_modifying_data(self):
        """要改数据就给改数据的语句，并由后端标明它会改动——而不是硬写成一条 SELECT"""
        self._stub_model(generated="UPDATE app_user SET nickname = 'x' WHERE id = 1")
        data = services.generate_sql(self.datasource.id, "把 id=1 的昵称改掉")
        self.assertTrue(data["modifies_data"])

    def test_select_is_not_flagged_as_modifying_data(self):
        self._stub_model(generated="SELECT id FROM app_user")
        data = services.generate_sql(self.datasource.id, "查用户 id")
        self.assertFalse(data["modifies_data"])

    def test_ddl_counts_as_modifying_data(self):
        self._stub_model(generated="CREATE TABLE t (id bigint)")
        data = services.generate_sql(self.datasource.id, "建一张表")
        self.assertTrue(data["modifies_data"])

    def test_dangerous_write_does_not_trigger_repair(self):
        """
        高危告警**不触发自修**：模型是按使用者要求写的——「把所有用户的昵称都改掉」本来
        就没有 WHERE——让它去加一个 WHERE 等于擅自改变语义。**危险只报出来，不由系统替
        使用者改。**
        """
        _, repair_call = self._stub_model(generated="UPDATE app_user SET nickname = 'x'")
        data = services.generate_sql(self.datasource.id, "把所有用户的昵称都改掉")
        repair_call.assert_not_called()
        self.assertFalse(data["repair"]["applied"])
        # 但危险必须报出来
        self.assertIn("update_without_where", [item["rule_code"] for item in data["issues"]])

    def test_analysis_result_is_returned_with_the_sql(self):
        self._stub_model(generated="SELECT id FROM app_user")
        data = services.generate_sql(self.datasource.id, "查用户 id")
        self.assertEqual(data["sql"], "SELECT id FROM app_user")
        self.assertTrue(data["verdict"]["label"])
        self.assertTrue(data["schema_check"]["performed"], "有快照时必须真的做过结构校验")
        self.assertEqual(data["schema_check"]["table_count"], 2)
        self.assertEqual(data["generation"]["model"], "test-model")


class Nl2SqlApiTests(TestCase):
    """
    接口层：入参校验与错误映射
    """

    URL = "/my-dba/v1/nl2sql"

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

    def post(self, payload):
        return self.client.post(f"{self.URL}/generate", payload, format="json").json()

    def test_requires_datasource_and_question(self):
        self.assertEqual(self.post({})["code"], 4000)
        self.assertEqual(self.post({"datasource_id": self.datasource.id})["code"], 4000)
        self.assertEqual(self.post({"question": "查用户"})["code"], 4000)

    def test_blank_question_is_rejected(self):
        payload = self.post({"datasource_id": self.datasource.id, "question": "   "})
        self.assertEqual(payload["code"], 4000)

    def test_unknown_datasource_returns_not_found(self):
        payload = self.post({"datasource_id": 999999, "question": "查用户"})
        self.assertEqual(payload["code"], 4004)

    def test_over_limit_structure_returns_bad_request_without_any_sql(self):
        models.MetadataSnapshot.objects.create(
            datasource_id=self.datasource.id,
            collect_time=datetime.now(),
            raw_data={"tables": [_table("t", [_column("id")])]},
        )
        with mock.patch.object(services, "_max_structure_chars", return_value=10):
            payload = self.post({"datasource_id": self.datasource.id, "question": "查 t"})
        self.assertEqual(payload["code"], 4000)
        self.assertIn("超出可承载范围", payload["message"])
        self.assertIsNone(payload["data"], "报错时不该带任何生成结果")

    def test_response_does_not_leak_credentials(self):
        models.MetadataSnapshot.objects.create(
            datasource_id=self.datasource.id,
            collect_time=datetime.now(),
            raw_data={"tables": [_table("t", [_column("id")])]},
        )
        with mock.patch.object(services.llm, "active_chat_provider", return_value=None):
            payload = self.post({"datasource_id": self.datasource.id, "question": "查 t"})
        rendered = str(payload)
        for key in ("password", "api_key"):
            self.assertNotIn(key, rendered)

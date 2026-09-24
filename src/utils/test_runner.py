"""
测试运行器：让 TestCase 能用上真实的表结构

本项目**禁止 Django migration**，表结构由 `sql/pg_struct.sql` 定义。而 Django 默认靠
`migrate` 建测试库，本项目没有 migration，于是测试库里一张业务表都不会有——任何碰到
模型的 TestCase 都会以 `relation "xxx" does not exist` 失败。

这里在建好测试库之后，把全量 DDL 直接灌进去。只影响测试，不改动生产的 DDL 管理方式。
"""

import os

from django.conf import settings
from django.db import connections
from django.test.runner import DiscoverRunner

SCHEMA_FILE = os.path.join(settings.BASE_DIR, "sql", "pg_struct.sql")


class SqlSchemaTestRunner(DiscoverRunner):
    """
    建库后灌入 `sql/pg_struct.sql`

    `pg_struct.sql` 全部是 `CREATE ... IF NOT EXISTS`，可重复执行。
    """

    def setup_databases(self, **kwargs):
        aliases = kwargs.get("aliases") or []
        old_config = super().setup_databases(**kwargs)
        # 全部测试都不碰数据库时 aliases 为空，此时**绝不能**灌 DDL——
        # setup_databases 没建测试库，连接仍指向真实库，灌下去就是改生产结构。
        if aliases:
            self._load_schema(aliases)
        return old_config

    @staticmethod
    def _load_schema(aliases):
        with open(SCHEMA_FILE, encoding="utf-8") as handle:
            sql = handle.read()
        # 每行语句都以分号结尾且全文无参数占位，psycopg2 可一次执行多条
        for alias in aliases:
            with connections[alias].cursor() as cursor:
                cursor.execute(sql)

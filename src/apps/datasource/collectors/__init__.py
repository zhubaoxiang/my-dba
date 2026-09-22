"""
元数据采集器

按数据源类型选择合适的采集器。新增数据库类型时在此注册。
"""

from apps.datasource.collectors.mysql import MySQLCollector
from apps.datasource.collectors.postgres import PostgresCollector
from utils import custom_enum

_COLLECTORS = {
    custom_enum.DbTypeEnum.POSTGRESQL.value: PostgresCollector,
    custom_enum.DbTypeEnum.MYSQL.value: MySQLCollector,
}


def get_collector(datasource, config, plain_password: str = None):
    """
    按数据源类型取得采集器实例
    """
    collector_cls = _COLLECTORS.get(datasource.db_type)
    if collector_cls is None:
        raise ValueError(f"暂不支持的数据源类型: {datasource.db_type}")
    return collector_cls(datasource, config, plain_password)

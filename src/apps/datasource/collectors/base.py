"""
元数据采集器基类

采集器使用独立短连接直连目标库并即用即关，不接入 Django ORM 多库路由：
ORM 会把目标库连接纳入 Django 的连接生命周期（长连接、请求结束不释放），
且 ORM 允许写入，存在误写被纳管库的风险。

各子类只需实现方言相关的取数逻辑，统一结构组装、降级标记与精确行数开关由本类负责。
"""

from utils.crypto import decrypt
from utils.logger import get_logger

LOGGER = get_logger("datasource.log")

# 采集失败会导致整体无意义的必需项；其余项失败只记入 unavailable 并跳过（design.md D8）
ESSENTIAL_SECTIONS = ("schemas", "tables", "columns")


class CollectError(Exception):
    """
    采集失败
    """


class BaseCollector:
    """
    数据库元数据采集器基类
    """

    db_type = None

    # 目标库是否提供了索引使用统计；子类取数时置位，供分析规则判断能否评估「未使用索引」
    _index_usage_available = False

    def __init__(self, datasource, config, plain_password: str = None):
        self.datasource = datasource
        self.config = config
        self._plain_password = plain_password
        self._conn = None

    # ------------------------------------------------------------------
    # 连接管理
    # ------------------------------------------------------------------

    def connect(self):
        """
        建立到目标库的连接，子类实现
        """
        raise NotImplementedError

    def close(self):
        if self._conn is not None:
            try:
                self._conn.close()
            except Exception:  # noqa: BLE001 关闭失败不影响采集结果
                LOGGER.warning("关闭数据源连接失败 datasource_id=%s", self.datasource.id)
            finally:
                self._conn = None

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False

    def password(self) -> str:
        """
        取得连接密码：连接测试可用明文直传，落库的数据源则解密存储值
        """
        if self._plain_password is not None:
            return self._plain_password
        return decrypt(self.datasource.password)

    # ------------------------------------------------------------------
    # 子类实现：方言取数
    # ------------------------------------------------------------------

    def _server_version(self) -> str:
        raise NotImplementedError

    def _schemas(self) -> list:
        raise NotImplementedError

    def _tables(self) -> list:
        raise NotImplementedError

    def _columns(self) -> list:
        raise NotImplementedError

    def _indexes(self) -> list:
        raise NotImplementedError

    def _foreign_keys(self) -> list:
        raise NotImplementedError

    def _primary_keys(self) -> list:
        raise NotImplementedError

    def _exact_row_count(self, schema, table) -> int:
        raise NotImplementedError

    # ------------------------------------------------------------------
    # 对外能力
    # ------------------------------------------------------------------

    def test_connection(self) -> dict:
        """
        连接测试：只取版本信息，不落库
        """
        with self:
            version = self._server_version()
        return {"database_version": version, "db_type": self.datasource.db_type}

    def collect(self) -> dict:
        """
        采集全部元数据，返回统一结构
        """
        data = {
            "database_version": "",
            "schemas": [],
            "tables": [],
            "unavailable": [],
        }
        with self:
            data["database_version"] = self._safe(data["unavailable"], "version", self._server_version, default="")

            collected = {}
            for section in ("schemas", "tables", "columns", "indexes", "primary_keys", "foreign_keys"):
                collected[section] = self._collect_section(data["unavailable"], section)

            data["schemas"] = collected["schemas"]
            data["tables"] = self._assemble(
                collected["tables"],
                collected["columns"],
                collected["indexes"],
                collected["primary_keys"],
                collected["foreign_keys"],
            )
            data["table_count"] = len(data["tables"])

            if not self._index_usage_available and "index_usage" not in data["unavailable"]:
                data["unavailable"].append("index_usage")

            if self.config.get("exact_count"):
                self._fill_exact_row_count(data["tables"], data["unavailable"])

        return data

    def _collect_section(self, unavailable: list, section: str) -> list:
        """
        采集单个分区；必需项失败直接抛错，可选项失败记入 unavailable 后跳过
        """
        try:
            return getattr(self, f"_{section}")()
        except Exception as exc:  # noqa: BLE001 异构生产库的单项缺失不应让整次采集失败
            if section in ESSENTIAL_SECTIONS:
                raise CollectError(f"采集 {section} 失败: {exc}") from exc
            LOGGER.warning("采集项降级跳过 section=%s datasource_id=%s err=%s", section, self.datasource.id, exc)
            if section not in unavailable:
                unavailable.append(section)
            return []

    def _safe(self, unavailable: list, name: str, func, default):
        try:
            return func()
        except Exception as exc:  # noqa: BLE001 同上
            LOGGER.warning("采集项降级跳过 item=%s datasource_id=%s err=%s", name, self.datasource.id, exc)
            if name not in unavailable:
                unavailable.append(name)
            return default

    def _fill_exact_row_count(self, tables: list, unavailable: list):
        """
        精确行数（COUNT(*)）代价高，仅在配置显式开启时执行
        """
        for table in tables:
            try:
                table["row_count"] = self._exact_row_count(table["schema"], table["name"])
                table["row_count_exact"] = True
            except Exception as exc:  # noqa: BLE001 单表统计失败不影响其他表
                LOGGER.warning("精确行数统计失败 table=%s err=%s", table["name"], exc)
                if "exact_row_count" not in unavailable:
                    unavailable.append("exact_row_count")

    @staticmethod
    def _assemble(tables: list, columns: list, indexes: list, primary_keys: list, foreign_keys: list) -> list:
        """
        把扁平的分区结果按 (schema, table) 组装为嵌套结构
        """
        keyed_columns, keyed_indexes, keyed_pk, keyed_fk = {}, {}, {}, {}
        for item in columns:
            keyed_columns.setdefault((item.pop("schema"), item.pop("table")), []).append(item)
        for item in indexes:
            keyed_indexes.setdefault((item.pop("schema"), item.pop("table")), []).append(item)
        for item in primary_keys:
            keyed_pk[(item.pop("schema"), item.pop("table"))] = item
        for item in foreign_keys:
            keyed_fk.setdefault((item.pop("schema"), item.pop("table")), []).append(item)

        assembled = []
        for table in tables:
            key = (table["schema"], table["name"])
            table["columns"] = keyed_columns.get(key, [])
            table["indexes"] = keyed_indexes.get(key, [])
            table["primary_key"] = keyed_pk.get(key)
            table["foreign_keys"] = keyed_fk.get(key, [])
            assembled.append(table)
        return assembled

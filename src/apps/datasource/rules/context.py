"""
规则执行上下文

规则函数的统一入参：持有快照数据与本规则的生效参数，并负责产出统一结构的问题项。

规则自己决定遍历什么（逐表、跨表、库级），框架不驱动调用，只在产出时校验层级一致性——
因为规则形态差异大，跨表规则（如孤立表）无法用单表入参表达，强行统一会让框架复杂且限制表达力。
"""


class RuleContext:
    """
    单条规则的执行上下文
    """

    def __init__(self, snapshot_data: dict, rule, thresholds: dict = None):
        data = snapshot_data or {}
        self.data = data
        self.tables = data.get("tables") or []
        self.schemas = data.get("schemas") or []
        self.database_version = data.get("database_version", "")
        self.unavailable = set(data.get("unavailable") or [])

        self.rule_code = rule.code
        self.rule_name = rule.name
        self.object_level = rule.object_level
        self.default_level = rule.default_level
        self.thresholds = rule.default_thresholds if thresholds is None else thresholds

    def issue(
        self,
        description: str,
        suggestion: str,
        table: dict = None,
        column_name: str = "",
        target: str = "",
        issue_level=None,
        object_level=None,
    ) -> dict:
        """
        产出一条问题

        - 传入 table 时自动推导对象定位（模式.表[.列]）
        - 不传 table 时用 target 直接指定定位（供库级/模式级规则使用）
        - issue_level 留空则使用规则配置的级别；规则内部有级别细分时可显式覆盖
        - object_level 留空则使用规则声明的层级；只能覆写为更细的层级，框架会校验
        """
        level = issue_level if issue_level is not None else self.default_level
        level_of_object = object_level if object_level is not None else self.object_level
        schema_name = ""
        table_name = ""
        if table is not None:
            schema_name = table.get("schema") or ""
            table_name = table.get("name") or ""
            target = f"{schema_name}.{table_name}" if schema_name else table_name
            if column_name:
                target = f"{target}.{column_name}"

        return {
            "issue_level": level.value,
            "rule_code": self.rule_code,
            "rule_name": self.rule_name,
            "object_level": level_of_object.value,
            "target": target,
            "schema_name": schema_name,
            "table_name": table_name,
            "column_name": column_name,
            "description": description,
            "suggestion": suggestion,
        }

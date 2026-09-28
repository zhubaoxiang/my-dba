"""
SQL 规则执行上下文

规则的统一入参：持有本次解析出的语句、原始 SQL、方言，以及**可选的**表结构索引
（只有 `needs_schema` 的规则才会拿到），并负责产出统一结构的问题项。

与库表分析的 `RuleContext` 是**两套**：那里的主语是表/列，产出定位到「哪张表哪一列」；
这里的主语是**语句**，产出定位到「第几条语句的哪一处」。强行合并会让两边都别扭。
"""


class SqlRuleContext:
    """
    单条 SQL 规则的执行上下文
    """

    def __init__(self, statements, rule, sql: str = "", dialect=None, schema=None, level=None):
        """
        :param schema: 表结构索引（`schema.SchemaIndex`）；`None` 表示本次没有可用的表结构，
            需快照的规则据此自行跳过
        :param level: 生效的严重级别（来自规则注册表）；留空时用代码声明的默认级别
        """
        self.statements = tuple(statements or ())
        self.sql = sql or ""
        self.dialect = dialect
        self.schema = schema

        self.rule_code = rule.code
        self.rule_name = rule.name
        self.default_level = level if level is not None else rule.default_level

    @property
    def has_schema(self) -> bool:
        """
        本次是否有表结构可用
        """
        return self.schema is not None

    def issue(
        self,
        description: str,
        suggestion: str,
        statement=None,
        target: str = "",
        issue_level=None,
    ) -> dict:
        """
        产出一条问题

        - `statement` 留空表示问题针对整条 SQL 而非某一条语句（`statement_index` 为 0）
        - `issue_level` 留空则使用规则配置的级别
        """
        level = issue_level if issue_level is not None else self.default_level
        return {
            "rule_code": self.rule_code,
            "rule_name": self.rule_name,
            "issue_level": level.value,
            "statement_index": getattr(statement, "index", 0),
            "target": target,
            "description": description,
            "suggestion": suggestion,
        }

"""
SQL 规则分析

输入是解析结果，输出问题清单。**纯计算，不访问数据库**，便于单测。

与前两个模块的分析器同一形状：本模块只负责遍历生效规则、注入上下文、汇总产出，
判定逻辑全在 `rules/definitions.py` 里。

规则清单是**可复现**的：同一份输入必得同一份问题清单，不依赖模型、不受随机性影响。
"""

from apps.sqlanalysis.rules import registry
from apps.sqlanalysis.rules.context import SqlRuleContext
from utils.logger import get_logger

LOGGER = get_logger("sqlanalysis.log")


class SqlAnalyzer:
    """
    基于解析结果产出 SQL 问题清单
    """

    def __init__(self, parse_result, sql: str = "", dialect=None, schema=None, rule_overrides: dict = None):
        """
        :param schema: 表结构索引；`None` 表示本次没有可用表结构，需快照的规则会被跳过
        :param rule_overrides: 按规则 code 的覆盖项，供上层与测试注入（接入统一注册表后由库提供）
        """
        self.statements = parse_result.statements
        self.sql = sql
        self.dialect = dialect
        self.schema = schema
        self.rule_overrides = rule_overrides
        # 本次实际参与评估的规则 code
        self.evaluated_rules = []
        # 因缺少表结构而跳过的规则 code —— 跳过必须可解释，不能静默少跑
        self.skipped_rules = []

    def analyze(self) -> list:
        issues = []
        for effective in registry.load_effective_rules(self.rule_overrides):
            if not effective.enabled:
                continue
            if effective.needs_schema and self.schema is None:
                self.skipped_rules.append(effective.code)
                continue

            context = SqlRuleContext(
                self.statements,
                effective.definition,
                sql=self.sql,
                dialect=self.dialect,
                schema=self.schema,
                level=effective.level,
            )
            self.evaluated_rules.append(effective.code)
            issues.extend(effective.handler(context))

        # 按级别、语句序号排序，使同一输入每次得到同样顺序的清单
        issues.sort(key=lambda item: (item["issue_level"], item["statement_index"], item["rule_code"]))
        return issues

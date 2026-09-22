"""
库表健康问题分析

输入是采集快照的原始结构，输出问题清单。纯计算，不访问数据库，便于单测。

规则不再是本模块里的硬编码调用，而是由 apps/datasource/rules/ 声明式注册；
本模块只负责遍历规则、注入 RuleContext、校验产出层级并汇总问题。
"""

from apps.datasource.rules import registry
from apps.datasource.rules.context import RuleContext
from utils.logger import get_logger

LOGGER = get_logger("datasource.log")


class CatalogAnalyzer:
    """
    基于采集快照产出库表健康问题清单
    """

    def __init__(self, snapshot_data: dict, rule_overrides: dict = None):
        """
        :param snapshot_data: 采集快照的原始结构
        :param rule_overrides: 按规则 code 的覆盖项 {code: {"thresholds": {...}}}，供上层与测试注入
        """
        self.data = snapshot_data or {}
        self.rule_overrides = rule_overrides or {}
        # 本次实际参与评估的规则 code，供总览统计与「规则集变化可被察觉」使用
        self.evaluated_rules = []

    def analyze(self) -> list:
        issues = []
        for rule in registry.all_rules():
            override = self.rule_overrides.get(rule.code) or {}
            ctx = RuleContext(self.data, rule, registry.resolve_thresholds(rule, override.get("thresholds")))
            self.evaluated_rules.append(rule.code)

            allowed_levels = registry.allowed_levels(rule)
            for issue in rule.handler(ctx):
                if issue["object_level"] not in allowed_levels:
                    LOGGER.warning(
                        "规则产出层级超出声明，跳过该条问题 rule=%s declared=%s produced=%s",
                        rule.code,
                        rule.object_level.name,
                        issue["object_level"],
                    )
                    continue
                issues.append(issue)
        return issues

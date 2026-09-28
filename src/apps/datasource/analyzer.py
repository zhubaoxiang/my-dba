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
        :param rule_overrides: 按规则 code 的覆盖项 `{code: {"enabled"/"level"/"thresholds"}}`。
            **留空（None）时从 `analysis_rule` 表读**；显式传字典（含空字典）则不查库，
            供测试与将来的「规则试运行」注入
        """
        self.data = snapshot_data or {}
        self.rule_overrides = rule_overrides
        # 本次实际参与评估的规则 code，供总览统计与「规则集变化可被察觉」使用
        self.evaluated_rules = []

    def analyze(self) -> list:
        issues = []
        for effective in registry.load_effective_rules(self.rule_overrides):
            # 停用的规则直接跳过，不调用 handler（design.md D6：不影响历史数据）
            if not effective.enabled:
                continue
            ctx = RuleContext(self.data, effective.definition, thresholds=effective.thresholds, level=effective.level)
            self.evaluated_rules.append(effective.code)

            allowed_levels = registry.allowed_levels(effective.definition)
            for issue in effective.handler(ctx):
                if issue["object_level"] not in allowed_levels:
                    LOGGER.warning(
                        "规则产出层级超出声明，跳过该条问题 rule=%s declared=%s produced=%s",
                        effective.code,
                        effective.object_level.name,
                        issue["object_level"],
                    )
                    continue
                issues.append(issue)
        return issues

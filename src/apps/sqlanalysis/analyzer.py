"""
SQL 规则分析

输入是解析结果，输出问题清单。**纯计算，不访问数据库**，便于单测。

与前两个模块的分析器同一形状：本模块只负责遍历生效规则、注入上下文、汇总产出，
判定逻辑全在 `rules/definitions.py` 里。

规则清单是**可复现**的：同一份输入必得同一份问题清单，不依赖模型、不受随机性影响。
"""

from apps.sqlanalysis.rules import registry
from apps.sqlanalysis.rules.context import SqlRuleContext
from utils import custom_enum
from utils.logger import get_logger

LOGGER = get_logger("sqlanalysis.log")

_HIGH = custom_enum.IssueLevelEnum.HIGH
_MEDIUM = custom_enum.IssueLevelEnum.MEDIUM
_LOW = custom_enum.IssueLevelEnum.LOW

# 结论文案按「最严重的那一档」定调，而不是按问题总数——一条无 WHERE 的 DELETE
# 就足以让整条语句不能直接执行，被十条「LIKE 前缀通配」淹掉是不对的
_VERDICT = {
    _HIGH.value: "有明显问题",
    _MEDIUM.value: "有值得注意的问题",
    _LOW.value: "仅有轻微问题",
}


def verdict_of(issues: list, parsed: bool = True, skipped_rules: int = 0) -> dict:
    """
    确定性结论，**不依赖模型**

    模型解读可能不可用（未配置 / 调用失败 / 输出解析不了），但「这条 SQL 有没有明显问题」
    必须任何时候都能回答——那是使用者最想知道的一件事。

    :param parsed: 是否解析成功。**解析失败时规则一条都没跑**，那时的问题清单为空是
        「没能分析」而不是「没有问题」——把两者混为一谈会给出最误导人的结论
    :param skipped_rules: 因缺少表结构被跳过的规则数。同理，「没跑」不等于「没问题」
    """
    if not parsed:
        return {
            "level": _HIGH.value,
            "label": "无法执行",
            "text": "SQL 解析失败，规则一条都没能运行——请先按上方的错误位置修正语法。",
        }

    counts = {}
    for item in issues:
        counts[item["issue_level"]] = counts.get(item["issue_level"], 0) + 1

    # 没跑过的规则不能算「没问题」，否则使用者会以为表名列名已经验证过了
    skipped_note = (
        f"另有 {skipped_rules} 条规则因缺少表结构被跳过，表名与列名是否存在尚未验证。" if skipped_rules else ""
    )

    if not issues:
        return {
            "level": 0,
            "label": "未见明显问题（未完整校验）" if skipped_rules else "未见明显问题",
            "text": f"规则未判出问题。{skipped_note or '结构校验与执行计划可作进一步参考。'}",
        }

    parts = [
        f"{counts[level.value]} 个{label}"
        for level, label in ((_HIGH, "高危"), (_MEDIUM, "中危"), (_LOW, "低危"))
        if counts.get(level.value)
    ]
    level = _HIGH.value if counts.get(_HIGH.value) else (_MEDIUM.value if counts.get(_MEDIUM.value) else _LOW.value)
    return {
        "level": level,
        "label": _VERDICT[level],
        "text": f"共发现 {len(issues)} 个问题：{'、'.join(parts)}。{skipped_note}详见下方清单。",
    }


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

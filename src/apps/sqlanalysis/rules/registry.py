"""
SQL 规则注册表

与库表分析的注册表同形不同源：规则清单的唯一来源也是代码声明（`definitions.RULES`），
但规则的主语是**语句**，且多了一项 `needs_schema`——需要表结构才能判定的规则在
未绑定数据源时会被跳过，并由上层说明跳过原因。

将来接入统一规则注册表时，由 scope 维度区分这两类规则（见 design.md 的前置依赖）。
"""

import dataclasses
from collections.abc import Callable

from apps.sqlanalysis.rules.definitions import RULES
from utils import custom_enum


def all_rules() -> tuple:
    """
    全部规则声明（只读）
    """
    return RULES


def rule_codes() -> list:
    """
    全部规则 code
    """
    return [rule.code for rule in RULES]


def get_rule(code: str):
    """
    按 code 查找规则声明，不存在返回 None
    """
    for rule in RULES:
        if rule.code == code:
            return rule
    return None


def schema_rules() -> tuple:
    """
    需要表结构才能判定的规则
    """
    return tuple(rule for rule in RULES if rule.needs_schema)


@dataclasses.dataclass(frozen=True)
class EffectiveSqlRule:
    """
    一条 SQL 规则的**生效形态**：代码声明 + 覆盖项

    当前覆盖项只来自注入（规则尚未落库）；接入统一注册表后，这里改为合并库中的记录，
    与库表分析的 `EffectiveRule` 对齐。
    """

    definition: object
    enabled: bool
    level: "custom_enum.IssueLevelEnum"

    @property
    def code(self) -> str:
        return self.definition.code

    @property
    def name(self) -> str:
        return self.definition.name

    @property
    def needs_schema(self) -> bool:
        return self.definition.needs_schema

    @property
    def handler(self) -> Callable:
        return self.definition.handler


def load_effective_rules(injected: dict = None) -> list:
    """
    本次分析生效的规则清单

    :param injected: 按 `code` 的覆盖项 `{code: {"enabled"/"level"}}`；传 `None` 等同于全部
        用代码默认值（SQL 规则尚未落库，两者当前等价，保留入参是为了与库表分析的接口一致，
        将来接入统一注册表时不必改调用方）
    """
    overrides = injected or {}
    effective = []
    for rule in RULES:
        row = overrides.get(rule.code) or {}
        effective.append(
            EffectiveSqlRule(
                definition=rule,
                enabled=bool(row.get("enabled", True)),
                level=_level_or_default(row.get("level"), rule.default_level),
            )
        )
    return effective


def _level_or_default(raw, default):
    """
    与库表分析的注册表同一条约定：空值是正常路径（没有覆盖项），静默退回默认级别；
    有值但不合法才留痕
    """
    if raw is None or raw == "":
        return default
    try:
        return custom_enum.IssueLevelEnum(int(raw))
    except (TypeError, ValueError):
        return default

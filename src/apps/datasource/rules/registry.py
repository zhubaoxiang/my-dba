"""
规则注册表

对外暴露全部规则声明，并负责把 `analysis_rule` 表中的覆盖项合并到代码声明之上。

**代码声明是全集与默认值来源，库是覆盖层**（design.md D3）：库里没有某个 `code` 的记录时
用代码默认值；有则取库里的启用开关、级别与阈值。这样「还没同步过」不影响功能，
一次部署也不会冲掉运维改过的开关。
"""

import dataclasses

from apps.datasource.rules.definitions import RULES
from utils import custom_enum
from utils.logger import get_logger

LOGGER = get_logger("datasource.log")


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


def allowed_levels(rule) -> set:
    """
    规则允许产出的问题层级：其声明层级及其下层。

    表级规则可以报列级问题（列属于表），但不能报库级问题——那说明规则声明写错了。
    ObjectLevelEnum 的值由粗到细递增，故「下层」即更大的值。
    """
    declared = rule.object_level.value
    return {level.value for level in custom_enum.ObjectLevelEnum if level.value >= declared}


@dataclasses.dataclass(frozen=True)
class EffectiveRule:
    """
    一条规则的**生效形态**：代码声明 + 覆盖项

    `definition` 保留代码声明（handler 与默认值的来源），其余三项是本次分析实际使用的值。
    """

    definition: object
    enabled: bool
    level: "custom_enum.IssueLevelEnum"
    thresholds: dict

    @property
    def code(self) -> str:
        return self.definition.code

    @property
    def name(self) -> str:
        return self.definition.name

    @property
    def object_level(self):
        return self.definition.object_level

    @property
    def handler(self):
        return self.definition.handler


def _level_or_default(raw, default):
    """
    取库中的级别；无覆盖项时静默退回代码默认值

    空值走的是**正常路径**（库里没有该规则的记录），不该记日志——否则每轮分析都会
    为每条未同步的规则刷一条警告，把真正需要排查的脏数据淹掉。
    """
    if raw is None or raw == "":
        return default
    try:
        return custom_enum.IssueLevelEnum(int(raw))
    except (TypeError, ValueError):
        LOGGER.warning("规则级别取值非法，退回默认级别 raw=%r", raw)
        return default


def _load_overrides_from_db() -> dict:
    """
    一次取全部覆盖项，不逐条查库
    """
    from apps.datasource.models import AnalysisRule

    return {
        row.code: {"enabled": row.enabled, "level": row.level, "thresholds": row.thresholds or {}}
        for row in AnalysisRule.objects.filter(is_deleted=False)
    }


def load_effective_rules(injected: dict = None) -> list:
    """
    本次分析生效的规则清单

    :param injected: 按 `code` 的覆盖项 `{code: {"enabled"/"level"/"thresholds"}}`。
        传 `None`（默认）表示从 `analysis_rule` 表读；传字典则**完全不查库**——
        分析器的既有测试是 `SimpleTestCase`（不碰数据库），靠它注入；将来的「规则试运行」
        也用同一入口预览改动后的效果。
    """
    overrides = _load_overrides_from_db() if injected is None else injected

    effective = []
    for rule in RULES:
        row = overrides.get(rule.code) or {}
        effective.append(
            EffectiveRule(
                definition=rule,
                enabled=bool(row.get("enabled", True)),
                level=_level_or_default(row.get("level"), rule.default_level),
                # 库中的阈值按键覆盖代码默认值：漏配的键仍取默认值，不会因少写一项而失效
                thresholds={**rule.default_thresholds, **(row.get("thresholds") or {})},
            )
        )
    return effective

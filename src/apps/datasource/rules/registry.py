"""
规则注册表

对外暴露全部规则声明。规则清单的唯一来源是 definitions.RULES；
后续接入 analysis_rule 表后，由本模块负责把库中的覆盖项合并到代码声明之上。
"""

from apps.datasource.rules.definitions import RULES
from utils import custom_enum
from utils.configure import CONF_ATTR


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


def resolve_thresholds(rule, override: dict = None) -> dict:
    """
    该规则生效的阈值，优先级：显式覆盖 > 配置文件 > 声明默认值

    过渡实现：阈值仍由 conf.ini 提供（键名 `datasource_<阈值键>`）。
    接入规则注册表后，本函数改为合并库中的覆盖项，配置读取随之移除。
    """
    if override:
        return override

    resolved = {}
    for key, default in rule.default_thresholds.items():
        try:
            resolved[key] = int(CONF_ATTR.get(f"datasource_{key}"))
        except (TypeError, ValueError):
            resolved[key] = default
    return resolved

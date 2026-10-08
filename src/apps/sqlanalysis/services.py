"""
SQL 分析的跨模块只读契约

**跨模块请调用本文件里的函数**：调用方不要直接 import 规则注册表或本模块的模型
（`architecture.md`）。本模块至今没有服务层，因为它的对外产出（分析结果）刻意不落库、
不需要别人来读；规则条目数是第一项需要被别的模块读取的东西。

后续若有别的对外只读需求，一律加在这里，不要开第二个入口。
"""

from apps.sqlanalysis.rules import registry as rule_registry


def rule_counts() -> dict:
    """
    SQL 分析规则的条目数

    取自**代码声明**。SQL 规则目前没有覆盖机制，库里也没有对应记录，
    数库只会数出 0。
    """
    return {"total": len(rule_registry.all_rules())}


def list_rules() -> list:
    """
    SQL 分析规则的清单（只读）

    `needs_schema` 一并给出：需要表结构的规则在**未绑定数据源**时不会被判定，
    看清单的人该知道这件事，否则会以为它一直在跑。
    """
    return [
        {
            "code": rule.code,
            "name": rule.name,
            "description": rule.description,
            "level": rule.default_level.value,
            "level_label": rule.default_level.label,
            "needs_schema": bool(rule.needs_schema),
        }
        for rule in rule_registry.all_rules()
    ]

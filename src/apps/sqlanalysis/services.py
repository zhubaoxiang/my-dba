"""
SQL 分析的跨模块只读契约

**跨模块请调用本文件里的函数**：调用方不要直接 import 规则注册表或本模块的模型
（`architecture.md`）。本模块至今没有服务层，因为它的对外产出（分析结果）刻意不落库、
不需要别人来读；规则条目数是第一项需要被别的模块读取的东西。

后续若有别的对外只读需求，一律加在这里，不要开第二个入口。
"""

from apps.sqlanalysis import analyzer, parse, schema
from apps.sqlanalysis.rules import registry as rule_registry


def syntax_of(result) -> dict:
    """
    解析结果的对外形态（`format` 与 `analyze` 共用，故为公开函数）
    """
    return {
        "ok": result.ok,
        "statement_count": len(result.statements),
        "statements": [
            {"index": item.index, "kind": item.kind.value, "kind_label": item.kind.label, "sql": item.sql}
            for item in result.statements
        ],
        "errors": [dict(item) for item in result.errors],
    }


def _schema_check(index, skipped_reason: str = "") -> dict:
    """
    结构校验的结果说明

    **未做校验时必须说明原因**——让使用者以为校验过了比不校验更糟。
    """
    if index is None:
        return {"performed": False, "note": skipped_reason, "table_count": 0}
    return {"performed": True, "note": "", "table_count": len(index.tables)}


def _load_schema_index(datasource_id):
    """
    取表结构索引；拿不到时返回 (None, 说明)
    """
    try:
        return schema.load_schema_index(datasource_id), ""
    except schema.SchemaUnavailable as exc:
        return None, str(exc)


def analyze_sql(sql: str, dialect=None, datasource_id=None) -> dict:
    """
    解析 + 规则判定 + 结构校验（**不含模型解读**）

    与 `POST /sql-analysis/analyze` 同一套结果。抽到服务层是为了让别的模块
    （`apps/nl2sql`）不必直接调用本模块的 `parse` / `analyzer` / `schema`——
    `architecture.md` 要求跨模块只读走服务函数。

    解析失败**不是整体失败**：仍返回语法错误，规则与结构校验标记为未进行。
    """
    result = parse.parse_sql(sql, dialect)
    issues = []
    runner = None
    skipped_rules = 0

    if result.ok:
        index, skipped = _load_schema_index(datasource_id)
        runner = analyzer.SqlAnalyzer(result, sql=sql, dialect=dialect, schema=index)
        issues = runner.analyze()
        skipped_rules = len(runner.skipped_rules)
        schema_check = _schema_check(index, skipped)
    else:
        # 解析不了，规则一条都跑不了——但「没能分析」要说清楚，不能呈现成「没有问题」
        schema_check = _schema_check(None, "SQL 未能解析，本次未做结构校验。")

    return {
        "sql": sql,
        "dialect": dialect or "",
        "syntax": syntax_of(result),
        # 结论由规则产出算出来，**不依赖模型**——「有没有明显问题」任何时候都要有答案
        "verdict": analyzer.verdict_of(issues, parsed=result.ok, skipped_rules=skipped_rules),
        "issues": issues,
        "schema_check": schema_check,
        "evaluated_rules": list(runner.evaluated_rules) if runner else [],
        "skipped_rules": list(runner.skipped_rules) if runner else [],
    }


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

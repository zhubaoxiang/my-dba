"""
DDL 评审编排

解析 → 取快照 → 顺序合并 → 设计问题增量 → 语句风险 → 结论。全程纯计算，不执行、不落库。
"""

import copy

from apps.datasource import services as datasource_services
from apps.datasource.rules import registry as rule_registry
from apps.ddlreview import merge, parser, risks
from utils import custom_enum

_POSTGRES = custom_enum.DbTypeEnum.POSTGRESQL
_DDL_SCOPE = custom_enum.RuleScopeEnum.DDL
_NEEDS_DATASOURCE = {"ALTER_TABLE", "CREATE_INDEX", "DROP_TABLE", "DROP_INDEX", "TRUNCATE"}

_HIGH = custom_enum.IssueLevelEnum.HIGH
_MEDIUM = custom_enum.IssueLevelEnum.MEDIUM
_LOW = custom_enum.IssueLevelEnum.LOW


class DdlReviewError(Exception):
    """
    评审无法开始（输入/环境问题）

    `code` 直接对应统一响应格式：4000 参数错误、4004 不存在、4017 业务条件未满足。
    """

    code = 4000

    def __init__(self, message: str, code: int = 4000):
        super().__init__(message)
        self.code = code


def _dialect_of(dialect, brief):
    if dialect not in (None, ""):
        try:
            return custom_enum.DbTypeEnum(int(dialect))
        except (TypeError, ValueError):
            return _POSTGRES
    if brief is not None:
        try:
            return custom_enum.DbTypeEnum(int(brief.get("db_type")))
        except (TypeError, ValueError):
            return _POSTGRES
    return _POSTGRES


def _effective_rules(rule_overrides):
    all_rules = rule_registry.load_effective_rules(rule_overrides)
    ddl_rules = [rule for rule in all_rules if _DDL_SCOPE in getattr(rule.definition, "scopes", ())]
    thresholds = next((rule.thresholds for rule in all_rules if rule.code == "big_table"), {})
    return ddl_rules, thresholds


def _snapshot_data(tables, snapshot) -> dict:
    data = dict(snapshot or {})
    data.pop("collect_time", None)
    data["tables"] = tables
    return data


def _issue_key(issue) -> tuple:
    return (
        issue.get("rule_code") or "",
        issue.get("schema_name") or "",
        issue.get("table_name") or "",
        issue.get("column_name") or "",
        issue.get("fix_key") or "",
    )


def _table_key(table) -> tuple:
    return ((table.get("schema") or "").lower(), (table.get("name") or "").lower())


def _changed_table_keys(before_tables, after_tables) -> set:
    before = {_table_key(table): table for table in before_tables}
    after = {_table_key(table): table for table in after_tables}
    return {key for key in set(before) | set(after) if before.get(key) != after.get(key)}


def _wide_table_detail(key, before_tables, after_tables):
    """
    `wide_table` 的键不含列数；只有它需要额外的「加重」判定
    """
    _, schema, table_name, _, _ = key
    before = next(
        (
            table
            for table in before_tables
            if (table.get("schema") or "").lower() == (schema or "").lower()
            and (table.get("name") or "").lower() == (table_name or "").lower()
        ),
        None,
    )
    after = next(
        (
            table
            for table in after_tables
            if (table.get("schema") or "").lower() == (schema or "").lower()
            and (table.get("name") or "").lower() == (table_name or "").lower()
        ),
        None,
    )
    if before is None or after is None:
        return ""
    before_count = len(before.get("columns") or ())
    after_count = len(after.get("columns") or ())
    if after_count > before_count:
        return f"列数从 {before_count} 增加到 {after_count}"
    return ""


def _normalize_renames(issues, summary) -> list:
    """
    把变更前的问题键映射到改名后的名字

    不改的话，RENAME TABLE / RENAME COLUMN 会让表上原有的问题因为键变了而被当成「新增」，
    正好违背「只报本次新引入」的口径。
    """
    result = [dict(issue) for issue in issues]
    for rename in summary.get("tables_renamed") or ():
        old_schema = (rename.get("schema") or "").lower()
        old_name = (rename.get("from") or "").lower()
        for issue in result:
            if (issue.get("schema_name") or "").lower() == old_schema and (
                issue.get("table_name") or ""
            ).lower() == old_name:
                issue["table_name"] = rename.get("to") or issue.get("table_name")
                issue["schema_name"] = rename.get("new_schema") or issue.get("schema_name")
    for rename in summary.get("columns_renamed") or ():
        schema = (rename.get("schema") or "").lower()
        table = (rename.get("table") or "").lower()
        old_name = (rename.get("from") or "").lower()
        for issue in result:
            if (
                (issue.get("schema_name") or "").lower() == schema
                and (issue.get("table_name") or "").lower() == table
                and (issue.get("column_name") or "").lower() == old_name
            ):
                issue["column_name"] = rename.get("to") or issue.get("column_name")
    return result


def _design_delta(before_issues, after_issues, before_tables, after_tables) -> list:
    before_map = {_issue_key(issue): issue for issue in before_issues}
    after_map = {_issue_key(issue): issue for issue in after_issues}
    result = []
    for key, issue in after_map.items():
        old = before_map.get(key)
        if old is None:
            result.append({**issue, "change": "new", "change_detail": ""})
        elif issue["issue_level"] < old["issue_level"]:
            result.append({**issue, "change": "worsened", "change_detail": "严重级别升高"})
        elif issue["rule_code"] == "wide_table":
            detail = _wide_table_detail(key, before_tables, after_tables)
            if detail:
                result.append({**issue, "change": "worsened", "change_detail": detail})
    result.sort(key=lambda item: (item["issue_level"], item["rule_code"], item.get("target") or ""))
    return result


def _verdict(statements, not_reviewed, parsed_ok=True, parse_errors=None) -> dict:
    if not parsed_ok:
        errors = parse_errors or ()
        first = errors[0].get("description") if errors else "SQL 未能解析"
        return {"level": _HIGH.value, "label": "无法评审", "text": f"SQL 解析失败，本次未做评审：{first}"}

    applied_any = any(statement["applied"] for statement in statements)
    blocking = [
        (statement["index"], risk) for statement in statements for risk in statement["risks"] if risk.get("blocking")
    ]
    if blocking:
        index, risk = blocking[0]
        if not applied_any:
            return {
                "level": _HIGH.value,
                "label": "无法评审",
                "text": f"没有语句可以应用：第 {index} 条存在执行阻断——{risk['description']}",
            }
        return {
            "level": _HIGH.value,
            "label": "存在执行阻断",
            "text": f"第 {index} 条语句存在执行阻断：{risk['description']}",
        }

    partial = [item for item in not_reviewed if item.get("partial")]
    if partial:
        if not applied_any:
            return {
                "level": _HIGH.value,
                "label": "无法评审",
                "text": "没有语句可以评审；请先处理下方未评审项。",
            }
        return {
            "level": _HIGH.value,
            "label": "部分未评审",
            "text": f"有 {len(partial)} 条语句未能评审，结论不完整；请先处理未评审项。",
        }

    levels = [issue["issue_level"] for statement in statements for issue in statement["design_issues"]]
    levels += [risk["level"] for statement in statements for risk in statement["risks"]]
    if levels:
        level = min(levels)
        label = {_HIGH.value: "有高危风险，需确认", _MEDIUM.value: "有风险，需确认"}.get(level, "有轻微提示")
        return {
            "level": level,
            "label": label,
            "text": f"本次变更未发现执行阻断，但引入了 {len(levels)} 条设计问题或操作风险，执行前请逐条确认。",
        }
    return {
        "level": 0,
        "label": "未发现新增问题",
        "text": "本次变更未发现新增设计问题或操作风险。评审只覆盖结构与快照，不覆盖业务语义与数据分布。",
    }


def _unreviewable(parsed) -> dict:
    return {
        "verdict": _verdict([], [], parsed_ok=False, parse_errors=parsed.errors),
        "syntax": {"ok": False, "statement_count": 0, "errors": list(parsed.errors)},
        "snapshot": {
            "performed": False,
            "table_count": 0,
            "collect_time": "",
            "note": "SQL 未能解析，本次未做结构评审。",
        },
        "summary": {
            "statements": 0,
            "applied": 0,
            "not_reviewed": 0,
            "design_issue_total": 0,
            "risk_total": 0,
            "blocking_total": 0,
            "existing_issue_total": 0,
            "disabled_rules": [],
        },
        "statements": [],
        "not_reviewed": [],
    }


def review(sql: str, datasource_id=None, dialect=None, rule_overrides=None) -> dict:
    """
    评审一条或多条 DDL

    :param rule_overrides: 供测试与规则试运行注入；留空时从 `analysis_rule` 表读
    """
    if not (sql or "").strip():
        raise DdlReviewError("SQL 不能为空")

    brief = None
    if datasource_id:
        brief = datasource_services.datasource_brief(datasource_id)
        if brief is None:
            raise DdlReviewError(f"数据源 {datasource_id} 不存在或已删除", 4004)

    dialect_value = _dialect_of(dialect, brief)
    if dialect_value != _POSTGRES:
        raise DdlReviewError("DDL 评审目前只支持 PostgreSQL；MySQL 暂不支持。", 4017)

    parsed = parser.parse_ddl(sql, dialect_value)
    if not parsed.ok:
        return _unreviewable(parsed)

    needs_datasource = any(
        statement.supported and statement.kind in _NEEDS_DATASOURCE for statement in parsed.statements
    )
    snapshot = None
    if datasource_id:
        snapshot = datasource_services.latest_snapshot_data(datasource_id)
        if snapshot is None:
            raise DdlReviewError(
                f"数据源「{brief.get('name') or datasource_id}」尚未采集，无法取得改表前的结构；请先执行采集。",
                4017,
            )
    elif needs_datasource:
        raise DdlReviewError(
            "改表 / 索引 / 删除类评审需要先选择数据源，并确保该数据源已完成采集。",
            4017,
        )

    ddl_rules, thresholds = _effective_rules(rule_overrides)
    disabled_rules = [rule.code for rule in ddl_rules if not rule.enabled]
    evaluated_rules = [rule.code for rule in ddl_rules if rule.enabled]
    context = {
        "database_version": (snapshot or {}).get("database_version") or "",
        "thresholds": thresholds,
    }

    initial_tables = merge.copy_tables((snapshot or {}).get("tables") or ())
    working = merge.copy_tables(initial_tables)

    statements = []
    not_reviewed = []
    existing_issue_keys = set()
    stopped_reason = ""
    if snapshot is None:
        not_reviewed.append(
            {
                "index": 0,
                "reason": "未绑定数据源：级联删除链、外键目标、依赖影响等整库检查未评审。",
                "partial": False,
            }
        )

    for statement in parsed.statements:
        item = {
            "index": statement.index,
            "sql": statement.sql,
            "kind": statement.kind,
            "applied": False,
            "apply_error": "",
            "change_summary": {},
            "design_issues": [],
            "risks": [],
            "existing_issue_total": 0,
        }
        statements.append(item)

        if stopped_reason:
            item["apply_error"] = stopped_reason
            not_reviewed.append({"index": statement.index, "reason": stopped_reason, "partial": True})
            continue

        if not statement.supported:
            item["apply_error"] = statement.reason
            partial = statement.kind not in ("OTHER", "COMMENT")
            not_reviewed.append({"index": statement.index, "reason": statement.reason, "partial": partial})
            if partial:
                stopped_reason = "前一条语句暂不支持，后续未评审。"
            continue

        before_tables = copy.deepcopy(working)
        statement_risks = risks.evaluate(statement, working, context)
        item["risks"] = statement_risks

        blocking = [risk for risk in statement_risks if risk.get("blocking")]
        if blocking:
            item["apply_error"] = blocking[0]["description"]
            not_reviewed.append({"index": statement.index, "reason": blocking[0]["description"], "partial": True})
            stopped_reason = "前一条语句存在执行阻断，后续未评审。"
            continue

        applied = merge.apply(working, statement.expression)
        if not applied["applied"]:
            item["apply_error"] = applied["error"]
            not_reviewed.append({"index": statement.index, "reason": applied["error"], "partial": True})
            stopped_reason = "前一条语句无法应用，后续未评审。"
            continue

        before_issues = datasource_services.analyze_snapshot_data(
            _snapshot_data(before_tables, snapshot), scope=_DDL_SCOPE, rule_overrides=rule_overrides
        )
        before_issues = _normalize_renames(before_issues, applied.get("change_summary") or {})
        after_issues = datasource_services.analyze_snapshot_data(
            _snapshot_data(working, snapshot), scope=_DDL_SCOPE, rule_overrides=rule_overrides
        )
        changed_keys = _changed_table_keys(before_tables, working)
        statement_existing = {
            _issue_key(issue)
            for issue in before_issues
            if ((issue.get("schema_name") or "").lower(), (issue.get("table_name") or "").lower()) in changed_keys
        }
        existing_issue_keys.update(statement_existing)

        item["applied"] = True
        item["change_summary"] = applied.get("change_summary") or {}
        item["design_issues"] = _design_delta(before_issues, after_issues, before_tables, working)
        item["existing_issue_total"] = len(statement_existing)

    summary = {
        "statements": len(parsed.statements),
        "applied": sum(1 for item in statements if item["applied"]),
        "not_reviewed": len([item for item in not_reviewed if item.get("partial")]),
        "design_issue_total": sum(len(item["design_issues"]) for item in statements),
        "risk_total": sum(len(item["risks"]) for item in statements),
        "blocking_total": sum(1 for item in statements for risk in item["risks"] if risk.get("blocking")),
        "existing_issue_total": len(existing_issue_keys),
        "disabled_rules": disabled_rules,
    }

    return {
        "verdict": _verdict(statements, not_reviewed),
        "syntax": {"ok": True, "statement_count": len(parsed.statements), "errors": []},
        "snapshot": {
            "performed": snapshot is not None,
            "table_count": len(initial_tables),
            "collect_time": (snapshot or {}).get("collect_time") or "",
            "note": "" if snapshot is not None else "未绑定数据源，未做整库依赖与级联检查。",
        },
        "summary": summary,
        "statements": statements,
        "not_reviewed": not_reviewed,
        "rules": {"evaluated": evaluated_rules, "disabled": disabled_rules},
    }

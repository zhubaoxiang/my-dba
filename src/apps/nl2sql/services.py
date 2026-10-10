"""
说人话 → 查询语句的编排

一条链路：**取表结构 → 生成 → 用系统自己的规则校验 → 有结构问题就自修一轮 → 再校验 → 组装**。

跨模块一律走对方的服务函数（architecture.md）：

- 表结构 ← `datasource.services.list_snapshot_tables`（那里已声明的对外契约）
- 校验   ← `sqlanalysis.services.analyze_sql`（本次新增）
- 试运行 ← **不在这里**，由前端直接调既有的 `/sql-analysis/execute`

本模块不落库：生成的语句不保存，与 `sql-analysis` 的「贴一条、看结论、改完就走」一致。
"""

from apps.datasource import services as datasource_services
from apps.knowledge import llm
from apps.nl2sql import generator
from apps.sqlanalysis import services as sqlanalysis_services
from utils import custom_enum
from utils.configure import CONF_ATTR
from utils.logger import get_logger

LOGGER = get_logger("nl2sql.log")

# 表结构文本的字符上限的**兜底值**。实际取值来自 `conf.ini` 的 `[nl2sql] max_structure_chars`
# ——不同模型的上下文差得多，这个数本来就该可调，而不是写死在代码里。
_DEFAULT_MAX_STRUCTURE_CHARS = 120000


def _max_structure_chars() -> int:
    """
    表结构文本的字符上限

    用**字符数**而不是 token 数：token 取决于具体模型的分词器，估不准；字符数是确定的，
    超限报错时能给使用者一个可验证的数字。
    """
    try:
        return int(CONF_ATTR.get("nl2sql_max_structure_chars"))
    except (TypeError, ValueError):
        return _DEFAULT_MAX_STRUCTURE_CHARS


NO_CHAT_MODEL_MESSAGE = "尚未配置对话模型，无法生成查询语句；请在「模型配置」页添加对话模型后重试。"

# 触发自修的问题类型：**只认这两条**。
#
# 它们是「表名/列名写错了」的直接证据，自修能确定性改善。类型类的另外三条
# （join_key_type_mismatch / incomparable_types / implicit_cast）多是对写法选择的提示，
# 让模型去改有把本来正确的部分改坏的风险，且它们不影响这条查询能否执行。
# 规则 code 是稳定标识（`sqlanalysis` 的规格写明「不随规则增删改变」），依赖它是允许的。
_REPAIR_RULE_CODES = ("unknown_table", "unknown_column")


class DatasourceNotFound(Exception):
    """数据源不存在或已删除"""


class StructureUnavailable(Exception):
    """拿不到表结构——没有结构就只剩猜，而猜出来的表名列名一定是错的（design.md D1）"""


class StructureTooLarge(Exception):
    """参与生成的表结构超出可承载范围。**绝不截断**（design.md D3）"""


def _dialect_label(dialect) -> str:
    """
    方言的**可读名**，用于提示词与响应展示

    `datasource_brief()` 给的 `db_type` 是枚举**整数**，不是字符串——直接按字符串用会崩
    （`.strip()` 都没有），塞进提示词模型也读不懂。认不出来就留空，提示词里干脆不提方言，
    由模型自己判断。

    注意与「给解析器的值」分开：`analyze_sql()` 要的是原始值（整数或枚举），不是这个标签。
    """
    if dialect in (None, ""):
        return ""
    try:
        return custom_enum.DbTypeEnum(int(dialect)).label
    except (TypeError, ValueError):
        return ""


def _structure_blocks(datasource_id: int, brief: dict, wanted) -> list:
    """
    取本次参与生成的表结构

    结果为空与「还没采集」是两件事，各自给出可读原因，不合并成一句「没有数据」。
    """
    tables = datasource_services.list_snapshot_tables(datasource_id)
    if not tables:
        raise StructureUnavailable(
            f"数据源「{brief.get('name') or datasource_id}」尚未采集，无法获取表结构；请先执行采集。"
        )
    if not wanted:
        return tables

    names = {str(name).strip().lower() for name in wanted if str(name).strip()}
    picked = [table for table in tables if (table.get("name") or "").lower() in names]
    if not picked:
        raise StructureUnavailable("所选表在该数据源的最新快照中均不存在，请重新选择表范围。")
    return picked


def _repair_note(applied: bool, failed: bool, remaining: int) -> str:
    """
    自修的经过，由后端算好——前端不必从「有没有问题」反推「有没有修过」
    """
    if not applied:
        return ""
    if failed:
        return "首次生成的表名或列名有误；自动修正时模型调用失败。"
    if remaining:
        return f"首次生成的表名或列名有误，已自动修正一轮；仍有 {remaining} 处结构问题未解决。"
    return "首次生成的表名或列名有误，已自动修正。"


def _unavailable(note: str, structure: dict, dialect_label: str, label: str = "未能生成") -> dict:
    """
    生成不可用时的降级结果

    字段与正常返回**完全一致**，前端不必到处判空。
    """
    return {
        "sql": "",
        "dialect": dialect_label or "",
        "attempts": 0,
        "repair": {"applied": False, "problems": [], "before_sql": "", "note": ""},
        "issues": [],
        "modifies_data": False,
        # 「没能生成」与「生成了但有问题」是两回事，结论要如实反映
        "verdict": {"level": custom_enum.IssueLevelEnum.HIGH.value, "label": label, "text": note},
        # 没生成语句也就没做结构校验——但**必须说明原因**，空 note 会被读成「校验过了、没问题」
        "schema_check": {"performed": False, "note": "本次未生成语句，未做结构校验。", "table_count": 0},
        "structure": structure,
        "generation": {"available": False, "note": note, "model": ""},
    }


def _insufficient_schema(picked: list, structure: dict, dialect_label: str, limited: bool) -> dict:
    """
    结构不足以表达需求时的结果

    与「生成失败」分开说：这不是模型没干成，是**给它的范围不对**。所以结论标签用
    「结构不足」而不是「未能生成」，提示也是让使用者扩大范围，而不是「请重试」。
    """
    if limited:
        note = f"本次仅提供了 {len(picked)} 张表的表结构，不足以表达该需求；请扩大表范围后重试。"
    else:
        note = "现有表结构不足以表达该需求；请确认要用的表是否已被采集，或缩小到相关的表再试。"
    return _unavailable(note, structure, dialect_label, label="结构不足")


def _modifies_data(syntax: dict) -> bool:
    """
    生成的语句是否可能改动数据或结构

    与 `sql-analysis` 的执行边界**同一判法**：按最严格的一条算，且认不出的类型
    （sqlglot 的 `Command` 兜底那类）一律当作会改动。判错一边只是多显示一句提示，
    另一边是让人以为「这条不会改数据」。
    """
    statements = syntax.get("statements") or []
    return any(item.get("kind") != custom_enum.StatementKindEnum.READ_ONLY.value for item in statements)


def _repair_problems(issues: list) -> list:
    """
    从问题清单里挑出会触发自修的那几条
    """
    return [item for item in issues if item.get("rule_code") in _REPAIR_RULE_CODES]


def generate_sql(datasource_id: int, question: str, tables=None, dialect=None) -> dict:
    """
    生成一条查询语句，并用系统自己的规则校验它

    :param tables: 参与生成的表名列表；留空表示用该数据源的全部表
    :param dialect: 目标方言；留空时按数据源的库类型推断

    模型不可用或生成失败时**不抛异常**，而是返回一份说明原因的降级结果——「生成不了」
    是使用者需要看到的正常结论，不该表现成一次接口错误。数据源与结构的问题才抛异常
    （那是使用者的输入/环境问题，需要明确指出来）。
    """
    brief = datasource_services.datasource_brief(datasource_id)
    if brief is None:
        raise DatasourceNotFound(f"数据源 {datasource_id} 不存在或已删除")

    # 显式指定的优先，其次按数据源的库类型推断。**两个值分开**：
    # 解析器要原始值，提示词要可读名（见 `_dialect_label` 的说明）
    raw_dialect = dialect if dialect not in (None, "") else brief.get("db_type")
    dialect_label = _dialect_label(raw_dialect)

    picked = _structure_blocks(datasource_id, brief, tables)
    structure_text = generator.build_structure_text(picked)

    limit = _max_structure_chars()
    if len(structure_text) > limit:
        # 截断后继续是最坏的选择：会安静地产出基于残缺结构的语句，而使用者以为模型看到了全库
        raise StructureTooLarge(
            f"本次参与的表结构约 {len(structure_text)} 字符，超出可承载范围（上限 {limit}）；"
            f"当前 {len(picked)} 张表，请减少参与的表数量。"
        )

    structure = {
        "table_count": len(picked),
        "chars": len(structure_text),
        "limited": bool(tables),
    }

    provider = llm.active_chat_provider()
    if provider is None:
        return _unavailable(NO_CHAT_MODEL_MESSAGE, structure, dialect_label)

    try:
        sql = generator.generate(provider, structure_text, question, dialect_label)
    except generator.SchemaInsufficient:
        # **不是失败，是范围不对**——与「模型没干成」分开报，处置也不同
        LOGGER.info("模型判定结构不足 datasource_id=%s tables=%s", datasource_id, len(picked))
        return _insufficient_schema(picked, structure, dialect_label, bool(tables))
    except generator.GenerateError as exc:
        LOGGER.warning("生成失败 datasource_id=%s err=%s", datasource_id, exc)
        return _unavailable(str(exc), structure, dialect_label)

    analysis = sqlanalysis_services.analyze_sql(sql, dialect=raw_dialect, datasource_id=datasource_id)
    attempts = 1
    repair = {"applied": False, "problems": [], "before_sql": "", "note": ""}

    problems = _repair_problems(analysis["issues"])
    if problems:
        LOGGER.info(
            "结构问题触发自修 datasource_id=%s codes=%s",
            datasource_id,
            [item.get("rule_code") for item in problems],
        )
        repair = {"applied": True, "problems": problems, "before_sql": sql, "note": ""}
        try:
            repaired = generator.repair(provider, structure_text, question, sql, problems, dialect_label)
        except generator.SchemaInsufficient:
            repair["applied"] = False
            repair["note"] = "修正时模型判定现有表结构不足以表达该需求。"
        except generator.GenerateError as exc:
            LOGGER.warning("自修失败 datasource_id=%s err=%s", datasource_id, exc)
            repair["applied"] = False
            repair["note"] = _repair_note(applied=True, failed=True, remaining=len(problems))
        else:
            # **一轮为限**：这里不再判断新结果是否又有问题，有问题就如实展示（design.md D2）
            sql = repaired
            attempts = 2
            analysis = sqlanalysis_services.analyze_sql(sql, dialect=raw_dialect, datasource_id=datasource_id)
            repair["note"] = _repair_note(
                applied=True, failed=False, remaining=len(_repair_problems(analysis["issues"]))
            )

    return {
        "sql": sql,
        "dialect": dialect_label or "",
        "attempts": attempts,
        "repair": repair,
        "issues": analysis["issues"],
        "verdict": analysis["verdict"],
        # 由后端算好给界面用：写语句要标明「不会被执行」，前端不维护语句类别枚举
        "modifies_data": _modifies_data(analysis["syntax"]),
        # 校验只覆盖结构——「未做校验」时必须说明原因，不能呈现成「未发现问题」
        "schema_check": analysis["schema_check"],
        "evaluated_rules": analysis["evaluated_rules"],
        "skipped_rules": analysis["skipped_rules"],
        "structure": structure,
        "generation": {"available": True, "note": "", "model": provider.model_name},
    }

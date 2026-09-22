"""
分析规则声明

每条规则 = 一个判定函数 + 一条 RuleDefinition 声明。声明是该规则默认值的唯一来源；
判定逻辑一律是代码，不通过表达式或配置动态求值。

新增一条规则：写一个接收 RuleContext、返回 issue 列表的函数，再追加一条声明即可，
无需修改 analyze() 的调用列表。
"""

import re
from collections.abc import Callable
from dataclasses import dataclass, field

from utils import custom_enum

# 字符类型里存时间语义的列名，常见的建模问题
_TIME_LIKE_NAME_PATTERN = re.compile(r"(^|_)(time|date|timestamp|datetime)s?$|_at$|_on$")

_CHAR_TYPES = {"char", "varchar", "character", "character varying", "bpchar", "nchar", "nvarchar"}

# 只把「无上界且不参与常规索引」的类型视为大对象。
# 不含 text / json / jsonb：前者在 PostgreSQL 中是推荐的字符串类型，后者是结构化且可索引的常用类型，
# 把它们一律报为可疑字段会在正常库上产生大量噪音。
_LARGE_OBJECT_TYPES = {"blob", "tinyblob", "mediumblob", "longblob", "bytea", "mediumtext", "longtext"}

_BYTES_PER_MB = 1024 * 1024


@dataclass(frozen=True)
class RuleDefinition:
    """
    一条分析规则的声明

    - code 为稳定标识，不随规则增删改变（改名属破坏性操作）
    - object_level 为声明式元数据，用于界面分组、筛选与产出校验
    - default_thresholds 仅为默认值，运行时可被规则注册表覆盖
    """

    code: str
    name: str
    description: str
    default_level: "custom_enum.IssueLevelEnum"
    object_level: "custom_enum.ObjectLevelEnum"
    handler: Callable
    default_thresholds: dict = field(default_factory=dict)


# ----------------------------------------------------------------------
# 判定函数
# ----------------------------------------------------------------------


def _check_no_primary_key(ctx):
    issues = []
    for table in ctx.tables:
        if table.get("primary_key"):
            continue
        issues.append(
            ctx.issue(
                table=table,
                description="表没有主键。没法按行精确定位数据，重复写入不容易被发现，后续加索引或改字段也更麻烦。",
                suggestion="为该表补充主键；若确无业务唯一列，可增加自增或雪花算法代理主键。",
            )
        )
    return issues


def _check_foreign_key_index(ctx):
    issues = []
    for table in ctx.tables:
        index_columns = [list(idx.get("columns") or []) for idx in table.get("indexes") or []]
        for fk in table.get("foreign_keys") or []:
            fk_columns = list(fk.get("columns") or [])
            if not fk_columns:
                continue
            if any(columns[: len(fk_columns)] == fk_columns for columns in index_columns):
                continue
            issues.append(
                ctx.issue(
                    table=table,
                    column_name=",".join(fk_columns),
                    description=f"外键 {fk.get('name')}（列 {', '.join(fk_columns)}）没有以它为前导列的索引，"
                    f"关联删除与联表查询会退化为全表扫描。",
                    suggestion=f"为 ({', '.join(fk_columns)}) 建立索引。",
                )
            )
    return issues


def _check_duplicate_index(ctx):
    issues = []
    for table in ctx.tables:
        candidates = [idx for idx in table.get("indexes") or [] if not idx.get("primary") and idx.get("columns")]
        reported = set()

        by_columns = {}
        for idx in candidates:
            by_columns.setdefault(tuple(idx["columns"]), []).append(idx)

        for columns, group in by_columns.items():
            if len(group) < 2:
                continue
            # 保留一个（优先保留唯一的），其余判为重复
            keep = next((idx for idx in group if idx.get("unique")), group[0])
            for idx in group:
                if idx is keep or idx["name"] in reported:
                    continue
                reported.add(idx["name"])
                issues.append(
                    ctx.issue(
                        table=table,
                        column_name=",".join(columns),
                        description=f"索引 {idx['name']} 与 {keep['name']} 的列完全相同（{', '.join(columns)}），属重复索引，"
                        f"会拖慢写入并占用额外空间。",
                        suggestion=f"确认无其他依赖后删除索引 {idx['name']}，保留 {keep['name']}。",
                    )
                )

        # 前缀包含：非唯一索引被更长列的索引覆盖时可删
        for idx in candidates:
            if idx["name"] in reported or idx.get("unique"):
                continue
            columns = list(idx["columns"])
            for other in candidates:
                if other is idx or other["name"] in reported:
                    continue
                other_columns = list(other["columns"])
                if len(other_columns) > len(columns) and other_columns[: len(columns)] == columns:
                    reported.add(idx["name"])
                    issues.append(
                        ctx.issue(
                            table=table,
                            column_name=",".join(columns),
                            issue_level=custom_enum.IssueLevelEnum.LOW,
                            description=f"索引 {idx['name']}（{', '.join(columns)}）是索引 {other['name']}"
                            f"（{', '.join(other_columns)}）的前缀，属冗余索引。",
                            suggestion=f"确认无其他依赖后删除索引 {idx['name']}。",
                        )
                    )
                    break
    return issues


def _check_unused_index(ctx):
    # 统计信息不可用时跳过本规则
    if "index_usage" in ctx.unavailable:
        return []
    min_rows = ctx.thresholds["unused_index_min_rows"]
    issues = []
    for table in ctx.tables:
        if int(table.get("row_count") or 0) < min_rows:
            continue
        for idx in table.get("indexes") or []:
            if idx.get("primary") or idx.get("unique"):
                continue
            if idx.get("scans") != 0:
                continue
            issues.append(
                ctx.issue(
                    table=table,
                    column_name=",".join(idx.get("columns") or []),
                    description=f"索引 {idx['name']} 自统计重置以来未被使用（scans=0），且表行数已达 "
                    f"{table.get('row_count')}，仅增加写入成本。",
                    suggestion=f"观察一个完整业务周期后确认无用，再删除索引 {idx['name']}。",
                )
            )
    return issues


def _check_big_table(ctx):
    row_limit = ctx.thresholds["big_table_rows"]
    size_limit = ctx.thresholds["big_table_size_mb"] * _BYTES_PER_MB
    issues = []
    for table in ctx.tables:
        rows = int(table.get("row_count") or 0)
        size = int(table.get("total_size") or 0)
        exceeded = []
        if rows > row_limit:
            exceeded.append(f"行数 {rows} 超过阈值 {row_limit}")
        if size > size_limit:
            exceeded.append(f"占用 {size} 字节超过阈值 {size_limit} 字节")
        if not exceeded:
            continue
        issues.append(
            ctx.issue(
                table=table,
                description="表体量偏大（" + "；".join(exceeded) + "），查询会变慢，加字段或加索引也要等更久。",
                suggestion="评估按时间或业务维度分区、归档历史数据，或把冷热数据拆开。",
            )
        )
    return issues


def _check_column_types(ctx):
    issues = []
    max_length = ctx.thresholds["varchar_max_length"]
    for table in ctx.tables:
        for column in table.get("columns") or []:
            data_type = (column.get("data_type") or "").lower()
            name = column.get("name") or ""
            length = int(column.get("length") or -1)

            if data_type in _CHAR_TYPES and _TIME_LIKE_NAME_PATTERN.search(name):
                issues.append(
                    ctx.issue(
                        table=table,
                        column_name=name,
                        description=f"列 {name} 语义上是时间，却使用字符类型 {data_type}。字符串比较无法利用时间运算与范围索引，"
                        f"格式也不受约束。",
                        suggestion="改用 timestamp / datetime 类型。",
                    )
                )
                continue

            if data_type in _CHAR_TYPES and length > max_length:
                issues.append(
                    ctx.issue(
                        table=table,
                        column_name=name,
                        issue_level=custom_enum.IssueLevelEnum.LOW,
                        description=f"列 {name} 定义为 {data_type}({length})，长度上限远超常规，实际占用与索引体积都偏大。",
                        suggestion="按实际数据分布收紧长度，或改用更合适的类型。",
                    )
                )
                continue

            if data_type in _LARGE_OBJECT_TYPES:
                issues.append(
                    ctx.issue(
                        table=table,
                        column_name=name,
                        issue_level=custom_enum.IssueLevelEnum.LOW,
                        description=f"列 {name} 使用大对象类型 {data_type}，与其他列同行存储会显著降低表扫描效率。",
                        suggestion="评估把大字段拆到独立扩展表，或改用更适合的类型。",
                    )
                )
    return issues


def _check_isolated_tables(ctx):
    referenced = set()
    for table in ctx.tables:
        for fk in table.get("foreign_keys") or []:
            referenced.add((fk.get("ref_schema"), fk.get("ref_table")))

    issues = []
    for table in ctx.tables:
        key = (table.get("schema"), table.get("name"))
        if table.get("foreign_keys") or key in referenced:
            continue
        issues.append(
            ctx.issue(
                table=table,
                description="该表既没有外键，也没有被任何表引用，与库内其他表无关联。",
                suggestion="确认是否为遗留表或临时表；若无业务代码引用，可安排清理。",
            )
        )
    return issues


# ----------------------------------------------------------------------
# 规则清单
# ----------------------------------------------------------------------

RULES = (
    RuleDefinition(
        code="no_primary_key",
        name="无主键表",
        description="表没有主键，无法按行定位数据，重复写入不易发现。",
        default_level=custom_enum.IssueLevelEnum.HIGH,
        object_level=custom_enum.ObjectLevelEnum.TABLE,
        handler=_check_no_primary_key,
    ),
    RuleDefinition(
        code="fk_without_index",
        name="外键缺索引",
        description="外键列没有以它为前导列的索引，关联删除与联表查询会退化为全表扫描。",
        default_level=custom_enum.IssueLevelEnum.MEDIUM,
        object_level=custom_enum.ObjectLevelEnum.TABLE,
        handler=_check_foreign_key_index,
    ),
    RuleDefinition(
        code="duplicate_index",
        name="重复或冗余索引",
        description="同一张表上存在列完全相同、或构成前缀包含关系的多个索引。",
        default_level=custom_enum.IssueLevelEnum.MEDIUM,
        object_level=custom_enum.ObjectLevelEnum.TABLE,
        handler=_check_duplicate_index,
    ),
    RuleDefinition(
        code="unused_index",
        name="疑似未使用索引",
        description="索引自统计重置以来未被使用，仅增加写入成本。",
        default_level=custom_enum.IssueLevelEnum.LOW,
        object_level=custom_enum.ObjectLevelEnum.TABLE,
        handler=_check_unused_index,
        default_thresholds={"unused_index_min_rows": 10000},
    ),
    RuleDefinition(
        code="big_table",
        name="超大表",
        description="表行数或占用空间超过阈值，查询与改表都会变慢。",
        default_level=custom_enum.IssueLevelEnum.MEDIUM,
        object_level=custom_enum.ObjectLevelEnum.TABLE,
        handler=_check_big_table,
        default_thresholds={"big_table_rows": 10000000, "big_table_size_mb": 10240},
    ),
    RuleDefinition(
        code="suspicious_column_type",
        name="可疑字段类型",
        description="用字符类型存时间、超长 varchar 或大对象类型。",
        default_level=custom_enum.IssueLevelEnum.MEDIUM,
        object_level=custom_enum.ObjectLevelEnum.COLUMN,
        handler=_check_column_types,
        default_thresholds={"varchar_max_length": 2000},
    ),
    RuleDefinition(
        code="isolated_table",
        name="孤立表",
        description="表既没有外键，也没有被任何表引用，与库内其他表无关联。",
        default_level=custom_enum.IssueLevelEnum.LOW,
        object_level=custom_enum.ObjectLevelEnum.TABLE,
        handler=_check_isolated_tables,
    ),
)

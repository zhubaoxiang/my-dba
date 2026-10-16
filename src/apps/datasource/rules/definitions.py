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

# 浮点族。**只用于「金额类字段」的判定**——浮点在科学计算与统计指标里是正确选择，
# 不报所有浮点列（design.md D1）
_FLOAT_TYPES = {"real", "double precision", "float", "double", "float4", "float8"}

# 金额语义的列名/注释。**刻意不收 total**——`total_count` 是数量不是金额，收进来会误报。
# 词表放宽的方向是漏报（可接受），不是误报
_MONEY_NAME_PATTERN = re.compile(r"amount|price|fee|cost|balance|money|金额|价格|费用|余额")

# 采集侧已把外键动作归一成这套名字（见 collectors/base.py）
_CASCADE = "CASCADE"


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
                        fix_key=idx["name"],
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
                            fix_key=idx["name"],
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
                    fix_key=idx["name"],
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
                        fix_key="char_time",
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
                        fix_key="long_varchar",
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
                        fix_key="large_object",
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


def _check_money_float(ctx):
    """
    金额类字段用了浮点：会丢精度，且越晚改越贵

    **只对「类型是浮点 + 名字或注释说是钱」的列报**。一律报所有浮点列会立刻把这条规则
    变成噪音源——浮点在科学计算里是对的。这条规则的代价是**漏报**（列叫 total 又没注释
    就判不出），那是刻意的取舍。
    """
    issues = []
    for table in ctx.tables:
        for column in table.get("columns") or []:
            if (column.get("data_type") or "").lower() not in _FLOAT_TYPES:
                continue
            text = f"{column.get('name') or ''} {column.get('comment') or ''}"
            if not _MONEY_NAME_PATTERN.search(text):
                continue
            issues.append(
                ctx.issue(
                    table=table,
                    column_name=column.get("name") or "",
                    description=f"列 {column.get('name')} 语义上是金额，却使用浮点类型 "
                    f"{column.get('data_type')}。浮点无法精确表示小数，累加与比较会出现"
                    f"对不上账的误差。",
                    suggestion="改用 numeric / decimal 类型，精度按业务定。",
                )
            )
    return issues


def _check_long_text_primary_key(ctx):
    """
    主键用了长字符类型

    主键会被**所有**引用它的外键与索引各复制一份——主键越宽，引用它的表每行就多背多少字节。
    """
    limit = ctx.thresholds["primary_key_max_length"]
    issues = []
    for table in ctx.tables:
        primary_key = table.get("primary_key") or {}
        key_columns = set(primary_key.get("columns") or [])
        if not key_columns:
            continue
        for column in table.get("columns") or []:
            if column.get("name") not in key_columns:
                continue
            data_type = (column.get("data_type") or "").lower()
            if data_type not in _CHAR_TYPES:
                continue
            length = int(column.get("length") or -1)
            # 长度为 -1 表示无界（PostgreSQL 把不限长的字符列报成 -1）
            if length != -1 and length <= limit:
                continue
            actual = "无长度上限" if length == -1 else f"长度 {length}"
            issues.append(
                ctx.issue(
                    table=table,
                    column_name=column.get("name") or "",
                    description=f"主键列 {column.get('name')} 是字符类型（{data_type}，{actual}）。"
                    f"主键会被所有引用它的外键与索引各复制一份，主键越宽，那些表每行就多背多少字节。",
                    suggestion="改用整型代理键；确需保留业务键时，优先把长度收窄。",
                )
            )
    return issues


def _check_wide_table(ctx):
    """
    列数过多（宽表）

    列数多通常意味着建模没有拆开，整行读写与 SELECT * 都会变重。
    """
    limit = ctx.thresholds["wide_table_max_columns"]
    issues = []
    for table in ctx.tables:
        count = len(table.get("columns") or [])
        if count <= limit:
            continue
        issues.append(
            ctx.issue(
                table=table,
                description=f"表有 {count} 列，超过 {limit}。列数过多通常意味着建模没有拆开，"
                f"整行读写与 SELECT * 都会变重。",
                suggestion="评估把低频使用的列拆到扩展表，或按访问模式分开存放。",
            )
        )
    return issues


def _check_index_larger_than_data(ctx):
    """
    索引占用超过数据占用

    整表视角：与逐索引的「重复或冗余索引」「疑似未使用索引」互补——那两条指出具体是哪个
    索引，这条指出这张表的索引整体失衡。
    """
    issues = []
    for table in ctx.tables:
        # 空表的数据占用接近 0，索引稍大是正常形态，不是问题
        if int(table.get("row_count") or 0) <= 0:
            continue
        data_size = int(table.get("data_size") or 0)
        index_size = int(table.get("index_size") or 0)
        if index_size <= data_size:
            continue
        issues.append(
            ctx.issue(
                table=table,
                description=f"索引占用 {index_size // _BYTES_PER_MB} MB，超过数据本身的 "
                f"{data_size // _BYTES_PER_MB} MB。索引比数据还大，说明索引过多或过宽，"
                f"每一项都在拖慢写入。",
                suggestion="看「重复或冗余索引」与「疑似未使用索引」列出的具体索引。",
            )
        )
    return issues


def _check_cascade_delete_chain(ctx):
    """
    跨表的级联删除链

    **只报跨两层以上的传递删除**：本表的 `ON DELETE CASCADE` 指向的表**自己也有**
    CASCADE 出边。不报单层级联（那是 CASCADE 的字面意思）与自引用级联（`parent_id →
    本表` 是树形结构的常规做法）——见 design.md D6。

    外键动作缺失时（本次补采之前的老快照）判不出来，按「没有级联」处理、不产出问题。
    """
    cascades = {}
    for table in ctx.tables:
        key = (table.get("schema") or "", table.get("name") or "")
        targets = {
            (foreign_key.get("ref_schema") or key[0], foreign_key.get("ref_table") or "")
            for foreign_key in table.get("foreign_keys") or []
            if (foreign_key.get("on_delete") or "").upper() == _CASCADE
        }
        if targets:
            cascades[key] = targets

    issues = []
    for table in ctx.tables:
        key = (table.get("schema") or "", table.get("name") or "")
        downstream = cascades.get(key)
        if not downstream:
            continue
        # 下游里**自己也有级联出边**的，才会继续往下传
        # 注意 `target` 是整体解出来的 (schema, table) 元组——写成 `for t, name in ...`
        # 会把元组拆开，再拿 schema 去比整张表的键，永远比不上
        chain = sorted(target[1] for target in downstream if target in cascades and target != key)
        if not chain:
            continue
        issues.append(
            ctx.issue(
                table=table,
                description=f"本表的外键是 ON DELETE CASCADE，而它指向的 "
                f"{'、'.join(chain)} 自己也有级联删除——删除本表的一行会跨两层继续往下删，"
                f"影响面不翻约束定义看不出来。",
                suggestion="确认这条传递删除是你想要的；若不是，把链上某一段改为 RESTRICT 或 SET NULL。",
            )
        )
    return issues


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
    # ---- 表设计层（add-table-design-rules）----
    RuleDefinition(
        code="float_money_column",
        name="金额字段用浮点类型",
        description="金额类字段使用浮点类型，无法精确表示小数，累加与比较会出现对不上账的误差。",
        default_level=custom_enum.IssueLevelEnum.HIGH,
        object_level=custom_enum.ObjectLevelEnum.COLUMN,
        handler=_check_money_float,
    ),
    RuleDefinition(
        code="long_text_primary_key",
        name="主键用长字符类型",
        description="主键列是长字符类型，会被所有引用它的外键与索引各复制一份。",
        default_level=custom_enum.IssueLevelEnum.MEDIUM,
        object_level=custom_enum.ObjectLevelEnum.COLUMN,
        handler=_check_long_text_primary_key,
        default_thresholds={"primary_key_max_length": 64},
    ),
    RuleDefinition(
        code="wide_table",
        name="宽表",
        description="列数过多，通常意味着建模没有拆开，整行读写与 SELECT * 都会变重。",
        default_level=custom_enum.IssueLevelEnum.MEDIUM,
        object_level=custom_enum.ObjectLevelEnum.TABLE,
        handler=_check_wide_table,
        default_thresholds={"wide_table_max_columns": 50},
    ),
    RuleDefinition(
        code="index_larger_than_data",
        name="索引体积超过数据",
        description="索引占用大于数据占用，说明索引过多或过宽，每一项都在拖慢写入。",
        default_level=custom_enum.IssueLevelEnum.MEDIUM,
        object_level=custom_enum.ObjectLevelEnum.TABLE,
        handler=_check_index_larger_than_data,
    ),
    RuleDefinition(
        code="cascade_delete_chain",
        name="跨表的级联删除链",
        description="删除会沿 ON DELETE CASCADE 跨两层以上传递下去，影响面不翻约束定义看不出来。",
        default_level=custom_enum.IssueLevelEnum.MEDIUM,
        object_level=custom_enum.ObjectLevelEnum.TABLE,
        handler=_check_cascade_delete_chain,
    ),
)

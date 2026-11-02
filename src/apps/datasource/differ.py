"""
快照结构差异对比

纯计算，输入两份快照的原始采集结构，输出表与列的新增/删除/变更以及索引增删。

默认按「schema.表名」精确匹配；跨 schema 比较时可用 `ignore_schema` 按表名对齐，
此时同一侧出现同名表会因对应关系不唯一而拒绝，**不悄悄合并**。
"""


class TableKeyCollision(Exception):
    """
    按表名对齐时，同一侧存在不同 schema 下的同名表，无法确定对应关系
    """

    def __init__(self, names):
        self.names = list(names)
        super().__init__("存在不同 schema 下的同名表：" + "、".join(self.names))


def diff_snapshots(base_data: dict, target_data: dict, ignore_schema: bool = False) -> dict:
    """
    对比两个快照（base 为基准，target 为对比对象）

    `ignore_schema=True` 时只按表名匹配，用于跨库/跨 schema 对比。
    """
    base_tables = _index_tables(base_data, ignore_schema)
    target_tables = _index_tables(target_data, ignore_schema)

    changed_tables = []
    for key in sorted(set(base_tables) & set(target_tables)):
        changes = _diff_table(base_tables[key], target_tables[key])
        if any(changes.values()):
            changed_tables.append({"table": key, **changes})

    return {
        "added_tables": sorted(set(target_tables) - set(base_tables)),
        "removed_tables": sorted(set(base_tables) - set(target_tables)),
        "changed_tables": changed_tables,
    }


def summarize_result(diff: dict) -> dict:
    """
    差异的紧凑计数，供快照摘要与接口响应共用
    """
    added = len(diff.get("added_tables") or [])
    removed = len(diff.get("removed_tables") or [])
    changed = len(diff.get("changed_tables") or [])
    return {
        "added_table_count": added,
        "removed_table_count": removed,
        "changed_table_count": changed,
        "has_changes": bool(added or removed or changed),
    }


def summarize_diff(base_data: dict, target_data: dict) -> dict:
    """
    同一数据源相邻快照的摘要。采集时调用，固定用 schema 精确模式
    """
    return summarize_result(diff_snapshots(base_data, target_data))


def _index_tables(data: dict, ignore_schema: bool = False) -> dict:
    tables = (data or {}).get("tables") or []
    if ignore_schema:
        _reject_duplicate_table_names(tables)
        return {table.get("name"): table for table in tables}
    return {f"{table.get('schema')}.{table.get('name')}": table for table in tables}


def _reject_duplicate_table_names(tables: list):
    """
    按表名对齐的前提是「一侧内表名唯一」；否则无法判断哪张同名表对应哪张
    """
    counts = {}
    for table in tables:
        name = table.get("name") or ""
        counts[name] = counts.get(name, 0) + 1
    duplicates = sorted(name for name, count in counts.items() if count > 1)
    if duplicates:
        raise TableKeyCollision(duplicates)


def _diff_table(base: dict, target: dict) -> dict:
    base_columns = {column["name"]: column for column in base.get("columns") or []}
    target_columns = {column["name"]: column for column in target.get("columns") or []}

    changed_columns = []
    for name in sorted(set(base_columns) & set(target_columns)):
        before, after = base_columns[name], target_columns[name]
        if _column_signature(before) != _column_signature(after):
            changed_columns.append(
                {"name": name, "before": _column_signature(before), "after": _column_signature(after)}
            )

    base_indexes = {index["name"] for index in base.get("indexes") or []}
    target_indexes = {index["name"] for index in target.get("indexes") or []}

    return {
        "added_columns": sorted(set(target_columns) - set(base_columns)),
        "removed_columns": sorted(set(base_columns) - set(target_columns)),
        "changed_columns": changed_columns,
        "added_indexes": sorted(target_indexes - base_indexes),
        "removed_indexes": sorted(base_indexes - target_indexes),
    }


def _column_signature(column: dict) -> str:
    length = column.get("length")
    length_part = "" if length in (None, -1) else f"({length})"
    nullable = "NULL" if column.get("nullable") else "NOT NULL"
    return f"{column.get('data_type')}{length_part} {nullable}"

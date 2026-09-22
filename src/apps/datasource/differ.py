"""
快照结构差异对比

纯计算，输入两份快照的原始采集结构，输出表与列的新增/删除/变更以及索引增删。
"""


def diff_snapshots(base_data: dict, target_data: dict) -> dict:
    """
    对比两个快照（base 为基准，target 为对比对象）
    """
    base_tables = _index_tables(base_data)
    target_tables = _index_tables(target_data)

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


def _index_tables(data: dict) -> dict:
    return {f"{table.get('schema')}.{table.get('name')}": table for table in (data or {}).get("tables") or []}


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

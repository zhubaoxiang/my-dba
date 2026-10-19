"""
DDL 语句级风险

与设计问题分区：设计问题回答「这次改动让表结构变差了什么」，风险回答「这条语句本身会不会失败、
会不会锁表、会影响谁」。不重复跑 SQL 分析的规则，避免同一件事报两遍。

**阻断性风险同时是合并的门槛**：编排层看到 `blocking=True` 就不会把语句合并进内存快照，
后续语句也不再评审——继续评审会基于一个不存在的状态给出看似完整的结论。
"""

import re

from sqlglot import exp

from apps.ddlreview import merge, types
from apps.sqlanalysis import services as sqlanalysis_services
from utils import custom_enum

_HIGH = custom_enum.IssueLevelEnum.HIGH
_MEDIUM = custom_enum.IssueLevelEnum.MEDIUM
_LOW = custom_enum.IssueLevelEnum.LOW

_DEFAULT_ROW_LIMIT = 10_000_000
_DEFAULT_SIZE_MB = 10240
_BYTES_PER_MB = 1024 * 1024


def _risk(code, level, description, suggestion, target="", blocking=False) -> dict:
    return {
        "code": code,
        "level": level.value,
        "level_label": level.label,
        "blocking": bool(blocking),
        "target": target,
        "description": description,
        "suggestion": suggestion,
    }


def _key(schema, name) -> tuple:
    return ((schema or "").lower(), (name or "").lower())


def _large(table, thresholds) -> bool:
    rows = int(table.get("row_count") or 0)
    size = int(table.get("total_size") or table.get("data_size") or 0)
    row_limit = int((thresholds or {}).get("big_table_rows", _DEFAULT_ROW_LIMIT))
    size_limit = int((thresholds or {}).get("big_table_size_mb", _DEFAULT_SIZE_MB)) * _BYTES_PER_MB
    return rows >= row_limit or size >= size_limit


def _pg_major(version) -> int:
    match = re.search(r"(\d+)", version or "")
    return int(match.group(1)) if match else 0


def _find_fk_target(tables, fk, owner, new_table=None):
    ref_schema = fk.get("ref_schema") or owner.get("schema") or ""
    ref_name = fk.get("ref_table") or ""
    if new_table is not None and _key(ref_schema, ref_name) == _key(new_table.get("schema"), new_table.get("name")):
        return new_table, ""
    table, error = merge._resolve(tables, ref_schema, ref_name)
    return table, error


def _check_reference(tables, fk, owner, target_label, new_table=None) -> list:
    table, error = _find_fk_target(tables, fk, owner, new_table)
    if table is None:
        return [
            _risk(
                "referenced_table_missing",
                _HIGH,
                f"外键 {fk.get('name')} 引用的表 {fk.get('ref_schema') + '.' if fk.get('ref_schema') else ''}"
                f"{fk.get('ref_table')} 不在最近一次快照中。",
                "核对目标表名；若该表确实存在，先重新采集数据源。",
                target=target_label,
                blocking=True,
            )
        ]
    missing = [column for column in fk.get("ref_columns") or () if merge._find_column(table, column) is None]
    if missing:
        return [
            _risk(
                "referenced_column_missing",
                _HIGH,
                f"外键 {fk.get('name')} 引用的列 {', '.join(missing)} 在表 {table.get('name')} 中不存在。",
                "核对目标列名；若该列确实存在，先重新采集数据源。",
                target=target_label,
                blocking=True,
            )
        ]
    return []


def _referencing_fks(tables, schema, name) -> list:
    result = []
    for other in tables:
        for foreign_key in other.get("foreign_keys") or ():
            ref_schema = foreign_key.get("ref_schema") or other.get("schema")
            if _key(ref_schema, foreign_key.get("ref_table")) == _key(schema, name):
                result.append((other, foreign_key))
    return result


def _column_dependencies(table, tables, name) -> list:
    dependencies = []
    for index in table.get("indexes") or ():
        if name in (index.get("columns") or ()):
            dependencies.append(f"索引 {index.get('name')}")
    primary_key = table.get("primary_key")
    if primary_key and name in (primary_key.get("columns") or ()):
        dependencies.append(f"主键 {primary_key.get('name')}")
    for foreign_key in table.get("foreign_keys") or ():
        if name in (foreign_key.get("columns") or ()):
            dependencies.append(f"外键 {foreign_key.get('name')}")
    for other in tables:
        for foreign_key in other.get("foreign_keys") or ():
            ref_schema = foreign_key.get("ref_schema") or other.get("schema")
            if _key(ref_schema, foreign_key.get("ref_table")) == _key(
                table.get("schema"), table.get("name")
            ) and name in (foreign_key.get("ref_columns") or ()):
                owner = "本表自引用" if other is table else other.get("name")
                dependencies.append(f"{owner}.{foreign_key.get('name')}")
    return dependencies


def _create_table_fks(expression) -> list:
    schema_node = expression.this
    if not isinstance(schema_node, exp.Schema):
        return []
    result = []
    for item in schema_node.expressions or ():
        if isinstance(item, exp.ColumnDef):
            _, _, _, foreign_keys = merge._column_spec(item)
            result.extend(foreign_keys)
        else:
            node, name = merge._constraint_node(item)
            if isinstance(node, exp.ForeignKey):
                columns = [merge._name(part) for part in node.expressions or () if isinstance(part, exp.Identifier)]
                reference = node.args.get("reference")
                if reference is not None:
                    result.append(merge._reference_spec(reference, columns, name))
    return result


def _create_table_risks(expression, tables) -> list:
    schema_node = expression.this
    if not isinstance(schema_node, exp.Schema):
        return []
    table_ref = schema_node.this
    schema = table_ref.db or "public"
    name = merge._name(table_ref)
    existing, _ = merge._resolve(tables, schema, name)
    if existing is not None:
        if expression.args.get("exists"):
            return []
        return [
            _risk(
                "target_table_exists",
                _HIGH,
                f"表 {schema}.{name} 已存在，这条 CREATE TABLE 会执行失败。",
                "确认是否要建新表；重复执行请加 IF NOT EXISTS，改已有表请改用 ALTER TABLE。",
                target=f"{schema}.{name}",
                blocking=True,
            )
        ]

    new_table = {"schema": schema, "name": name, "columns": []}
    risks = []
    for item in schema_node.expressions or ():
        if isinstance(item, exp.ColumnDef):
            column_name = merge._name(item.this)
            if any(column["name"] == column_name for column in new_table["columns"]):
                risks.append(
                    _risk(
                        "target_column_exists",
                        _HIGH,
                        f"列 {column_name} 在建表语句中重复定义，CREATE TABLE 会执行失败。",
                        "核对列定义，去掉重复项。",
                        target=f"{schema}.{name}.{column_name}",
                        blocking=True,
                    )
                )
            new_table["columns"].append({"name": column_name})
    for foreign_key in _create_table_fks(expression):
        risks.extend(_check_reference(tables, foreign_key, new_table, f"{schema}.{name}", new_table=new_table))
    return risks


def _action_foreign_keys(action) -> list:
    if isinstance(action, exp.ColumnDef):
        _, _, _, foreign_keys = merge._column_spec(action)
        return foreign_keys
    if isinstance(action, exp.AddConstraint):
        result = []
        for item in action.expressions or ():
            node, name = merge._constraint_node(item)
            if isinstance(node, exp.ForeignKey):
                columns = [merge._name(part) for part in node.expressions or () if isinstance(part, exp.Identifier)]
                reference = node.args.get("reference")
                if reference is not None:
                    result.append(merge._reference_spec(reference, columns, name))
        return result
    return []


def _alter_table_risks(expression, tables, context) -> list:
    table_ref = merge._table_ref(expression)
    if table_ref is None:
        return []
    table, error = merge._resolve(tables, table_ref.db, merge._name(table_ref))
    if table is None:
        return [
            _risk(
                "target_table_missing",
                _HIGH,
                f"{error}，这条 ALTER TABLE 会执行失败。",
                "核对表名；若该表是采集之后新建的，先重新采集数据源。",
                target=merge._name(table_ref),
                blocking=True,
            )
        ]

    risks = []
    target = f"{table.get('schema')}.{table.get('name')}"
    thresholds = (context or {}).get("thresholds") or {}
    database_version = (context or {}).get("database_version") or ""
    large = _large(table, thresholds)

    for action in expression.args.get("actions") or ():
        if isinstance(action, exp.ColumnDef):
            name = merge._name(action.this)
            column, primary, _, _ = merge._column_spec(action)
            if primary and table.get("primary_key"):
                risks.append(
                    _risk(
                        "target_constraint_exists",
                        _HIGH,
                        f"表 {table.get('name')} 已有主键，再通过 ADD COLUMN 加 PRIMARY KEY 会执行失败。",
                        "先删除原主键，或改用不带主键的列。",
                        target=f"{target}.{name}",
                        blocking=True,
                    )
                )
            if merge._find_column(table, name) is not None:
                if not action.args.get("exists"):
                    risks.append(
                        _risk(
                            "target_column_exists",
                            _HIGH,
                            f"列 {name} 已存在于表 {table.get('name')}，ADD COLUMN 会执行失败。",
                            "核对列名；要改已有列请改用 ALTER COLUMN。",
                            target=f"{target}.{name}",
                            blocking=True,
                        )
                    )
                continue
            if not column["nullable"] and not column["default"] and int(table.get("row_count") or 0) > 0:
                risks.append(
                    _risk(
                        "not_null_without_default",
                        _HIGH,
                        f"表 {table.get('name')} 已有数据，直接加 NOT NULL 且无默认值的列 {name} 会执行失败。",
                        "先加可空列并回填，再改为 NOT NULL；或提供默认值。",
                        target=f"{target}.{name}",
                        blocking=True,
                    )
                )
            if column["default"] and int(table.get("row_count") or 0) > 0 and 0 < _pg_major(database_version) < 11:
                risks.append(
                    _risk(
                        "add_column_default_rewrite",
                        _MEDIUM,
                        f"PostgreSQL 11 之前，给已有数据的表加带默认值的列会重写整张表 {table.get('name')}。",
                        "确认停机窗口；或先加可空列，回填后再设默认值。",
                        target=f"{target}.{name}",
                    )
                )
            for foreign_key in _action_foreign_keys(action):
                risks.extend(_check_reference(tables, foreign_key, table, f"{target}.{name}"))

        elif isinstance(action, exp.Drop):
            kind = str(action.args.get("kind") or "").upper()
            if kind == "COLUMN":
                names = [merge._name(item) for item in action.args.get("tables") or () if isinstance(item, exp.Column)]
                for name in names:
                    if merge._find_column(table, name) is None:
                        risks.append(
                            _risk(
                                "target_column_missing",
                                _HIGH,
                                f"列 {name} 不在表 {table.get('name')} 中，DROP COLUMN 会执行失败。",
                                "核对列名；若该列是采集之后新增的，先重新采集数据源。",
                                target=f"{target}.{name}",
                                blocking=True,
                            )
                        )
                        continue
                    dependencies = _column_dependencies(table, tables, name)
                    if dependencies:
                        risks.append(
                            _risk(
                                "drop_column_dependency",
                                _HIGH,
                                f"列 {name} 被以下对象引用：{'、'.join(dependencies)}。不带 CASCADE 的 DROP COLUMN 会执行失败。",
                                "先删除或调整依赖对象；确认可连带删除时再加 CASCADE。",
                                target=f"{target}.{name}",
                                blocking=not bool(action.args.get("cascade")),
                            )
                        )
            elif kind == "CONSTRAINT":
                names = [merge._name(item) for item in action.args.get("tables") or ()]
                for name in names:
                    known = {index.get("name") for index in table.get("indexes") or ()}
                    known.update(foreign_key.get("name") for foreign_key in table.get("foreign_keys") or ())
                    if table.get("primary_key"):
                        known.add(table["primary_key"].get("name"))
                    if name and name not in known:
                        risks.append(
                            _risk(
                                "target_constraint_missing",
                                _HIGH,
                                f"约束 {name} 不在表 {table.get('name')} 中，DROP CONSTRAINT 会执行失败。",
                                "核对约束名；若约束是采集之后新增的，先重新采集数据源。",
                                target=f"{target}.{name}",
                                blocking=True,
                            )
                        )

        elif isinstance(action, exp.AlterColumn):
            name = merge._name(action.this)
            column = merge._find_column(table, name)
            if column is None:
                risks.append(
                    _risk(
                        "target_column_missing",
                        _HIGH,
                        f"列 {name} 不在表 {table.get('name')} 中，ALTER COLUMN 会执行失败。",
                        "核对列名；若该列是采集之后新增的，先重新采集数据源。",
                        target=f"{target}.{name}",
                        blocking=True,
                    )
                )
                continue
            dtype = action.args.get("dtype")
            if dtype is not None:
                new_type, new_length = types.normalize_type(dtype)
                old_type = column.get("data_type") or ""
                old_length = int(column.get("length") or -1)
                old_family = sqlanalysis_services.type_family(old_type)
                new_family = sqlanalysis_services.type_family(new_type)
                reason = ""
                if old_family != new_family and "unknown" not in (old_family, new_family):
                    reason = f"类型族从 {old_family} 变为 {new_family}"
                elif old_length != -1 and new_length != -1 and new_length < old_length:
                    reason = f"字符长度从 {old_length} 收窄到 {new_length}"
                if reason:
                    risks.append(
                        _risk(
                            "type_narrowing",
                            _MEDIUM,
                            f"列 {name} 的{reason}，现有数据可能无法转换或会丢失精度。",
                            "执行前先核对现有数据；跨族转换确认 USING 表达式能处理全部行。",
                            target=f"{target}.{name}",
                        )
                    )

        elif isinstance(action, exp.AddConstraint):
            for foreign_key in _action_foreign_keys(action):
                risks.extend(_check_reference(tables, foreign_key, table, target))
                if large and not bool(expression.args.get("not_valid")):
                    risks.append(
                        _risk(
                            "add_fk_scan",
                            _MEDIUM,
                            f"表 {table.get('name')} 已达到大表体量，ADD FOREIGN KEY 会扫描全表并持锁。",
                            "先加 NOT VALID，再在低峰期 VALIDATE CONSTRAINT。",
                            target=target,
                        )
                    )
            for item in action.expressions or ():
                node, _ = merge._constraint_node(item)
                if isinstance(node, exp.PrimaryKey) and table.get("primary_key"):
                    risks.append(
                        _risk(
                            "target_constraint_exists",
                            _HIGH,
                            f"表 {table.get('name')} 已有主键，再 ADD PRIMARY KEY 会执行失败。",
                            "先删除原主键，或改用其他列。",
                            target=target,
                            blocking=True,
                        )
                    )

        elif isinstance(action, exp.RenameColumn):
            new_name = merge._name(action.args.get("to"))
            if new_name and merge._find_column(table, new_name) is not None:
                risks.append(
                    _risk(
                        "target_column_exists",
                        _HIGH,
                        f"列 {new_name} 已存在，RENAME COLUMN 会执行失败。",
                        "换一个列名。",
                        target=f"{target}.{new_name}",
                        blocking=True,
                    )
                )
            risks.append(
                _risk(
                    "rename_breaks_code",
                    _LOW,
                    f"列 {merge._name(action.this)} 改名后，引用旧列名的应用代码与查询会失效。",
                    "先搜索代码与报表中的旧列名，确认可以同步修改。",
                    target=target,
                )
            )

        elif isinstance(action, exp.AlterRename):
            new_ref = action.this
            new_schema = (new_ref.db if isinstance(new_ref, exp.Table) else "") or table.get("schema") or ""
            new_name = merge._name(new_ref)
            existing, _ = merge._resolve(tables, new_schema, new_name)
            if existing is not None and existing is not table:
                risks.append(
                    _risk(
                        "target_table_exists",
                        _HIGH,
                        f"表 {new_schema}.{new_name} 已存在，RENAME 会执行失败。",
                        "换一个表名。",
                        target=f"{new_schema}.{new_name}",
                        blocking=True,
                    )
                )
            risks.append(
                _risk(
                    "rename_breaks_code",
                    _LOW,
                    f"表 {table.get('name')} 改名后，引用旧表名的应用代码与查询会失效。",
                    "先搜索代码与报表中的旧表名，确认可以同步修改。",
                    target=target,
                )
            )
    return risks


def _plain_index_columns(index) -> list:
    """
    索引里的裸列名；表达式列（如 `lower(name)`）不是表列，不能拿去做「列是否存在」检查
    """
    params = index.args.get("params") if isinstance(index, exp.Index) else None
    if params is None:
        return []
    result = []
    for item in params.args.get("columns") or ():
        node = item.this if isinstance(item, exp.Ordered) else item
        if isinstance(node, exp.Column):
            result.append(merge._name(node))
    return result


def _create_index_risks(expression, tables, context) -> list:
    index = expression.this
    table_ref = index.args.get("table") if isinstance(index, exp.Index) else None
    table, error = merge._resolve(tables, table_ref.db if table_ref else "", merge._name(table_ref))
    if table is None:
        return [
            _risk(
                "target_table_missing",
                _HIGH,
                f"{error}，这条 CREATE INDEX 会执行失败。",
                "核对表名；若该表是采集之后新建的，先重新采集数据源。",
                target=merge._name(table_ref) if table_ref else "",
                blocking=True,
            )
        ]
    risks = []
    name = merge._name(index.this)
    schema = table.get("schema") or ""
    if any(
        other.get("schema") == schema and any(item.get("name") == name for item in other.get("indexes") or ())
        for other in tables
    ) and not expression.args.get("exists"):
        risks.append(
            _risk(
                "target_index_exists",
                _HIGH,
                f"索引 {name} 已存在，CREATE INDEX 会执行失败。",
                "换一个索引名，或先删除旧索引；重复执行请加 IF NOT EXISTS。",
                target=name,
                blocking=True,
            )
        )
    for column in _plain_index_columns(index):
        if merge._find_column(table, column) is None:
            risks.append(
                _risk(
                    "target_column_missing",
                    _HIGH,
                    f"索引列 {column} 不在表 {table.get('name')} 中，CREATE INDEX 会执行失败。",
                    "核对列名；若该列是采集之后新增的，先重新采集数据源。",
                    target=f"{schema}.{table.get('name')}.{column}",
                    blocking=True,
                )
            )
    if _large(table, (context or {}).get("thresholds") or {}) and not bool(expression.args.get("concurrently")):
        risks.append(
            _risk(
                "create_index_lock",
                _MEDIUM,
                f"表 {table.get('name')} 已达到大表体量，普通 CREATE INDEX 会阻塞写入。",
                "改用 CREATE INDEX CONCURRENTLY；它不能放在事务里。",
                target=f"{schema}.{table.get('name')}",
            )
        )
    return risks


def _drop_table_risks(expression, tables) -> list:
    risks = []
    for table_ref in expression.args.get("tables") or ():
        table, error = merge._resolve(tables, table_ref.db, merge._name(table_ref))
        if table is None:
            if expression.args.get("exists"):
                continue
            risks.append(
                _risk(
                    "target_table_missing",
                    _HIGH,
                    f"{error}，这条 DROP TABLE 会执行失败。",
                    "核对表名；若该表是采集之后新建的，先重新采集数据源。",
                    target=merge._name(table_ref),
                    blocking=True,
                )
            )
            continue
        dependencies = _referencing_fks(tables, table.get("schema"), table.get("name"))
        if dependencies:
            names = "、".join(f"{other.get('name')}.{foreign_key.get('name')}" for other, foreign_key in dependencies)
            cascade = bool(expression.args.get("cascade"))
            risks.append(
                _risk(
                    "drop_table_dependency",
                    _HIGH,
                    f"表 {table.get('name')} 被以下外键引用：{names}。"
                    + ("CASCADE 会连带删除这些引用约束。" if cascade else "不带 CASCADE 的 DROP TABLE 会执行失败。"),
                    "先确认这些依赖是否可以一并删除；不可以则先处理引用方。",
                    target=f"{table.get('schema')}.{table.get('name')}",
                    blocking=not cascade,
                )
            )
    return risks


def _truncate_risks(expression, tables) -> list:
    risks = []
    for table_ref in expression.args.get("expressions") or ():
        table, error = merge._resolve(tables, table_ref.db, merge._name(table_ref))
        if table is None:
            risks.append(
                _risk(
                    "target_table_missing",
                    _HIGH,
                    f"{error}，这条 TRUNCATE 会执行失败。",
                    "核对表名；若该表是采集之后新建的，先重新采集数据源。",
                    target=merge._name(table_ref),
                    blocking=True,
                )
            )
            continue
        dependencies = _referencing_fks(tables, table.get("schema"), table.get("name"))
        if dependencies:
            names = "、".join(f"{other.get('name')}.{foreign_key.get('name')}" for other, foreign_key in dependencies)
            cascade = str(expression.args.get("option") or "").upper() == "CASCADE"
            risks.append(
                _risk(
                    "truncate_dependency",
                    _HIGH,
                    f"表 {table.get('name')} 被以下外键引用：{names}。"
                    + ("CASCADE 会连带清空这些表。" if cascade else "不带 CASCADE 的 TRUNCATE 会执行失败。"),
                    "确认影响范围；清空主表前先处理引用方。",
                    target=f"{table.get('schema')}.{table.get('name')}",
                    blocking=not cascade,
                )
            )
    return risks


def _drop_index_risks(expression, tables) -> list:
    targets = expression.args.get("tables") or ()
    target = targets[0] if targets else None
    if target is None:
        return []
    name = merge._name(target)
    matches = merge._find_index(tables, getattr(target, "db", "") or "", name)
    if not matches:
        if expression.args.get("exists"):
            return []
        return [
            _risk(
                "target_index_missing",
                _HIGH,
                f"索引 {name} 不存在，DROP INDEX 会执行失败。",
                "核对索引名；若索引是采集之后新建的，先重新采集数据源。",
                target=name,
                blocking=True,
            )
        ]
    risks = []
    for table, index in matches:
        if index.get("primary"):
            risks.append(
                _risk(
                    "drop_index_constraint_backed",
                    _HIGH,
                    f"索引 {name} 支撑表 {table.get('name')} 的主键约束，直接 DROP INDEX 会执行失败。",
                    "要移除主键请使用 ALTER TABLE ... DROP CONSTRAINT。",
                    target=name,
                    blocking=True,
                )
            )
        elif index.get("unique"):
            risks.append(
                _risk(
                    "drop_index_constraint_backed",
                    _MEDIUM,
                    f"索引 {name} 是唯一索引，可能由唯一约束创建；直接删除可能失败。",
                    "确认它是否由 UNIQUE 约束创建；是则使用 ALTER TABLE ... DROP CONSTRAINT。",
                    target=name,
                )
            )
    return risks


def evaluate(statement, tables, context=None) -> list:
    """
    对一条已解析的语句给出语句风险

    `tables` 是应用这条语句**之前**的内存表结构；`context` 可带
    `database_version` 与 `thresholds`（复用 `big_table` 的阈值）。
    """
    expression = statement.expression
    if isinstance(expression, exp.Create):
        kind = str(expression.args.get("kind") or "").upper()
        if kind == "TABLE":
            return _create_table_risks(expression, tables)
        if kind == "INDEX":
            return _create_index_risks(expression, tables, context)
    if isinstance(expression, exp.Alter) and str(expression.args.get("kind") or "").upper() == "TABLE":
        return _alter_table_risks(expression, tables, context)
    if isinstance(expression, exp.Drop):
        kind = str(expression.args.get("kind") or "").upper()
        if kind == "TABLE":
            return _drop_table_risks(expression, tables)
        if kind == "INDEX":
            return _drop_index_risks(expression, tables)
    if isinstance(expression, exp.TruncateTable):
        return _truncate_risks(expression, tables)
    return []

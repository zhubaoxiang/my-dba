"""
把解析后的 DDL 合并进内存表结构

输入是快照形态的 `tables` 列表（会被原地修改），输出应用结果。
合并是**顺序语义**：后一条语句看到的是前一条应用后的状态。

这里只模拟「语句成功执行后结构会变成什么」；会不会执行失败由 `risks.py` 判定，
编排层在阻断性风险出现时不会调用本模块继续合并。
"""

import copy

from sqlglot import exp

from apps.ddlreview import types

_NO_ACTION = "NO ACTION"
_ACTIONS = ("NO ACTION", "SET DEFAULT", "SET NULL", "CASCADE", "RESTRICT")


def _ok(noop=False, summary=None) -> dict:
    return {"applied": True, "error": "", "noop": noop, "change_summary": summary or {}}


def _fail(error: str) -> dict:
    return {"applied": False, "error": error, "noop": False, "change_summary": {}}


def _key(schema, name) -> tuple:
    return ((schema or "").lower(), (name or "").lower())


def _name(node) -> str:
    return (getattr(node, "name", "") or "").strip()


def _table_ref(node):
    """
    取语句的目标表。`CREATE TABLE` 的目标在 `Schema.this` 里，其余多在 `this` 或列表里
    """
    target = node.args.get("this")
    if isinstance(target, exp.Schema):
        target = target.this
    if isinstance(target, exp.Table):
        return target
    for key in ("tables", "expressions"):
        for item in node.args.get(key) or ():
            if isinstance(item, exp.Table):
                return item
    return None


def _resolve(tables, schema, name, default_schema="public"):
    """
    按 schema + 表名找表；未限定 schema 时唯一匹配，多个同名时优先 public，仍有歧义则报错

    返回 `(table, error)`。
    """
    wanted = (name or "").lower()
    if not wanted:
        return None, "语句没有给出表名"
    if schema:
        for table in tables:
            if _key(table.get("schema"), table.get("name")) == _key(schema, name):
                return table, ""
        return None, f"表 {schema}.{name} 不在最近一次采集的快照中"

    matches = [table for table in tables if (table.get("name") or "").lower() == wanted]
    if not matches:
        return None, f"表 {name} 不在最近一次采集的快照中"
    if len(matches) == 1:
        return matches[0], ""
    public = [table for table in matches if (table.get("schema") or "").lower() == (default_schema or "").lower()]
    if len(public) == 1:
        return public[0], ""
    return None, f"表 {name} 在多个模式中存在，请显式指定模式"


def _find_column(table, name):
    wanted = (name or "").lower()
    for column in table.get("columns") or ():
        if (column.get("name") or "").lower() == wanted:
            return column
    return None


def _new_table(schema, name) -> dict:
    return {
        "schema": schema,
        "name": name,
        "comment": "",
        "row_count": 0,
        "row_count_exact": False,
        "data_size": 0,
        "index_size": 0,
        "total_size": 0,
        "columns": [],
        "indexes": [],
        "primary_key": None,
        "foreign_keys": [],
    }


def _add_index(table, name, columns, unique=False, primary=False):
    if not name or not columns:
        return
    if any(index.get("name") == name for index in table.get("indexes") or ()):
        return
    table.setdefault("indexes", []).append(
        {
            "name": name,
            "columns": list(columns),
            "unique": bool(unique),
            "primary": bool(primary),
            "index_type": "btree",
        }
    )


def _ensure_primary_key(table, columns, name="") -> tuple:
    columns = [column for column in columns if column]
    if not columns:
        return False, "主键没有列"
    if table.get("primary_key"):
        return False, f"表 {table['name']} 已有主键"
    name = name or f"pk_{table['name']}"
    table["primary_key"] = {"name": name, "columns": list(columns)}
    for column in table.get("columns") or ():
        if column.get("name") in columns:
            column["nullable"] = False
    _add_index(table, name, columns, unique=True, primary=True)
    return True, ""


def _action_of(options, keyword) -> str:
    """
    从 `Reference.options` 里取 `ON DELETE` / `ON UPDATE` 后面的动作
    """
    text = " ".join(options or ()).upper()
    marker = f"{keyword} "
    position = text.find(marker)
    if position < 0:
        return ""
    rest = text[position + len(marker) :]
    for action in _ACTIONS:
        if rest.startswith(action):
            return action
    return ""


def _reference_spec(reference, columns, constraint_name="") -> dict:
    target = reference.this if isinstance(reference, exp.Reference) else reference
    if isinstance(target, exp.Schema):
        table = target.this
        ref_columns = [_name(item) for item in target.expressions or () if isinstance(item, exp.Identifier)]
    else:
        table = target
        ref_columns = []
    options = reference.args.get("options") if isinstance(reference, exp.Reference) else []
    name = constraint_name or f"fk_{_name(table)}"
    return {
        "name": name,
        "columns": list(columns),
        "ref_schema": table.db or "",
        "ref_table": _name(table),
        "ref_columns": ref_columns,
        "on_delete": _action_of(options, "ON DELETE") or _NO_ACTION,
        "on_update": _action_of(options, "ON UPDATE") or _NO_ACTION,
    }


def _column_names(node) -> list:
    """
    从 `Schema` / 标识符列表里取列名
    """
    if isinstance(node, exp.Schema):
        items = node.expressions or ()
    elif isinstance(node, list | tuple):
        items = node
    else:
        items = getattr(node, "expressions", None) or ()
    return [_name(item) for item in items if isinstance(item, exp.Identifier | exp.Column)]


def _column_spec(coldef) -> tuple:
    """
    列定义 → `(列 dict, 是否主键, 是否唯一, 外键列表)`
    """
    name = _name(coldef.this)
    data_type, length = types.normalize_type(coldef.args.get("kind"))
    nullable = True
    default = ""
    primary = False
    unique = False
    foreign_keys = []
    for constraint in coldef.args.get("constraints") or ():
        kind = constraint.args.get("kind")
        if isinstance(kind, exp.NotNullColumnConstraint):
            nullable = False
        elif isinstance(kind, exp.PrimaryKeyColumnConstraint):
            primary = True
            nullable = False
        elif isinstance(kind, exp.DefaultColumnConstraint):
            default = kind.this.sql(dialect="postgres") if kind.this is not None else ""
        elif isinstance(kind, exp.UniqueColumnConstraint):
            unique = True
        elif isinstance(kind, exp.Reference):
            foreign_keys.append(_reference_spec(kind, [name]))
    return (
        {
            "name": name,
            "data_type": data_type,
            "length": length,
            "nullable": nullable,
            "default": default,
            "comment": "",
        },
        primary,
        unique,
        foreign_keys,
    )


def _index_columns(index) -> list:
    params = index.args.get("params") if isinstance(index, exp.Index) else None
    if params is None:
        return []
    result = []
    for item in params.args.get("columns") or ():
        node = item.this if isinstance(item, exp.Ordered) else item
        if isinstance(node, exp.Column):
            result.append(_name(node))
        else:
            result.append(node.sql(dialect="postgres"))
    return result


def _constraint_node(item) -> tuple:
    """
    表级约束可能被 `CONSTRAINT name` 包一层，取 `(约束节点, 约束名)`
    """
    if isinstance(item, exp.Constraint):
        inner = item.expressions or []
        name = _name(item.this) if isinstance(item.this, exp.Identifier) else ""
        return (inner[0] if len(inner) == 1 else None), name
    return item, ""


def _apply_table_constraint(table, item) -> tuple:
    node, name = _constraint_node(item)
    if node is None:
        return False, "无法解析表级约束"
    if isinstance(node, exp.PrimaryKey):
        return _ensure_primary_key(table, _column_names(node.expressions), name)
    if isinstance(node, exp.UniqueColumnConstraint):
        columns = _column_names(node.this)
        _add_index(table, name or f"uk_{table['name']}_{'_'.join(columns)}", columns, unique=True)
        return True, ""
    if isinstance(node, exp.ForeignKey):
        columns = [_name(item) for item in node.expressions or () if isinstance(item, exp.Identifier)]
        reference = node.args.get("reference")
        if reference is None:
            return False, "外键没有目标表"
        table.setdefault("foreign_keys", []).append(_reference_spec(reference, columns, name))
        return True, ""
    if isinstance(node, exp.CheckColumnConstraint):
        return True, ""
    return False, f"暂不支持表级约束 {type(node).__name__}"


def _create_table(tables, expression) -> dict:
    schema_node = expression.this
    if not isinstance(schema_node, exp.Schema):
        return _fail("CREATE TABLE AS / LIKE / INHERITS / PARTITION OF 暂不支持")

    table_ref = schema_node.this
    schema = table_ref.db or "public"
    name = _name(table_ref)
    existing, _ = _resolve(tables, schema, name)
    if existing is not None:
        if expression.args.get("exists"):
            return _ok(noop=True)
        return _fail(f"表 {schema}.{name} 已存在")

    table = _new_table(schema, name)
    primary_columns = []
    summary = {"tables_added": [name], "columns_added": [], "indexes_added": [], "constraints_added": []}

    for item in schema_node.expressions or ():
        if isinstance(item, exp.ColumnDef):
            column, primary, unique, foreign_keys = _column_spec(item)
            if _find_column(table, column["name"]) is not None:
                return _fail(f"列 {column['name']} 重复定义")
            column["ordinal"] = len(table["columns"]) + 1
            table["columns"].append(column)
            summary["columns_added"].append(column["name"])
            if primary:
                primary_columns.append(column["name"])
            if unique:
                index_name = f"uk_{name}_{column['name']}"
                _add_index(table, index_name, [column["name"]], unique=True)
                summary["indexes_added"].append(index_name)
            for foreign_key in foreign_keys:
                table["foreign_keys"].append(foreign_key)
                summary["constraints_added"].append(foreign_key["name"])
        else:
            applied, error = _apply_table_constraint(table, item)
            if not applied:
                return _fail(error)

    if primary_columns:
        applied, error = _ensure_primary_key(table, primary_columns)
        if not applied:
            return _fail(error)
        summary["constraints_added"].append(table["primary_key"]["name"])

    # 表级主键可能写在列定义之前；统一在列都齐了之后补 NOT NULL
    if table.get("primary_key"):
        for column in table["columns"]:
            if column["name"] in (table["primary_key"].get("columns") or ()):
                column["nullable"] = False

    tables.append(table)
    return _ok(summary=summary)


def _add_column(table, action) -> tuple:
    column, primary, unique, foreign_keys = _column_spec(action)
    if _find_column(table, column["name"]) is not None:
        if action.args.get("exists"):
            return True, ""
        return False, f"列 {column['name']} 已存在"
    column["ordinal"] = len(table["columns"]) + 1
    table["columns"].append(column)
    if primary:
        applied, error = _ensure_primary_key(table, [column["name"]])
        if not applied:
            return False, error
    if unique:
        _add_index(table, f"uk_{table['name']}_{column['name']}", [column["name"]], unique=True)
    for foreign_key in foreign_keys:
        table.setdefault("foreign_keys", []).append(foreign_key)
    return True, ""


def _drop_column(table, action) -> tuple:
    names = [_name(item) for item in action.args.get("tables") or () if isinstance(item, exp.Column)]
    if not names:
        return False, "没有给出要删除的列"
    name = names[0]
    if _find_column(table, name) is None:
        return False, f"列 {name} 不存在"
    cascade = bool(action.args.get("cascade"))

    table["columns"] = [column for column in table["columns"] if column["name"] != name]

    for index in list(table.get("indexes") or ()):
        if name in (index.get("columns") or ()):
            index["columns"] = [column for column in index["columns"] if column != name]
            if not index["columns"]:
                table["indexes"].remove(index)
                if index.get("primary"):
                    table["primary_key"] = None

    primary_key = table.get("primary_key")
    if primary_key and name in (primary_key.get("columns") or ()):
        table["primary_key"] = None
        table["indexes"] = [index for index in table["indexes"] if not index.get("primary")]

    table["foreign_keys"] = [
        foreign_key
        for foreign_key in table.get("foreign_keys") or ()
        if name not in (foreign_key.get("columns") or ())
        and not (
            _key(foreign_key.get("ref_schema") or table.get("schema"), foreign_key.get("ref_table"))
            == _key(table.get("schema"), table.get("name"))
            and name in (foreign_key.get("ref_columns") or ())
        )
    ]

    # 其他表引用本表这一列：只有 CASCADE 才会连带删掉引用约束
    if cascade:
        for other in table.get("_all_tables") or ():
            if other is table:
                continue
            other["foreign_keys"] = [
                foreign_key
                for foreign_key in other.get("foreign_keys") or ()
                if not (
                    _key(foreign_key.get("ref_schema") or other.get("schema"), foreign_key.get("ref_table"))
                    == _key(table.get("schema"), table.get("name"))
                    and name in (foreign_key.get("ref_columns") or ())
                )
            ]
    return True, ""


def _drop_constraint(table, action) -> tuple:
    targets = action.args.get("tables") or ()
    name = _name(targets[0]) if targets else ""
    if not name:
        return False, "没有给出约束名"

    primary_key = table.get("primary_key")
    if primary_key and primary_key.get("name") == name:
        table["primary_key"] = None
        table["indexes"] = [index for index in table["indexes"] if index.get("name") != name]
        return True, ""

    foreign_keys = table.get("foreign_keys") or ()
    remaining = [foreign_key for foreign_key in foreign_keys if foreign_key.get("name") != name]
    if len(remaining) != len(foreign_keys):
        table["foreign_keys"] = remaining
        return True, ""

    indexes = table.get("indexes") or ()
    remaining_indexes = [index for index in indexes if index.get("name") != name]
    if len(remaining_indexes) != len(indexes):
        table["indexes"] = remaining_indexes
        return True, ""

    return False, f"约束 {name} 不存在"


def _alter_column(table, action) -> tuple:
    name = _name(action.this)
    column = _find_column(table, name)
    if column is None:
        return False, f"列 {name} 不存在"

    data_type = action.args.get("dtype")
    if data_type is not None:
        column["data_type"], column["length"] = types.normalize_type(data_type)

    allow_null = action.args.get("allow_null")
    if allow_null is False:
        column["nullable"] = False
    elif allow_null is True:
        column["nullable"] = True

    default = action.args.get("default")
    if default is not None:
        column["default"] = default.sql(dialect="postgres")
    elif action.args.get("drop"):
        column["default"] = ""
    return True, ""


def _add_constraint(table, action) -> tuple:
    for item in action.expressions or ():
        node, name = _constraint_node(item)
        if node is None:
            return False, "无法解析约束"
        if isinstance(node, exp.PrimaryKey):
            applied, error = _ensure_primary_key(table, _column_names(node.expressions), name)
            if not applied:
                return False, error
        elif isinstance(node, exp.UniqueColumnConstraint):
            columns = _column_names(node.this)
            _add_index(table, name or f"uk_{table['name']}_{'_'.join(columns)}", columns, unique=True)
        elif isinstance(node, exp.ForeignKey):
            columns = [_name(item) for item in node.expressions or () if isinstance(item, exp.Identifier)]
            reference = node.args.get("reference")
            if reference is None:
                return False, "外键没有目标表"
            table.setdefault("foreign_keys", []).append(_reference_spec(reference, columns, name))
        elif isinstance(node, exp.CheckColumnConstraint):
            continue
        else:
            return False, f"暂不支持约束 {type(node).__name__}"
    return True, ""


def _rename_column(table, action, tables) -> tuple:
    old = _name(action.this)
    to = action.args.get("to")
    new = _name(to)
    if not old or not new:
        return False, "改列名缺少原列名或新列名"
    column = _find_column(table, old)
    if column is None:
        return False, f"列 {old} 不存在"
    if _find_column(table, new) is not None:
        return False, f"列 {new} 已存在"
    column["name"] = new
    for index in table.get("indexes") or ():
        index["columns"] = [new if column_name == old else column_name for column_name in index["columns"]]
    primary_key = table.get("primary_key")
    if primary_key:
        primary_key["columns"] = [new if column_name == old else column_name for column_name in primary_key["columns"]]
    for foreign_key in table.get("foreign_keys") or ():
        foreign_key["columns"] = [new if column_name == old else column_name for column_name in foreign_key["columns"]]

    for other in tables:
        for foreign_key in other.get("foreign_keys") or ():
            if _key(foreign_key.get("ref_schema") or other.get("schema"), foreign_key.get("ref_table")) == _key(
                table.get("schema"), table.get("name")
            ) and old in (foreign_key.get("ref_columns") or ()):
                foreign_key["ref_columns"] = [
                    new if column_name == old else column_name for column_name in foreign_key["ref_columns"]
                ]
    return True, ""


def _rename_table(table, action, tables) -> tuple:
    target = action.this
    if not isinstance(target, exp.Table):
        return False, "无法解析新的表名"
    new_schema = target.db or table.get("schema") or ""
    new_name = _name(target)
    existing, _ = _resolve(tables, new_schema, new_name)
    if existing is not None and existing is not table:
        return False, f"表 {new_schema}.{new_name} 已存在"
    old_key = _key(table.get("schema"), table.get("name"))
    table["schema"] = new_schema
    table["name"] = new_name
    for other in tables:
        for foreign_key in other.get("foreign_keys") or ():
            source = _key(foreign_key.get("ref_schema") or other.get("schema"), foreign_key.get("ref_table"))
            if source == old_key:
                foreign_key["ref_schema"] = new_schema
                foreign_key["ref_table"] = new_name
    return True, ""


def _alter_table(tables, expression) -> dict:
    table_ref = _table_ref(expression)
    if table_ref is None:
        return _fail("ALTER TABLE 没有给出目标表")
    table, error = _resolve(tables, table_ref.db, _name(table_ref))
    if table is None:
        return _fail(error)

    table["_all_tables"] = tables
    summary = {
        "columns_added": [],
        "columns_dropped": [],
        "columns_altered": [],
        "columns_renamed": [],
        "indexes_added": [],
        "indexes_dropped": [],
        "constraints_added": [],
        "constraints_dropped": [],
        "tables_renamed": [],
    }
    try:
        for action in expression.args.get("actions") or ():
            if isinstance(action, exp.ColumnDef):
                applied, error = _add_column(table, action)
                if applied:
                    summary["columns_added"].append(_name(action.this))
            elif isinstance(action, exp.Drop):
                kind = str(action.args.get("kind") or "").upper()
                if kind == "COLUMN":
                    names = [_name(item) for item in action.args.get("tables") or () if isinstance(item, exp.Column)]
                    applied, error = _drop_column(table, action)
                    if applied:
                        summary["columns_dropped"].extend(names)
                elif kind == "CONSTRAINT":
                    targets = action.args.get("tables") or ()
                    applied, error = _drop_constraint(table, action)
                    if applied:
                        summary["constraints_dropped"].append(_name(targets[0]) if targets else "")
                else:
                    applied, error = False, f"ALTER 动作 DROP {kind} 暂不支持"
            elif isinstance(action, exp.AlterColumn):
                applied, error = _alter_column(table, action)
                if applied:
                    summary["columns_altered"].append(_name(action.this))
            elif isinstance(action, exp.AddConstraint):
                applied, error = _add_constraint(table, action)
            elif isinstance(action, exp.RenameColumn):
                old = _name(action.this)
                applied, error = _rename_column(table, action, tables)
                if applied:
                    summary["columns_renamed"].append(
                        {
                            "from": old,
                            "to": _name(action.args.get("to")),
                            "schema": table.get("schema"),
                            "table": table.get("name"),
                        }
                    )
            elif isinstance(action, exp.AlterRename):
                old = table.get("name")
                old_schema = table.get("schema")
                applied, error = _rename_table(table, action, tables)
                if applied:
                    summary["tables_renamed"].append(
                        {
                            "from": old,
                            "to": table.get("name"),
                            "schema": old_schema,
                            "new_schema": table.get("schema"),
                        }
                    )
            else:
                applied, error = False, f"ALTER 动作 {type(action).__name__} 暂不支持"

            if not applied:
                return _fail(error)
    finally:
        table.pop("_all_tables", None)
    return _ok(summary=summary)


def _create_index(tables, expression) -> dict:
    index = expression.this
    if not isinstance(index, exp.Index):
        return _fail("无法解析 CREATE INDEX")
    table_ref = index.args.get("table")
    table, error = _resolve(tables, table_ref.db if table_ref else "", _name(table_ref))
    if table is None:
        return _fail(error)

    name = _name(index.this)
    if not name:
        return _fail("索引没有名字")
    schema = table.get("schema") or ""
    if any(
        other.get("schema") == schema and any(item.get("name") == name for item in other.get("indexes") or ())
        for other in tables
    ):
        if expression.args.get("exists"):
            return _ok(noop=True)
        return _fail(f"索引 {name} 已存在")

    columns = _index_columns(index)
    if not columns:
        return _fail("索引没有列")
    unique = bool(expression.args.get("unique"))
    _add_index(table, name, columns, unique=unique)
    return _ok(summary={"indexes_added": [name]})


def _find_index(tables, schema, name):
    matches = []
    for table in tables:
        if schema and (table.get("schema") or "").lower() != schema.lower():
            continue
        for index in table.get("indexes") or ():
            if index.get("name") == name:
                matches.append((table, index))
    return matches


def _drop_index(tables, expression) -> dict:
    targets = expression.args.get("tables") or ()
    target = targets[0] if targets else None
    if target is None:
        return _fail("DROP INDEX 没有给出索引名")
    name = _name(target)
    matches = _find_index(tables, getattr(target, "db", "") or "", name)
    if not matches:
        if expression.args.get("exists"):
            return _ok(noop=True)
        return _fail(f"索引 {name} 不存在")
    if len(matches) > 1:
        return _fail(f"索引 {name} 在多个模式下存在，请显式指定模式")
    table, index = matches[0]
    table["indexes"] = [item for item in table["indexes"] if item is not index]
    if index.get("primary"):
        table["primary_key"] = None
    return _ok(summary={"indexes_dropped": [name]})


def _drop_table(tables, expression) -> dict:
    dropped = []
    for target in expression.args.get("tables") or ():
        table, error = _resolve(tables, getattr(target, "db", "") or "", _name(target))
        if table is None:
            if expression.args.get("exists"):
                continue
            return _fail(error)
        tables.remove(table)
        dropped.append(_name(target))
        if expression.args.get("cascade"):
            for other in tables:
                other["foreign_keys"] = [
                    foreign_key
                    for foreign_key in other.get("foreign_keys") or ()
                    if _key(foreign_key.get("ref_schema") or other.get("schema"), foreign_key.get("ref_table"))
                    != _key(table.get("schema"), table.get("name"))
                ]
    if not dropped and expression.args.get("exists"):
        return _ok(noop=True)
    return _ok(summary={"tables_dropped": dropped})


def _truncate(tables, expression) -> dict:
    for target in expression.args.get("expressions") or ():
        table, error = _resolve(tables, getattr(target, "db", "") or "", _name(target))
        if table is None:
            return _fail(error)
    return _ok()


def apply(tables, expression) -> dict:
    """
    把一条语句合并进 `tables`（原地修改）

    返回 `{applied, error, noop, change_summary}`；`applied=False` 时调用方应停止后续语句。
    """
    if isinstance(expression, exp.Create):
        kind = str(expression.args.get("kind") or "").upper()
        if kind == "TABLE":
            return _create_table(tables, expression)
        if kind == "INDEX":
            return _create_index(tables, expression)
    if isinstance(expression, exp.Alter):
        return _alter_table(tables, expression)
    if isinstance(expression, exp.Drop):
        kind = str(expression.args.get("kind") or "").upper()
        if kind == "TABLE":
            return _drop_table(tables, expression)
        if kind == "INDEX":
            return _drop_index(tables, expression)
    if isinstance(expression, exp.TruncateTable):
        return _truncate(tables, expression)
    if isinstance(expression, exp.Comment):
        return _ok(noop=True)
    return _fail(f"暂不支持该语句 {type(expression).__name__}")


def copy_tables(tables) -> list:
    """深拷贝快照表结构，避免评审改动调用方的数据"""
    return copy.deepcopy(list(tables or ()))

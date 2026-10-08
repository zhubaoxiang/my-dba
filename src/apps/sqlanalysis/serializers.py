"""
SQL 分析接口的入参校验

分析不落库，因此这里没有 `ModelSerializer`——只有几个普通 `Serializer`。
"""

from rest_framework import serializers

from utils import custom_enum

# SQL 文本上限：一次分析要解析、做规则判定，还要交给模型解读，
# 过长既拖慢响应，也容易被模型的上下文窗口截断
MAX_SQL_LENGTH = 20000


class SqlRuleSerializer(serializers.Serializer):
    """
    SQL 规则清单的一项

    数据来自代码声明而非模型：SQL 规则没有覆盖机制、也不落库，因此这里只有只读字段。
    """

    code = serializers.CharField()
    name = serializers.CharField()
    description = serializers.CharField()
    level = serializers.IntegerField()
    level_label = serializers.CharField()
    needs_schema = serializers.BooleanField()


class SqlTextSerializer(serializers.Serializer):
    """
    只带 SQL 文本与方言的入参（格式化用它）

    格式化**不需要数据源**：只改排版，不校验表结构、也不连库。
    """

    sql = serializers.CharField(max_length=MAX_SQL_LENGTH)
    # 方言缺省时按数据源的库类型推断，再缺省则用 PostgreSQL
    dialect = serializers.ChoiceField(choices=custom_enum.DbTypeEnum.choices, required=False)

    def validate_sql(self, value):
        value = value.strip()
        if not value:
            raise serializers.ValidationError("SQL 不能为空")
        return value


class SqlAnalyzeSerializer(SqlTextSerializer):
    """
    分析入参

    比格式化多一个可选的数据源：绑上它才能做结构校验。
    """

    datasource_id = serializers.IntegerField(min_value=1, required=False, allow_null=True)


class SqlExecuteSerializer(SqlAnalyzeSerializer):
    """
    试运行入参

    `datasource_id` 是**必填**：没有目标库就无从执行，这与分析接口不同（那里不绑数据源也能做纯语法判定）。
    """

    datasource_id = serializers.IntegerField(min_value=1)
    # 行数上限；不传则用配置值。这里再设一个上界，避免使用者把上限开到失去意义
    max_rows = serializers.IntegerField(min_value=1, max_value=10000, required=False)

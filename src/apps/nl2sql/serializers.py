"""
说人话 → 查询语句的入参校验

不落库，因此这里没有 `ModelSerializer`——只有普通 `Serializer`。
"""

from rest_framework import serializers

from utils import custom_enum

# 需求的长度上限：它会被拼进提示词，过长既挤占结构上下文，也不像「一句话需求」
MAX_QUESTION_LENGTH = 2000

# 一次能指定的表数上限。这是**远高于**结构字符上限的粗筛，只为挡住明显异常的入参；
# 真正的上限由结构文本的字符数把关（`services._MAX_STRUCTURE_CHARS`）
MAX_TABLES = 500


class Nl2SqlGenerateSerializer(serializers.Serializer):
    """
    生成入参

    `datasource_id` **必填**：没有目标库就没有真实表结构可依据，生成出来的表名列名必然
    是编的（design.md D1）。这与 SQL 分析的入参不同——那里不绑数据源也能做纯语法判定。
    """

    datasource_id = serializers.IntegerField(min_value=1)
    question = serializers.CharField(max_length=MAX_QUESTION_LENGTH)
    # 参与生成的表名；留空表示用该数据源的全部表
    tables = serializers.ListField(child=serializers.CharField(max_length=128), required=False, allow_empty=True)
    dialect = serializers.ChoiceField(choices=custom_enum.DbTypeEnum.choices, required=False)

    def validate_question(self, value):
        value = value.strip()
        if not value:
            raise serializers.ValidationError("请描述你要查什么")
        return value

    def validate_tables(self, value):
        if len(value) > MAX_TABLES:
            raise serializers.ValidationError(f"一次最多指定 {MAX_TABLES} 张表")
        return value

from rest_framework import serializers


class DdlReviewSerializer(serializers.Serializer):
    """
    DDL 评审入参

    `datasource_id` 可选：纯建表脚本可以只评审语句本身；改表 / 索引 / 删除类语句
    由服务层要求必填并给出可操作提示。
    """

    sql = serializers.CharField()
    datasource_id = serializers.IntegerField(required=False, allow_null=True)
    dialect = serializers.IntegerField(required=False, allow_null=True)

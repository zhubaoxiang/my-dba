from rest_framework import serializers


class LoginSerializer(serializers.Serializer):
    """
    登录入参

    密码不做 trim：首尾空格可能是密码的一部分。
    """

    username = serializers.CharField(max_length=150)
    password = serializers.CharField(trim_whitespace=False)

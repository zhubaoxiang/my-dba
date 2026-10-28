from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password as django_validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers


class LoginSerializer(serializers.Serializer):
    """
    登录入参

    密码不做 trim：首尾空格可能是密码的一部分。
    """

    username = serializers.CharField(max_length=150)
    password = serializers.CharField(trim_whitespace=False)


ROLE_ADMIN = "admin"
ROLE_USER = "user"
_ROLE_CHOICES = (ROLE_ADMIN, ROLE_USER)


def role_of(user) -> str:
    """
    与登录时的 role 映射保持一致：is_staff 或 is_superuser 都视为管理员
    """
    return ROLE_ADMIN if (user.is_superuser or user.is_staff) else ROLE_USER


class UserSerializer(serializers.ModelSerializer):
    """
    本地用户查询序列化
    """

    role = serializers.SerializerMethodField()
    date_joined = serializers.DateTimeField(format="%Y-%m-%d %H:%M:%S")
    last_login = serializers.DateTimeField(format="%Y-%m-%d %H:%M:%S", allow_null=True)

    class Meta:
        model = get_user_model()
        fields = ("id", "username", "email", "role", "is_active", "date_joined", "last_login")

    def get_role(self, obj):
        return role_of(obj)


class UserCreateSerializer(serializers.Serializer):
    """
    创建本地用户
    """

    username = serializers.CharField(max_length=150)
    password = serializers.CharField(trim_whitespace=False, write_only=True)
    email = serializers.EmailField(required=False, allow_blank=True, default="")
    role = serializers.ChoiceField(choices=_ROLE_CHOICES, default=ROLE_USER)
    is_active = serializers.BooleanField(default=True)

    def validate_username(self, value):
        if get_user_model().objects.filter(username=value).exists():
            raise serializers.ValidationError("用户名已存在")
        return value

    def validate_password(self, value):
        try:
            django_validate_password(value)
        except DjangoValidationError as exc:
            raise serializers.ValidationError(list(exc.messages)) from None
        return value


class UserUpdateSerializer(serializers.Serializer):
    """
    修改本地用户

    用户名不在可修改字段里：会话与各类记录的 `creator` 都引用用户名，
    改名会让历史归属和会话隔离失效。
    """

    email = serializers.EmailField(required=False, allow_blank=True)
    role = serializers.ChoiceField(choices=_ROLE_CHOICES, required=False)
    is_active = serializers.BooleanField(required=False)
    password = serializers.CharField(required=False, allow_blank=True, trim_whitespace=False, write_only=True)

    def validate_password(self, value):
        if not value:
            return value
        try:
            django_validate_password(value)
        except DjangoValidationError as exc:
            raise serializers.ValidationError(list(exc.messages)) from None
        return value

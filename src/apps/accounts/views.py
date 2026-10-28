"""
登录与当前用户

`login` 是唯一公开的 action；`me` / `logout` 要求有效 JWT。
JWT 无服务端会话，`logout` 只返回成功，由客户端丢弃 token。
"""

from django.contrib.auth import get_user_model
from django.db import connection
from django.db.models import Q
from rest_framework.decorators import action

from apps.accounts import serializers, services
from apps.base import baseviews
from utils import common, pagination


class AuthView(baseviews.StatelessView):
    """
    登录、当前用户、退出
    """

    @action(
        detail=False,
        methods=["POST"],
        url_path="login",
        permission_classes=[],
        authentication_classes=[],
    )
    def login(self, request):
        serializer = serializers.LoginSerializer(data=request.data)
        if not serializer.is_valid():
            return baseviews.ResponseBadRequest(common.ToolUtil.format_drf_error(serializer.errors))

        data = serializer.validated_data
        try:
            token, user = services.login(data["username"], data["password"])
        except services.AuthError as exc:
            if exc.code == 4003:
                return baseviews.ResponseForbidden(str(exc))
            if exc.code == 4017:
                return baseviews.ResponseExpectationFailed(str(exc))
            return baseviews.ResponseBadRequest(str(exc))
        return baseviews.ResponseOK({"token": token, "user": user})

    @action(detail=False, methods=["GET"], url_path="me")
    def me(self, request):
        return baseviews.ResponseOK(
            {
                "username": request.user.username,
                "role": request.user.role,
                "is_staff": request.user.is_staff,
            }
        )

    @action(detail=False, methods=["POST"], url_path="logout")
    def logout(self, request):
        return baseviews.ResponseOK({"detail": "已退出登录"})


class UserView(baseviews.SuperUserView):
    """
    本地用户管理，仅超级管理员可用

    只管理 `auth_user` 里的本地账号；LDAP 用户不落库，不在本接口范围内。
    """

    queryset = get_user_model().objects.all()
    serializer_class = serializers.UserSerializer
    pagination_class = pagination.StandardPagination

    def _get_instance(self, kwargs):
        return get_user_model().objects.filter(id=kwargs.get("pk")).first()

    @staticmethod
    def _active_superuser_count() -> int:
        return get_user_model().objects.filter(is_active=True, is_superuser=True).count()

    def list(self, request, **kwargs):
        queryset = self.get_queryset().order_by("id")
        keyword = (request.query_params.get("keyword") or "").strip()
        if keyword:
            queryset = queryset.filter(Q(username__icontains=keyword) | Q(email__icontains=keyword))
        return baseviews.ResponseOK(pagination.paginate(self, queryset))

    def retrieve(self, request, *args, **kwargs):
        instance = self._get_instance(kwargs)
        if instance is None:
            return baseviews.ResponseNotFound("用户不存在")
        return baseviews.ResponseOK(self.get_serializer(instance).data)

    def create(self, request, *args, **kwargs):
        serializer = serializers.UserCreateSerializer(data=request.data)
        if not serializer.is_valid():
            return baseviews.ResponseBadRequest(common.ToolUtil.format_drf_error(serializer.errors))
        data = serializer.validated_data

        user = get_user_model().objects.create_user(
            username=data["username"],
            email=data.get("email", ""),
            password=data["password"],
            is_active=data.get("is_active", True),
        )
        is_admin = data.get("role") == serializers.ROLE_ADMIN
        user.is_superuser = is_admin
        user.is_staff = is_admin
        user.save(update_fields=["is_superuser", "is_staff"])
        return baseviews.ResponseOK(self.get_serializer(user).data)

    def update(self, request, *args, **kwargs):
        instance = self._get_instance(kwargs)
        if instance is None:
            return baseviews.ResponseNotFound("用户不存在")

        serializer = serializers.UserUpdateSerializer(data=request.data)
        if not serializer.is_valid():
            return baseviews.ResponseBadRequest(common.ToolUtil.format_drf_error(serializer.errors))
        data = serializer.validated_data

        is_self = instance.id == request.user.user_id
        new_role = data.get("role")
        new_active = data.get("is_active")

        # 管理员不能把自己降级或禁用，否则可能直接失去管理入口
        if is_self:
            if new_role is not None and new_role != serializers.role_of(instance):
                return baseviews.ResponseBadRequest("不能修改自己的角色")
            if new_active is not None and new_active != instance.is_active:
                return baseviews.ResponseBadRequest("不能修改自己的启用状态")

        if new_role is not None:
            is_admin = new_role == serializers.ROLE_ADMIN
            if (
                instance.is_superuser
                and instance.is_active
                and not is_admin
                and self._active_superuser_count() <= 1
            ):
                return baseviews.ResponseBadRequest("系统至少需要保留一个可用的超级管理员")
            instance.is_superuser = is_admin
            instance.is_staff = is_admin

        if new_active is not None:
            if (
                instance.is_superuser
                and instance.is_active
                and not new_active
                and self._active_superuser_count() <= 1
            ):
                return baseviews.ResponseBadRequest("系统至少需要保留一个可用的超级管理员")
            instance.is_active = new_active

        if "email" in data:
            instance.email = data["email"]
        if data.get("password"):
            instance.set_password(data["password"])
        instance.save()
        return baseviews.ResponseOK(self.get_serializer(instance).data)

    def destroy(self, request, *args, **kwargs):
        instance = self._get_instance(kwargs)
        if instance is None:
            return baseviews.ResponseNotFound("用户不存在")
        if instance.id == request.user.user_id:
            return baseviews.ResponseBadRequest("不能删除当前登录用户")
        if instance.is_superuser and instance.is_active and self._active_superuser_count() <= 1:
            return baseviews.ResponseBadRequest("系统至少需要保留一个可用的超级管理员")

        # 不允许走 User.delete()：Django 的级联收集会访问本库不存在的 django_admin_log 等表。
        # 参数化语句避免了拼接，也不会触发 ORM 的关联收集。
        with connection.cursor() as cursor:
            cursor.execute("DELETE FROM auth_user WHERE id = %s", [instance.id])
        return baseviews.ResponseOK(None)

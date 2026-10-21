"""
登录与当前用户

`login` 是唯一公开的 action；`me` / `logout` 要求有效 JWT。
JWT 无服务端会话，`logout` 只返回成功，由客户端丢弃 token。
"""

from rest_framework.decorators import action

from apps.accounts import serializers, services
from apps.base import baseviews
from utils import common


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

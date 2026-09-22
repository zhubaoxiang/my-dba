"""
自定义JWT认证方法，供rest_framework认证使用
"""

import jwt
from django.conf import settings
from rest_framework import authentication, exceptions


class AuthedUser:
    """
    认证用户，会将信息同步至request.user
    """

    def __init__(self, user_id, username, role, **kwargs):
        self.user_id = user_id
        self.username = username
        self.simple_username = username
        self.role = role
        self.is_authenticated = True
        self.is_staff = role == "admin"


class JwtOperate:
    """
    jwt操作
    """

    def __init__(self):
        self._secret = settings.JWT_SECRET

    def encode(self, payload: dict) -> str:
        """
        jwt加密

        :param payload:
        :return:
        """
        return jwt.encode(payload, self._secret, algorithm="HS256").decode("utf-8")

    def decode(self, payload: str) -> dict:
        """
        jwt解密

        :param payload:
        :return:
        """
        return jwt.decode(payload, self._secret, algorithms=["HS256"])


class JwtAuthentication(authentication.BaseAuthentication):
    def authenticate(self, request):
        try:
            return self.get_info_from_jwt(request)
        except jwt.DecodeError:
            raise exceptions.AuthenticationFailed("jwt decode error") from jwt.DecodeError

    def get_info_from_jwt(self, request):
        """
        从请求头中解析jwt的信息
        :param request:
        :return:
        """
        token = request.headers.get("Token")
        if token and token != "undefined":
            login_user_info = JwtOperate().decode(token)
            auth_user = AuthedUser(
                user_id=login_user_info["id"], username=login_user_info["name"], role=login_user_info.get("role")
            )
            return auth_user, None
        return None, None

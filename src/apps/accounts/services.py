"""
登录与认证服务

- LDAP 优先：`username@domain` UPN bind
- 本地兜底：LDAP 未配置或服务器不可达时走 Django `authenticate()`
- LDAP 明确拒绝密码时**不回落**，避免本地同名账号密码绕过域密码
- 登录成功签发 JWT；LDAP 用户不落 `auth_user`，本地用户按 is_staff/is_superuser 映射角色
"""

import contextlib
from datetime import datetime, timedelta

import ldap3
from django.conf import settings
from django.contrib.auth import authenticate

from utils.authentication import AuthedUser, JwtOperate
from utils.configure import CONF_ATTR
from utils.logger import get_logger

LOGGER = get_logger("accounts.log")


class AuthError(Exception):
    """
    可预期的登录失败

    code 与统一响应格式一致：4003 凭据错误 / 4017 配置或服务不可用。
    """

    def __init__(self, message: str, code: int = 4003):
        super().__init__(message)
        self.code = code


def ldap_config():
    """
    LDAP 配置

    `ldap_server` 为空表示未启用；有 server 但没有 domain 属于配置不完整，
    明确报错而不是静默当作未配置。
    """
    server = (CONF_ATTR.get("auth_ldap_server") or "").strip()
    if not server:
        return None

    domain = (CONF_ATTR.get("auth_ldap_domain") or "").strip()
    if not domain:
        raise AuthError("LDAP 配置不完整：ldap_server 已设置，但缺少 ldap_domain。", 4017)

    try:
        port = int(CONF_ATTR.get("auth_ldap_port") or 389)
    except (TypeError, ValueError):
        port = 389
    try:
        timeout = int(CONF_ATTR.get("auth_ldap_connect_timeout") or 10)
    except (TypeError, ValueError):
        timeout = 10

    return {"server": server, "port": port, "domain": domain, "connect_timeout": timeout}


def ldap_authenticate(username: str, password: str, config: dict):
    """
    LDAP UPN bind

    返回 True=成功、False=明确拒绝、None=服务器不可达。
    空密码直接失败，避免某些服务器把空密码当成匿名 bind。
    """
    if not username or not password:
        return False

    upn = f"{username}@{config['domain']}"
    conn = None
    try:
        server = ldap3.Server(
            config["server"],
            port=config["port"],
            connect_timeout=config["connect_timeout"],
            get_info=ldap3.NONE,
        )
        conn = ldap3.Connection(server, user=upn, password=password)
        if conn.bind():
            LOGGER.info("LDAP 认证成功 username=%s", username)
            return True
        LOGGER.warning("LDAP 认证失败（密码错误） username=%s", username)
        return False
    except ldap3.core.exceptions.LDAPBindError:
        LOGGER.warning("LDAP 认证失败（密码错误） username=%s", username)
        return False
    except ldap3.core.exceptions.LDAPException as exc:
        LOGGER.error("LDAP 服务器不可达 server=%s error=%s", config["server"], exc)
        return None
    finally:
        if conn is not None:
            with contextlib.suppress(ldap3.core.exceptions.LDAPException):
                conn.unbind()


def authenticate_user(username: str, password: str) -> AuthedUser:
    """
    按「LDAP 优先 + 本地兜底」返回登录用户

    LDAP 明确拒绝密码时抛 4003，不再看本地密码；LDAP 不可达时记告警并回落到本地。
    """
    config = ldap_config()
    ldap_unreachable = False

    if config is not None:
        result = ldap_authenticate(username, password, config)
        if result is True:
            return AuthedUser(user_id=username, username=username, role="user")
        if result is False:
            raise AuthError("用户名或密码错误", 4003)
        ldap_unreachable = True
        LOGGER.warning("LDAP 不可达，尝试本地认证 username=%s", username)

    user = authenticate(username=username, password=password)
    if user is None or not user.is_active:
        if ldap_unreachable:
            raise AuthError("LDAP 服务不可达，本地认证也未通过。", 4017)
        raise AuthError("用户名或密码错误", 4003)

    role = "admin" if (user.is_staff or user.is_superuser) else "user"
    return AuthedUser(user_id=user.id, username=user.username, role=role)


def issue_token(user: AuthedUser) -> str:
    """
    签发 JWT；密钥缺失时明确报配置错误，绝不用空密钥签发
    """
    if not getattr(settings, "JWT_SECRET", ""):
        raise AuthError("未配置 JWT 密钥，请在 conf.ini 的 [auth] 段设置 jwt_secret。", 4017)

    try:
        ttl_hours = int(CONF_ATTR.get("auth_token_ttl_hours") or 8)
    except (TypeError, ValueError):
        ttl_hours = 8

    payload = {
        "id": user.user_id,
        "name": user.username,
        "role": user.role,
        "exp": datetime.utcnow() + timedelta(hours=ttl_hours),
    }
    return JwtOperate().encode(payload)


def login(username: str, password: str) -> tuple:
    """
    登录并返回 `(token, user_info)`
    """
    user = authenticate_user(username, password)
    token = issue_token(user)
    return token, {"username": user.username, "role": user.role, "is_staff": user.is_staff}

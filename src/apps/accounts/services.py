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
from django.contrib.auth import authenticate, get_user_model

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
    raw_server = (CONF_ATTR.get("auth_ldap_server") or "").strip()
    if not raw_server:
        return None

    domain = (CONF_ATTR.get("auth_ldap_domain") or "").strip()
    if not domain:
        raise AuthError("LDAP 配置不完整：ldap_server 已设置，但缺少 ldap_domain。", 4017)

    # 兼容 ldap://host、ldaps://host、host:port；ldap3.Server 只接受主机名
    use_ssl = str(CONF_ATTR.get("auth_ldap_use_ssl") or "").strip().lower() in ("1", "true", "yes", "on")
    lowered = raw_server.lower()
    if lowered.startswith("ldaps://"):
        use_ssl = True
        raw_server = raw_server[8:]
    elif lowered.startswith("ldap://"):
        raw_server = raw_server[7:]
    raw_server = raw_server.rstrip("/")

    port_override = None
    if ":" in raw_server:
        raw_server, _, port_text = raw_server.partition(":")
        try:
            port_override = int(port_text)
        except ValueError:
            port_override = None
    server = raw_server.strip()
    if not server:
        raise AuthError("LDAP 配置不完整：ldap_server 没有主机名。", 4017)

    try:
        port = port_override if port_override is not None else int(CONF_ATTR.get("auth_ldap_port") or 389)
    except (TypeError, ValueError):
        port = 389
    try:
        timeout = int(CONF_ATTR.get("auth_ldap_connect_timeout") or 10)
    except (TypeError, ValueError):
        timeout = 10

    return {
        "server": server,
        "port": port,
        "domain": domain,
        "connect_timeout": timeout,
        "use_ssl": use_ssl,
    }


def ldap_authenticate(username: str, password: str, config: dict):
    """
    LDAP UPN bind

    返回 True=成功、False=明确拒绝、None=服务器不可达。
    空密码直接失败，避免某些服务器把空密码当成匿名 bind。
    """
    if not username or not password:
        return False

    # 兼容三种输入：短账号、完整 UPN、DOMAIN\user
    account = username.strip()
    if "\\" in account:
        account = account.split("\\", 1)[1]
    upn = account if "@" in account else f"{account}@{config['domain']}"
    conn = None
    try:
        server = ldap3.Server(
            config["server"],
            port=config["port"],
            use_ssl=bool(config.get("use_ssl")),
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


def _find_local_user(username: str):
    """
    本地账号是否存在（不校验密码）

    独立出来便于测试，也用于区分「本地没有该用户」与「本地有用户但密码错」。
    """
    return get_user_model().objects.filter(username=username).first()


def authenticate_user(username: str, password: str) -> AuthedUser:
    """
    本地账号优先；本地没有该用户时才走 LDAP

    本地存在同名用户时，即使密码错误也不再用 LDAP 密码尝试——按「本地没有该用户才走
    LDAP」的口径，避免同名账号在两套账号体系之间产生歧义。
    """
    local_user = _find_local_user(username)
    if local_user is not None:
        user = authenticate(username=username, password=password)
        if user is None or not user.is_active:
            LOGGER.warning("本地账号存在但认证失败，不再尝试 LDAP username=%s", username)
            raise AuthError("用户名或密码错误", 4003)
        # 用户管理页面要展示最后登录时间；Django 的 authenticate() 本身不写 last_login
        user.last_login = datetime.now()
        user.save(update_fields=["last_login"])
        LOGGER.info("本地认证成功 username=%s", username)
        role = "admin" if (user.is_staff or user.is_superuser) else "user"
        return AuthedUser(user_id=user.id, username=user.username, role=role)

    LOGGER.info("本地账号不存在，尝试 LDAP username=%s", username)
    config = ldap_config()
    if config is None:
        LOGGER.warning("LDAP 未配置，且本地账号不存在 username=%s", username)
        raise AuthError("用户名或密码错误", 4003)

    result = ldap_authenticate(username, password, config)
    if result is True:
        return AuthedUser(user_id=username, username=username, role="user")
    if result is False:
        raise AuthError("用户名或密码错误", 4003)
    raise AuthError("LDAP 服务不可达，且本地账号不存在。", 4017)


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

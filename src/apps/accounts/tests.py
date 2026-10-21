"""
登录与认证单元测试

LDAP 与本地认证都打桩，`SimpleTestCase` 不碰数据库；API 测试用
`force_authenticate` 或真实 JWT 验证权限链路。
"""

from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest import mock

from django.test import SimpleTestCase, override_settings
from rest_framework.test import APIClient

from apps.accounts import services
from utils.authentication import AuthedUser, JwtOperate

AUTH = "/my-dba/v1/auth"
RULES = "/my-dba/v1/sql-analysis/rules"
SECRET = "test-jwt-secret"


def _user(is_staff=False, is_superuser=False, active=True):
    return SimpleNamespace(
        id=1,
        username="alice",
        is_staff=is_staff,
        is_superuser=is_superuser,
        is_active=active,
    )


class LdapConfigTests(SimpleTestCase):
    def test_not_configured_when_server_empty(self):
        with mock.patch.object(services, "CONF_ATTR", {"auth_ldap_server": ""}):
            self.assertIsNone(services.ldap_config())

    def test_server_without_domain_is_config_error(self):
        with (
            mock.patch.object(services, "CONF_ATTR", {"auth_ldap_server": "ldap.example.com", "auth_ldap_domain": ""}),
            self.assertRaises(services.AuthError) as raised,
        ):
            services.ldap_config()
        self.assertEqual(raised.exception.code, 4017)

    def test_complete_config(self):
        with mock.patch.object(
            services,
            "CONF_ATTR",
            {
                "auth_ldap_server": "ldap.example.com",
                "auth_ldap_domain": "example.com",
                "auth_ldap_port": "636",
                "auth_ldap_connect_timeout": "4",
            },
        ):
            self.assertEqual(
                services.ldap_config(),
                {"server": "ldap.example.com", "port": 636, "domain": "example.com", "connect_timeout": 4},
            )


class LdapAuthenticateTests(SimpleTestCase):
    CONFIG = {"server": "ldap.example.com", "port": 389, "domain": "example.com", "connect_timeout": 10}

    def test_empty_password_never_binds(self):
        with mock.patch.object(services.ldap3, "Connection") as connection:
            self.assertFalse(services.ldap_authenticate("alice", "", self.CONFIG))
        connection.assert_not_called()

    def test_bind_success(self):
        with (
            mock.patch.object(services.ldap3, "Server"),
            mock.patch.object(services.ldap3, "Connection") as connection,
        ):
            connection.return_value.bind.return_value = True
            self.assertTrue(services.ldap_authenticate("alice", "secret", self.CONFIG))

    def test_bind_false_is_rejected(self):
        with (
            mock.patch.object(services.ldap3, "Server"),
            mock.patch.object(services.ldap3, "Connection") as connection,
        ):
            connection.return_value.bind.return_value = False
            self.assertFalse(services.ldap_authenticate("alice", "secret", self.CONFIG))

    def test_bind_error_is_rejected(self):
        with (
            mock.patch.object(services.ldap3, "Server"),
            mock.patch.object(services.ldap3, "Connection") as connection,
        ):
            connection.return_value.bind.side_effect = services.ldap3.core.exceptions.LDAPBindError("bad")
            self.assertFalse(services.ldap_authenticate("alice", "secret", self.CONFIG))

    def test_unreachable_returns_none(self):
        with (
            mock.patch.object(services.ldap3, "Server"),
            mock.patch.object(services.ldap3, "Connection") as connection,
        ):
            connection.side_effect = services.ldap3.core.exceptions.LDAPException("down")
            self.assertIsNone(services.ldap_authenticate("alice", "secret", self.CONFIG))


class AuthenticateUserTests(SimpleTestCase):
    CONFIG = {"server": "ldap.example.com", "port": 389, "domain": "example.com", "connect_timeout": 10}

    def test_ldap_success_does_not_touch_local(self):
        with (
            mock.patch.object(services, "ldap_config", return_value=self.CONFIG),
            mock.patch.object(services, "ldap_authenticate", return_value=True),
            mock.patch.object(services, "authenticate") as local,
        ):
            user = services.authenticate_user("alice", "secret")
        self.assertEqual(user.role, "user")
        self.assertEqual(user.username, "alice")
        local.assert_not_called()

    def test_ldap_bad_password_does_not_fall_back(self):
        with (
            mock.patch.object(services, "ldap_config", return_value=self.CONFIG),
            mock.patch.object(services, "ldap_authenticate", return_value=False),
            mock.patch.object(services, "authenticate") as local,
            self.assertRaises(services.AuthError) as raised,
        ):
            services.authenticate_user("alice", "wrong")
        self.assertEqual(raised.exception.code, 4003)
        local.assert_not_called()

    def test_ldap_unreachable_falls_back_to_local(self):
        with (
            mock.patch.object(services, "ldap_config", return_value=self.CONFIG),
            mock.patch.object(services, "ldap_authenticate", return_value=None),
            mock.patch.object(services, "authenticate", return_value=_user(is_staff=True)),
        ):
            user = services.authenticate_user("alice", "secret")
        self.assertEqual(user.role, "admin")
        self.assertTrue(user.is_staff)

    def test_local_auth_when_ldap_not_configured(self):
        with (
            mock.patch.object(services, "ldap_config", return_value=None),
            mock.patch.object(services, "authenticate", return_value=_user()),
        ):
            self.assertEqual(services.authenticate_user("alice", "secret").role, "user")

    def test_local_failure_is_4003(self):
        with (
            mock.patch.object(services, "ldap_config", return_value=None),
            mock.patch.object(services, "authenticate", return_value=None),
            self.assertRaises(services.AuthError) as raised,
        ):
            services.authenticate_user("alice", "wrong")
        self.assertEqual(raised.exception.code, 4003)

    def test_inactive_local_user_is_rejected(self):
        with (
            mock.patch.object(services, "ldap_config", return_value=None),
            mock.patch.object(services, "authenticate", return_value=_user(active=False)),
            self.assertRaises(services.AuthError) as raised,
        ):
            services.authenticate_user("alice", "secret")
        self.assertEqual(raised.exception.code, 4003)


class TokenTests(SimpleTestCase):
    def test_role_maps_to_is_staff(self):
        self.assertTrue(AuthedUser(1, "admin", "admin").is_staff)
        self.assertFalse(AuthedUser(1, "user", "user").is_staff)

    @override_settings(JWT_SECRET=SECRET)
    def test_issue_and_decode(self):
        with mock.patch.object(services, "CONF_ATTR", {"auth_token_ttl_hours": "1"}):
            token = services.issue_token(AuthedUser(user_id=1, username="alice", role="admin"))
        payload = JwtOperate().decode(token)
        self.assertEqual(payload["id"], 1)
        self.assertEqual(payload["name"], "alice")
        self.assertEqual(payload["role"], "admin")
        self.assertIn("exp", payload)

    @override_settings(JWT_SECRET="")
    def test_empty_secret_is_config_error(self):
        with self.assertRaises(services.AuthError) as raised:
            services.issue_token(AuthedUser(user_id=1, username="alice", role="user"))
        self.assertEqual(raised.exception.code, 4017)

    @override_settings(JWT_SECRET=SECRET)
    def test_login_returns_token_and_user(self):
        with (
            mock.patch.object(services, "authenticate_user", return_value=AuthedUser(1, "alice", "admin")),
            mock.patch.object(services, "CONF_ATTR", {}),
        ):
            token, user = services.login("alice", "secret")
        self.assertTrue(token)
        self.assertEqual(user, {"username": "alice", "role": "admin", "is_staff": True})


class AuthApiTests(SimpleTestCase):
    def setUp(self):
        self.client = APIClient()

    def test_login_success(self):
        with mock.patch.object(
            services, "login", return_value=("token-value", {"username": "alice", "role": "user", "is_staff": False})
        ):
            payload = self.client.post(
                f"{AUTH}/login", {"username": "alice", "password": "secret"}, format="json"
            ).json()
        self.assertEqual(payload["code"], 2000)
        self.assertEqual(payload["data"]["token"], "token-value")

    def test_login_bad_credentials(self):
        with mock.patch.object(services, "login", side_effect=services.AuthError("用户名或密码错误", 4003)):
            payload = self.client.post(
                f"{AUTH}/login", {"username": "alice", "password": "wrong"}, format="json"
            ).json()
        self.assertEqual(payload["code"], 4003)

    def test_login_empty_payload(self):
        payload = self.client.post(f"{AUTH}/login", {}, format="json").json()
        self.assertEqual(payload["code"], 4000)

    def test_me_requires_login(self):
        payload = self.client.get(f"{AUTH}/me").json()
        self.assertEqual(payload["code"], 4003)

    def test_me_with_authenticated_user(self):
        self.client.force_authenticate(user=AuthedUser(1, "alice", "admin"))
        payload = self.client.get(f"{AUTH}/me").json()
        self.assertEqual(payload["code"], 2000)
        self.assertEqual(payload["data"]["username"], "alice")
        self.assertTrue(payload["data"]["is_staff"])

    @override_settings(JWT_SECRET=SECRET)
    def test_me_accepts_jwt(self):
        token = JwtOperate().encode({"id": 1, "name": "alice", "role": "admin"})
        self.client.credentials(HTTP_TOKEN=token)
        payload = self.client.get(f"{AUTH}/me").json()
        self.assertEqual(payload["code"], 2000)
        self.assertEqual(payload["data"]["username"], "alice")
        self.assertTrue(payload["data"]["is_staff"])

    def test_protected_endpoint_requires_login(self):
        payload = self.client.get(RULES).json()
        self.assertEqual(payload["code"], 4003)
        self.assertIn("未登录", payload["message"])

    @override_settings(JWT_SECRET=SECRET)
    def test_protected_endpoint_accepts_jwt(self):
        token = JwtOperate().encode({"id": 1, "name": "alice", "role": "user"})
        self.client.credentials(HTTP_TOKEN=token)
        payload = self.client.get(RULES).json()
        self.assertEqual(payload["code"], 2000)

    @override_settings(JWT_SECRET=SECRET)
    def test_expired_token_is_4003(self):
        token = JwtOperate().encode(
            {"id": 1, "name": "alice", "role": "user", "exp": datetime.utcnow() - timedelta(seconds=1)}
        )
        self.client.credentials(HTTP_TOKEN=token)
        payload = self.client.get(RULES).json()
        self.assertEqual(payload["code"], 4003)
        self.assertIn("过期", payload["message"])

    def test_invalid_token_is_4003(self):
        self.client.credentials(HTTP_TOKEN="not-a-token")
        payload = self.client.get(RULES).json()
        self.assertEqual(payload["code"], 4003)

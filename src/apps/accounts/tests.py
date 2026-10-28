"""
登录与认证单元测试

LDAP 与本地认证都打桩，`SimpleTestCase` 不碰数据库；API 测试用
`force_authenticate` 或真实 JWT 验证权限链路。
"""

from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase, override_settings
from rest_framework.test import APIClient

from apps.accounts import services
from utils.authentication import AuthedUser, JwtOperate

AUTH = "/my-dba/v1/auth"
USER = "/my-dba/v1/user"
RULES = "/my-dba/v1/sql-analysis/rules"
SECRET = "test-jwt-secret"
PASSWORD = "Str0ngPass!23"


def _user(is_staff=False, is_superuser=False, active=True):
    return SimpleNamespace(
        id=1,
        username="alice",
        is_staff=is_staff,
        is_superuser=is_superuser,
        is_active=active,
        last_login=None,
        save=mock.Mock(),
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
                {
                    "server": "ldap.example.com",
                    "port": 636,
                    "domain": "example.com",
                    "connect_timeout": 4,
                    "use_ssl": False,
                },
            )

    def test_ldaps_flag(self):
        with mock.patch.object(
            services,
            "CONF_ATTR",
            {
                "auth_ldap_server": "ldap.example.com",
                "auth_ldap_domain": "example.com",
                "auth_ldap_use_ssl": "true",
            },
        ):
            self.assertTrue(services.ldap_config()["use_ssl"])

    def test_ldap_scheme_is_stripped(self):
        with mock.patch.object(
            services,
            "CONF_ATTR",
            {"auth_ldap_server": "ldap://ldap.example.com", "auth_ldap_domain": "example.com"},
        ):
            config = services.ldap_config()
        self.assertEqual(config["server"], "ldap.example.com")
        self.assertFalse(config["use_ssl"])

    def test_ldaps_scheme_sets_ssl(self):
        with mock.patch.object(
            services,
            "CONF_ATTR",
            {"auth_ldap_server": "ldaps://ldap.example.com", "auth_ldap_domain": "example.com"},
        ):
            config = services.ldap_config()
        self.assertEqual(config["server"], "ldap.example.com")
        self.assertTrue(config["use_ssl"])

    def test_port_in_url_overrides_config(self):
        with mock.patch.object(
            services,
            "CONF_ATTR",
            {
                "auth_ldap_server": "ldap://ldap.example.com:1389",
                "auth_ldap_domain": "example.com",
                "auth_ldap_port": "389",
            },
        ):
            self.assertEqual(services.ldap_config()["port"], 1389)


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
        self.assertEqual(connection.call_args.kwargs["user"], "alice@example.com")

    def test_full_upn_is_not_suffixed_again(self):
        with (
            mock.patch.object(services.ldap3, "Server"),
            mock.patch.object(services.ldap3, "Connection") as connection,
        ):
            connection.return_value.bind.return_value = True
            services.ldap_authenticate("alice@example.com", "secret", self.CONFIG)
        self.assertEqual(connection.call_args.kwargs["user"], "alice@example.com")

    def test_domain_prefix_is_normalized(self):
        with (
            mock.patch.object(services.ldap3, "Server"),
            mock.patch.object(services.ldap3, "Connection") as connection,
        ):
            connection.return_value.bind.return_value = True
            services.ldap_authenticate(r"EXAMPLE\alice", "secret", self.CONFIG)
        self.assertEqual(connection.call_args.kwargs["user"], "alice@example.com")

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

    def test_local_user_success_does_not_touch_ldap(self):
        with (
            mock.patch.object(services, "_find_local_user", return_value=_user(is_staff=True)),
            mock.patch.object(services, "authenticate", return_value=_user(is_staff=True)),
            mock.patch.object(services, "ldap_config") as ldap_config,
        ):
            user = services.authenticate_user("alice", "secret")
        self.assertEqual(user.role, "admin")
        self.assertTrue(user.is_staff)
        ldap_config.assert_not_called()

    def test_local_user_wrong_password_does_not_fall_back_to_ldap(self):
        with (
            mock.patch.object(services, "_find_local_user", return_value=_user()),
            mock.patch.object(services, "authenticate", return_value=None),
            mock.patch.object(services, "ldap_config") as ldap_config,
            self.assertRaises(services.AuthError) as raised,
        ):
            services.authenticate_user("alice", "wrong")
        self.assertEqual(raised.exception.code, 4003)
        ldap_config.assert_not_called()

    def test_local_user_absent_uses_ldap(self):
        with (
            mock.patch.object(services, "_find_local_user", return_value=None),
            mock.patch.object(services, "ldap_config", return_value=self.CONFIG),
            mock.patch.object(services, "ldap_authenticate", return_value=True),
            mock.patch.object(services, "authenticate") as local,
        ):
            user = services.authenticate_user("alice", "secret")
        self.assertEqual(user.role, "user")
        self.assertEqual(user.username, "alice")
        local.assert_not_called()

    def test_local_absent_ldap_bad_password_is_4003(self):
        with (
            mock.patch.object(services, "_find_local_user", return_value=None),
            mock.patch.object(services, "ldap_config", return_value=self.CONFIG),
            mock.patch.object(services, "ldap_authenticate", return_value=False),
            self.assertRaises(services.AuthError) as raised,
        ):
            services.authenticate_user("alice", "wrong")
        self.assertEqual(raised.exception.code, 4003)

    def test_local_absent_ldap_not_configured_is_4003(self):
        with (
            mock.patch.object(services, "_find_local_user", return_value=None),
            mock.patch.object(services, "ldap_config", return_value=None),
            self.assertRaises(services.AuthError) as raised,
        ):
            services.authenticate_user("alice", "secret")
        self.assertEqual(raised.exception.code, 4003)

    def test_local_absent_ldap_unreachable_is_4017(self):
        with (
            mock.patch.object(services, "_find_local_user", return_value=None),
            mock.patch.object(services, "ldap_config", return_value=self.CONFIG),
            mock.patch.object(services, "ldap_authenticate", return_value=None),
            self.assertRaises(services.AuthError) as raised,
        ):
            services.authenticate_user("alice", "secret")
        self.assertEqual(raised.exception.code, 4017)

    def test_inactive_local_user_is_rejected_without_ldap(self):
        with (
            mock.patch.object(services, "_find_local_user", return_value=_user(active=False)),
            mock.patch.object(services, "authenticate", return_value=None),
            mock.patch.object(services, "ldap_config") as ldap_config,
            self.assertRaises(services.AuthError) as raised,
        ):
            services.authenticate_user("alice", "secret")
        self.assertEqual(raised.exception.code, 4003)
        ldap_config.assert_not_called()


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


class UserManagementApiTests(TestCase):
    """
    本地用户管理：管理员可用、普通用户 4005、角色映射与删除保护
    """

    NEW_PASSWORD = "An0therStr0ng!23"

    def setUp(self):
        model = get_user_model()
        self.admin = model.objects.create_user(
            username="root", password=PASSWORD, is_staff=True, is_superuser=True
        )
        self.user = model.objects.create_user(username="alice", password=PASSWORD)

    @staticmethod
    def _client(user):
        client = APIClient()
        role = "admin" if (user.is_staff or user.is_superuser) else "user"
        client.force_authenticate(user=AuthedUser(user.id, user.username, role))
        return client

    def test_admin_can_list_users(self):
        payload = self._client(self.admin).get(USER).json()
        self.assertEqual(payload["code"], 2000)
        rows = {item["username"]: item for item in payload["data"]["results"]}
        self.assertEqual(set(rows), {"root", "alice"})
        self.assertEqual(rows["root"]["role"], "admin")
        self.assertEqual(rows["alice"]["role"], "user")

    def test_normal_user_gets_4005(self):
        payload = self._client(self.user).get(USER).json()
        self.assertEqual(payload["code"], 4005)
        self.assertIn("权限", payload["message"])

    def test_anonymous_gets_4003(self):
        payload = APIClient().get(USER).json()
        self.assertEqual(payload["code"], 4003)

    def test_create_user(self):
        payload = self._client(self.admin).post(
            USER,
            {
                "username": "bob",
                "password": PASSWORD,
                "email": "bob@example.com",
                "role": "admin",
            },
            format="json",
        ).json()
        self.assertEqual(payload["code"], 2000)
        bob = get_user_model().objects.get(username="bob")
        self.assertTrue(bob.is_superuser)
        self.assertTrue(bob.is_staff)
        self.assertTrue(bob.check_password(PASSWORD))

    def test_create_duplicate_username_is_rejected(self):
        payload = self._client(self.admin).post(
            USER, {"username": "root", "password": PASSWORD}, format="json"
        ).json()
        self.assertEqual(payload["code"], 4000)

    def test_update_role_email_and_password(self):
        payload = self._client(self.admin).put(
            f"{USER}/{self.user.id}",
            {"role": "admin", "email": "new@example.com", "password": self.NEW_PASSWORD},
            format="json",
        ).json()
        self.assertEqual(payload["code"], 2000)
        self.user.refresh_from_db()
        self.assertTrue(self.user.is_superuser)
        self.assertTrue(self.user.is_staff)
        self.assertEqual(self.user.email, "new@example.com")
        self.assertTrue(self.user.check_password(self.NEW_PASSWORD))

    def test_username_cannot_be_changed(self):
        payload = self._client(self.admin).put(
            f"{USER}/{self.user.id}", {"username": "renamed", "email": "x@example.com"}, format="json"
        ).json()
        self.assertEqual(payload["code"], 2000)
        self.user.refresh_from_db()
        self.assertEqual(self.user.username, "alice")

    def test_admin_cannot_change_own_role(self):
        payload = self._client(self.admin).put(
            f"{USER}/{self.admin.id}", {"role": "user"}, format="json"
        ).json()
        self.assertEqual(payload["code"], 4000)
        self.admin.refresh_from_db()
        self.assertTrue(self.admin.is_superuser)

    def test_admin_cannot_delete_self(self):
        payload = self._client(self.admin).delete(f"{USER}/{self.admin.id}").json()
        self.assertEqual(payload["code"], 4000)
        self.assertTrue(get_user_model().objects.filter(id=self.admin.id).exists())

    def test_delete_user(self):
        payload = self._client(self.admin).delete(f"{USER}/{self.user.id}").json()
        self.assertEqual(payload["code"], 2000)
        self.assertFalse(get_user_model().objects.filter(id=self.user.id).exists())

    def test_last_superuser_cannot_be_deleted(self):
        # 另一个只有 is_staff 的管理员来删唯一的 superuser
        actor = get_user_model().objects.create_user(
            username="ops", password=PASSWORD, is_staff=True, is_superuser=False
        )
        payload = self._client(actor).delete(f"{USER}/{self.admin.id}").json()
        self.assertEqual(payload["code"], 4000)
        self.assertTrue(get_user_model().objects.filter(id=self.admin.id).exists())

    def test_last_superuser_cannot_be_demoted_or_disabled(self):
        actor = get_user_model().objects.create_user(
            username="ops", password=PASSWORD, is_staff=True, is_superuser=False
        )
        client = self._client(actor)
        for data in ({"role": "user"}, {"is_active": False}):
            payload = client.put(f"{USER}/{self.admin.id}", data, format="json").json()
            self.assertEqual(payload["code"], 4000)
        self.admin.refresh_from_db()
        self.assertTrue(self.admin.is_superuser)
        self.assertTrue(self.admin.is_active)

    def test_local_login_updates_last_login(self):
        self.assertIsNone(self.user.last_login)
        result = services.authenticate_user("alice", PASSWORD)
        self.assertEqual(result.username, "alice")
        self.user.refresh_from_db()
        self.assertIsNotNone(self.user.last_login)

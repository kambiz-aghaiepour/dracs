"""Tests for token-based API authentication and password change endpoints."""

import json
import os
import tempfile
from unittest.mock import patch

import pytest

from dracs.db import db_initialize, get_default_site_id, upsert_system
from dracs.tokens import generate_token
from dracs.users import create_user, set_user_site_role


@pytest.fixture
def webapp_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    db_initialize(path)
    upsert_system(
        path, "TAG001", "server01", "R660", "7.0.0", "2.1.0", "Jan 1, 2027", 1893456000
    )
    yield path
    if os.path.exists(path):
        os.unlink(path)


@pytest.fixture
def client(webapp_db):
    log_dir = tempfile.mkdtemp()
    with patch.dict(
        os.environ,
        {
            "DRACS_DB": webapp_db,
            "DRACS_DNS_STRING": "mgmt-",
            "DRACS_DNS_MODE": "prefix",
            "DRACS_LOG_DIR": log_dir,
            "DRACS_TOKEN_EXPIRY": "36000",
        },
    ):
        import dracs.webapp as webapp_mod

        webapp_mod.DB_PATH = webapp_db
        webapp_mod.db_initialize(webapp_db)
        webapp_mod.app.config["TESTING"] = True
        with webapp_mod.app.test_client() as c:
            yield c


def _login_admin(client):
    client.post(
        "/login",
        data=json.dumps({"username": "admin", "password": "admin"}),
        content_type="application/json",
    )


class TestTokenLogin:
    def test_token_login_success(self, client, webapp_db):
        create_user("jsmith", "secret", "user")
        resp = client.post(
            "/api/token-login",
            data=json.dumps({"username": "jsmith", "password": "secret"}),
            content_type="application/json",
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        assert "token" in data
        assert data["role"] == "user"
        assert data["expires_in"] == 36000

    def test_token_login_bad_credentials(self, client, webapp_db):
        create_user("jsmith", "secret", "user")
        resp = client.post(
            "/api/token-login",
            data=json.dumps({"username": "jsmith", "password": "wrong"}),
            content_type="application/json",
        )
        assert resp.status_code == 401

    def test_token_login_superadmin_rejected(self, client):
        resp = client.post(
            "/api/token-login",
            data=json.dumps({"username": "admin", "password": "admin"}),
            content_type="application/json",
        )
        assert resp.status_code == 403
        assert "superadmin" in resp.get_json()["message"].lower()

    def test_token_login_no_json(self, client):
        resp = client.post(
            "/api/token-login",
            data="not json",
            content_type="text/plain",
        )
        assert resp.status_code in (400, 500)

    def test_token_login_nonexistent_user(self, client):
        resp = client.post(
            "/api/token-login",
            data=json.dumps({"username": "nobody", "password": "pass"}),
            content_type="application/json",
        )
        assert resp.status_code == 401


class TestTokenLogout:
    def test_token_logout_success(self, client, webapp_db):
        create_user("jsmith", "secret", "user")
        login_resp = client.post(
            "/api/token-login",
            data=json.dumps({"username": "jsmith", "password": "secret"}),
            content_type="application/json",
        )
        token = login_resp.get_json()["token"]

        resp = client.post(
            "/api/token-logout",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        assert resp.get_json()["success"] is True

    def test_token_logout_no_token(self, client):
        resp = client.post("/api/token-logout")
        assert resp.status_code == 400

    def test_token_logout_invalid_token(self, client):
        resp = client.post(
            "/api/token-logout",
            headers={"Authorization": "Bearer invalidtoken"},
        )
        assert resp.status_code == 401


class TestBearerAuth:
    def test_bearer_token_works_for_protected_endpoint(self, client, webapp_db):
        create_user("jsmith", "secret", "admin")
        try:
            set_user_site_role("jsmith", get_default_site_id(), "admin")
        except RuntimeError:
            pass
        login_resp = client.post(
            "/api/token-login",
            data=json.dumps({"username": "jsmith", "password": "secret"}),
            content_type="application/json",
        )
        token = login_resp.get_json()["token"]

        resp = client.post(
            "/api/refresh-all",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code != 401
        assert resp.status_code != 403

    def test_bearer_token_admin_role_can_list_users(self, client, webapp_db):
        create_user("jsmith", "secret", "admin")
        login_resp = client.post(
            "/api/token-login",
            data=json.dumps({"username": "jsmith", "password": "secret"}),
            content_type="application/json",
        )
        token = login_resp.get_json()["token"]

        resp = client.get(
            "/api/users",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        assert resp.get_json()["success"] is True

    def test_bearer_token_user_role_gets_403_on_admin_endpoint(self, client, webapp_db):
        create_user("jsmith", "secret", "user")
        login_resp = client.post(
            "/api/token-login",
            data=json.dumps({"username": "jsmith", "password": "secret"}),
            content_type="application/json",
        )
        token = login_resp.get_json()["token"]

        resp = client.post(
            "/api/refresh",
            data=json.dumps({"service_tag": "TAG001"}),
            content_type="application/json",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 403

    def test_bearer_token_user_role_can_access_user_endpoint(self, client, webapp_db):
        create_user("jsmith", "secret", "user")
        login_resp = client.post(
            "/api/token-login",
            data=json.dumps({"username": "jsmith", "password": "secret"}),
            content_type="application/json",
        )
        token = login_resp.get_json()["token"]

        resp = client.post(
            "/api/tsr-status",
            data=json.dumps({"hostname": "server01"}),
            content_type="application/json",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code != 401
        assert resp.status_code != 403

    def test_invalidated_token_returns_401(self, client, webapp_db):
        create_user("jsmith", "secret", "admin")
        login_resp = client.post(
            "/api/token-login",
            data=json.dumps({"username": "jsmith", "password": "secret"}),
            content_type="application/json",
        )
        token = login_resp.get_json()["token"]

        client.post(
            "/api/token-logout",
            headers={"Authorization": f"Bearer {token}"},
        )

        resp = client.get(
            "/api/users",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 401

    def test_session_auth_still_works(self, client):
        _login_admin(client)
        resp = client.get("/api/users")
        assert resp.status_code == 200


class TestTokenRefresh:
    def test_token_refreshed_on_unauthenticated_endpoint(self, client, webapp_db):
        create_user("jsmith", "secret", "user")
        login_resp = client.post(
            "/api/token-login",
            data=json.dumps({"username": "jsmith", "password": "secret"}),
            content_type="application/json",
        )
        token = login_resp.get_json()["token"]

        resp = client.get(
            "/api/systems",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200


class TestChangePassword:
    def test_change_password_db_user(self, client, webapp_db):
        create_user("jsmith", "oldpass", "user")
        _login_admin(client)
        client.post("/logout")

        client.post(
            "/login",
            data=json.dumps({"username": "jsmith", "password": "oldpass"}),
            content_type="application/json",
        )

        resp = client.post(
            "/api/change-password",
            data=json.dumps({"current_password": "oldpass", "new_password": "newpass"}),
            content_type="application/json",
        )
        assert resp.status_code == 200
        assert resp.get_json()["success"] is True

        client.post("/logout")
        login_resp = client.post(
            "/login",
            data=json.dumps({"username": "jsmith", "password": "newpass"}),
            content_type="application/json",
        )
        assert login_resp.status_code == 200

    def test_change_password_wrong_current(self, client, webapp_db):
        create_user("jsmith", "oldpass", "user")
        client.post(
            "/login",
            data=json.dumps({"username": "jsmith", "password": "oldpass"}),
            content_type="application/json",
        )

        resp = client.post(
            "/api/change-password",
            data=json.dumps(
                {"current_password": "wrongpass", "new_password": "newpass"}
            ),
            content_type="application/json",
        )
        assert resp.status_code == 401

    def test_change_password_missing_fields(self, client):
        _login_admin(client)
        resp = client.post(
            "/api/change-password",
            data=json.dumps({"current_password": "admin"}),
            content_type="application/json",
        )
        assert resp.status_code == 400

    def test_change_password_requires_auth(self, client):
        resp = client.post(
            "/api/change-password",
            data=json.dumps({"current_password": "old", "new_password": "new"}),
            content_type="application/json",
        )
        assert resp.status_code == 401

    def test_change_password_superadmin(self, client, webapp_db, tmp_path):
        conf = tmp_path / "dracs.conf"
        conf.write_text("WEBADMIN_USER=admin\nWEBADMIN_PASSWORD=admin\n")

        _login_admin(client)

        with patch.dict(os.environ, {"DRACS_CONF": str(conf)}):
            resp = client.post(
                "/api/change-password",
                data=json.dumps(
                    {"current_password": "admin", "new_password": "newadmin"}
                ),
                content_type="application/json",
            )
        assert resp.status_code == 200
        content = conf.read_text()
        assert "WEBADMIN_PASSWORD=newadmin" in content

    def test_change_password_via_token(self, client, webapp_db):
        create_user("jsmith", "oldpass", "user")
        login_resp = client.post(
            "/api/token-login",
            data=json.dumps({"username": "jsmith", "password": "oldpass"}),
            content_type="application/json",
        )
        token = login_resp.get_json()["token"]

        resp = client.post(
            "/api/change-password",
            data=json.dumps({"current_password": "oldpass", "new_password": "newpass"}),
            content_type="application/json",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200

    def test_change_password_no_json(self, client):
        _login_admin(client)
        resp = client.post(
            "/api/change-password",
            data="not json",
            content_type="text/plain",
        )
        assert resp.status_code in (400, 500)

    def test_change_password_empty_new_password(self, client, webapp_db):
        create_user("jsmith", "oldpass", "user")
        client.post(
            "/login",
            data=json.dumps({"username": "jsmith", "password": "oldpass"}),
            content_type="application/json",
        )
        resp = client.post(
            "/api/change-password",
            data=json.dumps({"current_password": "oldpass", "new_password": ""}),
            content_type="application/json",
        )
        assert resp.status_code == 400

    def test_change_password_null_body(self, client):
        _login_admin(client)
        resp = client.post(
            "/api/change-password",
            data=json.dumps(None),
            content_type="application/json",
        )
        assert resp.status_code == 400

    def test_change_password_sso_user_no_current_password_required(
        self, client, webapp_db
    ):
        create_user("ssouser", "randompw", "user")
        with client.session_transaction() as sess:
            sess["authenticated"] = True
            sess["username"] = "ssouser"
            sess["role"] = "user"
            sess["is_superadmin"] = False
            sess["sso_login"] = True
        resp = client.post(
            "/api/change-password",
            data=json.dumps({"new_password": "newsecurepass"}),
            content_type="application/json",
        )
        assert resp.status_code == 200
        assert resp.get_json()["success"] is True

    def test_change_password_sso_user_missing_new_password(self, client, webapp_db):
        create_user("ssouser2", "randompw", "user")
        with client.session_transaction() as sess:
            sess["authenticated"] = True
            sess["username"] = "ssouser2"
            sess["role"] = "user"
            sess["is_superadmin"] = False
            sess["sso_login"] = True
        resp = client.post(
            "/api/change-password",
            data=json.dumps({}),
            content_type="application/json",
        )
        assert resp.status_code == 400


class TestTokenLoginEdgeCases:
    def test_token_login_null_body(self, client):
        resp = client.post(
            "/api/token-login",
            data=json.dumps(None),
            content_type="application/json",
        )
        assert resp.status_code == 400

    def test_token_logout_internal_error(self, client, webapp_db):
        create_user("jsmith", "secret", "user")
        login_resp = client.post(
            "/api/token-login",
            data=json.dumps({"username": "jsmith", "password": "secret"}),
            content_type="application/json",
        )
        token = login_resp.get_json()["token"]

        with patch("dracs.tokens.validate_token", side_effect=RuntimeError("db error")):
            resp = client.post(
                "/api/token-logout",
                headers={"Authorization": f"Bearer {token}"},
            )
            assert resp.status_code == 500


class TestTokenRefreshError:
    def test_refresh_exception_logged(self, client, webapp_db):
        with patch("dracs.tokens.refresh_token", side_effect=RuntimeError("boom")):
            resp = client.get(
                "/api/systems",
                headers={"Authorization": "Bearer sometoken"},
            )
        assert resp.status_code == 200

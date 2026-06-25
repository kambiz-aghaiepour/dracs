"""Tests for RBAC and user management webapp endpoints."""

import json
import os
import tempfile
from unittest.mock import patch

import pytest

from dracs.db import db_initialize, get_default_site_id, upsert_system
from dracs.users import (
    create_user,
    get_user_site_roles,
    remove_user_site_role,
    set_user_site_role,
)


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
    with patch.dict(
        os.environ,
        {
            "DRACS_DB": webapp_db,
            "DRACS_DNS_STRING": "mgmt-",
            "DRACS_DNS_MODE": "prefix",
            "DRACS_LOG_DIR": tempfile.mkdtemp(),
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


def _login_user(client, webapp_db):
    create_user("testuser", "testpass", "user")
    try:
        set_user_site_role("testuser", get_default_site_id(), "user")
    except RuntimeError:
        pass
    client.post(
        "/login",
        data=json.dumps({"username": "testuser", "password": "testpass"}),
        content_type="application/json",
    )


class TestLoginRole:
    def test_login_sets_role_admin(self, client):
        resp = client.post(
            "/login",
            data=json.dumps({"username": "admin", "password": "admin"}),
            content_type="application/json",
        )
        assert resp.status_code == 200
        status = client.get("/api/auth-status").get_json()
        assert status["role"] == "admin"

    def test_login_sets_role_user(self, client, webapp_db):
        create_user("jsmith", "secret", "user")
        resp = client.post(
            "/login",
            data=json.dumps({"username": "jsmith", "password": "secret"}),
            content_type="application/json",
        )
        assert resp.status_code == 200
        status = client.get("/api/auth-status").get_json()
        assert status["role"] == "user"

    def test_auth_status_includes_role(self, client):
        _login_admin(client)
        status = client.get("/api/auth-status").get_json()
        assert "role" in status
        assert status["role"] == "admin"

    def test_auth_status_unauthenticated_no_role(self, client):
        status = client.get("/api/auth-status").get_json()
        assert status["role"] is None


class TestRoleBasedAccess:
    def test_user_role_denied_admin_endpoints(self, client, webapp_db):
        _login_user(client, webapp_db)
        admin_endpoints = [
            ("/api/refresh", {"service_tag": "TAG001"}),
            ("/api/refresh-multiple", {"systems": [{"hostname": "server01"}]}),
            ("/api/refresh-all", {}),
            (
                "/api/firmware-update",
                {"hostname": "server01", "target_version": "7.1.0", "model": "R660"},
            ),
            (
                "/api/bios-update",
                {"hostname": "server01", "target_bios": "2.2.0", "model": "R660"},
            ),
            ("/api/clear-job-queue", {"hostnames": ["server01"]}),
        ]
        for endpoint, data in admin_endpoints:
            resp = client.post(
                endpoint,
                data=json.dumps(data),
                content_type="application/json",
            )
            assert (
                resp.status_code == 403
            ), f"{endpoint} should return 403 for user role"

    def test_user_role_allowed_user_endpoints(self, client, webapp_db):
        _login_user(client, webapp_db)
        resp = client.post(
            "/api/tsr-status",
            data=json.dumps({"hostname": "server01"}),
            content_type="application/json",
        )
        assert resp.status_code != 401
        assert resp.status_code != 403

    def test_admin_role_allowed_everywhere(self, client):
        _login_admin(client)
        resp = client.post(
            "/api/tsr-status",
            data=json.dumps({"hostname": "server01"}),
            content_type="application/json",
        )
        assert resp.status_code != 401
        assert resp.status_code != 403

    def test_unauthenticated_gets_401(self, client):
        resp = client.post(
            "/api/refresh",
            data=json.dumps({"service_tag": "TAG001"}),
            content_type="application/json",
        )
        assert resp.status_code == 401

    def test_user_role_can_access_test_idrac(self, client, webapp_db):
        _login_user(client, webapp_db)
        resp = client.post(
            "/api/test-idrac",
            data=json.dumps({"hostname": "server01"}),
            content_type="application/json",
        )
        assert resp.status_code != 401
        assert resp.status_code != 403

    def test_user_role_can_access_job_queue(self, client, webapp_db):
        _login_user(client, webapp_db)
        resp = client.post(
            "/api/job-queue",
            data=json.dumps({"hostname": "server01"}),
            content_type="application/json",
        )
        assert resp.status_code != 401
        assert resp.status_code != 403

    def test_firmware_versions_admin_only(self, client, webapp_db):
        _login_user(client, webapp_db)
        resp = client.get("/api/firmware-versions/R660")
        assert resp.status_code == 403

    def test_bios_versions_admin_only(self, client, webapp_db):
        _login_user(client, webapp_db)
        resp = client.get("/api/bios-versions/R660")
        assert resp.status_code == 403


class TestUserManagementAPI:
    def test_list_users_empty(self, client):
        _login_admin(client)
        resp = client.get("/api/users")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        assert data["users"] == []

    def test_create_user(self, client):
        _login_admin(client)
        resp = client.post(
            "/api/users",
            data=json.dumps(
                {"username": "newuser", "password": "pass123", "role": "user"}
            ),
            content_type="application/json",
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True

        users_resp = client.get("/api/users")
        users = users_resp.get_json()["users"]
        assert len(users) == 1
        assert users[0]["username"] == "newuser"
        assert users[0]["role"] == "user"

    def test_create_user_none_role(self, client, webapp_db):
        _login_admin(client)
        resp = client.post(
            "/api/users",
            data=json.dumps(
                {"username": "quadsuser", "password": "pass123", "role": None}
            ),
            content_type="application/json",
        )
        assert resp.status_code == 200
        users = client.get("/api/users").get_json()["users"]
        match = next(u for u in users if u["username"] == "quadsuser")
        assert match["role"] is None
        assert match["site_roles"] == []

    def test_create_user_duplicate(self, client, webapp_db):
        _login_admin(client)
        create_user("existing", "pass", "user")
        resp = client.post(
            "/api/users",
            data=json.dumps(
                {"username": "existing", "password": "pass", "role": "user"}
            ),
            content_type="application/json",
        )
        assert resp.status_code == 400

    def test_create_user_superadmin_username_rejected(self, client):
        _login_admin(client)
        resp = client.post(
            "/api/users",
            data=json.dumps({"username": "admin", "password": "pass", "role": "user"}),
            content_type="application/json",
        )
        assert resp.status_code == 400
        assert "superadmin" in resp.get_json()["message"]

    def test_create_user_with_site_role(self, client, webapp_db):
        """POST /api/users with site_role sets quads site role on creation."""
        _login_admin(client)
        resp = client.post(
            "/api/users",
            data=json.dumps(
                {
                    "username": "quadsuser",
                    "password": "pass123",
                    "role": None,
                    "site_role": {"site_name": "Default", "role": "quads"},
                }
            ),
            content_type="application/json",
        )
        assert resp.status_code == 200
        users = client.get("/api/users").get_json()["users"]
        match = next(u for u in users if u["username"] == "quadsuser")
        assert match["role"] is None
        site_roles = {r["role"] for r in match["site_roles"]}
        assert "quads" in site_roles

    def test_delete_user(self, client, webapp_db):
        _login_admin(client)
        create_user("todelete", "pass", "user")
        resp = client.delete("/api/users/todelete")
        assert resp.status_code == 200
        assert resp.get_json()["success"] is True

    def test_delete_user_not_found(self, client):
        _login_admin(client)
        resp = client.delete("/api/users/nobody")
        assert resp.status_code == 404

    def test_delete_superadmin_rejected(self, client):
        _login_admin(client)
        resp = client.delete("/api/users/admin")
        assert resp.status_code == 400

    def test_delete_self_rejected(self, client):
        _login_admin(client)
        resp = client.delete("/api/users/admin")
        assert resp.status_code == 400

    def test_update_user_role(self, client, webapp_db):
        _login_admin(client)
        create_user("updaterole", "pass", "user")
        resp = client.patch(
            "/api/users/updaterole",
            data=json.dumps({"role": "admin"}),
            content_type="application/json",
        )
        assert resp.status_code == 200
        assert resp.get_json()["success"] is True

    def test_update_user_password(self, client, webapp_db):
        _login_admin(client)
        create_user("updatepass", "oldpass", "user")
        resp = client.patch(
            "/api/users/updatepass",
            data=json.dumps({"password": "newpass"}),
            content_type="application/json",
        )
        assert resp.status_code == 200
        assert resp.get_json()["success"] is True

    def test_update_superadmin_rejected(self, client):
        _login_admin(client)
        resp = client.patch(
            "/api/users/admin",
            data=json.dumps({"role": "user"}),
            content_type="application/json",
        )
        assert resp.status_code == 400

    def test_update_no_changes(self, client, webapp_db):
        _login_admin(client)
        create_user("nochange", "pass", "user")
        resp = client.patch(
            "/api/users/nochange",
            data=json.dumps({}),
            content_type="application/json",
        )
        assert resp.status_code == 400

    def test_user_endpoints_require_admin(self, client, webapp_db):
        _login_user(client, webapp_db)
        assert client.get("/api/users").status_code == 403
        assert (
            client.post(
                "/api/users",
                data=json.dumps({"username": "x", "password": "y", "role": "user"}),
                content_type="application/json",
            ).status_code
            == 403
        )
        assert client.delete("/api/users/someone").status_code == 403
        assert (
            client.patch(
                "/api/users/someone",
                data=json.dumps({"role": "admin"}),
                content_type="application/json",
            ).status_code
            == 403
        )

    def test_user_endpoints_require_auth(self, client):
        assert client.get("/api/users").status_code == 401
        assert (
            client.post(
                "/api/users",
                data=json.dumps({"username": "x", "password": "y", "role": "user"}),
                content_type="application/json",
            ).status_code
            == 401
        )

    def test_create_user_no_json(self, client):
        _login_admin(client)
        resp = client.post("/api/users", data="not json", content_type="text/plain")
        assert resp.status_code in (400, 500)

    def test_update_user_no_json(self, client, webapp_db):
        _login_admin(client)
        create_user("nojson", "pass", "user")
        resp = client.patch(
            "/api/users/nojson", data="not json", content_type="text/plain"
        )
        assert resp.status_code in (400, 500)

    def test_update_user_not_found(self, client):
        _login_admin(client)
        resp = client.patch(
            "/api/users/nobody",
            data=json.dumps({"password": "x"}),
            content_type="application/json",
        )
        assert resp.status_code == 404

    def test_update_role_not_found(self, client):
        _login_admin(client)
        resp = client.patch(
            "/api/users/nobody",
            data=json.dumps({"role": "admin"}),
            content_type="application/json",
        )
        assert resp.status_code == 404

    def test_delete_superadmin_via_validation(self, client, webapp_db):
        create_user("otheradmin", "pass", "admin")
        try:
            set_user_site_role("otheradmin", get_default_site_id(), "admin")
        except RuntimeError:
            pass
        client.post(
            "/login",
            data=json.dumps({"username": "otheradmin", "password": "pass"}),
            content_type="application/json",
        )
        resp = client.delete("/api/users/admin")
        data = resp.get_json()
        assert resp.status_code == 400
        assert "superadmin" in data["message"].lower()

    def test_create_user_empty_json_body(self, client):
        _login_admin(client)
        resp = client.post(
            "/api/users",
            data=json.dumps(None),
            content_type="application/json",
        )
        assert resp.status_code == 400

    def test_list_users_internal_error(self, client):
        _login_admin(client)
        with patch("dracs.webapp.list_users", side_effect=RuntimeError("db gone")):
            resp = client.get("/api/users")
            assert resp.status_code == 500
            assert "db gone" in resp.get_json()["message"]

    def test_delete_user_internal_error(self, client, webapp_db):
        create_user("victim", "pass", "user")
        _login_admin(client)
        with patch("dracs.webapp.delete_user", side_effect=RuntimeError("boom")):
            resp = client.delete("/api/users/victim")
            assert resp.status_code == 500

    def test_update_user_role_to_none_clears_role(self, client, webapp_db):
        create_user("emptyupd", "pass", "user")
        _login_admin(client)
        resp = client.patch(
            "/api/users/emptyupd",
            data=json.dumps({"role": None}),
            content_type="application/json",
        )
        assert resp.status_code == 200
        users = client.get("/api/users").get_json()["users"]
        match = next(u for u in users if u["username"] == "emptyupd")
        assert match["role"] is None


class TestClientIP:
    def test_client_ip_from_proxy_fix(self, client):
        _login_admin(client)
        with patch("dracs.webapp.audit_log") as mock_audit:
            client.post(
                "/logout",
                headers={"X-Forwarded-For": "192.168.1.100"},
            )
            call_kwargs = mock_audit.call_args
            assert call_kwargs.kwargs.get("source") == "192.168.1.100"

    def test_client_ip_without_proxy(self, client):
        _login_admin(client)
        with patch("dracs.webapp.audit_log") as mock_audit:
            client.post("/logout")
            call_kwargs = mock_audit.call_args
            assert call_kwargs.kwargs.get("source") == "127.0.0.1"


class TestIndexRoleContext:
    def test_index_passes_user_role(self, client):
        _login_admin(client)
        resp = client.get("/")
        assert resp.status_code == 200
        assert b"admin" in resp.data

    def test_index_shows_authenticated_for_no_site_role_user(self, client, webapp_db):
        create_user("norole", "testpass", "user")
        site_id = get_default_site_id()
        if site_id is not None:
            remove_user_site_role("norole", site_id)
        client.post(
            "/login",
            data=json.dumps({"username": "norole", "password": "testpass"}),
            content_type="application/json",
        )
        resp = client.get("/")
        assert resp.status_code == 200
        assert b"Logged in as" in resp.data
        assert b"norole" in resp.data
        assert b"handleLogout" in resp.data


class TestUserCreationSiteRoles:
    def test_create_with_empty_site_roles_produces_no_site_roles(
        self, client, webapp_db
    ):
        _login_admin(client)
        resp = client.post(
            "/api/users",
            data=json.dumps(
                {
                    "username": "ghostuser",
                    "password": "pass123",
                    "role": "user",
                    "site_roles": [],
                }
            ),
            content_type="application/json",
        )
        assert resp.get_json()["success"] is True
        assert get_user_site_roles("ghostuser") == []

    def test_create_without_site_roles_key_keeps_default(self, client, webapp_db):
        _login_admin(client)
        resp = client.post(
            "/api/users",
            data=json.dumps(
                {"username": "defaultuser", "password": "pass123", "role": "user"}
            ),
            content_type="application/json",
        )
        assert resp.get_json()["success"] is True
        assert get_user_site_roles("defaultuser") != []

    def test_create_without_site_roles_key_no_primary_site(self, client, webapp_db):
        _login_admin(client)
        with patch("dracs.db.get_default_site_id", side_effect=RuntimeError("no site")):
            resp = client.post(
                "/api/users",
                data=json.dumps(
                    {"username": "nosituser", "password": "pass123", "role": "user"}
                ),
                content_type="application/json",
            )
        assert resp.get_json()["success"] is True
        assert get_user_site_roles("nosituser") == []


class TestSiteRolePatch:
    def test_patch_site_role_sets_specific_site(self, client, webapp_db):
        from dracs.db import create_site

        create_user("siteroleuser", "pass", "user")
        sec = create_site("SecSite")
        _login_admin(client)
        resp = client.patch(
            "/api/users/siteroleuser",
            data=json.dumps({"site_role": {"site_name": "SecSite", "role": "admin"}}),
            content_type="application/json",
        )
        assert resp.status_code == 200
        roles = get_user_site_roles("siteroleuser")
        site_entry = next((r for r in roles if r["site_name"] == "SecSite"), None)
        assert site_entry is not None
        assert site_entry["role"] == "admin"

    def test_patch_site_role_none_removes_site_role(self, client, webapp_db):
        from dracs.db import create_site

        create_user("siteroleuser2", "pass", "user")
        sec = create_site("SecSite2")
        set_user_site_role("siteroleuser2", sec["id"], "user")
        _login_admin(client)
        resp = client.patch(
            "/api/users/siteroleuser2",
            data=json.dumps({"site_role": {"site_name": "SecSite2", "role": None}}),
            content_type="application/json",
        )
        assert resp.status_code == 200
        roles = get_user_site_roles("siteroleuser2")
        assert not any(r["site_name"] == "SecSite2" for r in roles)

    def test_patch_site_role_unknown_site_returns_404(self, client, webapp_db):
        create_user("siteroleuser3", "pass", "user")
        _login_admin(client)
        resp = client.patch(
            "/api/users/siteroleuser3",
            data=json.dumps({"site_role": {"site_name": "NoSuchSite", "role": "user"}}),
            content_type="application/json",
        )
        assert resp.status_code == 404

    def test_patch_site_role_does_not_touch_other_sites(self, client, webapp_db):
        from dracs.db import create_site

        create_user("siteroleuser4", "pass", "user")
        sec1 = create_site("SiteA")
        sec2 = create_site("SiteB")
        set_user_site_role("siteroleuser4", sec1["id"], "user")
        set_user_site_role("siteroleuser4", sec2["id"], "admin")
        _login_admin(client)
        resp = client.patch(
            "/api/users/siteroleuser4",
            data=json.dumps({"site_role": {"site_name": "SiteA", "role": "admin"}}),
            content_type="application/json",
        )
        assert resp.status_code == 200
        roles = get_user_site_roles("siteroleuser4")
        site_a = next(r for r in roles if r["site_name"] == "SiteA")
        site_b = next(r for r in roles if r["site_name"] == "SiteB")
        assert site_a["role"] == "admin"
        assert site_b["role"] == "admin"

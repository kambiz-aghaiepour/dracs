import json
import os
import tempfile
from unittest.mock import patch

import pytest

from dracs.db import create_site, db_initialize, get_default_site_id, upsert_system
from dracs.users import create_user, set_user_site_role


@pytest.fixture
def users_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    db_initialize(path)
    yield path
    if os.path.exists(path):
        os.unlink(path)


@pytest.fixture
def users_client(users_db):
    with patch.dict(
        os.environ,
        {
            "DRACS_DB": users_db,
            "DRACS_DNS_STRING": "mgmt-",
            "DRACS_DNS_MODE": "prefix",
            "WEBADMIN_USER": "admin",
            "WEBADMIN_PASSWORD": "admin",
        },
    ):
        import dracs.webapp as webapp_mod

        webapp_mod.DB_PATH = users_db
        webapp_mod.db_initialize(users_db)
        webapp_mod.app.config["TESTING"] = True
        with webapp_mod.app.test_client() as c:
            yield c


def _login(client, username="admin", password="admin"):
    client.post(
        "/login",
        data=json.dumps({"username": username, "password": password}),
        content_type="application/json",
    )


class TestUsersPageRoute:
    def test_unauthenticated_redirects(self, users_client):
        resp = users_client.get("/users")
        assert resp.status_code == 302

    def test_non_admin_redirects(self, users_client):
        create_user("viewer", "pass123", role="user")
        _login(users_client, "viewer", "pass123")
        resp = users_client.get("/users")
        assert resp.status_code == 302

    def test_superadmin_access(self, users_client):
        _login(users_client)
        resp = users_client.get("/users")
        assert resp.status_code == 200
        assert b"User Management" in resp.data

    def test_admin_user_access(self, users_client):
        create_user("adminuser", "pass123", role="admin")
        default_id = get_default_site_id()
        set_user_site_role("adminuser", default_id, "admin")
        _login(users_client, "adminuser", "pass123")
        resp = users_client.get("/users")
        assert resp.status_code == 200

    def test_admin_sees_only_their_sites(self, users_client):
        site2 = create_site("Site2")
        create_user("adminuser", "pass123", role="admin")
        default_id = get_default_site_id()
        set_user_site_role("adminuser", default_id, "admin")
        _login(users_client, "adminuser", "pass123")
        resp = users_client.get("/users")
        assert b"Default" in resp.data
        assert b"Site2" not in resp.data


class TestUsersApiWithSiteRoles:
    def test_list_users_includes_site_roles(self, users_client):
        _login(users_client)
        create_user("testuser", "pass123", role="user")
        default_id = get_default_site_id()
        set_user_site_role("testuser", default_id, "admin")

        resp = users_client.get("/api/users")
        data = resp.get_json()
        assert data["success"] is True
        user = next(u for u in data["users"] if u["username"] == "testuser")
        assert len(user["site_roles"]) >= 1
        assert any(r["site_name"] == "Default" for r in user["site_roles"])

    def test_create_user_with_site_roles(self, users_client):
        _login(users_client)
        site2 = create_site("Site2")
        resp = users_client.post(
            "/api/users",
            data=json.dumps(
                {
                    "username": "newuser",
                    "password": "pass123",
                    "role": "user",
                    "site_roles": [{"site_id": site2["id"], "role": "admin"}],
                }
            ),
            content_type="application/json",
        )
        assert resp.get_json()["success"] is True

        resp2 = users_client.get("/api/users/newuser/site-roles")
        data = resp2.get_json()
        roles = data["site_roles"]
        site_names = {r["site_name"] for r in roles}
        assert "Site2" in site_names

    def test_update_user_site_roles(self, users_client):
        _login(users_client)
        create_user("testuser", "pass123", role="user")
        site2 = create_site("Site2")

        resp = users_client.patch(
            "/api/users/testuser",
            data=json.dumps(
                {"site_roles": [{"site_id": site2["id"], "role": "admin"}]}
            ),
            content_type="application/json",
        )
        data = resp.get_json()
        assert data["success"] is True
        assert "site_roles" in data["message"]

    def test_update_user_replaces_site_roles(self, users_client):
        _login(users_client)
        create_user("testuser", "pass123", role="user")
        default_id = get_default_site_id()
        site2 = create_site("Site2")
        set_user_site_role("testuser", default_id, "admin")
        set_user_site_role("testuser", site2["id"], "user")

        resp = users_client.patch(
            "/api/users/testuser",
            data=json.dumps(
                {"site_roles": [{"site_id": site2["id"], "role": "admin"}]}
            ),
            content_type="application/json",
        )
        assert resp.get_json()["success"] is True

        resp2 = users_client.get("/api/users/testuser/site-roles")
        roles = resp2.get_json()["site_roles"]
        assert len(roles) == 1
        assert roles[0]["site_name"] == "Site2"
        assert roles[0]["role"] == "admin"


class TestUserSiteRolesEndpoint:
    def test_get_site_roles(self, users_client):
        _login(users_client)
        create_user("testuser", "pass123", role="user")
        default_id = get_default_site_id()
        set_user_site_role("testuser", default_id, "admin")

        resp = users_client.get("/api/users/testuser/site-roles")
        data = resp.get_json()
        assert data["success"] is True
        assert len(data["site_roles"]) >= 1

    def test_get_site_roles_unauthenticated(self, users_client):
        resp = users_client.get("/api/users/testuser/site-roles")
        assert resp.status_code == 401

    def test_get_site_roles_nonexistent_user(self, users_client):
        _login(users_client)
        resp = users_client.get("/api/users/nouser/site-roles")
        data = resp.get_json()
        assert data["success"] is True
        assert data["site_roles"] == []

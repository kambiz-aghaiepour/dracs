import json
import os
import tempfile
from unittest.mock import patch

import pytest

from dracs.db import db_initialize, create_site, get_default_site_id, upsert_system
from dracs.exceptions import ValidationError
from dracs.users import (
    create_user,
    get_user_role_for_site,
    get_user_site_roles,
    remove_user_site_role,
    set_user_site_role,
)


@pytest.fixture
def auth_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    db_initialize(path)
    yield path
    if os.path.exists(path):
        os.unlink(path)


class TestSetUserSiteRole:
    def test_set_role(self, auth_db):
        create_user("testuser", "password123", role="user")
        default_id = get_default_site_id()

        set_user_site_role("testuser", default_id, "admin")

        role = get_user_role_for_site("testuser", default_id)
        assert role == "admin"

    def test_update_existing_role(self, auth_db):
        create_user("testuser", "password123", role="user")
        default_id = get_default_site_id()

        set_user_site_role("testuser", default_id, "admin")
        set_user_site_role("testuser", default_id, "user")

        role = get_user_role_for_site("testuser", default_id)
        assert role == "user"

    def test_set_role_on_second_site(self, auth_db):
        create_user("testuser", "password123", role="user")
        site2 = create_site("Site2")

        set_user_site_role("testuser", site2["id"], "admin")

        role = get_user_role_for_site("testuser", site2["id"])
        assert role == "admin"

    def test_invalid_role_raises(self, auth_db):
        create_user("testuser", "password123", role="user")
        default_id = get_default_site_id()

        with pytest.raises(ValidationError, match="Invalid role"):
            set_user_site_role("testuser", default_id, "superadmin")

    def test_nonexistent_user_raises(self, auth_db):
        default_id = get_default_site_id()

        with pytest.raises(ValidationError, match="not found"):
            set_user_site_role("nouser", default_id, "admin")

    def test_nonexistent_site_raises(self, auth_db):
        create_user("testuser", "password123", role="user")

        with pytest.raises(ValidationError, match="not found"):
            set_user_site_role("testuser", 9999, "admin")


class TestRemoveUserSiteRole:
    def test_remove_existing(self, auth_db):
        create_user("testuser", "password123", role="user")
        site2 = create_site("Site2")
        set_user_site_role("testuser", site2["id"], "admin")

        result = remove_user_site_role("testuser", site2["id"])
        assert result is True
        assert get_user_role_for_site("testuser", site2["id"]) is None

    def test_remove_nonexistent_mapping(self, auth_db):
        create_user("testuser", "password123", role="user")
        site2 = create_site("Site2")

        result = remove_user_site_role("testuser", site2["id"])
        assert result is False

    def test_remove_nonexistent_user(self, auth_db):
        default_id = get_default_site_id()

        result = remove_user_site_role("nouser", default_id)
        assert result is False


class TestGetUserSiteRoles:
    def test_returns_all_site_roles(self, auth_db):
        create_user("testuser", "password123", role="admin")
        site2 = create_site("Site2")
        default_id = get_default_site_id()

        set_user_site_role("testuser", default_id, "admin")
        set_user_site_role("testuser", site2["id"], "user")

        roles = get_user_site_roles("testuser")
        role_map = {r["site_name"]: r["role"] for r in roles}
        assert role_map["Default"] == "admin"
        assert role_map["Site2"] == "user"

    def test_nonexistent_user_returns_empty(self, auth_db):
        roles = get_user_site_roles("nouser")
        assert roles == []


class TestGetUserRoleForSite:
    def test_user_with_role(self, auth_db):
        create_user("testuser", "password123", role="admin")
        default_id = get_default_site_id()
        set_user_site_role("testuser", default_id, "admin")

        role = get_user_role_for_site("testuser", default_id)
        assert role == "admin"

    def test_user_without_role(self, auth_db):
        create_user("testuser", "password123", role="user")
        site2 = create_site("Site2")

        role = get_user_role_for_site("testuser", site2["id"])
        assert role is None

    def test_nonexistent_user(self, auth_db):
        default_id = get_default_site_id()

        role = get_user_role_for_site("nouser", default_id)
        assert role is None


class TestRequireAuthSiteAware:
    @pytest.fixture
    def webapp_client(self, auth_db):
        with patch.dict(
            os.environ,
            {
                "DRACS_DB": auth_db,
                "DRACS_DNS_STRING": "mgmt-",
                "DRACS_DNS_MODE": "prefix",
                "WEBADMIN_USER": "admin",
                "WEBADMIN_PASSWORD": "admin",
            },
        ):
            import dracs.webapp as webapp_mod

            webapp_mod.DB_PATH = auth_db
            webapp_mod.db_initialize(auth_db)
            webapp_mod.app.config["TESTING"] = True
            with webapp_mod.app.test_client() as c:
                yield c

    def _login(self, client, username="admin", password="admin"):
        client.post(
            "/login",
            data=json.dumps({"username": username, "password": password}),
            content_type="application/json",
        )

    def test_superadmin_can_access_any_site(self, webapp_client):
        self._login(webapp_client)
        site2 = create_site("Site2")
        upsert_system(
            "",
            "TAG001",
            "host01",
            "R660",
            "7.0.0",
            "2.1.0",
            "Jan 1, 2027",
            1893456000,
            site_id=site2["id"],
        )

        resp = webapp_client.get(f"/?site=Site2")
        assert resp.status_code == 200

    def test_superadmin_session_flag(self, webapp_client):
        self._login(webapp_client)

        with webapp_client.session_transaction() as sess:
            assert sess.get("is_superadmin") is True

    def test_regular_user_no_superadmin_flag(self, webapp_client):
        create_user("testuser", "testpass", role="admin")
        default_id = get_default_site_id()
        set_user_site_role("testuser", default_id, "admin")
        self._login(webapp_client, "testuser", "testpass")

        with webapp_client.session_transaction() as sess:
            assert sess.get("is_superadmin") is False

    def test_require_auth_user_no_site_role_denied(self, webapp_client):
        create_user("testuser", "testpass", role="user")
        site2 = create_site("Site2")

        self._login(webapp_client, "testuser", "testpass")

        import dracs.webapp as webapp_mod

        with webapp_mod.app.test_request_context("/?site=Site2"):
            from flask import session as flask_session

            flask_session["authenticated"] = True
            flask_session["username"] = "testuser"
            flask_session["role"] = "user"
            flask_session["is_superadmin"] = False

            from dracs.webapp import _require_auth

            _, err = _require_auth(required_role="admin", site_id=site2["id"])
            assert err is not None
            assert err[1] == 401

    def test_require_auth_user_on_site_gets_403(self, webapp_client):
        create_user("testuser", "testpass", role="admin")
        default_id = get_default_site_id()
        site2 = create_site("Site2")
        set_user_site_role("testuser", default_id, "admin")
        set_user_site_role("testuser", site2["id"], "user")

        import dracs.webapp as webapp_mod

        with webapp_mod.app.test_request_context("/?site=Site2"):
            from flask import session as flask_session

            flask_session["authenticated"] = True
            flask_session["username"] = "testuser"
            flask_session["role"] = "admin"
            flask_session["is_superadmin"] = False

            from dracs.webapp import _require_auth

            _, err = _require_auth(required_role="admin", site_id=site2["id"])
            assert err is not None
            assert err[1] == 403

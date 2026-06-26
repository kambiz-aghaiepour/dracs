"""Tests for QUADS integration in the webapp."""

import json
import os
import tempfile
import time
from unittest.mock import MagicMock, patch

import pytest

from dracs.db import db_initialize, upsert_system

# ---------------------------------------------------------------------------
# Cache unit tests
# ---------------------------------------------------------------------------


class TestQuadsCacheFunctions:
    def setup_method(self):
        import dracs.webapp as webapp_mod

        webapp_mod._quads_host_cache.clear()

    def teardown_method(self):
        import dracs.webapp as webapp_mod

        webapp_mod._quads_host_cache.clear()

    def test_cache_miss_returns_none(self):
        from dracs.webapp import _quads_cache_get

        assert _quads_cache_get("nobody", 1) is None

    def test_cache_hit_returns_frozenset(self):
        from dracs.webapp import _quads_cache_get, _quads_cache_set

        _quads_cache_set("alice", 1, frozenset(["host1", "host2"]))
        result = _quads_cache_get("alice", 1)
        assert result == frozenset(["host1", "host2"])

    def test_cache_different_sites_independent(self):
        from dracs.webapp import _quads_cache_get, _quads_cache_set

        _quads_cache_set("alice", 1, frozenset(["host1"]))
        _quads_cache_set("alice", 2, frozenset(["host2"]))
        assert _quads_cache_get("alice", 1) == frozenset(["host1"])
        assert _quads_cache_get("alice", 2) == frozenset(["host2"])

    def test_cache_expired_returns_none(self):
        import dracs.webapp as webapp_mod
        from dracs.webapp import _quads_cache_get

        webapp_mod._quads_host_cache[("bob", 1)] = (
            frozenset(["host1"]),
            time.time() - 90000,
        )
        assert _quads_cache_get("bob", 1) is None

    def test_cache_expired_removes_entry(self):
        import dracs.webapp as webapp_mod
        from dracs.webapp import _quads_cache_get

        webapp_mod._quads_host_cache[("carol", 1)] = (
            frozenset(["host1"]),
            time.time() - 90000,
        )
        _quads_cache_get("carol", 1)
        assert ("carol", 1) not in webapp_mod._quads_host_cache

    def test_cache_invalidate_removes_all_sites(self):
        from dracs.webapp import (
            _quads_cache_get,
            _quads_cache_invalidate,
            _quads_cache_set,
        )

        _quads_cache_set("dave", 1, frozenset(["host1"]))
        _quads_cache_set("dave", 2, frozenset(["host2"]))
        _quads_cache_invalidate("dave")
        assert _quads_cache_get("dave", 1) is None
        assert _quads_cache_get("dave", 2) is None

    def test_cache_invalidate_nonexistent_is_noop(self):
        from dracs.webapp import _quads_cache_invalidate

        _quads_cache_invalidate("nobody")

    def test_get_quads_hosts_uses_cache(self):
        from dracs.webapp import _get_quads_hosts_for_user, _quads_cache_set

        _quads_cache_set("eve", 1, frozenset(["cached-host"]))
        with patch("dracs.webapp._fetch_quads_hosts") as mock_fetch:
            result = _get_quads_hosts_for_user("eve", 1, "http://quads.test")
        mock_fetch.assert_not_called()
        assert result == frozenset(["cached-host"])

    def test_get_quads_hosts_fetches_on_miss(self):
        from dracs.webapp import _get_quads_hosts_for_user

        with patch(
            "dracs.webapp._fetch_quads_hosts", return_value=frozenset(["fetched"])
        ) as mock_fetch:
            result = _get_quads_hosts_for_user("frank", 1, "http://quads.test")
        mock_fetch.assert_called_once_with("frank", "http://quads.test")
        assert result == frozenset(["fetched"])

    def test_get_quads_hosts_caches_after_fetch(self):
        import dracs.webapp as webapp_mod
        from dracs.webapp import _get_quads_hosts_for_user

        with patch(
            "dracs.webapp._fetch_quads_hosts", return_value=frozenset(["host1"])
        ):
            _get_quads_hosts_for_user("grace", 1, "http://quads.test")
        assert ("grace", 1) in webapp_mod._quads_host_cache

    def test_get_quads_hosts_does_not_cache_none(self):
        import dracs.webapp as webapp_mod
        from dracs.webapp import _get_quads_hosts_for_user

        with patch("dracs.webapp._fetch_quads_hosts", return_value=None):
            result = _get_quads_hosts_for_user("henry", 1, "http://quads.test")
        assert result is None
        assert ("henry", 1) not in webapp_mod._quads_host_cache


# ---------------------------------------------------------------------------
# Fetch unit tests
# ---------------------------------------------------------------------------


class TestFetchQuadsHosts:
    def _make_mock_resp(self, data):
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(data).encode()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        return mock_resp

    def test_empty_url_returns_none(self):
        from dracs.webapp import _fetch_quads_hosts

        assert _fetch_quads_hosts("alice", "") is None

    def test_owner_match(self):
        from dracs.webapp import _fetch_quads_hosts

        schedules = [
            {"host": {"name": "host1"}, "assignment": {"owner": "alice", "ccuser": []}},
            {"host": {"name": "host2"}, "assignment": {"owner": "bob", "ccuser": []}},
        ]
        mock_resp = self._make_mock_resp(schedules)
        with patch("urllib.request.urlopen", return_value=mock_resp):
            result = _fetch_quads_hosts("alice", "http://quads.test")
        assert result == frozenset(["host1"])

    def test_ccuser_match(self):
        from dracs.webapp import _fetch_quads_hosts

        schedules = [
            {
                "host": {"name": "host3"},
                "assignment": {"owner": "admin", "ccuser": ["alice", "charlie"]},
            },
        ]
        mock_resp = self._make_mock_resp(schedules)
        with patch("urllib.request.urlopen", return_value=mock_resp):
            result = _fetch_quads_hosts("alice", "http://quads.test")
        assert result == frozenset(["host3"])

    def test_owner_and_ccuser_combined(self):
        from dracs.webapp import _fetch_quads_hosts

        schedules = [
            {"host": {"name": "host1"}, "assignment": {"owner": "alice", "ccuser": []}},
            {
                "host": {"name": "host2"},
                "assignment": {"owner": "other", "ccuser": ["alice"]},
            },
            {"host": {"name": "host3"}, "assignment": {"owner": "bob", "ccuser": []}},
        ]
        mock_resp = self._make_mock_resp(schedules)
        with patch("urllib.request.urlopen", return_value=mock_resp):
            result = _fetch_quads_hosts("alice", "http://quads.test")
        assert result == frozenset(["host1", "host2"])

    def test_no_match_returns_empty_frozenset(self):
        from dracs.webapp import _fetch_quads_hosts

        schedules = [
            {"host": {"name": "host1"}, "assignment": {"owner": "bob", "ccuser": []}},
        ]
        mock_resp = self._make_mock_resp(schedules)
        with patch("urllib.request.urlopen", return_value=mock_resp):
            result = _fetch_quads_hosts("alice", "http://quads.test")
        assert result == frozenset()

    def test_empty_schedule_returns_empty_frozenset(self):
        from dracs.webapp import _fetch_quads_hosts

        mock_resp = self._make_mock_resp([])
        with patch("urllib.request.urlopen", return_value=mock_resp):
            result = _fetch_quads_hosts("alice", "http://quads.test")
        assert result == frozenset()

    def test_unreachable_returns_none(self):
        from dracs.webapp import _fetch_quads_hosts

        with patch("urllib.request.urlopen", side_effect=OSError("connection refused")):
            result = _fetch_quads_hosts("alice", "http://quads.test")
        assert result is None

    def test_missing_assignment_field_skipped(self):
        from dracs.webapp import _fetch_quads_hosts

        schedules = [
            {"host": {"name": "host1"}},
            {"host": {"name": "host2"}, "assignment": {"owner": "alice", "ccuser": []}},
        ]
        mock_resp = self._make_mock_resp(schedules)
        with patch("urllib.request.urlopen", return_value=mock_resp):
            result = _fetch_quads_hosts("alice", "http://quads.test")
        assert result == frozenset(["host2"])

    def test_missing_host_field_skipped(self):
        from dracs.webapp import _fetch_quads_hosts

        schedules = [
            {"assignment": {"owner": "alice", "ccuser": []}},
            {"host": {"name": "host2"}, "assignment": {"owner": "alice", "ccuser": []}},
        ]
        mock_resp = self._make_mock_resp(schedules)
        with patch("urllib.request.urlopen", return_value=mock_resp):
            result = _fetch_quads_hosts("alice", "http://quads.test")
        assert result == frozenset(["host2"])

    def test_null_ccuser_field_treated_as_empty(self):
        from dracs.webapp import _fetch_quads_hosts

        schedules = [
            {
                "host": {"name": "host1"},
                "assignment": {"owner": "alice", "ccuser": None},
            },
        ]
        mock_resp = self._make_mock_resp(schedules)
        with patch("urllib.request.urlopen", return_value=mock_resp):
            result = _fetch_quads_hosts("alice", "http://quads.test")
        assert result == frozenset(["host1"])


# ---------------------------------------------------------------------------
# Integration tests — index and api_systems
# ---------------------------------------------------------------------------

_QUADS_INI_CONFIG = {
    "defaults": {"quads_enabled": "true", "quads_url": "http://quads.test"},
    "hosts": {},
}

_QUADS_DISABLED_INI_CONFIG = {
    "defaults": {"quads_enabled": "false"},
    "hosts": {},
}


@pytest.fixture
def quads_webapp_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    db_initialize(path)
    upsert_system(
        path, "TAG001", "host1", "R660", "7.0.0", "2.1.0", "Jan 1, 2027", 1893456000
    )
    upsert_system(
        path, "TAG002", "host2", "R660", "7.0.0", "2.1.0", "Jan 1, 2027", 1893456000
    )
    yield path
    if os.path.exists(path):
        os.unlink(path)


@pytest.fixture
def quads_client(quads_webapp_db):
    with patch.dict(
        os.environ,
        {
            "DRACS_DB": quads_webapp_db,
            "DRACS_DNS_STRING": "mgmt-",
            "DRACS_DNS_MODE": "prefix",
            "DRACS_LOG_DIR": tempfile.mkdtemp(),
        },
    ):
        import dracs.webapp as webapp_mod

        webapp_mod.DB_PATH = quads_webapp_db
        webapp_mod.db_initialize(quads_webapp_db)
        webapp_mod.app.config["TESTING"] = True
        webapp_mod._quads_host_cache.clear()
        with patch("dracs.sites.get_site_ini_config", return_value=_QUADS_INI_CONFIG):
            with webapp_mod.app.test_client() as c:
                yield c
        webapp_mod._quads_host_cache.clear()


def _create_no_role_user(username="quadsuser", password="pass123"):
    from dracs.users import create_user

    try:
        create_user(username, password, "user")
    except Exception:
        pass


def _create_global_admin_user(username="gadminuser", password="pass123"):
    from dracs.users import create_user

    try:
        create_user(username, password, "admin")
    except Exception:
        pass


def _create_role_user(quads_webapp_db, username="roleuser", password="pass123"):
    from dracs.db import get_default_site_id
    from dracs.users import create_user, set_user_site_role

    try:
        create_user(username, password, "user")
        set_user_site_role(username, get_default_site_id(), "user")
    except Exception:
        pass


def _create_quads_role_user(quads_webapp_db, username="quadsuser", password="pass123"):
    from dracs.db import get_default_site_id
    from dracs.users import create_user, set_user_site_role

    try:
        create_user(username, password, None)
        set_user_site_role(username, get_default_site_id(), "quads")
    except Exception:
        pass


def _login(client, username, password):
    client.post(
        "/login",
        data=json.dumps({"username": username, "password": password}),
        content_type="application/json",
    )


def _make_quads_resp(schedules):
    mock_resp = MagicMock()
    mock_resp.read.return_value = json.dumps(schedules).encode()
    mock_resp.__enter__ = lambda s: s
    mock_resp.__exit__ = MagicMock(return_value=False)
    return mock_resp


class TestIndexQuads:
    def test_quads_user_sees_only_assigned_hosts(self, quads_client, quads_webapp_db):
        _create_quads_role_user(quads_webapp_db)
        _login(quads_client, "quadsuser", "pass123")

        schedules = [
            {
                "host": {"name": "host1"},
                "assignment": {"owner": "quadsuser", "ccuser": []},
            },
        ]
        import dracs.webapp as webapp_mod

        webapp_mod._quads_host_cache.clear()
        with patch("urllib.request.urlopen", return_value=_make_quads_resp(schedules)):
            resp = quads_client.get("/")
        assert resp.status_code == 200
        text = resp.get_data(as_text=True)
        assert "host1" in text
        assert "host2" not in text

    def test_quads_user_unreachable_fail_open(self, quads_client, quads_webapp_db):
        _create_quads_role_user(quads_webapp_db)
        _login(quads_client, "quadsuser", "pass123")

        import dracs.webapp as webapp_mod

        webapp_mod._quads_host_cache.clear()
        with patch("urllib.request.urlopen", side_effect=OSError("unreachable")):
            resp = quads_client.get("/")
        assert resp.status_code == 200
        text = resp.get_data(as_text=True)
        assert "host1" in text
        assert "host2" in text

    def test_quads_user_empty_list_shows_message(self, quads_client, quads_webapp_db):
        _create_quads_role_user(quads_webapp_db)
        _login(quads_client, "quadsuser", "pass123")

        import dracs.webapp as webapp_mod

        webapp_mod._quads_host_cache.clear()
        with patch("urllib.request.urlopen", return_value=_make_quads_resp([])):
            resp = quads_client.get("/")
        assert resp.status_code == 200
        text = resp.get_data(as_text=True)
        assert "host1" not in text
        assert "host2" not in text
        assert "quads-empty-message" in text

    def test_quads_disabled_shows_all_hosts(self, quads_webapp_db):
        """Quads-role user with QUADS disabled sees all hosts (unauthenticated view)."""
        with patch.dict(
            os.environ,
            {
                "DRACS_DB": quads_webapp_db,
                "DRACS_DNS_STRING": "mgmt-",
                "DRACS_DNS_MODE": "prefix",
                "DRACS_LOG_DIR": tempfile.mkdtemp(),
            },
        ):
            import dracs.webapp as webapp_mod

            webapp_mod.DB_PATH = quads_webapp_db
            webapp_mod.db_initialize(quads_webapp_db)
            webapp_mod.app.config["TESTING"] = True
            webapp_mod._quads_host_cache.clear()
            _create_quads_role_user(quads_webapp_db)
            with patch(
                "dracs.sites.get_site_ini_config",
                return_value=_QUADS_DISABLED_INI_CONFIG,
            ):
                with webapp_mod.app.test_client() as c:
                    _login(c, "quadsuser", "pass123")
                    resp = c.get("/")
            assert resp.status_code == 200
            text = resp.get_data(as_text=True)
            assert "host1" in text
            assert "host2" in text

    def test_no_site_role_sees_all_hosts(self, quads_client, quads_webapp_db):
        """User with no site role sees all hosts (unauthenticated view), QUADS not triggered."""
        _create_no_role_user()
        _login(quads_client, "quadsuser", "pass123")

        import dracs.webapp as webapp_mod

        webapp_mod._quads_host_cache.clear()
        resp = quads_client.get("/")
        assert resp.status_code == 200
        text = resp.get_data(as_text=True)
        assert "host1" in text
        assert "host2" in text

    def test_user_with_site_role_unaffected(self, quads_client, quads_webapp_db):
        _create_role_user(quads_webapp_db)
        _login(quads_client, "roleuser", "pass123")

        import dracs.webapp as webapp_mod

        webapp_mod._quads_host_cache.clear()
        resp = quads_client.get("/")
        assert resp.status_code == 200
        text = resp.get_data(as_text=True)
        assert "host1" in text
        assert "host2" in text

    def test_anonymous_user_unaffected(self, quads_client, quads_webapp_db):
        import dracs.webapp as webapp_mod

        webapp_mod._quads_host_cache.clear()
        resp = quads_client.get("/")
        assert resp.status_code == 200
        text = resp.get_data(as_text=True)
        assert "host1" in text
        assert "host2" in text

    def test_quads_user_cache_used_on_second_request(
        self, quads_client, quads_webapp_db
    ):
        _create_quads_role_user(quads_webapp_db)
        _login(quads_client, "quadsuser", "pass123")

        schedules = [
            {
                "host": {"name": "host1"},
                "assignment": {"owner": "quadsuser", "ccuser": []},
            },
        ]
        import dracs.webapp as webapp_mod

        webapp_mod._quads_host_cache.clear()
        with patch(
            "urllib.request.urlopen", return_value=_make_quads_resp(schedules)
        ) as mock_open:
            quads_client.get("/")
            quads_client.get("/")
        assert mock_open.call_count == 1


class TestApiSystemsQuads:
    def test_api_systems_filtered_for_quads_user(self, quads_client, quads_webapp_db):
        _create_quads_role_user(quads_webapp_db)
        _login(quads_client, "quadsuser", "pass123")

        schedules = [
            {
                "host": {"name": "host1"},
                "assignment": {"owner": "quadsuser", "ccuser": []},
            },
        ]
        import dracs.webapp as webapp_mod

        webapp_mod._quads_host_cache.clear()
        with patch("urllib.request.urlopen", return_value=_make_quads_resp(schedules)):
            resp = quads_client.get("/api/systems")
        assert resp.status_code == 200
        systems = resp.get_json()
        names = {s["name"] for s in systems}
        assert names == {"host1"}

    def test_api_systems_fail_open_on_unreachable(self, quads_client, quads_webapp_db):
        _create_quads_role_user(quads_webapp_db)
        _login(quads_client, "quadsuser", "pass123")

        import dracs.webapp as webapp_mod

        webapp_mod._quads_host_cache.clear()
        with patch("urllib.request.urlopen", side_effect=OSError("unreachable")):
            resp = quads_client.get("/api/systems")
        assert resp.status_code == 200
        systems = resp.get_json()
        names = {s["name"] for s in systems}
        assert names == {"host1", "host2"}

    def test_api_systems_empty_list_for_quads_user(self, quads_client, quads_webapp_db):
        _create_quads_role_user(quads_webapp_db)
        _login(quads_client, "quadsuser", "pass123")

        import dracs.webapp as webapp_mod

        webapp_mod._quads_host_cache.clear()
        with patch("urllib.request.urlopen", return_value=_make_quads_resp([])):
            resp = quads_client.get("/api/systems")
        assert resp.status_code == 200
        systems = resp.get_json()
        assert systems == []

    def test_api_systems_unfiltered_for_role_user(self, quads_client, quads_webapp_db):
        _create_role_user(quads_webapp_db)
        _login(quads_client, "roleuser", "pass123")

        import dracs.webapp as webapp_mod

        webapp_mod._quads_host_cache.clear()
        resp = quads_client.get("/api/systems")
        assert resp.status_code == 200
        systems = resp.get_json()
        names = {s["name"] for s in systems}
        assert names == {"host1", "host2"}

    def test_api_systems_no_site_role_sees_all(self, quads_client, quads_webapp_db):
        """User with no site role sees all hosts, QUADS not triggered."""
        _create_no_role_user()
        _login(quads_client, "quadsuser", "pass123")

        import dracs.webapp as webapp_mod

        webapp_mod._quads_host_cache.clear()
        resp = quads_client.get("/api/systems")
        assert resp.status_code == 200
        systems = resp.get_json()
        names = {s["name"] for s in systems}
        assert names == {"host1", "host2"}


class TestLogoutCacheInvalidation:
    def test_logout_clears_quads_cache(self, quads_client, quads_webapp_db):
        _create_no_role_user()
        _login(quads_client, "quadsuser", "pass123")

        import dracs.webapp as webapp_mod
        from dracs.db import get_default_site_id

        site_id = get_default_site_id()
        webapp_mod._quads_cache_set("quadsuser", site_id, frozenset(["host1"]))
        assert webapp_mod._quads_cache_get("quadsuser", site_id) is not None

        quads_client.post("/logout")
        assert webapp_mod._quads_cache_get("quadsuser", site_id) is None

    def test_logout_noop_when_no_cache(self, quads_client, quads_webapp_db):
        _create_no_role_user()
        _login(quads_client, "quadsuser", "pass123")

        import dracs.webapp as webapp_mod

        webapp_mod._quads_host_cache.clear()
        resp = quads_client.post("/logout")
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# QUADS role RBAC tests
# ---------------------------------------------------------------------------


class TestQuadsRBAC:
    def test_site_role_quads_valid(self, quads_webapp_db):
        """set_user_site_role accepts 'quads' as a valid site role."""
        from dracs.db import get_default_site_id
        from dracs.users import create_user, set_user_site_role

        create_user("quadstestuser", "pass123", None)
        site_id = get_default_site_id()
        set_user_site_role("quadstestuser", site_id, "quads")

        from dracs.users import get_user_role_for_site

        assert get_user_role_for_site("quadstestuser", site_id) == "quads"

    def test_site_role_invalid_raises(self, quads_webapp_db):
        """set_user_site_role rejects unknown site roles."""
        from dracs.exceptions import ValidationError
        from dracs.db import get_default_site_id
        from dracs.users import create_user, set_user_site_role

        create_user("badroleuser", "pass123", None)
        with pytest.raises(ValidationError, match="Invalid role"):
            set_user_site_role("badroleuser", get_default_site_id(), "superadmin")

    def test_power_status_quads_user_allowed(self, quads_client, quads_webapp_db):
        """Quads-role user with QUADS access to a host can check its power status."""
        _create_quads_role_user(quads_webapp_db)
        _login(quads_client, "quadsuser", "pass123")

        schedules = [
            {
                "host": {"name": "host1"},
                "assignment": {"owner": "quadsuser", "ccuser": []},
            },
        ]
        import dracs.webapp as webapp_mod

        webapp_mod._quads_host_cache.clear()
        mock_result = MagicMock(returncode=0, stdout="Server Power Status: ON")
        with patch("urllib.request.urlopen", return_value=_make_quads_resp(schedules)):
            with patch("dracs.webapp.subprocess.run", return_value=mock_result):
                resp = quads_client.post(
                    "/api/power-status",
                    data=json.dumps({"hostname": "host1"}),
                    content_type="application/json",
                )
        data = resp.get_json()
        assert resp.status_code == 200
        assert data["success"] is True
        assert data["status"] == "on"

    def test_power_status_no_site_role_blocked(self, quads_client, quads_webapp_db):
        """User with no site role cannot check power status."""
        _create_no_role_user()
        _login(quads_client, "quadsuser", "pass123")

        resp = quads_client.post(
            "/api/power-status",
            data=json.dumps({"hostname": "host1"}),
            content_type="application/json",
        )
        assert resp.status_code == 403

    def test_power_status_quads_user_wrong_host_blocked(
        self, quads_client, quads_webapp_db
    ):
        """Quads-role user cannot check power status for a host outside their QUADS list."""
        _create_quads_role_user(quads_webapp_db)
        _login(quads_client, "quadsuser", "pass123")

        schedules = [
            {
                "host": {"name": "host1"},
                "assignment": {"owner": "quadsuser", "ccuser": []},
            },
        ]
        import dracs.webapp as webapp_mod

        webapp_mod._quads_host_cache.clear()
        with patch("urllib.request.urlopen", return_value=_make_quads_resp(schedules)):
            resp = quads_client.post(
                "/api/power-status",
                data=json.dumps({"hostname": "host2"}),
                content_type="application/json",
            )
        assert resp.status_code == 403

    def test_power_action_quads_user_allowed(self, quads_client, quads_webapp_db):
        """Quads-role user with QUADS access to a host can execute a power action."""
        _create_quads_role_user(quads_webapp_db)
        _login(quads_client, "quadsuser", "pass123")

        schedules = [
            {
                "host": {"name": "host1"},
                "assignment": {"owner": "quadsuser", "ccuser": []},
            },
        ]
        import dracs.webapp as webapp_mod

        webapp_mod._quads_host_cache.clear()
        mock_result = MagicMock(
            returncode=0, stdout="Server power operation successful"
        )
        with patch("urllib.request.urlopen", return_value=_make_quads_resp(schedules)):
            with patch("dracs.webapp.subprocess.run", return_value=mock_result):
                resp = quads_client.post(
                    "/api/power-action",
                    data=json.dumps({"hostname": "host1", "action": "powerup"}),
                    content_type="application/json",
                )
        assert resp.status_code == 200

    def test_power_action_no_site_role_blocked(self, quads_client, quads_webapp_db):
        """User with no site role cannot execute power actions."""
        _create_no_role_user()
        _login(quads_client, "quadsuser", "pass123")

        resp = quads_client.post(
            "/api/power-action",
            data=json.dumps({"hostname": "host1", "action": "powerup"}),
            content_type="application/json",
        )
        assert resp.status_code == 403

    def test_power_status_bearer_token_admin_allowed(
        self, quads_client, quads_webapp_db
    ):
        """Bearer token with admin role can check power status via _get_effective_role bearer path."""
        from dracs.users import create_user

        create_user("tokenadmin", "pass123", "admin")
        login_resp = quads_client.post(
            "/api/token-login",
            data=json.dumps({"username": "tokenadmin", "password": "pass123"}),
            content_type="application/json",
        )
        token = login_resp.get_json()["token"]

        mock_result = MagicMock(returncode=0, stdout="Server Power Status: ON")
        with patch("dracs.webapp.subprocess.run", return_value=mock_result):
            resp = quads_client.post(
                "/api/power-status",
                data=json.dumps({"hostname": "host1"}),
                content_type="application/json",
                headers={"Authorization": f"Bearer {token}"},
            )
        data = resp.get_json()
        assert resp.status_code == 200
        assert data["success"] is True

    def test_power_status_quads_user_quads_disabled_blocked(
        self, quads_client, quads_webapp_db
    ):
        """Quads-role user is blocked when QUADS is disabled for the site."""
        _create_quads_role_user(quads_webapp_db)
        _login(quads_client, "quadsuser", "pass123")

        with patch(
            "dracs.sites.get_site_ini_config",
            return_value=_QUADS_DISABLED_INI_CONFIG,
        ):
            resp = quads_client.post(
                "/api/power-status",
                data=json.dumps({"hostname": "host1"}),
                content_type="application/json",
            )
        assert resp.status_code == 403


class TestQuadsHostAccessUnit:
    """Unit tests for _quads_host_access edge cases."""

    def test_unknown_site_id_returns_false(self, quads_webapp_db):
        """Returns False when the site_id has no matching site in the DB."""
        import dracs.webapp as webapp_mod
        from dracs.db import get_default_site_id
        from dracs.users import create_user, set_user_site_role

        create_user("quadshosttest", "pass", None)
        site_id = get_default_site_id()
        set_user_site_role("quadshosttest", site_id, "quads")

        with patch("dracs.users.get_user_role_for_site", return_value="quads"):
            result = webapp_mod._quads_host_access("quadshosttest", "host1", 99999)
        assert result is False

    def test_get_effective_role_invalid_bearer_returns_false(self, quads_webapp_db):
        """Returns (False, None) when the bearer token fails validation."""
        import dracs.webapp as webapp_mod

        with webapp_mod.app.test_request_context(
            headers={"Authorization": "Bearer invalidtoken"}
        ):
            with patch("dracs.tokens.validate_token", return_value=None):
                result = webapp_mod._get_effective_role()
        assert result == (False, None)


# ---------------------------------------------------------------------------
# Verify endpoint tests
# ---------------------------------------------------------------------------


def _superadmin_login(client):
    client.post(
        "/login",
        data=json.dumps(
            {
                "username": os.environ.get("WEBADMIN_USER", "admin"),
                "password": os.environ.get("WEBADMIN_PASSWORD", "admin"),
            }
        ),
        content_type="application/json",
    )


class TestQuadsVerifyEndpoint:
    def _make_ok_resp(self):
        mock_resp = MagicMock()
        mock_resp.read.return_value = b"[]"
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        return mock_resp

    def test_verify_success(self, quads_client, quads_webapp_db):
        with patch.dict(
            os.environ, {"WEBADMIN_USER": "admin", "WEBADMIN_PASSWORD": "admin"}
        ):
            _superadmin_login(quads_client)
        with patch("urllib.request.urlopen", return_value=self._make_ok_resp()):
            resp = quads_client.post(
                "/api/sites/Default/quads-verify",
                data=json.dumps({"quads_url": "http://quads.test"}),
                content_type="application/json",
            )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True

    def test_verify_unreachable(self, quads_client, quads_webapp_db):
        with patch.dict(
            os.environ, {"WEBADMIN_USER": "admin", "WEBADMIN_PASSWORD": "admin"}
        ):
            _superadmin_login(quads_client)
        with patch("urllib.request.urlopen", side_effect=OSError("refused")):
            resp = quads_client.post(
                "/api/sites/Default/quads-verify",
                data=json.dumps({"quads_url": "http://quads.test"}),
                content_type="application/json",
            )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is False
        assert "QUADS unreachable" in data["message"]

    def test_verify_empty_url(self, quads_client, quads_webapp_db):
        with patch.dict(
            os.environ, {"WEBADMIN_USER": "admin", "WEBADMIN_PASSWORD": "admin"}
        ):
            _superadmin_login(quads_client)
        resp = quads_client.post(
            "/api/sites/Default/quads-verify",
            data=json.dumps({"quads_url": ""}),
            content_type="application/json",
        )
        assert resp.status_code == 400
        data = resp.get_json()
        assert data["success"] is False
        assert "No QUADS URL" in data["message"]

    def test_verify_non_admin_user_rejected(self, quads_client, quads_webapp_db):
        _create_no_role_user()
        _login(quads_client, "quadsuser", "pass123")
        resp = quads_client.post(
            "/api/sites/Default/quads-verify",
            data=json.dumps({"quads_url": "http://quads.test"}),
            content_type="application/json",
        )
        assert resp.status_code == 403

    def test_verify_admin_role_not_superadmin_rejected(
        self, quads_client, quads_webapp_db
    ):
        _create_global_admin_user()
        _login(quads_client, "gadminuser", "pass123")
        resp = quads_client.post(
            "/api/sites/Default/quads-verify",
            data=json.dumps({"quads_url": "http://quads.test"}),
            content_type="application/json",
        )
        assert resp.status_code == 403
        data = resp.get_json()
        assert data["success"] is False
        assert "Superadmin required" in data["message"]


# ---------------------------------------------------------------------------
# Tests for GET /api/sites/<name>/quads-schedules
# ---------------------------------------------------------------------------


class TestQuadsSchedulesEndpoint:
    """Tests for GET /api/sites/<name>/quads-schedules."""

    def _make_quads_resp(self, schedules):
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(schedules).encode()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        return mock_resp

    def _sample_schedules(self):
        return [
            {
                "host": {"name": "host1"},
                "assignment": {
                    "cloud": {"name": "cloud01"},
                    "description": "Test cloud",
                    "owner": "alice",
                    "ccuser": [],
                },
            },
            {
                "host": {"name": "host2"},
                "assignment": {
                    "cloud": {"name": "cloud01"},
                    "description": "Test cloud",
                    "owner": "alice",
                    "ccuser": [],
                },
            },
        ]

    def test_schedules_unauthenticated(self, quads_client):
        resp = quads_client.get("/api/sites/Default/quads-schedules")
        assert resp.status_code == 401

    def test_schedules_site_not_found(self, quads_client, quads_webapp_db):
        _create_role_user(quads_webapp_db)
        _login(quads_client, "roleuser", "pass123")
        resp = quads_client.get("/api/sites/NoSuchSite/quads-schedules")
        assert resp.status_code == 404
        assert resp.get_json()["success"] is False

    def test_schedules_quads_not_enabled(self, quads_client, quads_webapp_db):
        """Returns 400 when site has QUADS disabled."""
        _create_role_user(quads_webapp_db)
        _login(quads_client, "roleuser", "pass123")
        with patch(
            "dracs.sites.get_site_ini_config",
            return_value=_QUADS_DISABLED_INI_CONFIG,
        ):
            resp = quads_client.get("/api/sites/Default/quads-schedules")
        assert resp.status_code == 400
        data = resp.get_json()
        assert data["success"] is False
        assert "QUADS not enabled" in data["message"]

    def test_schedules_access_denied_no_site_role(self, quads_client, quads_webapp_db):
        """User without a site role gets 403."""
        from dracs.users import create_user

        try:
            create_user("noroleuser", "pass123", "user")
        except Exception:
            pass
        _login(quads_client, "noroleuser", "pass123")
        resp = quads_client.get("/api/sites/Default/quads-schedules")
        assert resp.status_code == 403
        assert resp.get_json()["success"] is False

    def test_schedules_user_role_success(self, quads_client, quads_webapp_db):
        """Site-role 'user' sees all allocations for DRACS-known hosts."""
        _create_role_user(quads_webapp_db)
        _login(quads_client, "roleuser", "pass123")
        with patch(
            "urllib.request.urlopen",
            return_value=self._make_quads_resp(self._sample_schedules()),
        ):
            resp = quads_client.get("/api/sites/Default/quads-schedules")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        assert len(data["allocations"]) == 1
        alloc = data["allocations"][0]
        assert alloc["cloud"] == "cloud01"
        assert alloc["host_count"] == 2
        assert sorted(alloc["hosts"]) == ["host1", "host2"]

    def test_schedules_quads_api_failure(self, quads_client, quads_webapp_db):
        """QUADS API unreachable returns 502."""
        _create_role_user(quads_webapp_db)
        _login(quads_client, "roleuser", "pass123")
        with patch(
            "urllib.request.urlopen", side_effect=OSError("connection refused")
        ):
            resp = quads_client.get("/api/sites/Default/quads-schedules")
        assert resp.status_code == 502
        data = resp.get_json()
        assert data["success"] is False
        assert "Failed to fetch" in data["message"]

    def test_schedules_quads_role_sees_only_own_clouds(
        self, quads_client, quads_webapp_db
    ):
        """User with site role 'quads' sees only allocations they own or are cc'd on."""
        _create_quads_role_user(quads_webapp_db, username="qowner", password="pass123")
        _login(quads_client, "qowner", "pass123")
        schedules = [
            {
                "host": {"name": "host1"},
                "assignment": {
                    "cloud": {"name": "cloud01"},
                    "description": "My cloud",
                    "owner": "qowner",
                    "ccuser": [],
                },
            },
            {
                "host": {"name": "host2"},
                "assignment": {
                    "cloud": {"name": "cloud02"},
                    "description": "Other cloud",
                    "owner": "someoneelse",
                    "ccuser": [],
                },
            },
        ]
        with patch(
            "urllib.request.urlopen", return_value=self._make_quads_resp(schedules)
        ):
            resp = quads_client.get("/api/sites/Default/quads-schedules")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        clouds = [a["cloud"] for a in data["allocations"]]
        assert "cloud01" in clouds
        assert "cloud02" not in clouds

    def test_schedules_quads_role_ccuser_included(self, quads_client, quads_webapp_db):
        """User listed in ccuser can see that cloud even when not the owner."""
        _create_quads_role_user(quads_webapp_db, username="ccuserq", password="pass123")
        _login(quads_client, "ccuserq", "pass123")
        schedules = [
            {
                "host": {"name": "host1"},
                "assignment": {
                    "cloud": {"name": "cloud03"},
                    "description": "CC cloud",
                    "owner": "someoneelse",
                    "ccuser": ["ccuserq"],
                },
            },
        ]
        with patch(
            "urllib.request.urlopen", return_value=self._make_quads_resp(schedules)
        ):
            resp = quads_client.get("/api/sites/Default/quads-schedules")
        assert resp.status_code == 200
        data = resp.get_json()
        clouds = [a["cloud"] for a in data["allocations"]]
        assert "cloud03" in clouds

    def test_schedules_superadmin_sees_all(self, quads_client, quads_webapp_db):
        """Superadmin sees all allocations regardless of ownership."""
        with patch.dict(
            os.environ, {"WEBADMIN_USER": "admin", "WEBADMIN_PASSWORD": "admin"}
        ):
            _superadmin_login(quads_client)
        with patch(
            "urllib.request.urlopen",
            return_value=self._make_quads_resp(self._sample_schedules()),
        ):
            resp = quads_client.get("/api/sites/Default/quads-schedules")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        assert len(data["allocations"]) == 1

    def test_schedules_skips_non_dracs_hosts(self, quads_client, quads_webapp_db):
        """Hosts not in DRACS are excluded; empty allocations are omitted."""
        _create_role_user(quads_webapp_db)
        _login(quads_client, "roleuser", "pass123")
        schedules = [
            {
                "host": {"name": "unknown-host"},
                "assignment": {
                    "cloud": {"name": "cloud01"},
                    "description": "Cloud",
                    "owner": "alice",
                    "ccuser": [],
                },
            },
        ]
        with patch(
            "urllib.request.urlopen", return_value=self._make_quads_resp(schedules)
        ):
            resp = quads_client.get("/api/sites/Default/quads-schedules")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        assert len(data["allocations"]) == 0

    def test_schedules_skips_malformed_entries(self, quads_client, quads_webapp_db):
        """Schedule entries with missing host/assignment fields are ignored."""
        _create_role_user(quads_webapp_db)
        _login(quads_client, "roleuser", "pass123")
        schedules = [
            {"host": None, "assignment": None},
            {"host": {"name": "host1"}},
            {},
        ]
        with patch(
            "urllib.request.urlopen", return_value=self._make_quads_resp(schedules)
        ):
            resp = quads_client.get("/api/sites/Default/quads-schedules")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        assert len(data["allocations"]) == 0


# ---------------------------------------------------------------------------
# Tests for GET /console-quads
# ---------------------------------------------------------------------------


class TestConsoleQuadsRoute:
    def test_console_quads_vnc_disabled_returns_404(self, quads_client):
        import dracs.webapp as webapp_mod

        orig_enable, orig_manager = webapp_mod.VNC_ENABLE, webapp_mod.vnc_manager
        webapp_mod.VNC_ENABLE = False
        webapp_mod.vnc_manager = None
        try:
            resp = quads_client.get("/console-quads?site=Default")
        finally:
            webapp_mod.VNC_ENABLE = orig_enable
            webapp_mod.vnc_manager = orig_manager
        assert resp.status_code == 404
        assert resp.get_json()["success"] is False

    def test_console_quads_renders_page_when_vnc_enabled(self, quads_client):
        import dracs.webapp as webapp_mod

        orig_enable, orig_manager = webapp_mod.VNC_ENABLE, webapp_mod.vnc_manager
        webapp_mod.VNC_ENABLE = True
        webapp_mod.vnc_manager = MagicMock()
        try:
            resp = quads_client.get("/console-quads?site=Default&cloud=cloud01")
        finally:
            webapp_mod.VNC_ENABLE = orig_enable
            webapp_mod.vnc_manager = orig_manager
        assert resp.status_code == 200
        assert b"QUADS Consoles" in resp.data

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


def _create_role_user(quads_webapp_db, username="roleuser", password="pass123"):
    from dracs.db import get_default_site_id
    from dracs.users import create_user, set_user_site_role

    try:
        create_user(username, password, "user")
        set_user_site_role(username, get_default_site_id(), "user")
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
        _create_no_role_user()
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
        _create_no_role_user()
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
        _create_no_role_user()
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

    def test_quads_disabled_no_filtering(self, quads_webapp_db):
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
            _create_no_role_user()
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

    def test_user_with_site_role_unaffected(self, quads_client, quads_webapp_db):
        _create_role_user(quads_webapp_db)
        _login(quads_client, "roleuser", "pass123")

        schedules = [
            {
                "host": {"name": "host1"},
                "assignment": {"owner": "roleuser", "ccuser": []},
            },
        ]
        import dracs.webapp as webapp_mod

        webapp_mod._quads_host_cache.clear()
        with patch("urllib.request.urlopen", return_value=_make_quads_resp(schedules)):
            resp = quads_client.get("/")
        assert resp.status_code == 200
        text = resp.get_data(as_text=True)
        assert "host1" in text
        assert "host2" in text

    def test_anonymous_user_unaffected(self, quads_client, quads_webapp_db):
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
        assert "host2" in text

    def test_quads_user_cache_used_on_second_request(
        self, quads_client, quads_webapp_db
    ):
        _create_no_role_user()
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
        _create_no_role_user()
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
        _create_no_role_user()
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
        _create_no_role_user()
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

        schedules = [
            {
                "host": {"name": "host1"},
                "assignment": {"owner": "roleuser", "ccuser": []},
            },
        ]
        import dracs.webapp as webapp_mod

        webapp_mod._quads_host_cache.clear()
        with patch("urllib.request.urlopen", return_value=_make_quads_resp(schedules)):
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

    def test_verify_requires_superadmin(self, quads_client, quads_webapp_db):
        _create_no_role_user()
        _login(quads_client, "quadsuser", "pass123")
        resp = quads_client.post(
            "/api/sites/Default/quads-verify",
            data=json.dumps({"quads_url": "http://quads.test"}),
            content_type="application/json",
        )
        assert resp.status_code == 403

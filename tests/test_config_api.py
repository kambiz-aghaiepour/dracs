"""Tests for the iDRAC config page and API endpoints."""

import json
import os
import tempfile
from unittest.mock import patch

import pytest

from dracs.db import db_initialize, get_default_site_id, get_site_by_name


@pytest.fixture
def api_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    db_initialize(path)
    yield path
    if os.path.exists(path):
        os.unlink(path)


@pytest.fixture
def client(api_db):
    with patch.dict(os.environ, {"DRACS_DB": api_db}):
        with patch.dict(
            os.environ,
            {"DRACS_DNS_STRING": "mgmt-", "DRACS_DNS_MODE": "prefix"},
        ):
            import dracs.webapp as webapp_mod

            webapp_mod.DB_PATH = api_db
            webapp_mod.db_initialize(api_db)
            webapp_mod.app.config["TESTING"] = True
            with webapp_mod.app.test_client() as c:
                yield c


def _login(client):
    client.post(
        "/login",
        data=json.dumps({"username": "admin", "password": "admin"}),
        content_type="application/json",
    )


class TestConfigPage:
    def test_redirects_unauthenticated(self, client):
        resp = client.get("/config")
        assert resp.status_code == 302

    def test_serves_page_when_authenticated(self, client):
        _login(client)
        resp = client.get("/config")
        assert resp.status_code == 200


class TestApiConfigData:
    def test_requires_auth(self, client):
        resp = client.post("/api/config-data", json={"site": "Default", "hosts": []})
        assert resp.status_code == 401

    def test_returns_empty_for_unknown_site(self, client):
        _login(client)
        resp = client.post("/api/config-data", json={"site": "nosuchsite", "hosts": []})
        data = resp.get_json()
        assert data["success"] is True
        assert data["data"] == []

    def test_returns_data_for_known_site(self, client, api_db):
        from dracs.db import upsert_host_config

        site = get_site_by_name("Default")
        site_id = site["id"]
        upsert_host_config(
            "server01.example.com",
            site_id,
            {
                "ps_rapid_on": "Disabled",
                "collected_at": "2026-01-01T00:00:00",
            },
        )
        _login(client)
        resp = client.post(
            "/api/config-data",
            json={"site": "Default", "hosts": ["server01.example.com"]},
        )
        data = resp.get_json()
        assert data["success"] is True
        assert len(data["data"]) == 1
        assert data["data"][0]["hostname"] == "server01.example.com"
        assert data["data"][0]["ps_rapid_on"] == "Disabled"

    def test_returns_settings_with_data(self, client, api_db):
        from dracs.db import upsert_site_config_collection

        site = get_site_by_name("Default")
        upsert_site_config_collection(
            site["id"], {"ps_rapid_on_enabled": True, "ps_rapid_on_hours": 12}
        )
        _login(client)
        resp = client.post("/api/config-data", json={"site": "Default", "hosts": []})
        data = resp.get_json()
        assert data["settings"]["ps_rapid_on_enabled"] is True
        assert data["settings"]["ps_rapid_on_hours"] == 12

    def test_filters_by_hostnames(self, client, api_db):
        from dracs.db import upsert_host_config

        site = get_site_by_name("Default")
        site_id = site["id"]
        upsert_host_config("host01.example.com", site_id, {"ps_rapid_on": "Disabled"})
        upsert_host_config("host02.example.com", site_id, {"ps_rapid_on": "Enabled"})
        _login(client)
        resp = client.post(
            "/api/config-data",
            json={"site": "Default", "hosts": ["host01.example.com"]},
        )
        data = resp.get_json()
        assert len(data["data"]) == 1
        assert data["data"][0]["hostname"] == "host01.example.com"

    def test_post_with_large_host_list(self, client, api_db):
        from dracs.db import upsert_host_config

        site = get_site_by_name("Default")
        site_id = site["id"]
        hostnames = [f"host{i:03d}.example.com" for i in range(200)]
        for h in hostnames:
            upsert_host_config(h, site_id, {"ps_rapid_on": "Disabled"})
        _login(client)
        resp = client.post(
            "/api/config-data",
            json={"site": "Default", "hosts": hostnames},
        )
        data = resp.get_json()
        assert data["success"] is True
        assert len(data["data"]) == 200

    def test_post_accepts_hosts_as_comma_string(self, client, api_db):
        from dracs.db import upsert_host_config

        site = get_site_by_name("Default")
        site_id = site["id"]
        upsert_host_config("host01.example.com", site_id, {"ps_rapid_on": "Disabled"})
        upsert_host_config("host02.example.com", site_id, {"ps_rapid_on": "Enabled"})
        _login(client)
        resp = client.post(
            "/api/config-data",
            json={"site": "Default", "hosts": "host01.example.com,host02.example.com"},
        )
        data = resp.get_json()
        assert data["success"] is True
        assert len(data["data"]) == 2

    def test_get_returns_data(self, client, api_db):
        from dracs.db import upsert_host_config

        site = get_site_by_name("Default")
        site_id = site["id"]
        upsert_host_config("host01.example.com", site_id, {"ps_rapid_on": "Disabled"})
        _login(client)
        resp = client.get("/api/config-data?site=Default&hosts=host01.example.com")
        data = resp.get_json()
        assert data["success"] is True
        assert len(data["data"]) == 1
        assert data["data"][0]["hostname"] == "host01.example.com"


class TestApiSiteConfigCollectionGet:
    def test_requires_auth(self, client):
        resp = client.get("/api/sites/Default/config-collection")
        assert resp.status_code == 401

    def test_requires_superadmin(self, client):
        with patch.dict(
            client.application.test_request_context().session if False else {},
            {},
        ):
            pass
        _login(client)
        with client.session_transaction() as sess:
            sess["is_superadmin"] = False
            sess["role"] = "admin"
        resp = client.get("/api/sites/Default/config-collection")
        assert resp.status_code == 403

    def test_returns_defaults_for_new_site(self, client):
        _login(client)
        with client.session_transaction() as sess:
            sess["is_superadmin"] = True
        resp = client.get("/api/sites/Default/config-collection")
        data = resp.get_json()
        assert data["success"] is True
        assert data["settings"]["ps_rapid_on_enabled"] is False

    def test_returns_404_for_unknown_site(self, client):
        _login(client)
        with client.session_transaction() as sess:
            sess["is_superadmin"] = True
        resp = client.get("/api/sites/nosuchsite/config-collection")
        assert resp.status_code == 404


class TestApiSiteConfigCollectionPut:
    def test_requires_auth(self, client):
        resp = client.put(
            "/api/sites/Default/config-collection",
            data=json.dumps({}),
            content_type="application/json",
        )
        assert resp.status_code == 401

    def test_requires_superadmin(self, client):
        _login(client)
        with client.session_transaction() as sess:
            sess["is_superadmin"] = False
            sess["role"] = "admin"
        resp = client.put(
            "/api/sites/Default/config-collection",
            data=json.dumps({"ps_rapid_on_enabled": True}),
            content_type="application/json",
        )
        assert resp.status_code == 403

    def test_saves_settings(self, client, api_db):
        _login(client)
        with client.session_transaction() as sess:
            sess["is_superadmin"] = True
        resp = client.put(
            "/api/sites/Default/config-collection",
            data=json.dumps({"ps_rapid_on_enabled": True, "ps_rapid_on_hours": 6}),
            content_type="application/json",
        )
        data = resp.get_json()
        assert data["success"] is True

        from dracs.db import get_site_config_collection

        site = get_site_by_name("Default")
        settings = get_site_config_collection(site["id"])
        assert settings["ps_rapid_on_enabled"] is True
        assert settings["ps_rapid_on_hours"] == 6

    def test_returns_400_without_body(self, client):
        _login(client)
        with client.session_transaction() as sess:
            sess["is_superadmin"] = True
        resp = client.put("/api/sites/Default/config-collection")
        assert resp.status_code == 400

    def test_returns_404_for_unknown_site(self, client):
        _login(client)
        with client.session_transaction() as sess:
            sess["is_superadmin"] = True
        resp = client.put(
            "/api/sites/nosuchsite/config-collection",
            data=json.dumps({"ps_rapid_on_enabled": True}),
            content_type="application/json",
        )
        assert resp.status_code == 404

    def test_put_handles_server_error(self, client):
        _login(client)
        with client.session_transaction() as sess:
            sess["is_superadmin"] = True
        with patch(
            "dracs.db.upsert_site_config_collection",
            side_effect=RuntimeError("DB exploded"),
        ):
            resp = client.put(
                "/api/sites/Default/config-collection",
                data=json.dumps({"ps_rapid_on_enabled": True}),
                content_type="application/json",
            )
        assert resp.status_code == 500
        assert "DB exploded" in resp.get_json()["message"]


class TestApiConfigEdit:
    def test_requires_auth(self, client):
        resp = client.post(
            "/api/config-edit",
            json={"site": "Default", "hosts": ["h.example.com"], "settings": {}},
        )
        assert resp.status_code == 401

    def test_requires_admin(self, client):
        _login(client)
        with client.session_transaction() as sess:
            sess["is_superadmin"] = False
            sess["role"] = "user"
        resp = client.post(
            "/api/config-edit",
            json={"site": "Default", "hosts": ["h.example.com"], "settings": {}},
        )
        assert resp.status_code == 403

    def test_rejects_missing_hosts(self, client):
        _login(client)
        with client.session_transaction() as sess:
            sess["is_superadmin"] = True
        resp = client.post(
            "/api/config-edit",
            json={"site": "Default", "hosts": [], "settings": {}},
        )
        assert resp.status_code == 400

    def test_rejects_invalid_hostname(self, client):
        _login(client)
        with client.session_transaction() as sess:
            sess["is_superadmin"] = True
        resp = client.post(
            "/api/config-edit",
            json={"site": "Default", "hosts": ["bad hostname!"], "settings": {}},
        )
        assert resp.status_code == 400

    def test_rejects_unknown_site(self, client):
        _login(client)
        with client.session_transaction() as sess:
            sess["is_superadmin"] = True
        resp = client.post(
            "/api/config-edit",
            json={"site": "nosuchsite", "hosts": ["h.example.com"], "settings": {}},
        )
        assert resp.status_code == 400

    def test_enqueues_jobs(self, client, api_db):
        _login(client)
        with client.session_transaction() as sess:
            sess["is_superadmin"] = True
        resp = client.post(
            "/api/config-edit",
            json={
                "site": "Default",
                "hosts": ["host01.example.com", "host02.example.com"],
                "settings": {"ps_rapid_on": True, "dns_from_dhcp": False},
            },
        )
        data = resp.get_json()
        assert resp.status_code == 200
        assert data["success"] is True
        assert data["job_count"] == 2
        assert isinstance(data["parent_job_id"], int)

    def test_rejects_no_body(self, client):
        _login(client)
        with client.session_transaction() as sess:
            sess["is_superadmin"] = True
        resp = client.post("/api/config-edit", json={})
        assert resp.status_code == 400

    def test_server_error_returns_500(self, client):
        _login(client)
        with client.session_transaction() as sess:
            sess["is_superadmin"] = True
        with patch("dracs.jobqueue.enqueue_job", side_effect=RuntimeError("DB locked")):
            resp = client.post(
                "/api/config-edit",
                json={
                    "site": "Default",
                    "hosts": ["host01.example.com"],
                    "settings": {"ps_rapid_on": True},
                },
            )
        assert resp.status_code == 500
        assert "DB locked" in resp.get_json()["message"]


class TestApiConfigEditStatus:
    def test_requires_auth(self, client):
        resp = client.get("/api/config-edit/status/999")
        assert resp.status_code == 401

    def test_returns_not_found_for_missing_job(self, client):
        _login(client)
        resp = client.get("/api/config-edit/status/99999")
        assert resp.status_code == 404

    def test_returns_status_for_queued_batch(self, client, api_db):
        _login(client)
        with client.session_transaction() as sess:
            sess["is_superadmin"] = True
        submit_data = client.post(
            "/api/config-edit",
            json={
                "site": "Default",
                "hosts": ["host01.example.com"],
                "settings": {"ps_rapid_on": True},
            },
        ).get_json()
        parent_id = submit_data["parent_job_id"]

        resp = client.get(f"/api/config-edit/status/{parent_id}")
        data = resp.get_json()
        assert data["success"] is True
        assert data["parent"]["total_count"] == 1
        assert data["parent"]["status"] in ("pending", "running", "completed", "failed")
        assert len(data["children"]) == 1
        assert data["children"][0]["hostname"] == "host01.example.com"

    def test_server_error_returns_500(self, client, api_db):
        _login(client)
        with client.session_transaction() as sess:
            sess["is_superadmin"] = True
        submit_data = client.post(
            "/api/config-edit",
            json={
                "site": "Default",
                "hosts": ["host01.example.com"],
                "settings": {"ps_rapid_on": True},
            },
        ).get_json()
        parent_id = submit_data["parent_job_id"]
        with patch(
            "dracs.jobqueue.get_child_jobs", side_effect=RuntimeError("DB gone")
        ):
            resp = client.get(f"/api/config-edit/status/{parent_id}")
        assert resp.status_code == 500
        assert "DB gone" in resp.get_json()["message"]

    def test_completed_child_includes_config(self, client, api_db):
        from dracs.db import get_site_by_name, upsert_host_config
        from dracs.jobqueue import complete_job, enqueue_job

        site = get_site_by_name("Default")
        site_id = site["id"]
        upsert_host_config("host01.example.com", site_id, {"ps_rapid_on": "Disabled"})

        _login(client)
        with client.session_transaction() as sess:
            sess["is_superadmin"] = True

        submit_data = client.post(
            "/api/config-edit",
            json={
                "site": "Default",
                "hosts": ["host01.example.com"],
                "settings": {"ps_rapid_on": True},
            },
        ).get_json()
        parent_id = submit_data["parent_job_id"]

        # Manually complete the child job to simulate successful execution
        status_data = client.get(f"/api/config-edit/status/{parent_id}").get_json()
        child_id = status_data["children"][0]["status"]
        # Find the child job id via jobqueue
        from dracs.jobqueue import get_child_jobs

        children = get_child_jobs(parent_id)
        assert len(children) == 1
        complete_job(children[0]["id"], result="Success")

        resp = client.get(f"/api/config-edit/status/{parent_id}")
        data = resp.get_json()
        completed = [c for c in data["children"] if c["status"] == "completed"]
        assert len(completed) == 1
        assert completed[0]["config"] is not None
        assert completed[0]["config"]["ps_rapid_on"] == "Disabled"


class TestApiConfigRefresh:
    def test_requires_auth(self, client):
        resp = client.post(
            "/api/config-refresh",
            json={"site": "Default", "hosts": ["h.example.com"]},
        )
        assert resp.status_code == 401

    def test_requires_admin(self, client):
        _login(client)
        with client.session_transaction() as sess:
            sess["is_superadmin"] = False
            sess["role"] = "user"
        resp = client.post(
            "/api/config-refresh",
            json={"site": "Default", "hosts": ["h.example.com"]},
        )
        assert resp.status_code == 403

    def test_rejects_missing_hosts(self, client):
        _login(client)
        with client.session_transaction() as sess:
            sess["is_superadmin"] = True
        resp = client.post(
            "/api/config-refresh",
            json={"site": "Default", "hosts": []},
        )
        assert resp.status_code == 400

    def test_rejects_invalid_hostname(self, client):
        _login(client)
        with client.session_transaction() as sess:
            sess["is_superadmin"] = True
        resp = client.post(
            "/api/config-refresh",
            json={"site": "Default", "hosts": ["bad hostname!"]},
        )
        assert resp.status_code == 400

    def test_rejects_unknown_site(self, client):
        _login(client)
        with client.session_transaction() as sess:
            sess["is_superadmin"] = True
        resp = client.post(
            "/api/config-refresh",
            json={"site": "nosuchsite", "hosts": ["h.example.com"]},
        )
        assert resp.status_code == 400

    def test_no_body_returns_400(self, client):
        _login(client)
        with client.session_transaction() as sess:
            sess["is_superadmin"] = True
        resp = client.post("/api/config-refresh", json={})
        assert resp.status_code == 400

    def test_calls_trigger_host(self, client):
        from unittest.mock import MagicMock

        _login(client)
        with client.session_transaction() as sess:
            sess["is_superadmin"] = True
        mock_cc = MagicMock()
        with patch("dracs.config_collector.get_collector", return_value=mock_cc):
            resp = client.post(
                "/api/config-refresh",
                json={
                    "site": "Default",
                    "hosts": ["host01.example.com", "host02.example.com"],
                },
            )
        data = resp.get_json()
        assert resp.status_code == 200
        assert data["success"] is True
        assert data["queued"] == 2
        assert mock_cc.trigger_host.call_count == 2

    def test_collector_unavailable_returns_503(self, client):
        _login(client)
        with client.session_transaction() as sess:
            sess["is_superadmin"] = True
        with patch("dracs.config_collector.get_collector", return_value=None):
            resp = client.post(
                "/api/config-refresh",
                json={"site": "Default", "hosts": ["host01.example.com"]},
            )
        assert resp.status_code == 503

    def test_server_error_returns_500(self, client):
        _login(client)
        with client.session_transaction() as sess:
            sess["is_superadmin"] = True
        from unittest.mock import MagicMock

        mock_cc = MagicMock()
        mock_cc.trigger_host.side_effect = RuntimeError("executor gone")
        with patch("dracs.config_collector.get_collector", return_value=mock_cc):
            resp = client.post(
                "/api/config-refresh",
                json={"site": "Default", "hosts": ["host01.example.com"]},
            )
        assert resp.status_code == 500
        assert "executor gone" in resp.get_json()["message"]

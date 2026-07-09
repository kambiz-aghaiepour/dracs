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


def _insert_attr(site_id, attr_name, value, collected_at="2026-01-01T00:00:00"):
    """Helper: insert one EAV row via the new DB API."""
    from dracs.db import get_attr_def_by_name, upsert_host_config_attr

    hostname = f"host-{attr_name}.example.com"
    attr = get_attr_def_by_name(attr_name)
    if attr is None:
        raise RuntimeError(f"Attr def not found: {attr_name}")
    return hostname, attr


class TestConfigPage:
    def test_serves_page_unauthenticated(self, client):
        resp = client.get("/config")
        assert resp.status_code == 200

    def test_serves_page_when_authenticated(self, client):
        _login(client)
        resp = client.get("/config")
        assert resp.status_code == 200


class TestApiConfigData:
    def test_accessible_unauthenticated(self, client):
        resp = client.post("/api/config-data", json={"site": "Default", "hosts": []})
        assert resp.status_code == 200

    def test_returns_empty_for_unknown_site(self, client):
        _login(client)
        resp = client.post("/api/config-data", json={"site": "nosuchsite", "hosts": []})
        data = resp.get_json()
        assert data["success"] is True
        assert data["data"] == []

    def test_returns_attr_defs_for_known_site(self, client, api_db):
        _login(client)
        resp = client.post(
            "/api/config-data",
            json={"site": "Default", "hosts": []},
        )
        data = resp.get_json()
        assert data["success"] is True
        assert "attr_defs" in data
        # Seed data should have populated the catalog
        assert len(data["attr_defs"]) > 0
        names = [d["name"] for d in data["attr_defs"]]
        assert "ps_rapid_on" in names

    def test_returns_host_data_in_eav_format(self, client, api_db):
        from dracs.db import get_attr_def_by_name, upsert_host_config_attr

        site = get_site_by_name("Default")
        site_id = site["id"]
        attr = get_attr_def_by_name("ps_rapid_on")
        upsert_host_config_attr(
            "server01.example.com",
            site_id,
            attr["id"],
            "Disabled",
            "2026-01-01T00:00:00",
        )
        _login(client)
        resp = client.post(
            "/api/config-data",
            json={"site": "Default", "hosts": ["server01.example.com"]},
        )
        data = resp.get_json()
        assert data["success"] is True
        assert len(data["data"]) == 1
        row = data["data"][0]
        assert row["hostname"] == "server01.example.com"
        assert "attrs" in row
        assert row["attrs"]["ps_rapid_on"]["value"] == "Disabled"

    def test_filters_by_hostnames(self, client, api_db):
        from dracs.db import get_attr_def_by_name, upsert_host_config_attr

        site = get_site_by_name("Default")
        site_id = site["id"]
        attr = get_attr_def_by_name("ps_rapid_on")
        upsert_host_config_attr(
            "host01.example.com", site_id, attr["id"], "Disabled", "2026-01-01T00:00:00"
        )
        upsert_host_config_attr(
            "host02.example.com", site_id, attr["id"], "Enabled", "2026-01-01T00:00:00"
        )
        _login(client)
        resp = client.post(
            "/api/config-data",
            json={"site": "Default", "hosts": ["host01.example.com"]},
        )
        data = resp.get_json()
        assert len(data["data"]) == 1
        assert data["data"][0]["hostname"] == "host01.example.com"

    def test_post_with_large_host_list(self, client, api_db):
        from dracs.db import get_attr_def_by_name, upsert_host_config_attr

        site = get_site_by_name("Default")
        site_id = site["id"]
        attr = get_attr_def_by_name("ps_rapid_on")
        hostnames = [f"host{i:03d}.example.com" for i in range(200)]
        for h in hostnames:
            upsert_host_config_attr(
                h, site_id, attr["id"], "Disabled", "2026-01-01T00:00:00"
            )
        _login(client)
        resp = client.post(
            "/api/config-data",
            json={"site": "Default", "hosts": hostnames},
        )
        data = resp.get_json()
        assert data["success"] is True
        assert len(data["data"]) == 200

    def test_post_accepts_hosts_as_comma_string(self, client, api_db):
        from dracs.db import get_attr_def_by_name, upsert_host_config_attr

        site = get_site_by_name("Default")
        site_id = site["id"]
        attr = get_attr_def_by_name("ps_rapid_on")
        upsert_host_config_attr(
            "host01.example.com", site_id, attr["id"], "Disabled", "2026-01-01T00:00:00"
        )
        upsert_host_config_attr(
            "host02.example.com", site_id, attr["id"], "Enabled", "2026-01-01T00:00:00"
        )
        _login(client)
        resp = client.post(
            "/api/config-data",
            json={"site": "Default", "hosts": "host01.example.com,host02.example.com"},
        )
        data = resp.get_json()
        assert data["success"] is True
        assert len(data["data"]) == 2

    def test_get_returns_data(self, client, api_db):
        from dracs.db import get_attr_def_by_name, upsert_host_config_attr

        site = get_site_by_name("Default")
        site_id = site["id"]
        attr = get_attr_def_by_name("ps_rapid_on")
        upsert_host_config_attr(
            "host01.example.com", site_id, attr["id"], "Disabled", "2026-01-01T00:00:00"
        )
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
        _login(client)
        with client.session_transaction() as sess:
            sess["is_superadmin"] = False
            sess["role"] = "admin"
        resp = client.get("/api/sites/Default/config-collection")
        assert resp.status_code == 403

    def test_returns_catalog_for_site(self, client):
        _login(client)
        with client.session_transaction() as sess:
            sess["is_superadmin"] = True
        resp = client.get("/api/sites/Default/config-collection")
        data = resp.get_json()
        assert data["success"] is True
        assert "catalog" in data
        assert len(data["catalog"]) > 0
        # Each entry should have name, label, site_settings
        entry = data["catalog"][0]
        assert "name" in entry
        assert "label" in entry
        assert "site_settings" in entry
        assert "enabled" in entry["site_settings"]

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
            data=json.dumps([]),
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
            data=json.dumps([{"attr_def_id": 1, "enabled": True, "hours": 12}]),
            content_type="application/json",
        )
        assert resp.status_code == 403

    def test_saves_settings(self, client, api_db):
        from dracs.db import get_attr_catalog_for_site

        _login(client)
        with client.session_transaction() as sess:
            sess["is_superadmin"] = True

        site = get_site_by_name("Default")
        site_id = site["id"]
        catalog = get_attr_catalog_for_site(site_id)
        ps_attr = next(d for d in catalog if d["name"] == "ps_rapid_on")

        resp = client.put(
            "/api/sites/Default/config-collection",
            data=json.dumps(
                [{"attr_def_id": ps_attr["id"], "enabled": True, "hours": 6}]
            ),
            content_type="application/json",
        )
        data = resp.get_json()
        assert data["success"] is True

        # Verify via GET catalog that the setting was saved
        catalog2 = get_attr_catalog_for_site(site_id)
        ps_after = next(d for d in catalog2 if d["name"] == "ps_rapid_on")
        assert ps_after["site_settings"]["enabled"] is True
        assert ps_after["site_settings"]["hours"] == 6

    def test_returns_400_without_body(self, client):
        _login(client)
        with client.session_transaction() as sess:
            sess["is_superadmin"] = True
        resp = client.put("/api/sites/Default/config-collection")
        assert resp.status_code == 400

    def test_returns_400_with_non_list_body(self, client):
        _login(client)
        with client.session_transaction() as sess:
            sess["is_superadmin"] = True
        resp = client.put(
            "/api/sites/Default/config-collection",
            data=json.dumps({"ps_rapid_on_enabled": True}),
            content_type="application/json",
        )
        assert resp.status_code == 400

    def test_returns_404_for_unknown_site(self, client):
        _login(client)
        with client.session_transaction() as sess:
            sess["is_superadmin"] = True
        resp = client.put(
            "/api/sites/nosuchsite/config-collection",
            data=json.dumps([{"attr_def_id": 1, "enabled": True, "hours": 24}]),
            content_type="application/json",
        )
        assert resp.status_code == 404

    def test_put_handles_server_error(self, client):
        _login(client)
        with client.session_transaction() as sess:
            sess["is_superadmin"] = True
        with patch(
            "dracs.db.upsert_attr_site_settings",
            side_effect=RuntimeError("DB exploded"),
        ):
            resp = client.put(
                "/api/sites/Default/config-collection",
                data=json.dumps([{"attr_def_id": 1, "enabled": True, "hours": 24}]),
                content_type="application/json",
            )
        assert resp.status_code == 500
        assert "DB exploded" in resp.get_json()["message"]


class TestApiConfigEdit:
    def _push_settings(self):
        return [
            {
                "attr_name": "ps_rapid_on",
                "push_key": "System.ServerPwr.PSRapidOn",
                "push_value": "Disabled",
                "post_push_command": None,
            }
        ]

    def test_requires_auth(self, client):
        resp = client.post(
            "/api/config-edit",
            json={
                "site": "Default",
                "hosts": ["h.example.com"],
                "push_settings": self._push_settings(),
            },
        )
        assert resp.status_code == 401

    def test_requires_admin(self, client):
        _login(client)
        with client.session_transaction() as sess:
            sess["is_superadmin"] = False
            sess["role"] = "user"
        resp = client.post(
            "/api/config-edit",
            json={
                "site": "Default",
                "hosts": ["h.example.com"],
                "push_settings": self._push_settings(),
            },
        )
        assert resp.status_code in (401, 403)

    def test_rejects_missing_hosts(self, client):
        _login(client)
        with client.session_transaction() as sess:
            sess["is_superadmin"] = True
        resp = client.post(
            "/api/config-edit",
            json={"site": "Default", "hosts": [], "push_settings": self._push_settings()},
        )
        assert resp.status_code == 400

    def test_rejects_missing_push_settings(self, client):
        _login(client)
        with client.session_transaction() as sess:
            sess["is_superadmin"] = True
        resp = client.post(
            "/api/config-edit",
            json={"site": "Default", "hosts": ["h.example.com"], "push_settings": []},
        )
        assert resp.status_code == 400

    def test_rejects_invalid_hostname(self, client):
        _login(client)
        with client.session_transaction() as sess:
            sess["is_superadmin"] = True
        resp = client.post(
            "/api/config-edit",
            json={
                "site": "Default",
                "hosts": ["bad hostname!"],
                "push_settings": self._push_settings(),
            },
        )
        assert resp.status_code == 400

    def test_rejects_unknown_site(self, client):
        _login(client)
        with client.session_transaction() as sess:
            sess["is_superadmin"] = True
        resp = client.post(
            "/api/config-edit",
            json={
                "site": "nosuchsite",
                "hosts": ["h.example.com"],
                "push_settings": self._push_settings(),
            },
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
                "push_settings": self._push_settings(),
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
                    "push_settings": self._push_settings(),
                },
            )
        assert resp.status_code == 500
        assert "DB locked" in resp.get_json()["message"]


class TestApiConfigEditStatus:
    def _submit(self, client):
        return client.post(
            "/api/config-edit",
            json={
                "site": "Default",
                "hosts": ["host01.example.com"],
                "push_settings": [
                    {
                        "attr_name": "ps_rapid_on",
                        "push_key": "System.ServerPwr.PSRapidOn",
                        "push_value": "Disabled",
                        "post_push_command": None,
                    }
                ],
            },
        ).get_json()

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
        submit_data = self._submit(client)
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
        submit_data = self._submit(client)
        parent_id = submit_data["parent_job_id"]
        with patch(
            "dracs.jobqueue.get_child_jobs", side_effect=RuntimeError("DB gone")
        ):
            resp = client.get(f"/api/config-edit/status/{parent_id}")
        assert resp.status_code == 500
        assert "DB gone" in resp.get_json()["message"]

    def test_completed_child_includes_config(self, client, api_db):
        from dracs.db import get_attr_def_by_name, upsert_host_config_attr
        from dracs.jobqueue import complete_job, get_child_jobs

        site = get_site_by_name("Default")
        site_id = site["id"]
        attr = get_attr_def_by_name("ps_rapid_on")
        upsert_host_config_attr(
            "host01.example.com", site_id, attr["id"], "Disabled", "2026-01-01T00:00:00"
        )

        _login(client)
        with client.session_transaction() as sess:
            sess["is_superadmin"] = True

        submit_data = self._submit(client)
        parent_id = submit_data["parent_job_id"]

        children = get_child_jobs(parent_id)
        assert len(children) == 1
        complete_job(children[0]["id"], result="Success")

        resp = client.get(f"/api/config-edit/status/{parent_id}")
        data = resp.get_json()
        completed = [c for c in data["children"] if c["status"] == "completed"]
        assert len(completed) == 1
        assert completed[0]["config"] is not None
        # EAV format: {hostname, attrs: {attr_name: {value, collected_at}}}
        assert "attrs" in completed[0]["config"]
        assert completed[0]["config"]["attrs"]["ps_rapid_on"]["value"] == "Disabled"


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
        assert resp.status_code in (401, 403)

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

    def test_enqueues_collect_jobs(self, client, api_db):
        _login(client)
        with client.session_transaction() as sess:
            sess["is_superadmin"] = True
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

    def test_server_error_returns_500(self, client):
        _login(client)
        with client.session_transaction() as sess:
            sess["is_superadmin"] = True
        with patch("dracs.jobqueue.enqueue_job", side_effect=RuntimeError("DB locked")):
            resp = client.post(
                "/api/config-refresh",
                json={"site": "Default", "hosts": ["host01.example.com"]},
            )
        assert resp.status_code == 500
        assert "DB locked" in resp.get_json()["message"]

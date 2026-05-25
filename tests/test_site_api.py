import json
import os
import tempfile
from unittest.mock import patch

import pytest

from dracs.db import (
    create_site,
    db_initialize,
    get_default_site_id,
    upsert_system,
)
from dracs.users import create_user, set_user_site_role


@pytest.fixture
def site_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    db_initialize(path)
    upsert_system(
        path,
        "TAG001",
        "server01",
        "R660",
        "7.0.0",
        "2.1.0",
        "Jan 1, 2027",
        1893456000,
    )
    upsert_system(
        path,
        "TAG002",
        "server02",
        "R660",
        "7.0.0",
        "2.1.0",
        "Jan 1, 2027",
        1893456000,
    )
    yield path
    if os.path.exists(path):
        os.unlink(path)


@pytest.fixture
def site_client(site_db, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with patch.dict(
        os.environ,
        {
            "DRACS_DB": site_db,
            "DRACS_DNS_STRING": "mgmt-",
            "DRACS_DNS_MODE": "prefix",
            "WEBADMIN_USER": "admin",
            "WEBADMIN_PASSWORD": "admin",
        },
    ):
        import dracs.webapp as webapp_mod

        webapp_mod.DB_PATH = site_db
        webapp_mod.db_initialize(site_db)
        webapp_mod.app.config["TESTING"] = True
        with webapp_mod.app.test_client() as c:
            yield c


def _login(client, username="admin", password="admin"):
    client.post(
        "/login",
        data=json.dumps({"username": username, "password": password}),
        content_type="application/json",
    )


class TestApiSystemsSiteFilter:
    def test_default_site_returns_all(self, site_client):
        resp = site_client.get("/api/systems")
        data = resp.get_json()
        assert len(data) == 2

    def test_explicit_default_site(self, site_client):
        resp = site_client.get("/api/systems?site=Default")
        data = resp.get_json()
        assert len(data) == 2

    def test_empty_site_returns_empty(self, site_client):
        create_site("Site2")
        resp = site_client.get("/api/systems?site=Site2")
        data = resp.get_json()
        assert len(data) == 0

    def test_site_with_systems(self, site_client, site_db):
        site2 = create_site("Site2")
        upsert_system(
            site_db,
            "TAG003",
            "server03",
            "R660",
            "7.0.0",
            "2.1.0",
            "Jan 1, 2027",
            1893456000,
            site_id=site2["id"],
        )

        resp = site_client.get("/api/systems?site=Site2")
        data = resp.get_json()
        assert len(data) == 1
        assert data[0]["name"] == "server03"


class TestIndexRoute:
    def test_index_default_site(self, site_client):
        resp = site_client.get("/")
        assert resp.status_code == 200
        assert b"server01" in resp.data

    def test_index_with_site_param(self, site_client):
        create_site("Site2")
        resp = site_client.get("/?site=Site2")
        assert resp.status_code == 200
        assert b"server01" not in resp.data

    def test_index_with_multiple_sites(self, site_client):
        create_site("Site2")
        resp = site_client.get("/")
        assert resp.status_code == 200

    def test_unauthenticated_user_sees_all_sites(self, site_client):
        create_site("Site2")
        resp = site_client.get("/?site=Site2")
        assert resp.status_code == 200

    def test_user_no_role_on_site_treated_as_guest(self, site_client):
        create_user("testuser", "testpass", role="user")
        site2 = create_site("Site2")
        _login(site_client, "testuser", "testpass")
        resp = site_client.get("/?site=Site2")
        assert resp.status_code == 200
        assert b"Login" in resp.data

    def test_user_with_role_on_site_sees_role(self, site_client):
        create_user("testuser", "testpass", role="admin")
        site2 = create_site("Site2")
        set_user_site_role("testuser", site2["id"], "user")
        _login(site_client, "testuser", "testpass")
        resp = site_client.get("/?site=Site2")
        assert resp.status_code == 200
        assert b"testuser" in resp.data


class TestSitesCrud:
    def test_list_sites(self, site_client):
        _login(site_client)
        resp = site_client.get("/api/sites")
        data = resp.get_json()
        assert data["success"] is True
        assert len(data["sites"]) == 1
        assert data["sites"][0]["name"] == "Default"

    def test_list_sites_unauthenticated(self, site_client):
        resp = site_client.get("/api/sites")
        data = resp.get_json()
        assert data["success"] is True

    def test_create_site(self, site_client):
        _login(site_client)
        resp = site_client.post(
            "/api/sites",
            data=json.dumps({"name": "Site2"}),
            content_type="application/json",
        )
        data = resp.get_json()
        assert data["success"] is True
        assert data["site"]["name"] == "Site2"

    def test_create_site_invalid_name(self, site_client):
        _login(site_client)
        resp = site_client.post(
            "/api/sites",
            data=json.dumps({"name": "bad-name"}),
            content_type="application/json",
        )
        assert resp.status_code == 400

    def test_create_site_non_superadmin_denied(self, site_client):
        create_user("testuser", "testpass", role="admin")
        _login(site_client, "testuser", "testpass")
        resp = site_client.post(
            "/api/sites",
            data=json.dumps({"name": "Site2"}),
            content_type="application/json",
        )
        assert resp.status_code == 403

    def test_delete_site(self, site_client):
        _login(site_client)
        site_client.post(
            "/api/sites",
            data=json.dumps({"name": "Site2"}),
            content_type="application/json",
        )
        resp = site_client.delete("/api/sites/Site2")
        data = resp.get_json()
        assert data["success"] is True

    def test_delete_primary_site_fails(self, site_client):
        _login(site_client)
        resp = site_client.delete("/api/sites/Default")
        assert resp.status_code == 400

    def test_delete_nonexistent_site(self, site_client):
        _login(site_client)
        resp = site_client.delete("/api/sites/NoSuch")
        assert resp.status_code == 404

    def test_rename_site(self, site_client):
        _login(site_client)
        site_client.post(
            "/api/sites",
            data=json.dumps({"name": "Site2"}),
            content_type="application/json",
        )
        resp = site_client.patch(
            "/api/sites/Site2",
            data=json.dumps({"name": "Lab3"}),
            content_type="application/json",
        )
        data = resp.get_json()
        assert data["success"] is True
        assert "Lab3" in data["message"]

    def test_rename_nonexistent_site(self, site_client):
        _login(site_client)
        resp = site_client.patch(
            "/api/sites/NoSuch",
            data=json.dumps({"name": "NewName"}),
            content_type="application/json",
        )
        assert resp.status_code == 404


class TestSiteConfig:
    def test_get_config(self, site_client, tmp_path, monkeypatch):
        _login(site_client)
        ini = tmp_path / "drac-passwords.ini"
        ini.write_text("[Default-DEFAULTS]\nusername = root\npassword = calvin\n")
        monkeypatch.chdir(tmp_path)
        resp = site_client.get("/api/sites/Default/config")
        data = resp.get_json()
        assert data["success"] is True
        assert data["config"]["defaults"]["username"] == "root"

    def test_set_config(self, site_client, tmp_path, monkeypatch):
        _login(site_client)
        ini = tmp_path / "drac-passwords.ini"
        ini.write_text("[Default-DEFAULTS]\nusername = root\n")
        monkeypatch.chdir(tmp_path)

        resp = site_client.put(
            "/api/sites/Default/config",
            data=json.dumps(
                {
                    "defaults": {"username": "newroot", "password": "newpass"},
                }
            ),
            content_type="application/json",
        )
        data = resp.get_json()
        assert data["success"] is True

    def test_config_non_superadmin_denied(self, site_client):
        create_user("testuser", "testpass", role="admin")
        _login(site_client, "testuser", "testpass")
        resp = site_client.get("/api/sites/Default/config")
        assert resp.status_code == 403


class TestSitesCrudEdgeCases:
    def test_create_site_unauthenticated(self, site_client):
        resp = site_client.post(
            "/api/sites",
            data=json.dumps({"name": "Site2"}),
            content_type="application/json",
        )
        assert resp.status_code == 401

    def test_create_site_missing_name(self, site_client):
        _login(site_client)
        resp = site_client.post(
            "/api/sites",
            data=json.dumps({}),
            content_type="application/json",
        )
        assert resp.status_code == 400

    def test_delete_site_unauthenticated(self, site_client):
        resp = site_client.delete("/api/sites/Default")
        assert resp.status_code == 401

    def test_delete_site_non_superadmin(self, site_client):
        create_user("testuser", "testpass", role="admin")
        _login(site_client, "testuser", "testpass")
        resp = site_client.delete("/api/sites/Default")
        assert resp.status_code == 403

    def test_rename_site_unauthenticated(self, site_client):
        resp = site_client.patch(
            "/api/sites/Default",
            data=json.dumps({"name": "Main"}),
            content_type="application/json",
        )
        assert resp.status_code == 401

    def test_rename_site_non_superadmin(self, site_client):
        create_user("testuser", "testpass", role="admin")
        _login(site_client, "testuser", "testpass")
        resp = site_client.patch(
            "/api/sites/Default",
            data=json.dumps({"name": "Main"}),
            content_type="application/json",
        )
        assert resp.status_code == 403

    def test_rename_site_missing_name(self, site_client):
        _login(site_client)
        resp = site_client.patch(
            "/api/sites/Default",
            data=json.dumps({}),
            content_type="application/json",
        )
        assert resp.status_code == 400

    def test_rename_site_invalid_name(self, site_client):
        _login(site_client)
        resp = site_client.patch(
            "/api/sites/Default",
            data=json.dumps({"name": "bad-name"}),
            content_type="application/json",
        )
        assert resp.status_code == 400

    def test_config_set_unauthenticated(self, site_client):
        resp = site_client.put(
            "/api/sites/Default/config",
            data=json.dumps({"defaults": {}}),
            content_type="application/json",
        )
        assert resp.status_code == 401

    def test_config_set_non_superadmin(self, site_client):
        create_user("testuser", "testpass", role="admin")
        _login(site_client, "testuser", "testpass")
        resp = site_client.put(
            "/api/sites/Default/config",
            data=json.dumps({"defaults": {}}),
            content_type="application/json",
        )
        assert resp.status_code == 403

    def test_config_set_missing_body(self, site_client):
        _login(site_client)
        resp = site_client.put(
            "/api/sites/Default/config",
            data=json.dumps(None),
            content_type="application/json",
        )
        assert resp.status_code == 400

    def test_config_get_unauthenticated(self, site_client):
        resp = site_client.get("/api/sites/Default/config")
        assert resp.status_code == 401


class TestSitesCrudExceptionPaths:
    def test_create_duplicate_site_returns_error(self, site_client):
        _login(site_client)
        site_client.post(
            "/api/sites",
            data=json.dumps({"name": "Site2"}),
            content_type="application/json",
        )
        resp = site_client.post(
            "/api/sites",
            data=json.dumps({"name": "Site2"}),
            content_type="application/json",
        )
        assert resp.status_code == 400

    def test_delete_site_with_hosts_returns_error(self, site_client, site_db):
        _login(site_client)
        site_client.post(
            "/api/sites",
            data=json.dumps({"name": "Site2"}),
            content_type="application/json",
        )
        from dracs.db import get_site_by_name

        site = get_site_by_name("Site2")
        upsert_system(
            site_db,
            "TAG999",
            "sitehost",
            "R660",
            "7.0.0",
            "2.1.0",
            "Jan 1, 2027",
            1893456000,
            site_id=site["id"],
        )
        resp = site_client.delete("/api/sites/Site2")
        assert resp.status_code == 400

    def test_rename_to_duplicate_returns_error(self, site_client):
        _login(site_client)
        site_client.post(
            "/api/sites",
            data=json.dumps({"name": "Site2"}),
            content_type="application/json",
        )
        resp = site_client.patch(
            "/api/sites/Site2",
            data=json.dumps({"name": "Default"}),
            content_type="application/json",
        )
        assert resp.status_code == 400

    def test_config_set_exception_returns_500(self, site_client):
        _login(site_client)
        with patch("dracs.sites.set_site_ini_config", side_effect=OSError("disk full")):
            resp = site_client.put(
                "/api/sites/Default/config",
                data=json.dumps({"defaults": {"username": "root"}}),
                content_type="application/json",
            )
            assert resp.status_code == 500

    def test_delete_site_exception_returns_500(self, site_client):
        _login(site_client)
        with patch("dracs.db.delete_site", side_effect=RuntimeError("db error")):
            resp = site_client.delete("/api/sites/Default")
            assert resp.status_code == 500


class TestGetRequestedSiteUnknown:
    def test_unknown_site_returns_none_id(self, site_client):
        import dracs.webapp as webapp_mod

        with webapp_mod.app.test_request_context("/?site=NoSuchSite"):
            from dracs.webapp import _get_requested_site

            site_id, site_name = _get_requested_site()
            assert site_id is None
            assert site_name == "NoSuchSite"


class TestFwBiosSummaryEndpoints:
    def test_fw_summary(self, site_client):
        _login(site_client)
        resp = site_client.get("/api/fw-summary")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        assert len(data["models"]) >= 1
        model = data["models"][0]
        assert model["model"] == "R660"
        assert len(model["installed"]) >= 1
        assert model["installed"][0]["version"] == "7.0.0"
        assert model["installed"][0]["count"] == 2

    def test_fw_summary_with_site(self, site_client, site_db):
        _login(site_client)
        site2 = create_site("Site2")
        upsert_system(
            site_db,
            "TAG003",
            "server03",
            "R660",
            "8.0.0",
            "3.0.0",
            "Jan 1, 2028",
            1924992000,
            site_id=site2["id"],
        )
        resp = site_client.get("/api/fw-summary?site=Site2")
        data = resp.get_json()
        assert data["success"] is True
        assert len(data["models"]) == 1
        assert data["models"][0]["installed"][0]["version"] == "8.0.0"

    def test_fw_summary_unauthenticated(self, site_client):
        resp = site_client.get("/api/fw-summary")
        assert resp.status_code == 401

    def test_bios_summary(self, site_client):
        _login(site_client)
        resp = site_client.get("/api/bios-summary")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        assert len(data["models"]) >= 1
        model = data["models"][0]
        assert model["model"] == "R660"
        assert len(model["installed"]) >= 1
        assert model["installed"][0]["version"] == "2.1.0"

    def test_bios_summary_with_site(self, site_client, site_db):
        _login(site_client)
        site2 = create_site("Site2")
        upsert_system(
            site_db,
            "TAG003",
            "server03",
            "R660",
            "8.0.0",
            "3.0.0",
            "Jan 1, 2028",
            1924992000,
            site_id=site2["id"],
        )
        resp = site_client.get("/api/bios-summary?site=Site2")
        data = resp.get_json()
        assert data["success"] is True
        assert len(data["models"]) == 1
        assert data["models"][0]["installed"][0]["version"] == "3.0.0"

    def test_bios_summary_unauthenticated(self, site_client):
        resp = site_client.get("/api/bios-summary")
        assert resp.status_code == 401

    def test_fw_summary_with_model_filter(self, site_client, site_db):
        _login(site_client)
        upsert_system(
            site_db,
            "TAG003",
            "server03",
            "R650",
            "6.0.0",
            "1.5.0",
            "Jan 1, 2027",
            1893456000,
        )
        resp = site_client.get("/api/fw-summary?model=R660")
        data = resp.get_json()
        assert data["success"] is True
        assert len(data["models"]) == 1
        assert data["models"][0]["model"] == "R660"

    def test_bios_summary_with_model_filter(self, site_client, site_db):
        _login(site_client)
        upsert_system(
            site_db,
            "TAG003",
            "server03",
            "R650",
            "6.0.0",
            "1.5.0",
            "Jan 1, 2027",
            1893456000,
        )
        resp = site_client.get("/api/bios-summary?model=R660")
        data = resp.get_json()
        assert data["success"] is True
        assert len(data["models"]) == 1
        assert data["models"][0]["model"] == "R660"


class TestSitesPageRoute:
    def test_unauthenticated_redirects(self, site_client):
        resp = site_client.get("/sites")
        assert resp.status_code == 302

    def test_non_superadmin_redirects(self, site_client):
        create_user("adminuser", "pass123", role="admin")
        from dracs.users import set_user_site_role

        default_id = get_default_site_id()
        set_user_site_role("adminuser", default_id, "admin")
        _login(site_client, "adminuser", "pass123")
        resp = site_client.get("/sites")
        assert resp.status_code == 302

    def test_superadmin_access(self, site_client):
        _login(site_client)
        resp = site_client.get("/sites")
        assert resp.status_code == 200
        assert b"Site Management" in resp.data

    def test_preserves_site_param(self, site_client):
        _login(site_client)
        resp = site_client.get("/sites?site=SomePlace")
        assert resp.status_code == 200
        assert b"SomePlace" in resp.data


class TestDeleteSiteIniCleanup:
    def test_delete_removes_ini_sections(self, site_client, tmp_path, monkeypatch):
        _login(site_client)
        monkeypatch.chdir(tmp_path)
        site_client.post(
            "/api/sites",
            data=json.dumps({"name": "TestDel"}),
            content_type="application/json",
        )
        ini = tmp_path / "drac-passwords.ini"
        assert ini.exists()
        assert "TestDel-DEFAULTS" in ini.read_text()

        site_client.delete("/api/sites/TestDel")
        assert "TestDel-DEFAULTS" not in ini.read_text()


class TestRefreshAllSiteAware:
    @patch("dracs.jobqueue.enqueue_batch", return_value=2)
    def test_refresh_all_with_site(self, mock_enqueue, site_client):
        _login(site_client)
        resp = site_client.post("/api/refresh-all?site=Default")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True

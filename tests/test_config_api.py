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
        resp = client.get("/api/config-data")
        assert resp.status_code == 401

    def test_returns_empty_for_unknown_site(self, client):
        _login(client)
        resp = client.get("/api/config-data?site=nosuchsite")
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
        resp = client.get("/api/config-data?site=Default&hosts=server01.example.com")
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
        resp = client.get("/api/config-data?site=Default")
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
        resp = client.get("/api/config-data?site=Default&hosts=host01.example.com")
        data = resp.get_json()
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

"""Tests for the /attr-catalog page and /api/attr-catalog routes."""

import json
import os
import tempfile
from unittest.mock import patch

import pytest

from dracs.db import db_initialize, get_default_site_id


@pytest.fixture
def cat_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    db_initialize(path)
    yield path
    if os.path.exists(path):
        os.unlink(path)


@pytest.fixture
def client(cat_db, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with patch.dict(
        os.environ,
        {
            "DRACS_DB": cat_db,
            "DRACS_DNS_STRING": "mgmt-",
            "DRACS_DNS_MODE": "prefix",
            "WEBADMIN_USER": "admin",
            "WEBADMIN_PASSWORD": "admin",
        },
    ):
        import dracs.webapp as webapp_mod

        webapp_mod.DB_PATH = cat_db
        webapp_mod.db_initialize(cat_db)
        webapp_mod.app.config["TESTING"] = True
        with webapp_mod.app.test_client() as c:
            yield c


def _login(client, username="admin", password="admin"):
    client.post(
        "/login",
        data=json.dumps({"username": username, "password": password}),
        content_type="application/json",
    )


def _new_attr_payload(**kwargs):
    defaults = dict(
        name="test_custom",
        label="Test Custom",
        endpoint_type="idrac_attributes",
        display_type="string",
        display_order=99,
        attribute_path="Attributes.Test.1.Value",
        push_key="iDRAC.Test.Value",
        is_writable=False,
        post_push_command="",
        choices=[],
    )
    defaults.update(kwargs)
    return defaults


class TestAttrCatalogPage:
    def test_redirects_unauthenticated(self, client):
        resp = client.get("/attr-catalog")
        assert resp.status_code == 302

    def test_redirects_non_superadmin(self, client):
        _login(client)
        resp = client.get("/attr-catalog")
        # admin user is superadmin in the test env (webadmin), so this should succeed
        assert resp.status_code == 200

    def test_returns_200_for_superadmin(self, client):
        _login(client)
        resp = client.get("/attr-catalog")
        assert resp.status_code == 200
        assert b"Attribute Catalog" in resp.data


class TestApiAttrCatalogGet:
    def test_requires_auth(self, client):
        resp = client.get("/api/attr-catalog")
        assert resp.status_code in (401, 302, 403)

    def test_returns_catalog_for_superadmin(self, client):
        _login(client)
        resp = client.get("/api/attr-catalog")
        data = resp.get_json()
        assert data["success"] is True
        assert isinstance(data["catalog"], list)
        assert len(data["catalog"]) >= 10
        names = [d["name"] for d in data["catalog"]]
        assert "ps_rapid_on" in names

    def test_each_entry_has_required_fields(self, client):
        _login(client)
        resp = client.get("/api/attr-catalog")
        for entry in resp.get_json()["catalog"]:
            assert "id" in entry
            assert "name" in entry
            assert "label" in entry
            assert "endpoint_type" in entry
            assert "choices" in entry


class TestApiAttrCatalogPost:
    def test_requires_auth(self, client):
        resp = client.post("/api/attr-catalog", json=_new_attr_payload())
        assert resp.status_code in (401, 302, 403)

    def test_creates_new_attr(self, client):
        _login(client)
        resp = client.post("/api/attr-catalog", json=_new_attr_payload())
        data = resp.get_json()
        assert data["success"] is True
        assert data["entry"]["name"] == "test_custom"
        assert isinstance(data["entry"]["id"], int)

    def test_creates_with_choices(self, client):
        _login(client)
        payload = _new_attr_payload(
            name="test_bool",
            is_writable=True,
            choices=[{"label": "Enabled", "push_value": "Enabled"}, {"label": "Disabled", "push_value": "Disabled"}],
        )
        resp = client.post("/api/attr-catalog", json=payload)
        data = resp.get_json()
        assert data["success"] is True
        assert len(data["entry"]["choices"]) == 2

    def test_rejects_invalid_name(self, client):
        _login(client)
        resp = client.post("/api/attr-catalog", json=_new_attr_payload(name="bad name!"))
        data = resp.get_json()
        assert data["success"] is False
        assert resp.status_code == 400

    def test_rejects_invalid_endpoint_type(self, client):
        _login(client)
        resp = client.post("/api/attr-catalog", json=_new_attr_payload(endpoint_type="bogus"))
        data = resp.get_json()
        assert data["success"] is False
        assert resp.status_code == 400

    def test_rejects_invalid_display_type(self, client):
        _login(client)
        resp = client.post("/api/attr-catalog", json=_new_attr_payload(display_type="unknown"))
        data = resp.get_json()
        assert data["success"] is False
        assert resp.status_code == 400

    def test_rejects_missing_name(self, client):
        _login(client)
        resp = client.post("/api/attr-catalog", json=_new_attr_payload(name=""))
        data = resp.get_json()
        assert data["success"] is False

    def test_new_attr_visible_in_get(self, client):
        _login(client)
        client.post("/api/attr-catalog", json=_new_attr_payload())
        resp = client.get("/api/attr-catalog")
        names = [d["name"] for d in resp.get_json()["catalog"]]
        assert "test_custom" in names


class TestApiAttrCatalogPut:
    def _create(self, client, name="edit_attr"):
        resp = client.post("/api/attr-catalog", json=_new_attr_payload(name=name))
        return resp.get_json()["entry"]["id"]

    def test_updates_label(self, client):
        _login(client)
        attr_id = self._create(client)
        resp = client.put(f"/api/attr-catalog/{attr_id}", json=_new_attr_payload(name="edit_attr", label="Changed"))
        data = resp.get_json()
        assert data["success"] is True
        assert data["entry"]["label"] == "Changed"

    def test_replaces_choices(self, client):
        _login(client)
        attr_id = self._create(client)
        resp = client.put(
            f"/api/attr-catalog/{attr_id}",
            json=_new_attr_payload(
                name="edit_attr",
                is_writable=True,
                choices=[{"label": "A", "push_value": "a"}, {"label": "B", "push_value": "b"}],
            ),
        )
        data = resp.get_json()
        assert data["success"] is True
        assert len(data["entry"]["choices"]) == 2

    def test_requires_auth(self, client):
        resp = client.put("/api/attr-catalog/1", json=_new_attr_payload())
        assert resp.status_code in (401, 302, 403)

    def test_returns_404_for_missing_id(self, client):
        _login(client)
        resp = client.put("/api/attr-catalog/999999", json=_new_attr_payload())
        assert resp.status_code == 404

    def test_rejects_invalid_body(self, client):
        _login(client)
        attr_id = self._create(client)
        resp = client.put(f"/api/attr-catalog/{attr_id}", json=_new_attr_payload(endpoint_type="nope"))
        assert resp.status_code == 400


class TestApiAttrCatalogDelete:
    def _create(self, client, name="del_attr"):
        resp = client.post("/api/attr-catalog", json=_new_attr_payload(name=name))
        return resp.get_json()["entry"]["id"]

    def test_deletes_attr(self, client):
        _login(client)
        attr_id = self._create(client)
        resp = client.delete(f"/api/attr-catalog/{attr_id}")
        data = resp.get_json()
        assert data["success"] is True

    def test_deleted_attr_absent_from_catalog(self, client):
        _login(client)
        attr_id = self._create(client)
        client.delete(f"/api/attr-catalog/{attr_id}")
        resp = client.get("/api/attr-catalog")
        ids = [d["id"] for d in resp.get_json()["catalog"]]
        assert attr_id not in ids

    def test_returns_zero_counts_when_no_data(self, client):
        _login(client)
        attr_id = self._create(client)
        resp = client.delete(f"/api/attr-catalog/{attr_id}")
        data = resp.get_json()
        assert data["deleted_host_records"] == 0
        assert data["deleted_site_settings"] == 0

    def test_requires_auth(self, client):
        resp = client.delete("/api/attr-catalog/1")
        assert resp.status_code in (401, 302, 403)

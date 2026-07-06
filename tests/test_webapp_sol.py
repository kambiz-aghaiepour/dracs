"""Tests for the /api/sol/connect-info webapp endpoint."""

import json
import os
import tempfile
from unittest.mock import patch

import pytest

from dracs.db import db_initialize, get_default_site_id
from dracs.sites import set_site_ini_config
from dracs.users import create_user, set_user_site_role


@pytest.fixture
def webapp_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    db_initialize(path)
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
            "SOL_ENABLE": "true",
            "SOL_CONSERVER_PORT": "3109",
        },
    ):
        import dracs.webapp as webapp_mod

        webapp_mod.DB_PATH = webapp_db
        webapp_mod.db_initialize(webapp_db)
        webapp_mod.SOL_ENABLE = True
        webapp_mod.app.config["TESTING"] = True
        with webapp_mod.app.test_client() as c:
            yield c


@pytest.fixture
def client_sol_disabled(webapp_db):
    with patch.dict(
        os.environ,
        {
            "DRACS_DB": webapp_db,
            "DRACS_DNS_STRING": "mgmt-",
            "DRACS_DNS_MODE": "prefix",
            "DRACS_LOG_DIR": tempfile.mkdtemp(),
            "SOL_ENABLE": "false",
        },
    ):
        import dracs.webapp as webapp_mod

        webapp_mod.DB_PATH = webapp_db
        webapp_mod.db_initialize(webapp_db)
        webapp_mod.SOL_ENABLE = False
        webapp_mod.app.config["TESTING"] = True
        with webapp_mod.app.test_client() as c:
            yield c


def _login_admin(client):
    client.post(
        "/login",
        data=json.dumps({"username": "admin", "password": "admin"}),
        content_type="application/json",
    )


def _login_site_admin(client, webapp_db):
    create_user("siteadmin", "sitepass", "user")
    set_user_site_role("siteadmin", get_default_site_id(), "admin")
    client.post(
        "/login",
        data=json.dumps({"username": "siteadmin", "password": "sitepass"}),
        content_type="application/json",
    )


def _login_site_user(client, webapp_db):
    create_user("siteuser", "userpass", "user")
    set_user_site_role("siteuser", get_default_site_id(), "user")
    client.post(
        "/login",
        data=json.dumps({"username": "siteuser", "password": "userpass"}),
        content_type="application/json",
    )


def _set_conserver_password(site_name="Default", password="testpass123"):
    cfg = {"defaults": {"conserver_password": password}, "hosts": {}}
    set_site_ini_config(site_name, cfg)


class TestSolConnectInfoAuth:
    def test_no_auth_returns_401(self, client):
        resp = client.get("/api/sol/connect-info")
        assert resp.status_code == 401

    def test_site_user_returns_403(self, client, webapp_db):
        _set_conserver_password()
        _login_site_user(client, webapp_db)
        resp = client.get("/api/sol/connect-info")
        assert resp.status_code == 403

    def test_superadmin_allowed(self, client, webapp_db):
        _set_conserver_password()
        _login_admin(client)
        with patch("socket.getfqdn", return_value="dracs.example.com"):
            resp = client.get("/api/sol/connect-info")
        assert resp.status_code == 200

    def test_site_admin_allowed(self, client, webapp_db):
        _set_conserver_password()
        _login_site_admin(client, webapp_db)
        with patch("socket.getfqdn", return_value="dracs.example.com"):
            resp = client.get("/api/sol/connect-info")
        assert resp.status_code == 200


class TestSolConnectInfoResponse:
    def test_returns_expected_fields(self, client, webapp_db):
        _set_conserver_password(password="secret123")
        _login_admin(client)
        with patch("socket.getfqdn", return_value="dracs.example.com"):
            resp = client.get("/api/sol/connect-info")
        data = resp.get_json()
        assert data["success"] is True
        assert data["server"] == "dracs.example.com"
        assert data["port"] == "3109"
        assert data["username"] == "Default"
        assert data["password"] == "secret123"

    def test_site_param_selects_site(self, client, webapp_db):
        _set_conserver_password(site_name="Default", password="defaultpass")
        _login_admin(client)
        with patch("socket.getfqdn", return_value="dracs.example.com"):
            resp = client.get("/api/sol/connect-info?site=Default")
        assert resp.status_code == 200
        assert resp.get_json()["username"] == "Default"

    def test_unknown_site_returns_404(self, client, webapp_db):
        _login_admin(client)
        resp = client.get("/api/sol/connect-info?site=NoSuchSite")
        assert resp.status_code == 404

    def test_no_password_configured_returns_500(self, client, webapp_db):
        # Write config with no conserver_password
        set_site_ini_config("Default", {"defaults": {}, "hosts": {}})
        _login_admin(client)
        resp = client.get("/api/sol/connect-info")
        assert resp.status_code == 500
        assert "password" in resp.get_json()["message"].lower()


class TestSolConnectInfoDisabled:
    def test_sol_disabled_returns_404(self, client_sol_disabled, webapp_db):
        _login_admin(client_sol_disabled)
        resp = client_sol_disabled.get("/api/sol/connect-info")
        assert resp.status_code == 404
        assert "not enabled" in resp.get_json()["message"]


class TestSolConnectInfoSslFields:
    def test_ssl_false_when_no_cert_available(self, client, webapp_db):
        _set_conserver_password()
        _login_admin(client)
        with (
            patch("socket.getfqdn", return_value="dracs.example.com"),
            patch("dracs.sol._ssl_cert_key_paths", return_value=(None, None)),
            patch.dict(os.environ, {"SOL_SSL_CA": ""}),
        ):
            resp = client.get("/api/sol/connect-info")
        data = resp.get_json()
        assert data["ssl"] is False
        assert data["ssl_ca"] is None

    def test_ssl_true_when_cert_available(self, client, webapp_db, tmp_path):
        _set_conserver_password()
        _login_admin(client)
        fake_cert = tmp_path / "cert.pem"
        with (
            patch("socket.getfqdn", return_value="dracs.example.com"),
            patch("dracs.sol._ssl_cert_key_paths", return_value=(fake_cert, fake_cert)),
            patch.dict(os.environ, {"SOL_SSL_CA": ""}),
        ):
            resp = client.get("/api/sol/connect-info")
        data = resp.get_json()
        assert data["ssl"] is True
        assert data["ssl_ca"] is None

    def test_ssl_ca_content_included_when_ca_env_set(self, client, webapp_db, tmp_path):
        _set_conserver_password()
        _login_admin(client)
        fake_cert = tmp_path / "cert.pem"
        ca_file = tmp_path / "ca.pem"
        ca_file.write_text("-----BEGIN CERTIFICATE-----\nFAKECA\n-----END CERTIFICATE-----\n")
        with (
            patch("socket.getfqdn", return_value="dracs.example.com"),
            patch("dracs.sol._ssl_cert_key_paths", return_value=(fake_cert, fake_cert)),
            patch.dict(os.environ, {"SOL_SSL_CA": str(ca_file)}),
        ):
            resp = client.get("/api/sol/connect-info")
        data = resp.get_json()
        assert data["ssl"] is True
        assert "FAKECA" in data["ssl_ca"]

    def test_ssl_ca_none_when_ca_file_unreadable(self, client, webapp_db, tmp_path):
        _set_conserver_password()
        _login_admin(client)
        fake_cert = tmp_path / "cert.pem"
        with (
            patch("socket.getfqdn", return_value="dracs.example.com"),
            patch("dracs.sol._ssl_cert_key_paths", return_value=(fake_cert, fake_cert)),
            patch.dict(os.environ, {"SOL_SSL_CA": "/nonexistent/path/ca.pem"}),
        ):
            resp = client.get("/api/sol/connect-info")
        data = resp.get_json()
        assert data["ssl"] is True
        assert data["ssl_ca"] is None

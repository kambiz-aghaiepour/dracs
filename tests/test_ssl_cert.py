"""Tests for SSL certificate management: DB layer, scheduler, helpers, API routes, job executor."""

import json
import os
import tempfile
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from dracs.db import (
    create_site,
    db_initialize,
    delete_host_ssl_override,
    get_all_host_ssl_overrides,
    get_all_ssl_scheduled_sites,
    get_default_site_id,
    get_host_ssl_override,
    get_site_by_name,
    get_site_ssl_config,
    update_ssl_schedule_last_run,
    upsert_host_ssl_override,
    upsert_site_ssl_config,
    upsert_system,
)
from dracs.users import create_user, set_user_site_role

# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture
def ssl_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    db_initialize(path)
    yield path
    if os.path.exists(path):
        os.unlink(path)


@pytest.fixture
def ssl_db_with_system(ssl_db):
    upsert_system(
        ssl_db, "TAG001", "server01", "R660", "7.0.0", "2.1.0", "Jan 1, 2027", 0
    )
    yield ssl_db


@pytest.fixture
def ssl_client(ssl_db_with_system, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    with patch.dict(
        os.environ,
        {
            "DRACS_DB": ssl_db_with_system,
            "DRACS_DNS_STRING": "mgmt-",
            "DRACS_DNS_MODE": "prefix",
            "WEBADMIN_USER": "admin",
            "WEBADMIN_PASSWORD": "admin",
        },
    ):
        import dracs.webapp as webapp_mod

        webapp_mod.DB_PATH = ssl_db_with_system
        webapp_mod.db_initialize(ssl_db_with_system)
        webapp_mod.app.config["TESTING"] = True
        with webapp_mod.app.test_client() as c:
            yield c


def _login(client, username="admin", password="admin"):
    client.post(
        "/login",
        data=json.dumps({"username": username, "password": password}),
        content_type="application/json",
    )


def _login_non_superadmin(client, username="localadmin", password="pass123"):
    create_user(username, password, role="admin")
    default_id = get_default_site_id()
    set_user_site_role(username, default_id, "admin")
    _login(client, username, password)


def _make_cert_and_key_pem(days_until_expiry=90):
    """Generate a real self-signed cert + RSA key pair as PEM strings."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    now = datetime.now(timezone.utc)
    subject = x509.Name([x509.NameAttribute(x509.NameOID.COMMON_NAME, "*.example.com")])
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=days_until_expiry))
        .sign(key, hashes.SHA256())
    )
    cert_pem = cert.public_bytes(serialization.Encoding.PEM).decode()
    key_pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption(),
    ).decode()
    return cert_pem, key_pem


# ── DB layer: SiteSslConfig ───────────────────────────────────────────────────


class TestSiteSslConfigDb:
    def test_get_returns_defaults_when_no_row(self, ssl_db):
        site = get_site_by_name("Default")
        cfg = get_site_ssl_config(site["id"])
        assert cfg["enabled"] is False
        assert cfg["has_cert"] is False
        assert cfg["has_key"] is False
        assert cfg["cert_pem"] is None
        assert cfg["schedule_enabled"] is False

    def test_upsert_creates_row(self, ssl_db):
        site = get_site_by_name("Default")
        upsert_site_ssl_config(site["id"], {"enabled": True})
        cfg = get_site_ssl_config(site["id"])
        assert cfg["enabled"] is True

    def test_upsert_stores_cert_and_key(self, ssl_db):
        site = get_site_by_name("Default")
        cert_pem, key_pem = _make_cert_and_key_pem()
        upsert_site_ssl_config(
            site["id"],
            {
                "cert_pem": cert_pem,
                "key_pem": key_pem,
                "cert_fingerprint": "AA:BB",
                "cert_expiry": "2027-01-01T00:00:00+00:00",
            },
        )
        cfg = get_site_ssl_config(site["id"])
        assert cfg["has_cert"] is True
        assert cfg["has_key"] is True
        assert cfg["cert_fingerprint"] == "AA:BB"
        assert cfg["cert_expiry"] == "2027-01-01T00:00:00+00:00"
        assert cfg["cert_pem"] == cert_pem

    def test_upsert_partial_update_preserves_existing(self, ssl_db):
        site = get_site_by_name("Default")
        upsert_site_ssl_config(site["id"], {"enabled": True, "cert_fingerprint": "FP1"})
        upsert_site_ssl_config(site["id"], {"schedule_enabled": True})
        cfg = get_site_ssl_config(site["id"])
        assert cfg["enabled"] is True
        assert cfg["cert_fingerprint"] == "FP1"
        assert cfg["schedule_enabled"] is True

    def test_upsert_sets_schedule_fields(self, ssl_db):
        site = get_site_by_name("Default")
        upsert_site_ssl_config(
            site["id"],
            {
                "schedule_enabled": True,
                "schedule_frequency": "weekly",
                "schedule_time": "02:30",
            },
        )
        cfg = get_site_ssl_config(site["id"])
        assert cfg["schedule_enabled"] is True
        assert cfg["schedule_frequency"] == "weekly"
        assert cfg["schedule_time"] == "02:30"

    def test_upsert_clears_field_with_empty_string(self, ssl_db):
        site = get_site_by_name("Default")
        upsert_site_ssl_config(site["id"], {"schedule_frequency": "weekly"})
        upsert_site_ssl_config(site["id"], {"schedule_frequency": ""})
        cfg = get_site_ssl_config(site["id"])
        assert cfg["schedule_frequency"] is None

    def test_get_all_ssl_scheduled_sites_empty_when_none_configured(self, ssl_db):
        assert get_all_ssl_scheduled_sites() == []

    def test_get_all_ssl_scheduled_sites_returns_enabled_sites(self, ssl_db):
        site = get_site_by_name("Default")
        upsert_site_ssl_config(
            site["id"],
            {
                "enabled": True,
                "schedule_enabled": True,
                "schedule_frequency": "daily",
                "schedule_time": "03:00",
            },
        )
        sites = get_all_ssl_scheduled_sites()
        assert len(sites) == 1
        assert sites[0]["site_id"] == site["id"]
        assert sites[0]["schedule_frequency"] == "daily"

    def test_get_all_ssl_scheduled_sites_excludes_disabled(self, ssl_db):
        site = get_site_by_name("Default")
        upsert_site_ssl_config(
            site["id"],
            {
                "enabled": False,
                "schedule_enabled": True,
                "schedule_frequency": "daily",
                "schedule_time": "03:00",
            },
        )
        assert get_all_ssl_scheduled_sites() == []

    def test_get_all_ssl_scheduled_sites_excludes_schedule_disabled(self, ssl_db):
        site = get_site_by_name("Default")
        upsert_site_ssl_config(
            site["id"],
            {
                "enabled": True,
                "schedule_enabled": False,
                "schedule_frequency": "daily",
                "schedule_time": "03:00",
            },
        )
        assert get_all_ssl_scheduled_sites() == []

    def test_update_ssl_schedule_last_run_stamps_timestamp(self, ssl_db):
        site = get_site_by_name("Default")
        upsert_site_ssl_config(site["id"], {"enabled": True, "schedule_enabled": True})
        before = datetime.now()
        update_ssl_schedule_last_run(site["id"])
        after = datetime.now()
        cfg = get_site_ssl_config(site["id"])
        last_run = datetime.fromisoformat(cfg["schedule_last_run"])
        assert before <= last_run <= after

    def test_update_ssl_schedule_last_run_noop_when_no_row(self, ssl_db):
        site = get_site_by_name("Default")
        update_ssl_schedule_last_run(site["id"])  # must not raise


# ── DB layer: HostSslOverride ─────────────────────────────────────────────────


class TestHostSslOverrideDb:
    def test_get_returns_none_when_not_set(self, ssl_db):
        site = get_site_by_name("Default")
        assert get_host_ssl_override("server01.example.com", site["id"]) is None

    def test_upsert_creates_row(self, ssl_db):
        site = get_site_by_name("Default")
        cert_pem, key_pem = _make_cert_and_key_pem()
        upsert_host_ssl_override(
            "server01.example.com",
            site["id"],
            {"cert_pem": cert_pem, "key_pem": key_pem, "cert_fingerprint": "CC:DD"},
        )
        row = get_host_ssl_override("server01.example.com", site["id"])
        assert row is not None
        assert row["has_cert"] is True
        assert row["has_key"] is True
        assert row["cert_fingerprint"] == "CC:DD"

    def test_upsert_updates_existing_row(self, ssl_db):
        site = get_site_by_name("Default")
        cert_pem, key_pem = _make_cert_and_key_pem()
        upsert_host_ssl_override(
            "server01.example.com",
            site["id"],
            {"cert_pem": cert_pem, "cert_fingerprint": "OLD"},
        )
        upsert_host_ssl_override(
            "server01.example.com",
            site["id"],
            {"cert_fingerprint": "NEW"},
        )
        row = get_host_ssl_override("server01.example.com", site["id"])
        assert row["cert_fingerprint"] == "NEW"

    def test_delete_returns_false_when_not_found(self, ssl_db):
        site = get_site_by_name("Default")
        assert delete_host_ssl_override("nohost.example.com", site["id"]) is False

    def test_delete_returns_true_and_removes_row(self, ssl_db):
        site = get_site_by_name("Default")
        upsert_host_ssl_override(
            "server01.example.com", site["id"], {"cert_fingerprint": "FP"}
        )
        assert delete_host_ssl_override("server01.example.com", site["id"]) is True
        assert get_host_ssl_override("server01.example.com", site["id"]) is None

    def test_get_all_returns_empty_when_none(self, ssl_db):
        site = get_site_by_name("Default")
        assert get_all_host_ssl_overrides(site["id"]) == {}

    def test_get_all_returns_all_overrides(self, ssl_db):
        site = get_site_by_name("Default")
        upsert_host_ssl_override(
            "host1.example.com", site["id"], {"cert_fingerprint": "FP1"}
        )
        upsert_host_ssl_override(
            "host2.example.com", site["id"], {"cert_fingerprint": "FP2"}
        )
        overrides = get_all_host_ssl_overrides(site["id"])
        assert "host1.example.com" in overrides
        assert "host2.example.com" in overrides
        assert overrides["host1.example.com"]["cert_fingerprint"] == "FP1"

    def test_get_all_ignores_other_sites(self, ssl_db):
        site1 = get_site_by_name("Default")
        site2 = create_site("Site2")
        upsert_host_ssl_override(
            "host1.example.com", site1["id"], {"cert_fingerprint": "FP1"}
        )
        upsert_host_ssl_override(
            "host2.example.com", site2["id"], {"cert_fingerprint": "FP2"}
        )
        overrides = get_all_host_ssl_overrides(site1["id"])
        assert "host1.example.com" in overrides
        assert "host2.example.com" not in overrides


# ── Scheduler: _ssl_schedule_due ─────────────────────────────────────────────


class TestSslScheduleDue:
    def _cfg(self, **kwargs):
        base = {
            "enabled": True,
            "schedule_enabled": True,
            "schedule_frequency": "daily",
            "schedule_time": "02:00",
            "schedule_last_run": None,
        }
        base.update(kwargs)
        return base

    def test_returns_false_when_not_enabled(self):
        from dracs.jobqueue import _ssl_schedule_due

        cfg = self._cfg(enabled=False)
        assert _ssl_schedule_due(cfg) is False

    def test_returns_false_when_schedule_not_enabled(self):
        from dracs.jobqueue import _ssl_schedule_due

        cfg = self._cfg(schedule_enabled=False)
        assert _ssl_schedule_due(cfg) is False

    def test_returns_false_when_no_schedule_time(self):
        from dracs.jobqueue import _ssl_schedule_due

        cfg = self._cfg(schedule_time=None)
        assert _ssl_schedule_due(cfg) is False

    def test_returns_false_when_invalid_schedule_time(self):
        from dracs.jobqueue import _ssl_schedule_due

        cfg = self._cfg(schedule_time="not-a-time")
        assert _ssl_schedule_due(cfg) is False

    def test_returns_false_when_time_not_yet_reached(self):
        from dracs.jobqueue import _ssl_schedule_due

        # Freeze "now" to 06:00 so 23:59 is definitely in the future
        frozen = datetime(2026, 1, 15, 6, 0, 0)
        with patch("dracs.jobqueue.datetime") as mock_dt:
            mock_dt.now.return_value = frozen
            mock_dt.fromisoformat = datetime.fromisoformat
            cfg = self._cfg(schedule_time="23:59")
            assert _ssl_schedule_due(cfg) is False

    def test_returns_true_daily_not_run_today(self):
        from dracs.jobqueue import _ssl_schedule_due

        yesterday = (datetime.now() - timedelta(days=1)).isoformat()
        cfg = self._cfg(schedule_time="00:00", schedule_last_run=yesterday)
        assert _ssl_schedule_due(cfg) is True

    def test_returns_false_daily_already_run_today(self):
        from dracs.jobqueue import _ssl_schedule_due

        today = datetime.now().replace(hour=0, minute=0).isoformat()
        cfg = self._cfg(schedule_time="00:00", schedule_last_run=today)
        assert _ssl_schedule_due(cfg) is False

    def test_returns_true_daily_never_run(self):
        from dracs.jobqueue import _ssl_schedule_due

        cfg = self._cfg(schedule_time="00:00", schedule_last_run=None)
        assert _ssl_schedule_due(cfg) is True

    def test_returns_true_weekly_enough_days_passed(self):
        from dracs.jobqueue import _ssl_schedule_due

        last = (datetime.now() - timedelta(days=8)).isoformat()
        cfg = self._cfg(
            schedule_frequency="weekly", schedule_time="00:00", schedule_last_run=last
        )
        assert _ssl_schedule_due(cfg) is True

    def test_returns_false_weekly_too_soon(self):
        from dracs.jobqueue import _ssl_schedule_due

        last = (datetime.now() - timedelta(days=3)).isoformat()
        cfg = self._cfg(
            schedule_frequency="weekly", schedule_time="00:00", schedule_last_run=last
        )
        assert _ssl_schedule_due(cfg) is False

    def test_returns_true_biweekly_enough_days_passed(self):
        from dracs.jobqueue import _ssl_schedule_due

        last = (datetime.now() - timedelta(days=15)).isoformat()
        cfg = self._cfg(
            schedule_frequency="biweekly", schedule_time="00:00", schedule_last_run=last
        )
        assert _ssl_schedule_due(cfg) is True

    def test_returns_false_biweekly_too_soon(self):
        from dracs.jobqueue import _ssl_schedule_due

        last = (datetime.now() - timedelta(days=10)).isoformat()
        cfg = self._cfg(
            schedule_frequency="biweekly", schedule_time="00:00", schedule_last_run=last
        )
        assert _ssl_schedule_due(cfg) is False

    def test_returns_true_monthly_enough_days_passed(self):
        from dracs.jobqueue import _ssl_schedule_due

        last = (datetime.now() - timedelta(days=31)).isoformat()
        cfg = self._cfg(
            schedule_frequency="monthly", schedule_time="00:00", schedule_last_run=last
        )
        assert _ssl_schedule_due(cfg) is True

    def test_returns_false_monthly_too_soon(self):
        from dracs.jobqueue import _ssl_schedule_due

        last = (datetime.now() - timedelta(days=20)).isoformat()
        cfg = self._cfg(
            schedule_frequency="monthly", schedule_time="00:00", schedule_last_run=last
        )
        assert _ssl_schedule_due(cfg) is False

    def test_returns_true_quarterly_enough_days_passed(self):
        from dracs.jobqueue import _ssl_schedule_due

        last = (datetime.now() - timedelta(days=91)).isoformat()
        cfg = self._cfg(
            schedule_frequency="quarterly",
            schedule_time="00:00",
            schedule_last_run=last,
        )
        assert _ssl_schedule_due(cfg) is True

    def test_returns_false_unknown_frequency(self):
        from dracs.jobqueue import _ssl_schedule_due

        cfg = self._cfg(schedule_frequency="hourly", schedule_time="00:00")
        assert _ssl_schedule_due(cfg) is False


# ── Helper functions ──────────────────────────────────────────────────────────


class TestParseCertPem:
    def test_returns_fingerprint_and_expiry(self):
        import dracs.webapp as webapp_mod

        cert_pem, _ = _make_cert_and_key_pem(days_until_expiry=90)
        fp, expiry = webapp_mod._parse_cert_pem(cert_pem)
        assert len(fp) > 0
        assert ":" in fp
        assert "2026" in expiry or "2027" in expiry

    def test_raises_value_error_for_invalid_pem(self):
        import dracs.webapp as webapp_mod

        with pytest.raises(ValueError, match="Invalid certificate PEM"):
            webapp_mod._parse_cert_pem("not-a-cert")

    def test_raises_value_error_for_key_pem_as_cert(self):
        import dracs.webapp as webapp_mod

        _, key_pem = _make_cert_and_key_pem()
        with pytest.raises(ValueError, match="Invalid certificate PEM"):
            webapp_mod._parse_cert_pem(key_pem)


class TestValidateKeyPem:
    def test_succeeds_for_valid_key(self):
        import dracs.webapp as webapp_mod

        _, key_pem = _make_cert_and_key_pem()
        webapp_mod._validate_key_pem(key_pem)  # must not raise

    def test_raises_value_error_for_invalid_pem(self):
        import dracs.webapp as webapp_mod

        with pytest.raises(ValueError, match="Invalid private key PEM"):
            webapp_mod._validate_key_pem("not-a-key")

    def test_raises_value_error_for_cert_pem_as_key(self):
        import dracs.webapp as webapp_mod

        cert_pem, _ = _make_cert_and_key_pem()
        with pytest.raises(ValueError, match="Invalid private key PEM"):
            webapp_mod._validate_key_pem(cert_pem)


# ── API: /api/system/ssl-tools ────────────────────────────────────────────────


class TestApiSslTools:
    def test_requires_auth(self, ssl_client):
        resp = ssl_client.get("/api/system/ssl-tools")
        assert resp.status_code in (401, 302)

    def test_requires_superadmin(self, ssl_client):
        _login_non_superadmin(ssl_client)
        resp = ssl_client.get("/api/system/ssl-tools")
        assert resp.status_code == 403

    def test_binary_not_found(self, ssl_client):
        _login(ssl_client)
        with patch("os.path.exists", return_value=False):
            resp = ssl_client.get("/api/system/ssl-tools")
        data = resp.get_json()
        assert data["success"] is True
        assert data["available"] is False

    def test_binary_found(self, ssl_client):
        _login(ssl_client)
        with patch("os.path.exists", return_value=True):
            resp = ssl_client.get("/api/system/ssl-tools")
        data = resp.get_json()
        assert data["success"] is True
        assert data["available"] is True


# ── API: /api/sites/<name>/ssl-config ────────────────────────────────────────


class TestApiSiteSslConfig:
    def test_get_requires_auth(self, ssl_client):
        resp = ssl_client.get("/api/sites/Default/ssl-config")
        assert resp.status_code in (401, 302)

    def test_get_requires_superadmin(self, ssl_client):
        _login_non_superadmin(ssl_client)
        resp = ssl_client.get("/api/sites/Default/ssl-config")
        assert resp.status_code == 403

    def test_get_returns_404_for_unknown_site(self, ssl_client):
        _login(ssl_client)
        resp = ssl_client.get("/api/sites/NoSuch/ssl-config")
        assert resp.status_code == 404

    def test_get_returns_defaults_for_unconfigured_site(self, ssl_client):
        _login(ssl_client)
        resp = ssl_client.get("/api/sites/Default/ssl-config")
        data = resp.get_json()
        assert data["success"] is True
        assert data["enabled"] is False
        assert data["has_cert"] is False
        assert "cert_pem" not in data
        assert "key_pem" not in data

    def test_get_does_not_return_pem_content(self, ssl_client, ssl_db_with_system):
        _login(ssl_client)
        cert_pem, key_pem = _make_cert_and_key_pem()
        site = get_site_by_name("Default")
        upsert_site_ssl_config(
            site["id"],
            {"cert_pem": cert_pem, "key_pem": key_pem, "cert_fingerprint": "FP"},
        )
        resp = ssl_client.get("/api/sites/Default/ssl-config")
        data = resp.get_json()
        assert "cert_pem" not in data
        assert "key_pem" not in data
        assert data["has_cert"] is True

    def test_put_requires_auth(self, ssl_client):
        resp = ssl_client.put(
            "/api/sites/Default/ssl-config",
            json={"enabled": False},
        )
        assert resp.status_code in (401, 302)

    def test_put_requires_superadmin(self, ssl_client):
        _login_non_superadmin(ssl_client)
        resp = ssl_client.put("/api/sites/Default/ssl-config", json={"enabled": False})
        assert resp.status_code == 403

    def test_put_returns_404_for_unknown_site(self, ssl_client):
        _login(ssl_client)
        resp = ssl_client.put("/api/sites/NoSuch/ssl-config", json={"enabled": False})
        assert resp.status_code == 404

    def test_put_saves_enabled_false(self, ssl_client):
        _login(ssl_client)
        resp = ssl_client.put("/api/sites/Default/ssl-config", json={"enabled": False})
        assert resp.get_json()["success"] is True

    def test_put_rejects_enable_when_binary_missing(self, ssl_client):
        _login(ssl_client)
        with patch("os.path.exists", return_value=False):
            resp = ssl_client.put(
                "/api/sites/Default/ssl-config", json={"enabled": True}
            )
        assert resp.status_code == 400
        assert "not found" in resp.get_json()["message"]

    def test_put_saves_cert_and_key(self, ssl_client):
        _login(ssl_client)
        cert_pem, key_pem = _make_cert_and_key_pem()
        with patch("os.path.exists", return_value=True):
            resp = ssl_client.put(
                "/api/sites/Default/ssl-config",
                json={"cert_pem": cert_pem, "key_pem": key_pem, "enabled": True},
            )
        data = resp.get_json()
        assert data["success"] is True
        assert "cert_fingerprint" in data
        assert "cert_expiry" in data

    def test_put_rejects_cert_without_key(self, ssl_client):
        _login(ssl_client)
        cert_pem, _ = _make_cert_and_key_pem()
        resp = ssl_client.put(
            "/api/sites/Default/ssl-config", json={"cert_pem": cert_pem}
        )
        assert resp.status_code == 400
        assert "Both" in resp.get_json()["message"]

    def test_put_rejects_key_without_cert(self, ssl_client):
        _login(ssl_client)
        _, key_pem = _make_cert_and_key_pem()
        resp = ssl_client.put(
            "/api/sites/Default/ssl-config", json={"key_pem": key_pem}
        )
        assert resp.status_code == 400
        assert "Both" in resp.get_json()["message"]

    def test_put_rejects_invalid_cert_pem(self, ssl_client):
        _login(ssl_client)
        _, key_pem = _make_cert_and_key_pem()
        resp = ssl_client.put(
            "/api/sites/Default/ssl-config",
            json={"cert_pem": "INVALID", "key_pem": key_pem},
        )
        assert resp.status_code == 400
        assert "Invalid certificate" in resp.get_json()["message"]

    def test_put_rejects_invalid_key_pem(self, ssl_client):
        _login(ssl_client)
        cert_pem, _ = _make_cert_and_key_pem()
        resp = ssl_client.put(
            "/api/sites/Default/ssl-config",
            json={"cert_pem": cert_pem, "key_pem": "INVALID"},
        )
        assert resp.status_code == 400
        assert "Invalid private key" in resp.get_json()["message"]

    def test_put_rejects_invalid_frequency(self, ssl_client):
        _login(ssl_client)
        resp = ssl_client.put(
            "/api/sites/Default/ssl-config", json={"schedule_frequency": "hourly"}
        )
        assert resp.status_code == 400
        assert "frequency" in resp.get_json()["message"]

    def test_put_accepts_valid_frequencies(self, ssl_client):
        _login(ssl_client)
        for freq in ("daily", "weekly", "biweekly", "monthly", "quarterly"):
            resp = ssl_client.put(
                "/api/sites/Default/ssl-config", json={"schedule_frequency": freq}
            )
            assert resp.get_json()["success"] is True

    def test_put_rejects_invalid_time(self, ssl_client):
        _login(ssl_client)
        resp = ssl_client.put(
            "/api/sites/Default/ssl-config", json={"schedule_time": "99:99"}
        )
        assert resp.status_code == 400
        assert "time" in resp.get_json()["message"].lower()

    def test_put_accepts_valid_time(self, ssl_client):
        _login(ssl_client)
        resp = ssl_client.put(
            "/api/sites/Default/ssl-config", json={"schedule_time": "14:30"}
        )
        assert resp.get_json()["success"] is True

    def test_put_saves_schedule_enabled(self, ssl_client):
        _login(ssl_client)
        resp = ssl_client.put(
            "/api/sites/Default/ssl-config", json={"schedule_enabled": True}
        )
        assert resp.get_json()["success"] is True


# ── API: /api/sites/<name>/ssl-overrides ─────────────────────────────────────


class TestApiSslOverrides:
    def test_get_requires_auth(self, ssl_client):
        resp = ssl_client.get("/api/sites/Default/ssl-overrides")
        assert resp.status_code in (401, 302)

    def test_get_requires_superadmin(self, ssl_client):
        _login_non_superadmin(ssl_client)
        resp = ssl_client.get("/api/sites/Default/ssl-overrides")
        assert resp.status_code == 403

    def test_get_returns_404_for_unknown_site(self, ssl_client):
        _login(ssl_client)
        resp = ssl_client.get("/api/sites/NoSuch/ssl-overrides")
        assert resp.status_code == 404

    def test_get_returns_empty_dict_when_none(self, ssl_client):
        _login(ssl_client)
        resp = ssl_client.get("/api/sites/Default/ssl-overrides")
        data = resp.get_json()
        assert data["success"] is True
        assert data["overrides"] == {}

    def test_get_returns_existing_overrides(self, ssl_client, ssl_db_with_system):
        _login(ssl_client)
        site = get_site_by_name("Default")
        upsert_host_ssl_override(
            "host1.example.com", site["id"], {"cert_fingerprint": "FP1"}
        )
        resp = ssl_client.get("/api/sites/Default/ssl-overrides")
        data = resp.get_json()
        assert "host1.example.com" in data["overrides"]

    def test_put_requires_auth(self, ssl_client):
        cert_pem, key_pem = _make_cert_and_key_pem()
        resp = ssl_client.put(
            "/api/sites/Default/ssl-overrides/host1.example.com",
            json={"cert_pem": cert_pem, "key_pem": key_pem},
        )
        assert resp.status_code in (401, 302)

    def test_put_requires_superadmin(self, ssl_client):
        _login_non_superadmin(ssl_client)
        cert_pem, key_pem = _make_cert_and_key_pem()
        resp = ssl_client.put(
            "/api/sites/Default/ssl-overrides/host1.example.com",
            json={"cert_pem": cert_pem, "key_pem": key_pem},
        )
        assert resp.status_code == 403

    def test_put_returns_404_for_unknown_site(self, ssl_client):
        _login(ssl_client)
        cert_pem, key_pem = _make_cert_and_key_pem()
        resp = ssl_client.put(
            "/api/sites/NoSuch/ssl-overrides/host1.example.com",
            json={"cert_pem": cert_pem, "key_pem": key_pem},
        )
        assert resp.status_code == 404

    def test_put_rejects_missing_cert_or_key(self, ssl_client):
        _login(ssl_client)
        cert_pem, _ = _make_cert_and_key_pem()
        resp = ssl_client.put(
            "/api/sites/Default/ssl-overrides/host1.example.com",
            json={"cert_pem": cert_pem},
        )
        assert resp.status_code == 400
        assert "required" in resp.get_json()["message"]

    def test_put_rejects_invalid_cert(self, ssl_client):
        _login(ssl_client)
        _, key_pem = _make_cert_and_key_pem()
        resp = ssl_client.put(
            "/api/sites/Default/ssl-overrides/host1.example.com",
            json={"cert_pem": "BADCERT", "key_pem": key_pem},
        )
        assert resp.status_code == 400

    def test_put_saves_override_and_returns_fingerprint(self, ssl_client):
        _login(ssl_client)
        cert_pem, key_pem = _make_cert_and_key_pem()
        resp = ssl_client.put(
            "/api/sites/Default/ssl-overrides/host1.example.com",
            json={"cert_pem": cert_pem, "key_pem": key_pem},
        )
        data = resp.get_json()
        assert data["success"] is True
        assert "cert_fingerprint" in data
        assert "cert_expiry" in data

    def test_delete_requires_auth(self, ssl_client):
        resp = ssl_client.delete("/api/sites/Default/ssl-overrides/host1.example.com")
        assert resp.status_code in (401, 302)

    def test_delete_requires_superadmin(self, ssl_client):
        _login_non_superadmin(ssl_client)
        resp = ssl_client.delete("/api/sites/Default/ssl-overrides/host1.example.com")
        assert resp.status_code == 403

    def test_delete_returns_404_when_not_found(self, ssl_client):
        _login(ssl_client)
        resp = ssl_client.delete("/api/sites/Default/ssl-overrides/nohost.example.com")
        assert resp.status_code == 404

    def test_delete_removes_override(self, ssl_client, ssl_db_with_system):
        _login(ssl_client)
        site = get_site_by_name("Default")
        upsert_host_ssl_override(
            "host1.example.com", site["id"], {"cert_fingerprint": "FP"}
        )
        resp = ssl_client.delete("/api/sites/Default/ssl-overrides/host1.example.com")
        assert resp.get_json()["success"] is True
        assert get_host_ssl_override("host1.example.com", site["id"]) is None


# ── API: /api/sites/<name>/ssl-sweep ─────────────────────────────────────────


class TestApiSslSweep:
    def test_requires_auth(self, ssl_client):
        resp = ssl_client.post("/api/sites/Default/ssl-sweep")
        assert resp.status_code in (401, 302)

    def test_requires_superadmin(self, ssl_client):
        _login_non_superadmin(ssl_client)
        resp = ssl_client.post("/api/sites/Default/ssl-sweep")
        assert resp.status_code == 403

    def test_returns_404_for_unknown_site(self, ssl_client):
        _login(ssl_client)
        resp = ssl_client.post("/api/sites/NoSuch/ssl-sweep")
        assert resp.status_code == 404

    def test_rejects_when_ssl_not_enabled(self, ssl_client):
        _login(ssl_client)
        resp = ssl_client.post("/api/sites/Default/ssl-sweep")
        assert resp.status_code == 400
        assert "not enabled" in resp.get_json()["message"]

    def test_rejects_when_no_cert_configured(self, ssl_client, ssl_db_with_system):
        _login(ssl_client)
        site = get_site_by_name("Default")
        upsert_site_ssl_config(site["id"], {"enabled": True})
        resp = ssl_client.post("/api/sites/Default/ssl-sweep")
        assert resp.status_code == 400
        assert "No SSL" in resp.get_json()["message"]

    def test_enqueues_batch_and_returns_count(self, ssl_client, ssl_db_with_system):
        _login(ssl_client)
        site = get_site_by_name("Default")
        cert_pem, key_pem = _make_cert_and_key_pem()
        upsert_site_ssl_config(
            site["id"],
            {
                "enabled": True,
                "cert_pem": cert_pem,
                "key_pem": key_pem,
                "cert_fingerprint": "FP",
                "cert_expiry": "2027-01-01T00:00:00+00:00",
            },
        )
        with patch("dracs.jobqueue.enqueue_batch", return_value=1) as mock_enqueue:
            resp = ssl_client.post("/api/sites/Default/ssl-sweep")
        data = resp.get_json()
        assert data["success"] is True
        assert data["queued"] == 1
        mock_enqueue.assert_called_once_with(
            "ssl_cert_upload",
            "all",
            site_id=site["id"],
            metadata={"site_name": "Default"},
        )


# ── Job executor: execute_ssl_cert_upload_job ─────────────────────────────────


def _upsert_host_ssl_attrs(hostname, site_id, **kwargs):
    """Write SSL-related attrs to host_config_attr in the new EAV format.

    Accepts: ssl_expiry (str), ssl_self_signed (int 0/1), ssl_fingerprint (str).
    """
    from dracs.db import get_attr_def_by_name, upsert_host_config_attr

    ts = "2026-01-01T00:00:00"
    for attr_name, value in kwargs.items():
        if value is None:
            continue
        stored = str(int(value)) if attr_name == "ssl_self_signed" else str(value)
        attr = get_attr_def_by_name(attr_name)
        upsert_host_config_attr(hostname, site_id, attr["id"], stored, ts)


class TestExecuteSslCertUploadJob:
    def _make_metadata(self, site_name="Default"):
        return {"site_name": site_name}

    def test_raises_when_binary_not_found(self, ssl_db_with_system):
        from dracs.jobqueue import execute_ssl_cert_upload_job

        with patch("os.path.exists", return_value=False):
            with pytest.raises(RuntimeError, match="idracadm7 not found"):
                execute_ssl_cert_upload_job("server01", self._make_metadata())

    def test_raises_for_unknown_site(self, ssl_db_with_system):
        from dracs.jobqueue import execute_ssl_cert_upload_job

        with patch("os.path.exists", return_value=True):
            with pytest.raises(RuntimeError, match="Unknown site"):
                execute_ssl_cert_upload_job("server01", {"site_name": "NoSuch"})

    def test_returns_early_when_ssl_disabled(self, ssl_db_with_system):
        from dracs.jobqueue import execute_ssl_cert_upload_job

        with patch("os.path.exists", return_value=True):
            with patch("subprocess.run") as mock_run:
                execute_ssl_cert_upload_job("server01", self._make_metadata())
                mock_run.assert_not_called()

    def test_returns_early_when_cert_already_current(self, ssl_db_with_system):
        from dracs.jobqueue import execute_ssl_cert_upload_job

        site = get_site_by_name("Default")
        cert_pem, key_pem = _make_cert_and_key_pem(days_until_expiry=90)
        expiry = "2027-06-01T00:00:00+00:00"
        upsert_site_ssl_config(
            site["id"],
            {
                "enabled": True,
                "cert_pem": cert_pem,
                "key_pem": key_pem,
                "cert_expiry": expiry,
            },
        )
        # idrac already has same or newer expiry and is CA-signed (not self-signed)
        _upsert_host_ssl_attrs(
            "server01", site["id"], ssl_expiry=expiry, ssl_self_signed=0
        )

        with patch("os.path.exists", return_value=True):
            with patch("subprocess.run") as mock_run:
                execute_ssl_cert_upload_job("server01", self._make_metadata())
                mock_run.assert_not_called()

    def test_deploys_when_idrac_cert_is_self_signed(self, ssl_db_with_system):
        from dracs.jobqueue import execute_ssl_cert_upload_job

        site = get_site_by_name("Default")
        cert_pem, key_pem = _make_cert_and_key_pem(days_until_expiry=90)
        stored_expiry = "2027-06-01T00:00:00+00:00"
        upsert_site_ssl_config(
            site["id"],
            {
                "enabled": True,
                "cert_pem": cert_pem,
                "key_pem": key_pem,
                "cert_expiry": stored_expiry,
            },
        )
        # iDRAC has a self-signed cert with a later expiry than what's stored
        _upsert_host_ssl_attrs(
            "server01",
            site["id"],
            ssl_expiry="2036-05-26T00:00:00+00:00",
            ssl_self_signed=1,
        )

        ok_result = MagicMock()
        ok_result.returncode = 0

        with patch("os.path.exists", return_value=True):
            with patch("subprocess.run", return_value=ok_result) as mock_run:
                with patch(
                    "dracs.webapp.get_idrac_credentials",
                    return_value=("root", "calvin"),
                ):
                    with patch(
                        "dracs.snmp.build_idrac_hostname",
                        return_value="mgmt-server01.example.com",
                    ):
                        execute_ssl_cert_upload_job("server01", self._make_metadata())
                assert mock_run.called

    def test_skips_when_self_signed_fingerprint_matches(self, ssl_db_with_system):
        from dracs.jobqueue import execute_ssl_cert_upload_job

        site = get_site_by_name("Default")
        cert_pem, key_pem = _make_cert_and_key_pem(days_until_expiry=90)
        fingerprint = "AA:BB:CC:DD:EE:FF"
        upsert_site_ssl_config(
            site["id"],
            {
                "enabled": True,
                "cert_pem": cert_pem,
                "key_pem": key_pem,
                "cert_expiry": "2027-06-01T00:00:00+00:00",
                "cert_fingerprint": fingerprint,
            },
        )
        # iDRAC is self-signed and already has the same cert
        _upsert_host_ssl_attrs(
            "server01",
            site["id"],
            ssl_expiry="2036-05-26T00:00:00+00:00",
            ssl_self_signed=1,
            ssl_fingerprint=fingerprint,
        )

        with patch("os.path.exists", return_value=True):
            with patch("subprocess.run") as mock_run:
                execute_ssl_cert_upload_job("server01", self._make_metadata())
                mock_run.assert_not_called()

    def test_deploys_when_self_signed_fingerprint_differs(self, ssl_db_with_system):
        from dracs.jobqueue import execute_ssl_cert_upload_job

        site = get_site_by_name("Default")
        cert_pem, key_pem = _make_cert_and_key_pem(days_until_expiry=90)
        upsert_site_ssl_config(
            site["id"],
            {
                "enabled": True,
                "cert_pem": cert_pem,
                "key_pem": key_pem,
                "cert_expiry": "2027-06-01T00:00:00+00:00",
                "cert_fingerprint": "AA:BB:CC:DD:EE:FF",
            },
        )
        # iDRAC is self-signed but has a different cert
        _upsert_host_ssl_attrs(
            "server01",
            site["id"],
            ssl_expiry="2036-05-26T00:00:00+00:00",
            ssl_self_signed=1,
            ssl_fingerprint="11:22:33:44:55:66",
        )

        ok_result = MagicMock()
        ok_result.returncode = 0

        with patch("os.path.exists", return_value=True):
            with patch("subprocess.run", return_value=ok_result) as mock_run:
                with patch(
                    "dracs.webapp.get_idrac_credentials",
                    return_value=("root", "calvin"),
                ):
                    with patch(
                        "dracs.snmp.build_idrac_hostname",
                        return_value="mgmt-server01.example.com",
                    ):
                        execute_ssl_cert_upload_job("server01", self._make_metadata())
                assert mock_run.called

    def test_uploads_cert_when_newer(self, ssl_db_with_system):
        from dracs.jobqueue import execute_ssl_cert_upload_job

        site = get_site_by_name("Default")
        cert_pem, key_pem = _make_cert_and_key_pem(days_until_expiry=180)
        upsert_site_ssl_config(
            site["id"],
            {
                "enabled": True,
                "cert_pem": cert_pem,
                "key_pem": key_pem,
                "cert_expiry": "2027-06-01T00:00:00+00:00",
            },
        )
        _upsert_host_ssl_attrs(
            "server01", site["id"], ssl_expiry="2026-01-01T00:00:00+00:00"
        )

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stderr = ""
        mock_result.stdout = ""

        with patch("os.path.exists", return_value=True):
            with patch("subprocess.run", return_value=mock_result) as mock_run:
                with patch(
                    "dracs.webapp.get_idrac_credentials",
                    return_value=("root", "calvin"),
                ):
                    with patch(
                        "dracs.snmp.build_idrac_hostname",
                        return_value="mgmt-server01.example.com",
                    ):
                        execute_ssl_cert_upload_job("server01", self._make_metadata())
        assert mock_run.call_count == 2
        calls = [c.args[0] for c in mock_run.call_args_list]
        assert any("sslkeyupload" in str(c) for c in calls)
        assert any("sslcertupload" in str(c) for c in calls)

    def test_raises_when_keyupload_fails(self, ssl_db_with_system):
        from dracs.jobqueue import execute_ssl_cert_upload_job

        site = get_site_by_name("Default")
        cert_pem, key_pem = _make_cert_and_key_pem(days_until_expiry=180)
        upsert_site_ssl_config(
            site["id"],
            {
                "enabled": True,
                "cert_pem": cert_pem,
                "key_pem": key_pem,
                "cert_expiry": "2027-06-01T00:00:00+00:00",
            },
        )
        _upsert_host_ssl_attrs(
            "server01", site["id"], ssl_expiry="2026-01-01T00:00:00+00:00"
        )

        fail_result = MagicMock()
        fail_result.returncode = 1
        fail_result.stderr = "auth error"
        fail_result.stdout = ""

        with patch("os.path.exists", return_value=True):
            with patch("subprocess.run", return_value=fail_result):
                with patch("dracs.jobqueue.time.sleep"):
                    with patch(
                        "dracs.webapp.get_idrac_credentials",
                        return_value=("root", "calvin"),
                    ):
                        with patch(
                            "dracs.snmp.build_idrac_hostname",
                            return_value="mgmt-server01.example.com",
                        ):
                            with pytest.raises(
                                RuntimeError, match="sslkeyupload failed"
                            ):
                                execute_ssl_cert_upload_job(
                                    "server01", self._make_metadata()
                                )

    def test_keyupload_succeeds_on_retry(self, ssl_db_with_system):
        from dracs.jobqueue import execute_ssl_cert_upload_job

        site = get_site_by_name("Default")
        cert_pem, key_pem = _make_cert_and_key_pem(days_until_expiry=180)
        upsert_site_ssl_config(
            site["id"],
            {
                "enabled": True,
                "cert_pem": cert_pem,
                "key_pem": key_pem,
                "cert_expiry": "2027-06-01T00:00:00+00:00",
            },
        )
        _upsert_host_ssl_attrs(
            "server01", site["id"], ssl_expiry="2026-01-01T00:00:00+00:00"
        )

        fail_result = MagicMock()
        fail_result.returncode = 1
        fail_result.stderr = "Unable to transfer key to the RAC."
        fail_result.stdout = ""

        ok_result = MagicMock()
        ok_result.returncode = 0

        with patch("os.path.exists", return_value=True):
            with patch(
                "subprocess.run", side_effect=[fail_result, ok_result, ok_result]
            ):
                with patch("dracs.jobqueue.time.sleep") as mock_sleep:
                    with patch(
                        "dracs.webapp.get_idrac_credentials",
                        return_value=("root", "calvin"),
                    ):
                        with patch(
                            "dracs.snmp.build_idrac_hostname",
                            return_value="mgmt-server01.example.com",
                        ):
                            execute_ssl_cert_upload_job(
                                "server01", self._make_metadata()
                            )
                mock_sleep.assert_called_once_with(5)

    def test_cleans_up_temp_files_on_success(self, ssl_db_with_system):
        from dracs.jobqueue import execute_ssl_cert_upload_job

        site = get_site_by_name("Default")
        cert_pem, key_pem = _make_cert_and_key_pem(days_until_expiry=180)
        upsert_site_ssl_config(
            site["id"],
            {
                "enabled": True,
                "cert_pem": cert_pem,
                "key_pem": key_pem,
                "cert_expiry": "2027-06-01T00:00:00+00:00",
            },
        )
        _upsert_host_ssl_attrs(
            "server01", site["id"], ssl_expiry="2026-01-01T00:00:00+00:00"
        )

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stderr = ""
        mock_result.stdout = ""

        created_files = []

        original_ntf = tempfile.NamedTemporaryFile

        def tracking_ntf(*args, **kwargs):
            f = original_ntf(*args, **kwargs)
            created_files.append(f.name)
            return f

        with patch("os.path.exists", return_value=True):
            with patch("subprocess.run", return_value=mock_result):
                with patch(
                    "dracs.webapp.get_idrac_credentials",
                    return_value=("root", "calvin"),
                ):
                    with patch(
                        "dracs.snmp.build_idrac_hostname",
                        return_value="mgmt-server01.example.com",
                    ):
                        with patch(
                            "tempfile.NamedTemporaryFile", side_effect=tracking_ntf
                        ):
                            execute_ssl_cert_upload_job(
                                "server01", self._make_metadata()
                            )

        for path in created_files:
            assert not os.path.exists(path), f"Temp file not cleaned up: {path}"

    def test_cleans_up_temp_files_on_failure(self, ssl_db_with_system):
        from dracs.jobqueue import execute_ssl_cert_upload_job

        site = get_site_by_name("Default")
        cert_pem, key_pem = _make_cert_and_key_pem(days_until_expiry=180)
        upsert_site_ssl_config(
            site["id"],
            {
                "enabled": True,
                "cert_pem": cert_pem,
                "key_pem": key_pem,
                "cert_expiry": "2027-06-01T00:00:00+00:00",
            },
        )
        _upsert_host_ssl_attrs(
            "server01", site["id"], ssl_expiry="2026-01-01T00:00:00+00:00"
        )

        fail_result = MagicMock()
        fail_result.returncode = 1
        fail_result.stderr = "error"
        fail_result.stdout = ""

        created_files = []
        original_ntf = tempfile.NamedTemporaryFile

        def tracking_ntf(*args, **kwargs):
            f = original_ntf(*args, **kwargs)
            created_files.append(f.name)
            return f

        with patch("os.path.exists", return_value=True):
            with patch("subprocess.run", return_value=fail_result):
                with patch(
                    "dracs.webapp.get_idrac_credentials",
                    return_value=("root", "calvin"),
                ):
                    with patch(
                        "dracs.snmp.build_idrac_hostname",
                        return_value="mgmt-server01.example.com",
                    ):
                        with patch(
                            "tempfile.NamedTemporaryFile", side_effect=tracking_ntf
                        ):
                            with pytest.raises(RuntimeError):
                                execute_ssl_cert_upload_job(
                                    "server01", self._make_metadata()
                                )

        for path in created_files:
            assert not os.path.exists(path), f"Temp file not cleaned up: {path}"

    def test_raises_when_no_cert_key_configured(self, ssl_db_with_system):
        from dracs.jobqueue import execute_ssl_cert_upload_job

        site = get_site_by_name("Default")
        upsert_site_ssl_config(site["id"], {"enabled": True})

        with patch("os.path.exists", return_value=True):
            with pytest.raises(RuntimeError, match="No SSL cert/key"):
                execute_ssl_cert_upload_job("server01", self._make_metadata())

    def test_raises_when_certupload_fails(self, ssl_db_with_system):
        from dracs.jobqueue import execute_ssl_cert_upload_job

        site = get_site_by_name("Default")
        cert_pem, key_pem = _make_cert_and_key_pem(days_until_expiry=180)
        upsert_site_ssl_config(
            site["id"],
            {
                "enabled": True,
                "cert_pem": cert_pem,
                "key_pem": key_pem,
                "cert_expiry": "2027-06-01T00:00:00+00:00",
            },
        )
        _upsert_host_ssl_attrs(
            "server01", site["id"], ssl_expiry="2026-01-01T00:00:00+00:00"
        )

        ok_result = MagicMock()
        ok_result.returncode = 0
        ok_result.stderr = ""
        ok_result.stdout = ""

        fail_result = MagicMock()
        fail_result.returncode = 1
        fail_result.stderr = "cert upload error"
        fail_result.stdout = ""

        with patch("os.path.exists", return_value=True):
            with patch(
                "subprocess.run", side_effect=[ok_result, fail_result, fail_result]
            ):
                with patch("dracs.jobqueue.time.sleep"):
                    with patch(
                        "dracs.webapp.get_idrac_credentials",
                        return_value=("root", "calvin"),
                    ):
                        with patch(
                            "dracs.snmp.build_idrac_hostname",
                            return_value="mgmt-server01.example.com",
                        ):
                            with pytest.raises(
                                RuntimeError, match="sslcertupload failed"
                            ):
                                execute_ssl_cert_upload_job(
                                    "server01", self._make_metadata()
                                )

    def test_certupload_succeeds_on_retry(self, ssl_db_with_system):
        from dracs.jobqueue import execute_ssl_cert_upload_job

        site = get_site_by_name("Default")
        cert_pem, key_pem = _make_cert_and_key_pem(days_until_expiry=180)
        upsert_site_ssl_config(
            site["id"],
            {
                "enabled": True,
                "cert_pem": cert_pem,
                "key_pem": key_pem,
                "cert_expiry": "2027-06-01T00:00:00+00:00",
            },
        )
        _upsert_host_ssl_attrs(
            "server01", site["id"], ssl_expiry="2026-01-01T00:00:00+00:00"
        )

        ok_result = MagicMock()
        ok_result.returncode = 0

        fail_result = MagicMock()
        fail_result.returncode = 1
        fail_result.stderr = "Web server is restarting"
        fail_result.stdout = ""

        # key succeeds, cert fails first then succeeds on retry
        with patch("os.path.exists", return_value=True):
            with patch(
                "subprocess.run", side_effect=[ok_result, fail_result, ok_result]
            ):
                with patch("dracs.jobqueue.time.sleep") as mock_sleep:
                    with patch(
                        "dracs.webapp.get_idrac_credentials",
                        return_value=("root", "calvin"),
                    ):
                        with patch(
                            "dracs.snmp.build_idrac_hostname",
                            return_value="mgmt-server01.example.com",
                        ):
                            execute_ssl_cert_upload_job(
                                "server01", self._make_metadata()
                            )
                mock_sleep.assert_called_once_with(5)


# ── Dispatch through _execute_job ─────────────────────────────────────────────


class TestSslCertUploadDispatch:
    def test_ssl_cert_upload_dispatched_by_processor(self, ssl_db_with_system):
        import time

        from dracs.jobqueue import JobProcessor, enqueue_job

        enqueue_job(
            "ssl_cert_upload",
            "server01",
            metadata={"site_name": "Default"},
        )
        mock_execute = MagicMock()
        processor = JobProcessor(max_workers=2, poll_interval=0.05)
        with patch("dracs.jobqueue.execute_ssl_cert_upload_job", mock_execute):
            processor.start()
            time.sleep(0.3)
            processor.stop()
        mock_execute.assert_called_once()


# ── SSL schedule loop ─────────────────────────────────────────────────────────


class TestSslScheduleLoop:
    def test_schedule_loop_fires_when_due(self, ssl_db_with_system):
        import time

        from dracs.jobqueue import JobScheduler

        site_cfg = {
            "site_id": 1,
            "site_name": "Default",
            "enabled": True,
            "schedule_enabled": True,
            "schedule_frequency": "daily",
            "schedule_time": "00:00",
            "schedule_last_run": None,
        }

        scheduler = JobScheduler(config_path="/nonexistent")
        scheduler._running = True

        iteration = [0]

        def mock_sleep(seconds):
            iteration[0] += 1
            if iteration[0] >= 1:
                scheduler._running = False

        mock_enqueue = MagicMock(return_value=1)
        mock_last_run = MagicMock()

        with patch("dracs.db.get_all_ssl_scheduled_sites", return_value=[site_cfg]):
            with patch("dracs.jobqueue._ssl_schedule_due", return_value=True):
                with patch("dracs.jobqueue.enqueue_batch", mock_enqueue):
                    with patch("dracs.db.update_ssl_schedule_last_run", mock_last_run):
                        with patch("dracs.jobqueue.time.sleep", side_effect=mock_sleep):
                            scheduler._schedule_loop()

        mock_enqueue.assert_called_once_with(
            "ssl_cert_upload",
            "all",
            site_id=1,
            metadata={"site_name": "Default"},
        )
        mock_last_run.assert_called_once_with(1)

    def test_schedule_loop_skips_when_not_due(self, ssl_db_with_system):
        import time

        from dracs.jobqueue import JobScheduler

        site_cfg = {
            "site_id": 1,
            "site_name": "Default",
            "enabled": True,
            "schedule_enabled": True,
            "schedule_frequency": "daily",
            "schedule_time": "23:59",
            "schedule_last_run": None,
        }

        scheduler = JobScheduler(config_path="/nonexistent")
        scheduler._running = True

        iteration = [0]

        def mock_sleep(seconds):
            iteration[0] += 1
            scheduler._running = False

        mock_enqueue = MagicMock()

        with patch("dracs.db.get_all_ssl_scheduled_sites", return_value=[site_cfg]):
            with patch("dracs.jobqueue._ssl_schedule_due", return_value=False):
                with patch("dracs.jobqueue.enqueue_batch", mock_enqueue):
                    with patch("dracs.jobqueue.time.sleep", side_effect=mock_sleep):
                        scheduler._schedule_loop()

        mock_enqueue.assert_not_called()


# ── Additional scheduler: invalid last_run_str ────────────────────────────────


class TestSslScheduleDueEdgeCases:
    def test_returns_false_with_invalid_last_run_iso_string(self):
        from dracs.jobqueue import _ssl_schedule_due

        cfg = {
            "enabled": True,
            "schedule_enabled": True,
            "schedule_frequency": "daily",
            "schedule_time": "00:00",
            "schedule_last_run": "not-a-valid-iso-date",
        }
        # Invalid ISO string → last_run stays None → should return True (never run)
        assert _ssl_schedule_due(cfg) is True


# ── Additional helper: _parse_cert_pem AttributeError fallback ────────────────


class TestParseCertPemFallback:
    def test_fallback_when_not_valid_after_utc_missing(self):
        """Covers the AttributeError fallback path for older cryptography builds."""
        import dracs.webapp as webapp_mod

        fake_dt = datetime(2027, 6, 1, 0, 0, 0)

        class _OldStyleCert:
            """Simulates a cryptography cert object that lacks not_valid_after_utc."""

            @property
            def not_valid_after_utc(self):
                raise AttributeError("not_valid_after_utc")

            @property
            def not_valid_after(self):
                return fake_dt

            def fingerprint(self, alg):
                return bytes.fromhex("deadbeef" * 8)

        with patch(
            "cryptography.x509.load_pem_x509_certificate", return_value=_OldStyleCert()
        ):
            fp, expiry = webapp_mod._parse_cert_pem(
                "-----BEGIN CERTIFICATE-----\nfake\n-----END CERTIFICATE-----\n"
            )

        assert ":" in fp
        assert "2027" in expiry


# ── Additional API exception handler tests ────────────────────────────────────


class TestApiSslExceptionHandlers:
    def test_ssl_config_get_returns_500_on_exception(self, ssl_client):
        _login(ssl_client)
        with patch(
            "dracs.db.get_site_ssl_config", side_effect=RuntimeError("db error")
        ):
            resp = ssl_client.get("/api/sites/Default/ssl-config")
        assert resp.status_code == 500

    def test_ssl_config_put_returns_500_on_exception(self, ssl_client):
        _login(ssl_client)
        with patch(
            "dracs.db.upsert_site_ssl_config", side_effect=RuntimeError("db error")
        ):
            resp = ssl_client.put(
                "/api/sites/Default/ssl-config", json={"schedule_enabled": True}
            )
        assert resp.status_code == 500

    def test_ssl_overrides_get_returns_500_on_exception(self, ssl_client):
        _login(ssl_client)
        with patch(
            "dracs.db.get_all_host_ssl_overrides", side_effect=RuntimeError("db error")
        ):
            resp = ssl_client.get("/api/sites/Default/ssl-overrides")
        assert resp.status_code == 500

    def test_ssl_override_put_rejects_invalid_key(self, ssl_client):
        _login(ssl_client)
        cert_pem, _ = _make_cert_and_key_pem()
        resp = ssl_client.put(
            "/api/sites/Default/ssl-overrides/host1.example.com",
            json={"cert_pem": cert_pem, "key_pem": "INVALID-KEY"},
        )
        assert resp.status_code == 400
        assert "Invalid private key" in resp.get_json()["message"]

    def test_ssl_override_put_returns_500_on_exception(self, ssl_client):
        _login(ssl_client)
        cert_pem, key_pem = _make_cert_and_key_pem()
        with patch(
            "dracs.db.upsert_host_ssl_override", side_effect=RuntimeError("db error")
        ):
            resp = ssl_client.put(
                "/api/sites/Default/ssl-overrides/host1.example.com",
                json={"cert_pem": cert_pem, "key_pem": key_pem},
            )
        assert resp.status_code == 500

    def test_ssl_override_delete_returns_404_for_unknown_site(self, ssl_client):
        _login(ssl_client)
        resp = ssl_client.delete("/api/sites/NoSuch/ssl-overrides/host1.example.com")
        assert resp.status_code == 404

    def test_ssl_override_delete_returns_500_on_exception(self, ssl_client):
        _login(ssl_client)
        with patch(
            "dracs.db.delete_host_ssl_override", side_effect=RuntimeError("db error")
        ):
            resp = ssl_client.delete(
                "/api/sites/Default/ssl-overrides/host1.example.com"
            )
        assert resp.status_code == 500

    def test_ssl_sweep_returns_500_on_exception(self, ssl_client, ssl_db_with_system):
        _login(ssl_client)
        site = get_site_by_name("Default")
        cert_pem, key_pem = _make_cert_and_key_pem()
        upsert_site_ssl_config(
            site["id"],
            {
                "enabled": True,
                "cert_pem": cert_pem,
                "key_pem": key_pem,
                "cert_fingerprint": "FP",
            },
        )
        with patch(
            "dracs.jobqueue.enqueue_batch", side_effect=RuntimeError("queue error")
        ):
            resp = ssl_client.post("/api/sites/Default/ssl-sweep")
        assert resp.status_code == 500

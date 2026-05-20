import json
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from dracs.db import db_initialize, upsert_system


@pytest.fixture
def webapp_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    db_initialize(path)
    upsert_system(
        path,
        "TAG001",
        "server01.example.com",
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
def client(webapp_db):
    with patch.dict(os.environ, {"DRACS_DB": webapp_db}):
        with patch.dict(
            os.environ,
            {"DRACS_DNS_STRING": "mgmt-", "DRACS_DNS_MODE": "prefix"},
        ):
            import dracs.webapp as webapp_mod

            webapp_mod.DB_PATH = webapp_db
            webapp_mod.db_initialize(webapp_db)
            webapp_mod.app.config["TESTING"] = True
            with webapp_mod.app.test_client() as c:
                yield c


def _login(client):
    client.post(
        "/login",
        data=json.dumps({"username": "admin", "password": "admin"}),
        content_type="application/json",
    )


class TestAvailableFirmwareEndpoint:
    def test_requires_auth(self, client):
        resp = client.get("/api/available-firmware/R660")
        assert resp.status_code == 401

    def test_returns_versions_from_disk(self, client, tmp_path):
        _login(client)
        fw_dir = tmp_path / "firmware"
        fw_dir.mkdir()
        (fw_dir / "R660-7.10.50.d9").write_bytes(b"fake")
        (fw_dir / "R660-7.00.00.d9").write_bytes(b"fake")
        (fw_dir / "R660-6.10.80.d9").write_bytes(b"fake")
        (fw_dir / "R650-5.00.00.d9").write_bytes(b"fake")

        with patch("dracs.webapp.FIRMWARE_IMAGE_DIR", fw_dir):
            resp = client.get("/api/available-firmware/R660")
        data = resp.get_json()
        assert data["success"] is True
        assert len(data["versions"]) == 3
        assert data["versions"][0] == "7.10.50"
        assert data["versions"][-1] == "6.10.80"

    def test_no_matching_files(self, client, tmp_path):
        _login(client)
        fw_dir = tmp_path / "firmware"
        fw_dir.mkdir()

        with patch("dracs.webapp.FIRMWARE_IMAGE_DIR", fw_dir):
            resp = client.get("/api/available-firmware/R660")
        data = resp.get_json()
        assert data["success"] is True
        assert data["versions"] == []

    def test_no_directory(self, client, tmp_path):
        _login(client)
        with patch("dracs.webapp.FIRMWARE_IMAGE_DIR", tmp_path / "nonexistent"):
            resp = client.get("/api/available-firmware/R660")
        data = resp.get_json()
        assert data["success"] is True
        assert data["versions"] == []

    def test_error_handling(self, client):
        _login(client)
        mock_dir = patch(
            "dracs.webapp.FIRMWARE_IMAGE_DIR",
        )
        with patch(
            "dracs.webapp.FIRMWARE_IMAGE_DIR"
        ) as mock_fw:
            mock_fw.is_dir.side_effect = RuntimeError("disk error")
            resp = client.get("/api/available-firmware/R660")
        assert resp.status_code == 500


class TestAvailableBiosEndpoint:
    def test_requires_auth(self, client):
        resp = client.get("/api/available-bios/R660")
        assert resp.status_code == 401

    def test_returns_versions_from_ini(self, client, tmp_path, monkeypatch):
        _login(client)
        ini_file = tmp_path / "BIOS-filename.ini"
        ini_file.write_text(
            "[R660]\n"
            "2.10.1 = BIOS_R660_2.10.1.EXE\n"
            "2.5.0 = BIOS_R660_2.5.0.EXE\n"
            "2.1.0 = BIOS_R660_2.1.0.EXE\n"
        )
        monkeypatch.chdir(tmp_path)
        resp = client.get("/api/available-bios/R660")
        data = resp.get_json()
        assert data["success"] is True
        assert len(data["versions"]) == 3
        assert data["versions"][0] == "2.10.1"
        assert data["versions"][-1] == "2.1.0"

    def test_no_model_in_ini(self, client, tmp_path, monkeypatch):
        _login(client)
        ini_file = tmp_path / "BIOS-filename.ini"
        ini_file.write_text("[R650]\n2.0.0 = BIOS.EXE\n")
        monkeypatch.chdir(tmp_path)
        resp = client.get("/api/available-bios/R660")
        data = resp.get_json()
        assert data["success"] is True
        assert data["versions"] == []

    def test_no_ini_file(self, client, tmp_path, monkeypatch):
        _login(client)
        monkeypatch.chdir(tmp_path)
        resp = client.get("/api/available-bios/R660")
        data = resp.get_json()
        assert data["success"] is True
        assert data["versions"] == []

    def test_error_handling(self, client):
        _login(client)
        with patch(
            "dracs.webapp.configparser.ConfigParser",
            side_effect=RuntimeError("parse error"),
        ):
            resp = client.get("/api/available-bios/R660")
        assert resp.status_code == 500

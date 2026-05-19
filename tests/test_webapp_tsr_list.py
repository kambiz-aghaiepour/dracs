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
            {
                "DRACS_DNS_STRING": "mgmt-",
                "DRACS_DNS_MODE": "prefix",
            },
        ):
            import importlib
            import dracs.webapp as webapp_mod

            webapp_mod.DB_PATH = webapp_db
            webapp_mod.db_initialize(webapp_db)
            webapp_mod.app.config["TESTING"] = True
            with webapp_mod.app.test_client() as c:
                yield c


class TestTsrListEndpoint:
    def test_invalid_hostname(self, client):
        resp = client.get("/api/tsr-list/invalid hostname!!")
        assert resp.status_code == 400
        data = resp.get_json()
        assert data["success"] is False

    def test_host_not_found(self, client):
        resp = client.get("/api/tsr-list/nonexistent.example.com")
        assert resp.status_code == 404
        data = resp.get_json()
        assert data["success"] is False
        assert "not found" in data["message"].lower()

    def test_host_no_tsr_dir(self, client):
        with patch("dracs.webapp.TSR_IMAGE_DIR", Path("/nonexistent/path")):
            resp = client.get("/api/tsr-list/server01.example.com")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        assert data["entries"] == []

    def test_host_with_tsrs(self, client, tmp_path):
        host_dir = tmp_path / "server01.example.com"
        host_dir.mkdir()
        (host_dir / "TSR20260505170637_TAG001.zip").write_bytes(b"fake")
        (host_dir / "TSR20260505120000_TAG001.zip").write_bytes(b"fake")

        with patch("dracs.webapp.TSR_IMAGE_DIR", tmp_path):
            resp = client.get("/api/tsr-list/server01.example.com")

        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        assert len(data["entries"]) == 2
        assert data["entries"][0]["date"] == "2026/05/05 17:06:37"
        assert data["entries"][1]["date"] == "2026/05/05 12:00:00"

    def test_entries_sorted_newest_first(self, client, tmp_path):
        host_dir = tmp_path / "server01.example.com"
        host_dir.mkdir()
        (host_dir / "TSR20260101000000_TAG001.zip").write_bytes(b"fake")
        (host_dir / "TSR20260601000000_TAG001.zip").write_bytes(b"fake")
        (host_dir / "TSR20260301000000_TAG001.zip").write_bytes(b"fake")

        with patch("dracs.webapp.TSR_IMAGE_DIR", tmp_path):
            resp = client.get("/api/tsr-list/server01.example.com")

        data = resp.get_json()
        dates = [e["date"] for e in data["entries"]]
        assert dates == sorted(dates, reverse=True)

    def test_malformed_filename_skipped(self, client, tmp_path):
        host_dir = tmp_path / "server01.example.com"
        host_dir.mkdir()
        (host_dir / "TSR20260505170637_TAG001.zip").write_bytes(b"fake")
        (host_dir / "TSRbadtimestamp_TAG001.zip").write_bytes(b"fake")

        with patch("dracs.webapp.TSR_IMAGE_DIR", tmp_path):
            resp = client.get("/api/tsr-list/server01.example.com")

        data = resp.get_json()
        assert len(data["entries"]) == 1

    def test_entry_fields(self, client, tmp_path):
        host_dir = tmp_path / "server01.example.com"
        host_dir.mkdir()
        (host_dir / "TSR20260505170637_TAG001.zip").write_bytes(b"fake")

        with patch("dracs.webapp.TSR_IMAGE_DIR", tmp_path):
            resp = client.get("/api/tsr-list/server01.example.com")

        entry = resp.get_json()["entries"][0]
        assert "date" in entry
        assert "view_path" in entry
        assert "zip_file" in entry
        assert entry["view_path"] == "20260505170637/"
        assert entry["zip_file"] == "TSR20260505170637_TAG001.zip"

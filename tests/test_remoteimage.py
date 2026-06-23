"""Tests for remote image (racadm remoteimage) endpoints."""

import json
import os
import tempfile
from unittest.mock import MagicMock, patch

import pytest

from dracs.db import db_initialize, upsert_system

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_ENABLED_OUTPUT = (
    "Remote File Share is Enabled\n"
    "UserName \n"
    "Password \n"
    "ShareName http://dracs.example.com/iso/memtest.iso\n"
)

_DISABLED_OUTPUT = (
    "Remote File Share is Disabled\n" "UserName \n" "Password \n" "ShareName \n"
)


# ---------------------------------------------------------------------------
# _parse_remoteimage_status unit tests
# ---------------------------------------------------------------------------


class TestParseRemoteimageStatus:
    def test_enabled_parses_correctly(self):
        from dracs.webapp import _parse_remoteimage_status

        result = _parse_remoteimage_status(_ENABLED_OUTPUT)
        assert result["enabled"] is True
        assert result["url"] == "http://dracs.example.com/iso/memtest.iso"

    def test_disabled_parses_correctly(self):
        from dracs.webapp import _parse_remoteimage_status

        result = _parse_remoteimage_status(_DISABLED_OUTPUT)
        assert result["enabled"] is False
        assert result["url"] == ""

    def test_empty_output_returns_defaults(self):
        from dracs.webapp import _parse_remoteimage_status

        result = _parse_remoteimage_status("")
        assert result["enabled"] is False
        assert result["url"] == ""

    def test_sharename_without_url_returns_empty_url(self):
        from dracs.webapp import _parse_remoteimage_status

        output = "Remote File Share is Enabled\nShareName\n"
        result = _parse_remoteimage_status(output)
        assert result["enabled"] is True
        assert result["url"] == ""

    def test_extra_whitespace_in_sharename_stripped(self):
        from dracs.webapp import _parse_remoteimage_status

        output = (
            "Remote File Share is Enabled\nShareName   http://x.example.com/a.iso   \n"
        )
        result = _parse_remoteimage_status(output)
        assert result["url"] == "http://x.example.com/a.iso"


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------


@pytest.fixture
def ri_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    db_initialize(path)
    upsert_system(
        path, "AABB01", "server01", "R660", "7.0.0", "2.1.0", "Jan 1 2027", 1893456000
    )
    yield path
    if os.path.exists(path):
        os.unlink(path)


@pytest.fixture
def ri_client(ri_db):
    with patch.dict(
        os.environ,
        {
            "DRACS_DB": ri_db,
            "DRACS_DNS_STRING": "mgmt-",
            "DRACS_DNS_MODE": "prefix",
            "DRACS_LOG_DIR": tempfile.mkdtemp(),
        },
    ):
        import dracs.webapp as webapp_mod

        webapp_mod.DB_PATH = ri_db
        webapp_mod.db_initialize(ri_db)
        webapp_mod.app.config["TESTING"] = True
        with webapp_mod.app.test_client() as c:
            yield c


def _login(client, role="admin"):
    from dracs.users import create_user

    username = "testadmin"
    try:
        create_user(username, "pass123", role)
    except Exception:
        pass
    client.post(
        "/login",
        data=json.dumps({"username": username, "password": "pass123"}),
        content_type="application/json",
    )


# ---------------------------------------------------------------------------
# /api/iso-images tests
# ---------------------------------------------------------------------------


class TestIsoImages:
    def test_unauthenticated_redirects(self, ri_client):
        resp = ri_client.get("/api/iso-images")
        assert resp.status_code in (302, 401)

    def test_empty_directory_returns_empty_list(self, ri_client):
        _login(ri_client)
        with tempfile.TemporaryDirectory() as iso_dir:
            with patch("dracs.webapp.ISO_IMAGE_DIR") as mock_dir:
                from pathlib import Path

                mock_dir.__class__ = Path.__class__
                real_path = Path(iso_dir)
                with patch("dracs.webapp.ISO_IMAGE_DIR", real_path):
                    resp = ri_client.get("/api/iso-images")
        data = resp.get_json()
        assert data["success"] is True
        assert data["images"] == []

    def test_missing_directory_returns_empty_list(self, ri_client):
        _login(ri_client)
        from pathlib import Path

        with patch("dracs.webapp.ISO_IMAGE_DIR", Path("/nonexistent/dracs/iso")):
            resp = ri_client.get("/api/iso-images")
        data = resp.get_json()
        assert data["success"] is True
        assert data["images"] == []

    def test_lists_iso_files_with_url(self, ri_client):
        _login(ri_client)
        with tempfile.TemporaryDirectory() as iso_dir:
            from pathlib import Path

            iso_path = Path(iso_dir)
            (iso_path / "memtest.iso").write_text("data")
            (iso_path / "debian.iso").write_text("data")
            (iso_path / "readme.txt").write_text("not an iso")

            with patch("dracs.webapp.ISO_IMAGE_DIR", iso_path):
                with patch("socket.getfqdn", return_value="dracs.test"):
                    resp = ri_client.get("/api/iso-images")

        data = resp.get_json()
        assert data["success"] is True
        names = [img["name"] for img in data["images"]]
        assert sorted(names) == ["debian.iso", "memtest.iso"]
        assert "readme.txt" not in names
        for img in data["images"]:
            assert img["url"] == f"http://dracs.test/iso/{img['name']}"

    def test_images_sorted_alphabetically(self, ri_client):
        _login(ri_client)
        with tempfile.TemporaryDirectory() as iso_dir:
            from pathlib import Path

            iso_path = Path(iso_dir)
            (iso_path / "zz.iso").write_text("data")
            (iso_path / "aa.iso").write_text("data")
            (iso_path / "mm.iso").write_text("data")

            with patch("dracs.webapp.ISO_IMAGE_DIR", iso_path):
                with patch("socket.getfqdn", return_value="dracs.test"):
                    resp = ri_client.get("/api/iso-images")

        data = resp.get_json()
        names = [img["name"] for img in data["images"]]
        assert names == ["aa.iso", "mm.iso", "zz.iso"]


# ---------------------------------------------------------------------------
# GET /api/remoteimage/<hostname> tests
# ---------------------------------------------------------------------------


class TestRemoteimageStatus:
    def test_unauthenticated_redirects(self, ri_client):
        resp = ri_client.get("/api/remoteimage/server01")
        assert resp.status_code in (302, 401)

    def test_invalid_hostname_rejected(self, ri_client):
        _login(ri_client)
        resp = ri_client.get("/api/remoteimage/bad..host")
        assert resp.status_code == 400
        assert resp.get_json()["success"] is False

    def test_enabled_status_returned(self, ri_client):
        _login(ri_client)
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = _ENABLED_OUTPUT
        mock_result.stderr = ""
        with patch("dracs.webapp.subprocess.run", return_value=mock_result):
            with patch(
                "dracs.webapp._build_ssh_racadm_cmd", return_value=["ssh", "cmd"]
            ):
                resp = ri_client.get("/api/remoteimage/server01")
        data = resp.get_json()
        assert data["success"] is True
        assert data["enabled"] is True
        assert data["url"] == "http://dracs.example.com/iso/memtest.iso"

    def test_disabled_status_returned(self, ri_client):
        _login(ri_client)
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = _DISABLED_OUTPUT
        mock_result.stderr = ""
        with patch("dracs.webapp.subprocess.run", return_value=mock_result):
            with patch(
                "dracs.webapp._build_ssh_racadm_cmd", return_value=["ssh", "cmd"]
            ):
                resp = ri_client.get("/api/remoteimage/server01")
        data = resp.get_json()
        assert data["success"] is True
        assert data["enabled"] is False
        assert data["url"] == ""

    def test_ssh_failure_returns_500(self, ri_client):
        _login(ri_client)
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = ""
        mock_result.stderr = "ssh: connect to host failed"
        with patch("dracs.webapp.subprocess.run", return_value=mock_result):
            with patch(
                "dracs.webapp._build_ssh_racadm_cmd", return_value=["ssh", "cmd"]
            ):
                resp = ri_client.get("/api/remoteimage/server01")
        assert resp.status_code == 500
        data = resp.get_json()
        assert data["success"] is False

    def test_timeout_returns_500(self, ri_client):
        import subprocess as _sp

        _login(ri_client)
        with patch(
            "dracs.webapp.subprocess.run", side_effect=_sp.TimeoutExpired("cmd", 30)
        ):
            with patch(
                "dracs.webapp._build_ssh_racadm_cmd", return_value=["ssh", "cmd"]
            ):
                resp = ri_client.get("/api/remoteimage/server01")
        assert resp.status_code == 500
        data = resp.get_json()
        assert "timeout" in data["message"].lower()


# ---------------------------------------------------------------------------
# POST /api/remoteimage/<hostname> tests
# ---------------------------------------------------------------------------


class TestRemoteimageApply:
    def test_unauthenticated_redirects(self, ri_client):
        resp = ri_client.post(
            "/api/remoteimage/server01",
            json={"action": "disable"},
        )
        assert resp.status_code in (302, 401)

    def test_invalid_hostname_rejected(self, ri_client):
        _login(ri_client)
        resp = ri_client.post(
            "/api/remoteimage/bad..host",
            json={"action": "disable"},
        )
        assert resp.status_code == 400
        assert resp.get_json()["success"] is False

    def test_invalid_action_rejected(self, ri_client):
        _login(ri_client)
        resp = ri_client.post(
            "/api/remoteimage/server01",
            json={"action": "reboot"},
        )
        assert resp.status_code == 400
        assert resp.get_json()["success"] is False

    def test_enable_without_url_rejected(self, ri_client):
        _login(ri_client)
        resp = ri_client.post(
            "/api/remoteimage/server01",
            json={"action": "enable"},
        )
        assert resp.status_code == 400
        data = resp.get_json()
        assert data["success"] is False
        assert "url" in data["message"].lower()

    def test_disable_calls_racadm_d(self, ri_client):
        _login(ri_client)
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = ""
        mock_result.stderr = ""
        captured = {}
        with patch("dracs.webapp.subprocess.run", return_value=mock_result) as mock_run:
            with patch(
                "dracs.webapp._build_ssh_racadm_cmd",
                side_effect=lambda *a, **kw: list(a),
            ) as mock_cmd:
                resp = ri_client.post(
                    "/api/remoteimage/server01",
                    json={"action": "disable"},
                )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        args = mock_cmd.call_args[0]
        assert args[0] == "server01"
        assert "remoteimage" in args
        assert "-d" in args

    def test_enable_calls_racadm_c_l(self, ri_client):
        _login(ri_client)
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = ""
        mock_result.stderr = ""
        iso_url = "http://dracs.test/iso/memtest.iso"
        with patch("dracs.webapp.subprocess.run", return_value=mock_result):
            with patch(
                "dracs.webapp._build_ssh_racadm_cmd",
                side_effect=lambda *a, **kw: list(a),
            ) as mock_cmd:
                resp = ri_client.post(
                    "/api/remoteimage/server01",
                    json={"action": "enable", "url": iso_url},
                )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        args = mock_cmd.call_args[0]
        assert "-c" in args
        assert "-l" in args
        assert iso_url in args

    def test_ssh_failure_returns_500(self, ri_client):
        _login(ri_client)
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = "ERROR: Command failed"
        mock_result.stderr = ""
        with patch("dracs.webapp.subprocess.run", return_value=mock_result):
            with patch("dracs.webapp._build_ssh_racadm_cmd", return_value=["cmd"]):
                resp = ri_client.post(
                    "/api/remoteimage/server01",
                    json={"action": "disable"},
                )
        assert resp.status_code == 500
        assert resp.get_json()["success"] is False

    def test_timeout_returns_500(self, ri_client):
        import subprocess as _sp

        _login(ri_client)
        with patch(
            "dracs.webapp.subprocess.run", side_effect=_sp.TimeoutExpired("cmd", 30)
        ):
            with patch("dracs.webapp._build_ssh_racadm_cmd", return_value=["cmd"]):
                resp = ri_client.post(
                    "/api/remoteimage/server01",
                    json={"action": "enable", "url": "http://x.example.com/a.iso"},
                )
        assert resp.status_code == 500
        assert "timeout" in resp.get_json()["message"].lower()

    def test_enable_with_custom_url(self, ri_client):
        _login(ri_client)
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = ""
        mock_result.stderr = ""
        custom_url = "http://custom.example.com/path/to/my.iso"
        with patch("dracs.webapp.subprocess.run", return_value=mock_result):
            with patch(
                "dracs.webapp._build_ssh_racadm_cmd",
                side_effect=lambda *a, **kw: list(a),
            ) as mock_cmd:
                resp = ri_client.post(
                    "/api/remoteimage/server01",
                    json={"action": "enable", "url": custom_url},
                )
        assert resp.status_code == 200
        args = mock_cmd.call_args[0]
        assert custom_url in args

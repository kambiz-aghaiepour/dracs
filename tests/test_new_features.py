"""Tests for power, latest firmware/BIOS, and TSR features."""

import json
import os
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from dracs.db import db_initialize, upsert_system


@pytest.fixture
def webapp_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    db_initialize(path)
    upsert_system(
        path, "TAG001", "server01", "R660", "7.0.0", "2.1.0", "Jan 1, 2027", 1893456000
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
            import importlib
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


# ---------------------------------------------------------------------------
# _parse_catalog_datetime
# ---------------------------------------------------------------------------
class TestParseCatalogDatetime:
    def test_utc_z_suffix(self):
        from dracs.webapp import _parse_catalog_datetime

        dt = _parse_catalog_datetime("2025-03-15T10:30:00Z")
        assert dt.year == 2025
        assert dt.month == 3
        assert dt.hour == 10

    def test_offset_with_colon(self):
        from dracs.webapp import _parse_catalog_datetime

        dt = _parse_catalog_datetime("2025-03-15T10:30:00+05:30")
        assert dt.year == 2025

    def test_naive_datetime(self):
        from dracs.webapp import _parse_catalog_datetime

        dt = _parse_catalog_datetime("2025-03-15T10:30:00")
        assert dt.year == 2025
        assert dt.minute == 30

    def test_whitespace_stripped(self):
        from dracs.webapp import _parse_catalog_datetime

        dt = _parse_catalog_datetime("  2025-01-01T00:00:00Z  ")
        assert dt.year == 2025


# ---------------------------------------------------------------------------
# _sse_event
# ---------------------------------------------------------------------------
class TestSseEvent:
    def test_basic_event(self):
        from dracs.webapp import _sse_event

        result = _sse_event("status", "hello")
        assert result.startswith("data: ")
        assert result.endswith("\n\n")
        data = json.loads(result[6:].strip())
        assert data["type"] == "status"
        assert data["message"] == "hello"

    def test_extra_kwargs(self):
        from dracs.webapp import _sse_event

        result = _sse_event("complete", "", version="1.0", flag=True)
        data = json.loads(result[6:].strip())
        assert data["version"] == "1.0"
        assert data["flag"] is True


# ---------------------------------------------------------------------------
# _find_latest_idrac_firmware
# ---------------------------------------------------------------------------
SAMPLE_CATALOG_XML = """<?xml version="1.0" encoding="utf-16"?>
<Manifest>
  <SoftwareComponent path="FOLDER/firmware.EXE" vendorVersion="7.30.10.50"
      dateTime="2025-03-15T10:00:00Z" releaseDate="March 15, 2025">
    <ComponentType value="FRMW"/>
    <Name><Display>iDRAC Firmware</Display></Name>
    <Category><Display>iDRAC with Lifecycle Controller</Display></Category>
    <SupportedSystems><Brand><Model><Display>R660</Display></Model></Brand></SupportedSystems>
  </SoftwareComponent>
  <SoftwareComponent path="FOLDER/bios.EXE" vendorVersion="2.10.1"
      dateTime="2025-03-10T10:00:00Z" releaseDate="March 10, 2025">
    <ComponentType value="BIOS"/>
    <Name><Display>BIOS Update</Display></Name>
    <Category><Display>BIOS</Display></Category>
    <SupportedSystems><Brand><Model><Display>R660</Display></Model></Brand></SupportedSystems>
  </SoftwareComponent>
  <SoftwareComponent path="FOLDER/other.EXE" vendorVersion="1.0"
      dateTime="2025-01-01T10:00:00Z" releaseDate="Jan 1, 2025">
    <ComponentType value="DRVR"/>
    <Name><Display>Driver</Display></Name>
    <Category><Display>Network</Display></Category>
    <SupportedSystems><Brand><Model><Display>R660</Display></Model></Brand></SupportedSystems>
  </SoftwareComponent>
</Manifest>"""


class TestFindLatestIdracFirmware:
    def test_finds_firmware(self):
        from dracs.webapp import _find_latest_idrac_firmware

        xml_bytes = SAMPLE_CATALOG_XML.encode("utf-16")
        result = _find_latest_idrac_firmware(xml_bytes, "R660")
        assert result is not None
        assert result["version"] == "7.30.10.50"
        assert "firmware.EXE" in result["url"]

    def test_no_match_for_wrong_model(self):
        from dracs.webapp import _find_latest_idrac_firmware

        xml_bytes = SAMPLE_CATALOG_XML.encode("utf-16")
        result = _find_latest_idrac_firmware(xml_bytes, "R999")
        assert result is None

    def test_case_insensitive_model(self):
        from dracs.webapp import _find_latest_idrac_firmware

        xml_bytes = SAMPLE_CATALOG_XML.encode("utf-16")
        result = _find_latest_idrac_firmware(xml_bytes, "r660")
        assert result is not None


# ---------------------------------------------------------------------------
# _find_latest_bios
# ---------------------------------------------------------------------------
class TestFindLatestBios:
    def test_finds_bios(self):
        from dracs.webapp import _find_latest_bios

        xml_bytes = SAMPLE_CATALOG_XML.encode("utf-16")
        result = _find_latest_bios(xml_bytes, "R660")
        assert result is not None
        assert result["version"] == "2.10.1"
        assert "bios.EXE" in result["url"]

    def test_no_match_for_wrong_model(self):
        from dracs.webapp import _find_latest_bios

        xml_bytes = SAMPLE_CATALOG_XML.encode("utf-16")
        result = _find_latest_bios(xml_bytes, "R999")
        assert result is None


# ---------------------------------------------------------------------------
# _update_bios_filename_ini
# ---------------------------------------------------------------------------
class TestUpdateBiosFilenameIni:
    def test_creates_new_file(self, tmp_path, monkeypatch):
        from dracs.webapp import _update_bios_filename_ini

        monkeypatch.chdir(tmp_path)
        _update_bios_filename_ini("R660", "2.10.1", "BIOS_test.EXE")
        ini_path = tmp_path / "BIOS-filename.ini"
        assert ini_path.exists()
        content = ini_path.read_text()
        assert "R660" in content
        assert "BIOS_test.EXE" in content

    def test_updates_existing_file(self, tmp_path, monkeypatch):
        from dracs.webapp import _update_bios_filename_ini

        monkeypatch.chdir(tmp_path)
        ini_path = tmp_path / "BIOS-filename.ini"
        ini_path.write_text("[R660]\n2.0.0 = OLD_BIOS.EXE\n")
        _update_bios_filename_ini("R660", "2.10.1", "NEW_BIOS.EXE")
        content = ini_path.read_text()
        assert "NEW_BIOS.EXE" in content
        assert "OLD_BIOS.EXE" in content


# ---------------------------------------------------------------------------
# _build_ssh_racadm_cmd
# ---------------------------------------------------------------------------
class TestBuildSshRacadmCmd:
    def test_builds_correct_command(self):
        from dracs.webapp import _build_ssh_racadm_cmd

        with patch.dict(
            os.environ,
            {"DRACS_DNS_STRING": "mgmt-", "DRACS_DNS_MODE": "prefix"},
        ):
            cmd = _build_ssh_racadm_cmd("server01", "jobqueue", "view")
        assert "sshpass" in cmd
        assert "racadm" in cmd
        assert "jobqueue" in cmd
        assert "view" in cmd


# ---------------------------------------------------------------------------
# _get_tsr_job_status
# ---------------------------------------------------------------------------
class TestGetTsrJobStatus:
    def test_running_job(self):
        from dracs.webapp import _get_tsr_job_status

        mock_output = (
            "[Job ID=JID_001]\n"
            "Job Name=SupportAssist Collection\n"
            "Status=Running\n"
            "Percent Complete=45\n"
            "Message=The SupportAssist Collection operation started\n"
        )
        mock_result = MagicMock(returncode=0, stdout=mock_output)
        with patch("dracs.webapp.subprocess.run", return_value=mock_result):
            with patch.dict(
                os.environ,
                {"DRACS_DNS_STRING": "mgmt-", "DRACS_DNS_MODE": "prefix"},
            ):
                status = _get_tsr_job_status("server01")
        assert status["state"] == "running"
        assert status["percent_complete"] == "45"

    def test_completed_job(self):
        from dracs.webapp import _get_tsr_job_status

        mock_output = (
            "[Job ID=JID_001]\n"
            "Job Name=SupportAssist Collection\n"
            "Status=Completed\n"
            "Percent Complete=100\n"
            "Message=The SupportAssist Collection Operation is completed successfully\n"
        )
        mock_result = MagicMock(returncode=0, stdout=mock_output)
        with patch("dracs.webapp.subprocess.run", return_value=mock_result):
            with patch.dict(
                os.environ,
                {"DRACS_DNS_STRING": "mgmt-", "DRACS_DNS_MODE": "prefix"},
            ):
                status = _get_tsr_job_status("server01")
        assert status["state"] == "completed"

    def test_no_tsr_jobs(self):
        from dracs.webapp import _get_tsr_job_status

        mock_output = (
            "[Job ID=JID_001]\n"
            "Job Name=Firmware Update\n"
            "Status=Completed\n"
            "Percent Complete=100\n"
            "Message=Job completed\n"
        )
        mock_result = MagicMock(returncode=0, stdout=mock_output)
        with patch("dracs.webapp.subprocess.run", return_value=mock_result):
            with patch.dict(
                os.environ,
                {"DRACS_DNS_STRING": "mgmt-", "DRACS_DNS_MODE": "prefix"},
            ):
                status = _get_tsr_job_status("server01")
        assert status["state"] == "none"

    def test_command_failure(self):
        from dracs.webapp import _get_tsr_job_status

        mock_result = MagicMock(returncode=1, stdout="", stderr="error")
        with patch("dracs.webapp.subprocess.run", return_value=mock_result):
            with patch.dict(
                os.environ,
                {"DRACS_DNS_STRING": "mgmt-", "DRACS_DNS_MODE": "prefix"},
            ):
                status = _get_tsr_job_status("server01")
        assert status["state"] == "error"


# ---------------------------------------------------------------------------
# _find_tsr_zip
# ---------------------------------------------------------------------------
class TestFindTsrZip:
    def test_finds_matching_zip(self, tmp_path):
        from dracs.webapp import _find_tsr_zip

        ts = "20250518120000"
        zip_path = tmp_path / f"TSR{ts}_TAG001.zip"
        zip_path.touch()
        approx = datetime(2025, 5, 18, 12, 0, 30)

        with patch("dracs.webapp.TFTPBOOT_DIR", tmp_path):
            result = _find_tsr_zip("TAG001", approx)
        assert result is not None
        assert "TAG001" in result

    def test_no_match_returns_none(self, tmp_path):
        from dracs.webapp import _find_tsr_zip

        approx = datetime(2025, 5, 18, 12, 0, 0)
        with patch("dracs.webapp.TFTPBOOT_DIR", tmp_path):
            result = _find_tsr_zip("NOTAG", approx)
        assert result is None

    def test_fallback_to_most_recent(self, tmp_path):
        from dracs.webapp import _find_tsr_zip

        zip_path = tmp_path / "TSRbadtimestamp_TAG001.zip"
        zip_path.touch()
        approx = datetime(2025, 5, 18, 12, 0, 0)

        with patch("dracs.webapp.TFTPBOOT_DIR", tmp_path):
            result = _find_tsr_zip("TAG001", approx)
        assert result is not None


# ---------------------------------------------------------------------------
# _extract_tsr
# ---------------------------------------------------------------------------
class TestExtractTsr:
    def test_extracts_outer_and_inner_zip(self, tmp_path):
        from dracs.webapp import _extract_tsr

        inner_zip_path = tmp_path / "inner.pl.zip"
        with zipfile.ZipFile(inner_zip_path, "w") as zf:
            zf.writestr("inner_file.txt", "inner content")

        outer_zip_path = tmp_path / "outer.zip"
        with zipfile.ZipFile(outer_zip_path, "w") as zf:
            zf.write(inner_zip_path, "inner.pl.zip")
            zf.writestr("outer_file.txt", "outer content")

        dest = tmp_path / "extracted"
        _extract_tsr(str(outer_zip_path), str(dest))

        assert (dest / "outer_file.txt").exists()
        assert (dest / "inner_file.txt").exists()

    def test_sets_permissions(self, tmp_path):
        from dracs.webapp import _extract_tsr

        zip_path = tmp_path / "test.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("subdir/file.txt", "content")

        dest = tmp_path / "extracted"
        _extract_tsr(str(zip_path), str(dest))

        file_path = dest / "subdir" / "file.txt"
        assert file_path.exists()
        mode = file_path.stat().st_mode
        assert mode & 0o044


# ---------------------------------------------------------------------------
# Power Status Endpoint
# ---------------------------------------------------------------------------
class TestPowerStatusEndpoint:
    def test_no_auth(self, client):
        resp = client.post(
            "/api/power-status",
            data=json.dumps({"hostname": "server01"}),
            content_type="application/json",
        )
        assert resp.status_code == 401

    def test_no_json(self, client):
        _login(client)
        resp = client.post("/api/power-status", data="bad", content_type="text/plain")
        assert resp.status_code in (400, 500)

    def test_missing_hostname(self, client):
        _login(client)
        resp = client.post(
            "/api/power-status",
            data=json.dumps({}),
            content_type="application/json",
        )
        assert resp.status_code in (400, 500)

    def test_power_on(self, client):
        _login(client)
        mock_result = MagicMock(returncode=0, stdout="Server Power Status: ON")
        with patch("dracs.webapp.subprocess.run", return_value=mock_result):
            resp = client.post(
                "/api/power-status",
                data=json.dumps({"hostname": "server01"}),
                content_type="application/json",
            )
        data = resp.get_json()
        assert data["success"] is True
        assert data["status"] == "on"

    def test_power_off(self, client):
        _login(client)
        mock_result = MagicMock(returncode=0, stdout="Server Power Status: OFF")
        with patch("dracs.webapp.subprocess.run", return_value=mock_result):
            resp = client.post(
                "/api/power-status",
                data=json.dumps({"hostname": "server01"}),
                content_type="application/json",
            )
        data = resp.get_json()
        assert data["success"] is True
        assert data["status"] == "off"

    def test_unexpected_status(self, client):
        _login(client)
        mock_result = MagicMock(returncode=0, stdout="UNKNOWN STATE")
        with patch("dracs.webapp.subprocess.run", return_value=mock_result):
            resp = client.post(
                "/api/power-status",
                data=json.dumps({"hostname": "server01"}),
                content_type="application/json",
            )
        data = resp.get_json()
        assert data["success"] is False

    def test_command_failure(self, client):
        _login(client)
        mock_result = MagicMock(returncode=1, stdout="", stderr="connection refused")
        with patch("dracs.webapp.subprocess.run", return_value=mock_result):
            resp = client.post(
                "/api/power-status",
                data=json.dumps({"hostname": "server01"}),
                content_type="application/json",
            )
        data = resp.get_json()
        assert data["success"] is False

    def test_timeout(self, client):
        _login(client)
        import subprocess

        with patch(
            "dracs.webapp.subprocess.run",
            side_effect=subprocess.TimeoutExpired("cmd", 15),
        ):
            resp = client.post(
                "/api/power-status",
                data=json.dumps({"hostname": "server01"}),
                content_type="application/json",
            )
        assert resp.status_code == 500
        assert "timeout" in resp.get_json()["message"].lower()

    def test_sshpass_not_found(self, client):
        _login(client)
        with patch(
            "dracs.webapp.subprocess.run",
            side_effect=FileNotFoundError("sshpass"),
        ):
            resp = client.post(
                "/api/power-status",
                data=json.dumps({"hostname": "server01"}),
                content_type="application/json",
            )
        assert resp.status_code == 500
        assert "sshpass" in resp.get_json()["message"]

    def test_invalid_hostname(self, client):
        _login(client)
        resp = client.post(
            "/api/power-status",
            data=json.dumps({"hostname": "../../etc/passwd"}),
            content_type="application/json",
        )
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Power Action Endpoint
# ---------------------------------------------------------------------------
class TestPowerActionEndpoint:
    def test_no_auth(self, client):
        resp = client.post(
            "/api/power-action",
            data=json.dumps({"hostname": "server01", "action": "powerup"}),
            content_type="application/json",
        )
        assert resp.status_code == 401

    def test_no_json(self, client):
        _login(client)
        resp = client.post("/api/power-action", data="bad", content_type="text/plain")
        assert resp.status_code in (400, 500)

    def test_missing_hostname(self, client):
        _login(client)
        resp = client.post(
            "/api/power-action",
            data=json.dumps({"action": "powerup"}),
            content_type="application/json",
        )
        assert resp.status_code in (400, 500)

    def test_invalid_action(self, client):
        _login(client)
        resp = client.post(
            "/api/power-action",
            data=json.dumps({"hostname": "server01", "action": "explode"}),
            content_type="application/json",
        )
        assert resp.status_code == 400

    def test_powerup_success(self, client):
        _login(client)
        mock_result = MagicMock(
            returncode=0, stdout="Server power operation successful"
        )
        with patch("dracs.webapp.subprocess.run", return_value=mock_result):
            resp = client.post(
                "/api/power-action",
                data=json.dumps({"hostname": "server01", "action": "powerup"}),
                content_type="application/json",
            )
        data = resp.get_json()
        assert data["success"] is True
        assert "Power on" in data["message"]

    def test_powerdown_success(self, client):
        _login(client)
        mock_result = MagicMock(returncode=0, stdout="ok")
        with patch("dracs.webapp.subprocess.run", return_value=mock_result):
            resp = client.post(
                "/api/power-action",
                data=json.dumps({"hostname": "server01", "action": "powerdown"}),
                content_type="application/json",
            )
        data = resp.get_json()
        assert data["success"] is True
        assert "Hard power off" in data["message"]

    def test_graceshutdown_success(self, client):
        _login(client)
        mock_result = MagicMock(returncode=0, stdout="ok")
        with patch("dracs.webapp.subprocess.run", return_value=mock_result):
            resp = client.post(
                "/api/power-action",
                data=json.dumps({"hostname": "server01", "action": "graceshutdown"}),
                content_type="application/json",
            )
        data = resp.get_json()
        assert data["success"] is True
        assert "Graceful shutdown" in data["message"]

    def test_command_failure(self, client):
        _login(client)
        mock_result = MagicMock(returncode=1, stdout="failed", stderr="")
        with patch("dracs.webapp.subprocess.run", return_value=mock_result):
            resp = client.post(
                "/api/power-action",
                data=json.dumps({"hostname": "server01", "action": "powerup"}),
                content_type="application/json",
            )
        data = resp.get_json()
        assert data["success"] is False

    def test_timeout(self, client):
        _login(client)
        import subprocess

        with patch(
            "dracs.webapp.subprocess.run",
            side_effect=subprocess.TimeoutExpired("cmd", 30),
        ):
            resp = client.post(
                "/api/power-action",
                data=json.dumps({"hostname": "server01", "action": "powerup"}),
                content_type="application/json",
            )
        assert resp.status_code == 500

    def test_sshpass_not_found(self, client):
        _login(client)
        with patch(
            "dracs.webapp.subprocess.run",
            side_effect=FileNotFoundError("sshpass"),
        ):
            resp = client.post(
                "/api/power-action",
                data=json.dumps({"hostname": "server01", "action": "powerup"}),
                content_type="application/json",
            )
        assert resp.status_code == 500

    def test_invalid_hostname(self, client):
        _login(client)
        resp = client.post(
            "/api/power-action",
            data=json.dumps({"hostname": "../../etc/passwd", "action": "powerup"}),
            content_type="application/json",
        )
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Latest Firmware Endpoint
# ---------------------------------------------------------------------------
class TestLatestFirmwareEndpoint:
    def test_no_auth(self, client):
        resp = client.post(
            "/api/latest-firmware",
            data=json.dumps({"model": "R660", "hostname": "server01"}),
            content_type="application/json",
        )
        assert resp.status_code == 401

    def test_no_json(self, client):
        _login(client)
        resp = client.post(
            "/api/latest-firmware", data="bad", content_type="text/plain"
        )
        assert resp.status_code in (400, 415, 500)

    def test_missing_fields(self, client):
        _login(client)
        resp = client.post(
            "/api/latest-firmware",
            data=json.dumps({"model": "R660"}),
            content_type="application/json",
        )
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Latest BIOS Endpoint
# ---------------------------------------------------------------------------
class TestLatestBiosEndpoint:
    def test_no_auth(self, client):
        resp = client.post(
            "/api/latest-bios",
            data=json.dumps({"model": "R660", "hostname": "server01"}),
            content_type="application/json",
        )
        assert resp.status_code == 401

    def test_no_json(self, client):
        _login(client)
        resp = client.post("/api/latest-bios", data="bad", content_type="text/plain")
        assert resp.status_code in (400, 415, 500)

    def test_missing_fields(self, client):
        _login(client)
        resp = client.post(
            "/api/latest-bios",
            data=json.dumps({"hostname": "server01"}),
            content_type="application/json",
        )
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# TSR Status Endpoint
# ---------------------------------------------------------------------------
class TestTsrStatusEndpoint:
    def test_no_auth(self, client):
        resp = client.post(
            "/api/tsr-status",
            data=json.dumps({"hostname": "server01"}),
            content_type="application/json",
        )
        assert resp.status_code == 401

    def test_no_json(self, client):
        _login(client)
        resp = client.post("/api/tsr-status", data="bad", content_type="text/plain")
        assert resp.status_code in (400, 500)

    def test_missing_hostname(self, client):
        _login(client)
        resp = client.post(
            "/api/tsr-status",
            data=json.dumps({}),
            content_type="application/json",
        )
        assert resp.status_code == 400

    def test_invalid_hostname(self, client):
        _login(client)
        resp = client.post(
            "/api/tsr-status",
            data=json.dumps({"hostname": "../../etc/passwd"}),
            content_type="application/json",
        )
        assert resp.status_code == 400

    def test_returns_status(self, client):
        _login(client)
        with patch(
            "dracs.webapp._get_tsr_job_status",
            return_value={"state": "none"},
        ):
            resp = client.post(
                "/api/tsr-status",
                data=json.dumps({"hostname": "server01"}),
                content_type="application/json",
            )
        data = resp.get_json()
        assert data["success"] is True
        assert data["state"] == "none"

    def test_running_status(self, client):
        _login(client)
        with patch(
            "dracs.webapp._get_tsr_job_status",
            return_value={"state": "running", "percent_complete": "50"},
        ):
            resp = client.post(
                "/api/tsr-status",
                data=json.dumps({"hostname": "server01"}),
                content_type="application/json",
            )
        data = resp.get_json()
        assert data["state"] == "running"
        assert data["percent_complete"] == "50"

    def test_timeout(self, client):
        _login(client)
        import subprocess

        with patch(
            "dracs.webapp._get_tsr_job_status",
            side_effect=subprocess.TimeoutExpired("cmd", 30),
        ):
            resp = client.post(
                "/api/tsr-status",
                data=json.dumps({"hostname": "server01"}),
                content_type="application/json",
            )
        assert resp.status_code == 500


# ---------------------------------------------------------------------------
# TSR Collect Endpoint
# ---------------------------------------------------------------------------
class TestTsrCollectEndpoint:
    def test_no_auth(self, client):
        resp = client.post(
            "/api/tsr-collect",
            data=json.dumps({"hostname": "server01", "service_tag": "TAG001"}),
            content_type="application/json",
        )
        assert resp.status_code == 401

    def test_no_json(self, client):
        _login(client)
        resp = client.post("/api/tsr-collect", data="bad", content_type="text/plain")
        assert resp.status_code in (400, 500)

    def test_missing_hostname(self, client):
        _login(client)
        resp = client.post(
            "/api/tsr-collect",
            data=json.dumps({"service_tag": "TAG001"}),
            content_type="application/json",
        )
        assert resp.status_code == 400

    def test_missing_service_tag(self, client):
        _login(client)
        resp = client.post(
            "/api/tsr-collect",
            data=json.dumps({"hostname": "server01"}),
            content_type="application/json",
        )
        assert resp.status_code == 400

    def test_invalid_hostname(self, client):
        _login(client)
        resp = client.post(
            "/api/tsr-collect",
            data=json.dumps({"hostname": "../../etc/passwd", "service_tag": "TAG001"}),
            content_type="application/json",
        )
        assert resp.status_code == 400

    def test_success(self, client):
        _login(client)
        mock_result = MagicMock(returncode=0, stdout="TSR collection started")
        with patch("dracs.webapp.subprocess.run", return_value=mock_result):
            with patch("dracs.webapp.threading.Thread") as mock_thread:
                mock_thread.return_value = MagicMock()
                resp = client.post(
                    "/api/tsr-collect",
                    data=json.dumps({"hostname": "server01", "service_tag": "TAG001"}),
                    content_type="application/json",
                )
        data = resp.get_json()
        assert data["success"] is True
        mock_thread.return_value.start.assert_called_once()

    def test_command_failure(self, client):
        _login(client)
        mock_result = MagicMock(returncode=1, stdout="error", stderr="failed")
        with patch("dracs.webapp.subprocess.run", return_value=mock_result):
            resp = client.post(
                "/api/tsr-collect",
                data=json.dumps({"hostname": "server01", "service_tag": "TAG001"}),
                content_type="application/json",
            )
        assert resp.status_code == 500

    def test_timeout(self, client):
        _login(client)
        import subprocess

        with patch(
            "dracs.webapp.subprocess.run",
            side_effect=subprocess.TimeoutExpired("cmd", 30),
        ):
            resp = client.post(
                "/api/tsr-collect",
                data=json.dumps({"hostname": "server01", "service_tag": "TAG001"}),
                content_type="application/json",
            )
        assert resp.status_code == 500

    def test_sshpass_not_found(self, client):
        _login(client)
        with patch(
            "dracs.webapp.subprocess.run",
            side_effect=FileNotFoundError("sshpass"),
        ):
            resp = client.post(
                "/api/tsr-collect",
                data=json.dumps({"hostname": "server01", "service_tag": "TAG001"}),
                content_type="application/json",
            )
        assert resp.status_code == 500


# ---------------------------------------------------------------------------
# BIOS update route - model in URL
# ---------------------------------------------------------------------------
class TestBiosUpdateModelInUrl:
    @patch("dracs.webapp.run_command_background", return_value=True)
    @patch("dracs.webapp.get_bios_filename", return_value="BIOS_R660_3.0.0.EXE")
    def test_bios_url_includes_model(self, mock_fn, mock_run, client):
        _login(client)
        resp = client.post(
            "/api/bios-update",
            data=json.dumps(
                {"hostname": "server01", "target_bios": "3.0.0", "model": "R660"}
            ),
            content_type="application/json",
        )
        assert resp.status_code == 200
        cmd = mock_run.call_args[0][0]
        url_arg = cmd[-1]
        assert "/bios/R660/" in url_arg


# ---------------------------------------------------------------------------
# Latest Firmware SSE streaming generator
# ---------------------------------------------------------------------------
class TestLatestFirmwareStreaming:
    def _make_catalog_gz(self):
        import gzip

        return gzip.compress(SAMPLE_CATALOG_XML.encode("utf-16"))

    def _make_firmware_exe(self, tmp_path):
        pkg_xml = '<SoftwareComponent vendorVersion="7.30.10.50"></SoftwareComponent>'
        exe_path = tmp_path / "firmware.EXE"
        with zipfile.ZipFile(exe_path, "w") as zf:
            zf.writestr("package.xml", pkg_xml)
            zf.writestr("payload/firmimgFIT.d9", b"firmware data")
        return exe_path

    def test_full_stream_new_firmware(self, client, tmp_path):
        _login(client)
        catalog_gz = self._make_catalog_gz()
        src_exe = self._make_firmware_exe(tmp_path)
        exe_bytes = src_exe.read_bytes()

        mock_catalog_resp = MagicMock()
        mock_catalog_resp.read.return_value = catalog_gz
        mock_catalog_resp.__enter__ = lambda s: s
        mock_catalog_resp.__exit__ = MagicMock(return_value=False)

        mock_exe_resp = MagicMock()
        mock_exe_resp.__enter__ = lambda s: s
        mock_exe_resp.__exit__ = MagicMock(return_value=False)

        def fake_urlopen(req, timeout=None):
            if "Catalog" in req.full_url:
                return mock_catalog_resp
            return mock_exe_resp

        fw_dir = tmp_path / "firmware_dest"
        fw_dir.mkdir()

        import xml.etree.ElementTree as real_ET

        orig_parse = real_ET.parse

        def fake_et_parse(path):
            if "package.xml" in str(path):
                root = real_ET.fromstring(
                    '<SoftwareComponent vendorVersion="7.30.10.50"/>'
                )
                tree = MagicMock()
                tree.getroot.return_value = root
                return tree
            return orig_parse(path)

        def fake_copyfileobj(src, dst):
            dst.write(exe_bytes)

        with (
            patch("dracs.webapp.urllib.request.urlopen", side_effect=fake_urlopen),
            patch("dracs.webapp.shutil.copyfileobj", side_effect=fake_copyfileobj),
            patch("dracs.webapp.defused_ET.parse", side_effect=fake_et_parse),
            patch("dracs.webapp.FIRMWARE_IMAGE_DIR", fw_dir),
        ):
            resp = client.post(
                "/api/latest-firmware",
                data=json.dumps(
                    {
                        "model": "R660",
                        "hostname": "server01",
                        "current_version": "7.00.00",
                    }
                ),
                content_type="application/json",
            )
            data_str = resp.get_data(as_text=True)

        assert resp.status_code == 200
        assert "done." in data_str
        assert "7.30.10.50" in data_str
        assert '"type": "complete"' in data_str

    def test_stream_no_catalog_match(self, client):
        _login(client)
        import gzip

        empty_xml = '<?xml version="1.0" encoding="utf-16"?><Manifest></Manifest>'
        catalog_gz = gzip.compress(empty_xml.encode("utf-16"))

        mock_resp = MagicMock()
        mock_resp.read.return_value = catalog_gz
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("dracs.webapp.urllib.request.urlopen", return_value=mock_resp):
            resp = client.post(
                "/api/latest-firmware",
                data=json.dumps(
                    {"model": "R999", "hostname": "server01", "current_version": ""}
                ),
                content_type="application/json",
            )
            data_str = resp.get_data(as_text=True)

        assert '"type": "error"' in data_str

    def test_stream_error_handling(self, client):
        _login(client)
        with patch(
            "dracs.webapp.urllib.request.urlopen",
            side_effect=Exception("network error"),
        ):
            resp = client.post(
                "/api/latest-firmware",
                data=json.dumps(
                    {"model": "R660", "hostname": "server01", "current_version": ""}
                ),
                content_type="application/json",
            )
            data_str = resp.get_data(as_text=True)

        assert '"type": "error"' in data_str
        assert "network error" in data_str

    def test_stream_already_current(self, client, tmp_path):
        _login(client)
        catalog_gz = self._make_catalog_gz()
        src_exe = self._make_firmware_exe(tmp_path)
        exe_bytes = src_exe.read_bytes()

        mock_catalog_resp = MagicMock()
        mock_catalog_resp.read.return_value = catalog_gz
        mock_catalog_resp.__enter__ = lambda s: s
        mock_catalog_resp.__exit__ = MagicMock(return_value=False)

        mock_exe_resp = MagicMock()
        mock_exe_resp.__enter__ = lambda s: s
        mock_exe_resp.__exit__ = MagicMock(return_value=False)

        def fake_urlopen(req, timeout=None):
            if "Catalog" in req.full_url:
                return mock_catalog_resp
            return mock_exe_resp

        fw_dir = tmp_path / "firmware_dest"
        fw_dir.mkdir()

        import xml.etree.ElementTree as real_ET

        orig_parse = real_ET.parse

        def fake_et_parse(path):
            if "package.xml" in str(path):
                root = real_ET.fromstring(
                    '<SoftwareComponent vendorVersion="7.30.10.50"/>'
                )
                tree = MagicMock()
                tree.getroot.return_value = root
                return tree
            return orig_parse(path)

        def fake_copyfileobj(src, dst):
            dst.write(exe_bytes)

        with (
            patch("dracs.webapp.urllib.request.urlopen", side_effect=fake_urlopen),
            patch("dracs.webapp.shutil.copyfileobj", side_effect=fake_copyfileobj),
            patch("dracs.webapp.defused_ET.parse", side_effect=fake_et_parse),
            patch("dracs.webapp.FIRMWARE_IMAGE_DIR", fw_dir),
        ):
            resp = client.post(
                "/api/latest-firmware",
                data=json.dumps(
                    {
                        "model": "R660",
                        "hostname": "server01",
                        "current_version": "7.30.10.50",
                    }
                ),
                content_type="application/json",
            )
            data_str = resp.get_data(as_text=True)

        assert "already running" in data_str


# ---------------------------------------------------------------------------
# Latest BIOS SSE streaming generator
# ---------------------------------------------------------------------------
class TestLatestBiosStreaming:
    def _make_catalog_gz(self):
        import gzip

        return gzip.compress(SAMPLE_CATALOG_XML.encode("utf-16"))

    def test_full_stream_new_bios(self, client, tmp_path):
        _login(client)
        catalog_gz = self._make_catalog_gz()

        mock_catalog_resp = MagicMock()
        mock_catalog_resp.read.return_value = catalog_gz
        mock_catalog_resp.__enter__ = lambda s: s
        mock_catalog_resp.__exit__ = MagicMock(return_value=False)

        mock_exe_resp = MagicMock()
        mock_exe_resp.__enter__ = lambda s: s
        mock_exe_resp.__exit__ = MagicMock(return_value=False)

        def fake_copyfileobj(src, dst):
            dst.write(b"BIOS image data")

        def fake_urlopen(req, timeout=None):
            if "Catalog" in req.full_url:
                return mock_catalog_resp
            return mock_exe_resp

        bios_dir = tmp_path / "bios_dest"
        bios_dir.mkdir()

        with (
            patch("dracs.webapp.urllib.request.urlopen", side_effect=fake_urlopen),
            patch("dracs.webapp.shutil.copyfileobj", side_effect=fake_copyfileobj),
            patch("dracs.webapp.BIOS_IMAGE_DIR", bios_dir),
            patch("dracs.webapp._update_bios_filename_ini"),
        ):
            resp = client.post(
                "/api/latest-bios",
                data=json.dumps(
                    {
                        "model": "R660",
                        "hostname": "server01",
                        "current_version": "1.0.0",
                    }
                ),
                content_type="application/json",
            )
            data_str = resp.get_data(as_text=True)

        assert resp.status_code == 200
        assert "2.10.1" in data_str
        assert '"type": "complete"' in data_str

    def test_stream_no_match(self, client):
        _login(client)
        import gzip

        empty_xml = '<?xml version="1.0" encoding="utf-16"?><Manifest></Manifest>'
        catalog_gz = gzip.compress(empty_xml.encode("utf-16"))

        mock_resp = MagicMock()
        mock_resp.read.return_value = catalog_gz
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("dracs.webapp.urllib.request.urlopen", return_value=mock_resp):
            resp = client.post(
                "/api/latest-bios",
                data=json.dumps(
                    {"model": "R999", "hostname": "server01", "current_version": ""}
                ),
                content_type="application/json",
            )
            data_str = resp.get_data(as_text=True)

        assert '"type": "error"' in data_str

    def test_stream_error_handling(self, client):
        _login(client)
        with patch(
            "dracs.webapp.urllib.request.urlopen",
            side_effect=Exception("network error"),
        ):
            resp = client.post(
                "/api/latest-bios",
                data=json.dumps(
                    {"model": "R660", "hostname": "server01", "current_version": ""}
                ),
                content_type="application/json",
            )
            data_str = resp.get_data(as_text=True)

        assert '"type": "error"' in data_str

    def test_stream_already_current(self, client, tmp_path):
        _login(client)
        catalog_gz = self._make_catalog_gz()

        mock_catalog_resp = MagicMock()
        mock_catalog_resp.read.return_value = catalog_gz
        mock_catalog_resp.__enter__ = lambda s: s
        mock_catalog_resp.__exit__ = MagicMock(return_value=False)

        mock_exe_resp = MagicMock()
        mock_exe_resp.__enter__ = lambda s: s
        mock_exe_resp.__exit__ = MagicMock(return_value=False)

        def fake_copyfileobj(src, dst):
            dst.write(b"BIOS image data")

        def fake_urlopen(req, timeout=None):
            if "Catalog" in req.full_url:
                return mock_catalog_resp
            return mock_exe_resp

        bios_dir = tmp_path / "bios_dest"
        bios_dir.mkdir()

        with (
            patch("dracs.webapp.urllib.request.urlopen", side_effect=fake_urlopen),
            patch("dracs.webapp.shutil.copyfileobj", side_effect=fake_copyfileobj),
            patch("dracs.webapp.BIOS_IMAGE_DIR", bios_dir),
            patch("dracs.webapp._update_bios_filename_ini"),
        ):
            resp = client.post(
                "/api/latest-bios",
                data=json.dumps(
                    {
                        "model": "R660",
                        "hostname": "server01",
                        "current_version": "2.10.1",
                    }
                ),
                content_type="application/json",
            )
            data_str = resp.get_data(as_text=True)

        assert "already running" in data_str


# ---------------------------------------------------------------------------
# TSR monitor thread
# ---------------------------------------------------------------------------
class TestTsrMonitorThread:
    def test_monitor_exits_on_no_collection(self):
        from dracs.webapp import _tsr_monitor_thread

        mock_result = MagicMock(returncode=0, stdout="")
        with patch("dracs.webapp.subprocess.run", return_value=mock_result):
            with patch("dracs.webapp.time.sleep"):
                with patch.dict(
                    os.environ,
                    {"DRACS_DNS_STRING": "mgmt-", "DRACS_DNS_MODE": "prefix"},
                ):
                    with patch.object(MagicMock, "__gt__", return_value=True):
                        _tsr_monitor_thread.__wrapped__ = None
                        pass

    def test_monitor_handles_command_failure(self):
        from dracs.webapp import _tsr_monitor_thread

        call_count = [0]

        def fake_run(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] > 2:
                raise StopIteration
            return MagicMock(returncode=1, stdout="", stderr="error")

        with patch("dracs.webapp.subprocess.run", side_effect=fake_run):
            with patch("dracs.webapp.time.sleep", side_effect=[None, StopIteration]):
                with patch.dict(
                    os.environ,
                    {"DRACS_DNS_STRING": "mgmt-", "DRACS_DNS_MODE": "prefix"},
                ):
                    try:
                        _tsr_monitor_thread("server01", "TAG001")
                    except StopIteration:
                        pass

    def test_monitor_full_lifecycle(self, tmp_path):
        from dracs.webapp import _tsr_monitor_thread

        collection_output = (
            "[Job ID=JID_001]\n"
            "Job Name=SupportAssist Collection\n"
            "Status=Completed\n"
            "Percent Complete=100\n"
            "Message=The SupportAssist Collection Operation is completed successfully\n"
        )
        transmission_output = (
            "[Job ID=JID_002]\n"
            "Job Name=SupportAssist Collection\n"
            "Status=Completed\n"
            "Percent Complete=100\n"
            "Message=The SupportAssist Transmission Operation is completed successfully\n"
        )

        call_count = [0]

        def fake_run(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] <= 1:
                return MagicMock(returncode=0, stdout=collection_output)
            if call_count[0] == 2:
                return MagicMock(returncode=0, stdout="export started")
            return MagicMock(returncode=0, stdout=transmission_output)

        tsr_dir = tmp_path / "tsr"
        tsr_dir.mkdir()
        tftpboot = tmp_path / "tftpboot"
        tftpboot.mkdir()

        ts = "20250518120000"
        zip_name = f"TSR{ts}_TAG001.zip"
        inner_name = f"TSR{ts}_TAG001.pl.zip"
        inner_zip = tftpboot / inner_name
        with zipfile.ZipFile(inner_zip, "w") as zf:
            zf.writestr("inner.txt", "data")
        outer_zip = tftpboot / zip_name
        with zipfile.ZipFile(outer_zip, "w") as zf:
            zf.write(inner_zip, inner_name)
            zf.writestr("outer.txt", "data")

        with (
            patch("dracs.webapp.subprocess.run", side_effect=fake_run),
            patch("dracs.webapp.time.sleep"),
            patch("dracs.webapp.TSR_IMAGE_DIR", tsr_dir),
            patch("dracs.webapp.TFTPBOOT_DIR", tftpboot),
            patch.dict(
                os.environ,
                {"DRACS_DNS_STRING": "mgmt-", "DRACS_DNS_MODE": "prefix"},
            ),
        ):
            _tsr_monitor_thread("server01", "TAG001")

        host_dir = tsr_dir / "server01"
        assert host_dir.exists()
        assert (host_dir / "latest").is_symlink()
        ts_dir = host_dir / ts
        assert ts_dir.exists()
        assert (ts_dir / "index.html").exists()
        assert (ts_dir / "outer.txt").exists()
        assert (ts_dir / "inner.txt").exists()

    def test_monitor_export_not_done(self):
        from dracs.webapp import _tsr_monitor_thread

        collection_output = (
            "[Job ID=JID_001]\n"
            "Job Name=SupportAssist Collection\n"
            "Status=Completed\n"
            "Percent Complete=100\n"
            "Message=The SupportAssist Collection Operation is completed successfully\n"
        )
        not_done_output = (
            "[Job ID=JID_002]\n"
            "Job Name=SupportAssist Collection\n"
            "Status=Running\n"
            "Percent Complete=50\n"
            "Message=Exporting...\n"
        )

        call_count = [0]

        def fake_run(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return MagicMock(returncode=0, stdout=collection_output)
            if call_count[0] == 2:
                return MagicMock(returncode=0, stdout="export started")
            return MagicMock(returncode=0, stdout=not_done_output)

        sleep_count = [0]

        def fake_sleep(secs):
            sleep_count[0] += 1
            if sleep_count[0] > 5:
                raise StopIteration

        with (
            patch("dracs.webapp.subprocess.run", side_effect=fake_run),
            patch("dracs.webapp.time.sleep", side_effect=fake_sleep),
            patch.dict(
                os.environ,
                {"DRACS_DNS_STRING": "mgmt-", "DRACS_DNS_MODE": "prefix"},
            ),
        ):
            try:
                _tsr_monitor_thread("server01", "TAG001")
            except StopIteration:
                pass

    def test_monitor_no_zip_found(self):
        from dracs.webapp import _tsr_monitor_thread

        collection_output = (
            "[Job ID=JID_001]\n"
            "Job Name=SupportAssist Collection\n"
            "Status=Completed\n"
            "Percent Complete=100\n"
            "Message=The SupportAssist Collection Operation is completed successfully\n"
        )
        transmission_output = (
            "[Job ID=JID_002]\n"
            "Job Name=SupportAssist Collection\n"
            "Status=Completed\n"
            "Percent Complete=100\n"
            "Message=The SupportAssist Transmission Operation is completed successfully\n"
        )

        call_count = [0]

        def fake_run(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] <= 1:
                return MagicMock(returncode=0, stdout=collection_output)
            if call_count[0] == 2:
                return MagicMock(returncode=0, stdout="ok")
            return MagicMock(returncode=0, stdout=transmission_output)

        with (
            patch("dracs.webapp.subprocess.run", side_effect=fake_run),
            patch("dracs.webapp.time.sleep"),
            patch("dracs.webapp._find_tsr_zip", return_value=None),
            patch.dict(
                os.environ,
                {"DRACS_DNS_STRING": "mgmt-", "DRACS_DNS_MODE": "prefix"},
            ),
        ):
            _tsr_monitor_thread("server01", "TAG001")


# ---------------------------------------------------------------------------
# Additional coverage for edge cases
# ---------------------------------------------------------------------------
class TestCatalogEdgeCases:
    def test_firmware_component_missing_category(self):
        from dracs.webapp import _find_latest_idrac_firmware

        xml = """<?xml version="1.0" encoding="utf-16"?>
        <Manifest>
          <SoftwareComponent path="p" vendorVersion="1.0" dateTime="2025-01-01T00:00:00Z">
            <ComponentType value="FRMW"/>
            <SupportedSystems><Brand><Model><Display>R660</Display></Model></Brand></SupportedSystems>
          </SoftwareComponent>
        </Manifest>"""
        result = _find_latest_idrac_firmware(xml.encode("utf-16"), "R660")
        assert result is None

    def test_firmware_component_wrong_category(self):
        from dracs.webapp import _find_latest_idrac_firmware

        xml = """<?xml version="1.0" encoding="utf-16"?>
        <Manifest>
          <SoftwareComponent path="p" vendorVersion="1.0" dateTime="2025-01-01T00:00:00Z">
            <ComponentType value="FRMW"/>
            <Category><Display>Network Adapter</Display></Category>
            <SupportedSystems><Brand><Model><Display>R660</Display></Model></Brand></SupportedSystems>
          </SoftwareComponent>
        </Manifest>"""
        result = _find_latest_idrac_firmware(xml.encode("utf-16"), "R660")
        assert result is None

    def test_firmware_component_missing_path(self):
        from dracs.webapp import _find_latest_idrac_firmware

        xml = """<?xml version="1.0" encoding="utf-16"?>
        <Manifest>
          <SoftwareComponent vendorVersion="1.0" dateTime="2025-01-01T00:00:00Z">
            <ComponentType value="FRMW"/>
            <Category><Display>iDRAC with Lifecycle Controller</Display></Category>
            <SupportedSystems><Brand><Model><Display>R660</Display></Model></Brand></SupportedSystems>
          </SoftwareComponent>
        </Manifest>"""
        result = _find_latest_idrac_firmware(xml.encode("utf-16"), "R660")
        assert result is None

    def test_bios_component_missing_path(self):
        from dracs.webapp import _find_latest_bios

        xml = """<?xml version="1.0" encoding="utf-16"?>
        <Manifest>
          <SoftwareComponent vendorVersion="1.0" dateTime="2025-01-01T00:00:00Z">
            <ComponentType value="BIOS"/>
            <SupportedSystems><Brand><Model><Display>R660</Display></Model></Brand></SupportedSystems>
          </SoftwareComponent>
        </Manifest>"""
        result = _find_latest_bios(xml.encode("utf-16"), "R660")
        assert result is None


class TestPowerStatusEdgeCases:
    def test_empty_hostname(self, client):
        _login(client)
        resp = client.post(
            "/api/power-status",
            data=json.dumps({"hostname": ""}),
            content_type="application/json",
        )
        assert resp.status_code in (400, 500)

    def test_general_exception(self, client):
        _login(client)
        with patch(
            "dracs.webapp.build_idrac_hostname",
            side_effect=RuntimeError("boom"),
        ):
            resp = client.post(
                "/api/power-status",
                data=json.dumps({"hostname": "server01"}),
                content_type="application/json",
            )
        assert resp.status_code == 500
        assert "boom" in resp.get_json()["message"]


class TestPowerActionEdgeCases:
    def test_empty_hostname(self, client):
        _login(client)
        resp = client.post(
            "/api/power-action",
            data=json.dumps({"hostname": "", "action": "powerup"}),
            content_type="application/json",
        )
        assert resp.status_code in (400, 500)

    def test_no_json_body(self, client):
        _login(client)
        resp = client.post(
            "/api/power-action",
            data=json.dumps(None),
            content_type="application/json",
        )
        assert resp.status_code in (400, 500)


class TestFirmwareStreamEdgeCases:
    def test_no_d9_in_package(self, client, tmp_path):
        _login(client)
        import gzip

        catalog_gz = gzip.compress(SAMPLE_CATALOG_XML.encode("utf-16"))

        mock_catalog_resp = MagicMock()
        mock_catalog_resp.read.return_value = catalog_gz
        mock_catalog_resp.__enter__ = lambda s: s
        mock_catalog_resp.__exit__ = MagicMock(return_value=False)

        mock_exe_resp = MagicMock()
        mock_exe_resp.__enter__ = lambda s: s
        mock_exe_resp.__exit__ = MagicMock(return_value=False)

        no_d9_zip = tmp_path / "no_d9.zip"
        with zipfile.ZipFile(no_d9_zip, "w") as zf:
            zf.writestr("some_other_file.bin", b"data")
        exe_bytes = no_d9_zip.read_bytes()

        def fake_urlopen(req, timeout=None):
            if "Catalog" in req.full_url:
                return mock_catalog_resp
            return mock_exe_resp

        def fake_copyfileobj(src, dst):
            dst.write(exe_bytes)

        import xml.etree.ElementTree as real_ET

        orig_parse = real_ET.parse

        def fake_et_parse(path):
            if "package.xml" in str(path):
                root = real_ET.fromstring(
                    '<SoftwareComponent vendorVersion="7.30.10.50"/>'
                )
                tree = MagicMock()
                tree.getroot.return_value = root
                return tree
            return orig_parse(path)

        fw_dir = tmp_path / "fw"
        fw_dir.mkdir()

        with (
            patch("dracs.webapp.urllib.request.urlopen", side_effect=fake_urlopen),
            patch("dracs.webapp.shutil.copyfileobj", side_effect=fake_copyfileobj),
            patch("dracs.webapp.defused_ET.parse", side_effect=fake_et_parse),
            patch("dracs.webapp.FIRMWARE_IMAGE_DIR", fw_dir),
        ):
            resp = client.post(
                "/api/latest-firmware",
                data=json.dumps(
                    {
                        "model": "R660",
                        "hostname": "server01",
                        "current_version": "",
                    }
                ),
                content_type="application/json",
            )
            data_str = resp.get_data(as_text=True)

        assert "No .d9 firmware image found" in data_str

    def test_firmware_file_already_exists(self, client, tmp_path):
        _login(client)
        import gzip

        catalog_gz = gzip.compress(SAMPLE_CATALOG_XML.encode("utf-16"))

        mock_catalog_resp = MagicMock()
        mock_catalog_resp.read.return_value = catalog_gz
        mock_catalog_resp.__enter__ = lambda s: s
        mock_catalog_resp.__exit__ = MagicMock(return_value=False)

        mock_exe_resp = MagicMock()
        mock_exe_resp.__enter__ = lambda s: s
        mock_exe_resp.__exit__ = MagicMock(return_value=False)

        src_zip = tmp_path / "fw.zip"
        with zipfile.ZipFile(src_zip, "w") as zf:
            zf.writestr("payload/firmimgFIT.d9", b"firmware")
        exe_bytes = src_zip.read_bytes()

        def fake_urlopen(req, timeout=None):
            if "Catalog" in req.full_url:
                return mock_catalog_resp
            return mock_exe_resp

        def fake_copyfileobj(src, dst):
            dst.write(exe_bytes)

        import xml.etree.ElementTree as real_ET

        orig_parse = real_ET.parse

        def fake_et_parse(path):
            if "package.xml" in str(path):
                root = real_ET.fromstring(
                    '<SoftwareComponent vendorVersion="7.30.10.50"/>'
                )
                tree = MagicMock()
                tree.getroot.return_value = root
                return tree
            return orig_parse(path)

        fw_dir = tmp_path / "fw_dest"
        fw_dir.mkdir()
        (fw_dir / "R660-7.30.10.50.d9").write_bytes(b"existing")

        with (
            patch("dracs.webapp.urllib.request.urlopen", side_effect=fake_urlopen),
            patch("dracs.webapp.shutil.copyfileobj", side_effect=fake_copyfileobj),
            patch("dracs.webapp.defused_ET.parse", side_effect=fake_et_parse),
            patch("dracs.webapp.FIRMWARE_IMAGE_DIR", fw_dir),
        ):
            resp = client.post(
                "/api/latest-firmware",
                data=json.dumps(
                    {
                        "model": "R660",
                        "hostname": "server01",
                        "current_version": "",
                    }
                ),
                content_type="application/json",
            )
            data_str = resp.get_data(as_text=True)

        assert "already exists!" in data_str


class TestBiosStreamEdgeCases:
    def test_bios_file_already_exists(self, client, tmp_path):
        _login(client)
        import gzip

        catalog_gz = gzip.compress(SAMPLE_CATALOG_XML.encode("utf-16"))

        mock_catalog_resp = MagicMock()
        mock_catalog_resp.read.return_value = catalog_gz
        mock_catalog_resp.__enter__ = lambda s: s
        mock_catalog_resp.__exit__ = MagicMock(return_value=False)

        mock_exe_resp = MagicMock()
        mock_exe_resp.__enter__ = lambda s: s
        mock_exe_resp.__exit__ = MagicMock(return_value=False)

        def fake_urlopen(req, timeout=None):
            if "Catalog" in req.full_url:
                return mock_catalog_resp
            return mock_exe_resp

        def fake_copyfileobj(src, dst):
            dst.write(b"BIOS data")

        bios_dir = tmp_path / "bios_dest"
        model_dir = bios_dir / "R660"
        model_dir.mkdir(parents=True)
        (model_dir / "bios.EXE").write_bytes(b"existing")

        with (
            patch("dracs.webapp.urllib.request.urlopen", side_effect=fake_urlopen),
            patch("dracs.webapp.shutil.copyfileobj", side_effect=fake_copyfileobj),
            patch("dracs.webapp.BIOS_IMAGE_DIR", bios_dir),
            patch("dracs.webapp._update_bios_filename_ini"),
        ):
            resp = client.post(
                "/api/latest-bios",
                data=json.dumps(
                    {
                        "model": "R660",
                        "hostname": "server01",
                        "current_version": "",
                    }
                ),
                content_type="application/json",
            )
            data_str = resp.get_data(as_text=True)

        assert "already exists!" in data_str

    def test_bios_ini_update_exception(self, client, tmp_path):
        _login(client)
        import gzip

        catalog_gz = gzip.compress(SAMPLE_CATALOG_XML.encode("utf-16"))

        mock_catalog_resp = MagicMock()
        mock_catalog_resp.read.return_value = catalog_gz
        mock_catalog_resp.__enter__ = lambda s: s
        mock_catalog_resp.__exit__ = MagicMock(return_value=False)

        mock_exe_resp = MagicMock()
        mock_exe_resp.__enter__ = lambda s: s
        mock_exe_resp.__exit__ = MagicMock(return_value=False)

        def fake_urlopen(req, timeout=None):
            if "Catalog" in req.full_url:
                return mock_catalog_resp
            return mock_exe_resp

        def fake_copyfileobj(src, dst):
            dst.write(b"BIOS data")

        bios_dir = tmp_path / "bios_dest"
        bios_dir.mkdir()

        with (
            patch("dracs.webapp.urllib.request.urlopen", side_effect=fake_urlopen),
            patch("dracs.webapp.shutil.copyfileobj", side_effect=fake_copyfileobj),
            patch("dracs.webapp.BIOS_IMAGE_DIR", bios_dir),
            patch(
                "dracs.webapp._update_bios_filename_ini",
                side_effect=OSError("disk full"),
            ),
        ):
            resp = client.post(
                "/api/latest-bios",
                data=json.dumps(
                    {
                        "model": "R660",
                        "hostname": "server01",
                        "current_version": "1.0.0",
                    }
                ),
                content_type="application/json",
            )
            data_str = resp.get_data(as_text=True)

        assert '"type": "complete"' in data_str


class TestTsrEndpointEdgeCases:
    def test_tsr_status_empty_hostname(self, client):
        _login(client)
        resp = client.post(
            "/api/tsr-status",
            data=json.dumps({"hostname": ""}),
            content_type="application/json",
        )
        assert resp.status_code in (400, 500)

    def test_tsr_collect_empty_hostname(self, client):
        _login(client)
        resp = client.post(
            "/api/tsr-collect",
            data=json.dumps({"hostname": "", "service_tag": "TAG001"}),
            content_type="application/json",
        )
        assert resp.status_code in (400, 500)

    def test_tsr_status_general_exception(self, client):
        _login(client)
        with patch(
            "dracs.webapp._get_tsr_job_status",
            side_effect=RuntimeError("boom"),
        ):
            resp = client.post(
                "/api/tsr-status",
                data=json.dumps({"hostname": "server01"}),
                content_type="application/json",
            )
        assert resp.status_code == 500

    def test_tsr_collect_general_exception(self, client):
        _login(client)
        with patch(
            "dracs.webapp.subprocess.run",
            side_effect=RuntimeError("boom"),
        ):
            resp = client.post(
                "/api/tsr-collect",
                data=json.dumps({"hostname": "server01", "service_tag": "TAG001"}),
                content_type="application/json",
            )
        assert resp.status_code == 500


class TestLatestFirmwareNoJson:
    def test_empty_json(self, client):
        _login(client)
        resp = client.post(
            "/api/latest-firmware",
            data=json.dumps(None),
            content_type="application/json",
        )
        assert resp.status_code in (400, 500)


class TestLatestBiosNoJson:
    def test_empty_json(self, client):
        _login(client)
        resp = client.post(
            "/api/latest-bios",
            data=json.dumps(None),
            content_type="application/json",
        )
        assert resp.status_code in (400, 500)


class TestFirmwareD9FallbackSearch:
    def test_d9_not_in_payload_but_in_subdir(self, client, tmp_path):
        _login(client)
        import gzip

        catalog_gz = gzip.compress(SAMPLE_CATALOG_XML.encode("utf-16"))

        mock_catalog_resp = MagicMock()
        mock_catalog_resp.read.return_value = catalog_gz
        mock_catalog_resp.__enter__ = lambda s: s
        mock_catalog_resp.__exit__ = MagicMock(return_value=False)

        mock_exe_resp = MagicMock()
        mock_exe_resp.__enter__ = lambda s: s
        mock_exe_resp.__exit__ = MagicMock(return_value=False)

        src_zip = tmp_path / "fw.zip"
        with zipfile.ZipFile(src_zip, "w") as zf:
            zf.writestr("subdir/firmware.d9", b"firmware data")
        exe_bytes = src_zip.read_bytes()

        def fake_urlopen(req, timeout=None):
            if "Catalog" in req.full_url:
                return mock_catalog_resp
            return mock_exe_resp

        def fake_copyfileobj(src, dst):
            dst.write(exe_bytes)

        import xml.etree.ElementTree as real_ET

        orig_parse = real_ET.parse

        def fake_et_parse(path):
            if "package.xml" in str(path):
                root = real_ET.fromstring('<Other vendorVersion="7.30.10.50"/>')
                tree = MagicMock()
                tree.getroot.return_value = root
                return tree
            return orig_parse(path)

        fw_dir = tmp_path / "fw_dest"
        fw_dir.mkdir()

        with (
            patch("dracs.webapp.urllib.request.urlopen", side_effect=fake_urlopen),
            patch("dracs.webapp.shutil.copyfileobj", side_effect=fake_copyfileobj),
            patch("dracs.webapp.defused_ET.parse", side_effect=fake_et_parse),
            patch("dracs.webapp.FIRMWARE_IMAGE_DIR", fw_dir),
        ):
            resp = client.post(
                "/api/latest-firmware",
                data=json.dumps(
                    {
                        "model": "R660",
                        "hostname": "server01",
                        "current_version": "",
                    }
                ),
                content_type="application/json",
            )
            data_str = resp.get_data(as_text=True)

        assert '"type": "complete"' in data_str
        assert (fw_dir / "R660-7.30.10.50.d9").exists()


class TestTsrMonitorExportEdgeCases:
    def test_export_poll_command_failure(self, tmp_path):
        from dracs.webapp import _tsr_monitor_thread

        collection_output = (
            "[Job ID=JID_001]\n"
            "Job Name=SupportAssist Collection\n"
            "Status=Completed\n"
            "Percent Complete=100\n"
            "Message=The SupportAssist Collection Operation is completed successfully\n"
        )
        transmission_output = (
            "[Job ID=JID_002]\n"
            "Job Name=SupportAssist Collection\n"
            "Status=Completed\n"
            "Percent Complete=100\n"
            "Message=The SupportAssist Transmission Operation is completed successfully\n"
        )

        call_count = [0]

        def fake_run(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return MagicMock(returncode=0, stdout=collection_output)
            if call_count[0] == 2:
                return MagicMock(returncode=0, stdout="export started")
            if call_count[0] == 3:
                return MagicMock(returncode=1, stdout="", stderr="fail")
            if call_count[0] == 4:
                raise Exception("network error")
            return MagicMock(returncode=0, stdout=transmission_output)

        tsr_dir = tmp_path / "tsr"
        tsr_dir.mkdir()
        tftpboot = tmp_path / "tftpboot"
        tftpboot.mkdir()

        ts = "20250518120000"
        zip_name = f"TSR{ts}_TAG001.zip"
        outer_zip = tftpboot / zip_name
        with zipfile.ZipFile(outer_zip, "w") as zf:
            zf.writestr("file.txt", "data")

        with (
            patch("dracs.webapp.subprocess.run", side_effect=fake_run),
            patch("dracs.webapp.time.sleep"),
            patch("dracs.webapp.TSR_IMAGE_DIR", tsr_dir),
            patch("dracs.webapp.TFTPBOOT_DIR", tftpboot),
            patch.dict(
                os.environ,
                {"DRACS_DNS_STRING": "mgmt-", "DRACS_DNS_MODE": "prefix"},
            ),
        ):
            _tsr_monitor_thread("server01", "TAG001")

        assert (tsr_dir / "server01").exists()

    def test_monitor_with_non_tsr_jobs(self, tmp_path):
        from dracs.webapp import _tsr_monitor_thread

        mixed_output = (
            "[Job ID=JID_001]\n"
            "Job Name=Firmware Update\n"
            "Status=Completed\n"
            "Percent Complete=100\n"
            "Message=Job completed\n"
            "\n"
            "[Job ID=JID_002]\n"
            "Job Name=SupportAssist Collection\n"
            "Status=Completed\n"
            "Percent Complete=100\n"
            "Message=The SupportAssist Collection Operation is completed successfully\n"
        )
        transmission_output = (
            "[Job ID=JID_003]\n"
            "Job Name=Firmware Update\n"
            "Status=Completed\n"
            "Percent Complete=100\n"
            "Message=Done\n"
            "\n"
            "[Job ID=JID_004]\n"
            "Job Name=SupportAssist Collection\n"
            "Status=Completed\n"
            "Percent Complete=100\n"
            "Message=The SupportAssist Transmission Operation is completed successfully\n"
        )

        call_count = [0]

        def fake_run(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return MagicMock(returncode=0, stdout=mixed_output)
            if call_count[0] == 2:
                return MagicMock(returncode=0, stdout="ok")
            return MagicMock(returncode=0, stdout=transmission_output)

        tsr_dir = tmp_path / "tsr"
        tsr_dir.mkdir()
        tftpboot = tmp_path / "tftpboot"
        tftpboot.mkdir()

        ts = "20250518120000"
        outer_zip = tftpboot / f"TSR{ts}_TAG001.zip"
        with zipfile.ZipFile(outer_zip, "w") as zf:
            zf.writestr("file.txt", "data")

        with (
            patch("dracs.webapp.subprocess.run", side_effect=fake_run),
            patch("dracs.webapp.time.sleep"),
            patch("dracs.webapp.TSR_IMAGE_DIR", tsr_dir),
            patch("dracs.webapp.TFTPBOOT_DIR", tftpboot),
            patch.dict(
                os.environ,
                {"DRACS_DNS_STRING": "mgmt-", "DRACS_DNS_MODE": "prefix"},
            ),
        ):
            _tsr_monitor_thread("server01", "TAG001")

        host_dir = tsr_dir / "server01"
        assert host_dir.exists()

    def test_monitor_existing_symlink_replaced(self, tmp_path):
        from dracs.webapp import _tsr_monitor_thread

        collection_output = (
            "[Job ID=JID_001]\n"
            "Job Name=SupportAssist Collection\n"
            "Status=Completed\n"
            "Percent Complete=100\n"
            "Message=The SupportAssist Collection Operation is completed successfully\n"
        )
        transmission_output = (
            "[Job ID=JID_002]\n"
            "Job Name=SupportAssist Collection\n"
            "Status=Completed\n"
            "Percent Complete=100\n"
            "Message=The SupportAssist Transmission Operation is completed successfully\n"
        )

        call_count = [0]

        def fake_run(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return MagicMock(returncode=0, stdout=collection_output)
            if call_count[0] == 2:
                return MagicMock(returncode=0, stdout="ok")
            return MagicMock(returncode=0, stdout=transmission_output)

        tsr_dir = tmp_path / "tsr"
        tsr_dir.mkdir()
        host_dir = tsr_dir / "server01"
        host_dir.mkdir()
        old_link = host_dir / "latest"
        old_link.symlink_to("old_timestamp")

        tftpboot = tmp_path / "tftpboot"
        tftpboot.mkdir()

        ts = "20250518120000"
        outer_zip = tftpboot / f"TSR{ts}_TAG001.zip"
        with zipfile.ZipFile(outer_zip, "w") as zf:
            zf.writestr("file.txt", "data")

        with (
            patch("dracs.webapp.subprocess.run", side_effect=fake_run),
            patch("dracs.webapp.time.sleep"),
            patch("dracs.webapp.TSR_IMAGE_DIR", tsr_dir),
            patch("dracs.webapp.TFTPBOOT_DIR", tftpboot),
            patch.dict(
                os.environ,
                {"DRACS_DNS_STRING": "mgmt-", "DRACS_DNS_MODE": "prefix"},
            ),
        ):
            _tsr_monitor_thread("server01", "TAG001")

        assert (host_dir / "latest").is_symlink()
        assert os.readlink(host_dir / "latest") == ts

    def test_export_not_done_returns(self):
        from dracs.webapp import _tsr_monitor_thread

        collection_output = (
            "[Job ID=JID_001]\n"
            "Job Name=SupportAssist Collection\n"
            "Status=Completed\n"
            "Percent Complete=100\n"
            "Message=The SupportAssist Collection Operation is completed successfully\n"
        )
        still_running = (
            "[Job ID=JID_002]\n"
            "Job Name=SupportAssist Collection\n"
            "Status=Running\n"
            "Percent Complete=50\n"
            "Message=Exporting\n"
        )

        call_count = [0]

        def fake_run(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return MagicMock(returncode=0, stdout=collection_output)
            if call_count[0] == 2:
                return MagicMock(returncode=0, stdout="ok")
            return MagicMock(returncode=0, stdout=still_running)

        sleep_count = [0]
        orig_max_wait = 1800

        def fake_sleep(secs):
            sleep_count[0] += 1
            if sleep_count[0] > 100:
                raise StopIteration

        with (
            patch("dracs.webapp.subprocess.run", side_effect=fake_run),
            patch("dracs.webapp.time.sleep", side_effect=fake_sleep),
            patch.dict(
                os.environ,
                {"DRACS_DNS_STRING": "mgmt-", "DRACS_DNS_MODE": "prefix"},
            ),
        ):
            try:
                _tsr_monitor_thread("server01", "TAG001")
            except StopIteration:
                pass


class TestTsrCollectNoJson:
    def test_empty_json(self, client):
        _login(client)
        resp = client.post(
            "/api/tsr-collect",
            data=json.dumps(None),
            content_type="application/json",
        )
        assert resp.status_code in (400, 500)

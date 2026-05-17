import json
import os
import tempfile
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
    upsert_system(
        path, "TAG002", "server02", "R650", "6.0.0", "1.5.0", "Jan 1, 2020", 1577836800
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


class TestIndexRoute:
    def test_index_returns_200(self, client):
        resp = client.get("/")
        assert resp.status_code == 200

    def test_index_contains_systems(self, client):
        resp = client.get("/")
        assert b"TAG001" in resp.data or b"systems_json" in resp.data


class TestApiSystems:
    def test_api_systems_returns_json(self, client):
        resp = client.get("/api/systems")
        assert resp.status_code == 200
        data = resp.get_json()
        assert isinstance(data, list)
        assert len(data) == 2

    def test_api_systems_structure(self, client):
        resp = client.get("/api/systems")
        data = resp.get_json()
        system = data[0]
        assert "svc_tag" in system
        assert "name" in system
        assert "model" in system


class TestAuth:
    def test_login_success(self, client):
        resp = client.post(
            "/login",
            data=json.dumps({"username": "admin", "password": "admin"}),
            content_type="application/json",
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True

    def test_login_bad_credentials(self, client):
        resp = client.post(
            "/login",
            data=json.dumps({"username": "admin", "password": "wrong"}),
            content_type="application/json",
        )
        assert resp.status_code == 401

    def test_login_no_json(self, client):
        resp = client.post("/login", data="not json", content_type="text/plain")
        assert resp.status_code == 400

    def test_logout(self, client):
        client.post(
            "/login",
            data=json.dumps({"username": "admin", "password": "admin"}),
            content_type="application/json",
        )
        resp = client.post("/logout")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True

    def test_auth_status_not_authenticated(self, client):
        resp = client.get("/api/auth-status")
        data = resp.get_json()
        assert data["authenticated"] is False

    def test_auth_status_authenticated(self, client):
        client.post(
            "/login",
            data=json.dumps({"username": "admin", "password": "admin"}),
            content_type="application/json",
        )
        resp = client.get("/api/auth-status")
        data = resp.get_json()
        assert data["authenticated"] is True


class TestProtectedEndpoints:
    def test_refresh_requires_auth(self, client):
        resp = client.post(
            "/api/refresh",
            data=json.dumps({"service_tag": "TAG001"}),
            content_type="application/json",
        )
        assert resp.status_code == 401

    def test_refresh_multiple_requires_auth(self, client):
        resp = client.post(
            "/api/refresh-multiple",
            data=json.dumps({"systems": [{"service_tag": "TAG001"}]}),
            content_type="application/json",
        )
        assert resp.status_code == 401

    def test_test_idrac_requires_auth(self, client):
        resp = client.post(
            "/api/test-idrac",
            data=json.dumps({"hostname": "server01"}),
            content_type="application/json",
        )
        assert resp.status_code == 401

    def test_firmware_update_requires_auth(self, client):
        resp = client.post(
            "/api/firmware-update",
            data=json.dumps(
                {"hostname": "server01", "target_version": "8.0.0", "model": "R660"}
            ),
            content_type="application/json",
        )
        assert resp.status_code == 401

    def test_bios_update_requires_auth(self, client):
        resp = client.post(
            "/api/bios-update",
            data=json.dumps(
                {"hostname": "server01", "target_bios": "3.0.0", "model": "R660"}
            ),
            content_type="application/json",
        )
        assert resp.status_code == 401

    def test_job_queue_requires_auth(self, client):
        resp = client.post(
            "/api/job-queue",
            data=json.dumps({"hostname": "server01"}),
            content_type="application/json",
        )
        assert resp.status_code == 401

    def test_clear_job_queue_requires_auth(self, client):
        resp = client.post(
            "/api/clear-job-queue",
            data=json.dumps({"hostnames": ["server01"]}),
            content_type="application/json",
        )
        assert resp.status_code == 401

    def test_refresh_all_requires_auth(self, client):
        resp = client.post("/api/refresh-all")
        assert resp.status_code == 401

    def test_firmware_versions_requires_auth(self, client):
        resp = client.get("/api/firmware-versions/R660")
        assert resp.status_code == 401

    def test_bios_versions_requires_auth(self, client):
        resp = client.get("/api/bios-versions/R660")
        assert resp.status_code == 401


def _login(client):
    client.post(
        "/login",
        data=json.dumps({"username": "admin", "password": "admin"}),
        content_type="application/json",
    )


class TestRefreshEndpoint:
    def test_refresh_no_json(self, client):
        _login(client)
        resp = client.post("/api/refresh", data="not json", content_type="text/plain")
        assert resp.status_code in (400, 500)

    def test_refresh_missing_fields(self, client):
        _login(client)
        resp = client.post(
            "/api/refresh",
            data=json.dumps({}),
            content_type="application/json",
        )
        assert resp.status_code == 400

    @patch("dracs.webapp.refresh_dell_warranty")
    def test_refresh_success(self, mock_refresh, client):
        _login(client)
        mock_refresh.return_value = None
        resp = client.post(
            "/api/refresh",
            data=json.dumps({"service_tag": "TAG001"}),
            content_type="application/json",
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True

    @patch("dracs.webapp.refresh_dell_warranty", side_effect=Exception("fail"))
    def test_refresh_error(self, mock_refresh, client):
        _login(client)
        resp = client.post(
            "/api/refresh",
            data=json.dumps({"hostname": "server01"}),
            content_type="application/json",
        )
        assert resp.status_code == 500


class TestRefreshMultipleEndpoint:
    def test_refresh_multiple_no_json(self, client):
        _login(client)
        resp = client.post(
            "/api/refresh-multiple", data="bad", content_type="text/plain"
        )
        assert resp.status_code in (400, 500)

    def test_refresh_multiple_no_systems(self, client):
        _login(client)
        resp = client.post(
            "/api/refresh-multiple",
            data=json.dumps({"systems": []}),
            content_type="application/json",
        )
        assert resp.status_code == 400

    @patch("dracs.webapp.refresh_dell_warranty")
    def test_refresh_multiple_success(self, mock_refresh, client):
        _login(client)
        mock_refresh.return_value = None
        resp = client.post(
            "/api/refresh-multiple",
            data=json.dumps(
                {"systems": [{"service_tag": "TAG001"}, {"service_tag": "TAG002"}]}
            ),
            content_type="application/json",
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["refreshed"] == 2

    @patch("dracs.webapp.refresh_dell_warranty", side_effect=Exception("fail"))
    def test_refresh_multiple_with_failures(self, mock_refresh, client):
        _login(client)
        resp = client.post(
            "/api/refresh-multiple",
            data=json.dumps(
                {
                    "systems": [
                        {"service_tag": "TAG001"},
                        {"service_tag": "TAG002"},
                        {"service_tag": "TAG003"},
                        {"service_tag": "TAG004"},
                    ]
                }
            ),
            content_type="application/json",
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["refreshed"] == 0
        assert "and" in data["message"]


class TestTestIdracEndpoint:
    def test_test_idrac_no_json(self, client):
        _login(client)
        resp = client.post("/api/test-idrac", data="bad", content_type="text/plain")
        assert resp.status_code in (400, 500)

    def test_test_idrac_no_hostname(self, client):
        _login(client)
        resp = client.post(
            "/api/test-idrac",
            data=json.dumps({"hostname": ""}),
            content_type="application/json",
        )
        assert resp.status_code == 400

    @patch("dracs.webapp.test_idrac_connectivity", return_value=(True, "ok"))
    def test_test_idrac_success(self, mock_test, client):
        _login(client)
        resp = client.post(
            "/api/test-idrac",
            data=json.dumps({"hostname": "server01"}),
            content_type="application/json",
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True


class TestFirmwareUpdateEndpoint:
    def test_firmware_update_no_json(self, client):
        _login(client)
        resp = client.post(
            "/api/firmware-update", data="bad", content_type="text/plain"
        )
        assert resp.status_code in (400, 500)

    def test_firmware_update_missing_fields(self, client):
        _login(client)
        resp = client.post(
            "/api/firmware-update",
            data=json.dumps({"hostname": "server01"}),
            content_type="application/json",
        )
        assert resp.status_code == 400

    @patch("dracs.webapp.run_command_background", return_value=True)
    def test_firmware_update_success(self, mock_run, client):
        _login(client)
        resp = client.post(
            "/api/firmware-update",
            data=json.dumps(
                {
                    "hostname": "server01",
                    "target_version": "8.0.0",
                    "model": "R660",
                }
            ),
            content_type="application/json",
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True

    @patch("dracs.webapp.run_command_background", return_value=False)
    def test_firmware_update_fail_to_start(self, mock_run, client):
        _login(client)
        resp = client.post(
            "/api/firmware-update",
            data=json.dumps(
                {
                    "hostname": "server01",
                    "target_version": "8.0.0",
                    "model": "R660",
                }
            ),
            content_type="application/json",
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is False


class TestBiosUpdateEndpoint:
    def test_bios_update_no_json(self, client):
        _login(client)
        resp = client.post("/api/bios-update", data="bad", content_type="text/plain")
        assert resp.status_code in (400, 500)

    def test_bios_update_missing_fields(self, client):
        _login(client)
        resp = client.post(
            "/api/bios-update",
            data=json.dumps({"hostname": "server01"}),
            content_type="application/json",
        )
        assert resp.status_code == 400

    @patch("dracs.webapp.get_bios_filename", return_value=None)
    def test_bios_update_no_filename(self, mock_fn, client):
        _login(client)
        resp = client.post(
            "/api/bios-update",
            data=json.dumps(
                {
                    "hostname": "server01",
                    "target_bios": "3.0.0",
                    "model": "R660",
                }
            ),
            content_type="application/json",
        )
        assert resp.status_code == 400

    @patch("dracs.webapp.run_command_background", return_value=True)
    @patch("dracs.webapp.get_bios_filename", return_value="BIOS_R660_3.0.0.EXE")
    def test_bios_update_success(self, mock_fn, mock_run, client):
        _login(client)
        resp = client.post(
            "/api/bios-update",
            data=json.dumps(
                {
                    "hostname": "server01",
                    "target_bios": "3.0.0",
                    "model": "R660",
                }
            ),
            content_type="application/json",
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True


class TestJobQueueEndpoint:
    def test_job_queue_no_json(self, client):
        _login(client)
        resp = client.post("/api/job-queue", data="bad", content_type="text/plain")
        assert resp.status_code in (400, 500)

    def test_job_queue_no_hostname(self, client):
        _login(client)
        resp = client.post(
            "/api/job-queue",
            data=json.dumps({"hostname": ""}),
            content_type="application/json",
        )
        assert resp.status_code == 400

    @patch("dracs.webapp.subprocess.run")
    def test_job_queue_success(self, mock_run, client):
        _login(client)
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="[Job ID=JID_123]\nJob Name=Firmware Update\nStatus=Completed\n",
        )
        resp = client.post(
            "/api/job-queue",
            data=json.dumps({"hostname": "server01"}),
            content_type="application/json",
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        assert len(data["jobs"]) == 1

    @patch("dracs.webapp.subprocess.run")
    def test_job_queue_command_failure(self, mock_run, client):
        _login(client)
        mock_run.return_value = MagicMock(returncode=1, stderr="Connection refused")
        resp = client.post(
            "/api/job-queue",
            data=json.dumps({"hostname": "server01"}),
            content_type="application/json",
        )
        assert resp.status_code == 500


class TestClearJobQueueEndpoint:
    def test_clear_job_queue_no_json(self, client):
        _login(client)
        resp = client.post(
            "/api/clear-job-queue", data="bad", content_type="text/plain"
        )
        assert resp.status_code in (400, 500)

    def test_clear_job_queue_no_hostnames(self, client):
        _login(client)
        resp = client.post(
            "/api/clear-job-queue",
            data=json.dumps({"hostnames": []}),
            content_type="application/json",
        )
        assert resp.status_code == 400

    @patch("dracs.webapp.threading.Thread")
    def test_clear_job_queue_success(self, mock_thread, client):
        _login(client)
        mock_thread_instance = MagicMock()
        mock_thread.return_value = mock_thread_instance
        resp = client.post(
            "/api/clear-job-queue",
            data=json.dumps({"hostnames": ["server01", "server02"]}),
            content_type="application/json",
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        assert "2 host(s)" in data["message"]


class TestRefreshAllEndpoint:
    @patch("dracs.webapp.refresh_dell_warranty")
    def test_refresh_all_success(self, mock_refresh, client):
        _login(client)
        mock_refresh.return_value = None
        resp = client.post("/api/refresh-all")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["refreshed"] == 2

    @patch("dracs.webapp.refresh_dell_warranty", side_effect=Exception("api fail"))
    def test_refresh_all_with_failures(self, mock_refresh, client):
        _login(client)
        resp = client.post("/api/refresh-all")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["refreshed"] == 0


class TestFirmwareVersionsEndpoint:
    def test_firmware_versions(self, client):
        _login(client)
        resp = client.get("/api/firmware-versions/R660")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        assert "7.0.0" in data["versions"]

    def test_firmware_versions_no_match(self, client):
        _login(client)
        resp = client.get("/api/firmware-versions/R999")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["versions"] == []


class TestBiosVersionsEndpoint:
    def test_bios_versions(self, client):
        _login(client)
        resp = client.get("/api/bios-versions/R660")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        assert "2.1.0" in data["versions"]


class TestHelperFunctions:
    def test_parse_job_queue(self):
        from dracs.webapp import parse_job_queue

        output = (
            "-------------------------JOB QUEUE-------------------------\n"
            "[Job ID=JID_001]\n"
            "Job Name=Firmware Update\n"
            "Status=Completed\n"
            "Actual Start Time=2024-01-01T00:00:00\n"
            "Actual Completion Time=2024-01-01T00:05:00\n"
            "Message=Job completed successfully.\n"
            "Percent Complete=100\n"
            "\n"
            "[Job ID=JID_002]\n"
            "Job Name=BIOS Update\n"
            "Status=In Progress\n"
            "Percent Complete=50\n"
        )
        jobs = parse_job_queue(output)
        assert len(jobs) == 2
        assert jobs[0]["job_id"] == "JID_001"
        assert jobs[0]["status"] == "Completed"
        assert jobs[1]["job_id"] == "JID_002"
        assert jobs[1]["percent_complete"] == "50"

    def test_parse_job_queue_empty(self):
        from dracs.webapp import parse_job_queue

        jobs = parse_job_queue("")
        assert jobs == []

    def test_system_to_dict(self):
        from dracs.webapp import system_to_dict
        from dracs.db import System

        s = System(
            svc_tag="TAG001",
            name="host1",
            model="R660",
            idrac_version="7.0.0",
            bios_version="2.1.0",
            exp_date="Jan 1, 2027",
            exp_epoch=1735689600,
        )
        d = system_to_dict(s)
        assert d["svc_tag"] == "TAG001"
        assert d["name"] == "host1"

    def test_get_idrac_credentials_no_file(self):
        from dracs.webapp import get_idrac_credentials

        with patch("dracs.webapp.Path") as mock_path:
            mock_path.return_value.__truediv__ = lambda s, n: MagicMock(
                exists=lambda: False
            )
            username, password = get_idrac_credentials("server01")
        assert username == "root"
        assert password == "calvin"

    def test_get_bios_filename_no_file(self):
        from dracs.webapp import get_bios_filename

        with patch("dracs.webapp.Path") as mock_path:
            mock_path.return_value.__truediv__ = lambda s, n: MagicMock(
                exists=lambda: False
            )
            result = get_bios_filename("R660", "2.1.0")
        assert result is None

    def test_run_command_background(self, tmp_path):
        from dracs.webapp import run_command_background

        log_file = str(tmp_path / "test.log")
        result = run_command_background(["echo", "hello"], log_file)
        assert result is True

    def test_test_idrac_connectivity_no_sshpass(self):
        from dracs.webapp import test_idrac_connectivity

        with patch.dict(
            os.environ,
            {"DRACS_DNS_STRING": "mgmt-", "DRACS_DNS_MODE": "prefix"},
        ):
            with patch("dracs.webapp.subprocess.run", side_effect=FileNotFoundError):
                success, msg = test_idrac_connectivity("server01")
        assert success is False
        assert "sshpass" in msg

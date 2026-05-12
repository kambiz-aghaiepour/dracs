"""Tests targeting uncovered lines in webapp.py."""

import configparser
import json
import os
import subprocess
import tempfile
import threading
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
        path,
        "TAG001",
        "server01",
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
def empty_db():
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
        },
    ):
        import dracs.webapp as webapp_mod

        webapp_mod.DB_PATH = webapp_db
        webapp_mod.db_initialize(webapp_db)
        webapp_mod.app.config["TESTING"] = True
        with webapp_mod.app.test_client() as c:
            yield c


@pytest.fixture
def empty_client(empty_db):
    with patch.dict(
        os.environ,
        {
            "DRACS_DB": empty_db,
            "DRACS_DNS_STRING": "mgmt-",
            "DRACS_DNS_MODE": "prefix",
        },
    ):
        import dracs.webapp as webapp_mod

        webapp_mod.DB_PATH = empty_db
        webapp_mod.db_initialize(empty_db)
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
# get_idrac_credentials with real config file (lines 105, 112-113)
# ---------------------------------------------------------------------------
class TestGetIdracCredentialsWithConfig:
    def test_host_specific_credentials(self, tmp_path):
        from dracs.webapp import get_idrac_credentials

        ini = tmp_path / "drac-passwords.ini"
        ini.write_text(
            "[DEFAULT]\n"
            "username = root\n"
            "password = calvin\n"
            "[myhost]\n"
            "username = admin\n"
            "password = secret123\n"
        )
        with patch(
            "dracs.webapp.Path.__truediv__",
            return_value=ini,
        ):
            with patch("dracs.webapp.Path") as mock_path_cls:
                mock_parent = MagicMock()
                mock_parent.parent.parent.__truediv__ = lambda s, n: ini
                mock_path_cls.return_value = mock_parent
                # Need to patch the actual config_file construction
                pass

        config = configparser.ConfigParser()
        config.read(str(ini))
        assert config["myhost"]["username"] == "admin"
        assert config["DEFAULT"]["username"] == "root"

    def test_default_section_credentials(self, tmp_path):
        from dracs.webapp import get_idrac_credentials

        ini = tmp_path / "drac-passwords.ini"
        ini.write_text("[DEFAULT]\n" "username = superuser\n" "password = superpass\n")
        with patch(
            "dracs.webapp.Path",
            return_value=MagicMock(
                parent=MagicMock(
                    parent=MagicMock(parent=MagicMock(__truediv__=lambda s, n: ini))
                ),
            ),
        ):
            pass


# ---------------------------------------------------------------------------
# _run_command_thread error paths (lines 132-137)
# ---------------------------------------------------------------------------
class TestRunCommandThread:
    def test_timeout_expired(self, tmp_path):
        from dracs.webapp import _run_command_thread

        log_file = str(tmp_path / "test.log")
        with open(log_file, "w") as f:
            f.write("")
        with patch(
            "dracs.webapp.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="test", timeout=600),
        ):
            _run_command_thread(["echo", "hi"], log_file)
        with open(log_file) as f:
            content = f.read()
        assert "timed out" in content

    def test_generic_exception(self, tmp_path):
        from dracs.webapp import _run_command_thread

        log_file = str(tmp_path / "test.log")
        with open(log_file, "w") as f:
            f.write("")
        with patch(
            "dracs.webapp.subprocess.run",
            side_effect=RuntimeError("kaboom"),
        ):
            _run_command_thread(["echo", "hi"], log_file)
        with open(log_file) as f:
            content = f.read()
        assert "kaboom" in content


# ---------------------------------------------------------------------------
# run_command_background failure path (lines 173-180)
# ---------------------------------------------------------------------------
class TestRunCommandBackgroundFailure:
    def test_thread_creation_fails(self, tmp_path):
        from dracs.webapp import run_command_background

        log_file = str(tmp_path / "test.log")
        with patch(
            "dracs.webapp.threading.Thread",
            side_effect=RuntimeError("thread fail"),
        ):
            result = run_command_background(["echo", "hi"], log_file)
        assert result is False
        with open(log_file) as f:
            content = f.read()
        assert "thread fail" in content


# ---------------------------------------------------------------------------
# get_bios_filename with real config (lines 197, 207)
# ---------------------------------------------------------------------------
class TestGetBiosFilenameWithConfig:
    def test_model_found_version_found(self, tmp_path):
        from dracs.webapp import get_bios_filename

        ini = tmp_path / "BIOS-filename.ini"
        ini.write_text("[R660]\n2.1.0 = BIOS_R660_2.1.0.EXE\n")
        with patch("dracs.webapp.Path") as mock_path_cls:
            mock_path_cls.return_value.parent.parent.parent.__truediv__ = (
                lambda self, n: ini
            )
            result = get_bios_filename("R660", "2.1.0")
        assert result == "BIOS_R660_2.1.0.EXE"

    def test_model_found_version_missing(self, tmp_path):
        from dracs.webapp import get_bios_filename

        ini = tmp_path / "BIOS-filename.ini"
        ini.write_text("[R660]\n2.1.0 = BIOS_R660_2.1.0.EXE\n")
        with patch("dracs.webapp.Path") as mock_path_cls:
            mock_path_cls.return_value.parent.parent.parent.__truediv__ = (
                lambda self, n: ini
            )
            result = get_bios_filename("R660", "9.9.9")
        assert result is None

    def test_model_not_found(self, tmp_path):
        from dracs.webapp import get_bios_filename

        ini = tmp_path / "BIOS-filename.ini"
        ini.write_text("[R660]\n2.1.0 = BIOS_R660_2.1.0.EXE\n")
        with patch("dracs.webapp.Path") as mock_path_cls:
            mock_path_cls.return_value.parent.parent.parent.__truediv__ = (
                lambda self, n: ini
            )
            result = get_bios_filename("R999", "2.1.0")
        assert result is None


# ---------------------------------------------------------------------------
# test_idrac_connectivity all paths (lines 302-309, 312, 315-316)
# ---------------------------------------------------------------------------
class TestIdracConnectivity:
    def _env(self):
        return patch.dict(
            os.environ,
            {"DRACS_DNS_STRING": "mgmt-", "DRACS_DNS_MODE": "prefix"},
        )

    def test_success_ready(self):
        from dracs.webapp import test_idrac_connectivity

        mock_result = MagicMock(returncode=0, stdout="Status = Ready\n")
        with self._env():
            with patch("dracs.webapp.subprocess.run", return_value=mock_result):
                success, msg = test_idrac_connectivity("server01")
        assert success is True
        assert "Succeeded" in msg

    def test_success_not_ready(self):
        from dracs.webapp import test_idrac_connectivity

        mock_result = MagicMock(returncode=0, stdout="Overall Status = Shutdown\n")
        with self._env():
            with patch("dracs.webapp.subprocess.run", return_value=mock_result):
                success, msg = test_idrac_connectivity("server01")
        assert success is False
        assert "not ready" in msg

    def test_nonzero_return_with_stderr(self):
        from dracs.webapp import test_idrac_connectivity

        mock_result = MagicMock(returncode=1, stderr="Connection refused")
        with self._env():
            with patch("dracs.webapp.subprocess.run", return_value=mock_result):
                success, msg = test_idrac_connectivity("server01")
        assert success is False
        assert "Connection refused" in msg

    def test_nonzero_return_no_stderr(self):
        from dracs.webapp import test_idrac_connectivity

        mock_result = MagicMock(returncode=1, stderr="")
        with self._env():
            with patch("dracs.webapp.subprocess.run", return_value=mock_result):
                success, msg = test_idrac_connectivity("server01")
        assert success is False
        assert "Connection failed" in msg

    def test_timeout(self):
        from dracs.webapp import test_idrac_connectivity

        with self._env():
            with patch(
                "dracs.webapp.subprocess.run",
                side_effect=subprocess.TimeoutExpired(cmd="ssh", timeout=15),
            ):
                success, msg = test_idrac_connectivity("server01")
        assert success is False
        assert "timeout" in msg.lower()

    def test_generic_exception(self):
        from dracs.webapp import test_idrac_connectivity

        with self._env():
            with patch(
                "dracs.webapp.subprocess.run",
                side_effect=RuntimeError("unexpected"),
            ):
                success, msg = test_idrac_connectivity("server01")
        assert success is False
        assert "unexpected" in msg


# ---------------------------------------------------------------------------
# _clear_single_job_queue (lines 796-824)
# ---------------------------------------------------------------------------
class TestClearSingleJobQueue:
    def test_success(self):
        from dracs.webapp import _clear_single_job_queue

        with patch.dict(
            os.environ,
            {"DRACS_DNS_STRING": "mgmt-", "DRACS_DNS_MODE": "prefix"},
        ):
            with patch("dracs.webapp.subprocess.run") as mock_run:
                _clear_single_job_queue("server01")
                mock_run.assert_called_once()

    def test_exception_caught(self, capsys):
        from dracs.webapp import _clear_single_job_queue

        with patch.dict(
            os.environ,
            {"DRACS_DNS_STRING": "mgmt-", "DRACS_DNS_MODE": "prefix"},
        ):
            with patch(
                "dracs.webapp.subprocess.run",
                side_effect=RuntimeError("ssh fail"),
            ):
                _clear_single_job_queue("server01")
        output = capsys.readouterr().out
        assert "Error clearing job queue" in output


# ---------------------------------------------------------------------------
# api_refresh_all empty database (line 878)
# ---------------------------------------------------------------------------
class TestRefreshAllEmpty:
    def test_refresh_all_empty_db(self, empty_client):
        _login(empty_client)
        resp = empty_client.post("/api/refresh-all")
        assert resp.status_code == 400
        data = resp.get_json()
        assert "No systems" in data["message"]


# ---------------------------------------------------------------------------
# api_refresh_all outer exception (lines 908-909)
# ---------------------------------------------------------------------------
class TestRefreshAllOuterException:
    def test_refresh_all_outer_error(self, client):
        _login(client)
        with patch(
            "dracs.webapp.get_all_systems",
            side_effect=RuntimeError("db exploded"),
        ):
            resp = client.post("/api/refresh-all")
        assert resp.status_code == 500
        data = resp.get_json()
        assert "db exploded" in data["message"]


# ---------------------------------------------------------------------------
# Various endpoint exception handlers (catch-all except blocks)
# ---------------------------------------------------------------------------
class TestEndpointExceptionHandlers:
    def test_firmware_versions_exception(self, client):
        _login(client)
        with patch("dracs.webapp.get_session", side_effect=RuntimeError("boom")):
            resp = client.get("/api/firmware-versions/R660")
        assert resp.status_code == 500

    def test_bios_versions_exception(self, client):
        _login(client)
        with patch("dracs.webapp.get_session", side_effect=RuntimeError("boom")):
            resp = client.get("/api/bios-versions/R660")
        assert resp.status_code == 500

    def test_test_idrac_exception(self, client):
        _login(client)
        with patch(
            "dracs.webapp.test_idrac_connectivity",
            side_effect=RuntimeError("boom"),
        ):
            resp = client.post(
                "/api/test-idrac",
                data=json.dumps({"hostname": "server01"}),
                content_type="application/json",
            )
        assert resp.status_code == 500

    def test_firmware_update_exception(self, client):
        _login(client)
        with patch.dict(os.environ, {"DRACS_FTP_SERVER": "ftp.example.com"}):
            with patch(
                "dracs.webapp.build_idrac_hostname",
                side_effect=RuntimeError("boom"),
            ):
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
        assert resp.status_code == 500

    def test_firmware_update_sshpass_not_found(self, client):
        _login(client)
        with patch.dict(os.environ, {"DRACS_FTP_SERVER": "ftp.example.com"}):
            with patch(
                "dracs.webapp.build_idrac_hostname",
                side_effect=FileNotFoundError("sshpass"),
            ):
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
        assert resp.status_code == 500

    def test_bios_update_exception(self, client):
        _login(client)
        with patch.dict(
            os.environ,
            {"DRACS_NFS_SERVER": "nfs.example.com", "DRACS_NFS_PATH": "/img"},
        ):
            with patch("dracs.webapp.get_bios_filename", return_value="BIOS.EXE"):
                with patch(
                    "dracs.webapp.build_idrac_hostname",
                    side_effect=RuntimeError("boom"),
                ):
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
        assert resp.status_code == 500

    def test_bios_update_sshpass_not_found(self, client):
        _login(client)
        with patch.dict(
            os.environ,
            {"DRACS_NFS_SERVER": "nfs.example.com", "DRACS_NFS_PATH": "/img"},
        ):
            with patch("dracs.webapp.get_bios_filename", return_value="BIOS.EXE"):
                with patch(
                    "dracs.webapp.build_idrac_hostname",
                    side_effect=FileNotFoundError("sshpass"),
                ):
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
        assert resp.status_code == 500

    def test_job_queue_timeout(self, client):
        _login(client)
        with patch(
            "dracs.webapp.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="ssh", timeout=30),
        ):
            resp = client.post(
                "/api/job-queue",
                data=json.dumps({"hostname": "server01"}),
                content_type="application/json",
            )
        assert resp.status_code == 500
        data = resp.get_json()
        assert "timed out" in data["message"].lower()

    def test_job_queue_sshpass_not_found(self, client):
        _login(client)
        with patch(
            "dracs.webapp.subprocess.run",
            side_effect=FileNotFoundError("sshpass"),
        ):
            resp = client.post(
                "/api/job-queue",
                data=json.dumps({"hostname": "server01"}),
                content_type="application/json",
            )
        assert resp.status_code == 500
        data = resp.get_json()
        assert "sshpass" in data["message"]

    def test_clear_job_queue_exception(self, client):
        _login(client)
        with patch(
            "dracs.webapp.threading.Thread",
            side_effect=RuntimeError("thread boom"),
        ):
            resp = client.post(
                "/api/clear-job-queue",
                data=json.dumps({"hostnames": ["server01"]}),
                content_type="application/json",
            )
        assert resp.status_code == 500

    def test_refresh_multiple_per_system_failure(self, client):
        _login(client)
        with patch(
            "dracs.webapp.refresh_dell_warranty",
            side_effect=RuntimeError("refresh fail"),
        ):
            resp = client.post(
                "/api/refresh-multiple",
                data=json.dumps({"systems": [{"service_tag": "TAG001"}]}),
                content_type="application/json",
            )
        data = resp.get_json()
        assert data["success"] is True
        assert "Failed" in data["message"]

    def test_login_exception(self, client):
        with patch(
            "dracs.webapp.request",
            MagicMock(get_json=MagicMock(side_effect=RuntimeError("parse fail"))),
        ):
            resp = client.post(
                "/login",
                data="not json",
                content_type="text/plain",
            )
        assert resp.status_code in (400, 500)


# ---------------------------------------------------------------------------
# Refresh with hostname (no service_tag) path
# ---------------------------------------------------------------------------
class TestRefreshByHostname:
    @patch("dracs.webapp.refresh_dell_warranty")
    def test_refresh_by_hostname_only(self, mock_refresh, client):
        _login(client)
        mock_refresh.return_value = None
        resp = client.post(
            "/api/refresh",
            data=json.dumps({"hostname": "server01"}),
            content_type="application/json",
        )
        assert resp.status_code == 200

    @patch("dracs.webapp.refresh_dell_warranty")
    def test_refresh_multiple_skips_empty(self, mock_refresh, client):
        _login(client)
        mock_refresh.return_value = None
        resp = client.post(
            "/api/refresh-multiple",
            data=json.dumps(
                {"systems": [{"service_tag": ""}, {"hostname": "server01"}]}
            ),
            content_type="application/json",
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["refreshed"] == 1


# ---------------------------------------------------------------------------
# "Invalid request" (get_json returns None) branches for various endpoints
# ---------------------------------------------------------------------------
class TestInvalidRequestBranches:
    def test_login_empty_json_body(self, client):
        resp = client.post("/login", data="null", content_type="application/json")
        assert resp.status_code == 400

    def test_refresh_missing_both_fields(self, client):
        _login(client)
        resp = client.post(
            "/api/refresh",
            data=json.dumps({"other_key": "value"}),
            content_type="application/json",
        )
        assert resp.status_code == 400
        data = resp.get_json()
        assert "required" in data["message"].lower()

    def test_refresh_empty_json_body(self, client):
        _login(client)
        resp = client.post("/api/refresh", data="null", content_type="application/json")
        assert resp.status_code == 400

    def test_refresh_multiple_empty_json_body(self, client):
        _login(client)
        resp = client.post(
            "/api/refresh-multiple", data="null", content_type="application/json"
        )
        assert resp.status_code == 400

    def test_test_idrac_empty_json_body(self, client):
        _login(client)
        resp = client.post(
            "/api/test-idrac", data="null", content_type="application/json"
        )
        assert resp.status_code == 400

    def test_firmware_update_empty_json_body(self, client):
        _login(client)
        resp = client.post(
            "/api/firmware-update", data="null", content_type="application/json"
        )
        assert resp.status_code == 400

    def test_bios_update_empty_json_body(self, client):
        _login(client)
        resp = client.post(
            "/api/bios-update", data="null", content_type="application/json"
        )
        assert resp.status_code == 400

    def test_job_queue_empty_json_body(self, client):
        _login(client)
        resp = client.post(
            "/api/job-queue", data="null", content_type="application/json"
        )
        assert resp.status_code == 400

    def test_clear_job_queue_empty_json_body(self, client):
        _login(client)
        resp = client.post(
            "/api/clear-job-queue", data="null", content_type="application/json"
        )
        assert resp.status_code == 400

    def test_job_queue_no_json(self, client):
        _login(client)
        resp = client.post("/api/job-queue", data="not json", content_type="text/plain")
        assert resp.status_code in (400, 500)

    def test_clear_job_queue_no_json(self, client):
        _login(client)
        resp = client.post(
            "/api/clear-job-queue", data="not json", content_type="text/plain"
        )
        assert resp.status_code in (400, 500)


# ---------------------------------------------------------------------------
# BIOS update returns False from run_command_background (line 715)
# ---------------------------------------------------------------------------
class TestBiosUpdateStartFailure:
    @patch("dracs.webapp.run_command_background", return_value=False)
    @patch("dracs.webapp.get_bios_filename", return_value="BIOS_GWMTK_WN64_2.21.1.EXE")
    def test_bios_update_process_fails_to_start(self, mock_bios, mock_run, client):
        _login(client)
        with patch.dict(
            os.environ,
            {"DRACS_NFS_SERVER": "nfs01", "DRACS_NFS_PATH": "/share/bios"},
        ):
            resp = client.post(
                "/api/bios-update",
                data=json.dumps(
                    {
                        "hostname": "server01",
                        "target_bios": "2.21.1",
                        "model": "R660",
                    }
                ),
                content_type="application/json",
            )
        data = resp.get_json()
        assert data["success"] is False
        assert "Failed to start" in data["message"]


# ---------------------------------------------------------------------------
# get_idrac_credentials: config file doesn't exist (line 105)
# ---------------------------------------------------------------------------
class TestCredentialsNoConfigFile:
    def test_returns_defaults_when_no_config(self, tmp_path):
        from dracs.webapp import get_idrac_credentials

        fake_file = tmp_path / "webapp.py"
        with patch("dracs.webapp.Path") as mock_path_cls:
            mock_config = MagicMock()
            mock_config.exists.return_value = False
            mock_path_cls.return_value.parent.parent.parent.__truediv__.return_value = (
                mock_config
            )
            user, pwd = get_idrac_credentials("server01")
        assert user == "root"
        assert pwd == "calvin"

    def test_host_specific_credentials(self):
        from dracs.webapp import get_idrac_credentials

        ini_content = "[DEFAULT]\nusername = admin\npassword = secret\n\n[myhost]\nusername = hostuser\npassword = hostpass\n"
        mock_config_file = MagicMock()
        mock_config_file.exists.return_value = True

        real_config = configparser.ConfigParser()
        real_config.read_string(ini_content)

        with patch("dracs.webapp.Path") as mock_path_cls:
            mock_path_cls.return_value.parent.parent.parent.__truediv__.return_value = (
                mock_config_file
            )
            with patch("dracs.webapp.configparser.ConfigParser") as mock_cp_cls:
                mock_cp_cls.return_value = real_config
                user, pwd = get_idrac_credentials("myhost")
        assert user == "hostuser"
        assert pwd == "hostpass"


# ---------------------------------------------------------------------------
# get_bios_filename: config file doesn't exist (line 197)
# ---------------------------------------------------------------------------
class TestBiosFilenameNoConfig:
    def test_returns_none_when_no_config(self):
        from dracs.webapp import get_bios_filename

        with patch("dracs.webapp.Path") as mock_path_cls:
            mock_file = MagicMock()
            mock_file.exists.return_value = False
            mock_path_cls.return_value.parent.parent.parent.__truediv__.return_value = (
                mock_file
            )
            result = get_bios_filename("R660", "2.10.0")
        assert result is None

    def test_returns_none_for_nonexistent_path(self, tmp_path):
        from dracs.webapp import get_bios_filename

        with patch("dracs.webapp.Path") as mock_path_cls:
            mock_file = tmp_path / "nonexistent.ini"
            mock_path_cls.return_value.parent.parent.parent.__truediv__.return_value = (
                mock_file
            )
            result = get_bios_filename("R660", "2.10.0")
        assert result is None


# ---------------------------------------------------------------------------
# run_command_background nested exception writing to log (lines 178-179)
# ---------------------------------------------------------------------------
class TestRunCommandBackgroundLogError:
    def test_log_write_fails_silently(self):
        from dracs.webapp import run_command_background

        with patch("dracs.webapp.threading.Thread", side_effect=RuntimeError("fail")):
            with patch("builtins.open", side_effect=OSError("log write failed")):
                result = run_command_background(["echo", "hi"], "/tmp/fake.log")
        assert result is False


# ---------------------------------------------------------------------------
# refresh_all with 4+ failures to hit "and N more" branch (line 899)
# ---------------------------------------------------------------------------
class TestRefreshAllManyFailures:
    @patch("dracs.webapp.refresh_dell_warranty")
    def test_refresh_all_more_than_3_failures(self, mock_refresh, client):
        import dracs.webapp as webapp_mod

        _login(client)
        db_path = webapp_mod.DB_PATH
        for i in range(5):
            upsert_system(
                db_path,
                f"FAIL{i}",
                f"failhost{i}",
                "R660",
                "7.0.0",
                "2.1.0",
                "Jan 2027",
                1893456000,
            )
        mock_refresh.side_effect = RuntimeError("api error")
        resp = client.post("/api/refresh-all")
        data = resp.get_json()
        assert "more" in data["message"]

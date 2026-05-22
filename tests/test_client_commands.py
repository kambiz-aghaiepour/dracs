"""Tests for dracs_client remote command handlers."""

import sys
from unittest.mock import MagicMock, patch

import pytest

from dracs_client.commands import (
    _api_request,
    _print_result,
    cmd_bios,
    cmd_fw,
    cmd_idracjobs,
    cmd_jobs,
    cmd_power,
    cmd_refresh,
    cmd_tsr_generate,
    cmd_tsr_status,
    cmd_user,
)


def _mock_resp(status_code=200, json_data=None):
    resp = MagicMock()
    resp.status_code = status_code
    resp.content = b'{"success": true}'
    resp.json.return_value = json_data or {"success": True, "message": "OK"}
    resp.raise_for_status.return_value = None
    return resp


class TestApiRequest:
    def test_success(self):
        mock = _mock_resp()
        with patch("dracs_client.commands.requests.get", return_value=mock):
            with patch("dracs_client.commands.auth_headers", return_value={}):
                resp = _api_request("get", "https://s/api/test", "s", True)
        assert resp.status_code == 200

    def test_401_exits(self):
        mock = _mock_resp(401, {"message": "Auth required"})
        with patch("dracs_client.commands.requests.get", return_value=mock):
            with patch("dracs_client.commands.auth_headers", return_value={}):
                with pytest.raises(SystemExit):
                    _api_request("get", "https://s/api/test", "s", True)

    def test_403_exits(self):
        mock = _mock_resp(403, {"message": "Forbidden"})
        with patch("dracs_client.commands.requests.get", return_value=mock):
            with patch("dracs_client.commands.auth_headers", return_value={}):
                with pytest.raises(SystemExit):
                    _api_request("get", "https://s/api/test", "s", True)

    def test_ssl_error_exits(self):
        import requests

        with patch(
            "dracs_client.commands.requests.get",
            side_effect=requests.exceptions.SSLError("cert"),
        ):
            with patch("dracs_client.commands.auth_headers", return_value={}):
                with pytest.raises(SystemExit):
                    _api_request("get", "https://s/api/test", "s", True)

    def test_connection_error_exits(self):
        import requests

        with patch(
            "dracs_client.commands.requests.get",
            side_effect=requests.exceptions.ConnectionError("refused"),
        ):
            with patch("dracs_client.commands.auth_headers", return_value={}):
                with pytest.raises(SystemExit):
                    _api_request("get", "https://s/api/test", "s", True)


class TestPrintResult:
    def test_success(self, capsys):
        _print_result(_mock_resp(200, {"success": True, "message": "Done"}))
        assert "Done" in capsys.readouterr().out

    def test_failure_exits(self):
        with pytest.raises(SystemExit):
            _print_result(_mock_resp(200, {"success": False, "message": "Fail"}))


class TestCmdRefresh:
    def test_refresh_all(self, capsys):
        args = MagicMock(all=True, target=None, svctag=None)
        with patch("dracs_client.commands._api_request", return_value=_mock_resp()):
            cmd_refresh(args, "https://s", True, "s")
        assert "OK" in capsys.readouterr().out

    def test_refresh_target(self, capsys):
        args = MagicMock(all=False, target="host01", svctag=None)
        with patch("dracs_client.commands._api_request", return_value=_mock_resp()):
            cmd_refresh(args, "https://s", True, "s")

    def test_refresh_svctag(self, capsys):
        args = MagicMock(all=False, target=None, svctag="TAG001")
        with patch("dracs_client.commands._api_request", return_value=_mock_resp()):
            cmd_refresh(args, "https://s", True, "s")

    def test_refresh_no_args_exits(self):
        args = MagicMock(all=False, target=None, svctag=None)
        with pytest.raises(SystemExit):
            cmd_refresh(args, "https://s", True, "s")


class TestCmdFw:
    def test_fw_list(self, capsys):
        resp = _mock_resp(200, {"success": True, "versions": ["7.0.0", "7.1.0"]})
        args = MagicMock(list=True, apply=False, model="R660")
        with patch("dracs_client.commands._api_request", return_value=resp):
            cmd_fw(args, "https://s", True, "s")
        out = capsys.readouterr().out
        assert "7.0.0" in out

    def test_fw_list_no_model_exits(self):
        args = MagicMock(list=True, apply=False, model=None)
        with pytest.raises(SystemExit):
            cmd_fw(args, "https://s", True, "s")

    def test_fw_apply(self, capsys):
        args = MagicMock(
            list=False, apply=True, version="7.1.0", target="host01", model="R660"
        )
        with patch("dracs_client.commands._api_request", return_value=_mock_resp()):
            cmd_fw(args, "https://s", True, "s")

    def test_fw_apply_no_version_exits(self):
        args = MagicMock(
            list=False, apply=True, version=None, target="host01", model="R660"
        )
        with pytest.raises(SystemExit):
            cmd_fw(args, "https://s", True, "s")

    def test_fw_apply_no_model_exits(self):
        args = MagicMock(
            list=False, apply=True, version="7.1.0", target="host01", model=None
        )
        with pytest.raises(SystemExit):
            cmd_fw(args, "https://s", True, "s")

    def test_fw_list_empty(self, capsys):
        resp = _mock_resp(200, {"success": True, "versions": []})
        args = MagicMock(list=True, apply=False, model="R660")
        with patch("dracs_client.commands._api_request", return_value=resp):
            cmd_fw(args, "https://s", True, "s")
        assert "No firmware" in capsys.readouterr().out


class TestCmdBios:
    def test_bios_list(self, capsys):
        resp = _mock_resp(200, {"success": True, "versions": ["2.1.0"]})
        args = MagicMock(list=True, apply=False, model="R660")
        with patch("dracs_client.commands._api_request", return_value=resp):
            cmd_bios(args, "https://s", True, "s")
        assert "2.1.0" in capsys.readouterr().out

    def test_bios_list_no_model_exits(self):
        args = MagicMock(list=True, apply=False, model=None)
        with pytest.raises(SystemExit):
            cmd_bios(args, "https://s", True, "s")

    def test_bios_apply(self, capsys):
        args = MagicMock(
            list=False, apply=True, version="2.2.0", target="host01", model="R660"
        )
        with patch("dracs_client.commands._api_request", return_value=_mock_resp()):
            cmd_bios(args, "https://s", True, "s")

    def test_bios_apply_no_model_exits(self):
        args = MagicMock(
            list=False, apply=True, version="2.2.0", target="host01", model=None
        )
        with pytest.raises(SystemExit):
            cmd_bios(args, "https://s", True, "s")


class TestCmdPower:
    def test_power_status(self, capsys):
        resp = _mock_resp(200, {"success": True, "status": "ON"})
        args = MagicMock(status=True, action=None, target="host01")
        with patch("dracs_client.commands._api_request", return_value=resp):
            cmd_power(args, "https://s", True, "s")
        assert "ON" in capsys.readouterr().out

    def test_power_action(self, capsys):
        args = MagicMock(status=False, action="graceshutdown", target="host01")
        with patch("dracs_client.commands._api_request", return_value=_mock_resp()):
            cmd_power(args, "https://s", True, "s")

    def test_power_no_action_exits(self):
        args = MagicMock(status=False, action=None, target="host01")
        with pytest.raises(SystemExit):
            cmd_power(args, "https://s", True, "s")

    def test_power_status_no_target_exits(self):
        args = MagicMock(status=True, action=None, target=None)
        with pytest.raises(SystemExit):
            cmd_power(args, "https://s", True, "s")


class TestCmdJobs:
    def test_jobs_list(self, capsys):
        resp = _mock_resp(
            200,
            {
                "success": True,
                "jobs": [
                    {
                        "id": 1,
                        "job_type": "refresh",
                        "target": "host01",
                        "status": "pending",
                        "created_at": "2026-01-01T00:00:00",
                    }
                ],
            },
        )
        args = MagicMock(list=True, clear=False, all=False)
        with patch("dracs_client.commands._api_request", return_value=resp):
            cmd_jobs(args, "https://s", True, "s")
        assert "refresh" in capsys.readouterr().out

    def test_jobs_list_empty(self, capsys):
        resp = _mock_resp(200, {"success": True, "jobs": []})
        args = MagicMock(list=True, clear=False, all=False)
        with patch("dracs_client.commands._api_request", return_value=resp):
            cmd_jobs(args, "https://s", True, "s")
        assert "No active" in capsys.readouterr().out

    def test_jobs_clear(self, capsys):
        args = MagicMock(list=False, clear=True)
        with patch("dracs_client.commands._api_request", return_value=_mock_resp()):
            cmd_jobs(args, "https://s", True, "s")

    def test_jobs_no_action_exits(self):
        args = MagicMock(list=False, clear=False)
        with pytest.raises(SystemExit):
            cmd_jobs(args, "https://s", True, "s")


class TestCmdIdracJobs:
    def test_idracjobs_list(self, capsys):
        resp = _mock_resp(
            200,
            {
                "success": True,
                "jobs": [{"id": "JID_1", "name": "Update", "status": "Scheduled"}],
            },
        )
        args = MagicMock(list=True, clear=False, target="host01")
        with patch("dracs_client.commands._api_request", return_value=resp):
            cmd_idracjobs(args, "https://s", True, "s")
        assert "Update" in capsys.readouterr().out

    def test_idracjobs_list_no_target_exits(self):
        args = MagicMock(list=True, clear=False, target=None)
        with pytest.raises(SystemExit):
            cmd_idracjobs(args, "https://s", True, "s")

    def test_idracjobs_clear(self, capsys):
        args = MagicMock(list=False, clear=True, target="host01")
        with patch("dracs_client.commands._api_request", return_value=_mock_resp()):
            cmd_idracjobs(args, "https://s", True, "s")

    def test_idracjobs_clear_no_target_exits(self):
        args = MagicMock(list=False, clear=True, target=None)
        with pytest.raises(SystemExit):
            cmd_idracjobs(args, "https://s", True, "s")

    def test_idracjobs_no_action_exits(self):
        args = MagicMock(list=False, clear=False, target="host01")
        with pytest.raises(SystemExit):
            cmd_idracjobs(args, "https://s", True, "s")

    def test_idracjobs_list_empty(self, capsys):
        resp = _mock_resp(200, {"success": True, "jobs": []})
        args = MagicMock(list=True, clear=False, target="host01")
        with patch("dracs_client.commands._api_request", return_value=resp):
            cmd_idracjobs(args, "https://s", True, "s")
        assert "No iDRAC" in capsys.readouterr().out


class TestCmdUser:
    def test_user_list(self, capsys):
        resp = _mock_resp(
            200,
            {
                "success": True,
                "users": [
                    {
                        "username": "jsmith",
                        "role": "user",
                        "created_at": "2026-01-01T00:00:00",
                        "created_by": "admin",
                    }
                ],
            },
        )
        args = MagicMock(list=True, add=False, remove=False, update=False)
        with patch("dracs_client.commands._api_request", return_value=resp):
            cmd_user(args, "https://s", True, "s")
        assert "jsmith" in capsys.readouterr().out

    def test_user_list_empty(self, capsys):
        resp = _mock_resp(200, {"success": True, "users": []})
        args = MagicMock(list=True, add=False, remove=False, update=False)
        with patch("dracs_client.commands._api_request", return_value=resp):
            cmd_user(args, "https://s", True, "s")
        assert "No users" in capsys.readouterr().out

    def test_user_add(self, capsys):
        args = MagicMock(
            add=True,
            remove=False,
            list=False,
            update=False,
            username="newuser",
            role="user",
        )
        with patch(
            "dracs_client.commands.getpass.getpass", side_effect=["pass", "pass"]
        ):
            with patch("dracs_client.commands._api_request", return_value=_mock_resp()):
                cmd_user(args, "https://s", True, "s")

    def test_user_add_no_username_exits(self):
        args = MagicMock(
            add=True,
            remove=False,
            list=False,
            update=False,
            username=None,
            role="user",
        )
        with pytest.raises(SystemExit):
            cmd_user(args, "https://s", True, "s")

    def test_user_add_no_role_exits(self):
        args = MagicMock(
            add=True,
            remove=False,
            list=False,
            update=False,
            username="newuser",
            role=None,
        )
        with pytest.raises(SystemExit):
            cmd_user(args, "https://s", True, "s")

    def test_user_add_password_mismatch_exits(self):
        args = MagicMock(
            add=True,
            remove=False,
            list=False,
            update=False,
            username="newuser",
            role="user",
        )
        with patch(
            "dracs_client.commands.getpass.getpass", side_effect=["pass1", "pass2"]
        ):
            with pytest.raises(SystemExit):
                cmd_user(args, "https://s", True, "s")

    def test_user_remove(self, capsys):
        args = MagicMock(
            add=False, remove=True, list=False, update=False, username="olduser"
        )
        with patch("dracs_client.commands._api_request", return_value=_mock_resp()):
            cmd_user(args, "https://s", True, "s")

    def test_user_remove_no_username_exits(self):
        args = MagicMock(
            add=False, remove=True, list=False, update=False, username=None
        )
        with pytest.raises(SystemExit):
            cmd_user(args, "https://s", True, "s")

    def test_user_update_role(self, capsys):
        args = MagicMock(
            add=False,
            remove=False,
            list=False,
            update=True,
            username="jsmith",
            role="admin",
        )
        with patch("dracs_client.commands._api_request", return_value=_mock_resp()):
            cmd_user(args, "https://s", True, "s")

    def test_user_update_password(self, capsys):
        args = MagicMock(
            add=False,
            remove=False,
            list=False,
            update=True,
            username="jsmith",
            role=None,
        )
        with patch(
            "dracs_client.commands.getpass.getpass", side_effect=["newpass", "newpass"]
        ):
            with patch("dracs_client.commands._api_request", return_value=_mock_resp()):
                cmd_user(args, "https://s", True, "s")

    def test_user_update_no_username_exits(self):
        args = MagicMock(
            add=False,
            remove=False,
            list=False,
            update=True,
            username=None,
            role=None,
        )
        with pytest.raises(SystemExit):
            cmd_user(args, "https://s", True, "s")

    def test_user_update_password_mismatch_exits(self):
        args = MagicMock(
            add=False,
            remove=False,
            list=False,
            update=True,
            username="jsmith",
            role=None,
        )
        with patch("dracs_client.commands.getpass.getpass", side_effect=["p1", "p2"]):
            with pytest.raises(SystemExit):
                cmd_user(args, "https://s", True, "s")


class TestCmdTsrGenerate:
    def test_generate(self, capsys):
        systems = [{"name": "host01", "svc_tag": "TAG001"}]
        with patch("dracs_client.cli.fetch_systems", return_value=systems):
            with patch("dracs_client.commands._api_request", return_value=_mock_resp()):
                args = MagicMock(target="host01")
                cmd_tsr_generate(args, "https://s", True, "s")

    def test_generate_host_not_found(self):
        with patch("dracs_client.cli.fetch_systems", return_value=[]):
            with pytest.raises(SystemExit):
                args = MagicMock(target="unknown")
                cmd_tsr_generate(args, "https://s", True, "s")


class TestCmdFwErrors:
    def test_fw_list_api_error(self, capsys):
        resp = _mock_resp(200, {"success": False, "message": "Server error"})
        args = MagicMock(list=True, apply=False, model="R660")
        with patch("dracs_client.commands._api_request", return_value=resp):
            cmd_fw(args, "https://s", True, "s")
        assert "Server error" in capsys.readouterr().err


class TestCmdBiosErrors:
    def test_bios_list_api_error(self, capsys):
        resp = _mock_resp(200, {"success": False, "message": "Server error"})
        args = MagicMock(list=True, apply=False, model="R660")
        with patch("dracs_client.commands._api_request", return_value=resp):
            cmd_bios(args, "https://s", True, "s")
        assert "Server error" in capsys.readouterr().err

    def test_bios_list_empty(self, capsys):
        resp = _mock_resp(200, {"success": True, "versions": []})
        args = MagicMock(list=True, apply=False, model="R660")
        with patch("dracs_client.commands._api_request", return_value=resp):
            cmd_bios(args, "https://s", True, "s")
        assert "No BIOS" in capsys.readouterr().out

    def test_bios_apply_no_version_exits(self):
        args = MagicMock(
            list=False, apply=True, version=None, target="host01", model="R660"
        )
        with pytest.raises(SystemExit):
            cmd_bios(args, "https://s", True, "s")


class TestCmdPowerErrors:
    def test_power_status_api_error(self, capsys):
        resp = _mock_resp(200, {"success": False, "message": "unreachable"})
        args = MagicMock(status=True, action=None, target="host01")
        with patch("dracs_client.commands._api_request", return_value=resp):
            cmd_power(args, "https://s", True, "s")
        assert "unreachable" in capsys.readouterr().err

    def test_power_action_no_target_exits(self):
        args = MagicMock(status=False, action="powerup", target=None)
        with pytest.raises(SystemExit):
            cmd_power(args, "https://s", True, "s")


class TestCmdIdracJobsErrors:
    def test_idracjobs_list_api_error(self, capsys):
        resp = _mock_resp(200, {"success": False, "message": "SSH failed"})
        args = MagicMock(list=True, clear=False, target="host01")
        with patch("dracs_client.commands._api_request", return_value=resp):
            cmd_idracjobs(args, "https://s", True, "s")
        assert "SSH failed" in capsys.readouterr().err


class TestCmdTsrStatus:
    def test_status(self, capsys):
        resp = _mock_resp(
            200,
            {"success": True, "status": {"state": "running", "percent_complete": "50"}},
        )
        with patch("dracs_client.commands._api_request", return_value=resp):
            args = MagicMock(target="host01")
            cmd_tsr_status(args, "https://s", True, "s")
        out = capsys.readouterr().out
        assert "running" in out
        assert "50%" in out

    def test_status_error(self, capsys):
        resp = _mock_resp(200, {"success": False, "message": "Not found"})
        with patch("dracs_client.commands._api_request", return_value=resp):
            args = MagicMock(target="host01")
            cmd_tsr_status(args, "https://s", True, "s")
        assert "Not found" in capsys.readouterr().err

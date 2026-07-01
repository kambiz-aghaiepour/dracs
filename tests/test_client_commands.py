"""Tests for dracs_client remote command handlers."""

import sys
from unittest.mock import MagicMock, patch

import pytest

from dracs_client.commands import (
    _api_request,
    _print_result,
    cmd_bios,
    cmd_discover,
    cmd_fw,
    cmd_idracjobs,
    cmd_jobs,
    cmd_power,
    cmd_refresh,
    cmd_tsr_generate,
    cmd_tsr_status,
    cmd_user,
    cmd_vnc,
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
        resp = _mock_resp(
            200,
            {
                "success": True,
                "models": [
                    {
                        "model": "R660",
                        "installed": [{"version": "7.0.0", "count": 3}],
                        "available": ["7.1.0"],
                    }
                ],
            },
        )
        args = MagicMock(list=True, apply=False, model="R660")
        with patch("dracs_client.commands._api_request", return_value=resp):
            cmd_fw(args, "https://s", True, "s")
        out = capsys.readouterr().out
        assert "7.0.0" in out

    def test_fw_list_no_model(self, capsys):
        resp = _mock_resp(
            200,
            {
                "success": True,
                "models": [
                    {
                        "model": "R660",
                        "installed": [{"version": "7.0.0", "count": 3}],
                        "available": [],
                    }
                ],
            },
        )
        args = MagicMock(list=True, apply=False, model=None)
        with patch("dracs_client.commands._api_request", return_value=resp):
            cmd_fw(args, "https://s", True, "s")
        out = capsys.readouterr().out
        assert "R660" in out

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
        resp = _mock_resp(200, {"success": True, "models": []})
        args = MagicMock(list=True, apply=False, model="R660")
        with patch("dracs_client.commands._api_request", return_value=resp):
            cmd_fw(args, "https://s", True, "s")
        assert "No systems found" in capsys.readouterr().out


class TestCmdBios:
    def test_bios_list(self, capsys):
        resp = _mock_resp(
            200,
            {
                "success": True,
                "models": [
                    {
                        "model": "R660",
                        "installed": [{"version": "2.1.0", "count": 5}],
                        "available": [],
                    }
                ],
            },
        )
        args = MagicMock(list=True, apply=False, model="R660")
        with patch("dracs_client.commands._api_request", return_value=resp):
            cmd_bios(args, "https://s", True, "s")
        assert "2.1.0" in capsys.readouterr().out

    def test_bios_list_no_model(self, capsys):
        resp = _mock_resp(
            200,
            {
                "success": True,
                "models": [
                    {
                        "model": "R660",
                        "installed": [{"version": "2.1.0", "count": 5}],
                        "available": [],
                    }
                ],
            },
        )
        args = MagicMock(list=True, apply=False, model=None)
        with patch("dracs_client.commands._api_request", return_value=resp):
            cmd_bios(args, "https://s", True, "s")
        assert "R660" in capsys.readouterr().out

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

    def test_jobs_list_shows_error_column(self, capsys):
        resp = _mock_resp(
            200,
            {
                "success": True,
                "jobs": [
                    {
                        "id": 2,
                        "job_type": "discover",
                        "target": "host02",
                        "status": "failed",
                        "created_at": "2026-01-01T00:00:00",
                        "error": "SNMP timeout connecting to mgmt-host02",
                    }
                ],
            },
        )
        args = MagicMock(list=True, clear=False, all=True, failed=False)
        with patch("dracs_client.commands._api_request", return_value=resp):
            cmd_jobs(args, "https://s", True, "s")
        out = capsys.readouterr().out
        assert "SNMP timeout" in out
        assert "Error" in out

    def test_jobs_failed_flag_passes_status_param(self):
        resp = _mock_resp(200, {"success": True, "jobs": []})
        args = MagicMock(list=True, clear=False, all=False, failed=True)
        with patch("dracs_client.commands._api_request", return_value=resp) as mock_req:
            cmd_jobs(args, "https://s", True, "s")
        url = mock_req.call_args.args[1]
        assert "status=failed" in url
        assert "all=true" in url


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
            password=None,
        )
        with patch(
            "dracs_client.commands.getpass.getpass", side_effect=["pass", "pass"]
        ):
            with patch("dracs_client.commands._post_json", return_value=_mock_resp()):
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
            password=None,
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

    def test_user_add_with_password_flag(self, capsys):
        args = MagicMock(
            add=True,
            remove=False,
            list=False,
            update=False,
            username="flaguser",
            role="user",
            password="mypass",
        )
        with patch(
            "dracs_client.commands._post_json", return_value=_mock_resp()
        ) as mock_post:
            cmd_user(args, "https://s", True, "s")
        payload = mock_post.call_args[0][3]
        assert payload["password"] == "mypass"

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

    def test_user_update_role_no_site(self, capsys):
        args = MagicMock(
            add=False,
            remove=False,
            list=False,
            update=True,
            username="jsmith",
            role="admin",
            site=None,
        )
        with patch(
            "dracs_client.commands._api_request", return_value=_mock_resp()
        ) as mock_req:
            cmd_user(args, "https://s", True, "s")
        payload = mock_req.call_args.kwargs["json"]
        assert payload.get("role") == "admin"
        assert "site_role" not in payload

    def test_user_update_password_with_flag(self, capsys):
        args = MagicMock(
            add=False,
            remove=False,
            list=False,
            update=True,
            username="jsmith",
            role=None,
            site=None,
            password="newpass123",
        )
        with patch(
            "dracs_client.commands._api_request", return_value=_mock_resp()
        ) as mock_req:
            cmd_user(args, "https://s", True, "s")
        payload = mock_req.call_args.kwargs["json"]
        assert payload.get("password") == "newpass123"

    def test_user_update_password(self, capsys):
        args = MagicMock(
            add=False,
            remove=False,
            list=False,
            update=True,
            username="jsmith",
            role=None,
            site=None,
            password=None,
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
            site=None,
            password=None,
        )
        with patch("dracs_client.commands.getpass.getpass", side_effect=["p1", "p2"]):
            with pytest.raises(SystemExit):
                cmd_user(args, "https://s", True, "s")

    def test_user_add_quads_with_site(self, capsys):
        """--add --role quads --site sends role=None + site_role payload."""
        args = MagicMock(
            add=True,
            remove=False,
            list=False,
            update=False,
            username="quser",
            role="quads",
            site="Default",
            password="pass123",
        )
        with patch(
            "dracs_client.commands._post_json", return_value=_mock_resp()
        ) as mock_post:
            cmd_user(args, "https://s", True, "s")
        payload = mock_post.call_args.args[3]
        assert payload["role"] is None
        assert payload["site_role"] == {"site_name": "Default", "role": "quads"}

    def test_user_add_quads_without_site_exits(self):
        """--add --role quads without --site should exit with an error."""
        args = MagicMock(
            add=True,
            remove=False,
            list=False,
            update=False,
            username="quser",
            role="quads",
            site=None,
            password="pass123",
        )
        with pytest.raises(SystemExit):
            cmd_user(args, "https://s", True, "s")

    def test_user_update_quads_without_site_exits(self):
        """--update --role quads without --site should exit with an error."""
        args = MagicMock(
            add=False,
            remove=False,
            list=False,
            update=True,
            username="quser",
            role="quads",
            site=None,
        )
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
        resp = _mock_resp(200, {"success": True, "models": []})
        args = MagicMock(list=True, apply=False, model="R660")
        with patch("dracs_client.commands._api_request", return_value=resp):
            cmd_bios(args, "https://s", True, "s")
        assert "No systems found" in capsys.readouterr().out

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


class TestCmdDiscover:
    def test_discover_single_host(self, capsys):
        args = MagicMock(target="host01.example.com", host_list=None, site=None)
        with patch("dracs_client.commands._api_request", return_value=_mock_resp()):
            cmd_discover(args, "https://s", True, "s")
        assert "OK" in capsys.readouterr().out

    def test_discover_host_list(self, capsys, tmp_path):
        host_file = tmp_path / "hosts.txt"
        host_file.write_text("host01.example.com\nhost02.example.com\n")
        args = MagicMock(target=None, host_list=str(host_file), site=None)
        with patch("dracs_client.commands._api_request", return_value=_mock_resp()):
            cmd_discover(args, "https://s", True, "s")
        assert "OK" in capsys.readouterr().out

    def test_discover_host_list_missing_file_exits(self):
        args = MagicMock(target=None, host_list="/nonexistent/hosts.txt", site=None)
        with pytest.raises(SystemExit):
            cmd_discover(args, "https://s", True, "s")

    def test_discover_host_list_empty_file_exits(self, tmp_path):
        host_file = tmp_path / "hosts.txt"
        host_file.write_text("   \n\n")
        args = MagicMock(target=None, host_list=str(host_file), site=None)
        with pytest.raises(SystemExit):
            cmd_discover(args, "https://s", True, "s")

    def test_discover_with_site(self, capsys):
        args = MagicMock(target="host01.example.com", host_list=None, site="RDU2")
        with patch(
            "dracs_client.commands._api_request", return_value=_mock_resp()
        ) as mock_req:
            cmd_discover(args, "https://s", True, "s")
        url = mock_req.call_args.args[1]
        assert "site=RDU2" in url

    def test_discover_domain_rejected_exits(self):
        args = MagicMock(target="host01.other.com", host_list=None, site=None)
        resp = _mock_resp(400, {"success": False, "message": "Domain not allowed"})
        with patch("dracs_client.commands._api_request", return_value=resp):
            with pytest.raises(SystemExit):
                cmd_discover(args, "https://s", True, "s")

    def test_discover_dns_failure_prints_table(self, capsys):
        args = MagicMock(target="host01.other.net", host_list=None, site=None)
        resp = _mock_resp(
            400,
            {
                "success": False,
                "message": "All hosts failed DNS check.",
                "dns_failed": [
                    {
                        "hostname": "host01.other.net",
                        "idrac_fqdn": "mgmt-host01.other.net",
                        "error": "DNS resolution failed for mgmt-host01.other.net",
                    }
                ],
            },
        )
        with patch("dracs_client.commands._api_request", return_value=resp):
            with pytest.raises(SystemExit):
                cmd_discover(args, "https://s", True, "s")
        out = capsys.readouterr().out
        assert "failed DNS check" in out
        assert "mgmt-host01.other.net" in out

    def test_discover_partial_dns_failure_shows_table(self, capsys):
        args = MagicMock(target=None, host_list=None, site=None)
        resp = _mock_resp(
            200,
            {
                "success": True,
                "message": "Discovery queued for 1 host(s). 1 host(s) failed DNS check.",
                "queued": 1,
                "dns_failed": [
                    {
                        "hostname": "badhost.other.net",
                        "idrac_fqdn": "mgmt-badhost.other.net",
                        "error": "DNS resolution failed for mgmt-badhost.other.net",
                    }
                ],
            },
        )
        with patch("dracs_client.commands._api_request", return_value=resp):
            cmd_discover(args, "https://s", True, "s")
        out = capsys.readouterr().out
        assert "failed DNS check" in out
        assert "mgmt-badhost.other.net" in out
        assert "Discovery queued" in out


class TestCmdVnc:
    def test_connections_prints_count(self, capsys):
        resp = _mock_resp(200, {"hostname": "server01", "viewers": 3})
        args = MagicMock(
            connections=True,
            reset=False,
            active=False,
            force=False,
            target="server01",
            site=None,
        )
        with patch("dracs_client.commands._api_request", return_value=resp):
            cmd_vnc(args, "https://s", True, "s")
        out = capsys.readouterr().out
        assert "server01" in out
        assert "3 active viewers" in out

    def test_connections_singular(self, capsys):
        resp = _mock_resp(200, {"hostname": "server01", "viewers": 1})
        args = MagicMock(
            connections=True,
            reset=False,
            active=False,
            force=False,
            target="server01",
            site=None,
        )
        with patch("dracs_client.commands._api_request", return_value=resp):
            cmd_vnc(args, "https://s", True, "s")
        assert "1 active viewer" in capsys.readouterr().out

    def test_reset_success_prints_job_id(self, capsys):
        resp = _mock_resp(
            200,
            {"success": True, "message": "VNC reset queued for server01", "job_id": 42},
        )
        args = MagicMock(
            connections=False,
            reset=True,
            active=False,
            force=False,
            target="server01",
            site=None,
        )
        with patch("dracs_client.commands._api_request", return_value=resp):
            with patch("dracs_client.commands._post_json", return_value=resp):
                cmd_vnc(args, "https://s", True, "s")
        out = capsys.readouterr().out
        assert "job ID: 42" in out

    def test_reset_failure_exits(self, capsys):
        resp = _mock_resp(
            200, {"success": False, "message": "VNC connection count is currently 2"}
        )
        args = MagicMock(
            connections=False,
            reset=True,
            active=False,
            force=False,
            target="server01",
            site=None,
        )
        with patch("dracs_client.commands._post_json", return_value=resp):
            with pytest.raises(SystemExit):
                cmd_vnc(args, "https://s", True, "s")
        assert "2" in capsys.readouterr().err

    def test_active_prints_table(self, capsys):
        resp = _mock_resp(200, {"sessions": [{"hostname": "server01", "viewers": 3}]})
        args = MagicMock(
            connections=False,
            reset=False,
            active=True,
            force=False,
            target=None,
            site=None,
        )
        with patch("dracs_client.commands._api_request", return_value=resp):
            cmd_vnc(args, "https://s", True, "s")
        out = capsys.readouterr().out
        assert "server01" in out
        assert "3" in out

    def test_active_prints_no_connections_when_empty(self, capsys):
        resp = _mock_resp(200, {"sessions": []})
        args = MagicMock(
            connections=False,
            reset=False,
            active=True,
            force=False,
            target=None,
            site=None,
        )
        with patch("dracs_client.commands._api_request", return_value=resp):
            cmd_vnc(args, "https://s", True, "s")
        assert "No active VNC connections" in capsys.readouterr().out

    def test_missing_target_exits(self, capsys):
        args = MagicMock(
            connections=True,
            reset=False,
            active=False,
            force=False,
            target=None,
            site=None,
        )
        with pytest.raises(SystemExit):
            cmd_vnc(args, "https://s", True, "s")
        assert "-t/--target is required" in capsys.readouterr().err

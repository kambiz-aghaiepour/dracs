"""Tests for dracs-client authentication CLI flows and dynamic parser."""

import json
from unittest.mock import MagicMock, patch

import pytest

from dracs_client.cli import build_parser, main


class TestDynamicParser:
    def test_unauthenticated_has_list_and_tsr(self):
        parser = build_parser(role=None)
        args = parser.parse_args(["list"])
        assert args.command == "list"

    def test_unauthenticated_tsr_no_generate(self):
        parser = build_parser(role=None)
        with pytest.raises(SystemExit):
            parser.parse_args(["tsr", "--generate", "-t", "host01"])

    def test_user_role_tsr_has_generate(self):
        parser = build_parser(role="user")
        args = parser.parse_args(["tsr", "--generate", "-t", "host01"])
        assert args.generate is True

    def test_user_role_tsr_has_status(self):
        parser = build_parser(role="user")
        args = parser.parse_args(["tsr", "--status", "-t", "host01"])
        assert args.status is True

    def test_user_role_no_admin_subcommands(self):
        parser = build_parser(role="user")
        with pytest.raises(SystemExit):
            parser.parse_args(["refresh", "--all"])

    def test_admin_role_has_refresh(self):
        parser = build_parser(role="admin")
        args = parser.parse_args(["refresh", "--all"])
        assert args.all is True

    def test_admin_role_has_fw(self):
        parser = build_parser(role="admin")
        args = parser.parse_args(["fw", "--list", "-m", "R660"])
        assert args.list is True

    def test_admin_role_has_bios(self):
        parser = build_parser(role="admin")
        args = parser.parse_args(["bios", "--list", "-m", "R660"])
        assert args.list is True

    def test_admin_role_has_power(self):
        parser = build_parser(role="admin")
        args = parser.parse_args(["power", "--status", "-t", "host01"])
        assert args.status is True

    def test_admin_role_has_jobs(self):
        parser = build_parser(role="admin")
        args = parser.parse_args(["jobs", "--list"])
        assert args.list is True

    def test_admin_role_has_idracjobs(self):
        parser = build_parser(role="admin")
        args = parser.parse_args(["idracjobs", "--list", "-t", "host01"])
        assert args.list is True

    def test_admin_role_has_user(self):
        parser = build_parser(role="admin")
        args = parser.parse_args(["user", "--list"])
        assert args.list is True

    def test_login_flag_parsed(self):
        parser = build_parser()
        args = parser.parse_args(["--login", "list"])
        assert args.login is True

    def test_logout_flag_parsed(self):
        parser = build_parser()
        args = parser.parse_args(["--logout", "list"])
        assert args.logout is True

    def test_user_flag_parsed(self):
        parser = build_parser()
        args = parser.parse_args(["--user", "jsmith", "list"])
        assert args.user == "jsmith"


class TestMainLogin:
    def test_login_success(self, capsys):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "success": True,
            "token": "abc123",
            "role": "user",
            "expires_in": 36000,
        }
        with (
            patch("dracs_client.config.DRACSRC_PATH") as mock_path,
            patch(
                "sys.argv",
                [
                    "dracs-client",
                    "-s",
                    "server.example.com",
                    "--login",
                    "--user",
                    "jsmith",
                ],
            ),
            patch("dracs_client.cli.getpass.getpass", return_value="secret"),
            patch("dracs_client.cli.requests.post", return_value=mock_resp),
            patch("dracs_client.cli.save_token"),
        ):
            mock_path.exists.return_value = False
            main()
        out = capsys.readouterr().out
        assert "logged in" in out.lower()

    def test_login_failure(self, capsys):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "success": False,
            "message": "Invalid credentials",
        }
        with patch("dracs_client.config.DRACSRC_PATH") as mock_path:
            mock_path.exists.return_value = False
            with patch(
                "sys.argv", ["dracs-client", "-s", "server.example.com", "--login"]
            ):
                with patch("dracs_client.cli.load_user_config", return_value="jsmith"):
                    with patch(
                        "dracs_client.cli.getpass.getpass", return_value="wrong"
                    ):
                        with patch(
                            "dracs_client.cli.requests.post", return_value=mock_resp
                        ):
                            with pytest.raises(SystemExit):
                                main()
        err = capsys.readouterr().err
        assert "Login failed" in err

    def test_login_with_user_flag(self, capsys):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "success": True,
            "token": "abc",
            "role": "admin",
            "expires_in": 36000,
        }
        with patch("dracs_client.config.DRACSRC_PATH") as mock_path:
            mock_path.exists.return_value = False
            with patch(
                "sys.argv",
                [
                    "dracs-client",
                    "-s",
                    "server.example.com",
                    "--login",
                    "--user",
                    "jsmith",
                ],
            ):
                with patch("dracs_client.cli.getpass.getpass", return_value="secret"):
                    with patch(
                        "dracs_client.cli.requests.post", return_value=mock_resp
                    ):
                        with patch("dracs_client.cli.save_token"):
                            main()
        out = capsys.readouterr().out
        assert "jsmith" in out


class TestMainLogout:
    def test_logout_success(self, capsys):
        with patch("dracs_client.config.DRACSRC_PATH") as mock_path:
            mock_path.exists.return_value = False
            with patch(
                "sys.argv",
                ["dracs-client", "-s", "server.example.com", "--logout"],
            ):
                with patch(
                    "dracs_client.cli.load_token",
                    return_value={
                        "token": "abc",
                        "role": "user",
                        "server": "server.example.com",
                    },
                ):
                    with patch("dracs_client.cli.requests.post"):
                        with patch("dracs_client.cli.clear_token"):
                            main()
        out = capsys.readouterr().out
        assert "Logged out" in out

    def test_logout_not_logged_in(self, capsys):
        with patch("dracs_client.config.DRACSRC_PATH") as mock_path:
            mock_path.exists.return_value = False
            with patch(
                "sys.argv",
                ["dracs-client", "-s", "server.example.com", "--logout"],
            ):
                with patch("dracs_client.cli.load_token", return_value=None):
                    with pytest.raises(SystemExit):
                        main()
        err = capsys.readouterr().err
        assert "Not currently logged in" in err


class TestMainRouting:
    def test_admin_routes_to_refresh(self, capsys):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"success": True, "message": "Refreshed"}
        mock_resp.status_code = 200
        mock_resp.content = b'{"success": true}'
        with patch("dracs_client.config.DRACSRC_PATH") as mock_path:
            mock_path.exists.return_value = False
            with patch("dracs_client.cli.get_current_role", return_value="admin"):
                with patch(
                    "sys.argv",
                    [
                        "dracs-client",
                        "-s",
                        "server.example.com",
                        "refresh",
                        "--all",
                    ],
                ):
                    with patch(
                        "dracs_client.commands._api_request",
                        return_value=mock_resp,
                    ):
                        main()
        out = capsys.readouterr().out
        assert "Refreshed" in out

    def test_no_command_shows_help(self, capsys):
        with patch("dracs_client.config.DRACSRC_PATH") as mock_path:
            mock_path.exists.return_value = False
            with patch("dracs_client.cli.get_current_role", return_value=None):
                with patch("sys.argv", ["dracs-client", "-s", "server.example.com"]):
                    with pytest.raises(SystemExit):
                        main()

    def _run_main_admin(self, subcommand_args, mock_resp):
        with (
            patch("dracs_client.config.DRACSRC_PATH") as mock_path,
            patch("dracs_client.cli.get_current_role", return_value="admin"),
            patch(
                "sys.argv",
                ["dracs-client", "-s", "server.example.com"] + subcommand_args,
            ),
            patch("dracs_client.commands._api_request", return_value=mock_resp),
        ):
            mock_path.exists.return_value = False
            main()

    def test_route_fw(self, capsys):
        resp = MagicMock()
        resp.json.return_value = {"success": True, "versions": ["7.0.0"]}
        resp.status_code = 200
        self._run_main_admin(["fw", "--list", "-m", "R660"], resp)
        assert "7.0.0" in capsys.readouterr().out

    def test_route_bios(self, capsys):
        resp = MagicMock()
        resp.json.return_value = {"success": True, "versions": ["2.1.0"]}
        resp.status_code = 200
        self._run_main_admin(["bios", "--list", "-m", "R660"], resp)
        assert "2.1.0" in capsys.readouterr().out

    def test_route_power(self, capsys):
        resp = MagicMock()
        resp.json.return_value = {"success": True, "status": "ON"}
        resp.status_code = 200
        self._run_main_admin(["power", "--status", "-t", "host01"], resp)
        assert "ON" in capsys.readouterr().out

    def test_route_jobs(self, capsys):
        resp = MagicMock()
        resp.json.return_value = {"success": True, "jobs": []}
        resp.status_code = 200
        self._run_main_admin(["jobs", "--list"], resp)
        assert "No active" in capsys.readouterr().out

    def test_route_idracjobs(self, capsys):
        resp = MagicMock()
        resp.json.return_value = {"success": True, "jobs": []}
        resp.status_code = 200
        self._run_main_admin(["idracjobs", "--list", "-t", "host01"], resp)

    def test_route_user(self, capsys):
        resp = MagicMock()
        resp.json.return_value = {"success": True, "users": []}
        resp.status_code = 200
        self._run_main_admin(["user", "--list"], resp)

    def test_tsr_generate_via_main(self, capsys):
        systems_resp = MagicMock()
        systems_resp.json.return_value = [{"name": "host01", "svc_tag": "TAG001"}]
        systems_resp.raise_for_status.return_value = None
        tsr_resp = MagicMock()
        tsr_resp.json.return_value = {"success": True, "message": "TSR initiated"}
        tsr_resp.status_code = 200
        tsr_resp.content = b'{"success": true}'
        with (
            patch("dracs_client.config.DRACSRC_PATH") as mock_path,
            patch("dracs_client.cli.get_current_role", return_value="user"),
            patch(
                "sys.argv",
                [
                    "dracs-client",
                    "-s",
                    "server.example.com",
                    "tsr",
                    "--generate",
                    "-t",
                    "host01",
                ],
            ),
            patch("dracs_client.cli.requests.get", return_value=systems_resp),
            patch("dracs_client.commands._api_request", return_value=tsr_resp),
        ):
            mock_path.exists.return_value = False
            main()

    def test_login_interactive_username_prompt(self, capsys):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "success": True,
            "token": "abc",
            "role": "user",
            "expires_in": 36000,
        }
        with (
            patch("dracs_client.config.DRACSRC_PATH") as mock_path,
            patch(
                "sys.argv",
                ["dracs-client", "-s", "server.example.com", "--login"],
            ),
            patch("dracs_client.cli.load_user_config", return_value=None),
            patch("sys.stdin") as mock_stdin,
            patch("builtins.input", return_value="jsmith"),
            patch("dracs_client.cli.getpass.getpass", return_value="secret"),
            patch("dracs_client.cli.requests.post", return_value=mock_resp),
            patch("dracs_client.cli.save_token"),
        ):
            mock_path.exists.return_value = False
            mock_stdin.isatty.return_value = True
            main()
        assert "logged in" in capsys.readouterr().out.lower()

    def test_login_no_username_exits(self, capsys):
        with (
            patch("dracs_client.config.DRACSRC_PATH") as mock_path,
            patch(
                "sys.argv",
                ["dracs-client", "-s", "server.example.com", "--login"],
            ),
            patch("dracs_client.cli.load_user_config", return_value=None),
            patch("sys.stdin") as mock_stdin,
        ):
            mock_path.exists.return_value = False
            mock_stdin.isatty.return_value = False
            with pytest.raises(SystemExit):
                main()
        assert "username required" in capsys.readouterr().err

    def test_login_ssl_error(self, capsys):
        import requests as req

        with (
            patch("dracs_client.config.DRACSRC_PATH") as mock_path,
            patch(
                "sys.argv",
                [
                    "dracs-client",
                    "-s",
                    "server.example.com",
                    "--login",
                    "--user",
                    "jsmith",
                ],
            ),
            patch("dracs_client.cli.getpass.getpass", return_value="secret"),
            patch(
                "dracs_client.cli.requests.post",
                side_effect=req.exceptions.SSLError("cert"),
            ),
        ):
            mock_path.exists.return_value = False
            with pytest.raises(SystemExit):
                main()

    def test_login_connection_error(self, capsys):
        import requests as req

        with (
            patch("dracs_client.config.DRACSRC_PATH") as mock_path,
            patch(
                "sys.argv",
                [
                    "dracs-client",
                    "-s",
                    "server.example.com",
                    "--login",
                    "--user",
                    "jsmith",
                ],
            ),
            patch("dracs_client.cli.getpass.getpass", return_value="secret"),
            patch(
                "dracs_client.cli.requests.post",
                side_effect=req.exceptions.ConnectionError("refused"),
            ),
        ):
            mock_path.exists.return_value = False
            with pytest.raises(SystemExit):
                main()

    def test_logout_ignores_network_error(self, capsys):
        import requests as req

        with (
            patch("dracs_client.config.DRACSRC_PATH") as mock_path,
            patch(
                "sys.argv",
                ["dracs-client", "-s", "server.example.com", "--logout"],
            ),
            patch(
                "dracs_client.cli.load_token",
                return_value={
                    "token": "abc",
                    "role": "user",
                    "server": "server.example.com",
                },
            ),
            patch(
                "dracs_client.cli.requests.post",
                side_effect=req.exceptions.ConnectionError("down"),
            ),
            patch("dracs_client.cli.clear_token"),
        ):
            mock_path.exists.return_value = False
            main()
        assert "Logged out" in capsys.readouterr().out

import asyncio
import os
import tempfile
from unittest.mock import patch, MagicMock

import pytest
import requests

from dracs.db import create_site, db_initialize, upsert_system


@pytest.fixture
def cli_db():
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


def _run_cli(cli_db, *args):
    from dracs.cli import main

    log_dir = tempfile.mkdtemp()

    async def _run():
        with patch("sys.argv", ["dracs", "-w", cli_db] + list(args)):
            with patch.dict(os.environ, {"DRACS_LOG_DIR": log_dir}):
                await main()

    asyncio.run(_run())


class TestSitesCommand:
    def test_sites_command_lists_default(self, cli_db, capsys):
        _run_cli(cli_db, "sites")

        output = capsys.readouterr().out
        assert "Default" in output
        assert "1" in output

    def test_sites_command_multiple_sites(self, cli_db, capsys):
        create_site("Site2")
        site3 = create_site("Site3")
        upsert_system(
            cli_db,
            "TAG002",
            "server02",
            "R660",
            "7.0.0",
            "2.1.0",
            "Jan 1, 2027",
            1893456000,
            site_id=site3["id"],
        )

        _run_cli(cli_db, "sites")

        output = capsys.readouterr().out
        assert "Default" in output
        assert "Site2" in output
        assert "Site3" in output


class TestSiteArgument:
    def test_site_arg_accepted(self, cli_db, capsys):
        _run_cli(cli_db, "--site", "Default", "sites")

        output = capsys.readouterr().out
        assert "Default" in output

    def test_invalid_site_exits(self, cli_db):
        with pytest.raises(SystemExit) as exc_info:
            _run_cli(cli_db, "--site", "NoSuch", "sites")
        assert exc_info.value.code == 1

    def test_list_filters_by_site(self, cli_db, capsys):
        site2 = create_site("Site2")
        upsert_system(
            cli_db,
            "TAG002",
            "site2host",
            "R660",
            "7.0.0",
            "2.1.0",
            "Jan 1, 2027",
            1893456000,
            site_id=site2["id"],
        )

        _run_cli(cli_db, "--site", "Site2", "li")
        output = capsys.readouterr().out
        assert "site2host" in output
        assert "server01" not in output

    def test_list_default_site_only(self, cli_db, capsys):
        site2 = create_site("Site2")
        upsert_system(
            cli_db,
            "TAG002",
            "site2host",
            "R660",
            "7.0.0",
            "2.1.0",
            "Jan 1, 2027",
            1893456000,
            site_id=site2["id"],
        )

        _run_cli(cli_db, "li")
        output = capsys.readouterr().out
        assert "server01" in output
        assert "site2host" not in output


class TestClientSiteArgument:
    def test_client_accepts_site_arg(self):
        from dracs_client.cli import build_parser

        parser = build_parser("user")
        args = parser.parse_args(["--site", "Site2", "list"])
        assert args.site == "Site2"

    def test_client_sites_command_parsed(self):
        from dracs_client.cli import build_parser

        parser = build_parser("user")
        args = parser.parse_args(["sites"])
        assert args.command == "sites"

    def test_fetch_systems_appends_site(self):
        with patch("dracs_client.cli.requests.get") as mock_get:
            mock_resp = MagicMock()
            mock_resp.json.return_value = []
            mock_resp.raise_for_status = MagicMock()
            mock_get.return_value = mock_resp

            from dracs_client.cli import fetch_systems

            fetch_systems("http://localhost", True, "", site="Site2")
            call_url = mock_get.call_args[0][0]
            assert "site=Site2" in call_url

    def test_fetch_systems_no_site(self):
        with patch("dracs_client.cli.requests.get") as mock_get:
            mock_resp = MagicMock()
            mock_resp.json.return_value = []
            mock_resp.raise_for_status = MagicMock()
            mock_get.return_value = mock_resp

            from dracs_client.cli import fetch_systems

            fetch_systems("http://localhost", True, "")
            call_url = mock_get.call_args[0][0]
            assert "site=" not in call_url

    def test_client_sites_command_runs(self, capsys):
        with patch("dracs_client.cli.requests.get") as mock_get:
            mock_resp = MagicMock()
            mock_resp.json.return_value = {
                "success": True,
                "sites": [
                    {"name": "Default", "host_count": 5},
                    {"name": "Site2", "host_count": 3},
                ],
            }
            mock_get.return_value = mock_resp

            with patch("dracs_client.cli.auth_headers", return_value={}):
                with patch("dracs_client.cli.get_current_role", return_value="user"):
                    with patch(
                        "dracs_client.cli.load_server_config",
                        return_value="testserver",
                    ):
                        with patch(
                            "sys.argv",
                            [
                                "dracs-client",
                                "-s",
                                "testserver",
                                "sites",
                            ],
                        ):
                            from dracs_client.cli import main

                            main()

            output = capsys.readouterr().out
            assert "Default" in output
            assert "Site2" in output

    def test_client_sites_command_with_site_param(self, capsys):
        with patch("dracs_client.cli.requests.get") as mock_get:
            mock_resp = MagicMock()
            mock_resp.json.return_value = {
                "success": True,
                "sites": [{"name": "Default", "host_count": 5}],
            }
            mock_get.return_value = mock_resp

            with patch("dracs_client.cli.auth_headers", return_value={}):
                with patch("dracs_client.cli.get_current_role", return_value="user"):
                    with patch(
                        "dracs_client.cli.load_server_config",
                        return_value="testserver",
                    ):
                        with patch(
                            "sys.argv",
                            [
                                "dracs-client",
                                "-s",
                                "testserver",
                                "--site",
                                "Default",
                                "sites",
                            ],
                        ):
                            from dracs_client.cli import main

                            main()

            call_url = mock_get.call_args[0][0]
            assert "site=Default" in call_url

    def test_client_invalid_site_exits(self, capsys):
        with patch("dracs_client.cli.requests.get") as mock_get:
            mock_resp = MagicMock()
            mock_resp.json.return_value = {
                "success": True,
                "sites": [{"name": "Default", "host_count": 5}],
            }
            mock_get.return_value = mock_resp

            with patch("dracs_client.cli.auth_headers", return_value={}):
                with patch("dracs_client.cli.get_current_role", return_value="user"):
                    with patch(
                        "dracs_client.cli.load_server_config",
                        return_value="testserver",
                    ):
                        with patch(
                            "sys.argv",
                            [
                                "dracs-client",
                                "-s",
                                "testserver",
                                "--site",
                                "NoSuchSite",
                                "list",
                            ],
                        ):
                            from dracs_client.cli import main

                            with pytest.raises(SystemExit) as exc_info:
                                main()
                            assert exc_info.value.code == 1

        output = capsys.readouterr().err
        assert "not found" in output


class TestClientErrorHandling:
    def test_api_request_4xx_exits(self):
        from dracs_client.commands import _api_request

        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_resp.content = b'{"message": "server error"}'
        mock_resp.json.return_value = {"message": "server error"}

        with patch("dracs_client.commands.requests.get", return_value=mock_resp):
            with patch("dracs_client.commands.auth_headers", return_value={}):
                with pytest.raises(SystemExit):
                    _api_request("get", "http://x/api/test", "s", True)

    def test_api_request_4xx_no_json_body(self, capsys):
        from dracs_client.commands import _api_request

        mock_resp = MagicMock()
        mock_resp.status_code = 502
        mock_resp.content = b"Bad Gateway"
        mock_resp.json.side_effect = ValueError("No JSON")

        with patch("dracs_client.commands.requests.get", return_value=mock_resp):
            with patch("dracs_client.commands.auth_headers", return_value={}):
                with pytest.raises(SystemExit):
                    _api_request("get", "http://x/api/test", "s", True)
        assert "HTTP 502" in capsys.readouterr().err

    def test_fw_list_json_decode_error(self, capsys):
        from dracs_client.commands import cmd_fw

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.side_effect = ValueError("No JSON")

        with patch("dracs_client.commands._api_request", return_value=mock_resp):
            args = MagicMock()
            args.list = True
            args.apply = False
            args.model = None
            args.site = None
            with pytest.raises(SystemExit):
                cmd_fw(args, "http://test", True, "server")
        assert "unexpected response" in capsys.readouterr().err

    def test_bios_list_json_decode_error(self, capsys):
        from dracs_client.commands import cmd_bios

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.side_effect = ValueError("No JSON")

        with patch("dracs_client.commands._api_request", return_value=mock_resp):
            args = MagicMock()
            args.list = True
            args.apply = False
            args.model = None
            args.site = None
            with pytest.raises(SystemExit):
                cmd_bios(args, "http://test", True, "server")
        assert "unexpected response" in capsys.readouterr().err

    def test_sites_command_connection_error(self, capsys):
        with patch(
            "dracs_client.cli.requests.get",
            side_effect=requests.exceptions.ConnectionError("refused"),
        ):
            with patch("dracs_client.cli.auth_headers", return_value={}):
                with patch("dracs_client.cli.get_current_role", return_value="user"):
                    with patch(
                        "dracs_client.cli.load_server_config",
                        return_value="testserver",
                    ):
                        with patch(
                            "sys.argv",
                            ["dracs-client", "-s", "testserver", "sites"],
                        ):
                            from dracs_client.cli import main

                            with pytest.raises(SystemExit):
                                main()
        assert "Connection error" in capsys.readouterr().err

    def test_sites_command_json_error(self, capsys):
        mock_resp = MagicMock()
        mock_resp.status_code = 502
        mock_resp.json.side_effect = ValueError("No JSON")

        with patch("dracs_client.cli.requests.get", return_value=mock_resp):
            with patch("dracs_client.cli.auth_headers", return_value={}):
                with patch("dracs_client.cli.get_current_role", return_value="user"):
                    with patch(
                        "dracs_client.cli.load_server_config",
                        return_value="testserver",
                    ):
                        with patch(
                            "sys.argv",
                            ["dracs-client", "-s", "testserver", "sites"],
                        ):
                            from dracs_client.cli import main

                            with pytest.raises(SystemExit):
                                main()
        assert "HTTP 502" in capsys.readouterr().err

    def test_site_validation_connection_error(self, capsys):
        with patch(
            "dracs_client.cli.requests.get",
            side_effect=requests.exceptions.ConnectionError("refused"),
        ):
            with patch("dracs_client.cli.auth_headers", return_value={}):
                with patch("dracs_client.cli.get_current_role", return_value="user"):
                    with patch(
                        "dracs_client.cli.load_server_config",
                        return_value="testserver",
                    ):
                        with patch(
                            "sys.argv",
                            [
                                "dracs-client",
                                "-s",
                                "testserver",
                                "--site",
                                "Site2",
                                "list",
                            ],
                        ):
                            from dracs_client.cli import main

                            with pytest.raises(SystemExit):
                                main()
        assert "not found" in capsys.readouterr().err


class TestClientSiteUrl:
    def test_site_url_appends(self):
        from dracs_client.commands import _site_url

        assert _site_url("http://x/api/test", "Site2") == "http://x/api/test?site=Site2"

    def test_site_url_appends_with_existing_param(self):
        from dracs_client.commands import _site_url

        result = _site_url("http://x/api/test?all=true", "Site2")
        assert result == "http://x/api/test?all=true&site=Site2"

    def test_site_url_none_returns_original(self):
        from dracs_client.commands import _site_url

        assert _site_url("http://x/api/test", None) == "http://x/api/test"


class TestRenderVersionSummary:
    def test_render_with_data(self, capsys):
        from dracs_client.commands import _render_version_summary

        models = [
            {
                "model": "R660",
                "installed": [{"version": "7.0.0", "count": 5}],
                "available": ["7.1.0"],
            }
        ]
        _render_version_summary(models, "Firmware")
        output = capsys.readouterr().out
        assert "R660" in output
        assert "7.0.0" in output
        assert "(5)" in output

    def test_render_empty(self, capsys):
        from dracs_client.commands import _render_version_summary

        _render_version_summary([], "Firmware")
        output = capsys.readouterr().out
        assert "No systems found" in output


class TestFwBiosListEmpty:
    def test_fw_list_no_systems(self, cli_db, capsys):
        from dracs.db import get_default_site_id

        site2 = create_site("EmptySite")
        _run_cli(cli_db, "--site", "EmptySite", "fw", "--list")
        output = capsys.readouterr().out
        assert "No systems found" in output

    def test_bios_list_no_systems(self, cli_db, capsys):
        create_site("EmptySite")
        _run_cli(cli_db, "--site", "EmptySite", "bios", "--list")
        output = capsys.readouterr().out
        assert "No systems found" in output


class TestClientPowerWithSite:
    def test_power_status_passes_site(self):
        from dracs_client.commands import cmd_power

        mock_resp = MagicMock()
        mock_resp.json.return_value = {"success": True, "status": "On"}
        mock_resp.status_code = 200
        with patch("dracs_client.commands._post_json", return_value=mock_resp) as mock:
            args = MagicMock()
            args.status = True
            args.action = None
            args.target = "host01"
            args.site = "Site2"
            cmd_power(args, "http://test", True, "server")
            call_url = mock.call_args[0][0]
            assert "site=Site2" in call_url


class TestClientFwBiosSummary:
    def test_fw_list_no_model_renders_summary(self, capsys):
        from dracs_client.commands import cmd_fw

        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "success": True,
            "models": [
                {
                    "model": "R660",
                    "installed": [{"version": "7.0.0", "count": 3}],
                    "available": ["7.1.0"],
                }
            ],
        }
        mock_resp.status_code = 200
        with patch("dracs_client.commands._api_request", return_value=mock_resp):
            args = MagicMock()
            args.list = True
            args.apply = False
            args.model = None
            args.site = None
            cmd_fw(args, "http://test", True, "server")

        output = capsys.readouterr().out
        assert "R660" in output
        assert "7.0.0" in output

    def test_bios_list_no_model_renders_summary(self, capsys):
        from dracs_client.commands import cmd_bios

        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "success": True,
            "models": [
                {
                    "model": "R660",
                    "installed": [{"version": "2.1.0", "count": 5}],
                    "available": [],
                }
            ],
        }
        mock_resp.status_code = 200
        with patch("dracs_client.commands._api_request", return_value=mock_resp):
            args = MagicMock()
            args.list = True
            args.apply = False
            args.model = None
            args.site = "Site2"
            cmd_bios(args, "http://test", True, "server")

        output = capsys.readouterr().out
        assert "R660" in output
        assert "2.1.0" in output


class TestResolveTargetsWithSite:
    def test_resolve_all_with_site_filter(self, cli_db):
        from dracs.db import get_default_site_id

        site2 = create_site("Site2")
        upsert_system(
            cli_db,
            "TAG003",
            "server03",
            "R660",
            "7.0.0",
            "2.1.0",
            "Jan 1, 2027",
            1893456000,
            site_id=site2["id"],
        )

        from dracs.jobqueue import _resolve_targets

        default_id = get_default_site_id()
        result = _resolve_targets("all", site_id=default_id)
        assert "server01" in result
        assert "server03" not in result

    def test_resolve_model_with_site_filter(self, cli_db):
        site2 = create_site("Site2")
        upsert_system(
            cli_db,
            "TAG003",
            "server03",
            "R660",
            "7.0.0",
            "2.1.0",
            "Jan 1, 2027",
            1893456000,
            site_id=site2["id"],
        )

        from dracs.jobqueue import _resolve_targets

        result = _resolve_targets("model:R660", site_id=site2["id"])
        assert result == ["server03"]

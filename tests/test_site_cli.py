import asyncio
import os
import tempfile
from unittest.mock import patch, MagicMock

import pytest

from dracs.db import create_site, db_initialize, upsert_system


@pytest.fixture
def cli_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    db_initialize(path)
    upsert_system(
        path, "TAG001", "server01", "R660",
        "7.0.0", "2.1.0", "Jan 1, 2027", 1893456000,
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
            cli_db, "TAG002", "server02", "R660",
            "7.0.0", "2.1.0", "Jan 1, 2027", 1893456000,
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

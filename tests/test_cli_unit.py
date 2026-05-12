import logging
import sys
from unittest.mock import patch

import pytest

from dracs.cli import setup_logging, CustomParser


class TestSetupLogging:
    def test_debug_mode(self):
        logging.root.handlers.clear()
        setup_logging(debug=True)
        assert logging.root.level == logging.DEBUG

    def test_verbose_mode(self):
        logging.root.handlers.clear()
        setup_logging(verbose=True)
        assert logging.root.level == logging.INFO

    def test_default_mode(self):
        logging.root.handlers.clear()
        setup_logging()
        assert logging.root.level == logging.WARNING


class TestCustomParser:
    def test_missing_command_error(self, capsys):
        parser = CustomParser(description="test")
        parser.add_subparsers(dest="command", required=True)

        with pytest.raises(SystemExit) as exc_info:
            parser.error("the following arguments are required: command")

        assert exc_info.value.code == 2

    def test_other_error_falls_through(self):
        parser = CustomParser(description="test")

        with pytest.raises(SystemExit) as exc_info:
            parser.error("unrecognized arguments: --foo")

        assert exc_info.value.code == 2


class TestMainCli:
    @patch("dracs.cli.asyncio.run")
    @patch("dracs.cli.load_dotenv")
    def test_main_cli_debug_env(self, mock_dotenv, mock_run):
        with patch.dict("os.environ", {"DEBUG": "true"}):
            import dracs.commands as commands

            from dracs.cli import main_cli

            main_cli()
            assert commands.debug_output is True

    @patch("dracs.cli.asyncio.run")
    @patch("dracs.cli.load_dotenv")
    def test_main_cli_no_debug_env(self, mock_dotenv, mock_run):
        with patch.dict("os.environ", {}, clear=True):
            import dracs.commands as commands

            from dracs.cli import main_cli

            main_cli()
            assert commands.debug_output is False

    @patch(
        "dracs.cli.asyncio.run",
        side_effect=__import__(
            "dracs.exceptions", fromlist=["ValidationError"]
        ).ValidationError("bad input"),
    )
    @patch("dracs.cli.load_dotenv")
    def test_main_cli_validation_error_exits(self, mock_dotenv, mock_run):
        from dracs.cli import main_cli

        with pytest.raises(SystemExit) as exc_info:
            main_cli()
        assert exc_info.value.code == 1

    @patch(
        "dracs.cli.asyncio.run",
        side_effect=__import__(
            "dracs.exceptions", fromlist=["DatabaseError"]
        ).DatabaseError("db fail"),
    )
    @patch("dracs.cli.load_dotenv")
    def test_main_cli_database_error_exits(self, mock_dotenv, mock_run):
        from dracs.cli import main_cli

        with pytest.raises(SystemExit) as exc_info:
            main_cli()
        assert exc_info.value.code == 1

    @patch(
        "dracs.cli.asyncio.run",
        side_effect=__import__("dracs.exceptions", fromlist=["APIError"]).APIError(
            "api fail"
        ),
    )
    @patch("dracs.cli.load_dotenv")
    def test_main_cli_api_error_exits(self, mock_dotenv, mock_run):
        from dracs.cli import main_cli

        with pytest.raises(SystemExit) as exc_info:
            main_cli()
        assert exc_info.value.code == 1

    @patch(
        "dracs.cli.asyncio.run",
        side_effect=__import__("dracs.exceptions", fromlist=["SNMPError"]).SNMPError(
            "snmp fail"
        ),
    )
    @patch("dracs.cli.load_dotenv")
    def test_main_cli_snmp_error_exits(self, mock_dotenv, mock_run):
        from dracs.cli import main_cli

        with pytest.raises(SystemExit) as exc_info:
            main_cli()
        assert exc_info.value.code == 1

    @patch(
        "dracs.cli.asyncio.run",
        side_effect=__import__("dracs.exceptions", fromlist=["DracsError"]).DracsError(
            "generic"
        ),
    )
    @patch("dracs.cli.load_dotenv")
    def test_main_cli_dracs_error_exits(self, mock_dotenv, mock_run):
        from dracs.cli import main_cli

        with pytest.raises(SystemExit) as exc_info:
            main_cli()
        assert exc_info.value.code == 1

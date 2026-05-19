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
    @patch("dracs.config.load_config")
    def test_main_cli_debug_env(self, mock_dotenv, mock_run):
        with patch.dict("os.environ", {"DEBUG": "true"}):
            import dracs.commands as commands

            from dracs.cli import main_cli

            main_cli()
            assert commands.debug_output is True

    @patch("dracs.cli.asyncio.run")
    @patch("dracs.config.load_config")
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
    @patch("dracs.config.load_config")
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
    @patch("dracs.config.load_config")
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
    @patch("dracs.config.load_config")
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
    @patch("dracs.config.load_config")
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
    @patch("dracs.config.load_config")
    def test_main_cli_dracs_error_exits(self, mock_dotenv, mock_run):
        from dracs.cli import main_cli

        with pytest.raises(SystemExit) as exc_info:
            main_cli()
        assert exc_info.value.code == 1


class TestTsrSubparser:
    def _build_tsr_parser(self):
        parser = CustomParser(description="test")
        subparsers = parser.add_subparsers(dest="command", required=True)
        parser_tsr = subparsers.add_parser("tsr")
        parser_tsr.add_argument("-t", "--target", required=True)
        tsr_action = parser_tsr.add_mutually_exclusive_group(required=True)
        tsr_action.add_argument("--list", action="store_true")
        tsr_action.add_argument("--download", action="store_true")
        tsr_action.add_argument("--generate", action="store_true")
        tsr_action.add_argument("--status", action="store_true")
        parser_tsr.add_argument("--last", nargs="?", const=1, type=int, default=None)
        return parser

    def test_tsr_list(self):
        args = self._build_tsr_parser().parse_args(["tsr", "--list", "-t", "host1"])
        assert args.command == "tsr"
        assert args.list is True
        assert args.target == "host1"
        assert args.last is None

    def test_tsr_generate(self):
        args = self._build_tsr_parser().parse_args(["tsr", "--generate", "-t", "host1"])
        assert args.generate is True

    def test_tsr_status(self):
        args = self._build_tsr_parser().parse_args(["tsr", "--status", "-t", "host1"])
        assert args.status is True

    def test_tsr_download(self):
        args = self._build_tsr_parser().parse_args(["tsr", "--download", "-t", "host1"])
        assert args.download is True

    def test_tsr_mutually_exclusive(self):
        with pytest.raises(SystemExit):
            self._build_tsr_parser().parse_args(
                ["tsr", "--list", "--generate", "-t", "host1"]
            )

    def test_tsr_last_with_value(self):
        args = self._build_tsr_parser().parse_args(
            ["tsr", "--list", "-t", "host1", "--last", "3"]
        )
        assert args.last == 3

    def test_tsr_last_no_value(self):
        args = self._build_tsr_parser().parse_args(
            ["tsr", "--list", "-t", "host1", "--last"]
        )
        assert args.last == 1

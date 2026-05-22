import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from dracs_client.config import load_server_config, load_user_config


class TestLoadServerConfig:
    def test_override_takes_priority(self):
        result = load_server_config("myserver.example.com")
        assert result == "myserver.example.com"

    def test_override_strips_whitespace(self):
        result = load_server_config("  myserver.example.com  ")
        assert result == "myserver.example.com"

    def test_reads_dracsrc(self, tmp_path):
        dracsrc = tmp_path / ".dracsrc"
        dracsrc.write_text("dracs_server: dracs.lab.example.com\n")
        with patch("dracs_client.config.DRACSRC_PATH", dracsrc):
            result = load_server_config()
        assert result == "dracs.lab.example.com"

    def test_dracsrc_ignores_comments(self, tmp_path):
        dracsrc = tmp_path / ".dracsrc"
        dracsrc.write_text("# comment\ndracs_server: dracs.example.com\n")
        with patch("dracs_client.config.DRACSRC_PATH", dracsrc):
            result = load_server_config()
        assert result == "dracs.example.com"

    def test_dracsrc_ignores_empty_lines(self, tmp_path):
        dracsrc = tmp_path / ".dracsrc"
        dracsrc.write_text("\n\ndracs_server: dracs.example.com\n\n")
        with patch("dracs_client.config.DRACSRC_PATH", dracsrc):
            result = load_server_config()
        assert result == "dracs.example.com"

    def test_dracsrc_empty_value_falls_through(self, tmp_path):
        dracsrc = tmp_path / ".dracsrc"
        dracsrc.write_text("dracs_server:\n")
        with patch("dracs_client.config.DRACSRC_PATH", dracsrc):
            with patch("sys.stdin") as mock_stdin:
                mock_stdin.isatty.return_value = False
                with pytest.raises(SystemExit):
                    load_server_config()

    def test_missing_file_no_tty_exits(self, tmp_path):
        dracsrc = tmp_path / ".dracsrc_nonexistent"
        with patch("dracs_client.config.DRACSRC_PATH", dracsrc):
            with patch("sys.stdin") as mock_stdin:
                mock_stdin.isatty.return_value = False
                with pytest.raises(SystemExit):
                    load_server_config()

    def test_interactive_prompt(self, tmp_path):
        dracsrc = tmp_path / ".dracsrc_nonexistent"
        with patch("dracs_client.config.DRACSRC_PATH", dracsrc):
            with patch("sys.stdin") as mock_stdin:
                mock_stdin.isatty.return_value = True
                with patch("builtins.input", return_value="prompted.example.com"):
                    result = load_server_config()
        assert result == "prompted.example.com"

    def test_interactive_empty_input_exits(self, tmp_path):
        dracsrc = tmp_path / ".dracsrc_nonexistent"
        with patch("dracs_client.config.DRACSRC_PATH", dracsrc):
            with patch("sys.stdin") as mock_stdin:
                mock_stdin.isatty.return_value = True
                with patch("builtins.input", return_value=""):
                    with pytest.raises(SystemExit):
                        load_server_config()

    def test_eof_during_prompt(self, tmp_path):
        dracsrc = tmp_path / ".dracsrc_nonexistent"
        with patch("dracs_client.config.DRACSRC_PATH", dracsrc):
            with patch("sys.stdin") as mock_stdin:
                mock_stdin.isatty.return_value = True
                with patch("builtins.input", side_effect=EOFError):
                    with pytest.raises(SystemExit):
                        load_server_config()


class TestLoadUserConfig:
    def test_override_takes_priority(self):
        assert load_user_config("jsmith") == "jsmith"

    def test_override_strips_whitespace(self):
        assert load_user_config("  jsmith  ") == "jsmith"

    def test_reads_dracsrc(self, tmp_path):
        dracsrc = tmp_path / ".dracsrc"
        dracsrc.write_text("dracs_server: server\ndracs_user: jsmith\n")
        with patch("dracs_client.config.DRACSRC_PATH", dracsrc):
            result = load_user_config()
        assert result == "jsmith"

    def test_dracsrc_ignores_comments(self, tmp_path):
        dracsrc = tmp_path / ".dracsrc"
        dracsrc.write_text("# comment\ndracs_user: jsmith\n")
        with patch("dracs_client.config.DRACSRC_PATH", dracsrc):
            assert load_user_config() == "jsmith"

    def test_dracsrc_empty_value(self, tmp_path):
        dracsrc = tmp_path / ".dracsrc"
        dracsrc.write_text("dracs_user:\n")
        with patch("dracs_client.config.DRACSRC_PATH", dracsrc):
            assert load_user_config() is None

    def test_no_file_returns_none(self, tmp_path):
        dracsrc = tmp_path / ".dracsrc_nonexistent"
        with patch("dracs_client.config.DRACSRC_PATH", dracsrc):
            assert load_user_config() is None

    def test_no_override_no_file(self):
        with patch("dracs_client.config.DRACSRC_PATH") as mock_path:
            mock_path.exists.return_value = False
            assert load_user_config() is None

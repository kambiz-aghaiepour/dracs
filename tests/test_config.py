"""Tests for dracs.config module."""

import os
from pathlib import Path
from unittest.mock import patch

from dracs.config import load_config, SYSTEM_CONFIG


class TestLoadConfig:
    def test_loads_system_config(self, tmp_path, monkeypatch):
        conf = tmp_path / "dracs.conf"
        conf.write_text("MY_TEST_VAR=from_system\n")
        monkeypatch.setattr("dracs.config.SYSTEM_CONFIG", conf)
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("MY_TEST_VAR", raising=False)

        load_config()

        assert os.environ.get("MY_TEST_VAR") == "from_system"
        monkeypatch.delenv("MY_TEST_VAR", raising=False)

    def test_cwd_env_overrides_system_config(self, tmp_path, monkeypatch):
        conf = tmp_path / "dracs.conf"
        conf.write_text("OVERRIDE_VAR=system_value\n")
        env_file = tmp_path / ".env"
        env_file.write_text("OVERRIDE_VAR=local_value\n")
        monkeypatch.setattr("dracs.config.SYSTEM_CONFIG", conf)
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("OVERRIDE_VAR", raising=False)

        load_config()

        assert os.environ.get("OVERRIDE_VAR") == "local_value"
        monkeypatch.delenv("OVERRIDE_VAR", raising=False)

    def test_warning_when_system_config_missing_as_dracs_user(
        self, tmp_path, monkeypatch, capsys
    ):
        monkeypatch.setattr("dracs.config.SYSTEM_CONFIG", tmp_path / "nonexistent.conf")
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr("dracs.config.getpass.getuser", lambda: "dracs")

        load_config()

        captured = capsys.readouterr()
        assert "Warning: could not read configuration" in captured.err

    def test_no_warning_when_system_config_missing_as_other_user(
        self, tmp_path, monkeypatch, capsys
    ):
        monkeypatch.setattr("dracs.config.SYSTEM_CONFIG", tmp_path / "nonexistent.conf")
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr("dracs.config.getpass.getuser", lambda: "developer")

        load_config()

        captured = capsys.readouterr()
        assert "Warning" not in captured.err

    def test_no_error_when_cwd_env_missing(self, tmp_path, monkeypatch):
        conf = tmp_path / "dracs.conf"
        conf.write_text("CONF_ONLY_VAR=works\n")
        monkeypatch.setattr("dracs.config.SYSTEM_CONFIG", conf)
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("CONF_ONLY_VAR", raising=False)

        load_config()

        assert os.environ.get("CONF_ONLY_VAR") == "works"
        monkeypatch.delenv("CONF_ONLY_VAR", raising=False)

    def test_both_missing_no_crash(self, tmp_path, monkeypatch):
        monkeypatch.setattr("dracs.config.SYSTEM_CONFIG", tmp_path / "nonexistent.conf")
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr("dracs.config.getpass.getuser", lambda: "developer")

        load_config()

"""Tests for webapp_runner module."""

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from dracs.webapp_runner import (
    REQUIRED_ENV_VARS,
    OPTIONAL_ENV_DEFAULTS,
    get_gunicorn_conf_path,
    validate_env,
    apply_optional_defaults,
)


class TestGetGunicornConfPath:
    def test_returns_path_in_package_dir(self):
        conf = get_gunicorn_conf_path()
        assert conf.name == "gunicorn.conf.py"
        assert conf.parent == Path(__file__).resolve().parent.parent / "src" / "dracs"

    def test_bundled_config_exists(self):
        assert get_gunicorn_conf_path().exists()


class TestValidateEnv:
    def test_all_present(self):
        env = {var: "value" for var in REQUIRED_ENV_VARS}
        with patch.dict(os.environ, env, clear=True):
            assert validate_env() == []

    def test_all_missing(self):
        with patch.dict(os.environ, {}, clear=True):
            missing = validate_env()
            assert missing == list(REQUIRED_ENV_VARS)

    def test_single_missing(self):
        env = {var: "value" for var in REQUIRED_ENV_VARS}
        del env["FLASK_SECRET_KEY"]
        with patch.dict(os.environ, env, clear=True):
            missing = validate_env()
            assert missing == ["FLASK_SECRET_KEY"]

    def test_empty_value_counts_as_missing(self):
        env = {var: "value" for var in REQUIRED_ENV_VARS}
        env["TOKEN_URL"] = ""
        with patch.dict(os.environ, env, clear=True):
            missing = validate_env()
            assert missing == ["TOKEN_URL"]


class TestApplyOptionalDefaults:
    def test_sets_defaults_when_unset(self):
        with patch.dict(os.environ, {}, clear=True):
            apply_optional_defaults()
            for var, default in OPTIONAL_ENV_DEFAULTS.items():
                assert os.environ[var] == default

    def test_preserves_existing_values(self):
        with patch.dict(os.environ, {"DRACS_DB": "/custom/path.db"}, clear=True):
            apply_optional_defaults()
            assert os.environ["DRACS_DB"] == "/custom/path.db"

    def test_does_not_override_nonempty(self):
        overrides = {var: "custom" for var in OPTIONAL_ENV_DEFAULTS}
        with patch.dict(os.environ, overrides, clear=True):
            apply_optional_defaults()
            for var in OPTIONAL_ENV_DEFAULTS:
                assert os.environ[var] == "custom"


class TestRequiredEnvVarsList:
    def test_contains_expected_vars(self):
        expected = [
            "CLIENT_ID",
            "CLIENT_SECRET",
            "TOKEN_URL",
            "FLASK_SECRET_KEY",
            "WEBADMIN_USER",
            "WEBADMIN_PASSWORD",
            "DRACS_DNS_STRING",
            "DRACS_DNS_MODE",
        ]
        assert REQUIRED_ENV_VARS == expected

    def test_optional_defaults_contains_expected_vars(self):
        expected_keys = [
            "DRACS_DB",
            "REFRESH_FREQUENCY",
            "HIGHLIGHT_EXPIRED",
            "HIGHLIGHT_EXPIRING",
            "DEFAULT_PAGE_SIZE",
            "HIGHLIGHT_FIRMWARE",
            "HIGHLIGHT_BIOS",
            "SNMP_COMMUNITY",
            "DEBUG",
            "DRACS_BIND",
            "DRACS_LOG_DIR",
            "VNC_ENABLE",
            "VNC_TIMEOUT",
            "VNC_MAX_SESSIONS",
            "VNC_WEBSOCKIFY_PORT",
            "VNC_CONSOLE_SIZE",
        ]
        assert list(OPTIONAL_ENV_DEFAULTS.keys()) == expected_keys

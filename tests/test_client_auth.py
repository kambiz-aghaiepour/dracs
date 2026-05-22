"""Tests for the dracs-client authentication module."""

import json
import os
from unittest.mock import patch

import pytest

from dracs_client.auth import (
    TOKEN_PATH,
    auth_headers,
    clear_token,
    get_current_role,
    load_token,
    save_token,
)


@pytest.fixture(autouse=True)
def _clean_token(tmp_path):
    token_dir = tmp_path / ".config" / "dracs"
    token_path = token_dir / "login_token"
    with patch("dracs_client.auth.TOKEN_DIR", token_dir):
        with patch("dracs_client.auth.TOKEN_PATH", token_path):
            yield
            if token_path.exists():
                token_path.unlink()


class TestSaveAndLoadToken:
    def test_save_and_load_roundtrip(self):
        save_token("mytoken", "user", "dracs.example.com")
        result = load_token("dracs.example.com")
        assert result is not None
        assert result["token"] == "mytoken"
        assert result["role"] == "user"
        assert result["server"] == "dracs.example.com"

    def test_load_wrong_server(self):
        save_token("mytoken", "user", "dracs.example.com")
        result = load_token("other.server.com")
        assert result is None

    def test_load_no_file(self):
        assert load_token("dracs.example.com") is None

    def test_load_corrupt_file(self):
        from dracs_client.auth import TOKEN_DIR, TOKEN_PATH

        TOKEN_DIR.mkdir(parents=True, exist_ok=True)
        TOKEN_PATH.write_text("not json")
        assert load_token("dracs.example.com") is None


class TestClearToken:
    def test_clear_existing(self):
        save_token("mytoken", "user", "server")
        clear_token()
        assert load_token("server") is None

    def test_clear_nonexistent(self):
        clear_token()


class TestAuthHeaders:
    def test_with_token(self):
        save_token("mytoken", "user", "server")
        headers = auth_headers("server")
        assert headers == {"Authorization": "Bearer mytoken"}

    def test_without_token(self):
        assert auth_headers("server") == {}

    def test_wrong_server(self):
        save_token("mytoken", "user", "server")
        assert auth_headers("other") == {}


class TestGetCurrentRole:
    def test_with_token(self):
        save_token("mytoken", "admin", "server")
        assert get_current_role("server") == "admin"

    def test_without_token(self):
        assert get_current_role("server") is None

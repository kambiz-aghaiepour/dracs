"""Tests for the API token management module."""

import os
import tempfile
import time
from unittest.mock import patch

import pytest

from dracs.db import db_initialize
from dracs.tokens import (
    cleanup_expired_tokens,
    generate_token,
    invalidate_all_tokens,
    invalidate_token,
    refresh_token,
    validate_token,
)


@pytest.fixture
def token_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    db_initialize(path)
    yield path
    if os.path.exists(path):
        os.unlink(path)


class TestGenerateToken:
    def test_generates_valid_token(self, token_db):
        result = generate_token("jsmith", "user", 3600)
        assert result["token"]
        assert len(result["token"]) == 64
        assert result["role"] == "user"
        assert result["expires_in"] == 3600

    def test_uses_default_expiry(self, token_db):
        with patch.dict(os.environ, {"DRACS_TOKEN_EXPIRY": "7200"}):
            result = generate_token("jsmith", "user")
            assert result["expires_in"] == 7200

    def test_generates_unique_tokens(self, token_db):
        t1 = generate_token("jsmith", "user", 3600)
        t2 = generate_token("jsmith", "user", 3600)
        assert t1["token"] != t2["token"]


class TestValidateToken:
    def test_valid_token(self, token_db):
        result = generate_token("jsmith", "user", 3600)
        validated = validate_token(result["token"])
        assert validated == ("jsmith", "user")

    def test_nonexistent_token(self, token_db):
        assert validate_token("nonexistent") is None

    def test_expired_token(self, token_db):
        result = generate_token("jsmith", "user", 1)
        time.sleep(1.1)
        assert validate_token(result["token"]) is None

    def test_expired_token_is_deleted(self, token_db):
        result = generate_token("jsmith", "user", 1)
        time.sleep(1.1)
        validate_token(result["token"])
        assert validate_token(result["token"]) is None


class TestRefreshToken:
    def test_refresh_valid_token(self, token_db):
        result = generate_token("jsmith", "user", 3600)
        assert refresh_token(result["token"]) is True

    def test_refresh_nonexistent_token(self, token_db):
        assert refresh_token("nonexistent") is False

    def test_refresh_expired_token(self, token_db):
        result = generate_token("jsmith", "user", 1)
        time.sleep(1.1)
        assert refresh_token(result["token"]) is False

    def test_refresh_extends_validity(self, token_db):
        result = generate_token("jsmith", "user", 2)
        time.sleep(1)
        refresh_token(result["token"])
        time.sleep(1)
        assert validate_token(result["token"]) is not None


class TestInvalidateToken:
    def test_invalidate_existing(self, token_db):
        result = generate_token("jsmith", "user", 3600)
        assert invalidate_token(result["token"]) is True
        assert validate_token(result["token"]) is None

    def test_invalidate_nonexistent(self, token_db):
        assert invalidate_token("nonexistent") is False


class TestInvalidateAllTokens:
    def test_invalidate_all_for_user(self, token_db):
        generate_token("jsmith", "user", 3600)
        generate_token("jsmith", "user", 3600)
        generate_token("other", "admin", 3600)
        count = invalidate_all_tokens("jsmith")
        assert count == 2

    def test_invalidate_none(self, token_db):
        assert invalidate_all_tokens("nobody") == 0


class TestCleanupExpiredTokens:
    def test_cleanup_expired(self, token_db):
        generate_token("old", "user", 1)
        generate_token("fresh", "user", 3600)
        time.sleep(1.1)
        cleaned = cleanup_expired_tokens()
        assert cleaned == 1
        assert (
            validate_token(generate_token("fresh2", "user", 3600)["token"]) is not None
        )

    def test_cleanup_none_expired(self, token_db):
        generate_token("fresh", "user", 3600)
        assert cleanup_expired_tokens() == 0


class TestMultipleSimultaneousTokens:
    def test_user_can_have_multiple_tokens(self, token_db):
        t1 = generate_token("jsmith", "user", 3600)
        t2 = generate_token("jsmith", "user", 3600)
        assert validate_token(t1["token"]) == ("jsmith", "user")
        assert validate_token(t2["token"]) == ("jsmith", "user")

    def test_invalidating_one_keeps_other(self, token_db):
        t1 = generate_token("jsmith", "user", 3600)
        t2 = generate_token("jsmith", "user", 3600)
        invalidate_token(t1["token"])
        assert validate_token(t1["token"]) is None
        assert validate_token(t2["token"]) == ("jsmith", "user")

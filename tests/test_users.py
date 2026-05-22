"""Tests for the user management module."""

import os
import tempfile
from unittest.mock import patch

import pytest

from dracs.db import db_initialize, User
from dracs.exceptions import ValidationError
from dracs.users import (
    authenticate,
    create_user,
    delete_user,
    get_user,
    list_users,
    update_user_password,
    update_user_role,
    validate_username,
)


@pytest.fixture
def user_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    db_initialize(path)
    yield path
    if os.path.exists(path):
        os.unlink(path)


class TestValidateUsername:
    def test_valid_usernames(self):
        assert validate_username("admin") is True
        assert validate_username("jsmith") is True
        assert validate_username("user-01") is True
        assert validate_username("user_name") is True
        assert validate_username("abc") is True
        assert validate_username("a" * 32) is True

    def test_invalid_usernames(self):
        assert validate_username("") is False
        assert validate_username("ab") is False
        assert validate_username("a" * 33) is False
        assert validate_username("user name") is False
        assert validate_username("user@name") is False
        assert validate_username("user.name") is False


class TestCreateUser:
    def test_create_user_success(self, user_db):
        user = create_user("jsmith", "password123", "user", created_by="admin")
        assert user.username == "jsmith"
        assert user.role == "user"
        assert user.created_by == "admin"
        assert user.created_at is not None
        assert user.password_hash != "password123"

    def test_create_admin_user(self, user_db):
        user = create_user("newadmin", "secret", "admin")
        assert user.role == "admin"

    def test_create_user_invalid_username(self, user_db):
        with pytest.raises(ValidationError, match="Invalid username"):
            create_user("ab", "password", "user")

    def test_create_user_invalid_role(self, user_db):
        with pytest.raises(ValidationError, match="Invalid role"):
            create_user("jsmith", "password", "superuser")

    def test_create_user_empty_password(self, user_db):
        with pytest.raises(ValidationError, match="Password cannot be empty"):
            create_user("jsmith", "", "user")

    def test_create_user_duplicate(self, user_db):
        create_user("jsmith", "password", "user")
        with pytest.raises(ValidationError, match="already exists"):
            create_user("jsmith", "other", "user")

    def test_create_user_superadmin_username_rejected(self, user_db):
        with patch.dict(os.environ, {"WEBADMIN_USER": "admin"}):
            with pytest.raises(ValidationError, match="reserved for superadmin"):
                create_user("admin", "password", "user")

    def test_create_user_custom_superadmin_username_rejected(self, user_db):
        with patch.dict(os.environ, {"WEBADMIN_USER": "boss"}):
            with pytest.raises(ValidationError, match="reserved for superadmin"):
                create_user("boss", "password", "admin")


class TestAuthenticate:
    def test_authenticate_db_user(self, user_db):
        create_user("jsmith", "secret123", "user")
        result = authenticate("jsmith", "secret123")
        assert result == ("jsmith", "user")

    def test_authenticate_db_user_wrong_password(self, user_db):
        create_user("jsmith", "secret123", "user")
        result = authenticate("jsmith", "wrongpassword")
        assert result is None

    def test_authenticate_nonexistent_user(self, user_db):
        result = authenticate("nobody", "password")
        assert result is None

    def test_authenticate_env_var_fallback(self, user_db):
        with patch.dict(
            os.environ,
            {"WEBADMIN_USER": "superadmin", "WEBADMIN_PASSWORD": "superpass"},
        ):
            result = authenticate("superadmin", "superpass")
            assert result == ("superadmin", "admin")

    def test_authenticate_env_var_wrong_password(self, user_db):
        with patch.dict(
            os.environ,
            {"WEBADMIN_USER": "admin", "WEBADMIN_PASSWORD": "secret"},
        ):
            result = authenticate("admin", "wrongpassword")
            assert result is None

    def test_authenticate_env_var_defaults(self, user_db):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("WEBADMIN_USER", None)
            os.environ.pop("WEBADMIN_PASSWORD", None)
            result = authenticate("admin", "admin")
            assert result == ("admin", "admin")


class TestDeleteUser:
    def test_delete_user_success(self, user_db):
        create_user("jsmith", "password", "user")
        assert delete_user("jsmith") is True
        assert get_user("jsmith") is None

    def test_delete_user_nonexistent(self, user_db):
        assert delete_user("nobody") is False

    def test_delete_superadmin_rejected(self, user_db):
        with patch.dict(os.environ, {"WEBADMIN_USER": "admin"}):
            with pytest.raises(ValidationError, match="Cannot delete the superadmin"):
                delete_user("admin")


class TestListUsers:
    def test_list_users_empty(self, user_db):
        result = list_users()
        assert result == []

    def test_list_users_multiple(self, user_db):
        create_user("alice", "pass1", "admin")
        create_user("bob", "pass2", "user")
        result = list_users()
        assert len(result) == 2
        assert result[0]["username"] == "alice"
        assert result[1]["username"] == "bob"
        for u in result:
            assert "password_hash" not in u
            assert "id" in u
            assert "role" in u
            assert "created_at" in u


class TestUpdateUserPassword:
    def test_update_password_success(self, user_db):
        create_user("jsmith", "oldpass", "user")
        assert update_user_password("jsmith", "newpass") is True
        assert authenticate("jsmith", "newpass") == ("jsmith", "user")
        assert authenticate("jsmith", "oldpass") is None

    def test_update_password_nonexistent(self, user_db):
        assert update_user_password("nobody", "newpass") is False

    def test_update_password_empty(self, user_db):
        create_user("jsmith", "oldpass", "user")
        with pytest.raises(ValidationError, match="Password cannot be empty"):
            update_user_password("jsmith", "")

    def test_update_superadmin_password_rejected(self, user_db):
        with patch.dict(os.environ, {"WEBADMIN_USER": "admin"}):
            with pytest.raises(ValidationError, match="Cannot modify superadmin"):
                update_user_password("admin", "newpass")


class TestUpdateUserRole:
    def test_update_role_success(self, user_db):
        create_user("jsmith", "pass", "user")
        assert update_user_role("jsmith", "admin") is True
        result = authenticate("jsmith", "pass")
        assert result == ("jsmith", "admin")

    def test_update_role_nonexistent(self, user_db):
        assert update_user_role("nobody", "admin") is False

    def test_update_role_invalid(self, user_db):
        create_user("jsmith", "pass", "user")
        with pytest.raises(ValidationError, match="Invalid role"):
            update_user_role("jsmith", "superuser")

    def test_update_superadmin_role_rejected(self, user_db):
        with patch.dict(os.environ, {"WEBADMIN_USER": "admin"}):
            with pytest.raises(ValidationError, match="Cannot modify superadmin"):
                update_user_role("admin", "user")


class TestGetUser:
    def test_get_user_exists(self, user_db):
        create_user("jsmith", "pass", "user")
        user = get_user("jsmith")
        assert user is not None
        assert user.username == "jsmith"

    def test_get_user_nonexistent(self, user_db):
        assert get_user("nobody") is None

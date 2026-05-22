"""Tests for the CLI user management subcommand."""

import os
import tempfile
from unittest.mock import patch, MagicMock

import pytest

from dracs.db import db_initialize
from dracs.users import create_user, list_users


@pytest.fixture
def user_cli_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    db_initialize(path)
    yield path
    if os.path.exists(path):
        os.unlink(path)


@pytest.fixture
def run_cli(user_cli_db):
    import asyncio
    from dracs.cli import main

    log_dir = tempfile.mkdtemp()

    async def _run(*args):
        with patch(
            "sys.argv",
            ["dracs", "-w", user_cli_db] + list(args),
        ):
            with patch.dict(os.environ, {"DRACS_LOG_DIR": log_dir}):
                await main()

    def runner(*args):
        asyncio.run(_run(*args))

    return runner


class TestUserAdd:
    def test_add_user(self, run_cli, user_cli_db, capsys):
        run_cli(
            "user",
            "--add",
            "--username",
            "jsmith",
            "--role",
            "user",
            "--password",
            "secret",
        )
        captured = capsys.readouterr()
        assert "created" in captured.out.lower()
        users = list_users()
        assert len(users) == 1
        assert users[0]["username"] == "jsmith"
        assert users[0]["role"] == "user"

    def test_add_admin_user(self, run_cli, user_cli_db, capsys):
        run_cli(
            "user",
            "--add",
            "--username",
            "boss",
            "--role",
            "admin",
            "--password",
            "secret",
        )
        users = list_users()
        assert users[0]["role"] == "admin"

    def test_add_user_prompted_password(self, run_cli, user_cli_db, capsys):
        with patch("dracs.cli.getpass.getpass", side_effect=["mypass", "mypass"]):
            run_cli("user", "--add", "--username", "prompted", "--role", "user")
        users = list_users()
        assert len(users) == 1
        assert users[0]["username"] == "prompted"

    def test_add_user_password_mismatch(self, run_cli, user_cli_db):
        with patch("dracs.cli.getpass.getpass", side_effect=["pass1", "pass2"]):
            with pytest.raises(SystemExit):
                run_cli("user", "--add", "--username", "fail", "--role", "user")

    def test_add_user_missing_username(self, run_cli, user_cli_db):
        with pytest.raises(SystemExit):
            run_cli("user", "--add", "--role", "user", "--password", "pass")

    def test_add_user_missing_role(self, run_cli, user_cli_db):
        with pytest.raises(SystemExit):
            run_cli("user", "--add", "--username", "norole", "--password", "pass")


class TestUserRemove:
    def test_remove_user(self, run_cli, user_cli_db, capsys):
        run_cli(
            "user",
            "--add",
            "--username",
            "todelete",
            "--role",
            "user",
            "--password",
            "pass",
        )
        run_cli("user", "--remove", "--username", "todelete")
        captured = capsys.readouterr()
        assert "deleted" in captured.out.lower()
        assert list_users() == []

    def test_remove_nonexistent(self, run_cli, user_cli_db):
        with pytest.raises(SystemExit):
            run_cli("user", "--remove", "--username", "nobody")

    def test_remove_missing_username(self, run_cli, user_cli_db):
        with pytest.raises(SystemExit):
            run_cli("user", "--remove")


class TestUserList:
    def test_list_empty(self, run_cli, user_cli_db, capsys):
        run_cli("user", "--list")
        captured = capsys.readouterr()
        assert "no users" in captured.out.lower()

    def test_list_with_users(self, run_cli, user_cli_db, capsys):
        run_cli(
            "user",
            "--add",
            "--username",
            "alice",
            "--role",
            "admin",
            "--password",
            "pass",
        )
        run_cli(
            "user", "--add", "--username", "bob", "--role", "user", "--password", "pass"
        )
        capsys.readouterr()
        run_cli("user", "--list")
        captured = capsys.readouterr()
        assert "alice" in captured.out
        assert "bob" in captured.out


class TestUserUpdate:
    def test_update_role(self, run_cli, user_cli_db, capsys):
        run_cli(
            "user",
            "--add",
            "--username",
            "changeme",
            "--role",
            "user",
            "--password",
            "pass",
        )
        run_cli("user", "--update", "--username", "changeme", "--role", "admin")
        captured = capsys.readouterr()
        assert "updated" in captured.out.lower()
        users = list_users()
        assert users[0]["role"] == "admin"

    def test_update_password(self, run_cli, user_cli_db, capsys):
        run_cli(
            "user",
            "--add",
            "--username",
            "passchange",
            "--role",
            "user",
            "--password",
            "old",
        )
        run_cli("user", "--update", "--username", "passchange", "--password", "new")
        captured = capsys.readouterr()
        assert "updated" in captured.out.lower()

    def test_update_prompted_password(self, run_cli, user_cli_db, capsys):
        run_cli(
            "user",
            "--add",
            "--username",
            "promptpass",
            "--role",
            "user",
            "--password",
            "old",
        )
        with patch("dracs.cli.getpass.getpass", side_effect=["newpass", "newpass"]):
            run_cli("user", "--update", "--username", "promptpass")
        captured = capsys.readouterr()
        assert "updated" in captured.out.lower()

    def test_update_password_mismatch(self, run_cli, user_cli_db):
        run_cli(
            "user",
            "--add",
            "--username",
            "mismatch",
            "--role",
            "user",
            "--password",
            "old",
        )
        with patch("dracs.cli.getpass.getpass", side_effect=["pass1", "pass2"]):
            with pytest.raises(SystemExit):
                run_cli("user", "--update", "--username", "mismatch")

    def test_update_missing_username(self, run_cli, user_cli_db):
        with pytest.raises(SystemExit):
            run_cli("user", "--update")

    def test_user_alias(self, run_cli, user_cli_db, capsys):
        run_cli(
            "u",
            "--add",
            "--username",
            "aliasuser",
            "--role",
            "user",
            "--password",
            "pass",
        )
        captured = capsys.readouterr()
        assert "created" in captured.out.lower()

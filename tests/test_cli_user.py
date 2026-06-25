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


def _add_user(run_cli, username, role, password="secret"):
    with patch("dracs.cli.getpass.getpass", side_effect=[password, password]):
        run_cli("user", "--add", "--username", username, "--role", role)


class TestUserAdd:
    def test_add_user(self, run_cli, user_cli_db, capsys):
        _add_user(run_cli, "jsmith", "user")
        captured = capsys.readouterr()
        assert "created" in captured.out.lower()
        users = list_users()
        assert len(users) == 1
        assert users[0]["username"] == "jsmith"
        assert users[0]["role"] == "user"

    def test_add_user_with_password_flag(self, run_cli, user_cli_db, capsys):
        run_cli(
            "user",
            "--add",
            "--username",
            "flaguser",
            "--role",
            "user",
            "--password",
            "mypass",
        )
        captured = capsys.readouterr()
        assert "created" in captured.out.lower()
        from dracs.users import authenticate

        assert authenticate("flaguser", "mypass") is not None

    def test_add_admin_user(self, run_cli, user_cli_db, capsys):
        _add_user(run_cli, "boss", "admin")
        users = list_users()
        assert users[0]["role"] == "admin"

    def test_add_user_password_mismatch(self, run_cli, user_cli_db):
        with patch("dracs.cli.getpass.getpass", side_effect=["pass1", "pass2"]):
            with pytest.raises(SystemExit):
                run_cli("user", "--add", "--username", "fail", "--role", "user")

    def test_add_user_missing_username(self, run_cli, user_cli_db):
        with pytest.raises(SystemExit):
            run_cli("user", "--add", "--role", "user")

    def test_add_user_missing_role(self, run_cli, user_cli_db):
        with pytest.raises(SystemExit):
            run_cli("user", "--add", "--username", "norole")

    def test_add_user_no_primary_site(self, run_cli, user_cli_db, capsys):
        with patch("dracs.cli.getpass.getpass", side_effect=["secret", "secret"]):
            with patch(
                "dracs.db.get_default_site_id", side_effect=RuntimeError("no site")
            ):
                run_cli("user", "--add", "--username", "nositeuser", "--role", "user")
        captured = capsys.readouterr()
        assert "created" in captured.out.lower()
        users = list_users()
        assert any(u["username"] == "nositeuser" for u in users)


class TestUserRemove:
    def test_remove_user(self, run_cli, user_cli_db, capsys):
        _add_user(run_cli, "todelete", "user")
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
        _add_user(run_cli, "alice", "admin")
        _add_user(run_cli, "bob", "user")
        capsys.readouterr()
        run_cli("user", "--list")
        captured = capsys.readouterr()
        assert "alice" in captured.out
        assert "bob" in captured.out


class TestUserUpdate:
    def test_update_role(self, run_cli, user_cli_db, capsys):
        _add_user(run_cli, "changeme", "user")
        run_cli("user", "--update", "--username", "changeme", "--role", "admin")
        captured = capsys.readouterr()
        assert "updated" in captured.out.lower()
        users = list_users()
        assert users[0]["role"] == "admin"

    def test_update_password(self, run_cli, user_cli_db, capsys):
        _add_user(run_cli, "passchange", "user")
        with patch("dracs.cli.getpass.getpass", side_effect=["newpass", "newpass"]):
            run_cli("user", "--update", "--username", "passchange")
        captured = capsys.readouterr()
        assert "updated" in captured.out.lower()

    def test_update_password_with_flag(self, run_cli, user_cli_db, capsys):
        _add_user(run_cli, "flagpass", "user")
        run_cli(
            "user", "--update", "--username", "flagpass", "--password", "newpass123"
        )
        captured = capsys.readouterr()
        assert "updated" in captured.out.lower()
        from dracs.users import authenticate

        assert authenticate("flagpass", "newpass123") is not None

    def test_update_password_mismatch(self, run_cli, user_cli_db):
        _add_user(run_cli, "mismatch", "user")
        with patch("dracs.cli.getpass.getpass", side_effect=["pass1", "pass2"]):
            with pytest.raises(SystemExit):
                run_cli("user", "--update", "--username", "mismatch")

    def test_update_missing_username(self, run_cli, user_cli_db):
        with pytest.raises(SystemExit):
            run_cli("user", "--update")

    def test_user_alias(self, run_cli, user_cli_db, capsys):
        with patch("dracs.cli.getpass.getpass", side_effect=["pass", "pass"]):
            run_cli("u", "--add", "--username", "aliasuser", "--role", "user")
        captured = capsys.readouterr()
        assert "created" in captured.out.lower()


class TestUserQuadsRole:
    def test_add_quads_role_uses_default_site(self, run_cli, user_cli_db, capsys):
        """--add --role quads creates user with no global role and sets quads site role."""
        from dracs.users import get_user_role_for_site
        from dracs.db import get_default_site_id

        with patch("dracs.cli.getpass.getpass", side_effect=["pass", "pass"]):
            run_cli("user", "--add", "--username", "quser", "--role", "quads")
        captured = capsys.readouterr()
        assert "created" in captured.out.lower()

        users = list_users()
        match = next(u for u in users if u["username"] == "quser")
        assert match["role"] is None
        assert get_user_role_for_site("quser", get_default_site_id()) == "quads"

    def test_add_none_role_creates_user_with_no_role(
        self, run_cli, user_cli_db, capsys
    ):
        """--add --role none creates user with no global role and no site role."""
        with patch("dracs.cli.getpass.getpass", side_effect=["pass", "pass"]):
            run_cli("user", "--add", "--username", "noroleuser", "--role", "none")
        captured = capsys.readouterr()
        assert "created" in captured.out.lower()
        users = list_users()
        match = next(u for u in users if u["username"] == "noroleuser")
        assert match["role"] is None

    def test_update_quads_role_without_site_exits(self, run_cli, user_cli_db):
        """--update --role quads without --site should exit with an error."""
        _add_user(run_cli, "quser2", "user")
        with pytest.raises(SystemExit):
            run_cli("user", "--update", "--username", "quser2", "--role", "quads")

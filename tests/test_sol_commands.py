"""Tests for cmd_sol in dracs/commands.py and dracs_client/commands.py."""

import os
import sys
import tempfile
from argparse import Namespace
from unittest.mock import MagicMock, call, patch

import pexpect
import pytest

from dracs.db import db_initialize

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def isolated_db(tmp_path):
    db_path = str(tmp_path / "test.db")
    db_initialize(db_path)
    with patch.dict(os.environ, {"DRACS_DB": db_path}):
        yield db_path


# ---------------------------------------------------------------------------
# dracs/commands.py  cmd_sol
# ---------------------------------------------------------------------------


class TestDracsCmdSol:
    """Unit tests for the local dracs cmd_sol (reads password from site INI)."""

    def _args(self, target="testhost"):
        return Namespace(target=target)

    def _make_cfg(self, password="secret"):
        return {"defaults": {"conserver_password": password}, "hosts": {}}

    def test_no_password_configured_exits(self, capsys):
        from dracs.commands import cmd_sol

        with (
            patch(
                "dracs.sites.get_site_ini_config",
                return_value={"defaults": {}, "hosts": {}},
            ),
            patch("dracs.db.get_primary_site_name", return_value="Default"),
            pytest.raises(SystemExit) as exc,
        ):
            cmd_sol(self._args())
        assert exc.value.code == 1
        assert "conserver password" in capsys.readouterr().err

    def test_console_missing_exits(self, capsys):
        from dracs.commands import cmd_sol

        with (
            patch("dracs.sites.get_site_ini_config", return_value=self._make_cfg()),
            patch("dracs.db.get_primary_site_name", return_value="Default"),
            patch("dracs.commands.shutil.which", return_value=None),
            pytest.raises(SystemExit) as exc,
        ):
            cmd_sol(self._args())
        assert exc.value.code == 1
        assert "console" in capsys.readouterr().err

    def test_pexpect_timeout_exits(self, capsys):
        from dracs.commands import cmd_sol

        mock_child = MagicMock()
        mock_child.expect.side_effect = pexpect.TIMEOUT("timed out")

        with (
            patch("dracs.sites.get_site_ini_config", return_value=self._make_cfg()),
            patch("dracs.db.get_primary_site_name", return_value="Default"),
            patch("dracs.commands.shutil.which", return_value="/usr/bin/console"),
            patch("pexpect.spawn", return_value=mock_child),
            pytest.raises(SystemExit) as exc,
        ):
            cmd_sol(self._args())
        assert exc.value.code == 1
        assert "timed out" in capsys.readouterr().err

    def test_pexpect_eof_exits(self, capsys):
        from dracs.commands import cmd_sol

        mock_child = MagicMock()
        mock_child.expect.side_effect = pexpect.EOF("EOF")

        with (
            patch("dracs.sites.get_site_ini_config", return_value=self._make_cfg()),
            patch("dracs.db.get_primary_site_name", return_value="Default"),
            patch("dracs.commands.shutil.which", return_value="/usr/bin/console"),
            patch("pexpect.spawn", return_value=mock_child),
            pytest.raises(SystemExit) as exc,
        ):
            cmd_sol(self._args())
        assert exc.value.code == 1
        assert "failed" in capsys.readouterr().err

    def test_spawns_console_with_correct_args(self, capsys):
        from dracs.commands import cmd_sol

        mock_child = MagicMock()

        with (
            patch(
                "dracs.sites.get_site_ini_config", return_value=self._make_cfg("mypass")
            ),
            patch("dracs.db.get_primary_site_name", return_value="mysite"),
            patch("dracs.commands.shutil.which", return_value="/usr/bin/console"),
            patch("dracs.commands.socket.gethostname", return_value="myserver"),
            patch.dict(os.environ, {"SOL_CONSERVER_PORT": "3109"}),
            patch("pexpect.spawn", return_value=mock_child) as mock_spawn,
        ):
            cmd_sol(self._args(target="myhost"), site_name="mysite")

        mock_spawn.assert_called_once_with(
            "/usr/bin/console",
            ["-M", "myserver", "-l", "mysite", "myhost", "-p", "3109"],
            timeout=10,
            encoding="utf-8",
            codec_errors="replace",
        )
        mock_child.sendline.assert_called_once_with("mypass")
        mock_child.interact.assert_called_once()

    def test_uses_site_name_argument(self, capsys):
        from dracs.commands import cmd_sol

        mock_child = MagicMock()

        # site_name passed explicitly — should NOT call get_primary_site_name
        with (
            patch(
                "dracs.sites.get_site_ini_config", return_value=self._make_cfg()
            ) as mock_cfg,
            patch("dracs.commands.shutil.which", return_value="/usr/bin/console"),
            patch("dracs.commands.socket.gethostname", return_value="myserver"),
            patch("pexpect.spawn", return_value=mock_child),
        ):
            cmd_sol(self._args(), site_name="SiteX")

        mock_cfg.assert_called_once_with("SiteX")


# ---------------------------------------------------------------------------
# dracs_client/commands.py  cmd_sol
# ---------------------------------------------------------------------------


class TestDracsClientCmdSol:
    """Unit tests for the remote dracs-client cmd_sol (calls API for credentials)."""

    def _args(self, target="clienthost", site=None):
        return Namespace(target=target, site=site)

    def _api_data(self):
        return {
            "success": True,
            "server": "dracs.example.com",
            "port": "3109",
            "username": "Default",
            "password": "apipass",
        }

    def _mock_api_response(self, data=None):
        resp = MagicMock()
        resp.json.return_value = data or self._api_data()
        return resp

    def test_calls_correct_api_url_without_site(self):
        from dracs_client.commands import cmd_sol

        mock_child = MagicMock()

        with (
            patch(
                "dracs_client.commands._api_request",
                return_value=self._mock_api_response(),
            ) as mock_req,
            patch("shutil.which", return_value="/usr/bin/console"),
            patch("pexpect.spawn", return_value=mock_child),
        ):
            cmd_sol(self._args(site=None), "https://dracs.local", True, "dracs.local")

        mock_req.assert_called_once_with(
            "get",
            "https://dracs.local/api/sol/connect-info",
            "dracs.local",
            True,
        )

    def test_calls_correct_api_url_with_site(self):
        from dracs_client.commands import cmd_sol

        mock_child = MagicMock()

        with (
            patch(
                "dracs_client.commands._api_request",
                return_value=self._mock_api_response(),
            ) as mock_req,
            patch("shutil.which", return_value="/usr/bin/console"),
            patch("pexpect.spawn", return_value=mock_child),
        ):
            cmd_sol(
                self._args(site="MySite"), "https://dracs.local", True, "dracs.local"
            )

        mock_req.assert_called_once_with(
            "get",
            "https://dracs.local/api/sol/connect-info?site=MySite",
            "dracs.local",
            True,
        )

    def test_console_missing_exits(self, capsys):
        from dracs_client.commands import cmd_sol

        with (
            patch(
                "dracs_client.commands._api_request",
                return_value=self._mock_api_response(),
            ),
            patch("shutil.which", return_value=None),
            pytest.raises(SystemExit) as exc,
        ):
            cmd_sol(self._args(), "https://dracs.local", True, "dracs.local")

        assert exc.value.code == 1
        assert "console" in capsys.readouterr().err

    def test_pexpect_timeout_exits(self, capsys):
        from dracs_client.commands import cmd_sol

        mock_child = MagicMock()
        mock_child.expect.side_effect = pexpect.TIMEOUT("timed out")

        with (
            patch(
                "dracs_client.commands._api_request",
                return_value=self._mock_api_response(),
            ),
            patch("shutil.which", return_value="/usr/bin/console"),
            patch("pexpect.spawn", return_value=mock_child),
            pytest.raises(SystemExit) as exc,
        ):
            cmd_sol(self._args(), "https://dracs.local", True, "dracs.local")

        assert exc.value.code == 1
        assert "timed out" in capsys.readouterr().err

    def test_pexpect_eof_exits(self, capsys):
        from dracs_client.commands import cmd_sol

        mock_child = MagicMock()
        mock_child.expect.side_effect = pexpect.EOF("EOF")

        with (
            patch(
                "dracs_client.commands._api_request",
                return_value=self._mock_api_response(),
            ),
            patch("shutil.which", return_value="/usr/bin/console"),
            patch("pexpect.spawn", return_value=mock_child),
            pytest.raises(SystemExit) as exc,
        ):
            cmd_sol(self._args(), "https://dracs.local", True, "dracs.local")

        assert exc.value.code == 1
        assert "failed" in capsys.readouterr().err

    def test_spawns_console_with_correct_args(self):
        from dracs_client.commands import cmd_sol

        mock_child = MagicMock()

        with (
            patch(
                "dracs_client.commands._api_request",
                return_value=self._mock_api_response(),
            ),
            patch("shutil.which", return_value="/usr/bin/console"),
            patch("pexpect.spawn", return_value=mock_child) as mock_spawn,
        ):
            cmd_sol(
                self._args(target="targethost"),
                "https://dracs.local",
                True,
                "dracs.local",
            )

        mock_spawn.assert_called_once_with(
            "/usr/bin/console",
            ["-M", "dracs.example.com", "-l", "Default", "targethost", "-p", "3109"],
            timeout=10,
            encoding="utf-8",
            codec_errors="replace",
        )
        mock_child.sendline.assert_called_once_with("apipass")
        mock_child.interact.assert_called_once()

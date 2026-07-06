"""Tests for cmd_sol in dracs/commands.py and dracs_client/commands.py."""

import os
import sys
import tempfile
from argparse import Namespace
from pathlib import Path
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
            patch.dict(os.environ, {"SOL_CONSERVER_PORT": "3109", "SOL_SSL_CA": ""}),
            patch("dracs.sol._ssl_cert_key_paths", return_value=(None, None)),
            patch("pexpect.spawn", return_value=mock_child) as mock_spawn,
        ):
            cmd_sol(self._args(target="myhost"), site_name="mysite")

        mock_spawn.assert_called_once_with(
            "/usr/bin/console",
            ["-E", "-M", "myserver", "-l", "mysite", "myhost", "-p", "3109"],
            timeout=10,
            encoding="utf-8",
            codec_errors="replace",
        )
        mock_child.sendline.assert_called_once_with("mypass")
        mock_child.interact.assert_called_once()

    def test_no_ssl_prepends_E_flag(self, capsys):
        from dracs.commands import cmd_sol

        mock_child = MagicMock()

        with (
            patch("dracs.sites.get_site_ini_config", return_value=self._make_cfg()),
            patch("dracs.db.get_primary_site_name", return_value="site1"),
            patch("dracs.commands.shutil.which", return_value="/usr/bin/console"),
            patch("dracs.commands.socket.gethostname", return_value="srv"),
            patch("dracs.sol._ssl_cert_key_paths", return_value=(None, None)),
            patch("pexpect.spawn", return_value=mock_child) as mock_spawn,
        ):
            cmd_sol(self._args(), site_name="site1")

        args_passed = mock_spawn.call_args[0][1]
        assert args_passed[0] == "-E"

    def test_ssl_enabled_no_ca_omits_E_flag(self, capsys, tmp_path):
        from dracs.commands import cmd_sol

        mock_child = MagicMock()
        fake_cert = tmp_path / "cert.pem"

        with (
            patch("dracs.sites.get_site_ini_config", return_value=self._make_cfg()),
            patch("dracs.db.get_primary_site_name", return_value="site1"),
            patch("dracs.commands.shutil.which", return_value="/usr/bin/console"),
            patch("dracs.commands.socket.gethostname", return_value="srv"),
            patch.dict(os.environ, {"SOL_SSL_CA": ""}),
            patch("dracs.sol._ssl_cert_key_paths", return_value=(fake_cert, fake_cert)),
            patch("pexpect.spawn", return_value=mock_child) as mock_spawn,
        ):
            cmd_sol(self._args(), site_name="site1")

        args_passed = mock_spawn.call_args[0][1]
        assert "-E" not in args_passed
        assert "-n" not in args_passed

    def test_ssl_enabled_with_ca_adds_n_C_flags(self, capsys, tmp_path):
        from dracs.commands import cmd_sol

        mock_child = MagicMock()
        fake_cert = tmp_path / "cert.pem"

        with (
            patch("dracs.sites.get_site_ini_config", return_value=self._make_cfg()),
            patch("dracs.db.get_primary_site_name", return_value="site1"),
            patch("dracs.commands.shutil.which", return_value="/usr/bin/console"),
            patch("dracs.commands.socket.gethostname", return_value="srv"),
            patch.dict(os.environ, {"SOL_SSL_CA": "/etc/pki/ca-trust/my-ca.pem"}),
            patch("dracs.sol._ssl_cert_key_paths", return_value=(fake_cert, fake_cert)),
            patch("pexpect.spawn", return_value=mock_child) as mock_spawn,
        ):
            cmd_sol(self._args(), site_name="site1")

        args_passed = mock_spawn.call_args[0][1]
        assert "-n" in args_passed
        assert "-C" in args_passed
        assert "/etc/dracs/console.cf" in args_passed
        assert "-E" not in args_passed

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

    def _api_data(self, ssl=False, ssl_ca=None):
        return {
            "success": True,
            "server": "dracs.example.com",
            "port": "3109",
            "username": "Default",
            "password": "apipass",
            "ssl": ssl,
            "ssl_ca": ssl_ca,
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
            [
                "-E",
                "-M",
                "dracs.example.com",
                "-l",
                "Default",
                "targethost",
                "-p",
                "3109",
            ],
            timeout=10,
            encoding="utf-8",
            codec_errors="replace",
        )
        mock_child.sendline.assert_called_once_with("apipass")
        mock_child.interact.assert_called_once()

    def test_ssl_false_prepends_E_flag(self):
        from dracs_client.commands import cmd_sol

        mock_child = MagicMock()

        with (
            patch(
                "dracs_client.commands._api_request",
                return_value=self._mock_api_response(self._api_data(ssl=False)),
            ),
            patch("shutil.which", return_value="/usr/bin/console"),
            patch("pexpect.spawn", return_value=mock_child) as mock_spawn,
        ):
            cmd_sol(self._args(), "https://dracs.local", True, "dracs.local")

        args_passed = mock_spawn.call_args[0][1]
        assert args_passed[0] == "-E"

    def test_ssl_true_no_ca_omits_E_flag(self):
        from dracs_client.commands import cmd_sol

        mock_child = MagicMock()

        with (
            patch(
                "dracs_client.commands._api_request",
                return_value=self._mock_api_response(
                    self._api_data(ssl=True, ssl_ca=None)
                ),
            ),
            patch("shutil.which", return_value="/usr/bin/console"),
            patch("pexpect.spawn", return_value=mock_child) as mock_spawn,
        ):
            cmd_sol(self._args(), "https://dracs.local", True, "dracs.local")

        args_passed = mock_spawn.call_args[0][1]
        assert "-E" not in args_passed
        assert "-n" not in args_passed

    def test_ssl_true_with_ca_tempfile_oserror_falls_back_gracefully(self, capsys):
        from dracs_client.commands import cmd_sol

        mock_child = MagicMock()
        ca_content = "-----BEGIN CERTIFICATE-----\nFAKECA\n-----END CERTIFICATE-----\n"

        with (
            patch(
                "dracs_client.commands._api_request",
                return_value=self._mock_api_response(
                    self._api_data(ssl=True, ssl_ca=ca_content)
                ),
            ),
            patch("shutil.which", return_value="/usr/bin/console"),
            patch("tempfile.mkdtemp", side_effect=OSError("no space")),
            patch("pexpect.spawn", return_value=mock_child) as mock_spawn,
        ):
            cmd_sol(self._args(), "https://dracs.local", True, "dracs.local")

        args_passed = mock_spawn.call_args[0][1]
        # Fell back: no -E (ssl is true) and no -C (temp file failed)
        assert "-E" not in args_passed
        assert "-C" not in args_passed
        assert "Warning" in capsys.readouterr().err

    def test_ssl_true_with_ca_writes_temp_files_and_uses_n_C(self):
        from dracs_client.commands import cmd_sol

        mock_child = MagicMock()
        ca_content = "-----BEGIN CERTIFICATE-----\nFAKECA\n-----END CERTIFICATE-----\n"

        with (
            patch(
                "dracs_client.commands._api_request",
                return_value=self._mock_api_response(
                    self._api_data(ssl=True, ssl_ca=ca_content)
                ),
            ),
            patch("shutil.which", return_value="/usr/bin/console"),
            patch("pexpect.spawn", return_value=mock_child) as mock_spawn,
        ):
            cmd_sol(self._args(), "https://dracs.local", True, "dracs.local")

        args_passed = mock_spawn.call_args[0][1]
        assert "-n" in args_passed
        assert "-C" in args_passed
        assert "-E" not in args_passed
        cf_path = args_passed[args_passed.index("-C") + 1]
        # The temp dir should be cleaned up after interact()
        assert not Path(cf_path).exists()

"""Tests for conserver/IPMI SOL management (sol.py)."""

import os
import signal
import subprocess
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

from dracs.sol import (
    ConserverConfig,
    ConserverPasswd,
    disable_systemd_service,
    start_conserver,
    startup,
    stop_conserver,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def passwd_file(tmp_path):
    return tmp_path / "conserver.passwd"


@pytest.fixture
def cf_file(tmp_path):
    return tmp_path / "conserver.cf"


@pytest.fixture
def log_dir(tmp_path):
    d = tmp_path / "logs"
    d.mkdir()
    return d


@pytest.fixture
def passwd_mgr(passwd_file):
    return ConserverPasswd(passwd_file)


@pytest.fixture
def config_gen(cf_file, passwd_file, log_dir):
    return ConserverConfig(cf_file, passwd_file, log_dir)


# ---------------------------------------------------------------------------
# ConserverPasswd tests
# ---------------------------------------------------------------------------


class TestConserverPasswdGeneratePassword:
    def test_length(self, passwd_mgr):
        pw = passwd_mgr._generate_password()
        assert len(pw) == 20

    def test_alphanumeric(self, passwd_mgr):
        pw = passwd_mgr._generate_password()
        assert pw.isalnum()

    def test_uniqueness(self, passwd_mgr):
        passwords = {passwd_mgr._generate_password() for _ in range(10)}
        assert len(passwords) == 10


class TestConserverPasswdHashPassword:
    def test_calls_openssl(self, passwd_mgr):
        fake_hash = "ABhashvalue"
        with (
            patch("shutil.which", return_value="/usr/bin/openssl"),
            patch("subprocess.run") as mock_run,
        ):
            mock_run.return_value = MagicMock(stdout=fake_hash + "\n", returncode=0)
            result = passwd_mgr._hash_password("secret")
        mock_run.assert_called_once_with(
            ["/usr/bin/openssl", "passwd", "-crypt", "-stdin"],
            input="secret",
            capture_output=True,
            text=True,
            check=True,
        )
        assert result == fake_hash

    def test_strips_whitespace(self, passwd_mgr):
        with (
            patch("shutil.which", return_value="/usr/bin/openssl"),
            patch("subprocess.run") as mock_run,
        ):
            mock_run.return_value = MagicMock(stdout="  ABhash  \n", returncode=0)
            result = passwd_mgr._hash_password("pw")
        assert result == "ABhash"


class TestConserverPasswdSync:
    def test_generates_password_for_none(self, passwd_mgr):
        with patch.object(ConserverPasswd, "_hash_password", return_value="ABhash"):
            result = passwd_mgr.sync({"site1": None})
        assert "site1" in result
        assert result["site1"] is not None
        assert len(result["site1"]) == 20

    def test_preserves_existing_password(self, passwd_mgr):
        with patch.object(ConserverPasswd, "_hash_password", return_value="ABhash"):
            result = passwd_mgr.sync({"site1": "mypassword"})
        assert result["site1"] == "mypassword"

    def test_writes_passwd_file(self, passwd_mgr, passwd_file):
        with patch.object(ConserverPasswd, "_hash_password", return_value="ABhash"):
            passwd_mgr.sync({"site1": "pw1", "site2": "pw2"})
        assert passwd_file.exists()
        content = passwd_file.read_text()
        assert "site1:ABhash" in content
        assert "site2:ABhash" in content

    def test_removes_stale_entries(self, passwd_mgr, passwd_file):
        passwd_file.write_text("old_site:OLDhash\n")
        with patch.object(ConserverPasswd, "_hash_password", return_value="NEWhash"):
            passwd_mgr.sync({"new_site": "pw"})
        content = passwd_file.read_text()
        assert "old_site" not in content
        assert "new_site" in content

    def test_file_permissions(self, passwd_mgr, passwd_file):
        with patch.object(ConserverPasswd, "_hash_password", return_value="ABhash"):
            passwd_mgr.sync({"site1": "pw"})
        assert oct(passwd_file.stat().st_mode)[-3:] == "640"

    def test_empty_sites(self, passwd_mgr, passwd_file):
        with patch.object(ConserverPasswd, "_hash_password", return_value="ABhash"):
            result = passwd_mgr.sync({})
        assert result == {}
        assert passwd_file.read_text() == ""


class TestConserverPasswdRead:
    def test_reads_entries(self, passwd_mgr, passwd_file):
        passwd_file.write_text("site1:ABhash1\nsite2:CDhash2\n")
        assert passwd_mgr._read() == {"site1": "ABhash1", "site2": "CDhash2"}

    def test_missing_file(self, passwd_mgr):
        assert passwd_mgr._read() == {}

    def test_skips_blank_lines(self, passwd_mgr, passwd_file):
        passwd_file.write_text("site1:hash\n\n\nsite2:hash2\n")
        result = passwd_mgr._read()
        assert len(result) == 2


class TestConserverPasswdVerify:
    def test_correct_password(self, passwd_mgr, passwd_file):
        passwd_file.write_text("site1:ABhashvalue\n")
        with (
            patch("shutil.which", return_value="/usr/bin/openssl"),
            patch("subprocess.run") as mock_run,
        ):
            mock_run.return_value = MagicMock(stdout="ABhashvalue\n", returncode=0)
            assert passwd_mgr.verify("site1", "correct") is True
        mock_run.assert_called_once_with(
            ["/usr/bin/openssl", "passwd", "-crypt", "-salt", "AB", "-stdin"],
            input="correct",
            capture_output=True,
            text=True,
            check=True,
        )

    def test_wrong_password(self, passwd_mgr, passwd_file):
        passwd_file.write_text("site1:ABhashvalue\n")
        with (
            patch("shutil.which", return_value="/usr/bin/openssl"),
            patch("subprocess.run") as mock_run,
        ):
            mock_run.return_value = MagicMock(stdout="ABwronghash\n", returncode=0)
            assert passwd_mgr.verify("site1", "wrong") is False

    def test_missing_site(self, passwd_mgr, passwd_file):
        passwd_file.write_text("other:ABhash\n")
        assert passwd_mgr.verify("site1", "pw") is False

    def test_openssl_failure(self, passwd_mgr, passwd_file):
        passwd_file.write_text("site1:ABhash\n")
        with patch("subprocess.run", side_effect=subprocess.CalledProcessError(1, [])):
            assert passwd_mgr.verify("site1", "pw") is False


# ---------------------------------------------------------------------------
# ConserverConfig tests
# ---------------------------------------------------------------------------


class TestConserverConfigSafeName:
    @pytest.mark.parametrize(
        "name,expected",
        [
            ("site1", "site1"),
            ("my-site", "my_site"),
            ("host.example.com", "host_example_com"),
            ("host01.example.com", "host01_example_com"),
            ("site name", "site_name"),
        ],
    )
    def test_safe_name(self, config_gen, name, expected):
        assert config_gen._safe_name(name) == expected


class TestConserverConfigHasHostOverride:
    def test_same_credentials(self, config_gen):
        assert not config_gen._has_host_override(
            {"username": "root", "password": "pw"},
            {"username": "root", "password": "pw"},
        )

    def test_different_username(self, config_gen):
        assert config_gen._has_host_override(
            {"username": "admin", "password": "pw"},
            {"username": "root", "password": "pw"},
        )

    def test_different_password(self, config_gen):
        assert config_gen._has_host_override(
            {"username": "root", "password": "different"},
            {"username": "root", "password": "pw"},
        )

    def test_empty_host_creds(self, config_gen):
        assert not config_gen._has_host_override(
            {},
            {"username": "root", "password": "pw"},
        )

    def test_none_values_ignored(self, config_gen):
        assert not config_gen._has_host_override(
            {"username": None, "password": None},
            {"username": "root", "password": "pw"},
        )


class TestConserverConfigGenerate:
    SITE_DATA = [
        {
            "name": "Default",
            "defaults": {"username": "root", "password": "sitepass"},
            "hosts": {
                "host01.example.com": {},
                "host02.example.com": {"username": "admin", "password": "hostpass"},
            },
        }
    ]

    def _generate(self, config_gen):
        with patch(
            "dracs.snmp.build_idrac_hostname",
            side_effect=lambda h: f"mgmt-{h}",
        ):
            config_gen.generate(self.SITE_DATA)
        return config_gen.cf_path.read_text()

    def test_file_created(self, config_gen):
        content = self._generate(config_gen)
        assert config_gen.cf_path.exists()
        assert len(content) > 0

    def test_file_permissions(self, config_gen):
        self._generate(config_gen)
        assert oct(config_gen.cf_path.stat().st_mode)[-3:] == "640"

    def test_config_block_present(self, config_gen, passwd_file, log_dir):
        content = self._generate(config_gen)
        assert "config * {" in content
        assert f"passwdfile {passwd_file};" in content
        assert f"logfile {log_dir}/conserver.log;" in content
        assert "daemonmode no;" in content

    def test_access_block_present(self, config_gen):
        content = self._generate(config_gen)
        assert "access * {" in content
        assert "allowed *.*;" in content

    def test_site_default_block(self, config_gen):
        content = self._generate(config_gen)
        assert "default ipmi_sol_Default {" in content
        assert "-U root -P sitepass" in content

    def test_per_host_override_block(self, config_gen):
        content = self._generate(config_gen)
        assert "default ipmi_sol_host02_example_com {" in content
        assert "-U admin -P hostpass" in content

    def test_no_override_block_for_default_host(self, config_gen):
        content = self._generate(config_gen)
        assert "default ipmi_sol_host01_example_com" not in content

    def test_console_stanza_site_include(self, config_gen):
        content = self._generate(config_gen)
        assert "console host01.example.com {" in content
        assert "include ipmi_sol_Default;" in content
        assert "host mgmt-host01.example.com;" in content
        assert "rw Default;" in content

    def test_console_stanza_host_override_include(self, config_gen):
        content = self._generate(config_gen)
        assert "console host02.example.com {" in content
        assert "include ipmi_sol_host02_example_com;" in content
        assert "host mgmt-host02.example.com;" in content

    def test_skips_host_on_validation_error(self, config_gen):
        from dracs.snmp import ValidationError

        data = [
            {
                "name": "S",
                "defaults": {"username": "root", "password": "pw"},
                "hosts": {"bad-host": {}},
            }
        ]
        with patch(
            "dracs.snmp.build_idrac_hostname",
            side_effect=ValidationError("bad"),
        ):
            config_gen.generate(data)
        content = config_gen.cf_path.read_text()
        assert "console bad-host" not in content

    def test_empty_sites(self, config_gen):
        config_gen.generate([])
        content = config_gen.cf_path.read_text()
        assert "config * {" in content
        assert "console" not in content


# ---------------------------------------------------------------------------
# Process management tests
# ---------------------------------------------------------------------------


class TestDisableSystemdService:
    def test_calls_systemctl(self):
        with (
            patch("shutil.which", return_value="/usr/bin/systemctl"),
            patch("subprocess.run") as mock_run,
        ):
            mock_run.return_value = MagicMock(returncode=0)
            disable_systemd_service()
        mock_run.assert_called_once_with(
            ["/usr/bin/systemctl", "disable", "--now", "conserver"],
            capture_output=True,
            check=False,
        )

    def test_ignores_failure(self):
        with patch("subprocess.run", side_effect=FileNotFoundError):
            disable_systemd_service()  # must not raise


class TestStartConserver:
    def test_starts_process(self, tmp_path):
        cf = tmp_path / "conserver.cf"
        mock_proc = MagicMock()
        mock_proc.pid = 12345
        with (
            patch("shutil.which", return_value="/usr/sbin/conserver"),
            patch("subprocess.Popen", return_value=mock_proc) as mock_popen,
            patch("dracs.sol._pid_file_path", tmp_path / "conserver.pid"),
        ):
            result = start_conserver(cf)
        mock_popen.assert_called_once_with(
            ["/usr/sbin/conserver", "-c", str(cf)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        assert result is mock_proc

    def test_writes_pid_file(self, tmp_path):
        cf = tmp_path / "conserver.cf"
        pid_file = tmp_path / "conserver.pid"
        mock_proc = MagicMock()
        mock_proc.pid = 99999
        with (
            patch("shutil.which", return_value="/usr/sbin/conserver"),
            patch("subprocess.Popen", return_value=mock_proc),
            patch("dracs.sol._pid_file_path", pid_file),
        ):
            start_conserver(cf)
        assert pid_file.read_text() == "99999"

    def test_returns_none_when_not_found(self, tmp_path):
        cf = tmp_path / "conserver.cf"
        with patch("shutil.which", return_value=None):
            result = start_conserver(cf)
        assert result is None


class TestStopConserver:
    def test_terminates_tracked_process(self, tmp_path):
        import dracs.sol as sol_module

        mock_proc = MagicMock()
        mock_proc.wait.return_value = 0
        sol_module._conserver_process = mock_proc
        with patch("dracs.sol._pid_file_path", tmp_path / "conserver.pid"):
            stop_conserver()
        mock_proc.terminate.assert_called_once()
        assert sol_module._conserver_process is None

    def test_kills_on_timeout(self, tmp_path):
        import dracs.sol as sol_module

        mock_proc = MagicMock()
        mock_proc.wait.side_effect = subprocess.TimeoutExpired([], 5)
        sol_module._conserver_process = mock_proc
        with patch("dracs.sol._pid_file_path", tmp_path / "conserver.pid"):
            stop_conserver()
        mock_proc.kill.assert_called_once()

    def test_uses_pid_file_fallback(self, tmp_path):
        import dracs.sol as sol_module

        sol_module._conserver_process = None
        pid_file = tmp_path / "conserver.pid"
        pid_file.write_text("55555")
        with (
            patch("dracs.sol._pid_file_path", pid_file),
            patch("os.kill") as mock_kill,
        ):
            stop_conserver()
        mock_kill.assert_called_once_with(55555, signal.SIGTERM)
        assert not pid_file.exists()

    def test_handles_missing_pid_file(self, tmp_path):
        import dracs.sol as sol_module

        sol_module._conserver_process = None
        with patch("dracs.sol._pid_file_path", tmp_path / "nonexistent.pid"):
            stop_conserver()  # must not raise


# ---------------------------------------------------------------------------
# startup() orchestration test
# ---------------------------------------------------------------------------


class TestStartup:
    def test_calls_components_in_order(self, tmp_path, temp_db):
        from dracs.db import Site, System, db_initialize, get_session

        db_initialize(temp_db)
        with get_session() as session:
            site = Site(
                name="TestSite", is_primary=True, created_at=datetime.now().isoformat()
            )
            session.add(site)
            session.flush()
            system = System(
                svc_tag="SVC001",
                name="host01.example.com",
                site_id=site.id,
            )
            session.add(system)
            session.commit()

        cf = tmp_path / "conserver.cf"
        pw = tmp_path / "conserver.passwd"
        logs = tmp_path / "logs"

        with (
            patch.object(
                ConserverPasswd,
                "_hash_password",
                return_value="ABhash",
            ),
            patch(
                "dracs.snmp.build_idrac_hostname",
                return_value="mgmt-host01.example.com",
            ),
            patch("dracs.sol.disable_systemd_service") as mock_disable,
            patch("dracs.sol.start_conserver") as mock_start,
        ):
            startup(temp_db, None, cf, pw, logs)

        assert cf.exists()
        assert pw.exists()
        mock_disable.assert_called_once()
        mock_start.assert_called_once_with(cf)

    def test_generates_and_stores_missing_password(self, tmp_path, temp_db):
        from dracs.db import Site, db_initialize, get_session

        db_initialize(temp_db)
        with get_session() as session:
            site = Site(
                name="NewSite", is_primary=True, created_at=datetime.now().isoformat()
            )
            session.add(site)
            session.commit()

        cf = tmp_path / "conserver.cf"
        pw = tmp_path / "conserver.passwd"
        logs = tmp_path / "logs"

        with (
            patch.object(ConserverPasswd, "_hash_password", return_value="ABhash"),
            patch("dracs.snmp.build_idrac_hostname", return_value="mgmt-host"),
            patch("dracs.sol.disable_systemd_service"),
            patch("dracs.sol.start_conserver"),
        ):
            startup(temp_db, None, cf, pw, logs)

        from dracs.sites import get_site_ini_config

        cfg = get_site_ini_config("NewSite")
        assert cfg["defaults"].get("conserver_password")

    def test_handles_startup_exception(self, tmp_path):
        with patch("dracs.db.db_initialize", side_effect=Exception("DB error")):
            startup("bad_path", None, tmp_path / "cf", tmp_path / "pw", tmp_path)

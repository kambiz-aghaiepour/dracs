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
    _is_conserver_with_config,
    _kill_conservers_on_port,
    _kill_conservers_with_config,
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
        fake_hash = "$6$somesalt$hashvalue"
        with (
            patch("shutil.which", return_value="/usr/bin/openssl"),
            patch("subprocess.run") as mock_run,
        ):
            mock_run.return_value = MagicMock(stdout=fake_hash + "\n", returncode=0)
            result = passwd_mgr._hash_password("secret")
        mock_run.assert_called_once_with(
            ["/usr/bin/openssl", "passwd", "-6", "-stdin"],
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
        stored = "$6$somesalt$hashvalue"
        passwd_file.write_text(f"site1:{stored}\n")
        with (
            patch("shutil.which", return_value="/usr/bin/openssl"),
            patch("subprocess.run") as mock_run,
        ):
            mock_run.return_value = MagicMock(stdout=stored + "\n", returncode=0)
            assert passwd_mgr.verify("site1", "correct") is True
        mock_run.assert_called_once_with(
            ["/usr/bin/openssl", "passwd", "-6", "-salt", "somesalt", "-stdin"],
            input="correct",
            capture_output=True,
            text=True,
            check=True,
        )

    def test_wrong_password(self, passwd_mgr, passwd_file):
        passwd_file.write_text("site1:$6$somesalt$hashvalue\n")
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
        assert "primaryport 3109;" in content
        assert "secondaryport 3110;" in content
        assert f"passwdfile {passwd_file};" in content
        assert f"logfile {log_dir}/conserver.log;" in content
        assert "daemonmode no;" in content

    def test_config_block_custom_ports(self, config_gen, passwd_file, log_dir):
        config_gen.generate(self.SITE_DATA, primary_port="4242", secondary_port="5555")
        content = config_gen.cf_path.read_text()
        assert "primaryport 4242;" in content
        assert "secondaryport 5555;" in content

    def test_access_block_present(self, config_gen):
        content = self._generate(config_gen)
        assert "access * {" in content
        assert "allowed 0.0.0.0/0;" in content

    def test_console_stanza_master_is_fqdn(self, config_gen):
        with patch("dracs.sol.socket.gethostname", return_value="myserver.example.com"):
            content = self._generate(config_gen)
        assert "master myserver.example.com;" in content
        assert "master localhost;" not in content

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
    def _start(self, cf, pid_file=None, *, extra_patches=()):
        mock_proc = MagicMock()
        mock_proc.pid = 12345
        pid_path = pid_file or (cf.parent / "conserver.pid")
        patches = [
            patch("shutil.which", return_value="/usr/sbin/conserver"),
            patch("subprocess.Popen", return_value=mock_proc),
            patch("dracs.sol._pid_file_path", pid_path),
            patch("dracs.sol._kill_conservers_with_config"),
        ] + list(extra_patches)
        return mock_proc, patches

    def test_starts_process(self, tmp_path):
        cf = tmp_path / "conserver.cf"
        mock_proc = MagicMock()
        mock_proc.pid = 12345
        with (
            patch("shutil.which", return_value="/usr/sbin/conserver"),
            patch("subprocess.Popen", return_value=mock_proc) as mock_popen,
            patch("dracs.sol._pid_file_path", tmp_path / "conserver.pid"),
            patch("dracs.sol._kill_conservers_with_config"),
        ):
            result = start_conserver(cf)
        mock_popen.assert_called_once_with(
            ["/usr/sbin/conserver", "-C", str(cf), "-m", "10000"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        assert result is mock_proc

    def test_kills_orphans_before_starting(self, tmp_path):
        cf = tmp_path / "conserver.cf"
        mock_proc = MagicMock()
        mock_proc.pid = 12345
        with (
            patch("shutil.which", return_value="/usr/sbin/conserver"),
            patch("subprocess.Popen", return_value=mock_proc),
            patch("dracs.sol._pid_file_path", tmp_path / "conserver.pid"),
            patch("dracs.sol._kill_conservers_with_config") as mock_kill,
        ):
            start_conserver(cf)
        mock_kill.assert_called_once_with(cf)

    def test_writes_pid_file(self, tmp_path):
        cf = tmp_path / "conserver.cf"
        pid_file = tmp_path / "conserver.pid"
        mock_proc = MagicMock()
        mock_proc.pid = 99999
        with (
            patch("shutil.which", return_value="/usr/sbin/conserver"),
            patch("subprocess.Popen", return_value=mock_proc),
            patch("dracs.sol._pid_file_path", pid_file),
            patch("dracs.sol._kill_conservers_with_config"),
        ):
            start_conserver(cf)
        assert pid_file.read_text() == "99999"

    def test_returns_none_when_not_found(self, tmp_path):
        cf = tmp_path / "conserver.cf"
        with patch("shutil.which", return_value=None):
            result = start_conserver(cf)
        assert result is None

    def test_handles_pid_file_write_error(self, tmp_path):
        cf = tmp_path / "conserver.cf"
        mock_proc = MagicMock()
        mock_proc.pid = 99999
        pid_mock = MagicMock()
        pid_mock.parent.mkdir.return_value = None
        pid_mock.write_text.side_effect = OSError("permission denied")
        with (
            patch("shutil.which", return_value="/usr/sbin/conserver"),
            patch("subprocess.Popen", return_value=mock_proc),
            patch("dracs.sol._pid_file_path", pid_mock),
            patch("dracs.sol._kill_conservers_with_config"),
        ):
            result = start_conserver(cf)
        assert result is mock_proc


class TestKillConserversOnPort:
    def _make_proc(self, tmp_path, pid: int, cmdline_args: list[str]) -> Path:
        """Create a fake /proc/<pid>/cmdline entry."""
        proc_entry = tmp_path / str(pid)
        proc_entry.mkdir()
        raw = b"\x00".join(a.encode() for a in cmdline_args) + b"\x00"
        (proc_entry / "cmdline").write_bytes(raw)
        return proc_entry

    def test_kills_matching_conserver(self, tmp_path):
        self._make_proc(
            tmp_path,
            11111,
            [
                "/usr/bin/conserver",
                "-C",
                "/etc/dracs/conserver.cf",
                "-p",
                "3109",
                "-b",
                "3110",
            ],
        )
        with (
            patch("os.getpgid", return_value=11111),
            patch("os.killpg") as mock_killpg,
        ):
            _kill_conservers_on_port("3109", _proc_root=tmp_path)
        mock_killpg.assert_called_once_with(11111, signal.SIGTERM)

    def test_skips_nonmatching_port(self, tmp_path):
        self._make_proc(
            tmp_path,
            22222,
            [
                "/usr/bin/conserver",
                "-C",
                "/etc/dracs/conserver.cf",
                "-p",
                "3200",
                "-b",
                "3201",
            ],
        )
        with patch("os.killpg") as mock_killpg:
            _kill_conservers_on_port("3109", _proc_root=tmp_path)
        mock_killpg.assert_not_called()

    def test_skips_non_conserver_processes(self, tmp_path):
        self._make_proc(tmp_path, 33333, ["/usr/bin/python3", "-p", "3109"])
        with patch("os.killpg") as mock_killpg:
            _kill_conservers_on_port("3109", _proc_root=tmp_path)
        mock_killpg.assert_not_called()

    def test_skips_conserver_without_port_flag(self, tmp_path):
        self._make_proc(
            tmp_path, 77777, ["/usr/bin/conserver", "-C", "/etc/dracs/conserver.cf"]
        )
        with patch("os.killpg") as mock_killpg:
            _kill_conservers_on_port("3109", _proc_root=tmp_path)
        mock_killpg.assert_not_called()

    def test_skips_conserver_with_port_flag_at_end(self, tmp_path):
        # -p with no following value: args.index("-p") + 1 is out of bounds
        self._make_proc(
            tmp_path,
            88888,
            ["/usr/bin/conserver", "-C", "/etc/dracs/conserver.cf", "-p"],
        )
        with patch("os.killpg") as mock_killpg:
            _kill_conservers_on_port("3109", _proc_root=tmp_path)
        mock_killpg.assert_not_called()

    def test_kills_each_process_group_once(self, tmp_path):
        """Master and child share a PGID; killpg should be called only once."""
        self._make_proc(
            tmp_path,
            44444,
            ["/usr/bin/conserver", "-C", "/etc/dracs/conserver.cf", "-p", "3109"],
        )
        self._make_proc(
            tmp_path,
            44445,
            ["/usr/bin/conserver", "-C", "/etc/dracs/conserver.cf", "-p", "3109"],
        )
        with (
            patch("os.getpgid", return_value=44444),
            patch("os.killpg") as mock_killpg,
        ):
            _kill_conservers_on_port("3109", _proc_root=tmp_path)
        assert mock_killpg.call_count == 1

    def test_ignores_unreadable_cmdline(self, tmp_path):
        proc_entry = tmp_path / "55555"
        proc_entry.mkdir()
        # No cmdline file → OSError on read_bytes
        with patch("os.killpg") as mock_killpg:
            _kill_conservers_on_port("3109", _proc_root=tmp_path)
        mock_killpg.assert_not_called()

    def test_handles_proc_oserror(self, tmp_path):
        nonexistent = tmp_path / "no_proc"
        _kill_conservers_on_port("3109", _proc_root=nonexistent)  # must not raise

    def test_tolerates_getpgid_error(self, tmp_path):
        self._make_proc(
            tmp_path,
            66666,
            ["/usr/bin/conserver", "-C", "/etc/dracs/conserver.cf", "-p", "3109"],
        )
        with (
            patch("os.getpgid", side_effect=ProcessLookupError()),
            patch("os.killpg") as mock_killpg,
        ):
            _kill_conservers_on_port("3109", _proc_root=tmp_path)  # must not raise
        mock_killpg.assert_not_called()


class TestIsConserverWithConfig:
    def test_matches_conserver_with_config(self):
        args = ["/usr/bin/conserver", "-C", "/etc/dracs/conserver.cf", "-m", "10000"]
        assert _is_conserver_with_config(args, "/etc/dracs/conserver.cf") is True

    def test_no_match_wrong_config(self):
        args = ["/usr/bin/conserver", "-C", "/other/conserver.cf"]
        assert _is_conserver_with_config(args, "/etc/dracs/conserver.cf") is False

    def test_no_match_not_conserver(self):
        args = ["/usr/bin/python3", "-C", "/etc/dracs/conserver.cf"]
        assert _is_conserver_with_config(args, "/etc/dracs/conserver.cf") is False

    def test_no_match_empty_args(self):
        assert _is_conserver_with_config([], "/etc/dracs/conserver.cf") is False

    def test_no_match_flag_at_end(self):
        args = ["/usr/bin/conserver", "-C"]
        assert _is_conserver_with_config(args, "/etc/dracs/conserver.cf") is False

    def test_no_match_no_c_flag(self):
        args = ["/usr/bin/conserver", "-m", "10000"]
        assert _is_conserver_with_config(args, "/etc/dracs/conserver.cf") is False


class TestKillConserversWithConfig:
    def _make_proc(self, tmp_path, pid: int, cmdline_args: list[str]) -> Path:
        proc_dir = tmp_path / str(pid)
        proc_dir.mkdir()
        cmdline = b"\x00".join(a.encode() for a in cmdline_args) + b"\x00"
        (proc_dir / "cmdline").write_bytes(cmdline)
        return proc_dir

    CF = "/etc/dracs/conserver.cf"

    def test_kills_matching_conserver(self, tmp_path):
        self._make_proc(
            tmp_path, 11111, ["/usr/bin/conserver", "-C", self.CF, "-m", "10000"]
        )
        with (
            patch("os.getpgid", return_value=11111),
            patch("os.killpg") as mock_killpg,
        ):
            _kill_conservers_with_config(Path(self.CF), _proc_root=tmp_path)
        mock_killpg.assert_called_once_with(11111, signal.SIGTERM)

    def test_skips_different_config(self, tmp_path):
        self._make_proc(
            tmp_path, 22222, ["/usr/bin/conserver", "-C", "/other/conserver.cf"]
        )
        with patch("os.killpg") as mock_killpg:
            _kill_conservers_with_config(Path(self.CF), _proc_root=tmp_path)
        mock_killpg.assert_not_called()

    def test_skips_non_conserver(self, tmp_path):
        self._make_proc(tmp_path, 33333, ["/usr/bin/python3", "-C", self.CF])
        with patch("os.killpg") as mock_killpg:
            _kill_conservers_with_config(Path(self.CF), _proc_root=tmp_path)
        mock_killpg.assert_not_called()

    def test_kills_each_pgid_once(self, tmp_path):
        for pid in (44444, 44445):
            self._make_proc(
                tmp_path, pid, ["/usr/bin/conserver", "-C", self.CF, "-m", "10000"]
            )
        with (
            patch("os.getpgid", return_value=44444),
            patch("os.killpg") as mock_killpg,
        ):
            _kill_conservers_with_config(Path(self.CF), _proc_root=tmp_path)
        assert mock_killpg.call_count == 1

    def test_handles_proc_oserror(self, tmp_path):
        _kill_conservers_with_config(
            Path(self.CF), _proc_root=tmp_path / "nonexistent"
        )  # must not raise

    def test_tolerates_getpgid_error(self, tmp_path):
        self._make_proc(
            tmp_path, 55555, ["/usr/bin/conserver", "-C", self.CF, "-m", "10000"]
        )
        with (
            patch("os.getpgid", side_effect=ProcessLookupError()),
            patch("os.killpg") as mock_killpg,
        ):
            _kill_conservers_with_config(Path(self.CF), _proc_root=tmp_path)
        mock_killpg.assert_not_called()

    def test_skips_non_digit_proc_entries(self, tmp_path):
        (tmp_path / "self").mkdir()
        (tmp_path / "net").mkdir()
        with patch("os.killpg") as mock_killpg:
            _kill_conservers_with_config(Path(self.CF), _proc_root=tmp_path)
        mock_killpg.assert_not_called()


class TestStartConserverKillsOrphans:
    def test_kills_existing_conserver_before_start(self, tmp_path):
        cf = tmp_path / "conserver.cf"
        mock_proc = MagicMock()
        mock_proc.pid = 12345
        with (
            patch("shutil.which", return_value="/usr/sbin/conserver"),
            patch("subprocess.Popen", return_value=mock_proc),
            patch("dracs.sol._pid_file_path", tmp_path / "conserver.pid"),
            patch("dracs.sol._kill_conservers_with_config") as mock_kill,
        ):
            start_conserver(cf)
        mock_kill.assert_called_once_with(cf)


class TestStopConserver:
    def test_terminates_tracked_process(self, tmp_path):
        import dracs.sol as sol_module

        mock_proc = MagicMock()
        mock_proc.pid = 12345
        mock_proc.wait.return_value = 0
        sol_module._conserver_process = mock_proc
        with (
            patch("dracs.sol._pid_file_path", tmp_path / "conserver.pid"),
            patch("os.getpgid", return_value=12345),
            patch("os.killpg") as mock_killpg,
        ):
            stop_conserver()
        mock_killpg.assert_called_once_with(12345, signal.SIGTERM)
        assert sol_module._conserver_process is None

    def test_kills_group_on_timeout(self, tmp_path):
        import dracs.sol as sol_module

        mock_proc = MagicMock()
        mock_proc.pid = 12345
        mock_proc.wait.side_effect = subprocess.TimeoutExpired([], 5)
        sol_module._conserver_process = mock_proc
        with (
            patch("dracs.sol._pid_file_path", tmp_path / "conserver.pid"),
            patch("os.getpgid", return_value=12345),
            patch("os.killpg") as mock_killpg,
        ):
            stop_conserver()
        assert mock_killpg.call_count == 2
        mock_killpg.assert_any_call(12345, signal.SIGTERM)
        mock_killpg.assert_any_call(12345, signal.SIGKILL)

    def test_uses_pid_file_fallback(self, tmp_path):
        import dracs.sol as sol_module

        sol_module._conserver_process = None
        pid_file = tmp_path / "conserver.pid"
        pid_file.write_text("55555")
        with (
            patch("dracs.sol._pid_file_path", pid_file),
            patch("os.getpgid", return_value=55555),
            patch("os.killpg") as mock_killpg,
        ):
            stop_conserver()
        mock_killpg.assert_called_once_with(55555, signal.SIGTERM)
        assert not pid_file.exists()

    def test_handles_missing_pid_file(self, tmp_path):
        import dracs.sol as sol_module

        sol_module._conserver_process = None
        with patch("dracs.sol._pid_file_path", tmp_path / "nonexistent.pid"):
            stop_conserver()  # must not raise

    def test_killpg_raises_lookup_error(self, tmp_path):
        import dracs.sol as sol_module

        mock_proc = MagicMock()
        mock_proc.pid = 12345
        mock_proc.wait.side_effect = subprocess.TimeoutExpired([], 5)
        sol_module._conserver_process = mock_proc
        with (
            patch("dracs.sol._pid_file_path", tmp_path / "nonexistent.pid"),
            patch("os.getpgid", return_value=12345),
            patch("os.killpg", side_effect=ProcessLookupError()),
        ):
            stop_conserver()  # must not raise
        assert sol_module._conserver_process is None

    def test_handles_stale_pid_kill_error(self, tmp_path):
        import dracs.sol as sol_module

        sol_module._conserver_process = None
        pid_file = tmp_path / "conserver.pid"
        pid_file.write_text("55555")
        with (
            patch("dracs.sol._pid_file_path", pid_file),
            patch("os.getpgid", return_value=55555),
            patch("os.killpg", side_effect=ProcessLookupError()),
        ):
            stop_conserver()  # must not raise
        assert not pid_file.exists()

    def test_handles_corrupt_pid_file(self, tmp_path):
        import dracs.sol as sol_module

        sol_module._conserver_process = None
        pid_file = tmp_path / "conserver.pid"
        pid_file.write_text("not-a-number")
        with patch("dracs.sol._pid_file_path", pid_file):
            stop_conserver()  # must not raise (ValueError from int())
        assert not pid_file.exists()

    def test_handles_pid_file_read_error(self, tmp_path):
        import dracs.sol as sol_module

        sol_module._conserver_process = None
        pid_file = tmp_path / "conserver.pid"
        pid_file.write_text("55555")
        with (
            patch("dracs.sol._pid_file_path", pid_file),
            patch("pathlib.Path.read_text", side_effect=OSError("unreadable")),
        ):
            stop_conserver()  # must not raise (OSError from read_text)
        assert not pid_file.exists()

    def test_kills_orphaned_conserver_by_port(self, tmp_path):
        import dracs.sol as sol_module

        sol_module._conserver_process = None
        with (
            patch("dracs.sol._pid_file_path", tmp_path / "nonexistent.pid"),
            patch("dracs.sol._kill_conservers_on_port") as mock_kill,
        ):
            stop_conserver()
        mock_kill.assert_called_once_with("3109")

    def test_kills_orphaned_conserver_invalid_port_falls_back(self, tmp_path):
        import dracs.sol as sol_module

        sol_module._conserver_process = None
        with (
            patch("dracs.sol._pid_file_path", tmp_path / "nonexistent.pid"),
            patch("dracs.sol._kill_conservers_on_port") as mock_kill,
            patch.dict("os.environ", {"SOL_CONSERVER_PORT": "bad"}),
        ):
            stop_conserver()
        mock_kill.assert_called_once_with("3109")


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
            patch.object(ConserverPasswd, "_hash_password", return_value="ABhash"),
            patch(
                "dracs.snmp.build_idrac_hostname",
                return_value="mgmt-host01.example.com",
            ),
            patch("dracs.sol.disable_systemd_service") as mock_disable,
            patch("dracs.sol.start_conserver") as mock_start,
            patch(
                "dracs.sites.get_site_ini_config",
                return_value={"defaults": {}, "hosts": {}},
            ),
            patch("dracs.sites.set_site_ini_config"),
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

        ini_store = {}

        def fake_get(site_name):
            return ini_store.get(site_name, {"defaults": {}, "hosts": {}})

        def fake_set(site_name, cfg):
            ini_store[site_name] = cfg

        with (
            patch.object(ConserverPasswd, "_hash_password", return_value="ABhash"),
            patch("dracs.snmp.build_idrac_hostname", return_value="mgmt-host"),
            patch("dracs.sol.disable_systemd_service"),
            patch("dracs.sol.start_conserver"),
            patch("dracs.sites.get_site_ini_config", side_effect=fake_get),
            patch("dracs.sites.set_site_ini_config", side_effect=fake_set),
        ):
            startup(temp_db, None, cf, pw, logs)

        assert (
            ini_store.get("NewSite", {}).get("defaults", {}).get("conserver_password")
        )

    def _minimal_startup(self, tmp_path, temp_db, env_overrides=None):
        """Run startup() with minimal DB and all side-effectful calls mocked."""
        from dracs.db import Site, db_initialize, get_session

        db_initialize(temp_db)
        with get_session() as session:
            site = Site(
                name="S", is_primary=True, created_at=datetime.now().isoformat()
            )
            session.add(site)
            session.commit()

        cf = tmp_path / "conserver.cf"
        pw = tmp_path / "conserver.passwd"
        logs = tmp_path / "logs"

        patches = [
            patch.object(ConserverPasswd, "_hash_password", return_value="ABhash"),
            patch("dracs.sol.disable_systemd_service"),
            patch("dracs.sol.start_conserver"),
            patch("dracs.sites.get_site_ini_config", return_value={"defaults": {}, "hosts": {}}),
            patch("dracs.sites.set_site_ini_config"),
        ]
        env = env_overrides or {}
        with patch.dict("os.environ", env):
            ctx = __import__("contextlib").ExitStack()
            for p in patches:
                ctx.enter_context(p)
            with ctx:
                startup(temp_db, None, cf, pw, logs)
        return cf

    def test_invalid_primary_port_falls_back(self, tmp_path, temp_db):
        cf = self._minimal_startup(
            tmp_path, temp_db, {"SOL_CONSERVER_PORT": "notanumber"}
        )
        assert "primaryport 3109;" in cf.read_text()

    def test_invalid_secondary_port_falls_back(self, tmp_path, temp_db):
        cf = self._minimal_startup(
            tmp_path, temp_db, {"SOL_CONSERVER_SLAVE_PORT": "notanumber"}
        )
        assert "secondaryport 3110;" in cf.read_text()

    def test_handles_startup_exception(self, tmp_path):
        with patch("dracs.db.db_initialize", side_effect=Exception("DB error")):
            startup("bad_path", None, tmp_path / "cf", tmp_path / "pw", tmp_path)

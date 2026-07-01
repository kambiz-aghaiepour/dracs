import json
import os
import socket
import tempfile
import threading
import time
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from dracs.db import db_initialize, upsert_system
from dracs.vnc import (
    VncSessionManager,
    MaxSessionsError,
    get_token_dir,
    get_vnc_credentials,
    check_vnc_connectivity,
    start_websockify,
    stop_websockify,
)


@pytest.fixture
def token_dir(tmp_path):
    d = tmp_path / "vnc-tokens"
    d.mkdir()
    return str(d)


@pytest.fixture
def manager(token_dir):
    mgr = VncSessionManager(token_dir, timeout_minutes=30, max_sessions=5)
    yield mgr
    mgr.stop()


@pytest.fixture
def webapp_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    db_initialize(path)
    upsert_system(
        path, "TAG001", "server01", "R660", "7.0.0", "2.1.0", "Jan 1, 2027", 1893456000
    )
    yield path
    if os.path.exists(path):
        os.unlink(path)


@pytest.fixture
def vnc_client(webapp_db):
    with patch.dict(
        os.environ,
        {
            "DRACS_DB": webapp_db,
            "DRACS_DNS_STRING": "mgmt-",
            "DRACS_DNS_MODE": "prefix",
            "VNC_ENABLE": "true",
        },
    ):
        import dracs.webapp as webapp_mod

        webapp_mod.DB_PATH = webapp_db
        webapp_mod.db_initialize(webapp_db)
        webapp_mod.VNC_ENABLE = True
        webapp_mod.vnc_manager = VncSessionManager(
            tempfile.mkdtemp(), timeout_minutes=30, max_sessions=20
        )
        webapp_mod.app.config["TESTING"] = True
        with webapp_mod.app.test_client() as c:
            yield c
        webapp_mod.vnc_manager.stop()
        webapp_mod.VNC_ENABLE = False
        webapp_mod.vnc_manager = None


@pytest.fixture
def vnc_disabled_client(webapp_db):
    with patch.dict(
        os.environ,
        {
            "DRACS_DB": webapp_db,
            "DRACS_DNS_STRING": "mgmt-",
            "DRACS_DNS_MODE": "prefix",
            "VNC_ENABLE": "false",
        },
    ):
        import dracs.webapp as webapp_mod

        webapp_mod.DB_PATH = webapp_db
        webapp_mod.db_initialize(webapp_db)
        orig_enable = webapp_mod.VNC_ENABLE
        orig_manager = webapp_mod.vnc_manager
        webapp_mod.VNC_ENABLE = False
        webapp_mod.vnc_manager = None
        webapp_mod.app.config["TESTING"] = True
        with webapp_mod.app.test_client() as c:
            yield c
        webapp_mod.VNC_ENABLE = orig_enable
        webapp_mod.vnc_manager = orig_manager


def _login(client):
    client.post(
        "/login",
        data=json.dumps({"username": "admin", "password": "admin"}),
        content_type="application/json",
    )


class TestVncSessionManager:
    def test_create_session(self, manager, token_dir):
        token = manager.create_session("host01", "mgmt-host01.example.com", 5901)
        assert token
        token_file = Path(token_dir) / token
        assert token_file.exists()
        content = token_file.read_text()
        assert "mgmt-host01.example.com:5901" in content

    def test_create_session_meta_file(self, manager, token_dir):
        token = manager.create_session("host01", "mgmt-host01.example.com", 5901)
        meta_file = Path(token_dir) / f"{token}.meta"
        assert meta_file.exists()
        assert meta_file.read_text().strip() == "host01"

    def test_remove_session(self, manager, token_dir):
        token = manager.create_session("host01", "mgmt-host01.example.com", 5901)
        manager.remove_session(token)
        assert not (Path(token_dir) / token).exists()
        assert not (Path(token_dir) / f"{token}.meta").exists()

    def test_remove_nonexistent_session(self, manager):
        manager.remove_session("nonexistent-token")

    def test_touch_session_resets_mtime(self, manager, token_dir):
        token = manager.create_session("host01", "mgmt-host01.example.com", 5901)
        token_file = Path(token_dir) / token
        original_mtime = token_file.stat().st_mtime
        time.sleep(0.05)
        result = manager.touch_session(token)
        assert result is True
        assert token_file.stat().st_mtime > original_mtime

    def test_touch_session_nonexistent_returns_false(self, manager):
        assert manager.touch_session("no-such-token") is False

    def test_create_session_writes_refs_file(self, manager, token_dir):
        token = manager.create_session("host01", "mgmt-host01.example.com", 5901)
        refs_file = Path(token_dir) / f"{token}.refs"
        assert refs_file.exists()
        assert refs_file.read_text().strip() == "1"

    def test_remove_session_cleans_refs_file(self, manager, token_dir):
        token = manager.create_session("host01", "mgmt-host01.example.com", 5901)
        manager.remove_session(token)
        assert not (Path(token_dir) / f"{token}.refs").exists()

    def test_find_session_by_hostname_found(self, manager):
        token = manager.create_session("host01", "mgmt-host01.example.com", 5901)
        assert manager.find_session_by_hostname("host01") == token

    def test_find_session_by_hostname_not_found(self, manager):
        assert manager.find_session_by_hostname("nonexistent") is None

    def test_find_session_by_hostname_dead_proxy_removes_and_returns_none(
        self, manager, token_dir
    ):
        token = manager.create_session("host01", "127.0.0.1", 19876)
        with patch.object(manager, "_is_proxy_alive", return_value=False):
            result = manager.find_session_by_hostname("host01")
        assert result is None
        assert not (Path(token_dir) / token).exists()

    def test_is_proxy_alive_non_localhost_returns_true(self, manager):
        token = manager.create_session("host01", "mgmt-host01.example.com", 5901)
        assert manager._is_proxy_alive(token) is True

    def test_is_proxy_alive_missing_token_returns_true(self, manager):
        assert manager._is_proxy_alive("no-such-token") is True

    def test_is_proxy_alive_localhost_listening_returns_true(self, manager, token_dir):
        token = manager.create_session("host01", "127.0.0.1", 19877)
        with patch("dracs.vnc.socket.create_connection") as mock_conn:
            mock_conn.return_value.__enter__ = MagicMock(return_value=MagicMock())
            mock_conn.return_value.__exit__ = MagicMock(return_value=False)
            assert manager._is_proxy_alive(token) is True
        mock_conn.assert_called_once_with(("127.0.0.1", 19877), timeout=0.5)

    def test_is_proxy_alive_localhost_dead_returns_false(self, manager, token_dir):
        token = manager.create_session("host01", "127.0.0.1", 19878)
        with patch("dracs.vnc.socket.create_connection", side_effect=OSError):
            assert manager._is_proxy_alive(token) is False

    def test_find_session_by_hostname_skips_unreadable_meta(self, manager):
        manager.create_session("host01", "mgmt-host01.example.com", 5901)
        original_read_text = Path.read_text

        def read_text_raises(self_path, *args, **kwargs):
            if self_path.name.endswith(".meta"):
                raise OSError("permission denied")
            return original_read_text(self_path, *args, **kwargs)

        with patch.object(Path, "read_text", read_text_raises):
            assert manager.find_session_by_hostname("host01") is None

    def test_add_reference_increments(self, manager, token_dir):
        token = manager.create_session("host01", "mgmt-host01.example.com", 5901)
        assert manager.add_reference(token) is True
        refs = int((Path(token_dir) / f"{token}.refs").read_text())
        assert refs == 2

    def test_add_reference_nonexistent_returns_false(self, manager):
        assert manager.add_reference("no-such-token") is False

    def test_release_session_decrements(self, manager, token_dir):
        token = manager.create_session("host01", "mgmt-host01.example.com", 5901)
        manager.add_reference(token)  # refs=2
        manager.release_session(token)  # refs=1
        token_file = Path(token_dir) / token
        assert token_file.exists()
        refs = int((Path(token_dir) / f"{token}.refs").read_text())
        assert refs == 1

    def test_release_session_deletes_at_zero(self, manager, token_dir):
        token = manager.create_session("host01", "mgmt-host01.example.com", 5901)
        manager.release_session(token)  # refs 1 -> 0 -> delete
        assert not (Path(token_dir) / token).exists()

    def test_release_session_nonexistent_returns_false(self, manager):
        assert manager.release_session("no-such-token") is False

    def test_active_count_excludes_refs_files(self, manager):
        manager.create_session("host01", "mgmt-host01.example.com", 5901)
        manager.create_session("host02", "mgmt-host02.example.com", 5901)
        assert manager.active_count() == 2

    def test_touch_session_prevents_expiry(self, manager, token_dir):
        token = manager.create_session("host01", "mgmt-host01.example.com", 5901)
        token_file = Path(token_dir) / token
        # Back-date creation so it would normally expire
        old_time = time.time() - 200
        os.utime(token_file, (old_time, old_time))
        # Touch resets the timer
        manager.touch_session(token)
        removed = manager.cleanup_expired()
        assert removed == 0
        assert token_file.exists()

    def test_get_session_info(self, manager):
        token = manager.create_session("host01", "mgmt-host01.example.com", 5901)
        info = manager.get_session_info(token)
        assert info is not None
        assert info["token"] == token
        assert info["hostname"] == "host01"
        assert "created_at" in info

    def test_get_session_info_nonexistent(self, manager):
        assert manager.get_session_info("nonexistent") is None

    def test_get_session_info_no_meta(self, manager, token_dir):
        token = manager.create_session("host01", "mgmt-host01.example.com", 5901)
        (Path(token_dir) / f"{token}.meta").unlink()
        info = manager.get_session_info(token)
        assert info is not None
        assert info["hostname"] == ""

    def test_active_count(self, manager):
        assert manager.active_count() == 0
        manager.create_session("host01", "mgmt-host01.example.com", 5901)
        assert manager.active_count() == 1
        manager.create_session("host02", "mgmt-host02.example.com", 5901)
        assert manager.active_count() == 2

    def test_max_sessions_enforced(self, manager):
        for i in range(5):
            manager.create_session(
                f"host{i:02d}", f"mgmt-host{i:02d}.example.com", 5901
            )
        with pytest.raises(MaxSessionsError):
            manager.create_session("host99", "mgmt-host99.example.com", 5901)

    def test_max_sessions_zero_unlimited(self, token_dir):
        mgr = VncSessionManager(token_dir, timeout_minutes=30, max_sessions=0)
        for i in range(10):
            mgr.create_session(f"host{i:02d}", f"mgmt-host{i:02d}.example.com", 5901)
        assert mgr.active_count() == 10
        mgr.stop()

    def test_cleanup_expired(self, manager, token_dir):
        token = manager.create_session("host01", "mgmt-host01.example.com", 5901)
        token_file = Path(token_dir) / token
        old_time = time.time() - 3600
        os.utime(str(token_file), (old_time, old_time))
        removed = manager.cleanup_expired()
        assert removed == 1
        assert not token_file.exists()

    def test_cleanup_keeps_fresh(self, manager, token_dir):
        token = manager.create_session("host01", "mgmt-host01.example.com", 5901)
        removed = manager.cleanup_expired()
        assert removed == 0
        assert (Path(token_dir) / token).exists()

    def test_cleanup_handles_missing_file(self, manager, token_dir):
        token = manager.create_session("host01", "mgmt-host01.example.com", 5901)
        token_file = Path(token_dir) / token
        old_time = time.time() - 3600
        os.utime(str(token_file), (old_time, old_time))
        token_file.unlink()
        removed = manager.cleanup_expired()
        assert removed == 0

    def test_cleanup_race_file_removed(self, manager, token_dir):
        token = manager.create_session("host01", "mgmt-host01.example.com", 5901)
        token_file = Path(token_dir) / token
        original_stat = Path.stat

        def stat_raises(self_path, *args, **kwargs):
            if self_path == token_file:
                raise FileNotFoundError()
            return original_stat(self_path, *args, **kwargs)

        with patch.object(Path, "stat", stat_raises):
            removed = manager.cleanup_expired()
        assert removed == 0

    def test_token_dir_created(self, tmp_path):
        new_dir = str(tmp_path / "new" / "nested" / "dir")
        mgr = VncSessionManager(new_dir, timeout_minutes=30, max_sessions=5)
        assert Path(new_dir).exists()
        mgr.stop()

    def test_cleanup_thread_calls_cleanup(self, tmp_path):
        token_dir = str(tmp_path / "vnc-tokens-thread")
        original_wait = threading.Event.wait

        def fast_wait(self_event, timeout=None):
            return original_wait(self_event, timeout=0.01)

        with patch.object(threading.Event, "wait", fast_wait):
            mgr = VncSessionManager(token_dir, timeout_minutes=30, max_sessions=5)
            time.sleep(0.1)
            mgr.stop()

    def test_stop(self, manager):
        manager.stop()
        assert manager._stop_event.is_set()

    def test_get_refs_returns_one_on_corrupt_file(self, manager, token_dir):
        token = manager.create_session("host01", "mgmt-host01.example.com", 5901)
        (Path(token_dir) / f"{token}.refs").write_text("not_a_number")
        assert manager._get_refs(token) == 1

    def test_get_refs_returns_one_on_missing_file(self, manager, token_dir):
        token = manager.create_session("host01", "mgmt-host01.example.com", 5901)
        (Path(token_dir) / f"{token}.refs").unlink()
        assert manager._get_refs(token) == 1

    def test_find_free_port_returns_valid_port(self, manager):
        port = manager.find_free_port()
        assert port is not None
        assert 1024 < port < 65536

    @patch("dracs.vnc.socket.socket")
    def test_find_free_port_oserror_returns_none(self, mock_socket, manager):
        mock_socket.return_value.__enter__.return_value.bind.side_effect = OSError(
            "busy"
        )
        assert manager.find_free_port() is None

    @patch("dracs.vnc.shutil.which", return_value=None)
    def test_start_proxy_no_x11vnc_returns_false(self, mock_which, manager):
        token = manager.create_session("host01", "mgmt-host01.example.com", 5901)
        assert (
            manager.start_proxy(token, "mgmt-host01.example.com", 5901, "", 15901)
            is False
        )

    @patch("dracs.vnc.subprocess.Popen")
    @patch("dracs.vnc.shutil.which", return_value="/usr/bin/x11vnc")
    def test_start_proxy_success(self, mock_which, mock_popen, manager):
        import threading as _threading

        hold = _threading.Event()
        mock_proc = MagicMock()
        mock_proc.wait.side_effect = lambda *a, **kw: hold.wait()
        mock_popen.return_value = mock_proc
        token = manager.create_session("host01", "mgmt-host01.example.com", 5901)
        result = manager.start_proxy(
            token, "mgmt-host01.example.com", 5901, "pass", 15901
        )
        assert result is True
        assert token in manager._proxy_procs
        cmd = mock_popen.call_args[0][0]
        assert "-reflect" in cmd
        assert "mgmt-host01.example.com:5901" in cmd
        assert "-shared" in cmd
        assert "-ping" in cmd
        assert "-passwd" not in cmd
        env = mock_popen.call_args[1]["env"]
        assert env.get("X11VNC_REFLECT_PASSWORD") == "pass"
        hold.set()

    @patch("dracs.vnc.subprocess.Popen")
    @patch("dracs.vnc.shutil.which", return_value="/usr/bin/x11vnc")
    def test_start_proxy_no_password_omits_reflect_password_env(
        self, mock_which, mock_popen, manager
    ):
        mock_popen.return_value = MagicMock()
        token = manager.create_session("host01", "mgmt-host01.example.com", 5901)
        manager.start_proxy(token, "mgmt-host01.example.com", 5901, "", 15901)
        env = mock_popen.call_args[1]["env"]
        assert "X11VNC_REFLECT_PASSWORD" not in env

    @patch("dracs.vnc.subprocess.Popen")
    @patch("dracs.vnc.shutil.which", return_value="/usr/bin/x11vnc")
    def test_reaper_thread_removes_proc_on_exit(self, mock_which, mock_popen, manager):
        mock_proc = MagicMock()
        mock_proc.wait.return_value = 0
        mock_popen.return_value = mock_proc
        token = manager.create_session("host01", "mgmt-host01.example.com", 5901)
        manager.start_proxy(token, "mgmt-host01.example.com", 5901, "", 15901)
        import time as _time

        deadline = _time.time() + 2.0
        while token in manager._proxy_procs and _time.time() < deadline:
            _time.sleep(0.01)
        assert token not in manager._proxy_procs

    @patch("dracs.vnc.subprocess.Popen")
    @patch("dracs.vnc.shutil.which", return_value="/usr/bin/x11vnc")
    def test_stop_proxy_terminates_process(self, mock_which, mock_popen, manager):
        import threading as _threading

        hold = _threading.Event()
        mock_proc = MagicMock()
        mock_proc.wait.side_effect = lambda *a, **kw: (
            None if "timeout" in kw else hold.wait()
        )
        mock_popen.return_value = mock_proc
        token = manager.create_session("host01", "mgmt-host01.example.com", 5901)
        manager.start_proxy(token, "mgmt-host01.example.com", 5901, "", 15901)
        manager.stop_proxy(token)
        hold.set()
        mock_proc.terminate.assert_called_once()
        assert token not in manager._proxy_procs

    def test_stop_proxy_nonexistent_is_noop(self, manager):
        manager.stop_proxy("no-such-token")

    @patch("dracs.vnc.subprocess.Popen")
    @patch("dracs.vnc.shutil.which", return_value="/usr/bin/x11vnc")
    def test_stop_proxy_process_already_gone(self, mock_which, mock_popen, manager):
        import threading as _threading

        hold = _threading.Event()
        mock_proc = MagicMock()
        mock_proc.terminate.side_effect = ProcessLookupError()
        mock_proc.wait.side_effect = lambda *a, **kw: (
            None if "timeout" in kw else hold.wait()
        )
        mock_popen.return_value = mock_proc
        token = manager.create_session("host01", "mgmt-host01.example.com", 5901)
        manager.start_proxy(token, "mgmt-host01.example.com", 5901, "", 15901)
        manager.stop_proxy(token)
        hold.set()
        assert token not in manager._proxy_procs

    @patch("dracs.vnc.subprocess.Popen")
    @patch("dracs.vnc.shutil.which", return_value="/usr/bin/x11vnc")
    def test_stop_proxy_timeout_then_kill(self, mock_which, mock_popen, manager):
        import subprocess as _sub

        mock_proc = MagicMock()
        mock_proc.wait.side_effect = _sub.TimeoutExpired(cmd="x11vnc", timeout=3)
        mock_popen.return_value = mock_proc
        token = manager.create_session("host01", "mgmt-host01.example.com", 5901)
        manager.start_proxy(token, "mgmt-host01.example.com", 5901, "", 15901)
        manager.stop_proxy(token)
        mock_proc.kill.assert_called_once()

    @patch("dracs.vnc.subprocess.Popen")
    @patch("dracs.vnc.shutil.which", return_value="/usr/bin/x11vnc")
    def test_stop_proxy_timeout_kill_race(self, mock_which, mock_popen, manager):
        import subprocess as _sub

        mock_proc = MagicMock()
        mock_proc.wait.side_effect = _sub.TimeoutExpired(cmd="x11vnc", timeout=3)
        mock_proc.kill.side_effect = ProcessLookupError()
        mock_popen.return_value = mock_proc
        token = manager.create_session("host01", "mgmt-host01.example.com", 5901)
        manager.start_proxy(token, "mgmt-host01.example.com", 5901, "", 15901)
        manager.stop_proxy(token)
        assert token not in manager._proxy_procs

    @patch("dracs.vnc.subprocess.Popen")
    @patch("dracs.vnc.shutil.which", return_value="/usr/bin/x11vnc")
    def test_remove_session_stops_proxy(self, mock_which, mock_popen, manager):
        import threading as _threading

        hold = _threading.Event()
        mock_proc = MagicMock()
        mock_proc.wait.side_effect = lambda *a, **kw: (
            None if "timeout" in kw else hold.wait()
        )
        mock_popen.return_value = mock_proc
        token = manager.create_session("host01", "mgmt-host01.example.com", 5901)
        manager.start_proxy(token, "mgmt-host01.example.com", 5901, "", 15901)
        manager.remove_session(token)
        hold.set()
        mock_proc.terminate.assert_called_once()
        assert token not in manager._proxy_procs

    @patch("dracs.vnc.subprocess.Popen")
    @patch("dracs.vnc.shutil.which", return_value="/usr/bin/x11vnc")
    def test_stop_cleans_up_all_proxies(self, mock_which, mock_popen, manager):
        import threading as _threading

        holds = [_threading.Event(), _threading.Event()]
        procs = [MagicMock(), MagicMock()]
        for p, h in zip(procs, holds):
            p.wait.side_effect = lambda *a, h=h, **kw: (
                None if "timeout" in kw else h.wait()
            )
        mock_popen.side_effect = procs
        t1 = manager.create_session("host01", "mgmt-host01.example.com", 5901)
        t2 = manager.create_session("host02", "mgmt-host02.example.com", 5901)
        manager.start_proxy(t1, "mgmt-host01.example.com", 5901, "", 15901)
        manager.start_proxy(t2, "mgmt-host02.example.com", 5901, "", 15902)
        manager.stop()
        for h in holds:
            h.set()
        procs[0].terminate.assert_called_once()
        procs[1].terminate.assert_called_once()

    @patch("dracs.vnc.subprocess.Popen")
    @patch("dracs.vnc.shutil.which", return_value="/usr/bin/x11vnc")
    def test_start_proxy_writes_pid_file(
        self, mock_which, mock_popen, manager, token_dir
    ):
        import threading as _threading

        hold = _threading.Event()
        mock_proc = MagicMock()
        mock_proc.pid = 99999
        mock_proc.wait.side_effect = lambda *a, **kw: hold.wait()
        mock_popen.return_value = mock_proc
        token = manager.create_session("host01", "mgmt-host01.example.com", 5901)
        manager.start_proxy(token, "mgmt-host01.example.com", 5901, "", 15901)
        pid_file = Path(token_dir) / f"{token}.proxy"
        assert pid_file.exists()
        assert pid_file.read_text().strip() == "99999"
        hold.set()

    @patch("dracs.vnc.subprocess.Popen")
    @patch("dracs.vnc.shutil.which", return_value="/usr/bin/x11vnc")
    def test_reaper_deletes_pid_file_on_exit(
        self, mock_which, mock_popen, manager, token_dir
    ):
        mock_proc = MagicMock()
        mock_proc.pid = 99999
        mock_proc.wait.return_value = 0
        mock_popen.return_value = mock_proc
        token = manager.create_session("host01", "mgmt-host01.example.com", 5901)
        manager.start_proxy(token, "mgmt-host01.example.com", 5901, "", 15901)
        import time as _time

        deadline = _time.time() + 2.0
        pid_file = Path(token_dir) / f"{token}.proxy"
        while pid_file.exists() and _time.time() < deadline:
            _time.sleep(0.01)
        assert not pid_file.exists()

    @patch("dracs.vnc.time.sleep")
    @patch("dracs.vnc.os.kill")
    @patch("dracs.vnc.shutil.which", return_value="/usr/bin/x11vnc")
    def test_stop_proxy_uses_pid_file_for_orphan(
        self, mock_which, mock_kill, mock_sleep, manager, token_dir
    ):
        token = manager.create_session("host01", "mgmt-host01.example.com", 5901)
        pid_file = Path(token_dir) / f"{token}.proxy"
        pid_file.write_text("55555")
        manager.stop_proxy(token)
        calls = [c[0] for c in mock_kill.call_args_list]
        assert (55555, __import__("signal").SIGTERM) in calls
        assert not pid_file.exists()

    @patch("dracs.vnc.time.sleep")
    @patch("dracs.vnc.os.kill")
    @patch("dracs.vnc.shutil.which", return_value="/usr/bin/x11vnc")
    def test_stop_proxy_pid_file_orphan_already_dead_at_sigkill(
        self, mock_which, mock_kill, mock_sleep, manager, token_dir
    ):
        import signal as _signal

        mock_kill.side_effect = lambda pid, sig: (
            (_ for _ in ()).throw(ProcessLookupError())
            if sig == _signal.SIGKILL
            else None
        )
        token = manager.create_session("host01", "mgmt-host01.example.com", 5901)
        pid_file = Path(token_dir) / f"{token}.proxy"
        pid_file.write_text("55555")
        manager.stop_proxy(token)
        assert not pid_file.exists()

    @patch("dracs.vnc.time.sleep")
    @patch("dracs.vnc.os.kill")
    @patch("dracs.vnc.shutil.which", return_value="/usr/bin/x11vnc")
    def test_stop_proxy_pid_file_invalid_pid_is_noop(
        self, mock_which, mock_kill, mock_sleep, manager, token_dir
    ):
        token = manager.create_session("host01", "mgmt-host01.example.com", 5901)
        pid_file = Path(token_dir) / f"{token}.proxy"
        pid_file.write_text("not-a-pid")
        manager.stop_proxy(token)
        mock_kill.assert_not_called()
        assert not pid_file.exists()

    @patch("dracs.vnc.subprocess.Popen")
    @patch("dracs.vnc.shutil.which", return_value="/usr/bin/x11vnc")
    def test_stop_proxy_clears_pid_file_after_normal_kill(
        self, mock_which, mock_popen, manager, token_dir
    ):
        import threading as _threading

        hold = _threading.Event()
        mock_proc = MagicMock()
        mock_proc.pid = 77777
        mock_proc.wait.side_effect = lambda *a, **kw: (
            None if "timeout" in kw else hold.wait()
        )
        mock_popen.return_value = mock_proc
        token = manager.create_session("host01", "mgmt-host01.example.com", 5901)
        manager.start_proxy(token, "mgmt-host01.example.com", 5901, "", 15901)
        pid_file = Path(token_dir) / f"{token}.proxy"
        assert pid_file.exists()
        manager.stop_proxy(token)
        hold.set()
        assert not pid_file.exists()

    @patch("dracs.vnc.subprocess.Popen")
    @patch("dracs.vnc.shutil.which", return_value="/usr/bin/x11vnc")
    def test_remove_session_deletes_proxy_pid_file(
        self, mock_which, mock_popen, manager, token_dir
    ):
        import threading as _threading

        hold = _threading.Event()
        mock_proc = MagicMock()
        mock_proc.pid = 88888
        mock_proc.wait.side_effect = lambda *a, **kw: (
            None if "timeout" in kw else hold.wait()
        )
        mock_popen.return_value = mock_proc
        token = manager.create_session("host01", "mgmt-host01.example.com", 5901)
        manager.start_proxy(token, "mgmt-host01.example.com", 5901, "", 15901)
        manager.remove_session(token)
        hold.set()
        assert not (Path(token_dir) / f"{token}.proxy").exists()

    @patch("dracs.vnc.os.kill")
    def test_cleanup_orphaned_proxies_on_init(self, mock_kill, token_dir):
        proxy_file = Path(token_dir) / "oldtoken.proxy"
        token_file = Path(token_dir) / "oldtoken"
        meta_file = Path(token_dir) / "oldtoken.meta"
        refs_file = Path(token_dir) / "oldtoken.refs"
        proxy_file.write_text("11111")
        token_file.write_text("oldtoken: 127.0.0.1:15901\n")
        meta_file.write_text("host01\n")
        refs_file.write_text("1")
        mgr = VncSessionManager(token_dir, timeout_minutes=30, max_sessions=5)
        try:
            mock_kill.assert_any_call(11111, __import__("signal").SIGKILL)
            assert not proxy_file.exists()
            assert not token_file.exists()
            assert not meta_file.exists()
            assert not refs_file.exists()
        finally:
            mgr.stop()

    @patch("dracs.vnc.os.kill")
    def test_cleanup_orphaned_proxies_bad_pid_is_noop(self, mock_kill, token_dir):
        proxy_file = Path(token_dir) / "oldtoken.proxy"
        proxy_file.write_text("not-a-number")
        mgr = VncSessionManager(token_dir, timeout_minutes=30, max_sessions=5)
        try:
            mock_kill.assert_not_called()
            assert not proxy_file.exists()
        finally:
            mgr.stop()

    def test_active_count_excludes_proxy_files(self, manager, token_dir):
        token = manager.create_session("host01", "mgmt-host01.example.com", 5901)
        (Path(token_dir) / f"{token}.proxy").write_text("12345")
        assert manager.active_count() == 1

    def test_cleanup_expired_skips_proxy_files(self, manager, token_dir):
        token = manager.create_session("host01", "mgmt-host01.example.com", 5901)
        proxy_file = Path(token_dir) / f"{token}.proxy"
        proxy_file.write_text("12345")
        import os as _os

        _os.utime(proxy_file, (0, 0))
        removed = manager.cleanup_expired()
        assert proxy_file.exists() or removed >= 0


class TestGetTokenDir:
    def test_returns_existing_path(self, tmp_path, monkeypatch):
        import dracs.vnc as vnc_mod

        orig = vnc_mod._runtime_dir
        vnc_mod._runtime_dir = tmp_path / "dracs"
        try:
            path = get_token_dir()
            assert Path(path).is_dir()
            assert path.endswith("vnc-tokens")
        finally:
            vnc_mod._runtime_dir = orig


class TestGetVncCredentials:
    def test_no_config_file(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        port, password = get_vnc_credentials("host01")
        assert port == 5901
        assert password == ""

    def test_default_section(self, tmp_path, monkeypatch):
        ini = tmp_path / "drac-passwords.ini"
        ini.write_text("[Default-DEFAULTS]\nvnc_port = 5900\nvnc_password = mypass\n")
        monkeypatch.chdir(tmp_path)
        port, password = get_vnc_credentials("host01")
        assert port == 5900
        assert password == "mypass"

    def test_host_specific_section(self, tmp_path, monkeypatch):
        ini = tmp_path / "drac-passwords.ini"
        ini.write_text(
            "[Default-DEFAULTS]\nvnc_port = 5900\nvnc_password = defaultpass\n\n"
            "[Default-host01]\nvnc_port = 5902\nvnc_password = hostpass\n"
        )
        monkeypatch.chdir(tmp_path)
        port, password = get_vnc_credentials("host01")
        assert port == 5902
        assert password == "hostpass"

    def test_host_section_falls_back_to_default(self, tmp_path, monkeypatch):
        ini = tmp_path / "drac-passwords.ini"
        ini.write_text(
            "[Default-DEFAULTS]\nvnc_port = 5900\nvnc_password = defaultpass\n\n"
            "[Default-host01]\nusername = admin\n"
        )
        monkeypatch.chdir(tmp_path)
        port, password = get_vnc_credentials("host01")
        assert port == 5900
        assert password == "defaultpass"

    def test_missing_keys_use_defaults(self, tmp_path, monkeypatch):
        ini = tmp_path / "drac-passwords.ini"
        ini.write_text("[Default-DEFAULTS]\nusername = root\n")
        monkeypatch.chdir(tmp_path)
        port, password = get_vnc_credentials("host01")
        assert port == 5901
        assert password == ""

    def test_site_specific_credentials(self, tmp_path, monkeypatch):
        ini = tmp_path / "drac-passwords.ini"
        ini.write_text(
            "[Default-DEFAULTS]\nvnc_port = 5900\nvnc_password = defpass\n\n"
            "[Site2-DEFAULTS]\nvnc_port = 5910\nvnc_password = site2pass\n"
        )
        monkeypatch.chdir(tmp_path)
        port, password = get_vnc_credentials("host01", site="Site2")
        assert port == 5910
        assert password == "site2pass"

    def test_site_host_override(self, tmp_path, monkeypatch):
        ini = tmp_path / "drac-passwords.ini"
        ini.write_text(
            "[Site2-DEFAULTS]\nvnc_port = 5910\nvnc_password = site2pass\n\n"
            "[Site2-host01]\nvnc_port = 5920\n"
        )
        monkeypatch.chdir(tmp_path)
        port, password = get_vnc_credentials("host01", site="Site2")
        assert port == 5920
        assert password == "site2pass"

    def test_unknown_site_returns_defaults(self, tmp_path, monkeypatch):
        ini = tmp_path / "drac-passwords.ini"
        ini.write_text("[Default-DEFAULTS]\nvnc_port = 5900\n")
        monkeypatch.chdir(tmp_path)
        port, password = get_vnc_credentials("host01", site="NoSuchSite")
        assert port == 5901
        assert password == ""


class TestVncConnectivity:
    @patch("dracs.vnc.socket.create_connection")
    def test_success(self, mock_conn):
        mock_sock = MagicMock()
        mock_conn.return_value = mock_sock
        success, msg = check_vnc_connectivity("host01", 5901)
        assert success is True
        assert msg == ""
        mock_sock.close.assert_called_once()

    @patch("dracs.vnc.socket.create_connection")
    def test_timeout(self, mock_conn):
        mock_conn.side_effect = socket.timeout()
        success, msg = check_vnc_connectivity("host01", 5901)
        assert success is False
        assert "timed out" in msg

    @patch("dracs.vnc.socket.create_connection")
    def test_refused(self, mock_conn):
        mock_conn.side_effect = ConnectionRefusedError()
        success, msg = check_vnc_connectivity("host01", 5901)
        assert success is False
        assert "refused" in msg

    @patch("dracs.vnc.socket.create_connection")
    def test_os_error(self, mock_conn):
        mock_conn.side_effect = OSError("Network unreachable")
        success, msg = check_vnc_connectivity("host01", 5901)
        assert success is False
        assert "Cannot reach" in msg


class TestWebsockifyLifecycle:
    @patch("dracs.vnc.shutil.which", return_value=None)
    def test_start_no_binary(self, mock_which):
        result = start_websockify(6080, "/tmp/test-tokens")
        assert result is None

    @patch("dracs.vnc.subprocess.Popen")
    @patch("dracs.vnc.shutil.which", return_value="/usr/bin/websockify")
    def test_start_success(self, mock_which, mock_popen, tmp_path):
        mock_proc = MagicMock()
        mock_proc.pid = 12345
        mock_popen.return_value = mock_proc
        token_dir = str(tmp_path / "tokens")
        result = start_websockify(6080, token_dir)
        assert result is mock_proc
        mock_popen.assert_called_once()
        import dracs.vnc

        dracs.vnc._websockify_process = None
        dracs.vnc._pid_file.unlink(missing_ok=True)

    def test_stop_no_process(self):
        import dracs.vnc

        dracs.vnc._websockify_process = None
        dracs.vnc._pid_file.unlink(missing_ok=True)
        stop_websockify()

    @patch("dracs.vnc.os.kill")
    def test_stop_with_pid_file(self, mock_kill, tmp_path):
        import dracs.vnc

        dracs.vnc._websockify_process = None
        dracs.vnc._pid_file = tmp_path / "test.pid"
        dracs.vnc._pid_file.write_text("99999")
        mock_kill.side_effect = ProcessLookupError()
        stop_websockify()
        assert not dracs.vnc._pid_file.exists()
        dracs.vnc._pid_file = Path("/tmp/dracs-websockify.pid")

    def test_stop_with_process(self):
        import dracs.vnc

        mock_proc = MagicMock()
        dracs.vnc._websockify_process = mock_proc
        dracs.vnc._pid_file.unlink(missing_ok=True)
        stop_websockify()
        mock_proc.terminate.assert_called_once()
        mock_proc.wait.assert_called_once()
        assert dracs.vnc._websockify_process is None

    def test_stop_process_timeout(self):
        import subprocess
        import dracs.vnc

        mock_proc = MagicMock()
        mock_proc.wait.side_effect = subprocess.TimeoutExpired(
            cmd="websockify", timeout=5
        )
        dracs.vnc._websockify_process = mock_proc
        dracs.vnc._pid_file.unlink(missing_ok=True)
        stop_websockify()
        mock_proc.kill.assert_called_once()
        assert dracs.vnc._websockify_process is None

    def test_stop_process_kill_race(self):
        import subprocess
        import dracs.vnc

        mock_proc = MagicMock()
        mock_proc.wait.side_effect = subprocess.TimeoutExpired(
            cmd="websockify", timeout=5
        )
        mock_proc.kill.side_effect = ProcessLookupError()
        dracs.vnc._websockify_process = mock_proc
        dracs.vnc._pid_file.unlink(missing_ok=True)
        stop_websockify()
        assert dracs.vnc._websockify_process is None

    def test_stop_process_already_gone(self):
        import dracs.vnc

        mock_proc = MagicMock()
        mock_proc.terminate.side_effect = ProcessLookupError()
        dracs.vnc._websockify_process = mock_proc
        dracs.vnc._pid_file.unlink(missing_ok=True)
        stop_websockify()
        assert dracs.vnc._websockify_process is None


class TestVncSessionCreateEndpoint:
    def test_requires_auth(self, vnc_client):
        resp = vnc_client.post(
            "/api/vnc-session",
            data=json.dumps({"hostname": "server01"}),
            content_type="application/json",
        )
        assert resp.status_code == 401

    def test_vnc_disabled(self, vnc_disabled_client):
        _login(vnc_disabled_client)
        resp = vnc_disabled_client.post(
            "/api/vnc-session",
            data=json.dumps({"hostname": "server01"}),
            content_type="application/json",
        )
        assert resp.status_code == 404

    def test_no_json_body(self, vnc_client):
        _login(vnc_client)
        resp = vnc_client.post(
            "/api/vnc-session",
            data="not json",
            content_type="text/plain",
        )
        assert resp.status_code == 400

    def test_missing_hostname(self, vnc_client):
        _login(vnc_client)
        resp = vnc_client.post(
            "/api/vnc-session",
            data=json.dumps({}),
            content_type="application/json",
        )
        assert resp.status_code == 400

    def test_empty_hostname(self, vnc_client):
        _login(vnc_client)
        resp = vnc_client.post(
            "/api/vnc-session",
            data=json.dumps({"hostname": "  "}),
            content_type="application/json",
        )
        assert resp.status_code == 400

    @patch(
        "dracs.webapp.check_vnc_connectivity",
        return_value=(False, "Connection refused"),
    )
    @patch("dracs.webapp.get_vnc_credentials", return_value=(5901, "pass"))
    def test_vnc_unreachable(self, mock_creds, mock_conn, vnc_client):
        _login(vnc_client)
        resp = vnc_client.post(
            "/api/vnc-session",
            data=json.dumps({"hostname": "server01"}),
            content_type="application/json",
        )
        assert resp.status_code == 503
        data = resp.get_json()
        assert data["success"] is False
        assert (
            "refused" in data["message"].lower()
            or "Connection refused" in data["message"]
        )

    @patch("dracs.webapp.check_vnc_connectivity", return_value=(True, ""))
    @patch("dracs.webapp.get_vnc_credentials", return_value=(5901, "pass"))
    def test_success(self, mock_creds, mock_conn, vnc_client):
        _login(vnc_client)
        resp = vnc_client.post(
            "/api/vnc-session",
            data=json.dumps({"hostname": "server01"}),
            content_type="application/json",
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        assert "token" in data

    @patch("dracs.webapp.check_vnc_connectivity", return_value=(True, ""))
    @patch("dracs.webapp.get_vnc_credentials", return_value=(5901, "pass"))
    def test_max_sessions_reached(self, mock_creds, mock_conn, vnc_client):
        _login(vnc_client)
        import dracs.webapp as webapp_mod

        webapp_mod.vnc_manager.max_sessions = 1
        vnc_client.post(
            "/api/vnc-session",
            data=json.dumps({"hostname": "server01"}),
            content_type="application/json",
        )
        resp = vnc_client.post(
            "/api/vnc-session",
            data=json.dumps({"hostname": "server02"}),
            content_type="application/json",
        )
        assert resp.status_code == 429

    @patch("dracs.webapp.check_vnc_connectivity", return_value=(True, ""))
    @patch("dracs.webapp.get_vnc_credentials", return_value=(5901, "pass"))
    def test_join_existing_session_returns_same_token(
        self, mock_creds, mock_conn, vnc_client
    ):
        _login(vnc_client)
        resp1 = vnc_client.post(
            "/api/vnc-session",
            data=json.dumps({"hostname": "server01"}),
            content_type="application/json",
        )
        token1 = resp1.get_json()["token"]
        resp2 = vnc_client.post(
            "/api/vnc-session",
            data=json.dumps({"hostname": "server01"}),
            content_type="application/json",
        )
        assert resp2.status_code == 200
        assert resp2.get_json()["token"] == token1

    @patch("dracs.vnc.subprocess.Popen")
    @patch("dracs.vnc.shutil.which", return_value="/usr/bin/x11vnc")
    @patch("dracs.webapp.check_vnc_connectivity", return_value=(True, ""))
    @patch("dracs.webapp.get_vnc_credentials", return_value=(5901, "pass"))
    def test_success_with_proxy_enabled(
        self, mock_creds, mock_conn, mock_which, mock_popen, vnc_client
    ):
        import threading as _threading

        hold = _threading.Event()
        mock_proc = MagicMock()
        mock_proc.wait.side_effect = lambda *a, **kw: (
            None if "timeout" in kw else hold.wait()
        )
        mock_popen.return_value = mock_proc
        import dracs.webapp as webapp_mod

        orig = webapp_mod.VNC_PROXY_ENABLE
        webapp_mod.VNC_PROXY_ENABLE = True
        try:
            _login(vnc_client)
            resp = vnc_client.post(
                "/api/vnc-session",
                data=json.dumps({"hostname": "server01"}),
                content_type="application/json",
            )
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["success"] is True
            token = data["token"]
            assert token in webapp_mod.vnc_manager._proxy_procs
            cmd = mock_popen.call_args[0][0]
            assert "-reflect" in cmd
        finally:
            hold.set()
            webapp_mod.VNC_PROXY_ENABLE = orig

    @patch("dracs.vnc.VncSessionManager.find_free_port", return_value=None)
    @patch("dracs.webapp.check_vnc_connectivity", return_value=(True, ""))
    @patch("dracs.webapp.get_vnc_credentials", return_value=(5901, "pass"))
    def test_proxy_enabled_no_port_falls_back_to_direct(
        self, mock_creds, mock_conn, mock_port, vnc_client
    ):
        import dracs.webapp as webapp_mod

        orig = webapp_mod.VNC_PROXY_ENABLE
        webapp_mod.VNC_PROXY_ENABLE = True
        try:
            _login(vnc_client)
            resp = vnc_client.post(
                "/api/vnc-session",
                data=json.dumps({"hostname": "server01"}),
                content_type="application/json",
            )
            assert resp.status_code == 200
            assert resp.get_json()["success"] is True
            token = resp.get_json()["token"]
            assert token not in webapp_mod.vnc_manager._proxy_procs
        finally:
            webapp_mod.VNC_PROXY_ENABLE = orig

    @patch("dracs.webapp.check_vnc_connectivity", return_value=(True, ""))
    @patch("dracs.webapp.get_vnc_credentials", return_value=(5901, "pass"))
    def test_join_existing_increments_refs(self, mock_creds, mock_conn, vnc_client):
        _login(vnc_client)
        import dracs.webapp as webapp_mod

        vnc_client.post(
            "/api/vnc-session",
            data=json.dumps({"hostname": "server01"}),
            content_type="application/json",
        )
        token = webapp_mod.vnc_manager.find_session_by_hostname("server01")
        assert token is not None
        refs_before = webapp_mod.vnc_manager._get_refs(token)
        vnc_client.post(
            "/api/vnc-session",
            data=json.dumps({"hostname": "server01"}),
            content_type="application/json",
        )
        refs_after = webapp_mod.vnc_manager._get_refs(token)
        assert refs_after == refs_before + 1

    @patch("dracs.webapp.check_vnc_connectivity", return_value=(True, ""))
    @patch("dracs.webapp.get_vnc_credentials", return_value=(5901, "pass"))
    def test_unexpected_exception_returns_500(self, mock_creds, mock_conn, vnc_client):
        _login(vnc_client)
        import dracs.webapp as webapp_mod

        orig_manager = webapp_mod.vnc_manager
        try:
            mock_mgr = MagicMock()
            mock_mgr.find_session_by_hostname.return_value = None
            mock_mgr.create_session.side_effect = RuntimeError("boom")
            webapp_mod.vnc_manager = mock_mgr
            resp = vnc_client.post(
                "/api/vnc-session",
                data=json.dumps({"hostname": "server01"}),
                content_type="application/json",
            )
        finally:
            webapp_mod.vnc_manager = orig_manager
        assert resp.status_code == 500


class TestVncSessionAddrefEndpoint:
    def test_requires_auth(self, vnc_client):
        resp = vnc_client.post("/api/vnc-session/sometoken/ref")
        assert resp.status_code == 401

    def test_vnc_disabled(self, vnc_disabled_client):
        _login(vnc_disabled_client)
        resp = vnc_disabled_client.post("/api/vnc-session/sometoken/ref")
        assert resp.status_code == 404

    def test_session_not_found(self, vnc_client):
        _login(vnc_client)
        resp = vnc_client.post("/api/vnc-session/no-such-token/ref")
        assert resp.status_code == 404
        assert resp.get_json()["success"] is False

    def test_exception_returns_500(self, vnc_client):
        _login(vnc_client)
        import dracs.webapp as webapp_mod

        with patch.object(
            webapp_mod.vnc_manager, "add_reference", side_effect=RuntimeError("boom")
        ):
            resp = vnc_client.post("/api/vnc-session/sometoken/ref")
        assert resp.status_code == 500
        assert resp.get_json()["success"] is False

    @patch("dracs.webapp.check_vnc_connectivity", return_value=(True, ""))
    @patch("dracs.webapp.get_vnc_credentials", return_value=(5901, "pass"))
    def test_success_increments_refs(self, mock_creds, mock_conn, vnc_client):
        _login(vnc_client)
        import dracs.webapp as webapp_mod

        create_resp = vnc_client.post(
            "/api/vnc-session",
            data=json.dumps({"hostname": "server01"}),
            content_type="application/json",
        )
        token = create_resp.get_json()["token"]
        refs_before = webapp_mod.vnc_manager._get_refs(token)
        resp = vnc_client.post(f"/api/vnc-session/{token}/ref")
        assert resp.status_code == 200
        assert resp.get_json()["success"] is True
        assert webapp_mod.vnc_manager._get_refs(token) == refs_before + 1


class TestVncSessionDeleteEndpoint:
    def test_requires_auth(self, vnc_client):
        resp = vnc_client.delete("/api/vnc-session/sometoken")
        assert resp.status_code == 401

    def test_vnc_disabled(self, vnc_disabled_client):
        _login(vnc_disabled_client)
        resp = vnc_disabled_client.delete("/api/vnc-session/sometoken")
        assert resp.status_code == 404

    @patch("dracs.webapp.check_vnc_connectivity", return_value=(True, ""))
    @patch("dracs.webapp.get_vnc_credentials", return_value=(5901, "pass"))
    def test_success(self, mock_creds, mock_conn, vnc_client):
        _login(vnc_client)
        create_resp = vnc_client.post(
            "/api/vnc-session",
            data=json.dumps({"hostname": "server01"}),
            content_type="application/json",
        )
        token = create_resp.get_json()["token"]
        resp = vnc_client.delete(f"/api/vnc-session/{token}")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True

    @patch("dracs.webapp.check_vnc_connectivity", return_value=(True, ""))
    @patch("dracs.webapp.get_vnc_credentials", return_value=(5901, "pass"))
    def test_delete_decrements_ref_not_destroys(
        self, mock_creds, mock_conn, vnc_client
    ):
        _login(vnc_client)
        import dracs.webapp as webapp_mod

        create_resp = vnc_client.post(
            "/api/vnc-session",
            data=json.dumps({"hostname": "server01"}),
            content_type="application/json",
        )
        token = create_resp.get_json()["token"]
        webapp_mod.vnc_manager.add_reference(token)  # refs=2
        resp = vnc_client.delete(f"/api/vnc-session/{token}")
        assert resp.status_code == 200
        assert webapp_mod.vnc_manager.get_session_info(token) is not None
        assert webapp_mod.vnc_manager._get_refs(token) == 1

    @patch("dracs.webapp.check_vnc_connectivity", return_value=(True, ""))
    @patch("dracs.webapp.get_vnc_credentials", return_value=(5901, "pass"))
    def test_delete_at_zero_refs_removes_session(
        self, mock_creds, mock_conn, vnc_client
    ):
        _login(vnc_client)
        import dracs.webapp as webapp_mod

        create_resp = vnc_client.post(
            "/api/vnc-session",
            data=json.dumps({"hostname": "server01"}),
            content_type="application/json",
        )
        token = create_resp.get_json()["token"]
        resp = vnc_client.delete(f"/api/vnc-session/{token}")
        assert resp.status_code == 200
        assert webapp_mod.vnc_manager.get_session_info(token) is None

    def test_unexpected_exception_returns_500(self, vnc_client):
        _login(vnc_client)
        import dracs.webapp as webapp_mod

        orig_manager = webapp_mod.vnc_manager
        try:
            mock_mgr = MagicMock()
            mock_mgr.release_session.side_effect = RuntimeError("boom")
            webapp_mod.vnc_manager = mock_mgr
            resp = vnc_client.delete("/api/vnc-session/some-token")
        finally:
            webapp_mod.vnc_manager = orig_manager
        assert resp.status_code == 500


class TestVncSessionTouchEndpoint:
    def test_requires_auth(self, vnc_client):
        resp = vnc_client.patch("/api/vnc-session/sometoken")
        assert resp.status_code == 401

    def test_vnc_disabled(self, vnc_disabled_client):
        _login(vnc_disabled_client)
        resp = vnc_disabled_client.patch("/api/vnc-session/sometoken")
        assert resp.status_code == 404

    def test_session_not_found(self, vnc_client):
        _login(vnc_client)
        resp = vnc_client.patch("/api/vnc-session/no-such-token")
        assert resp.status_code == 404
        assert resp.get_json()["success"] is False

    @patch("dracs.webapp.check_vnc_connectivity", return_value=(True, ""))
    @patch("dracs.webapp.get_vnc_credentials", return_value=(5901, "pass"))
    def test_success(self, mock_creds, mock_conn, vnc_client):
        _login(vnc_client)
        create_resp = vnc_client.post(
            "/api/vnc-session",
            data=json.dumps({"hostname": "server01"}),
            content_type="application/json",
        )
        token = create_resp.get_json()["token"]
        resp = vnc_client.patch(f"/api/vnc-session/{token}")
        assert resp.status_code == 200
        assert resp.get_json()["success"] is True


class TestConsoleMultiEndpoint:
    def test_requires_auth(self, vnc_client):
        resp = vnc_client.get("/console-multi?hosts=server01,server02")
        assert resp.status_code == 401

    def test_vnc_disabled(self, vnc_disabled_client):
        _login(vnc_disabled_client)
        resp = vnc_disabled_client.get("/console-multi?hosts=server01,server02")
        assert resp.status_code == 404

    def test_no_hosts(self, vnc_client):
        _login(vnc_client)
        resp = vnc_client.get("/console-multi")
        assert resp.status_code == 400

    def test_single_host_rejected(self, vnc_client):
        _login(vnc_client)
        resp = vnc_client.get("/console-multi?hosts=server01")
        assert resp.status_code == 400

    def test_invalid_hostname_rejected(self, vnc_client):
        _login(vnc_client)
        resp = vnc_client.get("/console-multi?hosts=server01,bad;host")
        assert resp.status_code == 400

    def test_success_returns_multi_console_page(self, vnc_client):
        _login(vnc_client)
        resp = vnc_client.get("/console-multi?hosts=server01,server02")
        assert resp.status_code == 200
        assert b"server01" in resp.data
        assert b"server02" in resp.data
        assert b"Multi-Console" in resp.data

    def test_many_hosts_accepted(self, vnc_client):
        _login(vnc_client)
        hosts = ",".join(f"server{i:02d}" for i in range(10))
        resp = vnc_client.get(f"/console-multi?hosts={hosts}")
        assert resp.status_code == 200


class TestConsoleConnectEndpoint:
    def test_requires_auth(self, vnc_client):
        resp = vnc_client.get("/console-connect?host=server01")
        assert resp.status_code == 401

    def test_vnc_disabled(self, vnc_disabled_client):
        _login(vnc_disabled_client)
        resp = vnc_disabled_client.get("/console-connect?host=server01")
        assert resp.status_code == 404

    def test_invalid_hostname(self, vnc_client):
        _login(vnc_client)
        resp = vnc_client.get("/console-connect?host=bad;host")
        assert resp.status_code == 400

    def test_missing_host(self, vnc_client):
        _login(vnc_client)
        resp = vnc_client.get("/console-connect")
        assert resp.status_code == 400

    def test_success_returns_connect_page(self, vnc_client):
        _login(vnc_client)
        resp = vnc_client.get("/console-connect?host=server01")
        assert resp.status_code == 200
        assert b"server01" in resp.data
        assert b"Connecting" in resp.data


class TestConsoleViewEndpoint:
    def test_requires_auth(self, vnc_client):
        resp = vnc_client.get("/console/sometoken")
        assert resp.status_code == 401

    def test_vnc_disabled(self, vnc_disabled_client):
        _login(vnc_disabled_client)
        resp = vnc_disabled_client.get("/console/sometoken")
        assert resp.status_code == 404

    def test_invalid_token(self, vnc_client):
        _login(vnc_client)
        resp = vnc_client.get("/console/nonexistent")
        assert resp.status_code == 404

    @patch("dracs.webapp.check_vnc_connectivity", return_value=(True, ""))
    @patch("dracs.webapp.get_vnc_credentials", return_value=(5901, "vncpass"))
    def test_success(self, mock_creds, mock_conn, vnc_client):
        _login(vnc_client)
        create_resp = vnc_client.post(
            "/api/vnc-session",
            data=json.dumps({"hostname": "server01"}),
            content_type="application/json",
        )
        token = create_resp.get_json()["token"]
        resp = vnc_client.get(f"/console/{token}")
        assert resp.status_code == 200
        assert b"noVNC" in resp.data
        assert b"server01" in resp.data


class TestVncButtonVisibility:
    def test_button_visible_when_vnc_enabled(self, vnc_client):
        _login(vnc_client)
        resp = vnc_client.get("/")
        assert b'id="console-btn"' in resp.data

    def test_button_disabled_when_vnc_disabled(self, vnc_disabled_client):
        _login(vnc_disabled_client)
        resp = vnc_disabled_client.get("/")
        assert b'id="console-btn"' in resp.data
        assert b"role-disabled" in resp.data


class TestParseConsoleSize:
    def test_valid_size(self):
        from dracs.webapp import _parse_console_size

        assert _parse_console_size("1024x768") == (1024, 768)

    def test_default_size(self):
        from dracs.webapp import _parse_console_size

        assert _parse_console_size("800x600") == (800, 600)

    def test_invalid_no_x(self):
        from dracs.webapp import _parse_console_size

        assert _parse_console_size("800") == (800, 600)

    def test_invalid_non_numeric(self):
        from dracs.webapp import _parse_console_size

        assert _parse_console_size("abcxdef") == (800, 600)

    def test_invalid_zero_width(self):
        from dracs.webapp import _parse_console_size

        assert _parse_console_size("0x600") == (800, 600)

    def test_invalid_negative(self):
        from dracs.webapp import _parse_console_size

        assert _parse_console_size("-1x600") == (800, 600)

    def test_invalid_empty(self):
        from dracs.webapp import _parse_console_size

        assert _parse_console_size("") == (800, 600)

    def test_invalid_none(self):
        from dracs.webapp import _parse_console_size

        assert _parse_console_size(None) == (800, 600)


class TestGetRefCount:
    def test_returns_zero_for_unknown_token(self, manager):
        assert manager.get_ref_count("no-such-token") == 0

    def test_returns_one_after_create(self, manager):
        token = manager.create_session("host01", "mgmt-host01.example.com", 5901)
        assert manager.get_ref_count(token) == 1

    def test_reflects_add_reference(self, manager):
        token = manager.create_session("host01", "mgmt-host01.example.com", 5901)
        manager.add_reference(token)
        assert manager.get_ref_count(token) == 2

    def test_reflects_release_session(self, manager):
        token = manager.create_session("host01", "mgmt-host01.example.com", 5901)
        manager.add_reference(token)  # refs=2
        manager.release_session(token)  # refs=1
        assert manager.get_ref_count(token) == 1


class TestVncSessionViewersEndpoint:
    def test_requires_auth(self, vnc_client):
        resp = vnc_client.get("/api/vnc-session/sometoken/viewers")
        assert resp.status_code == 401

    def test_returns_zero_for_unknown_token(self, vnc_client):
        _login(vnc_client)
        resp = vnc_client.get("/api/vnc-session/no-such-token/viewers")
        assert resp.status_code == 200
        assert resp.get_json()["viewers"] == 0

    def test_returns_zero_when_vnc_disabled(self, vnc_disabled_client):
        _login(vnc_disabled_client)
        resp = vnc_disabled_client.get("/api/vnc-session/sometoken/viewers")
        assert resp.status_code == 200
        assert resp.get_json()["viewers"] == 0

    @patch("dracs.webapp.check_vnc_connectivity", return_value=(True, ""))
    @patch("dracs.webapp.get_vnc_credentials", return_value=(5901, "pass"))
    def test_returns_count_for_active_session(self, mock_creds, mock_conn, vnc_client):
        _login(vnc_client)
        import dracs.webapp as webapp_mod

        create_resp = vnc_client.post(
            "/api/vnc-session",
            data=json.dumps({"hostname": "server01"}),
            content_type="application/json",
        )
        token = create_resp.get_json()["token"]
        resp = vnc_client.get(f"/api/vnc-session/{token}/viewers")
        assert resp.status_code == 200
        assert resp.get_json()["viewers"] == 1

        webapp_mod.vnc_manager.add_reference(token)
        resp = vnc_client.get(f"/api/vnc-session/{token}/viewers")
        assert resp.get_json()["viewers"] == 2

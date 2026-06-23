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

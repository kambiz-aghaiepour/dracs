"""VNC session management for DRACS console feature."""

import configparser
import os
import secrets
import shutil
import signal
import socket
import subprocess
import tempfile
import threading
import time
from pathlib import Path


class VncSessionManager:
    """Manages VNC console session tokens and lifecycle."""

    def __init__(self, token_dir: str, timeout_minutes: int, max_sessions: int):
        self.token_dir = Path(token_dir)
        self.timeout_minutes = timeout_minutes
        self.max_sessions = max_sessions
        self.token_dir.mkdir(parents=True, exist_ok=True)
        self._cleanup_thread = None
        self._stop_event = threading.Event()
        self._start_cleanup_thread()

    def create_session(self, hostname: str, idrac_fqdn: str, vnc_port: int) -> str:
        if self.max_sessions > 0 and self.active_count() >= self.max_sessions:
            raise MaxSessionsError(
                f"Maximum VNC sessions ({self.max_sessions}) reached"
            )

        token = secrets.token_urlsafe(16)
        token_file = self.token_dir / token
        token_file.write_text(f"{token}: {idrac_fqdn}:{vnc_port}\n")

        meta_file = self.token_dir / f"{token}.meta"
        meta_file.write_text(f"{hostname}\n")

        return token

    def remove_session(self, token: str) -> None:
        token_file = self.token_dir / token
        token_file.unlink(missing_ok=True)
        meta_file = self.token_dir / f"{token}.meta"
        meta_file.unlink(missing_ok=True)

    def get_session_info(self, token: str) -> dict | None:
        token_file = self.token_dir / token
        if not token_file.exists():
            return None

        meta_file = self.token_dir / f"{token}.meta"
        hostname = ""
        if meta_file.exists():
            hostname = meta_file.read_text().strip()

        return {
            "token": token,
            "hostname": hostname,
            "created_at": token_file.stat().st_mtime,
        }

    def active_count(self) -> int:
        return len(
            [f for f in self.token_dir.iterdir() if not f.name.endswith(".meta")]
        )

    def cleanup_expired(self) -> int:
        removed = 0
        cutoff = time.time() - (self.timeout_minutes * 60)
        for token_file in list(self.token_dir.iterdir()):
            if token_file.name.endswith(".meta"):
                continue
            try:
                if token_file.stat().st_mtime < cutoff:
                    token = token_file.name
                    self.remove_session(token)
                    removed += 1
            except FileNotFoundError:
                pass
        return removed

    def _start_cleanup_thread(self) -> None:
        self._stop_event.clear()

        def _cleanup_loop():
            while not self._stop_event.is_set():
                self._stop_event.wait(60)
                if not self._stop_event.is_set():
                    self.cleanup_expired()

        self._cleanup_thread = threading.Thread(target=_cleanup_loop, daemon=True)
        self._cleanup_thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._cleanup_thread:
            self._cleanup_thread.join(timeout=5)


class MaxSessionsError(Exception):
    pass


def get_vnc_credentials(hostname: str, site: str | None = None) -> tuple:
    config_file = Path("drac-passwords.ini")

    if not config_file.exists():
        config_file = Path("/etc/dracs/drac-passwords.ini")

    if not config_file.exists():
        return (5901, "")

    if site is None:
        site = "Default"

    config = configparser.RawConfigParser()
    config.read(config_file)

    host_section = f"{site}-{hostname}"
    defaults_section = f"{site}-DEFAULTS"

    if host_section in config:
        vnc_port = int(
            config.get(
                host_section,
                "vnc_port",
                fallback=config.get(defaults_section, "vnc_port", fallback="5901"),
            )
        )
        vnc_password = config.get(
            host_section,
            "vnc_password",
            fallback=config.get(defaults_section, "vnc_password", fallback=""),
        )
    elif defaults_section in config:
        vnc_port = int(config.get(defaults_section, "vnc_port", fallback="5901"))
        vnc_password = config.get(defaults_section, "vnc_password", fallback="")
    else:
        return (5901, "")

    return (vnc_port, vnc_password)


def check_vnc_connectivity(host: str, port: int, timeout: int = 5) -> tuple:
    try:
        sock = socket.create_connection((host, port), timeout=timeout)
        sock.close()
        return (True, "")
    except socket.timeout:
        return (False, f"Connection to {host}:{port} timed out")
    except ConnectionRefusedError:
        return (False, f"Connection to {host}:{port} refused")
    except OSError as e:
        return (False, f"Cannot reach {host}:{port}: {e}")


_websockify_process = None
_runtime_dir = Path(tempfile.gettempdir()) / "dracs"
_pid_file = _runtime_dir / "websockify.pid"


def get_token_dir() -> str:
    """Return the path to the VNC token directory, created securely."""
    token_dir = _runtime_dir / "vnc-tokens"
    token_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    return str(token_dir)


def start_websockify(
    port: int, token_dir: str | None = None
) -> subprocess.Popen | None:
    global _websockify_process

    token_path = Path(token_dir) if token_dir else Path(get_token_dir())
    token_path.mkdir(parents=True, exist_ok=True, mode=0o700)
    _runtime_dir.mkdir(parents=True, exist_ok=True, mode=0o700)

    websockify_bin = shutil.which("websockify")
    if not websockify_bin:
        print("Warning: websockify not found in PATH, VNC console disabled")
        return None

    cmd = [
        websockify_bin,
        f"127.0.0.1:{port}",
        "--token-plugin",
        "TokenFile",
        "--token-source",
        str(token_path),
    ]

    _websockify_process = subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    _pid_file.write_text(str(_websockify_process.pid))
    print(f"websockify started on 127.0.0.1:{port} (PID {_websockify_process.pid})")
    return _websockify_process


def stop_websockify() -> None:
    global _websockify_process

    if _websockify_process:
        try:
            _websockify_process.terminate()
            _websockify_process.wait(timeout=5)
        except (ProcessLookupError, subprocess.TimeoutExpired):
            try:
                _websockify_process.kill()
            except ProcessLookupError:
                pass
        _websockify_process = None

    if _pid_file.exists():
        try:
            pid = int(_pid_file.read_text().strip())
            os.kill(pid, signal.SIGTERM)
        except (ProcessLookupError, ValueError, OSError):
            pass
        _pid_file.unlink(missing_ok=True)

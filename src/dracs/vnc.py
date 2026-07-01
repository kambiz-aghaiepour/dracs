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
        self._proxy_procs: dict = {}
        self._cleanup_orphaned_proxies()
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

        self._refs_file(token).write_text("1")
        return token

    def find_session_by_hostname(self, hostname: str) -> str | None:
        """Return an active session token for hostname, or None.

        Removes stale proxy sessions whose x11vnc process is no longer
        listening so the caller can create a fresh working session.
        """
        for meta_file in self.token_dir.glob("*.meta"):
            try:
                if meta_file.read_text().strip() == hostname:
                    token = meta_file.stem
                    if (self.token_dir / token).exists():
                        if not self._is_proxy_alive(token):
                            self.remove_session(token)
                            continue
                        return token
            except OSError:
                continue
        return None

    def _is_proxy_alive(self, token: str) -> bool:
        """Return True if the proxy backing this token is still accepting connections.

        Tokens that route directly to an iDRAC (non-localhost) always return
        True.  Tokens routed through 127.0.0.1 are probed with a short-timeout
        TCP connect so stale x11vnc sessions are detected before use.
        """
        token_file = self.token_dir / token
        try:
            content = token_file.read_text()
            _, addr = content.strip().split(": ", 1)
            host, port_str = addr.rsplit(":", 1)
            port = int(port_str)
        except (OSError, ValueError):
            return True
        if host != "127.0.0.1":
            return True
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return True
        except OSError:
            return False

    def add_reference(self, token: str) -> bool:
        """Increment the reference count for a session. Returns True if session exists."""
        if not (self.token_dir / token).exists():
            return False
        count = self._get_refs(token) + 1
        self._refs_file(token).write_text(str(count))
        return True

    def release_session(self, token: str) -> bool:
        """
        Decrement the reference count for a session.  Removes the session when the
        count reaches zero.  Returns True if the session existed.
        """
        if not (self.token_dir / token).exists():
            return False
        count = self._get_refs(token) - 1
        if count <= 0:
            self.remove_session(token)
        else:
            self._refs_file(token).write_text(str(count))
        return True

    def find_free_port(self) -> int | None:
        """Return an available localhost TCP port, or None on failure."""
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(("127.0.0.1", 0))
                return s.getsockname()[1]
        except OSError:
            return None

    def _proxy_pid_file(self, token: str) -> Path:
        return self.token_dir / f"{token}.proxy"

    def start_proxy(
        self,
        token: str,
        idrac_host: str,
        idrac_port: int,
        vnc_password: str,
        port: int,
    ) -> bool:
        """
        Start an x11vnc VNC repeater proxy for a session.

        The proxy holds one upstream TCP connection to idrac_host:idrac_port and
        accepts multiple downstream viewers on 127.0.0.1:port, allowing several
        simultaneous read-write connections through a single iDRAC VNC session.
        Returns True if x11vnc was found and launched, False otherwise.
        """
        x11vnc_bin = shutil.which("x11vnc")
        if not x11vnc_bin:
            return False
        cmd = [
            x11vnc_bin,
            "-reflect",
            f"{idrac_host}:{idrac_port}",
            "-shared",
            "-many",
            "-forever",
            "-rfbport",
            str(port),
            "-localhost",
            "-nopw",
            "-quiet",
            "-ping",
            "60",
        ]
        env = {k: v for k, v in os.environ.items() if k != "DISPLAY"}
        if vnc_password:
            env["X11VNC_REFLECT_PASSWORD"] = vnc_password
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=env,
        )
        self._proxy_procs[token] = proc
        self._proxy_pid_file(token).write_text(str(proc.pid))
        threading.Thread(
            target=self._reap_proxy, args=(token, proc), daemon=True
        ).start()
        return True

    def _reap_proxy(self, token: str, proc: subprocess.Popen) -> None:
        """Wait for a proxy process to exit and remove it from the tracking dict."""
        try:
            proc.wait()
        except Exception:
            return
        self._proxy_procs.pop(token, None)
        self._proxy_pid_file(token).unlink(missing_ok=True)

    def _kill_by_pid_file(self, token: str) -> None:
        """Kill an orphaned proxy process using its persisted PID file."""
        pid_file = self._proxy_pid_file(token)
        if not pid_file.exists():
            return
        try:
            pid = int(pid_file.read_text().strip())
            os.kill(pid, signal.SIGTERM)
            time.sleep(0.5)
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        except (ProcessLookupError, ValueError, OSError):
            pass
        pid_file.unlink(missing_ok=True)

    def stop_proxy(self, token: str) -> None:
        """Terminate the x11vnc proxy process for a session, if any."""
        proc = self._proxy_procs.pop(token, None)
        if proc:
            try:
                proc.terminate()
                proc.wait(timeout=3)
            except (ProcessLookupError, subprocess.TimeoutExpired):
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass
            self._proxy_pid_file(token).unlink(missing_ok=True)
        else:
            self._kill_by_pid_file(token)

    def _cleanup_orphaned_proxies(self) -> None:
        """Kill and remove proxy sessions left over from a previous process."""
        for pid_file in list(self.token_dir.glob("*.proxy")):
            token = pid_file.stem
            try:
                pid = int(pid_file.read_text().strip())
                os.kill(pid, signal.SIGKILL)
            except (ProcessLookupError, ValueError, OSError):
                pass
            (self.token_dir / token).unlink(missing_ok=True)
            (self.token_dir / f"{token}.meta").unlink(missing_ok=True)
            self._refs_file(token).unlink(missing_ok=True)
            pid_file.unlink(missing_ok=True)

    def remove_session(self, token: str) -> None:
        """Force-remove a session regardless of reference count."""
        self.stop_proxy(token)
        (self.token_dir / token).unlink(missing_ok=True)
        (self.token_dir / f"{token}.meta").unlink(missing_ok=True)
        self._refs_file(token).unlink(missing_ok=True)

    def touch_session(self, token: str) -> bool:
        """Reset the expiry timer for an active session. Returns True if session exists."""
        token_file = self.token_dir / token
        if not token_file.exists():
            return False
        token_file.touch()
        return True

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

    def _refs_file(self, token: str) -> Path:
        return self.token_dir / f"{token}.refs"

    def _get_refs(self, token: str) -> int:
        try:
            return int(self._refs_file(token).read_text().strip())
        except (OSError, ValueError):
            return 1

    def get_ref_count(self, token: str) -> int:
        """Return the current viewer reference count, or 0 if the session does not exist."""
        if not (self.token_dir / token).exists():
            return 0
        return self._get_refs(token)

    def active_count(self) -> int:
        return len(
            [
                f
                for f in self.token_dir.iterdir()
                if not f.name.endswith((".meta", ".refs", ".proxy"))
            ]
        )

    def cleanup_expired(self) -> int:
        removed = 0
        cutoff = time.time() - (self.timeout_minutes * 60)
        for token_file in list(self.token_dir.iterdir()):
            if token_file.name.endswith((".meta", ".refs", ".proxy")):
                continue
            try:
                if token_file.stat().st_mtime < cutoff:
                    self.remove_session(token_file.name)
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
        for token in list(self._proxy_procs.keys()):
            self.stop_proxy(token)


class MaxSessionsError(Exception):
    pass


def get_hostname_viewer_count(hostname: str, token_dir: str | None = None) -> int:
    """Return the active viewer count for hostname without requiring a VncSessionManager.

    Reads .meta and .refs files directly from the token directory so the job
    queue can call this without depending on the webapp's in-memory vnc_manager.
    Returns 0 when no session exists or when the token directory is absent.
    """
    td = Path(token_dir) if token_dir else Path(get_token_dir())
    if not td.exists():
        return 0
    for meta_file in td.glob("*.meta"):
        try:
            if meta_file.read_text().strip() == hostname:
                token = meta_file.stem
                refs_file = td / f"{token}.refs"
                try:
                    return int(refs_file.read_text().strip())
                except (OSError, ValueError):
                    return 1
        except OSError:
            continue
    return 0


def get_vnc_credentials(hostname: str, site: str | None = None) -> tuple:
    config_file = Path("drac-passwords.ini")

    if not config_file.exists():
        config_file = Path("/etc/dracs/drac-passwords.ini")

    if not config_file.exists():
        return (5901, "")

    if site is None:
        try:
            from dracs.db import System, Site, get_session, get_primary_site_name

            with get_session() as sess:
                system = sess.query(System).filter(System.name == hostname).first()
                if system and system.site_id:
                    site_obj = sess.get(Site, system.site_id)
                    if site_obj:
                        site = site_obj.name
            if site is None:
                site = get_primary_site_name()
        except Exception:
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

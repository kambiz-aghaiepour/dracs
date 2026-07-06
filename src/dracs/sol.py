"""Conserver management for DRACS IPMI SOL feature."""

import logging
import os
import re
import secrets
import shutil
import signal
import socket
import string
import subprocess  # nosec B404
from pathlib import Path

logger = logging.getLogger(__name__)

_conserver_process = None
_pid_file_path = Path("/var/run/dracs/conserver.pid")

# Standard directory for DRACS TLS certificates (matches nginx dracs_ssl.conf.example).
# Override in tests via patch("dracs.sol._SSL_CERT_DIR", tmp_path).
_SSL_CERT_DIR = Path("/etc/pki/tls/certs")


def _ssl_cert_key_paths() -> tuple[Path, Path] | tuple[None, None]:
    """Return (cert_path, key_path) for conserver SSL, or (None, None) if unavailable.

    Checks SOL_SSL_CERT / SOL_SSL_KEY env vars first; if unset, auto-detects from
    the standard DRACS nginx cert location: /etc/pki/tls/certs/<hostname>.{pem,key}.
    """
    cert_str = os.environ.get("SOL_SSL_CERT", "")
    key_str = os.environ.get("SOL_SSL_KEY", "")
    if cert_str and key_str:
        return Path(cert_str), Path(key_str)
    hostname = socket.gethostname()
    cert = _SSL_CERT_DIR / f"{hostname}.pem"
    key = _SSL_CERT_DIR / f"{hostname}.key"
    if cert.exists() and key.exists():
        return cert, key
    return None, None


class ConserverPasswd:
    """
    Manages /etc/dracs/conserver.passwd - one entry per dracs site.

    Handles hashing, storage, and verification of per-site passwords
    used by conserver to authenticate all console clients.
    """

    def __init__(self, passwd_path: Path):
        """Initialize with the path to the conserver passwd file."""
        self.passwd_path = passwd_path

    def sync(self, site_passwords: dict) -> dict:
        """Sync conserver.passwd; generate random passwords for any None entries."""
        result = {}
        entries = {}
        for site_name, plaintext in site_passwords.items():
            if not plaintext:
                plaintext = self._generate_password()
            result[site_name] = plaintext
            entries[site_name] = self._hash_password(plaintext)
        self._write(entries)
        return result

    def verify(self, site_name: str, plaintext: str) -> bool:
        """Verify a plaintext password against the stored hash."""
        stored = self._read().get(site_name)
        if not stored:
            return False
        try:
            openssl = shutil.which("openssl") or "openssl"
            parts = stored.split("$")
            if stored.startswith("$") and len(parts) >= 4:
                algo_flag = f"-{parts[1]}"
                salt = parts[2]
            else:
                algo_flag = "-crypt"
                salt = stored[:2]
            result = subprocess.run(  # nosec B603  # nosemgrep
                [openssl, "passwd", algo_flag, "-salt", salt, "-stdin"],
                input=plaintext,
                capture_output=True,
                text=True,
                check=True,
            )
            return result.stdout.strip() == stored
        except subprocess.CalledProcessError:
            return False

    def _read(self) -> dict:
        if not self.passwd_path.exists():
            return {}
        entries = {}
        for line in self.passwd_path.read_text().splitlines():
            line = line.strip()
            if not line or ":" not in line:
                continue
            name, _, hashed = line.partition(":")
            entries[name.strip()] = hashed.strip()
        return entries

    def _write(self, entries: dict) -> None:
        self.passwd_path.parent.mkdir(parents=True, exist_ok=True)
        content = "".join(
            f"{name}:{hashed}\n" for name, hashed in sorted(entries.items())
        )
        tmp = self.passwd_path.with_suffix(".tmp")
        tmp.write_text(content)
        tmp.chmod(0o640)
        tmp.rename(self.passwd_path)

    @staticmethod
    def _generate_password(length: int = 20) -> str:
        alphabet = string.ascii_letters + string.digits
        return "".join(secrets.choice(alphabet) for _ in range(length))

    @staticmethod
    def _hash_password(plaintext: str) -> str:
        """Hash a plaintext password using openssl passwd -6 (SHA-512) via stdin."""
        openssl = shutil.which("openssl") or "openssl"
        result = subprocess.run(  # nosec B603  # nosemgrep
            [openssl, "passwd", "-6", "-stdin"],
            input=plaintext,
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout.strip()


class ConserverConfig:
    """
    Generates /etc/dracs/conserver.cf from dracs site and host data.

    Writes config, access, default, and console stanzas for each site
    and host; sets file permissions to 0640.
    """

    def __init__(self, cf_path: Path, passwd_path: Path, log_dir: Path):
        """Initialize with paths to the config file, passwd file, and log directory."""
        self.cf_path = cf_path
        self.passwd_path = passwd_path
        self.log_dir = log_dir

    def generate(
        self,
        sites_data: list,
        primary_port: str = "3109",
        secondary_port: str = "3110",
        ssl_creds_path: Path | None = None,
    ) -> None:
        """Write conserver.cf; creates per-site default blocks and console stanzas."""
        master_hostname = socket.gethostname()
        config_block = [
            "config * {\n",
            f"    primaryport {primary_port};\n",
            f"    secondaryport {secondary_port};\n",
            f"    passwdfile {self.passwd_path};\n",
            f"    logfile {self.log_dir}/conserver.log;\n",
            "    daemonmode no;\n",
        ]
        if ssl_creds_path is not None:
            config_block.append(f"    sslcredentials {ssl_creds_path};\n")
            config_block.append("    sslrequired yes;\n")
        config_block.append("}\n")

        lines = [
            "# Managed by dracs-webapp. Do not edit manually.\n",
            "\n",
            *config_block,
            "\n",
            "access * {\n",
            "    allowed 0.0.0.0/0;\n",
            "}\n",
        ]
        for site in sites_data:
            lines.extend(self._generate_site_lines(site, master_hostname))

        self.cf_path.parent.mkdir(parents=True, exist_ok=True)
        content = "".join(lines)
        tmp = self.cf_path.with_suffix(".tmp")
        tmp.write_text(content)
        tmp.chmod(0o640)
        tmp.rename(self.cf_path)

    def _generate_site_lines(self, site: dict, master_hostname: str) -> list:
        """Return conserver.cf lines for one site: default blocks + console stanzas."""
        from dracs.snmp import ValidationError, build_idrac_hostname

        site_name = site["name"]
        safe_site = self._safe_name(site_name)
        site_defs = site.get("defaults", {})
        site_user = site_defs.get("username") or "root"
        site_pass = site_defs.get("password") or ""

        lines = ["\n"]
        lines.extend(self._format_default_block(f"ipmi_sol_{safe_site}", site_user, site_pass))

        for hostname, host_creds in site.get("hosts", {}).items():
            if self._has_host_override(host_creds, site_defs):
                h_user = host_creds.get("username") or site_user
                h_pass = host_creds.get("password") or site_pass
                lines.append("\n")
                lines.extend(
                    self._format_default_block(
                        f"ipmi_sol_{self._safe_name(hostname)}", h_user, h_pass
                    )
                )

        for hostname, host_creds in site.get("hosts", {}).items():
            try:
                mgmt_host = build_idrac_hostname(hostname)
            except ValidationError as exc:
                logger.warning("Skipping host %s: %s", hostname, exc)
                continue
            if self._has_host_override(host_creds, site_defs):
                default_name = f"ipmi_sol_{self._safe_name(hostname)}"
            else:
                default_name = f"ipmi_sol_{safe_site}"
            lines.append("\n")
            lines.extend(
                self._format_console_block(
                    hostname, mgmt_host, default_name, site_name, master_hostname
                )
            )

        return lines

    def _format_default_block(self, name: str, username: str, password: str) -> list:
        """Return conserver.cf lines for a named ipmi_sol default block."""
        return [
            f"default {name} {{\n",
            "    type exec;\n",
            f"    exec /usr/bin/ipmitool -I lanplus -H & -U {username} -P {password} sol activate;\n",
            "    execsubst &=hs;\n",
            "    options ondemand;\n",
            f"    logfile {self.log_dir}/&.log;\n",
            "}\n",
        ]

    def _format_console_block(
        self,
        console_name: str,
        mgmt_host: str,
        default_name: str,
        site_name: str,
        master_hostname: str,
    ) -> list:
        """Return conserver.cf lines for a console stanza."""
        return [
            f"console {console_name} {{\n",
            f"    master {master_hostname};\n",
            f"    include {default_name};\n",
            f"    host {mgmt_host};\n",
            f"    rw {site_name};\n",
            "}\n",
        ]

    @staticmethod
    def _safe_name(name: str) -> str:
        """Convert a name to a valid conserver identifier."""
        return re.sub(r"[^a-zA-Z0-9_]", "_", name)

    @staticmethod
    def _has_host_override(host_creds: dict, site_defaults: dict) -> bool:
        """Return True if host credentials differ from the site defaults."""
        for key in ("username", "password"):
            host_val = host_creds.get(key) or ""
            site_val = site_defaults.get(key) or ""
            if host_val and host_val != site_val:
                return True
        return False


def _build_ssl_credentials(cert_path: Path, key_path: Path, out_path: Path) -> None:
    """Combine key + cert into a single PEM file for conserver sslcredentials.

    Conserver expects a single file containing both the private key and certificate.
    File is written 0600 since it contains the private key.
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)
    content = key_path.read_text() + cert_path.read_text()
    tmp = out_path.with_suffix(".tmp")
    tmp.write_text(content)
    tmp.chmod(0o600)
    tmp.rename(out_path)


def _write_console_cf(console_cf_path: Path, ssl_ca_path: Path | None = None) -> None:
    """Write a console client config file for dracs-managed SSL connections.

    When ssl_ca_path is provided (self-signed / private CA), the CA cert is
    set so the console client can verify the conserver certificate. Without it
    the client falls back to the system CA bundle.
    Pass '-n -C <console_cf_path>' to the console command to use this file.
    """
    lines = ["config * {\n", "    sslrequired yes;\n"]
    if ssl_ca_path is not None:
        lines.append(f"    sslcacertificatefile {ssl_ca_path};\n")
    lines.append("}\n")
    console_cf_path.parent.mkdir(parents=True, exist_ok=True)
    content = "".join(lines)
    tmp = console_cf_path.with_suffix(".tmp")
    tmp.write_text(content)
    tmp.chmod(0o644)
    tmp.rename(console_cf_path)


def disable_systemd_service() -> None:
    """Ensure the conserver systemd service is disabled."""
    try:
        systemctl = shutil.which("systemctl") or "systemctl"
        subprocess.run(  # nosec B603  # nosemgrep
            [systemctl, "disable", "--now", "conserver"],
            capture_output=True,
            check=False,
        )
    except Exception as exc:
        logger.debug("systemctl disable conserver: %s", exc)


def start_conserver(cf_path: Path) -> subprocess.Popen | None:
    """Start the conserver process managed as a subprocess."""
    global _conserver_process

    conserver_bin = shutil.which("conserver")
    if not conserver_bin:
        logger.warning("conserver not found in PATH; SOL feature disabled")
        return None

    # Kill any orphaned conserver using this config file before starting a new one.
    # Ports are in conserver.cf (primaryport/secondaryport), not on the command line,
    # so orphans are identified by config-file path rather than -p flag.
    _kill_conservers_with_config(cf_path)
    _conserver_process = subprocess.Popen(  # nosec B603
        [conserver_bin, "-C", str(cf_path), "-m", "10000"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    try:
        _pid_file_path.parent.mkdir(parents=True, exist_ok=True)
        _pid_file_path.write_text(str(_conserver_process.pid))
    except OSError:
        pass
    logger.info("conserver started (PID %s)", _conserver_process.pid)
    return _conserver_process


def _is_conserver_with_config(args: list[str], cf_path_str: str) -> bool:
    """Return True if the cmdline args represent a conserver using the given config."""
    if not args or Path(args[0]).name != "conserver":
        return False
    try:
        return args[args.index("-C") + 1] == cf_path_str
    except (ValueError, IndexError):
        return False


def _kill_conservers_with_config(
    cf_path: Path, _proc_root: Path = Path("/proc")
) -> None:
    """Kill all conserver processes using the given config file.

    Scans /proc to find processes regardless of which parent started them,
    so orphaned conservers from previous service runs are also cleaned up.
    """
    cf_path_str = str(cf_path)
    seen_pgids: set[int] = set()
    try:
        for proc in _proc_root.iterdir():
            if not proc.name.isdigit():
                continue
            try:
                raw = (proc / "cmdline").read_bytes().split(b"\x00")
                args = [a.decode("utf-8", errors="replace") for a in raw if a]
                if _is_conserver_with_config(args, cf_path_str):
                    pid = int(proc.name)
                    pgid = os.getpgid(pid)
                    if pgid not in seen_pgids:
                        seen_pgids.add(pgid)
                        os.killpg(pgid, signal.SIGTERM)
            except (OSError, ValueError, ProcessLookupError):
                pass
    except OSError:
        pass


def _is_conserver_on_port(args: list[str], port: str) -> bool:
    """Return True if the cmdline args represent a conserver master on the given port."""
    if not args or Path(args[0]).name != "conserver":
        return False
    try:
        return args[args.index("-p") + 1] == port
    except (ValueError, IndexError):
        return False


def _kill_conservers_on_port(port: str, _proc_root: Path = Path("/proc")) -> None:
    """Kill all conserver processes bound to the given master port.

    Used by stop_conserver() as a belt-and-suspenders cleanup for any conservers
    that still carry -p <port> in their command line (older deployments).
    """
    seen_pgids: set[int] = set()
    try:
        for proc in _proc_root.iterdir():
            if not proc.name.isdigit():
                continue
            try:
                raw = (proc / "cmdline").read_bytes().split(b"\x00")
                args = [a.decode("utf-8", errors="replace") for a in raw if a]
                if _is_conserver_on_port(args, port):
                    pid = int(proc.name)
                    pgid = os.getpgid(pid)
                    if pgid not in seen_pgids:
                        seen_pgids.add(pgid)
                        os.killpg(pgid, signal.SIGTERM)
            except (OSError, ValueError, ProcessLookupError):
                pass
    except OSError:
        pass


def stop_conserver() -> None:
    """Stop conserver and its child process by killing the whole process group."""
    global _conserver_process

    def _kill_pgroup(pid: int, sig: int) -> None:
        try:
            os.killpg(os.getpgid(pid), sig)
        except (ProcessLookupError, OSError):
            pass

    if _conserver_process:
        pid = _conserver_process.pid
        _kill_pgroup(pid, signal.SIGTERM)
        try:
            _conserver_process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            _kill_pgroup(pid, signal.SIGKILL)
        _conserver_process = None

    if _pid_file_path.exists():
        try:
            pid = int(_pid_file_path.read_text().strip())
            _kill_pgroup(pid, signal.SIGTERM)
        except (ValueError, OSError):
            pass
        _pid_file_path.unlink(missing_ok=True)

    try:
        port = str(int(os.environ.get("SOL_CONSERVER_PORT", "3109")))
    except ValueError:
        port = "3109"
    _kill_conservers_on_port(port)


def startup(
    db_path: str,
    ini_path,
    cf_path: Path,
    passwd_path: Path,
    log_dir: Path,
) -> None:
    """Orchestrate conserver startup in a daemon thread during gunicorn on_starting."""
    try:
        from dracs.db import Site, System, db_initialize, get_session
        from dracs.sites import get_site_ini_config, set_site_ini_config

        db_initialize(db_path)
        log_dir.mkdir(parents=True, exist_ok=True)

        with get_session() as session:
            sites = session.query(Site).all()
            site_names = [s.name for s in sites]
            site_systems = {}
            for site in sites:
                systems = session.query(System).filter(System.site_id == site.id).all()
                site_systems[site.name] = [s.name for s in systems if s.name]

        site_passwords = {}
        for site_name in site_names:
            cfg = get_site_ini_config(site_name)
            site_passwords[site_name] = (
                cfg.get("defaults", {}).get("conserver_password") or None
            )

        passwd_mgr = ConserverPasswd(passwd_path)
        final_passwords = passwd_mgr.sync(site_passwords)

        for site_name, plaintext in final_passwords.items():
            if site_passwords.get(site_name) is None:
                cfg = get_site_ini_config(site_name)
                cfg["defaults"]["conserver_password"] = plaintext
                set_site_ini_config(site_name, cfg)
                logger.info("Initialized conserver auth for site '%s'", site_name)

        sites_data = []
        for site_name in site_names:
            ini_cfg = get_site_ini_config(site_name)
            ini_hosts = ini_cfg.get("hosts", {})
            all_hosts = {
                hostname: ini_hosts.get(hostname, {})
                for hostname in site_systems.get(site_name, [])
            }
            sites_data.append(
                {
                    "name": site_name,
                    "defaults": ini_cfg.get("defaults", {}),
                    "hosts": all_hosts,
                }
            )

        try:
            primary_port = str(int(os.environ.get("SOL_CONSERVER_PORT", "3109")))
        except ValueError:
            primary_port = "3109"
        try:
            secondary_port = str(
                int(os.environ.get("SOL_CONSERVER_SLAVE_PORT", "3110"))
            )
        except ValueError:
            secondary_port = "3110"

        ssl_cert_path, ssl_key_path = _ssl_cert_key_paths()
        ssl_ca = os.environ.get("SOL_SSL_CA", "")
        ssl_creds_path = None
        if ssl_cert_path and ssl_key_path:
            ssl_creds_path = cf_path.parent / "conserver-ssl.pem"
            _build_ssl_credentials(ssl_cert_path, ssl_key_path, ssl_creds_path)
            console_cf_path = cf_path.parent / "console.cf"
            _write_console_cf(console_cf_path, Path(ssl_ca) if ssl_ca else None)
            logger.info("Conserver SSL enabled (cert: %s)", ssl_cert_path)

        config_gen = ConserverConfig(cf_path, passwd_path, log_dir)
        config_gen.generate(
            sites_data,
            primary_port=primary_port,
            secondary_port=secondary_port,
            ssl_creds_path=ssl_creds_path,
        )

        disable_systemd_service()
        start_conserver(cf_path)

    except Exception as exc:
        logger.error("conserver startup failed: %s", exc, exc_info=True)

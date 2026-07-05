"""Conserver management for DRACS IPMI SOL feature."""

import logging
import os
import re
import secrets
import shutil
import signal
import string
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

_conserver_process = None
_pid_file_path = Path("/var/run/dracs/conserver.pid")


class ConserverPasswd:
    """Manages /etc/dracs/conserver.passwd - one entry per dracs site."""

    def __init__(self, passwd_path: Path):
        self.passwd_path = passwd_path

    def sync(self, site_passwords: dict) -> dict:
        """
        Ensure conserver.passwd has exactly one entry per site.

        Accepts {site_name: plaintext_password or None}.
        Generates a random password for any None entry.
        Returns {site_name: plaintext} with generated passwords filled in.
        """
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
        """Verify a plaintext password against the stored crypt hash."""
        stored = self._read().get(site_name)
        if not stored:
            return False
        try:
            salt = stored[:2]
            result = subprocess.run(
                ["openssl", "passwd", "-crypt", "-salt", salt, plaintext],
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
        result = subprocess.run(
            ["openssl", "passwd", "-crypt", plaintext],
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout.strip()


class ConserverConfig:
    """Generates /etc/dracs/conserver.cf from dracs site and host data."""

    def __init__(self, cf_path: Path, passwd_path: Path, log_dir: Path):
        self.cf_path = cf_path
        self.passwd_path = passwd_path
        self.log_dir = log_dir

    def generate(self, sites_data: list) -> None:
        """
        Write conserver.cf.

        sites_data: list of {
            name: str,
            defaults: {username, password, ...},
            hosts: {hostname: {username, password, ...}}
        }
        hosts contains ALL systems for the site; entries without credentials
        (empty dict) fall back to the site-level default block.
        """
        from dracs.snmp import ValidationError, build_idrac_hostname

        lines = [
            "# Managed by dracs-webapp. Do not edit manually.\n",
            "\n",
            "config * {\n",
            f"    passwdfile {self.passwd_path};\n",
            f"    logfile {self.log_dir}/conserver.log;\n",
            "    daemonmode no;\n",
            "}\n",
            "\n",
            "access * {\n",
            "    allowed *.*;\n",
            "}\n",
        ]

        for site in sites_data:
            site_name = site["name"]
            safe_site = self._safe_name(site_name)
            site_defs = site.get("defaults", {})
            site_user = site_defs.get("username") or "root"
            site_pass = site_defs.get("password") or ""

            lines.append("\n")
            lines.extend(
                self._format_default_block(
                    f"ipmi_sol_{safe_site}", site_user, site_pass
                )
            )

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
                        hostname, mgmt_host, default_name, site_name
                    )
                )

        self.cf_path.parent.mkdir(parents=True, exist_ok=True)
        content = "".join(lines)
        tmp = self.cf_path.with_suffix(".tmp")
        tmp.write_text(content)
        tmp.chmod(0o640)
        tmp.rename(self.cf_path)

    def _format_default_block(self, name: str, username: str, password: str) -> list:
        return [
            f"default {name} {{\n",
            "    type exec;\n",
            f"    exec /usr/bin/ipmitool -I lanplus -H & -U {username} -P {password} sol activate;\n",
            "    execsubst & = hs;\n",
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
    ) -> list:
        return [
            f"console {console_name} {{\n",
            "    master localhost;\n",
            f"    include {default_name};\n",
            f"    host {mgmt_host};\n",
            f"    rw {site_name};\n",
            "}\n",
        ]

    @staticmethod
    def _safe_name(name: str) -> str:
        return re.sub(r"[^a-zA-Z0-9_]", "_", name)

    @staticmethod
    def _has_host_override(host_creds: dict, site_defaults: dict) -> bool:
        for key in ("username", "password"):
            host_val = host_creds.get(key) or ""
            site_val = site_defaults.get(key) or ""
            if host_val and host_val != site_val:
                return True
        return False


def disable_systemd_service() -> None:
    """Ensure the conserver systemd service is disabled."""
    try:
        subprocess.run(
            ["systemctl", "disable", "--now", "conserver"],
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

    _conserver_process = subprocess.Popen(
        [conserver_bin, "-c", str(cf_path)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        _pid_file_path.parent.mkdir(parents=True, exist_ok=True)
        _pid_file_path.write_text(str(_conserver_process.pid))
    except OSError:
        pass
    logger.info("conserver started (PID %s)", _conserver_process.pid)
    return _conserver_process


def stop_conserver() -> None:
    """Stop the conserver process."""
    global _conserver_process

    if _conserver_process:
        try:
            _conserver_process.terminate()
            _conserver_process.wait(timeout=5)
        except (ProcessLookupError, subprocess.TimeoutExpired):
            try:
                _conserver_process.kill()
            except ProcessLookupError:
                pass
        _conserver_process = None

    if _pid_file_path.exists():
        try:
            pid = int(_pid_file_path.read_text().strip())
            os.kill(pid, signal.SIGTERM)
        except (ProcessLookupError, ValueError, OSError):
            pass
        _pid_file_path.unlink(missing_ok=True)


def startup(
    db_path: str,
    ini_path,
    cf_path: Path,
    passwd_path: Path,
    log_dir: Path,
) -> None:
    """
    Orchestrate conserver startup.

    Called from a daemon thread in gunicorn on_starting. Initializes the DB
    connection independently (master process, before worker forks).
    """
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
                logger.info("Generated conserver password for site '%s'", site_name)

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

        config_gen = ConserverConfig(cf_path, passwd_path, log_dir)
        config_gen.generate(sites_data)

        disable_systemd_service()
        start_conserver(cf_path)

    except Exception as exc:
        logger.error("conserver startup failed: %s", exc, exc_info=True)

"""Flask web application for DRACS inventory management."""

import asyncio
import configparser
from datetime import datetime
import glob
import gzip
import hashlib
import json
import os
import re
import secrets
import shutil
import socket
import sys
import subprocess
import tempfile
import threading
import time
import urllib.request
import zipfile
from urllib.parse import quote as url_quote, urlunparse

import defusedxml.ElementTree as defused_ET
from pathlib import Path
from dotenv import load_dotenv
from flask import (
    Flask,
    render_template,
    jsonify,
    session,
    request,
    redirect,
    url_for,
    Response,
)
from markupsafe import Markup
from werkzeug.middleware.proxy_fix import ProxyFix

import dracs
from dracs.audit import audit_log
from dracs.db import db_initialize, get_session, System
from dracs.commands import refresh_dell_warranty
from dracs.sites import migrate_passwords_ini
from dracs.snmp import build_idrac_hostname
from dracs.users import (
    authenticate as authenticate_user,
    create_user,
    delete_user,
    get_user_site_roles,
    list_users,
    set_user_site_role,
    update_user_password,
    update_user_role,
)
from dracs.validation import validate_hostname, validate_version
from dracs.vnc import (
    VncSessionManager,
    MaxSessionsError,
    get_vnc_credentials,
    check_vnc_connectivity,
)

# Load environment variables from .env file
# Look for .env in current directory or parent directories
env_path = Path(".env")
if env_path.exists():
    load_dotenv(env_path)
else:  # pragma: no cover
    # Try to find .env in the project root
    project_root = Path(__file__).parent.parent.parent
    env_path = project_root / ".env"
    if env_path.exists():
        load_dotenv(env_path)


app = Flask(__name__)
# Trust one proxy (nginx) for X-Forwarded-For and X-Forwarded-Proto
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1)

# Secret key for sessions (use environment variable in production)
# Default key is only for development - change in production!
app.secret_key = os.environ.get(
    "FLASK_SECRET_KEY",
    "dev-secret-key-change-in-production-12345678901234567890123456789012",
)

# Session security settings
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"

# Auto-refresh frequency (in seconds, 0 = disabled)
REFRESH_FREQUENCY = int(os.environ.get("REFRESH_FREQUENCY", "10"))

# Warranty expiration highlighting
HIGHLIGHT_EXPIRED = os.environ.get("HIGHLIGHT_EXPIRED", "true").lower() in (
    "true",
    "1",
    "yes",
)
HIGHLIGHT_EXPIRING = int(os.environ.get("HIGHLIGHT_EXPIRING", "30"))

# Pagination
DEFAULT_PAGE_SIZE = int(os.environ.get("DEFAULT_PAGE_SIZE", "20"))

# Firmware and BIOS version highlighting
HIGHLIGHT_FIRMWARE = os.environ.get("HIGHLIGHT_FIRMWARE", "true").lower() in (
    "true",
    "1",
    "yes",
)
HIGHLIGHT_BIOS = os.environ.get("HIGHLIGHT_BIOS", "true").lower() in (
    "true",
    "1",
    "yes",
)

# VNC Console configuration
VNC_ENABLE = os.environ.get("VNC_ENABLE", "false").lower() in (
    "true",
    "1",
    "yes",
)
SOL_ENABLE = os.environ.get("SOL_ENABLE", "false").lower() in ("true", "1", "yes")
VNC_TIMEOUT = int(os.environ.get("VNC_TIMEOUT", "30"))
VNC_MAX_SESSIONS = int(os.environ.get("VNC_MAX_SESSIONS", "20"))
VNC_WEBSOCKIFY_PORT = int(os.environ.get("VNC_WEBSOCKIFY_PORT", "6080"))
VNC_PROXY_ENABLE = os.environ.get("VNC_PROXY_ENABLE", "false").lower() in (
    "true",
    "1",
    "yes",
)

_DEFAULT_CONSOLE_WIDTH = 800
_DEFAULT_CONSOLE_HEIGHT = 600


def _parse_console_size(value: str) -> tuple:
    try:
        w, h = value.lower().split("x")
        w, h = int(w), int(h)
        if w > 0 and h > 0:
            return (w, h)
    except (ValueError, AttributeError):
        pass
    return (_DEFAULT_CONSOLE_WIDTH, _DEFAULT_CONSOLE_HEIGHT)


VNC_CONSOLE_WIDTH, VNC_CONSOLE_HEIGHT = _parse_console_size(
    os.environ.get("VNC_CONSOLE_SIZE", "800x600")
)


# Google OAuth2 — enabled at startup so the template can reflect it
def _google_auth_enabled() -> bool:
    from dracs.google_auth import is_enabled as _ga_is_enabled

    return _ga_is_enabled()


GOOGLE_AUTH_ENABLED = _google_auth_enabled()

# QUADS integration — configured per-site via Manage Site UI
_QUADS_CACHE_TTL = 86400
_quads_host_cache: dict = {}

vnc_manager = None
if VNC_ENABLE:
    from dracs.vnc import get_token_dir

    vnc_manager = VncSessionManager(get_token_dir(), VNC_TIMEOUT, VNC_MAX_SESSIONS)

# Initialize database on app startup
DB_PATH = os.environ.get("DRACS_DB", "warranty.db")
db_initialize(DB_PATH)

# Migrate drac-passwords.ini to site-prefixed format on first startup
migrate_passwords_ini()


@app.before_request
def _refresh_bearer_token():
    auth = request.headers.get("Authorization", "")
    if isinstance(auth, str) and auth.startswith("Bearer "):
        from dracs.tokens import refresh_token

        try:
            refresh_token(auth[7:])
        except Exception as e:
            app.logger.debug("Token refresh failed: %s", e)


def _client_ip() -> str:
    return request.remote_addr or ""


def _quads_cache_get(username: str, site_id):
    entry = _quads_host_cache.get((username, site_id))
    if entry is None:
        return None
    hosts, ts = entry
    if time.time() - ts > _QUADS_CACHE_TTL:
        _quads_host_cache.pop((username, site_id), None)
        return None
    return hosts


def _quads_cache_set(username: str, site_id, hosts) -> None:
    _quads_host_cache[(username, site_id)] = (frozenset(hosts), time.time())


def _quads_cache_invalidate(username: str) -> None:
    for key in [k for k in _quads_host_cache if k[0] == username]:
        _quads_host_cache.pop(key, None)


def _fetch_quads_hosts(username: str, quads_url: str):
    if not quads_url:
        return None
    url = f"{quads_url}/api/v3/schedules/current"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "dracs-webapp/1.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:  # nosec
            schedules = json.loads(resp.read().decode())
    except Exception:
        return None
    return frozenset(
        s["host"]["name"]
        for s in schedules
        if s.get("assignment")
        and s.get("host")
        and (
            s["assignment"].get("owner") == username
            or username in (s["assignment"].get("ccuser") or [])
        )
    )


def _get_quads_hosts_for_user(username: str, site_id, quads_url: str):
    cached = _quads_cache_get(username, site_id)
    if cached is not None:
        return cached
    hosts = _fetch_quads_hosts(username, quads_url)
    if hosts is not None:
        _quads_cache_set(username, site_id, hosts)
    return hosts


def _site_id_for_host(hostname: str):
    """Return the site_id for a given hostname, or None."""
    with get_session() as db_sess:
        system = db_sess.query(System).filter(System.name == hostname).first()
        return system.site_id if system else None


def _get_effective_role():
    """Return (is_superadmin, effective_role) for the current request."""
    if session.get("is_superadmin", False):
        return True, "admin"
    if session.get("authenticated", False):
        return False, session.get("role")
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        from dracs.tokens import validate_token

        result = validate_token(auth[7:])
        if result:
            from dracs.users import _superadmin_username

            is_super = result[0] == _superadmin_username()
            return is_super, result[1]
    return False, None


def _quads_host_access(username: str, hostname: str, site_id: int) -> bool:
    """Return True if the user has quads-role access to the given hostname."""
    from dracs.users import get_user_role_for_site
    from dracs.sites import get_site_ini_config
    from dracs.db import Site

    site_role = get_user_role_for_site(username, site_id)
    if site_role != "quads":
        return False

    with get_session() as db_sess:
        site_obj = db_sess.get(Site, site_id)
        site_name = site_obj.name if site_obj else None
    if not site_name:
        return False

    site_cfg = get_site_ini_config(site_name)
    quads_enabled = site_cfg["defaults"].get("quads_enabled", "false").lower() in (
        "true",
        "1",
        "yes",
    )
    quads_url = site_cfg["defaults"].get("quads_url", "").rstrip("/")
    if not (quads_enabled and quads_url):
        return False

    allowed = _get_quads_hosts_for_user(username, site_id, quads_url)
    return allowed is not None and hostname in allowed


def _require_auth(required_role=None, site_id=None):
    username = None
    is_superadmin = False
    token_role = None

    if session.get("authenticated", False):
        username = session.get("username", "")
        is_superadmin = session.get("is_superadmin", False)
    else:
        auth = request.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            from dracs.tokens import validate_token

            result = validate_token(auth[7:])
            if result:
                from dracs.users import _superadmin_username

                username = result[0]
                token_role = result[1]
                is_superadmin = username == _superadmin_username()

    if username is None:
        return None, (
            jsonify({"success": False, "message": "Authentication required"}),
            401,
        )

    if is_superadmin:
        return username, None

    if site_id is not None and required_role:
        from dracs.users import get_user_role_for_site

        site_role = get_user_role_for_site(username, site_id)
        if site_role is None:
            return None, (
                jsonify({"success": False, "message": "Authentication required"}),
                401,
            )
        if required_role and site_role != required_role:
            return None, (
                jsonify({"success": False, "message": "Insufficient permissions"}),
                403,
            )
        return username, None

    role = token_role if token_role is not None else session.get("role", "user")
    if required_role and role != required_role:
        return None, (
            jsonify({"success": False, "message": "Insufficient permissions"}),
            403,
        )
    return username, None


def _get_requested_site():
    from dracs.db import get_default_site_id, get_primary_site_name, get_site_by_name

    site_name = request.args.get("site")
    if not site_name:
        default_id = get_default_site_id()
        return default_id, get_primary_site_name()
    site = get_site_by_name(site_name)
    if site is None:
        return None, site_name
    return site["id"], site["name"]


def get_all_systems(site_id=None):
    with get_session() as session:
        query = session.query(System).order_by(System.name)
        if site_id is not None:
            query = query.filter(System.site_id == site_id)
        return query.all()


def system_to_dict(system):
    """Convert System object to dictionary."""
    return {
        "svc_tag": system.svc_tag,
        "name": system.name,
        "model": system.model,
        "idrac_version": system.idrac_version,
        "bios_version": system.bios_version,
        "exp_date": system.exp_date,
        "exp_epoch": system.exp_epoch,
    }


def _find_passwords_ini() -> Path | None:
    config_file = Path("drac-passwords.ini")
    if config_file.exists():
        return config_file
    config_file = Path("/etc/dracs/drac-passwords.ini")
    if config_file.exists():
        return config_file
    return None


def _resolve_site_for_host(hostname: str) -> str:
    try:
        with get_session() as sess:
            from dracs.db import Site

            system = sess.query(System).filter(System.name == hostname).first()
            if system and system.site_id:
                site_obj = sess.get(Site, system.site_id)
                if site_obj:
                    return site_obj.name
        from dracs.db import get_primary_site_name

        return get_primary_site_name()
    except Exception:
        return "Default"


def get_idrac_credentials(hostname: str, site: str | None = None) -> tuple:
    config_file = _find_passwords_ini()
    if config_file is None:
        return ("root", "calvin")

    if site is None:
        site = _resolve_site_for_host(hostname)

    config = configparser.RawConfigParser()
    config.read(config_file)

    host_section = f"{site}-{hostname}"
    defaults_section = f"{site}-DEFAULTS"

    if host_section in config:
        username = config.get(
            host_section,
            "username",
            fallback=config.get(defaults_section, "username", fallback="root"),
        )
        password = config.get(
            host_section,
            "password",
            fallback=config.get(defaults_section, "password", fallback="calvin"),
        )
    elif defaults_section in config:
        username = config.get(defaults_section, "username", fallback="root")
        password = config.get(defaults_section, "password", fallback="calvin")
    else:
        return ("root", "calvin")

    return (username, password)


def _run_command_thread(cmd: list, log_file_path: str) -> None:
    """Run a command in a background thread and properly wait for completion."""
    try:
        with open(log_file_path, "a") as log_file:
            subprocess.run(  # nosec # nosemgrep
                cmd, stdout=log_file, stderr=subprocess.STDOUT, timeout=600
            )
    except subprocess.TimeoutExpired:
        with open(log_file_path, "a") as log_file:
            log_file.write("\nCommand timed out after 600 seconds\n")
    except Exception as e:
        with open(log_file_path, "a") as log_file:
            log_file.write(f"\nError running command: {str(e)}\n")


def run_command_background(cmd: list, log_file_path: str) -> bool:
    """
    Run a command in the background without blocking.

    Args:
        cmd: Command and arguments as a list
        log_file_path: Path to log file for stdout/stderr

    Returns:
        bool: True if process started successfully, False otherwise
    """
    try:
        # Ensure log directory exists
        log_dir = os.path.dirname(log_file_path)
        if log_dir:
            os.makedirs(log_dir, exist_ok=True)

        # Write initial log header
        with open(log_file_path, "w") as log_file:
            log_file.write(f"Command started at: {datetime.now().isoformat()}\n")
            log_file.write(f"Command: {' '.join(cmd)}\n")
            log_file.write("-" * 80 + "\n\n")

        # Start command in a daemon thread
        thread = threading.Thread(
            target=_run_command_thread, args=(cmd, log_file_path), daemon=True
        )
        thread.start()

        return True

    except Exception as e:
        # Log the error
        try:
            with open(log_file_path, "a") as log_file:
                log_file.write(f"\nError starting process: {str(e)}\n")
        except Exception as log_err:
            print(
                f"Failed to write to log file {log_file_path}: {log_err}",
                file=sys.stderr,
            )
        return False


def get_bios_filename(model: str, bios_version: str) -> str:
    """
    Get BIOS filename from BIOS-filename.ini file.

    Args:
        model: The system model (e.g., "R640", "R650")
        bios_version: The BIOS version (e.g., "2.10.0")

    Returns:
        str: The BIOS filename if found, None otherwise
    """
    config_file = Path("BIOS-filename.ini")

    if not config_file.exists():
        return None

    config = configparser.ConfigParser()
    config.read(config_file)

    # Check if model section exists
    if model not in config:
        return None

    # Get filename for the BIOS version
    return config[model].get(bios_version, None)


def parse_job_queue(output: str) -> list:
    """
    Parse the output from 'racadm jobqueue view' command.

    Args:
        output: The raw output from the command

    Returns:
        list: List of dictionaries containing job information
    """
    jobs = []
    current_job = {}

    for line in output.split("\n"):
        line = line.strip()

        # Skip empty lines and separator lines
        if not line or line.startswith("---") or "JOB QUEUE" in line:
            continue

        # New job starts with [Job ID=...]
        if line.startswith("[Job ID="):
            # Save previous job if it exists
            if current_job:
                jobs.append(current_job)
            # Start new job
            job_id = line.replace("[Job ID=", "").replace("]", "")
            current_job = {"job_id": job_id}
        elif "=" in line and current_job:
            # Parse key=value pairs
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().replace("[", "").replace("]", "")

            # Map to our field names
            if key == "Job Name":
                current_job["job_name"] = value
            elif key == "Status":
                current_job["status"] = value
            elif key == "Actual Start Time":
                current_job["actual_start_time"] = value
            elif key == "Actual Completion Time":
                current_job["actual_completion_time"] = value
            elif key == "Message":
                current_job["message"] = value
            elif key == "Percent Complete":
                current_job["percent_complete"] = value

    # Don't forget the last job
    if current_job:
        jobs.append(current_job)

    return jobs


def test_idrac_connectivity(hostname: str) -> tuple:
    """
    Test SSH connectivity to the iDRAC interface.

    Args:
        hostname: The system hostname

    Returns:
        tuple: (success: bool, message: str)
    """
    if not validate_hostname(hostname):
        return (False, f"Invalid hostname: {hostname}")
    try:
        cmd = _build_ssh_racadm_cmd(hostname, "getremoteservicesstatus")

        result = subprocess.run(  # nosec # nosemgrep
            cmd, capture_output=True, text=True, timeout=15  # nosemgrep
        )

        # Check if command succeeded and output contains "Status.*Ready"
        if result.returncode == 0:
            # Use regex to check for "Status.*Ready" pattern
            if re.search(r"Status.*Ready", result.stdout, re.IGNORECASE):
                return (
                    True,
                    f"iDRAC Access Succeeded for {build_idrac_hostname(hostname)}",
                )
            else:
                return (
                    False,
                    f"iDRAC responded but status not ready: {result.stdout[:100]}",
                )
        else:
            return (
                False,
                "iDRAC Access Failed: "
                f"{result.stderr[:100] if result.stderr else 'Connection failed'}",
            )

    except subprocess.TimeoutExpired:
        return (False, "iDRAC Access Failed: Connection timeout")
    except FileNotFoundError:
        return (
            False,
            "iDRAC Access Failed: sshpass command not found (please install sshpass)",
        )
    except Exception as e:
        return (False, f"iDRAC Access Failed: {str(e)}")


@app.route("/")
def index():
    """Main page with inventory table and filters."""
    from dracs.db import list_sites

    site_id, site_name = _get_requested_site()
    systems = get_all_systems(site_id=site_id)

    systems_data = [system_to_dict(s) for s in systems]

    bios_versions = sorted(set(s.bios_version for s in systems if s.bios_version))
    firmware_versions = sorted(set(s.idrac_version for s in systems if s.idrac_version))
    models = sorted(set(s.model for s in systems if s.model))

    is_authenticated = session.get("authenticated", False)
    username = session.get("username", None)
    user_role = session.get("role", None)
    is_superadmin = session.get("is_superadmin", False)
    is_sso_login = session.get("sso_login", False)

    is_quads_user = False
    quads_empty = False
    if is_authenticated and not is_superadmin and site_id is not None:
        from dracs.sites import get_site_ini_config
        from dracs.users import get_user_role_for_site

        site_role = get_user_role_for_site(username, site_id)
        if site_role is not None:
            user_role = site_role
        else:
            user_role = None  # no site role → unauthenticated view

        site_cfg = get_site_ini_config(site_name)
        site_quads_enabled = site_cfg["defaults"].get(
            "quads_enabled", "false"
        ).lower() in (
            "true",
            "1",
            "yes",
        )
        site_quads_url = site_cfg["defaults"].get("quads_url", "").rstrip("/")
        if site_quads_enabled and site_quads_url and site_role == "quads":
            allowed = _get_quads_hosts_for_user(username, site_id, site_quads_url)
            if allowed is not None:
                is_quads_user = True
                if not allowed:
                    systems_data = []
                    quads_empty = True
                else:
                    systems_data = [s for s in systems_data if s["name"] in allowed]

    site_quads_enabled = False
    if is_authenticated and site_id is not None:
        from dracs.sites import get_site_ini_config

        _qcfg = get_site_ini_config(site_name)
        _qon = _qcfg["defaults"].get("quads_enabled", "false").lower() in (
            "true",
            "1",
            "yes",
        )
        _qurl = _qcfg["defaults"].get("quads_url", "").strip()
        site_quads_enabled = _qon and bool(_qurl)

    all_sites = [s["name"] for s in list_sites()]

    return render_template(
        "index.html",
        systems_json=json.dumps(systems_data),
        bios_versions_json=json.dumps(bios_versions),
        firmware_versions_json=json.dumps(firmware_versions),
        models_json=json.dumps(models),
        is_authenticated=is_authenticated,
        username=username,
        user_role=user_role,
        is_superadmin=is_superadmin,
        current_site=site_name,
        all_sites=all_sites,
        refresh_frequency=REFRESH_FREQUENCY,
        highlight_expired=HIGHLIGHT_EXPIRED,
        highlight_expiring=HIGHLIGHT_EXPIRING,
        default_page_size=DEFAULT_PAGE_SIZE,
        highlight_firmware=HIGHLIGHT_FIRMWARE,
        highlight_bios=HIGHLIGHT_BIOS,
        vnc_enabled=VNC_ENABLE,
        vnc_console_width=VNC_CONSOLE_WIDTH,
        vnc_console_height=VNC_CONSOLE_HEIGHT,
        is_quads_user=is_quads_user,
        quads_empty=quads_empty,
        google_auth_enabled=GOOGLE_AUTH_ENABLED,
        is_sso_login=is_sso_login,
        site_quads_enabled=site_quads_enabled,
        dracs_version=dracs.__version__,
    )


@app.route("/api/systems")
def api_systems():
    """JSON API endpoint to get all systems."""
    site_id, site_name = _get_requested_site()
    systems = get_all_systems(site_id=site_id)
    systems_data = [system_to_dict(s) for s in systems]
    is_authenticated = session.get("authenticated", False)
    username = session.get("username")
    is_superadmin = session.get("is_superadmin", False)
    if is_authenticated and not is_superadmin and site_id is not None:
        from dracs.sites import get_site_ini_config
        from dracs.users import get_user_role_for_site

        site_role = get_user_role_for_site(username, site_id)
        site_cfg = get_site_ini_config(site_name)
        site_quads_enabled = site_cfg["defaults"].get(
            "quads_enabled", "false"
        ).lower() in (
            "true",
            "1",
            "yes",
        )
        site_quads_url = site_cfg["defaults"].get("quads_url", "").rstrip("/")
        if site_quads_enabled and site_quads_url and site_role == "quads":
            allowed = _get_quads_hosts_for_user(username, site_id, site_quads_url)
            if allowed is not None:
                systems_data = [s for s in systems_data if s["name"] in allowed]
    return jsonify(systems_data)


@app.route("/api/firmware-versions/<model>")
def api_firmware_versions(model):
    """Get unique firmware versions for systems matching the specified model."""
    try:
        _, err = _require_auth(required_role="admin")
        if err:
            return err

        # Get all systems with the specified model
        with get_session() as db_session:
            systems = db_session.query(System).filter(System.model == model).all()

        # Extract unique firmware versions (excluding None/empty)
        firmware_versions = sorted(
            set(s.idrac_version for s in systems if s.idrac_version)
        )

        return jsonify({"success": True, "model": model, "versions": firmware_versions})

    except Exception as e:
        return jsonify({"success": False, "message": f"Error: {str(e)}"}), 500


@app.route("/api/bios-versions/<model>")
def api_bios_versions(model):
    """Get unique BIOS versions for systems matching the specified model."""
    try:
        _, err = _require_auth(required_role="admin")
        if err:
            return err

        # Get all systems with the specified model
        with get_session() as db_session:
            systems = db_session.query(System).filter(System.model == model).all()

        # Extract unique BIOS versions (excluding None/empty)
        bios_versions = sorted(set(s.bios_version for s in systems if s.bios_version))

        return jsonify({"success": True, "model": model, "versions": bios_versions})

    except Exception as e:
        return jsonify({"success": False, "message": f"Error: {str(e)}"}), 500


@app.route("/api/available-firmware/<model>")
def api_available_firmware(model):
    """List firmware versions available on disk for a model."""
    try:
        _, err = _require_auth(required_role="admin")
        if err:
            return err

        prefix = f"{model}-"
        suffix = ".d9"
        versions = []
        if FIRMWARE_IMAGE_DIR.is_dir():
            for f in FIRMWARE_IMAGE_DIR.iterdir():
                name = f.name
                if name.startswith(prefix) and name.endswith(suffix):
                    ver = name[len(prefix) : -len(suffix)]
                    if ver:
                        versions.append(ver)

        versions.sort(key=lambda v: tuple(map(int, v.split("."))), reverse=True)
        return jsonify({"success": True, "model": model, "versions": versions})

    except Exception as e:
        return jsonify({"success": False, "message": f"Error: {str(e)}"}), 500


@app.route("/api/available-bios/<model>")
def api_available_bios(model):
    """List BIOS versions available on disk for a model."""
    try:
        _, err = _require_auth(required_role="admin")
        if err:
            return err

        config_file = Path("BIOS-filename.ini")
        if not config_file.exists():
            config_file = Path("/etc/dracs/BIOS-filename.ini")

        versions = []
        if config_file.exists():
            config = configparser.ConfigParser()
            config.read(config_file)
            if model in config:
                versions = list(config[model].keys())

        versions.sort(key=lambda v: tuple(map(int, v.split("."))), reverse=True)
        return jsonify({"success": True, "model": model, "versions": versions})

    except Exception as e:
        return jsonify({"success": False, "message": f"Error: {str(e)}"}), 500


@app.route("/login", methods=["POST"])
def login():
    """Handle login POST request."""
    try:
        data = request.get_json()
        if not data:
            return jsonify({"success": False, "message": "Invalid request"}), 400

        username = data.get("username", "")
        password = data.get("password", "")

        result = authenticate_user(username, password)
        if result:
            auth_username, auth_role = result
            from dracs.users import _superadmin_username

            if auth_role is None:
                sr_values = [sr["role"] for sr in get_user_site_roles(auth_username)]
                if "admin" in sr_values:
                    auth_role = "admin"
                elif "user" in sr_values:
                    auth_role = "user"

            session["authenticated"] = True
            session["username"] = auth_username
            session["role"] = auth_role
            session["is_superadmin"] = auth_username == _superadmin_username()
            audit_log("login", user=auth_username, source=_client_ip())
            return jsonify({"success": True, "message": "Login successful"})
        else:
            audit_log("login", user=username, source=_client_ip(), result="denied")
            return jsonify({"success": False, "message": "Invalid credentials"}), 401
    except Exception as e:
        return jsonify({"success": False, "message": f"Login error: {str(e)}"}), 400


@app.route("/logout", methods=["POST"])
def logout():
    """Handle logout request."""
    username = session.get("username", "")
    _quads_cache_invalidate(username)
    audit_log("logout", user=username, source=_client_ip())
    session.clear()
    return jsonify({"success": True, "message": "Logged out successfully"})


@app.route("/auth/google")
def auth_google():
    """Initiate Google OAuth2 login flow."""
    if not GOOGLE_AUTH_ENABLED:
        return redirect(url_for("index"))
    from dracs.google_auth import make_flow

    return_url = request.args.get("return_url", "")
    if return_url and return_url.startswith("/"):
        session["oauth_return_url"] = return_url

    state = secrets.token_hex(16)
    session["google_oauth_state"] = state
    redirect_uri = url_for("auth_google_callback", _external=True)
    flow = make_flow(redirect_uri, state=state)
    auth_url, _ = flow.authorization_url()
    return redirect(auth_url)


@app.route("/auth/google/callback")
def auth_google_callback():
    """Handle Google OAuth2 callback and establish a session."""
    if not GOOGLE_AUTH_ENABLED:
        return redirect(url_for("index"))

    expected_state = session.pop("google_oauth_state", None)
    if not expected_state or expected_state != request.args.get("state"):
        return redirect(url_for("index"))

    from dracs.google_auth import make_flow, get_verified_email
    from dracs.db import list_sites
    from dracs.sites import get_site_ini_config

    redirect_uri = url_for("auth_google_callback", _external=True)
    flow = make_flow(redirect_uri, state=expected_state)
    try:
        flow.fetch_token(authorization_response=request.url)
    except Exception:
        return redirect(url_for("index"))

    email = get_verified_email(flow.credentials)
    if not email:
        return redirect(url_for("index"))

    username = email.split("@")[0]

    all_users = list_users()
    existing = {u["username"] for u in all_users}
    if username not in existing:
        try:
            create_user(username, secrets.token_hex(32), None, created_by="google-sso")
        except Exception:
            return redirect(url_for("index"))
        for site in list_sites():
            cfg = get_site_ini_config(site["name"])
            quads_on = cfg["defaults"].get("quads_enabled", "false").lower() in (
                "true",
                "1",
                "yes",
            )
            if quads_on:
                set_user_site_role(username, site["id"], "quads")
        stored_role = None
    else:
        user_record = next((u for u in all_users if u["username"] == username), None)
        stored_role = user_record["role"] if user_record else None
        if user_record and stored_role is None:
            sr_values = [sr["role"] for sr in user_record.get("site_roles", [])]
            if "admin" in sr_values:
                stored_role = "admin"
            elif "user" in sr_values:
                stored_role = "user"

    session["authenticated"] = True
    session["username"] = username
    session["role"] = stored_role
    session["is_superadmin"] = False
    session["sso_login"] = True
    audit_log("login", user=email, source=_client_ip())
    return_url = session.pop("oauth_return_url", "") or url_for("index")
    return redirect(return_url)


@app.route("/api/auth-status")
def auth_status():
    """Check if user is authenticated."""
    return jsonify(
        {
            "authenticated": session.get("authenticated", False),
            "username": session.get("username", None),
            "role": session.get("role", None),
        }
    )


@app.route("/api/token-login", methods=["POST"])
def api_token_login():
    """Authenticate and return an API token. Rejects superadmin."""
    try:
        data = request.get_json()
        if not data:
            return jsonify({"success": False, "message": "Invalid request"}), 400

        username = data.get("username", "")
        password = data.get("password", "")

        from dracs.users import _superadmin_username

        if username == _superadmin_username():
            return (
                jsonify(
                    {
                        "success": False,
                        "message": "Superadmin cannot authenticate via API token. "
                        "Use the web interface.",
                    }
                ),
                403,
            )

        result = authenticate_user(username, password)
        if not result:
            audit_log(
                "token_login", user=username, source=_client_ip(), result="denied"
            )
            return jsonify({"success": False, "message": "Invalid credentials"}), 401

        auth_username, auth_role = result
        if auth_role is None:
            sr_values = [sr["role"] for sr in get_user_site_roles(auth_username)]
            auth_role = "admin" if "admin" in sr_values else "user"

        expiry = int(os.environ.get("DRACS_TOKEN_EXPIRY", "36000"))

        from dracs.tokens import cleanup_expired_tokens, generate_token

        cleanup_expired_tokens()
        token_data = generate_token(auth_username, auth_role, expiry)

        audit_log("token_login", user=auth_username, source=_client_ip())

        return jsonify(
            {
                "success": True,
                "token": token_data["token"],
                "role": token_data["role"],
                "expires_in": token_data["expires_in"],
            }
        )
    except Exception as e:
        return jsonify({"success": False, "message": f"Error: {str(e)}"}), 500


@app.route("/api/token-logout", methods=["POST"])
def api_token_logout():
    """Invalidate an API token."""
    try:
        auth = request.headers.get("Authorization", "")
        if not auth.startswith("Bearer "):
            return jsonify({"success": False, "message": "No token provided"}), 400

        token_str = auth[7:]
        from dracs.tokens import invalidate_token, validate_token

        result = validate_token(token_str)
        if not result:
            return (
                jsonify({"success": False, "message": "Invalid or expired token"}),
                401,
            )

        username, _ = result
        invalidate_token(token_str)
        audit_log("token_logout", user=username, source=_client_ip())

        return jsonify({"success": True, "message": "Token invalidated"})
    except Exception as e:
        return jsonify({"success": False, "message": f"Error: {str(e)}"}), 500


@app.route("/api/change-password", methods=["POST"])
def api_change_password():
    """Change the current user's own password."""
    try:
        user, err = _require_auth()
        if err:
            return err

        data = request.get_json()
        if not data:
            return jsonify({"success": False, "message": "Invalid request"}), 400

        new_password = data.get("new_password", "")
        is_sso = session.get("sso_login", False)

        if is_sso:
            if not new_password:
                return (
                    jsonify({"success": False, "message": "New password is required"}),
                    400,
                )
        else:
            current_password = data.get("current_password", "")
            if not current_password or not new_password:
                return (
                    jsonify(
                        {
                            "success": False,
                            "message": "Current and new password are required",
                        }
                    ),
                    400,
                )
            result = authenticate_user(user, current_password)
            if not result:
                return (
                    jsonify(
                        {"success": False, "message": "Current password is incorrect"}
                    ),
                    401,
                )

        from dracs.exceptions import ValidationError
        from dracs.users import _superadmin_username, update_superadmin_password

        try:
            if user == _superadmin_username():
                update_superadmin_password(new_password)
            else:
                update_user_password(user, new_password)
        except ValidationError as ve:
            return jsonify({"success": False, "message": str(ve)}), 400

        audit_log("password_change", user=user, source=_client_ip())

        return jsonify({"success": True, "message": "Password changed successfully"})
    except Exception as e:
        return jsonify({"success": False, "message": f"Error: {str(e)}"}), 500


@app.route("/api/refresh", methods=["POST"])
def api_refresh():
    """Refresh warranty and system info for selected system."""
    try:
        user, err = _require_auth(required_role="admin")
        if err:
            return err

        data = request.get_json()
        if not data:
            return jsonify({"success": False, "message": "Invalid request"}), 400

        service_tag = (
            data.get("service_tag", "").strip() if data.get("service_tag") else None
        )
        hostname = data.get("hostname", "").strip() if data.get("hostname") else None

        if not service_tag and not hostname:
            return (
                jsonify(
                    {"success": False, "message": "Service tag or hostname required"}
                ),
                400,
            )

        # Run async refresh function
        asyncio.run(
            refresh_dell_warranty(
                service_tag=service_tag,
                hostname=hostname if not service_tag else None,
                warranty=DB_PATH,
            )
        )

        audit_log(
            "refresh",
            target=service_tag or hostname,
            user=user,
            source=_client_ip(),
        )

        return jsonify(
            {
                "success": True,
                "message": f"Successfully refreshed data for {service_tag or hostname}",
            }
        )

    except Exception as e:
        return jsonify({"success": False, "message": f"Error: {str(e)}"}), 500


@app.route("/api/refresh-multiple", methods=["POST"])
def api_refresh_multiple():
    """Queue refresh jobs for multiple systems."""
    try:
        user, err = _require_auth(required_role="admin")
        if err:
            return err

        data = request.get_json()
        if not data:
            return jsonify({"success": False, "message": "Invalid request"}), 400

        systems = data.get("systems", [])
        if not systems:
            return jsonify({"success": False, "message": "No systems provided"}), 400

        from dracs.jobqueue import enqueue_job

        queued = 0
        for system in systems:
            hostname = (
                system.get("hostname", "").strip() if system.get("hostname") else None
            )
            if hostname:
                enqueue_job("refresh", hostname)
                queued += 1

        audit_log(
            "refresh_multiple",
            user=user,
            source=_client_ip(),
            details=f"queued={queued}",
        )

        return jsonify(
            {
                "success": True,
                "message": f"Queued {queued} refresh jobs.",
                "queued": queued,
                "total": len(systems),
            }
        )

    except Exception as e:
        return jsonify({"success": False, "message": f"Error: {str(e)}"}), 500


@app.route("/api/test-idrac", methods=["POST"])
def api_test_idrac():
    """Test SSH connectivity to the iDRAC interface."""
    try:
        _, err = _require_auth()
        if err:
            return err

        data = request.get_json()
        if not data:
            return jsonify({"success": False, "message": "Invalid request"}), 400

        hostname = data.get("hostname", "").strip()
        if not hostname:
            return jsonify({"success": False, "message": "Hostname required"}), 400

        # Test iDRAC connectivity
        success, message = test_idrac_connectivity(hostname)

        return jsonify({"success": success, "message": message})

    except Exception as e:
        return jsonify({"success": False, "message": f"Error: {str(e)}"}), 500


@app.route("/api/firmware-update", methods=["POST"])
def api_firmware_update():
    """Queue firmware update for a host via the job queue."""
    try:
        user, err = _require_auth(required_role="admin")
        if err:
            return err

        data = request.get_json()
        if not data:
            return jsonify({"success": False, "message": "Invalid request"}), 400

        hostname = data.get("hostname", "").strip()
        target_version = data.get("target_version", "").strip()
        model = data.get("model", "").strip()

        if not hostname or not target_version or not model:
            return (
                jsonify(
                    {
                        "success": False,
                        "message": "Hostname, target version, and model required",
                    }
                ),
                400,
            )

        if not validate_hostname(hostname):
            return jsonify({"success": False, "message": "Invalid hostname"}), 400
        if not validate_version(target_version):
            return jsonify({"success": False, "message": "Invalid version format"}), 400
        if not re.match(r"^[A-Za-z0-9\-]+$", model):
            return jsonify({"success": False, "message": "Invalid model format"}), 400

        from dracs.jobqueue import enqueue_job

        job_id = enqueue_job(
            "firmware_update",
            hostname,
            metadata={"target_version": target_version, "model": model},
        )

        audit_log(
            "firmware_update",
            target=hostname,
            user=user,
            source=_client_ip(),
            details=f"version={target_version},model={model},job_id={job_id}",
        )

        return jsonify(
            {
                "success": True,
                "message": f"Firmware update queued for {hostname}"
                f" to version {target_version}.",
                "job_id": job_id,
            }
        )

    except Exception as e:
        return jsonify({"success": False, "message": f"Error: {str(e)}"}), 500


@app.route("/api/bios-update", methods=["POST"])
def api_bios_update():
    """Queue BIOS update for a host via the job queue."""
    try:
        user, err = _require_auth(required_role="admin")
        if err:
            return err

        data = request.get_json()
        if not data:
            return jsonify({"success": False, "message": "Invalid request"}), 400

        hostname = data.get("hostname", "").strip()
        target_bios = data.get("target_bios", "").strip()
        model = data.get("model", "").strip()

        if not hostname or not target_bios or not model:
            return (
                jsonify(
                    {
                        "success": False,
                        "message": "Hostname, target BIOS version, and model required",
                    }
                ),
                400,
            )

        if not validate_hostname(hostname):
            return jsonify({"success": False, "message": "Invalid hostname"}), 400
        if not validate_version(target_bios):
            return (
                jsonify({"success": False, "message": "Invalid BIOS version format"}),
                400,
            )
        if not re.match(r"^[A-Za-z0-9\-]+$", model):
            return jsonify({"success": False, "message": "Invalid model format"}), 400

        bios_filename = get_bios_filename(model, target_bios)
        if not bios_filename:
            return (
                jsonify(
                    {
                        "success": False,
                        "message": f"BIOS filename not found for"
                        f" model {model} version {target_bios}"
                        " in BIOS-filename.ini",
                    }
                ),
                400,
            )

        from dracs.jobqueue import enqueue_job

        job_id = enqueue_job(
            "bios_update",
            hostname,
            metadata={"target_bios": target_bios, "model": model},
        )

        audit_log(
            "bios_update",
            target=hostname,
            user=user,
            source=_client_ip(),
            details=f"version={target_bios},model={model},job_id={job_id}",
        )

        return jsonify(
            {
                "success": True,
                "message": f"BIOS update queued for {hostname}"
                f" to version {target_bios}.",
                "job_id": job_id,
            }
        )

    except Exception as e:
        return jsonify({"success": False, "message": f"Error: {str(e)}"}), 500


@app.route("/api/job-queue", methods=["POST"])
def api_job_queue():
    """Retrieve job queue from iDRAC via SSH."""
    try:
        _, err = _require_auth()
        if err:
            return err

        data = request.get_json()
        if not data:
            return jsonify({"success": False, "message": "Invalid request"}), 400

        hostname = data.get("hostname", "").strip()

        if not hostname:
            return jsonify({"success": False, "message": "Hostname required"}), 400
        if not validate_hostname(hostname):
            return jsonify({"success": False, "message": "Invalid hostname"}), 400

        cmd = _build_ssh_racadm_cmd(hostname, "jobqueue", "view")

        # Run command and capture output
        result = subprocess.run(  # nosec # nosemgrep
            cmd, capture_output=True, text=True, timeout=30  # nosemgrep
        )

        if result.returncode != 0:
            return (
                jsonify(
                    {
                        "success": False,
                        "message": "Command failed with exit code"
                        f" {result.returncode}: {result.stderr}",
                    }
                ),
                500,
            )

        # Parse job queue output
        jobs = parse_job_queue(result.stdout)

        return jsonify({"success": True, "jobs": jobs})

    except FileNotFoundError:
        return jsonify({"success": False, "message": "sshpass command not found"}), 500
    except subprocess.TimeoutExpired:
        return jsonify({"success": False, "message": "Command timed out"}), 500
    except Exception as e:
        return jsonify({"success": False, "message": f"Error: {str(e)}"}), 500


def _clear_single_job_queue(hostname: str) -> None:
    """
    Clear job queue for a single host in a background thread.
    This function properly waits for the command to complete, avoiding zombie processes.
    """
    try:
        if not validate_hostname(hostname):
            print(f"Invalid hostname: {hostname}")
            return

        cmd = _build_ssh_racadm_cmd(hostname, "jobqueue", "delete", "--all")

        # Run command and wait for completion (prevents zombie processes)
        subprocess.run(  # nosec # nosemgrep
            cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30
        )

    except Exception as e:
        print(f"Error clearing job queue for {hostname}: {str(e)}")


@app.route("/api/clear-job-queue", methods=["POST"])
def api_clear_job_queue():
    """Queue clear job queue operations for selected hosts."""
    try:
        user, err = _require_auth(required_role="admin")
        if err:
            return err

        data = request.get_json()
        if not data:
            return jsonify({"success": False, "message": "Invalid request"}), 400

        hostnames = data.get("hostnames", [])

        if not hostnames or not isinstance(hostnames, list):
            return (
                jsonify({"success": False, "message": "Hostnames list required"}),
                400,
            )

        for h in hostnames:
            if not validate_hostname(h):
                return (
                    jsonify({"success": False, "message": f"Invalid hostname: {h}"}),
                    400,
                )

        from dracs.jobqueue import enqueue_job

        for hostname in hostnames:
            enqueue_job("clear_job_queue", hostname)

        audit_log(
            "clear_job_queue",
            user=user,
            source=_client_ip(),
            details=f"hosts={','.join(hostnames)}",
        )

        return jsonify(
            {
                "success": True,
                "message": f"Clear job queue queued for {len(hostnames)} host(s)",
            }
        )

    except Exception as e:
        return jsonify({"success": False, "message": f"Error: {str(e)}"}), 500


@app.route("/api/refresh-all", methods=["POST"])
def api_refresh_all():
    """Queue refresh jobs for all systems in database."""
    try:
        site_id, _ = _get_requested_site()
        user, err = _require_auth(required_role="admin", site_id=site_id)
        if err:
            return err

        systems = get_all_systems(site_id=site_id)
        total_systems = len(systems)

        if total_systems == 0:
            return jsonify({"success": False, "message": "No systems in database"}), 400

        from dracs.jobqueue import enqueue_batch

        count = enqueue_batch("refresh", "all", site_id=site_id)

        audit_log(
            "refresh_all",
            user=user,
            source=_client_ip(),
            details=f"queued={count}",
        )

        return jsonify(
            {
                "success": True,
                "message": f"Queued {count} refresh jobs.",
                "queued": count,
                "total": total_systems,
            }
        )

    except Exception as e:
        return jsonify({"success": False, "message": f"Error: {str(e)}"}), 500


@app.route("/api/power-status", methods=["POST"])
def api_power_status():
    """Check system power status via racadm."""
    try:
        user, err = _require_auth()
        if err:
            return err

        data = request.get_json()
        if not data:
            return jsonify({"success": False, "message": "Invalid request"}), 400

        hostname = data.get("hostname", "").strip()
        if not hostname:
            return jsonify({"success": False, "message": "Hostname required"}), 400

        if not validate_hostname(hostname):
            return (
                jsonify({"success": False, "message": f"Invalid hostname: {hostname}"}),
                400,
            )

        is_superadmin, eff_role = _get_effective_role()
        if not is_superadmin and eff_role != "admin":
            host_site_id = _site_id_for_host(hostname)
            if host_site_id is None or not _quads_host_access(
                user, hostname, host_site_id
            ):
                return (
                    jsonify({"success": False, "message": "Insufficient permissions"}),
                    403,
                )

        cmd = _build_ssh_racadm_cmd(hostname, "serveraction", "powerstatus")

        result = subprocess.run(  # nosec # nosemgrep
            cmd, capture_output=True, text=True, timeout=15  # nosemgrep
        )

        if result.returncode == 0:
            output = result.stdout.upper()
            if "ON" in output:
                return jsonify({"success": True, "status": "on"})
            elif "OFF" in output:
                return jsonify({"success": True, "status": "off"})
            else:
                return jsonify(
                    {
                        "success": False,
                        "message": f"Unexpected power status: {result.stdout[:100]}",
                    }
                )
        else:
            return jsonify(
                {
                    "success": False,
                    "message": f"Power status check failed: "
                    f"{result.stderr[:100] if result.stderr else 'Unknown error'}",
                }
            )

    except subprocess.TimeoutExpired:
        return jsonify({"success": False, "message": "Connection timeout"}), 500
    except FileNotFoundError:
        return (
            jsonify(
                {
                    "success": False,
                    "message": "sshpass command not found (please install sshpass)",
                }
            ),
            500,
        )
    except Exception as e:
        return jsonify({"success": False, "message": f"Error: {str(e)}"}), 500


@app.route("/api/power-action", methods=["POST"])
def api_power_action():
    """Execute power action on a system via racadm."""
    VALID_ACTIONS = {"powerup", "powerdown", "graceshutdown", "hardreset", "powercycle"}

    try:
        user, err = _require_auth()
        if err:
            return err

        data = request.get_json()
        if not data:
            return jsonify({"success": False, "message": "Invalid request"}), 400

        hostname = data.get("hostname", "").strip()
        if not hostname:
            return jsonify({"success": False, "message": "Hostname required"}), 400

        if not validate_hostname(hostname):
            return (
                jsonify({"success": False, "message": f"Invalid hostname: {hostname}"}),
                400,
            )

        is_superadmin, eff_role = _get_effective_role()
        if not is_superadmin and eff_role != "admin":
            host_site_id = _site_id_for_host(hostname)
            if host_site_id is None or not _quads_host_access(
                user, hostname, host_site_id
            ):
                return (
                    jsonify({"success": False, "message": "Insufficient permissions"}),
                    403,
                )

        action = data.get("action", "").strip()
        if action not in VALID_ACTIONS:
            return (
                jsonify(
                    {
                        "success": False,
                        "message": f"Invalid action: {action}. "
                        f"Must be one of: {', '.join(sorted(VALID_ACTIONS))}",
                    }
                ),
                400,
            )

        cmd = _build_ssh_racadm_cmd(hostname, "serveraction", action)

        result = subprocess.run(  # nosec # nosemgrep
            cmd, capture_output=True, text=True, timeout=30  # nosemgrep
        )

        if result.returncode == 0:
            action_label = {
                "powerup": "Power on",
                "powerdown": "Hard power off",
                "graceshutdown": "Graceful shutdown",
                "hardreset": "Hard reboot",
                "powercycle": "Graceful reboot",
            }[action]
            audit_log(
                "power_action",
                target=hostname,
                user=user,
                source=_client_ip(),
                details=action,
            )
            return jsonify(
                {
                    "success": True,
                    "message": f"{action_label} command sent to {hostname}",
                }
            )
        else:
            return jsonify(
                {
                    "success": False,
                    "message": f"Power action failed: "
                    f"{result.stderr[:100] if result.stderr else result.stdout[:100]}",
                }
            )

    except subprocess.TimeoutExpired:
        return jsonify({"success": False, "message": "Connection timeout"}), 500
    except FileNotFoundError:
        return (
            jsonify(
                {
                    "success": False,
                    "message": "sshpass command not found (please install sshpass)",
                }
            ),
            500,
        )
    except Exception as e:
        return jsonify({"success": False, "message": f"Error: {str(e)}"}), 500


CATALOG_URL = "https://downloads.dell.com/catalog/Catalog.xml.gz"
CATALOG_BASE_URL = "https://downloads.dell.com"
FIRMWARE_IMAGE_DIR = Path("/var/lib/dracs/web/firmware")
BIOS_IMAGE_DIR = Path("/var/lib/dracs/web/bios")
ISO_IMAGE_DIR = Path("/var/lib/dracs/web/iso")

_ARCHIVE_BASE = os.environ.get("DRACS_ARCHIVE_DIR", "./archive")
FIRMWARE_ARCHIVE_DIR = Path(_ARCHIVE_BASE) / "firmware"
BIOS_ARCHIVE_DIR = Path(_ARCHIVE_BASE) / "bios"


def _parse_catalog_datetime(dt_str: str) -> datetime:
    dt_str = dt_str.strip()
    if dt_str.endswith("Z"):
        dt_str = dt_str[:-1] + "+00:00"
    sign_pos = max(dt_str.rfind("+"), dt_str.rfind("-", 11))
    if sign_pos > 0 and ":" in dt_str[sign_pos:]:
        colon_in_tz = dt_str.rfind(":")
        if colon_in_tz > sign_pos:
            naive_part = dt_str[:sign_pos]
            tz_part = dt_str[sign_pos:].replace(":", "")
            dt_str = naive_part + tz_part
    try:
        return datetime.strptime(dt_str, "%Y-%m-%dT%H:%M:%S%z")
    except ValueError:
        return datetime.strptime(dt_str, "%Y-%m-%dT%H:%M:%S")


def _find_latest_idrac_firmware(xml_bytes: bytes, model: str) -> dict | None:
    text = xml_bytes.decode("utf-16")
    root = defused_ET.fromstring(text)
    target = model.lower()
    best = None
    best_dt = None

    for comp in root.iter("SoftwareComponent"):
        comp_type_node = comp.find("ComponentType")
        if comp_type_node is None or comp_type_node.get("value", "") != "FRMW":
            continue

        cat_display = comp.find(".//Category/Display")
        if cat_display is None or not cat_display.text:
            continue
        if cat_display.text.strip() != "iDRAC with Lifecycle Controller":
            continue

        models_in_comp = []
        for display in comp.findall(".//SupportedSystems/Brand/Model/Display"):
            if display.text:
                models_in_comp.append(display.text.strip())
        if not any(m.lower() == target for m in models_in_comp):
            continue

        path = comp.get("path", "")
        dt_str = comp.get("dateTime", "")
        if not path or not dt_str:
            continue

        dt = _parse_catalog_datetime(dt_str)
        if best_dt is None or dt > best_dt:
            best_dt = dt
            best = {
                "version": comp.get("vendorVersion", ""),
                "path": path,
                "url": f"{CATALOG_BASE_URL}/{path}",
                "hash_sha256": comp.get("hash", ""),
            }

    return best


def _sse_event(event_type: str, message: str, **kwargs) -> str:
    payload = {"type": event_type, "message": message}
    payload.update(kwargs)
    return f"data: {json.dumps(payload)}\n\n"


def _extract_firmware_version(extract_dir: str, fallback_version: str) -> str:
    pkg_xml_path = os.path.join(extract_dir, "package.xml")
    if not os.path.exists(pkg_xml_path):
        return fallback_version
    pkg_tree = defused_ET.parse(pkg_xml_path)
    pkg_root = pkg_tree.getroot()
    sc = pkg_root.find(".//SoftwareComponent")
    if sc is None and pkg_root.tag == "SoftwareComponent":
        sc = pkg_root
    if sc is not None:
        vv = sc.get("vendorVersion", "")
        if vv:
            return vv
    return fallback_version


def _find_d9_file(extract_dir: str) -> str | None:
    payload_dir = os.path.join(extract_dir, "payload")
    if os.path.isdir(payload_dir):
        for fname in os.listdir(payload_dir):
            if fname.lower().endswith(".d9"):
                return os.path.join(payload_dir, fname)
    for root_dir, _dirs, files in os.walk(extract_dir):
        for fname in files:
            if fname.lower().endswith(".d9"):
                return os.path.join(root_dir, fname)
    return None


def _wait_for_tsr_export(cmd: list, poll_interval: int, max_wait: int) -> bool:
    elapsed = 0
    while elapsed < max_wait:
        time.sleep(poll_interval)
        elapsed += poll_interval
        try:
            result = subprocess.run(  # nosec # nosemgrep
                cmd, capture_output=True, text=True, timeout=30  # nosemgrep
            )
            if result.returncode != 0:
                continue
            jobs = parse_job_queue(result.stdout)
            for job in jobs:
                if job.get("job_name") != "SupportAssist Collection":
                    continue
                if (
                    job.get("status") == "Completed"
                    and "transmission operation is completed successfully"
                    in job.get("message", "").lower()
                ):
                    return True
        except Exception as exc:
            print(f"TSR export poll error: {exc}", file=sys.stderr)
            continue
    return False


def _stage_tsr_files(zip_path: str, hostname: str, service_tag: str) -> None:
    zip_fname = os.path.basename(zip_path)
    ts_part = zip_fname.replace("TSR", "").split("_")[0]

    host_dir = TSR_IMAGE_DIR / hostname
    host_dir.mkdir(parents=True, exist_ok=True)

    shutil.copy2(zip_path, host_dir / zip_fname)

    ts_dir = host_dir / ts_part
    _extract_tsr(str(host_dir / zip_fname), str(ts_dir))

    latest_link = host_dir / "latest"
    if latest_link.is_symlink() or latest_link.exists():
        latest_link.unlink()
    latest_link.symlink_to(ts_part)

    index_path = ts_dir / "index.html"
    index_path.write_text(
        '<html><head><meta http-equiv="refresh" '
        'content="0;url=tsr/viewer.html"></head></html>\n'
    )

    _generate_tsr_index(hostname)


def _generate_tsr_index(hostname: str) -> None:
    host_dir = TSR_IMAGE_DIR / hostname
    if not host_dir.is_dir():
        host_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(host_dir, 0o755)  # nosec # nosemgrep

    entries = []
    for zip_file in host_dir.glob("TSR*.zip"):
        fname = zip_file.name
        ts_part = fname.replace("TSR", "").split("_")[0]
        try:
            dt = datetime.strptime(ts_part, "%Y%m%d%H%M%S")
            entries.append((dt, ts_part, fname))
        except ValueError:
            continue

    entries.sort(key=lambda e: e[0], reverse=True)

    btn = (
        'style="display:inline-block;padding:6px 18px;'
        "background:#0d6efd;color:#fff;border-radius:4px;"
        'text-decoration:none;font-size:14px"'
    )
    row_tpl = Markup(
        '<tr style="background:{}">'
        '<td style="padding:10px 16px">{}</td>'
        '<td style="padding:10px 16px;text-align:center">'
        '<a href="{}" ' + btn + ">View</a>"
        "</td>"
        '<td style="padding:10px 16px;text-align:center">'
        '<a href="{}" download ' + btn + ">Download</a>"
        "</td></tr>"
    )

    rows = []
    for i, (dt, ts_part, fname) in enumerate(entries):
        bg = "#ffffff" if i % 2 == 0 else "#f5f5f5"
        date_str = dt.strftime("%Y/%m/%d %H:%M:%S")
        view_path = ts_part + "/"
        rows.append(row_tpl.format(bg, date_str, view_path, fname))

    table_rows = (
        Markup("\n").join(rows)
        if rows
        else Markup(
            '<tr><td colspan="3" style="padding:20px;text-align:center;'
            'color:#666">No TSR collections found.</td></tr>'
        )
    )

    page_tpl = Markup(
        "<!DOCTYPE html>\n"
        "<html>\n<head>\n"
        '<meta charset="utf-8">\n'
        "<title>TSR Collection for {}</title>\n"
        "<style>\n"
        "body {{ font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;\n"
        "       margin: 40px auto; max-width: 960px; color: #333; }}\n"
        "h1 {{ font-size: 24px; font-weight: 600;"
        " margin-bottom: 24px; word-break: keep-all; }}\n"
        "table {{ width: 100%; border-collapse: collapse; }}\n"
        "th {{ text-align: left; padding: 10px 16px;"
        " border-bottom: 2px solid #dee2e6;\n"
        "      font-size: 14px; color: #555; }}\n"
        "</style>\n</head>\n<body>\n"
        "<h1>TSR Collection for {}</h1>\n"
        "<table>\n"
        "<tr><th>Date Collected</th><th></th><th></th></tr>\n"
        "{}\n"
        "</table>\n</body>\n</html>\n"
    )

    (host_dir / "index.html").write_text(
        page_tpl.format(hostname, hostname, table_rows)
    )


@app.route("/api/latest-firmware", methods=["POST"])
def api_latest_firmware():
    """Stream latest firmware check and download progress via SSE."""
    user, err = _require_auth(required_role="admin")
    if err:
        return err

    _audit_user = user
    _audit_source = _client_ip()

    data = request.get_json()
    if not data:
        return jsonify({"success": False, "message": "Invalid request"}), 400

    model = data.get("model", "").strip()
    hostname = data.get("hostname", "").strip()
    current_version = data.get("current_version", "").strip()

    if not model or not hostname:
        return (
            jsonify({"success": False, "message": "Model and hostname required"}),
            400,
        )

    def generate():
        tmp_dir = None
        try:
            yield _sse_event("status", "Downloading Dell Catalog ....")

            req = urllib.request.Request(
                CATALOG_URL,
                headers={"User-Agent": "dracs-webapp/1.0"},
            )
            with urllib.request.urlopen(req, timeout=120) as resp:  # nosec
                compressed = resp.read()
            xml_bytes = gzip.decompress(compressed)

            yield _sse_event("append", "done.")

            yield _sse_event("status", "Checking for the latest available version...")

            result = _find_latest_idrac_firmware(xml_bytes, model)
            if not result:
                yield _sse_event(
                    "error",
                    f"No iDRAC firmware found in Dell catalog for model {model}.",
                )
                return

            version = result["version"]
            download_url = result["url"]
            expected_sha256 = result.get("hash_sha256", "")

            yield _sse_event("append", "done.")

            yield _sse_event("status", f"Downloading {download_url}...")

            tmp_dir = tempfile.mkdtemp(prefix="dracs_fw_")
            exe_filename = download_url.rsplit("/", 1)[-1]
            exe_path = os.path.join(tmp_dir, exe_filename)

            dl_req = urllib.request.Request(
                download_url,
                headers={"User-Agent": "dracs-webapp/1.0"},
            )
            with urllib.request.urlopen(dl_req, timeout=300) as resp:  # nosec
                with open(exe_path, "wb") as f:
                    shutil.copyfileobj(resp, f)

            yield _sse_event("append", "done.")

            if expected_sha256:
                yield _sse_event("status", "Verifying SHA256 ....")
                with open(exe_path, "rb") as f:
                    calculated = hashlib.sha256(f.read()).hexdigest()
                if calculated != expected_sha256:
                    yield _sse_event("error", "Verifying SHA256 ... FAIL!")
                    return
                yield _sse_event("append", "done.")

            FIRMWARE_ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
            archive_path = FIRMWARE_ARCHIVE_DIR / exe_filename
            if not archive_path.exists():
                shutil.copy2(exe_path, archive_path)
            if expected_sha256:
                sha_path = FIRMWARE_ARCHIVE_DIR / f"{exe_filename}.sha256"
                sha_path.write_text(f"{expected_sha256}  {exe_filename}\n")

            yield _sse_event("status", "Extracting firmware package...")

            extract_dir = os.path.join(tmp_dir, "extracted")
            with zipfile.ZipFile(exe_path, "r") as zf:
                zf.extractall(extract_dir)

            yield _sse_event("append", "done.")

            pkg_version = _extract_firmware_version(extract_dir, version)

            yield _sse_event(
                "status",
                f"Latest Firmware version for {model} found: {pkg_version}",
            )

            d9_file = _find_d9_file(extract_dir)
            if not d9_file:
                yield _sse_event("error", "No .d9 firmware image found in package.")
                return

            dest_filename = f"{model}-{pkg_version}.d9"
            dest_path = FIRMWARE_IMAGE_DIR / dest_filename
            file_exists = dest_path.exists()

            if file_exists:
                yield _sse_event(
                    "status",
                    f"{dest_path} already exists!",
                )
            else:
                FIRMWARE_IMAGE_DIR.mkdir(parents=True, exist_ok=True)
                shutil.copy2(d9_file, dest_path)
                os.chmod(dest_path, 0o444)
                yield _sse_event(
                    "status",
                    f"Firmware image staged at {dest_path}",
                )

            already_current = current_version == pkg_version

            if already_current:
                yield _sse_event(
                    "status",
                    f"{hostname} already running firmware version {pkg_version}.",
                )

            audit_log(
                "firmware_download",
                target=hostname,
                user=_audit_user,
                source=_audit_source,
                details=f"model={model},version={pkg_version}",
            )

            yield _sse_event(
                "complete",
                "",
                version=pkg_version,
                file_exists=file_exists,
                already_current=already_current,
                hostname=hostname,
                model=model,
            )

        except Exception as e:
            yield _sse_event("error", f"Error: {str(e)}")

        finally:
            if tmp_dir and os.path.isdir(tmp_dir):
                shutil.rmtree(tmp_dir, ignore_errors=True)

    return Response(
        generate(),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


def _find_latest_bios(xml_bytes: bytes, model: str) -> dict | None:
    text = xml_bytes.decode("utf-16")
    root = defused_ET.fromstring(text)
    target = model.lower()
    best = None
    best_dt = None

    for comp in root.iter("SoftwareComponent"):
        comp_type_node = comp.find("ComponentType")
        if comp_type_node is None or comp_type_node.get("value", "") != "BIOS":
            continue

        models_in_comp = []
        for display in comp.findall(".//SupportedSystems/Brand/Model/Display"):
            if display.text:
                models_in_comp.append(display.text.strip())
        if not any(m.lower() == target for m in models_in_comp):
            continue

        path = comp.get("path", "")
        dt_str = comp.get("dateTime", "")
        if not path or not dt_str:
            continue

        dt = _parse_catalog_datetime(dt_str)
        if best_dt is None or dt > best_dt:
            best_dt = dt
            best = {
                "version": comp.get("vendorVersion", ""),
                "path": path,
                "url": f"{CATALOG_BASE_URL}/{path}",
                "hash_sha256": comp.get("hash", ""),
            }

    return best


def _update_bios_filename_ini(model: str, version: str, filename: str) -> None:
    config_file = Path("BIOS-filename.ini")
    config = configparser.ConfigParser()
    if config_file.exists():
        config.read(config_file)
    if model not in config:
        config[model] = {}
    config[model][version] = filename
    with open(config_file, "w") as f:
        config.write(f)


@app.route("/api/latest-bios", methods=["POST"])
def api_latest_bios():
    """Stream latest BIOS check and download progress via SSE."""
    user, err = _require_auth(required_role="admin")
    if err:
        return err

    _audit_user = user
    _audit_source = _client_ip()

    data = request.get_json()
    if not data:
        return jsonify({"success": False, "message": "Invalid request"}), 400

    model = data.get("model", "").strip()
    hostname = data.get("hostname", "").strip()
    current_version = data.get("current_version", "").strip()

    if not model or not hostname:
        return (
            jsonify({"success": False, "message": "Model and hostname required"}),
            400,
        )

    def generate():
        tmp_dir = None
        try:
            yield _sse_event("status", "Downloading Dell Catalog ....")

            req = urllib.request.Request(
                CATALOG_URL,
                headers={"User-Agent": "dracs-webapp/1.0"},
            )
            with urllib.request.urlopen(req, timeout=120) as resp:  # nosec
                compressed = resp.read()
            xml_bytes = gzip.decompress(compressed)

            yield _sse_event("append", "done.")

            yield _sse_event("status", "Checking for the latest available version...")

            result = _find_latest_bios(xml_bytes, model)
            if not result:
                yield _sse_event(
                    "error",
                    f"No BIOS found in Dell catalog for model {model}.",
                )
                return

            version = result["version"]
            download_url = result["url"]
            expected_sha256 = result.get("hash_sha256", "")

            yield _sse_event("append", "done.")

            yield _sse_event("status", f"Downloading {download_url}...")

            tmp_dir = tempfile.mkdtemp(prefix="dracs_bios_")
            exe_filename = download_url.rsplit("/", 1)[-1]
            exe_path = os.path.join(tmp_dir, exe_filename)

            dl_req = urllib.request.Request(
                download_url,
                headers={"User-Agent": "dracs-webapp/1.0"},
            )
            with urllib.request.urlopen(dl_req, timeout=300) as resp:  # nosec
                with open(exe_path, "wb") as f:
                    shutil.copyfileobj(resp, f)

            yield _sse_event("append", "done.")

            if expected_sha256:
                yield _sse_event("status", "Verifying SHA256 ....")
                with open(exe_path, "rb") as f:
                    calculated = hashlib.sha256(f.read()).hexdigest()
                if calculated != expected_sha256:
                    yield _sse_event("error", "Verifying SHA256 ... FAIL!")
                    return
                yield _sse_event("append", "done.")

            BIOS_ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
            archive_path = BIOS_ARCHIVE_DIR / exe_filename
            if not archive_path.exists():
                shutil.copy2(exe_path, archive_path)
            if expected_sha256:
                sha_path = BIOS_ARCHIVE_DIR / f"{exe_filename}.sha256"
                sha_path.write_text(f"{expected_sha256}  {exe_filename}\n")

            yield _sse_event(
                "status",
                f"Latest BIOS version for {model} found: {version}",
            )

            model_dir = BIOS_IMAGE_DIR / model
            model_dir.mkdir(parents=True, exist_ok=True)
            os.chmod(model_dir, 0o755)  # nosec # nosemgrep

            dest_path = model_dir / exe_filename
            file_exists = dest_path.exists()

            if file_exists:
                yield _sse_event(
                    "status",
                    f"{dest_path} already exists!",
                )
            else:
                try:
                    os.link(archive_path, dest_path)
                except OSError:
                    shutil.copy2(archive_path, dest_path)
                os.chmod(dest_path, 0o644)
                yield _sse_event(
                    "status",
                    f"BIOS image staged at {dest_path}",
                )

            try:
                _update_bios_filename_ini(model, version, exe_filename)
            except Exception as exc:
                print(
                    f"Warning: failed to update BIOS-filename.ini: {exc}",
                    file=sys.stderr,
                )

            already_current = current_version == version

            if already_current:
                yield _sse_event(
                    "status",
                    f"{hostname} already running BIOS version {version}.",
                )

            audit_log(
                "bios_download",
                target=hostname,
                user=_audit_user,
                source=_audit_source,
                details=f"model={model},version={version}",
            )

            yield _sse_event(
                "complete",
                "",
                version=version,
                file_exists=file_exists,
                already_current=already_current,
                hostname=hostname,
                model=model,
            )

        except Exception as e:
            yield _sse_event("error", f"Error: {str(e)}")

        finally:
            if tmp_dir and os.path.isdir(tmp_dir):
                shutil.rmtree(tmp_dir, ignore_errors=True)

    return Response(
        generate(),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


TSR_IMAGE_DIR = Path("/var/lib/dracs/web/tsr")
TFTPBOOT_DIR = Path("/var/lib/tftpboot")


def _build_ssh_racadm_cmd(
    hostname: str, *racadm_args: str, site: str | None = None
) -> list:
    idrac_fqdn = build_idrac_hostname(hostname)
    username, password = get_idrac_credentials(hostname, site=site)
    return [
        "sshpass",
        "-p",
        password,
        "ssh",
        "-o",
        "StrictHostKeyChecking=no",
        "-o",
        "UserKnownHostsFile=/dev/null",
        "-o",
        "ConnectTimeout=10",
        f"{username}@{idrac_fqdn}",
        "racadm",
        *racadm_args,
    ]


def _get_tsr_job_status(hostname: str) -> dict:
    cmd = _build_ssh_racadm_cmd(hostname, "jobqueue", "view")
    result = subprocess.run(  # nosec # nosemgrep
        cmd, capture_output=True, text=True, timeout=30  # nosemgrep
    )
    if result.returncode != 0:
        return {"state": "error", "message": "Failed to query job queue"}

    jobs = parse_job_queue(result.stdout)

    sa_jobs = [j for j in jobs if j.get("job_name") == "SupportAssist Collection"]

    for job in sa_jobs:
        if job.get("status") == "Running":
            return {
                "state": "running",
                "percent_complete": job.get("percent_complete", "0"),
            }

    for job in sa_jobs:
        if (
            job.get("status") == "Completed"
            and "completed successfully" in job.get("message", "").lower()
        ):
            return {"state": "completed"}

    return {"state": "none"}


def _find_tsr_zip(service_tag: str, approx_time: datetime, fudge_seconds: int = 300):
    pattern = str(TFTPBOOT_DIR / f"TSR*_{service_tag}.zip")
    candidates = glob.glob(pattern)
    if not candidates:
        return None

    best = None
    best_diff = None
    for path in candidates:
        fname = os.path.basename(path)
        ts_part = fname.replace("TSR", "").split("_")[0]
        try:
            file_dt = datetime.strptime(ts_part, "%Y%m%d%H%M%S")
            diff = abs((file_dt - approx_time.replace(tzinfo=None)).total_seconds())
            if diff <= fudge_seconds and (best_diff is None or diff < best_diff):
                best = path
                best_diff = diff
        except ValueError:
            continue

    if best is None and candidates:
        candidates.sort(key=os.path.getmtime, reverse=True)
        best = candidates[0]

    return best


def _extract_tsr(zip_path: str, dest_dir: str) -> None:
    os.makedirs(dest_dir, exist_ok=True)
    with zipfile.ZipFile(zip_path, "r") as zf:
        zf.extractall(dest_dir)

    for fname in os.listdir(dest_dir):
        if fname.lower().endswith(".pl.zip"):
            pl_zip_path = os.path.join(dest_dir, fname)
            with zipfile.ZipFile(pl_zip_path, "r") as zf:
                zf.extractall(dest_dir)
            break

    for root_dir, dirs, files in os.walk(dest_dir):
        for d in dirs:
            dp = os.path.join(root_dir, d)
            st = os.stat(dp)
            os.chmod(dp, st.st_mode | 0o055)
        for f in files:
            fp = os.path.join(root_dir, f)
            st = os.stat(fp)
            os.chmod(fp, st.st_mode | 0o044)


def _get_sa_jobs(hostname: str) -> list | None:
    cmd = _build_ssh_racadm_cmd(hostname, "jobqueue", "view")
    result = subprocess.run(  # nosec # nosemgrep
        cmd, capture_output=True, text=True, timeout=30  # nosemgrep
    )
    if result.returncode != 0:
        return None
    return [
        j
        for j in parse_job_queue(result.stdout)
        if j.get("job_name") == "SupportAssist Collection"
    ]


@app.route("/api/tsr-status", methods=["POST"])
def api_tsr_status():
    """Check TSR job status for a host."""
    try:
        _, err = _require_auth()
        if err:
            return err

        data = request.get_json()
        if not data:
            return jsonify({"success": False, "message": "Invalid request"}), 400

        hostname = data.get("hostname", "").strip()
        if not hostname:
            return jsonify({"success": False, "message": "Hostname required"}), 400
        if not validate_hostname(hostname):
            return jsonify({"success": False, "message": "Invalid hostname"}), 400

        from dracs.jobqueue import get_latest_job_for_host

        job = get_latest_job_for_host(hostname, "tsr")
        if job and job["status"] == "pending":
            status = {"state": "pending"}
        elif job and job["status"] == "running":
            progress = job.get("result", "0%")
            pct = progress.replace("%", "") if progress and "%" in progress else "0"
            status = {"state": "running", "percent_complete": pct}
        else:
            status = _get_tsr_job_status(hostname)

        fqdn = socket.getfqdn()
        status["tsr_url"] = urlunparse(
            ("http", fqdn, f"/tsr/{url_quote(hostname, safe='')}/", "", "", "")
        )
        return jsonify({"success": True, **status})

    except subprocess.TimeoutExpired:
        return jsonify({"success": False, "message": "Connection timeout"}), 500
    except Exception as e:
        return jsonify({"success": False, "message": f"Error: {str(e)}"}), 500


@app.route("/api/tsr-ensure-index", methods=["POST"])
def api_tsr_ensure_index():
    """Regenerate the TSR index page for a host."""
    try:
        data = request.get_json()
        if not data:
            return jsonify({"success": False, "message": "Invalid request"}), 400

        hostname = data.get("hostname", "").strip()
        if not hostname:
            return jsonify({"success": False, "message": "Hostname required"}), 400
        if not validate_hostname(hostname):
            return jsonify({"success": False, "message": "Invalid hostname"}), 400

        _generate_tsr_index(hostname)
        return jsonify({"success": True})

    except Exception as e:
        return jsonify({"success": False, "message": f"Error: {str(e)}"}), 500


@app.route("/api/tsr-list/<hostname>")
def api_tsr_list(hostname):
    """List TSR collections for a host (unauthenticated)."""
    if not validate_hostname(hostname):
        return jsonify({"success": False, "message": "Invalid hostname"}), 400

    with get_session() as db_session:
        system = db_session.query(System).filter(System.name == hostname).first()
    if system is None:
        return jsonify({"success": False, "message": "Host not found"}), 404

    host_dir = TSR_IMAGE_DIR / hostname
    if not host_dir.is_dir():
        return jsonify({"success": True, "entries": []})

    entries = []
    for zip_file in host_dir.glob("TSR*.zip"):
        fname = zip_file.name
        ts_part = fname.replace("TSR", "").split("_")[0]
        try:
            dt = datetime.strptime(ts_part, "%Y%m%d%H%M%S")
            entries.append(
                {
                    "date": dt.strftime("%Y/%m/%d %H:%M:%S"),
                    "view_path": ts_part + "/",
                    "zip_file": fname,
                }
            )
        except ValueError:
            continue

    entries.sort(key=lambda e: e["date"], reverse=True)
    return jsonify({"success": True, "entries": entries})


@app.route("/api/tsr-collect", methods=["POST"])
def api_tsr_collect():
    """Initiate a TSR collection on a host via the job queue."""
    try:
        user, err = _require_auth()
        if err:
            return err

        data = request.get_json()
        if not data:
            return jsonify({"success": False, "message": "Invalid request"}), 400

        hostname = data.get("hostname", "").strip()
        service_tag = data.get("service_tag", "").strip()
        if not hostname:
            return jsonify({"success": False, "message": "Hostname required"}), 400
        if not validate_hostname(hostname):
            return jsonify({"success": False, "message": "Invalid hostname"}), 400
        if not service_tag:
            return jsonify({"success": False, "message": "Service tag required"}), 400

        from dracs.jobqueue import enqueue_job, get_latest_job_for_host

        existing = get_latest_job_for_host(hostname, "tsr")
        if existing and existing["status"] in ("pending", "running"):
            return jsonify(
                {
                    "success": True,
                    "message": f"TSR already in progress for {hostname}",
                    "job_id": existing["id"],
                    "existing": True,
                }
            )

        job_id = enqueue_job("tsr", hostname)

        audit_log(
            "tsr_collect",
            target=hostname,
            user=user,
            source=_client_ip(),
        )

        return jsonify(
            {
                "success": True,
                "message": f"TSR initiated for {hostname}",
                "job_id": job_id,
            }
        )

    except Exception as e:
        return jsonify({"success": False, "message": f"Error: {str(e)}"}), 500


@app.route("/api/jobs")
def api_jobs():
    """List active jobs (authenticated)."""
    try:
        _, err = _require_auth(required_role="admin")
        if err:
            return err

        from dracs.jobqueue import get_active_jobs

        include_all = request.args.get("all", "false").lower() in (
            "true",
            "1",
            "yes",
        )
        status_filter = request.args.get("status")
        jobs = get_active_jobs(
            include_completed=include_all or bool(status_filter),
            status_filter=status_filter,
            limit=200,
        )
        return jsonify({"success": True, "jobs": jobs})

    except Exception as e:
        return jsonify({"success": False, "message": f"Error: {str(e)}"}), 500


@app.route("/api/users", methods=["GET"])
def api_users_list():
    """List all users."""
    try:
        _, err = _require_auth(required_role="admin")
        if err:
            return err
        return jsonify({"success": True, "users": list_users()})
    except Exception as e:
        return jsonify({"success": False, "message": f"Error: {str(e)}"}), 500


@app.route("/api/users", methods=["POST"])
def api_users_create():
    """Create a new user."""
    try:
        user, err = _require_auth(required_role="admin")
        if err:
            return err

        data = request.get_json()
        if not data:
            return jsonify({"success": False, "message": "Invalid request"}), 400

        username = data.get("username", "").strip()
        password = data.get("password", "")
        raw_role = data.get("role")
        role = (
            None
            if (raw_role is None or str(raw_role).strip().lower() == "none")
            else str(raw_role).strip()
        )

        from dracs.exceptions import ValidationError

        try:
            create_user(username, password, role, created_by=user)
        except ValidationError as ve:
            return jsonify({"success": False, "message": str(ve)}), 400

        site_role = data.get("site_role")
        site_roles = data.get("site_roles")
        if site_role is not None:
            from dracs.db import get_site_by_name
            from dracs.users import set_user_site_role

            sr_name = site_role.get("site_name")
            sr_role = site_role.get("role")
            sr_site = get_site_by_name(sr_name) if sr_name else None
            if sr_site and sr_role:
                set_user_site_role(username, sr_site["id"], str(sr_role).strip())
        elif site_roles is None and role is not None:
            from dracs.db import get_default_site_id
            from dracs.users import set_user_site_role

            try:
                set_user_site_role(username, get_default_site_id(), role)
            except RuntimeError:
                pass
        elif site_roles:
            from dracs.users import set_user_site_role

            for sr in site_roles:
                try:
                    set_user_site_role(username, sr["site_id"], sr["role"])
                except (ValidationError, KeyError):
                    pass

        audit_log(
            "user_create",
            target=username,
            user=user,
            source=_client_ip(),
            details=f"role={role}",
        )

        return jsonify(
            {
                "success": True,
                "message": f"User '{username}' created with role '{role}'.",
            }
        )

    except Exception as e:
        return jsonify({"success": False, "message": f"Error: {str(e)}"}), 500


@app.route("/api/users/<username>", methods=["DELETE"])
def api_users_delete(username):
    """Delete a user."""
    try:
        user, err = _require_auth(required_role="admin")
        if err:
            return err

        if username == user:
            return (
                jsonify({"success": False, "message": "Cannot delete yourself"}),
                400,
            )

        if not session.get("is_superadmin", False):
            from dracs.db import list_sites
            from dracs.users import get_user_site_roles

            all_sites = list_sites()
            admin_sites = get_user_site_roles(user)
            admin_site_ids = {r["site_id"] for r in admin_sites if r["role"] == "admin"}
            if len(admin_site_ids) < len(all_sites):
                return (
                    jsonify({"success": False, "message": "Insufficient permissions"}),
                    403,
                )

        from dracs.exceptions import ValidationError

        try:
            deleted = delete_user(username)
        except ValidationError as ve:
            return jsonify({"success": False, "message": str(ve)}), 400

        if not deleted:
            return jsonify({"success": False, "message": "User not found"}), 404

        audit_log(
            "user_delete",
            target=username,
            user=user,
            source=_client_ip(),
        )

        return jsonify({"success": True, "message": f"User '{username}' deleted."})

    except Exception as e:
        return jsonify({"success": False, "message": f"Error: {str(e)}"}), 500


@app.route("/api/users/<username>", methods=["PATCH"])
def api_users_update(username):
    """Update a user's password or role."""
    try:
        user, err = _require_auth(required_role="admin")
        if err:
            return err

        data = request.get_json()
        if not data:
            return jsonify({"success": False, "message": "Invalid request"}), 400

        from dracs.exceptions import ValidationError

        new_password = data.get("password")
        _ROLE_SENTINEL = object()
        raw_new_role = data.get("role", _ROLE_SENTINEL)
        role_provided = raw_new_role is not _ROLE_SENTINEL
        if role_provided:
            new_role = (
                None
                if (raw_new_role is None or str(raw_new_role).strip().lower() == "none")
                else str(raw_new_role).strip()
            )
        else:
            new_role = _ROLE_SENTINEL
        changes = []

        try:
            if new_password:
                if not update_user_password(username, new_password):
                    return jsonify({"success": False, "message": "User not found"}), 404
                changes.append("password")

            if role_provided:
                if not update_user_role(username, new_role):
                    return jsonify({"success": False, "message": "User not found"}), 404
                changes.append(f"role={new_role}")

            site_role = data.get("site_role")
            if site_role is not None:
                from dracs.db import get_site_by_name
                from dracs.users import remove_user_site_role, set_user_site_role

                sr_name = site_role.get("site_name")
                sr_role = site_role.get("role")
                sr_site = get_site_by_name(sr_name) if sr_name else None
                if not sr_site:
                    return (
                        jsonify(
                            {"success": False, "message": f"Site '{sr_name}' not found"}
                        ),
                        404,
                    )
                if sr_role is None or str(sr_role).strip().lower() == "none":
                    remove_user_site_role(username, sr_site["id"])
                else:
                    set_user_site_role(username, sr_site["id"], str(sr_role).strip())
                changes.append(f"site_role({sr_name})={sr_role}")

            site_roles = data.get("site_roles")
            if site_roles is not None:
                from dracs.users import (
                    get_user_site_roles,
                    remove_user_site_role,
                    set_user_site_role,
                )

                existing = get_user_site_roles(username)
                existing_site_ids = {r["site_id"] for r in existing}
                new_site_ids = {sr["site_id"] for sr in site_roles}

                for sid in existing_site_ids - new_site_ids:
                    remove_user_site_role(username, sid)

                for sr in site_roles:
                    set_user_site_role(username, sr["site_id"], sr["role"])

                changes.append("site_roles")
        except ValidationError as ve:
            return jsonify({"success": False, "message": str(ve)}), 400

        if not changes:
            return (
                jsonify({"success": False, "message": "No changes provided"}),
                400,
            )

        audit_log(
            "user_update",
            target=username,
            user=user,
            source=_client_ip(),
            details=",".join(changes),
        )

        return jsonify(
            {
                "success": True,
                "message": f"User '{username}' updated: {', '.join(changes)}.",
            }
        )

    except Exception as e:
        return jsonify({"success": False, "message": f"Error: {str(e)}"}), 500


@app.route("/api/discover", methods=["POST"])
def api_discover():
    """Bulk discover and add hosts."""
    try:
        site_id, site_name = _get_requested_site()
        user, err = _require_auth(required_role="admin", site_id=site_id)
        if err:
            return err

        data = request.get_json()
        if not data or not data.get("hostnames"):
            return jsonify({"success": False, "message": "Hostnames required"}), 400

        hostnames = [h.strip() for h in data["hostnames"] if h.strip()]
        if not hostnames:
            return jsonify({"success": False, "message": "No valid hostnames"}), 400

        for h in hostnames:
            if not validate_hostname(h):
                return (
                    jsonify({"success": False, "message": f"Invalid hostname: {h}"}),
                    400,
                )

        from dracs.db import get_site_allowed_domains
        from dracs.sites import is_domain_allowed
        from dracs.jobqueue import enqueue_job
        from dracs.snmp import check_idrac_dns

        allowed = get_site_allowed_domains(site_id)
        for h in hostnames:
            if not is_domain_allowed(h, allowed):
                return (
                    jsonify(
                        {
                            "success": False,
                            "message": f"Cannot add host '{h}'. Domain not allowed.",
                        }
                    ),
                    400,
                )

        dns_failed = []
        queued_hosts = []
        for h in hostnames:
            idrac_fqdn, dns_err = check_idrac_dns(h)
            if dns_err:
                dns_failed.append(
                    {"hostname": h, "idrac_fqdn": idrac_fqdn, "error": dns_err}
                )
            else:
                queued_hosts.append(h)

        if not queued_hosts:
            return (
                jsonify(
                    {
                        "success": False,
                        "message": "All hosts failed DNS check.",
                        "dns_failed": dns_failed,
                    }
                ),
                400,
            )

        for hostname in queued_hosts:
            enqueue_job(
                "discover",
                hostname,
                metadata={"auto_add": True, "site_id": site_id},
                site_id=site_id,
            )

        audit_log(
            "discover",
            user=user,
            source=_client_ip(),
            details=f"hosts={len(queued_hosts)},dns_failed={len(dns_failed)},site={site_name}",
        )

        msg = f"Discovery queued for {len(queued_hosts)} host(s)."
        if dns_failed:
            msg += f" {len(dns_failed)} host(s) failed DNS check."
        return jsonify(
            {
                "success": True,
                "message": msg,
                "queued": len(queued_hosts),
                "dns_failed": dns_failed,
            }
        )
    except Exception as e:
        return jsonify({"success": False, "message": f"Error: {str(e)}"}), 500


@app.route("/api/delete-systems", methods=["POST"])
def api_delete_systems():
    """Delete one or more systems from the database."""
    try:
        site_id, _ = _get_requested_site()
        user, err = _require_auth(required_role="admin", site_id=site_id)
        if err:
            return err

        data = request.get_json()
        if not data or not data.get("hostnames"):
            return jsonify({"success": False, "message": "Hostnames required"}), 400

        hostnames = data["hostnames"]
        deleted = 0
        with get_session() as sess:
            for hostname in hostnames:
                system = sess.query(System).filter(System.name == hostname).first()
                if system:
                    sess.delete(system)
                    deleted += 1
            sess.commit()

        audit_log(
            "delete_systems",
            user=user,
            source=_client_ip(),
            details=f"deleted={deleted},requested={len(hostnames)}",
        )

        return jsonify(
            {
                "success": True,
                "message": f"Deleted {deleted} system(s).",
                "deleted": deleted,
            }
        )
    except Exception as e:
        return jsonify({"success": False, "message": f"Error: {str(e)}"}), 500


@app.route("/sites")
def sites_page():
    """Full-page site management (superadmin only)."""
    is_authenticated = session.get("authenticated", False)
    if not is_authenticated:
        return redirect(url_for("index"))
    if not session.get("is_superadmin", False):
        return redirect(url_for("index"))

    from_site = request.args.get("site", "")

    return render_template(
        "sites.html",
        username=session.get("username", ""),
        user_role=session.get("role", ""),
        is_superadmin=True,
        from_site=from_site,
    )


@app.route("/users")
def users_page():
    """Full-page user management."""
    from dracs.db import list_sites

    is_authenticated = session.get("authenticated", False)
    if not is_authenticated:
        return redirect(url_for("index"))

    username = session.get("username", "")
    user_role = session.get("role", "")
    is_superadmin = session.get("is_superadmin", False)

    if user_role != "admin" and not is_superadmin:
        return redirect(url_for("index"))

    all_sites_full = list_sites()
    can_delete = is_superadmin

    if not is_superadmin:
        from dracs.users import get_user_site_roles

        admin_sites = get_user_site_roles(username)
        admin_site_ids = {r["site_id"] for r in admin_sites if r["role"] == "admin"}
        all_sites = [s for s in all_sites_full if s["id"] in admin_site_ids]
        can_delete = len(all_sites_full) > 0 and len(admin_site_ids) >= len(
            all_sites_full
        )
    else:
        all_sites = all_sites_full

    from_site = request.args.get("site", "")

    return render_template(
        "users.html",
        username=username,
        user_role=user_role,
        is_superadmin=is_superadmin,
        can_delete=can_delete,
        all_sites=all_sites,
        from_site=from_site,
    )


@app.route("/api/users/<username>/site-roles")
def api_user_site_roles(username):
    """Get site roles for a specific user."""
    try:
        _, err = _require_auth(required_role="admin")
        if err:
            return err
        from dracs.users import get_user_site_roles

        roles = get_user_site_roles(username)
        return jsonify({"success": True, "site_roles": roles})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


@app.route("/api/fw-summary")
def api_fw_summary():
    """Firmware version summary grouped by model."""
    from collections import Counter

    site_id, _ = _get_requested_site()
    _, err = _require_auth(required_role="admin", site_id=site_id)
    if err:
        return err

    systems = get_all_systems(site_id=site_id)
    model_filter = request.args.get("model")
    if model_filter:
        systems = [s for s in systems if s.model == model_filter]
    models = sorted(set(s.model for s in systems if s.model))
    result = []
    for m in models:
        model_systems = [s for s in systems if s.model == m]
        counts = Counter(s.idrac_version for s in model_systems if s.idrac_version)
        installed = sorted(counts.keys(), reverse=True)
        installed_data = [{"version": v, "count": counts[v]} for v in installed]

        from dracs.commands import _get_available_firmware_versions

        available = _get_available_firmware_versions(m)
        other = sorted([v for v in available if v not in counts], reverse=True)
        result.append({"model": m, "installed": installed_data, "available": other})
    return jsonify({"success": True, "models": result})


@app.route("/api/bios-summary")
def api_bios_summary():
    """BIOS version summary grouped by model."""
    from collections import Counter

    site_id, _ = _get_requested_site()
    _, err = _require_auth(required_role="admin", site_id=site_id)
    if err:
        return err

    systems = get_all_systems(site_id=site_id)
    model_filter = request.args.get("model")
    if model_filter:
        systems = [s for s in systems if s.model == model_filter]
    models = sorted(set(s.model for s in systems if s.model))
    result = []
    for m in models:
        model_systems = [s for s in systems if s.model == m]
        counts = Counter(s.bios_version for s in model_systems if s.bios_version)
        installed = sorted(counts.keys(), reverse=True)
        installed_data = [{"version": v, "count": counts[v]} for v in installed]

        from dracs.commands import _get_available_bios_versions

        available = _get_available_bios_versions(m)
        other = sorted([v for v in available if v not in counts], reverse=True)
        result.append({"model": m, "installed": installed_data, "available": other})
    return jsonify({"success": True, "models": result})


@app.route("/api/sites")
def api_sites_list():
    """List all sites with host counts."""
    from dracs.db import list_sites

    sites = list_sites()
    return jsonify({"success": True, "sites": sites})


@app.route("/api/sites", methods=["POST"])
def api_sites_create():
    """Create a new site (superadmin only)."""
    try:
        user, err = _require_auth(required_role="admin")
        if err:
            return err
        if not session.get("is_superadmin", False):
            return jsonify({"success": False, "message": "Superadmin required"}), 403

        data = request.get_json()
        if not data or not data.get("name"):
            return jsonify({"success": False, "message": "Site name required"}), 400

        from dracs.validation import validate_site_name

        name = data["name"].strip()
        if not validate_site_name(name):
            return (
                jsonify(
                    {
                        "success": False,
                        "message": "Invalid site name. Use alphanumeric characters or underscores only, max 32.",
                    }
                ),
                400,
            )

        from dracs.db import create_site

        site = create_site(name)

        from dracs.sites import get_site_ini_config, set_site_ini_config

        existing = get_site_ini_config(name)
        if not existing["defaults"]:
            set_site_ini_config(
                name,
                {
                    "defaults": {
                        "username": "root",
                        "password": "calvin",
                        "vnc_port": "5901",
                        "vnc_password": "",
                    }
                },
            )

        audit_log(
            "site_create",
            target=name,
            user=user,
            source=_client_ip(),
        )
        return jsonify({"success": True, "site": site})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 400


@app.route("/api/sites/<name>", methods=["DELETE"])
def api_sites_delete(name):
    """Delete a site (superadmin only)."""
    try:
        user, err = _require_auth(required_role="admin")
        if err:
            return err
        if not session.get("is_superadmin", False):
            return jsonify({"success": False, "message": "Superadmin required"}), 403

        from dracs.db import get_site_by_name, delete_site

        site = get_site_by_name(name)
        if site is None:
            return jsonify({"success": False, "message": "Site not found"}), 404

        delete_site(site["id"])

        from dracs.sites import remove_site_ini_sections

        remove_site_ini_sections(name)

        audit_log(
            "site_delete",
            target=name,
            user=user,
            source=_client_ip(),
        )
        return jsonify({"success": True, "message": f"Site '{name}' deleted."})
    except ValueError as ve:
        return jsonify({"success": False, "message": str(ve)}), 400
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


@app.route("/api/sites/<name>", methods=["PATCH"])
def api_sites_rename(name):
    """Rename a site (superadmin only)."""
    try:
        user, err = _require_auth(required_role="admin")
        if err:
            return err
        if not session.get("is_superadmin", False):
            return jsonify({"success": False, "message": "Superadmin required"}), 403

        data = request.get_json()
        if not data or not data.get("name"):
            return jsonify({"success": False, "message": "New name required"}), 400

        from dracs.validation import validate_site_name

        new_name = data["name"].strip()
        if not validate_site_name(new_name):
            return (
                jsonify(
                    {
                        "success": False,
                        "message": "Invalid site name. Use alphanumeric characters or underscores only, max 32.",
                    }
                ),
                400,
            )

        from dracs.db import get_site_by_name, rename_site
        from dracs.sites import rename_site_ini_sections

        site = get_site_by_name(name)
        if site is None:
            return jsonify({"success": False, "message": "Site not found"}), 404

        rename_site(site["id"], new_name)
        rename_site_ini_sections(name, new_name)

        audit_log(
            "site_rename",
            target=f"{name} -> {new_name}",
            user=user,
            source=_client_ip(),
        )
        return jsonify(
            {"success": True, "message": f"Site '{name}' renamed to '{new_name}'."}
        )
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 400


@app.route("/api/sites/<name>/config")
def api_sites_config_get(name):
    """Get site credential configuration (superadmin only)."""
    _, err = _require_auth(required_role="admin")
    if err:
        return err
    if not session.get("is_superadmin", False):
        return jsonify({"success": False, "message": "Superadmin required"}), 403

    from dracs.db import get_site_by_name
    from dracs.sites import get_site_ini_config

    config = get_site_ini_config(name)
    site = get_site_by_name(name)
    config["allowed_domains"] = site["allowed_domains"] or "" if site else ""
    return jsonify({"success": True, "config": config})


@app.route("/api/sites/<name>/config", methods=["PUT"])
def api_sites_config_set(name):
    """Set site credential configuration (superadmin only)."""
    try:
        user, err = _require_auth(required_role="admin")
        if err:
            return err
        if not session.get("is_superadmin", False):
            return jsonify({"success": False, "message": "Superadmin required"}), 403

        data = request.get_json()
        if not data:
            return jsonify({"success": False, "message": "Config data required"}), 400

        from dracs.db import get_site_by_name, update_site_allowed_domains
        from dracs.sites import set_site_ini_config

        set_site_ini_config(name, data)

        site = get_site_by_name(name)
        if site:
            update_site_allowed_domains(site["id"], data.get("allowed_domains") or None)

        audit_log(
            "site_config_update",
            target=name,
            user=user,
            source=_client_ip(),
        )
        return jsonify(
            {"success": True, "message": f"Config for site '{name}' updated."}
        )
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


@app.route("/api/sites/<name>/quads-verify", methods=["POST"])
def api_sites_quads_verify(name):
    """Test QUADS API connectivity (superadmin only)."""
    _, err = _require_auth(required_role="admin")
    if err:
        return err
    if not session.get("is_superadmin", False):
        return jsonify({"success": False, "message": "Superadmin required"}), 403
    data = request.get_json(silent=True) or {}
    quads_url = data.get("quads_url", "").rstrip("/")
    if not quads_url:
        return jsonify({"success": False, "message": "No QUADS URL provided"}), 400
    url = f"{quads_url}/api/v3/schedules/current"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "dracs-webapp/1.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:  # nosec
            resp.read()
        return jsonify({"success": True, "message": "QUADS endpoint reachable"})
    except Exception as e:
        return jsonify({"success": False, "message": f"QUADS unreachable: {e}"})


@app.route("/api/sites/<name>/quads-schedules")
def api_sites_quads_schedules(name):
    """Fetch current QUADS allocations filtered by role and DRACS host membership."""
    user, err = _require_auth()
    if err:
        return err

    from dracs.db import get_site_by_name
    from dracs.sites import get_site_ini_config
    from dracs.users import get_user_role_for_site

    site = get_site_by_name(name)
    if site is None:
        return jsonify({"success": False, "message": "Site not found"}), 404
    site_id = site["id"]

    cfg = get_site_ini_config(name)
    quads_enabled = cfg["defaults"].get("quads_enabled", "false").lower() in (
        "true",
        "1",
        "yes",
    )
    quads_url = cfg["defaults"].get("quads_url", "").rstrip("/")
    if not quads_enabled or not quads_url:
        return (
            jsonify({"success": False, "message": "QUADS not enabled for this site"}),
            400,
        )

    is_super = session.get("is_superadmin", False)
    if is_super:
        site_role = "admin"
    else:
        site_role = get_user_role_for_site(user, site_id)
        if site_role not in ("admin", "user", "quads"):
            return jsonify({"success": False, "message": "Access denied"}), 403

    quads_api_url = f"{quads_url}/api/v3/schedules/current"
    try:
        req = urllib.request.Request(
            quads_api_url, headers={"User-Agent": "dracs-webapp/1.0"}
        )
        with urllib.request.urlopen(req, timeout=10) as resp:  # nosec
            schedules = json.loads(resp.read().decode())
    except Exception as e:
        return (
            jsonify({"success": False, "message": f"Failed to fetch QUADS data: {e}"}),
            502,
        )

    with get_session() as db_sess:
        dracs_hosts = frozenset(
            s.name
            for s in db_sess.query(System)
            .filter(
                System.site_id == site_id,
                System.name.isnot(None),
            )
            .all()
        )

    is_quads_only = site_role == "quads"
    clouds = {}
    for sched in schedules:
        assignment = sched.get("assignment")
        host_obj = sched.get("host")
        if not assignment or not host_obj:
            continue
        cloud_obj = assignment.get("cloud") or {}
        cloud_name = cloud_obj.get("name")
        hostname = host_obj.get("name")
        if not cloud_name or not hostname:
            continue
        if is_quads_only:
            owner = assignment.get("owner", "")
            ccuser = assignment.get("ccuser") or []
            if owner != user and user not in ccuser:
                continue
        if hostname not in dracs_hosts:
            continue
        if cloud_name not in clouds:
            clouds[cloud_name] = {
                "cloud": cloud_name,
                "description": assignment.get("description", ""),
                "hosts": [],
            }
        clouds[cloud_name]["hosts"].append(hostname)

    allocations = sorted(
        [
            {
                "cloud": info["cloud"],
                "description": info["description"],
                "host_count": len(info["hosts"]),
                "hosts": sorted(info["hosts"]),
            }
            for info in clouds.values()
            if info["hosts"]
        ],
        key=lambda x: x["cloud"],
    )

    return jsonify({"success": True, "allocations": allocations})


@app.route("/api/sites/<name>/set-primary", methods=["PUT"])
def api_sites_set_primary(name):
    """Promote a site to primary (superadmin only)."""
    try:
        user, err = _require_auth(required_role="admin")
        if err:
            return err
        if not session.get("is_superadmin", False):
            return jsonify({"success": False, "message": "Superadmin required"}), 403

        from dracs.db import get_site_by_name, set_primary_site

        site = get_site_by_name(name)
        if site is None:
            return jsonify({"success": False, "message": "Site not found"}), 404
        if site["is_primary"]:
            return (
                jsonify({"success": False, "message": "Site is already primary"}),
                400,
            )

        set_primary_site(site["id"])
        audit_log("site_set_primary", target=name, user=user, source=_client_ip())
        return jsonify(
            {"success": True, "message": f"'{name}' is now the primary site"}
        )
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


@app.route("/api/sites/reorder", methods=["POST"])
def api_sites_reorder():
    """Persist a new display order for all sites (superadmin only)."""
    try:
        user, err = _require_auth(required_role="admin")
        if err:
            return err
        if not session.get("is_superadmin", False):
            return jsonify({"success": False, "message": "Superadmin required"}), 403

        data = request.get_json()
        if not data or not isinstance(data.get("site_ids"), list):
            return jsonify({"success": False, "message": "site_ids list required"}), 400

        from dracs.db import reorder_sites

        reorder_sites(data["site_ids"])
        audit_log("site_reorder", user=user, source=_client_ip())
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


@app.route("/api/vnc-session", methods=["POST"])
def api_vnc_session_create():
    """Create a VNC console session for a host."""
    try:
        user, err = _require_auth()
        if err:
            return err

        if not VNC_ENABLE or vnc_manager is None:
            return (
                jsonify({"success": False, "message": "VNC console is not enabled"}),
                404,
            )

        data = request.get_json(silent=True)
        if not data:
            return jsonify({"success": False, "message": "Invalid request"}), 400

        hostname = data.get("hostname", "").strip()
        if not hostname:
            return (
                jsonify({"success": False, "message": "Hostname is required"}),
                400,
            )

        idrac_fqdn = build_idrac_hostname(hostname)
        vnc_port, vnc_password = get_vnc_credentials(hostname)

        existing = vnc_manager.find_session_by_hostname(hostname)
        if existing:
            vnc_manager.add_reference(existing)
            vnc_manager.touch_session(existing)
            audit_log(
                "vnc_session_join",
                target=hostname,
                user=user,
                source=_client_ip(),
                details=f"token={existing}",
            )
            return jsonify({"success": True, "token": existing})

        reachable, error_msg = check_vnc_connectivity(idrac_fqdn, int(vnc_port))
        if not reachable:
            return (
                jsonify({"success": False, "message": error_msg}),
                503,
            )

        if VNC_PROXY_ENABLE:
            proxy_port = vnc_manager.find_free_port()
            if proxy_port:
                token = vnc_manager.create_session(hostname, "127.0.0.1", proxy_port)
                vnc_manager.start_proxy(
                    token, idrac_fqdn, int(vnc_port), vnc_password, proxy_port
                )
            else:
                token = vnc_manager.create_session(hostname, idrac_fqdn, int(vnc_port))
        else:
            token = vnc_manager.create_session(hostname, idrac_fqdn, int(vnc_port))

        audit_log(
            "vnc_session_create",
            target=hostname,
            user=user,
            source=_client_ip(),
        )

        return jsonify({"success": True, "token": token})

    except MaxSessionsError as e:
        return jsonify({"success": False, "message": str(e)}), 429
    except Exception as e:
        return jsonify({"success": False, "message": f"Error: {str(e)}"}), 500


@app.route("/api/vnc-session/<token>", methods=["DELETE"])
def api_vnc_session_delete(token):
    """Destroy a VNC console session."""
    try:
        user, err = _require_auth()
        if err:
            return err

        if not VNC_ENABLE or vnc_manager is None:
            return (
                jsonify({"success": False, "message": "VNC console is not enabled"}),
                404,
            )

        released = vnc_manager.release_session(token)

        audit_log(
            "vnc_session_delete",
            user=user,
            source=_client_ip(),
            details=f"token={token},released={released}",
        )

        return jsonify({"success": True, "message": "Session closed"})

    except Exception as e:
        return jsonify({"success": False, "message": f"Error: {str(e)}"}), 500


@app.route("/api/vnc-session/<token>/ref", methods=["POST"])
def api_vnc_session_addref(token):
    """
    Increment the reference count for a shared VNC session.

    Called by popout windows opened from the multi-console page so that
    closing the popout decrements rather than force-removes the session.
    """
    try:
        _, err = _require_auth()
        if err:
            return err
        if not VNC_ENABLE or vnc_manager is None:
            return (
                jsonify({"success": False, "message": "VNC console is not enabled"}),
                404,
            )
        if vnc_manager.add_reference(token):
            return jsonify({"success": True})
        return jsonify({"success": False, "message": "Session not found"}), 404
    except Exception as e:
        return jsonify({"success": False, "message": f"Error: {str(e)}"}), 500


@app.route("/console-multi")
def console_multi():
    """
    Multi-console viewer.
    opens a tiled grid of embedded noVNC sessions for
    two or more hosts simultaneously.  Session creation is deferred to
    JavaScript so the popup appears immediately.
    """
    _, err = _require_auth()
    if err:
        return err
    if not VNC_ENABLE or vnc_manager is None:
        return (
            jsonify({"success": False, "message": "VNC console is not enabled"}),
            404,
        )
    hosts_param = request.args.get("hosts", "").strip()
    if not hosts_param:
        return jsonify({"success": False, "message": "No hosts specified"}), 400
    hostnames = [h.strip() for h in hosts_param.split(",") if h.strip()]
    if len(hostnames) < 2:
        return (
            jsonify({"success": False, "message": "At least two hosts required"}),
            400,
        )
    for hostname in hostnames:
        if not validate_hostname(hostname):
            return (
                jsonify({"success": False, "message": f"Invalid hostname: {hostname}"}),
                400,
            )
    return render_template("console_multi.html", hostnames=hostnames)


@app.route("/console-quads")
def console_quads():
    """QUADS multi-console viewer — allocations-based tiled VNC grid."""
    if not VNC_ENABLE or vnc_manager is None:
        return (
            jsonify({"success": False, "message": "VNC console is not enabled"}),
            404,
        )
    site_name_param = request.args.get("site", "").strip()
    cloud_param = request.args.get("cloud", "").strip()
    is_authenticated = session.get("authenticated", False) or session.get(
        "is_superadmin", False
    )
    return render_template(
        "console_quads.html",
        site_name=site_name_param,
        cloud=cloud_param,
        authenticated=is_authenticated,
        google_auth_enabled=GOOGLE_AUTH_ENABLED,
        is_sso_login=session.get("sso_login", False),
    )


@app.route("/console-connect")
def console_connect():
    """
    Interstitial page that creates a VNC session and redirects to the console.

    Opened synchronously (no await) from the main UI so that all popup windows
    are created within the user-gesture context, avoiding browser popup blocking
    when multiple consoles are opened at once.
    """
    _, err = _require_auth()
    if err:
        return err

    if not VNC_ENABLE or vnc_manager is None:
        return (
            jsonify({"success": False, "message": "VNC console is not enabled"}),
            404,
        )

    hostname = request.args.get("host", "").strip()
    if not hostname or not validate_hostname(hostname):
        return jsonify({"success": False, "message": "Invalid hostname"}), 400

    return render_template("console_connect.html", hostname=hostname)


@app.route("/console/<token>")
def console_view(token):
    """Serve the noVNC console viewer for a session."""
    _, err = _require_auth()
    if err:
        return err

    if not VNC_ENABLE or vnc_manager is None:
        return (
            jsonify({"success": False, "message": "VNC console is not enabled"}),
            404,
        )

    session_info = vnc_manager.get_session_info(token)
    if not session_info:
        return (
            jsonify(
                {"success": False, "message": "Console session not found or expired"}
            ),
            404,
        )

    hostname = session_info["hostname"]
    _, vnc_password = get_vnc_credentials(hostname)

    return render_template(
        "console.html",
        token=token,
        hostname=hostname,
        vnc_password=vnc_password,
    )


@app.route("/api/vnc-session/<token>", methods=["PATCH"])
def api_vnc_session_touch(token):
    """Heartbeat: reset expiry timer for an active VNC session."""
    _, err = _require_auth()
    if err:
        return err

    if not VNC_ENABLE or vnc_manager is None:
        return jsonify({"success": False, "message": "VNC console is not enabled"}), 404

    if vnc_manager.touch_session(token):
        return jsonify({"success": True})
    return jsonify({"success": False, "message": "Session not found"}), 404


@app.route("/api/vnc-session/<token>/viewers", methods=["GET"])
def api_vnc_session_viewers(token):
    """Return the current viewer reference count for a VNC session."""
    _, err = _require_auth()
    if err:
        return err

    if vnc_manager is None:
        return jsonify({"viewers": 0})

    return jsonify({"viewers": vnc_manager.get_ref_count(token)})


@app.route("/api/host/<hostname>/vnc-viewers", methods=["GET"])
def api_host_vnc_viewers(hostname):
    """Return the current VNC viewer count for a host by name."""
    _, err = _require_auth()
    if err:
        return err

    if vnc_manager is None:
        return jsonify({"hostname": hostname, "viewers": 0})

    token = vnc_manager.find_session_by_hostname(hostname)
    if token is None:
        return jsonify({"hostname": hostname, "viewers": 0})

    return jsonify({"hostname": hostname, "viewers": vnc_manager.get_ref_count(token)})


@app.route("/api/vnc-viewers", methods=["GET"])
def api_vnc_viewers():
    """Return all active VNC sessions, optionally filtered by site."""
    site_name = request.args.get("site")
    site_id = None
    if site_name:
        from dracs.db import get_site_by_name

        site_row = get_site_by_name(site_name)
        site_id = site_row["id"] if site_row else None

    _, err = _require_auth()
    if err:
        return err

    from dracs.vnc import get_all_active_viewer_counts

    counts = get_all_active_viewer_counts()

    if site_id is not None:
        from dracs.db import get_hosts_for_site

        site_hostnames = {h["hostname"] for h in get_hosts_for_site(site_id)}
        counts = {h: c for h, c in counts.items() if h in site_hostnames}

    sessions = [{"hostname": h, "viewers": c} for h, c in sorted(counts.items())]
    return jsonify({"sessions": sessions})


@app.route("/api/host/<hostname>/vnc-reset", methods=["POST"])
def api_host_vnc_reset(hostname):
    """Enqueue a VNC configuration reset for a host.

    Returns 409 if active viewers are connected unless force=true is passed.
    Requires admin role.
    """
    site_name = request.args.get("site")
    site_id = None
    if site_name:
        from dracs.db import get_site_by_name

        site_row = get_site_by_name(site_name)
        site_id = site_row["id"] if site_row else None

    _, err = _require_auth(required_role="admin", site_id=site_id)
    if err:
        return err

    data = request.get_json(silent=True) or {}
    force = bool(data.get("force", False))

    if vnc_manager is not None:
        token = vnc_manager.find_session_by_hostname(hostname)
        if token is not None:
            count = vnc_manager.get_ref_count(token)
            if count > 0 and not force:
                return (
                    jsonify(
                        {
                            "success": False,
                            "message": (
                                f"VNC connection count is currently {count} for "
                                f"{hostname}. Use --force option to reset anyway."
                            ),
                        }
                    ),
                    409,
                )

    from dracs.jobqueue import enqueue_job

    job_id = enqueue_job("vnc_reset", hostname, site_id=site_id)
    return jsonify(
        {
            "success": True,
            "message": f"VNC reset queued for {hostname}",
            "job_id": job_id,
        }
    )


@app.route("/api/sol/connect-info", methods=["GET"])
def api_sol_connect_info():
    """Return conserver connection info for a site (server, port, username, password).

    Requires site-admin role for the requested site. The plaintext password is
    used by the dracs / dracs-client sol subcommand to authenticate via pexpect.
    """
    if not SOL_ENABLE:
        return jsonify({"success": False, "message": "SOL feature is not enabled"}), 404

    site_id, site_name = _get_requested_site()
    if site_id is None:
        return (
            jsonify({"success": False, "message": f"Site '{site_name}' not found"}),
            404,
        )

    _, err = _require_auth(required_role="admin", site_id=site_id)
    if err:
        return err

    from dracs.sites import get_site_ini_config

    cfg = get_site_ini_config(site_name)
    password = cfg.get("defaults", {}).get("conserver_password")
    if not password:
        return (
            jsonify(
                {
                    "success": False,
                    "message": "Conserver password not configured for this site",
                }
            ),
            500,
        )

    from dracs.sol import _ssl_cert_key_paths

    ssl_cert_path, _ = _ssl_cert_key_paths()
    ssl_ca_path = os.environ.get("SOL_SSL_CA", "")
    ssl_ca_content = None
    if ssl_cert_path and ssl_ca_path:
        try:
            ssl_ca_content = Path(ssl_ca_path).read_text()
        except OSError:
            app.logger.warning("Could not read SOL_SSL_CA file: %s", ssl_ca_path)

    return jsonify(
        {
            "success": True,
            "server": socket.getfqdn(),
            "port": os.environ.get("SOL_CONSERVER_PORT", "3109"),
            "username": site_name,
            "password": password,
            "ssl": bool(ssl_cert_path),
            "ssl_ca": ssl_ca_content,
        }
    )


def _parse_remoteimage_status(output: str) -> dict:
    """Parse output of racadm remoteimage -s into {enabled, url}."""
    enabled = False
    url = ""
    for line in output.splitlines():
        line = line.strip()
        if line.startswith("Remote File Share is"):
            enabled = "Enabled" in line
        elif line.startswith("ShareName"):
            parts = line.split(None, 1)
            if len(parts) > 1:
                url = parts[1].strip()
    return {"enabled": enabled, "url": url}


@app.route("/api/iso-images")
def api_iso_images():
    """List ISO images available for remote image mounting."""
    _, err = _require_auth()
    if err:
        return err
    try:
        if not ISO_IMAGE_DIR.is_dir():
            return jsonify({"success": True, "images": []})
        fqdn = socket.getfqdn()
        images = [
            {"name": p.name, "url": f"http://{fqdn}/iso/{p.name}"}
            for p in sorted(ISO_IMAGE_DIR.iterdir(), key=lambda p: p.name)
            if p.is_file() and p.suffix.lower() == ".iso"
        ]
        return jsonify({"success": True, "images": images})
    except Exception as e:
        return jsonify({"success": False, "message": f"Error: {str(e)}"}), 500


@app.route("/api/remoteimage/<hostname>")
def api_remoteimage_status(hostname):
    """Get current remote image status for a host via racadm."""
    _, err = _require_auth()
    if err:
        return err
    if not validate_hostname(hostname):
        return jsonify({"success": False, "message": "Invalid hostname"}), 400
    try:
        cmd = _build_ssh_racadm_cmd(hostname, "remoteimage", "-s")
        result = subprocess.run(  # nosec # nosemgrep
            cmd, capture_output=True, text=True, timeout=30  # nosemgrep
        )
        if result.returncode != 0:
            stderr = result.stderr.strip() or result.stdout.strip()
            return (
                jsonify(
                    {
                        "success": False,
                        "message": f"Failed to query remote image status: {stderr}",
                    }
                ),
                500,
            )
        status = _parse_remoteimage_status(result.stdout)
        return jsonify({"success": True, **status})
    except subprocess.TimeoutExpired:
        return jsonify({"success": False, "message": "Connection timeout"}), 500
    except Exception as e:
        return jsonify({"success": False, "message": f"Error: {str(e)}"}), 500


@app.route("/api/remoteimage/<hostname>", methods=["POST"])
def api_remoteimage_apply(hostname):
    """Enable or disable remote image on a host via racadm."""
    user, err = _require_auth()
    if err:
        return err
    if not validate_hostname(hostname):
        return jsonify({"success": False, "message": "Invalid hostname"}), 400
    try:
        data = request.get_json(silent=True) or {}
        action = data.get("action", "")
        if action not in ("enable", "disable"):
            return jsonify({"success": False, "message": "Invalid action"}), 400

        if action == "disable":
            cmd = _build_ssh_racadm_cmd(hostname, "remoteimage", "-d")
        else:
            url = data.get("url", "").strip()
            if not url:
                return (
                    jsonify(
                        {
                            "success": False,
                            "message": "URL required to enable remote image",
                        }
                    ),
                    400,
                )
            cmd = _build_ssh_racadm_cmd(hostname, "remoteimage", "-c", "-l", url)

        result = subprocess.run(  # nosec # nosemgrep
            cmd, capture_output=True, text=True, timeout=30  # nosemgrep
        )
        if result.returncode != 0:
            stderr = result.stderr.strip() or result.stdout.strip()
            return (
                jsonify({"success": False, "message": f"Command failed: {stderr}"}),
                500,
            )

        racadm_out = (result.stdout.strip() or result.stderr.strip())[:300]
        audit_log(
            "remoteimage_apply",
            target=hostname,
            user=user,
            source=_client_ip(),
            details=f"action={action},output={racadm_out!r}",
        )
        return jsonify({"success": True, "message": f"Remote image {action}d"})
    except subprocess.TimeoutExpired:
        return jsonify({"success": False, "message": "Connection timeout"}), 500
    except Exception as e:
        return jsonify({"success": False, "message": f"Error: {str(e)}"}), 500


@app.route("/config")
def config_page():
    """Serve the iDRAC configuration view page."""
    is_sa = session.get("is_superadmin", False)
    role = session.get("role", "")
    username = session.get("username", "")

    is_admin = is_sa
    if not is_admin:
        site_id, _ = _get_requested_site()
        if site_id is not None:
            from dracs.users import get_user_role_for_site

            is_admin = get_user_role_for_site(username, site_id) == "admin"

    return render_template(
        "config.html",
        username=username,
        user_role=role,
        is_superadmin=is_sa,
        is_admin=is_admin,
    )


@app.route("/api/config-data", methods=["GET", "POST"])
def api_config_data():
    """Return cached iDRAC config data for requested hosts within a site."""
    from dracs.db import (
        get_host_config_data,
        get_site_by_name,
        get_site_config_collection,
    )

    if request.method == "POST":
        body = request.get_json(silent=True) or {}
        site_name = body.get("site", "")
        hostnames = body.get("hosts", [])
        if isinstance(hostnames, str):
            hostnames = [h.strip() for h in hostnames.split(",") if h.strip()]
    else:
        site_name = request.args.get("site", "")
        hosts_param = request.args.get("hosts", "")
        hostnames = [h.strip() for h in hosts_param.split(",") if h.strip()]

    site = get_site_by_name(site_name) if site_name else None
    site_id = site["id"] if site else None

    if site_id is None:
        return jsonify({"success": True, "settings": {}, "data": []})

    settings = get_site_config_collection(site_id)
    data = get_host_config_data(site_id, hostnames)
    return jsonify({"success": True, "settings": settings, "data": data})


@app.route("/api/config-edit", methods=["POST"])
def api_config_edit():
    """Queue racadm_config jobs for selected hosts (admin only)."""
    try:
        data = request.get_json()
        if not data:
            return jsonify({"success": False, "message": "Invalid request"}), 400

        site_name = data.get("site", "")
        hosts = data.get("hosts", [])
        settings = data.get("settings", {})

        if not hosts or not isinstance(hosts, list):
            return jsonify({"success": False, "message": "hosts list required"}), 400

        for h in hosts:
            if not validate_hostname(h):
                return (
                    jsonify({"success": False, "message": f"Invalid hostname: {h}"}),
                    400,
                )

        from dracs.db import get_site_by_name as _gsbn
        from dracs.jobqueue import enqueue_job as _eq

        site = _gsbn(site_name) if site_name else None
        if site is None:
            return (
                jsonify({"success": False, "message": f"Unknown site: {site_name!r}"}),
                400,
            )

        user, err = _require_auth(required_role="admin", site_id=site["id"])
        if err:
            return err

        site_id = site["id"]
        parent_id = _eq(
            "racadm_config_batch",
            "batch",
            site_id=site_id,
            metadata={"site_name": site_name},
        )
        for hostname in hosts:
            _eq(
                "racadm_config",
                hostname,
                parent_id=parent_id,
                site_id=site_id,
                metadata={"site_name": site_name, "settings": settings},
            )

        audit_log(
            "config_edit",
            user=user,
            source=_client_ip(),
            details=f"site={site_name} hosts={','.join(hosts)} settings={settings}",
        )

        return jsonify(
            {"success": True, "parent_job_id": parent_id, "job_count": len(hosts)}
        )

    except Exception as e:
        return jsonify({"success": False, "message": f"Error: {str(e)}"}), 500


@app.route("/api/config-refresh", methods=["POST"])
def api_config_refresh():
    """Immediately queue a full Redfish re-collection for selected hosts (admin only)."""
    try:
        data = request.get_json()
        if not data:
            return jsonify({"success": False, "message": "Invalid request"}), 400

        site_name = data.get("site", "")
        hosts = data.get("hosts", [])

        if not hosts or not isinstance(hosts, list):
            return jsonify({"success": False, "message": "hosts list required"}), 400

        for h in hosts:
            if not validate_hostname(h):
                return (
                    jsonify({"success": False, "message": f"Invalid hostname: {h}"}),
                    400,
                )

        from dracs.db import get_site_by_name as _gsbn
        from dracs.jobqueue import enqueue_job as _eq

        site = _gsbn(site_name) if site_name else None
        if site is None:
            return (
                jsonify({"success": False, "message": f"Unknown site: {site_name!r}"}),
                400,
            )

        user, err = _require_auth(required_role="admin", site_id=site["id"])
        if err:
            return err

        site_id = site["id"]
        for hostname in hosts:
            _eq(
                "config_collect",
                hostname,
                site_id=site_id,
                metadata={"site_name": site_name},
            )

        audit_log(
            "config_refresh",
            user=user,
            source=_client_ip(),
            details=f"site={site_name} hosts={','.join(hosts)}",
        )

        return jsonify({"success": True, "queued": len(hosts)})

    except Exception as e:
        return jsonify({"success": False, "message": f"Error: {str(e)}"}), 500


@app.route("/api/config-edit/status/<int:parent_id>")
def api_config_edit_status(parent_id):
    """Poll status of a config-edit batch job."""
    try:
        _, err = _require_auth()
        if err:
            return err

        from dracs.jobqueue import get_child_jobs, get_job_status
        from dracs.db import get_host_config_data

        parent = get_job_status(parent_id)
        if parent is None:
            return jsonify({"success": False, "message": "Job not found"}), 404

        children = get_child_jobs(parent_id)
        total = len(children)
        completed_count = sum(1 for c in children if c["status"] == "completed")
        failed_count = sum(1 for c in children if c["status"] == "failed")

        parent_meta = parent.get("metadata") or {}
        site_name = parent_meta.get("site_name", "")

        from dracs.db import get_site_by_name as _gsbn

        site = _gsbn(site_name) if site_name else None
        site_id = site["id"] if site else None

        child_results = []
        for c in children:
            config_data = None
            if c["status"] == "completed" and site_id is not None:
                rows = get_host_config_data(site_id, [c["target"]])
                config_data = rows[0] if rows else None
            child_results.append(
                {
                    "hostname": c["target"],
                    "status": c["status"],
                    "error": c.get("error"),
                    "config": config_data,
                }
            )

        return jsonify(
            {
                "success": True,
                "parent": {
                    "status": parent["status"],
                    "total_count": total,
                    "completed_count": completed_count,
                    "failed_count": failed_count,
                },
                "children": child_results,
            }
        )

    except Exception as e:
        return jsonify({"success": False, "message": f"Error: {str(e)}"}), 500


@app.route("/api/sites/<name>/config-collection")
def api_site_config_collection_get(name):
    """Get iDRAC collection settings for a site (superadmin only)."""
    _, err = _require_auth(required_role="admin")
    if err:
        return err
    if not session.get("is_superadmin", False):
        return jsonify({"success": False, "message": "Superadmin required"}), 403

    from dracs.db import get_site_by_name, get_site_config_collection

    site = get_site_by_name(name)
    if not site:
        return jsonify({"success": False, "message": "Site not found"}), 404

    settings = get_site_config_collection(site["id"])
    return jsonify({"success": True, "settings": settings})


@app.route("/api/sites/<name>/config-collection", methods=["PUT"])
def api_site_config_collection_put(name):
    """Update iDRAC collection settings for a site (superadmin only)."""
    try:
        user, err = _require_auth(required_role="admin")
        if err:
            return err
        if not session.get("is_superadmin", False):
            return jsonify({"success": False, "message": "Superadmin required"}), 403

        data = request.get_json(silent=True)
        if not data:
            return (
                jsonify({"success": False, "message": "Settings data required"}),
                400,
            )

        from dracs.db import get_site_by_name, upsert_site_config_collection

        site = get_site_by_name(name)
        if not site:
            return jsonify({"success": False, "message": "Site not found"}), 404

        upsert_site_config_collection(site["id"], data)
        audit_log(
            "site_config_collection_update",
            target=name,
            user=user,
            source=_client_ip(),
        )
        return jsonify(
            {"success": True, "message": f"Collection settings for '{name}' updated."}
        )
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


_IDRACADM7 = "/opt/dell/srvadmin/bin/idracadm7"


def _parse_cert_pem(pem: str):
    """Return (sha256_fingerprint_hex, expiry_iso) from a PEM certificate string."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes

    try:
        cert = x509.load_pem_x509_certificate(pem.encode())
    except Exception as exc:
        raise ValueError(f"Invalid certificate PEM: {exc}") from exc
    fp_str = ":".join(f"{b:02X}" for b in cert.fingerprint(hashes.SHA256()))
    try:
        expiry = cert.not_valid_after_utc.isoformat()
    except AttributeError:
        from datetime import timezone as _tz

        expiry = cert.not_valid_after.replace(tzinfo=_tz.utc).isoformat()
    return fp_str, expiry


def _validate_key_pem(pem: str) -> None:
    """Validate a PEM private key; raises ValueError if invalid."""
    from cryptography.hazmat.primitives.serialization import load_pem_private_key

    try:
        load_pem_private_key(pem.encode(), password=None)
    except Exception as exc:
        raise ValueError(f"Invalid private key PEM: {exc}") from exc


@app.route("/api/system/ssl-tools")
def api_ssl_tools():
    """Report whether idracadm7 is present on this system (superadmin only)."""
    _, err = _require_auth(required_role="admin")
    if err:
        return err
    if not session.get("is_superadmin", False):
        return jsonify({"success": False, "message": "Superadmin required"}), 403
    return jsonify(
        {"success": True, "available": os.path.exists(_IDRACADM7), "path": _IDRACADM7}
    )


@app.route("/api/sites/<name>/ssl-config")
def api_site_ssl_config_get(name):
    """Return SSL cert management config for a site (PEM content never returned)."""
    _, err = _require_auth(required_role="admin")
    if err:
        return err
    if not session.get("is_superadmin", False):
        return jsonify({"success": False, "message": "Superadmin required"}), 403
    try:
        from dracs.db import get_site_by_name, get_site_ssl_config

        site = get_site_by_name(name)
        if not site:
            return jsonify({"success": False, "message": "Site not found"}), 404
        cfg = get_site_ssl_config(site["id"])
        cfg.pop("cert_pem", None)
        cfg.pop("key_pem", None)
        return jsonify({"success": True, **cfg})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


@app.route("/api/sites/<name>/ssl-config", methods=["PUT"])
def api_site_ssl_config_put(name):
    """Save SSL cert management and schedule config for a site (superadmin only)."""
    user, err = _require_auth(required_role="admin")
    if err:
        return err
    if not session.get("is_superadmin", False):
        return jsonify({"success": False, "message": "Superadmin required"}), 403
    try:
        from dracs.db import get_site_by_name, upsert_site_ssl_config

        data = request.get_json(silent=True) or {}
        site = get_site_by_name(name)
        if not site:
            return jsonify({"success": False, "message": "Site not found"}), 404

        payload = {}

        if "enabled" in data:
            if data["enabled"] and not os.path.exists(_IDRACADM7):
                return (
                    jsonify(
                        {
                            "success": False,
                            "message": f"Cannot enable: {_IDRACADM7} not found on this system",
                        }
                    ),
                    400,
                )
            payload["enabled"] = bool(data["enabled"])

        cert_pem = (data.get("cert_pem") or "").strip()
        key_pem = (data.get("key_pem") or "").strip()
        cert_fingerprint = cert_expiry = None

        if cert_pem or key_pem:
            if not cert_pem or not key_pem:
                return (
                    jsonify(
                        {
                            "success": False,
                            "message": "Both certificate and private key are required",
                        }
                    ),
                    400,
                )
            try:
                cert_fingerprint, cert_expiry = _parse_cert_pem(cert_pem)
            except ValueError as ve:
                return jsonify({"success": False, "message": str(ve)}), 400
            try:
                _validate_key_pem(key_pem)
            except ValueError as ve:
                return jsonify({"success": False, "message": str(ve)}), 400
            payload.update(
                cert_pem=cert_pem,
                key_pem=key_pem,
                cert_fingerprint=cert_fingerprint,
                cert_expiry=cert_expiry,
            )

        if "schedule_enabled" in data:
            payload["schedule_enabled"] = bool(data["schedule_enabled"])
        if "schedule_frequency" in data:
            freq = data["schedule_frequency"] or ""
            valid = {"daily", "weekly", "biweekly", "monthly", "quarterly", ""}
            if freq not in valid:
                return (
                    jsonify(
                        {"success": False, "message": f"Invalid frequency: {freq!r}"}
                    ),
                    400,
                )
            payload["schedule_frequency"] = freq or None
        if "schedule_time" in data:
            t = (data.get("schedule_time") or "").strip()
            if t:
                try:
                    h, m = t.split(":")
                    if not (0 <= int(h) <= 23 and 0 <= int(m) <= 59):
                        raise ValueError()
                except Exception:
                    return (
                        jsonify({"success": False, "message": f"Invalid time: {t!r}"}),
                        400,
                    )
            payload["schedule_time"] = t or None

        upsert_site_ssl_config(site["id"], payload)
        audit_log("site_ssl_config_update", target=name, user=user, source=_client_ip())
        resp = {"success": True}
        if cert_fingerprint:
            resp["cert_fingerprint"] = cert_fingerprint
            resp["cert_expiry"] = cert_expiry
        return jsonify(resp)
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


@app.route("/api/sites/<name>/ssl-overrides")
def api_site_ssl_overrides_get(name):
    """Return per-host SSL override summaries for a site (fingerprints only)."""
    _, err = _require_auth(required_role="admin")
    if err:
        return err
    if not session.get("is_superadmin", False):
        return jsonify({"success": False, "message": "Superadmin required"}), 403
    try:
        from dracs.db import get_site_by_name, get_all_host_ssl_overrides

        site = get_site_by_name(name)
        if not site:
            return jsonify({"success": False, "message": "Site not found"}), 404
        return jsonify(
            {"success": True, "overrides": get_all_host_ssl_overrides(site["id"])}
        )
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


@app.route("/api/sites/<name>/ssl-overrides/<path:hostname>", methods=["PUT"])
def api_host_ssl_override_put(name, hostname):
    """Set per-host SSL cert/key override (superadmin only)."""
    user, err = _require_auth(required_role="admin")
    if err:
        return err
    if not session.get("is_superadmin", False):
        return jsonify({"success": False, "message": "Superadmin required"}), 403
    try:
        from dracs.db import get_site_by_name, upsert_host_ssl_override

        data = request.get_json(silent=True) or {}
        site = get_site_by_name(name)
        if not site:
            return jsonify({"success": False, "message": "Site not found"}), 404

        cert_pem = (data.get("cert_pem") or "").strip()
        key_pem = (data.get("key_pem") or "").strip()
        if not cert_pem or not key_pem:
            return (
                jsonify(
                    {
                        "success": False,
                        "message": "Both cert_pem and key_pem are required",
                    }
                ),
                400,
            )

        try:
            cert_fingerprint, cert_expiry = _parse_cert_pem(cert_pem)
        except ValueError as ve:
            return jsonify({"success": False, "message": str(ve)}), 400
        try:
            _validate_key_pem(key_pem)
        except ValueError as ve:
            return jsonify({"success": False, "message": str(ve)}), 400

        upsert_host_ssl_override(
            hostname,
            site["id"],
            {
                "cert_pem": cert_pem,
                "key_pem": key_pem,
                "cert_fingerprint": cert_fingerprint,
            },
        )
        audit_log(
            "host_ssl_override_set",
            target=hostname,
            user=user,
            source=_client_ip(),
            details=f"site={name}",
        )
        return jsonify(
            {
                "success": True,
                "cert_fingerprint": cert_fingerprint,
                "cert_expiry": cert_expiry,
            }
        )
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


@app.route("/api/sites/<name>/ssl-overrides/<path:hostname>", methods=["DELETE"])
def api_host_ssl_override_delete(name, hostname):
    """Remove per-host SSL cert/key override (superadmin only)."""
    user, err = _require_auth(required_role="admin")
    if err:
        return err
    if not session.get("is_superadmin", False):
        return jsonify({"success": False, "message": "Superadmin required"}), 403
    try:
        from dracs.db import get_site_by_name, delete_host_ssl_override

        site = get_site_by_name(name)
        if not site:
            return jsonify({"success": False, "message": "Site not found"}), 404
        if not delete_host_ssl_override(hostname, site["id"]):
            return jsonify({"success": False, "message": "Override not found"}), 404
        audit_log(
            "host_ssl_override_delete",
            target=hostname,
            user=user,
            source=_client_ip(),
            details=f"site={name}",
        )
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


@app.route("/api/sites/<name>/ssl-sweep", methods=["POST"])
def api_site_ssl_sweep(name):
    """Manually trigger an SSL cert sweep for all hosts in a site (superadmin only)."""
    user, err = _require_auth(required_role="admin")
    if err:
        return err
    if not session.get("is_superadmin", False):
        return jsonify({"success": False, "message": "Superadmin required"}), 403
    try:
        from dracs.db import get_site_by_name, get_site_ssl_config
        from dracs.jobqueue import enqueue_batch

        site = get_site_by_name(name)
        if not site:
            return jsonify({"success": False, "message": "Site not found"}), 404
        cfg = get_site_ssl_config(site["id"])
        if not cfg.get("enabled"):
            return (
                jsonify(
                    {
                        "success": False,
                        "message": "SSL cert management is not enabled for this site",
                    }
                ),
                400,
            )
        if not cfg.get("has_cert") or not cfg.get("has_key"):
            return (
                jsonify(
                    {
                        "success": False,
                        "message": "No SSL certificate/key configured for this site",
                    }
                ),
                400,
            )
        count = enqueue_batch(
            "ssl_cert_upload",
            "all",
            site_id=site["id"],
            metadata={"site_name": name},
        )
        audit_log(
            "site_ssl_sweep",
            target=name,
            user=user,
            source=_client_ip(),
            details=f"queued={count}",
        )
        return jsonify({"success": True, "queued": count})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


def _parse_debug_env() -> bool:
    value = os.getenv("DEBUG", "false")
    if value in ("true", "True", "TRUE", "1"):
        return True
    if value in ("false", "False", "FALSE", "0"):
        return False
    raise ValueError(
        f"Invalid DEBUG value '{value}' in .env file. "
        "Must be one of: true, True, TRUE, 1, false, False, FALSE, 0"
    )


if __name__ == "__main__":  # pragma: no cover
    app.run(host="127.0.0.1", port=1888, debug=_parse_debug_env())

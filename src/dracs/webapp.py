"""Flask web application for DRACS inventory management."""

import asyncio
import configparser
from datetime import datetime
import glob
import gzip
import json
import os
import re
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
from flask import Flask, render_template, jsonify, session, request, Response
from markupsafe import Markup

from dracs.db import db_initialize, get_session, System
from dracs.commands import refresh_dell_warranty
from dracs.snmp import build_idrac_hostname
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

# Secret key for sessions (use environment variable in production)
# Default key is only for development - change in production!
app.secret_key = os.environ.get(
    "FLASK_SECRET_KEY",
    "dev-secret-key-change-in-production-12345678901234567890123456789012",
)

# Session security settings
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"

# Load admin credentials from environment or use defaults
# Priority: 1) .env file (if exists), 2) environment variables, 3) defaults below
# This allows local installations to override the password via .env
# even when gunicorn.conf.py is updated via git pull
ADMIN_USER = os.environ.get("WEBADMIN_USER", "admin")
ADMIN_PASSWORD = os.environ.get("WEBADMIN_PASSWORD", "admin")

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
VNC_TIMEOUT = int(os.environ.get("VNC_TIMEOUT", "30"))
VNC_MAX_SESSIONS = int(os.environ.get("VNC_MAX_SESSIONS", "20"))
VNC_WEBSOCKIFY_PORT = int(os.environ.get("VNC_WEBSOCKIFY_PORT", "6080"))

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

vnc_manager = None
if VNC_ENABLE:
    from dracs.vnc import get_token_dir

    vnc_manager = VncSessionManager(get_token_dir(), VNC_TIMEOUT, VNC_MAX_SESSIONS)

# Initialize database on app startup
DB_PATH = os.environ.get("DRACS_DB", "warranty.db")
db_initialize(DB_PATH)


def get_all_systems():
    """Get all systems from database ordered by hostname."""
    with get_session() as session:
        systems = session.query(System).order_by(System.name).all()
        return systems


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


def get_idrac_credentials(hostname: str) -> tuple:
    """
    Get iDRAC credentials from drac-passwords.ini file.

    Args:
        hostname: The hostname to look up credentials for

    Returns:
        tuple: (username, password)
    """
    config_file = Path("drac-passwords.ini")

    if not config_file.exists():
        # Return default credentials if file doesn't exist
        return ("root", "calvin")

    config = configparser.ConfigParser()
    config.read(config_file)

    # Check for host-specific section first
    if hostname in config:
        username = config[hostname].get(
            "username", config["DEFAULT"].get("username", "root")
        )
        password = config[hostname].get(
            "password", config["DEFAULT"].get("password", "calvin")
        )
    else:
        # Use DEFAULT section
        username = config["DEFAULT"].get("username", "root")
        password = config["DEFAULT"].get("password", "calvin")

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
        # Build iDRAC FQDN
        idrac_fqdn = build_idrac_hostname(hostname)

        # Get credentials
        username, password = get_idrac_credentials(hostname)

        # Test SSH connectivity using sshpass
        cmd = [
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
            "getremoteservicesstatus",
        ]

        result = subprocess.run(  # nosec # nosemgrep
            cmd, capture_output=True, text=True, timeout=15  # nosemgrep
        )

        # Check if command succeeded and output contains "Status.*Ready"
        if result.returncode == 0:
            # Use regex to check for "Status.*Ready" pattern
            if re.search(r"Status.*Ready", result.stdout, re.IGNORECASE):
                return (True, f"iDRAC Access Succeeded for {idrac_fqdn}")
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
    systems = get_all_systems()

    # Convert systems to dictionaries for JSON serialization
    systems_data = [system_to_dict(s) for s in systems]

    # Extract unique BIOS and firmware versions for dropdowns
    bios_versions = sorted(set(s.bios_version for s in systems if s.bios_version))
    firmware_versions = sorted(set(s.idrac_version for s in systems if s.idrac_version))
    # Extract unique models (host types) for multi-select dropdown
    models = sorted(set(s.model for s in systems if s.model))

    # Add authentication status to template
    is_authenticated = session.get("authenticated", False)
    username = session.get("username", None)

    return render_template(
        "index.html",
        systems_json=json.dumps(systems_data),
        bios_versions_json=json.dumps(bios_versions),
        firmware_versions_json=json.dumps(firmware_versions),
        models_json=json.dumps(models),
        is_authenticated=is_authenticated,
        username=username,
        refresh_frequency=REFRESH_FREQUENCY,
        highlight_expired=HIGHLIGHT_EXPIRED,
        highlight_expiring=HIGHLIGHT_EXPIRING,
        default_page_size=DEFAULT_PAGE_SIZE,
        highlight_firmware=HIGHLIGHT_FIRMWARE,
        highlight_bios=HIGHLIGHT_BIOS,
        vnc_enabled=VNC_ENABLE,
        vnc_console_width=VNC_CONSOLE_WIDTH,
        vnc_console_height=VNC_CONSOLE_HEIGHT,
    )


@app.route("/api/systems")
def api_systems():
    """JSON API endpoint to get all systems."""
    systems = get_all_systems()
    systems_data = [system_to_dict(s) for s in systems]
    return jsonify(systems_data)


@app.route("/api/firmware-versions/<model>")
def api_firmware_versions(model):
    """Get unique firmware versions for systems matching the specified model."""
    try:
        # Check authentication
        if not session.get("authenticated", False):
            return (
                jsonify({"success": False, "message": "Authentication required"}),
                401,
            )

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
        # Check authentication
        if not session.get("authenticated", False):
            return (
                jsonify({"success": False, "message": "Authentication required"}),
                401,
            )

        # Get all systems with the specified model
        with get_session() as db_session:
            systems = db_session.query(System).filter(System.model == model).all()

        # Extract unique BIOS versions (excluding None/empty)
        bios_versions = sorted(set(s.bios_version for s in systems if s.bios_version))

        return jsonify({"success": True, "model": model, "versions": bios_versions})

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

        if username == ADMIN_USER and password == ADMIN_PASSWORD:
            session["authenticated"] = True
            session["username"] = username
            return jsonify({"success": True, "message": "Login successful"})
        else:
            return jsonify({"success": False, "message": "Invalid credentials"}), 401
    except Exception as e:
        return jsonify({"success": False, "message": f"Login error: {str(e)}"}), 400


@app.route("/logout", methods=["POST"])
def logout():
    """Handle logout request."""
    session.clear()
    return jsonify({"success": True, "message": "Logged out successfully"})


@app.route("/api/auth-status")
def auth_status():
    """Check if user is authenticated."""
    return jsonify(
        {
            "authenticated": session.get("authenticated", False),
            "username": session.get("username", None),
        }
    )


@app.route("/api/refresh", methods=["POST"])
def api_refresh():
    """Refresh warranty and system info for selected system."""
    try:
        # Check authentication
        if not session.get("authenticated", False):
            return (
                jsonify({"success": False, "message": "Authentication required"}),
                401,
            )

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
        if not session.get("authenticated", False):
            return (
                jsonify({"success": False, "message": "Authentication required"}),
                401,
            )

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
        # Check authentication
        if not session.get("authenticated", False):
            return (
                jsonify({"success": False, "message": "Authentication required"}),
                401,
            )

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
        if not session.get("authenticated", False):
            return (
                jsonify({"success": False, "message": "Authentication required"}),
                401,
            )

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
        if not session.get("authenticated", False):
            return (
                jsonify({"success": False, "message": "Authentication required"}),
                401,
            )

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
        # Check authentication
        if not session.get("authenticated", False):
            return (
                jsonify({"success": False, "message": "Authentication required"}),
                401,
            )

        data = request.get_json()
        if not data:
            return jsonify({"success": False, "message": "Invalid request"}), 400

        hostname = data.get("hostname", "").strip()

        if not hostname:
            return jsonify({"success": False, "message": "Hostname required"}), 400
        if not validate_hostname(hostname):
            return jsonify({"success": False, "message": "Invalid hostname"}), 400

        # Build iDRAC FQDN
        idrac_fqdn = build_idrac_hostname(hostname)

        # Get credentials
        username, password = get_idrac_credentials(hostname)

        # Build job queue command
        cmd = [
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
            "jobqueue",
            "view",
        ]

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

        # Build iDRAC FQDN
        idrac_fqdn = build_idrac_hostname(hostname)

        # Get credentials
        username, password = get_idrac_credentials(hostname)

        # Build clear job queue command
        cmd = [
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
            "jobqueue",
            "delete",
            "--all",
        ]

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
        if not session.get("authenticated", False):
            return (
                jsonify({"success": False, "message": "Authentication required"}),
                401,
            )

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
        if not session.get("authenticated", False):
            return (
                jsonify({"success": False, "message": "Authentication required"}),
                401,
            )

        systems = get_all_systems()
        total_systems = len(systems)

        if total_systems == 0:
            return jsonify({"success": False, "message": "No systems in database"}), 400

        from dracs.jobqueue import enqueue_batch

        count = enqueue_batch("refresh", "all")

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
        if not session.get("authenticated", False):
            return (
                jsonify({"success": False, "message": "Authentication required"}),
                401,
            )

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

        idrac_fqdn = build_idrac_hostname(hostname)
        username, password = get_idrac_credentials(hostname)

        cmd = [
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
            "serveraction",
            "powerstatus",
        ]

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
    VALID_ACTIONS = {"powerup", "powerdown", "graceshutdown"}

    try:
        if not session.get("authenticated", False):
            return (
                jsonify({"success": False, "message": "Authentication required"}),
                401,
            )

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

        idrac_fqdn = build_idrac_hostname(hostname)
        username, password = get_idrac_credentials(hostname)

        cmd = [
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
            "serveraction",
            action,
        ]

        result = subprocess.run(  # nosec # nosemgrep
            cmd, capture_output=True, text=True, timeout=30  # nosemgrep
        )

        if result.returncode == 0:
            action_label = {
                "powerup": "Power on",
                "powerdown": "Hard power off",
                "graceshutdown": "Graceful shutdown",
            }[action]
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
    if not session.get("authenticated", False):
        return (
            jsonify({"success": False, "message": "Authentication required"}),
            401,
        )

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
    if not session.get("authenticated", False):
        return (
            jsonify({"success": False, "message": "Authentication required"}),
            401,
        )

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
                shutil.copy2(exe_path, dest_path)
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


def _build_ssh_racadm_cmd(hostname: str, *racadm_args: str) -> list:
    idrac_fqdn = build_idrac_hostname(hostname)
    username, password = get_idrac_credentials(hostname)
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
        if not session.get("authenticated", False):
            return (
                jsonify({"success": False, "message": "Authentication required"}),
                401,
            )

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
        if not session.get("authenticated", False):
            return (
                jsonify({"success": False, "message": "Authentication required"}),
                401,
            )

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
        if not session.get("authenticated", False):
            return (
                jsonify({"success": False, "message": "Authentication required"}),
                401,
            )

        from dracs.jobqueue import get_active_jobs

        include_all = request.args.get("all", "false").lower() in (
            "true",
            "1",
            "yes",
        )
        jobs = get_active_jobs(include_completed=include_all)
        return jsonify({"success": True, "jobs": jobs})

    except Exception as e:
        return jsonify({"success": False, "message": f"Error: {str(e)}"}), 500


@app.route("/api/vnc-session", methods=["POST"])
def api_vnc_session_create():
    """Create a VNC console session for a host."""
    try:
        if not session.get("authenticated", False):
            return (
                jsonify({"success": False, "message": "Authentication required"}),
                401,
            )

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
        vnc_port, _ = get_vnc_credentials(hostname)

        reachable, error_msg = check_vnc_connectivity(idrac_fqdn, int(vnc_port))
        if not reachable:
            return (
                jsonify({"success": False, "message": error_msg}),
                503,
            )

        token = vnc_manager.create_session(hostname, idrac_fqdn, int(vnc_port))
        return jsonify({"success": True, "token": token})

    except MaxSessionsError as e:
        return jsonify({"success": False, "message": str(e)}), 429
    except Exception as e:
        return jsonify({"success": False, "message": f"Error: {str(e)}"}), 500


@app.route("/api/vnc-session/<token>", methods=["DELETE"])
def api_vnc_session_delete(token):
    """Destroy a VNC console session."""
    try:
        if not session.get("authenticated", False):
            return (
                jsonify({"success": False, "message": "Authentication required"}),
                401,
            )

        if not VNC_ENABLE or vnc_manager is None:
            return (
                jsonify({"success": False, "message": "VNC console is not enabled"}),
                404,
            )

        vnc_manager.remove_session(token)
        return jsonify({"success": True, "message": "Session closed"})

    except Exception as e:
        return jsonify({"success": False, "message": f"Error: {str(e)}"}), 500


@app.route("/console/<token>")
def console_view(token):
    """Serve the noVNC console viewer for a session."""
    if not session.get("authenticated", False):
        return (
            jsonify({"success": False, "message": "Authentication required"}),
            401,
        )

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

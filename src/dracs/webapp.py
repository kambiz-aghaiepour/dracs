"""Flask web application for DRACS inventory management."""

import asyncio
import configparser
from datetime import datetime
import json
import os
import re
import subprocess
from pathlib import Path
from dotenv import load_dotenv
from flask import Flask, render_template, jsonify, session, request

from dracs.db import db_initialize, get_session, System
from dracs.commands import refresh_dell_warranty
from dracs.snmp import build_idrac_hostname

# Load environment variables from .env file
# Look for .env in current directory or parent directories
env_path = Path('.env')
if env_path.exists():
    load_dotenv(env_path)
else:
    # Try to find .env in the project root
    project_root = Path(__file__).parent.parent.parent
    env_path = project_root / '.env'
    if env_path.exists():
        load_dotenv(env_path)


app = Flask(__name__)

# Secret key for sessions (use environment variable in production)
# Default key is only for development - change in production!
app.secret_key = os.environ.get(
    "FLASK_SECRET_KEY",
    "dev-secret-key-change-in-production-12345678901234567890123456789012"
)

# Session security settings
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'

# Load admin credentials from environment or use defaults
ADMIN_USER = os.environ.get("WEBADMIN_USER", "admin")
ADMIN_PASSWORD = os.environ.get("WEBADMIN_PASSWORD", "admin")

# Auto-refresh frequency (in seconds, 0 = disabled)
REFRESH_FREQUENCY = int(os.environ.get("REFRESH_FREQUENCY", "10"))

# Warranty expiration highlighting
HIGHLIGHT_EXPIRED = os.environ.get("HIGHLIGHT_EXPIRED", "true").lower() in ("true", "1", "yes")
HIGHLIGHT_EXPIRING = int(os.environ.get("HIGHLIGHT_EXPIRING", "30"))

# Pagination
DEFAULT_PAGE_SIZE = int(os.environ.get("DEFAULT_PAGE_SIZE", "20"))

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
    config_file = Path(__file__).parent.parent.parent / "drac-passwords.ini"

    if not config_file.exists():
        # Return default credentials if file doesn't exist
        return ("root", "calvin")

    config = configparser.ConfigParser()
    config.read(config_file)

    # Check for host-specific section first
    if hostname in config:
        username = config[hostname].get("username", config["DEFAULT"].get("username", "root"))
        password = config[hostname].get("password", config["DEFAULT"].get("password", "calvin"))
    else:
        # Use DEFAULT section
        username = config["DEFAULT"].get("username", "root")
        password = config["DEFAULT"].get("password", "calvin")

    return (username, password)


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

        # Open log file for writing
        with open(log_file_path, 'w') as log_file:
            log_file.write(f"Command started at: {datetime.now().isoformat()}\n")
            log_file.write(f"Command: {' '.join(cmd)}\n")
            log_file.write("-" * 80 + "\n\n")
            log_file.flush()

            # Start process in background
            # Use Popen instead of run to avoid blocking
            # Redirect stdout and stderr to log file
            subprocess.Popen(
                cmd,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                start_new_session=True  # Detach from parent process
            )

            # Process is now running in background
            # We don't wait for it to complete
            return True

    except Exception as e:
        # Log the error
        try:
            with open(log_file_path, 'a') as log_file:
                log_file.write(f"\nError starting process: {str(e)}\n")
        except Exception:
            pass
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
    config_file = Path(__file__).parent.parent.parent / "BIOS-filename.ini"

    if not config_file.exists():
        return None

    config = configparser.ConfigParser()
    config.read(config_file)

    # Check if model section exists
    if model not in config:
        return None

    # Get filename for the BIOS version
    return config[model].get(bios_version, None)


def test_idrac_connectivity(hostname: str) -> tuple:
    """
    Test SSH connectivity to the iDRAC interface.

    Args:
        hostname: The system hostname

    Returns:
        tuple: (success: bool, message: str)
    """
    try:
        # Build iDRAC FQDN
        idrac_fqdn = build_idrac_hostname(hostname)

        # Get credentials
        username, password = get_idrac_credentials(hostname)

        # Test SSH connectivity using sshpass
        cmd = [
            "sshpass",
            "-p", password,
            "ssh",
            "-o", "StrictHostKeyChecking=no",
            "-o", "UserKnownHostsFile=/dev/null",
            "-o", "ConnectTimeout=10",
            f"{username}@{idrac_fqdn}",
            "racadm", "getremoteservicesstatus"
        ]

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=15
        )

        # Check if command succeeded and output contains "Status.*Ready"
        if result.returncode == 0:
            # Use regex to check for "Status.*Ready" pattern
            if re.search(r'Status.*Ready', result.stdout, re.IGNORECASE):
                return (True, f"iDRAC Access Succeeded for {idrac_fqdn}")
            else:
                return (False, f"iDRAC responded but status not ready: {result.stdout[:100]}")
        else:
            return (False, f"iDRAC Access Failed: {result.stderr[:100] if result.stderr else 'Connection failed'}")

    except subprocess.TimeoutExpired:
        return (False, "iDRAC Access Failed: Connection timeout")
    except FileNotFoundError:
        return (False, "iDRAC Access Failed: sshpass command not found (please install sshpass)")
    except Exception as e:
        return (False, f"iDRAC Access Failed: {str(e)}")


@app.route("/")
def index():
    """Main page with inventory table and filters."""
    systems = get_all_systems()

    # Convert systems to dictionaries for JSON serialization
    systems_data = [system_to_dict(s) for s in systems]

    # Extract unique BIOS and firmware versions for dropdowns
    bios_versions = sorted(set(
        s.bios_version for s in systems if s.bios_version
    ))
    firmware_versions = sorted(set(
        s.idrac_version for s in systems if s.idrac_version
    ))
    # Extract unique models (host types) for multi-select dropdown
    models = sorted(set(
        s.model for s in systems if s.model
    ))

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
            return jsonify({"success": False, "message": "Authentication required"}), 401

        # Get all systems with the specified model
        with get_session() as db_session:
            systems = db_session.query(System).filter(System.model == model).all()

        # Extract unique firmware versions (excluding None/empty)
        firmware_versions = sorted(set(
            s.idrac_version for s in systems
            if s.idrac_version
        ))

        return jsonify({
            "success": True,
            "model": model,
            "versions": firmware_versions
        })

    except Exception as e:
        return jsonify({"success": False, "message": f"Error: {str(e)}"}), 500


@app.route("/api/bios-versions/<model>")
def api_bios_versions(model):
    """Get unique BIOS versions for systems matching the specified model."""
    try:
        # Check authentication
        if not session.get("authenticated", False):
            return jsonify({"success": False, "message": "Authentication required"}), 401

        # Get all systems with the specified model
        with get_session() as db_session:
            systems = db_session.query(System).filter(System.model == model).all()

        # Extract unique BIOS versions (excluding None/empty)
        bios_versions = sorted(set(
            s.bios_version for s in systems
            if s.bios_version
        ))

        return jsonify({
            "success": True,
            "model": model,
            "versions": bios_versions
        })

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
    return jsonify({
        "authenticated": session.get("authenticated", False),
        "username": session.get("username", None)
    })


@app.route("/api/refresh", methods=["POST"])
def api_refresh():
    """Refresh warranty and system info for selected system."""
    try:
        # Check authentication
        if not session.get("authenticated", False):
            return jsonify({"success": False, "message": "Authentication required"}), 401

        data = request.get_json()
        if not data:
            return jsonify({"success": False, "message": "Invalid request"}), 400

        service_tag = data.get("service_tag", "").strip() if data.get("service_tag") else None
        hostname = data.get("hostname", "").strip() if data.get("hostname") else None

        if not service_tag and not hostname:
            return jsonify({"success": False, "message": "Service tag or hostname required"}), 400

        # Run async refresh function
        asyncio.run(refresh_dell_warranty(
            service_tag=service_tag,
            hostname=hostname if not service_tag else None,
            warranty=DB_PATH
        ))

        return jsonify({
            "success": True,
            "message": f"Successfully refreshed data for {service_tag or hostname}"
        })

    except Exception as e:
        return jsonify({"success": False, "message": f"Error: {str(e)}"}), 500


@app.route("/api/refresh-multiple", methods=["POST"])
def api_refresh_multiple():
    """Refresh warranty and system info for multiple systems."""
    try:
        # Check authentication
        if not session.get("authenticated", False):
            return jsonify({"success": False, "message": "Authentication required"}), 401

        data = request.get_json()
        if not data:
            return jsonify({"success": False, "message": "Invalid request"}), 400

        systems = data.get("systems", [])
        if not systems:
            return jsonify({"success": False, "message": "No systems provided"}), 400

        # Refresh each system
        success_count = 0
        failed_systems = []

        for system in systems:
            service_tag = system.get("service_tag", "").strip() if system.get("service_tag") else None
            hostname = system.get("hostname", "").strip() if system.get("hostname") else None

            if not service_tag and not hostname:
                continue

            try:
                asyncio.run(refresh_dell_warranty(
                    service_tag=service_tag,
                    hostname=hostname if not service_tag else None,
                    warranty=DB_PATH
                ))
                success_count += 1
            except Exception as e:
                failed_systems.append(f"{service_tag or hostname}: {str(e)}")

        message = f"Successfully refreshed {success_count} of {len(systems)} systems"
        if failed_systems:
            message += f". Failed: {', '.join(failed_systems[:3])}"
            if len(failed_systems) > 3:
                message += f" and {len(failed_systems) - 3} more"

        return jsonify({
            "success": True,
            "message": message,
            "refreshed": success_count,
            "total": len(systems)
        })

    except Exception as e:
        return jsonify({"success": False, "message": f"Error: {str(e)}"}), 500


@app.route("/api/test-idrac", methods=["POST"])
def api_test_idrac():
    """Test SSH connectivity to the iDRAC interface."""
    try:
        # Check authentication
        if not session.get("authenticated", False):
            return jsonify({"success": False, "message": "Authentication required"}), 401

        data = request.get_json()
        if not data:
            return jsonify({"success": False, "message": "Invalid request"}), 400

        hostname = data.get("hostname", "").strip()
        if not hostname:
            return jsonify({"success": False, "message": "Hostname required"}), 400

        # Test iDRAC connectivity
        success, message = test_idrac_connectivity(hostname)

        return jsonify({
            "success": success,
            "message": message
        })

    except Exception as e:
        return jsonify({"success": False, "message": f"Error: {str(e)}"}), 500


@app.route("/api/firmware-update", methods=["POST"])
def api_firmware_update():
    """Execute firmware update on iDRAC via SSH."""
    try:
        # Check authentication
        if not session.get("authenticated", False):
            return jsonify({"success": False, "message": "Authentication required"}), 401

        data = request.get_json()
        if not data:
            return jsonify({"success": False, "message": "Invalid request"}), 400

        hostname = data.get("hostname", "").strip()
        target_version = data.get("target_version", "").strip()
        model = data.get("model", "").strip()

        if not hostname or not target_version or not model:
            return jsonify({"success": False, "message": "Hostname, target version, and model required"}), 400

        # Get FTP server from environment
        ftp_server = os.environ.get("DRACS_FTP_SERVER")
        if not ftp_server:
            return jsonify({"success": False, "message": "DRACS_FTP_SERVER environment variable not set"}), 500

        # Build iDRAC FQDN
        idrac_fqdn = build_idrac_hostname(hostname)

        # Get credentials
        username, password = get_idrac_credentials(hostname)

        # Build firmware filename: MODEL-TARGET_VERSION.d9
        firmware_file = f"{model}-{target_version}.d9"

        # Prepare log file path
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        log_dir = Path("logs/firmware-updates")
        log_file = log_dir / f"{hostname}_{target_version}_{timestamp}.log"

        # Build firmware update command
        cmd = [
            "sshpass",
            "-p", password,
            "ssh",
            "-o", "StrictHostKeyChecking=no",
            "-o", "UserKnownHostsFile=/dev/null",
            "-o", "ConnectTimeout=10",
            f"{username}@{idrac_fqdn}",
            "racadm", "fwupdate", "-f", ftp_server, "ftp", "user", "-d", f"pub/{firmware_file}"
        ]

        # Run firmware update command in background
        success = run_command_background(cmd, str(log_file))

        if success:
            return jsonify({
                "success": True,
                "message": f"Firmware update initiated for {hostname} to version {target_version}. Check logs/{log_file.relative_to('logs')} for progress."
            })
        else:
            return jsonify({
                "success": False,
                "message": f"Failed to start firmware update process. Check {log_file} for details."
            })

    except FileNotFoundError:
        return jsonify({"success": False, "message": "sshpass command not found"}), 500
    except Exception as e:
        return jsonify({"success": False, "message": f"Error: {str(e)}"}), 500


@app.route("/api/bios-update", methods=["POST"])
def api_bios_update():
    """Execute BIOS update on iDRAC via SSH."""
    try:
        # Check authentication
        if not session.get("authenticated", False):
            return jsonify({"success": False, "message": "Authentication required"}), 401

        data = request.get_json()
        if not data:
            return jsonify({"success": False, "message": "Invalid request"}), 400

        hostname = data.get("hostname", "").strip()
        target_bios = data.get("target_bios", "").strip()
        model = data.get("model", "").strip()

        if not hostname or not target_bios or not model:
            return jsonify({"success": False, "message": "Hostname, target BIOS version, and model required"}), 400

        # Get NFS server and path from environment
        nfs_server = os.environ.get("DRACS_NFS_SERVER")
        nfs_path = os.environ.get("DRACS_NFS_PATH")
        if not nfs_server or not nfs_path:
            return jsonify({"success": False, "message": "DRACS_NFS_SERVER or DRACS_NFS_PATH environment variable not set"}), 500

        # Look up BIOS filename
        nfs_filename = get_bios_filename(model, target_bios)
        if not nfs_filename:
            return jsonify({
                "success": False,
                "message": f"BIOS filename not found for model {model} version {target_bios} in BIOS-filename.ini"
            }), 400

        # Build iDRAC FQDN
        idrac_fqdn = build_idrac_hostname(hostname)

        # Get credentials
        username, password = get_idrac_credentials(hostname)

        # Build NFS path: DRACS_NFS_SERVER:DRACS_NFS_PATH/MODEL
        nfs_location = f"{nfs_server}:{nfs_path}/{model}"

        # Prepare log file path
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        log_dir = Path("logs/bios-updates")
        log_file = log_dir / f"{hostname}_{target_bios}_{timestamp}.log"

        # Build BIOS update command
        cmd = [
            "sshpass",
            "-p", password,
            "ssh",
            "-o", "StrictHostKeyChecking=no",
            "-o", "UserKnownHostsFile=/dev/null",
            "-o", "ConnectTimeout=10",
            f"{username}@{idrac_fqdn}",
            "racadm", "update", "-f", nfs_filename, "-l", nfs_location
        ]

        # Run BIOS update command in background
        success = run_command_background(cmd, str(log_file))

        if success:
            return jsonify({
                "success": True,
                "message": f"BIOS update initiated for {hostname} to version {target_bios}. Check logs/{log_file.relative_to('logs')} for progress."
            })
        else:
            return jsonify({
                "success": False,
                "message": f"Failed to start BIOS update process. Check {log_file} for details."
            })

    except FileNotFoundError:
        return jsonify({"success": False, "message": "sshpass command not found"}), 500
    except Exception as e:
        return jsonify({"success": False, "message": f"Error: {str(e)}"}), 500


@app.route("/api/refresh-all", methods=["POST"])
def api_refresh_all():
    """Refresh warranty and system info for all systems in database."""
    try:
        # Check authentication
        if not session.get("authenticated", False):
            return jsonify({"success": False, "message": "Authentication required"}), 401

        # Get all systems from database
        systems = get_all_systems()
        total_systems = len(systems)

        if total_systems == 0:
            return jsonify({"success": False, "message": "No systems in database"}), 400

        # Refresh each system
        success_count = 0
        failed_systems = []

        for system in systems:
            try:
                asyncio.run(refresh_dell_warranty(
                    service_tag=system.svc_tag,
                    hostname=None,
                    warranty=DB_PATH
                ))
                success_count += 1
            except Exception as e:
                failed_systems.append(f"{system.svc_tag}: {str(e)}")

        message = f"Successfully refreshed {success_count} of {total_systems} systems"
        if failed_systems:
            message += f". Failed: {', '.join(failed_systems[:3])}"
            if len(failed_systems) > 3:
                message += f" and {len(failed_systems) - 3} more"

        return jsonify({
            "success": True,
            "message": message,
            "refreshed": success_count,
            "total": total_systems
        })

    except Exception as e:
        return jsonify({"success": False, "message": f"Error: {str(e)}"}), 500


if __name__ == "__main__":
    # Development server (use gunicorn for production)
    app.run(host="0.0.0.0", port=1888, debug=True)

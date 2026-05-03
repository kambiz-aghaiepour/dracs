"""Flask web application for DRACS inventory management."""

import asyncio
import json
import os
import secrets
from pathlib import Path
from dotenv import load_dotenv
from flask import Flask, render_template, jsonify, session, request

from dracs.db import db_initialize, get_session, System
from dracs.commands import refresh_dell_warranty

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

"""Entry point for launching the DRACS web application via gunicorn."""

import os
import shutil
import sys
from pathlib import Path

from dotenv import load_dotenv

REQUIRED_ENV_VARS = [
    "CLIENT_ID",
    "CLIENT_SECRET",
    "TOKEN_URL",
    "FLASK_SECRET_KEY",
    "WEBADMIN_USER",
    "WEBADMIN_PASSWORD",
    "DRACS_DNS_STRING",
    "DRACS_DNS_MODE",
]

OPTIONAL_ENV_DEFAULTS = {
    "DRACS_DB": "./warranty.db",
    "REFRESH_FREQUENCY": "10",
    "HIGHLIGHT_EXPIRED": "true",
    "HIGHLIGHT_EXPIRING": "30",
    "DEFAULT_PAGE_SIZE": "20",
    "HIGHLIGHT_FIRMWARE": "true",
    "HIGHLIGHT_BIOS": "true",
    "SNMP_COMMUNITY": "public",
    "DEBUG": "false",
    "DRACS_BIND": "127.0.0.1:1888",
}


def get_gunicorn_conf_path() -> Path:
    return Path(__file__).parent / "gunicorn.conf.py"


def validate_env() -> list[str]:
    """Return list of missing required environment variables."""
    return [var for var in REQUIRED_ENV_VARS if not os.environ.get(var)]


def apply_optional_defaults():
    """Set optional environment variables to defaults if not already set."""
    for var, default in OPTIONAL_ENV_DEFAULTS.items():
        if not os.environ.get(var):
            os.environ[var] = default


def main():  # pragma: no cover
    env_path = Path(".env")
    if env_path.exists():
        load_dotenv(env_path)

    missing = validate_env()
    if missing:
        print("Error: required environment variables not set:", file=sys.stderr)
        for var in missing:
            print(f"  - {var}", file=sys.stderr)
        print(
            "\nPlease configure these in your .env file.",
            file=sys.stderr,
        )
        sys.exit(1)

    apply_optional_defaults()

    conf_path = get_gunicorn_conf_path()
    if not conf_path.exists():
        print(
            f"Error: gunicorn config not found at {conf_path}",
            file=sys.stderr,
        )
        sys.exit(1)

    db_path = os.environ.get("DRACS_DB", "./warranty.db")
    print("Starting DRACS web application...")
    print(f"Database: {db_path}")
    print("Server: http://127.0.0.1:1888")
    print()

    gunicorn_path = shutil.which("gunicorn")
    if not gunicorn_path:
        print("Error: gunicorn not found in PATH.", file=sys.stderr)
        sys.exit(1)

    os.execvp(  # noqa  # nosec  # nosemgrep
        gunicorn_path, ["gunicorn", "-c", str(conf_path), "dracs.webapp:app"]
    )

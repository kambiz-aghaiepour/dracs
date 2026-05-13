#!/bin/bash
# Launch script for DRACS web application

# Change to project directory
cd "$(dirname "$0")"

# Require .env file
if [ ! -f .env ]; then
    echo "Error: .env file not found in $(pwd)" >&2
    echo "Copy .env.example to .env and configure it before starting." >&2
    exit 1
fi

# Load .env file
echo "Loading settings from .env file..."
set -a
source .env
set +a

# Check if virtualenv is activated
if [ -z "$VIRTUAL_ENV" ]; then
    echo "Error: No virtualenv activated!" >&2
    echo "Please run: uv sync && source .venv/bin/activate" >&2
    exit 1
fi

# Validate required environment variables
MISSING=""
for VAR in CLIENT_ID CLIENT_SECRET TOKEN_URL FLASK_SECRET_KEY WEBADMIN_USER WEBADMIN_PASSWORD DRACS_DNS_STRING DRACS_DNS_MODE; do
    if [ -z "${!VAR}" ]; then
        MISSING="$MISSING  - $VAR\n"
    fi
done

if [ -n "$MISSING" ]; then
    echo "Error: required environment variables not set:" >&2
    echo -e "$MISSING" >&2
    echo "Please configure these in your .env file." >&2
    exit 1
fi

# Set optional defaults
export DRACS_DB="${DRACS_DB:-./warranty.db}"
export REFRESH_FREQUENCY="${REFRESH_FREQUENCY:-10}"
export HIGHLIGHT_EXPIRED="${HIGHLIGHT_EXPIRED:-true}"
export HIGHLIGHT_EXPIRING="${HIGHLIGHT_EXPIRING:-30}"
export DEFAULT_PAGE_SIZE="${DEFAULT_PAGE_SIZE:-20}"
export HIGHLIGHT_FIRMWARE="${HIGHLIGHT_FIRMWARE:-true}"
export HIGHLIGHT_BIOS="${HIGHLIGHT_BIOS:-true}"
export SNMP_COMMUNITY="${SNMP_COMMUNITY:-public}"
export DEBUG="${DEBUG:-false}"

echo "Starting DRACS web application..."
echo "Using virtualenv: $VIRTUAL_ENV"
echo "Database: $DRACS_DB"
echo "Server: http://127.0.0.1:1888"
echo ""

# Launch gunicorn from activated virtualenv
gunicorn -c gunicorn.conf.py "dracs.webapp:app"

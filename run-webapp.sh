#!/bin/bash
# Launch script for DRACS web application

# Change to project directory
cd "$(dirname "$0")"

# Load .env file if it exists (so local settings take precedence over defaults below)
if [ -f .env ]; then
    echo "Loading settings from .env file..."
    # Export all variables from .env
    set -a
    source .env
    set +a
fi

# Set database path (default to current directory)
export DRACS_DB="${DRACS_DB:-./warranty.db}"

# Check if virtualenv is activated
if [ -z "$VIRTUAL_ENV" ]; then
    echo "Warning: No virtualenv activated!"
    echo "Please run: uv sync && source .venv/bin/activate"
    echo "Or use: uv run gunicorn -c gunicorn.conf.py dracs.webapp:app"
    exit 1
fi

# Set Flask secret key from gunicorn config (unless already set)
if [ -z "$FLASK_SECRET_KEY" ]; then
    export FLASK_SECRET_KEY="dev-secret-key-change-in-production-12345678901234567890123456789012"
fi

# Set admin credentials from gunicorn config (unless already set)
# Note: .env file values (loaded above) take precedence over these defaults
if [ -z "$WEBADMIN_USER" ]; then
    export WEBADMIN_USER="admin"
fi
if [ -z "$WEBADMIN_PASSWORD" ]; then
    export WEBADMIN_PASSWORD="admin"
fi

# Set refresh frequency from gunicorn config (unless already set)
if [ -z "$REFRESH_FREQUENCY" ]; then
    export REFRESH_FREQUENCY="10"
fi

# Set warranty expiration highlighting from gunicorn config (unless already set)
if [ -z "$HIGHLIGHT_EXPIRED" ]; then
    export HIGHLIGHT_EXPIRED="true"
fi
if [ -z "$HIGHLIGHT_EXPIRING" ]; then
    export HIGHLIGHT_EXPIRING="30"
fi

# Set pagination from gunicorn config (unless already set)
if [ -z "$DEFAULT_PAGE_SIZE" ]; then
    export DEFAULT_PAGE_SIZE="20"
fi

# Set firmware and BIOS highlighting from gunicorn config (unless already set)
if [ -z "$HIGHLIGHT_FIRMWARE" ]; then
    export HIGHLIGHT_FIRMWARE="true"
fi
if [ -z "$HIGHLIGHT_BIOS" ]; then
    export HIGHLIGHT_BIOS="true"
fi

echo "Starting DRACS web application..."
echo "Using virtualenv: $VIRTUAL_ENV"
echo "Database: $DRACS_DB"
echo "Server: http://0.0.0.0:1888"
echo ""

# Launch gunicorn from activated virtualenv
gunicorn -c gunicorn.conf.py "dracs.webapp:app"

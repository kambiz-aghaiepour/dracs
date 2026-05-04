"""Gunicorn configuration for DRACS web application."""

# Server socket
bind = "0.0.0.0:1888"

# Worker processes
workers = 4
worker_class = "sync"
threads = 1

# Logging
accesslog = "-"  # Log to stdout
errorlog = "-"   # Log to stderr
loglevel = "info"

# Application
wsgi_app = "dracs.webapp:app"

# Process naming
proc_name = "dracs-webapp"

# Timeout (increased for refresh operations which involve SNMP + API calls)
timeout = 120

# Daemon mode (set to False to run in foreground)
daemon = False

# Admin credentials for web interface
webadmin_user = "admin"
webadmin_password = "admin"  # TODO: Change in production

# Flask secret key for sessions (generate with: python -c "import secrets; print(secrets.token_hex(32))")
# TODO: Change this in production!
flask_secret_key = "dev-secret-key-change-in-production-12345678901234567890123456789012"

# Auto-refresh frequency for webapp display (in seconds)
# Set to 0 to disable auto-refresh
refresh_frequency = 10

# Warranty expiration highlighting
# Highlight systems with expired warranties in red (true/false)
highlight_expired = True

# Highlight systems expiring within this many days in yellow
# Set to 0 or negative to disable expiring-soon highlighting
highlight_expiring = 30

# Pagination
# Default number of systems to display per page
default_page_size = 20

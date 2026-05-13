"""Gunicorn configuration for DRACS web application."""

import os

# Server socket
bind = os.environ.get("DRACS_BIND", "127.0.0.1:1888")

# Worker processes
workers = 4
worker_class = "sync"
threads = 1

# Logging
accesslog = "-"  # Log to stdout
errorlog = "-"  # Log to stderr
loglevel = "info"

# Application
wsgi_app = "dracs.webapp:app"

# Process naming
proc_name = "dracs-webapp"

# Timeout (increased for refresh operations which involve SNMP + API calls)
timeout = 120

# Daemon mode (set to False to run in foreground)
daemon = False

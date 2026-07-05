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


def on_starting(server):
    """Start websockify for VNC console support and conserver for IPMI SOL."""
    if os.environ.get("VNC_ENABLE", "false").lower() in ("true", "1", "yes"):
        from dracs.vnc import get_token_dir, start_websockify

        port = int(os.environ.get("VNC_WEBSOCKIFY_PORT", "6080"))
        start_websockify(port, get_token_dir())

    if os.environ.get("SOL_ENABLE", "false").lower() in ("true", "1", "yes"):
        import threading
        from pathlib import Path

        from dracs.sol import startup as sol_startup

        threading.Thread(
            target=sol_startup,
            args=(
                os.environ.get("DRACS_DB", "./warranty.db"),
                None,
                Path(os.environ.get("SOL_CONSERVER_CF", "/etc/dracs/conserver.cf")),
                Path(os.environ.get("SOL_CONSERVER_PASSWD", "/etc/dracs/conserver.passwd")),
                Path(os.environ.get("SOL_CONSERVER_LOGDIR", "/var/log/dracs/conserver")),
            ),
            daemon=True,
        ).start()


def post_worker_init(worker):
    """Start job processor in exactly one gunicorn worker using a file lock."""
    import fcntl

    lock_path = os.environ.get(
        "JOB_PROCESSOR_LOCK", "/var/lib/dracs/.job_processor.lock"
    )
    try:
        lock_dir = os.path.dirname(lock_path)
        if lock_dir:
            os.makedirs(lock_dir, exist_ok=True)
        lock_file = open(lock_path, "w")
        fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)

        from dracs.jobqueue import JobProcessor, JobScheduler, recover_stale_jobs

        recover_stale_jobs()

        max_workers = int(os.environ.get("JOB_MAX_WORKERS", "50"))
        processor = JobProcessor(max_workers=max_workers)
        processor.start()

        schedule_path = os.environ.get(
            "DRACS_SCHEDULE_CONFIG", "/etc/dracs/schedule.ini"
        )
        scheduler = JobScheduler(config_path=schedule_path)
        scheduler.start()

        from dracs.config_collector import ConfigCollector, set_instance as _set_cc

        config_collector = ConfigCollector()
        config_collector.start()
        _set_cc(config_collector)

        worker._job_processor = processor
        worker._job_scheduler = scheduler
        worker._config_collector = config_collector
        worker._job_lock_file = lock_file
    except (IOError, OSError):
        pass


def on_exit(server):
    """Stop websockify and conserver on shutdown."""
    from dracs.vnc import stop_websockify

    stop_websockify()

    if os.environ.get("SOL_ENABLE", "false").lower() in ("true", "1", "yes"):
        from dracs.sol import stop_conserver

        stop_conserver()

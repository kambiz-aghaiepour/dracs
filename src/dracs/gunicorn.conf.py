"""Gunicorn configuration for DRACS web application."""

import logging
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

# Graceful shutdown: force-kill workers after this many seconds on SIGTERM.
graceful_timeout = 3

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
                Path(
                    os.environ.get(
                        "SOL_CONSERVER_PASSWD", "/etc/dracs/conserver.passwd"
                    )
                ),
                Path(
                    os.environ.get("SOL_CONSERVER_LOGDIR", "/var/log/dracs/conserver")
                ),
            ),
            daemon=True,
        ).start()


def _stale_holder_pid(lock_path: str, max_age_secs: float):
    """PID of the alive lock holder whose heartbeat is stale, else None."""
    from dracs.jobqueue import heartbeat_stale

    try:
        holder_pid = int(open(lock_path).read().strip())
    except (OSError, ValueError):
        return None
    try:
        os.kill(holder_pid, 0)
    except OSError:
        return None  # holder gone; flock will be released
    if not heartbeat_stale(lock_path, max_age_secs):
        return None
    return holder_pid


def _start_processor_watchdog(lock_path: str) -> None:
    """Log when the job processor lock holder stops heartbeating.

    The flock itself survives a hung holder, so a stale heartbeat is the
    only signal that the single processor is stuck (incident 2026-10-26:
    three weeks with no job processed).
    """
    import threading
    import time

    heartbeat_max_age = float(os.environ.get("JOB_PROCESSOR_HEARTBEAT_MAX_AGE", "120"))

    def watch() -> None:
        while True:
            time.sleep(30)
            holder_pid = _stale_holder_pid(lock_path, heartbeat_max_age)
            if holder_pid is None:
                continue
            logging.getLogger("dracs").error(
                "Job processor heartbeat stale (holder pid %s): processor "
                "appears hung; inspect and restart dracs-webapp",
                holder_pid,
            )

    threading.Thread(target=watch, daemon=True).start()


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
        lock_file.write(f"{os.getpid()}\n")
        lock_file.flush()

        from dracs.jobqueue import (
            JobProcessor,
            JobScheduler,
            recover_stale_jobs,
        )

        recover_stale_jobs()

        max_workers = int(os.environ.get("JOB_MAX_WORKERS", "50"))
        processor = JobProcessor(max_workers=max_workers, heartbeat_path=lock_path)
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
    except (IOError, OSError) as exc:
        logging.getLogger("dracs").warning(
            "Failed to start job processor in this worker " "(lock %s): %s",
            lock_path,
            exc,
        )
    finally:
        _start_processor_watchdog(lock_path)


def on_exit(server):
    """Stop websockify and conserver on shutdown."""
    from dracs.vnc import stop_websockify

    stop_websockify()

    if os.environ.get("SOL_ENABLE", "false").lower() in ("true", "1", "yes"):
        from dracs.sol import stop_conserver

        stop_conserver()

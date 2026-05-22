"""Audit logging for DRACS admin actions."""

import logging
import os
import sys
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler

_audit_logger = logging.getLogger("dracs.audit")
_audit_logger.propagate = False

_INITIALIZED = False


def _init_audit_logger() -> None:
    global _INITIALIZED
    if _INITIALIZED:
        return
    _INITIALIZED = True

    log_dir = os.environ.get("DRACS_LOG_DIR", "logs")
    log_path = os.path.join(log_dir, "audit.log")

    try:
        os.makedirs(log_dir, exist_ok=True)
        handler = RotatingFileHandler(
            log_path,
            maxBytes=10 * 1024 * 1024,
            backupCount=5,
        )
        handler.setFormatter(logging.Formatter("%(message)s"))
        _audit_logger.addHandler(handler)
        _audit_logger.setLevel(logging.INFO)
    except OSError:
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(logging.Formatter("AUDIT: %(message)s"))
        _audit_logger.addHandler(handler)
        _audit_logger.setLevel(logging.INFO)


def audit_log(
    action: str,
    target: str = "",
    user: str = "",
    source: str = "",
    details: str = "",
    result: str = "success",
) -> None:
    _init_audit_logger()
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    parts = [
        timestamp,
        f"user={user or '-'}",
        f"source={source or '-'}",
        f"action={action}",
        f"target={target or '-'}",
        f"result={result}",
    ]
    if details:
        parts.append(f"details={details}")
    _audit_logger.info(" ".join(parts))

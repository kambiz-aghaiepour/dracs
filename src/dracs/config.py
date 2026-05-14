"""Shared configuration loading for DRACS CLI and webapp."""

import getpass
import sys
from pathlib import Path

from dotenv import load_dotenv

SYSTEM_CONFIG = Path("/etc/dracs/dracs.conf")


def load_config():
    """Load configuration from system config and CWD .env file.

    Precedence: CWD .env overrides /etc/dracs/dracs.conf.
    """
    if SYSTEM_CONFIG.exists():
        load_dotenv(SYSTEM_CONFIG, override=False)
    elif getpass.getuser() == "dracs":
        print(
            f"Warning: could not read configuration {SYSTEM_CONFIG}",
            file=sys.stderr,
        )

    env_path = Path(".env")
    if env_path.exists():
        load_dotenv(env_path, override=True)

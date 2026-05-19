import sys
from pathlib import Path
from typing import Optional

DRACSRC_PATH = Path.home() / ".dracsrc"


def load_server_config(server_override: Optional[str] = None) -> str:
    if server_override:
        return server_override.strip()

    if DRACSRC_PATH.exists():
        for line in DRACSRC_PATH.read_text().splitlines():
            line = line.strip()
            if line.startswith("#") or not line:
                continue
            if line.startswith("dracs_server:"):
                value = line.split(":", 1)[1].strip()
                if value:
                    return value

    if sys.stdin.isatty():
        try:
            server = input("DRACS server hostname: ").strip()
            if server:
                return server
        except (EOFError, KeyboardInterrupt):
            print()

    print(
        "Error: No DRACS server configured.\n"
        "Set 'dracs_server: <FQDN>' in ~/.dracsrc or use -s/--server.",
        file=sys.stderr,
    )
    sys.exit(1)

import re
from pathlib import Path
from typing import List, Optional

from dracs.exceptions import ValidationError


def validate_service_tag(svctag: Optional[str]) -> bool:
    """
    Validates Dell service tag format.
    Service tags are typically 7 alphanumeric characters.
    """
    if not svctag or not isinstance(svctag, str):
        return False
    if not re.match(r"^[A-Z0-9]{5,7}$", svctag):
        return False
    return True


def validate_hostname(hostname: Optional[str]) -> bool:
    """
    Validates hostname format (DNS-safe characters).
    """
    if not hostname or not isinstance(hostname, str):
        return False
    if len(hostname) > 253:
        return False
    pattern = (
        r"^[a-zA-Z0-9]([a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?"
        r"(\.[a-zA-Z0-9]([a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?)*$"
    )
    if not re.match(pattern, hostname):
        return False
    return True


def read_host_list(filepath: str) -> List[str]:
    """
    Reads a plain text file containing one hostname per line.
    Strips whitespace and skips empty lines and comments.
    """
    path = Path(filepath)
    if not path.is_file():
        raise ValidationError(f"Host list file not found: {filepath}")

    hosts = []
    for line in path.read_text().splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            if not validate_hostname(stripped):
                raise ValidationError(
                    f"Invalid hostname in host list: {stripped}. "
                    "Hostnames should contain only letters, numbers, "
                    "hyphens, and periods"
                )
            hosts.append(stripped)

    if not hosts:
        raise ValidationError(f"Host list file is empty: {filepath}")

    return hosts


def validate_site_name(name: Optional[str]) -> bool:
    if not name or not isinstance(name, str):
        return False
    if len(name) > 32:
        return False
    if not re.match(r"^[a-zA-Z0-9]+$", name):
        return False
    return True


def validate_version(version: Optional[str]) -> bool:
    """
    Validates version string format (e.g., 2.1.0).
    """
    if not version or not isinstance(version, str):
        return False
    if not re.match(r"^\d+(\.\d+)*$", version):
        return False
    return True

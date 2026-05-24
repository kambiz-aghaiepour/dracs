"""Site management utilities for multi-site INI configuration."""

import configparser
import logging
import shutil
from pathlib import Path

logger = logging.getLogger(__name__)


def _parse_explicit_keys(config_path: Path) -> dict:
    """Parse INI file to extract only explicitly set keys per section."""
    sections: dict = {}
    current_section = None

    for line in config_path.read_text().splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith(("#", ";")):
            continue
        if stripped.startswith("[") and stripped.endswith("]"):
            current_section = stripped[1:-1]
            if current_section.upper() != "DEFAULT":
                sections.setdefault(current_section, {})
            continue
        if current_section and current_section.upper() != "DEFAULT":
            if "=" in stripped:
                key, _, value = stripped.partition("=")
                sections[current_section][key.strip().lower()] = value.strip()
            elif ":" in stripped:
                key, _, value = stripped.partition(":")
                sections[current_section][key.strip().lower()] = value.strip()

    return sections


def _find_passwords_ini() -> Path | None:
    config_file = Path("drac-passwords.ini")
    if config_file.exists():
        return config_file
    config_file = Path("/etc/dracs/drac-passwords.ini")
    if config_file.exists():
        return config_file
    return None


def _is_old_format(config: configparser.RawConfigParser) -> bool:
    for section in config.sections():
        if "-" not in section:
            return True
    if config.defaults():
        return True
    return False


def migrate_passwords_ini(config_path: Path | None = None) -> bool:
    if config_path is None:
        config_path = _find_passwords_ini()
    if config_path is None:
        return False

    config = configparser.RawConfigParser()
    config.read(config_path)

    if not _is_old_format(config):
        return False

    backup_path = config_path.with_suffix(config_path.suffix + ".bak")
    shutil.copy2(str(config_path), str(backup_path))
    logger.info("Backed up %s to %s", config_path, backup_path)

    section_keys = _parse_explicit_keys(config_path)

    new_config = configparser.RawConfigParser()

    defaults = dict(config.defaults())
    if defaults:
        new_config.add_section("Default-DEFAULTS")
        for key, value in defaults.items():
            new_config.set("Default-DEFAULTS", key, value)

    for section in config.sections():
        if "-" in section:
            new_section = section
        else:
            new_section = f"Default-{section}"
        new_config.add_section(new_section)
        explicit = section_keys.get(section, {})
        for key, value in explicit.items():
            new_config.set(new_section, key, value)

    with open(config_path, "w") as f:
        new_config.write(f)

    logger.info("Migrated %s to site-prefixed format", config_path)
    return True


def rename_site_ini_sections(old_name: str, new_name: str) -> bool:
    config_path = _find_passwords_ini()
    if config_path is None:
        return False

    backup_path = config_path.with_suffix(config_path.suffix + ".bak")
    shutil.copy2(str(config_path), str(backup_path))

    config = configparser.RawConfigParser()
    config.read(config_path)

    prefix = f"{old_name}-"
    sections_to_rename = [s for s in config.sections() if s.startswith(prefix)]
    if not sections_to_rename:
        return False

    new_config = configparser.RawConfigParser()
    for section in config.sections():
        if section.startswith(prefix):
            suffix = section[len(prefix):]
            new_section = f"{new_name}-{suffix}"
        else:
            new_section = section
        new_config.add_section(new_section)
        for key in config.options(section):
            new_config.set(new_section, key, config.get(section, key))

    with open(config_path, "w") as f:
        new_config.write(f)

    return True


def get_site_ini_config(site_name: str) -> dict:
    config_path = _find_passwords_ini()
    if config_path is None:
        return {"defaults": {}, "hosts": {}}

    config = configparser.RawConfigParser()
    config.read(config_path)

    prefix = f"{site_name}-"
    defaults_section = f"{site_name}-DEFAULTS"

    result = {"defaults": {}, "hosts": {}}

    if defaults_section in config:
        for key in config.options(defaults_section):
            result["defaults"][key] = config.get(defaults_section, key)

    for section in config.sections():
        if section.startswith(prefix) and section != defaults_section:
            hostname = section[len(prefix):]
            result["hosts"][hostname] = {}
            for key in config.options(section):
                result["hosts"][hostname][key] = config.get(section, key)

    return result


def set_site_ini_config(site_name: str, site_config: dict) -> None:
    config_path = _find_passwords_ini()
    if config_path is None:
        config_path = Path("drac-passwords.ini")

    if config_path.exists():
        backup_path = config_path.with_suffix(config_path.suffix + ".bak")
        shutil.copy2(str(config_path), str(backup_path))

    config = configparser.RawConfigParser()
    if config_path.exists():
        config.read(config_path)

    prefix = f"{site_name}-"
    for section in list(config.sections()):
        if section.startswith(prefix):
            config.remove_section(section)

    defaults = site_config.get("defaults", {})
    if defaults:
        defaults_section = f"{site_name}-DEFAULTS"
        config.add_section(defaults_section)
        for key, value in defaults.items():
            config.set(defaults_section, key, value)

    hosts = site_config.get("hosts", {})
    for hostname, host_config in hosts.items():
        section = f"{site_name}-{hostname}"
        config.add_section(section)
        for key, value in host_config.items():
            config.set(section, key, value)

    with open(config_path, "w") as f:
        config.write(f)

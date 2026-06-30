"""Background collector that keeps iDRAC configuration data fresh in the DB."""

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

_instance = None


def set_instance(collector) -> None:
    global _instance
    _instance = collector


def get_collector():
    return _instance


CHECK_INTERVAL = 300
MAX_WORKERS = 20


def _needs_collection(hostname: str, site_id: int, settings: dict) -> bool:
    """Return True if no HostConfig row exists or collected_at is stale."""
    from dracs.db import get_host_config_data

    enabled_hours = [
        settings[f"{attr}_hours"]
        for attr in [
            "ps_rapid_on",
            "dns_from_dhcp",
            "ipmi_lan_enable",
            "host_header_check",
            "sys_profile",
            "ssl",
            "idrac_hostname",
        ]
        if settings.get(f"{attr}_enabled")
    ]
    if not enabled_hours:
        return False

    rows = get_host_config_data(site_id, [hostname])
    if not rows:
        return True

    collected_at = rows[0].get("collected_at")
    if not collected_at:
        return True

    min_hours = min(enabled_hours)
    try:
        last = datetime.fromisoformat(collected_at).replace(tzinfo=timezone.utc)
    except ValueError:
        return True

    age_hours = (datetime.now(timezone.utc) - last).total_seconds() / 3600
    return age_hours >= min_hours


def _collect_and_store(hostname: str, site_name: str, site_id: int) -> None:
    """Collect config for one host and persist to DB. Logs failures, never raises."""
    try:
        from dracs.db import get_site_config_collection, upsert_host_config
        from dracs.redfish import collect_all_for_host

        settings = get_site_config_collection(site_id)
        data = collect_all_for_host(hostname, site_name, settings)
        upsert_host_config(hostname, site_id, data)
        logger.debug("Collected config for %s (site=%s)", hostname, site_name)
    except Exception as exc:
        logger.warning(
            "Failed to collect config for %s (site=%s): %s", hostname, site_name, exc
        )


class ConfigCollector:
    def __init__(self):
        """Initialize the collector with no running executor."""
        self._executor = None
        self._running = False
        self._thread = None

    def start(self) -> None:
        if self._running:
            return
        self._executor = ThreadPoolExecutor(max_workers=MAX_WORKERS)
        self._running = True
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        logger.info("ConfigCollector started (max_workers=%d)", MAX_WORKERS)

    def stop(self) -> None:
        self._running = False
        if self._executor:
            self._executor.shutdown(wait=False)
        logger.info("ConfigCollector stopped")

    @property
    def is_running(self) -> bool:
        return self._running

    def _run_loop(self) -> None:
        while self._running:
            try:
                self._sweep()
            except Exception as exc:
                logger.error("ConfigCollector sweep error: %s", exc)
            time.sleep(CHECK_INTERVAL)

    def _sweep(self) -> None:
        from dracs.db import (
            get_hosts_for_site,
            get_site_config_collection,
            list_sites,
        )

        sites = list_sites()
        for site in sites:
            site_id = site["id"]
            site_name = site["name"]
            settings = get_site_config_collection(site_id)

            any_enabled = any(
                settings.get(f"{attr}_enabled")
                for attr in [
                    "ps_rapid_on",
                    "dns_from_dhcp",
                    "ipmi_lan_enable",
                    "host_header_check",
                    "sys_profile",
                    "ssl",
                    "idrac_hostname",
                ]
            )
            if not any_enabled:
                continue

            hosts = get_hosts_for_site(site_id)
            for host in hosts:
                hostname = host["hostname"]
                if _needs_collection(hostname, site_id, settings):
                    self._executor.submit(
                        _collect_and_store, hostname, site_name, site_id
                    )

    def trigger_host(self, hostname: str, site_name: str, site_id: int) -> None:
        """Submit an immediate on-demand collection for one host."""
        if self._executor is not None:
            self._executor.submit(_collect_and_store, hostname, site_name, site_id)

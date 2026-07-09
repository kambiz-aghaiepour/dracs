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


def _needs_collection(hostname: str, site_id: int, enabled_attr_defs: list) -> bool:
    """Return True if any enabled attribute for this host is missing or stale."""
    from dracs.db import get_host_config_attrs

    if not enabled_attr_defs:
        return False

    rows = get_host_config_attrs(site_id, [hostname])
    host_attrs = rows[0]["attrs"] if rows else {}
    now = datetime.now(timezone.utc)

    for attr in enabled_attr_defs:
        existing = host_attrs.get(attr["name"])
        if not existing or not existing.get("collected_at"):
            return True

        try:
            last = datetime.fromisoformat(existing["collected_at"])
            if last.tzinfo is None:
                last = last.replace(tzinfo=timezone.utc)
        except ValueError:
            return True

        hours = attr["site_settings"]["hours"]
        age_hours = (now - last).total_seconds() / 3600
        if age_hours >= hours:
            return True

    return False


def _collect_and_store(hostname: str, site_name: str, site_id: int) -> None:
    """Collect all enabled attributes for one host and persist to DB. Never raises."""
    try:
        from dracs.db import get_enabled_attr_defs_for_site, upsert_host_config_attr
        from dracs.redfish import collect_for_host_dynamic

        enabled_attrs = get_enabled_attr_defs_for_site(site_id)
        if not enabled_attrs:
            return

        results = collect_for_host_dynamic(hostname, site_name, enabled_attrs)

        for attr in enabled_attrs:
            attr_name = attr["name"]
            if attr_name not in results:
                continue
            entry = results[attr_name]
            upsert_host_config_attr(
                hostname=hostname,
                site_id=site_id,
                attr_def_id=attr["id"],
                value=entry["value"],
                collected_at=entry["collected_at"],
            )

        logger.debug("Collected config for %s (site=%s)", hostname, site_name)
    except Exception as exc:
        logger.warning(
            "Failed to collect config for %s (site=%s): %s", hostname, site_name, exc
        )


class ConfigCollector:
    def __init__(self):
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
            get_enabled_attr_defs_for_site,
            get_hosts_for_site,
            list_sites,
        )

        for site in list_sites():
            site_id = site["id"]
            site_name = site["name"]
            enabled_attrs = get_enabled_attr_defs_for_site(site_id)
            if not enabled_attrs:
                continue

            for host in get_hosts_for_site(site_id):
                hostname = host["hostname"]
                if _needs_collection(hostname, site_id, enabled_attrs):
                    self._executor.submit(
                        _collect_and_store, hostname, site_name, site_id
                    )

    def trigger_host(self, hostname: str, site_name: str, site_id: int) -> None:
        """Submit an immediate on-demand collection for one host."""
        if self._executor is not None:
            self._executor.submit(_collect_and_store, hostname, site_name, site_id)

"""Tests for src/dracs/config_collector.py."""

import os
import tempfile
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, call, patch

import pytest

from dracs.config_collector import (
    CHECK_INTERVAL,
    MAX_WORKERS,
    ConfigCollector,
    _collect_and_store,
    _needs_collection,
)
from dracs.db import (
    db_initialize,
    get_default_site_id,
    upsert_host_config,
    upsert_site_config_collection,
    upsert_system,
)


@pytest.fixture
def coll_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    db_initialize(path)
    yield path
    if os.path.exists(path):
        os.unlink(path)


@pytest.fixture
def site_id(coll_db):
    return get_default_site_id()


class TestConstants:
    def test_check_interval(self):
        assert CHECK_INTERVAL == 300

    def test_max_workers(self):
        assert MAX_WORKERS == 20


class TestNeedsCollection:
    def test_true_when_no_record(self, coll_db, site_id):
        settings = {"ps_rapid_on_enabled": True, "ps_rapid_on_hours": 24}
        assert _needs_collection("host01.example.com", site_id, settings) is True

    def test_true_when_collected_at_is_none(self, coll_db, site_id):
        upsert_host_config("host01.example.com", site_id, {"ps_rapid_on": "Disabled"})
        settings = {"ps_rapid_on_enabled": True, "ps_rapid_on_hours": 24}
        assert _needs_collection("host01.example.com", site_id, settings) is True

    def test_false_when_fresh(self, coll_db, site_id):
        now = datetime.now(timezone.utc).isoformat()
        upsert_host_config(
            "host01.example.com",
            site_id,
            {"ps_rapid_on": "Disabled", "collected_at": now},
        )
        settings = {"ps_rapid_on_enabled": True, "ps_rapid_on_hours": 24}
        assert _needs_collection("host01.example.com", site_id, settings) is False

    def test_true_when_stale(self, coll_db, site_id):
        old = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()
        upsert_host_config(
            "host01.example.com",
            site_id,
            {"ps_rapid_on": "Disabled", "collected_at": old},
        )
        settings = {"ps_rapid_on_enabled": True, "ps_rapid_on_hours": 24}
        assert _needs_collection("host01.example.com", site_id, settings) is True

    def test_false_when_nothing_enabled(self, coll_db, site_id):
        settings = {
            "ps_rapid_on_enabled": False,
            "ps_rapid_on_hours": 24,
            "dns_from_dhcp_enabled": False,
            "dns_from_dhcp_hours": 24,
            "ipmi_lan_enable_enabled": False,
            "ipmi_lan_enable_hours": 24,
            "host_header_check_enabled": False,
            "host_header_check_hours": 24,
            "sys_profile_enabled": False,
            "sys_profile_hours": 24,
            "ssl_enabled": False,
            "ssl_hours": 24,
            "idrac_hostname_enabled": False,
            "idrac_hostname_hours": 24,
        }
        assert _needs_collection("host01.example.com", site_id, settings) is False


class TestCollectAndStore:
    @patch.dict(
        os.environ,
        {"DRACS_DNS_STRING": "mgmt-", "DRACS_DNS_MODE": "prefix"},
    )
    def test_calls_upsert_on_success(self, coll_db, site_id):
        data = {"ps_rapid_on": "Disabled", "collected_at": "2026-01-01T00:00:00"}
        with patch("dracs.db.get_site_config_collection", return_value={}):
            with patch("dracs.redfish.collect_all_for_host", return_value=data):
                with patch("dracs.db.upsert_host_config") as mock_upsert:
                    _collect_and_store("server01.example.com", "Default", site_id)
        mock_upsert.assert_called_once_with("server01.example.com", site_id, data)

    def test_logs_on_exception_without_raising(self, coll_db, site_id):
        with patch(
            "dracs.db.get_site_config_collection",
            side_effect=RuntimeError("DB error"),
        ):
            _collect_and_store("server01.example.com", "Default", site_id)


class TestSweep:
    def test_skips_site_with_nothing_enabled(self, coll_db, site_id):
        with patch(
            "dracs.db.get_site_config_collection",
            return_value={
                "ps_rapid_on_enabled": False,
                "dns_from_dhcp_enabled": False,
                "ipmi_lan_enable_enabled": False,
                "host_header_check_enabled": False,
                "sys_profile_enabled": False,
                "ssl_enabled": False,
                "idrac_hostname_enabled": False,
            },
        ):
            with patch("dracs.db.get_hosts_for_site") as mock_hosts:
                collector = ConfigCollector()
                collector._executor = MagicMock()
                collector._sweep()
        mock_hosts.assert_not_called()

    def test_submits_jobs_for_hosts_needing_collection(self, coll_db, site_id):
        settings = {
            "ps_rapid_on_enabled": True,
            "ps_rapid_on_hours": 24,
            "dns_from_dhcp_enabled": False,
            "dns_from_dhcp_hours": 24,
            "ipmi_lan_enable_enabled": False,
            "ipmi_lan_enable_hours": 24,
            "host_header_check_enabled": False,
            "host_header_check_hours": 24,
            "sys_profile_enabled": False,
            "sys_profile_hours": 24,
            "ssl_enabled": False,
            "ssl_hours": 24,
            "idrac_hostname_enabled": False,
            "idrac_hostname_hours": 24,
        }
        hosts = [
            {"hostname": "host01.example.com", "svc_tag": "T1"},
            {"hostname": "host02.example.com", "svc_tag": "T2"},
        ]
        with patch("dracs.db.get_site_config_collection", return_value=settings):
            with patch("dracs.db.get_hosts_for_site", return_value=hosts):
                with patch(
                    "dracs.config_collector._needs_collection", return_value=True
                ):
                    collector = ConfigCollector()
                    collector._executor = MagicMock()
                    collector._sweep()
        assert collector._executor.submit.call_count == 2


class TestConfigCollector:
    def test_starts_and_reports_running(self):
        collector = ConfigCollector()
        with patch.object(collector, "_run_loop"):
            collector.start()
        assert collector.is_running is True
        collector.stop()

    def test_start_is_idempotent(self):
        collector = ConfigCollector()
        with patch.object(collector, "_run_loop"):
            collector.start()
            collector.start()
        assert collector._thread is not None
        collector.stop()

    def test_stop_marks_not_running(self):
        collector = ConfigCollector()
        with patch.object(collector, "_run_loop"):
            collector.start()
        collector.stop()
        assert collector.is_running is False

    def test_trigger_host_submits_to_executor(self):
        collector = ConfigCollector()
        collector._executor = MagicMock()
        collector.trigger_host("host01.example.com", "Default", 1)
        collector._executor.submit.assert_called_once_with(
            _collect_and_store, "host01.example.com", "Default", 1
        )

    def test_trigger_host_noop_when_not_started(self):
        collector = ConfigCollector()
        collector.trigger_host("host01.example.com", "Default", 1)

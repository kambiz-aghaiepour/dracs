"""Tests for src/dracs/config_collector.py."""

import os
import tempfile
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from dracs.config_collector import (
    CHECK_INTERVAL,
    MAX_WORKERS,
    ConfigCollector,
    _collect_and_store,
    _needs_collection,
    get_collector,
    set_instance,
)
from dracs.db import (
    db_initialize,
    get_attr_def_by_name,
    get_default_site_id,
    upsert_host_config_attr,
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


def _make_attr_def(name="ps_rapid_on", hours=24):
    """Return a minimal attr_def dict matching the new _needs_collection signature."""
    return {"name": name, "site_settings": {"enabled": True, "hours": hours}}


class TestConstants:
    def test_check_interval(self):
        assert CHECK_INTERVAL == 300

    def test_max_workers(self):
        assert MAX_WORKERS == 20


class TestNeedsCollection:
    def test_true_when_no_record(self, coll_db, site_id):
        enabled = [_make_attr_def("ps_rapid_on", 24)]
        assert _needs_collection("host01.example.com", site_id, enabled) is True

    def test_true_when_collected_at_is_none(self, coll_db, site_id):
        attr = get_attr_def_by_name("ps_rapid_on")
        upsert_host_config_attr(
            "host01.example.com", site_id, attr["id"], "Disabled", None
        )
        enabled = [_make_attr_def("ps_rapid_on", 24)]
        assert _needs_collection("host01.example.com", site_id, enabled) is True

    def test_false_when_fresh(self, coll_db, site_id):
        now = datetime.now(timezone.utc).isoformat()
        attr = get_attr_def_by_name("ps_rapid_on")
        upsert_host_config_attr(
            "host01.example.com", site_id, attr["id"], "Disabled", now
        )
        enabled = [_make_attr_def("ps_rapid_on", 24)]
        assert _needs_collection("host01.example.com", site_id, enabled) is False

    def test_true_when_stale(self, coll_db, site_id):
        old = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()
        attr = get_attr_def_by_name("ps_rapid_on")
        upsert_host_config_attr(
            "host01.example.com", site_id, attr["id"], "Disabled", old
        )
        enabled = [_make_attr_def("ps_rapid_on", 24)]
        assert _needs_collection("host01.example.com", site_id, enabled) is True

    def test_true_when_collected_at_is_malformed(self, coll_db, site_id):
        attr = get_attr_def_by_name("ps_rapid_on")
        upsert_host_config_attr(
            "host01.example.com",
            site_id,
            attr["id"],
            "Disabled",
            "not-a-valid-datetime",
        )
        enabled = [_make_attr_def("ps_rapid_on", 24)]
        assert _needs_collection("host01.example.com", site_id, enabled) is True

    def test_false_when_nothing_enabled(self, coll_db, site_id):
        assert _needs_collection("host01.example.com", site_id, []) is False

    def test_true_when_stale_naive_datetime(self, coll_db, site_id):
        # Naive datetime (no timezone) should be treated as UTC.
        naive_stale = (datetime.now() - timedelta(hours=25)).isoformat()
        attr = get_attr_def_by_name("ps_rapid_on")
        upsert_host_config_attr("host01.example.com", site_id, attr["id"], "Disabled", naive_stale)
        enabled = [_make_attr_def("ps_rapid_on", 24)]
        assert _needs_collection("host01.example.com", site_id, enabled) is True


class TestCollectAndStore:
    @patch.dict(
        os.environ,
        {"DRACS_DNS_STRING": "mgmt-", "DRACS_DNS_MODE": "prefix"},
    )
    def test_calls_upsert_on_success(self, coll_db, site_id):
        attr_id = get_attr_def_by_name("ps_rapid_on")["id"]
        enabled_attrs = [{"id": attr_id, "name": "ps_rapid_on"}]
        collect_result = {
            "ps_rapid_on": {"value": "Disabled", "collected_at": "2026-01-01T00:00:00"}
        }
        with patch(
            "dracs.db.get_enabled_attr_defs_for_site", return_value=enabled_attrs
        ):
            with patch(
                "dracs.redfish.collect_for_host_dynamic", return_value=collect_result
            ):
                with patch("dracs.db.upsert_host_config_attr") as mock_upsert:
                    _collect_and_store("server01.example.com", "Default", site_id)
        mock_upsert.assert_called_once_with(
            hostname="server01.example.com",
            site_id=site_id,
            attr_def_id=attr_id,
            value="Disabled",
            collected_at="2026-01-01T00:00:00",
        )

    def test_returns_early_when_no_attrs_enabled(self, coll_db, site_id):
        with patch("dracs.db.get_enabled_attr_defs_for_site", return_value=[]):
            with patch("dracs.redfish.collect_for_host_dynamic") as mock_collect:
                _collect_and_store("server01.example.com", "Default", site_id)
        mock_collect.assert_not_called()

    @patch.dict(os.environ, {"DRACS_DNS_STRING": "mgmt-", "DRACS_DNS_MODE": "prefix"})
    def test_skips_attr_not_in_collect_result(self, coll_db, site_id):
        attr_id = get_attr_def_by_name("ps_rapid_on")["id"]
        enabled_attrs = [{"id": attr_id, "name": "ps_rapid_on"}]
        with patch("dracs.db.get_enabled_attr_defs_for_site", return_value=enabled_attrs):
            with patch("dracs.redfish.collect_for_host_dynamic", return_value={}):
                with patch("dracs.db.upsert_host_config_attr") as mock_upsert:
                    _collect_and_store("server01.example.com", "Default", site_id)
        mock_upsert.assert_not_called()

    def test_logs_on_exception_without_raising(self, coll_db, site_id):
        with patch(
            "dracs.db.get_enabled_attr_defs_for_site",
            side_effect=RuntimeError("DB error"),
        ):
            _collect_and_store("server01.example.com", "Default", site_id)


class TestSweep:
    def test_skips_site_with_nothing_enabled(self, coll_db, site_id):
        with patch("dracs.db.get_enabled_attr_defs_for_site", return_value=[]):
            with patch("dracs.db.get_hosts_for_site") as mock_hosts:
                collector = ConfigCollector()
                collector._executor = MagicMock()
                collector._sweep()
        mock_hosts.assert_not_called()

    def test_submits_jobs_for_hosts_needing_collection(self, coll_db, site_id):
        enabled_attrs = [_make_attr_def("ps_rapid_on", 24)]
        hosts = [
            {"hostname": "host01.example.com", "svc_tag": "T1"},
            {"hostname": "host02.example.com", "svc_tag": "T2"},
        ]
        with patch(
            "dracs.db.get_enabled_attr_defs_for_site", return_value=enabled_attrs
        ):
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


class TestSingleton:
    def setup_method(self):
        set_instance(None)

    def teardown_method(self):
        set_instance(None)

    def test_get_collector_returns_none_by_default(self):
        assert get_collector() is None

    def test_set_and_get_instance(self):
        mock_cc = MagicMock()
        set_instance(mock_cc)
        assert get_collector() is mock_cc

    def test_set_instance_to_none_clears(self):
        set_instance(MagicMock())
        set_instance(None)
        assert get_collector() is None

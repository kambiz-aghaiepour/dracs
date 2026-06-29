import os
import tempfile

import pytest

from dracs.db import (
    db_initialize,
    get_default_site_id,
    get_host_config_data,
    get_hosts_for_site,
    get_site_config_collection,
    upsert_host_config,
    upsert_site_config_collection,
    upsert_system,
)


@pytest.fixture
def config_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    db_initialize(path)
    yield path
    if os.path.exists(path):
        os.unlink(path)


@pytest.fixture
def site_id(config_db):
    return get_default_site_id()


class TestGetSiteConfigCollection:
    def test_returns_defaults_for_new_site(self, config_db, site_id):
        result = get_site_config_collection(site_id)
        for attr in [
            "ps_rapid_on",
            "dns_from_dhcp",
            "ipmi_lan_enable",
            "host_header_check",
            "sys_profile",
            "ssl",
            "idrac_hostname",
        ]:
            assert result[f"{attr}_enabled"] is False
            assert result[f"{attr}_hours"] == 24

    def test_returns_saved_values(self, config_db, site_id):
        upsert_site_config_collection(
            site_id, {"ps_rapid_on_enabled": True, "ps_rapid_on_hours": 12}
        )
        result = get_site_config_collection(site_id)
        assert result["ps_rapid_on_enabled"] is True
        assert result["ps_rapid_on_hours"] == 12
        assert result["dns_from_dhcp_enabled"] is False

    def test_upsert_updates_existing(self, config_db, site_id):
        upsert_site_config_collection(site_id, {"ssl_enabled": True, "ssl_hours": 6})
        upsert_site_config_collection(site_id, {"ssl_hours": 48})
        result = get_site_config_collection(site_id)
        assert result["ssl_enabled"] is True
        assert result["ssl_hours"] == 48

    def test_upsert_ignores_unknown_keys(self, config_db, site_id):
        upsert_site_config_collection(site_id, {"nonexistent_field": True})
        result = get_site_config_collection(site_id)
        assert result["ssl_enabled"] is False


class TestUpsertHostConfig:
    def test_insert_then_retrieve(self, config_db, site_id):
        data = {
            "ps_rapid_on": "Disabled",
            "dns_from_dhcp": "Enabled",
            "ipmi_lan_enable": "Enabled",
            "host_header_check": "Disabled",
            "sys_profile": "PerfPerWattOptimizedOs",
            "idrac_hostname": "mgmt-server01.example.com",
            "ssl_self_signed": 1,
            "ssl_valid_name": 0,
            "ssl_expiry": "2025-12-31",
            "collected_at": "2026-01-01T00:00:00",
        }
        upsert_host_config("server01.example.com", site_id, data)
        rows = get_host_config_data(site_id, ["server01.example.com"])
        assert len(rows) == 1
        row = rows[0]
        assert row["hostname"] == "server01.example.com"
        assert row["ps_rapid_on"] == "Disabled"
        assert row["ssl_self_signed"] == 1
        assert row["ssl_expiry"] == "2025-12-31"

    def test_update_existing(self, config_db, site_id):
        upsert_host_config("server01.example.com", site_id, {"ps_rapid_on": "Enabled"})
        upsert_host_config("server01.example.com", site_id, {"ps_rapid_on": "Disabled"})
        rows = get_host_config_data(site_id, ["server01.example.com"])
        assert rows[0]["ps_rapid_on"] == "Disabled"

    def test_multiple_hosts(self, config_db, site_id):
        upsert_host_config("host01.example.com", site_id, {"sys_profile": "Perf"})
        upsert_host_config("host02.example.com", site_id, {"sys_profile": "MaxPerf"})
        rows = get_host_config_data(site_id, [])
        assert len(rows) == 2
        hostnames = [r["hostname"] for r in rows]
        assert "host01.example.com" in hostnames
        assert "host02.example.com" in hostnames

    def test_filter_by_hostnames(self, config_db, site_id):
        upsert_host_config("host01.example.com", site_id, {"sys_profile": "Perf"})
        upsert_host_config("host02.example.com", site_id, {"sys_profile": "MaxPerf"})
        rows = get_host_config_data(site_id, ["host01.example.com"])
        assert len(rows) == 1
        assert rows[0]["hostname"] == "host01.example.com"


class TestGetHostsForSite:
    def test_returns_hosts_for_site(self, config_db, site_id):
        upsert_system(
            config_db,
            "TAG001",
            "server01.example.com",
            "R660",
            "7.0.0",
            "2.1.0",
            "Jan 1, 2027",
            1893456000,
            site_id,
        )
        upsert_system(
            config_db,
            "TAG002",
            "server02.example.com",
            "R660",
            "7.0.0",
            "2.1.0",
            "Jan 1, 2027",
            1893456000,
            site_id,
        )
        hosts = get_hosts_for_site(site_id)
        hostnames = [h["hostname"] for h in hosts]
        assert "server01.example.com" in hostnames
        assert "server02.example.com" in hostnames

    def test_returns_empty_for_site_with_no_hosts(self, config_db):
        from dracs.db import create_site

        new_site = create_site("empty-site")
        hosts = get_hosts_for_site(new_site["id"])
        assert hosts == []

"""Tests for EAV config DB functions in src/dracs/db.py."""

import os
import sqlite3
import tempfile

import pytest

from dracs.db import (
    AttrDefParams,
    create_attr_def,
    create_site,
    db_initialize,
    delete_attr_def,
    get_all_attr_defs,
    get_attr_catalog_for_site,
    get_attr_def_by_name,
    get_default_site_id,
    get_enabled_attr_defs_for_site,
    get_host_config_attrs,
    get_hosts_for_site,
    update_attr_def,
    upsert_attr_site_settings,
    upsert_host_config_attr,
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


class TestSeedAttrDefs:
    def test_seed_data_present_after_init(self, config_db, site_id):
        defs = get_all_attr_defs()
        names = [d["name"] for d in defs]
        for expected in [
            "ps_rapid_on",
            "dns_from_dhcp",
            "ipmi_lan_enable",
            "host_header_check",
            "sys_profile",
            "idrac_hostname",
            "ssl_self_signed",
            "ssl_valid_name",
            "ssl_expiry",
            "ssl_fingerprint",
        ]:
            assert expected in names, f"Missing seed attr: {expected}"

    def test_each_def_has_required_fields(self, config_db, site_id):
        for d in get_all_attr_defs():
            assert "id" in d
            assert "name" in d
            assert "label" in d
            assert "endpoint_type" in d
            assert "display_type" in d


class TestGetAttrDefByName:
    def test_returns_def_for_known_name(self, config_db, site_id):
        d = get_attr_def_by_name("ps_rapid_on")
        assert d is not None
        assert d["name"] == "ps_rapid_on"
        assert "endpoint_type" in d

    def test_returns_none_for_unknown_name(self, config_db, site_id):
        assert get_attr_def_by_name("no_such_attr") is None

    def test_returns_choices_if_any(self, config_db, site_id):
        d = get_attr_def_by_name("sys_profile")
        assert "choices" in d


class TestAttrCatalog:
    def test_returns_all_defs_for_site(self, config_db, site_id):
        catalog = get_attr_catalog_for_site(site_id)
        assert len(catalog) >= 10
        names = [d["name"] for d in catalog]
        assert "ps_rapid_on" in names

    def test_each_entry_has_site_settings(self, config_db, site_id):
        catalog = get_attr_catalog_for_site(site_id)
        for entry in catalog:
            ss = entry["site_settings"]
            assert "enabled" in ss
            assert "hours" in ss
            assert "desired_choice_id" in ss

    def test_defaults_to_disabled(self, config_db, site_id):
        catalog = get_attr_catalog_for_site(site_id)
        for entry in catalog:
            assert entry["site_settings"]["enabled"] is False

    def test_enabled_attr_returned_correctly_after_upsert(self, config_db, site_id):
        attr = get_attr_def_by_name("ps_rapid_on")
        upsert_attr_site_settings(
            attr["id"], site_id, enabled=True, hours=6, desired_choice_id=None
        )
        catalog = get_attr_catalog_for_site(site_id)
        ps = next(d for d in catalog if d["name"] == "ps_rapid_on")
        assert ps["site_settings"]["enabled"] is True
        assert ps["site_settings"]["hours"] == 6

    def test_contains_post_push_command(self, config_db, site_id):
        catalog = get_attr_catalog_for_site(site_id)
        # post_push_command field must be present (may be None for most attrs)
        for entry in catalog:
            assert "post_push_command" in entry


class TestGetEnabledAttrDefsForSite:
    def test_returns_empty_when_nothing_enabled(self, config_db, site_id):
        enabled = get_enabled_attr_defs_for_site(site_id)
        assert enabled == []

    def test_returns_only_enabled_attrs(self, config_db, site_id):
        ps = get_attr_def_by_name("ps_rapid_on")
        dns = get_attr_def_by_name("dns_from_dhcp")
        upsert_attr_site_settings(
            ps["id"], site_id, enabled=True, hours=24, desired_choice_id=None
        )
        upsert_attr_site_settings(
            dns["id"], site_id, enabled=False, hours=24, desired_choice_id=None
        )
        enabled = get_enabled_attr_defs_for_site(site_id)
        names = [d["name"] for d in enabled]
        assert "ps_rapid_on" in names
        assert "dns_from_dhcp" not in names


class TestUpsertAttrSiteSettings:
    def test_sets_enabled_and_hours(self, config_db, site_id):
        attr = get_attr_def_by_name("dns_from_dhcp")
        upsert_attr_site_settings(
            attr["id"], site_id, enabled=True, hours=12, desired_choice_id=None
        )
        catalog = get_attr_catalog_for_site(site_id)
        d = next(x for x in catalog if x["name"] == "dns_from_dhcp")
        assert d["site_settings"]["enabled"] is True
        assert d["site_settings"]["hours"] == 12

    def test_updates_existing(self, config_db, site_id):
        attr = get_attr_def_by_name("dns_from_dhcp")
        upsert_attr_site_settings(
            attr["id"], site_id, enabled=True, hours=12, desired_choice_id=None
        )
        upsert_attr_site_settings(
            attr["id"], site_id, enabled=False, hours=48, desired_choice_id=None
        )
        catalog = get_attr_catalog_for_site(site_id)
        d = next(x for x in catalog if x["name"] == "dns_from_dhcp")
        assert d["site_settings"]["enabled"] is False
        assert d["site_settings"]["hours"] == 48

    def test_desired_choice_id_stored(self, config_db, site_id):
        attr = get_attr_def_by_name("sys_profile")
        choices = attr.get("choices", [])
        if choices:
            choice_id = choices[0]["id"]
            upsert_attr_site_settings(
                attr["id"], site_id, enabled=True, hours=24, desired_choice_id=choice_id
            )
            catalog = get_attr_catalog_for_site(site_id)
            d = next(x for x in catalog if x["name"] == "sys_profile")
            assert d["site_settings"]["desired_choice_id"] == choice_id


class TestHostConfigAttrs:
    def test_insert_and_retrieve(self, config_db, site_id):
        attr = get_attr_def_by_name("ps_rapid_on")
        upsert_host_config_attr(
            "server01.example.com",
            site_id,
            attr["id"],
            "Disabled",
            "2026-01-01T00:00:00",
        )
        rows = get_host_config_attrs(site_id, ["server01.example.com"])
        assert len(rows) == 1
        row = rows[0]
        assert row["hostname"] == "server01.example.com"
        assert row["attrs"]["ps_rapid_on"]["value"] == "Disabled"
        assert row["attrs"]["ps_rapid_on"]["collected_at"] == "2026-01-01T00:00:00"

    def test_update_existing(self, config_db, site_id):
        attr = get_attr_def_by_name("ps_rapid_on")
        upsert_host_config_attr(
            "server01.example.com",
            site_id,
            attr["id"],
            "Enabled",
            "2026-01-01T00:00:00",
        )
        upsert_host_config_attr(
            "server01.example.com",
            site_id,
            attr["id"],
            "Disabled",
            "2026-01-02T00:00:00",
        )
        rows = get_host_config_attrs(site_id, ["server01.example.com"])
        assert rows[0]["attrs"]["ps_rapid_on"]["value"] == "Disabled"

    def test_multiple_attrs_same_host(self, config_db, site_id):
        ps = get_attr_def_by_name("ps_rapid_on")
        dns = get_attr_def_by_name("dns_from_dhcp")
        upsert_host_config_attr(
            "server01.example.com", site_id, ps["id"], "Disabled", "2026-01-01T00:00:00"
        )
        upsert_host_config_attr(
            "server01.example.com", site_id, dns["id"], "Enabled", "2026-01-01T00:00:00"
        )
        rows = get_host_config_attrs(site_id, ["server01.example.com"])
        assert rows[0]["attrs"]["ps_rapid_on"]["value"] == "Disabled"
        assert rows[0]["attrs"]["dns_from_dhcp"]["value"] == "Enabled"

    def test_multiple_hosts(self, config_db, site_id):
        attr = get_attr_def_by_name("ps_rapid_on")
        upsert_host_config_attr(
            "host01.example.com", site_id, attr["id"], "Disabled", "2026-01-01T00:00:00"
        )
        upsert_host_config_attr(
            "host02.example.com", site_id, attr["id"], "Enabled", "2026-01-01T00:00:00"
        )
        rows = get_host_config_attrs(site_id, [])
        hostnames = [r["hostname"] for r in rows]
        assert "host01.example.com" in hostnames
        assert "host02.example.com" in hostnames

    def test_filter_by_hostnames(self, config_db, site_id):
        attr = get_attr_def_by_name("ps_rapid_on")
        upsert_host_config_attr(
            "host01.example.com", site_id, attr["id"], "Disabled", "2026-01-01T00:00:00"
        )
        upsert_host_config_attr(
            "host02.example.com", site_id, attr["id"], "Enabled", "2026-01-01T00:00:00"
        )
        rows = get_host_config_attrs(site_id, ["host01.example.com"])
        assert len(rows) == 1
        assert rows[0]["hostname"] == "host01.example.com"

    def test_none_value_stored_and_retrieved(self, config_db, site_id):
        attr = get_attr_def_by_name("ssl_self_signed")
        upsert_host_config_attr(
            "server01.example.com", site_id, attr["id"], None, "2026-01-01T00:00:00"
        )
        rows = get_host_config_attrs(site_id, ["server01.example.com"])
        assert rows[0]["attrs"]["ssl_self_signed"]["value"] is None

    def test_returns_empty_for_unknown_host(self, config_db, site_id):
        rows = get_host_config_attrs(site_id, ["nosuchhost.example.com"])
        assert rows == []


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
        new_site = create_site("empty-site")
        hosts = get_hosts_for_site(new_site["id"])
        assert hosts == []


class TestMigrateCollectionTables:
    def test_seeds_on_fresh_db(self, config_db, site_id):
        """Seed runs automatically on fresh DB."""
        defs = get_all_attr_defs()
        assert len(defs) >= 10

    def test_migration_no_op_when_old_tables_absent(self, config_db, site_id):
        """Re-initializing a DB that already has EAV tables is safe."""
        db_initialize(config_db)
        defs = get_all_attr_defs()
        assert len(defs) >= 10

    def test_migrates_old_site_config_collection(self):
        """Old site_config_collection rows are migrated to config_attr_site_settings."""
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        try:
            db_initialize(path)
            # Simulate an old-style site_config_collection table (full schema)
            with sqlite3.connect(path) as con:
                default_site_id = con.execute(
                    "SELECT id FROM sites WHERE name='Default'"
                ).fetchone()[0]
                con.execute("""
                    CREATE TABLE IF NOT EXISTS site_config_collection (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        site_id INTEGER NOT NULL UNIQUE,
                        ps_rapid_on_enabled INTEGER NOT NULL DEFAULT 0,
                        ps_rapid_on_hours INTEGER NOT NULL DEFAULT 24,
                        dns_from_dhcp_enabled INTEGER NOT NULL DEFAULT 0,
                        dns_from_dhcp_hours INTEGER NOT NULL DEFAULT 24,
                        ipmi_lan_enable_enabled INTEGER NOT NULL DEFAULT 0,
                        ipmi_lan_enable_hours INTEGER NOT NULL DEFAULT 24,
                        host_header_check_enabled INTEGER NOT NULL DEFAULT 0,
                        host_header_check_hours INTEGER NOT NULL DEFAULT 24,
                        sys_profile_enabled INTEGER NOT NULL DEFAULT 0,
                        sys_profile_hours INTEGER NOT NULL DEFAULT 24,
                        ssl_enabled INTEGER NOT NULL DEFAULT 0,
                        ssl_hours INTEGER NOT NULL DEFAULT 24,
                        idrac_hostname_enabled INTEGER NOT NULL DEFAULT 0,
                        idrac_hostname_hours INTEGER NOT NULL DEFAULT 24
                    )
                """)
                con.execute(
                    "INSERT OR IGNORE INTO site_config_collection "
                    "(site_id, ps_rapid_on_enabled, ps_rapid_on_hours, ssl_enabled, ssl_hours) "
                    "VALUES (?, 1, 12, 1, 6)",
                    (default_site_id,),
                )
                con.commit()
            db_initialize(path)
            # After migration: ps_rapid_on should be enabled with hours=12
            catalog = get_attr_catalog_for_site(default_site_id)
            ps = next((d for d in catalog if d["name"] == "ps_rapid_on"), None)
            assert ps is not None
            assert ps["site_settings"]["enabled"] is True
            assert ps["site_settings"]["hours"] == 12
            # ssl attrs: ssl_self_signed, ssl_valid_name, ssl_expiry, ssl_fingerprint
            # should all be enabled with hours=6
            for ssl_name in (
                "ssl_self_signed",
                "ssl_valid_name",
                "ssl_expiry",
                "ssl_fingerprint",
            ):
                ssl_entry = next((d for d in catalog if d["name"] == ssl_name), None)
                assert ssl_entry is not None, f"Missing SSL attr: {ssl_name}"
                assert ssl_entry["site_settings"]["enabled"] is True
                assert ssl_entry["site_settings"]["hours"] == 6
        finally:
            if os.path.exists(path):
                os.unlink(path)

    def test_migrates_old_host_config_rows(self):
        """Old host_config rows are migrated to host_config_attr EAV rows."""
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        try:
            db_initialize(path)
            with sqlite3.connect(path) as con:
                default_site_id = con.execute(
                    "SELECT id FROM sites WHERE name='Default'"
                ).fetchone()[0]
                con.execute("""
                    CREATE TABLE IF NOT EXISTS host_config (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        hostname VARCHAR NOT NULL,
                        site_id INTEGER NOT NULL,
                        ps_rapid_on TEXT,
                        dns_from_dhcp TEXT,
                        ipmi_lan_enable TEXT,
                        host_header_check TEXT,
                        sys_profile TEXT,
                        idrac_hostname INTEGER,
                        idrac_hostname_value TEXT,
                        ssl_self_signed INTEGER,
                        ssl_valid_name INTEGER,
                        ssl_expiry TEXT,
                        ssl_fingerprint TEXT,
                        collected_at TEXT,
                        UNIQUE (hostname, site_id)
                    )
                """)
                con.execute(
                    "INSERT INTO host_config (hostname, site_id, ps_rapid_on, collected_at) "
                    "VALUES ('server01.example.com', ?, 'Disabled', '2026-01-01T00:00:00')",
                    (default_site_id,),
                )
                con.commit()
            db_initialize(path)
            rows = get_host_config_attrs(default_site_id, ["server01.example.com"])
            assert len(rows) == 1
            assert rows[0]["attrs"].get("ps_rapid_on", {}).get("value") == "Disabled"
        finally:
            if os.path.exists(path):
                os.unlink(path)


def _make_params(**kwargs):
    defaults = dict(
        name="test_attr",
        label="Test Attr",
        endpoint_type="idrac_attributes",
        display_type="string",
        display_order=99,
        choices=[],
        attribute_path="Attributes.Test.1.Value",
        push_key="iDRAC.Test.Value",
        is_writable=False,
        post_push_command=None,
    )
    defaults.update(kwargs)
    return AttrDefParams(**defaults)


class TestCreateAttrDef:
    def test_creates_def_and_returns_dict(self, config_db):
        entry = create_attr_def(_make_params())
        assert entry["name"] == "test_attr"
        assert entry["label"] == "Test Attr"
        assert entry["endpoint_type"] == "idrac_attributes"
        assert isinstance(entry["id"], int)

    def test_creates_with_choices(self, config_db):
        params = _make_params(
            name="test_choices",
            is_writable=True,
            choices=[{"label": "Yes", "push_value": "Enabled"}, {"label": "No", "push_value": "Disabled"}],
        )
        entry = create_attr_def(params)
        assert len(entry["choices"]) == 2
        assert entry["choices"][0]["label"] == "Yes"
        assert entry["choices"][1]["push_value"] == "Disabled"

    def test_new_def_appears_in_get_all(self, config_db):
        create_attr_def(_make_params())
        names = [d["name"] for d in get_all_attr_defs()]
        assert "test_attr" in names

    def test_post_push_command_stored(self, config_db):
        entry = create_attr_def(_make_params(post_push_command="jobqueue create BIOS.Setup.1-1"))
        assert entry["post_push_command"] == "jobqueue create BIOS.Setup.1-1"

    def test_empty_string_path_stored_as_none(self, config_db):
        entry = create_attr_def(_make_params(attribute_path="", push_key=""))
        assert entry["attribute_path"] is None
        assert entry["push_key"] is None


class TestUpdateAttrDef:
    def test_updates_label_and_returns_dict(self, config_db):
        entry = create_attr_def(_make_params())
        updated = update_attr_def(entry["id"], _make_params(label="Updated Label"))
        assert updated["label"] == "Updated Label"
        assert updated["id"] == entry["id"]

    def test_replaces_choices(self, config_db):
        entry = create_attr_def(_make_params(
            is_writable=True,
            choices=[{"label": "Old", "push_value": "old"}],
        ))
        updated = update_attr_def(entry["id"], _make_params(
            is_writable=True,
            choices=[{"label": "New1", "push_value": "n1"}, {"label": "New2", "push_value": "n2"}],
        ))
        assert len(updated["choices"]) == 2
        assert updated["choices"][0]["label"] == "New1"

    def test_nullifies_desired_choice_id_on_site_settings(self, config_db, site_id):
        entry = create_attr_def(_make_params(
            is_writable=True,
            choices=[{"label": "On", "push_value": "Enabled"}],
        ))
        choice_id = entry["choices"][0]["id"]
        upsert_attr_site_settings(entry["id"], site_id, enabled=True, hours=24, desired_choice_id=choice_id)
        update_attr_def(entry["id"], _make_params(
            is_writable=True,
            choices=[{"label": "New", "push_value": "New"}],
        ))
        catalog = get_attr_catalog_for_site(site_id)
        found = next((d for d in catalog if d["id"] == entry["id"]), None)
        assert found is not None
        assert found["site_settings"]["desired_choice_id"] is None

    def test_raises_for_nonexistent_id(self, config_db):
        with pytest.raises(ValueError, match="not found"):
            update_attr_def(999999, _make_params())


class TestDeleteAttrDef:
    def test_removes_def_from_catalog(self, config_db):
        entry = create_attr_def(_make_params())
        delete_attr_def(entry["id"])
        names = [d["name"] for d in get_all_attr_defs()]
        assert "test_attr" not in names

    def test_returns_counts(self, config_db, site_id):
        entry = create_attr_def(_make_params())
        upsert_host_config_attr("host1.example.com", site_id, entry["id"], "val", "2026-01-01T00:00:00")
        upsert_attr_site_settings(entry["id"], site_id, enabled=True, hours=24, desired_choice_id=None)
        result = delete_attr_def(entry["id"])
        assert result["deleted_host_records"] == 1
        assert result["deleted_site_settings"] == 1

    def test_cascades_choices(self, config_db):
        entry = create_attr_def(_make_params(
            is_writable=True,
            choices=[{"label": "A", "push_value": "a"}],
        ))
        delete_attr_def(entry["id"])
        # After delete, get_all_attr_defs should not include the deleted entry
        ids = [d["id"] for d in get_all_attr_defs()]
        assert entry["id"] not in ids

    def test_zero_counts_when_no_collected_data(self, config_db):
        entry = create_attr_def(_make_params())
        result = delete_attr_def(entry["id"])
        assert result["deleted_host_records"] == 0
        assert result["deleted_site_settings"] == 0

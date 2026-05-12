import asyncio
import os
from unittest.mock import patch, AsyncMock, MagicMock

import pytest

from dracs.commands import (
    add_dell_warranty,
    edit_dell_warranty,
    lookup_dell_warranty,
    remove_dell_warranty,
    discover_dell_system,
    filter_list_results,
)
from dracs.db import db_initialize, upsert_system
from dracs.exceptions import DatabaseError, SNMPError, ValidationError


class TestAddDellWarranty:
    @patch("dracs.commands.dell_api_warranty_date")
    @patch("dracs.commands.get_snmp_value", new_callable=AsyncMock)
    @patch("dracs.commands.build_idrac_hostname", return_value="mgmt-server01")
    def test_add_new_system(self, mock_build, mock_snmp, mock_api, temp_db):
        mock_snmp.side_effect = ["2.1.0", "7.0.0"]
        mock_api.return_value = {"TAG001": (1735689600, "Jan 1, 2027")}

        with patch.dict(os.environ, {"SNMP_COMMUNITY": "public"}):
            asyncio.run(add_dell_warranty("TAG001", "server01", "R660", temp_db))

        from dracs.db import query_by_service_tag

        results = query_by_service_tag(temp_db, "TAG001")
        assert len(results) == 1
        assert results[0][2] == "R660"

    @patch("dracs.commands.get_snmp_value", new_callable=AsyncMock)
    @patch("dracs.commands.build_idrac_hostname", return_value="mgmt-server01")
    def test_add_existing_system_updates(self, mock_build, mock_snmp, temp_db):
        mock_snmp.side_effect = ["3.0.0", "8.0.0"]

        db_initialize(temp_db)
        upsert_system(
            temp_db,
            "TAG001",
            "server01",
            "R660",
            "7.0.0",
            "2.1.0",
            "Jan 1, 2027",
            1735689600,
        )

        with patch.dict(os.environ, {"SNMP_COMMUNITY": "public"}):
            asyncio.run(add_dell_warranty("TAG001", "server01", "R660", temp_db))

        from dracs.db import query_by_service_tag

        results = query_by_service_tag(temp_db, "TAG001")
        assert len(results) == 1
        assert results[0][4] == "3.0.0"

    @patch("dracs.commands.get_snmp_value", new_callable=AsyncMock)
    @patch("dracs.commands.build_idrac_hostname", return_value="mgmt-server01")
    def test_add_with_precomputed_warranty(self, mock_build, mock_snmp, temp_db):
        mock_snmp.side_effect = ["2.1.0", "7.0.0"]

        warranty_results = {"TAG001": (1735689600, "Jan 1, 2027")}

        with patch.dict(os.environ, {"SNMP_COMMUNITY": "public"}):
            asyncio.run(
                add_dell_warranty(
                    "TAG001",
                    "server01",
                    "R660",
                    temp_db,
                    warranty_results=warranty_results,
                )
            )

        from dracs.db import query_by_service_tag

        results = query_by_service_tag(temp_db, "TAG001")
        assert len(results) == 1

    @patch("dracs.commands.get_snmp_value", new_callable=AsyncMock)
    @patch("dracs.commands.build_idrac_hostname", return_value="mgmt-server01")
    def test_add_multiple_records_raises(self, mock_build, mock_snmp, temp_db):
        mock_snmp.side_effect = ["2.1.0", "7.0.0"]

        db_initialize(temp_db)
        upsert_system(
            temp_db,
            "TAG001",
            "server01",
            "R660",
            "7.0.0",
            "2.1.0",
            "Jan 1, 2027",
            1735689600,
        )

        from dracs.db import get_session, System

        with get_session() as session:
            session.add(
                System(
                    svc_tag="TAG001DUP",
                    name="server01",
                    model="R660",
                    idrac_version="7.0.0",
                    bios_version="2.1.0",
                    exp_date="Jan 1, 2027",
                    exp_epoch=1735689600,
                )
            )
            session.commit()

        with patch.dict(os.environ, {"SNMP_COMMUNITY": "public"}):
            with patch("dracs.commands.get_session") as mock_get_session:
                mock_session = MagicMock()
                mock_results = [MagicMock(), MagicMock()]
                mock_session.query.return_value.filter.return_value.all.return_value = (
                    mock_results
                )
                mock_get_session.return_value.__enter__ = lambda s: mock_session
                mock_get_session.return_value.__exit__ = lambda s, *a: None

                with pytest.raises(DatabaseError, match="Multiple matching"):
                    asyncio.run(
                        add_dell_warranty("TAG001", "server01", "R660", temp_db)
                    )


class TestEditDellWarranty:
    def test_no_model_no_flags_raises(self, temp_db):
        with pytest.raises(ValidationError, match="Model parameter required"):
            asyncio.run(edit_dell_warranty("TAG001", None, None, False, False, temp_db))

    @patch("dracs.commands.get_snmp_value", new_callable=AsyncMock)
    @patch("dracs.commands.build_idrac_hostname", return_value="mgmt-server01")
    def test_edit_by_svctag_updates_idrac(self, mock_build, mock_snmp, temp_db):
        mock_snmp.return_value = "8.0.0"

        db_initialize(temp_db)
        upsert_system(
            temp_db,
            "TAG001",
            "server01",
            "R660",
            "7.0.0",
            "2.1.0",
            "Jan 1, 2027",
            1735689600,
        )

        with patch.dict(os.environ, {"SNMP_COMMUNITY": "public"}):
            asyncio.run(edit_dell_warranty("TAG001", None, None, True, False, temp_db))

        from dracs.db import query_by_service_tag

        results = query_by_service_tag(temp_db, "TAG001")
        assert results[0][3] == "8.0.0"

    @patch("dracs.commands.get_snmp_value", new_callable=AsyncMock)
    @patch("dracs.commands.build_idrac_hostname", return_value="mgmt-server01")
    def test_edit_by_hostname_updates_bios(self, mock_build, mock_snmp, temp_db):
        mock_snmp.return_value = "3.0.0"

        db_initialize(temp_db)
        upsert_system(
            temp_db,
            "TAG001",
            "server01",
            "R660",
            "7.0.0",
            "2.1.0",
            "Jan 1, 2027",
            1735689600,
        )

        with patch.dict(os.environ, {"SNMP_COMMUNITY": "public"}):
            asyncio.run(
                edit_dell_warranty(None, "server01", None, False, True, temp_db)
            )

        from dracs.db import query_by_service_tag

        results = query_by_service_tag(temp_db, "TAG001")
        assert results[0][4] == "3.0.0"

    def test_edit_not_found_raises(self, temp_db):
        db_initialize(temp_db)

        with pytest.raises(DatabaseError, match="Record not found"):
            asyncio.run(
                edit_dell_warranty("NOTHERE", None, "R660", False, False, temp_db)
            )

    def test_edit_no_tag_no_hostname(self, temp_db):
        db_initialize(temp_db)

        with pytest.raises(DatabaseError, match="Record not found"):
            asyncio.run(edit_dell_warranty(None, None, "R660", False, False, temp_db))

    def test_edit_model_only(self, temp_db):
        db_initialize(temp_db)
        upsert_system(
            temp_db,
            "TAG001",
            "server01",
            "R660",
            "7.0.0",
            "2.1.0",
            "Jan 1, 2027",
            1735689600,
        )

        with patch.dict(
            os.environ,
            {
                "SNMP_COMMUNITY": "public",
                "DRACS_DNS_STRING": "mgmt-",
                "DRACS_DNS_MODE": "prefix",
            },
        ):
            asyncio.run(
                edit_dell_warranty("TAG001", None, "R760", False, False, temp_db)
            )

        from dracs.db import query_by_service_tag

        results = query_by_service_tag(temp_db, "TAG001")
        assert results[0][2] == "R760"


class TestLookupDellWarranty:
    def test_lookup_by_svctag(self, temp_db, capsys):
        db_initialize(temp_db)
        upsert_system(
            temp_db,
            "TAG001",
            "server01",
            "R660",
            "7.0.0",
            "2.1.0",
            "Jan 1, 2027",
            1735689600,
        )

        asyncio.run(lookup_dell_warranty("TAG001", None, False, False, False, temp_db))

        output = capsys.readouterr().out
        assert "server01" in output
        assert "TAG001" in output

    def test_lookup_by_hostname(self, temp_db, capsys):
        db_initialize(temp_db)
        upsert_system(
            temp_db,
            "TAG001",
            "server01",
            "R660",
            "7.0.0",
            "2.1.0",
            "Jan 1, 2027",
            1735689600,
        )

        asyncio.run(
            lookup_dell_warranty(None, "server01", False, False, False, temp_db)
        )

        output = capsys.readouterr().out
        assert "TAG001" in output

    def test_lookup_full(self, temp_db, capsys):
        db_initialize(temp_db)
        upsert_system(
            temp_db,
            "TAG001",
            "server01",
            "R660",
            "7.0.0",
            "2.1.0",
            "Jan 1, 2027",
            1735689600,
        )

        asyncio.run(lookup_dell_warranty("TAG001", None, False, False, True, temp_db))

        output = capsys.readouterr().out
        assert "idrac_version" in output
        assert "bios_version" in output

    def test_lookup_idrac_only(self, temp_db, capsys):
        db_initialize(temp_db)
        upsert_system(
            temp_db,
            "TAG001",
            "server01",
            "R660",
            "7.0.0",
            "2.1.0",
            "Jan 1, 2027",
            1735689600,
        )

        asyncio.run(lookup_dell_warranty("TAG001", None, True, False, False, temp_db))

        output = capsys.readouterr().out
        assert "idrac_version" in output
        assert "model" not in output

    def test_lookup_bios_only(self, temp_db, capsys):
        db_initialize(temp_db)
        upsert_system(
            temp_db,
            "TAG001",
            "server01",
            "R660",
            "7.0.0",
            "2.1.0",
            "Jan 1, 2027",
            1735689600,
        )

        asyncio.run(lookup_dell_warranty("TAG001", None, False, True, False, temp_db))

        output = capsys.readouterr().out
        assert "bios_version" in output

    def test_lookup_not_found(self, temp_db):
        db_initialize(temp_db)

        with pytest.raises(DatabaseError, match="No matching"):
            asyncio.run(
                lookup_dell_warranty("NOTHERE", None, False, False, False, temp_db)
            )

    def test_lookup_no_tag_no_hostname(self, temp_db):
        db_initialize(temp_db)

        with pytest.raises(DatabaseError, match="No matching"):
            asyncio.run(lookup_dell_warranty(None, None, False, False, False, temp_db))


class TestRemoveDellWarranty:
    def test_remove_by_svctag(self, temp_db, capsys):
        db_initialize(temp_db)
        upsert_system(
            temp_db,
            "TAG001",
            "server01",
            "R660",
            "7.0.0",
            "2.1.0",
            "Jan 1, 2027",
            1735689600,
        )

        asyncio.run(remove_dell_warranty("TAG001", None, temp_db))

        output = capsys.readouterr().out
        assert "Record deleted" in output

        from dracs.db import query_by_service_tag

        results = query_by_service_tag(temp_db, "TAG001")
        assert len(results) == 0

    def test_remove_by_hostname(self, temp_db, capsys):
        db_initialize(temp_db)
        upsert_system(
            temp_db,
            "TAG001",
            "server01",
            "R660",
            "7.0.0",
            "2.1.0",
            "Jan 1, 2027",
            1735689600,
        )

        asyncio.run(remove_dell_warranty(None, "server01", temp_db))

        output = capsys.readouterr().out
        assert "Record deleted" in output

    def test_remove_not_found(self, temp_db):
        db_initialize(temp_db)

        with pytest.raises(DatabaseError, match="No matching"):
            asyncio.run(remove_dell_warranty("NOTHERE", None, temp_db))

    def test_remove_no_tag_no_hostname(self, temp_db):
        db_initialize(temp_db)

        with pytest.raises(DatabaseError, match="No matching"):
            asyncio.run(remove_dell_warranty(None, None, temp_db))


class TestDiscoverDellSystem:
    @patch("dracs.commands.get_snmp_value", new_callable=AsyncMock)
    @patch("dracs.commands.build_idrac_hostname", return_value="mgmt-server01")
    def test_discover_success(self, mock_build, mock_snmp, temp_db):
        mock_snmp.side_effect = ["TAG001", "PowerEdge R660"]

        with patch.dict(os.environ, {"SNMP_COMMUNITY": "public"}):
            tag, model = asyncio.run(discover_dell_system("server01", temp_db))

        assert tag == "TAG001"
        assert model == "R660"

    @patch("dracs.commands.get_snmp_value", new_callable=AsyncMock)
    @patch("dracs.commands.build_idrac_hostname", return_value="mgmt-server01")
    def test_discover_no_service_tag(self, mock_build, mock_snmp, temp_db):
        mock_snmp.side_effect = [None, "PowerEdge R660"]

        with patch.dict(os.environ, {"SNMP_COMMUNITY": "public"}):
            with pytest.raises(SNMPError, match="Failed to retrieve service tag"):
                asyncio.run(discover_dell_system("server01", temp_db))

    @patch("dracs.commands.get_snmp_value", new_callable=AsyncMock)
    @patch("dracs.commands.build_idrac_hostname", return_value="mgmt-server01")
    def test_discover_no_model(self, mock_build, mock_snmp, temp_db):
        mock_snmp.side_effect = ["TAG001", None]

        with patch.dict(os.environ, {"SNMP_COMMUNITY": "public"}):
            with pytest.raises(SNMPError, match="Failed to retrieve model"):
                asyncio.run(discover_dell_system("server01", temp_db))

    @patch("dracs.commands.get_snmp_value", new_callable=AsyncMock)
    @patch("dracs.commands.build_idrac_hostname", return_value="mgmt-server01")
    def test_discover_model_no_poweredge_prefix(self, mock_build, mock_snmp, temp_db):
        mock_snmp.side_effect = ["TAG001", "R660"]

        with patch.dict(os.environ, {"SNMP_COMMUNITY": "public"}):
            tag, model = asyncio.run(discover_dell_system("server01", temp_db))

        assert model == "R660"


class TestFilterListResultsExtended:
    @pytest.mark.asyncio
    async def test_bios_le(self):
        results = [
            ("TAG1", "host1", "R660", "5.0.0", "2.5.0", "Jan 1, 2027", 1735689600),
            ("TAG2", "host2", "R660", "5.0.0", "3.0.0", "Jan 1, 2027", 1735689600),
        ]
        filtered = await filter_list_results(
            results,
            bios_le="2.5.0",
            bios_lt=None,
            bios_ge=None,
            bios_gt=None,
            bios_eq=None,
            idrac_le=None,
            idrac_lt=None,
            idrac_ge=None,
            idrac_gt=None,
            idrac_eq=None,
        )
        assert len(filtered) == 1
        assert filtered[0][0] == "TAG1"

    @pytest.mark.asyncio
    async def test_bios_ge(self):
        results = [
            ("TAG1", "host1", "R660", "5.0.0", "2.5.0", "Jan 1, 2027", 1735689600),
            ("TAG2", "host2", "R660", "5.0.0", "3.0.0", "Jan 1, 2027", 1735689600),
        ]
        filtered = await filter_list_results(
            results,
            bios_le=None,
            bios_lt=None,
            bios_ge="3.0.0",
            bios_gt=None,
            bios_eq=None,
            idrac_le=None,
            idrac_lt=None,
            idrac_ge=None,
            idrac_gt=None,
            idrac_eq=None,
        )
        assert len(filtered) == 1
        assert filtered[0][0] == "TAG2"

    @pytest.mark.asyncio
    async def test_bios_gt(self):
        results = [
            ("TAG1", "host1", "R660", "5.0.0", "2.5.0", "Jan 1, 2027", 1735689600),
            ("TAG2", "host2", "R660", "5.0.0", "3.0.0", "Jan 1, 2027", 1735689600),
        ]
        filtered = await filter_list_results(
            results,
            bios_le=None,
            bios_lt=None,
            bios_ge=None,
            bios_gt="2.5.0",
            bios_eq=None,
            idrac_le=None,
            idrac_lt=None,
            idrac_ge=None,
            idrac_gt=None,
            idrac_eq=None,
        )
        assert len(filtered) == 1
        assert filtered[0][0] == "TAG2"

    @pytest.mark.asyncio
    async def test_idrac_le(self):
        results = [
            ("TAG1", "host1", "R660", "4.0.0", "2.1.0", "Jan 1, 2027", 1735689600),
            ("TAG2", "host2", "R660", "6.0.0", "2.1.0", "Jan 1, 2027", 1735689600),
        ]
        filtered = await filter_list_results(
            results,
            bios_le=None,
            bios_lt=None,
            bios_ge=None,
            bios_gt=None,
            bios_eq=None,
            idrac_le="4.0.0",
            idrac_lt=None,
            idrac_ge=None,
            idrac_gt=None,
            idrac_eq=None,
        )
        assert len(filtered) == 1
        assert filtered[0][0] == "TAG1"

    @pytest.mark.asyncio
    async def test_idrac_lt(self):
        results = [
            ("TAG1", "host1", "R660", "4.0.0", "2.1.0", "Jan 1, 2027", 1735689600),
            ("TAG2", "host2", "R660", "6.0.0", "2.1.0", "Jan 1, 2027", 1735689600),
        ]
        filtered = await filter_list_results(
            results,
            bios_le=None,
            bios_lt=None,
            bios_ge=None,
            bios_gt=None,
            bios_eq=None,
            idrac_le=None,
            idrac_lt="5.0.0",
            idrac_ge=None,
            idrac_gt=None,
            idrac_eq=None,
        )
        assert len(filtered) == 1
        assert filtered[0][0] == "TAG1"

    @pytest.mark.asyncio
    async def test_idrac_gt(self):
        results = [
            ("TAG1", "host1", "R660", "4.0.0", "2.1.0", "Jan 1, 2027", 1735689600),
            ("TAG2", "host2", "R660", "6.0.0", "2.1.0", "Jan 1, 2027", 1735689600),
        ]
        filtered = await filter_list_results(
            results,
            bios_le=None,
            bios_lt=None,
            bios_ge=None,
            bios_gt=None,
            bios_eq=None,
            idrac_le=None,
            idrac_lt=None,
            idrac_ge=None,
            idrac_gt="5.0.0",
            idrac_eq=None,
        )
        assert len(filtered) == 1
        assert filtered[0][0] == "TAG2"

    @pytest.mark.asyncio
    async def test_idrac_eq(self):
        results = [
            ("TAG1", "host1", "R660", "5.0.0", "2.1.0", "Jan 1, 2027", 1735689600),
            ("TAG2", "host2", "R660", "6.0.0", "2.1.0", "Jan 1, 2027", 1735689600),
        ]
        filtered = await filter_list_results(
            results,
            bios_le=None,
            bios_lt=None,
            bios_ge=None,
            bios_gt=None,
            bios_eq=None,
            idrac_le=None,
            idrac_lt=None,
            idrac_ge=None,
            idrac_gt=None,
            idrac_eq="5.0.0",
        )
        assert len(filtered) == 1
        assert filtered[0][0] == "TAG1"

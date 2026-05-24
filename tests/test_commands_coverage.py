"""Tests targeting uncovered lines in commands.py."""

import asyncio
import os
import time
from unittest.mock import patch, AsyncMock, MagicMock

import pytest

import dracs.commands as commands
from dracs.commands import (
    add_dell_warranty,
    edit_dell_warranty,
    lookup_dell_warranty,
    remove_dell_warranty,
    refresh_dell_warranty,
    refresh_by_model,
    refresh_all_systems,
    list_dell_warranty,
    _discover_single_host,
    discover_dell_systems_batch,
)
from dracs.db import db_initialize, upsert_system, get_session, System
from dracs.exceptions import DatabaseError, SNMPError, ValidationError


# ---------------------------------------------------------------------------
# debug_output branches in add_dell_warranty (lines 63-66, 93)
# ---------------------------------------------------------------------------
class TestAddDebugOutput:
    @patch("dracs.commands.get_snmp_value", new_callable=AsyncMock)
    @patch("dracs.commands.build_idrac_hostname", return_value="mgmt-h1")
    def test_add_existing_with_debug(self, mock_build, mock_snmp, temp_db):
        mock_snmp.side_effect = ["2.1.0", "7.0.0"]
        db_initialize(temp_db)
        upsert_system(
            temp_db,
            "TAG001",
            "h1",
            "R660",
            "7.0.0",
            "2.1.0",
            "Jan 1, 2027",
            1735689600,
        )
        old_debug = commands.debug_output
        commands.debug_output = True
        try:
            with patch.dict(os.environ, {"SNMP_COMMUNITY": "public"}):
                asyncio.run(add_dell_warranty("TAG001", "h1", "R660", temp_db))
        finally:
            commands.debug_output = old_debug

    @patch("dracs.commands.dell_api_warranty_date")
    @patch("dracs.commands.get_snmp_value", new_callable=AsyncMock)
    @patch("dracs.commands.build_idrac_hostname", return_value="mgmt-h1")
    def test_add_new_with_debug(self, mock_build, mock_snmp, mock_api, temp_db):
        mock_snmp.side_effect = ["2.1.0", "7.0.0"]
        mock_api.return_value = {"TAG001": (1735689600, "Jan 1, 2027")}
        db_initialize(temp_db)
        old_debug = commands.debug_output
        commands.debug_output = True
        try:
            with patch.dict(os.environ, {"SNMP_COMMUNITY": "public"}):
                asyncio.run(add_dell_warranty("TAG001", "h1", "R660", temp_db))
        finally:
            commands.debug_output = old_debug


# ---------------------------------------------------------------------------
# debug_output branches in edit_dell_warranty (lines 121, 124, 127, 151-154, 157, 191)
# ---------------------------------------------------------------------------
class TestEditDebugOutput:
    @patch("dracs.commands.get_snmp_value", new_callable=AsyncMock)
    @patch("dracs.commands.build_idrac_hostname", return_value="mgmt-h1")
    def test_edit_with_debug_svctag_idrac(self, mock_build, mock_snmp, temp_db):
        mock_snmp.return_value = "8.0.0"
        db_initialize(temp_db)
        upsert_system(
            temp_db,
            "TAG001",
            "h1",
            "R660",
            "7.0.0",
            "2.1.0",
            "Jan 1, 2027",
            1735689600,
        )
        old_debug = commands.debug_output
        commands.debug_output = True
        try:
            with patch.dict(os.environ, {"SNMP_COMMUNITY": "public"}):
                asyncio.run(
                    edit_dell_warranty("TAG001", None, None, True, False, temp_db)
                )
        finally:
            commands.debug_output = old_debug

    def test_edit_with_debug_hostname_model(self, temp_db):
        db_initialize(temp_db)
        upsert_system(
            temp_db,
            "TAG001",
            "h1",
            "R660",
            "7.0.0",
            "2.1.0",
            "Jan 1, 2027",
            1735689600,
        )
        old_debug = commands.debug_output
        commands.debug_output = True
        try:
            with patch.dict(
                os.environ,
                {
                    "SNMP_COMMUNITY": "public",
                    "DRACS_DNS_STRING": "mgmt-",
                    "DRACS_DNS_MODE": "prefix",
                },
            ):
                asyncio.run(
                    edit_dell_warranty(None, "h1", "R760", False, False, temp_db)
                )
        finally:
            commands.debug_output = old_debug

    @patch("dracs.commands.get_snmp_value", new_callable=AsyncMock)
    @patch("dracs.commands.build_idrac_hostname", return_value="mgmt-h1")
    def test_edit_multiple_results_raises(self, mock_build, mock_snmp, temp_db):
        db_initialize(temp_db)
        upsert_system(
            temp_db,
            "TAG001",
            "h1",
            "R660",
            "7.0.0",
            "2.1.0",
            "Jan 1, 2027",
            1735689600,
        )
        with patch("dracs.commands.get_session") as mock_gs:
            mock_session = MagicMock()
            mock_session.query.return_value.filter.return_value.all.return_value = [
                MagicMock(),
                MagicMock(),
            ]
            mock_gs.return_value.__enter__ = lambda s: mock_session
            mock_gs.return_value.__exit__ = lambda s, *a: None
            with pytest.raises(DatabaseError, match="Multiple matching"):
                asyncio.run(
                    edit_dell_warranty("TAG001", None, "R660", False, False, temp_db)
                )


# ---------------------------------------------------------------------------
# debug_output branches in remove_dell_warranty (lines 741, 744)
# ---------------------------------------------------------------------------
class TestRemoveDebugOutput:
    def test_remove_by_svctag_with_debug(self, temp_db, capsys):
        db_initialize(temp_db)
        upsert_system(
            temp_db,
            "TAG001",
            "h1",
            "R660",
            "7.0.0",
            "2.1.0",
            "Jan 1, 2027",
            1735689600,
        )
        old_debug = commands.debug_output
        commands.debug_output = True
        try:
            asyncio.run(remove_dell_warranty("TAG001", None, temp_db))
        finally:
            commands.debug_output = old_debug
        output = capsys.readouterr().out
        assert "service_tag = TAG001" in output

    def test_remove_by_hostname_with_debug(self, temp_db, capsys):
        db_initialize(temp_db)
        upsert_system(
            temp_db,
            "TAG001",
            "h1",
            "R660",
            "7.0.0",
            "2.1.0",
            "Jan 1, 2027",
            1735689600,
        )
        old_debug = commands.debug_output
        commands.debug_output = True
        try:
            asyncio.run(remove_dell_warranty(None, "h1", temp_db))
        finally:
            commands.debug_output = old_debug
        output = capsys.readouterr().out
        assert "hostname = h1" in output

    def test_remove_multiple_results_raises(self, temp_db):
        db_initialize(temp_db)
        upsert_system(
            temp_db,
            "TAG001",
            "h1",
            "R660",
            "7.0.0",
            "2.1.0",
            "Jan 1, 2027",
            1735689600,
        )
        with patch("dracs.commands.get_session") as mock_gs:
            mock_session = MagicMock()
            mock_session.query.return_value.filter.return_value.all.return_value = [
                MagicMock(),
                MagicMock(),
            ]
            mock_gs.return_value.__enter__ = lambda s: mock_session
            mock_gs.return_value.__exit__ = lambda s, *a: None
            with pytest.raises(DatabaseError, match="Multiple matching"):
                asyncio.run(remove_dell_warranty("TAG001", None, temp_db))


# ---------------------------------------------------------------------------
# lookup_dell_warranty multiple results (line 223)
# ---------------------------------------------------------------------------
class TestLookupMultipleResults:
    def test_lookup_multiple_raises(self, temp_db):
        db_initialize(temp_db)
        with patch("dracs.commands.get_session") as mock_gs:
            mock_session = MagicMock()
            mock_session.query.return_value.filter.return_value.all.return_value = [
                MagicMock(),
                MagicMock(),
            ]
            mock_gs.return_value.__enter__ = lambda s: mock_session
            mock_gs.return_value.__exit__ = lambda s, *a: None
            with pytest.raises(DatabaseError, match="Multiple matching"):
                asyncio.run(
                    lookup_dell_warranty("TAG001", None, False, False, False, temp_db)
                )


# ---------------------------------------------------------------------------
# list_dell_warranty rich table coloring (lines 461, 465, 472, 476)
# ---------------------------------------------------------------------------
class TestListColorBranches:
    def test_list_table_coloring(self, temp_db, capsys):
        """3 distinct firmware+BIOS versions for same model hits all color branches."""
        db_initialize(temp_db)
        future = int(time.time()) + 365 * 86400
        past = int(time.time()) - 365 * 86400
        soon = int(time.time()) + 30 * 86400

        upsert_system(temp_db, "T1", "a", "R660", "9.0.0", "3.0.0", "Jan 2030", future)
        upsert_system(temp_db, "T2", "b", "R660", "8.0.0", "2.0.0", "Jan 2020", past)
        upsert_system(temp_db, "T3", "c", "R660", "7.0.0", "1.0.0", "Feb 2025", soon)
        # 4th system with None firmware and bios to hit else branches (lines 465, 476)
        upsert_system(temp_db, "T4", "d", "R650", None, None, "Jan 2030", future)

        asyncio.run(
            list_dell_warranty(
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                False,
                False,
                False,
                temp_db,
            )
        )
        # Just verify it doesn't crash; color codes are Rich markup


# ---------------------------------------------------------------------------
# refresh_dell_warranty change-detection prints (line 554) + multiple results (505)
# ---------------------------------------------------------------------------
class TestRefreshChanges:
    @patch("dracs.commands.dell_api_warranty_date")
    @patch("dracs.commands.get_snmp_value", new_callable=AsyncMock)
    @patch("dracs.commands.build_idrac_hostname", return_value="mgmt-h1")
    def test_refresh_detects_model_change(
        self, mock_build, mock_snmp, mock_api, temp_db, capsys
    ):
        db_initialize(temp_db)
        upsert_system(
            temp_db,
            "TAG001",
            "h1",
            "R660",
            "7.0.0",
            "2.1.0",
            "Jan 1, 2027",
            1735689600,
        )
        mock_snmp.side_effect = ["2.1.0", "7.0.0", "PowerEdge R760"]
        mock_api.return_value = {"TAG001": (1735689600, "Jan 1, 2027")}
        with patch.dict(os.environ, {"SNMP_COMMUNITY": "public"}):
            asyncio.run(refresh_dell_warranty("TAG001", None, temp_db))
        output = capsys.readouterr().out
        assert "Model changed from R660 to R760" in output

    @patch("dracs.commands.dell_api_warranty_date")
    @patch("dracs.commands.get_snmp_value", new_callable=AsyncMock)
    @patch("dracs.commands.build_idrac_hostname", return_value="mgmt-h1")
    def test_refresh_detects_firmware_change(
        self, mock_build, mock_snmp, mock_api, temp_db, capsys
    ):
        db_initialize(temp_db)
        upsert_system(
            temp_db,
            "TAG001",
            "h1",
            "R660",
            "7.0.0",
            "2.1.0",
            "Jan 1, 2027",
            1735689600,
        )
        mock_snmp.side_effect = ["2.1.0", "8.0.0", "PowerEdge R660"]
        mock_api.return_value = {"TAG001": (1735689600, "Jan 1, 2027")}
        with patch.dict(os.environ, {"SNMP_COMMUNITY": "public"}):
            asyncio.run(refresh_dell_warranty("TAG001", None, temp_db))
        output = capsys.readouterr().out
        assert "Firmware changed from 7.0.0 to 8.0.0" in output

    @patch("dracs.commands.dell_api_warranty_date")
    @patch("dracs.commands.get_snmp_value", new_callable=AsyncMock)
    @patch("dracs.commands.build_idrac_hostname", return_value="mgmt-h1")
    def test_refresh_detects_bios_change(
        self, mock_build, mock_snmp, mock_api, temp_db, capsys
    ):
        db_initialize(temp_db)
        upsert_system(
            temp_db,
            "TAG001",
            "h1",
            "R660",
            "7.0.0",
            "2.1.0",
            "Jan 1, 2027",
            1735689600,
        )
        mock_snmp.side_effect = ["3.0.0", "7.0.0", "PowerEdge R660"]
        mock_api.return_value = {"TAG001": (1735689600, "Jan 1, 2027")}
        with patch.dict(os.environ, {"SNMP_COMMUNITY": "public"}):
            asyncio.run(refresh_dell_warranty("TAG001", None, temp_db))
        output = capsys.readouterr().out
        assert "BIOS changed from 2.1.0 to 3.0.0" in output

    @patch("dracs.commands.dell_api_warranty_date")
    @patch("dracs.commands.get_snmp_value", new_callable=AsyncMock)
    @patch("dracs.commands.build_idrac_hostname", return_value="mgmt-h1")
    def test_refresh_detects_warranty_change(
        self, mock_build, mock_snmp, mock_api, temp_db, capsys
    ):
        db_initialize(temp_db)
        upsert_system(
            temp_db,
            "TAG001",
            "h1",
            "R660",
            "7.0.0",
            "2.1.0",
            "Jan 1, 2027",
            1735689600,
        )
        mock_snmp.side_effect = ["2.1.0", "7.0.0", "PowerEdge R660"]
        mock_api.return_value = {"TAG001": (1893456000, "Jan 1, 2030")}
        with patch.dict(os.environ, {"SNMP_COMMUNITY": "public"}):
            asyncio.run(refresh_dell_warranty("TAG001", None, temp_db))
        output = capsys.readouterr().out
        assert "Warranty Expiration changed" in output

    def test_refresh_multiple_results_raises(self, temp_db):
        db_initialize(temp_db)
        from dracs.db import query_by_service_tag

        with patch("dracs.commands.query_by_service_tag") as mock_q:
            mock_q.return_value = [("T1",), ("T2",)]
            with pytest.raises(DatabaseError, match="Multiple matching"):
                asyncio.run(refresh_dell_warranty("TAG001", None, temp_db))


# ---------------------------------------------------------------------------
# refresh_by_model (now delegates to enqueue_batch)
# ---------------------------------------------------------------------------
class TestRefreshByModel:
    @patch("dracs.jobqueue.enqueue_batch", return_value=2)
    def test_refresh_by_model_success(self, mock_enqueue, temp_db, capsys):
        db_initialize(temp_db)
        upsert_system(
            temp_db, "T1", "h1", "R660", "7.0.0", "2.1.0", "Jan 2027", 1735689600
        )
        upsert_system(
            temp_db, "T2", "h2", "R660", "7.0.0", "2.1.0", "Jan 2027", 1735689600
        )
        asyncio.run(refresh_by_model("R660", temp_db, verbose=True))
        output = capsys.readouterr().out
        assert "Queued 2 refresh jobs for model R660" in output
        mock_enqueue.assert_called_once()
        args, kwargs = mock_enqueue.call_args
        assert args == ("refresh", "model:R660")

    def test_refresh_by_model_empty_raises(self, temp_db):
        db_initialize(temp_db)
        with pytest.raises(DatabaseError, match="No systems found with model"):
            asyncio.run(refresh_by_model("R999", temp_db))

    @patch("dracs.jobqueue.enqueue_batch", return_value=1)
    def test_refresh_by_model_not_verbose(self, mock_enqueue, temp_db, capsys):
        db_initialize(temp_db)
        upsert_system(
            temp_db, "T1", "h1", "R660", "7.0.0", "2.1.0", "Jan 2027", 1735689600
        )
        asyncio.run(refresh_by_model("R660", temp_db, verbose=False))
        output = capsys.readouterr().out
        assert "Queued 1 refresh jobs for model R660" in output
        mock_enqueue.assert_called_once()
        args, kwargs = mock_enqueue.call_args
        assert args == ("refresh", "model:R660")


# ---------------------------------------------------------------------------
# refresh_all_systems (now delegates to enqueue_batch)
# ---------------------------------------------------------------------------
class TestRefreshAllSystems:
    @patch("dracs.jobqueue.enqueue_batch", return_value=2)
    def test_refresh_all_success(self, mock_enqueue, temp_db, capsys):
        db_initialize(temp_db)
        upsert_system(
            temp_db, "T1", "h1", "R660", "7.0.0", "2.1.0", "Jan 2027", 1735689600
        )
        upsert_system(
            temp_db, "T2", "h2", "R650", "6.0.0", "1.5.0", "Jan 2027", 1735689600
        )
        asyncio.run(refresh_all_systems(temp_db, verbose=True))
        output = capsys.readouterr().out
        assert "Queued 2 refresh jobs for all systems" in output
        mock_enqueue.assert_called_once()
        args, kwargs = mock_enqueue.call_args
        assert args == ("refresh", "all")

    def test_refresh_all_empty_raises(self, temp_db):
        db_initialize(temp_db)
        with pytest.raises(DatabaseError, match="No systems found in database"):
            asyncio.run(refresh_all_systems(temp_db))

    @patch("dracs.jobqueue.enqueue_batch", return_value=1)
    def test_refresh_all_not_verbose(self, mock_enqueue, temp_db, capsys):
        db_initialize(temp_db)
        upsert_system(
            temp_db, "T1", "h1", "R660", "7.0.0", "2.1.0", "Jan 2027", 1735689600
        )
        asyncio.run(refresh_all_systems(temp_db, verbose=False))
        output = capsys.readouterr().out
        assert "Queued 1 refresh jobs for all systems" in output
        mock_enqueue.assert_called_once()
        args, kwargs = mock_enqueue.call_args
        assert args == ("refresh", "all")


# ---------------------------------------------------------------------------
# _discover_single_host auto_add path (lines 663-664)
# ---------------------------------------------------------------------------
class TestDiscoverSingleHost:
    @patch("dracs.commands.add_dell_warranty", new_callable=AsyncMock)
    @patch("dracs.commands.discover_dell_system", new_callable=AsyncMock)
    def test_discover_single_with_auto_add(self, mock_discover, mock_add, temp_db):
        mock_discover.return_value = ("TAG001", "R660")
        mock_add.return_value = None
        result = asyncio.run(_discover_single_host("h1", temp_db, auto_add=True))
        assert result["added"] is True
        mock_add.assert_called_once()

    @patch("dracs.commands.discover_dell_system", new_callable=AsyncMock)
    def test_discover_single_without_auto_add(self, mock_discover, temp_db):
        mock_discover.return_value = ("TAG001", "R660")
        result = asyncio.run(_discover_single_host("h1", temp_db, auto_add=False))
        assert result["added"] is False


# ---------------------------------------------------------------------------
# discover_dell_systems_batch add failure (lines 703-704)
# ---------------------------------------------------------------------------
class TestBatchDiscoverAddFailure:
    @patch("dracs.commands.dell_api_warranty_date")
    @patch("dracs.commands.discover_dell_system", new_callable=AsyncMock)
    @patch("dracs.commands.add_dell_warranty", new_callable=AsyncMock)
    def test_batch_add_raises_marks_error(
        self, mock_add, mock_discover, mock_api, temp_db, capsys
    ):
        mock_discover.return_value = ("TAG001", "R660")
        mock_api.return_value = {"TAG001": (1735689600, "Jan 2027")}
        mock_add.side_effect = Exception("insert failed")
        asyncio.run(
            discover_dell_systems_batch(
                ["h1"], temp_db, auto_add=True, show_discovered=False
            )
        )
        output = capsys.readouterr().out
        assert "Failed: 1" in output

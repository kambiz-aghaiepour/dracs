import asyncio
import os
import sys
import tempfile
from unittest.mock import AsyncMock, patch, MagicMock

import pytest

from dracs.cli import main
from dracs.commands import (
    bios_apply,
    bios_list,
    fw_apply,
    fw_list,
    _get_available_firmware_versions,
    _get_available_bios_versions,
)
from dracs.db import db_initialize, upsert_system
from dracs.exceptions import DatabaseError


def run_main_with_args(args):
    with patch.object(sys, "argv", ["dracs"] + args):
        asyncio.run(main())


@pytest.fixture
def fw_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    db_initialize(path)
    upsert_system(
        path,
        "TAG001",
        "host01.example.com",
        "R660",
        "7.10.50",
        "2.10.1",
        "Jan 1, 2027",
        1893456000,
    )
    upsert_system(
        path,
        "TAG002",
        "host02.example.com",
        "R660",
        "7.10.50",
        "2.10.1",
        "Jan 1, 2027",
        1893456000,
    )
    upsert_system(
        path,
        "TAG003",
        "host03.example.com",
        "R660",
        "7.00.00",
        "2.5.0",
        "Jan 1, 2027",
        1893456000,
    )
    upsert_system(
        path,
        "TAG004",
        "host04.example.com",
        "R650",
        "6.10.80",
        "1.5.0",
        "Jan 1, 2027",
        1893456000,
    )
    yield path
    if os.path.exists(path):
        os.unlink(path)


class TestGetAvailableFirmwareVersions:
    def test_finds_d9_files(self, tmp_path):
        (tmp_path / "R660-7.10.50.d9").write_bytes(b"fake")
        (tmp_path / "R660-7.00.00.d9").write_bytes(b"fake")
        (tmp_path / "R660-6.10.80.d9").write_bytes(b"fake")
        (tmp_path / "R650-5.00.00.d9").write_bytes(b"fake")
        with patch("dracs.webapp.FIRMWARE_IMAGE_DIR", tmp_path):
            versions = _get_available_firmware_versions("R660")
        assert len(versions) == 3
        assert "7.10.50" in versions

    def test_no_dir(self, tmp_path):
        with patch("dracs.webapp.FIRMWARE_IMAGE_DIR", tmp_path / "nope"):
            versions = _get_available_firmware_versions("R660")
        assert versions == []


class TestGetAvailableBiosVersions:
    def test_reads_ini(self, tmp_path, monkeypatch):
        ini = tmp_path / "BIOS-filename.ini"
        ini.write_text("[R660]\n2.10.1 = BIOS.EXE\n2.5.0 = BIOS2.EXE\n")
        monkeypatch.chdir(tmp_path)
        versions = _get_available_bios_versions("R660")
        assert len(versions) == 2

    def test_no_ini(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        versions = _get_available_bios_versions("R660")
        assert versions == []


class TestFwList:
    @pytest.mark.asyncio
    async def test_lists_all_models(self, fw_db, capsys, tmp_path):
        (tmp_path / "R660-6.10.80.d9").write_bytes(b"fake")
        with patch("dracs.webapp.FIRMWARE_IMAGE_DIR", tmp_path):
            await fw_list(None, fw_db)
        captured = capsys.readouterr()
        assert "R660" in captured.out
        assert "R650" in captured.out
        assert "7.10.50" in captured.out
        assert "(2)" in captured.out
        assert "6.10.80" in captured.out

    @pytest.mark.asyncio
    async def test_list_filtered_by_model(self, fw_db, capsys, tmp_path):
        with patch("dracs.webapp.FIRMWARE_IMAGE_DIR", tmp_path):
            await fw_list("R660", fw_db)
        captured = capsys.readouterr()
        assert "R660" in captured.out
        assert "R650" not in captured.out

    @pytest.mark.asyncio
    async def test_list_no_systems(self, fw_db, capsys, tmp_path):
        with patch("dracs.webapp.FIRMWARE_IMAGE_DIR", tmp_path):
            await fw_list("R999", fw_db)
        captured = capsys.readouterr()
        assert "No systems found" in captured.out


class TestFwApply:
    @pytest.mark.asyncio
    async def test_version_not_available(self, fw_db, capsys, tmp_path):
        with patch("dracs.webapp.FIRMWARE_IMAGE_DIR", tmp_path):
            await fw_apply("9.99.99", "host01.example.com", False, True, fw_db)
        captured = capsys.readouterr()
        assert "not available" in captured.out

    @pytest.mark.asyncio
    async def test_version_not_running_no_force(self, fw_db, capsys, tmp_path):
        (tmp_path / "R660-6.10.80.d9").write_bytes(b"fake")
        with patch("dracs.webapp.FIRMWARE_IMAGE_DIR", tmp_path):
            await fw_apply("6.10.80", "host01.example.com", False, True, fw_db)
        captured = capsys.readouterr()
        assert "not running on any" in captured.out
        assert "--force" in captured.out

    @pytest.mark.asyncio
    async def test_version_not_running_with_force(self, fw_db, capsys, tmp_path):
        (tmp_path / "R660-6.10.80.d9").write_bytes(b"fake")
        with patch("dracs.webapp.FIRMWARE_IMAGE_DIR", tmp_path):
            with patch("dracs.jobqueue.enqueue_job", return_value=42):
                await fw_apply("6.10.80", "host01.example.com", True, True, fw_db)
        captured = capsys.readouterr()
        assert "queued" in captured.out

    @pytest.mark.asyncio
    async def test_version_running_with_yes(self, fw_db, capsys, tmp_path):
        (tmp_path / "R660-7.00.00.d9").write_bytes(b"fake")
        with patch("dracs.webapp.FIRMWARE_IMAGE_DIR", tmp_path):
            with patch("dracs.jobqueue.enqueue_job", return_value=42):
                await fw_apply("7.00.00", "host01.example.com", False, True, fw_db)
        captured = capsys.readouterr()
        assert "queued" in captured.out

    @pytest.mark.asyncio
    async def test_version_running_prompt_cancel(self, fw_db, capsys, tmp_path):
        (tmp_path / "R660-7.00.00.d9").write_bytes(b"fake")
        with patch("dracs.webapp.FIRMWARE_IMAGE_DIR", tmp_path):
            with patch("builtins.input", return_value="n"):
                await fw_apply("7.00.00", "host01.example.com", False, False, fw_db)
        captured = capsys.readouterr()
        assert "Cancelled" in captured.out

    @pytest.mark.asyncio
    async def test_host_not_found(self, fw_db):
        with pytest.raises(DatabaseError, match="not found"):
            await fw_apply("7.00.00", "unknown.example.com", False, True, fw_db)


class TestBiosList:
    @pytest.mark.asyncio
    async def test_lists_all_models(self, fw_db, capsys, tmp_path, monkeypatch):
        ini = tmp_path / "BIOS-filename.ini"
        ini.write_text("[R660]\n2.10.1 = BIOS.EXE\n3.0.0 = BIOS3.EXE\n")
        monkeypatch.chdir(tmp_path)
        await bios_list(None, fw_db)
        captured = capsys.readouterr()
        assert "R660" in captured.out
        assert "2.10.1" in captured.out
        assert "(2)" in captured.out
        assert "3.0.0" in captured.out

    @pytest.mark.asyncio
    async def test_list_no_systems(self, fw_db, capsys, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        await bios_list("R999", fw_db)
        captured = capsys.readouterr()
        assert "No systems found" in captured.out


class TestBiosApply:
    @pytest.mark.asyncio
    async def test_version_not_available(self, fw_db, capsys, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        await bios_apply("9.99.99", "host01.example.com", False, True, fw_db)
        captured = capsys.readouterr()
        assert "not available" in captured.out

    @pytest.mark.asyncio
    async def test_version_not_running_no_force(
        self, fw_db, capsys, tmp_path, monkeypatch
    ):
        ini = tmp_path / "BIOS-filename.ini"
        ini.write_text("[R660]\n3.0.0 = BIOS3.EXE\n")
        monkeypatch.chdir(tmp_path)
        await bios_apply("3.0.0", "host01.example.com", False, True, fw_db)
        captured = capsys.readouterr()
        assert "not running on any" in captured.out

    @pytest.mark.asyncio
    async def test_version_not_running_with_force(
        self, fw_db, capsys, tmp_path, monkeypatch
    ):
        ini = tmp_path / "BIOS-filename.ini"
        ini.write_text("[R660]\n3.0.0 = BIOS3.EXE\n")
        monkeypatch.chdir(tmp_path)
        with patch("dracs.jobqueue.enqueue_job", return_value=42):
            await bios_apply("3.0.0", "host01.example.com", True, True, fw_db)
        captured = capsys.readouterr()
        assert "queued" in captured.out

    @pytest.mark.asyncio
    async def test_version_running_with_yes(self, fw_db, capsys, tmp_path, monkeypatch):
        ini = tmp_path / "BIOS-filename.ini"
        ini.write_text("[R660]\n2.5.0 = BIOS.EXE\n")
        monkeypatch.chdir(tmp_path)
        with patch("dracs.jobqueue.enqueue_job", return_value=42):
            await bios_apply("2.5.0", "host01.example.com", False, True, fw_db)
        captured = capsys.readouterr()
        assert "queued" in captured.out

    @pytest.mark.asyncio
    async def test_version_running_prompt_cancel(
        self, fw_db, capsys, tmp_path, monkeypatch
    ):
        ini = tmp_path / "BIOS-filename.ini"
        ini.write_text("[R660]\n2.5.0 = BIOS.EXE\n")
        monkeypatch.chdir(tmp_path)
        with patch("builtins.input", return_value="n"):
            await bios_apply("2.5.0", "host01.example.com", False, False, fw_db)
        captured = capsys.readouterr()
        assert "Cancelled" in captured.out

    @pytest.mark.asyncio
    async def test_host_not_found(self, fw_db, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        with pytest.raises(DatabaseError, match="not found"):
            await bios_apply("2.5.0", "unknown.example.com", False, True, fw_db)


class TestCliRouting:
    @patch("dracs.commands.fw_list", new_callable=AsyncMock)
    @patch("dracs.cli.db_initialize")
    def test_fw_list(self, mock_db, mock_list):
        mock_list.return_value = None
        run_main_with_args(["fw", "--list"])
        mock_list.assert_called_once()

    @patch("dracs.commands.fw_list", new_callable=AsyncMock)
    @patch("dracs.cli.db_initialize")
    def test_fw_list_with_model(self, mock_db, mock_list):
        mock_list.return_value = None
        run_main_with_args(["fw", "--list", "-m", "R660"])
        mock_list.assert_called_once()
        assert mock_list.call_args[0][0] == "R660"

    @patch("dracs.commands.fw_apply", new_callable=AsyncMock)
    @patch("dracs.cli.db_initialize")
    def test_fw_apply(self, mock_db, mock_apply):
        mock_apply.return_value = None
        run_main_with_args(["fw", "--apply", "--version", "7.00.00", "-t", "host01"])
        mock_apply.assert_called_once()

    @patch("dracs.cli.db_initialize")
    def test_fw_apply_missing_args(self, mock_db, capsys):
        with pytest.raises(SystemExit) as exc_info:
            run_main_with_args(["fw", "--apply"])
        assert exc_info.value.code == 1

    @patch("dracs.commands.bios_list", new_callable=AsyncMock)
    @patch("dracs.cli.db_initialize")
    def test_bios_list(self, mock_db, mock_list):
        mock_list.return_value = None
        run_main_with_args(["bios", "--list"])
        mock_list.assert_called_once()

    @patch("dracs.commands.bios_apply", new_callable=AsyncMock)
    @patch("dracs.cli.db_initialize")
    def test_bios_apply(self, mock_db, mock_apply):
        mock_apply.return_value = None
        run_main_with_args(["bios", "--apply", "--version", "2.5.0", "-t", "host01"])
        mock_apply.assert_called_once()

    @patch("dracs.cli.db_initialize")
    def test_bios_apply_missing_args(self, mock_db, capsys):
        with pytest.raises(SystemExit) as exc_info:
            run_main_with_args(["bios", "--apply"])
        assert exc_info.value.code == 1

import os
import tempfile
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from dracs.commands import (
    TSR_DIR,
    _scan_tsr_entries,
    tsr_download,
    tsr_generate,
    tsr_list,
    tsr_status,
)
from dracs.db import db_initialize, upsert_system
from dracs.exceptions import DatabaseError


@pytest.fixture
def tsr_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    db_initialize(path)
    upsert_system(
        path,
        "TAG001",
        "server01.example.com",
        "R660",
        "7.0.0",
        "2.1.0",
        "Jan 1, 2027",
        1893456000,
    )
    yield path
    if os.path.exists(path):
        os.unlink(path)


@pytest.fixture
def tsr_dir(tmp_path):
    host_dir = tmp_path / "server01.example.com"
    host_dir.mkdir()
    (host_dir / "TSR20260505170637_TAG001.zip").write_bytes(b"fake1")
    (host_dir / "TSR20260501120000_TAG001.zip").write_bytes(b"fake2")
    (host_dir / "TSR20260415080000_TAG001.zip").write_bytes(b"fake3")
    return tmp_path


class TestScanTsrEntries:
    def test_scans_files(self, tsr_dir):
        with patch("dracs.commands.TSR_DIR", str(tsr_dir)):
            entries = _scan_tsr_entries("server01.example.com")
        assert len(entries) == 3
        assert entries[0]["date"] == "2026/05/05 17:06:37"

    def test_sorted_descending(self, tsr_dir):
        with patch("dracs.commands.TSR_DIR", str(tsr_dir)):
            entries = _scan_tsr_entries("server01.example.com")
        dates = [e["date"] for e in entries]
        assert dates == sorted(dates, reverse=True)

    def test_no_host_dir(self, tmp_path):
        with patch("dracs.commands.TSR_DIR", str(tmp_path)):
            entries = _scan_tsr_entries("nonexistent.example.com")
        assert entries == []

    def test_malformed_filename_skipped(self, tmp_path):
        host_dir = tmp_path / "server01.example.com"
        host_dir.mkdir()
        (host_dir / "TSR20260505170637_TAG001.zip").write_bytes(b"fake")
        (host_dir / "TSRbadtime_TAG001.zip").write_bytes(b"fake")
        with patch("dracs.commands.TSR_DIR", str(tmp_path)):
            entries = _scan_tsr_entries("server01.example.com")
        assert len(entries) == 1


class TestTsrList:
    @pytest.mark.asyncio
    async def test_lists_tsrs(self, tsr_db, tsr_dir, capsys):
        with patch("dracs.commands.TSR_DIR", str(tsr_dir)):
            with patch(
                "dracs.commands.socket.getfqdn", return_value="dracs.example.com"
            ):
                await tsr_list("server01.example.com", tsr_db)
        captured = capsys.readouterr()
        assert "Date: 2026/05/05" in captured.out
        assert "TSR" in captured.out

    @pytest.mark.asyncio
    async def test_list_with_last(self, tsr_db, tsr_dir, capsys):
        with patch("dracs.commands.TSR_DIR", str(tsr_dir)):
            with patch(
                "dracs.commands.socket.getfqdn", return_value="dracs.example.com"
            ):
                await tsr_list("server01.example.com", tsr_db, last=1)
        captured = capsys.readouterr()
        assert "2026/05/05" in captured.out
        assert "2026/05/01" not in captured.out

    @pytest.mark.asyncio
    async def test_list_no_tsrs(self, tsr_db, tmp_path, capsys):
        with patch("dracs.commands.TSR_DIR", str(tmp_path)):
            await tsr_list("server01.example.com", tsr_db)
        captured = capsys.readouterr()
        assert "No TSR collections found" in captured.out

    @pytest.mark.asyncio
    async def test_list_host_not_found(self, tsr_db):
        with pytest.raises(DatabaseError, match="not found"):
            await tsr_list("nonexistent.example.com", tsr_db)


class TestTsrDownload:
    @pytest.mark.asyncio
    async def test_downloads_newest(self, tsr_db, tsr_dir, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        with patch("dracs.commands.TSR_DIR", str(tsr_dir)):
            await tsr_download("server01.example.com", tsr_db)
        assert (tmp_path / "TSR20260505170637_TAG001.zip").exists()

    @pytest.mark.asyncio
    async def test_download_no_tsrs(self, tsr_db, tmp_path, capsys):
        with patch("dracs.commands.TSR_DIR", str(tmp_path)):
            await tsr_download("server01.example.com", tsr_db)
        captured = capsys.readouterr()
        assert "No TSR collections found" in captured.out

    @pytest.mark.asyncio
    async def test_download_host_not_found(self, tsr_db):
        with pytest.raises(DatabaseError, match="not found"):
            await tsr_download("nonexistent.example.com", tsr_db)


class TestTsrGenerate:
    @pytest.mark.asyncio
    async def test_generate_enqueues_job(self, tsr_db, capsys):
        with patch("dracs.jobqueue.get_latest_job_for_host", return_value=None):
            with patch("dracs.jobqueue.enqueue_job", return_value=42) as mock_enqueue:
                await tsr_generate("server01.example.com", tsr_db)
        mock_enqueue.assert_called_once_with("tsr", "server01.example.com")
        captured = capsys.readouterr()
        assert "queued" in captured.out
        assert "42" in captured.out

    @pytest.mark.asyncio
    async def test_generate_host_not_found(self, tsr_db):
        with pytest.raises(DatabaseError, match="not found"):
            await tsr_generate("nonexistent.example.com", tsr_db)

    @pytest.mark.asyncio
    async def test_generate_skips_when_running(self, tsr_db, capsys):
        existing = {
            "id": 10,
            "status": "running",
            "result": "45%",
        }
        with patch("dracs.jobqueue.get_latest_job_for_host", return_value=existing):
            await tsr_generate("server01.example.com", tsr_db)
        captured = capsys.readouterr()
        assert "already in progress" in captured.out
        assert "45%" in captured.out
        assert "job 10" in captured.out

    @pytest.mark.asyncio
    async def test_generate_skips_when_pending(self, tsr_db, capsys):
        existing = {
            "id": 11,
            "status": "pending",
            "result": None,
        }
        with patch("dracs.jobqueue.get_latest_job_for_host", return_value=existing):
            await tsr_generate("server01.example.com", tsr_db)
        captured = capsys.readouterr()
        assert "already in progress" in captured.out
        assert "job 11" in captured.out


class TestTsrStatus:
    @pytest.mark.asyncio
    async def test_status_running_with_progress(self, tsr_db, capsys):
        mock_job = {
            "status": "running",
            "job_type": "tsr",
            "target": "server01.example.com",
            "result": "45%",
        }
        with patch("dracs.jobqueue.get_latest_job_for_host", return_value=mock_job):
            await tsr_status("server01.example.com", tsr_db)
        captured = capsys.readouterr()
        assert "45%" in captured.out
        assert "Completed" in captured.out

    @pytest.mark.asyncio
    async def test_status_running_phase_label(self, tsr_db, capsys):
        mock_job = {
            "status": "running",
            "job_type": "tsr",
            "target": "server01.example.com",
            "result": "Exporting",
        }
        with patch("dracs.jobqueue.get_latest_job_for_host", return_value=mock_job):
            await tsr_status("server01.example.com", tsr_db)
        captured = capsys.readouterr()
        assert "Exporting" in captured.out

    @pytest.mark.asyncio
    async def test_status_running_no_progress(self, tsr_db, capsys):
        mock_job = {
            "status": "running",
            "job_type": "tsr",
            "target": "server01.example.com",
            "result": None,
        }
        with patch("dracs.jobqueue.get_latest_job_for_host", return_value=mock_job):
            await tsr_status("server01.example.com", tsr_db)
        captured = capsys.readouterr()
        assert "TSR Collection in progress." in captured.out

    @pytest.mark.asyncio
    async def test_status_pending(self, tsr_db, capsys):
        mock_job = {
            "status": "pending",
            "job_type": "tsr",
            "target": "server01.example.com",
        }
        with patch("dracs.jobqueue.get_latest_job_for_host", return_value=mock_job):
            await tsr_status("server01.example.com", tsr_db)
        captured = capsys.readouterr()
        assert "TSR Collection pending." in captured.out

    @pytest.mark.asyncio
    async def test_status_none(self, tsr_db, capsys):
        with patch("dracs.jobqueue.get_latest_job_for_host", return_value=None):
            await tsr_status("server01.example.com", tsr_db)
        captured = capsys.readouterr()
        assert "No TSR Collection in progress." in captured.out

    @pytest.mark.asyncio
    async def test_status_completed_shows_none(self, tsr_db, capsys):
        mock_job = {
            "status": "completed",
            "job_type": "tsr",
            "target": "server01.example.com",
        }
        with patch("dracs.jobqueue.get_latest_job_for_host", return_value=mock_job):
            await tsr_status("server01.example.com", tsr_db)
        captured = capsys.readouterr()
        assert "No TSR Collection in progress." in captured.out

    @pytest.mark.asyncio
    async def test_status_host_not_found(self, tsr_db):
        with pytest.raises(DatabaseError, match="not found"):
            await tsr_status("nonexistent.example.com", tsr_db)

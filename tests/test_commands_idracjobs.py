import os
import tempfile
from unittest.mock import MagicMock, patch

import pytest

from dracs.commands import idrac_jobs_clear, idrac_jobs_list
from dracs.db import db_initialize, upsert_system
from dracs.exceptions import DatabaseError, DracsError, ValidationError


@pytest.fixture
def ij_db():
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
    upsert_system(
        path,
        "TAG002",
        "server02.example.com",
        "R660",
        "7.0.0",
        "2.1.0",
        "Jan 1, 2027",
        1893456000,
    )
    yield path
    if os.path.exists(path):
        os.unlink(path)


class TestIdracJobsList:
    @pytest.mark.asyncio
    async def test_lists_jobs(self, ij_db, capsys):
        mock_cmd = MagicMock(return_value=["echo"])
        mock_result = MagicMock(
            returncode=0,
            stdout=(
                "[Job ID=JID_001]\n"
                "Job Name=Firmware Update\n"
                "Status=Completed\n"
                "Percent Complete=100\n"
                "Actual Start Time=2026-05-20T10:00:00\n"
                "Actual Completion Time=2026-05-20T10:05:00\n"
                "Message=Job completed successfully\n"
            ),
        )
        with patch("subprocess.run", return_value=mock_result):
            with patch("dracs.webapp._build_ssh_racadm_cmd", mock_cmd):
                await idrac_jobs_list("server01.example.com", ij_db)
        captured = capsys.readouterr()
        assert "JID_001" in captured.out
        assert "Firmware" in captured.out
        assert "Completed" in captured.out

    @pytest.mark.asyncio
    async def test_no_jobs(self, ij_db, capsys):
        mock_cmd = MagicMock(return_value=["echo"])
        mock_result = MagicMock(returncode=0, stdout="")
        with patch("subprocess.run", return_value=mock_result):
            with patch("dracs.webapp._build_ssh_racadm_cmd", mock_cmd):
                await idrac_jobs_list("server01.example.com", ij_db)
        captured = capsys.readouterr()
        assert "No jobs" in captured.out

    @pytest.mark.asyncio
    async def test_host_not_found(self, ij_db):
        with pytest.raises(DatabaseError, match="not found"):
            await idrac_jobs_list("unknown.example.com", ij_db)

    @pytest.mark.asyncio
    async def test_command_failure(self, ij_db):
        mock_cmd = MagicMock(return_value=["echo"])
        mock_result = MagicMock(returncode=1, stderr="Connection refused", stdout="")
        with patch("subprocess.run", return_value=mock_result):
            with patch("dracs.webapp._build_ssh_racadm_cmd", mock_cmd):
                with pytest.raises(DracsError, match="Failed to query"):
                    await idrac_jobs_list("server01.example.com", ij_db)


class TestIdracJobsClear:
    @pytest.mark.asyncio
    async def test_clear_single_host_with_force(self, ij_db, capsys):
        with patch("dracs.jobqueue.enqueue_job", return_value=1) as mock_enq:
            await idrac_jobs_clear("server01.example.com", None, False, True, ij_db)
        mock_enq.assert_called_once_with("clear_job_queue", "server01.example.com")
        captured = capsys.readouterr()
        assert "queued" in captured.out

    @pytest.mark.asyncio
    async def test_clear_single_host_confirm(self, ij_db, capsys):
        with patch("builtins.input", return_value="y"):
            with patch("dracs.jobqueue.enqueue_job", return_value=1):
                await idrac_jobs_clear(
                    "server01.example.com", None, False, False, ij_db
                )
        captured = capsys.readouterr()
        assert "queued" in captured.out

    @pytest.mark.asyncio
    async def test_clear_single_host_cancel(self, ij_db, capsys):
        with patch("builtins.input", return_value="n"):
            await idrac_jobs_clear("server01.example.com", None, False, False, ij_db)
        captured = capsys.readouterr()
        assert "Cancelled" in captured.out

    @pytest.mark.asyncio
    async def test_clear_by_model_with_force(self, ij_db, capsys):
        with patch("dracs.jobqueue.enqueue_batch", return_value=2) as mock_batch:
            await idrac_jobs_clear(None, "R660", False, True, ij_db)
        mock_batch.assert_called_once_with("clear_job_queue", "model:R660")
        captured = capsys.readouterr()
        assert "2" in captured.out
        assert "R660" in captured.out

    @pytest.mark.asyncio
    async def test_clear_all_with_force(self, ij_db, capsys):
        with patch("dracs.jobqueue.enqueue_batch", return_value=2) as mock_batch:
            await idrac_jobs_clear(None, None, True, True, ij_db)
        mock_batch.assert_called_once_with("clear_job_queue", "all")
        captured = capsys.readouterr()
        assert "2" in captured.out

    @pytest.mark.asyncio
    async def test_clear_all_confirm(self, ij_db, capsys):
        with patch("builtins.input", return_value="yes"):
            with patch("dracs.jobqueue.enqueue_batch", return_value=2):
                await idrac_jobs_clear(None, None, True, False, ij_db)
        captured = capsys.readouterr()
        assert "queued" in captured.out

    @pytest.mark.asyncio
    async def test_clear_all_cancel(self, ij_db, capsys):
        with patch("builtins.input", return_value="n"):
            await idrac_jobs_clear(None, None, True, False, ij_db)
        captured = capsys.readouterr()
        assert "Cancelled" in captured.out

    @pytest.mark.asyncio
    async def test_clear_no_option_raises(self, ij_db):
        with pytest.raises(ValidationError, match="required"):
            await idrac_jobs_clear(None, None, False, False, ij_db)

    @pytest.mark.asyncio
    async def test_clear_host_not_found(self, ij_db):
        with pytest.raises(DatabaseError, match="not found"):
            await idrac_jobs_clear("unknown.example.com", None, False, True, ij_db)

    @pytest.mark.asyncio
    async def test_clear_model_not_found(self, ij_db):
        with pytest.raises(DatabaseError, match="No systems found"):
            await idrac_jobs_clear(None, "R999", False, True, ij_db)

    @pytest.mark.asyncio
    async def test_clear_model_confirm_cancel(self, ij_db, capsys):
        with patch("builtins.input", return_value="n"):
            await idrac_jobs_clear(None, "R660", False, False, ij_db)
        captured = capsys.readouterr()
        assert "Cancelled" in captured.out

    @pytest.mark.asyncio
    async def test_clear_all_empty_db(self):
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        db_initialize(path)
        try:
            with pytest.raises(DatabaseError, match="No systems found"):
                await idrac_jobs_clear(None, None, True, True, path)
        finally:
            os.unlink(path)

import os
import tempfile

import pytest

from dracs.commands import cancel_job_cmd, clear_jobs, list_jobs
from dracs.db import db_initialize, get_session, Job
from dracs.jobqueue import complete_job, enqueue_job, claim_next_job


@pytest.fixture
def job_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    db_initialize(path)
    yield path
    if os.path.exists(path):
        os.unlink(path)


class TestListJobs:
    @pytest.mark.asyncio
    async def test_lists_active_jobs(self, job_db, capsys):
        enqueue_job("tsr", "host01.example.com")
        enqueue_job("refresh", "host02.example.com")
        await list_jobs(False, job_db)
        captured = capsys.readouterr()
        assert "tsr" in captured.out
        assert "refresh" in captured.out
        assert "host01" in captured.out

    @pytest.mark.asyncio
    async def test_no_jobs(self, job_db, capsys):
        await list_jobs(False, job_db)
        captured = capsys.readouterr()
        assert "No jobs found" in captured.out

    @pytest.mark.asyncio
    async def test_excludes_completed_by_default(self, job_db, capsys):
        job_id = enqueue_job("tsr", "host01.example.com")
        claim_next_job("w1")
        complete_job(job_id)
        await list_jobs(False, job_db)
        captured = capsys.readouterr()
        assert "No jobs found" in captured.out

    @pytest.mark.asyncio
    async def test_includes_completed_with_all(self, job_db, capsys):
        job_id = enqueue_job("tsr", "host01.example.com")
        claim_next_job("w1")
        complete_job(job_id)
        await list_jobs(True, job_db)
        captured = capsys.readouterr()
        assert "tsr" in captured.out
        assert "completed" in captured.out

    @pytest.mark.asyncio
    async def test_shows_batch_progress(self, job_db, capsys):
        parent_id = enqueue_job("tsr", "all")
        child1 = enqueue_job("tsr", "host01.example.com", parent_id=parent_id)
        enqueue_job("tsr", "host02.example.com", parent_id=parent_id)

        with get_session() as session:
            parent = session.get(Job, parent_id)
            parent.status = "running"
            session.commit()

        claim_next_job("w1")
        complete_job(child1)

        await list_jobs(False, job_db)
        captured = capsys.readouterr()
        assert "1/2" in captured.out


class TestClearJobs:
    @pytest.mark.asyncio
    async def test_purges_old_jobs(self, job_db, capsys):
        job_id = enqueue_job("tsr", "host01.example.com")
        claim_next_job("w1")
        complete_job(job_id)

        with get_session() as session:
            job = session.get(Job, job_id)
            job.completed_at = "2020-01-01T00:00:00"
            session.commit()

        with patch_purge_days("1"):
            await clear_jobs(job_db)
        captured = capsys.readouterr()
        assert "Purged 1" in captured.out

    @pytest.mark.asyncio
    async def test_purges_nothing_when_recent(self, job_db, capsys):
        job_id = enqueue_job("tsr", "host01.example.com")
        claim_next_job("w1")
        complete_job(job_id)

        with patch_purge_days("7"):
            await clear_jobs(job_db)
        captured = capsys.readouterr()
        assert "Purged 0" in captured.out


class TestCancelJobCmd:
    @pytest.mark.asyncio
    async def test_cancels_pending(self, job_db, capsys):
        job_id = enqueue_job("tsr", "host01.example.com")
        await cancel_job_cmd(job_id, job_db)
        captured = capsys.readouterr()
        assert "cancelled" in captured.out

    @pytest.mark.asyncio
    async def test_cannot_cancel_running(self, job_db, capsys):
        job_id = enqueue_job("tsr", "host01.example.com")
        claim_next_job("w1")
        await cancel_job_cmd(job_id, job_db)
        captured = capsys.readouterr()
        assert "cannot be cancelled" in captured.out

    @pytest.mark.asyncio
    async def test_nonexistent_job(self, job_db, capsys):
        await cancel_job_cmd(9999, job_db)
        captured = capsys.readouterr()
        assert "cannot be cancelled" in captured.out


def patch_purge_days(days):
    from unittest.mock import patch

    return patch.dict(os.environ, {"JOB_PURGE_DAYS": days})

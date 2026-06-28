import os
import tempfile
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from dracs.db import Job, db_initialize, get_session
from dracs.jobqueue import (
    _prune_tsr_before_collect,
    cancel_job,
    claim_next_job,
    complete_job,
    enqueue_job,
    fail_job,
    get_active_jobs,
    get_job_status,
    get_jobs_for_host,
    get_latest_job_for_host,
    parse_schedule_config,
    purge_completed_jobs,
)


@pytest.fixture
def job_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    db_initialize(path)
    yield path
    if os.path.exists(path):
        os.unlink(path)


class TestEnqueueJob:
    def test_enqueue_returns_id(self, job_db):
        job_id = enqueue_job("tsr", "host01.example.com")
        assert isinstance(job_id, int)
        assert job_id > 0

    def test_enqueue_sets_pending_status(self, job_db):
        job_id = enqueue_job("tsr", "host01.example.com")
        status = get_job_status(job_id)
        assert status["status"] == "pending"
        assert status["job_type"] == "tsr"
        assert status["target"] == "host01.example.com"
        assert status["created_at"] is not None

    def test_enqueue_with_parent(self, job_db):
        parent_id = enqueue_job("tsr", "all")
        child_id = enqueue_job("tsr", "host01.example.com", parent_id=parent_id)
        child = get_job_status(child_id)
        assert child["parent_id"] == parent_id

    def test_enqueue_multiple(self, job_db):
        id1 = enqueue_job("tsr", "host01.example.com")
        id2 = enqueue_job("refresh", "host02.example.com")
        assert id1 != id2


class TestClaimNextJob:
    def test_claims_oldest_first(self, job_db):
        enqueue_job("tsr", "host01.example.com")
        enqueue_job("tsr", "host02.example.com")
        claimed = claim_next_job("worker-1")
        assert claimed["target"] == "host01.example.com"

    def test_sets_running_status(self, job_db):
        job_id = enqueue_job("tsr", "host01.example.com")
        claim_next_job("worker-1")
        status = get_job_status(job_id)
        assert status["status"] == "running"
        assert status["worker_id"] == "worker-1"
        assert status["started_at"] is not None

    def test_skips_running_jobs(self, job_db):
        enqueue_job("tsr", "host01.example.com")
        enqueue_job("tsr", "host02.example.com")
        claim_next_job("worker-1")
        claimed = claim_next_job("worker-2")
        assert claimed["target"] == "host02.example.com"

    def test_returns_none_when_empty(self, job_db):
        result = claim_next_job("worker-1")
        assert result is None

    def test_returns_none_when_all_claimed(self, job_db):
        enqueue_job("tsr", "host01.example.com")
        claim_next_job("worker-1")
        result = claim_next_job("worker-2")
        assert result is None

    def test_returns_job_fields(self, job_db):
        parent_id = enqueue_job("tsr", "all")
        enqueue_job("tsr", "host01.example.com", parent_id=parent_id)
        # Claim the parent first (it's oldest)
        claim_next_job("worker-1")
        claimed = claim_next_job("worker-2")
        assert "id" in claimed
        assert "parent_id" in claimed
        assert "job_type" in claimed
        assert "target" in claimed


class TestCompleteJob:
    def test_sets_completed_status(self, job_db):
        job_id = enqueue_job("tsr", "host01.example.com")
        claim_next_job("worker-1")
        complete_job(job_id, result="Success")
        status = get_job_status(job_id)
        assert status["status"] == "completed"
        assert status["result"] == "Success"
        assert status["completed_at"] is not None

    def test_completes_parent_when_all_children_done(self, job_db):
        parent_id = enqueue_job("tsr", "all")
        child1 = enqueue_job("tsr", "host01.example.com", parent_id=parent_id)
        child2 = enqueue_job("tsr", "host02.example.com", parent_id=parent_id)

        with get_session() as session:
            parent = session.get(Job, parent_id)
            parent.status = "running"
            session.commit()

        claim_next_job("w1")
        claim_next_job("w2")
        complete_job(child1)
        parent_status = get_job_status(parent_id)
        assert parent_status["status"] == "running"

        complete_job(child2)
        parent_status = get_job_status(parent_id)
        assert parent_status["status"] == "completed"
        assert "2 completed" in parent_status["result"]

    def test_nonexistent_job_is_noop(self, job_db):
        complete_job(9999)


class TestFailJob:
    def test_sets_failed_status(self, job_db):
        job_id = enqueue_job("tsr", "host01.example.com")
        claim_next_job("worker-1")
        fail_job(job_id, error="SSH timeout")
        status = get_job_status(job_id)
        assert status["status"] == "failed"
        assert status["error"] == "SSH timeout"
        assert status["completed_at"] is not None

    def test_parent_fails_if_any_child_fails(self, job_db):
        parent_id = enqueue_job("tsr", "all")
        child1 = enqueue_job("tsr", "host01.example.com", parent_id=parent_id)
        child2 = enqueue_job("tsr", "host02.example.com", parent_id=parent_id)

        with get_session() as session:
            parent = session.get(Job, parent_id)
            parent.status = "running"
            session.commit()

        claim_next_job("w1")
        claim_next_job("w2")
        complete_job(child1)
        fail_job(child2, error="SSH timeout")

        parent_status = get_job_status(parent_id)
        assert parent_status["status"] == "failed"
        assert "1 completed, 1 failed" in parent_status["result"]

    def test_nonexistent_job_is_noop(self, job_db):
        fail_job(9999, error="test")


class TestCancelJob:
    def test_cancels_pending_job(self, job_db):
        job_id = enqueue_job("tsr", "host01.example.com")
        result = cancel_job(job_id)
        assert result is True
        status = get_job_status(job_id)
        assert status["status"] == "failed"
        assert status["error"] == "Cancelled"

    def test_cannot_cancel_running_job(self, job_db):
        job_id = enqueue_job("tsr", "host01.example.com")
        claim_next_job("worker-1")
        result = cancel_job(job_id)
        assert result is False
        status = get_job_status(job_id)
        assert status["status"] == "running"

    def test_cannot_cancel_nonexistent(self, job_db):
        result = cancel_job(9999)
        assert result is False

    def test_cancel_updates_parent(self, job_db):
        parent_id = enqueue_job("tsr", "all")
        child1 = enqueue_job("tsr", "host01.example.com", parent_id=parent_id)
        child2 = enqueue_job("tsr", "host02.example.com", parent_id=parent_id)

        with get_session() as session:
            parent = session.get(Job, parent_id)
            parent.status = "running"
            session.commit()

        claim_next_job("w1")
        complete_job(child1)
        cancel_job(child2)

        parent_status = get_job_status(parent_id)
        assert parent_status["status"] == "failed"


class TestGetJobStatus:
    def test_returns_dict(self, job_db):
        job_id = enqueue_job("tsr", "host01.example.com")
        status = get_job_status(job_id)
        assert isinstance(status, dict)
        assert status["id"] == job_id

    def test_returns_none_for_nonexistent(self, job_db):
        assert get_job_status(9999) is None


class TestGetJobsForHost:
    def test_returns_host_jobs(self, job_db):
        enqueue_job("tsr", "host01.example.com")
        enqueue_job("refresh", "host01.example.com")
        enqueue_job("tsr", "host02.example.com")
        jobs = get_jobs_for_host("host01.example.com")
        assert len(jobs) == 2

    def test_returns_empty_for_unknown_host(self, job_db):
        jobs = get_jobs_for_host("unknown.example.com")
        assert jobs == []

    def test_ordered_newest_first(self, job_db):
        enqueue_job("tsr", "host01.example.com")
        enqueue_job("refresh", "host01.example.com")
        jobs = get_jobs_for_host("host01.example.com")
        assert jobs[0]["job_type"] == "refresh"


class TestGetActiveJobs:
    def test_returns_only_top_level(self, job_db):
        parent_id = enqueue_job("tsr", "all")
        enqueue_job("tsr", "host01.example.com", parent_id=parent_id)
        jobs = get_active_jobs()
        assert len(jobs) == 1
        assert jobs[0]["target"] == "all"

    def test_excludes_completed_by_default(self, job_db):
        job_id = enqueue_job("tsr", "host01.example.com")
        claim_next_job("w1")
        complete_job(job_id)
        jobs = get_active_jobs()
        assert len(jobs) == 0

    def test_includes_completed_when_requested(self, job_db):
        job_id = enqueue_job("tsr", "host01.example.com")
        claim_next_job("w1")
        complete_job(job_id)
        jobs = get_active_jobs(include_completed=True)
        assert len(jobs) == 1

    def test_shows_progress_for_batch(self, job_db):
        parent_id = enqueue_job("tsr", "all")
        child1 = enqueue_job("tsr", "host01.example.com", parent_id=parent_id)
        enqueue_job("tsr", "host02.example.com", parent_id=parent_id)

        with get_session() as session:
            parent = session.get(Job, parent_id)
            parent.status = "running"
            session.commit()

        claim_next_job("w1")
        claim_next_job("w2")
        complete_job(child1)

        jobs = get_active_jobs()
        assert len(jobs) == 1
        assert jobs[0]["progress"] == "1/2"


class TestGetLatestJobForHost:
    def test_returns_latest(self, job_db):
        enqueue_job("tsr", "host01.example.com")
        enqueue_job("tsr", "host01.example.com")
        latest = get_latest_job_for_host("host01.example.com", "tsr")
        assert latest is not None

    def test_filters_by_type(self, job_db):
        enqueue_job("tsr", "host01.example.com")
        enqueue_job("refresh", "host01.example.com")
        latest = get_latest_job_for_host("host01.example.com", "refresh")
        assert latest["job_type"] == "refresh"

    def test_returns_none_for_no_match(self, job_db):
        assert get_latest_job_for_host("host01.example.com", "tsr") is None


class TestPurgeCompletedJobs:
    def test_purges_old_completed(self, job_db):
        job_id = enqueue_job("tsr", "host01.example.com")
        claim_next_job("w1")
        complete_job(job_id)

        with get_session() as session:
            job = session.get(Job, job_id)
            job.completed_at = "2020-01-01T00:00:00"
            session.commit()

        count = purge_completed_jobs(older_than_days=1)
        assert count == 1
        assert get_job_status(job_id) is None

    def test_keeps_recent_completed(self, job_db):
        job_id = enqueue_job("tsr", "host01.example.com")
        claim_next_job("w1")
        complete_job(job_id)

        count = purge_completed_jobs(older_than_days=1)
        assert count == 0
        assert get_job_status(job_id) is not None

    def test_keeps_running_jobs(self, job_db):
        job_id = enqueue_job("tsr", "host01.example.com")
        claim_next_job("w1")

        count = purge_completed_jobs(older_than_days=0)
        assert count == 0

    def test_purges_parent_only_after_children(self, job_db):
        parent_id = enqueue_job("tsr", "all")
        child_id = enqueue_job("tsr", "host01.example.com", parent_id=parent_id)

        with get_session() as session:
            parent = session.get(Job, parent_id)
            parent.status = "running"
            session.commit()

        claim_next_job("w1")
        complete_job(child_id)

        with get_session() as session:
            for job in session.query(Job).all():
                job.completed_at = "2020-01-01T00:00:00"
            session.commit()

        count = purge_completed_jobs(older_than_days=1)
        assert count == 2

    def test_purges_old_failed(self, job_db):
        job_id = enqueue_job("tsr", "host01.example.com")
        claim_next_job("w1")
        fail_job(job_id, "error")

        with get_session() as session:
            job = session.get(Job, job_id)
            job.completed_at = "2020-01-01T00:00:00"
            session.commit()

        count = purge_completed_jobs(older_than_days=1)
        assert count == 1


class TestUpdateParentEdgeCases:
    def test_no_children(self, job_db):
        from dracs.jobqueue import _update_parent_status

        parent_id = enqueue_job("tsr", "all")
        with get_session() as session:
            _update_parent_status(session, parent_id)
            parent = session.get(Job, parent_id)
            assert parent.status == "pending"

    def test_nonexistent_parent(self, job_db):
        from dracs.jobqueue import _update_parent_status

        child_id = enqueue_job("tsr", "host01.example.com", parent_id=9999)
        claim_next_job("w1")
        with get_session() as session:
            child = session.get(Job, child_id)
            child.status = "completed"
            session.commit()
            _update_parent_status(session, 9999)


class TestUpdateJobProgress:
    def test_updates_result_field(self, job_db):
        from dracs.jobqueue import update_job_progress

        job_id = enqueue_job("tsr", "host01.example.com")
        update_job_progress(job_id, "45%")
        status = get_job_status(job_id)
        assert status["result"] == "45%"

    def test_updates_multiple_times(self, job_db):
        from dracs.jobqueue import update_job_progress

        job_id = enqueue_job("tsr", "host01.example.com")
        update_job_progress(job_id, "Collecting")
        update_job_progress(job_id, "25%")
        update_job_progress(job_id, "50%")
        status = get_job_status(job_id)
        assert status["result"] == "50%"

    def test_nonexistent_job_is_noop(self, job_db):
        from dracs.jobqueue import update_job_progress

        update_job_progress(9999, "50%")


class TestRecoverStaleJobs:
    def test_recovers_running_jobs(self, job_db):
        from dracs.jobqueue import recover_stale_jobs

        job_id = enqueue_job("tsr", "host01.example.com")
        claim_next_job("w1")
        status = get_job_status(job_id)
        assert status["status"] == "running"

        count = recover_stale_jobs()
        assert count == 1
        status = get_job_status(job_id)
        assert status["status"] == "pending"
        assert status["worker_id"] is None
        assert status["started_at"] is None

    def test_no_stale_jobs(self, job_db):
        from dracs.jobqueue import recover_stale_jobs

        enqueue_job("tsr", "host01.example.com")
        count = recover_stale_jobs()
        assert count == 0

    def test_preserves_pending_jobs(self, job_db):
        from dracs.jobqueue import recover_stale_jobs

        job_id = enqueue_job("tsr", "host01.example.com")
        recover_stale_jobs()
        status = get_job_status(job_id)
        assert status["status"] == "pending"


class TestEnqueueJobWithMetadata:
    def test_metadata_stored(self, job_db):
        job_id = enqueue_job(
            "firmware_update",
            "host01.example.com",
            metadata={"target_version": "8.0.0", "model": "R660"},
        )
        status = get_job_status(job_id)
        assert status["metadata"] == {"target_version": "8.0.0", "model": "R660"}

    def test_no_metadata(self, job_db):
        job_id = enqueue_job("tsr", "host01.example.com")
        status = get_job_status(job_id)
        assert status["metadata"] is None

    def test_metadata_in_claimed_job(self, job_db):
        enqueue_job(
            "bios_update",
            "host01.example.com",
            metadata={"target_bios": "2.10.0", "model": "R660"},
        )
        claimed = claim_next_job("w1")
        assert claimed["metadata"] == {"target_bios": "2.10.0", "model": "R660"}


class TestParsedScheduleKeepMax:
    def test_valid_keep_max_parsed_as_int(self, tmp_path):
        ini = tmp_path / "schedule.ini"
        ini.write_text(
            "[tsr-weekly]\ntype = tsr\nschedule = weekly\nday = sunday\n"
            "time = 02:00\ntarget = all\nkeep_max = 3\n"
        )
        tasks = parse_schedule_config(str(ini))
        assert len(tasks) == 1
        assert tasks[0]["keep_max"] == 3

    def test_invalid_keep_max_defaults_to_none(self, tmp_path):
        ini = tmp_path / "schedule.ini"
        ini.write_text(
            "[tsr-weekly]\ntype = tsr\nschedule = weekly\nday = sunday\n"
            "time = 02:00\ntarget = all\nkeep_max = notanumber\n"
        )
        tasks = parse_schedule_config(str(ini))
        assert len(tasks) == 1
        assert tasks[0]["keep_max"] is None

    def test_absent_keep_max_is_none(self, tmp_path):
        ini = tmp_path / "schedule.ini"
        ini.write_text(
            "[tsr-weekly]\ntype = tsr\nschedule = weekly\nday = sunday\n"
            "time = 02:00\ntarget = all\n"
        )
        tasks = parse_schedule_config(str(ini))
        assert tasks[0]["keep_max"] is None


class TestPruneTsrBeforeCollect:
    @pytest.fixture
    def tsr_dir(self, tmp_path):
        host_dir = tmp_path / "host01.example.com"
        host_dir.mkdir()
        return host_dir

    def _make_zip(self, host_dir: Path, ts: str, svc: str = "ABCD123") -> Path:
        zf = host_dir / f"TSR{ts}_{svc}.zip"
        zf.write_text("fake")
        (host_dir / ts).mkdir(exist_ok=True)
        return zf

    def test_no_prune_when_dir_missing(self, tmp_path):
        import dracs.jobqueue as jq

        with patch.object(jq, "_TSR_BASE_DIR", tmp_path):
            with patch("dracs.webapp._generate_tsr_index") as mock_idx:
                _prune_tsr_before_collect("nonexistent.example.com", keep_max=2)
        mock_idx.assert_not_called()

    def test_no_prune_when_within_limit(self, tmp_path, tsr_dir):
        self._make_zip(tsr_dir, "20260601120000")
        import dracs.jobqueue as jq

        with patch.object(jq, "_TSR_BASE_DIR", tmp_path):
            with patch("dracs.webapp._generate_tsr_index") as mock_idx:
                _prune_tsr_before_collect("host01.example.com", keep_max=2)
        mock_idx.assert_not_called()

    def test_prunes_excess_zips_and_dirs(self, tmp_path, tsr_dir):
        self._make_zip(tsr_dir, "20260601120000")
        self._make_zip(tsr_dir, "20260610120000")
        self._make_zip(tsr_dir, "20260620120000")

        import dracs.jobqueue as jq

        with patch.object(jq, "_TSR_BASE_DIR", tmp_path):
            with patch("dracs.webapp._generate_tsr_index") as mock_idx:
                _prune_tsr_before_collect("host01.example.com", keep_max=2)

        remaining_zips = list(tsr_dir.glob("TSR*.zip"))
        assert len(remaining_zips) == 1
        assert "20260620120000" in remaining_zips[0].name
        assert not (tsr_dir / "20260601120000").exists()
        assert not (tsr_dir / "20260610120000").exists()
        assert (tsr_dir / "20260620120000").exists()
        mock_idx.assert_called_once_with("host01.example.com")

    def test_zip_delete_error_is_logged(self, tmp_path, tsr_dir):
        self._make_zip(tsr_dir, "20260601120000")
        self._make_zip(tsr_dir, "20260620120000")

        import dracs.jobqueue as jq

        with patch.object(jq, "_TSR_BASE_DIR", tmp_path):
            with patch("dracs.webapp._generate_tsr_index"):
                with patch.object(
                    Path, "unlink", side_effect=PermissionError("denied")
                ):
                    with patch("dracs.jobqueue.logger") as mock_logger:
                        _prune_tsr_before_collect("host01.example.com", keep_max=2)
        assert mock_logger.warning.called

    def test_dir_delete_error_is_logged(self, tmp_path, tsr_dir):
        self._make_zip(tsr_dir, "20260601120000")
        self._make_zip(tsr_dir, "20260620120000")

        import dracs.jobqueue as jq

        with patch.object(jq, "_TSR_BASE_DIR", tmp_path):
            with patch("dracs.webapp._generate_tsr_index"):
                with patch("dracs.jobqueue.shutil.rmtree", side_effect=OSError("busy")):
                    with patch("dracs.jobqueue.logger") as mock_logger:
                        _prune_tsr_before_collect("host01.example.com", keep_max=2)
        assert mock_logger.warning.called

    def test_unparseable_zip_is_skipped(self, tmp_path, tsr_dir):
        # Zip with a non-timestamp name should be ignored, not raise
        (tsr_dir / "TSRbadname_ABCD123.zip").write_text("fake")
        self._make_zip(tsr_dir, "20260620120000")
        self._make_zip(tsr_dir, "20260621120000")

        import dracs.jobqueue as jq

        with patch.object(jq, "_TSR_BASE_DIR", tmp_path):
            with patch("dracs.webapp._generate_tsr_index"):
                _prune_tsr_before_collect("host01.example.com", keep_max=2)

        # The two valid zips are within keep_max so nothing is deleted
        assert (tsr_dir / "TSRbadname_ABCD123.zip").exists()


class TestExecuteTsrJobKeepMax:
    def test_keep_max_in_metadata_calls_prune(self):
        with patch("dracs.jobqueue._prune_tsr_before_collect") as mock_prune:
            with patch("dracs.jobqueue.get_session") as mock_session:
                mock_session.return_value.__enter__ = MagicMock(
                    return_value=MagicMock(
                        query=MagicMock(
                            return_value=MagicMock(
                                filter=MagicMock(
                                    return_value=MagicMock(
                                        first=MagicMock(return_value=None)
                                    )
                                )
                            )
                        )
                    )
                )
                mock_session.return_value.__exit__ = MagicMock(return_value=False)
                from dracs.jobqueue import execute_tsr_job

                try:
                    execute_tsr_job(
                        "host01.example.com",
                        metadata={"keep_max": 2},
                    )
                except Exception:
                    pass
        mock_prune.assert_called_once_with("host01.example.com", 2)

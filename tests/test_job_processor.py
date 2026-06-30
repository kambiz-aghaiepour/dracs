import importlib
import os
import tempfile
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from dracs.db import db_initialize, upsert_system
from dracs.jobqueue import (
    JobProcessor,
    claim_next_job,
    complete_job,
    enqueue_job,
    execute_bios_update_job,
    execute_clear_job_queue,
    execute_firmware_update_job,
    execute_refresh_job,
    execute_tsr_job,
    get_job_status,
)


@pytest.fixture
def job_db():
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


class TestJobProcessor:
    def test_start_and_stop(self, job_db):
        processor = JobProcessor(max_workers=2, poll_interval=0.1)
        processor.start()
        assert processor.is_running is True
        processor.stop()
        assert processor.is_running is False

    def test_double_start_is_noop(self, job_db):
        processor = JobProcessor(max_workers=2, poll_interval=0.1)
        processor.start()
        processor.start()
        assert processor.is_running is True
        processor.stop()

    def test_processes_job(self, job_db):
        job_id = enqueue_job("tsr", "server01.example.com")

        mock_execute = MagicMock()
        processor = JobProcessor(max_workers=2, poll_interval=0.05)

        with patch("dracs.jobqueue.execute_tsr_job", mock_execute):
            processor.start()
            time.sleep(0.3)
            processor.stop()

        status = get_job_status(job_id)
        assert status["status"] in ("completed", "running")

    def test_handles_execution_failure(self, job_db):
        job_id = enqueue_job("tsr", "server01.example.com")

        def mock_execute(hostname, job_id=None, metadata=None):
            raise RuntimeError("SSH timeout")

        processor = JobProcessor(max_workers=2, poll_interval=0.05)

        with patch("dracs.jobqueue.execute_tsr_job", mock_execute):
            processor.start()
            time.sleep(0.3)
            processor.stop()

        status = get_job_status(job_id)
        assert status["status"] == "failed"
        assert "SSH timeout" in status["error"]

    def test_processes_refresh_job(self, job_db):
        job_id = enqueue_job("refresh", "server01.example.com")

        mock_execute = MagicMock()
        processor = JobProcessor(max_workers=2, poll_interval=0.05)

        with patch("dracs.jobqueue.execute_refresh_job", mock_execute):
            processor.start()
            time.sleep(0.3)
            processor.stop()

        mock_execute.assert_called_once_with("server01.example.com")

    def test_unknown_job_type_fails(self, job_db):
        job_id = enqueue_job("unknown_type", "server01.example.com")

        processor = JobProcessor(max_workers=2, poll_interval=0.05)
        processor.start()
        time.sleep(0.3)
        processor.stop()

        # Poll until executor thread finishes writing the failed status
        deadline = time.time() + 2.0
        status = get_job_status(job_id)
        while status["status"] == "running" and time.time() < deadline:
            time.sleep(0.05)
            status = get_job_status(job_id)

        assert status["status"] == "failed"
        assert "Unknown job type" in status["error"]

    def test_idles_when_no_jobs(self, job_db):
        processor = JobProcessor(max_workers=2, poll_interval=0.05)
        processor.start()
        time.sleep(0.2)
        processor.stop()

    def test_handles_claim_exception(self, job_db):
        processor = JobProcessor(max_workers=2, poll_interval=0.05)
        with patch(
            "dracs.jobqueue.claim_next_job",
            side_effect=[RuntimeError("DB locked"), None],
        ):
            processor.start()
            time.sleep(0.2)
            processor.stop()


class TestExecuteTsrJob:
    def test_full_lifecycle(self, job_db):
        mock_build_cmd = MagicMock(return_value=["echo", "test"])
        mock_subprocess = MagicMock()
        mock_subprocess.returncode = 0

        running_job = {
            "status": "Running",
            "job_name": "SupportAssist Collection",
        }
        completed_job = {
            "status": "Completed",
            "job_name": "SupportAssist Collection",
            "message": "collection operation is completed successfully",
        }

        call_count = [0]

        def mock_get_sa_jobs(hostname):
            call_count[0] += 1
            if call_count[0] <= 1:
                return [running_job]
            return [completed_job]

        mock_wait_export = MagicMock(return_value=True)
        mock_find_zip = MagicMock(return_value="/tmp/TSR20260505_TAG001.zip")
        mock_stage = MagicMock()

        with patch.dict(os.environ, {"DRACS_DB": job_db}):
            import dracs.webapp as webapp_mod

            webapp_mod.DB_PATH = job_db
            webapp_mod.db_initialize(job_db)

            with patch("dracs.jobqueue.subprocess.run", return_value=mock_subprocess):
                with patch("dracs.jobqueue.time.sleep"):
                    with patch.multiple(
                        "dracs.webapp",
                        _build_ssh_racadm_cmd=mock_build_cmd,
                        _get_sa_jobs=mock_get_sa_jobs,
                        _wait_for_tsr_export=mock_wait_export,
                        _find_tsr_zip=mock_find_zip,
                        _stage_tsr_files=mock_stage,
                    ):
                        execute_tsr_job("server01.example.com")

        mock_stage.assert_called_once()

    def test_full_lifecycle_with_job_id(self, job_db):
        job_id = enqueue_job("tsr", "server01.example.com")
        claim_next_job("w1")

        mock_build_cmd = MagicMock(return_value=["echo", "test"])
        mock_subprocess = MagicMock()
        mock_subprocess.returncode = 0

        running_job = {
            "status": "Running",
            "job_name": "SupportAssist Collection",
            "percent_complete": "45",
        }
        completed_job = {
            "status": "Completed",
            "job_name": "SupportAssist Collection",
            "message": "collection operation is completed successfully",
        }

        call_count = [0]

        def mock_get_sa_jobs(hostname):
            call_count[0] += 1
            if call_count[0] <= 1:
                return [running_job]
            return [completed_job]

        with patch.dict(os.environ, {"DRACS_DB": job_db}):
            import dracs.webapp as webapp_mod

            webapp_mod.DB_PATH = job_db
            webapp_mod.db_initialize(job_db)

            with patch("dracs.jobqueue.subprocess.run", return_value=mock_subprocess):
                with patch("dracs.jobqueue.time.sleep"):
                    with patch.multiple(
                        "dracs.webapp",
                        _build_ssh_racadm_cmd=mock_build_cmd,
                        _get_sa_jobs=mock_get_sa_jobs,
                        _wait_for_tsr_export=MagicMock(return_value=True),
                        _find_tsr_zip=MagicMock(
                            return_value="/tmp/TSR20260505_TAG001.zip"
                        ),
                        _stage_tsr_files=MagicMock(),
                    ):
                        execute_tsr_job("server01.example.com", job_id=job_id)

        status = get_job_status(job_id)
        assert status is not None

    def test_host_not_found(self, job_db):
        with pytest.raises(ValueError, match="not found"):
            execute_tsr_job("nonexistent.example.com")

    def test_collect_command_fails(self, job_db):
        mock_build_cmd = MagicMock(return_value=["echo", "test"])
        mock_subprocess = MagicMock()
        mock_subprocess.returncode = 1
        mock_subprocess.stderr = "Connection refused"
        mock_subprocess.stdout = ""

        with patch("dracs.jobqueue.subprocess.run", return_value=mock_subprocess):
            with patch("dracs.webapp._build_ssh_racadm_cmd", mock_build_cmd):
                with pytest.raises(RuntimeError, match="Failed to start"):
                    execute_tsr_job("server01.example.com")

    def test_collection_timeout(self, job_db):
        mock_build_cmd = MagicMock(return_value=["echo", "test"])
        mock_subprocess = MagicMock()
        mock_subprocess.returncode = 0

        with patch("dracs.jobqueue.subprocess.run", return_value=mock_subprocess):
            with patch("dracs.webapp._build_ssh_racadm_cmd", mock_build_cmd):
                with patch("dracs.webapp._get_sa_jobs", return_value=None):
                    with patch("dracs.jobqueue.time.sleep"):
                        with pytest.raises(RuntimeError, match="did not start"):
                            execute_tsr_job("server01.example.com")

    def test_export_not_found(self, job_db):
        mock_build_cmd = MagicMock(return_value=["echo", "test"])
        mock_subprocess = MagicMock()
        mock_subprocess.returncode = 0

        running_job = {
            "status": "Running",
            "job_name": "SupportAssist Collection",
        }
        completed_job = {
            "status": "Completed",
            "job_name": "SupportAssist Collection",
            "message": "collection operation is completed successfully",
        }

        call_count = [0]

        def mock_get_sa_jobs(hostname):
            call_count[0] += 1
            if call_count[0] <= 1:
                return [running_job]
            return [completed_job]

        with patch("dracs.jobqueue.subprocess.run", return_value=mock_subprocess):
            with patch("dracs.webapp._build_ssh_racadm_cmd", mock_build_cmd):
                with patch("dracs.webapp._get_sa_jobs", mock_get_sa_jobs):
                    with patch(
                        "dracs.webapp._wait_for_tsr_export",
                        return_value=True,
                    ):
                        with patch("dracs.webapp._find_tsr_zip", return_value=None):
                            with patch("dracs.jobqueue.time.sleep"):
                                with pytest.raises(
                                    RuntimeError, match="zip file not found"
                                ):
                                    execute_tsr_job("server01.example.com")

    def test_export_timeout(self, job_db):
        mock_build_cmd = MagicMock(return_value=["echo", "test"])
        mock_subprocess = MagicMock()
        mock_subprocess.returncode = 0

        running_job = {
            "status": "Running",
            "job_name": "SupportAssist Collection",
        }
        completed_job = {
            "status": "Completed",
            "job_name": "SupportAssist Collection",
            "message": "collection operation is completed successfully",
        }

        call_count = [0]

        def mock_get_sa_jobs(hostname):
            call_count[0] += 1
            if call_count[0] <= 1:
                return [running_job]
            return [completed_job]

        with patch("dracs.jobqueue.subprocess.run", return_value=mock_subprocess):
            with patch("dracs.webapp._build_ssh_racadm_cmd", mock_build_cmd):
                with patch("dracs.webapp._get_sa_jobs", mock_get_sa_jobs):
                    with patch(
                        "dracs.webapp._wait_for_tsr_export",
                        return_value=False,
                    ):
                        with patch("dracs.jobqueue.time.sleep"):
                            with pytest.raises(
                                RuntimeError,
                                match="export did not complete",
                            ):
                                execute_tsr_job("server01.example.com")

    def test_phase2_with_progress_tracking(self, job_db):
        jid = enqueue_job("tsr", "server01.example.com")
        claim_next_job("w1")

        mock_build_cmd = MagicMock(return_value=["echo", "test"])
        mock_subprocess = MagicMock()
        mock_subprocess.returncode = 0

        running_job = {
            "status": "Running",
            "job_name": "SupportAssist Collection",
            "percent_complete": "60",
        }
        completed_job = {
            "status": "Completed",
            "job_name": "SupportAssist Collection",
            "message": "collection operation is completed successfully",
        }

        call_count = [0]

        def mock_get_sa_jobs(hostname):
            call_count[0] += 1
            if call_count[0] == 1:
                return [running_job]
            if call_count[0] == 2:
                return None
            if call_count[0] == 3:
                return [running_job]
            return [completed_job]

        with patch.dict(os.environ, {"DRACS_DB": job_db}):
            import dracs.webapp as webapp_mod

            webapp_mod.DB_PATH = job_db
            webapp_mod.db_initialize(job_db)

            with patch("dracs.jobqueue.subprocess.run", return_value=mock_subprocess):
                with patch("dracs.jobqueue.time.sleep"):
                    with patch.multiple(
                        "dracs.webapp",
                        _build_ssh_racadm_cmd=mock_build_cmd,
                        _get_sa_jobs=mock_get_sa_jobs,
                        _wait_for_tsr_export=MagicMock(return_value=True),
                        _find_tsr_zip=MagicMock(
                            return_value="/tmp/TSR20260505_TAG001.zip"
                        ),
                        _stage_tsr_files=MagicMock(),
                    ):
                        execute_tsr_job("server01.example.com", job_id=jid)

    def test_phase2_completion_timeout(self, job_db):
        mock_build_cmd = MagicMock(return_value=["echo", "test"])
        mock_subprocess = MagicMock()
        mock_subprocess.returncode = 0

        running_job = {
            "status": "Running",
            "job_name": "SupportAssist Collection",
        }

        with patch.dict(os.environ, {"DRACS_DB": job_db}):
            import dracs.webapp as webapp_mod

            webapp_mod.DB_PATH = job_db
            webapp_mod.db_initialize(job_db)

            with patch("dracs.jobqueue.subprocess.run", return_value=mock_subprocess):
                with patch("dracs.jobqueue.time.sleep"):
                    with patch.multiple(
                        "dracs.webapp",
                        _build_ssh_racadm_cmd=mock_build_cmd,
                        _get_sa_jobs=MagicMock(return_value=[running_job]),
                    ):
                        with pytest.raises(RuntimeError, match="did not complete"):
                            execute_tsr_job("server01.example.com")


class TestExecuteRefreshJob:
    def test_calls_refresh(self, job_db):
        mock_refresh = AsyncMock()

        with patch.dict(os.environ, {"DRACS_DB": job_db}):
            with patch("dracs.commands.refresh_dell_warranty", mock_refresh):
                execute_refresh_job("server01.example.com")

        mock_refresh.assert_called_once()
        call_args = mock_refresh.call_args[0]
        assert call_args[0] is None
        assert call_args[1] == "server01.example.com"


class TestExecuteFirmwareUpdateJob:
    def test_success(self, job_db):
        mock_build_cmd = MagicMock(return_value=["echo", "test"])
        mock_result = MagicMock(returncode=0)
        with patch("dracs.jobqueue.subprocess.run", return_value=mock_result):
            with patch("dracs.webapp._build_ssh_racadm_cmd", mock_build_cmd):
                execute_firmware_update_job(
                    "server01.example.com",
                    {"target_version": "8.0.0", "model": "R660"},
                )

    def test_missing_metadata(self, job_db):
        with pytest.raises(ValueError, match="target_version and model required"):
            execute_firmware_update_job("server01.example.com", {})

    def test_command_failure(self, job_db):
        mock_build_cmd = MagicMock(return_value=["echo", "test"])
        mock_result = MagicMock(returncode=1, stderr="error", stdout="")
        with patch("dracs.jobqueue.subprocess.run", return_value=mock_result):
            with patch("dracs.webapp._build_ssh_racadm_cmd", mock_build_cmd):
                with pytest.raises(RuntimeError, match="Firmware update failed"):
                    execute_firmware_update_job(
                        "server01.example.com",
                        {"target_version": "8.0.0", "model": "R660"},
                    )


class TestExecuteBiosUpdateJob:
    def test_success(self, job_db):
        mock_build_cmd = MagicMock(return_value=["echo", "test"])
        mock_result = MagicMock(returncode=0)
        with patch("dracs.jobqueue.subprocess.run", return_value=mock_result):
            with patch("dracs.webapp._build_ssh_racadm_cmd", mock_build_cmd):
                with patch(
                    "dracs.webapp.get_bios_filename",
                    return_value="BIOS_TEST.EXE",
                ):
                    execute_bios_update_job(
                        "server01.example.com",
                        {"target_bios": "2.10.0", "model": "R660"},
                    )

    def test_missing_metadata(self, job_db):
        with pytest.raises(ValueError, match="target_bios and model required"):
            execute_bios_update_job("server01.example.com", {})

    def test_bios_filename_not_found(self, job_db):
        with patch("dracs.webapp.get_bios_filename", return_value=None):
            with pytest.raises(ValueError, match="BIOS filename not found"):
                execute_bios_update_job(
                    "server01.example.com",
                    {"target_bios": "2.10.0", "model": "R660"},
                )

    def test_command_failure(self, job_db):
        mock_build_cmd = MagicMock(return_value=["echo", "test"])
        mock_result = MagicMock(returncode=1, stderr="error", stdout="")
        with patch("dracs.jobqueue.subprocess.run", return_value=mock_result):
            with patch("dracs.webapp._build_ssh_racadm_cmd", mock_build_cmd):
                with patch(
                    "dracs.webapp.get_bios_filename",
                    return_value="BIOS_TEST.EXE",
                ):
                    with pytest.raises(RuntimeError, match="BIOS update failed"):
                        execute_bios_update_job(
                            "server01.example.com",
                            {"target_bios": "2.10.0", "model": "R660"},
                        )


class TestExecuteClearJobQueue:
    def test_success(self, job_db):
        mock_build_cmd = MagicMock(return_value=["echo", "test"])
        mock_result = MagicMock(returncode=0)
        with patch("dracs.jobqueue.subprocess.run", return_value=mock_result):
            with patch("dracs.webapp._build_ssh_racadm_cmd", mock_build_cmd):
                execute_clear_job_queue("server01.example.com")
        mock_build_cmd.assert_called_once_with(
            "server01.example.com", "jobqueue", "delete", "--all"
        )

    def test_command_failure(self, job_db):
        mock_build_cmd = MagicMock(return_value=["echo", "test"])
        mock_result = MagicMock(returncode=1, stderr="error", stdout="")
        with patch("dracs.jobqueue.subprocess.run", return_value=mock_result):
            with patch("dracs.webapp._build_ssh_racadm_cmd", mock_build_cmd):
                with pytest.raises(RuntimeError, match="Clear job queue failed"):
                    execute_clear_job_queue("server01.example.com")


class TestProcessorDispatchUpdateJobs:
    def test_firmware_update_dispatched(self, job_db):
        enqueue_job(
            "firmware_update",
            "server01.example.com",
            metadata={"target_version": "8.0.0", "model": "R660"},
        )
        mock_execute = MagicMock()
        processor = JobProcessor(max_workers=2, poll_interval=0.05)
        with patch("dracs.jobqueue.execute_firmware_update_job", mock_execute):
            processor.start()
            time.sleep(0.3)
            processor.stop()
        mock_execute.assert_called_once()

    def test_bios_update_dispatched(self, job_db):
        enqueue_job(
            "bios_update",
            "server01.example.com",
            metadata={"target_bios": "2.10.0", "model": "R660"},
        )
        mock_execute = MagicMock()
        processor = JobProcessor(max_workers=2, poll_interval=0.05)
        with patch("dracs.jobqueue.execute_bios_update_job", mock_execute):
            processor.start()
            time.sleep(0.3)
            processor.stop()
        mock_execute.assert_called_once()

    def test_clear_job_queue_dispatched(self, job_db):
        enqueue_job("clear_job_queue", "server01.example.com")
        mock_execute = MagicMock()
        processor = JobProcessor(max_workers=2, poll_interval=0.05)
        with patch("dracs.jobqueue.execute_clear_job_queue", mock_execute):
            processor.start()
            time.sleep(0.3)
            processor.stop()
        mock_execute.assert_called_once_with("server01.example.com")


class TestGunicornHook:
    def _load_gunicorn_conf(self):
        from pathlib import Path

        conf_path = Path(__file__).parent.parent / "src" / "dracs" / "gunicorn.conf.py"
        spec = importlib.util.spec_from_file_location("gunicorn_conf", str(conf_path))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def test_post_worker_init_acquires_lock(self, tmp_path):
        lock_path = str(tmp_path / "test.lock")
        worker = MagicMock()

        mock_processor = MagicMock()
        with patch.dict(os.environ, {"JOB_PROCESSOR_LOCK": lock_path}):
            with patch(
                "dracs.jobqueue.JobProcessor",
                return_value=mock_processor,
            ):
                with patch("dracs.jobqueue.recover_stale_jobs", return_value=0):
                    conf = self._load_gunicorn_conf()
                    conf.post_worker_init(worker)

        mock_processor.start.assert_called_once()

    def test_post_worker_init_second_worker_skips(self, tmp_path):
        import fcntl

        lock_path = str(tmp_path / "test.lock")
        lock_file = open(lock_path, "w")
        fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)

        worker = MagicMock(spec=[])
        with patch.dict(os.environ, {"JOB_PROCESSOR_LOCK": lock_path}):
            conf = self._load_gunicorn_conf()
            conf.post_worker_init(worker)

        assert not hasattr(worker, "_job_processor")
        lock_file.close()

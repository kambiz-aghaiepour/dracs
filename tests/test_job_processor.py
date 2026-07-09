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
    execute_config_collect_job,
    execute_firmware_update_job,
    execute_racadm_config_job,
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


class TestExecuteRacadmConfigJob:
    _MOCK_SITE = {"id": 1, "name": "Default"}

    def _ps(self, attr_name, push_key, push_value, post_push_command=None):
        return {
            "attr_name": attr_name,
            "push_key": push_key,
            "push_value": push_value,
            "post_push_command": post_push_command,
        }

    def test_unknown_site_raises(self):
        with patch("dracs.db.get_site_by_name", return_value=None):
            with pytest.raises(RuntimeError, match="Unknown site"):
                execute_racadm_config_job(
                    "host01.example.com",
                    {"site_name": "nosuchsite", "push_settings": []},
                )

    def test_basic_setting_applied(self):
        mock_build_cmd = MagicMock(return_value=["echo", "test"])
        mock_result = MagicMock(returncode=0)
        mock_upsert = MagicMock()
        mock_attr_def = {"id": 1, "name": "ps_rapid_on", "endpoint_type": "system_oem_dell", "attribute_path": "Attributes.ServerPwr.1.PSRapidOn"}
        collect_ret = {"ps_rapid_on": {"value": "Disabled", "collected_at": "2026-01-01T00:00:00"}}
        with patch("dracs.jobqueue.subprocess.run", return_value=mock_result):
            with patch("dracs.webapp._build_ssh_racadm_cmd", mock_build_cmd):
                with patch("dracs.db.get_site_by_name", return_value=self._MOCK_SITE):
                    with patch("dracs.db.get_attr_def_by_name", return_value=mock_attr_def):
                        with patch("dracs.redfish.collect_for_host_dynamic", return_value=collect_ret):
                            with patch("dracs.db.upsert_host_config_attr", mock_upsert):
                                with patch("dracs.snmp.build_idrac_hostname", return_value="mgmt-host01.example.com"):
                                    execute_racadm_config_job(
                                        "host01.example.com",
                                        {
                                            "site_name": "Default",
                                            "push_settings": [
                                                self._ps("ps_rapid_on", "System.ServerPwr.PSRapidOn", "Disabled"),
                                            ],
                                        },
                                    )
        mock_build_cmd.assert_called_once_with(
            "host01.example.com",
            "set",
            "System.ServerPwr.PSRapidOn",
            "Disabled",
            site="Default",
        )
        mock_upsert.assert_called_once()

    def test_empty_push_settings_sends_no_command(self):
        mock_build_cmd = MagicMock(return_value=["echo", "test"])
        with patch("dracs.db.get_site_by_name", return_value=self._MOCK_SITE):
            with patch("dracs.snmp.build_idrac_hostname", return_value="mgmt-host01.example.com"):
                execute_racadm_config_job(
                    "host01.example.com",
                    {"site_name": "Default", "push_settings": []},
                )
        mock_build_cmd.assert_not_called()

    def test_idrac_fqdn_token_substituted(self):
        mock_build_cmd = MagicMock(return_value=["echo", "test"])
        mock_result = MagicMock(returncode=0)
        with patch("dracs.jobqueue.subprocess.run", return_value=mock_result):
            with patch("dracs.webapp._build_ssh_racadm_cmd", mock_build_cmd):
                with patch("dracs.db.get_site_by_name", return_value=self._MOCK_SITE):
                    with patch("dracs.db.get_attr_def_by_name", return_value=None):
                        with patch("dracs.snmp.build_idrac_hostname", return_value="mgmt-host01.example.com"):
                            execute_racadm_config_job(
                                "host01.example.com",
                                {
                                    "site_name": "Default",
                                    "push_settings": [
                                        self._ps("idrac_hostname", "System.ServerOS.Hostname", "{idrac_fqdn}"),
                                    ],
                                },
                            )
        mock_build_cmd.assert_called_once_with(
            "host01.example.com",
            "set",
            "System.ServerOS.Hostname",
            "mgmt-host01.example.com",
            site="Default",
        )

    def test_post_push_command_is_run(self):
        mock_build_cmd = MagicMock(return_value=["echo", "test"])
        mock_result = MagicMock(returncode=0)
        with patch("dracs.jobqueue.subprocess.run", return_value=mock_result):
            with patch("dracs.webapp._build_ssh_racadm_cmd", mock_build_cmd):
                with patch("dracs.db.get_site_by_name", return_value=self._MOCK_SITE):
                    with patch("dracs.db.get_attr_def_by_name", return_value=None):
                        with patch("dracs.snmp.build_idrac_hostname", return_value="mgmt-host01.example.com"):
                            execute_racadm_config_job(
                                "host01.example.com",
                                {
                                    "site_name": "Default",
                                    "push_settings": [
                                        self._ps("sys_profile", "BIOS.Setup.1-1.SysProfile",
                                                 "PerfPerWattOptimizedOs",
                                                 "jobqueue create BIOS.Setup.1-1"),
                                    ],
                                },
                            )
        assert mock_build_cmd.call_count == 2
        calls = [c.args for c in mock_build_cmd.call_args_list]
        assert any("jobqueue" in c for c in calls)

    def test_command_failure_raises(self):
        mock_build_cmd = MagicMock(return_value=["echo", "test"])
        mock_result = MagicMock(returncode=1, stderr="SSH error", stdout="")
        with patch("dracs.jobqueue.subprocess.run", return_value=mock_result):
            with patch("dracs.webapp._build_ssh_racadm_cmd", mock_build_cmd):
                with patch("dracs.db.get_site_by_name", return_value=self._MOCK_SITE):
                    with patch("dracs.snmp.build_idrac_hostname", return_value="mgmt-host01.example.com"):
                        with pytest.raises(RuntimeError, match="ps_rapid_on"):
                            execute_racadm_config_job(
                                "host01.example.com",
                                {
                                    "site_name": "Default",
                                    "push_settings": [
                                        self._ps("ps_rapid_on", "System.ServerPwr.PSRapidOn", "Disabled"),
                                    ],
                                },
                            )

    def test_verification_failure_is_logged_not_raised(self):
        mock_build_cmd = MagicMock(return_value=["echo", "test"])
        mock_result = MagicMock(returncode=0)
        mock_attr_def = {"id": 1, "name": "ps_rapid_on", "endpoint_type": "system_oem_dell", "attribute_path": "Attributes.ServerPwr.1.PSRapidOn"}
        with patch("dracs.jobqueue.subprocess.run", return_value=mock_result):
            with patch("dracs.webapp._build_ssh_racadm_cmd", mock_build_cmd):
                with patch("dracs.db.get_site_by_name", return_value=self._MOCK_SITE):
                    with patch("dracs.db.get_attr_def_by_name", return_value=mock_attr_def):
                        with patch("dracs.snmp.build_idrac_hostname", return_value="mgmt-host01.example.com"):
                            with patch(
                                "dracs.redfish.collect_for_host_dynamic",
                                side_effect=RuntimeError("Redfish timeout"),
                            ):
                                # Must NOT raise — failure is logged
                                execute_racadm_config_job(
                                    "host01.example.com",
                                    {
                                        "site_name": "Default",
                                        "push_settings": [
                                            self._ps("ps_rapid_on", "System.ServerPwr.PSRapidOn", "Disabled"),
                                        ],
                                    },
                                )

    def test_post_job_trigger_host_called(self):
        mock_build_cmd = MagicMock(return_value=["echo", "test"])
        mock_result = MagicMock(returncode=0)
        mock_cc = MagicMock()
        with patch("dracs.jobqueue.subprocess.run", return_value=mock_result):
            with patch("dracs.webapp._build_ssh_racadm_cmd", mock_build_cmd):
                with patch("dracs.db.get_site_by_name", return_value=self._MOCK_SITE):
                    with patch("dracs.db.get_attr_def_by_name", return_value=None):
                        with patch("dracs.snmp.build_idrac_hostname", return_value="mgmt-host01.example.com"):
                            with patch(
                                "dracs.config_collector.get_collector",
                                return_value=mock_cc,
                            ):
                                execute_racadm_config_job(
                                    "host01.example.com",
                                    {
                                        "site_name": "Default",
                                        "push_settings": [
                                            self._ps("ps_rapid_on", "System.ServerPwr.PSRapidOn", "Disabled"),
                                        ],
                                    },
                                )
        mock_cc.trigger_host.assert_called_once_with("host01.example.com", "Default", 1)

    def test_post_job_no_error_when_collector_none(self):
        mock_build_cmd = MagicMock(return_value=["echo", "test"])
        mock_result = MagicMock(returncode=0)
        with patch("dracs.jobqueue.subprocess.run", return_value=mock_result):
            with patch("dracs.webapp._build_ssh_racadm_cmd", mock_build_cmd):
                with patch("dracs.db.get_site_by_name", return_value=self._MOCK_SITE):
                    with patch("dracs.db.get_attr_def_by_name", return_value=None):
                        with patch("dracs.snmp.build_idrac_hostname", return_value="mgmt-host01.example.com"):
                            with patch(
                                "dracs.config_collector.get_collector",
                                return_value=None,
                            ):
                                execute_racadm_config_job(
                                    "host01.example.com",
                                    {
                                        "site_name": "Default",
                                        "push_settings": [
                                            self._ps("ps_rapid_on", "System.ServerPwr.PSRapidOn", "Disabled"),
                                        ],
                                    },
                                )

    def test_dispatched_by_processor(self, job_db):
        enqueue_job(
            "racadm_config",
            "server01.example.com",
            metadata={"site_name": "Default", "push_settings": []},
        )
        mock_execute = MagicMock()
        processor = JobProcessor(max_workers=2, poll_interval=0.05)
        with patch("dracs.jobqueue.execute_racadm_config_job", mock_execute):
            processor.start()
            time.sleep(0.3)
            processor.stop()
        mock_execute.assert_called_once()


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


class TestExecuteConfigCollectJob:
    _MOCK_SITE = {"id": 1, "name": "Default"}
    _MOCK_ATTR = {"id": 1, "name": "ps_rapid_on", "endpoint_type": "system_oem_dell", "attribute_path": "Attributes.ServerPwr.1.PSRapidOn"}

    def test_unknown_site_raises(self):
        with patch("dracs.db.get_site_by_name", return_value=None):
            with pytest.raises(RuntimeError, match="Unknown site"):
                execute_config_collect_job(
                    "host01.example.com",
                    {"site_name": "nosuchsite"},
                )

    def test_collects_and_stores(self):
        mock_upsert = MagicMock()
        collect_ret = {"ps_rapid_on": {"value": "Disabled", "collected_at": "2026-01-01T00:00:00"}}
        with patch("dracs.db.get_site_by_name", return_value=self._MOCK_SITE):
            with patch(
                "dracs.db.get_enabled_attr_defs_for_site",
                return_value=[self._MOCK_ATTR],
            ):
                with patch(
                    "dracs.redfish.collect_for_host_dynamic",
                    return_value=collect_ret,
                ):
                    with patch("dracs.db.upsert_host_config_attr", mock_upsert):
                        execute_config_collect_job(
                            "host01.example.com",
                            {"site_name": "Default"},
                        )
        mock_upsert.assert_called_once()

    def test_empty_collection_skips_upsert(self):
        mock_upsert = MagicMock()
        with patch("dracs.db.get_site_by_name", return_value=self._MOCK_SITE):
            with patch("dracs.db.get_enabled_attr_defs_for_site", return_value=[]):
                with patch("dracs.db.upsert_host_config_attr", mock_upsert):
                    execute_config_collect_job(
                        "host01.example.com",
                        {"site_name": "Default"},
                    )
        mock_upsert.assert_not_called()

    def test_redfish_failure_propagates(self):
        with patch("dracs.db.get_site_by_name", return_value=self._MOCK_SITE):
            with patch(
                "dracs.db.get_enabled_attr_defs_for_site",
                return_value=[self._MOCK_ATTR],
            ):
                with patch(
                    "dracs.redfish.collect_for_host_dynamic",
                    side_effect=RuntimeError("Redfish timeout"),
                ):
                    with pytest.raises(RuntimeError, match="Redfish timeout"):
                        execute_config_collect_job(
                            "host01.example.com",
                            {"site_name": "Default"},
                        )

    def test_dispatched_by_processor(self, job_db):
        enqueue_job(
            "config_collect",
            "server01.example.com",
            metadata={"site_name": "Default"},
        )
        mock_execute = MagicMock()
        processor = JobProcessor(max_workers=2, poll_interval=0.05)
        with patch("dracs.jobqueue.execute_config_collect_job", mock_execute):
            processor.start()
            time.sleep(0.3)
            processor.stop()
        mock_execute.assert_called_once()


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

"""Tests for mass power/racreset job executors and dispatch."""

from unittest.mock import MagicMock, patch

import pytest

OK_RESULT = MagicMock(returncode=0, stdout="", stderr="")
FAIL_RESULT = MagicMock(returncode=1, stdout="", stderr="ERROR: command failed")

FQDN = "idrac-server01.example.com"


class TestExecutePowerActionJob:
    def test_powerup_runs_serveraction(self):
        from dracs.jobqueue import execute_power_action_job

        with (
            patch("dracs.snmp.build_idrac_hostname", return_value=FQDN),
            patch(
                "dracs.webapp.get_idrac_credentials", return_value=("root", "calvin")
            ),
            patch("dracs.jobqueue.run_racadm_ssh", return_value=OK_RESULT) as mock_ssh,
        ):
            execute_power_action_job("server01", {"action": "powerup"})
        mock_ssh.assert_called_once_with(
            FQDN, "root", "calvin", ["serveraction", "powerup"]
        )

    def test_powercycle_runs_serveraction(self):
        from dracs.jobqueue import execute_power_action_job

        with (
            patch("dracs.snmp.build_idrac_hostname", return_value=FQDN),
            patch(
                "dracs.webapp.get_idrac_credentials", return_value=("root", "calvin")
            ),
            patch("dracs.jobqueue.run_racadm_ssh", return_value=OK_RESULT) as mock_ssh,
        ):
            execute_power_action_job("server01", {"action": "powercycle"})
        mock_ssh.assert_called_once_with(
            FQDN, "root", "calvin", ["serveraction", "powercycle"]
        )

    def test_invalid_action_raises(self):
        from dracs.jobqueue import execute_power_action_job

        with (
            patch("dracs.snmp.build_idrac_hostname", return_value=FQDN),
            patch(
                "dracs.webapp.get_idrac_credentials", return_value=("root", "calvin")
            ),
        ):
            with pytest.raises(RuntimeError, match="Invalid power action"):
                execute_power_action_job("server01", {})

    def test_missing_action_raises(self):
        from dracs.jobqueue import execute_power_action_job

        with (
            patch("dracs.snmp.build_idrac_hostname", return_value=FQDN),
            patch(
                "dracs.webapp.get_idrac_credentials", return_value=("root", "calvin")
            ),
        ):
            with pytest.raises(RuntimeError, match="Invalid power action"):
                execute_power_action_job("server01", None)

    def test_failure_raises(self):
        from dracs.jobqueue import execute_power_action_job

        with (
            patch("dracs.snmp.build_idrac_hostname", return_value=FQDN),
            patch(
                "dracs.webapp.get_idrac_credentials", return_value=("root", "calvin")
            ),
            patch("dracs.jobqueue.run_racadm_ssh", return_value=FAIL_RESULT),
        ):
            with pytest.raises(
                RuntimeError, match="Power action powerup on server01 failed"
            ):
                execute_power_action_job("server01", {"action": "powerup"})


class TestExecuteRacresetJob:
    def test_racreset_runs_racadm_racreset(self):
        from dracs.jobqueue import execute_racreset_job

        with (
            patch("dracs.snmp.build_idrac_hostname", return_value=FQDN),
            patch(
                "dracs.webapp.get_idrac_credentials", return_value=("root", "calvin")
            ),
            patch("dracs.jobqueue.run_racadm_ssh", return_value=OK_RESULT) as mock_ssh,
        ):
            execute_racreset_job("server01", {})
        mock_ssh.assert_called_once_with(FQDN, "root", "calvin", ["racreset"])

    def test_failure_raises(self):
        from dracs.jobqueue import execute_racreset_job

        with (
            patch("dracs.snmp.build_idrac_hostname", return_value=FQDN),
            patch(
                "dracs.webapp.get_idrac_credentials", return_value=("root", "calvin")
            ),
            patch("dracs.jobqueue.run_racadm_ssh", return_value=FAIL_RESULT),
        ):
            with pytest.raises(RuntimeError, match="Racreset on server01 failed"):
                execute_racreset_job("server01", {})

    def test_invalid_hostname_fqdn_build_raises(self):
        from dracs.exceptions import ValidationError
        from dracs.jobqueue import execute_racreset_job

        with (
            patch(
                "dracs.snmp.build_idrac_hostname",
                side_effect=ValidationError("DNS not configured"),
            ),
        ):
            with pytest.raises(RuntimeError, match="Cannot build iDRAC FQDN"):
                execute_racreset_job("server01", {})


class TestMassJobDispatch:
    def _processor(self):
        from dracs.jobqueue import JobProcessor

        return JobProcessor.__new__(JobProcessor)

    def test_power_action_dispatched(self):
        from dracs.jobqueue import JobProcessor

        processor = JobProcessor.__new__(JobProcessor)
        job = {
            "id": 99,
            "job_type": "power_action",
            "target": "server01",
            "metadata": {"action": "powerdown"},
        }
        with (
            patch("dracs.jobqueue.execute_power_action_job") as mock_exec,
            patch("dracs.jobqueue.complete_job"),
        ):
            processor._execute_job(job)
        mock_exec.assert_called_once_with("server01", {"action": "powerdown"})

    def test_racreset_dispatched(self):
        from dracs.jobqueue import JobProcessor

        processor = JobProcessor.__new__(JobProcessor)
        job = {
            "id": 100,
            "job_type": "racreset",
            "target": "server01",
            "metadata": {},
        }
        with (
            patch("dracs.jobqueue.execute_racreset_job") as mock_exec,
            patch("dracs.jobqueue.complete_job"),
        ):
            processor._execute_job(job)
        mock_exec.assert_called_once_with("server01", {})

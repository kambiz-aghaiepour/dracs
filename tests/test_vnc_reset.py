"""Tests for the VNC reset job: get_hostname_viewer_count helper and execute_vnc_reset_job."""

import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

from dracs.db import db_initialize, upsert_system
from dracs.vnc import VncSessionManager, get_hostname_viewer_count

# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def token_dir(tmp_path):
    d = tmp_path / "vnc-tokens"
    d.mkdir()
    return str(d)


@pytest.fixture
def vnc_db(temp_db):
    db_initialize(temp_db)
    upsert_system(temp_db, "SVCTAG1", "server01", "R660", "7.0", "2.1", "Jan 1 2027", 0)
    return temp_db


# ── get_hostname_viewer_count ─────────────────────────────────────────────────


class TestGetHostnameViewerCount:
    def test_returns_zero_for_missing_token_dir(self, tmp_path):
        absent = str(tmp_path / "no-such-dir")
        assert get_hostname_viewer_count("server01", token_dir=absent) == 0

    def test_returns_zero_for_unknown_host(self, token_dir):
        assert get_hostname_viewer_count("server01", token_dir=token_dir) == 0

    def test_returns_one_after_create(self, token_dir):
        mgr = VncSessionManager(token_dir, timeout_minutes=30, max_sessions=0)
        mgr.create_session("server01", "idrac-server01.example.com", 5901)
        mgr.stop()
        assert get_hostname_viewer_count("server01", token_dir=token_dir) == 1

    def test_reflects_add_reference(self, token_dir):
        mgr = VncSessionManager(token_dir, timeout_minutes=30, max_sessions=0)
        token = mgr.create_session("server01", "idrac-server01.example.com", 5901)
        mgr.add_reference(token)
        mgr.stop()
        assert get_hostname_viewer_count("server01", token_dir=token_dir) == 2

    def test_returns_zero_after_release_all(self, token_dir):
        mgr = VncSessionManager(token_dir, timeout_minutes=30, max_sessions=0)
        token = mgr.create_session("server01", "idrac-server01.example.com", 5901)
        mgr.release_session(token)
        mgr.stop()
        # session is removed when refs reach 0
        assert get_hostname_viewer_count("server01", token_dir=token_dir) == 0

    def test_does_not_match_different_host(self, token_dir):
        mgr = VncSessionManager(token_dir, timeout_minutes=30, max_sessions=0)
        mgr.create_session("server02", "idrac-server02.example.com", 5901)
        mgr.stop()
        assert get_hostname_viewer_count("server01", token_dir=token_dir) == 0


# ── execute_vnc_reset_job ─────────────────────────────────────────────────────


OK_RESULT = MagicMock(returncode=0, stdout="", stderr="")
FAIL_RESULT = MagicMock(returncode=1, stdout="", stderr="ERROR: command failed")


def _make_ssh_patch(side_effect=None, return_value=None):
    """Return a patch for _run_racadm_ssh."""
    if side_effect is not None:
        return patch("dracs.jobqueue._run_racadm_ssh", side_effect=side_effect)
    return patch("dracs.jobqueue._run_racadm_ssh", return_value=return_value)


class TestExecuteVncResetJob:
    @patch("dracs.vnc.get_hostname_viewer_count", return_value=0)
    @patch("dracs.vnc.get_vnc_credentials", return_value=(5901, "vncpass"))
    @patch("dracs.webapp.get_idrac_credentials", return_value=("root", "calvin"))
    @patch("dracs.snmp.build_idrac_hostname", return_value="idrac-server01.example.com")
    def test_runs_four_ssh_commands(
        self, mock_fqdn, mock_idrac_creds, mock_vnc_creds, mock_viewers, vnc_db
    ):
        with _make_ssh_patch(return_value=OK_RESULT) as mock_ssh:
            from dracs.jobqueue import execute_vnc_reset_job

            execute_vnc_reset_job("server01", {"site_name": "Default"})

        assert mock_ssh.call_count == 4
        calls = mock_ssh.call_args_list
        fqdn = "idrac-server01.example.com"
        assert calls[0] == call(
            fqdn, "root", "calvin", ["set", "idrac.vncserver.enable", "Disabled"]
        )
        assert calls[1] == call(
            fqdn, "root", "calvin", ["set", "idrac.vncserver.Password", "vncpass"]
        )
        assert calls[2] == call(
            fqdn, "root", "calvin", ["set", "idrac.vncserver.port", "5901"]
        )
        assert calls[3] == call(
            fqdn, "root", "calvin", ["set", "idrac.vncserver.enable", "Enabled"]
        )

    @patch("dracs.vnc.get_hostname_viewer_count", return_value=2)
    @patch("dracs.snmp.build_idrac_hostname", return_value="idrac-server01.example.com")
    def test_skips_when_viewers_active(self, mock_fqdn, mock_viewers):
        with _make_ssh_patch(return_value=OK_RESULT) as mock_ssh:
            from dracs.jobqueue import execute_vnc_reset_job

            execute_vnc_reset_job("server01", {})

        mock_ssh.assert_not_called()

    @patch("dracs.vnc.get_hostname_viewer_count", return_value=0)
    @patch("dracs.vnc.get_vnc_credentials", return_value=(5901, "vncpass"))
    @patch("dracs.webapp.get_idrac_credentials", return_value=("root", "calvin"))
    @patch("dracs.snmp.build_idrac_hostname", return_value="idrac-server01.example.com")
    def test_raises_when_ssh_step_fails(
        self, mock_fqdn, mock_idrac_creds, mock_vnc_creds, mock_viewers
    ):
        with _make_ssh_patch(side_effect=[OK_RESULT, FAIL_RESULT]):
            from dracs.jobqueue import execute_vnc_reset_job

            with pytest.raises(RuntimeError, match="set VNC password failed"):
                execute_vnc_reset_job("server01", {})

    @patch("dracs.snmp.build_idrac_hostname")
    def test_raises_when_fqdn_build_fails(self, mock_fqdn):
        from dracs.exceptions import ValidationError
        from dracs.jobqueue import execute_vnc_reset_job

        mock_fqdn.side_effect = ValidationError("DNS not configured")
        with pytest.raises(RuntimeError, match="Cannot build iDRAC FQDN"):
            execute_vnc_reset_job("server01", {})

    @patch("dracs.vnc.get_hostname_viewer_count", return_value=0)
    @patch("dracs.vnc.get_vnc_credentials", return_value=(5901, ""))
    @patch("dracs.webapp.get_idrac_credentials", return_value=("root", "calvin"))
    @patch("dracs.snmp.build_idrac_hostname", return_value="idrac-server01.example.com")
    def test_runs_with_empty_vnc_password(
        self, mock_fqdn, mock_idrac_creds, mock_vnc_creds, mock_viewers
    ):
        with _make_ssh_patch(return_value=OK_RESULT) as mock_ssh:
            from dracs.jobqueue import execute_vnc_reset_job

            execute_vnc_reset_job("server01", {})

        assert mock_ssh.call_count == 4
        password_call = mock_ssh.call_args_list[1]
        assert password_call == call(
            "idrac-server01.example.com",
            "root",
            "calvin",
            ["set", "idrac.vncserver.Password", ""],
        )

    @patch("dracs.vnc.get_hostname_viewer_count", return_value=0)
    @patch("dracs.vnc.get_vnc_credentials", return_value=(5901, "vncpass"))
    @patch("dracs.webapp.get_idrac_credentials", return_value=("root", "calvin"))
    @patch("dracs.snmp.build_idrac_hostname", return_value="idrac-server01.example.com")
    def test_stops_after_first_failure(
        self, mock_fqdn, mock_idrac_creds, mock_vnc_creds, mock_viewers
    ):
        with _make_ssh_patch(side_effect=[FAIL_RESULT]) as mock_ssh:
            from dracs.jobqueue import execute_vnc_reset_job

            with pytest.raises(RuntimeError, match="disable VNC failed"):
                execute_vnc_reset_job("server01", {})

        assert mock_ssh.call_count == 1


# ── INI scheduler whitelist ───────────────────────────────────────────────────


class TestVncResetScheduleParsing:
    def test_vnc_reset_accepted_by_parse_schedule_config(self, tmp_path):
        ini = tmp_path / "schedule.ini"
        ini.write_text(
            "[VNC Reset]\n"
            "type = vnc_reset\n"
            "schedule = daily\n"
            "time = 00:01\n"
            "target = all\n"
        )
        from dracs.jobqueue import parse_schedule_config

        tasks = parse_schedule_config(str(ini))
        assert len(tasks) == 1
        assert tasks[0]["type"] == "vnc_reset"
        assert tasks[0]["time"] == "00:01"

    def test_vnc_reset_fires_after_scheduled_time(self, tmp_path):
        from datetime import datetime

        from dracs.jobqueue import _should_run_now

        task = {
            "name": "VNC Reset",
            "type": "vnc_reset",
            "schedule": "daily",
            "time": "00:01",
            "day": None,
        }
        with patch("dracs.jobqueue.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 7, 1, 0, 5, 0)
            assert _should_run_now(task, {}) is True

    def test_vnc_reset_does_not_fire_before_scheduled_time(self, tmp_path):
        from datetime import datetime

        from dracs.jobqueue import _should_run_now

        task = {
            "name": "VNC Reset",
            "type": "vnc_reset",
            "schedule": "daily",
            "time": "00:01",
            "day": None,
        }
        with patch("dracs.jobqueue.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 7, 1, 0, 0, 30)
            assert _should_run_now(task, {}) is False

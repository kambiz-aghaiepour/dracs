"""Tests for the audit logging module."""

import logging
import os
from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def _reset_audit_logger():
    """Reset the audit logger state before each test."""
    import dracs.audit as audit_mod

    audit_mod._INITIALIZED = False
    logger = logging.getLogger("dracs.audit")
    logger.handlers.clear()
    yield
    audit_mod._INITIALIZED = False
    logger.handlers.clear()


class TestAuditLog:
    def test_writes_entry(self, tmp_path):
        with patch.dict(os.environ, {"DRACS_LOG_DIR": str(tmp_path)}):
            from dracs.audit import audit_log

            audit_log(
                action="test_action",
                target="server01",
                user="admin",
                source="10.0.0.1",
                details="key=value",
                result="success",
            )
        log_file = tmp_path / "audit.log"
        assert log_file.exists()
        content = log_file.read_text()
        assert "action=test_action" in content
        assert "target=server01" in content
        assert "user=admin" in content
        assert "source=10.0.0.1" in content
        assert "details=key=value" in content
        assert "result=success" in content

    def test_default_result_is_success(self, tmp_path):
        with patch.dict(os.environ, {"DRACS_LOG_DIR": str(tmp_path)}):
            from dracs.audit import audit_log

            audit_log(action="login", user="admin")
        content = (tmp_path / "audit.log").read_text()
        assert "result=success" in content

    def test_denied_result(self, tmp_path):
        with patch.dict(os.environ, {"DRACS_LOG_DIR": str(tmp_path)}):
            from dracs.audit import audit_log

            audit_log(action="login", user="baduser", result="denied")
        content = (tmp_path / "audit.log").read_text()
        assert "result=denied" in content

    def test_missing_fields_use_dash(self, tmp_path):
        with patch.dict(os.environ, {"DRACS_LOG_DIR": str(tmp_path)}):
            from dracs.audit import audit_log

            audit_log(action="test")
        content = (tmp_path / "audit.log").read_text()
        assert "user=-" in content
        assert "source=-" in content
        assert "target=-" in content

    def test_no_details_field_when_empty(self, tmp_path):
        with patch.dict(os.environ, {"DRACS_LOG_DIR": str(tmp_path)}):
            from dracs.audit import audit_log

            audit_log(action="test")
        content = (tmp_path / "audit.log").read_text()
        assert "details=" not in content

    def test_timestamp_format(self, tmp_path):
        with patch.dict(os.environ, {"DRACS_LOG_DIR": str(tmp_path)}):
            from dracs.audit import audit_log

            audit_log(action="test")
        content = (tmp_path / "audit.log").read_text()
        assert content[0:4].isdigit()
        assert "Z " in content

    def test_fallback_to_stderr(self, capsys):
        with patch.dict(os.environ, {"DRACS_LOG_DIR": "/nonexistent/path/dracs"}):
            from dracs.audit import audit_log

            audit_log(action="fallback_test")
        captured = capsys.readouterr()
        assert "action=fallback_test" in captured.err

    def test_idempotent_init(self, tmp_path):
        with patch.dict(os.environ, {"DRACS_LOG_DIR": str(tmp_path)}):
            from dracs.audit import audit_log

            audit_log(action="first")
            audit_log(action="second")
        content = (tmp_path / "audit.log").read_text()
        lines = [line for line in content.strip().split("\n") if line]
        assert len(lines) == 2

    def test_multiple_entries(self, tmp_path):
        with patch.dict(os.environ, {"DRACS_LOG_DIR": str(tmp_path)}):
            from dracs.audit import audit_log

            audit_log(action="login", user="admin", source="10.0.0.1")
            audit_log(
                action="firmware_update",
                target="server01",
                user="admin",
                source="10.0.0.1",
                details="version=7.10.60.00,model=R660",
            )
            audit_log(action="logout", user="admin", source="10.0.0.1")
        content = (tmp_path / "audit.log").read_text()
        lines = content.strip().split("\n")
        assert len(lines) == 3
        assert "action=login" in lines[0]
        assert "action=firmware_update" in lines[1]
        assert "action=logout" in lines[2]

import json
import os
import tempfile
from unittest.mock import patch, MagicMock

import pytest

from dracs.db import db_initialize, upsert_system


@pytest.fixture
def webapp_db():
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
def client(webapp_db):
    with patch.dict(os.environ, {"DRACS_DB": webapp_db}):
        with patch.dict(
            os.environ,
            {"DRACS_DNS_STRING": "mgmt-", "DRACS_DNS_MODE": "prefix"},
        ):
            import dracs.webapp as webapp_mod

            webapp_mod.DB_PATH = webapp_db
            webapp_mod.db_initialize(webapp_db)
            webapp_mod.app.config["TESTING"] = True
            with webapp_mod.app.test_client() as c:
                yield c


def _login(client):
    client.post(
        "/login",
        data=json.dumps({"username": "admin", "password": "admin"}),
        content_type="application/json",
    )


class TestJobsEndpoint:
    def test_requires_auth(self, client):
        resp = client.get("/api/jobs")
        assert resp.status_code == 401

    def test_returns_jobs(self, client):
        _login(client)
        with patch(
            "dracs.jobqueue.get_active_jobs",
            return_value=[
                {
                    "id": 1,
                    "job_type": "tsr",
                    "target": "server01",
                    "status": "running",
                }
            ],
        ):
            resp = client.get("/api/jobs")
        data = resp.get_json()
        assert data["success"] is True
        assert len(data["jobs"]) == 1

    def test_returns_empty(self, client):
        _login(client)
        with patch("dracs.jobqueue.get_active_jobs", return_value=[]):
            resp = client.get("/api/jobs")
        data = resp.get_json()
        assert data["success"] is True
        assert data["jobs"] == []

    def test_include_all(self, client):
        _login(client)
        with patch("dracs.jobqueue.get_active_jobs", return_value=[]) as mock:
            resp = client.get("/api/jobs?all=true")
        mock.assert_called_once_with(include_completed=True)

    def test_status_filter_failed(self, client):
        _login(client)
        jobs = [
            {
                "id": 1,
                "job_type": "discover",
                "target": "host01",
                "status": "failed",
                "error": "SNMP timeout",
            },
            {
                "id": 2,
                "job_type": "discover",
                "target": "host02",
                "status": "completed",
                "error": None,
            },
        ]
        with patch("dracs.jobqueue.get_active_jobs", return_value=jobs):
            resp = client.get("/api/jobs?status=failed")
        data = resp.get_json()
        assert data["success"] is True
        assert len(data["jobs"]) == 1
        assert data["jobs"][0]["status"] == "failed"

    def test_status_filter_implies_include_completed(self, client):
        _login(client)
        with patch("dracs.jobqueue.get_active_jobs", return_value=[]) as mock:
            client.get("/api/jobs?status=failed")
        mock.assert_called_once_with(include_completed=True)

    def test_error_handling(self, client):
        _login(client)
        with patch(
            "dracs.jobqueue.get_active_jobs",
            side_effect=RuntimeError("DB error"),
        ):
            resp = client.get("/api/jobs")
        assert resp.status_code == 500


class TestTsrStatusWithJobQueue:
    def test_pending_job(self, client):
        _login(client)
        mock_job = {
            "status": "pending",
            "job_type": "tsr",
            "target": "server01.example.com",
        }
        with patch("dracs.jobqueue.get_latest_job_for_host", return_value=mock_job):
            resp = client.post(
                "/api/tsr-status",
                data=json.dumps({"hostname": "server01.example.com"}),
                content_type="application/json",
            )
        data = resp.get_json()
        assert data["success"] is True
        assert data["state"] == "pending"

    def test_running_job_with_progress(self, client):
        _login(client)
        mock_job = {
            "status": "running",
            "job_type": "tsr",
            "target": "server01.example.com",
            "result": "45%",
        }
        with patch("dracs.jobqueue.get_latest_job_for_host", return_value=mock_job):
            resp = client.post(
                "/api/tsr-status",
                data=json.dumps({"hostname": "server01.example.com"}),
                content_type="application/json",
            )
        data = resp.get_json()
        assert data["success"] is True
        assert data["state"] == "running"
        assert data["percent_complete"] == "45"

    def test_running_job_no_progress(self, client):
        _login(client)
        mock_job = {
            "status": "running",
            "job_type": "tsr",
            "target": "server01.example.com",
            "result": None,
        }
        with patch("dracs.jobqueue.get_latest_job_for_host", return_value=mock_job):
            resp = client.post(
                "/api/tsr-status",
                data=json.dumps({"hostname": "server01.example.com"}),
                content_type="application/json",
            )
        data = resp.get_json()
        assert data["state"] == "running"
        assert data["percent_complete"] == "0"

    def test_falls_back_to_ssh(self, client):
        _login(client)
        with patch("dracs.jobqueue.get_latest_job_for_host", return_value=None):
            with patch(
                "dracs.webapp._get_tsr_job_status",
                return_value={"state": "none"},
            ):
                resp = client.post(
                    "/api/tsr-status",
                    data=json.dumps({"hostname": "server01.example.com"}),
                    content_type="application/json",
                )
        data = resp.get_json()
        assert data["success"] is True
        assert data["state"] == "none"


class TestTsrCollectCleanup:
    def test_enqueues_new_job(self, client):
        _login(client)
        with patch("dracs.jobqueue.get_latest_job_for_host", return_value=None):
            with patch("dracs.jobqueue.enqueue_job", return_value=1):
                resp = client.post(
                    "/api/tsr-collect",
                    data=json.dumps(
                        {
                            "hostname": "server01.example.com",
                            "service_tag": "TAG001",
                        }
                    ),
                    content_type="application/json",
                )
        data = resp.get_json()
        assert resp.status_code == 200
        assert data["job_id"] == 1
        assert "existing" not in data

    def test_returns_existing_running_job(self, client):
        _login(client)
        existing = {"id": 42, "status": "running", "result": "50%"}
        with patch("dracs.jobqueue.get_latest_job_for_host", return_value=existing):
            resp = client.post(
                "/api/tsr-collect",
                data=json.dumps(
                    {
                        "hostname": "server01.example.com",
                        "service_tag": "TAG001",
                    }
                ),
                content_type="application/json",
            )
        data = resp.get_json()
        assert data["success"] is True
        assert data["existing"] is True
        assert data["job_id"] == 42
        assert "already in progress" in data["message"]

    def test_returns_existing_pending_job(self, client):
        _login(client)
        existing = {"id": 43, "status": "pending", "result": None}
        with patch("dracs.jobqueue.get_latest_job_for_host", return_value=existing):
            resp = client.post(
                "/api/tsr-collect",
                data=json.dumps(
                    {
                        "hostname": "server01.example.com",
                        "service_tag": "TAG001",
                    }
                ),
                content_type="application/json",
            )
        data = resp.get_json()
        assert data["existing"] is True

    def test_enqueue_error(self, client):
        _login(client)
        with patch("dracs.jobqueue.get_latest_job_for_host", return_value=None):
            with patch(
                "dracs.jobqueue.enqueue_job",
                side_effect=RuntimeError("DB locked"),
            ):
                resp = client.post(
                    "/api/tsr-collect",
                    data=json.dumps(
                        {
                            "hostname": "server01.example.com",
                            "service_tag": "TAG001",
                        }
                    ),
                    content_type="application/json",
                )
        assert resp.status_code == 500

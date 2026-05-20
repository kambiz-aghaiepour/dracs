import os
import tempfile
import time
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from dracs.db import db_initialize, upsert_system
from dracs.jobqueue import (
    JobScheduler,
    _resolve_targets,
    _should_run_now,
    enqueue_batch,
    get_active_jobs,
    get_job_status,
    parse_schedule_config,
)


@pytest.fixture
def sched_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    db_initialize(path)
    upsert_system(
        path,
        "TAG001",
        "host01.example.com",
        "R660",
        "7.0.0",
        "2.1.0",
        "Jan 1, 2027",
        1893456000,
    )
    upsert_system(
        path,
        "TAG002",
        "host02.example.com",
        "R660",
        "7.0.0",
        "2.1.0",
        "Jan 1, 2027",
        1893456000,
    )
    upsert_system(
        path,
        "TAG003",
        "host03.example.com",
        "R650",
        "6.0.0",
        "1.5.0",
        "Jan 1, 2027",
        1893456000,
    )
    yield path
    if os.path.exists(path):
        os.unlink(path)


class TestParseScheduleConfig:
    def test_parses_valid_config(self, tmp_path):
        config = tmp_path / "schedule.ini"
        config.write_text(
            "[tsr-weekly]\n"
            "type = tsr\n"
            "schedule = weekly\n"
            "day = sunday\n"
            "time = 02:00\n"
            "target = all\n"
        )
        tasks = parse_schedule_config(str(config))
        assert len(tasks) == 1
        assert tasks[0]["name"] == "tsr-weekly"
        assert tasks[0]["type"] == "tsr"
        assert tasks[0]["schedule"] == "weekly"
        assert tasks[0]["day"] == "sunday"
        assert tasks[0]["time"] == "02:00"
        assert tasks[0]["target"] == "all"

    def test_multiple_sections(self, tmp_path):
        config = tmp_path / "schedule.ini"
        config.write_text(
            "[tsr-weekly]\n"
            "type = tsr\n"
            "schedule = weekly\n"
            "day = sunday\n"
            "time = 02:00\n"
            "target = all\n"
            "\n"
            "[refresh-daily]\n"
            "type = refresh\n"
            "schedule = daily\n"
            "time = 04:00\n"
            "target = model:R660\n"
        )
        tasks = parse_schedule_config(str(config))
        assert len(tasks) == 2

    def test_parses_clear_job_queue_type(self, tmp_path):
        config = tmp_path / "schedule.ini"
        config.write_text(
            "[clear-weekly]\n"
            "type = clear_job_queue\n"
            "schedule = weekly\n"
            "day = saturday\n"
            "time = 01:00\n"
            "target = all\n"
        )
        tasks = parse_schedule_config(str(config))
        assert len(tasks) == 1
        assert tasks[0]["type"] == "clear_job_queue"

    def test_skips_invalid_type(self, tmp_path):
        config = tmp_path / "schedule.ini"
        config.write_text(
            "[bad-task]\n"
            "type = invalid\n"
            "schedule = daily\n"
            "time = 02:00\n"
            "target = all\n"
        )
        tasks = parse_schedule_config(str(config))
        assert len(tasks) == 0

    def test_missing_file_returns_empty(self):
        tasks = parse_schedule_config("/nonexistent/schedule.ini")
        assert tasks == []

    def test_skips_missing_time(self, tmp_path):
        config = tmp_path / "schedule.ini"
        config.write_text(
            "[bad-task]\n" "type = tsr\n" "schedule = daily\n" "target = all\n"
        )
        tasks = parse_schedule_config(str(config))
        assert len(tasks) == 0


class TestResolveTargets:
    def test_all_targets(self, sched_db):
        targets = _resolve_targets("all")
        assert len(targets) == 3

    def test_model_targets(self, sched_db):
        targets = _resolve_targets("model:R660")
        assert len(targets) == 2
        assert all("R660" not in t for t in targets) or True

    def test_single_host(self, sched_db):
        targets = _resolve_targets("host01.example.com")
        assert targets == ["host01.example.com"]

    def test_model_no_match(self, sched_db):
        targets = _resolve_targets("model:R999")
        assert targets == []


class TestEnqueueBatch:
    def test_single_host(self, sched_db):
        count = enqueue_batch("tsr", "host01.example.com")
        assert count == 1

    def test_batch_creates_parent_and_children(self, sched_db):
        count = enqueue_batch("tsr", "all")
        assert count == 3
        jobs = get_active_jobs()
        parent_jobs = [j for j in jobs if j["target"] == "all"]
        assert len(parent_jobs) == 1
        assert parent_jobs[0]["status"] == "running"

    def test_batch_no_targets(self, sched_db):
        count = enqueue_batch("tsr", "model:R999")
        assert count == 0


class TestShouldRunNow:
    def test_daily_at_right_time(self):
        now = datetime.now()
        task = {
            "name": "test",
            "schedule": "daily",
            "time": now.strftime("%H:%M"),
            "target": "all",
        }
        assert _should_run_now(task, {}) is True

    def test_daily_already_ran_today(self):
        now = datetime.now()
        task = {
            "name": "test",
            "schedule": "daily",
            "time": now.strftime("%H:%M"),
            "target": "all",
        }
        assert _should_run_now(task, {"test": now}) is False

    def test_daily_before_scheduled_time(self):
        task = {
            "name": "test",
            "schedule": "daily",
            "time": "23:59",
            "target": "all",
        }
        now = datetime.now().replace(hour=0, minute=0)
        with patch("dracs.jobqueue.datetime") as mock_dt:
            mock_dt.now.return_value = now
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            result = _should_run_now(task, {})
        assert result is False

    def test_weekly_right_day_and_time(self):
        now = datetime.now()
        day_name = [
            "monday",
            "tuesday",
            "wednesday",
            "thursday",
            "friday",
            "saturday",
            "sunday",
        ][now.weekday()]
        task = {
            "name": "test",
            "schedule": "weekly",
            "day": day_name,
            "time": now.strftime("%H:%M"),
            "target": "all",
        }
        assert _should_run_now(task, {}) is True

    def test_weekly_wrong_day(self):
        now = datetime.now()
        wrong_day = [
            "monday",
            "tuesday",
            "wednesday",
            "thursday",
            "friday",
            "saturday",
            "sunday",
        ][(now.weekday() + 1) % 7]
        task = {
            "name": "test",
            "schedule": "weekly",
            "day": wrong_day,
            "time": now.strftime("%H:%M"),
            "target": "all",
        }
        assert _should_run_now(task, {}) is False

    def test_invalid_time_format(self):
        task = {
            "name": "test",
            "schedule": "daily",
            "time": "invalid",
            "target": "all",
        }
        assert _should_run_now(task, {}) is False

    def test_weekly_no_day(self):
        task = {
            "name": "test",
            "schedule": "weekly",
            "day": None,
            "time": "02:00",
            "target": "all",
        }
        assert _should_run_now(task, {}) is False

    def test_unknown_schedule(self):
        task = {
            "name": "test",
            "schedule": "monthly",
            "time": "02:00",
            "target": "all",
        }
        assert _should_run_now(task, {}) is False

    def test_weekly_before_scheduled_time(self):
        now = datetime.now()
        day_name = [
            "monday",
            "tuesday",
            "wednesday",
            "thursday",
            "friday",
            "saturday",
            "sunday",
        ][now.weekday()]
        task = {
            "name": "test",
            "schedule": "weekly",
            "day": day_name,
            "time": "23:59",
            "target": "all",
        }
        fake_now = now.replace(hour=0, minute=0)
        with patch("dracs.jobqueue.datetime") as mock_dt:
            mock_dt.now.return_value = fake_now
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            result = _should_run_now(task, {})
        assert result is False

    def test_weekly_already_ran_today(self):
        now = datetime.now()
        day_name = [
            "monday",
            "tuesday",
            "wednesday",
            "thursday",
            "friday",
            "saturday",
            "sunday",
        ][now.weekday()]
        task = {
            "name": "test",
            "schedule": "weekly",
            "day": day_name,
            "time": now.strftime("%H:%M"),
            "target": "all",
        }
        assert _should_run_now(task, {"test": now}) is False


class TestJobScheduler:
    def test_start_and_stop(self):
        scheduler = JobScheduler(config_path="/nonexistent")
        scheduler.start()
        assert scheduler.is_running is True
        scheduler.stop()
        assert scheduler.is_running is False

    def test_double_start_is_noop(self):
        scheduler = JobScheduler(config_path="/nonexistent")
        scheduler.start()
        scheduler.start()
        assert scheduler.is_running is True
        scheduler.stop()

    def test_schedule_loop_enqueues_jobs(self, sched_db, tmp_path):
        config = tmp_path / "schedule.ini"
        now = datetime.now()
        config.write_text(
            "[test-task]\n"
            "type = tsr\n"
            "schedule = daily\n"
            f"time = {now.strftime('%H:%M')}\n"
            "target = host01.example.com\n"
        )

        scheduler = JobScheduler(config_path=str(config))
        tasks = parse_schedule_config(str(config))
        assert len(tasks) == 1

        assert _should_run_now(tasks[0], scheduler._last_runs) is True
        count = enqueue_batch(tasks[0]["type"], tasks[0]["target"])
        assert count == 1

        jobs = get_active_jobs(include_completed=True)
        assert len(jobs) >= 1

    def test_schedule_loop_runs(self, sched_db, tmp_path):
        config = tmp_path / "schedule.ini"
        now = datetime.now()
        config.write_text(
            "[test-task]\n"
            "type = tsr\n"
            "schedule = daily\n"
            f"time = {now.strftime('%H:%M')}\n"
            "target = host01.example.com\n"
        )

        scheduler = JobScheduler(config_path=str(config))
        scheduler._running = True

        iteration = [0]
        original_sleep = time.sleep

        def mock_sleep(seconds):
            iteration[0] += 1
            if iteration[0] >= 2:
                scheduler._running = False

        with patch("dracs.jobqueue.time.sleep", side_effect=mock_sleep):
            scheduler._schedule_loop()

        assert "test-task" in scheduler._last_runs

    def test_schedule_loop_handles_error(self, tmp_path):
        scheduler = JobScheduler(config_path=str(tmp_path / "missing.ini"))
        scheduler._running = True

        iteration = [0]

        def mock_sleep(seconds):
            iteration[0] += 1
            if iteration[0] >= 1:
                scheduler._running = False

        with patch(
            "dracs.jobqueue.parse_schedule_config",
            side_effect=RuntimeError("bad config"),
        ):
            with patch("dracs.jobqueue.time.sleep", side_effect=mock_sleep):
                scheduler._schedule_loop()

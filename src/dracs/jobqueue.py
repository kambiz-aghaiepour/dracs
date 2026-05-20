import asyncio
import configparser
import logging
import os
import socket
import subprocess  # nosec
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from dracs.db import Job, System, get_session

logger = logging.getLogger(__name__)


def enqueue_job(
    job_type: str,
    target: str,
    parent_id: Optional[int] = None,
) -> int:
    with get_session() as session:
        job = Job(
            parent_id=parent_id,
            job_type=job_type,
            target=target,
            status="pending",
            created_at=datetime.now().isoformat(),
        )
        session.add(job)
        session.commit()
        return job.id


def claim_next_job(worker_id: str) -> Optional[dict]:
    with get_session() as session:
        job = (
            session.query(Job)
            .filter(Job.status == "pending")
            .order_by(Job.created_at)
            .first()
        )
        if job is None:
            return None
        job.status = "running"
        job.worker_id = worker_id
        job.started_at = datetime.now().isoformat()
        session.commit()
        return {
            "id": job.id,
            "parent_id": job.parent_id,
            "job_type": job.job_type,
            "target": job.target,
        }


def complete_job(job_id: int, result: Optional[str] = None) -> None:
    with get_session() as session:
        job = session.get(Job, job_id)
        if job is None:
            return
        job.status = "completed"
        job.completed_at = datetime.now().isoformat()
        job.result = result
        session.commit()

        if job.parent_id is not None:
            _update_parent_status(session, job.parent_id)


def fail_job(job_id: int, error: str) -> None:
    with get_session() as session:
        job = session.get(Job, job_id)
        if job is None:
            return
        job.status = "failed"
        job.completed_at = datetime.now().isoformat()
        job.error = error
        session.commit()

        if job.parent_id is not None:
            _update_parent_status(session, job.parent_id)


def _update_parent_status(session, parent_id: int) -> None:
    children = session.query(Job).filter(Job.parent_id == parent_id).all()
    if not children:
        return

    all_done = all(c.status in ("completed", "failed") for c in children)
    if not all_done:
        return

    parent = session.get(Job, parent_id)
    if parent is None:
        return

    any_failed = any(c.status == "failed" for c in children)
    parent.status = "failed" if any_failed else "completed"
    parent.completed_at = datetime.now().isoformat()

    completed_count = sum(1 for c in children if c.status == "completed")
    failed_count = sum(1 for c in children if c.status == "failed")
    parent.result = f"{completed_count} completed, {failed_count} failed"
    session.commit()


def cancel_job(job_id: int) -> bool:
    with get_session() as session:
        job = session.get(Job, job_id)
        if job is None or job.status != "pending":
            return False
        job.status = "failed"
        job.completed_at = datetime.now().isoformat()
        job.error = "Cancelled"
        session.commit()

        if job.parent_id is not None:
            _update_parent_status(session, job.parent_id)
        return True


def get_job_status(job_id: int) -> Optional[dict]:
    with get_session() as session:
        job = session.get(Job, job_id)
        if job is None:
            return None
        return _job_to_dict(job)


def get_jobs_for_host(hostname: str) -> list:
    with get_session() as session:
        jobs = (
            session.query(Job)
            .filter(Job.target == hostname)
            .order_by(Job.created_at.desc())
            .all()
        )
        return [_job_to_dict(j) for j in jobs]


def get_active_jobs(include_completed: bool = False) -> list:
    with get_session() as session:
        query = session.query(Job).filter(Job.parent_id.is_(None))
        if not include_completed:
            query = query.filter(Job.status.in_(["pending", "running"]))
        query = query.order_by(Job.created_at.desc())
        jobs = query.all()

        result = []
        for job in jobs:
            d = _job_to_dict(job)
            children = session.query(Job).filter(Job.parent_id == job.id).all()
            if children:
                completed = sum(1 for c in children if c.status == "completed")
                failed = sum(1 for c in children if c.status == "failed")
                total = len(children)
                d["progress"] = f"{completed + failed}/{total}"
            result.append(d)
        return result


def get_latest_job_for_host(hostname: str, job_type: str) -> Optional[dict]:
    with get_session() as session:
        job = (
            session.query(Job)
            .filter(Job.target == hostname, Job.job_type == job_type)
            .order_by(Job.created_at.desc())
            .first()
        )
        if job is None:
            return None
        return _job_to_dict(job)


def purge_completed_jobs(older_than_days: int = 7) -> int:
    cutoff = (datetime.now() - timedelta(days=older_than_days)).isoformat()
    count = 0
    with get_session() as session:
        old_children = (
            session.query(Job)
            .filter(
                Job.status.in_(["completed", "failed"]),
                Job.completed_at < cutoff,
                Job.parent_id.isnot(None),
            )
            .all()
        )
        for c in old_children:
            session.delete(c)
            count += 1

        old_parents = (
            session.query(Job)
            .filter(
                Job.status.in_(["completed", "failed"]),
                Job.completed_at < cutoff,
                Job.parent_id.is_(None),
            )
            .all()
        )
        for p in old_parents:
            remaining = session.query(Job).filter(Job.parent_id == p.id).count()
            if remaining == 0:
                session.delete(p)
                count += 1

        session.commit()
    return count


def update_job_progress(job_id: int, progress: str) -> None:
    with get_session() as session:
        job = session.get(Job, job_id)
        if job:
            job.result = progress
            session.commit()


def _job_to_dict(job: Job) -> dict:
    return {
        "id": job.id,
        "parent_id": job.parent_id,
        "job_type": job.job_type,
        "target": job.target,
        "status": job.status,
        "created_at": job.created_at,
        "started_at": job.started_at,
        "completed_at": job.completed_at,
        "result": job.result,
        "error": job.error,
        "worker_id": job.worker_id,
    }


class JobProcessor:
    def __init__(self, max_workers: int = 50, poll_interval: float = 2.0):
        """Initialize job processor with bounded thread pool."""
        self._max_workers = max_workers
        self._poll_interval = poll_interval
        self._executor = None
        self._running = False
        self._thread = None
        self._worker_id = f"processor-{id(self)}"

    def start(self) -> None:
        if self._running:
            return
        self._executor = ThreadPoolExecutor(max_workers=self._max_workers)
        self._running = True
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        logger.info("Job processor started (max_workers=%d)", self._max_workers)

    def stop(self) -> None:
        self._running = False
        if self._executor:
            self._executor.shutdown(wait=False)
        logger.info("Job processor stopped")

    @property
    def is_running(self) -> bool:
        return self._running

    def _run_loop(self) -> None:
        while self._running:
            try:
                job = claim_next_job(self._worker_id)
                if job:
                    self._executor.submit(self._execute_job, job)
                else:
                    time.sleep(self._poll_interval)
            except Exception as exc:
                logger.error("Job processor error: %s", exc)
                time.sleep(self._poll_interval)

    def _execute_job(self, job: dict) -> None:
        job_id = job["id"]
        try:
            if job["job_type"] == "tsr":
                execute_tsr_job(job["target"], job_id=job_id)
            elif job["job_type"] == "refresh":
                execute_refresh_job(job["target"])
            else:
                fail_job(job_id, error=f"Unknown job type: {job['job_type']}")
                return
            complete_job(job_id, result="Success")
        except Exception as exc:
            logger.error("Job %d failed: %s", job_id, exc)
            fail_job(job_id, error=str(exc))


def _report_running_progress(jobs: list, job_id: Optional[int]) -> bool:
    for j in jobs:
        if j.get("status") == "Running":
            if job_id is not None:
                pct = j.get("percent_complete", "0")
                update_job_progress(job_id, f"{pct}%")
            return True
    return False


def _poll_for_start(get_sa_jobs, hostname, job_id, poll_interval, max_wait):
    elapsed = 0
    while elapsed < max_wait:
        time.sleep(poll_interval)
        elapsed += poll_interval
        jobs = get_sa_jobs(hostname)
        if jobs and _report_running_progress(jobs, job_id):
            return elapsed
    raise RuntimeError("TSR collection did not start within timeout")


def _poll_for_complete(get_sa_jobs, hostname, job_id, poll_interval, max_wait, elapsed):
    while elapsed < max_wait:
        time.sleep(poll_interval)
        elapsed += poll_interval
        jobs = get_sa_jobs(hostname)
        if jobs is None:
            continue
        if _report_running_progress(jobs, job_id):
            continue
        collection_done = any(
            "collection operation is completed successfully"
            in j.get("message", "").lower()
            for j in jobs
            if j.get("status") == "Completed"
        )
        if collection_done:
            return
    raise RuntimeError("TSR collection did not complete within timeout")


def execute_tsr_job(hostname: str, job_id: Optional[int] = None) -> None:
    from dracs.webapp import (
        _build_ssh_racadm_cmd,
        _find_tsr_zip,
        _get_sa_jobs,
        _stage_tsr_files,
        _wait_for_tsr_export,
    )

    with get_session() as session:
        system = session.query(System).filter(System.name == hostname).first()
        if system is None:
            raise ValueError(f"Host {hostname} not found in database")
        service_tag = system.svc_tag

    fqdn = socket.getfqdn()
    poll_interval = 20
    max_wait = 1800

    if job_id is not None:
        update_job_progress(job_id, "Collecting")

    cmd = _build_ssh_racadm_cmd(
        hostname, "techsupreport", "collect", "-t", "SysInfo,TTYLog"
    )
    result = subprocess.run(  # nosec # nosemgrep
        cmd, capture_output=True, text=True, timeout=30  # nosemgrep
    )
    if result.returncode != 0:
        error_msg = result.stderr[:200] if result.stderr else result.stdout[:200]
        raise RuntimeError(f"Failed to start TSR collection: {error_msg}")

    elapsed = _poll_for_start(_get_sa_jobs, hostname, job_id, poll_interval, max_wait)
    _poll_for_complete(_get_sa_jobs, hostname, job_id, poll_interval, max_wait, elapsed)

    if job_id is not None:
        update_job_progress(job_id, "Exporting")

    export_cmd = _build_ssh_racadm_cmd(
        hostname, "techsupreport", "export", "-l", f"tftp://{fqdn}"
    )
    subprocess.run(  # nosec # nosemgrep
        export_cmd, capture_output=True, text=True, timeout=30  # nosemgrep
    )

    poll_cmd = _build_ssh_racadm_cmd(hostname, "jobqueue", "view")
    if not _wait_for_tsr_export(poll_cmd, poll_interval, max_wait):
        raise RuntimeError("TSR export did not complete within timeout")

    approx_time = datetime.now()
    time.sleep(5)
    zip_path = _find_tsr_zip(service_tag, approx_time)
    if not zip_path:
        raise RuntimeError("TSR zip file not found after export")

    _stage_tsr_files(zip_path, hostname, service_tag)


def execute_refresh_job(hostname: str) -> None:
    from dracs.commands import refresh_dell_warranty

    warranty = os.environ.get("DRACS_DB", "warranty.db")
    asyncio.run(refresh_dell_warranty(None, hostname, warranty))


VALID_DAYS = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}

DEFAULT_SCHEDULE_PATH = "/etc/dracs/schedule.ini"


def parse_schedule_config(
    config_path: str = DEFAULT_SCHEDULE_PATH,
) -> list:
    config = configparser.ConfigParser()
    if not Path(config_path).exists():
        return []
    config.read(config_path)

    tasks = []
    for section in config.sections():
        task = {
            "name": section,
            "type": config.get(section, "type", fallback=None),
            "schedule": config.get(section, "schedule", fallback=None),
            "time": config.get(section, "time", fallback=None),
            "day": config.get(section, "day", fallback=None),
            "target": config.get(section, "target", fallback=None),
        }
        if task["type"] in ("tsr", "refresh") and task["schedule"] and task["time"]:
            tasks.append(task)
        else:
            logger.warning("Skipping invalid schedule entry: %s", section)
    return tasks


def _resolve_targets(target_spec: str) -> list:
    if target_spec == "all":
        with get_session() as session:
            systems = session.query(System).order_by(System.name).all()
            return [s.name for s in systems]
    elif target_spec.startswith("model:"):
        model = target_spec.split(":", 1)[1]
        with get_session() as session:
            systems = session.query(System).filter(System.model == model).all()
            return [s.name for s in systems]
    else:
        return [target_spec]


def enqueue_batch(job_type: str, target_spec: str) -> int:
    hostnames = _resolve_targets(target_spec)
    if not hostnames:
        return 0
    if len(hostnames) == 1:
        enqueue_job(job_type, hostnames[0])
        return 1

    parent_id = enqueue_job(job_type, target_spec)
    with get_session() as session:
        parent = session.get(Job, parent_id)
        parent.status = "running"
        session.commit()

    for hostname in hostnames:
        enqueue_job(job_type, hostname, parent_id=parent_id)
    return len(hostnames)


def _should_run_now(task: dict, last_runs: dict) -> bool:
    now = datetime.now()
    task_name = task["name"]

    try:
        hour, minute = map(int, task["time"].split(":"))
    except (ValueError, AttributeError):
        return False

    if task["schedule"] == "daily":
        scheduled_today = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if now < scheduled_today:
            return False
        last = last_runs.get(task_name)
        if last and last.date() == now.date():
            return False
        return True

    elif task["schedule"] == "weekly":
        day_name = (task.get("day") or "").lower()
        target_weekday = VALID_DAYS.get(day_name)
        if target_weekday is None:
            return False
        if now.weekday() != target_weekday:
            return False
        scheduled_today = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if now < scheduled_today:
            return False
        last = last_runs.get(task_name)
        if last and last.date() == now.date():
            return False
        return True

    return False


class JobScheduler:
    def __init__(self, config_path: str = DEFAULT_SCHEDULE_PATH):
        """Initialize job scheduler with path to schedule config."""
        self._config_path = config_path
        self._thread = None
        self._running = False
        self._last_runs: dict = {}

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._schedule_loop, daemon=True)
        self._thread.start()
        logger.info("Job scheduler started (config=%s)", self._config_path)

    def stop(self) -> None:
        self._running = False

    @property
    def is_running(self) -> bool:
        return self._running

    def _schedule_loop(self) -> None:
        while self._running:
            try:
                tasks = parse_schedule_config(self._config_path)
                for task in tasks:
                    if _should_run_now(task, self._last_runs):
                        count = enqueue_batch(task["type"], task["target"])
                        self._last_runs[task["name"]] = datetime.now()
                        logger.info(
                            "Scheduled %s: enqueued %d jobs for %s",
                            task["name"],
                            count,
                            task["target"],
                        )
            except Exception as exc:
                logger.error("Scheduler error: %s", exc)
            time.sleep(60)

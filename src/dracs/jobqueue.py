import asyncio
import logging
import socket
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from typing import Optional

from dracs.db import Job, get_session

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
                execute_tsr_job(job["target"])
            elif job["job_type"] == "refresh":
                execute_refresh_job(job["target"])
            else:
                fail_job(job_id, error=f"Unknown job type: {job['job_type']}")
                return
            complete_job(job_id, result="Success")
        except Exception as exc:
            logger.error("Job %d failed: %s", job_id, exc)
            fail_job(job_id, error=str(exc))


def execute_tsr_job(hostname: str) -> None:
    from dracs.webapp import (
        _build_ssh_racadm_cmd,
        _find_tsr_zip,
        _get_sa_jobs,
        _stage_tsr_files,
        _wait_for_tsr_export,
    )
    from dracs.db import System

    with get_session() as session:
        system = session.query(System).filter(System.name == hostname).first()
        if system is None:
            raise ValueError(f"Host {hostname} not found in database")
        service_tag = system.svc_tag

    fqdn = socket.getfqdn()
    poll_interval = 20
    max_wait = 1800

    cmd = _build_ssh_racadm_cmd(
        hostname, "techsupreport", "collect", "-t", "SysInfo,TTYLog"
    )
    result = subprocess.run(  # nosec # nosemgrep
        cmd, capture_output=True, text=True, timeout=30  # nosemgrep
    )
    if result.returncode != 0:
        error_msg = result.stderr[:200] if result.stderr else result.stdout[:200]
        raise RuntimeError(f"Failed to start TSR collection: {error_msg}")

    elapsed = 0
    while elapsed < max_wait:
        time.sleep(poll_interval)
        elapsed += poll_interval
        jobs = _get_sa_jobs(hostname)
        if jobs and any(j.get("status") == "Running" for j in jobs):
            break
    else:
        raise RuntimeError("TSR collection did not start within timeout")

    while elapsed < max_wait:
        time.sleep(poll_interval)
        elapsed += poll_interval
        jobs = _get_sa_jobs(hostname)
        if jobs is None:
            continue
        if any(j.get("status") == "Running" for j in jobs):
            continue
        collection_done = any(
            "collection operation is completed successfully"
            in j.get("message", "").lower()
            for j in jobs
            if j.get("status") == "Completed"
        )
        if collection_done:
            break
    else:
        raise RuntimeError("TSR collection did not complete within timeout")

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
    import os

    warranty = os.environ.get("DRACS_DB", "warranty.db")
    asyncio.run(refresh_dell_warranty(None, hostname, warranty))

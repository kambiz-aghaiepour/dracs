from datetime import datetime, timedelta
from typing import Optional

from dracs.db import Job, get_session


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

import asyncio
import configparser
import json as json_module
import logging
import os
import re
import shutil
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
    metadata: Optional[dict] = None,
    site_id: Optional[int] = None,
) -> int:
    with get_session() as session:
        job = Job(
            parent_id=parent_id,
            job_type=job_type,
            target=target,
            status="pending",
            created_at=datetime.now().isoformat(),
            metadata_json=json_module.dumps(metadata) if metadata else None,
            site_id=site_id,
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
            "metadata": (
                json_module.loads(job.metadata_json) if job.metadata_json else None
            ),
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


def get_active_jobs(
    include_completed: bool = False,
    status_filter: str = None,
    limit: int = None,
) -> list:
    with get_session() as session:
        query = session.query(Job).filter(Job.parent_id.is_(None))
        if status_filter:
            query = query.filter(Job.status == status_filter)
        elif not include_completed:
            query = query.filter(Job.status.in_(["pending", "running"]))
        query = query.order_by(Job.created_at.desc())
        if limit:
            query = query.limit(limit)
        jobs = query.all()

        # Batch-load children in a single query to avoid N+1 per parent
        job_ids = [j.id for j in jobs]
        children_map: dict = {}
        if job_ids:
            all_children = session.query(Job).filter(Job.parent_id.in_(job_ids)).all()
            for child in all_children:
                children_map.setdefault(child.parent_id, []).append(child)

        result = []
        for job in jobs:
            d = _job_to_dict(job)
            children = children_map.get(job.id, [])
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


def recover_stale_jobs() -> int:
    with get_session() as session:
        stale = session.query(Job).filter(Job.status == "running").all()
        for job in stale:
            job.status = "pending"
            job.worker_id = None
            job.started_at = None
        session.commit()
        if stale:
            logger.warning("Recovered %d stale jobs to pending status", len(stale))
        return len(stale)


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
        "metadata": json_module.loads(job.metadata_json) if job.metadata_json else None,
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
        meta = job.get("metadata") or {}
        try:
            if job["job_type"] == "tsr":
                execute_tsr_job(job["target"], job_id=job_id, metadata=meta)
            elif job["job_type"] == "refresh":
                execute_refresh_job(job["target"])
            elif job["job_type"] == "firmware_update":
                execute_firmware_update_job(job["target"], meta)
            elif job["job_type"] == "bios_update":
                execute_bios_update_job(job["target"], meta)
            elif job["job_type"] == "clear_job_queue":
                execute_clear_job_queue(job["target"])
            elif job["job_type"] == "discover":
                execute_discover_job(job["target"], meta)
            elif job["job_type"] == "racadm_config":
                execute_racadm_config_job(job["target"], meta)
            elif job["job_type"] == "config_collect":
                execute_config_collect_job(job["target"], meta)
            elif job["job_type"] == "ssl_cert_upload":
                execute_ssl_cert_upload_job(job["target"], meta)
            elif job["job_type"] == "vnc_reset":
                execute_vnc_reset_job(job["target"], meta)
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


_TSR_TS_RE = re.compile(r"^\d{14}$")
_TSR_BASE_DIR = Path("/var/lib/dracs/web/tsr")


def _prune_tsr_before_collect(hostname: str, keep_max: int) -> None:
    from dracs.webapp import _generate_tsr_index

    host_dir = _TSR_BASE_DIR / hostname
    if not host_dir.is_dir():
        return

    entries = []
    for zf in host_dir.glob("TSR*.zip"):
        ts_part = zf.name.replace("TSR", "").split("_")[0]
        try:
            dt = datetime.strptime(ts_part, "%Y%m%d%H%M%S")
            entries.append((dt, ts_part, zf))
        except ValueError:
            continue

    entries.sort(key=lambda e: e[0], reverse=True)

    target = keep_max - 1
    if len(entries) <= target:
        return

    keep_ts = {e[1] for e in entries[:target]}

    for _dt, _ts, zf_path in entries[target:]:
        try:
            zf_path.unlink()
        except Exception as exc:
            logger.warning("TSR prune: could not delete %s: %s", zf_path.name, exc)

    for ts_dir in host_dir.iterdir():
        if (
            ts_dir.is_dir()
            and _TSR_TS_RE.match(ts_dir.name)
            and ts_dir.name not in keep_ts
        ):
            try:
                shutil.rmtree(ts_dir)
            except Exception as exc:
                logger.warning(
                    "TSR prune: could not delete dir %s: %s", ts_dir.name, exc
                )

    _generate_tsr_index(hostname)


def execute_tsr_job(
    hostname: str,
    job_id: Optional[int] = None,
    metadata: Optional[dict] = None,
) -> None:
    from dracs.webapp import (
        _build_ssh_racadm_cmd,
        _find_tsr_zip,
        _get_sa_jobs,
        _stage_tsr_files,
        _wait_for_tsr_export,
    )

    keep_max = (metadata or {}).get("keep_max")
    if keep_max:
        _prune_tsr_before_collect(hostname, int(keep_max))

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


def execute_firmware_update_job(hostname: str, metadata: dict) -> None:
    from dracs.webapp import _build_ssh_racadm_cmd

    target_version = metadata.get("target_version", "")
    model = metadata.get("model", "")
    if not target_version or not model:
        raise ValueError("target_version and model required in job metadata")

    firmware_file = f"{model}-{target_version}.d9"
    fw_server = os.environ.get("DRACS_FIRMWARE_SERVER") or socket.getfqdn()
    fw_uri = os.environ.get("DRACS_FIRMWARE_URI", "/firmware/")
    firmware_url = f"http://{fw_server}{fw_uri}"

    cmd = _build_ssh_racadm_cmd(
        hostname, "update", "-f", firmware_file, "-l", firmware_url
    )
    result = subprocess.run(  # nosec # nosemgrep
        cmd, capture_output=True, text=True, timeout=120  # nosemgrep
    )
    if result.returncode != 0:
        error_msg = result.stderr[:200] if result.stderr else result.stdout[:200]
        raise RuntimeError(f"Firmware update failed: {error_msg}")


def execute_bios_update_job(hostname: str, metadata: dict) -> None:
    from dracs.webapp import (
        _build_ssh_racadm_cmd,
        get_bios_filename,
    )
    from urllib.parse import quote as url_quote, urlunparse

    target_bios = metadata.get("target_bios", "")
    model = metadata.get("model", "")
    if not target_bios or not model:
        raise ValueError("target_bios and model required in job metadata")

    bios_filename = get_bios_filename(model, target_bios)
    if not bios_filename:
        raise ValueError(
            f"BIOS filename not found for model {model} version {target_bios}"
        )

    bios_server = os.environ.get("DRACS_BIOS_SERVER") or socket.getfqdn()
    bios_uri = os.environ.get("DRACS_BIOS_URI", "/bios/")
    bios_url = urlunparse(
        ("http", bios_server, f"{bios_uri}{url_quote(model, safe='')}/", "", "", "")
    )

    cmd = _build_ssh_racadm_cmd(hostname, "update", "-f", bios_filename, "-l", bios_url)
    result = subprocess.run(  # nosec # nosemgrep
        cmd, capture_output=True, text=True, timeout=120  # nosemgrep
    )
    if result.returncode != 0:
        error_msg = result.stderr[:200] if result.stderr else result.stdout[:200]
        raise RuntimeError(f"BIOS update failed: {error_msg}")


def execute_clear_job_queue(hostname: str) -> None:
    from dracs.webapp import _build_ssh_racadm_cmd

    cmd = _build_ssh_racadm_cmd(hostname, "jobqueue", "delete", "--all")
    result = subprocess.run(  # nosec # nosemgrep
        cmd, capture_output=True, text=True, timeout=30  # nosemgrep
    )
    if result.returncode != 0:
        error_msg = result.stderr[:200] if result.stderr else result.stdout[:200]
        raise RuntimeError(f"Clear job queue failed: {error_msg}")


def execute_discover_job(hostname: str, metadata: dict) -> None:
    from dracs.commands import add_dell_warranty, discover_dell_system

    warranty = os.environ.get("DRACS_DB", "warranty.db")
    site_id = metadata.get("site_id")

    service_tag, model = asyncio.run(discover_dell_system(hostname, warranty))
    asyncio.run(
        add_dell_warranty(service_tag, hostname, model, warranty, site_id=site_id)
    )


_RACADM_SETTINGS = {
    "dns_from_dhcp": ("iDRAC.IPv4.DNSFromDHCP", "Enabled"),
    "ipmi_lan": ("iDRAC.IPMILan.Enable", "Enabled"),
    "host_header": ("iDRAC.webserver.HostHeaderCheck", "Disabled"),
    "ps_rapid_on": ("System.ServerPwr.PSRapidOn", "Disabled"),
    "sys_profile": ("BIOS.SysProfileSettings.SysProfile", "PerfPerWattOptimizedOs"),
}


def execute_racadm_config_job(hostname: str, metadata: dict) -> None:
    from dracs.webapp import _build_ssh_racadm_cmd
    from dracs.snmp import build_idrac_hostname
    from dracs.db import get_site_by_name, upsert_host_config
    from dracs.redfish import collect_all_for_host

    site_name = metadata.get("site_name", "")
    settings = metadata.get("settings", {})

    site = get_site_by_name(site_name) if site_name else None
    if site is None:
        raise RuntimeError(f"Unknown site: {site_name!r}")

    errors = []
    for key, enabled in settings.items():
        if not enabled:
            continue
        if key == "idrac_hostname":
            attr, value = "System.ServerOS.Hostname", build_idrac_hostname(hostname)
        elif key in _RACADM_SETTINGS:
            attr, value = _RACADM_SETTINGS[key]
        else:
            continue
        cmd = _build_ssh_racadm_cmd(hostname, "set", attr, value, site=site_name)
        result = subprocess.run(  # nosec # nosemgrep
            cmd, capture_output=True, text=True, timeout=60  # nosemgrep
        )
        if result.returncode != 0:
            errors.append(f"{key}: {(result.stderr or result.stdout)[:120]}")
            continue
        if key == "sys_profile":
            cmd2 = _build_ssh_racadm_cmd(
                hostname, "jobqueue", "create", "BIOS.Setup.1-1", site=site_name
            )
            subprocess.run(  # nosec # nosemgrep
                cmd2, capture_output=True, text=True, timeout=60  # nosemgrep
            )

    if errors:
        raise RuntimeError("; ".join(errors))

    enabled_attrs = {
        "ps_rapid_on_enabled": settings.get("ps_rapid_on", False),
        "dns_from_dhcp_enabled": settings.get("dns_from_dhcp", False),
        "ipmi_lan_enable_enabled": settings.get("ipmi_lan", False),
        "host_header_check_enabled": settings.get("host_header", False),
        "idrac_hostname_enabled": settings.get("idrac_hostname", False),
        "sys_profile_enabled": settings.get("sys_profile", False),
        "ssl_enabled": False,
    }
    try:
        collected = collect_all_for_host(hostname, site_name, enabled_attrs)
        if collected:
            upsert_host_config(hostname, site["id"], collected)
    except Exception as exc:
        logger.warning("Post-edit verification failed for %s: %s", hostname, exc)

    from dracs.config_collector import get_collector

    _cc = get_collector()
    if _cc is not None:
        _cc.trigger_host(hostname, site_name, site["id"])


def execute_config_collect_job(hostname: str, metadata: dict) -> None:
    from dracs.db import (
        get_site_by_name,
        get_site_config_collection,
        upsert_host_config,
    )
    from dracs.redfish import collect_all_for_host

    site_name = metadata.get("site_name", "")
    site = get_site_by_name(site_name) if site_name else None
    if site is None:
        raise RuntimeError(f"Unknown site: {site_name!r}")

    site_id = site["id"]
    settings = get_site_config_collection(site_id)
    data = collect_all_for_host(hostname, site_name, settings)
    if data:
        upsert_host_config(hostname, site_id, data)


def run_racadm_ssh(
    idrac_fqdn: str, username: str, password: str, racadm_args: list
) -> subprocess.CompletedProcess:
    """Run a single racadm command on an iDRAC over SSH using sshpass."""
    cmd = [
        "sshpass",
        "-p",
        password,
        "ssh",
        "-o",
        "StrictHostKeyChecking=no",
        "-o",
        "ConnectTimeout=30",
        "-o",
        "BatchMode=no",
        f"{username}@{idrac_fqdn}",
        "racadm",
    ] + racadm_args
    return subprocess.run(  # nosec # nosemgrep
        cmd, capture_output=True, text=True, timeout=60
    )


def execute_vnc_reset_job(hostname: str, metadata: dict) -> None:
    """Disable then re-enable the iDRAC VNC server to clear transient unresponsiveness.

    Skips the host if there are active VNC viewers so in-progress console
    sessions are not interrupted.  Reads iDRAC and VNC credentials from
    drac-passwords.ini using the same host-specific-then-site-default lookup
    used by all other job types.
    """
    from dracs.snmp import ValidationError, build_idrac_hostname
    from dracs.vnc import get_hostname_viewer_count, get_vnc_credentials
    from dracs.webapp import get_idrac_credentials

    try:
        idrac_fqdn = build_idrac_hostname(hostname)
    except ValidationError as exc:
        raise RuntimeError(f"Cannot build iDRAC FQDN for {hostname}: {exc}") from exc

    viewers = get_hostname_viewer_count(hostname)
    if viewers > 0:
        logger.info("vnc_reset skipping %s: %d active viewer(s)", hostname, viewers)
        return

    site_name = metadata.get("site_name")
    username, password = get_idrac_credentials(hostname, site=site_name)
    vnc_port, vnc_password = get_vnc_credentials(hostname, site=site_name)

    steps = [
        (["set", "idrac.vncserver.enable", "Disabled"], "disable VNC"),
        (["set", "idrac.vncserver.Password", vnc_password], "set VNC password"),
        (["set", "idrac.vncserver.port", str(vnc_port)], "set VNC port"),
        (["set", "idrac.vncserver.enable", "Enabled"], "enable VNC"),
    ]

    for args, description in steps:
        result = run_racadm_ssh(idrac_fqdn, username, password, args)
        if result.returncode != 0:
            raise RuntimeError(
                f"vnc_reset {hostname}: {description} failed "
                f"(rc={result.returncode}): {result.stderr.strip()}"
            )

    logger.info("vnc_reset completed for %s", hostname)


def _run_idracadm7(cmd: list, *, retries: int = 1, retry_delay: int = 5):
    """Run an idracadm7 command, retrying once on transient iDRAC failures."""
    for attempt in range(retries + 1):
        result = subprocess.run(  # nosec # nosemgrep
            cmd, capture_output=True, text=True, timeout=120  # nosemgrep
        )
        if result.returncode == 0:
            return result
        if attempt < retries:
            logger.warning(
                "idracadm7 attempt %d/%d failed (rc=%d), retrying in %ds: %s",
                attempt + 1,
                retries + 1,
                result.returncode,
                retry_delay,
                (result.stderr or result.stdout)[:200],
            )
            time.sleep(retry_delay)
    return result


def execute_ssl_cert_upload_job(hostname: str, metadata: dict) -> None:
    """Upload site SSL cert/key to an iDRAC if the stored cert expires later than the current one."""
    import tempfile

    from dracs.db import (
        get_host_config_data,
        get_host_ssl_override,
        get_site_by_name,
        get_site_ssl_config,
    )
    from dracs.snmp import build_idrac_hostname
    from dracs.webapp import get_idrac_credentials

    _IDRACADM7 = "/opt/dell/srvadmin/bin/idracadm7"
    if not os.path.exists(_IDRACADM7):
        raise RuntimeError(f"idracadm7 not found at {_IDRACADM7}")

    site_name = metadata.get("site_name", "")
    site = get_site_by_name(site_name) if site_name else None
    if site is None:
        raise RuntimeError(f"Unknown site: {site_name!r}")
    site_id = site["id"]

    ssl_cfg = get_site_ssl_config(site_id)
    if not ssl_cfg.get("enabled"):
        return

    host_override = get_host_ssl_override(hostname, site_id)
    cert_pem = (host_override or {}).get("cert_pem") or ssl_cfg.get("cert_pem")
    key_pem = (host_override or {}).get("key_pem") or ssl_cfg.get("key_pem")

    if not cert_pem or not key_pem:
        raise RuntimeError("No SSL cert/key configured for this site")

    stored_expiry = ssl_cfg.get("cert_expiry")
    stored_fingerprint = (host_override or {}).get("cert_fingerprint") or ssl_cfg.get(
        "cert_fingerprint"
    )
    host_rows = get_host_config_data(site_id, [hostname])
    idrac_expiry = host_rows[0].get("ssl_expiry") if host_rows else None
    idrac_self_signed = host_rows[0].get("ssl_self_signed") if host_rows else None
    idrac_fingerprint = host_rows[0].get("ssl_fingerprint") if host_rows else None

    if idrac_self_signed:
        if (
            stored_fingerprint
            and idrac_fingerprint
            and stored_fingerprint == idrac_fingerprint
        ):
            logger.info(
                "SSL cert for %s already deployed (fingerprint match on self-signed), skipping",
                hostname,
            )
            return
    elif stored_expiry and idrac_expiry:
        if stored_expiry <= idrac_expiry:
            logger.info(
                "SSL cert for %s already current (stored=%s idrac=%s), skipping",
                hostname,
                stored_expiry,
                idrac_expiry,
            )
            return

    idrac_fqdn = build_idrac_hostname(hostname)
    username, password = get_idrac_credentials(hostname, site=site_name)

    tmp_key = tmp_cert = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".pem", delete=False) as f:
            f.write(key_pem)
            tmp_key = f.name
        with tempfile.NamedTemporaryFile(mode="w", suffix=".pem", delete=False) as f:
            f.write(cert_pem)
            tmp_cert = f.name

        result = _run_idracadm7(
            [
                _IDRACADM7,
                "-r",
                idrac_fqdn,
                "-u",
                username,
                "-p",
                password,
                "sslkeyupload",
                "-t",
                "1",
                "-f",
                tmp_key,
            ]
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"sslkeyupload failed: {(result.stderr or result.stdout)[:200]}"
            )

        result = _run_idracadm7(
            [
                _IDRACADM7,
                "-r",
                idrac_fqdn,
                "-u",
                username,
                "-p",
                password,
                "sslcertupload",
                "-t",
                "1",
                "-f",
                tmp_cert,
            ]
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"sslcertupload failed: {(result.stderr or result.stdout)[:200]}"
            )

        logger.info(
            "SSL cert uploaded to %s (idrac_expiry=%s → stored_expiry=%s)",
            hostname,
            idrac_expiry,
            stored_expiry,
        )
    finally:
        for path in (tmp_key, tmp_cert):
            if path and os.path.exists(path):
                os.unlink(path)


def get_child_jobs(parent_id: int) -> list:
    with get_session() as session:
        jobs = session.query(Job).filter(Job.parent_id == parent_id).all()
        return [_job_to_dict(j) for j in jobs]


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
        raw_keep_max = config.get(section, "keep_max", fallback=None)
        try:
            keep_max = int(raw_keep_max) if raw_keep_max is not None else None
        except ValueError:
            keep_max = None
        task = {
            "name": section,
            "type": config.get(section, "type", fallback=None),
            "schedule": config.get(section, "schedule", fallback=None),
            "time": config.get(section, "time", fallback=None),
            "day": config.get(section, "day", fallback=None),
            "target": config.get(section, "target", fallback=None),
            "site": config.get(section, "site", fallback=None),
            "keep_max": keep_max,
        }
        if (
            task["type"] in ("tsr", "refresh", "clear_job_queue", "vnc_reset")
            and task["schedule"]
            and task["time"]
        ):
            tasks.append(task)
        else:
            logger.warning("Skipping invalid schedule entry: %s", section)
    return tasks


def _resolve_targets(target_spec: str, site_id: Optional[int] = None) -> list:
    if target_spec == "all":
        with get_session() as session:
            query = session.query(System).order_by(System.name)
            if site_id is not None:
                query = query.filter(System.site_id == site_id)
            return [s.name for s in query.all()]
    elif target_spec.startswith("model:"):
        model = target_spec.split(":", 1)[1]
        with get_session() as session:
            query = session.query(System).filter(System.model == model)
            if site_id is not None:
                query = query.filter(System.site_id == site_id)
            return [s.name for s in query.all()]
    else:
        return [target_spec]


def enqueue_batch(
    job_type: str,
    target_spec: str,
    site_id: Optional[int] = None,
    metadata: Optional[dict] = None,
) -> int:
    hostnames = _resolve_targets(target_spec, site_id=site_id)
    if not hostnames:
        return 0
    if len(hostnames) == 1:
        enqueue_job(job_type, hostnames[0], site_id=site_id, metadata=metadata)
        return 1

    parent_id = enqueue_job(job_type, target_spec, site_id=site_id, metadata=metadata)
    with get_session() as session:
        parent = session.get(Job, parent_id)
        parent.status = "running"
        session.commit()

    for hostname in hostnames:
        enqueue_job(
            job_type, hostname, parent_id=parent_id, site_id=site_id, metadata=metadata
        )
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


def _ssl_schedule_due(cfg: dict) -> bool:
    """Return True if the SSL cert schedule for the given site config should fire now."""
    if not cfg.get("enabled") or not cfg.get("schedule_enabled"):
        return False
    schedule_time = cfg.get("schedule_time") or ""
    frequency = cfg.get("schedule_frequency") or ""
    last_run_str = cfg.get("schedule_last_run")

    try:
        hour, minute = map(int, schedule_time.split(":"))
    except (ValueError, AttributeError):
        return False

    now = datetime.now()
    if now < now.replace(hour=hour, minute=minute, second=0, microsecond=0):
        return False  # haven't hit the scheduled time yet today

    last_run = None
    if last_run_str:
        try:
            last_run = datetime.fromisoformat(last_run_str)
        except (ValueError, TypeError):
            pass

    min_days = {"daily": 1, "weekly": 7, "biweekly": 14, "monthly": 30, "quarterly": 90}
    days_needed = min_days.get(frequency)
    if days_needed is None:
        return False
    if frequency == "daily":
        if last_run and last_run.date() == now.date():
            return False
    else:
        if last_run and (now - last_run).days < days_needed:
            return False
    return True


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
                        metadata = (
                            {"keep_max": task["keep_max"]}
                            if task.get("keep_max")
                            else None
                        )
                        count = enqueue_batch(
                            task["type"], task["target"], metadata=metadata
                        )
                        self._last_runs[task["name"]] = datetime.now()
                        logger.info(
                            "Scheduled %s: enqueued %d jobs for %s",
                            task["name"],
                            count,
                            task["target"],
                        )
            except Exception as exc:
                logger.error("Scheduler error: %s", exc)

            try:
                from dracs.db import (
                    get_all_ssl_scheduled_sites,
                    update_ssl_schedule_last_run,
                )

                for cfg in get_all_ssl_scheduled_sites():
                    if _ssl_schedule_due(cfg):
                        count = enqueue_batch(
                            "ssl_cert_upload",
                            "all",
                            site_id=cfg["site_id"],
                            metadata={"site_name": cfg["site_name"]},
                        )
                        update_ssl_schedule_last_run(cfg["site_id"])
                        logger.info(
                            "SSL cert schedule fired for site %s: %d jobs enqueued",
                            cfg["site_name"],
                            count,
                        )
            except Exception as exc:
                logger.error("SSL schedule check error: %s", exc)

            time.sleep(60)

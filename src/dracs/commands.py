import asyncio
import logging
import os
import sys
import shutil
import socket
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Tuple, Optional

from dracs.display import (
    filter_list_results as _sync_filter_list_results,
    render_list_table,
    render_list_json,
    render_list_host_only,
    render_tsr_table,
)
from dracs.exceptions import (
    DatabaseError,
    DracsError,
    SNMPError,
    ValidationError,
)
from dracs.db import (
    System,
    get_session,
    db_initialize,
    query_by_service_tag,
    query_by_hostname,
    query_by_model,
    query_all_systems,
    upsert_system,
)
from dracs.snmp import get_snmp_value, build_idrac_hostname
from dracs.api import dell_api_warranty_date

logger = logging.getLogger(__name__)

debug_output = False


async def add_dell_warranty(
    service_tag: str,
    hostname: str,
    model: str,
    warranty: str,
    warranty_results: Optional[Dict] = None,
    site_id: int | None = None,
) -> None:
    idrac_host = build_idrac_hostname(hostname)
    community_string = os.getenv("SNMP_COMMUNITY", "public")
    BIOS_OID = "1.3.6.1.4.1.674.10892.5.4.300.50.1.8.1.1"
    IDRAC_FW_OID = "1.3.6.1.4.1.674.10892.5.1.1.8.0"

    bios_version = await get_snmp_value(idrac_host, community_string, BIOS_OID)
    idrac_version = await get_snmp_value(idrac_host, community_string, IDRAC_FW_OID)

    logger.info(f"Retrieved SNMP values - BIOS: {bios_version}, iDRAC: {idrac_version}")

    db_initialize(warranty)

    with get_session() as session:
        results = (
            session.query(System)
            .filter(System.svc_tag == service_tag, System.name == hostname)
            .all()
        )

    if debug_output:
        logger.debug(f"service_tag = {service_tag}")
        logger.debug(f"hostname = {hostname}")
        logger.debug(f"warranty = {warranty}")
        logger.debug(f"results = {results}")

    if len(results) > 1:
        raise DatabaseError("Multiple matching records found in database")

    if len(results) == 1:
        logger.info(f"Updating existing record for {service_tag}")
        upsert_system(
            warranty,
            service_tag,
            hostname,
            model,
            idrac_version,
            bios_version,
            results[0].exp_date,
            results[0].exp_epoch,
            site_id=site_id,
        )
        logger.info(f"Successfully updated record for {service_tag}")
    else:
        logger.info(
            f"Adding new record for {service_tag}, fetching warranty from Dell API"
        )
        if warranty_results is None:
            warranty_results = dell_api_warranty_date(service_tag)
        h_epoch, h_date = warranty_results[service_tag]

        if debug_output:
            logger.debug(
                f"Warranty result: svc_tag={service_tag}, exp_date={h_date}, "
                f"exp_epoch={h_epoch}"
            )

        upsert_system(
            warranty,
            service_tag,
            hostname,
            model,
            idrac_version,
            bios_version,
            h_date,
            h_epoch,
            site_id=site_id,
        )
        logger.info(f"Successfully added record for {service_tag}")


async def edit_dell_warranty(
    service_tag: Optional[str],
    hostname: Optional[str],
    model: Optional[str],
    idrac: bool,
    bios: bool,
    warranty: str,
) -> None:
    if service_tag:
        if debug_output:
            print(f"service_tag = {service_tag}")
    if hostname:
        if debug_output:
            print(f"hostname = {hostname}")
    if model:
        if debug_output:
            logger.debug(f"model = {model}")
    else:
        if not idrac and not bios:
            raise ValidationError(
                "Model parameter required for edit mode when not updating idrac or bios"
            )

    db_initialize(warranty)

    with get_session() as session:
        if service_tag:
            results = session.query(System).filter(System.svc_tag == service_tag).all()
        elif hostname:
            results = session.query(System).filter(System.name == hostname).all()
        else:
            results = []

    if debug_output:
        logger.debug(f"service_tag = {service_tag}")
        logger.debug(f"hostname = {hostname}")
        logger.debug(f"warranty = {warranty}")
        logger.debug(f"results = {results}")

    if len(results) > 1:
        raise DatabaseError("Multiple matching records found in database")

    if len(results) == 1:
        record = results[0]
        hostname = record.name
        idrac_host = build_idrac_hostname(hostname)
        community_string = os.getenv("SNMP_COMMUNITY", "public")
        BIOS_OID = "1.3.6.1.4.1.674.10892.5.4.300.50.1.8.1.1"
        IDRAC_FW_OID = "1.3.6.1.4.1.674.10892.5.1.1.8.0"

        if idrac:
            idrac_version = await get_snmp_value(
                idrac_host, community_string, IDRAC_FW_OID
            )
        else:
            idrac_version = record.idrac_version
        if bios:
            bios_version = await get_snmp_value(idrac_host, community_string, BIOS_OID)
        else:
            bios_version = record.bios_version
        if not model:
            model = record.model

        upsert_system(
            warranty,
            record.svc_tag,
            record.name,
            model,
            idrac_version,
            bios_version,
            record.exp_date,
            record.exp_epoch,
        )
        if debug_output:
            logger.info("Database updated successfully")
    else:
        raise DatabaseError("Record not found in database")


async def lookup_dell_warranty(
    service_tag: Optional[str],
    hostname: Optional[str],
    idrac: bool,
    bios: bool,
    full: bool,
    warranty: str,
) -> None:
    db_initialize(warranty)

    with get_session() as session:
        if service_tag:
            results = session.query(System).filter(System.svc_tag == service_tag).all()
        elif hostname:
            results = session.query(System).filter(System.name == hostname).all()
        else:
            results = []

    if len(results) == 0:
        raise DatabaseError("No matching records found in database")
    if len(results) > 1:
        raise DatabaseError("Multiple matching records found in database")

    record = results[0]
    result = {"hostname": record.name}
    if idrac or full:
        result["idrac_version"] = record.idrac_version
    if bios or full:
        result["bios_version"] = record.bios_version
    result["svc_tag"] = record.svc_tag
    if not idrac and not bios:
        result["model"] = record.model
        result["exp_date"] = record.exp_date
        result["exp_epoch"] = record.exp_epoch
    print(result)


async def filter_list_results(
    results: List[Tuple],
    bios_le: Optional[str],
    bios_lt: Optional[str],
    bios_ge: Optional[str],
    bios_gt: Optional[str],
    bios_eq: Optional[str],
    idrac_le: Optional[str],
    idrac_lt: Optional[str],
    idrac_ge: Optional[str],
    idrac_gt: Optional[str],
    idrac_eq: Optional[str],
) -> List[Tuple]:
    return _sync_filter_list_results(
        results,
        bios_le,
        bios_lt,
        bios_ge,
        bios_gt,
        bios_eq,
        idrac_le,
        idrac_lt,
        idrac_ge,
        idrac_gt,
        idrac_eq,
    )


async def list_dell_warranty(
    service_tag: Optional[str],
    hostname: Optional[str],
    model: Optional[str],
    regex: Optional[str],
    bios_le: Optional[str],
    bios_lt: Optional[str],
    bios_ge: Optional[str],
    bios_gt: Optional[str],
    bios_eq: Optional[str],
    idrac_le: Optional[str],
    idrac_lt: Optional[str],
    idrac_ge: Optional[str],
    idrac_gt: Optional[str],
    idrac_eq: Optional[str],
    expires_in: Optional[str],
    expired: bool,
    printjson: bool,
    host_only: bool,
    warranty: str,
    site_id: Optional[int] = None,
) -> None:
    db_initialize(warranty)

    if service_tag and hostname:
        raise ValidationError(
            "Cannot specify both --svctag and --target; they are mutually exclusive"
        )
    if (hostname or service_tag) and (model or regex):
        raise ValidationError(
            "Cannot specify --model or --regex when using --svctag or --target"
        )

    with get_session() as session:
        query = session.query(System)
        if site_id is not None:
            query = query.filter(System.site_id == site_id)

        if service_tag:
            query = query.filter(System.svc_tag == service_tag)
        elif hostname:
            query = query.filter(System.name == hostname)
        else:
            if model and regex:
                query = query.filter(System.name.like(regex), System.model == model)
            elif model:
                query = query.filter(System.model == model)
            elif regex:
                query = query.filter(System.name.like(regex))

        if expires_in:
            current_time = int(time.time())
            future_timestamp = current_time + (int(expires_in) * 86400)
            query = query.filter(
                System.exp_epoch > current_time,
                System.exp_epoch <= future_timestamp,
            )

        if expired:
            current_time = int(time.time())
            query = query.filter(System.exp_epoch < current_time)

        query = query.order_by(System.name)
        records = query.all()
        results = [r.to_tuple() for r in records]

    if (
        bios_le
        or bios_lt
        or bios_ge
        or bios_gt
        or bios_eq
        or idrac_le
        or idrac_lt
        or idrac_ge
        or idrac_gt
        or idrac_eq
    ):
        results = await filter_list_results(
            results,
            bios_le,
            bios_lt,
            bios_ge,
            bios_gt,
            bios_eq,
            idrac_le,
            idrac_lt,
            idrac_ge,
            idrac_gt,
            idrac_eq,
        )
    if host_only:
        render_list_host_only(results)
    elif printjson:
        render_list_json(results)
    else:
        render_list_table(results)


async def refresh_dell_warranty(
    service_tag: Optional[str],
    hostname: Optional[str],
    warranty: str,
    verbose: bool = False,
) -> None:
    db_initialize(warranty)

    if service_tag:
        results = query_by_service_tag(service_tag)
    elif hostname:
        results = query_by_hostname(hostname)
    else:
        raise ValidationError("Either service tag or hostname must be provided")

    if len(results) == 0:
        raise DatabaseError("No matching record found to refresh")
    if len(results) > 1:
        raise DatabaseError("Multiple matching records found in database")

    existing = results[0]
    svc_tag = existing[0]
    name = existing[1]
    old_model = existing[2]
    old_idrac_version = existing[3]
    old_bios_version = existing[4]
    old_exp_date = existing[5]

    if verbose:
        print(f"Refreshing {name} ... ", end="", flush=True)

    logger.debug(f"Refreshing data for {svc_tag} ({name})")

    idrac_host = build_idrac_hostname(name)
    community_string = os.getenv("SNMP_COMMUNITY", "public")
    BIOS_OID = "1.3.6.1.4.1.674.10892.5.4.300.50.1.8.1.1"
    IDRAC_FW_OID = "1.3.6.1.4.1.674.10892.5.1.1.8.0"
    MODEL_OID = ".1.3.6.1.4.1.674.10892.5.1.3.12.0"

    bios_version = await get_snmp_value(idrac_host, community_string, BIOS_OID)
    idrac_version = await get_snmp_value(idrac_host, community_string, IDRAC_FW_OID)
    model = await get_snmp_value(idrac_host, community_string, MODEL_OID)

    if model and model.startswith("PowerEdge "):
        model = model.replace("PowerEdge ", "")

    logger.debug(
        f"Updated SNMP values - Model: {model}, BIOS: {bios_version}, iDRAC: {idrac_version}"
    )

    warranty_results = dell_api_warranty_date(svc_tag)
    exp_epoch, exp_date = warranty_results[svc_tag]

    logger.debug(f"Updated warranty expiration: {exp_date}")

    upsert_system(
        warranty, svc_tag, name, model, idrac_version, bios_version, exp_date, exp_epoch
    )

    logger.debug(f"Successfully refreshed record for {svc_tag}")

    if verbose:
        print("done.")

    # Check for changes and report them
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    if old_model != model:
        print(
            f"{timestamp} - dracs.cli - INFO - {name} updated: Model changed from {old_model} to {model}"
        )

    if old_idrac_version != idrac_version:
        print(
            f"{timestamp} - dracs.cli - INFO - {name} updated: Firmware changed from {old_idrac_version} to {idrac_version}"
        )

    if old_bios_version != bios_version:
        print(
            f"{timestamp} - dracs.cli - INFO - {name} updated: BIOS changed from {old_bios_version} to {bios_version}"
        )

    if old_exp_date != exp_date:
        print(
            f"{timestamp} - dracs.cli - INFO - {name} updated: Warranty Expiration changed from {old_exp_date} to {exp_date}"
        )


async def refresh_by_model(
    model: str, warranty: str, verbose: bool = False, site_id: int | None = None
) -> None:
    from dracs.jobqueue import enqueue_batch

    db_initialize(warranty)
    results = query_by_model(model)

    if len(results) == 0:
        raise DatabaseError(f"No systems found with model {model}")

    count = enqueue_batch("refresh", f"model:{model}", site_id=site_id)
    print(f"Queued {count} refresh jobs for model {model}.")


async def refresh_all_systems(
    warranty: str, verbose: bool = False, site_id: int | None = None
) -> None:
    from dracs.jobqueue import enqueue_batch

    db_initialize(warranty)
    results = query_all_systems()

    if len(results) == 0:
        raise DatabaseError("No systems found in database")

    count = enqueue_batch("refresh", "all", site_id=site_id)
    print(f"Queued {count} refresh jobs for all systems.")


async def discover_dell_system(hostname: str, warranty: str) -> Tuple[str, str]:
    logger.info(f"Discovering system information for {hostname}")

    idrac_host = build_idrac_hostname(hostname)
    community_string = os.getenv("SNMP_COMMUNITY", "public")

    SERVICE_TAG_OID = ".1.3.6.1.4.1.674.10892.5.1.3.2.0"
    MODEL_OID = ".1.3.6.1.4.1.674.10892.5.1.3.12.0"

    logger.info(f"Querying {idrac_host} for service tag and model")

    service_tag = await get_snmp_value(idrac_host, community_string, SERVICE_TAG_OID)
    if not service_tag:
        raise SNMPError(f"Failed to retrieve service tag from {idrac_host}")

    model = await get_snmp_value(idrac_host, community_string, MODEL_OID)
    if not model:
        raise SNMPError(f"Failed to retrieve model from {idrac_host}")

    if model.startswith("PowerEdge "):
        model = model.replace("PowerEdge ", "")

    logger.info(f"Discovered: Service Tag={service_tag}, Model={model}")

    return (service_tag, model)


async def _discover_single_host(
    hostname: str, warranty: str, auto_add: bool, site_id: int | None = None
) -> dict:
    result = {"hostname": hostname, "status": "ok", "error": None}
    if site_id is not None:
        from dracs.db import get_site_allowed_domains
        from dracs.sites import is_domain_allowed

        allowed = get_site_allowed_domains(site_id)
        if not is_domain_allowed(hostname, allowed):
            result["status"] = "error"
            result["error"] = f"Cannot add host '{hostname}'. Domain not allowed."
            logger.warning(result["error"])
            return result
    try:
        service_tag, model = await discover_dell_system(hostname, warranty)
        result["service_tag"] = service_tag
        result["model"] = model

        if auto_add:
            await add_dell_warranty(
                service_tag, hostname, model, warranty, site_id=site_id
            )
            result["added"] = True
        else:
            result["added"] = False
    except (SNMPError, DracsError) as e:
        result["status"] = "error"
        result["error"] = str(e)
        logger.error(f"Failed to discover {hostname}: {e}")

    return result


async def discover_dell_systems_batch(
    hosts: List[str],
    warranty: str,
    auto_add: bool,
    show_discovered: bool = False,
    site_id: int | None = None,
) -> None:
    tasks = [_discover_single_host(h, warranty, False, site_id=site_id) for h in hosts]
    results = await asyncio.gather(*tasks)

    if auto_add:
        discovered = [r for r in results if r["status"] == "ok"]
        if discovered:
            tags = [r["service_tag"] for r in discovered]
            warranty_results = dell_api_warranty_date(tags)

            add_tasks = [
                add_dell_warranty(
                    r["service_tag"],
                    r["hostname"],
                    r["model"],
                    warranty,
                    warranty_results=warranty_results,
                    site_id=site_id,
                )
                for r in discovered
            ]
            add_outcomes = await asyncio.gather(*add_tasks, return_exceptions=True)

            for r, outcome in zip(discovered, add_outcomes):
                if isinstance(outcome, Exception):
                    r["status"] = "error"
                    r["error"] = str(outcome)
                else:
                    r["added"] = True

    succeeded = [r for r in results if r["status"] == "ok"]
    failed = [r for r in results if r["status"] == "error"]

    if succeeded and show_discovered:
        from rich.console import Console
        from rich.table import Table

        console = Console()
        table = Table(show_header=True, header_style="bold cyan", show_lines=True)
        table.add_column("Hostname")
        table.add_column("Service Tag")
        table.add_column("Model")
        table.add_column("Status")
        for r in succeeded:
            table.add_row(
                r["hostname"],
                r["service_tag"],
                r["model"],
                "Added" if r.get("added") else "Discovered",
            )
        console.print(table)

    if succeeded:
        print(f"\nSucceeded: {len(succeeded)}")

    if failed:
        print(f"Failed: {len(failed)}")
        from rich.console import Console
        from rich.table import Table

        console = Console()
        table = Table(show_header=True, header_style="bold red", show_lines=True)
        table.add_column("Hostname")
        table.add_column("Error")
        for r in failed:
            table.add_row(r["hostname"], r["error"])
        console.print(table)

    total = len(results)
    print(f"Total: {total} hosts")


async def remove_dell_warranty(
    service_tag: Optional[str], hostname: Optional[str], warranty: str
) -> None:
    if service_tag:
        if debug_output:
            print(f"service_tag = {service_tag}")
    if hostname:
        if debug_output:
            print(f"hostname = {hostname}")

    db_initialize(warranty)

    with get_session() as session:
        if service_tag:
            results = session.query(System).filter(System.svc_tag == service_tag).all()
        elif hostname:
            results = session.query(System).filter(System.name == hostname).all()
        else:
            results = []

        if len(results) == 0:
            raise DatabaseError("No matching records found in database")
        if len(results) > 1:
            raise DatabaseError("Multiple matching records found in database")

        record = results[0]
        session.delete(record)
        session.commit()
        print("Record deleted")


TSR_DIR = "/var/lib/dracs/web/tsr"


def _scan_tsr_entries(hostname: str) -> List[dict]:
    host_dir = Path(TSR_DIR) / hostname
    if not host_dir.is_dir():
        return []

    entries = []
    for zip_file in host_dir.glob("TSR*.zip"):
        fname = zip_file.name
        ts_part = fname.replace("TSR", "").split("_")[0]
        try:
            dt = datetime.strptime(ts_part, "%Y%m%d%H%M%S")
            entries.append(
                {
                    "date": dt.strftime("%Y/%m/%d %H:%M:%S"),
                    "view_path": ts_part + "/",
                    "zip_file": fname,
                }
            )
        except ValueError:
            continue

    entries.sort(key=lambda e: e["date"], reverse=True)
    return entries


async def tsr_list(
    hostname: str,
    warranty: str,
    last: Optional[int] = None,
) -> None:
    db_initialize(warranty)

    results = query_by_hostname(hostname)
    if not results:
        raise DatabaseError(f"Host {hostname} not found in database")

    entries = _scan_tsr_entries(hostname)
    if not entries:
        print(f"No TSR collections found for {hostname}.")
        return

    if last is not None:
        entries = entries[:last]

    fqdn = socket.getfqdn()
    base_url = f"https://{fqdn}"
    render_tsr_table(entries, base_url, hostname)


async def tsr_download(hostname: str, warranty: str) -> None:
    db_initialize(warranty)

    results = query_by_hostname(hostname)
    if not results:
        raise DatabaseError(f"Host {hostname} not found in database")

    entries = _scan_tsr_entries(hostname)
    if not entries:
        print(f"No TSR collections found for {hostname}.")
        return

    newest = entries[0]
    src = Path(TSR_DIR) / hostname / newest["zip_file"]
    dst = Path.cwd() / newest["zip_file"]
    shutil.copy2(src, dst)
    print(f"Downloaded: {newest['zip_file']}")


async def tsr_generate(hostname: str, warranty: str) -> None:
    from dracs.jobqueue import enqueue_job, get_latest_job_for_host

    db_initialize(warranty)

    results = query_by_hostname(hostname)
    if not results:
        raise DatabaseError(f"Host {hostname} not found in database")

    existing = get_latest_job_for_host(hostname, "tsr")
    if existing and existing["status"] in ("pending", "running"):
        progress = existing.get("result", "")
        msg = f"TSR already in progress for {hostname} (job {existing['id']})"
        if progress and "%" in progress:
            msg += f" - {progress} Completed."
        elif progress:
            msg += f" - {progress}."
        else:
            msg += "."
        print(msg)
        return

    job_id = enqueue_job("tsr", hostname)
    print(f"TSR collection queued for {hostname} (job {job_id})")


async def tsr_status(hostname: str, warranty: str) -> None:
    from dracs.jobqueue import get_latest_job_for_host

    db_initialize(warranty)

    results = query_by_hostname(hostname)
    if not results:
        raise DatabaseError(f"Host {hostname} not found in database")

    job = get_latest_job_for_host(hostname, "tsr")
    if job and job["status"] == "pending":
        print("TSR Collection pending.")
    elif job and job["status"] == "running":
        progress = job.get("result", "")
        if progress and "%" in progress:
            print(f"TSR Collection in progress: {progress} Completed.")
        elif progress:
            print(f"TSR Collection in progress: {progress}.")
        else:
            print("TSR Collection in progress.")
    else:
        print("No TSR Collection in progress.")


async def list_jobs(include_all: bool, failed_only: bool, warranty: str) -> None:
    from dracs.jobqueue import get_active_jobs
    from rich.console import Console
    from rich.table import Table

    db_initialize(warranty)

    status_filter = "failed" if failed_only else None
    jobs = get_active_jobs(
        include_completed=include_all or failed_only,
        status_filter=status_filter,
        limit=200,
    )
    if not jobs:
        print("No jobs found.")
        return

    console = Console()
    table = Table(show_header=True, header_style="bold cyan")
    table.add_column("ID")
    table.add_column("Type")
    table.add_column("Target")
    table.add_column("Status")
    table.add_column("Created")
    table.add_column("Error")

    for job in jobs:
        target_display = job["target"]
        if "progress" in job:
            target_display += f" ({job['progress']})"
        table.add_row(
            str(job["id"]),
            job["job_type"],
            target_display,
            job["status"],
            job["created_at"],
            job.get("error") or "",
        )

    console.print(table)


async def clear_jobs(warranty: str) -> None:
    from dracs.jobqueue import purge_completed_jobs

    db_initialize(warranty)
    purge_days = int(os.environ.get("JOB_PURGE_DAYS", "7"))
    count = purge_completed_jobs(older_than_days=purge_days)
    print(f"Purged {count} completed jobs.")


async def cancel_job_cmd(job_id: int, warranty: str) -> None:
    from dracs.jobqueue import cancel_job

    db_initialize(warranty)
    if cancel_job(job_id):
        print(f"Job {job_id} cancelled.")
    else:
        print(f"Job {job_id} cannot be cancelled (not found or not pending).")


async def idrac_jobs_list(hostname: str, warranty: str) -> None:
    import subprocess as sp  # nosec

    from rich.console import Console
    from rich.table import Table

    from dracs.webapp import _build_ssh_racadm_cmd, parse_job_queue

    db_initialize(warranty)

    results = query_by_hostname(hostname)
    if not results:
        raise DatabaseError(f"Host {hostname} not found in database")

    cmd = _build_ssh_racadm_cmd(hostname, "jobqueue", "view")
    result = sp.run(  # nosec # nosemgrep
        cmd, capture_output=True, text=True, timeout=30  # nosemgrep
    )
    if result.returncode != 0:
        detail = result.stdout[:200] if result.stdout.strip() else result.stderr[:200]
        raise DracsError(
            f"Failed to query job queue (exit {result.returncode}): {detail}"
        )

    jobs = parse_job_queue(result.stdout)
    if not jobs:
        print(f"No jobs in iDRAC job queue for {hostname}.")
        return

    console = Console()
    table = Table(show_header=True, header_style="bold cyan", show_lines=True)
    table.add_column("Job ID", no_wrap=True)
    table.add_column("Job Name", overflow="fold")
    table.add_column("Status", no_wrap=True)
    table.add_column("Percent", no_wrap=True)
    table.add_column("Start Time", overflow="fold")
    table.add_column("Completion Time", overflow="fold")
    table.add_column("Message", overflow="fold")

    for job in jobs:
        table.add_row(
            job.get("job_id", ""),
            job.get("job_name", ""),
            job.get("status", ""),
            job.get("percent_complete", ""),
            job.get("actual_start_time", ""),
            job.get("actual_completion_time", ""),
            job.get("message", ""),
        )

    console.print(table)


async def idrac_jobs_clear(
    hostname: str | None,
    model: str | None,
    all_hosts: bool,
    force: bool,
    warranty: str,
) -> None:
    from dracs.jobqueue import enqueue_batch, enqueue_job

    db_initialize(warranty)

    if not hostname and not model and not all_hosts:
        raise ValidationError(
            "One of --target, --model, or --all is required with --clear."
        )

    if hostname:
        results = query_by_hostname(hostname)
        if not results:
            raise DatabaseError(f"Host {hostname} not found in database")

        if not force:
            response = (
                input(
                    f"You are about to clear all non-applied jobs on {hostname}. "
                    "Are you sure? (y/n): "
                )
                .strip()
                .lower()
            )
            if response not in ("y", "yes"):
                print("Cancelled.")
                return

        enqueue_job("clear_job_queue", hostname)
        print(f"Clear job queue queued for {hostname}.")

    elif model:
        results = query_by_model(model)
        if not results:
            raise DatabaseError(f"No systems found with model {model}")

        if not force:
            response = (
                input(
                    f"You are about to clear all non-applied jobs on all {model} hosts. "
                    "Are you sure? (y/n): "
                )
                .strip()
                .lower()
            )
            if response not in ("y", "yes"):
                print("Cancelled.")
                return

        count = enqueue_batch("clear_job_queue", f"model:{model}")
        print(f"Clear job queue queued for {count} {model} hosts.")

    elif all_hosts:
        results = query_all_systems()
        if not results:
            raise DatabaseError("No systems found in database")

        if not force:
            response = (
                input(
                    "You are about to clear all non-applied jobs on ALL hosts. "
                    "Are you sure? (y/n): "
                )
                .strip()
                .lower()
            )
            if response not in ("y", "yes"):
                print("Cancelled.")
                return

        count = enqueue_batch("clear_job_queue", "all")
        print(f"Clear job queue queued for {count} hosts.")


def _get_available_firmware_versions(model: str) -> list:
    from dracs.webapp import FIRMWARE_IMAGE_DIR

    versions = []
    prefix = f"{model}-"
    suffix = ".d9"
    if FIRMWARE_IMAGE_DIR.is_dir():
        for f in FIRMWARE_IMAGE_DIR.iterdir():
            name = f.name
            if name.startswith(prefix) and name.endswith(suffix):
                ver = name[len(prefix) : -len(suffix)]
                if ver:
                    versions.append(ver)
    return versions


def _get_available_bios_versions(model: str) -> list:
    import configparser

    config_file = Path("BIOS-filename.ini")
    if not config_file.exists():
        config_file = Path("/etc/dracs/BIOS-filename.ini")
    if not config_file.exists():
        return []
    config = configparser.ConfigParser()
    config.read(config_file)
    if model not in config:
        return []
    return list(config[model].keys())


def _version_sort_key(v: str):
    return tuple(map(int, v.split(".")))


async def fw_list(
    model_filter: str | None, warranty: str, site_id: int | None = None
) -> None:
    from collections import Counter

    from rich.console import Console
    from rich.table import Table

    db_initialize(warranty)

    with get_session() as session:
        query = session.query(System)
        if site_id is not None:
            query = query.filter(System.site_id == site_id)
        if model_filter:
            query = query.filter(System.model == model_filter)
        systems = query.all()

    if not systems:
        print("No systems found.")
        return

    models = sorted(set(s.model for s in systems if s.model))

    console = Console()
    table = Table(show_header=True, header_style="bold cyan", show_lines=True)
    table.add_column("Model")
    table.add_column("Installed Versions")
    table.add_column("Other Versions")

    for m in models:
        model_systems = [s for s in systems if s.model == m]
        counts = Counter(s.idrac_version for s in model_systems if s.idrac_version)
        installed = sorted(counts.keys(), key=_version_sort_key, reverse=True)
        installed_lines = "\n".join(f"{v} ({counts[v]})" for v in installed)

        available = _get_available_firmware_versions(m)
        other = sorted(
            [v for v in available if v not in counts],
            key=_version_sort_key,
            reverse=True,
        )
        other_lines = "\n".join(other) if other else ""

        table.add_row(m, installed_lines, other_lines)

    console.print(table)


async def fw_apply(
    version: str,
    hostname: str,
    force: bool,
    yes_flag: bool,
    warranty: str,
) -> None:
    from dracs.jobqueue import enqueue_job

    db_initialize(warranty)

    results = query_by_hostname(hostname)
    if not results:
        raise DatabaseError(f"Host {hostname} not found in database")

    host = results[0]
    model = host[2]

    available = _get_available_firmware_versions(model)
    if version not in available:
        print(f"Firmware version {version} is not available for the {model} hosts.")
        return

    with get_session() as session:
        running = (
            session.query(System)
            .filter(System.model == model, System.idrac_version == version)
            .count()
        )

    if running == 0 and not force:
        print(
            f"Firmware version {version} is not running on any {model} host."
            "  Use --force to install this version."
        )
        return

    if not yes_flag:
        response = (
            input(f"Install firmware {version} on {hostname} ? [y/n] ").strip().lower()
        )
        if response not in ("y", "yes"):
            print("Cancelled.")
            return

    job_id = enqueue_job(
        "firmware_update",
        hostname,
        metadata={"target_version": version, "model": model},
    )
    print(f"Firmware update {version} queued for {hostname} (job {job_id})")


async def bios_list(
    model_filter: str | None, warranty: str, site_id: int | None = None
) -> None:
    from collections import Counter

    from rich.console import Console
    from rich.table import Table

    db_initialize(warranty)

    with get_session() as session:
        query = session.query(System)
        if site_id is not None:
            query = query.filter(System.site_id == site_id)
        if model_filter:
            query = query.filter(System.model == model_filter)
        systems = query.all()

    if not systems:
        print("No systems found.")
        return

    models = sorted(set(s.model for s in systems if s.model))

    console = Console()
    table = Table(show_header=True, header_style="bold cyan", show_lines=True)
    table.add_column("Model")
    table.add_column("Installed Versions")
    table.add_column("Other Versions")

    for m in models:
        model_systems = [s for s in systems if s.model == m]
        counts = Counter(s.bios_version for s in model_systems if s.bios_version)
        installed = sorted(counts.keys(), key=_version_sort_key, reverse=True)
        installed_lines = "\n".join(f"{v} ({counts[v]})" for v in installed)

        available = _get_available_bios_versions(m)
        other = sorted(
            [v for v in available if v not in counts],
            key=_version_sort_key,
            reverse=True,
        )
        other_lines = "\n".join(other) if other else ""

        table.add_row(m, installed_lines, other_lines)

    console.print(table)


async def bios_apply(
    version: str,
    hostname: str,
    force: bool,
    yes_flag: bool,
    warranty: str,
) -> None:
    from dracs.jobqueue import enqueue_job

    db_initialize(warranty)

    results = query_by_hostname(hostname)
    if not results:
        raise DatabaseError(f"Host {hostname} not found in database")

    host = results[0]
    model = host[2]

    available = _get_available_bios_versions(model)
    if version not in available:
        print(f"BIOS version {version} is not available for the {model} hosts.")
        return

    with get_session() as session:
        running = (
            session.query(System)
            .filter(System.model == model, System.bios_version == version)
            .count()
        )

    if running == 0 and not force:
        print(
            f"BIOS version {version} is not running on any {model} host."
            "  Use --force to install this version."
        )
        return

    if not yes_flag:
        response = (
            input(f"Install BIOS {version} on {hostname} ? [y/n] ").strip().lower()
        )
        if response not in ("y", "yes"):
            print("Cancelled.")
            return

    job_id = enqueue_job(
        "bios_update", hostname, metadata={"target_bios": version, "model": model}
    )
    print(f"BIOS update {version} queued for {hostname} (job {job_id})")


def cmd_vnc(args, site_name=None):
    """Handle the dracs vnc subcommand."""
    from dracs.vnc import get_hostname_viewer_count, get_vnc_credentials

    hostname = args.target

    if args.connections:
        count = get_hostname_viewer_count(hostname)
        label = "viewer" if count == 1 else "viewers"
        print(f"{hostname}: {count} active {label}")
        return

    # --reset path
    count = get_hostname_viewer_count(hostname)
    if count > 0 and not args.force:
        print(
            f"Error: VNC connection count is currently {count} for {hostname}. "
            "Use --force option to reset anyway.",
            file=sys.stderr,
        )
        sys.exit(1)

    from dracs.exceptions import ValidationError
    from dracs.jobqueue import run_racadm_ssh
    from dracs.snmp import build_idrac_hostname
    from dracs.webapp import get_idrac_credentials

    try:
        idrac_fqdn = build_idrac_hostname(hostname)
    except ValidationError as exc:
        print(f"Error: Cannot build iDRAC FQDN for {hostname}: {exc}", file=sys.stderr)
        sys.exit(1)

    username, password = get_idrac_credentials(hostname, site=site_name)
    vnc_port, vnc_password = get_vnc_credentials(hostname, site=site_name)

    steps = [
        (["set", "idrac.vncserver.enable", "Disabled"], "Disabling VNC"),
        (["set", "idrac.vncserver.Password", vnc_password], "Setting VNC password"),
        (["set", "idrac.vncserver.port", str(vnc_port)], "Setting VNC port"),
        (["set", "idrac.vncserver.enable", "Enabled"], "Enabling VNC"),
    ]

    for racadm_args, description in steps:
        print(f"  {description}...", end="", flush=True)
        result = run_racadm_ssh(idrac_fqdn, username, password, racadm_args)
        if result.returncode != 0:
            print(" FAILED")
            print(f"Error: {result.stderr.strip()}", file=sys.stderr)
            sys.exit(1)
        print(" OK")

    print(f"VNC configuration reset successfully for {hostname}.")

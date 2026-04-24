import asyncio
import json
import logging
import os
import time
from typing import Dict, List, Tuple, Optional

from tabulate import tabulate

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
            results = (
                session.query(System)
                .filter(System.svc_tag == service_tag)
                .all()
            )
        elif hostname:
            results = (
                session.query(System).filter(System.name == hostname).all()
            )
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
            results = (
                session.query(System)
                .filter(System.svc_tag == service_tag)
                .all()
            )
        elif hostname:
            results = (
                session.query(System).filter(System.name == hostname).all()
            )
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
    output = []
    for s in results:
        s_idrac = s[3]
        s_idrac_tuple = tuple(map(int, s_idrac.split(".")))
        s_bios = s[4]
        s_bios_tuple = tuple(map(int, s_bios.split(".")))
        if idrac_le:
            idrac_le_tuple = tuple(map(int, idrac_le.split(".")))
            if s_idrac_tuple <= idrac_le_tuple:
                output.append(s)
        if idrac_lt:
            idrac_lt_tuple = tuple(map(int, idrac_lt.split(".")))
            if s_idrac_tuple < idrac_lt_tuple:
                output.append(s)
        if idrac_ge:
            idrac_ge_tuple = tuple(map(int, idrac_ge.split(".")))
            if s_idrac_tuple >= idrac_ge_tuple:
                output.append(s)
        if idrac_gt:
            idrac_gt_tuple = tuple(map(int, idrac_gt.split(".")))
            if s_idrac_tuple > idrac_gt_tuple:
                output.append(s)
        if idrac_eq:
            idrac_eq_tuple = tuple(map(int, idrac_eq.split(".")))
            if s_idrac_tuple == idrac_eq_tuple:
                output.append(s)
        if bios_le:
            bios_le_tuple = tuple(map(int, bios_le.split(".")))
            if s_bios_tuple <= bios_le_tuple:
                output.append(s)
        if bios_lt:
            bios_lt_tuple = tuple(map(int, bios_lt.split(".")))
            if s_bios_tuple < bios_lt_tuple:
                output.append(s)
        if bios_ge:
            bios_ge_tuple = tuple(map(int, bios_ge.split(".")))
            if s_bios_tuple >= bios_ge_tuple:
                output.append(s)
        if bios_gt:
            bios_gt_tuple = tuple(map(int, bios_gt.split(".")))
            if s_bios_tuple > bios_gt_tuple:
                output.append(s)
        if bios_eq:
            bios_eq_tuple = tuple(map(int, bios_eq.split(".")))
            if s_bios_tuple == bios_eq_tuple:
                output.append(s)

    return output


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

        if service_tag:
            query = query.filter(System.svc_tag == service_tag)
        elif hostname:
            query = query.filter(System.name == hostname)
        else:
            if model and regex:
                query = query.filter(
                    System.name.like(regex), System.model == model
                )
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
        for row in results:
            print(row[1])
    elif printjson:
        print(json.dumps(results, indent=4))
    else:
        headers = [
            "Service Tag",
            "Hostname",
            "Model",
            "Firmware",
            "BIOS",
            "Expires",
            "Timestamp",
        ]
        print(tabulate(results, headers=headers, tablefmt="grid"))


async def refresh_dell_warranty(
    service_tag: Optional[str], hostname: Optional[str], warranty: str
) -> None:
    db_initialize(warranty)

    if service_tag:
        results = query_by_service_tag(warranty, service_tag)
    elif hostname:
        results = query_by_hostname(warranty, hostname)
    else:
        raise ValidationError("Either service tag or hostname must be provided")

    if len(results) == 0:
        raise DatabaseError("No matching record found to refresh")
    if len(results) > 1:
        raise DatabaseError("Multiple matching records found in database")

    existing = results[0]
    svc_tag = existing[0]
    name = existing[1]
    model = existing[2]

    logger.info(f"Refreshing data for {svc_tag} ({name})")

    idrac_host = build_idrac_hostname(name)
    community_string = os.getenv("SNMP_COMMUNITY", "public")
    BIOS_OID = "1.3.6.1.4.1.674.10892.5.4.300.50.1.8.1.1"
    IDRAC_FW_OID = "1.3.6.1.4.1.674.10892.5.1.1.8.0"

    bios_version = await get_snmp_value(idrac_host, community_string, BIOS_OID)
    idrac_version = await get_snmp_value(idrac_host, community_string, IDRAC_FW_OID)

    logger.info(f"Updated SNMP values - BIOS: {bios_version}, iDRAC: {idrac_version}")

    logger.info("Fetching updated warranty information from Dell API")
    warranty_results = dell_api_warranty_date(svc_tag)
    exp_epoch, exp_date = warranty_results[svc_tag]

    logger.info(f"Updated warranty expiration: {exp_date}")

    upsert_system(
        warranty, svc_tag, name, model, idrac_version, bios_version, exp_date, exp_epoch
    )

    logger.info(f"Successfully refreshed record for {svc_tag}")


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
    hostname: str, warranty: str, auto_add: bool
) -> dict:
    result = {"hostname": hostname, "status": "ok", "error": None}
    try:
        service_tag, model = await discover_dell_system(hostname, warranty)
        result["service_tag"] = service_tag
        result["model"] = model

        if auto_add:
            await add_dell_warranty(service_tag, hostname, model, warranty)
            result["added"] = True
        else:
            result["added"] = False
    except (SNMPError, DracsError) as e:
        result["status"] = "error"
        result["error"] = str(e)
        logger.error(f"Failed to discover {hostname}: {e}")

    return result


async def discover_dell_systems_batch(
    hosts: List[str], warranty: str, auto_add: bool
) -> None:
    tasks = [_discover_single_host(h, warranty, False) for h in hosts]
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
                )
                for r in discovered
            ]
            add_outcomes = await asyncio.gather(
                *add_tasks, return_exceptions=True
            )

            for r, outcome in zip(discovered, add_outcomes):
                if isinstance(outcome, Exception):
                    r["status"] = "error"
                    r["error"] = str(outcome)
                else:
                    r["added"] = True

    succeeded = [r for r in results if r["status"] == "ok"]
    failed = [r for r in results if r["status"] == "error"]

    if succeeded:
        table_data = [
            (r["hostname"], r["service_tag"], r["model"],
             "Added" if r.get("added") else "Discovered")
            for r in succeeded
        ]
        headers = ["Hostname", "Service Tag", "Model", "Status"]
        print(tabulate(table_data, headers=headers, tablefmt="grid"))

    if failed:
        print(f"\nFailed ({len(failed)}/{len(results)}):")
        for r in failed:
            print(f"  {r['hostname']}: {r['error']}")

    total = len(results)
    print(
        f"\nSummary: {len(succeeded)} succeeded, "
        f"{len(failed)} failed out of {total} hosts"
    )


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
            results = (
                session.query(System)
                .filter(System.svc_tag == service_tag)
                .all()
            )
        elif hostname:
            results = (
                session.query(System).filter(System.name == hostname).all()
            )
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

#!/usr/bin/env python3

import argparse
import asyncio
import json
import logging
import os
import re
import requests
import sqlite3
import sys
import time
from contextlib import contextmanager
from datetime import datetime
from dotenv import load_dotenv
from pathlib import Path
from typing import List, Tuple, Optional
from pysnmp.hlapi.v1arch.asyncio import (
    SnmpDispatcher,
    CommunityData,
    UdpTransportTarget,
    ObjectIdentity,
    ObjectType,
    get_cmd,
)
from tabulate import tabulate

__version__ = "1.0.0"

logger = logging.getLogger(__name__)


def setup_logging(debug: bool = False, verbose: bool = False) -> None:
    """
    Configure logging with appropriate level and format.

    Args:
        debug: Enable DEBUG level logging (most detailed)
        verbose: Enable INFO level logging (progress messages)

    If neither flag is set, only WARNING and ERROR messages are shown.
    """
    if debug:
        level = logging.DEBUG
    elif verbose:
        level = logging.INFO
    else:
        level = logging.WARNING

    logging.basicConfig(
        level=level,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


class DracsError(Exception):
    """Base exception for DRACS application errors."""

    pass


class ValidationError(DracsError):
    """Raised when input validation fails."""

    pass


class DatabaseError(DracsError):
    """Raised when database operations fail."""

    pass


class APIError(DracsError):
    """Raised when API calls fail."""

    pass


class SNMPError(DracsError):
    """Raised when SNMP operations fail."""

    pass


def validate_service_tag(svctag: Optional[str]) -> bool:
    """
    Validates Dell service tag format.
    Service tags are typically 7 alphanumeric characters.
    """
    if not svctag or not isinstance(svctag, str):
        return False
    if not re.match(r"^[A-Z0-9]{5,7}$", svctag):
        return False
    return True


def validate_hostname(hostname: Optional[str]) -> bool:
    """
    Validates hostname format (DNS-safe characters).
    """
    if not hostname or not isinstance(hostname, str):
        return False
    if len(hostname) > 253:
        return False
    pattern = (
        r"^[a-zA-Z0-9]([a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?"
        r"(\.[a-zA-Z0-9]([a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?)*$"
    )
    if not re.match(pattern, hostname):
        return False
    return True


def read_host_list(filepath: str) -> List[str]:
    """
    Reads a plain text file containing one hostname per line.
    Strips whitespace and skips empty lines and comments.
    """
    path = Path(filepath)
    if not path.is_file():
        raise ValidationError(f"Host list file not found: {filepath}")

    hosts = []
    for line in path.read_text().splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            if not validate_hostname(stripped):
                raise ValidationError(
                    f"Invalid hostname in host list: {stripped}. "
                    "Hostnames should contain only letters, numbers, "
                    "hyphens, and periods"
                )
            hosts.append(stripped)

    if not hosts:
        raise ValidationError(f"Host list file is empty: {filepath}")

    return hosts


def validate_version(version: Optional[str]) -> bool:
    """
    Validates version string format (e.g., 2.1.0).
    """
    if not version or not isinstance(version, str):
        return False
    if not re.match(r"^\d+(\.\d+)*$", version):
        return False
    return True


def build_idrac_hostname(hostname: str) -> str:
    """
    Builds the iDRAC hostname from the target hostname using environment variables.

    Uses DRACS_DNS_STRING and DRACS_DNS_MODE to determine how to
    construct the iDRAC FQDN.
    - prefix mode: DRACS_DNS_STRING + hostname
    - suffix mode: hostname_part + DRACS_DNS_STRING + domain

    Args:
        hostname: The target system hostname

    Returns:
        The constructed iDRAC hostname

    Raises:
        ValidationError: If DRACS_DNS_MODE or DRACS_DNS_STRING
            are not properly configured
    """
    dns_string = os.getenv("DRACS_DNS_STRING")
    dns_mode = os.getenv("DRACS_DNS_MODE")

    if not dns_string:
        raise ValidationError(
            "DRACS_DNS_STRING environment variable is required. "
            "Set it in your .env file (e.g., DRACS_DNS_STRING=mgmt-)"
        )

    if not dns_mode:
        raise ValidationError(
            "DRACS_DNS_MODE environment variable is required. "
            "Set it to either 'prefix' or 'suffix' in your .env file"
        )

    if dns_mode not in ["prefix", "suffix"]:
        raise ValidationError(
            f"DRACS_DNS_MODE must be either 'prefix' or 'suffix', got: {dns_mode}"
        )

    if dns_mode == "prefix":
        # Simple prefix: dns_string + hostname
        return dns_string + hostname
    else:
        # Suffix mode: extract hostname part, add suffix, then add domain
        if "." in hostname:
            # Split into hostname and domain
            parts = hostname.split(".", 1)
            hostname_part = parts[0]
            domain_part = parts[1]
            return f"{hostname_part}{dns_string}.{domain_part}"
        else:
            # No domain, just add suffix to hostname
            return hostname + dns_string


@contextmanager
def get_db_connection(dbpath):
    """
    Context manager for database connections.
    Ensures connections are properly closed.
    """
    conn = sqlite3.connect(dbpath)
    try:
        yield conn
    finally:
        conn.close()


def query_by_service_tag(dbpath: str, service_tag: str) -> List[Tuple]:
    """
    Helper function to query systems by service tag.
    Returns list of matching records.
    """
    with get_db_connection(dbpath) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT * FROM systems
            WHERE svc_tag = ?
        """,
            (service_tag,),
        )
        return cursor.fetchall()


def query_by_hostname(dbpath: str, hostname: str) -> List[Tuple]:
    """
    Helper function to query systems by hostname.
    Returns list of matching records.
    """
    with get_db_connection(dbpath) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT * FROM systems
            WHERE name = ?
        """,
            (hostname,),
        )
        return cursor.fetchall()


def upsert_system(
    dbpath: str,
    svc_tag: str,
    name: str,
    model: str,
    idrac_version: str,
    bios_version: str,
    exp_date: str,
    exp_epoch: int,
) -> None:
    """
    Helper function to insert or replace a system record.
    """
    with get_db_connection(dbpath) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT OR REPLACE INTO systems
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
            (svc_tag, name, model, idrac_version, bios_version, exp_date, exp_epoch),
        )
        conn.commit()


def db_initialize(dbpath: str) -> None:
    """
    Initializes the SQLite database. Creates the 'systems' table if it does not
    already exist in the specified file path.
    """
    with get_db_connection(dbpath) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS systems (
                svc_tag TEXT PRIMARY KEY,
                name TEXT,
                model TEXT,
                idrac_version TEXT,
                bios_version TEXT,
                exp_date TEXT,
                exp_epoch INTEGER
            )
        """)
        conn.commit()
    return


async def get_snmp_value(target: str, community: str, oid: str) -> Optional[str]:
    """
    Asynchronously queries a specific SNMP OID from a target host.
    Used here to pull BIOS and iDRAC firmware versions from Dell servers.
    """
    snmp_dispatcher = SnmpDispatcher()

    # Standard SNMP v2c Get Command
    errorIndication, errorStatus, errorIndex, varBinds = await get_cmd(
        snmp_dispatcher,
        CommunityData(community),
        await UdpTransportTarget.create((target, 161)),
        ObjectType(ObjectIdentity(oid)),
    )

    if errorIndication:
        logger.error(f"SNMP error: {errorIndication}")
        return None
    elif errorStatus:
        logger.error(f"SNMP error: {errorStatus.prettyPrint()} at {errorIndex}")
        return None
    else:
        for varBind in varBinds:
            return varBind[1].prettyPrint()


def dell_api_warranty_date(svctag: Optional[str]) -> Tuple[int, str]:
    """
    Authenticates with Dell's OAuth2 API and fetches the latest warranty
    expiration date for a given service tag. Returns a tuple of (epoch, string).
    """
    if svctag is None:
        raise ValidationError("Service tag parameter is required")

    # Your credentials from TechDirect
    CLIENT_ID = os.getenv("CLIENT_ID")
    CLIENT_SECRET = os.getenv("CLIENT_SECRET")

    if not CLIENT_ID or not CLIENT_SECRET:
        raise APIError(
            "Dell API credentials not found! "
            "Please set CLIENT_ID and CLIENT_SECRET in your .env file. "
            "Visit https://techdirect.dell.com to obtain API credentials"
        )

    # Verify current URL in TechDirect docs
    TOKEN_URL = (
        "https://apigtwb2c.us.dell.com/auth/oauth/v2/token"
    )

    # Fetch the token
    auth_response = requests.post(
        TOKEN_URL,
        data={"grant_type": "client_credentials"},
        auth=(CLIENT_ID, CLIENT_SECRET),
    )

    token = auth_response.json().get("access_token")

    WARRANTY_API_URL = (
        "https://apigtwb2c.us.dell.com/PROD/sbil/eapi/v5/asset-entitlements"
    )

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }

    payload = {"servicetags": [svctag]}

    response = requests.get(WARRANTY_API_URL, headers=headers, params=payload)

    if response.status_code == 200:
        warranty_data = response.json()
    else:
        raise APIError(
            f"Dell API request failed: {response.status_code} - {response.text}"
        )

    for s in warranty_data:
        svctag = s["serviceTag"]
        entitlements = s["entitlements"]

    cur_eed = 0
    cur_eed_string = "January 1, 1970"
    for e in entitlements:
        eed = e["endDate"]
        eed_dt = datetime.fromisoformat(eed.replace("Z", "+00:00"))
        eed_dt_epoch = int(eed_dt.strftime("%s"))
        eed_dt_string = eed_dt.strftime("%B %e, %Y")
        if eed_dt_epoch > cur_eed:
            cur_eed = eed_dt_epoch
            cur_eed_string = eed_dt_string

    return (cur_eed, cur_eed_string)


async def add_dell_warranty(
    service_tag: str, hostname: str, model: str, warranty: str
) -> None:
    """
    Logic for the 'add' command. Fetches hardware versions via SNMP and
    warranty dates via API, then saves the new record to the local DB.
    """
    idrac_host = build_idrac_hostname(hostname)
    community_string = os.getenv("SNMP_COMMUNITY", "public")
    BIOS_OID = "1.3.6.1.4.1.674.10892.5.4.300.50.1.8.1.1"
    IDRAC_FW_OID = "1.3.6.1.4.1.674.10892.5.1.1.8.0"

    bios_version = await get_snmp_value(idrac_host, community_string, BIOS_OID)
    idrac_version = await get_snmp_value(idrac_host, community_string, IDRAC_FW_OID)

    logger.info(f"Retrieved SNMP values - BIOS: {bios_version}, iDRAC: {idrac_version}")

    db_initialize(warranty)

    with get_db_connection(warranty) as conn:
        cursor = conn.cursor()
        query = """
            SELECT * FROM systems
            WHERE svc_tag = :service_tag
               AND name = :hostname
        """
        params = {"service_tag": service_tag, "hostname": hostname}
        cursor.execute(query, params)
        results = cursor.fetchall()
    if debug_output:
        logger.debug(f"service_tag = {service_tag}")
        logger.debug(f"hostname = {hostname}")
        logger.debug(f"warranty = {warranty}")
        logger.debug(f"query = {query}")
        logger.debug(f"params = {params}")
        logger.debug(f"results = {results}")

    if len(results) > 1:
        raise DatabaseError("Multiple matching records found in database")

    # If the host is already in the DB, then we
    # update the FW and BIOS versions, as well as model.
    # No need to reach out to Dell to refetch warranty
    if len(results) == 1:
        logger.info(f"Updating existing record for {service_tag}")
        exp_date = results[0][5]
        exp_epoch = results[0][6]
        with get_db_connection(warranty) as conn:
            cursor = conn.cursor()
            data = {
                "svc_tag": service_tag,
                "name": hostname,
                "model": model,
                "idrac_version": idrac_version,
                "bios_version": bios_version,
                "exp_date": exp_date,
                "exp_epoch": exp_epoch,
            }
            cursor.execute(
                """
                INSERT OR REPLACE INTO systems
                VALUES (:svc_tag, :name, :model,
                :idrac_version, :bios_version,
                :exp_date, :exp_epoch)
            """,
                data,
            )
            conn.commit()
        logger.info(f"Successfully updated record for {service_tag}")
    else:
        # get warranty from Dell API
        logger.info(
            f"Adding new record for {service_tag}, fetching warranty from Dell API"
        )
        h_epoch, h_date = dell_api_warranty_date(service_tag)
        result = {"svctag": service_tag}
        result["exp_date"] = h_date
        result["exp_epoch"] = h_epoch
        result["hostname"] = hostname
        result["model"] = model
        result["bios_version"] = bios_version
        result["idrac_version"] = idrac_version

        if debug_output:
            logger.debug(f"Warranty result: {result}")

        with get_db_connection(warranty) as conn:
            cursor = conn.cursor()
            data = {
                "svc_tag": service_tag,
                "name": hostname,
                "model": model,
                "idrac_version": idrac_version,
                "bios_version": bios_version,
                "exp_date": result["exp_date"],
                "exp_epoch": result["exp_epoch"],
            }
            logger.debug(f"Inserting data: {data}")
            cursor.execute(
                """
                INSERT OR REPLACE INTO systems
                VALUES (:svc_tag, :name, :model,
                :idrac_version, :bios_version,
                :exp_date, :exp_epoch)
            """,
                data,
            )
            conn.commit()
        logger.info(f"Successfully added record for {service_tag}")


async def edit_dell_warranty(
    service_tag: Optional[str],
    hostname: Optional[str],
    model: Optional[str],
    idrac: bool,
    bios: bool,
    warranty: str,
) -> None:
    """
    Logic for the 'edit' command. Allows updating specific fields (model, BIOS, iDRAC)
    for an existing record in the database without re-fetching warranty dates.
    """
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
    conn = sqlite3.connect(warranty)
    cursor = conn.cursor()
    if service_tag:
        query = """
            SELECT * FROM systems
            WHERE svc_tag = :service_tag
        """
        params = {"service_tag": service_tag}
    if hostname:
        query = """
            SELECT * FROM systems
            WHERE name = :hostname
        """
        params = {"hostname": hostname}
    cursor.execute(query, params)
    results = cursor.fetchall()
    conn.close()

    if debug_output:
        logger.debug(f"service_tag = {service_tag}")
        logger.debug(f"hostname = {hostname}")
        logger.debug(f"warranty = {warranty}")
        logger.debug(f"query = {query}")
        logger.debug(f"params = {params}")
        logger.debug(f"results = {results}")

    if len(results) > 1:
        raise DatabaseError("Multiple matching records found in database")

    if len(results) == 1:
        hostname = results[0][1]
        idrac_host = build_idrac_hostname(hostname)
        community_string = os.getenv("SNMP_COMMUNITY", "public")
        BIOS_OID = "1.3.6.1.4.1.674.10892.5.4.300.50.1.8.1.1"
        IDRAC_FW_OID = "1.3.6.1.4.1.674.10892.5.1.1.8.0"

        if idrac:
            idrac_version = await get_snmp_value(
                idrac_host, community_string, IDRAC_FW_OID
            )
        else:
            idrac_version = results[0][3]
        if bios:
            bios_version = await get_snmp_value(idrac_host, community_string, BIOS_OID)
        else:
            bios_version = results[0][4]
        if not model:
            model = results[0][2]
        exp_date = results[0][5]
        exp_epoch = results[0][6]
        conn = sqlite3.connect(warranty)
        cursor = conn.cursor()
        data = {
            "svc_tag": results[0][0],
            "name": results[0][1],
            "model": model,
            "idrac_version": idrac_version,
            "bios_version": bios_version,
            "exp_date": exp_date,
            "exp_epoch": exp_epoch,
        }
        # Insert data
        cursor.execute(
            """
            INSERT OR REPLACE INTO systems
            VALUES (:svc_tag, :name, :model,
            :idrac_version, :bios_version,
            :exp_date, :exp_epoch)
        """,
            data,
        )
        conn.commit()
        conn.close()
        if debug_output:
            logger.info("Database updated successfully")
    else:
        raise DatabaseError("Record not found in database")
    return


async def lookup_dell_warranty(
    service_tag: Optional[str],
    hostname: Optional[str],
    idrac: bool,
    bios: bool,
    full: bool,
    warranty: str,
) -> None:
    """
    Logic for the 'lookup' command. Retrieves a single system's data from
    the DB and prints it to the console in dictionary format.
    """
    db_initialize(warranty)
    conn = sqlite3.connect(warranty)
    cursor = conn.cursor()
    if service_tag:
        query = """
            SELECT * FROM systems
            WHERE svc_tag = :service_tag
        """
        params = {"service_tag": service_tag}
    if hostname:
        query = """
            SELECT * FROM systems
            WHERE name = :hostname
        """
        params = {"hostname": hostname}
    cursor.execute(query, params)
    results = cursor.fetchall()
    conn.close()
    if len(results) == 0:
        raise DatabaseError("No matching records found in database")
    if len(results) > 1:
        raise DatabaseError("Multiple matching records found in database")
    if len(results) == 1:
        hostname = results[0][1]
        result = {"hostname": hostname}
        model = results[0][2]
        if idrac or full:
            idrac_version = results[0][3]
            result["idrac_version"] = idrac_version
        if bios or full:
            bios_version = results[0][4]
            result["bios_version"] = bios_version
        result["svc_tag"] = results[0][0]
        if not idrac and not bios:
            result["model"] = model
            result["exp_date"] = results[0][5]
            result["exp_epoch"] = results[0][6]
        print(result)
    else:
        raise DatabaseError("Record not found in database")
    return


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
    """
    Helper function to filter a list of systems based on version comparison.
    Converts version strings (e.g., '2.1.1') into tuples for proper numeric comparison.
    """
    output = []
    # columns are svc_tag,hostname,model,idrac_version,bios_version,exp_string,exp_epoch
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
    """
    Logic for the 'list' command. Performs complex SQL queries based on filters
    (model, regex, expiration time) and outputs results in JSON,
    Grid table, or hostname-only format.
    """
    db_initialize(warranty)
    conn = sqlite3.connect(warranty)
    cursor = conn.cursor()
    # default query
    query = """
            SELECT * FROM systems
            WHERE svc_tag LIKE '%'
    """
    params = {}
    if service_tag and hostname:
        raise ValidationError(
            "Cannot specify both --svctag and --target; they are mutually exclusive"
        )
    if service_tag:
        query = """
            SELECT * FROM systems
            WHERE svc_tag = :service_tag
        """
        params = {"service_tag": service_tag}
    if hostname:
        query = """
            SELECT * FROM systems
            WHERE name = :hostname
        """
        params = {"hostname": hostname}

    if hostname or service_tag:
        if model or regex:
            raise ValidationError(
                "Cannot specify --model or --regex when using --svctag or --target"
            )

    if model and regex:
        query = """
            SELECT * from systems
            WHERE name LIKE :regex AND model = :model
        """
        params = {"regex": regex, "model": model}

    if model and not regex:
        query = """
            SELECT * from systems
            WHERE model = :model
        """
        params = {"model": model}

    if not model and regex:
        query = """
            SELECT * from systems
            WHERE name LIKE :regex
        """
        params = {"regex": regex}

    if expires_in:
        current_time = int(time.time())
        future_timestamp = current_time + (int(expires_in) * 86400)
        # Only include systems expiring in the future (not already expired)
        query += "AND exp_epoch > :current_time AND exp_epoch <= :future_timestamp\n"
        params["current_time"] = current_time
        params["future_timestamp"] = future_timestamp

    if expired:
        current_time = int(time.time())
        # Only include systems that have already expired
        query += "AND exp_epoch < :current_time\n"
        params["current_time"] = current_time

    # Always sort by hostname for consistent output
    query += " ORDER BY name"

    cursor.execute(query, params)
    results = cursor.fetchall()
    conn.close()
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
        # Print only hostnames, one per line
        for row in results:
            print(row[1])  # Index 1 is the hostname (name field)
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
    return


async def refresh_dell_warranty(
    service_tag: Optional[str], hostname: Optional[str], warranty: str
) -> None:
    """
    Logic for the 'refresh' command. Refreshes SNMP data (BIOS/iDRAC versions)
    and warranty information from Dell API for an existing system.
    """
    db_initialize(warranty)

    # Query the existing record
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

    # Extract existing data
    existing = results[0]
    svc_tag = existing[0]
    name = existing[1]
    model = existing[2]

    logger.info(f"Refreshing data for {svc_tag} ({name})")

    # Fetch fresh SNMP data
    idrac_host = build_idrac_hostname(name)
    community_string = os.getenv("SNMP_COMMUNITY", "public")
    BIOS_OID = "1.3.6.1.4.1.674.10892.5.4.300.50.1.8.1.1"
    IDRAC_FW_OID = "1.3.6.1.4.1.674.10892.5.1.1.8.0"

    bios_version = await get_snmp_value(idrac_host, community_string, BIOS_OID)
    idrac_version = await get_snmp_value(idrac_host, community_string, IDRAC_FW_OID)

    logger.info(f"Updated SNMP values - BIOS: {bios_version}, iDRAC: {idrac_version}")

    # Fetch fresh warranty data from Dell
    logger.info("Fetching updated warranty information from Dell API")
    exp_epoch, exp_date = dell_api_warranty_date(svc_tag)

    logger.info(f"Updated warranty expiration: {exp_date}")

    # Update the database
    upsert_system(
        warranty, svc_tag, name, model, idrac_version, bios_version, exp_date, exp_epoch
    )

    logger.info(f"Successfully refreshed record for {svc_tag}")


async def discover_dell_system(hostname: str, warranty: str) -> Tuple[str, str]:
    """
    Logic for the 'discover' command. Queries a Dell iDRAC interface via SNMP
    to automatically discover the service tag and model information.

    Returns:
        Tuple of (service_tag, model) discovered from the system
    """
    logger.info(f"Discovering system information for {hostname}")

    idrac_host = build_idrac_hostname(hostname)
    community_string = os.getenv("SNMP_COMMUNITY", "public")

    # Dell OIDs for service tag and model
    SERVICE_TAG_OID = ".1.3.6.1.4.1.674.10892.5.1.3.2.0"
    MODEL_OID = ".1.3.6.1.4.1.674.10892.5.1.3.12.0"

    logger.info(f"Querying {idrac_host} for service tag and model")

    # Query service tag
    service_tag = await get_snmp_value(idrac_host, community_string, SERVICE_TAG_OID)
    if not service_tag:
        raise SNMPError(f"Failed to retrieve service tag from {idrac_host}")

    # Query model
    model = await get_snmp_value(idrac_host, community_string, MODEL_OID)
    if not model:
        raise SNMPError(f"Failed to retrieve model from {idrac_host}")

    # Strip "PowerEdge " prefix if present
    if model.startswith("PowerEdge "):
        model = model.replace("PowerEdge ", "")

    logger.info(f"Discovered: Service Tag={service_tag}, Model={model}")

    return (service_tag, model)


async def _discover_single_host(
    hostname: str, warranty: str, auto_add: bool
) -> dict:
    """
    Discovers a single host and optionally adds it to the database.
    Returns a result dict with status information.
    """
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
    """
    Discovers multiple hosts concurrently using asyncio.gather.
    Prints a summary table of results.
    """
    tasks = [_discover_single_host(h, warranty, auto_add) for h in hosts]
    results = await asyncio.gather(*tasks)

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
    """
    Logic for the 'remove' command. Deletes a system record from the
    database by service tag or hostname.
    """
    if service_tag:
        if debug_output:
            print(f"service_tag = {service_tag}")
    if hostname:
        if debug_output:
            print(f"hostname = {hostname}")

    db_initialize(warranty)
    conn = sqlite3.connect(warranty)
    cursor = conn.cursor()
    if service_tag:
        query = """
            SELECT * FROM systems
            WHERE svc_tag = :service_tag
        """
        params = {"service_tag": service_tag}
    if hostname:
        query = """
            SELECT * FROM systems
            WHERE name = :hostname
        """
        params = {"hostname": hostname}
    cursor.execute(query, params)
    results = cursor.fetchall()
    conn.close()
    if len(results) == 0:
        raise DatabaseError("No matching records found in database")
    if len(results) > 1:
        raise DatabaseError("Multiple matching records found in database")
    if len(results) == 1:
        hostname = results[0][1]
        result = {"hostname": hostname}
        result["svc_tag"] = results[0][0]
        service_tag = result["svc_tag"]
        query = """
            DELETE FROM systems
            WHERE svc_tag = :service_tag
        """
        params = {"service_tag": result["svc_tag"]}
        conn = sqlite3.connect(warranty)
        cursor = conn.cursor()
        cursor.execute(query, params)
        if cursor.rowcount == 0:
            print(f"No system found with svctag {service_tag}.")
        else:
            conn.commit()
            print("Record deleted")
        conn.close()
    return


class CustomParser(argparse.ArgumentParser):
    """
    Extended ArgumentParser to provide customized error messages
    when no sub-command (add, edit, etc.) is provided.
    """

    def error(self, message):
        # Check if the error is specifically about the missing subparser
        if "required: command" in message:
            print("\nError: One of the following modes must be used:\n")
            print("    add (a)         Add a system")
            print("    discover (d)    Discover system via SNMP")
            print("    edit (e)        Edit a system")
            print("    lookup (l)      Lookup a system")
            print("    refresh (rf)    Refresh SNMP and warranty data")
            print("    remove (r)      Remove a system")
            print("    list (li)       List systems\n")
            self.print_usage()
            sys.exit(2)
        # Fall back to default behavior for other errors
        super().error(message)


async def main() -> None:
    """
    Main entry point. Configures CLI arguments, subparsers for commands,
    handles global debug settings, and routes execution to the appropriate logic.
    """
    parser = CustomParser(description="System Warranty Database Manager")

    # Global Optional Arguments
    parser.add_argument("-d", "--debug", action="store_true", help="Enable debug mode")
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="Enable verbose output"
    )
    parser.add_argument("-w", "--warranty", help="Path to SQLite warranty.db")

    # Create Subparsers (This makes -a, -e, -l, -r mutually exclusive)
    subparsers = parser.add_subparsers(dest="command", required=True)

    # --- ADD COMMAND ---
    parser_add = subparsers.add_parser("add", aliases=["a"], help="Add a system")
    parser_add.add_argument("-s", "--svctag", required=True, help="Service tag")
    parser_add.add_argument("-t", "--target", required=True, help="DNS Hostname")
    parser_add.add_argument(
        "-m", "--model", required=True, help="System model (e.g. R660)"
    )

    # --- DISCOVER COMMAND ---
    parser_discover = subparsers.add_parser(
        "discover", aliases=["d"], help="Discover system via SNMP"
    )
    discover_target_group = parser_discover.add_mutually_exclusive_group(required=True)
    discover_target_group.add_argument(
        "-t", "--target", help="DNS Hostname to discover"
    )
    discover_target_group.add_argument(
        "--host-list", help="Path to file containing hostnames, one per line"
    )
    parser_discover.add_argument(
        "--add",
        action="store_true",
        help="Automatically add to database without prompting",
    )

    # --- EDIT COMMAND ---
    parser_edit = subparsers.add_parser("edit", aliases=["e"], help="Edit a system")
    # Mutually exclusive group: Must have tag OR target
    edit_group = parser_edit.add_mutually_exclusive_group(required=True)
    edit_group.add_argument("-s", "--svctag", help="Service tag to edit")
    edit_group.add_argument("-t", "--target", help="Target hostname to edit")
    # Optional flag for edit
    parser_edit.add_argument("-m", "--model", help="New model name")
    parser_edit.add_argument(
        "--idrac", action="store_true", help="Update iDRAC version"
    )
    parser_edit.add_argument("--bios", action="store_true", help="Update BIOS version")

    # --- LOOKUP COMMAND ---
    parser_lookup = subparsers.add_parser(
        "lookup", aliases=["l"], help="Lookup a system"
    )
    lookup_group = parser_lookup.add_mutually_exclusive_group(required=True)
    lookup_group.add_argument("-s", "--svctag", help="Service tag to find")
    lookup_group.add_argument("-t", "--target", help="Target hostname to find")
    # Specific optional flags for lookup only
    parser_lookup.add_argument(
        "--idrac", action="store_true", help="Print iDRAC version"
    )
    parser_lookup.add_argument("--bios", action="store_true", help="Print BIOS version")
    parser_lookup.add_argument("--full", action="store_true", help="Print All fields")

    # --- LIST COMMAND ---
    parser_list = subparsers.add_parser("list", aliases=["li"], help="List systems")
    # Specific optional flags for list only
    parser_list.add_argument("-s", "--svctag", help="Service tag to find")
    parser_list.add_argument("-t", "--target", help="Target hostname to find")
    parser_list.add_argument("-m", "--model", help="Target model to list")
    parser_list.add_argument("--expires_in", help="List hosts that expire in N days")
    parser_list.add_argument(
        "--expired", action="store_true", help="List hosts with expired warranties"
    )
    parser_list.add_argument(
        "--json", action="store_true", help="Print list results in json format"
    )
    parser_list.add_argument(
        "--host-only", action="store_true", help="Print only hostname (one per line)"
    )
    parser_list.add_argument("--regex", help="Target hostname regex to list")
    # bios args
    list_bios_group = parser_list.add_mutually_exclusive_group(required=False)
    list_bios_group.add_argument(
        "--bios_le", help="Target hostname with BIOS less than or equal to list"
    )
    list_bios_group.add_argument(
        "--bios_lt", help="Target hostname with BIOS less than to list"
    )
    list_bios_group.add_argument(
        "--bios_ge", help="Target hostname with BIOS greater than or equal to list"
    )
    list_bios_group.add_argument(
        "--bios_gt", help="Target hostname with BIOS greater than to list"
    )
    list_bios_group.add_argument(
        "--bios_eq", help="Target hostname with BIOS equal to to list"
    )
    # idrac args
    list_idrac_group = parser_list.add_mutually_exclusive_group(required=False)
    list_idrac_group.add_argument(
        "--idrac_le", help="Target hostname with iDRAC less than or equal to list"
    )
    list_idrac_group.add_argument(
        "--idrac_lt", help="Target hostname with iDRAC less than to list"
    )
    list_idrac_group.add_argument(
        "--idrac_ge", help="Target hostname with iDRAC greater than or equal to list"
    )
    list_idrac_group.add_argument(
        "--idrac_gt", help="Target hostname with iDRAC greater than to list"
    )
    list_idrac_group.add_argument(
        "--idrac_eq", help="Target hostname with iDRAC equal to to list"
    )

    # --- REFRESH COMMAND ---
    parser_refresh = subparsers.add_parser(
        "refresh", aliases=["rf"], help="Refresh SNMP and warranty data for a system"
    )
    refresh_group = parser_refresh.add_mutually_exclusive_group(required=True)
    refresh_group.add_argument(
        "-s", "--svctag", help="Service tag of system to refresh"
    )
    refresh_group.add_argument(
        "-t", "--target", help="Target hostname of system to refresh"
    )

    # --- REMOVE COMMAND ---
    parser_remove = subparsers.add_parser(
        "remove", aliases=["r"], help="Remove a system"
    )
    remove_group = parser_remove.add_mutually_exclusive_group(required=True)
    remove_group.add_argument("-s", "--svctag", help="Service tag to remove")
    remove_group.add_argument("-t", "--target", help="Target hostname to remove")

    args = parser.parse_args()

    # Set up logging based on command-line flags
    setup_logging(debug=args.debug, verbose=args.verbose)

    # Handling Global Debug
    global debug
    debug = args.debug
    global debug_output
    debug_output = debug

    if hasattr(args, "svctag") and args.svctag:
        target_tag = args.svctag.upper()
        if not validate_service_tag(target_tag):
            raise ValidationError(
                f"Invalid service tag format: {args.svctag}. "
                "Service tags should be 5-7 alphanumeric characters"
            )
    else:
        target_tag = None

    if hasattr(args, "target") and args.target:
        if not validate_hostname(args.target):
            raise ValidationError(
                f"Invalid hostname format: {args.target}. "
                "Hostnames should contain only letters, numbers, hyphens, and periods"
            )

    if args.warranty:
        warranty = args.warranty
    else:
        warranty = str(Path(__file__).resolve().parent) + "/warranty.db"

    db_initialize(warranty)

    # Logic Routing
    if args.command in ["discover", "d"]:
        if args.host_list:
            hosts = read_host_list(args.host_list)
            auto_add = hasattr(args, "add") and args.add
            if not auto_add:
                print(f"Discovering {len(hosts)} hosts from {args.host_list}...")
                response = input(
                    "Add discovered systems to database? (y/n): "
                ).strip().lower()
                auto_add = response in ["y", "yes"]
            await discover_dell_systems_batch(hosts, warranty, auto_add)
        else:
            # Single host discover
            discovered_tag, discovered_model = await discover_dell_system(
                args.target, warranty
            )

            # Check if --add flag was provided
            if hasattr(args, "add") and args.add:
                # Auto-add without prompting
                logger.info("Auto-adding system to database (--add flag provided)")
                await add_dell_warranty(
                    discovered_tag, args.target, discovered_model, warranty
                )
            else:
                # Prompt user
                print("\nDiscovered system:")
                print(f"  Hostname:    {args.target}")
                print(f"  Service Tag: {discovered_tag}")
                print(f"  Model:       {discovered_model}")
                print()
                response = input("Add to database? (y/n): ").strip().lower()

                if response in ["y", "yes"]:
                    logger.info("User confirmed, adding system to database")
                    await add_dell_warranty(
                        discovered_tag, args.target, discovered_model, warranty
                    )
                else:
                    logger.info("User declined, not adding to database")
                    print("System not added to database")

    elif args.command in ["add", "a"]:
        await add_dell_warranty(target_tag, args.target, args.model, warranty)
    elif args.command in ["edit", "e"]:
        await edit_dell_warranty(
            target_tag, args.target, args.model, args.idrac, args.bios, warranty
        )
    elif args.command in ["lookup", "l"]:
        await lookup_dell_warranty(
            target_tag, args.target, args.idrac, args.bios, args.full, warranty
        )
    elif args.command in ["refresh", "rf"]:
        await refresh_dell_warranty(target_tag, args.target, warranty)
    elif args.command in ["remove", "r"]:
        await remove_dell_warranty(target_tag, args.target, warranty)
    elif args.command in ["list", "li"]:
        await list_dell_warranty(
            target_tag,
            args.target,
            args.model,
            args.regex,
            args.bios_le,
            args.bios_lt,
            args.bios_ge,
            args.bios_gt,
            args.bios_eq,
            args.idrac_le,
            args.idrac_lt,
            args.idrac_ge,
            args.idrac_gt,
            args.idrac_eq,
            args.expires_in,
            args.expired,
            args.json,
            args.host_only,
            warranty,
        )


def main_cli() -> None:
    load_dotenv()
    global debug_output, debug
    debug_output = False
    try:
        debug = os.environ["DEBUG"]
        if debug == "true":
            debug_output = True
    except KeyError:
        debug_output = False
        debug = False

    try:
        asyncio.run(main())
    except ValidationError as e:
        logger.error(f"Validation Error: {e}")
        sys.exit(1)
    except DatabaseError as e:
        logger.error(f"Database Error: {e}")
        sys.exit(1)
    except APIError as e:
        logger.error(f"API Error: {e}")
        sys.exit(1)
    except SNMPError as e:
        logger.error(f"SNMP Error: {e}")
        sys.exit(1)
    except DracsError as e:
        logger.error(f"Error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main_cli()

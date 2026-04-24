import logging
import os
import socket
from typing import Optional

from pysnmp.hlapi.v1arch.asyncio import (
    SnmpDispatcher,
    CommunityData,
    UdpTransportTarget,
    ObjectIdentity,
    ObjectType,
    get_cmd,
)
from pysnmp.error import PySnmpError

from dracs.exceptions import SNMPError, ValidationError

logger = logging.getLogger(__name__)


async def get_snmp_value(target: str, community: str, oid: str) -> Optional[str]:
    """
    Asynchronously queries a specific SNMP OID from a target host.
    Used here to pull BIOS and iDRAC firmware versions from Dell servers.
    """
    snmp_dispatcher = SnmpDispatcher()

    try:
        udp_target = await UdpTransportTarget.create((target, 161))
    except PySnmpError as e:
        error_msg = str(e)
        if "No address associated with hostname" in error_msg or "Name or service not known" in error_msg:
            raise SNMPError(f"DNS resolution failed for {target}: unable to resolve hostname")
        else:
            raise SNMPError(f"SNMP transport error for {target}: {e}")
    except socket.gaierror as e:
        raise SNMPError(f"DNS resolution failed for {target}: {e}")
    except OSError as e:
        raise SNMPError(f"Network error connecting to {target}: {e}")

    errorIndication, errorStatus, errorIndex, varBinds = await get_cmd(
        snmp_dispatcher,
        CommunityData(community),
        udp_target,
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
        return dns_string + hostname
    else:
        if "." in hostname:
            parts = hostname.split(".", 1)
            hostname_part = parts[0]
            domain_part = parts[1]
            return f"{hostname_part}{dns_string}.{domain_part}"
        else:
            return hostname + dns_string

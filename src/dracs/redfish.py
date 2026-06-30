"""Redfish API collection functions for iDRAC configuration data."""

import fnmatch
import logging
import ssl

import requests

logger = logging.getLogger(__name__)

DESIRED = {
    "ps_rapid_on": "Disabled",
    "dns_from_dhcp": "Enabled",
    "ipmi_lan_enable": "Enabled",
    "host_header_check": "Disabled",
    "sys_profile": "PerfPerWattOptimizedOs",
}

_TIMEOUT = 15
_VERIFY = False


def _get_credentials(site_name: str, hostname: str) -> tuple[str, str]:
    from dracs.sites import get_site_ini_config

    cfg = get_site_ini_config(site_name)
    host_cfg = cfg.get("hosts", {}).get(hostname, {})
    defaults = cfg.get("defaults", {})
    username = host_cfg.get("username") or defaults.get("username", "root")
    password = host_cfg.get("password") or defaults.get("password", "")
    return username, password


def collect_ps_rapid_on(idrac_fqdn: str, user: str, pw: str) -> str | None:
    url = (
        f"https://{idrac_fqdn}/redfish/v1/Managers/iDRAC.Embedded.1"
        "/Oem/Dell/DellAttributes/System.Embedded.1"
    )
    try:
        resp = requests.get(  # nosec # nosemgrep
            url, auth=(user, pw), verify=_VERIFY, timeout=_TIMEOUT
        )
        resp.raise_for_status()
        attrs = resp.json().get("Attributes", {})
        return attrs.get("ServerPwr.1.PSRapidOn")
    except Exception as exc:
        logger.debug("collect_ps_rapid_on %s: %s", idrac_fqdn, exc)
        return None


def collect_idrac_hostname(idrac_fqdn: str, user: str, pw: str) -> str | None:
    url = f"https://{idrac_fqdn}/redfish/v1/Systems/System.Embedded.1"
    try:
        resp = requests.get(  # nosec # nosemgrep
            url, auth=(user, pw), verify=_VERIFY, timeout=_TIMEOUT
        )
        resp.raise_for_status()
        return resp.json().get("HostName")
    except Exception as exc:
        logger.debug("collect_idrac_hostname %s: %s", idrac_fqdn, exc)
        return None


def collect_idrac_attributes(idrac_fqdn: str, user: str, pw: str) -> dict:
    """Return dns_from_dhcp, ipmi_lan_enable, host_header_check in one call."""
    url = f"https://{idrac_fqdn}/redfish/v1/Managers/iDRAC.Embedded.1/Attributes"
    result: dict = {}
    try:
        resp = requests.get(  # nosec # nosemgrep
            url, auth=(user, pw), verify=_VERIFY, timeout=_TIMEOUT
        )
        resp.raise_for_status()
        attrs = resp.json().get("Attributes", {})
        val = attrs.get("IPv4.1.DNSFromDHCP")
        if val is not None:
            result["dns_from_dhcp"] = val
        val = attrs.get("IPMILan.1.Enable")
        if val is not None:
            result["ipmi_lan_enable"] = val
        val = attrs.get("WebServer.1.HostHeaderCheck")
        if val is not None:
            result["host_header_check"] = val
    except Exception as exc:
        logger.debug("collect_idrac_attributes %s: %s", idrac_fqdn, exc)
    return result


def collect_sys_profile(idrac_fqdn: str, user: str, pw: str) -> str | None:
    url = f"https://{idrac_fqdn}/redfish/v1/Systems/System.Embedded.1/Bios"
    try:
        resp = requests.get(  # nosec # nosemgrep
            url, auth=(user, pw), verify=_VERIFY, timeout=_TIMEOUT
        )
        resp.raise_for_status()
        attrs = resp.json().get("Attributes", {})
        return attrs.get("SysProfile")
    except Exception as exc:
        logger.debug("collect_sys_profile %s: %s", idrac_fqdn, exc)
        return None


def collect_ssl_info(idrac_fqdn: str) -> dict:
    """Fetch the iDRAC TLS cert; return self_signed, valid_name, and expiry date."""
    result: dict = {"self_signed": None, "valid_name": None, "expiry": None}
    try:
        pem = ssl.get_server_certificate((idrac_fqdn, 443))
        from cryptography import x509

        cert = x509.load_pem_x509_certificate(pem.encode())

        issuer = cert.issuer.rfc4514_string()
        subject = cert.subject.rfc4514_string()
        result["self_signed"] = issuer == subject

        names: list[str] = []
        try:
            san_ext = cert.extensions.get_extension_for_class(
                x509.SubjectAlternativeName
            )
            names = [n.value for n in san_ext.value if isinstance(n, x509.DNSName)]
        except x509.ExtensionNotFound:
            pass
        if not names:
            for attr in cert.subject:
                if attr.oid == x509.NameOID.COMMON_NAME:
                    names.append(attr.value)

        result["valid_name"] = any(
            fnmatch.fnmatch(idrac_fqdn, pattern) for pattern in names
        )

        expiry = cert.not_valid_after_utc.date()
        result["expiry"] = expiry.isoformat()
    except Exception as exc:
        logger.debug("collect_ssl_info %s: %s", idrac_fqdn, exc)
    return result


def collect_all_for_host(hostname: str, site_name: str, enabled_attrs: dict) -> dict:
    """Collect enabled iDRAC config attributes for one host; return HostConfig fields."""
    from dracs.snmp import build_idrac_hostname

    idrac_fqdn = build_idrac_hostname(hostname)
    user, pw = _get_credentials(site_name, hostname)

    data: dict = {}

    needs_idrac_attrs = (
        enabled_attrs.get("dns_from_dhcp_enabled")
        or enabled_attrs.get("ipmi_lan_enable_enabled")
        or enabled_attrs.get("host_header_check_enabled")
    )

    if enabled_attrs.get("ps_rapid_on_enabled"):
        data["ps_rapid_on"] = collect_ps_rapid_on(idrac_fqdn, user, pw)

    if needs_idrac_attrs:
        attrs = collect_idrac_attributes(idrac_fqdn, user, pw)
        if enabled_attrs.get("dns_from_dhcp_enabled"):
            data["dns_from_dhcp"] = attrs.get("dns_from_dhcp")
        if enabled_attrs.get("ipmi_lan_enable_enabled"):
            data["ipmi_lan_enable"] = attrs.get("ipmi_lan_enable")
        if enabled_attrs.get("host_header_check_enabled"):
            data["host_header_check"] = attrs.get("host_header_check")

    if enabled_attrs.get("sys_profile_enabled"):
        data["sys_profile"] = collect_sys_profile(idrac_fqdn, user, pw)

    if enabled_attrs.get("idrac_hostname_enabled"):
        fetched = collect_idrac_hostname(idrac_fqdn, user, pw)
        if fetched is None:
            data["idrac_hostname"] = None
        else:
            data["idrac_hostname"] = 1 if fetched.lower() == idrac_fqdn.lower() else 0

    if enabled_attrs.get("ssl_enabled"):
        ssl_info = collect_ssl_info(idrac_fqdn)
        data["ssl_self_signed"] = 1 if ssl_info.get("self_signed") else 0
        data["ssl_valid_name"] = 1 if ssl_info.get("valid_name") else 0
        data["ssl_expiry"] = ssl_info.get("expiry")

    from datetime import datetime

    data["collected_at"] = datetime.now().isoformat()
    return data

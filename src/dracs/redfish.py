"""Redfish API collection functions for iDRAC configuration data."""

import fnmatch
import logging
import ssl as _ssl

import requests

logger = logging.getLogger(__name__)

_TIMEOUT = 15
_VERIFY = False

_ENDPOINT_URLS = {
    "system_oem_dell": (
        "https://{host}/redfish/v1/Managers/iDRAC.Embedded.1"
        "/Oem/Dell/DellAttributes/System.Embedded.1"
    ),
    "idrac_attributes": (
        "https://{host}/redfish/v1/Managers/iDRAC.Embedded.1/Attributes"
    ),
    "bios": "https://{host}/redfish/v1/Systems/System.Embedded.1/Bios",
    "system": "https://{host}/redfish/v1/Systems/System.Embedded.1",
}


def _get_credentials(site_name: str, hostname: str) -> tuple[str, str]:
    from dracs.sites import get_site_ini_config

    cfg = get_site_ini_config(site_name)
    host_cfg = cfg.get("hosts", {}).get(hostname, {})
    defaults = cfg.get("defaults", {})
    username = host_cfg.get("username") or defaults.get("username", "root")
    password = host_cfg.get("password") or defaults.get("password", "")
    return username, password


def _extract_by_path(data: dict, path: str):
    """Extract a value from a Redfish response using a dot-notation path.

    Paths starting with 'Attributes.' index into data['Attributes'] using the
    remainder as the literal key (which may itself contain dots, e.g.
    'IPv4.1.DNSFromDHCP').  All other paths are treated as top-level keys.
    """
    if path.startswith("Attributes."):
        attr_key = path[len("Attributes."):]
        return data.get("Attributes", {}).get(attr_key)
    return data.get(path)


def collect_ssl_info(idrac_fqdn: str) -> dict:
    """Fetch the iDRAC TLS cert; return self_signed, valid_name, expiry, fingerprint."""
    result: dict = {
        "self_signed": None,
        "valid_name": None,
        "expiry": None,
        "fingerprint": None,
    }
    try:
        pem = _ssl.get_server_certificate((idrac_fqdn, 443))
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes

        cert = x509.load_pem_x509_certificate(pem.encode())

        issuer = cert.issuer.rfc4514_string()
        subject = cert.subject.rfc4514_string()
        result["self_signed"] = issuer == subject
        result["fingerprint"] = ":".join(
            f"{b:02X}" for b in cert.fingerprint(hashes.SHA256())
        )

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


def collect_for_host_dynamic(
    hostname: str, site_name: str, attr_defs: list
) -> dict:
    """Collect config attributes for one host using the DB-driven attr catalog.

    attr_defs: list of dicts from get_enabled_attr_defs_for_site() — each has
    at minimum: name, endpoint_type, attribute_path.

    Returns {attr_name: {"value": str|None, "collected_at": str}}
    """
    from datetime import datetime

    from dracs.snmp import build_idrac_hostname

    idrac_fqdn = build_idrac_hostname(hostname)
    user, pw = _get_credentials(site_name, hostname)
    collected_at = datetime.now().isoformat()

    # Group attr_defs by endpoint_type so we make one HTTP call per endpoint.
    by_endpoint: dict[str, list] = {}
    for attr in attr_defs:
        ep = attr["endpoint_type"]
        by_endpoint.setdefault(ep, []).append(attr)

    results: dict = {}

    for endpoint_type, attrs in by_endpoint.items():
        if endpoint_type == "ssl":
            ssl_info = collect_ssl_info(idrac_fqdn)

            def _ssl_int(flag) -> str | None:
                if flag is None:
                    return None
                return "1" if flag else "0"

            attr_value_map = {
                "ssl_self_signed": _ssl_int(ssl_info.get("self_signed")),
                "ssl_valid_name": _ssl_int(ssl_info.get("valid_name")),
                "ssl_expiry": ssl_info.get("expiry"),
                "ssl_fingerprint": ssl_info.get("fingerprint"),
            }
            for attr in attrs:
                results[attr["name"]] = {
                    "value": attr_value_map.get(attr["name"]),
                    "collected_at": collected_at,
                }
            continue

        url_template = _ENDPOINT_URLS.get(endpoint_type)
        if url_template is None:
            logger.warning(
                "collect_for_host_dynamic: unknown endpoint_type %r", endpoint_type
            )
            for attr in attrs:
                results[attr["name"]] = {"value": None, "collected_at": collected_at}
            continue

        url = url_template.format(host=idrac_fqdn)
        try:
            resp = requests.get(  # nosec # nosemgrep
                url, auth=(user, pw), verify=_VERIFY, timeout=_TIMEOUT
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            logger.debug(
                "collect_for_host_dynamic %s [%s]: %s", idrac_fqdn, endpoint_type, exc
            )
            for attr in attrs:
                results[attr["name"]] = {"value": None, "collected_at": collected_at}
            continue

        for attr in attrs:
            attr_path = attr.get("attribute_path")
            if attr_path:
                raw = _extract_by_path(data, attr_path)
            else:
                raw = None

            if attr["name"] == "idrac_hostname" and raw is not None:
                # Store match indicator: "1" if hostname matches the iDRAC FQDN.
                val = "1" if str(raw).lower() == idrac_fqdn.lower() else "0"
            elif raw is not None:
                val = str(raw)
            else:
                val = None

            results[attr["name"]] = {"value": val, "collected_at": collected_at}

    return results

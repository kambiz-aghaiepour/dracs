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
        attr_key = path[len("Attributes.") :]
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


def _ssl_flag_to_str(flag) -> str | None:
    if flag is None:
        return None
    return "1" if flag else "0"


def _collect_ssl_endpoint(attrs: list, idrac_fqdn: str, collected_at: str) -> dict:
    ssl_info = collect_ssl_info(idrac_fqdn)
    value_map = {
        "ssl_self_signed": _ssl_flag_to_str(ssl_info.get("self_signed")),
        "ssl_valid_name": _ssl_flag_to_str(ssl_info.get("valid_name")),
        "ssl_expiry": ssl_info.get("expiry"),
        "ssl_fingerprint": ssl_info.get("fingerprint"),
    }
    return {
        attr["name"]: {
            "value": value_map.get(attr["name"]),
            "collected_at": collected_at,
        }
        for attr in attrs
    }


def _resolve_attr_value(attr_name: str, raw, idrac_fqdn: str) -> str | None:
    if raw is None:
        return None
    if attr_name == "idrac_hostname":
        # Store match indicator: "1" if hostname matches the iDRAC FQDN.
        return "1" if str(raw).lower() == idrac_fqdn.lower() else "0"
    return str(raw)


def _collect_redfish_endpoint(
    attrs: list,
    endpoint_type: str,
    idrac_fqdn: str,
    user: str,
    pw: str,
    collected_at: str,
) -> dict:
    null_result = {
        attr["name"]: {"value": None, "collected_at": collected_at} for attr in attrs
    }
    url_template = _ENDPOINT_URLS.get(endpoint_type)
    if url_template is None:
        logger.warning(
            "collect_for_host_dynamic: unknown endpoint_type %r", endpoint_type
        )
        return null_result

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
        return null_result

    return {
        attr["name"]: {
            "value": _resolve_attr_value(
                attr["name"],
                _extract_by_path(data, attr.get("attribute_path")),
                idrac_fqdn,
            ),
            "collected_at": collected_at,
        }
        for attr in attrs
    }


def collect_for_host_dynamic(hostname: str, site_name: str, attr_defs: list) -> dict:
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

    by_endpoint: dict[str, list] = {}
    for attr in attr_defs:
        by_endpoint.setdefault(attr["endpoint_type"], []).append(attr)

    results: dict = {}
    for endpoint_type, attrs in by_endpoint.items():
        if endpoint_type == "ssl":
            results.update(_collect_ssl_endpoint(attrs, idrac_fqdn, collected_at))
        else:
            results.update(
                _collect_redfish_endpoint(
                    attrs, endpoint_type, idrac_fqdn, user, pw, collected_at
                )
            )
    return results

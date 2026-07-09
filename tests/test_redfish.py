"""Tests for src/dracs/redfish.py collection functions."""

import os
import ssl
import tempfile
from unittest.mock import MagicMock, patch

import pytest

import dracs.redfish as redfish_mod
from dracs.redfish import (
    _extract_by_path,
    _get_credentials,
    collect_for_host_dynamic,
    collect_ssl_info,
)


def _mock_response(json_data, status_code=200):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data
    resp.raise_for_status = MagicMock()
    if status_code >= 400:
        resp.raise_for_status.side_effect = Exception(f"HTTP {status_code}")
    return resp


def _attr(name, endpoint_type, attribute_path):
    """Build a minimal attr_def dict for collect_for_host_dynamic."""
    return {
        "id": 1,
        "name": name,
        "endpoint_type": endpoint_type,
        "attribute_path": attribute_path,
    }


class TestGetCredentials:
    def test_returns_defaults_when_no_host_section(self):
        mock_cfg = {"hosts": {}, "defaults": {"username": "root", "password": "secret"}}
        with patch("dracs.sites.get_site_ini_config", return_value=mock_cfg):
            user, pw = _get_credentials("Default", "server01.example.com")
        assert user == "root"
        assert pw == "secret"

    def test_host_specific_overrides_defaults(self):
        mock_cfg = {
            "hosts": {
                "server01.example.com": {
                    "username": "customuser",
                    "password": "custompw",
                }
            },
            "defaults": {"username": "root", "password": "secret"},
        }
        with patch("dracs.sites.get_site_ini_config", return_value=mock_cfg):
            user, pw = _get_credentials("Default", "server01.example.com")
        assert user == "customuser"
        assert pw == "custompw"

    def test_falls_back_to_root_when_no_config(self):
        mock_cfg = {"hosts": {}, "defaults": {}}
        with patch("dracs.sites.get_site_ini_config", return_value=mock_cfg):
            user, pw = _get_credentials("Default", "server01.example.com")
        assert user == "root"
        assert pw == ""


class TestExtractByPath:
    def test_attributes_prefix_uses_remainder_as_literal_key(self):
        data = {"Attributes": {"IPv4.1.DNSFromDHCP": "Enabled"}}
        assert _extract_by_path(data, "Attributes.IPv4.1.DNSFromDHCP") == "Enabled"

    def test_attributes_prefix_missing_key_returns_none(self):
        data = {"Attributes": {}}
        assert _extract_by_path(data, "Attributes.IPv4.1.DNSFromDHCP") is None

    def test_top_level_key(self):
        data = {"HostName": "server01"}
        assert _extract_by_path(data, "HostName") == "server01"

    def test_top_level_missing_returns_none(self):
        data = {}
        assert _extract_by_path(data, "HostName") is None

    def test_no_attributes_dict_returns_none(self):
        data = {"SomeOtherKey": "value"}
        assert _extract_by_path(data, "Attributes.SomeKey") is None


class TestCollectSslInfo:
    def _make_cert(self, self_signed=True, valid_name=True, days_until_expiry=90):
        from datetime import datetime, timedelta, timezone

        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa

        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

        subject_name = x509.Name(
            [x509.NameAttribute(x509.NameOID.COMMON_NAME, "mgmt-host.example.com")]
        )
        if self_signed:
            issuer_name = subject_name
        else:
            issuer_name = x509.Name(
                [x509.NameAttribute(x509.NameOID.COMMON_NAME, "My CA")]
            )

        now = datetime.now(timezone.utc)
        builder = (
            x509.CertificateBuilder()
            .subject_name(subject_name)
            .issuer_name(issuer_name)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(days=1))
            .not_valid_after(now + timedelta(days=days_until_expiry))
        )
        if valid_name:
            builder = builder.add_extension(
                x509.SubjectAlternativeName([x509.DNSName("mgmt-host.example.com")]),
                critical=False,
            )
        cert = builder.sign(key, hashes.SHA256())
        return cert.public_bytes(serialization.Encoding.PEM).decode()

    def test_self_signed_cert(self):
        pem = self._make_cert(self_signed=True, valid_name=True)
        with patch("ssl.get_server_certificate", return_value=pem):
            result = collect_ssl_info("mgmt-host.example.com")
        assert result["self_signed"] is True
        assert result["valid_name"] is True
        assert result["expiry"] is not None

    def test_ca_signed_cert(self):
        pem = self._make_cert(self_signed=False, valid_name=True)
        with patch("ssl.get_server_certificate", return_value=pem):
            result = collect_ssl_info("mgmt-host.example.com")
        assert result["self_signed"] is False
        assert result["valid_name"] is True

    def test_invalid_name(self):
        pem = self._make_cert(self_signed=True, valid_name=False)
        with patch("ssl.get_server_certificate", return_value=pem):
            result = collect_ssl_info("different-host.example.com")
        assert result["valid_name"] is False

    def test_returns_none_fields_on_ssl_error(self):
        with patch("ssl.get_server_certificate", side_effect=ssl.SSLError("refused")):
            result = collect_ssl_info("mgmt-host.example.com")
        assert result["self_signed"] is None
        assert result["valid_name"] is None
        assert result["expiry"] is None

    def test_fingerprint_present_on_success(self):
        pem = self._make_cert(self_signed=True, valid_name=True)
        with patch("ssl.get_server_certificate", return_value=pem):
            result = collect_ssl_info("mgmt-host.example.com")
        assert result["fingerprint"] is not None
        assert ":" in result["fingerprint"]


class TestCollectForHostDynamic:
    _PATCH_CREDS = patch(
        "dracs.redfish._get_credentials", return_value=("root", "secret")
    )
    _PATCH_FQDN = patch(
        "dracs.snmp.build_idrac_hostname", return_value="mgmt-server01.example.com"
    )

    @patch.dict(os.environ, {"DRACS_DNS_STRING": "mgmt-", "DRACS_DNS_MODE": "prefix"})
    def test_returns_eav_format(self):
        attr_defs = [
            _attr("ps_rapid_on", "system_oem_dell", "Attributes.ServerPwr.1.PSRapidOn")
        ]
        resp = _mock_response({"Attributes": {"ServerPwr.1.PSRapidOn": "Disabled"}})
        with patch("dracs.redfish._get_credentials", return_value=("root", "pw")):
            with patch(
                "dracs.snmp.build_idrac_hostname",
                return_value="mgmt-server01.example.com",
            ):
                with patch("requests.get", return_value=resp):
                    result = collect_for_host_dynamic(
                        "server01.example.com", "Default", attr_defs
                    )
        assert "ps_rapid_on" in result
        assert result["ps_rapid_on"]["value"] == "Disabled"
        assert result["ps_rapid_on"]["collected_at"] is not None

    @patch.dict(os.environ, {"DRACS_DNS_STRING": "mgmt-", "DRACS_DNS_MODE": "prefix"})
    def test_batches_same_endpoint_into_one_request(self):
        attr_defs = [
            _attr("dns_from_dhcp", "idrac_attributes", "Attributes.IPv4.1.DNSFromDHCP"),
            _attr("ipmi_lan_enable", "idrac_attributes", "Attributes.IPMILan.1.Enable"),
            _attr(
                "host_header_check",
                "idrac_attributes",
                "Attributes.WebServer.1.HostHeaderCheck",
            ),
        ]
        resp = _mock_response(
            {
                "Attributes": {
                    "IPv4.1.DNSFromDHCP": "Enabled",
                    "IPMILan.1.Enable": "Enabled",
                    "WebServer.1.HostHeaderCheck": "Disabled",
                }
            }
        )
        with patch("dracs.redfish._get_credentials", return_value=("root", "pw")):
            with patch(
                "dracs.snmp.build_idrac_hostname",
                return_value="mgmt-server01.example.com",
            ):
                with patch("requests.get", return_value=resp) as mock_get:
                    result = collect_for_host_dynamic(
                        "server01.example.com", "Default", attr_defs
                    )
        assert mock_get.call_count == 1
        assert result["dns_from_dhcp"]["value"] == "Enabled"
        assert result["ipmi_lan_enable"]["value"] == "Enabled"
        assert result["host_header_check"]["value"] == "Disabled"

    @patch.dict(os.environ, {"DRACS_DNS_STRING": "mgmt-", "DRACS_DNS_MODE": "prefix"})
    def test_ssl_endpoint_stores_strings(self):
        ssl_info = {
            "self_signed": True,
            "valid_name": False,
            "expiry": "2026-12-31",
            "fingerprint": "AA:BB:CC",
        }
        attr_defs = [
            _attr("ssl_self_signed", "ssl", None),
            _attr("ssl_valid_name", "ssl", None),
            _attr("ssl_expiry", "ssl", None),
            _attr("ssl_fingerprint", "ssl", None),
        ]
        with patch("dracs.redfish._get_credentials", return_value=("root", "pw")):
            with patch(
                "dracs.snmp.build_idrac_hostname",
                return_value="mgmt-server01.example.com",
            ):
                with patch("dracs.redfish.collect_ssl_info", return_value=ssl_info):
                    result = collect_for_host_dynamic(
                        "server01.example.com", "Default", attr_defs
                    )
        assert result["ssl_self_signed"]["value"] == "1"
        assert result["ssl_valid_name"]["value"] == "0"
        assert result["ssl_expiry"]["value"] == "2026-12-31"
        assert result["ssl_fingerprint"]["value"] == "AA:BB:CC"

    @patch.dict(os.environ, {"DRACS_DNS_STRING": "mgmt-", "DRACS_DNS_MODE": "prefix"})
    def test_ssl_none_fields_stored_as_none(self):
        ssl_info = {
            "self_signed": None,
            "valid_name": None,
            "expiry": None,
            "fingerprint": None,
        }
        attr_defs = [_attr("ssl_self_signed", "ssl", None)]
        with patch("dracs.redfish._get_credentials", return_value=("root", "pw")):
            with patch(
                "dracs.snmp.build_idrac_hostname",
                return_value="mgmt-server01.example.com",
            ):
                with patch("dracs.redfish.collect_ssl_info", return_value=ssl_info):
                    result = collect_for_host_dynamic(
                        "server01.example.com", "Default", attr_defs
                    )
        assert result["ssl_self_signed"]["value"] is None

    @patch.dict(os.environ, {"DRACS_DNS_STRING": "mgmt-", "DRACS_DNS_MODE": "prefix"})
    def test_idrac_hostname_match_stores_one(self):
        attr_defs = [_attr("idrac_hostname", "system", "HostName")]
        resp = _mock_response({"HostName": "mgmt-server01.example.com"})
        with patch("dracs.redfish._get_credentials", return_value=("root", "pw")):
            with patch(
                "dracs.snmp.build_idrac_hostname",
                return_value="mgmt-server01.example.com",
            ):
                with patch("requests.get", return_value=resp):
                    result = collect_for_host_dynamic(
                        "server01.example.com", "Default", attr_defs
                    )
        assert result["idrac_hostname"]["value"] == "1"

    @patch.dict(os.environ, {"DRACS_DNS_STRING": "mgmt-", "DRACS_DNS_MODE": "prefix"})
    def test_idrac_hostname_mismatch_stores_zero(self):
        attr_defs = [_attr("idrac_hostname", "system", "HostName")]
        resp = _mock_response({"HostName": "wrong-name.example.com"})
        with patch("dracs.redfish._get_credentials", return_value=("root", "pw")):
            with patch(
                "dracs.snmp.build_idrac_hostname",
                return_value="mgmt-server01.example.com",
            ):
                with patch("requests.get", return_value=resp):
                    result = collect_for_host_dynamic(
                        "server01.example.com", "Default", attr_defs
                    )
        assert result["idrac_hostname"]["value"] == "0"

    @patch.dict(os.environ, {"DRACS_DNS_STRING": "mgmt-", "DRACS_DNS_MODE": "prefix"})
    def test_http_error_stores_none_for_affected_attrs(self):
        attr_defs = [
            _attr("ps_rapid_on", "system_oem_dell", "Attributes.ServerPwr.1.PSRapidOn")
        ]
        resp = _mock_response({}, status_code=401)
        with patch("dracs.redfish._get_credentials", return_value=("root", "pw")):
            with patch(
                "dracs.snmp.build_idrac_hostname",
                return_value="mgmt-server01.example.com",
            ):
                with patch("requests.get", return_value=resp):
                    result = collect_for_host_dynamic(
                        "server01.example.com", "Default", attr_defs
                    )
        assert result["ps_rapid_on"]["value"] is None
        assert result["ps_rapid_on"]["collected_at"] is not None

    @patch.dict(os.environ, {"DRACS_DNS_STRING": "mgmt-", "DRACS_DNS_MODE": "prefix"})
    def test_connection_error_stores_none(self):
        attr_defs = [
            _attr("ps_rapid_on", "system_oem_dell", "Attributes.ServerPwr.1.PSRapidOn")
        ]
        with patch("dracs.redfish._get_credentials", return_value=("root", "pw")):
            with patch(
                "dracs.snmp.build_idrac_hostname",
                return_value="mgmt-server01.example.com",
            ):
                with patch("requests.get", side_effect=ConnectionError("unreachable")):
                    result = collect_for_host_dynamic(
                        "server01.example.com", "Default", attr_defs
                    )
        assert result["ps_rapid_on"]["value"] is None

    @patch.dict(os.environ, {"DRACS_DNS_STRING": "mgmt-", "DRACS_DNS_MODE": "prefix"})
    def test_empty_attr_defs_returns_empty_dict(self):
        with patch("dracs.redfish._get_credentials", return_value=("root", "pw")):
            with patch(
                "dracs.snmp.build_idrac_hostname",
                return_value="mgmt-server01.example.com",
            ):
                result = collect_for_host_dynamic("server01.example.com", "Default", [])
        assert result == {}

    @patch.dict(os.environ, {"DRACS_DNS_STRING": "mgmt-", "DRACS_DNS_MODE": "prefix"})
    def test_unknown_endpoint_type_stores_none(self):
        attr_defs = [_attr("mystery_attr", "unknown_endpoint", "SomeKey")]
        with patch("dracs.redfish._get_credentials", return_value=("root", "pw")):
            with patch(
                "dracs.snmp.build_idrac_hostname",
                return_value="mgmt-server01.example.com",
            ):
                result = collect_for_host_dynamic(
                    "server01.example.com", "Default", attr_defs
                )
        assert result["mystery_attr"]["value"] is None

    @patch.dict(os.environ, {"DRACS_DNS_STRING": "mgmt-", "DRACS_DNS_MODE": "prefix"})
    def test_missing_path_key_stores_none(self):
        attr_defs = [
            _attr("ps_rapid_on", "system_oem_dell", "Attributes.ServerPwr.1.PSRapidOn")
        ]
        resp = _mock_response({"Attributes": {}})
        with patch("dracs.redfish._get_credentials", return_value=("root", "pw")):
            with patch(
                "dracs.snmp.build_idrac_hostname",
                return_value="mgmt-server01.example.com",
            ):
                with patch("requests.get", return_value=resp):
                    result = collect_for_host_dynamic(
                        "server01.example.com", "Default", attr_defs
                    )
        assert result["ps_rapid_on"]["value"] is None

    @patch.dict(os.environ, {"DRACS_DNS_STRING": "mgmt-", "DRACS_DNS_MODE": "prefix"})
    def test_multiple_endpoints_each_make_one_request(self):
        """Attrs from different endpoint_types each trigger a separate HTTP call."""
        ps_attr = _attr(
            "ps_rapid_on", "system_oem_dell", "Attributes.ServerPwr.1.PSRapidOn"
        )
        sp_attr = _attr("sys_profile", "bios", "Attributes.SysProfile")

        responses = {
            "system_oem_dell": _mock_response(
                {"Attributes": {"ServerPwr.1.PSRapidOn": "Disabled"}}
            ),
            "bios": _mock_response(
                {"Attributes": {"SysProfile": "PerfPerWattOptimizedOs"}}
            ),
        }

        call_count = 0

        def side_effect(url, **kwargs):
            nonlocal call_count
            call_count += 1
            if "DellAttributes" in url:
                return responses["system_oem_dell"]
            if "Bios" in url:
                return responses["bios"]
            return _mock_response({})

        with patch("dracs.redfish._get_credentials", return_value=("root", "pw")):
            with patch(
                "dracs.snmp.build_idrac_hostname",
                return_value="mgmt-server01.example.com",
            ):
                with patch("requests.get", side_effect=side_effect):
                    result = collect_for_host_dynamic(
                        "server01.example.com", "Default", [ps_attr, sp_attr]
                    )
        assert call_count == 2
        assert result["ps_rapid_on"]["value"] == "Disabled"
        assert result["sys_profile"]["value"] == "PerfPerWattOptimizedOs"

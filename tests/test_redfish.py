"""Tests for src/dracs/redfish.py collection functions."""

import os
import ssl
import tempfile
from unittest.mock import MagicMock, patch

import pytest

import dracs.redfish as redfish_mod
from dracs.redfish import (
    DESIRED,
    _get_credentials,
    collect_all_for_host,
    collect_idrac_attributes,
    collect_idrac_hostname,
    collect_ps_rapid_on,
    collect_ssl_info,
    collect_sys_profile,
)


def _mock_response(json_data, status_code=200):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data
    resp.raise_for_status = MagicMock()
    if status_code >= 400:
        resp.raise_for_status.side_effect = Exception(f"HTTP {status_code}")
    return resp


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
                "server01.example.com": {"username": "customuser", "password": "custompw"}
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


class TestDesiredValues:
    def test_desired_constants(self):
        assert DESIRED["ps_rapid_on"] == "Disabled"
        assert DESIRED["dns_from_dhcp"] == "Enabled"
        assert DESIRED["ipmi_lan_enable"] == "Enabled"
        assert DESIRED["host_header_check"] == "Disabled"
        assert DESIRED["sys_profile"] == "PerfPerWattOptimizedOs"


class TestCollectPsRapidOn:
    def test_success(self):
        resp = _mock_response({"Attributes": {"ServerPwr.1.PSRapidOn": "Disabled"}})
        with patch("requests.get", return_value=resp):
            result = collect_ps_rapid_on("mgmt-host.example.com", "root", "secret")
        assert result == "Disabled"

    def test_returns_none_on_http_error(self):
        resp = _mock_response({}, status_code=401)
        with patch("requests.get", return_value=resp):
            result = collect_ps_rapid_on("mgmt-host.example.com", "root", "wrong")
        assert result is None

    def test_returns_none_on_connection_error(self):
        with patch("requests.get", side_effect=ConnectionError("unreachable")):
            result = collect_ps_rapid_on("mgmt-host.example.com", "root", "secret")
        assert result is None

    def test_missing_key_returns_none(self):
        resp = _mock_response({"Attributes": {}})
        with patch("requests.get", return_value=resp):
            result = collect_ps_rapid_on("mgmt-host.example.com", "root", "secret")
        assert result is None


class TestCollectIdracHostname:
    def test_success(self):
        resp = _mock_response({"HostName": "mgmt-server01.example.com"})
        with patch("requests.get", return_value=resp):
            result = collect_idrac_hostname("mgmt-server01.example.com", "root", "pw")
        assert result == "mgmt-server01.example.com"

    def test_returns_none_on_error(self):
        with patch("requests.get", side_effect=OSError("timeout")):
            result = collect_idrac_hostname("mgmt-host.example.com", "root", "pw")
        assert result is None


class TestCollectIdracAttributes:
    def test_returns_all_three_fields(self):
        resp = _mock_response(
            {
                "Attributes": {
                    "IPv4.1.DNSFromDHCP": "Enabled",
                    "IPMILan.1.Enable": "Enabled",
                    "WebServer.1.HostHeaderCheck": "Disabled",
                }
            }
        )
        with patch("requests.get", return_value=resp):
            result = collect_idrac_attributes("mgmt-host.example.com", "root", "pw")
        assert result["dns_from_dhcp"] == "Enabled"
        assert result["ipmi_lan_enable"] == "Enabled"
        assert result["host_header_check"] == "Disabled"

    def test_single_http_call(self):
        resp = _mock_response({"Attributes": {}})
        with patch("requests.get", return_value=resp) as mock_get:
            collect_idrac_attributes("mgmt-host.example.com", "root", "pw")
        assert mock_get.call_count == 1

    def test_returns_empty_dict_on_error(self):
        with patch("requests.get", side_effect=OSError("timeout")):
            result = collect_idrac_attributes("mgmt-host.example.com", "root", "pw")
        assert result == {}

    def test_omits_missing_keys(self):
        resp = _mock_response({"Attributes": {"IPv4.1.DNSFromDHCP": "Enabled"}})
        with patch("requests.get", return_value=resp):
            result = collect_idrac_attributes("mgmt-host.example.com", "root", "pw")
        assert "dns_from_dhcp" in result
        assert "ipmi_lan_enable" not in result
        assert "host_header_check" not in result


class TestCollectSysProfile:
    def test_success(self):
        resp = _mock_response({"Attributes": {"SysProfile": "PerfPerWattOptimizedOs"}})
        with patch("requests.get", return_value=resp):
            result = collect_sys_profile("mgmt-host.example.com", "root", "pw")
        assert result == "PerfPerWattOptimizedOs"

    def test_returns_none_on_error(self):
        with patch("requests.get", side_effect=OSError("unreachable")):
            result = collect_sys_profile("mgmt-host.example.com", "root", "pw")
        assert result is None


class TestCollectSslInfo:
    def _make_cert(self, self_signed=True, valid_name=True, days_until_expiry=90):
        """Build a minimal x509 cert object that cryptography can parse."""
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from datetime import datetime, timezone, timedelta

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


class TestCollectAllForHost:
    @patch.dict(
        os.environ,
        {"DRACS_DNS_STRING": "mgmt-", "DRACS_DNS_MODE": "prefix"},
    )
    def test_only_calls_enabled_endpoints(self):
        enabled = {
            "ps_rapid_on_enabled": True,
            "dns_from_dhcp_enabled": False,
            "ipmi_lan_enable_enabled": False,
            "host_header_check_enabled": False,
            "sys_profile_enabled": False,
            "ssl_enabled": False,
            "idrac_hostname_enabled": False,
        }
        with patch("dracs.redfish._get_credentials", return_value=("root", "pw")):
            with patch(
                "dracs.redfish.collect_ps_rapid_on", return_value="Disabled"
            ) as mock_ps:
                with patch(
                    "dracs.redfish.collect_idrac_attributes", return_value={}
                ) as mock_attrs:
                    with patch(
                        "dracs.redfish.collect_sys_profile", return_value=None
                    ) as mock_sys:
                        result = collect_all_for_host(
                            "server01.example.com", "Default", enabled
                        )
        mock_ps.assert_called_once()
        mock_attrs.assert_not_called()
        mock_sys.assert_not_called()
        assert result["ps_rapid_on"] == "Disabled"
        assert "collected_at" in result

    @patch.dict(
        os.environ,
        {"DRACS_DNS_STRING": "mgmt-", "DRACS_DNS_MODE": "prefix"},
    )
    def test_batches_idrac_attributes(self):
        enabled = {
            "ps_rapid_on_enabled": False,
            "dns_from_dhcp_enabled": True,
            "ipmi_lan_enable_enabled": True,
            "host_header_check_enabled": True,
            "sys_profile_enabled": False,
            "ssl_enabled": False,
            "idrac_hostname_enabled": False,
        }
        attrs_data = {
            "dns_from_dhcp": "Enabled",
            "ipmi_lan_enable": "Enabled",
            "host_header_check": "Disabled",
        }
        with patch("dracs.redfish._get_credentials", return_value=("root", "pw")):
            with patch(
                "dracs.redfish.collect_idrac_attributes", return_value=attrs_data
            ) as mock_attrs:
                result = collect_all_for_host(
                    "server01.example.com", "Default", enabled
                )
        mock_attrs.assert_called_once()
        assert result["dns_from_dhcp"] == "Enabled"
        assert result["ipmi_lan_enable"] == "Enabled"
        assert result["host_header_check"] == "Disabled"

    @patch.dict(
        os.environ,
        {"DRACS_DNS_STRING": "mgmt-", "DRACS_DNS_MODE": "prefix"},
    )
    def test_ssl_info_stored(self):
        enabled = {
            "ps_rapid_on_enabled": False,
            "dns_from_dhcp_enabled": False,
            "ipmi_lan_enable_enabled": False,
            "host_header_check_enabled": False,
            "sys_profile_enabled": False,
            "ssl_enabled": True,
            "idrac_hostname_enabled": False,
        }
        ssl_data = {"self_signed": True, "valid_name": False, "expiry": "2026-12-31"}
        with patch("dracs.redfish._get_credentials", return_value=("root", "pw")):
            with patch("dracs.redfish.collect_ssl_info", return_value=ssl_data):
                result = collect_all_for_host(
                    "server01.example.com", "Default", enabled
                )
        assert result["ssl_self_signed"] == 1
        assert result["ssl_valid_name"] == 0
        assert result["ssl_expiry"] == "2026-12-31"

    @patch.dict(
        os.environ,
        {"DRACS_DNS_STRING": "mgmt-", "DRACS_DNS_MODE": "prefix"},
    )
    def test_sys_profile_collected_when_enabled(self):
        enabled = {
            "ps_rapid_on_enabled": False,
            "dns_from_dhcp_enabled": False,
            "ipmi_lan_enable_enabled": False,
            "host_header_check_enabled": False,
            "sys_profile_enabled": True,
            "ssl_enabled": False,
            "idrac_hostname_enabled": False,
        }
        with patch("dracs.redfish._get_credentials", return_value=("root", "pw")):
            with patch(
                "dracs.redfish.collect_sys_profile", return_value="PerfPerWattOptimizedOs"
            ) as mock_sys:
                result = collect_all_for_host("server01.example.com", "Default", enabled)
        mock_sys.assert_called_once()
        assert result["sys_profile"] == "PerfPerWattOptimizedOs"

    @patch.dict(
        os.environ,
        {"DRACS_DNS_STRING": "mgmt-", "DRACS_DNS_MODE": "prefix"},
    )
    def test_idrac_hostname_collected_when_enabled(self):
        enabled = {
            "ps_rapid_on_enabled": False,
            "dns_from_dhcp_enabled": False,
            "ipmi_lan_enable_enabled": False,
            "host_header_check_enabled": False,
            "sys_profile_enabled": False,
            "ssl_enabled": False,
            "idrac_hostname_enabled": True,
        }
        with patch("dracs.redfish._get_credentials", return_value=("root", "pw")):
            with patch(
                "dracs.redfish.collect_idrac_hostname",
                return_value="mgmt-server01.example.com",
            ) as mock_hostname:
                result = collect_all_for_host("server01.example.com", "Default", enabled)
        mock_hostname.assert_called_once()
        assert result["idrac_hostname"] == "mgmt-server01.example.com"

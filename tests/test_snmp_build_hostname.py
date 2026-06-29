import os
import socket
from unittest.mock import patch

import pytest

from dracs.exceptions import ValidationError
from dracs.snmp import build_idrac_hostname, check_idrac_dns


class TestBuildIdracHostname:
    def test_prefix_mode(self):
        with patch.dict(
            os.environ,
            {"DRACS_DNS_STRING": "mgmt-", "DRACS_DNS_MODE": "prefix"},
        ):
            result = build_idrac_hostname("server01.example.com")
            assert result == "mgmt-server01.example.com"

    def test_suffix_mode_with_domain(self):
        with patch.dict(
            os.environ,
            {"DRACS_DNS_STRING": "-mm", "DRACS_DNS_MODE": "suffix"},
        ):
            result = build_idrac_hostname("server01.example.com")
            assert result == "server01-mm.example.com"

    def test_suffix_mode_no_domain(self):
        with patch.dict(
            os.environ,
            {"DRACS_DNS_STRING": "-mm", "DRACS_DNS_MODE": "suffix"},
        ):
            result = build_idrac_hostname("server01")
            assert result == "server01-mm"

    def test_missing_dns_string(self):
        with patch.dict(os.environ, {"DRACS_DNS_MODE": "prefix"}, clear=True):
            with pytest.raises(ValidationError, match="DRACS_DNS_STRING"):
                build_idrac_hostname("server01")

    def test_missing_dns_mode(self):
        with patch.dict(os.environ, {"DRACS_DNS_STRING": "mgmt-"}, clear=True):
            with pytest.raises(ValidationError, match="DRACS_DNS_MODE"):
                build_idrac_hostname("server01")

    def test_invalid_dns_mode(self):
        with patch.dict(
            os.environ,
            {"DRACS_DNS_STRING": "mgmt-", "DRACS_DNS_MODE": "invalid"},
        ):
            with pytest.raises(
                ValidationError, match="must be either 'prefix' or 'suffix'"
            ):
                build_idrac_hostname("server01")


class TestCheckIdracDns:
    def _prefix_env(self):
        return {"DRACS_DNS_STRING": "mgmt-", "DRACS_DNS_MODE": "prefix"}

    def test_success_returns_fqdn_and_none(self):
        with patch.dict(os.environ, self._prefix_env()):
            with patch("dracs.snmp.socket.getaddrinfo"):
                fqdn, err = check_idrac_dns("server01.example.com")
        assert fqdn == "mgmt-server01.example.com"
        assert err is None

    def test_dns_failure_returns_error_string(self):
        with patch.dict(os.environ, self._prefix_env()):
            with patch(
                "dracs.snmp.socket.getaddrinfo",
                side_effect=socket.gaierror("Name or service not known"),
            ):
                fqdn, err = check_idrac_dns("server01.example.com")
        assert fqdn == "mgmt-server01.example.com"
        assert "DNS resolution failed" in err
        assert "mgmt-server01.example.com" in err

    def test_validation_error_returns_error_string(self):
        with patch.dict(os.environ, {}, clear=True):
            fqdn, err = check_idrac_dns("server01.example.com")
        assert fqdn == "server01.example.com"
        assert err is not None
        assert "DRACS_DNS_STRING" in err

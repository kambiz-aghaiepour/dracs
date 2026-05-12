import os
from unittest.mock import patch

import pytest

from dracs.exceptions import ValidationError
from dracs.snmp import build_idrac_hostname


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

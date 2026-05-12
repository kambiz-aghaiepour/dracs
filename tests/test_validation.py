import pytest

from dracs.validation import validate_service_tag, validate_hostname, validate_version


class TestValidateServiceTag:
    def test_valid_5_char(self):
        assert validate_service_tag("ABCDE") is True

    def test_valid_7_char(self):
        assert validate_service_tag("ABC1234") is True

    def test_valid_6_char(self):
        assert validate_service_tag("ABC123") is True

    def test_lowercase_rejected(self):
        assert validate_service_tag("abc1234") is False

    def test_too_short(self):
        assert validate_service_tag("ABCD") is False

    def test_too_long(self):
        assert validate_service_tag("ABCD1234") is False

    def test_special_chars(self):
        assert validate_service_tag("ABC-123") is False

    def test_none(self):
        assert validate_service_tag(None) is False

    def test_empty_string(self):
        assert validate_service_tag("") is False

    def test_not_a_string(self):
        assert validate_service_tag(12345) is False


class TestValidateHostname:
    def test_simple_hostname(self):
        assert validate_hostname("server01") is True

    def test_fqdn(self):
        assert validate_hostname("server01.example.com") is True

    def test_with_hyphens(self):
        assert validate_hostname("my-server-01.example.com") is True

    def test_none(self):
        assert validate_hostname(None) is False

    def test_empty_string(self):
        assert validate_hostname("") is False

    def test_not_a_string(self):
        assert validate_hostname(12345) is False

    def test_too_long(self):
        assert validate_hostname("a" * 254) is False

    def test_invalid_chars(self):
        assert validate_hostname("server!01") is False

    def test_starts_with_hyphen(self):
        assert validate_hostname("-server01") is False


class TestValidateVersion:
    def test_valid_three_part(self):
        assert validate_version("2.1.0") is True

    def test_valid_four_part(self):
        assert validate_version("6.10.30.00") is True

    def test_valid_single_digit(self):
        assert validate_version("1") is True

    def test_none(self):
        assert validate_version(None) is False

    def test_empty_string(self):
        assert validate_version("") is False

    def test_not_a_string(self):
        assert validate_version(123) is False

    def test_letters_in_version(self):
        assert validate_version("2.1.0a") is False

    def test_trailing_dot(self):
        assert validate_version("2.1.") is False

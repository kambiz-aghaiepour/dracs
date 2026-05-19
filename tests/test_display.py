import json
import time

import pytest

from dracs.display import (
    filter_list_results,
    regex_like_match,
    render_list_host_only,
    render_list_json,
    render_list_table,
)


class TestFilterListResults:
    def test_bios_lt(self):
        results = [
            ("TAG1", "host1", "R660", "5.0.0", "2.1.0", "Jan 1, 2027", 1735689600),
            ("TAG2", "host2", "R660", "5.0.0", "2.5.0", "Jan 1, 2027", 1735689600),
            ("TAG3", "host3", "R660", "5.0.0", "3.0.0", "Jan 1, 2027", 1735689600),
        ]
        filtered = filter_list_results(
            results,
            None,
            "2.5.0",
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
        )
        assert len(filtered) == 1
        assert filtered[0][0] == "TAG1"

    def test_bios_eq(self):
        results = [
            ("TAG1", "host1", "R660", "5.0.0", "2.1.0", "Jan 1, 2027", 1735689600),
            ("TAG2", "host2", "R660", "5.0.0", "2.5.0", "Jan 1, 2027", 1735689600),
            ("TAG3", "host3", "R660", "5.0.0", "2.5.0", "Jan 1, 2027", 1735689600),
        ]
        filtered = filter_list_results(
            results,
            None,
            None,
            None,
            None,
            "2.5.0",
            None,
            None,
            None,
            None,
            None,
        )
        assert len(filtered) == 2
        assert filtered[0][0] == "TAG2"
        assert filtered[1][0] == "TAG3"

    def test_idrac_ge(self):
        results = [
            ("TAG1", "host1", "R660", "4.0.0", "2.1.0", "Jan 1, 2027", 1735689600),
            ("TAG2", "host2", "R660", "5.0.0", "2.5.0", "Jan 1, 2027", 1735689600),
            ("TAG3", "host3", "R660", "6.0.0", "3.0.0", "Jan 1, 2027", 1735689600),
        ]
        filtered = filter_list_results(
            results,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            "5.0.0",
            None,
            None,
        )
        assert len(filtered) == 2
        assert filtered[0][0] == "TAG2"
        assert filtered[1][0] == "TAG3"

    def test_idrac_le(self):
        results = [
            ("TAG1", "host1", "R660", "4.0.0", "2.1.0", "Jan 1, 2027", 1735689600),
            ("TAG2", "host2", "R660", "5.0.0", "2.5.0", "Jan 1, 2027", 1735689600),
        ]
        filtered = filter_list_results(
            results,
            None,
            None,
            None,
            None,
            None,
            "4.0.0",
            None,
            None,
            None,
            None,
        )
        assert len(filtered) == 1
        assert filtered[0][0] == "TAG1"

    def test_idrac_gt(self):
        results = [
            ("TAG1", "host1", "R660", "4.0.0", "2.1.0", "Jan 1, 2027", 1735689600),
            ("TAG2", "host2", "R660", "5.0.0", "2.5.0", "Jan 1, 2027", 1735689600),
        ]
        filtered = filter_list_results(
            results,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            "4.0.0",
            None,
        )
        assert len(filtered) == 1
        assert filtered[0][0] == "TAG2"

    def test_idrac_eq(self):
        results = [
            ("TAG1", "host1", "R660", "5.0.0", "2.1.0", "Jan 1, 2027", 1735689600),
            ("TAG2", "host2", "R660", "5.0.0", "2.5.0", "Jan 1, 2027", 1735689600),
        ]
        filtered = filter_list_results(
            results,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            "5.0.0",
        )
        assert len(filtered) == 2

    def test_bios_le(self):
        results = [
            ("TAG1", "host1", "R660", "5.0.0", "2.0.0", "Jan 1, 2027", 1735689600),
            ("TAG2", "host2", "R660", "5.0.0", "3.0.0", "Jan 1, 2027", 1735689600),
        ]
        filtered = filter_list_results(
            results,
            "2.0.0",
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
        )
        assert len(filtered) == 1
        assert filtered[0][0] == "TAG1"

    def test_bios_ge(self):
        results = [
            ("TAG1", "host1", "R660", "5.0.0", "2.0.0", "Jan 1, 2027", 1735689600),
            ("TAG2", "host2", "R660", "5.0.0", "3.0.0", "Jan 1, 2027", 1735689600),
        ]
        filtered = filter_list_results(
            results,
            None,
            None,
            "3.0.0",
            None,
            None,
            None,
            None,
            None,
            None,
            None,
        )
        assert len(filtered) == 1
        assert filtered[0][0] == "TAG2"

    def test_bios_gt(self):
        results = [
            ("TAG1", "host1", "R660", "5.0.0", "2.0.0", "Jan 1, 2027", 1735689600),
            ("TAG2", "host2", "R660", "5.0.0", "3.0.0", "Jan 1, 2027", 1735689600),
        ]
        filtered = filter_list_results(
            results,
            None,
            None,
            None,
            "2.0.0",
            None,
            None,
            None,
            None,
            None,
            None,
        )
        assert len(filtered) == 1
        assert filtered[0][0] == "TAG2"

    def test_no_filters_returns_empty(self):
        results = [
            ("TAG1", "host1", "R660", "5.0.0", "2.1.0", "Jan 1, 2027", 1735689600),
        ]
        filtered = filter_list_results(
            results,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
        )
        assert len(filtered) == 0

    def test_version_tuple_comparison(self):
        results = [
            ("TAG1", "host1", "R660", "5.0.0", "2.1.10", "Jan 1, 2027", 1735689600),
            ("TAG2", "host2", "R660", "5.0.0", "2.1.2", "Jan 1, 2027", 1735689600),
            ("TAG3", "host3", "R660", "5.0.0", "2.10.0", "Jan 1, 2027", 1735689600),
        ]
        filtered = filter_list_results(
            results,
            None,
            "2.10.0",
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
        )
        assert len(filtered) == 2


class TestRenderListTable:
    def test_renders_without_error(self, capsys):
        results = [
            ("TAG1", "host1", "R660", "7.0.0", "2.1.0", "Jan 1, 2027", 1893456000),
        ]
        render_list_table(results)
        captured = capsys.readouterr()
        assert "TAG1" in captured.out
        assert "host1" in captured.out
        assert "R660" in captured.out

    def test_empty_results(self, capsys):
        render_list_table([])
        captured = capsys.readouterr()
        assert "Service Tag" in captured.out


class TestRenderListJson:
    def test_valid_json(self, capsys):
        results = [
            ("TAG1", "host1", "R660", "7.0.0", "2.1.0", "Jan 1, 2027", 1893456000),
        ]
        render_list_json(results)
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert len(data) == 1
        assert data[0][0] == "TAG1"


class TestRenderListHostOnly:
    def test_prints_hostnames(self, capsys):
        results = [
            ("TAG1", "host1", "R660", "7.0.0", "2.1.0", "Jan 1, 2027", 1893456000),
            ("TAG2", "host2", "R660", "7.0.0", "2.1.0", "Jan 1, 2027", 1893456000),
        ]
        render_list_host_only(results)
        captured = capsys.readouterr()
        lines = captured.out.strip().split("\n")
        assert lines == ["host1", "host2"]


class TestRegexLikeMatch:
    def test_percent_wildcard(self):
        assert regex_like_match("server%", "server01") is True
        assert regex_like_match("server%", "server") is True
        assert regex_like_match("server%", "other01") is False

    def test_underscore_wildcard(self):
        assert regex_like_match("server0_", "server01") is True
        assert regex_like_match("server0_", "server001") is False

    def test_both_wildcards(self):
        assert regex_like_match("%web_", "prod-web1") is True
        assert regex_like_match("%web_", "prod-web12") is False

    def test_no_wildcards(self):
        assert regex_like_match("exact", "exact") is True
        assert regex_like_match("exact", "notexact") is False

    def test_special_chars_escaped(self):
        assert regex_like_match("host.name", "host.name") is True
        assert regex_like_match("host.name", "hostXname") is False

    def test_case_insensitive(self):
        assert regex_like_match("Server%", "server01") is True
        assert regex_like_match("SERVER%", "server01") is True

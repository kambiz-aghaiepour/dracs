import json
import time
from unittest.mock import MagicMock, patch

import pytest

from dracs_client.cli import (
    build_parser,
    client_side_filter,
    cmd_list,
    cmd_tsr,
    fetch_systems,
    fetch_tsr_list,
    main,
    systems_to_tuples,
)

SAMPLE_SYSTEMS = [
    {
        "svc_tag": "TAG001",
        "name": "server01.example.com",
        "model": "R660",
        "idrac_version": "7.0.0",
        "bios_version": "2.1.0",
        "exp_date": "Jan 1, 2027",
        "exp_epoch": 1893456000,
    },
    {
        "svc_tag": "TAG002",
        "name": "server02.example.com",
        "model": "R650",
        "idrac_version": "6.0.0",
        "bios_version": "1.5.0",
        "exp_date": "Jan 1, 2020",
        "exp_epoch": 1577836800,
    },
    {
        "svc_tag": "TAG003",
        "name": "web01.example.com",
        "model": "R660",
        "idrac_version": "7.0.0",
        "bios_version": "2.5.0",
        "exp_date": "Jun 1, 2027",
        "exp_epoch": 1811894400,
    },
]


class TestBuildParser:
    def test_list_subcommand(self):
        parser = build_parser()
        args = parser.parse_args(["list"])
        assert args.command == "list"

    def test_list_alias(self):
        parser = build_parser()
        args = parser.parse_args(["li"])
        assert args.command == "li"

    def test_list_with_svctag(self):
        parser = build_parser()
        args = parser.parse_args(["list", "-s", "ABC1234"])
        assert args.svctag == "ABC1234"

    def test_list_with_all_flags(self):
        parser = build_parser()
        args = parser.parse_args(["list", "-t", "host1", "--json", "--expired"])
        assert args.target == "host1"
        assert args.json is True
        assert args.expired is True

    def test_tsr_subcommand(self):
        parser = build_parser()
        args = parser.parse_args(["tsr", "--list", "-t", "host1"])
        assert args.command == "tsr"
        assert args.target == "host1"
        assert args.list is True

    def test_tsr_download(self):
        parser = build_parser()
        args = parser.parse_args(["tsr", "--download", "-t", "host1"])
        assert args.download is True

    def test_tsr_last_no_value(self):
        parser = build_parser()
        args = parser.parse_args(["tsr", "--list", "-t", "host1", "--last"])
        assert args.last == 1

    def test_tsr_last_with_value(self):
        parser = build_parser()
        args = parser.parse_args(["tsr", "--list", "-t", "host1", "--last", "5"])
        assert args.last == 5

    def test_tsr_last_default_none(self):
        parser = build_parser()
        args = parser.parse_args(["tsr", "--list", "-t", "host1"])
        assert args.last is None

    def test_tsr_last_rejects_non_integer(self):
        parser = build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["tsr", "--list", "-t", "host1", "--last", "abc"])

    def test_tsr_requires_target(self):
        parser = build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["tsr", "--list"])

    def test_tsr_requires_action(self):
        parser = build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["tsr", "-t", "host1"])

    def test_global_server_flag(self):
        parser = build_parser()
        args = parser.parse_args(["-s", "myserver", "list"])
        assert args.server == "myserver"

    def test_global_no_verify(self):
        parser = build_parser()
        args = parser.parse_args(["--no-verify", "list"])
        assert args.no_verify is True

    def test_insecure_alias(self):
        parser = build_parser()
        args = parser.parse_args(["--insecure", "list"])
        assert args.no_verify is True

    def test_bios_mutually_exclusive(self):
        parser = build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["list", "--bios_le", "2.0", "--bios_gt", "1.0"])

    def test_idrac_mutually_exclusive(self):
        parser = build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["list", "--idrac_le", "5.0", "--idrac_gt", "4.0"])


class TestSystemsToTuples:
    def test_converts_correctly(self):
        tuples = systems_to_tuples(SAMPLE_SYSTEMS[:1])
        assert len(tuples) == 1
        assert tuples[0] == (
            "TAG001",
            "server01.example.com",
            "R660",
            "7.0.0",
            "2.1.0",
            "Jan 1, 2027",
            1893456000,
        )


class TestClientSideFilter:
    def _tuples(self):
        return systems_to_tuples(SAMPLE_SYSTEMS)

    def test_filter_by_svctag(self):
        results = client_side_filter(
            self._tuples(), "TAG001", None, None, None, None, False
        )
        assert len(results) == 1
        assert results[0][0] == "TAG001"

    def test_filter_by_hostname(self):
        results = client_side_filter(
            self._tuples(),
            None,
            "server02.example.com",
            None,
            None,
            None,
            False,
        )
        assert len(results) == 1
        assert results[0][0] == "TAG002"

    def test_filter_by_model(self):
        results = client_side_filter(
            self._tuples(), None, None, "R660", None, None, False
        )
        assert len(results) == 2

    def test_filter_by_regex(self):
        results = client_side_filter(
            self._tuples(), None, None, None, "server%", None, False
        )
        assert len(results) == 2

    def test_filter_expired(self):
        results = client_side_filter(self._tuples(), None, None, None, None, None, True)
        assert len(results) == 1
        assert results[0][0] == "TAG002"

    def test_filter_expires_in(self):
        future_days = str(int((1893456000 - time.time()) / 86400) + 1)
        results = client_side_filter(
            self._tuples(), None, None, None, None, future_days, False
        )
        assert any(r[0] == "TAG001" for r in results)

    def test_no_filters(self):
        results = client_side_filter(
            self._tuples(), None, None, None, None, None, False
        )
        assert len(results) == 3

    def test_model_and_regex_combined(self):
        results = client_side_filter(
            self._tuples(), None, None, "R660", "server%", None, False
        )
        assert len(results) == 1
        assert results[0][0] == "TAG001"


class TestFetchSystems:
    def test_success(self):
        mock_resp = MagicMock()
        mock_resp.json.return_value = SAMPLE_SYSTEMS
        mock_resp.raise_for_status.return_value = None
        with patch("dracs_client.cli.requests.get", return_value=mock_resp):
            result = fetch_systems("https://server", True)
        assert len(result) == 3

    def test_ssl_error(self):
        import requests

        with patch(
            "dracs_client.cli.requests.get",
            side_effect=requests.exceptions.SSLError("cert error"),
        ):
            with pytest.raises(SystemExit):
                fetch_systems("https://server", True)

    def test_connection_error(self):
        import requests

        with patch(
            "dracs_client.cli.requests.get",
            side_effect=requests.exceptions.ConnectionError("refused"),
        ):
            with pytest.raises(SystemExit):
                fetch_systems("https://server", True)


class TestCmdList:
    def _mock_args(self, **kwargs):
        defaults = {
            "svctag": None,
            "target": None,
            "model": None,
            "regex": None,
            "expires_in": None,
            "expired": False,
            "json": False,
            "host_only": False,
            "bios_le": None,
            "bios_lt": None,
            "bios_ge": None,
            "bios_gt": None,
            "bios_eq": None,
            "idrac_le": None,
            "idrac_lt": None,
            "idrac_ge": None,
            "idrac_gt": None,
            "idrac_eq": None,
        }
        defaults.update(kwargs)
        args = MagicMock()
        for k, v in defaults.items():
            setattr(args, k, v)
        return args

    def test_list_json_output(self, capsys):
        mock_resp = MagicMock()
        mock_resp.json.return_value = SAMPLE_SYSTEMS
        mock_resp.raise_for_status.return_value = None
        with patch("dracs_client.cli.requests.get", return_value=mock_resp):
            cmd_list(self._mock_args(json=True), "https://server", True)
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert len(data) == 3

    def test_list_host_only(self, capsys):
        mock_resp = MagicMock()
        mock_resp.json.return_value = SAMPLE_SYSTEMS
        mock_resp.raise_for_status.return_value = None
        with patch("dracs_client.cli.requests.get", return_value=mock_resp):
            cmd_list(self._mock_args(host_only=True), "https://server", True)
        captured = capsys.readouterr()
        lines = captured.out.strip().split("\n")
        assert len(lines) == 3

    def test_list_table_output(self, capsys):
        mock_resp = MagicMock()
        mock_resp.json.return_value = SAMPLE_SYSTEMS
        mock_resp.raise_for_status.return_value = None
        with patch("dracs_client.cli.requests.get", return_value=mock_resp):
            cmd_list(self._mock_args(), "https://server", True)
        captured = capsys.readouterr()
        assert "TAG001" in captured.out

    def test_list_with_model_filter(self, capsys):
        mock_resp = MagicMock()
        mock_resp.json.return_value = SAMPLE_SYSTEMS
        mock_resp.raise_for_status.return_value = None
        with patch("dracs_client.cli.requests.get", return_value=mock_resp):
            cmd_list(
                self._mock_args(json=True, model="R650"),
                "https://server",
                True,
            )
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert len(data) == 1
        assert data[0][0] == "TAG002"

    def test_svctag_and_target_conflict(self, capsys):
        mock_resp = MagicMock()
        mock_resp.json.return_value = SAMPLE_SYSTEMS
        mock_resp.raise_for_status.return_value = None
        with patch("dracs_client.cli.requests.get", return_value=mock_resp):
            with pytest.raises(SystemExit):
                cmd_list(
                    self._mock_args(svctag="TAG1", target="host1"),
                    "https://server",
                    True,
                )


class TestFetchTsrList:
    def test_success(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "success": True,
            "entries": [
                {
                    "date": "2026/05/05 17:06:37",
                    "view_path": "20260505170637/",
                    "zip_file": "TSR20260505170637_TAG001.zip",
                }
            ],
        }
        mock_resp.raise_for_status.return_value = None
        with patch("dracs_client.cli.requests.get", return_value=mock_resp):
            entries = fetch_tsr_list("https://server", "host1", True)
        assert len(entries) == 1

    def test_host_not_found(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 404
        with patch("dracs_client.cli.requests.get", return_value=mock_resp):
            result = fetch_tsr_list("https://server", "unknown", True)
        assert result is None


class TestCmdTsr:
    def test_host_not_found(self, capsys):
        mock_resp = MagicMock()
        mock_resp.json.return_value = SAMPLE_SYSTEMS
        mock_resp.raise_for_status.return_value = None
        with patch("dracs_client.cli.requests.get", return_value=mock_resp):
            args = MagicMock()
            args.target = "nonexistent.example.com"
            args.list = True
            args.download = False
            args.last = None
            with pytest.raises(SystemExit):
                cmd_tsr(args, "https://server", True)
        captured = capsys.readouterr()
        assert "Target host not found" in captured.out

    def test_tsr_list(self, capsys):
        systems_resp = MagicMock()
        systems_resp.json.return_value = SAMPLE_SYSTEMS
        systems_resp.raise_for_status.return_value = None

        tsr_resp = MagicMock()
        tsr_resp.status_code = 200
        tsr_resp.json.return_value = {
            "success": True,
            "entries": [
                {
                    "date": "2026/05/05 17:06:37",
                    "view_path": "20260505170637/",
                    "zip_file": "TSR20260505170637_TAG001.zip",
                }
            ],
        }
        tsr_resp.raise_for_status.return_value = None

        def mock_get(url, **kwargs):
            if "/api/systems" in url:
                return systems_resp
            return tsr_resp

        with patch("dracs_client.cli.requests.get", side_effect=mock_get):
            args = MagicMock()
            args.target = "server01.example.com"
            args.list = True
            args.download = False
            args.last = None
            cmd_tsr(args, "https://server", True)

        captured = capsys.readouterr()
        assert "Date: 2026/05/05" in captured.out
        assert "View:" in captured.out
        assert "Download:" in captured.out
        assert "TSR" in captured.out

    def test_tsr_download(self, tmp_path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)

        systems_resp = MagicMock()
        systems_resp.json.return_value = SAMPLE_SYSTEMS
        systems_resp.raise_for_status.return_value = None

        tsr_resp = MagicMock()
        tsr_resp.status_code = 200
        tsr_resp.json.return_value = {
            "success": True,
            "entries": [
                {
                    "date": "2026/05/05 17:06:37",
                    "view_path": "20260505170637/",
                    "zip_file": "TSR20260505170637_TAG001.zip",
                }
            ],
        }
        tsr_resp.raise_for_status.return_value = None

        download_resp = MagicMock()
        download_resp.headers = {"content-length": "100"}
        download_resp.iter_content.return_value = [b"x" * 100]
        download_resp.raise_for_status.return_value = None

        call_count = 0

        def mock_get(url, **kwargs):
            nonlocal call_count
            call_count += 1
            if "/api/systems" in url:
                return systems_resp
            if "/api/tsr-list/" in url:
                return tsr_resp
            return download_resp

        with patch("dracs_client.cli.requests.get", side_effect=mock_get):
            args = MagicMock()
            args.target = "server01.example.com"
            args.list = False
            args.download = True
            cmd_tsr(args, "https://server", True)

        assert (tmp_path / "TSR20260505170637_TAG001.zip").exists()
        captured = capsys.readouterr()
        assert "Downloaded" in captured.out

    def test_tsr_no_collections(self, capsys):
        systems_resp = MagicMock()
        systems_resp.json.return_value = SAMPLE_SYSTEMS
        systems_resp.raise_for_status.return_value = None

        tsr_resp = MagicMock()
        tsr_resp.status_code = 200
        tsr_resp.json.return_value = {"success": True, "entries": []}
        tsr_resp.raise_for_status.return_value = None

        def mock_get(url, **kwargs):
            if "/api/systems" in url:
                return systems_resp
            return tsr_resp

        with patch("dracs_client.cli.requests.get", side_effect=mock_get):
            args = MagicMock()
            args.target = "server01.example.com"
            args.list = True
            args.download = False
            args.last = None
            cmd_tsr(args, "https://server", True)

        captured = capsys.readouterr()
        assert "No TSR collections found" in captured.out

    def test_tsr_list_last(self, capsys):
        systems_resp = MagicMock()
        systems_resp.json.return_value = SAMPLE_SYSTEMS
        systems_resp.raise_for_status.return_value = None

        tsr_resp = MagicMock()
        tsr_resp.status_code = 200
        tsr_resp.json.return_value = {
            "success": True,
            "entries": [
                {
                    "date": "2026/05/05 17:06:37",
                    "view_path": "20260505170637/",
                    "zip_file": "TSR20260505170637_TAG001.zip",
                },
                {
                    "date": "2026/05/01 12:00:00",
                    "view_path": "20260501120000/",
                    "zip_file": "TSR20260501120000_TAG001.zip",
                },
                {
                    "date": "2026/04/15 08:00:00",
                    "view_path": "20260415080000/",
                    "zip_file": "TSR20260415080000_TAG001.zip",
                },
            ],
        }
        tsr_resp.raise_for_status.return_value = None

        def mock_get(url, **kwargs):
            if "/api/systems" in url:
                return systems_resp
            return tsr_resp

        with patch("dracs_client.cli.requests.get", side_effect=mock_get):
            args = MagicMock()
            args.target = "server01.example.com"
            args.list = True
            args.download = False
            args.last = 1
            cmd_tsr(args, "https://server", True)

        captured = capsys.readouterr()
        assert "2026/05/05" in captured.out
        assert "2026/05/01" not in captured.out
        assert "2026/04/15" not in captured.out

    def test_tsr_list_last_n(self, capsys):
        systems_resp = MagicMock()
        systems_resp.json.return_value = SAMPLE_SYSTEMS
        systems_resp.raise_for_status.return_value = None

        tsr_resp = MagicMock()
        tsr_resp.status_code = 200
        tsr_resp.json.return_value = {
            "success": True,
            "entries": [
                {
                    "date": "2026/05/05 17:06:37",
                    "view_path": "20260505170637/",
                    "zip_file": "TSR20260505170637_TAG001.zip",
                },
                {
                    "date": "2026/05/01 12:00:00",
                    "view_path": "20260501120000/",
                    "zip_file": "TSR20260501120000_TAG001.zip",
                },
                {
                    "date": "2026/04/15 08:00:00",
                    "view_path": "20260415080000/",
                    "zip_file": "TSR20260415080000_TAG001.zip",
                },
            ],
        }
        tsr_resp.raise_for_status.return_value = None

        def mock_get(url, **kwargs):
            if "/api/systems" in url:
                return systems_resp
            return tsr_resp

        with patch("dracs_client.cli.requests.get", side_effect=mock_get):
            args = MagicMock()
            args.target = "server01.example.com"
            args.list = True
            args.download = False
            args.last = 2
            cmd_tsr(args, "https://server", True)

        captured = capsys.readouterr()
        assert "2026/05/05" in captured.out
        assert "2026/05/01" in captured.out
        assert "2026/04/15" not in captured.out

    def test_tsr_list_api_returns_none(self, capsys):
        systems_resp = MagicMock()
        systems_resp.json.return_value = SAMPLE_SYSTEMS
        systems_resp.raise_for_status.return_value = None

        tsr_resp = MagicMock()
        tsr_resp.status_code = 404

        def mock_get(url, **kwargs):
            if "/api/systems" in url:
                return systems_resp
            return tsr_resp

        with patch("dracs_client.cli.requests.get", side_effect=mock_get):
            args = MagicMock()
            args.target = "server01.example.com"
            args.list = True
            args.download = False
            args.last = None
            with pytest.raises(SystemExit):
                cmd_tsr(args, "https://server", True)
        captured = capsys.readouterr()
        assert "Target host not found" in captured.out

    def test_tsr_download_no_entries(self, capsys):
        systems_resp = MagicMock()
        systems_resp.json.return_value = SAMPLE_SYSTEMS
        systems_resp.raise_for_status.return_value = None

        tsr_resp = MagicMock()
        tsr_resp.status_code = 200
        tsr_resp.json.return_value = {"success": True, "entries": []}
        tsr_resp.raise_for_status.return_value = None

        def mock_get(url, **kwargs):
            if "/api/systems" in url:
                return systems_resp
            return tsr_resp

        with patch("dracs_client.cli.requests.get", side_effect=mock_get):
            args = MagicMock()
            args.target = "server01.example.com"
            args.list = False
            args.download = True
            with pytest.raises(SystemExit):
                cmd_tsr(args, "https://server", True)
        captured = capsys.readouterr()
        assert "No TSR collections found" in captured.out

    def test_tsr_download_api_returns_none(self, capsys):
        systems_resp = MagicMock()
        systems_resp.json.return_value = SAMPLE_SYSTEMS
        systems_resp.raise_for_status.return_value = None

        tsr_resp = MagicMock()
        tsr_resp.status_code = 404

        def mock_get(url, **kwargs):
            if "/api/systems" in url:
                return systems_resp
            return tsr_resp

        with patch("dracs_client.cli.requests.get", side_effect=mock_get):
            args = MagicMock()
            args.target = "server01.example.com"
            args.list = False
            args.download = True
            with pytest.raises(SystemExit):
                cmd_tsr(args, "https://server", True)
        captured = capsys.readouterr()
        assert "Target host not found" in captured.out

    def test_tsr_download_ssl_error(self, capsys):
        import requests as req

        systems_resp = MagicMock()
        systems_resp.json.return_value = SAMPLE_SYSTEMS
        systems_resp.raise_for_status.return_value = None

        tsr_resp = MagicMock()
        tsr_resp.status_code = 200
        tsr_resp.json.return_value = {
            "success": True,
            "entries": [
                {
                    "date": "2026/05/05 17:06:37",
                    "view_path": "20260505170637/",
                    "zip_file": "TSR20260505170637_TAG001.zip",
                }
            ],
        }
        tsr_resp.raise_for_status.return_value = None

        call_count = [0]

        def mock_get(url, **kwargs):
            call_count[0] += 1
            if "/api/systems" in url:
                return systems_resp
            if "/api/tsr-list/" in url:
                return tsr_resp
            raise req.exceptions.SSLError("cert error")

        with patch("dracs_client.cli.requests.get", side_effect=mock_get):
            args = MagicMock()
            args.target = "server01.example.com"
            args.list = False
            args.download = True
            with pytest.raises(SystemExit):
                cmd_tsr(args, "https://server", True)

    def test_tsr_download_connection_error(self, capsys):
        import requests as req

        systems_resp = MagicMock()
        systems_resp.json.return_value = SAMPLE_SYSTEMS
        systems_resp.raise_for_status.return_value = None

        tsr_resp = MagicMock()
        tsr_resp.status_code = 200
        tsr_resp.json.return_value = {
            "success": True,
            "entries": [
                {
                    "date": "2026/05/05 17:06:37",
                    "view_path": "20260505170637/",
                    "zip_file": "TSR20260505170637_TAG001.zip",
                }
            ],
        }
        tsr_resp.raise_for_status.return_value = None

        def mock_get(url, **kwargs):
            if "/api/systems" in url:
                return systems_resp
            if "/api/tsr-list/" in url:
                return tsr_resp
            raise req.exceptions.ConnectionError("refused")

        with patch("dracs_client.cli.requests.get", side_effect=mock_get):
            args = MagicMock()
            args.target = "server01.example.com"
            args.list = False
            args.download = True
            with pytest.raises(SystemExit):
                cmd_tsr(args, "https://server", True)

    def test_tsr_no_action(self, capsys):
        systems_resp = MagicMock()
        systems_resp.json.return_value = SAMPLE_SYSTEMS
        systems_resp.raise_for_status.return_value = None

        with patch("dracs_client.cli.requests.get", return_value=systems_resp):
            args = MagicMock()
            args.target = "server01.example.com"
            args.list = False
            args.download = False
            with pytest.raises(SystemExit):
                cmd_tsr(args, "https://server", True)
        captured = capsys.readouterr()
        assert "--list or --download" in captured.err


class TestFetchTsrListErrors:
    def test_api_error(self, capsys):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "success": False,
            "message": "Something went wrong",
        }
        mock_resp.raise_for_status.return_value = None
        with patch("dracs_client.cli.requests.get", return_value=mock_resp):
            with pytest.raises(SystemExit):
                fetch_tsr_list("https://server", "host1", True)

    def test_ssl_error(self):
        import requests as req

        with patch(
            "dracs_client.cli.requests.get",
            side_effect=req.exceptions.SSLError("cert"),
        ):
            with pytest.raises(SystemExit):
                fetch_tsr_list("https://server", "host1", True)

    def test_connection_error(self):
        import requests as req

        with patch(
            "dracs_client.cli.requests.get",
            side_effect=req.exceptions.ConnectionError("refused"),
        ):
            with pytest.raises(SystemExit):
                fetch_tsr_list("https://server", "host1", True)


class TestFetchSystemsHTTPError:
    def test_http_error(self):
        import requests as req

        mock_resp = MagicMock()
        mock_resp.raise_for_status.side_effect = req.exceptions.HTTPError("500")
        with patch("dracs_client.cli.requests.get", return_value=mock_resp):
            with pytest.raises(SystemExit):
                fetch_systems("https://server", True)


class TestCmdListConflicts:
    def _mock_args(self, **kwargs):
        defaults = {
            "svctag": None,
            "target": None,
            "model": None,
            "regex": None,
            "expires_in": None,
            "expired": False,
            "json": False,
            "host_only": False,
            "bios_le": None,
            "bios_lt": None,
            "bios_ge": None,
            "bios_gt": None,
            "bios_eq": None,
            "idrac_le": None,
            "idrac_lt": None,
            "idrac_ge": None,
            "idrac_gt": None,
            "idrac_eq": None,
        }
        defaults.update(kwargs)
        args = MagicMock()
        for k, v in defaults.items():
            setattr(args, k, v)
        return args

    def test_target_with_model_conflict(self, capsys):
        mock_resp = MagicMock()
        mock_resp.json.return_value = SAMPLE_SYSTEMS
        mock_resp.raise_for_status.return_value = None
        with patch("dracs_client.cli.requests.get", return_value=mock_resp):
            with pytest.raises(SystemExit):
                cmd_list(
                    self._mock_args(target="host1", model="R660"),
                    "https://server",
                    True,
                )
        captured = capsys.readouterr()
        assert "--model or --regex" in captured.err

    def test_list_with_version_filter(self, capsys):
        mock_resp = MagicMock()
        mock_resp.json.return_value = SAMPLE_SYSTEMS
        mock_resp.raise_for_status.return_value = None
        with patch("dracs_client.cli.requests.get", return_value=mock_resp):
            cmd_list(
                self._mock_args(json=True, bios_le="3.0.0"),
                "https://server",
                True,
            )
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert all(d[4] <= "3.0.0" for d in data)


class TestMainFunction:
    def test_main_list(self, capsys):
        mock_resp = MagicMock()
        mock_resp.json.return_value = SAMPLE_SYSTEMS
        mock_resp.raise_for_status.return_value = None
        with patch("dracs_client.config.DRACSRC_PATH") as mock_path:
            mock_path.exists.return_value = False
            with patch(
                "sys.argv",
                ["dracs-client", "-s", "server.example.com", "list", "--json"],
            ):
                with patch("dracs_client.cli.requests.get", return_value=mock_resp):
                    main()
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert len(data) == 3

    def test_main_tsr(self, capsys):
        systems_resp = MagicMock()
        systems_resp.json.return_value = SAMPLE_SYSTEMS
        systems_resp.raise_for_status.return_value = None

        tsr_resp = MagicMock()
        tsr_resp.status_code = 200
        tsr_resp.json.return_value = {"success": True, "entries": []}
        tsr_resp.raise_for_status.return_value = None

        def mock_get(url, **kwargs):
            if "/api/systems" in url:
                return systems_resp
            return tsr_resp

        with patch("dracs_client.config.DRACSRC_PATH") as mock_path:
            mock_path.exists.return_value = False
            with patch(
                "sys.argv",
                [
                    "dracs-client",
                    "-s",
                    "server.example.com",
                    "tsr",
                    "--list",
                    "-t",
                    "server01.example.com",
                ],
            ):
                with patch("dracs_client.cli.requests.get", side_effect=mock_get):
                    main()
        captured = capsys.readouterr()
        assert "No TSR collections found" in captured.out

    def test_main_no_verify(self, capsys):
        mock_resp = MagicMock()
        mock_resp.json.return_value = SAMPLE_SYSTEMS
        mock_resp.raise_for_status.return_value = None
        with patch("dracs_client.config.DRACSRC_PATH") as mock_path:
            mock_path.exists.return_value = False
            with patch(
                "sys.argv",
                [
                    "dracs-client",
                    "-s",
                    "server.example.com",
                    "--no-verify",
                    "list",
                    "--json",
                ],
            ):
                with patch("dracs_client.cli.requests.get", return_value=mock_resp):
                    main()
        captured = capsys.readouterr()
        assert "WARNING: SSL certificate verification is disabled" in captured.err

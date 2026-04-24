import asyncio
import tempfile
import os
from unittest.mock import patch

import pytest

from dracs import (
    read_host_list,
    discover_dell_systems_batch,
    ValidationError,
)


@pytest.fixture
def host_list_file():
    fd, path = tempfile.mkstemp(suffix=".txt")
    os.close(fd)
    yield path
    if os.path.exists(path):
        os.unlink(path)


def write_hosts(path, content):
    with open(path, "w") as f:
        f.write(content)


def test_read_host_list_basic(host_list_file):
    write_hosts(host_list_file, "server01.example.com\nserver02.example.com\n")
    hosts = read_host_list(host_list_file)
    assert hosts == ["server01.example.com", "server02.example.com"]


def test_read_host_list_strips_whitespace(host_list_file):
    write_hosts(host_list_file, "  server01.example.com  \n  server02.example.com\n")
    hosts = read_host_list(host_list_file)
    assert hosts == ["server01.example.com", "server02.example.com"]


def test_read_host_list_skips_empty_lines(host_list_file):
    write_hosts(host_list_file, "server01.example.com\n\n\nserver02.example.com\n\n")
    hosts = read_host_list(host_list_file)
    assert hosts == ["server01.example.com", "server02.example.com"]


def test_read_host_list_skips_comments(host_list_file):
    write_hosts(
        host_list_file,
        "# Production servers\nserver01.example.com\n# Staging\nserver02.example.com\n",
    )
    hosts = read_host_list(host_list_file)
    assert hosts == ["server01.example.com", "server02.example.com"]


def test_read_host_list_file_not_found():
    with pytest.raises(ValidationError, match="Host list file not found"):
        read_host_list("/nonexistent/path/hosts.txt")


def test_read_host_list_empty_file(host_list_file):
    write_hosts(host_list_file, "\n\n# just comments\n\n")
    with pytest.raises(ValidationError, match="Host list file is empty"):
        read_host_list(host_list_file)


def test_read_host_list_invalid_hostname(host_list_file):
    write_hosts(host_list_file, "server01.example.com\ninvalid host!!\n")
    with pytest.raises(ValidationError, match="Invalid hostname in host list"):
        read_host_list(host_list_file)


@patch("dracs.commands.dell_api_warranty_date")
@patch("dracs.commands.discover_dell_system")
@patch("dracs.commands.add_dell_warranty")
def test_batch_discover_with_add(
    mock_add, mock_discover, mock_warranty, host_list_file, capsys
):
    mock_discover.side_effect = [
        ("TAG0001", "R660"),
        ("TAG0002", "R650"),
    ]
    mock_warranty.return_value = {
        "TAG0001": (1700000000, "November 14, 2023"),
        "TAG0002": (1700000000, "November 14, 2023"),
    }
    mock_add.return_value = None

    hosts = ["server01.example.com", "server02.example.com"]
    asyncio.run(discover_dell_systems_batch(hosts, "/tmp/test.db", auto_add=True, show_discovered=True))

    assert mock_discover.call_count == 2
    mock_warranty.assert_called_once_with(["TAG0001", "TAG0002"])
    assert mock_add.call_count == 2

    output = capsys.readouterr().out
    assert "TAG0001" in output
    assert "TAG0002" in output
    assert "Succeeded: 2" in output
    assert "Total: 2 hosts" in output


@patch("dracs.commands.discover_dell_system")
@patch("dracs.commands.add_dell_warranty")
def test_batch_discover_without_add(mock_add, mock_discover, capsys):
    mock_discover.side_effect = [
        ("TAG0001", "R660"),
    ]

    hosts = ["server01.example.com"]
    asyncio.run(discover_dell_systems_batch(hosts, "/tmp/test.db", auto_add=False, show_discovered=True))

    assert mock_discover.call_count == 1
    assert mock_add.call_count == 0

    output = capsys.readouterr().out
    assert "Discovered" in output
    assert "Succeeded: 1" in output


@patch("dracs.commands.discover_dell_system")
def test_batch_discover_partial_failure(mock_discover, capsys):
    from dracs import SNMPError

    mock_discover.side_effect = [
        ("TAG0001", "R660"),
        SNMPError("Connection timeout"),
    ]

    hosts = ["server01.example.com", "server02.example.com"]
    asyncio.run(discover_dell_systems_batch(hosts, "/tmp/test.db", auto_add=False, show_discovered=False))

    output = capsys.readouterr().out
    assert "Succeeded: 1" in output
    assert "Failed: 1" in output
    assert "Total: 2 hosts" in output
    assert "Connection timeout" in output


@patch("dracs.commands.dell_api_warranty_date")
@patch("dracs.commands.discover_dell_system")
@patch("dracs.commands.add_dell_warranty")
def test_batch_discover_without_show_discovered(
    mock_add, mock_discover, mock_warranty, capsys
):
    mock_discover.side_effect = [
        ("TAG0001", "R660"),
        ("TAG0002", "R650"),
    ]
    mock_warranty.return_value = {
        "TAG0001": (1700000000, "November 14, 2023"),
        "TAG0002": (1700000000, "November 14, 2023"),
    }
    mock_add.return_value = None

    hosts = ["server01.example.com", "server02.example.com"]
    asyncio.run(discover_dell_systems_batch(hosts, "/tmp/test.db", auto_add=True, show_discovered=False))

    assert mock_discover.call_count == 2
    assert mock_add.call_count == 2

    output = capsys.readouterr().out
    assert "TAG0001" not in output
    assert "TAG0002" not in output
    assert "Hostname" not in output
    assert "Service Tag" not in output
    assert "Succeeded: 2" in output
    assert "Total: 2 hosts" in output

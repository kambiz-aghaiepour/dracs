import pytest
import socket
from unittest.mock import patch, AsyncMock

from pysnmp.error import PySnmpError

from dracs.exceptions import SNMPError
from dracs.snmp import get_snmp_value


@pytest.mark.asyncio
async def test_dns_resolution_failure():
    """Test that DNS resolution failures raise SNMPError with appropriate message."""
    with patch(
        "dracs.snmp.UdpTransportTarget.create",
        side_effect=socket.gaierror("[Errno -2] Name or service not known")
    ):
        with pytest.raises(SNMPError) as exc_info:
            await get_snmp_value(
                "nonexistent.invalid.hostname",
                "public",
                "1.3.6.1.4.1.674.10892.5.1.3.2.0"
            )

        assert "DNS resolution failed" in str(exc_info.value)
        assert "nonexistent.invalid.hostname" in str(exc_info.value)


@pytest.mark.asyncio
async def test_pysnmp_dns_resolution_failure():
    """Test that PySnmpError with DNS failure is caught and converted to SNMPError."""
    with patch(
        "dracs.snmp.UdpTransportTarget.create",
        side_effect=PySnmpError(
            "Bad IPv4/UDP transport address mgmt-bar.example.com@161: "
            "[Errno -5] No address associated with hostname caused by "
            "<class 'socket.gaierror'>: [Errno -5] No address associated with hostname"
        )
    ):
        with pytest.raises(SNMPError) as exc_info:
            await get_snmp_value(
                "mgmt-bar.example.com",
                "public",
                "1.3.6.1.4.1.674.10892.5.1.3.2.0"
            )

        assert "DNS resolution failed" in str(exc_info.value)
        assert "mgmt-bar.example.com" in str(exc_info.value)


@pytest.mark.asyncio
async def test_pysnmp_other_transport_error():
    """Test that PySnmpError with non-DNS errors is caught and converted to SNMPError."""
    with patch(
        "dracs.snmp.UdpTransportTarget.create",
        side_effect=PySnmpError("Invalid transport configuration")
    ):
        with pytest.raises(SNMPError) as exc_info:
            await get_snmp_value(
                "10.0.0.1",
                "public",
                "1.3.6.1.4.1.674.10892.5.1.3.2.0"
            )

        assert "SNMP transport error" in str(exc_info.value)
        assert "10.0.0.1" in str(exc_info.value)


@pytest.mark.asyncio
async def test_network_error_on_connection():
    """Test that network errors during connection raise SNMPError."""
    with patch(
        "dracs.snmp.UdpTransportTarget.create",
        side_effect=OSError("Network is unreachable")
    ):
        with pytest.raises(SNMPError) as exc_info:
            await get_snmp_value(
                "10.0.0.1",
                "public",
                "1.3.6.1.4.1.674.10892.5.1.3.2.0"
            )

        assert "Network error connecting to" in str(exc_info.value)
        assert "10.0.0.1" in str(exc_info.value)


@pytest.mark.asyncio
async def test_successful_snmp_query_with_valid_host():
    """Test that successful SNMP queries work correctly."""
    mock_transport = AsyncMock()
    mock_varbind = AsyncMock()
    mock_varbind.__getitem__ = lambda self, idx: (
        AsyncMock() if idx == 0 else AsyncMock(prettyPrint=lambda: "TEST_VALUE")
    )

    with patch(
        "dracs.snmp.UdpTransportTarget.create",
        return_value=mock_transport
    ):
        with patch(
            "dracs.snmp.get_cmd",
            return_value=(None, None, None, [mock_varbind])
        ):
            result = await get_snmp_value(
                "valid.hostname.com",
                "public",
                "1.3.6.1.4.1.674.10892.5.1.3.2.0"
            )

            assert result == "TEST_VALUE"

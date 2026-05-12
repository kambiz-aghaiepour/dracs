from unittest.mock import patch, AsyncMock, MagicMock

import pytest

from dracs.snmp import get_snmp_value


@pytest.mark.asyncio
async def test_snmp_error_indication_returns_none():
    mock_transport = AsyncMock()

    with patch(
        "dracs.snmp.UdpTransportTarget.create",
        return_value=mock_transport,
    ):
        with patch(
            "dracs.snmp.get_cmd",
            return_value=("Some error indication", None, None, []),
        ):
            result = await get_snmp_value("host", "public", "1.3.6.1.2.1.1.1.0")

    assert result is None


@pytest.mark.asyncio
async def test_snmp_error_status_returns_none():
    mock_transport = AsyncMock()
    mock_error_status = MagicMock()
    mock_error_status.prettyPrint.return_value = "noSuchObject"
    mock_error_status.__bool__ = lambda self: True

    with patch(
        "dracs.snmp.UdpTransportTarget.create",
        return_value=mock_transport,
    ):
        with patch(
            "dracs.snmp.get_cmd",
            return_value=(None, mock_error_status, 1, []),
        ):
            result = await get_snmp_value("host", "public", "1.3.6.1.2.1.1.1.0")

    assert result is None

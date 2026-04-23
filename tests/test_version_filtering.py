import pytest

from dracs import filter_list_results

@pytest.mark.asyncio
async def test_filter_bios_lt():
    """Test filtering systems with BIOS version less than target."""
    results = [
        ('TAG1', 'host1', 'R660', '5.0.0', '2.1.0', 'Jan 1, 2027', 1735689600),
        ('TAG2', 'host2', 'R660', '5.0.0', '2.5.0', 'Jan 1, 2027', 1735689600),
        ('TAG3', 'host3', 'R660', '5.0.0', '3.0.0', 'Jan 1, 2027', 1735689600),
    ]

    filtered = await filter_list_results(
        results,
        bios_le=None, bios_lt='2.5.0', bios_ge=None, bios_gt=None, bios_eq=None,
        idrac_le=None, idrac_lt=None, idrac_ge=None, idrac_gt=None, idrac_eq=None
    )

    assert len(filtered) == 1
    assert filtered[0][0] == 'TAG1'

@pytest.mark.asyncio
async def test_filter_bios_eq():
    """Test filtering systems with BIOS version equal to target."""
    results = [
        ('TAG1', 'host1', 'R660', '5.0.0', '2.1.0', 'Jan 1, 2027', 1735689600),
        ('TAG2', 'host2', 'R660', '5.0.0', '2.5.0', 'Jan 1, 2027', 1735689600),
        ('TAG3', 'host3', 'R660', '5.0.0', '2.5.0', 'Jan 1, 2027', 1735689600),
    ]

    filtered = await filter_list_results(
        results,
        bios_le=None, bios_lt=None, bios_ge=None, bios_gt=None, bios_eq='2.5.0',
        idrac_le=None, idrac_lt=None, idrac_ge=None, idrac_gt=None, idrac_eq=None
    )

    assert len(filtered) == 2
    assert filtered[0][0] == 'TAG2'
    assert filtered[1][0] == 'TAG3'

@pytest.mark.asyncio
async def test_filter_idrac_ge():
    """Test filtering systems with iDRAC version greater than or equal to target."""
    results = [
        ('TAG1', 'host1', 'R660', '4.0.0', '2.1.0', 'Jan 1, 2027', 1735689600),
        ('TAG2', 'host2', 'R660', '5.0.0', '2.5.0', 'Jan 1, 2027', 1735689600),
        ('TAG3', 'host3', 'R660', '6.0.0', '3.0.0', 'Jan 1, 2027', 1735689600),
    ]

    filtered = await filter_list_results(
        results,
        bios_le=None, bios_lt=None, bios_ge=None, bios_gt=None, bios_eq=None,
        idrac_le=None, idrac_lt=None, idrac_ge='5.0.0', idrac_gt=None, idrac_eq=None
    )

    assert len(filtered) == 2
    assert filtered[0][0] == 'TAG2'
    assert filtered[1][0] == 'TAG3'

@pytest.mark.asyncio
async def test_filter_no_filters():
    """Test that no filters returns empty list."""
    results = [
        ('TAG1', 'host1', 'R660', '5.0.0', '2.1.0', 'Jan 1, 2027', 1735689600),
    ]

    filtered = await filter_list_results(
        results,
        bios_le=None, bios_lt=None, bios_ge=None, bios_gt=None, bios_eq=None,
        idrac_le=None, idrac_lt=None, idrac_ge=None, idrac_gt=None, idrac_eq=None
    )

    assert len(filtered) == 0

@pytest.mark.asyncio
async def test_filter_version_tuple_comparison():
    """Test that version comparison works correctly with different formats."""
    results = [
        ('TAG1', 'host1', 'R660', '5.0.0', '2.1.10', 'Jan 1, 2027', 1735689600),
        ('TAG2', 'host2', 'R660', '5.0.0', '2.1.2', 'Jan 1, 2027', 1735689600),
        ('TAG3', 'host3', 'R660', '5.0.0', '2.10.0', 'Jan 1, 2027', 1735689600),
    ]

    filtered = await filter_list_results(
        results,
        bios_le=None, bios_lt='2.10.0', bios_ge=None, bios_gt=None, bios_eq=None,
        idrac_le=None, idrac_lt=None, idrac_ge=None, idrac_gt=None, idrac_eq=None
    )

    assert len(filtered) == 2

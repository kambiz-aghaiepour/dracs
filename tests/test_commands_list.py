import asyncio
import calendar
import time
from datetime import datetime
from unittest.mock import patch, AsyncMock

import pytest

from dracs.commands import (
    list_dell_warranty,
    refresh_dell_warranty,
    warranty_duration_matches,
)
from dracs.db import db_initialize, upsert_system
from dracs.exceptions import DatabaseError, ValidationError


class TestListDellWarranty:
    def _setup_db(self, temp_db):
        db_initialize(temp_db)
        future_epoch = int(time.time()) + (365 * 86400)
        past_epoch = int(time.time()) - (365 * 86400)
        soon_epoch = int(time.time()) + (30 * 86400)

        upsert_system(
            temp_db,
            "TAG001",
            "alpha.example.com",
            "R660",
            "7.0.0",
            "2.1.0",
            "Jan 1, 2027",
            future_epoch,
        )
        upsert_system(
            temp_db,
            "TAG002",
            "bravo.example.com",
            "R650",
            "6.0.0",
            "1.5.0",
            "Jan 1, 2020",
            past_epoch,
        )
        upsert_system(
            temp_db,
            "TAG003",
            "charlie.example.com",
            "R660",
            "7.1.0",
            "2.2.0",
            "Feb 15, 2025",
            soon_epoch,
        )

    def test_list_all(self, temp_db, capsys):
        self._setup_db(temp_db)

        asyncio.run(
            list_dell_warranty(
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
                None,
                None,
                None,
                None,
                None,
                False,
                False,
                False,
                temp_db,
            )
        )

        output = capsys.readouterr().out
        assert "TAG001" in output

    def test_list_json(self, temp_db, capsys):
        self._setup_db(temp_db)

        asyncio.run(
            list_dell_warranty(
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
                None,
                None,
                None,
                None,
                None,
                False,
                True,
                False,
                temp_db,
            )
        )

        import json

        output = capsys.readouterr().out
        data = json.loads(output)
        assert len(data) == 3

    def test_list_host_only(self, temp_db, capsys):
        self._setup_db(temp_db)

        asyncio.run(
            list_dell_warranty(
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
                None,
                None,
                None,
                None,
                None,
                False,
                False,
                True,
                temp_db,
            )
        )

        output = capsys.readouterr().out
        lines = [l for l in output.strip().split("\n") if l]
        assert len(lines) == 3
        assert "alpha.example.com" in lines

    def test_list_by_svctag(self, temp_db, capsys):
        self._setup_db(temp_db)

        asyncio.run(
            list_dell_warranty(
                "TAG001",
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
                None,
                None,
                None,
                None,
                False,
                True,
                False,
                temp_db,
            )
        )

        import json

        output = capsys.readouterr().out
        data = json.loads(output)
        assert len(data) == 1

    def test_list_by_hostname(self, temp_db, capsys):
        self._setup_db(temp_db)

        asyncio.run(
            list_dell_warranty(
                None,
                "alpha.example.com",
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
                None,
                None,
                None,
                False,
                True,
                False,
                temp_db,
            )
        )

        import json

        output = capsys.readouterr().out
        data = json.loads(output)
        assert len(data) == 1

    def test_list_by_model(self, temp_db, capsys):
        self._setup_db(temp_db)

        asyncio.run(
            list_dell_warranty(
                None,
                None,
                "R660",
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
                None,
                None,
                False,
                True,
                False,
                temp_db,
            )
        )

        import json

        output = capsys.readouterr().out
        data = json.loads(output)
        assert len(data) == 2

    def test_list_by_regex(self, temp_db, capsys):
        self._setup_db(temp_db)

        asyncio.run(
            list_dell_warranty(
                None,
                None,
                None,
                "alpha%",
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
                None,
                False,
                True,
                False,
                temp_db,
            )
        )

        import json

        output = capsys.readouterr().out
        data = json.loads(output)
        assert len(data) == 1

    def test_list_model_and_regex(self, temp_db, capsys):
        self._setup_db(temp_db)

        asyncio.run(
            list_dell_warranty(
                None,
                None,
                "R660",
                "%example%",
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
                None,
                False,
                True,
                False,
                temp_db,
            )
        )

        import json

        output = capsys.readouterr().out
        data = json.loads(output)
        assert len(data) == 2

    def test_list_expired(self, temp_db, capsys):
        self._setup_db(temp_db)

        asyncio.run(
            list_dell_warranty(
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
                None,
                None,
                None,
                None,
                None,
                True,
                True,
                False,
                temp_db,
            )
        )

        import json

        output = capsys.readouterr().out
        data = json.loads(output)
        assert len(data) == 1
        assert data[0][0] == "TAG002"

    def test_list_expires_in(self, temp_db, capsys):
        self._setup_db(temp_db)

        asyncio.run(
            list_dell_warranty(
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
                None,
                None,
                None,
                None,
                "60",
                False,
                True,
                False,
                temp_db,
            )
        )

        import json

        output = capsys.readouterr().out
        data = json.loads(output)
        assert len(data) == 1
        assert data[0][0] == "TAG003"

    def test_list_with_bios_filter(self, temp_db, capsys):
        self._setup_db(temp_db)

        asyncio.run(
            list_dell_warranty(
                None,
                None,
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
                None,
                None,
                None,
                False,
                True,
                False,
                temp_db,
            )
        )

        import json

        output = capsys.readouterr().out
        data = json.loads(output)
        assert len(data) == 1
        assert data[0][0] == "TAG002"

    def test_list_svctag_and_hostname_raises(self, temp_db):
        db_initialize(temp_db)

        with pytest.raises(ValidationError, match="Cannot specify both"):
            asyncio.run(
                list_dell_warranty(
                    "TAG001",
                    "host1",
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
                    None,
                    None,
                    None,
                    False,
                    False,
                    False,
                    temp_db,
                )
            )

    def test_list_hostname_and_model_raises(self, temp_db):
        db_initialize(temp_db)

        with pytest.raises(ValidationError, match="Cannot specify"):
            asyncio.run(
                list_dell_warranty(
                    None,
                    "host1",
                    "R660",
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
                    None,
                    None,
                    False,
                    False,
                    False,
                    temp_db,
                )
            )


class TestListDuration:
    def _setup_db(self, temp_db):
        db_initialize(temp_db)

        def epoch(y, m, d):
            return calendar.timegm(datetime(y, m, d).utctimetuple())

        # 1096 days (~3 years, includes leap day) -> matches 3
        upsert_system(
            temp_db,
            "TAG3Y",
            "three.example.com",
            "R660",
            "7.0.0",
            "2.1.0",
            "Jan 1, 2025",
            epoch(2025, 1, 1),
            start_date="January 1, 2022",
        )
        upsert_system(
            temp_db,
            "TAG5Y",
            "five.example.com",
            "R660",
            "7.0.0",
            "2.1.0",
            "Jan 1, 2025",
            epoch(2025, 1, 1),
            start_date="January 1, 2020",
        )
        upsert_system(
            temp_db,
            "TAG3YEDGE",
            "edge.example.com",
            "R660",
            "7.0.0",
            "2.1.0",
            "Jan 30, 2025",
            epoch(2025, 1, 30),
            start_date="January 1, 2022",
        )
        upsert_system(
            temp_db,
            "TAG3YOUT",
            "out.example.com",
            "R660",
            "7.0.0",
            "2.1.0",
            "Jan 31, 2025",
            epoch(2025, 1, 31),
            start_date="January 1, 2022",
        )
        upsert_system(
            temp_db,
            "TAGNOSTART",
            "nostart.example.com",
            "R660",
            "7.0.0",
            "2.1.0",
            "Jan 1, 2025",
            epoch(2025, 1, 1),
        )

    def _list(self, temp_db, capsys, duration):
        import json

        asyncio.run(
            list_dell_warranty(
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
                None,
                None,
                None,
                None,
                None,
                False,
                True,
                False,
                temp_db,
                duration=duration,
            )
        )
        return json.loads(capsys.readouterr().out)

    def test_list_duration_3(self, temp_db, capsys):
        self._setup_db(temp_db)
        data = self._list(temp_db, capsys, 3)
        assert [r[0] for r in data] == ["TAG3YEDGE", "TAG3Y"]

    def test_list_duration_5(self, temp_db, capsys):
        self._setup_db(temp_db)
        data = self._list(temp_db, capsys, 5)
        assert [r[0] for r in data] == ["TAG5Y"]

    def test_list_no_duration_shows_all(self, temp_db, capsys):
        self._setup_db(temp_db)
        data = self._list(temp_db, capsys, None)
        assert len(data) == 5


class TestWarrantyDurationMatches:
    def test_3y_with_leap_day(self):
        end = calendar.timegm((2025, 1, 1, 0, 0, 0, 0, 0, 0))
        assert warranty_duration_matches("January 1, 2022", end, 3)

    def test_5y(self):
        end = calendar.timegm((2025, 1, 1, 0, 0, 0, 0, 0, 0))
        assert warranty_duration_matches("January 1, 2020", end, 5)

    def test_outside_margin(self):
        end = calendar.timegm((2025, 1, 31, 0, 0, 0, 0, 0, 0))
        assert not warranty_duration_matches("January 1, 2022", end, 3)

    def test_legacy_space_padded_date(self):
        # Rows written before the f-string change are space-padded
        end = calendar.timegm((2023, 1, 1, 0, 0, 0, 0, 0, 0))
        assert warranty_duration_matches("January  1, 2020", end, 3)

    def test_missing_start_date(self):
        assert not warranty_duration_matches(None, 1735689600, 3)

    def test_unparseable_start_date(self):
        assert not warranty_duration_matches("garbage", 1735689600, 3)


class TestRefreshDellWarranty:
    @patch("dracs.commands.dell_api_warranty_date")
    @patch("dracs.commands.get_snmp_value", new_callable=AsyncMock)
    @patch("dracs.commands.build_idrac_hostname", return_value="mgmt-server01")
    def test_refresh_by_hostname(
        self, mock_build, mock_snmp, mock_api, temp_db, capsys
    ):
        db_initialize(temp_db)
        upsert_system(
            temp_db,
            "TAG001",
            "server01",
            "R660",
            "7.0.0",
            "2.1.0",
            "Jan 1, 2027",
            1735689600,
        )

        mock_snmp.side_effect = ["2.2.0", "7.1.0", "PowerEdge R660"]
        mock_api.return_value = {
            "TAG001": (1893456000, "Jan 1, 2030", "September 1, 2020")
        }

        import os

        with patch.dict(os.environ, {"SNMP_COMMUNITY": "public"}):
            asyncio.run(refresh_dell_warranty(None, "server01", temp_db, verbose=True))

        output = capsys.readouterr().out
        assert "done." in output

    def test_refresh_no_args_raises(self, temp_db):
        db_initialize(temp_db)

        with pytest.raises(ValidationError, match="Either service tag or hostname"):
            asyncio.run(refresh_dell_warranty(None, None, temp_db))

    def test_refresh_not_found_raises(self, temp_db):
        db_initialize(temp_db)

        with pytest.raises(DatabaseError, match="No matching record"):
            asyncio.run(refresh_dell_warranty("NOTHERE", None, temp_db))

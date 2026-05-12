import asyncio
import time
from unittest.mock import patch, AsyncMock

import pytest

from dracs.commands import list_dell_warranty, refresh_dell_warranty
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
        mock_api.return_value = {"TAG001": (1893456000, "Jan 1, 2030")}

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

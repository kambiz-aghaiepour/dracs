import asyncio
import sys
from unittest.mock import patch, AsyncMock

import pytest

from dracs.cli import main
from dracs.exceptions import ValidationError


def run_main_with_args(args):
    with patch.object(sys, "argv", ["dracs"] + args):
        asyncio.run(main())


class TestMainAdd:
    @patch("dracs.commands.add_dell_warranty", new_callable=AsyncMock)
    @patch("dracs.cli.db_initialize")
    def test_add_command(self, mock_db, mock_add, temp_db):
        mock_add.return_value = None
        run_main_with_args(
            ["add", "-s", "ABC1234", "-t", "server01.example.com", "-m", "R660"]
        )
        mock_add.assert_called_once()
        call_args = mock_add.call_args[0]
        assert call_args[0] == "ABC1234"
        assert call_args[1] == "server01.example.com"
        assert call_args[2] == "R660"

    @patch("dracs.commands.add_dell_warranty", new_callable=AsyncMock)
    @patch("dracs.cli.db_initialize")
    def test_add_alias(self, mock_db, mock_add, temp_db):
        mock_add.return_value = None
        run_main_with_args(
            ["a", "-s", "ABC1234", "-t", "server01.example.com", "-m", "R660"]
        )
        mock_add.assert_called_once()

    @patch("dracs.cli.db_initialize")
    def test_add_invalid_svctag(self, mock_db, temp_db):
        with pytest.raises(ValidationError, match="Invalid service tag"):
            run_main_with_args(
                ["add", "-s", "bad!", "-t", "server01.example.com", "-m", "R660"]
            )

    @patch("dracs.cli.db_initialize")
    def test_add_invalid_hostname(self, mock_db, temp_db):
        with pytest.raises(ValidationError, match="Invalid hostname"):
            run_main_with_args(
                ["add", "-s", "ABC1234", "-t", "bad host!!", "-m", "R660"]
            )


class TestMainEdit:
    @patch("dracs.commands.edit_dell_warranty", new_callable=AsyncMock)
    @patch("dracs.cli.db_initialize")
    def test_edit_by_svctag(self, mock_db, mock_edit, temp_db):
        mock_edit.return_value = None
        run_main_with_args(["edit", "-s", "ABC1234", "-m", "R760"])
        mock_edit.assert_called_once()
        call_args = mock_edit.call_args[0]
        assert call_args[0] == "ABC1234"
        assert call_args[2] == "R760"

    @patch("dracs.commands.edit_dell_warranty", new_callable=AsyncMock)
    @patch("dracs.cli.db_initialize")
    def test_edit_by_target(self, mock_db, mock_edit, temp_db):
        mock_edit.return_value = None
        run_main_with_args(["edit", "-t", "server01.example.com", "--idrac", "--bios"])
        mock_edit.assert_called_once()
        call_args = mock_edit.call_args[0]
        assert call_args[1] == "server01.example.com"
        assert call_args[3] is True  # idrac
        assert call_args[4] is True  # bios


class TestMainLookup:
    @patch("dracs.commands.lookup_dell_warranty", new_callable=AsyncMock)
    @patch("dracs.cli.db_initialize")
    def test_lookup_by_svctag(self, mock_db, mock_lookup, temp_db):
        mock_lookup.return_value = None
        run_main_with_args(["lookup", "-s", "ABC1234", "--full"])
        mock_lookup.assert_called_once()
        call_args = mock_lookup.call_args[0]
        assert call_args[0] == "ABC1234"
        assert call_args[4] is True  # full

    @patch("dracs.commands.lookup_dell_warranty", new_callable=AsyncMock)
    @patch("dracs.cli.db_initialize")
    def test_lookup_by_target(self, mock_db, mock_lookup, temp_db):
        mock_lookup.return_value = None
        run_main_with_args(["lookup", "-t", "server01.example.com"])
        mock_lookup.assert_called_once()

    @patch("dracs.commands.lookup_dell_warranty", new_callable=AsyncMock)
    @patch("dracs.cli.db_initialize")
    def test_lookup_alias(self, mock_db, mock_lookup, temp_db):
        mock_lookup.return_value = None
        run_main_with_args(["l", "-s", "ABC1234"])
        mock_lookup.assert_called_once()


class TestMainList:
    @patch("dracs.commands.list_dell_warranty", new_callable=AsyncMock)
    @patch("dracs.cli.db_initialize")
    def test_list_no_args(self, mock_db, mock_list, temp_db):
        mock_list.return_value = None
        run_main_with_args(["list"])
        mock_list.assert_called_once()

    @patch("dracs.commands.list_dell_warranty", new_callable=AsyncMock)
    @patch("dracs.cli.db_initialize")
    def test_list_with_model(self, mock_db, mock_list, temp_db):
        mock_list.return_value = None
        run_main_with_args(["list", "-m", "R660"])
        mock_list.assert_called_once()
        call_args = mock_list.call_args[0]
        assert call_args[2] == "R660"

    @patch("dracs.commands.list_dell_warranty", new_callable=AsyncMock)
    @patch("dracs.cli.db_initialize")
    def test_list_alias(self, mock_db, mock_list, temp_db):
        mock_list.return_value = None
        run_main_with_args(["li", "--json"])
        mock_list.assert_called_once()

    @patch("dracs.commands.list_dell_warranty", new_callable=AsyncMock)
    @patch("dracs.cli.db_initialize")
    def test_list_with_bios_filter(self, mock_db, mock_list, temp_db):
        mock_list.return_value = None
        run_main_with_args(["list", "--bios_lt", "2.5.0"])
        mock_list.assert_called_once()

    @patch("dracs.commands.list_dell_warranty", new_callable=AsyncMock)
    @patch("dracs.cli.db_initialize")
    def test_list_with_idrac_filter(self, mock_db, mock_list, temp_db):
        mock_list.return_value = None
        run_main_with_args(["list", "--idrac_ge", "6.0.0"])
        mock_list.assert_called_once()

    @patch("dracs.commands.list_dell_warranty", new_callable=AsyncMock)
    @patch("dracs.cli.db_initialize")
    def test_list_with_expires_in(self, mock_db, mock_list, temp_db):
        mock_list.return_value = None
        run_main_with_args(["list", "--expires_in", "30"])
        mock_list.assert_called_once()

    @patch("dracs.commands.list_dell_warranty", new_callable=AsyncMock)
    @patch("dracs.cli.db_initialize")
    def test_list_host_only(self, mock_db, mock_list, temp_db):
        mock_list.return_value = None
        run_main_with_args(["list", "--host-only"])
        mock_list.assert_called_once()


class TestMainRemove:
    @patch("dracs.commands.remove_dell_warranty", new_callable=AsyncMock)
    @patch("dracs.cli.db_initialize")
    def test_remove_by_svctag(self, mock_db, mock_remove, temp_db):
        mock_remove.return_value = None
        run_main_with_args(["remove", "-s", "ABC1234"])
        mock_remove.assert_called_once()

    @patch("dracs.commands.remove_dell_warranty", new_callable=AsyncMock)
    @patch("dracs.cli.db_initialize")
    def test_remove_by_target(self, mock_db, mock_remove, temp_db):
        mock_remove.return_value = None
        run_main_with_args(["remove", "-t", "server01.example.com"])
        mock_remove.assert_called_once()

    @patch("dracs.commands.remove_dell_warranty", new_callable=AsyncMock)
    @patch("dracs.cli.db_initialize")
    def test_remove_alias(self, mock_db, mock_remove, temp_db):
        mock_remove.return_value = None
        run_main_with_args(["r", "-s", "ABC1234"])
        mock_remove.assert_called_once()


class TestMainRefresh:
    @patch("dracs.commands.refresh_dell_warranty", new_callable=AsyncMock)
    @patch("dracs.cli.db_initialize")
    def test_refresh_by_svctag(self, mock_db, mock_refresh, temp_db):
        mock_refresh.return_value = None
        run_main_with_args(["refresh", "-s", "ABC1234"])
        mock_refresh.assert_called_once()

    @patch("dracs.commands.refresh_dell_warranty", new_callable=AsyncMock)
    @patch("dracs.cli.db_initialize")
    def test_refresh_by_target(self, mock_db, mock_refresh, temp_db):
        mock_refresh.return_value = None
        run_main_with_args(["refresh", "-t", "server01.example.com"])
        mock_refresh.assert_called_once()

    @patch("dracs.commands.refresh_by_model", new_callable=AsyncMock)
    @patch("dracs.cli.db_initialize")
    def test_refresh_by_model(self, mock_db, mock_refresh, temp_db):
        mock_refresh.return_value = None
        run_main_with_args(["refresh", "-m", "R660"])
        mock_refresh.assert_called_once()
        assert mock_refresh.call_args[0][0] == "R660"

    @patch("dracs.commands.refresh_all_systems", new_callable=AsyncMock)
    @patch("dracs.cli.db_initialize")
    def test_refresh_all(self, mock_db, mock_refresh, temp_db):
        mock_refresh.return_value = None
        run_main_with_args(["refresh", "-a"])
        mock_refresh.assert_called_once()

    @patch("dracs.commands.refresh_dell_warranty", new_callable=AsyncMock)
    @patch("dracs.cli.db_initialize")
    def test_refresh_alias(self, mock_db, mock_refresh, temp_db):
        mock_refresh.return_value = None
        run_main_with_args(["rf", "-s", "ABC1234"])
        mock_refresh.assert_called_once()


class TestMainDiscover:
    @patch("dracs.commands.add_dell_warranty", new_callable=AsyncMock)
    @patch("dracs.commands.discover_dell_system", new_callable=AsyncMock)
    @patch("dracs.cli.db_initialize")
    def test_discover_with_add_flag(self, mock_db, mock_discover, mock_add, temp_db):
        mock_discover.return_value = ("TAG001", "R660")
        mock_add.return_value = None
        run_main_with_args(["discover", "-t", "server01.example.com", "--add"])
        mock_discover.assert_called_once()
        mock_add.assert_called_once()

    @patch("dracs.commands.discover_dell_system", new_callable=AsyncMock)
    @patch("dracs.cli.db_initialize")
    @patch("builtins.input", return_value="n")
    def test_discover_user_declines(
        self, mock_input, mock_db, mock_discover, temp_db, capsys
    ):
        mock_discover.return_value = ("TAG001", "R660")
        run_main_with_args(["discover", "-t", "server01.example.com"])
        output = capsys.readouterr().out
        assert "System not added to database" in output

    @patch("dracs.commands.add_dell_warranty", new_callable=AsyncMock)
    @patch("dracs.commands.discover_dell_system", new_callable=AsyncMock)
    @patch("dracs.cli.db_initialize")
    @patch("builtins.input", return_value="y")
    def test_discover_user_accepts(
        self, mock_input, mock_db, mock_discover, mock_add, temp_db, capsys
    ):
        mock_discover.return_value = ("TAG001", "R660")
        mock_add.return_value = None
        run_main_with_args(["discover", "-t", "server01.example.com"])
        mock_add.assert_called_once()

    @patch("dracs.commands.discover_dell_system", new_callable=AsyncMock)
    @patch("dracs.cli.db_initialize")
    def test_discover_alias(self, mock_db, mock_discover, temp_db):
        mock_discover.return_value = ("TAG001", "R660")
        with patch("builtins.input", return_value="n"):
            run_main_with_args(["d", "-t", "server01.example.com"])
        mock_discover.assert_called_once()

    @patch("dracs.commands.discover_dell_systems_batch", new_callable=AsyncMock)
    @patch("dracs.cli.read_host_list", return_value=["host1", "host2"])
    @patch("dracs.cli.db_initialize")
    def test_discover_host_list_with_add(self, mock_db, mock_read, mock_batch, temp_db):
        mock_batch.return_value = None
        run_main_with_args(["discover", "--host-list", "/tmp/hosts.txt", "--add"])
        mock_batch.assert_called_once()

    @patch("dracs.commands.discover_dell_systems_batch", new_callable=AsyncMock)
    @patch("dracs.cli.read_host_list", return_value=["host1", "host2"])
    @patch("dracs.cli.db_initialize")
    @patch("builtins.input", return_value="y")
    def test_discover_host_list_user_accepts(
        self, mock_input, mock_db, mock_read, mock_batch, temp_db, capsys
    ):
        mock_batch.return_value = None
        run_main_with_args(["discover", "--host-list", "/tmp/hosts.txt"])
        mock_batch.assert_called_once()
        output = capsys.readouterr().out
        assert "Discovering 2 hosts" in output


class TestMainGlobalArgs:
    @patch("dracs.commands.list_dell_warranty", new_callable=AsyncMock)
    @patch("dracs.cli.db_initialize")
    def test_custom_warranty_path(self, mock_db, mock_list, temp_db):
        mock_list.return_value = None
        run_main_with_args(["-w", "/custom/path.db", "list"])
        mock_db.assert_called_with("/custom/path.db")

    @patch("dracs.commands.list_dell_warranty", new_callable=AsyncMock)
    @patch("dracs.cli.db_initialize")
    def test_debug_flag(self, mock_db, mock_list, temp_db):
        mock_list.return_value = None
        import dracs.commands as commands

        run_main_with_args(["-d", "list"])
        assert commands.debug_output is True

    @patch("dracs.commands.list_dell_warranty", new_callable=AsyncMock)
    @patch("dracs.cli.db_initialize")
    def test_default_warranty_path(self, mock_db, mock_list, temp_db):
        mock_list.return_value = None
        run_main_with_args(["list"])
        call_arg = mock_db.call_args[0][0]
        assert call_arg.endswith("warranty.db")

    @patch("dracs.commands.list_dell_warranty", new_callable=AsyncMock)
    @patch("dracs.cli.db_initialize")
    def test_no_svctag_sets_none(self, mock_db, mock_list, temp_db):
        mock_list.return_value = None
        run_main_with_args(["list"])
        call_args = mock_list.call_args[0]
        assert call_args[0] is None  # target_tag

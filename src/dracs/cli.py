import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

import dracs.commands as commands
from dracs.db import db_initialize
from dracs.exceptions import (
    APIError,
    DatabaseError,
    DracsError,
    SNMPError,
    ValidationError,
)
from dracs.validation import (
    read_host_list,
    validate_hostname,
    validate_service_tag,
)

logger = logging.getLogger(__name__)


def setup_logging(debug: bool = False, verbose: bool = False) -> None:
    """
    Configure logging with appropriate level and format.

    Args:
        debug: Enable DEBUG level logging (most detailed)
        verbose: Enable INFO level logging (progress messages)

    If neither flag is set, only WARNING and ERROR messages are shown.
    """
    if debug:
        level = logging.DEBUG
    elif verbose:
        level = logging.INFO
    else:
        level = logging.WARNING

    logging.basicConfig(
        level=level,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


class CustomParser(argparse.ArgumentParser):
    """
    Extended ArgumentParser to provide customized error messages
    when no sub-command (add, edit, etc.) is provided.
    """

    def error(self, message):
        # Check if the error is specifically about the missing subparser
        if "required: command" in message:
            print("\nError: One of the following modes must be used:\n")
            print("    add (a)         Add a system")
            print("    discover (d)    Discover system via SNMP")
            print("    edit (e)        Edit a system")
            print("    lookup (l)      Lookup a system")
            print("    refresh (rf)    Refresh SNMP and warranty data")
            print("    remove (r)      Remove a system")
            print("    list (li)       List systems\n")
            self.print_usage()
            sys.exit(2)
        # Fall back to default behavior for other errors
        super().error(message)


async def main() -> None:
    """
    Main entry point. Configures CLI arguments, subparsers for commands,
    handles global debug settings, and routes execution to the appropriate logic.
    """
    parser = CustomParser(description="System Warranty Database Manager")

    # Global Optional Arguments
    parser.add_argument("-d", "--debug", action="store_true", help="Enable debug mode")
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="Enable verbose output"
    )
    parser.add_argument(
        "-w",
        "--warranty",
        help="Database URL (e.g. sqlite:///warranty.db, postgresql://user:pass@host/db) "
        "or path to SQLite file",
    )

    # Create Subparsers (This makes -a, -e, -l, -r mutually exclusive)
    subparsers = parser.add_subparsers(dest="command", required=True)

    # --- ADD COMMAND ---
    parser_add = subparsers.add_parser("add", aliases=["a"], help="Add a system")
    parser_add.add_argument("-s", "--svctag", required=True, help="Service tag")
    parser_add.add_argument("-t", "--target", required=True, help="DNS Hostname")
    parser_add.add_argument(
        "-m", "--model", required=True, help="System model (e.g. R660)"
    )

    # --- DISCOVER COMMAND ---
    parser_discover = subparsers.add_parser(
        "discover", aliases=["d"], help="Discover system via SNMP"
    )
    discover_target_group = parser_discover.add_mutually_exclusive_group(required=True)
    discover_target_group.add_argument(
        "-t", "--target", help="DNS Hostname to discover"
    )
    discover_target_group.add_argument(
        "--host-list", help="Path to file containing hostnames, one per line"
    )
    parser_discover.add_argument(
        "--add",
        action="store_true",
        help="Automatically add to database without prompting",
    )
    parser_discover.add_argument(
        "--show-discovered",
        action="store_true",
        help="Show detailed table of discovered systems",
    )

    # --- EDIT COMMAND ---
    parser_edit = subparsers.add_parser("edit", aliases=["e"], help="Edit a system")
    # Mutually exclusive group: Must have tag OR target
    edit_group = parser_edit.add_mutually_exclusive_group(required=True)
    edit_group.add_argument("-s", "--svctag", help="Service tag to edit")
    edit_group.add_argument("-t", "--target", help="Target hostname to edit")
    # Optional flag for edit
    parser_edit.add_argument("-m", "--model", help="New model name")
    parser_edit.add_argument(
        "--idrac", action="store_true", help="Update iDRAC version"
    )
    parser_edit.add_argument("--bios", action="store_true", help="Update BIOS version")

    # --- LOOKUP COMMAND ---
    parser_lookup = subparsers.add_parser(
        "lookup", aliases=["l"], help="Lookup a system"
    )
    lookup_group = parser_lookup.add_mutually_exclusive_group(required=True)
    lookup_group.add_argument("-s", "--svctag", help="Service tag to find")
    lookup_group.add_argument("-t", "--target", help="Target hostname to find")
    # Specific optional flags for lookup only
    parser_lookup.add_argument(
        "--idrac", action="store_true", help="Print iDRAC version"
    )
    parser_lookup.add_argument("--bios", action="store_true", help="Print BIOS version")
    parser_lookup.add_argument("--full", action="store_true", help="Print All fields")

    # --- LIST COMMAND ---
    parser_list = subparsers.add_parser("list", aliases=["li"], help="List systems")
    # Specific optional flags for list only
    parser_list.add_argument("-s", "--svctag", help="Service tag to find")
    parser_list.add_argument("-t", "--target", help="Target hostname to find")
    parser_list.add_argument("-m", "--model", help="Target model to list")
    parser_list.add_argument("--expires_in", help="List hosts that expire in N days")
    parser_list.add_argument(
        "--expired", action="store_true", help="List hosts with expired warranties"
    )
    parser_list.add_argument(
        "--json", action="store_true", help="Print list results in json format"
    )
    parser_list.add_argument(
        "--host-only", action="store_true", help="Print only hostname (one per line)"
    )
    parser_list.add_argument("--regex", help="Target hostname regex to list")
    # bios args
    list_bios_group = parser_list.add_mutually_exclusive_group(required=False)
    list_bios_group.add_argument(
        "--bios_le", help="Target hostname with BIOS less than or equal to list"
    )
    list_bios_group.add_argument(
        "--bios_lt", help="Target hostname with BIOS less than to list"
    )
    list_bios_group.add_argument(
        "--bios_ge", help="Target hostname with BIOS greater than or equal to list"
    )
    list_bios_group.add_argument(
        "--bios_gt", help="Target hostname with BIOS greater than to list"
    )
    list_bios_group.add_argument(
        "--bios_eq", help="Target hostname with BIOS equal to to list"
    )
    # idrac args
    list_idrac_group = parser_list.add_mutually_exclusive_group(required=False)
    list_idrac_group.add_argument(
        "--idrac_le", help="Target hostname with iDRAC less than or equal to list"
    )
    list_idrac_group.add_argument(
        "--idrac_lt", help="Target hostname with iDRAC less than to list"
    )
    list_idrac_group.add_argument(
        "--idrac_ge", help="Target hostname with iDRAC greater than or equal to list"
    )
    list_idrac_group.add_argument(
        "--idrac_gt", help="Target hostname with iDRAC greater than to list"
    )
    list_idrac_group.add_argument(
        "--idrac_eq", help="Target hostname with iDRAC equal to to list"
    )

    # --- REFRESH COMMAND ---
    parser_refresh = subparsers.add_parser(
        "refresh", aliases=["rf"], help="Refresh SNMP and warranty data for a system"
    )
    refresh_group = parser_refresh.add_mutually_exclusive_group(required=True)
    refresh_group.add_argument(
        "-s", "--svctag", help="Service tag of system to refresh"
    )
    refresh_group.add_argument(
        "-t", "--target", help="Target hostname of system to refresh"
    )

    # --- REMOVE COMMAND ---
    parser_remove = subparsers.add_parser(
        "remove", aliases=["r"], help="Remove a system"
    )
    remove_group = parser_remove.add_mutually_exclusive_group(required=True)
    remove_group.add_argument("-s", "--svctag", help="Service tag to remove")
    remove_group.add_argument("-t", "--target", help="Target hostname to remove")

    args = parser.parse_args()

    # Set up logging based on command-line flags
    setup_logging(debug=args.debug, verbose=args.verbose)

    # Handling Global Debug
    global debug
    debug = args.debug
    commands.debug_output = debug

    if hasattr(args, "svctag") and args.svctag:
        target_tag = args.svctag.upper()
        if not validate_service_tag(target_tag):
            raise ValidationError(
                f"Invalid service tag format: {args.svctag}. "
                "Service tags should be 5-7 alphanumeric characters"
            )
    else:
        target_tag = None

    if hasattr(args, "target") and args.target:
        if not validate_hostname(args.target):
            raise ValidationError(
                f"Invalid hostname format: {args.target}. "
                "Hostnames should contain only letters, numbers, hyphens, and periods"
            )

    if args.warranty:
        warranty = args.warranty
    else:
        warranty = str(Path(__file__).resolve().parent) + "/warranty.db"

    db_initialize(warranty)

    # Logic Routing
    if args.command in ["discover", "d"]:
        if args.host_list:
            hosts = read_host_list(args.host_list)
            auto_add = hasattr(args, "add") and args.add
            show_discovered = hasattr(args, "show_discovered") and args.show_discovered
            if not auto_add:
                print(f"Discovering {len(hosts)} hosts from {args.host_list}...")
                response = input(
                    "Add discovered systems to database? (y/n): "
                ).strip().lower()
                auto_add = response in ["y", "yes"]
            await commands.discover_dell_systems_batch(hosts, warranty, auto_add, show_discovered)
        else:
            # Single host discover
            discovered_tag, discovered_model = await commands.discover_dell_system(
                args.target, warranty
            )

            # Check if --add flag was provided
            if hasattr(args, "add") and args.add:
                # Auto-add without prompting
                logger.info("Auto-adding system to database (--add flag provided)")
                await commands.add_dell_warranty(
                    discovered_tag, args.target, discovered_model, warranty
                )
            else:
                # Prompt user
                print("\nDiscovered system:")
                print(f"  Hostname:    {args.target}")
                print(f"  Service Tag: {discovered_tag}")
                print(f"  Model:       {discovered_model}")
                print()
                response = input("Add to database? (y/n): ").strip().lower()

                if response in ["y", "yes"]:
                    logger.info("User confirmed, adding system to database")
                    await commands.add_dell_warranty(
                        discovered_tag, args.target, discovered_model, warranty
                    )
                else:
                    logger.info("User declined, not adding to database")
                    print("System not added to database")

    elif args.command in ["add", "a"]:
        await commands.add_dell_warranty(target_tag, args.target, args.model, warranty)
    elif args.command in ["edit", "e"]:
        await commands.edit_dell_warranty(
            target_tag, args.target, args.model, args.idrac, args.bios, warranty
        )
    elif args.command in ["lookup", "l"]:
        await commands.lookup_dell_warranty(
            target_tag, args.target, args.idrac, args.bios, args.full, warranty
        )
    elif args.command in ["refresh", "rf"]:
        await commands.refresh_dell_warranty(target_tag, args.target, warranty)
    elif args.command in ["remove", "r"]:
        await commands.remove_dell_warranty(target_tag, args.target, warranty)
    elif args.command in ["list", "li"]:
        await commands.list_dell_warranty(
            target_tag,
            args.target,
            args.model,
            args.regex,
            args.bios_le,
            args.bios_lt,
            args.bios_ge,
            args.bios_gt,
            args.bios_eq,
            args.idrac_le,
            args.idrac_lt,
            args.idrac_ge,
            args.idrac_gt,
            args.idrac_eq,
            args.expires_in,
            args.expired,
            args.json,
            args.host_only,
            warranty,
        )


def main_cli() -> None:
    load_dotenv()
    commands.debug_output = False
    try:
        debug = os.environ["DEBUG"]
        if debug == "true":
            commands.debug_output = True
    except KeyError:
        commands.debug_output = False

    try:
        asyncio.run(main())
    except ValidationError as e:
        logger.error(f"Validation Error: {e}")
        sys.exit(1)
    except DatabaseError as e:
        logger.error(f"Database Error: {e}")
        sys.exit(1)
    except APIError as e:
        logger.error(f"API Error: {e}")
        sys.exit(1)
    except SNMPError as e:
        logger.error(f"SNMP Error: {e}")
        sys.exit(1)
    except DracsError as e:
        logger.error(f"Error: {e}")
        sys.exit(1)

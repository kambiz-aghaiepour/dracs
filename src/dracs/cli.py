import argparse
import asyncio
import getpass
import logging
import os
import shutil
import sys
from pathlib import Path

import dracs.commands as commands
from dracs.audit import audit_log
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

debug = False

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
            print("    init (i)        Initialize config files")
            print("    lookup (l)      Lookup a system")
            print("    refresh (rf)    Refresh SNMP and warranty data")
            print("    remove (r)      Remove a system")
            print("    list (li)       List systems")
            print("    tsr (t)         TSR operations")
            print("    jobs (j)        Job queue operations")
            print("    idracjobs (ij)  iDRAC job queue operations")
            print("    fw              Firmware operations")
            print("    bios            BIOS operations")
            print("    user (u)        User management\n")
            self.print_usage()
            sys.exit(2)
        # Fall back to default behavior for other errors
        super().error(message)


EXAMPLES_DIR = Path(__file__).parent / "examples"

EXAMPLE_FILES = {
    ".env.example": ".env.example",
    "drac-passwords.ini.example": "drac-passwords.ini.example",
    "BIOS-filename.ini.example": "BIOS-filename.ini.example",
}


def init_config_files() -> None:
    examples_dir = EXAMPLES_DIR
    created = []
    skipped = []

    for src_name, dst_name in EXAMPLE_FILES.items():
        src = examples_dir / src_name
        dst = Path(dst_name)

        if dst.exists():
            skipped.append(dst_name)
            continue

        if not src.exists():
            print(f"Warning: bundled example {src_name} not found", file=sys.stderr)
            continue

        shutil.copy2(src, dst)
        created.append(dst_name)

    if created:
        print("Created:")
        for f in created:
            print(f"  {f}")

    if skipped:
        print("Skipped (already exist):")
        for f in skipped:
            print(f"  {f}")

    if created:
        print("\nCopy .env.example to .env and configure it before starting.")


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
    parser.add_argument(
        "--site",
        help="Site name to operate on (defaults to primary site)",
    )

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
    refresh_group.add_argument(
        "-m", "--model", help="Refresh all systems of specified model"
    )
    refresh_group.add_argument(
        "-a", "--all", action="store_true", help="Refresh all systems in database"
    )
    parser_refresh.add_argument(
        "-v", "--verbose", action="store_true", help="Show detailed refresh progress"
    )

    # --- REMOVE COMMAND ---
    parser_remove = subparsers.add_parser(
        "remove", aliases=["r"], help="Remove a system"
    )
    remove_group = parser_remove.add_mutually_exclusive_group(required=True)
    remove_group.add_argument("-s", "--svctag", help="Service tag to remove")
    remove_group.add_argument("-t", "--target", help="Target hostname to remove")

    # --- INIT COMMAND ---
    subparsers.add_parser(
        "init",
        aliases=["i"],
        help="Initialize config files in current directory",
    )

    # --- TSR COMMAND ---
    parser_tsr = subparsers.add_parser("tsr", aliases=["t"], help="TSR operations")
    parser_tsr.add_argument("-t", "--target", required=True, help="Target hostname")
    tsr_action = parser_tsr.add_mutually_exclusive_group(required=True)
    tsr_action.add_argument("--list", action="store_true", help="List TSR collections")
    tsr_action.add_argument(
        "--download", action="store_true", help="Download most recent TSR"
    )
    tsr_action.add_argument(
        "--generate", action="store_true", help="Generate new TSR collection"
    )
    tsr_action.add_argument(
        "--status", action="store_true", help="Check TSR collection status"
    )
    parser_tsr.add_argument(
        "--last",
        nargs="?",
        const=1,
        type=int,
        default=None,
        help="Show only the last N TSRs (default: 1 if no value given)",
    )

    # --- JOBS COMMAND ---
    parser_jobs = subparsers.add_parser(
        "jobs", aliases=["j"], help="Job queue operations"
    )
    jobs_action = parser_jobs.add_mutually_exclusive_group(required=True)
    jobs_action.add_argument("--list", action="store_true", help="List jobs")
    jobs_action.add_argument(
        "--clear", action="store_true", help="Purge completed jobs"
    )
    jobs_action.add_argument(
        "--cancel", type=int, metavar="JOB_ID", help="Cancel a pending job"
    )
    parser_jobs.add_argument(
        "--all",
        action="store_true",
        help="Include completed/failed jobs in listing",
    )
    parser_jobs.add_argument(
        "--failed",
        action="store_true",
        help="Show only failed jobs (implies --all)",
    )

    # --- IDRACJOBS COMMAND ---
    parser_ij = subparsers.add_parser(
        "idracjobs", aliases=["ij"], help="iDRAC job queue operations"
    )
    ij_action = parser_ij.add_mutually_exclusive_group(required=True)
    ij_action.add_argument("--list", action="store_true", help="List iDRAC job queue")
    ij_action.add_argument("--clear", action="store_true", help="Clear iDRAC job queue")
    parser_ij.add_argument("-t", "--target", help="Target hostname")
    parser_ij.add_argument("-m", "--model", help="Target model")
    parser_ij.add_argument("--all", action="store_true", help="All hosts")
    parser_ij.add_argument(
        "-f", "--force", action="store_true", help="Skip confirmation prompt"
    )

    # --- FW COMMAND ---
    parser_fw = subparsers.add_parser("fw", help="Firmware operations")
    fw_action = parser_fw.add_mutually_exclusive_group(required=True)
    fw_action.add_argument("--list", action="store_true", help="List firmware versions")
    fw_action.add_argument(
        "--apply", action="store_true", help="Apply firmware to a host"
    )
    parser_fw.add_argument("-m", "--model", help="Filter by model")
    parser_fw.add_argument("--version", help="Firmware version to apply")
    parser_fw.add_argument("-t", "--target", help="Target hostname")
    parser_fw.add_argument(
        "--force", action="store_true", help="Force install untested version"
    )
    parser_fw.add_argument(
        "--yes", action="store_true", help="Skip confirmation prompt"
    )

    # --- BIOS COMMAND ---
    parser_bios = subparsers.add_parser("bios", help="BIOS operations")
    bios_action = parser_bios.add_mutually_exclusive_group(required=True)
    bios_action.add_argument("--list", action="store_true", help="List BIOS versions")
    bios_action.add_argument(
        "--apply", action="store_true", help="Apply BIOS to a host"
    )
    parser_bios.add_argument("-m", "--model", help="Filter by model")
    parser_bios.add_argument("--version", help="BIOS version to apply")
    parser_bios.add_argument("-t", "--target", help="Target hostname")
    parser_bios.add_argument(
        "--force", action="store_true", help="Force install untested version"
    )
    parser_bios.add_argument(
        "--yes", action="store_true", help="Skip confirmation prompt"
    )

    # --- USER COMMAND ---
    parser_user = subparsers.add_parser("user", aliases=["u"], help="User management")
    user_action = parser_user.add_mutually_exclusive_group(required=True)
    user_action.add_argument("--add", action="store_true", help="Add a user")
    user_action.add_argument("--remove", action="store_true", help="Remove a user")
    user_action.add_argument("--list", action="store_true", help="List users")
    user_action.add_argument("--update", action="store_true", help="Update a user")
    parser_user.add_argument("--username", help="Username")
    parser_user.add_argument(
        "--role",
        choices=["admin", "user", "none", "quads"],
        help="User role (use 'none' for no global role; 'quads' is site-only, requires --site)",
    )
    parser_user.add_argument(
        "--password",
        help="Password (skips interactive prompt)",
    )

    parser_vnc = subparsers.add_parser("vnc", help="VNC console operations")
    parser_vnc.add_argument("-t", "--target", required=True, help="Target hostname")
    vnc_action = parser_vnc.add_mutually_exclusive_group(required=True)
    vnc_action.add_argument(
        "--connections",
        action="store_true",
        help="Print active viewer count for the host",
    )
    vnc_action.add_argument(
        "--reset",
        action="store_true",
        help="Reset VNC configuration on the iDRAC",
    )
    parser_vnc.add_argument(
        "--force",
        action="store_true",
        help="Force --reset even when active viewers are connected",
    )

    parser_sites = subparsers.add_parser("sites", help="Manage configured sites")
    sites_action = parser_sites.add_mutually_exclusive_group()
    sites_action.add_argument(
        "--list", action="store_true", help="List sites (default)"
    )
    sites_action.add_argument("--add", action="store_true", help="Add a new site")
    sites_action.add_argument("--delete", action="store_true", help="Delete a site")
    sites_action.add_argument("--rename", action="store_true", help="Rename a site")
    sites_action.add_argument("--config", action="store_true", help="Show site config")
    sites_action.add_argument(
        "--set-config",
        action="store_true",
        dest="set_config",
        help="Update site config",
    )
    parser_sites.add_argument("--name", help="Site name")
    parser_sites.add_argument(
        "--new-name", dest="new_name", help="New site name (for --rename)"
    )
    parser_sites.add_argument("--username", dest="site_username", help="iDRAC username")
    parser_sites.add_argument("--password", dest="site_password", help="iDRAC password")
    parser_sites.add_argument("--vnc-port", dest="vnc_port", help="VNC port")
    parser_sites.add_argument(
        "--vnc-password", dest="vnc_password", help="VNC password"
    )
    parser_sites.add_argument("--quads-url", dest="quads_url", help="QUADS API URL")
    parser_sites.add_argument(
        "--quads-enabled",
        dest="quads_enabled",
        choices=["true", "false"],
        help="Enable QUADS integration",
    )

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

    if args.command in ["init", "i"]:
        init_config_files()
        return

    if args.warranty:
        warranty = args.warranty
    else:
        warranty = os.environ.get("DRACS_DB", "warranty.db")

    db_initialize(warranty)

    from dracs.sites import migrate_passwords_ini

    migrate_passwords_ini()

    if args.site:
        from dracs.db import get_site_by_name

        _site = get_site_by_name(args.site)
        if _site is None:
            print(f"Error: site '{args.site}' not found.", file=sys.stderr)
            sys.exit(1)

    def _resolve_site_id():
        if args.site:
            from dracs.db import get_site_by_name

            return get_site_by_name(args.site)["id"]
        from dracs.db import get_default_site_id

        return get_default_site_id()

    if args.command == "vnc":
        from dracs.commands import cmd_vnc

        cmd_vnc(args, site_name=getattr(args, "site", None))
        return

    if args.command == "sites":
        from dracs.db import (
            create_site,
            delete_site,
            get_site_by_name,
            list_sites,
            rename_site,
        )
        from dracs.sites import (
            get_site_ini_config,
            remove_site_ini_sections,
            rename_site_ini_sections,
            set_site_ini_config,
        )
        from dracs.validation import validate_site_name

        do_add = getattr(args, "add", False)
        do_delete = getattr(args, "delete", False)
        do_rename = getattr(args, "rename", False)
        do_config = getattr(args, "config", False)
        do_set_config = getattr(args, "set_config", False)

        if do_add:
            if not args.name:
                print("Error: --name is required with --add.", file=sys.stderr)
                sys.exit(1)
            if not validate_site_name(args.name):
                print(
                    "Error: invalid site name. Use alphanumeric characters or underscores, max 32.",
                    file=sys.stderr,
                )
                sys.exit(1)
            site = create_site(args.name)
            existing = get_site_ini_config(args.name)
            if not existing["defaults"]:
                set_site_ini_config(
                    args.name,
                    {
                        "defaults": {
                            "username": "root",
                            "password": "calvin",
                            "vnc_port": "5901",
                            "vnc_password": "",
                        }
                    },
                )
            print(f"Site '{args.name}' created.")
            audit_log(
                "site_create", target=args.name, user=getpass.getuser(), source="cli"
            )

        elif do_delete:
            if not args.name:
                print("Error: --name is required with --delete.", file=sys.stderr)
                sys.exit(1)
            site = get_site_by_name(args.name)
            if site is None:
                print(f"Error: site '{args.name}' not found.", file=sys.stderr)
                sys.exit(1)
            try:
                delete_site(site["id"])
            except (ValueError, RuntimeError) as e:
                print(f"Error: {e}", file=sys.stderr)
                sys.exit(1)
            remove_site_ini_sections(args.name)
            print(f"Site '{args.name}' deleted.")
            audit_log(
                "site_delete", target=args.name, user=getpass.getuser(), source="cli"
            )

        elif do_rename:
            if not args.name or not args.new_name:
                print(
                    "Error: --name and --new-name are required with --rename.",
                    file=sys.stderr,
                )
                sys.exit(1)
            if not validate_site_name(args.new_name):
                print(
                    "Error: invalid site name. Use alphanumeric characters or underscores, max 32.",
                    file=sys.stderr,
                )
                sys.exit(1)
            site = get_site_by_name(args.name)
            if site is None:
                print(f"Error: site '{args.name}' not found.", file=sys.stderr)
                sys.exit(1)
            rename_site(site["id"], args.new_name)
            rename_site_ini_sections(args.name, args.new_name)
            print(f"Site '{args.name}' renamed to '{args.new_name}'.")
            audit_log(
                "site_rename",
                target=f"{args.name} -> {args.new_name}",
                user=getpass.getuser(),
                source="cli",
            )

        elif do_config:
            if not args.name:
                print("Error: --name is required with --config.", file=sys.stderr)
                sys.exit(1)
            cfg = get_site_ini_config(args.name)
            defaults = cfg.get("defaults", {})
            hosts = cfg.get("hosts", {})
            if defaults:
                from rich.console import Console
                from rich.table import Table

                print(f"Defaults for site '{args.name}':")
                console = Console()
                tbl = Table(show_header=True, header_style="bold cyan")
                tbl.add_column("Key")
                tbl.add_column("Value")
                for k, v in sorted(defaults.items()):
                    tbl.add_row(k, v)
                console.print(tbl)
            else:
                print(f"No defaults configured for site '{args.name}'.")
            if hosts:
                print("\nPer-host overrides:")
                for hostname, hcfg in sorted(hosts.items()):
                    print(f"  [{hostname}]")
                    for k, v in sorted(hcfg.items()):
                        print(f"    {k} = {v}")

        elif do_set_config:
            if not args.name:
                print("Error: --name is required with --set-config.", file=sys.stderr)
                sys.exit(1)
            cfg = get_site_ini_config(args.name)
            defaults = dict(cfg.get("defaults", {}))
            updates = {}
            if args.site_username is not None:
                updates["username"] = args.site_username
            if args.site_password is not None:
                updates["password"] = args.site_password
            if args.vnc_port is not None:
                updates["vnc_port"] = args.vnc_port
            if args.vnc_password is not None:
                updates["vnc_password"] = args.vnc_password
            if args.quads_url is not None:
                updates["quads_url"] = args.quads_url
            if args.quads_enabled is not None:
                updates["quads_enabled"] = args.quads_enabled.lower()
            if not updates:
                print(
                    "Error: no config values provided. Use --username, --password, --vnc-port, --vnc-password, --quads-url, or --quads-enabled.",
                    file=sys.stderr,
                )
                sys.exit(1)
            defaults.update(updates)
            set_site_ini_config(
                args.name, {"defaults": defaults, "hosts": cfg.get("hosts", {})}
            )
            print(f"Config for site '{args.name}' updated.")
            audit_log(
                "site_config_update",
                target=args.name,
                user=getpass.getuser(),
                source="cli",
            )

        else:
            from rich.console import Console
            from rich.table import Table

            sites = list_sites()
            console = Console()
            tbl = Table(show_header=True, header_style="bold cyan")
            tbl.add_column("Site")
            tbl.add_column("Hosts", justify="right")
            for s in sites:
                tbl.add_row(s["name"], str(s["host_count"]))
            console.print(tbl)
        return

    if args.command in ["discover", "d"]:
        if args.host_list:
            hosts = read_host_list(args.host_list)
            auto_add = hasattr(args, "add") and args.add
            show_discovered = hasattr(args, "show_discovered") and args.show_discovered
            if not auto_add:
                print(f"Discovering {len(hosts)} hosts from {args.host_list}...")
                response = (
                    input("Add discovered systems to database? (y/n): ").strip().lower()
                )
                auto_add = response in ["y", "yes"]
            await commands.discover_dell_systems_batch(
                hosts, warranty, auto_add, show_discovered, site_id=_resolve_site_id()
            )
        else:
            # Single host discover
            site_id = _resolve_site_id()
            from dracs.db import get_site_allowed_domains
            from dracs.sites import is_domain_allowed

            allowed = get_site_allowed_domains(site_id)
            if not is_domain_allowed(args.target, allowed):
                logger.error(f"Cannot add host '{args.target}'. Domain not allowed.")
                sys.exit(1)

            from dracs.snmp import check_idrac_dns

            _, dns_err = check_idrac_dns(args.target)
            if dns_err:
                logger.error(dns_err)
                sys.exit(1)

            discovered_tag, discovered_model = await commands.discover_dell_system(
                args.target, warranty
            )

            # Check if --add flag was provided
            if hasattr(args, "add") and args.add:
                # Auto-add without prompting
                logger.info("Auto-adding system to database (--add flag provided)")
                await commands.add_dell_warranty(
                    discovered_tag,
                    args.target,
                    discovered_model,
                    warranty,
                    site_id=site_id,
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
                        discovered_tag,
                        args.target,
                        discovered_model,
                        warranty,
                        site_id=site_id,
                    )
                else:
                    logger.info("User declined, not adding to database")
                    print("System not added to database")

    elif args.command in ["add", "a"]:
        await commands.add_dell_warranty(
            target_tag, args.target, args.model, warranty, site_id=_resolve_site_id()
        )
        audit_log(
            "add",
            target=args.target,
            user=getpass.getuser(),
            source="cli",
            details=f"svctag={target_tag},model={args.model}",
        )
    elif args.command in ["edit", "e"]:
        await commands.edit_dell_warranty(
            target_tag, args.target, args.model, args.idrac, args.bios, warranty
        )
        audit_log(
            "edit",
            target=args.target or target_tag or "",
            user=getpass.getuser(),
            source="cli",
        )
    elif args.command in ["lookup", "l"]:
        await commands.lookup_dell_warranty(
            target_tag, args.target, args.idrac, args.bios, args.full, warranty
        )
    elif args.command in ["refresh", "rf"]:
        if args.all:
            await commands.refresh_all_systems(
                warranty, args.verbose, site_id=_resolve_site_id()
            )
        elif args.model:
            await commands.refresh_by_model(
                args.model, warranty, args.verbose, site_id=_resolve_site_id()
            )
        else:
            await commands.refresh_dell_warranty(
                target_tag, args.target, warranty, args.verbose
            )
    elif args.command in ["remove", "r"]:
        await commands.remove_dell_warranty(target_tag, args.target, warranty)
        audit_log(
            "remove",
            target=args.target or target_tag or "",
            user=getpass.getuser(),
            source="cli",
        )
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
            site_id=_resolve_site_id(),
        )
    elif args.command in ["tsr", "t"]:
        if args.list:
            await commands.tsr_list(args.target, warranty, args.last)
        elif args.download:
            await commands.tsr_download(args.target, warranty)
            audit_log(
                "tsr_download",
                target=args.target,
                user=getpass.getuser(),
                source="cli",
            )
        elif args.generate:
            await commands.tsr_generate(args.target, warranty)
            audit_log(
                "tsr_generate",
                target=args.target,
                user=getpass.getuser(),
                source="cli",
            )
        elif args.status:
            await commands.tsr_status(args.target, warranty)
    elif args.command in ["jobs", "j"]:
        if args.list:
            await commands.list_jobs(args.all, getattr(args, "failed", False), warranty)
        elif args.clear:
            await commands.clear_jobs(warranty)
            audit_log("jobs_clear", user=getpass.getuser(), source="cli")
        elif args.cancel:
            await commands.cancel_job_cmd(args.cancel, warranty)
            audit_log(
                "jobs_cancel",
                user=getpass.getuser(),
                source="cli",
                details=f"job_id={args.cancel}",
            )
    elif args.command in ["idracjobs", "ij"]:
        if args.list:
            if not args.target:
                print(
                    "Error: --target is required with --list.",
                    file=sys.stderr,
                )
                sys.exit(1)
            await commands.idrac_jobs_list(args.target, warranty)
        elif args.clear:
            await commands.idrac_jobs_clear(
                args.target, args.model, args.all, args.force, warranty
            )
            audit_log(
                "idracjobs_clear",
                target=args.target or args.model or "all",
                user=getpass.getuser(),
                source="cli",
            )
    elif args.command == "fw":
        if args.list:
            await commands.fw_list(args.model, warranty, site_id=_resolve_site_id())
        elif args.apply:
            if not args.version or not args.target:
                print(
                    "Error: --version and --target are required with --apply.",
                    file=sys.stderr,
                )
                sys.exit(1)
            await commands.fw_apply(
                args.version, args.target, args.force, args.yes, warranty
            )
            audit_log(
                "fw_apply",
                target=args.target,
                user=getpass.getuser(),
                source="cli",
                details=f"version={args.version}",
            )
    elif args.command == "bios":
        if args.list:
            await commands.bios_list(args.model, warranty, site_id=_resolve_site_id())
        elif args.apply:
            if not args.version or not args.target:
                print(
                    "Error: --version and --target are required with --apply.",
                    file=sys.stderr,
                )
                sys.exit(1)
            await commands.bios_apply(
                args.version, args.target, args.force, args.yes, warranty
            )
            audit_log(
                "bios_apply",
                target=args.target,
                user=getpass.getuser(),
                source="cli",
                details=f"version={args.version}",
            )
    elif args.command in ["user", "u"]:
        from dracs.users import (
            create_user as _create_user,
            delete_user as _delete_user,
            list_users as _list_users,
            update_user_password as _update_password,
            update_user_role as _update_role,
        )

        if args.add:
            if not args.username:
                print("Error: --username is required with --add.", file=sys.stderr)
                sys.exit(1)
            if not args.role:
                print("Error: --role is required with --add.", file=sys.stderr)
                sys.exit(1)
            if args.role == "quads":
                global_role = None
                site_role_to_set = "quads"
            elif args.role == "none":
                global_role = None
                site_role_to_set = None
            else:
                global_role = args.role
                site_role_to_set = args.role
            if args.password:
                password = args.password
            else:
                password = getpass.getpass(f"Password for {args.username}: ")
                confirm = getpass.getpass("Confirm password: ")
                if password != confirm:
                    print("Error: passwords do not match.", file=sys.stderr)
                    sys.exit(1)
            _create_user(
                args.username, password, global_role, created_by=getpass.getuser()
            )
            if site_role_to_set is not None:
                from dracs.users import set_user_site_role as _set_site_role

                try:
                    _set_site_role(args.username, _resolve_site_id(), site_role_to_set)
                except RuntimeError:
                    pass
            print(f"User '{args.username}' created with role '{args.role}'.")
            audit_log(
                "user_create",
                target=args.username,
                user=getpass.getuser(),
                source="cli",
                details=f"role={args.role}",
            )
        elif args.remove:
            if not args.username:
                print("Error: --username is required with --remove.", file=sys.stderr)
                sys.exit(1)
            if _delete_user(args.username):
                print(f"User '{args.username}' deleted.")
                audit_log(
                    "user_delete",
                    target=args.username,
                    user=getpass.getuser(),
                    source="cli",
                )
            else:
                print(f"User '{args.username}' not found.", file=sys.stderr)
                sys.exit(1)
        elif args.list:
            from dracs.db import get_primary_site_name

            site_name = args.site if args.site else get_primary_site_name()
            print(f"Using Site: {site_name}")
            users = _list_users()
            if users:
                from rich.console import Console
                from rich.table import Table

                console = Console()
                tbl = Table(show_header=True, header_style="bold cyan")
                tbl.add_column("Username")
                tbl.add_column("Site Role")
                tbl.add_column("Created")
                tbl.add_column("By")
                for u in users:
                    site_role = next(
                        (
                            r["role"]
                            for r in u.get("site_roles", [])
                            if r["site_name"] == site_name
                        ),
                        "",
                    )
                    tbl.add_row(
                        u["username"],
                        site_role,
                        u["created_at"],
                        u["created_by"] or "-",
                    )
                console.print(tbl)
            else:
                print("No users found. Only the superadmin (config) account exists.")
        elif args.update:
            if not args.username:
                print("Error: --username is required with --update.", file=sys.stderr)
                sys.exit(1)
            changes = []
            if not args.role:
                if args.password:
                    password = args.password
                else:
                    password = getpass.getpass(f"New password for {args.username}: ")
                    confirm = getpass.getpass("Confirm password: ")
                    if password != confirm:
                        print("Error: passwords do not match.", file=sys.stderr)
                        sys.exit(1)
                _update_password(args.username, password)
                changes.append("password")
            if args.role:
                if args.site:
                    from dracs.users import remove_user_site_role as _remove_site_role
                    from dracs.users import set_user_site_role as _set_site_role

                    site_id = _resolve_site_id()
                    if args.role == "none":
                        _remove_site_role(args.username, site_id)
                    else:
                        _set_site_role(args.username, site_id, args.role)
                    changes.append(f"site_role({args.site})={args.role}")
                else:
                    if args.role == "quads":
                        print(
                            "Error: 'quads' is a site-only role; specify --site.",
                            file=sys.stderr,
                        )
                        sys.exit(1)
                    role = None if args.role == "none" else args.role
                    _update_role(args.username, role)
                    changes.append(f"role={args.role}")
            print(f"User '{args.username}' updated: {', '.join(changes)}.")
            audit_log(
                "user_update",
                target=args.username,
                user=getpass.getuser(),
                source="cli",
                details=",".join(changes),
            )


def main_cli() -> None:
    from dracs.config import load_config

    load_config()
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

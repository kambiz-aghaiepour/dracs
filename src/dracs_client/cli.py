import argparse
import getpass
import sys
import time
from typing import List, Optional, Tuple
from urllib.parse import quote as url_quote

import requests
from rich.progress import Progress

from dracs.display import (
    filter_list_results,
    regex_like_match,
    render_list_host_only,
    render_list_json,
    render_list_table,
    render_tsr_table,
)
from dracs_client.auth import (
    auth_headers,
    clear_token,
    get_current_role,
    load_token,
    save_token,
)
from dracs_client.config import load_server_config, load_user_config


def fetch_systems(
    base_url: str, verify_ssl: bool, server: str = "", site: str | None = None
) -> List[dict]:
    url = f"{base_url}/api/systems"
    if site:
        url += f"?site={site}"
    headers = auth_headers(server) if server else {}
    try:
        resp = requests.get(url, verify=verify_ssl, timeout=30, headers=headers)
        resp.raise_for_status()
        return resp.json()
    except requests.exceptions.SSLError as e:
        print(
            f"SSL error connecting to {url}: {e}\n"
            "Use --no-verify or --insecure for self-signed certificates.",
            file=sys.stderr,
        )
        sys.exit(1)
    except requests.exceptions.ConnectionError as e:
        print(f"Connection error: {e}", file=sys.stderr)
        sys.exit(1)
    except requests.exceptions.HTTPError as e:
        print(f"HTTP error: {e}", file=sys.stderr)
        sys.exit(1)


def systems_to_tuples(systems: List[dict]) -> List[Tuple]:
    return [
        (
            s["svc_tag"],
            s["name"],
            s["model"],
            s["idrac_version"],
            s["bios_version"],
            s["exp_date"],
            s["exp_epoch"],
        )
        for s in systems
    ]


def client_side_filter(
    results: List[Tuple],
    service_tag: Optional[str],
    hostname: Optional[str],
    model: Optional[str],
    regex: Optional[str],
    expires_in: Optional[str],
    expired: bool,
) -> List[Tuple]:
    filtered = results

    if service_tag:
        filtered = [r for r in filtered if r[0] == service_tag]
    elif hostname:
        filtered = [r for r in filtered if r[1] == hostname]
    else:
        if model:
            filtered = [r for r in filtered if r[2] == model]
        if regex:
            filtered = [r for r in filtered if regex_like_match(regex, r[1])]

    if expires_in:
        current_time = int(time.time())
        future_timestamp = current_time + (int(expires_in) * 86400)
        filtered = [
            r
            for r in filtered
            if r[6] is not None
            and int(r[6]) > current_time
            and int(r[6]) <= future_timestamp
        ]

    if expired:
        current_time = int(time.time())
        filtered = [
            r for r in filtered if r[6] is not None and int(r[6]) < current_time
        ]

    return filtered


def cmd_list(
    args: argparse.Namespace, base_url: str, verify_ssl: bool, server: str
) -> None:
    site = getattr(args, "site", None)
    systems = fetch_systems(base_url, verify_ssl, server, site=site)
    results = systems_to_tuples(systems)

    if args.svctag and args.target:
        print(
            "Error: Cannot specify both --svctag and --target; "
            "they are mutually exclusive.",
            file=sys.stderr,
        )
        sys.exit(1)
    if (args.target or args.svctag) and (args.model or args.regex):
        print(
            "Error: Cannot specify --model or --regex when "
            "using --svctag or --target.",
            file=sys.stderr,
        )
        sys.exit(1)

    results = client_side_filter(
        results,
        args.svctag.upper() if args.svctag else None,
        args.target,
        args.model,
        args.regex,
        args.expires_in,
        args.expired,
    )

    results.sort(key=lambda r: r[1] or "")

    if any(
        [
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
        ]
    ):
        results = filter_list_results(
            results,
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
        )

    if args.host_only:
        render_list_host_only(results)
    elif args.json:
        render_list_json(results)
    else:
        render_list_table(results)


def fetch_tsr_list(
    base_url: str, hostname: str, verify_ssl: bool, server: str = ""
) -> Optional[List[dict]]:
    url = f"{base_url}/api/tsr-list/{url_quote(hostname, safe='')}"
    headers = auth_headers(server) if server else {}
    try:
        resp = requests.get(url, verify=verify_ssl, timeout=30, headers=headers)
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        data = resp.json()
        if not data.get("success"):
            print(f"Error: {data.get('message', 'Unknown error')}", file=sys.stderr)
            sys.exit(1)
        return data.get("entries", [])
    except requests.exceptions.SSLError as e:
        print(
            f"SSL error: {e}\n"
            "Use --no-verify or --insecure for self-signed certificates.",
            file=sys.stderr,
        )
        sys.exit(1)
    except requests.exceptions.ConnectionError as e:
        print(f"Connection error: {e}", file=sys.stderr)
        sys.exit(1)


def cmd_tsr(
    args: argparse.Namespace, base_url: str, verify_ssl: bool, server: str
) -> None:
    hostname = args.target

    systems = fetch_systems(base_url, verify_ssl, server)
    host_found = any(s["name"] == hostname for s in systems)
    if not host_found:
        print("Target host not found.")
        sys.exit(1)

    if args.list:
        entries = fetch_tsr_list(base_url, hostname, verify_ssl, server)
        if entries is None:
            print("Target host not found.")
            sys.exit(1)
        if not entries:
            print(f"No TSR collections found for {hostname}.")
            return

        if args.last is not None:
            entries = entries[: args.last]

        render_tsr_table(entries, base_url, hostname)

    elif args.download:
        entries = fetch_tsr_list(base_url, hostname, verify_ssl, server)
        if entries is None:
            print("Target host not found.")
            sys.exit(1)
        if not entries:
            print(f"No TSR collections found for {hostname}.")
            sys.exit(1)

        latest = entries[0]
        zip_file = latest["zip_file"]
        download_url = f"{base_url}/tsr/" f"{url_quote(hostname, safe='')}/{zip_file}"

        try:
            headers = auth_headers(server) if server else {}
            resp = requests.get(
                download_url,
                stream=True,
                verify=verify_ssl,
                timeout=300,
                headers=headers,
            )
            resp.raise_for_status()
            total = int(resp.headers.get("content-length", 0))

            with Progress() as progress:
                task = progress.add_task(f"Downloading {zip_file}", total=total or None)
                with open(zip_file, "wb") as f:
                    for chunk in resp.iter_content(chunk_size=8192):
                        f.write(chunk)
                        progress.update(task, advance=len(chunk))

            print(f"Downloaded: {zip_file}")
        except requests.exceptions.SSLError as e:
            print(f"SSL error: {e}", file=sys.stderr)
            sys.exit(1)
        except requests.exceptions.ConnectionError as e:
            print(f"Connection error: {e}", file=sys.stderr)
            sys.exit(1)
    elif getattr(args, "generate", False):
        from dracs_client.commands import cmd_tsr_generate

        cmd_tsr_generate(args, base_url, verify_ssl, server)
    elif getattr(args, "status", False):
        from dracs_client.commands import cmd_tsr_status

        cmd_tsr_status(args, base_url, verify_ssl, server)
    else:
        print(
            "Error: --list or --download is required for tsr subcommand.",
            file=sys.stderr,
        )
        sys.exit(1)


def _add_list_subparser(subparsers):
    parser_list = subparsers.add_parser("list", aliases=["li"], help="List systems")
    parser_list.add_argument("-s", "--svctag", help="Service tag to find")
    parser_list.add_argument("-t", "--target", help="Target hostname to find")
    parser_list.add_argument("-m", "--model", help="Target model to list")
    parser_list.add_argument("--expires_in", help="List hosts that expire in N days")
    parser_list.add_argument(
        "--expired", action="store_true", help="List hosts with expired warranties"
    )
    parser_list.add_argument(
        "--json", action="store_true", help="Print results in JSON format"
    )
    parser_list.add_argument(
        "--host-only",
        action="store_true",
        help="Print only hostname (one per line)",
    )
    parser_list.add_argument("--regex", help="Target hostname regex to list")

    list_bios_group = parser_list.add_mutually_exclusive_group(required=False)
    list_bios_group.add_argument("--bios_le", help="BIOS less than or equal to")
    list_bios_group.add_argument("--bios_lt", help="BIOS less than")
    list_bios_group.add_argument("--bios_ge", help="BIOS greater than or equal to")
    list_bios_group.add_argument("--bios_gt", help="BIOS greater than")
    list_bios_group.add_argument("--bios_eq", help="BIOS equal to")

    list_idrac_group = parser_list.add_mutually_exclusive_group(required=False)
    list_idrac_group.add_argument("--idrac_le", help="iDRAC less than or equal to")
    list_idrac_group.add_argument("--idrac_lt", help="iDRAC less than")
    list_idrac_group.add_argument("--idrac_ge", help="iDRAC greater than or equal to")
    list_idrac_group.add_argument("--idrac_gt", help="iDRAC greater than")
    list_idrac_group.add_argument("--idrac_eq", help="iDRAC equal to")


def _add_tsr_subparser(subparsers, role=None):
    parser_tsr = subparsers.add_parser("tsr", aliases=["t"], help="TSR operations")
    parser_tsr.add_argument(
        "-t", "--target", required=True, help="Target hostname (required)"
    )
    tsr_action = parser_tsr.add_mutually_exclusive_group(required=True)
    tsr_action.add_argument("--list", action="store_true", help="List TSR collections")
    tsr_action.add_argument(
        "--download", action="store_true", help="Download most recent TSR"
    )
    if role in ("user", "admin"):
        tsr_action.add_argument(
            "--generate", action="store_true", help="Generate a TSR collection"
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


def _add_admin_subparsers(subparsers):
    # --- REFRESH ---
    p_rf = subparsers.add_parser("refresh", aliases=["rf"], help="Refresh system data")
    rf_target = p_rf.add_mutually_exclusive_group(required=True)
    rf_target.add_argument("-s", "--svctag", help="Service tag to refresh")
    rf_target.add_argument("-t", "--target", help="Target hostname to refresh")
    rf_target.add_argument("--all", action="store_true", help="Refresh all systems")

    # --- FW ---
    p_fw = subparsers.add_parser("fw", help="Firmware operations")
    fw_action = p_fw.add_mutually_exclusive_group(required=True)
    fw_action.add_argument("--list", action="store_true", help="List firmware versions")
    fw_action.add_argument("--apply", action="store_true", help="Apply firmware")
    p_fw.add_argument("-m", "--model", help="Model name")
    p_fw.add_argument("--version", help="Firmware version to apply")
    p_fw.add_argument("-t", "--target", help="Target hostname")

    # --- BIOS ---
    p_bios = subparsers.add_parser("bios", help="BIOS operations")
    bios_action = p_bios.add_mutually_exclusive_group(required=True)
    bios_action.add_argument("--list", action="store_true", help="List BIOS versions")
    bios_action.add_argument("--apply", action="store_true", help="Apply BIOS")
    p_bios.add_argument("-m", "--model", help="Model name")
    p_bios.add_argument("--version", help="BIOS version to apply")
    p_bios.add_argument("-t", "--target", help="Target hostname")

    # --- POWER ---
    p_pwr = subparsers.add_parser("power", help="Power operations")
    pwr_action = p_pwr.add_mutually_exclusive_group(required=True)
    pwr_action.add_argument("--status", action="store_true", help="Check power status")
    pwr_action.add_argument(
        "--action",
        choices=["powerup", "powerdown", "graceshutdown", "hardreset", "powercycle"],
        help="Execute power action",
    )
    p_pwr.add_argument("-t", "--target", required=True, help="Target hostname")

    # --- JOBS ---
    p_jobs = subparsers.add_parser("jobs", aliases=["j"], help="Job queue operations")
    jobs_action = p_jobs.add_mutually_exclusive_group(required=True)
    jobs_action.add_argument("--list", action="store_true", help="List jobs")
    jobs_action.add_argument(
        "--clear", action="store_true", help="Clear completed jobs"
    )
    p_jobs.add_argument("--all", action="store_true", help="Include completed/failed")

    # --- IDRACJOBS ---
    p_ij = subparsers.add_parser(
        "idracjobs", aliases=["ij"], help="iDRAC job queue operations"
    )
    ij_action = p_ij.add_mutually_exclusive_group(required=True)
    ij_action.add_argument("--list", action="store_true", help="List iDRAC job queue")
    ij_action.add_argument("--clear", action="store_true", help="Clear iDRAC job queue")
    p_ij.add_argument("-t", "--target", help="Target hostname")

    # --- USER ---
    p_user = subparsers.add_parser("user", aliases=["u"], help="User management")
    user_action = p_user.add_mutually_exclusive_group(required=True)
    user_action.add_argument("--add", action="store_true", help="Add a user")
    user_action.add_argument("--remove", action="store_true", help="Remove a user")
    user_action.add_argument("--list", action="store_true", help="List users")
    user_action.add_argument("--update", action="store_true", help="Update a user")
    p_user.add_argument("--username", help="Username")
    p_user.add_argument("--role", choices=["admin", "user"], help="User role")


def build_parser(role: Optional[str] = None) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="DRACS Client - remote inventory query tool"
    )

    parser.add_argument(
        "-s", "--server", help="DRACS server FQDN (overrides ~/.dracsrc)"
    )
    parser.add_argument(
        "--no-verify",
        "--insecure",
        action="store_true",
        dest="no_verify",
        help="Disable SSL certificate verification",
    )
    parser.add_argument("--login", action="store_true", help="Log in to DRACS server")
    parser.add_argument(
        "--logout", action="store_true", help="Log out from DRACS server"
    )
    parser.add_argument("--user", help="Username for login")
    parser.add_argument("--site", help="Site name to operate on")

    subparsers = parser.add_subparsers(dest="command")

    _add_list_subparser(subparsers)
    _add_tsr_subparser(subparsers, role)
    subparsers.add_parser("sites", help="List configured sites")

    if role == "admin":
        _add_admin_subparsers(subparsers)

    return parser


def _handle_login(server, base_url, verify_ssl, user_override):
    username = load_user_config(user_override)
    if not username:
        if sys.stdin.isatty():
            try:
                username = input("Username: ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                sys.exit(1)
        if not username:
            print(
                "Error: username required. Use --user or set dracs_user in ~/.dracsrc.",
                file=sys.stderr,
            )
            sys.exit(1)

    password = getpass.getpass("Password: ")

    try:
        resp = requests.post(
            f"{base_url}/api/token-login",
            json={"username": username, "password": password},
            verify=verify_ssl,
            timeout=30,
        )
    except requests.exceptions.SSLError as e:
        print(f"SSL error: {e}", file=sys.stderr)
        sys.exit(1)
    except requests.exceptions.ConnectionError as e:
        print(f"Connection error: {e}", file=sys.stderr)
        sys.exit(1)

    data = resp.json()
    if data.get("success"):
        save_token(data["token"], data["role"], server)
        expiry_hours = data["expires_in"] / 3600
        print(f"{username} logged in!")
        print(
            f"You will be automatically logged out after "
            f"{expiry_hours:.0f} hours of inactivity"
        )
    else:
        print(f"Login failed: {data.get('message', 'Unknown error')}", file=sys.stderr)
        sys.exit(1)


def _handle_logout(server, base_url, verify_ssl):
    token_data = load_token(server)
    if not token_data:
        print("Not currently logged in.", file=sys.stderr)
        sys.exit(1)

    try:
        requests.post(
            f"{base_url}/api/token-logout",
            headers={"Authorization": f"Bearer {token_data['token']}"},
            verify=verify_ssl,
            timeout=30,
        )
    except (requests.exceptions.ConnectionError, requests.exceptions.SSLError):
        pass

    clear_token()
    print("Logged out successfully.")


def main() -> None:
    pre_parser = argparse.ArgumentParser(add_help=False)
    pre_parser.add_argument("-s", "--server")
    pre_parser.add_argument(
        "--no-verify", "--insecure", action="store_true", dest="no_verify"
    )
    pre_parser.add_argument("--login", action="store_true")
    pre_parser.add_argument("--logout", action="store_true")
    pre_parser.add_argument("--user")

    pre_args, remaining = pre_parser.parse_known_args()

    if "-h" in remaining or "--help" in remaining:
        role = None
        if pre_args.server:
            role = get_current_role(pre_args.server)
        else:
            from dracs_client.config import DRACSRC_PATH

            if DRACSRC_PATH.exists():
                try:
                    srv = load_server_config()
                    role = get_current_role(srv)
                except SystemExit:
                    pass
        parser = build_parser(role)
        parser.parse_args(remaining)
        return

    server = load_server_config(pre_args.server)
    verify_ssl = not pre_args.no_verify
    base_url = f"https://{server}"

    if pre_args.no_verify:
        print("WARNING: SSL certificate verification is disabled.", file=sys.stderr)
        import urllib3

        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    if pre_args.login:
        _handle_login(server, base_url, verify_ssl, pre_args.user)
        return

    if pre_args.logout:
        _handle_logout(server, base_url, verify_ssl)
        return

    role = get_current_role(server)
    parser = build_parser(role)
    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    if args.command == "sites":
        site_param = getattr(args, "site", None)
        url = f"{base_url}/api/sites"
        if site_param:
            url += f"?site={site_param}"
        headers = auth_headers(server) if server else {}
        resp = requests.get(url, verify=verify_ssl, timeout=30, headers=headers)
        data = resp.json()
        if data.get("success") and data.get("sites"):
            from tabulate import tabulate

            table = [[s["name"], s["host_count"]] for s in data["sites"]]
            print(tabulate(table, headers=["Site", "Hosts"], tablefmt="simple"))
        return

    if args.command in ["list", "li"]:
        cmd_list(args, base_url, verify_ssl, server)
    elif args.command in ["tsr", "t"]:
        cmd_tsr(args, base_url, verify_ssl, server)
    elif args.command in ["refresh", "rf"]:
        from dracs_client.commands import cmd_refresh

        cmd_refresh(args, base_url, verify_ssl, server)
    elif args.command in ["fw"]:
        from dracs_client.commands import cmd_fw

        cmd_fw(args, base_url, verify_ssl, server)
    elif args.command in ["bios"]:
        from dracs_client.commands import cmd_bios

        cmd_bios(args, base_url, verify_ssl, server)
    elif args.command in ["power"]:
        from dracs_client.commands import cmd_power

        cmd_power(args, base_url, verify_ssl, server)
    elif args.command in ["jobs", "j"]:
        from dracs_client.commands import cmd_jobs

        cmd_jobs(args, base_url, verify_ssl, server)
    elif args.command in ["idracjobs", "ij"]:
        from dracs_client.commands import cmd_idracjobs

        cmd_idracjobs(args, base_url, verify_ssl, server)
    elif args.command in ["user", "u"]:
        from dracs_client.commands import cmd_user

        cmd_user(args, base_url, verify_ssl, server)

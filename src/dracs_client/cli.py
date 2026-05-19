import argparse
import sys
import time
from typing import List, Optional, Tuple
from urllib.parse import quote as url_quote

import requests
from rich import box
from rich.console import Console
from rich.progress import Progress
from rich.table import Table

from dracs.display import (
    filter_list_results,
    regex_like_match,
    render_list_host_only,
    render_list_json,
    render_list_table,
)
from dracs_client.config import load_server_config


def fetch_systems(base_url: str, verify_ssl: bool) -> List[dict]:
    url = f"{base_url}/api/systems"
    try:
        resp = requests.get(url, verify=verify_ssl, timeout=30)
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


def cmd_list(args: argparse.Namespace, base_url: str, verify_ssl: bool) -> None:
    systems = fetch_systems(base_url, verify_ssl)
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
    base_url: str, hostname: str, verify_ssl: bool
) -> Optional[List[dict]]:
    url = f"{base_url}/api/tsr-list/{url_quote(hostname, safe='')}"
    try:
        resp = requests.get(url, verify=verify_ssl, timeout=30)
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


def cmd_tsr(args: argparse.Namespace, base_url: str, verify_ssl: bool) -> None:
    hostname = args.target

    systems = fetch_systems(base_url, verify_ssl)
    host_found = any(s["name"] == hostname for s in systems)
    if not host_found:
        print("Target host not found.")
        sys.exit(1)

    if args.list:
        entries = fetch_tsr_list(base_url, hostname, verify_ssl)
        if entries is None:
            print("Target host not found.")
            sys.exit(1)
        if not entries:
            print(f"No TSR collections found for {hostname}.")
            return

        if args.last is not None:
            entries = entries[: args.last]

        console = Console()
        table = Table(
            show_header=True,
            header_style="bold cyan",
            show_lines=True,
            box=box.HEAVY_EDGE,
        )
        table.add_column("TSR")

        for entry in entries:
            view_url = (
                f"{base_url}/tsr/"
                f"{url_quote(hostname, safe='')}/{entry['view_path']}"
            )
            download_url = (
                f"{base_url}/tsr/" f"{url_quote(hostname, safe='')}/{entry['zip_file']}"
            )
            cell = (
                f"Date: {entry['date']}\n"
                f"View: {view_url}\n"
                f"Download: {download_url}"
            )
            table.add_row(cell)

        console.print(table)

    elif args.download:
        entries = fetch_tsr_list(base_url, hostname, verify_ssl)
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
            resp = requests.get(
                download_url, stream=True, verify=verify_ssl, timeout=300
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
    else:
        print(
            "Error: --list or --download is required for tsr subcommand.",
            file=sys.stderr,
        )
        sys.exit(1)


def build_parser() -> argparse.ArgumentParser:
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

    subparsers = parser.add_subparsers(dest="command", required=True)

    # --- LIST COMMAND ---
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

    # --- TSR COMMAND ---
    parser_tsr = subparsers.add_parser("tsr", help="TSR operations")
    parser_tsr.add_argument(
        "-t", "--target", required=True, help="Target hostname (required)"
    )
    tsr_action = parser_tsr.add_mutually_exclusive_group(required=True)
    tsr_action.add_argument("--list", action="store_true", help="List TSR collections")
    tsr_action.add_argument(
        "--download", action="store_true", help="Download most recent TSR"
    )
    parser_tsr.add_argument(
        "--last",
        nargs="?",
        const=1,
        type=int,
        default=None,
        help="Show only the last N TSRs (default: 1 if no value given)",
    )

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    server = load_server_config(args.server)
    verify_ssl = not args.no_verify
    base_url = f"https://{server}"

    if args.no_verify:
        print(
            "WARNING: SSL certificate verification is disabled.",
            file=sys.stderr,
        )
        import urllib3

        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    if args.command in ["list", "li"]:
        cmd_list(args, base_url, verify_ssl)
    elif args.command == "tsr":
        cmd_tsr(args, base_url, verify_ssl)

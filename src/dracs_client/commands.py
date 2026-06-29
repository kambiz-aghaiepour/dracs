"""Remote command handlers for authenticated dracs-client operations."""

import getpass
import sys

import requests

from dracs_client.auth import auth_headers


def _api_request(method, url, server, verify_ssl, **kwargs):
    headers = kwargs.pop("headers", {})
    headers.update(auth_headers(server))
    timeout = kwargs.pop("timeout", 30)
    try:
        resp = getattr(requests, method)(
            url, verify=verify_ssl, headers=headers, timeout=timeout, **kwargs
        )
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

    if resp.status_code == 401:
        data = resp.json() if resp.content else {}
        msg = data.get("message", "Authentication required")
        print(f"Error: {msg}", file=sys.stderr)
        print("Try: dracs-client --login", file=sys.stderr)
        sys.exit(1)
    if resp.status_code == 403:
        data = resp.json() if resp.content else {}
        msg = data.get("message", "Insufficient permissions")
        print(f"Error: {msg}", file=sys.stderr)
        sys.exit(1)

    if resp.status_code >= 400:
        try:
            data = resp.json() if resp.content else {}
        except (ValueError, requests.exceptions.JSONDecodeError):
            data = {}
        msg = data.get("message", f"HTTP {resp.status_code}")
        print(f"Error: {msg}", file=sys.stderr)
        sys.exit(1)

    return resp


def _post_json(url, server, verify_ssl, data):
    return _api_request(
        "post",
        url,
        server,
        verify_ssl,
        json=data,
    )


def _render_version_summary(models, label):
    from rich.console import Console
    from rich.table import Table

    if not models:
        print("No systems found.")
        return

    console = Console()
    table = Table(show_header=True, header_style="bold cyan", show_lines=True)
    table.add_column("Model")
    table.add_column("Installed Versions")
    table.add_column("Other Versions")

    for m in models:
        installed_lines = "\n".join(
            f"{i['version']} ({i['count']})" for i in m["installed"]
        )
        other_lines = "\n".join(m.get("available", []))
        table.add_row(m["model"], installed_lines, other_lines)

    console.print(table)


def _site_url(url, site):
    if not site:
        return url
    sep = "&" if "?" in url else "?"
    return f"{url}{sep}site={site}"


def _print_result(resp):
    data = resp.json()
    if data.get("success"):
        print(data.get("message", "OK"))
    else:
        print(f"Error: {data.get('message', 'Unknown error')}", file=sys.stderr)
        sys.exit(1)


def cmd_tsr_generate(args, base_url, verify_ssl, server):
    site = getattr(args, "site", None)
    from dracs_client.cli import fetch_systems

    systems = fetch_systems(base_url, verify_ssl, server, site=site)
    host = next((s for s in systems if s["name"] == args.target), None)
    if not host:
        print("Target host not found.", file=sys.stderr)
        sys.exit(1)

    resp = _post_json(
        f"{base_url}/api/tsr-collect",
        server,
        verify_ssl,
        {"hostname": args.target, "service_tag": host["svc_tag"]},
    )
    _print_result(resp)


def cmd_tsr_status(args, base_url, verify_ssl, server):
    site = getattr(args, "site", None)
    resp = _post_json(
        _site_url(f"{base_url}/api/tsr-status", site),
        server,
        verify_ssl,
        {"hostname": args.target},
    )
    data = resp.json()
    if data.get("success"):
        status = data.get("status", {})
        print(f"State: {status.get('state', 'unknown')}")
        pct = status.get("percent_complete")
        if pct:
            print(f"Progress: {pct}%")
    else:
        print(f"Error: {data.get('message', 'Unknown error')}", file=sys.stderr)


def cmd_refresh(args, base_url, verify_ssl, server):
    site = getattr(args, "site", None)
    if args.all:
        resp = _post_json(
            _site_url(f"{base_url}/api/refresh-all", site), server, verify_ssl, {}
        )
    elif args.target:
        resp = _post_json(
            _site_url(f"{base_url}/api/refresh", site),
            server,
            verify_ssl,
            {"hostname": args.target},
        )
    elif args.svctag:
        resp = _post_json(
            _site_url(f"{base_url}/api/refresh", site),
            server,
            verify_ssl,
            {"service_tag": args.svctag},
        )
    else:
        print("Error: --target, --svctag, or --all is required.", file=sys.stderr)
        sys.exit(1)
    _print_result(resp)


def cmd_fw(args, base_url, verify_ssl, server):
    site = getattr(args, "site", None)
    if args.list:
        url = _site_url(f"{base_url}/api/fw-summary", site)
        if args.model:
            sep = "&" if "?" in url else "?"
            url = f"{url}{sep}model={args.model}"
        resp = _api_request("get", url, server, verify_ssl)
        try:
            data = resp.json()
        except (ValueError, requests.exceptions.JSONDecodeError):
            print("Error: unexpected response from server", file=sys.stderr)
            sys.exit(1)
        if data.get("success"):
            _render_version_summary(data["models"], "Firmware")
        else:
            print(f"Error: {data.get('message')}", file=sys.stderr)
    elif args.apply:
        if not args.version or not args.target:
            print(
                "Error: --version and --target are required with --apply.",
                file=sys.stderr,
            )
            sys.exit(1)
        if not args.model:
            print("Error: --model is required with --apply.", file=sys.stderr)
            sys.exit(1)
        resp = _post_json(
            _site_url(f"{base_url}/api/firmware-update", site),
            server,
            verify_ssl,
            {
                "hostname": args.target,
                "target_version": args.version,
                "model": args.model,
            },
        )
        _print_result(resp)


def cmd_bios(args, base_url, verify_ssl, server):
    site = getattr(args, "site", None)
    if args.list:
        url = _site_url(f"{base_url}/api/bios-summary", site)
        if args.model:
            sep = "&" if "?" in url else "?"
            url = f"{url}{sep}model={args.model}"
        resp = _api_request("get", url, server, verify_ssl)
        try:
            data = resp.json()
        except (ValueError, requests.exceptions.JSONDecodeError):
            print("Error: unexpected response from server", file=sys.stderr)
            sys.exit(1)
        if data.get("success"):
            _render_version_summary(data["models"], "BIOS")
        else:
            print(f"Error: {data.get('message')}", file=sys.stderr)
    elif args.apply:
        if not args.version or not args.target:
            print(
                "Error: --version and --target are required with --apply.",
                file=sys.stderr,
            )
            sys.exit(1)
        if not args.model:
            print("Error: --model is required with --apply.", file=sys.stderr)
            sys.exit(1)
        resp = _post_json(
            _site_url(f"{base_url}/api/bios-update", site),
            server,
            verify_ssl,
            {
                "hostname": args.target,
                "target_bios": args.version,
                "model": args.model,
            },
        )
        _print_result(resp)


def cmd_power(args, base_url, verify_ssl, server):
    site = getattr(args, "site", None)
    if args.status:
        if not args.target:
            print("Error: --target is required with --status.", file=sys.stderr)
            sys.exit(1)
        resp = _post_json(
            _site_url(f"{base_url}/api/power-status", site),
            server,
            verify_ssl,
            {"hostname": args.target},
        )
        data = resp.json()
        if data.get("success"):
            print(f"{args.target}: {data.get('status', 'unknown')}")
        else:
            print(f"Error: {data.get('message')}", file=sys.stderr)
    elif args.action:
        if not args.target:
            print("Error: --target is required with --action.", file=sys.stderr)
            sys.exit(1)
        resp = _post_json(
            _site_url(f"{base_url}/api/power-action", site),
            server,
            verify_ssl,
            {"hostname": args.target, "action": args.action},
        )
        _print_result(resp)
    else:
        print(
            "Error: --status or --action is required for power subcommand.",
            file=sys.stderr,
        )
        sys.exit(1)


def cmd_jobs(args, base_url, verify_ssl, server):
    site = getattr(args, "site", None)
    if args.list:
        failed_only = getattr(args, "failed", False)
        params_parts = []
        if args.all or failed_only:
            params_parts.append("all=true")
        if failed_only:
            params_parts.append("status=failed")
        params = ("?" + "&".join(params_parts)) if params_parts else ""
        resp = _api_request(
            "get", _site_url(f"{base_url}/api/jobs{params}", site), server, verify_ssl
        )
        data = resp.json()
        if data.get("success"):
            jobs = data.get("jobs", [])
            if jobs:
                from rich.console import Console
                from rich.table import Table

                console = Console()
                tbl = Table(show_header=True, header_style="bold cyan")
                tbl.add_column("ID", justify="right")
                tbl.add_column("Type")
                tbl.add_column("Target")
                tbl.add_column("Status")
                tbl.add_column("Created")
                tbl.add_column("Error")
                for j in jobs:
                    tbl.add_row(
                        str(j.get("id")),
                        j.get("job_type") or "",
                        j.get("target") or "",
                        j.get("status") or "",
                        (j.get("created_at") or "")[:19],
                        j.get("error") or "",
                    )
                console.print(tbl)
            else:
                print("No active jobs.")
    elif args.clear:
        resp = _post_json(
            _site_url(f"{base_url}/api/clear-job-queue", site),
            server,
            verify_ssl,
            {},
        )
        _print_result(resp)
    else:
        print(
            "Error: --list or --clear is required for jobs subcommand.",
            file=sys.stderr,
        )
        sys.exit(1)


def cmd_idracjobs(args, base_url, verify_ssl, server):
    site = getattr(args, "site", None)
    if args.list:
        if not args.target:
            print("Error: --target is required with --list.", file=sys.stderr)
            sys.exit(1)
        resp = _post_json(
            _site_url(f"{base_url}/api/job-queue", site),
            server,
            verify_ssl,
            {"hostname": args.target},
        )
        data = resp.json()
        if data.get("success"):
            jobs = data.get("jobs", [])
            if jobs:
                from rich.console import Console
                from rich.table import Table

                console = Console()
                tbl = Table(show_header=True, header_style="bold cyan")
                tbl.add_column("ID", justify="right")
                tbl.add_column("Name")
                tbl.add_column("Status")
                for j in jobs:
                    tbl.add_row(str(j.get("id")), j.get("name") or "", j.get("status") or "")
                console.print(tbl)
            else:
                print(f"No iDRAC jobs for {args.target}.")
        else:
            print(f"Error: {data.get('message')}", file=sys.stderr)
    elif args.clear:
        if not args.target:
            print("Error: --target is required with --clear.", file=sys.stderr)
            sys.exit(1)
        resp = _post_json(
            _site_url(f"{base_url}/api/clear-job-queue", site),
            server,
            verify_ssl,
            {"hostnames": [args.target]},
        )
        _print_result(resp)
    else:
        print(
            "Error: --list or --clear is required for idracjobs subcommand.",
            file=sys.stderr,
        )
        sys.exit(1)


def cmd_user(args, base_url, verify_ssl, server):
    if args.add:
        if not args.username:
            print("Error: --username is required with --add.", file=sys.stderr)
            sys.exit(1)
        if not args.role:
            print("Error: --role is required with --add.", file=sys.stderr)
            sys.exit(1)
        if getattr(args, "password", None):
            password = args.password
        else:
            password = getpass.getpass(f"Password for {args.username}: ")
            confirm = getpass.getpass("Confirm password: ")
            if password != confirm:
                print("Error: passwords do not match.", file=sys.stderr)
                sys.exit(1)
        if args.role == "quads":
            site_name = getattr(args, "site", None)
            if not site_name:
                print(
                    "Error: 'quads' is a site-only role; specify --site.",
                    file=sys.stderr,
                )
                sys.exit(1)
            payload = {
                "username": args.username,
                "password": password,
                "role": None,
                "site_role": {"site_name": site_name, "role": "quads"},
            }
        else:
            role = None if args.role == "none" else args.role
            payload = {"username": args.username, "password": password, "role": role}
        resp = _post_json(f"{base_url}/api/users", server, verify_ssl, payload)
        _print_result(resp)
    elif args.remove:
        if not args.username:
            print("Error: --username is required with --remove.", file=sys.stderr)
            sys.exit(1)
        resp = _api_request(
            "delete",
            f"{base_url}/api/users/{args.username}",
            server,
            verify_ssl,
        )
        _print_result(resp)
    elif args.list:
        resp = _api_request("get", f"{base_url}/api/users", server, verify_ssl)
        data = resp.json()
        if data.get("success"):
            users = data.get("users", [])
            site_name = getattr(args, "site", None)
            if not site_name:
                sites_resp = _api_request(
                    "get", f"{base_url}/api/sites", server, verify_ssl
                )
                sites_data = sites_resp.json()
                primary = next(
                    (s for s in sites_data.get("sites", []) if s.get("is_primary")),
                    None,
                )
                site_name = primary["name"] if primary else "Default"
            print(f"Using Site: {site_name}")
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
                        u.get("created_at", "")[:19],
                        u.get("created_by") or "-",
                    )
                console.print(tbl)
            else:
                print("No users found. Only the superadmin (config) account exists.")
    elif args.update:
        if not args.username:
            print("Error: --username is required with --update.", file=sys.stderr)
            sys.exit(1)
        payload = {}
        if args.role:
            site_name = getattr(args, "site", None)
            if site_name:
                role = None if args.role == "none" else args.role
                payload["site_role"] = {"site_name": site_name, "role": role}
            elif args.role == "quads":
                print(
                    "Error: 'quads' is a site-only role; specify --site.",
                    file=sys.stderr,
                )
                sys.exit(1)
            else:
                payload["role"] = None if args.role == "none" else args.role
        else:
            if getattr(args, "password", None):
                password = args.password
            else:
                password = getpass.getpass(f"New password for {args.username}: ")
                confirm = getpass.getpass("Confirm password: ")
                if password != confirm:
                    print("Error: passwords do not match.", file=sys.stderr)
                    sys.exit(1)
            payload["password"] = password
        resp = _api_request(
            "patch",
            f"{base_url}/api/users/{args.username}",
            server,
            verify_ssl,
            json=payload,
        )
        _print_result(resp)


def cmd_discover(args, base_url, verify_ssl, server):
    site = getattr(args, "site", None)

    if args.host_list:
        try:
            with open(args.host_list) as fh:
                hostnames = [line.strip() for line in fh if line.strip()]
        except OSError as e:
            print(f"Error reading host list: {e}", file=sys.stderr)
            sys.exit(1)
        if not hostnames:
            print("Error: host list file is empty.", file=sys.stderr)
            sys.exit(1)
    else:
        hostnames = [args.target]

    resp = _post_json(
        _site_url(f"{base_url}/api/discover", site),
        server,
        verify_ssl,
        {"hostnames": hostnames},
    )
    data = resp.json()
    dns_failed = data.get("dns_failed", [])
    if dns_failed:
        from rich.console import Console
        from rich.table import Table

        print(f"\n{len(dns_failed)} host(s) failed DNS check:")
        console = Console()
        tbl = Table(show_header=True, header_style="bold red")
        tbl.add_column("Hostname")
        tbl.add_column("iDRAC FQDN")
        tbl.add_column("Error")
        for f in dns_failed:
            tbl.add_row(f["hostname"], f["idrac_fqdn"], f["error"])
        console.print(tbl)
    _print_result(resp)

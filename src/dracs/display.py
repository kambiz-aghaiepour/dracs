import json
import operator
import re
import time
from typing import List, Optional, Tuple

from rich import box
from rich.console import Console
from rich.table import Table


def _parse_version(v: str) -> Tuple[int, ...]:
    return tuple(map(int, v.split(".")))


def filter_list_results(
    results: List[Tuple],
    bios_le: Optional[str],
    bios_lt: Optional[str],
    bios_ge: Optional[str],
    bios_gt: Optional[str],
    bios_eq: Optional[str],
    idrac_le: Optional[str],
    idrac_lt: Optional[str],
    idrac_ge: Optional[str],
    idrac_gt: Optional[str],
    idrac_eq: Optional[str],
) -> List[Tuple]:
    checks = [
        (3, operator.le, idrac_le),
        (3, operator.lt, idrac_lt),
        (3, operator.ge, idrac_ge),
        (3, operator.gt, idrac_gt),
        (3, operator.eq, idrac_eq),
        (4, operator.le, bios_le),
        (4, operator.lt, bios_lt),
        (4, operator.ge, bios_ge),
        (4, operator.gt, bios_gt),
        (4, operator.eq, bios_eq),
    ]
    active = [(idx, cmp, _parse_version(val)) for idx, cmp, val in checks if val]

    output = []
    for s in results:
        for idx, cmp, threshold in active:
            if cmp(_parse_version(s[idx]), threshold):
                output.append(s)
    return output


def _build_version_ranks(results: List[Tuple], col_index: int) -> dict:
    by_model = {}
    for row in results:
        model = row[2]
        value = row[col_index]
        if model and value:
            by_model.setdefault(model, set()).add(value)
    for model in by_model:
        by_model[model] = sorted(
            by_model[model],
            key=lambda v: tuple(map(int, v.split("."))),
            reverse=True,
        )
    return by_model


def _color_by_rank(value: str, model: str, ranks: dict) -> str:
    if model in ranks and value in ranks[model]:
        idx = ranks[model].index(value)
        if idx == 1:
            return f"[yellow]{value}[/yellow]"
        elif idx >= 2:
            return f"[red]{value}[/red]"
    return value


def _color_expiry(exp_date: str, exp_epoch: int, current_time: int) -> str:
    ninety_days_future = current_time + (90 * 86400)
    if exp_epoch < current_time:
        return f"[red]{exp_date}[/red]"
    elif exp_epoch <= ninety_days_future:
        return f"[yellow]{exp_date}[/yellow]"
    return exp_date


def render_list_table(results: List[Tuple]) -> None:
    firmware_ranks = _build_version_ranks(results, 3)
    bios_ranks = _build_version_ranks(results, 4)

    console = Console()
    table = Table(show_header=True, header_style="bold cyan")
    table.add_column("Service Tag")
    table.add_column("Hostname")
    table.add_column("Model")
    table.add_column("Firmware")
    table.add_column("BIOS")
    table.add_column("Expires")

    current_time = int(time.time())

    for row in results:
        model = str(row[2])
        firmware = str(row[3])
        bios = str(row[4])

        table.add_row(
            str(row[0]),
            str(row[1]),
            model,
            _color_by_rank(firmware, model, firmware_ranks),
            _color_by_rank(bios, model, bios_ranks),
            _color_expiry(str(row[5]), int(row[6]), current_time),
        )

    console.print(table)


def render_list_json(results: List[Tuple]) -> None:
    print(json.dumps(results, indent=4))


def render_list_host_only(results: List[Tuple]) -> None:
    for row in results:
        print(row[1])


def render_tsr_table(entries: List[dict], base_url: str, hostname: str) -> None:
    from urllib.parse import quote as url_quote

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
            f"{base_url}/tsr/" f"{url_quote(hostname, safe='')}/{entry['view_path']}"
        )
        download_url = (
            f"{base_url}/tsr/" f"{url_quote(hostname, safe='')}/{entry['zip_file']}"
        )
        cell = (
            f"Date: {entry['date']}\n" f"View: {view_url}\n" f"Download: {download_url}"
        )
        table.add_row(cell)

    console.print(table)


def regex_like_match(pattern: str, value: str) -> bool:
    parts = []
    i = 0
    while i < len(pattern):
        ch = pattern[i]
        if ch == "%":
            parts.append(".*")
        elif ch == "_":
            parts.append(".")
        else:
            parts.append(re.escape(ch))
        i += 1
    return bool(re.fullmatch("".join(parts), value, re.IGNORECASE))

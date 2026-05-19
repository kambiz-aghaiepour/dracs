import json
import re
import time
from typing import List, Optional, Tuple

from rich.console import Console
from rich.table import Table


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
    output = []
    for s in results:
        s_idrac = s[3]
        s_idrac_tuple = tuple(map(int, s_idrac.split(".")))
        s_bios = s[4]
        s_bios_tuple = tuple(map(int, s_bios.split(".")))
        if idrac_le:
            idrac_le_tuple = tuple(map(int, idrac_le.split(".")))
            if s_idrac_tuple <= idrac_le_tuple:
                output.append(s)
        if idrac_lt:
            idrac_lt_tuple = tuple(map(int, idrac_lt.split(".")))
            if s_idrac_tuple < idrac_lt_tuple:
                output.append(s)
        if idrac_ge:
            idrac_ge_tuple = tuple(map(int, idrac_ge.split(".")))
            if s_idrac_tuple >= idrac_ge_tuple:
                output.append(s)
        if idrac_gt:
            idrac_gt_tuple = tuple(map(int, idrac_gt.split(".")))
            if s_idrac_tuple > idrac_gt_tuple:
                output.append(s)
        if idrac_eq:
            idrac_eq_tuple = tuple(map(int, idrac_eq.split(".")))
            if s_idrac_tuple == idrac_eq_tuple:
                output.append(s)
        if bios_le:
            bios_le_tuple = tuple(map(int, bios_le.split(".")))
            if s_bios_tuple <= bios_le_tuple:
                output.append(s)
        if bios_lt:
            bios_lt_tuple = tuple(map(int, bios_lt.split(".")))
            if s_bios_tuple < bios_lt_tuple:
                output.append(s)
        if bios_ge:
            bios_ge_tuple = tuple(map(int, bios_ge.split(".")))
            if s_bios_tuple >= bios_ge_tuple:
                output.append(s)
        if bios_gt:
            bios_gt_tuple = tuple(map(int, bios_gt.split(".")))
            if s_bios_tuple > bios_gt_tuple:
                output.append(s)
        if bios_eq:
            bios_eq_tuple = tuple(map(int, bios_eq.split(".")))
            if s_bios_tuple == bios_eq_tuple:
                output.append(s)

    return output


def render_list_table(results: List[Tuple]) -> None:
    firmware_by_model = {}
    bios_by_model = {}

    for row in results:
        model = row[2]
        firmware = row[3]
        bios = row[4]

        if model and firmware:
            if model not in firmware_by_model:
                firmware_by_model[model] = set()
            firmware_by_model[model].add(firmware)

        if model and bios:
            if model not in bios_by_model:
                bios_by_model[model] = set()
            bios_by_model[model].add(bios)

    for model in firmware_by_model:
        firmware_by_model[model] = sorted(
            firmware_by_model[model],
            key=lambda v: tuple(map(int, v.split("."))),
            reverse=True,
        )

    for model in bios_by_model:
        bios_by_model[model] = sorted(
            bios_by_model[model],
            key=lambda v: tuple(map(int, v.split("."))),
            reverse=True,
        )

    console = Console()
    table = Table(show_header=True, header_style="bold cyan")
    table.add_column("Service Tag")
    table.add_column("Hostname")
    table.add_column("Model")
    table.add_column("Firmware")
    table.add_column("BIOS")
    table.add_column("Expires")

    current_time = int(time.time())
    ninety_days_future = current_time + (90 * 86400)

    for row in results:
        model = str(row[2])
        firmware = str(row[3])
        bios = str(row[4])
        exp_epoch = int(row[6])
        exp_date = str(row[5])

        if exp_epoch < current_time:
            colored_exp_date = f"[red]{exp_date}[/red]"
        elif exp_epoch <= ninety_days_future:
            colored_exp_date = f"[yellow]{exp_date}[/yellow]"
        else:
            colored_exp_date = exp_date

        if model in firmware_by_model and firmware in firmware_by_model[model]:
            firmware_index = firmware_by_model[model].index(firmware)
            if firmware_index == 1:
                colored_firmware = f"[yellow]{firmware}[/yellow]"
            elif firmware_index >= 2:
                colored_firmware = f"[red]{firmware}[/red]"
            else:
                colored_firmware = firmware
        else:
            colored_firmware = firmware

        if model in bios_by_model and bios in bios_by_model[model]:
            bios_index = bios_by_model[model].index(bios)
            if bios_index == 1:
                colored_bios = f"[yellow]{bios}[/yellow]"
            elif bios_index >= 2:
                colored_bios = f"[red]{bios}[/red]"
            else:
                colored_bios = bios
        else:
            colored_bios = bios

        table.add_row(
            str(row[0]),
            str(row[1]),
            model,
            colored_firmware,
            colored_bios,
            colored_exp_date,
        )

    console.print(table)


def render_list_json(results: List[Tuple]) -> None:
    print(json.dumps(results, indent=4))


def render_list_host_only(results: List[Tuple]) -> None:
    for row in results:
        print(row[1])


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

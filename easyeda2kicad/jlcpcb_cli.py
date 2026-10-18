"""JLCPCB parts library on the command line: --search, --stock and --datasheet.

Prints tables or, with --json, exactly one JSON object. Exit codes: 0 = ok,
1 = part not listed or datasheet unavailable (no PDF, write error),
2 = invalid arguments, 3 = JLCPCB/LCSC error (not reachable, unexpected response).
"""

from __future__ import annotations

# Global imports
import argparse
import json
import re
import shutil
import sys
from dataclasses import dataclass
from typing import Any, Callable, TextIO

# Local imports
from .easyeda.easyeda_api import (
    JLCPCB_SORT_MODES,
    EasyedaApi,
    JlcpcbConnectionError,
    JlcpcbError,
    normalize_lcsc_id,
)

EXIT_NOT_FOUND = 1
EXIT_CONNECTION_ERROR = 3

PARTS_HELP = """\
JLCPCB parts library (no login needed):
  easyeda2kicad --search BQ24073RGTR "100nF 0603"         one table per keyword
  easyeda2kicad --search 10k --package 0402 --basic-only  10k in 0402, no setup fee
  easyeda2kicad --search RT9080 --min-stock 1 --sort stock
  easyeda2kicad --stock C15220 C841192                    exact match by LCSC number
  easyeda2kicad --datasheet C15220 --datasheet-dir ds     saves ds/BQ24073RGTR_C15220.pdf
  add --json for one JSON object: {"search": [...], "stock": [...], "datasheet": [...]}

TYPE: Basic and Ext-pref have no JLCPCB setup fee, Ext has one per part type.
Keywords match loosely ("10k 0402" also finds 0603 parts); use --package, whose
names are exact and case-sensitive (SOT-23-5 does not include TSOT-23-5).
Exit codes: 0 ok, 1 part not listed or datasheet unavailable, 2 invalid
arguments, 3 JLCPCB/LCSC error (not reachable, unexpected response).
Logs and errors go to stderr.
"""

PARTS_COMMANDS = ("search", "stock", "datasheet")
SEARCH_ONLY_OPTIONS = (
    "--package",
    "--basic-only",
    "--min-stock",
    "--sort",
    "--limit",
)

TABLE_COLUMNS: list[tuple[str, int]] = [
    ("LCSC", 11),
    ("TYPE", 9),
    ("STOCK", 9),
    ("PRICE", 9),
    ("MPN", 28),
    ("BRAND", 16),
    ("PACKAGE", 18),
    ("DESCRIPTION", 0),
]
# Width of all columns before DESCRIPTION, including the separating spaces
FIXED_COLUMNS_WIDTH = sum(width + 1 for _, width in TABLE_COLUMNS[:-1])
DESCRIPTION_WIDTH = 90  # when stdout is not a terminal

# --search fetches further pages until --limit parts pass the local filters
MAX_SEARCH_PAGES = 5

NEXT_STEPS = (
    "Next: --stock C… stock check · --datasheet C… PDF · "
    "--lcsc_id C… --full import into KiCad\n"
)


# ------------------------------------------------------------
# Command line arguments


def _int_at_least(minimum: int) -> Callable[[str], int]:
    def parse(value: str) -> int:
        number = int(value)
        if number < minimum:
            raise argparse.ArgumentTypeError(f"must be at least {minimum}, got {value}")
        return number

    parse.__name__ = "int"  # argparse reports "invalid int value" for non-numbers
    return parse


def _lcsc_number(value: str) -> str:
    """Accepts "C15220", "c15220" and "15220"."""
    code = normalize_lcsc_id(value)
    if not re.fullmatch(r"C\d+", code):
        raise argparse.ArgumentTypeError(
            f"'{value}' is not an LCSC number like C15220"
            " (use --search for manufacturer part numbers)"
        )
    return code


def add_parts_arguments(parser: argparse.ArgumentParser) -> None:
    group = parser.add_argument_group(
        "JLCPCB parts library", "Works without --lcsc_id; see the examples below."
    )
    group.add_argument(
        "--search",
        nargs="+",
        metavar="KEYWORD",
        help="Search by MPN, value or description (one table per keyword)",
    )
    group.add_argument(
        "--stock",
        nargs="+",
        type=_lcsc_number,
        metavar="LCSC_ID",
        help="Stock, price and type by LCSC number (C15220), exact match only",
    )
    group.add_argument(
        "--datasheet",
        nargs="+",
        type=_lcsc_number,
        metavar="LCSC_ID",
        help="Download the datasheet PDF(s) as <MPN>_<LCSC>.pdf",
    )
    group.add_argument(
        "--datasheet-dir",
        dest="datasheet_dir",
        metavar="DIR",
        help="--datasheet: target folder (default: current folder)",
    )
    group.add_argument(
        "--package",
        nargs="+",
        metavar="PKG",
        help="--search: only these packages, exact and case-sensitive (0402, SOT-23-5)",
    )
    group.add_argument(
        "--basic-only",
        dest="basic_only",
        action="store_true",
        help="--search: only Basic parts (no setup fee)",
    )
    group.add_argument(
        "--min-stock",
        dest="min_stock",
        type=_int_at_least(0),
        metavar="N",
        help="--search: only parts with at least N in stock (1 = in stock)",
    )
    group.add_argument(
        "--sort",
        choices=list(JLCPCB_SORT_MODES),
        help="--search: stock = most first, price = cheapest first"
        " (default: relevance)",
    )
    group.add_argument(
        "--limit",
        type=_int_at_least(1),
        metavar="N",
        help="--search: show up to N parts per keyword (default: 10)",
    )
    group.add_argument(
        "--json",
        action="store_true",
        help="Print the results as one JSON object",
    )


def has_parts_command(arguments: dict[str, Any]) -> bool:
    return any(arguments[command] for command in PARTS_COMMANDS)


def check_parts_arguments(
    parser: argparse.ArgumentParser, arguments: dict[str, Any]
) -> None:
    """Reject options that would silently do nothing (exit code 2)."""
    if arguments["search"] is None:
        for option in SEARCH_ONLY_OPTIONS:
            if arguments[option[2:].replace("-", "_")] not in (None, False):
                parser.error(f"{option} only works together with --search")
    elif any(not keyword.strip() for keyword in arguments["search"]):
        parser.error("--search: empty keyword")
    if arguments["datasheet_dir"] is not None and not arguments["datasheet"]:
        parser.error("--datasheet-dir only works together with --datasheet")
    if arguments["json"] and not has_parts_command(arguments):
        parser.error("--json only works together with --search, --stock or --datasheet")


def run_parts_commands(arguments: dict[str, Any]) -> int:
    """Run --search/--stock/--datasheet from parsed arguments; returns the exit code."""
    return run_parts(
        EasyedaApi(use_cache=arguments["use_cache"]),
        search=arguments["search"],
        stock=arguments["stock"],
        datasheet=arguments["datasheet"],
        datasheet_dir=arguments["datasheet_dir"] or ".",
        limit=arguments["limit"] or 10,
        filters=SearchFilters(
            packages=arguments["package"],
            basic_only=arguments["basic_only"],
            min_stock=arguments["min_stock"] or 0,
            sort=arguments["sort"],
        ),
        as_json=arguments["json"],
    )


# ------------------------------------------------------------
# Commands


@dataclass
class SearchFilters:
    """Options of --search besides the keywords and --limit."""

    packages: list[str] | None = None
    basic_only: bool = False
    min_stock: int = 0
    sort: str | None = None  # None = relevance, else a key of JLCPCB_SORT_MODES

    def describe(self) -> str:
        """Active filters as short text, e.g. "package 0402, Basic only"."""
        parts = []
        if self.packages:
            parts.append("package " + "/".join(self.packages))
        if self.basic_only:
            parts.append("Basic only")
        if self.min_stock:
            parts.append(f"stock ≥ {self.min_stock}")
        if self.sort:
            parts.append(f"sorted by {self.sort}")
        return ", ".join(parts)


def search_parts(
    api: EasyedaApi,
    keywords: list[str],
    limit: int = 10,
    filters: SearchFilters | None = None,
) -> list[dict[str, Any]]:
    """One {"query", "status", "total", "shown", "results"} entry per keyword.

    JLCPCB applies package, Basic, in-stock and sort. Placeholders (C9900…,
    usually without stock) and the exact --min-stock are filtered locally, so
    up to MAX_SEARCH_PAGES pages are fetched to fill `limit`; "truncated" is
    true if that page limit was reached. "status" is "ok" or "error" (with "error").
    """
    f = filters or SearchFilters()
    report = []
    for keyword in keywords:
        results: list[dict[str, Any]] = []
        total, error, truncated = 0, None, False
        for page in range(1, MAX_SEARCH_PAGES + 1):
            data = api.search_jlcpcb_components(
                keyword,
                page=page,
                page_size=limit,
                part_type="base" if f.basic_only else None,
                packages=f.packages,
                in_stock=f.min_stock > 0,
                sort=f.sort,
            )
            if "error" in data:
                error = data["error"]
                break
            total, batch = data["total"], data["results"]
            results += [
                r
                for r in batch
                if not r["placeholder"] and int(r["stock"] or 0) >= f.min_stock
            ]
            if len(results) >= limit or len(batch) < limit or page * limit >= total:
                break
        else:
            truncated = True  # page limit reached before `limit` parts were found
        results = results[:limit]
        entry: dict[str, Any] = {
            "query": keyword,
            "status": "error" if error else "ok",
            "total": total,
            "shown": len(results),
            "truncated": truncated,
            "results": results,
        }
        if error:
            entry["error"] = error
        report.append(entry)
    return report


def stock_parts(api: EasyedaApi, lcsc_ids: list[str]) -> list[dict[str, Any]]:
    """One entry per number; "status" is "listed" (with all part fields),
    "not_listed" or "error" (with "error")."""
    report: list[dict[str, Any]] = []
    for lcsc_id in lcsc_ids:
        try:
            result = api.get_jlcpcb_component(lcsc_id)
        except JlcpcbConnectionError as e:
            report.append({"lcsc": lcsc_id, "status": "error", "error": str(e)})
            continue
        if result is None:
            report.append({"lcsc": lcsc_id, "status": "not_listed"})
        else:
            report.append({"lcsc": lcsc_id, "status": "listed", **result})
    return report


def download_datasheets(
    api: EasyedaApi, lcsc_ids: list[str], out_dir: str
) -> list[dict[str, Any]]:
    """One entry per number; "status" is "saved" (with "file"), "unavailable"
    or "error" (JLCPCB/LCSC not reachable or unexpected response), the latter
    two with "error"."""
    report: list[dict[str, Any]] = []
    for lcsc_id in lcsc_ids:
        try:
            path = api.download_datasheet(lcsc_id, out_dir)
        except JlcpcbError as e:
            status = "error" if isinstance(e, JlcpcbConnectionError) else "unavailable"
            report.append(
                {"lcsc": lcsc_id, "status": status, "file": None, "error": str(e)}
            )
            continue
        report.append({"lcsc": lcsc_id, "status": "saved", "file": str(path)})
    return report


# ------------------------------------------------------------
# Output


def fee_type(result: dict[str, Any]) -> str:
    """Basic and Ext-pref have no JLCPCB setup fee; Ext has one per part type."""
    if result.get("type") == "Basic":
        return "Basic"
    return "Ext-pref" if result.get("preferred") else "Ext"


def _cut(value: Any, width: int) -> str:
    text = " ".join(str(value if value is not None else "").split())
    if width and len(text) > width - 1:
        text = text[: width - 2] + "…"
    return text


def _row(result: dict[str, Any]) -> list[str]:
    price = result.get("price")
    return [
        str(result.get("lcsc", "")),
        fee_type(result),
        str(result.get("stock", 0)),
        f"${price}" if price is not None else "-",
        str(result.get("model", "")),
        str(result.get("brand", "")),
        str(result.get("package", "")),
        str(result.get("description", "")),
    ]


def _description_width(out: TextIO) -> int:
    """Full terminal width for DESCRIPTION, a fixed width otherwise."""
    try:
        if out.isatty():
            return max(30, shutil.get_terminal_size().columns - FIXED_COLUMNS_WIDTH)
    except (AttributeError, ValueError, OSError):
        pass
    return DESCRIPTION_WIDTH


def _print_table(rows: list[list[str]], out: TextIO) -> None:
    description_width = _description_width(out)
    for cells in [[name for name, _ in TABLE_COLUMNS], *rows]:
        line = " ".join(
            _cut(cell, width).ljust(width) if width else _cut(cell, description_width)
            for cell, (_, width) in zip(cells, TABLE_COLUMNS)
        )
        out.write(line.rstrip() + "\n")


def _print_search(
    report: list[dict[str, Any]], out: TextIO, filters: SearchFilters
) -> None:
    description = filters.describe()
    for entry in report:
        title = f"### {entry['query']}" + (f"  [{description}]" if description else "")
        shown, total = entry["shown"], entry["total"]
        if entry["status"] == "error":
            out.write(f"{title}\nERROR: {entry['error']}\n\n")
            continue
        if not total:
            out.write(f"{title}  (no matches)\n")
        else:
            note = (
                f", stopped after {MAX_SEARCH_PAGES} pages"
                if entry["truncated"]
                else ""
            )
            more = ", raise --limit for more" if 0 < shown < total or note else ""
            out.write(f"{title}  ({shown} of {total} matches shown{note}{more})\n")
        if entry["results"]:
            _print_table([_row(r) for r in entry["results"]], out)
        elif total:
            out.write("(all matches hidden by --min-stock or as placeholders)\n")
        elif filters.packages:
            out.write(
                "(package names are exact and case-sensitive; search without"
                " --package to see them in the PACKAGE column)\n"
            )
        out.write("\n")


def _print_stock(report: list[dict[str, Any]], out: TextIO) -> None:
    rows = []
    for r in report:
        if r["status"] == "listed":
            rows.append(_row(r))
        elif r["status"] == "not_listed":
            rows.append([r["lcsc"], "-", "0", "-", "NOT LISTED AT JLCPCB", "", "", ""])
        else:
            rows.append([r["lcsc"], "-", "-", "-", "CHECK FAILED", "", "", r["error"]])
    _print_table(rows, out)
    out.write("\n")


def _print_datasheets(report: list[dict[str, Any]], out: TextIO) -> None:
    for entry in report:
        result = entry["file"] or f"FAILED: {entry['error']}"
        out.write(f"{entry['lcsc']}\t{result}\n")


def exit_code(report: dict[str, list[dict[str, Any]]]) -> int:
    statuses = {entry["status"] for section in report.values() for entry in section}
    if "error" in statuses:
        return EXIT_CONNECTION_ERROR
    if statuses & {"not_listed", "unavailable"}:
        return EXIT_NOT_FOUND
    return 0


def run_parts(
    api: EasyedaApi,
    search: list[str] | None = None,
    stock: list[str] | None = None,
    datasheet: list[str] | None = None,
    datasheet_dir: str = ".",
    limit: int = 10,
    filters: SearchFilters | None = None,
    as_json: bool = False,
    out: TextIO | None = None,
) -> int:
    """Run the given commands, print tables or one JSON object with a key per
    command that was run, and return the exit code."""
    stream: TextIO = out or sys.stdout
    filters = filters or SearchFilters()
    report: dict[str, list[dict[str, Any]]] = {}
    if search:
        report["search"] = search_parts(api, search, limit, filters)
    if stock:
        report["stock"] = stock_parts(api, stock)
    if datasheet:
        report["datasheet"] = download_datasheets(api, datasheet, datasheet_dir)

    if as_json:
        json.dump(report, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    else:
        if "search" in report:
            _print_search(report["search"], stream, filters)
        if "stock" in report:
            _print_stock(report["stock"], stream)
        if "datasheet" in report:
            _print_datasheets(report["datasheet"], stream)
        if any(entry["results"] for entry in report.get("search", [])):
            stream.write(NEXT_STEPS)

    return exit_code(report)

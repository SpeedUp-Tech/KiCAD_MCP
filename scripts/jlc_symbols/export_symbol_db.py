#!/usr/bin/env python3
"""
Build an incremental SQLite index of KiCad symbols grouped by MPN.

The script scans one or more directories for `.kicad_sym` files,
extracts each symbol's S-expression together with its library (file stem)
and desired MPN property, and stores them in a SQLite database so future
lookups can be served without reparsing the source libraries.
"""

from __future__ import annotations

import argparse
import logging
import re
import sqlite3
import sys
from pathlib import Path
from typing import Dict, Iterator, List, NamedTuple, Sequence

import sexpdata

LOGGER = logging.getLogger("export_symbol_db")
PROJECT_ROOT = Path(__file__).resolve().parents[2]
PYTHON_ROOT = PROJECT_ROOT / "python"
if str(PYTHON_ROOT) not in sys.path:
    sys.path.insert(0, str(PYTHON_ROOT))

from kicad_catalog.workdir import resolve_write_db_path
from kicad_catalog.sqlite import connect_sqlite


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Index KiCad symbols into a SQLite database."
    )
    parser.add_argument(
        "--symbols-dir",
        action="append",
        dest="symbols_dirs",
        required=True,
        help="Directory containing .kicad_sym files (can be repeated).",
    )
    parser.add_argument(
        "dest",
        type=Path,
        help="Destination SQLite database path.",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress progress logs.",
    )
    return parser.parse_args(argv)


_SYMBOL_HEADER_REGEX = re.compile(r'\(symbol\s+"([^"]+)')
_PIN_TOKEN_SPLIT_RE = re.compile(r'[^A-Z0-9\+\-]+')


class PinToken(NamedTuple):
    raw: str
    nosign: str
    core: str


_PASSIVE_PIN_CORES = {
    "EP",
    "EPAD",
    "PAD",
    "THERMAL",
    "THERMALPAD",
    "POWERPAD",
    "SHIELD",
    "SHLD",
    "SH",
    "CASE",
    "CASING",
    "CHASSIS",
    "SHELL",
    "TAB",
    "MOUNT",
    "MNT",
    "HS",
    "HEATSINK",
    "PADGND",
}
_INPUT_PIN_KEYWORDS = {
    "EN",
    "ENABLE",
    "CE",
    "OE",
    "OEB",
    "OEN",
    "RESET",
    "RST",
    "NRST",
    "MRST",
    "TRST",
    "SRST",
    "CS",
    "CSB",
    "CSN",
    "CSS",
    "SS",
    "LE",
    "LAT",
    "LATCH",
    "WE",
    "WR",
    "WRB",
    "WRN",
    "ADR",
    "ADDR",
    "ADDRESS",
    "CFG",
    "CONFIG",
    "MODE",
    "BOOT",
    "BOOT0",
    "BOOT1",
    "WAKE",
    "WAKEUP",
    "SLEEP",
    "SHDN",
    "SD",
    "PD",
    "PWDN",
    "PWRDN",
    "SET",
    "SETB",
    "SETN",
    "TRIG",
    "SYNC",
    "SYNCIN",
    "FB",
    "FBN",
    "FBP",
    "ILIM",
    "IMAX",
    "ISET",
    "ISEL",
    "SEN",
    "SENSE",
    "ADJ",
    "COMP",
    "COMP1",
    "COMP2",
    "SS",
    "SOFTSTART",
    "DIN",
    "SDI",
    "SI",
    "MOSI",
    "TDI",
    "TMS",
    "TCK",
    "CLKIN",
    "CKIN",
    "REFIN",
    "REFP",
    "REFN",
    "KEY",
    "TEST",
    "SEL",
    "SELECT",
}
_OUTPUT_PIN_KEYWORDS = {
    "OUT",
    "OUTP",
    "OUTN",
    "DOUT",
    "ROUT",
    "LOUT",
    "SOUT",
    "IOUT",
    "TOUT",
    "VOUT",
    "CLKOUT",
    "CKOUT",
    "CKO",
    "CLKO",
    "DO",
    "SDO",
    "MISO",
    "TDO",
    "RDY",
    "DRDY",
    "READY",
    "IRQ",
    "INT",
    "ALERT",
    "ALARM",
    "FAULT",
    "PGOOD",
    "PWRGD",
    "POK",
    "PWRGOOD",
    "STATUS",
    "STAT",
    "FLAG",
    "BUSY",
    "LOCK",
    "TX",
    "TXD",
    "TXP",
    "TXN",
    "TXO",
    "TXOUT",
    "TXA",
    "TXB",
    "PWMOUT",
    "FOUT",
    "TACH",
}
_BIDIR_USB_TOKENS = {"DP", "DM", "D+", "D-"}
_BIDIR_PORT_CORES = {"IOA", "IOB", "IOC", "IOD", "IOE", "IOF", "IOG", "IOH"}
_POWER_IN_EXACT = {
    "VIO",
    "VUSB",
    "VSYS",
    "VCCA",
    "VCCD",
    "VCORE",
    "VCAP",
    "VDRV",
    "VREG",
    "VCCAUX",
    "VDDAUX",
    "VSSA",
    "VSSB",
    "VSSAUX",
    "VPLL",
    "VPP",
    "VCCH",
    "VBACKUP",
}


def iter_symbol_blocks(text: str, *, source: Path) -> Iterator[str]:
    """Yield raw `(symbol ...)` blocks from a .kicad_sym file."""
    i = 0
    length = len(text)
    while True:
        start = text.find("(symbol ", i)
        if start == -1:
            break
        depth = 0
        in_string = False
        escape = False
        pos = start
        while pos < length:
            ch = text[pos]
            if in_string:
                if escape:
                    escape = False
                elif ch == "\\":
                    escape = True
                elif ch == '"':
                    in_string = False
            else:
                if ch == '"':
                    in_string = True
                elif ch == "(":
                    depth += 1
                elif ch == ")":
                    depth -= 1
                    if depth == 0:
                        pos += 1
                        yield text[start:pos]
                        i = pos
                        break
            pos += 1
        else:
            if depth > 0:
                LOGGER.warning("Unbalanced parentheses in %s; attempting to auto-close.", source)
                patched = text[start:length] + (")" * depth)
                yield patched
            else:
                LOGGER.warning("Truncated symbol definition in %s.", source)
            break


def _sexp_to_str(atom: object) -> str:
    if isinstance(atom, sexpdata.Symbol):
        return atom.value()
    return str(atom)


def _extract_symbol_name_hint(block: str) -> str | None:
    match = _SYMBOL_HEADER_REGEX.match(block)
    if match:
        return match.group(1)
    return None


def _count_symbols_in_file(path: Path) -> int:
    try:
        text = path.read_text(encoding="utf-8")
    except Exception:
        return 0
    return sum(1 for _ in iter_symbol_blocks(text, source=path))


def _tokenize_pin_name(name: str) -> List[PinToken]:
    upper = name.strip().upper()
    if not upper:
        return []
    upper = upper.replace("\\", "/")
    raw_tokens = [token for token in _PIN_TOKEN_SPLIT_RE.split(upper) if token]
    tokens: List[PinToken] = []
    for token in raw_tokens:
        nosign = token.lstrip("+-~")
        if not nosign:
            continue
        nosign = nosign.rstrip("~")
        core = nosign.rstrip("0123456789")
        core = core.rstrip("+-")
        if not core:
            core = nosign
        tokens.append(PinToken(raw=token, nosign=nosign, core=core))
    return tokens


def _compact_pin_name(name: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", name.upper())


def _matches_no_connect(tokens: List[PinToken], compact: str) -> bool:
    if not tokens and not compact:
        return False
    if re.fullmatch(r"NC\d*", compact):
        return True
    if compact in {"NOCONNECT", "NOCON"}:
        return True
    for token in tokens:
        if token.core == "NC" and (token.nosign == "NC" or token.nosign[2:].isdigit()):
            return True
    return False


def _matches_passive(tokens: List[PinToken]) -> bool:
    for token in tokens:
        if token.core in _PASSIVE_PIN_CORES:
            return True
        if token.core.endswith("PAD"):
            return True
        if "THERM" in token.core:
            return True
        if token.core in {"EP", "EPAD"}:
            return True
    return False


def _token_has_digits(token: PinToken) -> bool:
    return any(ch.isdigit() for ch in token.nosign)


def _matches_power_in(tokens: List[PinToken]) -> bool:
    for token in tokens:
        core = token.core
        nosign = token.nosign
        if not core:
            continue
        if "GND" in core or core == "GROUND":
            return True
        if any(marker in core for marker in ("VCC", "VDD", "VSS", "VEE")):
            return True
        if core in _POWER_IN_EXACT:
            return True
        if core.startswith("VREF"):
            return True
        if re.fullmatch(r"VIN\d*", nosign):
            return True
        if nosign == "VIN":
            return True
    return False


def _matches_bidirectional(tokens: List[PinToken], upper_name: str) -> bool:
    usb_present = "USB" in upper_name
    has_usb_diff = False
    if usb_present:
        usb_tokens = {token.core for token in tokens}
        usb_tokens.update(token.nosign for token in tokens)
        if any(val in usb_tokens for val in _BIDIR_USB_TOKENS):
            has_usb_diff = True
    if usb_present and has_usb_diff:
        return True
    for token in tokens:
        core = token.core
        nosign = token.nosign
        if core in {"SDA", "SCL"}:
            return True
        if core.startswith("SDA") or core.startswith("SCL"):
            return True
        if core in {"SDIO", "MDIO"} or "SDIO" in core or "MDIO" in core:
            return True
        if core.startswith("DQ"):
            return True
        if core in {"CANH", "CANL"}:
            return True
        if core.startswith("GPIO"):
            return True
        if core in _BIDIR_PORT_CORES:
            return True
        if core.startswith("IO") and (_token_has_digits(token) or core in _BIDIR_PORT_CORES):
            return True
        if nosign.startswith("USB") and any(val in nosign for val in ("DP", "DM")):
            return True
    return False


def _matches_output(tokens: List[PinToken], upper_name: str) -> bool:
    for token in tokens:
        core = token.core
        nosign = token.nosign
        if not core:
            continue
        if core in _OUTPUT_PIN_KEYWORDS:
            return True
        if core.endswith("OUT") or core.startswith("OUT"):
            return True
        if core.endswith("DO") or core in {"DO", "SDO", "MISO", "TDO"}:
            return True
        if core in {"INT", "IRQ", "ALERT", "ALARM", "FAULT", "BUSY", "FLAG", "STATUS", "STAT"}:
            return True
        if core in {"PGOOD", "PWRGD", "POK", "PWRGOOD"}:
            return True
        if core == "RDY" or core == "DRDY":
            return True
        if nosign in {"Q", "QA", "QB", "QC", "QD", "QE", "QF", "QG", "QH"}:
            return True
        if re.fullmatch(r"Q\d+[A-Z]?", nosign):
            return True
        if re.fullmatch(r"TX\d*", nosign) or nosign in {"TX", "TXD", "TXP", "TXN", "TXO"}:
            return True
        if core.startswith("TX") and not nosign.endswith("EN"):
            return True
    if "PGOOD" in upper_name or "PWRGD" in upper_name:
        return True
    if "CLKOUT" in upper_name:
        return True
    return False


def _matches_input(tokens: List[PinToken]) -> bool:
    for token in tokens:
        core = token.core
        nosign = token.nosign
        if not core:
            continue
        if core in _INPUT_PIN_KEYWORDS:
            return True
        if core.endswith("IN") and core not in {"GNDIN"}:
            return True
        if core.startswith("IN") and not core.startswith("INT"):
            return True
        if core in {"FB", "FBN", "FBP", "SENSE", "COMP", "ADJ"}:
            return True
        if nosign in {"RX", "RXD"}:
            return True
    return False


def _guess_pin_type(pin_name: str) -> str | None:
    tokens = _tokenize_pin_name(pin_name)
    if not tokens:
        return None
    upper_name = pin_name.upper()
    compact = _compact_pin_name(pin_name)
    if _matches_no_connect(tokens, compact):
        return "no_connect"
    if _matches_passive(tokens):
        return "passive"
    if _matches_power_in(tokens):
        return "power_in"
    if _matches_bidirectional(tokens, upper_name):
        return "bidirectional"
    if _matches_output(tokens, upper_name):
        return "output"
    if _matches_input(tokens):
        return "input"
    return None


def _pin_name_from_expr(pin_expr: Sequence[object]) -> str | None:
    for element in pin_expr:
        if isinstance(element, list) and element:
            head = element[0]
            if isinstance(head, sexpdata.Symbol) and head.value() == "name" and len(element) >= 2:
                return _sexp_to_str(element[1]).strip()
    return None


def _apply_pin_type_inference(symbol_expr: list) -> bool:
    changed = False

    def _walk(node: object) -> None:
        nonlocal changed
        if not isinstance(node, list) or not node:
            return
        head = node[0]
        if isinstance(head, sexpdata.Symbol) and head.value() == "pin":
            if len(node) < 2 or not isinstance(node[1], sexpdata.Symbol):
                return
            current_type = node[1].value()
            if current_type != "unspecified":
                return
            pin_name = _pin_name_from_expr(node)
            if not pin_name:
                return
            guessed = _guess_pin_type(pin_name)
            if guessed:
                node[1] = sexpdata.Symbol(guessed)
                changed = True
            return
        for child in node:
            if isinstance(child, list):
                _walk(child)

    _walk(symbol_expr)
    return changed


def _format_atom(atom: object) -> str:
    if isinstance(atom, sexpdata.Symbol):
        return atom.value()
    if isinstance(atom, str):
        return sexpdata.dumps(atom)
    if isinstance(atom, (int, float)):
        return str(atom)
    return sexpdata.dumps(atom)


def _format_symbol_expr(expr: object, indent: int = 0) -> str:
    indent_str = "  " * indent
    if not isinstance(expr, list):
        return indent_str + _format_atom(expr)
    if not expr:
        return indent_str + "()"
    head = _format_atom(expr[0])
    parts: List[str] = []
    for element in expr[1:]:
        if isinstance(element, list):
            parts.append("\n" + _format_symbol_expr(element, indent + 1))
        else:
            parts.append(" " + _format_atom(element))
    return f"{indent_str}({head}{''.join(parts)})"


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS symbol_index (
            mpn TEXT NOT NULL,
            library TEXT NOT NULL,
            sexp TEXT NOT NULL,
            PRIMARY KEY (mpn, library)
        )
        """
    )
    conn.execute("PRAGMA journal_mode=WAL;")


def process_library(
    path: Path,
    conn: sqlite3.Connection,
    on_symbol_processed,
) -> tuple[int, int, int]:
    """Insert symbols from `path` into the database; return stats."""
    text = path.read_text(encoding="utf-8")
    library = path.stem
    inserted = 0
    skipped_existing = 0
    skipped_missing_mpn = 0

    for block in iter_symbol_blocks(text, source=path):
        symbol_hint = _extract_symbol_name_hint(block) or "<unknown>"
        try:
            symbol_expr = sexpdata.loads(block)
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(
                f"Failed to parse symbol '{symbol_hint}' in {path}: {exc}"
            ) from exc

        if len(symbol_expr) < 2:
            skipped_missing_mpn += 1
            on_symbol_processed()
            continue
        symbol_name = _sexp_to_str(symbol_expr[1]).strip()
        if not symbol_name:
            skipped_missing_mpn += 1
            on_symbol_processed()
            continue

        pins_updated = _apply_pin_type_inference(symbol_expr)
        sexp_text = _format_symbol_expr(symbol_expr).strip() if pins_updated else block.strip()

        cursor = conn.execute(
            "INSERT OR IGNORE INTO symbol_index (mpn, library, sexp) VALUES (?, ?, ?)",
            (symbol_name, library, sexp_text),
        )
        if cursor.rowcount == 1:
            inserted += 1
        else:
            skipped_existing += 1
        on_symbol_processed()

    return inserted, skipped_existing, skipped_missing_mpn


def _gather_symbol_files(symbols_dirs: Sequence[Path]) -> tuple[List[Path], Dict[Path, int], int]:
    files: List[Path] = []
    counts: Dict[Path, int] = {}
    total_symbols = 0
    for directory in symbols_dirs:
        if not directory.is_dir():
            LOGGER.warning("Symbols directory not found: %s", directory)
            continue
        for sym_file in sorted(directory.rglob("*.kicad_sym")):
            files.append(sym_file)
            count = _count_symbols_in_file(sym_file)
            counts[sym_file] = count
            total_symbols += count
    return files, counts, total_symbols


def _print_dual_progress(
    files_done: int,
    files_total: int,
    symbols_done: int,
    symbols_total: int,
) -> None:
    def fmt(done: int, total: int) -> str:
        if total == 0:
            return "0/0 (  0.0%)"
        percent = (done / total) * 100
        return f"{done}/{total} ({percent:5.1f}%)"

    print(
        f"\rFiles: {fmt(files_done, files_total)} | Symbols: {fmt(symbols_done, symbols_total)}",
        end="",
        flush=True,
    )


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.ERROR if args.quiet else logging.INFO,
        format="%(levelname)s: %(message)s",
    )

    symbols_dirs = [Path(p).resolve() for p in args.symbols_dirs]
    dest = args.dest.resolve()
    safe_dest = resolve_write_db_path(dest, prefix="symbol", repo_root=PROJECT_ROOT)
    if safe_dest != dest:
        print(f"Destination DB is protected; writing to working copy instead: {safe_dest}")
    dest = safe_dest
    dest.parent.mkdir(parents=True, exist_ok=True)

    conn = connect_sqlite(dest)
    try:
        ensure_schema(conn)
        total_inserted = 0
        total_exists = 0
        total_missing = 0
        symbol_files, symbol_counts, total_symbols = _gather_symbol_files(symbols_dirs)
        total_files = len(symbol_files)
        processed_files = 0
        processed_symbols = 0

        def advance_symbol_progress() -> None:
            nonlocal processed_symbols
            processed_symbols += 1
            if not args.quiet:
                _print_dual_progress(processed_files, total_files, processed_symbols, total_symbols)

        LOGGER.info("Found %d symbol libraries to process.", total_files)

        for index, sym_file in enumerate(symbol_files, start=1):
            counts = process_library(sym_file, conn, advance_symbol_progress)
            total_inserted += counts[0]
            total_exists += counts[1]
            total_missing += counts[2]
            processed_files = index
            if not args.quiet:
                _print_dual_progress(processed_files, total_files, processed_symbols, total_symbols)
        conn.commit()
        if not args.quiet and (total_files or total_symbols):
            print()  # newline after progress updates

        LOGGER.info(
            "Done. inserted=%d, skipped_existing=%d, skipped_missing_mpn=%d",
            total_inserted,
            total_exists,
            total_missing,
        )
    finally:
        conn.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

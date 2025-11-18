#!/usr/bin/env python3
"""
CLI wrapper for python.skidl_tools.pyspice_converter.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import textwrap

from python.skidl_tools import DEFAULT_MAPPING_PATH, convert_skidl_module


def main() -> None:
    parser = argparse.ArgumentParser(description="Convert SKiDL module to a PySpice-ready module.")
    parser.add_argument("--input", required=True, type=Path, help="Path to the SKiDL Python file.")
    parser.add_argument("--subckt", required=True, help="Subcircuit function name to instantiate.")
    parser.add_argument("--output", required=True, type=Path, help="Destination path for the PySpice module.")
    parser.add_argument(
        "--mapping",
        type=Path,
        default=DEFAULT_MAPPING_PATH,
        help="JSON mapping of (library,name) to SPICE models.",
    )
    parser.add_argument(
        "--subckt-output",
        type=str,
        default=None,
        help="Optional override for the generated subcircuit name.",
    )
    args = parser.parse_args()

    subckt_name = convert_skidl_module(
        input_path=args.input,
        subckt_name=args.subckt,
        output_path=args.output,
        mapping_path=args.mapping,
        subckt_output=args.subckt_output,
    )
    print(
        textwrap.dedent(
            f"""
            PySpice module written to {args.output}.
            Subcircuit exported as {subckt_name}.
            """
        ).strip()
    )


if __name__ == "__main__":
    main()


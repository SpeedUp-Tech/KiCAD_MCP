"""
Regression tests for adaptive sizing in symbol builders.
"""

from __future__ import annotations

import re
import unittest

from python.commands.database_tools.symbol_builders.connector_builder import build_connector_symbol
from python.commands.database_tools.symbol_builders.ic_builder import build_ic_symbol


_RECT_RE = re.compile(
    r"\(rectangle\s+\(start\s+([-\d.]+)\s+([-\d.]+)\)\s+\(end\s+([-\d.]+)\s+([-\d.]+)\)"
)


def _first_rectangle_width_mm(sexp: str) -> float:
    match = _RECT_RE.search(sexp)
    if not match:
        raise AssertionError("No rectangle found in symbol S-expression")
    x1 = float(match.group(1))
    x2 = float(match.group(3))
    return abs(x2 - x1)


def _assert_multiple_of(test_case: unittest.TestCase, value: float, step: float) -> None:
    test_case.assertAlmostEqual(value / step, round(value / step), places=6)


class TestSymbolBuildersAdaptiveWidth(unittest.TestCase):
    def test_ic_width_increases_with_longer_names(self):
        pins_short = [
            {"name": "A", "number": "1", "orientation": "left"},
            {"name": "B", "number": "2", "orientation": "left"},
            {"name": "C", "number": "3", "orientation": "right"},
            {"name": "D", "number": "4", "orientation": "right"},
        ]
        pins_long = [
            {"name": "VERY_LONG_SIGNAL_NAME_1", "number": "1", "orientation": "left"},
            {"name": "VERY_LONG_SIGNAL_NAME_2", "number": "2", "orientation": "left"},
            {"name": "VERY_LONG_SIGNAL_NAME_3", "number": "3", "orientation": "right"},
            {"name": "VERY_LONG_SIGNAL_NAME_4", "number": "4", "orientation": "right"},
        ]

        sexp_short = build_ic_symbol({"name": "Test:IC_Short", "pins": pins_short})
        sexp_long = build_ic_symbol({"name": "Test:IC_Long", "pins": pins_long})

        width_short = _first_rectangle_width_mm(sexp_short)
        width_long = _first_rectangle_width_mm(sexp_long)
        _assert_multiple_of(self, width_short, 2.54)
        _assert_multiple_of(self, width_long, 2.54)
        self.assertGreater(width_long, width_short)

    def test_ic_width_increases_with_pin_count(self):
        small_pins = []
        for i in range(8):
            side = "left" if i < 4 else "right"
            small_pins.append({"name": f"P{i+1}", "number": str(i + 1), "orientation": side})

        large_pins = []
        for i in range(64):
            side = "left" if i < 32 else "right"
            large_pins.append({"name": f"P{i+1}", "number": str(i + 1), "orientation": side})

        sexp_small = build_ic_symbol({"name": "Test:IC_8", "pins": small_pins})
        sexp_large = build_ic_symbol({"name": "Test:IC_64", "pins": large_pins})

        width_small = _first_rectangle_width_mm(sexp_small)
        width_large = _first_rectangle_width_mm(sexp_large)
        _assert_multiple_of(self, width_small, 2.54)
        _assert_multiple_of(self, width_large, 2.54)
        self.assertGreater(width_large, width_small)

    def test_connector_width_increases_with_longer_names(self):
        pins_short = [{"name": "A", "number": "1"}, {"name": "B", "number": "2"}]
        pins_long = [
            {"name": "VERY_LONG_CONNECTOR_PIN_1", "number": "1"},
            {"name": "VERY_LONG_CONNECTOR_PIN_2", "number": "2"},
        ]

        sexp_short = build_connector_symbol({"name": "Test:J_Short", "pins": pins_short, "orientation": "right"})
        sexp_long = build_connector_symbol({"name": "Test:J_Long", "pins": pins_long, "orientation": "right"})

        width_short = _first_rectangle_width_mm(sexp_short)
        width_long = _first_rectangle_width_mm(sexp_long)
        _assert_multiple_of(self, width_short, 2.54)
        _assert_multiple_of(self, width_long, 2.54)
        self.assertGreater(width_long, width_short)


if __name__ == "__main__":
    unittest.main()

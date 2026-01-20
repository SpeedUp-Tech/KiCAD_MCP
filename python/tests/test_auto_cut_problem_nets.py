from __future__ import annotations

import unittest

from python.netlist_to_schematic.auto_cut import analyze_net_layout, select_problem_nets


class TestAutoCutProblemNets(unittest.TestCase):
    def test_analyze_net_layout_counts_crossings(self) -> None:
        elk_output = {
            "edges": [
                {
                    "id": "e_A",
                    "properties": {"net_name": "A"},
                    "sections": [
                        {
                            "startPoint": {"x": 0, "y": 0},
                            "endPoint": {"x": 10, "y": 0},
                            "bendPoints": [],
                        }
                    ],
                },
                {
                    "id": "e_B",
                    "properties": {"net_name": "B"},
                    "sections": [
                        {
                            "startPoint": {"x": 5, "y": -20},
                            "endPoint": {"x": 5, "y": 20},
                            "bendPoints": [],
                        }
                    ],
                },
            ]
        }

        stats = analyze_net_layout(elk_output)
        self.assertEqual(stats["A"].crossings, 1)
        self.assertEqual(stats["B"].crossings, 1)
        self.assertEqual(stats["A"].max_length_mm, 10)
        self.assertEqual(stats["B"].max_length_mm, 40)

    def test_select_problem_nets_prefers_longer_when_tied(self) -> None:
        elk_output = {
            "edges": [
                {
                    "id": "e_A",
                    "properties": {"net_name": "A"},
                    "sections": [{"startPoint": {"x": 0, "y": 0}, "endPoint": {"x": 10, "y": 0}, "bendPoints": []}],
                },
                {
                    "id": "e_B",
                    "properties": {"net_name": "B"},
                    "sections": [{"startPoint": {"x": 5, "y": -20}, "endPoint": {"x": 5, "y": 20}, "bendPoints": []}],
                },
            ]
        }
        stats = analyze_net_layout(elk_output)
        picked = select_problem_nets(
            stats,
            exclude=set(),
            max_nets=1,
            min_crossings=1,
            min_max_length_mm=0.0,
            min_max_backtrack_mm=0.0,
        )
        self.assertEqual(picked, ["B"])

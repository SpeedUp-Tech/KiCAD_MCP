from __future__ import annotations

import unittest

from python.netlist_to_schematic.auto_cut import analyze_edge_layout, select_problem_edges


class TestAutoCutProblemEdges(unittest.TestCase):
    def test_analyze_edge_layout_counts_crossings(self) -> None:
        elk_output = {
            "edges": [
                {
                    "id": "e_A",
                    "sources": ["U1.1"],
                    "targets": ["R1.1"],
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
                    "sources": ["U2.1"],
                    "targets": ["R2.1"],
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

        stats = analyze_edge_layout(elk_output)
        self.assertEqual(stats["e_A"].crossings, 1)
        self.assertEqual(stats["e_B"].crossings, 1)
        self.assertEqual(stats["e_A"].length_mm, 10)
        self.assertEqual(stats["e_B"].length_mm, 40)

    def test_select_problem_edges_prefers_longer_when_tied(self) -> None:
        elk_output = {
            "edges": [
                {
                    "id": "e_A",
                    "sources": ["U1.1"],
                    "targets": ["R1.1"],
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
                    "sources": ["U2.1"],
                    "targets": ["R2.1"],
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
        stats = analyze_edge_layout(elk_output)
        picked = select_problem_edges(
            stats,
            exclude_edges=set(),
            exclude_nets=set(),
            max_edges=1,
            min_crossings=1,
            min_length_mm=0.0,
            min_backtrack_mm=0.0,
        )
        self.assertEqual(picked, ["e_B"])


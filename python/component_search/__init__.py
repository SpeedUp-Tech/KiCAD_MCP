"""
Lightweight component search interface for the JLCPCB parts database.

This package keeps the search functionality self-contained so it can be
ported to other projects with minimal changes.
"""

from .service import (
    ComponentSearchConfig,
    SearchMPNResult,
    search_datasheet,
    search_mpn_part,
)

__all__ = [
    "ComponentSearchConfig",
    "SearchMPNResult",
    "search_datasheet",
    "search_mpn_part",
]

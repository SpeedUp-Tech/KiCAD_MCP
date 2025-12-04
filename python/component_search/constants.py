from __future__ import annotations

import re
from typing import Dict, Tuple

_REGEX_REPLACEMENTS: Tuple[Tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"pmosfet", re.IGNORECASE), "p channel mosfet"),
    (re.compile(r"p[\s\-]*mos\b", re.IGNORECASE), "p channel mosfet"),
    (re.compile(r"nmosfet", re.IGNORECASE), "n channel mosfet"),
    (re.compile(r"n[\s\-]*mos\b", re.IGNORECASE), "n channel mosfet"),
    (re.compile(r"rds[\s_\-]*on", re.IGNORECASE), "rds on"),
    (re.compile(r"v[\s_\-]*dss", re.IGNORECASE), "vdss"),
)

_TOKEN_EXPANSIONS: Dict[str, Tuple[str, ...]] = {
    "pmos": ("p-channel", "mosfet"),
    "p": ("p-channel",),
    "p-channel": ("mosfet",),
    "nmos": ("n-channel", "mosfet"),
    "n": ("n-channel",),
    "n-channel": ("mosfet",),
    "rds": ("resistance",),
    "vdss": ("voltage",),
    "id": ("current",),
}

_STOPWORD_TOKENS: frozenset[str] = frozenset(
    [
        "transistor",
        "channel",
        "type",
        "resistance",
        "voltage",
        "current",
        "continuous",
        "drain",
        "source",
        "gate",
        "and",
        "or",
        "the",
        "of",
        "for",
    ]
)

_EXCLUDED_FAMILIES: Tuple[str, ...] = ("Resistors", "Capacitors")

FILTERED_FTS_TABLE = "v_components_search_filtered_fts"

_IC_FAMILIES: Tuple[str, ...] = (
    "ADC/DAC/Data Conversion",
    "Amplifiers",
    "Amplifiers/Comparators",
    "Clock and Timing",
    "Clock/Timing",
    "Communication Interface Chip",
    "Communication Interface Chip/UART/485/232",
    "Data Acquisition",
    "Data Converters",
    "Embedded Processors & Controllers",
    "IoT/Communication Modules",
    "Interface",
    "Interface ICs",
    "LED Drivers",
    "Logic",
    "Logic ICs",
    "Memory",
    "Motor Driver ICs",
    "Nixie Tube Driver/LED Driver",
    "Operational Amplifier/Comparator",
    "Optocoupler",
    "Optocoupler/LED/Digital Tube/Photoelectric Device",
    "Optocouplers & LEDs & Infrared",
    "Optocouplers/Photocouplers",
    "Optoisolators",
    "Photoelectric Devices",
    "Power Management",
    "Power Management (PMIC)",
    "Power Management ICs",
    "Power Modules",
    "Power Supply Chip",
    "Radio Frequency Chip/Antenna",
    "RF & Radio",
    "RF And Wireless",
    "RTC/Clock Chip",
    "Sensors",
    "Signal Isolation Devices",
    "Single Chip Microcomputer/Microcontroller",
)

_IMPORTANT_ATTRIBUTE_KEYS: Tuple[str, ...] = (
    "Type",
    "Drain Source Voltage (Vdss)",
    "Drain Source On Resistance (RDS(on)@Vgs,Id)",
    "Continuous Drain Current (Id)",
    "Power Dissipation (Pd)",
    "Gate Threshold Voltage (Vgs(th)@Id)",
)

_VALUE_UNIT_RE = re.compile(r"^([0-9]+(?:\.[0-9]+)?)([a-z\u00b5\u03bc\u03a9]+)$", re.IGNORECASE)

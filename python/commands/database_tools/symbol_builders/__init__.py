"""
Unified symbol builder interface.

This module provides a single entry point for building KiCad symbol S-expressions
for various component types (ICs, transistors, diodes, passives, connectors, etc.).
"""

from typing import Dict, Any

from .ic_builder import build_ic_symbol
from .transistor_builder import build_nmos_symbol, build_pmos_symbol, build_npn_symbol, build_pnp_symbol
from .diode_builder import build_diode_symbol, build_zener_symbol, build_led_symbol
from .passive_builder import build_resistor_symbol, build_capacitor_symbol, build_inductor_symbol
from .connector_builder import build_connector_symbol
from .misc_builder import build_optocoupler_symbol, build_transformer_symbol

# Symbol type to builder function mapping
_BUILDERS = {
    # ICs (rectangular)
    "IC": build_ic_symbol,
    
    # Transistors
    "NMOS": build_nmos_symbol,
    "PMOS": build_pmos_symbol,
    "NPN": build_npn_symbol,
    "PNP": build_pnp_symbol,
    
    # Diodes
    "DIODE": build_diode_symbol,
    "ZENER": build_zener_symbol,
    "LED": build_led_symbol,
    
    # Passives
    "RESISTOR": build_resistor_symbol,
    "CAPACITOR": build_capacitor_symbol,
    "INDUCTOR": build_inductor_symbol,
    
    # Connectors
    "CONNECTOR": build_connector_symbol,
    
    # Misc
    "OPTOCOUPLER": build_optocoupler_symbol,
    "TRANSFORMER": build_transformer_symbol,
}


def get_supported_types() -> list:
    """Return list of supported symbol types."""
    return list(_BUILDERS.keys())


def build_symbol(symbol_type: str, params: Dict[str, Any]) -> str:
    """
    Build a KiCad symbol S-expression.
    
    Args:
        symbol_type: One of the supported symbol types (IC, NMOS, PMOS, etc.)
                     Case-insensitive.
        params: Symbol parameters. Common fields:
            - name (str): Symbol name (required)
            - pins (dict/list): Pin definitions (type-specific format)
            - footprint (str): Footprint reference
            - properties (dict): Additional properties (reference, value, datasheet, etc.)
    
    Returns:
        S-expression string for the symbol
    
    Raises:
        ValueError: If symbol_type is not supported
        KeyError: If required parameters are missing
    """
    symbol_type_upper = symbol_type.upper()
    
    if symbol_type_upper not in _BUILDERS:
        supported = ", ".join(get_supported_types())
        raise ValueError(f"Unsupported symbol type: {symbol_type}. Supported types: {supported}")
    
    builder = _BUILDERS[symbol_type_upper]
    return builder(params)

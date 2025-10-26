from skip import Schematic
# Symbol class might not be directly importable in the current version
import os
import glob
import logging
from typing import Dict, Any, List, Optional, Tuple

logger = logging.getLogger('kicad_interface')


def _format_float(value: float) -> str:
    """Format float to KiCAD-friendly string."""
    return f"{value:.4f}".rstrip('0').rstrip('.') if isinstance(value, float) else str(value)


def symbol_name_from_qualified(qualified: str) -> str:
    """Extract the symbol name from library-qualified identifier."""
    return qualified.split(':', 1)[1] if ':' in qualified else qualified

class LibraryManager:
    """Manage symbol libraries"""

    @staticmethod
    def list_available_libraries(search_paths=None):
        """List all available symbol libraries"""
        if search_paths is None:
            # Default library paths based on common KiCAD installations
            # This would need to be configured for the specific environment
            search_paths = [
                "C:/Program Files/KiCad/*/share/kicad/symbols/*.kicad_sym",  # Windows path pattern
                "/usr/share/kicad/symbols/*.kicad_sym",                      # Linux path pattern
                "/Applications/KiCad/KiCad.app/Contents/SharedSupport/symbols/*.kicad_sym",  # macOS path pattern
                os.path.expanduser("~/Documents/KiCad/*/symbols/*.kicad_sym")  # User libraries pattern
            ]

        libraries = []
        for path_pattern in search_paths:
            try:
                # Use glob to find all matching files
                matching_libs = glob.glob(path_pattern, recursive=True)
                libraries.extend(matching_libs)
            except Exception as e:
                logger.error(f"Error searching for libraries at {path_pattern}: {e}")

        # Extract library names from paths
        library_names = [os.path.splitext(os.path.basename(lib))[0] for lib in libraries]
        logger.info(
            "Found %d libraries: %s%s",
            len(library_names),
            ', '.join(library_names[:10]),
            '...' if len(library_names) > 10 else ''
        )
        
        # Return both full paths and library names
        return {"paths": libraries, "names": library_names}

    @staticmethod
    def create_symbol(params: Dict[str, Any]) -> Dict[str, Any]:
        """Create or extend a KiCAD symbol library with a new symbol definition."""
        try:
            library_path = params.get("libraryPath")
            symbol_name = params.get("symbolName")

            if not library_path or not symbol_name:
                return {
                    "success": False,
                    "message": "libraryPath and symbolName are required"
                }

            library_path = os.path.abspath(os.path.expanduser(library_path))
            os.makedirs(os.path.dirname(library_path), exist_ok=True)

            library_name = params.get("libraryName")
            if not library_name:
                library_name = os.path.splitext(os.path.basename(library_path))[0]

            qualified_symbol = symbol_name if ":" in symbol_name else f"{library_name}:{symbol_name}"

            pins: List[Dict[str, Any]] = params.get("pins") or []
            body_width = params.get("bodyWidth", 10.0)
            body_height = params.get("bodyHeight")
            properties: Dict[str, Any] = params.get("properties") or {}

            symbol_entry = LibraryManager._build_symbol_entry(
                qualified_symbol,
                pins,
                properties,
                body_width,
                body_height
            )

            if os.path.exists(library_path):
                with open(library_path, "r", encoding="utf-8") as fp:
                    existing = fp.read()
                if qualified_symbol in existing:
                    return {
                        "success": False,
                        "message": "Symbol already exists in library",
                        "errorDetails": qualified_symbol
                    }
                trimmed = existing.rstrip()
                if trimmed.endswith(')'):
                    updated = trimmed[:-1] + "\n" + symbol_entry + "\n)\n"
                else:
                    updated = existing + "\n" + symbol_entry + "\n)\n"
            else:
                header = '(kicad_symbol_lib (version 20211014) (generator "KiCAD-MCP"))\n'
                updated = header + symbol_entry + "\n)\n"

            with open(library_path, "w", encoding="utf-8") as fp:
                fp.write(updated)

            logger.info(f"Created symbol {qualified_symbol} in {library_path}")
            return {
                "success": True,
                "message": "Created symbol",
                "libraryPath": library_path,
                "symbolName": qualified_symbol
            }

        except Exception as exc:
            logger.error(f"Error creating symbol: {exc}")
            return {
                "success": False,
                "message": "Failed to create symbol",
                "errorDetails": str(exc)
            }

    @staticmethod
    def _build_symbol_entry(
        qualified_symbol: str,
        pins: List[Dict[str, Any]],
        properties: Dict[str, Any],
        body_width: float,
        body_height: Optional[float] = None
    ) -> str:
        """Construct the S-expression for the symbol."""

        if not pins:
            pins = [
                {"name": "PIN1", "number": "1", "orientation": "left"},
                {"name": "PIN2", "number": "2", "orientation": "right"}
            ]

        orientation_rotation = {
            "left": 180,
            "right": 0,
            "up": 90,
            "down": 270
        }

        pin_spacing = 2.54
        processed: List[Dict[str, Any]] = []
        total = len(pins)

        for index, pin in enumerate(pins):
            pin_copy = dict(pin)
            orientation = pin_copy.get("orientation")
            if not orientation:
                orientation = 'left' if index < total / 2 else 'right'
            pin_copy['orientation'] = orientation
            processed.append(pin_copy)

        left_count = sum(1 for p in processed if p['orientation'] in ('left', 'up'))
        right_count = sum(1 for p in processed if p['orientation'] in ('right', 'down'))
        left_index = 0
        right_index = 0
        left_start = ((left_count - 1) * pin_spacing / 2) if left_count else 0.0
        right_start = ((right_count - 1) * pin_spacing / 2) if right_count else 0.0

        formatted_pins: List[str] = []

        for index, pin in enumerate(processed):
            name = str(pin.get("name", f"PIN{index+1}"))
            number = str(pin.get("number", str(index + 1)))
            pin_type = pin.get("type", "passive")
            length = float(pin.get("length", 2.54))
            orientation = pin.get("orientation", "left")
            rotation = orientation_rotation.get(orientation, 0)

            if "x" in pin and "y" in pin:
                x_pos = float(pin["x"])
                y_pos = float(pin["y"])
            else:
                if orientation in ("left", "up"):
                    x_pos = -body_width / 2 - length
                    y_pos = left_start - left_index * pin_spacing
                    left_index += 1
                else:
                    x_pos = body_width / 2 + length
                    y_pos = right_start - right_index * pin_spacing
                    right_index += 1

            effects = "(effects (font (size 1.27 1.27)))"

            formatted_pins.append(
                "      (" +
                f"pin {pin_type} line (at {_format_float(x_pos)} {_format_float(y_pos)} {rotation}) "
                f"(length {_format_float(length)}) (name \"{name}\" {effects}) "
                f"(number \"{number}\" {effects}))"
            )

        if body_height is None:
            vertical_span = max(len(pins), 2) * pin_spacing
            body_height = max(5.08, vertical_span)

        half_w = body_width / 2
        half_h = body_height / 2

        rectangle = (
            "      (polyline (pts "
            f"(xy {_format_float(-half_w)} {_format_float(half_h)}) "
            f"(xy {_format_float(half_w)} {_format_float(half_h)}) "
            f"(xy {_format_float(half_w)} {_format_float(-half_h)}) "
            f"(xy {_format_float(-half_w)} {_format_float(-half_h)}) "
            f"(xy {_format_float(-half_w)} {_format_float(half_h)})))"
        )

        reference = str(properties.get("reference", "U"))
        value = str(properties.get("value", symbol_name_from_qualified(qualified_symbol)))
        footprint = str(properties.get("footprint", ""))
        datasheet = str(properties.get("datasheet", ""))

        property_lines = [
            f"    (property \"Reference\" \"{reference}\" (at 0 5 0) (effects (font (size 1.27 1.27))))",
            f"    (property \"Value\" \"{value}\" (at 0 -5 0) (effects (font (size 1.27 1.27))))",
            f"    (property \"Footprint\" \"{footprint}\" (at 0 -7 0) (effects (font (size 1.0 1.0))) hide)",
            f"    (property \"Datasheet\" \"{datasheet}\" (at 0 -9 0) (effects (font (size 1.0 1.0))) hide)",
        ]

        for custom_key, custom_value in properties.items():
            if custom_key.lower() in {"reference", "value", "footprint", "datasheet"}:
                continue
            property_lines.append(
                f"    (property \"{custom_key}\" \"{str(custom_value)}\" (at 0 0 0) (effects (font (size 1.0 1.0))) hide)"
            )

        symbol_body = [
            f"  (symbol \"{qualified_symbol}\"",
            *property_lines,
            f"    (symbol \"{qualified_symbol}_0_1\"",
            rectangle,
            *formatted_pins,
            "    )",
            "  )"
        ]

        return "\n".join(symbol_body)

    @staticmethod
    def list_library_symbols(library_path):
        """List all symbols in a library"""
        try:
            # kicad-skip doesn't provide a direct way to simply list symbols in a library
            # without loading each one. We might need to implement this using KiCAD's Python API
            # directly, or by using a different approach.
            # For now, this is a placeholder implementation.
            
            # A potential approach would be to load the library file using KiCAD's Python API
            # or by parsing the library file format.
            # KiCAD symbol libraries are .kicad_sym files which are S-expression format
            logger.warning(f"Attempted to list symbols in library {library_path}. This requires advanced implementation.")
            return []
        except Exception as e:
            logger.error(f"Error listing symbols in library {library_path}: {e}")
            return []

    @staticmethod
    def get_symbol_details(library_path, symbol_name):
        """Get detailed information about a symbol"""
        try:
            # Similar to list_library_symbols, this might require a more direct approach
            # using KiCAD's Python API or by parsing the symbol library.
            logger.warning(f"Attempted to get details for symbol {symbol_name} in library {library_path}. This requires advanced implementation.")
            return {}
        except Exception as e:
            logger.error(f"Error getting symbol details for {symbol_name} in {library_path}: {e}")
            return {}

    @staticmethod
    def search_symbols(query, search_paths=None):
        """Search for symbols matching criteria"""
        try:
            # This would typically involve:
            # 1. Getting a list of all libraries using list_available_libraries
            # 2. For each library, getting a list of all symbols
            # 3. Filtering symbols based on the query
            
            # For now, this is a placeholder implementation
            libraries = LibraryManager.list_available_libraries(search_paths)
            
            results = []
            logger.warning(f"Searched for symbols matching '{query}'. This requires advanced implementation.")
            return results
        except Exception as e:
            logger.error(f"Error searching for symbols matching '{query}': {e}")
            return []
            
    @staticmethod
    def get_default_symbol_for_component_type(component_type, search_paths=None):
        """Get a recommended default symbol for a given component type"""
        # This method provides a simplified way to get a symbol for common component types
        # It's useful when the user doesn't specify a particular library/symbol
        
        # Define common mappings from component type to library/symbol
        common_mappings = {
            "resistor": {"library": "Device", "symbol": "R"},
            "capacitor": {"library": "Device", "symbol": "C"},
            "inductor": {"library": "Device", "symbol": "L"},
            "diode": {"library": "Device", "symbol": "D"},
            "led": {"library": "Device", "symbol": "LED"},
            "transistor_npn": {"library": "Device", "symbol": "Q_NPN_BCE"},
            "transistor_pnp": {"library": "Device", "symbol": "Q_PNP_BCE"},
            "opamp": {"library": "Amplifier_Operational", "symbol": "OpAmp_Dual_Generic"},
            "microcontroller": {"library": "MCU_Module", "symbol": "Arduino_UNO_R3"},
            # Add more common components as needed
        }
        
        # Normalize input to lowercase
        component_type_lower = component_type.lower()
        
        # Try direct match first
        if component_type_lower in common_mappings:
            return common_mappings[component_type_lower]
            
        # Try partial matches
        for key, value in common_mappings.items():
            if component_type_lower in key or key in component_type_lower:
                return value
                
        # Default fallback
        return {"library": "Device", "symbol": "R"}

if __name__ == '__main__':
    # Example Usage (for testing)
    # List available libraries
    libraries = LibraryManager.list_available_libraries()
    if libraries["paths"]:
        first_lib = libraries["paths"][0]
        lib_name = libraries["names"][0]
        logger.debug(f"Testing with first library: {lib_name} ({first_lib})")
        
        # List symbols in the first library
        symbols = LibraryManager.list_library_symbols(first_lib)
        # This will report that it requires advanced implementation
        
    # Get default symbol for a component type
    resistor_sym = LibraryManager.get_default_symbol_for_component_type("resistor")
    logger.info(f"Default symbol for resistor: {resistor_sym['library']}/{resistor_sym['symbol']}")
    
    # Try a partial match
    cap_sym = LibraryManager.get_default_symbol_for_component_type("cap")
    logger.info(f"Default symbol for 'cap': {cap_sym['library']}/{cap_sym['symbol']}")

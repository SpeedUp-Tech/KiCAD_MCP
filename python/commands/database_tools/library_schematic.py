from skip import Schematic
# Symbol class might not be directly importable in the current version
import os
import glob
import logging
import sqlite3
import re
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple

import sexpdata

from kicad_catalog.config import load_catalog_paths
from kicad_catalog.sqlite import connect_sqlite
from kicad_catalog.workdir import resolve_write_db_path

logger = logging.getLogger('kicad_interface')


def _format_float(value: float) -> str:
    """Format float to KiCAD-friendly string."""
    return f"{value:.4f}".rstrip('0').rstrip('.') if isinstance(value, float) else str(value)


def symbol_name_from_qualified(qualified: str) -> str:
    """Extract the symbol name from library-qualified identifier."""
    return qualified.split(':', 1)[1] if ':' in qualified else qualified

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_SYMBOL_DB = PROJECT_ROOT / 'symbol_lib' / 'kicad_symbols.sqlite3'
_PIN_SORT_PATTERN = re.compile(r'(-?\d+(?:\.\d+)?)')

class LibraryManager:
    """Manage symbol libraries"""

    @staticmethod
    def _atom_to_str(atom: Any) -> str:
        """Convert an S-expression atom (Symbol, str, etc.) to string."""
        try:
            if isinstance(atom, sexpdata.Symbol):
                return atom.value()
            if isinstance(atom, str):
                return atom
        except Exception:
            pass
        return str(atom)

    @staticmethod
    def _is_entry(node: Any, name: str) -> bool:
        """Return True if node is an S-expression list with the given head symbol."""
        if not isinstance(node, list) or not node:
            return False
        head = node[0]
        if isinstance(head, sexpdata.Symbol):
            return head.value() == name
        return str(head) == name

    @staticmethod
    def _extract_pin_map_from_symbol_tree(symbol_tree: Any) -> Dict[str, Dict[str, str]]:
        """Traverse the symbol S-expression and return pin metadata keyed by number."""
        pins: Dict[str, Dict[str, str]] = {}

        def _walk(node: Any) -> None:
            if not isinstance(node, list) or not node:
                return

            if LibraryManager._is_entry(node, 'pin'):
                pin_number = ''
                pin_name = ''
                pin_type = 'passive'
                for idx, entry in enumerate(node[1:], start=1):
                    if idx == 1 and not isinstance(entry, list):
                        candidate = LibraryManager._atom_to_str(entry).strip()
                        if candidate:
                            pin_type = candidate
                    elif LibraryManager._is_entry(entry, 'name') and len(entry) > 1:
                        pin_name = LibraryManager._atom_to_str(entry[1]).strip()
                    elif LibraryManager._is_entry(entry, 'number') and len(entry) > 1:
                        pin_number = LibraryManager._atom_to_str(entry[1]).strip()

                if pin_number:
                    existing = pins.get(pin_number, {})
                    chosen_name = pin_name or existing.get('name', '')
                    chosen_type = pin_type or existing.get('type', 'passive')
                    pins[pin_number] = {'name': chosen_name, 'type': chosen_type}
                return

            for child in node[1:]:
                _walk(child)

        _walk(symbol_tree)
        return pins

    @staticmethod
    def _pin_sort_key(pin_number: str) -> Tuple[int, float, str]:
        """Sort pins numerically when possible, otherwise lexicographically."""
        cleaned = pin_number.strip()
        if not cleaned:
            return (2, float('inf'), pin_number)
        match = _PIN_SORT_PATTERN.search(cleaned)
        if match:
            try:
                numeric = float(match.group(1))
                return (0, numeric, cleaned)
            except ValueError:
                pass
        return (1, float('inf'), cleaned)

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
    def get_symbol_pinout(params: Dict[str, Any]) -> Dict[str, Any]:
        """Return pin name/type mappings for a symbol stored in the SQLite symbol index."""
        symbol_input = params.get("symbol") or params.get("symbolName") or params.get("mpn") or params.get("type")
        library_input = params.get("library") or params.get("libraryName")
        db_override = params.get("symbolDbPath")

        symbol_name = str(symbol_input).strip() if symbol_input else ""
        library_name = str(library_input).strip() if library_input else ""

        if not symbol_name or not library_name:
            return {
                "success": False,
                "message": "Both 'symbol' (or mpn/type) and 'library' are required inputs"
            }

        if db_override:
            candidate = Path(str(db_override)).expanduser()
            if not candidate.is_absolute():
                candidate = (PROJECT_ROOT / candidate).resolve()
            db_paths = [candidate]
        else:
            db_paths = list(load_catalog_paths(repo_root=PROJECT_ROOT).symbol_dbs) or [DEFAULT_SYMBOL_DB]

        row: Optional[sqlite3.Row] = None
        last_error: Optional[str] = None

        for db_path in db_paths:
            if not db_path.exists():
                if db_override:
                    return {
                        "success": False,
                        "message": f"Symbol database not found at {db_path}",
                    }
                continue
            try:
                with connect_sqlite(db_path, readonly=True) as conn:
                    cursor = conn.execute(
                        "SELECT mpn, library, sexp FROM symbol_index WHERE library = ? AND mpn = ?",
                        (library_name, symbol_name),
                    )
                    row = cursor.fetchone()
                    if row is not None:
                        break
            except sqlite3.Error as exc:
                last_error = str(exc)
                continue

        if last_error and row is None:
            logger.error("Failed to query symbol index: %s", last_error)
            return {
                "success": False,
                "message": "Unable to query symbol database",
                "errorDetails": last_error,
            }

        if row is None:
            return {
                "success": False,
                "message": f"Symbol '{symbol_name}' not found in library '{library_name}'",
            }

        sexp_text = row["sexp"] if isinstance(row, sqlite3.Row) else row[2]
        try:
            parsed = sexpdata.loads(sexp_text)
        except Exception as exc:
            logger.error("Failed to parse symbol %s:%s S-expression: %s", library_name, symbol_name, exc)
            return {
                "success": False,
                "message": f"Unable to parse symbol data for {library_name}:{symbol_name}",
                "errorDetails": str(exc),
            }

        symbol_node: Optional[Any] = None
        if LibraryManager._is_entry(parsed, 'symbol'):
            symbol_node = parsed
        elif isinstance(parsed, list):
            for entry in parsed:
                if LibraryManager._is_entry(entry, 'symbol'):
                    symbol_node = entry
                    break

        if symbol_node is None:
            return {
                "success": False,
                "message": f"Symbol definition for {library_name}:{symbol_name} does not contain pin data",
            }

        pin_map = LibraryManager._extract_pin_map_from_symbol_tree(symbol_node)
        ordered_keys = sorted(pin_map.keys(), key=LibraryManager._pin_sort_key)
        ordered_pins = [
            {"number": key, "name": pin_map[key]["name"], "type": pin_map[key]["type"]}
            for key in ordered_keys
        ]

        return {
            "success": True,
            "message": f"Retrieved pinout for {library_name}:{symbol_name}",
            "symbol": row["mpn"] if isinstance(row, sqlite3.Row) else symbol_name,
            "library": row["library"] if isinstance(row, sqlite3.Row) else library_name,
            "pinCount": len(ordered_keys),
            "pins": ordered_pins,
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

    @staticmethod
    def add_symbol_entry(params: Dict[str, Any]) -> Dict[str, Any]:
        """Add a symbol entry to the symbol_index database.

        Required parameters:
            mpn: Manufacturer part number / symbol name
            library: Library name for grouping
            sexp: Symbol S-expression definition

        Optional parameters:
            symbolDbPath: Override the default symbol database path
            overwrite: If True, overwrite existing entry (default False)

        Returns:
            Dict with success status and inserted symbol info
        """
        mpn = params.get("mpn")
        library = params.get("library")
        sexp = params.get("sexp")
        db_override = params.get("symbolDbPath")
        overwrite = params.get("overwrite", False)

        # Validate required parameters
        if not isinstance(mpn, str) or not mpn.strip():
            return {
                "success": False,
                "message": "mpn parameter is required and must be a non-empty string",
                "errorDetails": "The 'mpn' parameter was missing or empty",
            }

        if not isinstance(library, str) or not library.strip():
            return {
                "success": False,
                "message": "library parameter is required and must be a non-empty string",
                "errorDetails": "The 'library' parameter was missing or empty",
            }

        if not isinstance(sexp, str) or not sexp.strip():
            return {
                "success": False,
                "message": "sexp parameter is required and must be a non-empty string",
                "errorDetails": "The 'sexp' parameter was missing or empty",
            }

        mpn = mpn.strip()
        library = library.strip()
        sexp = sexp.strip()

        # Resolve database path (writes are redirected to a working copy when the target is protected).
        db_path = DEFAULT_SYMBOL_DB
        db_index = 0
        total_dbs = 1

        if db_override:
            candidate = Path(str(db_override)).expanduser()
            if not candidate.is_absolute():
                candidate = (PROJECT_ROOT / candidate).resolve()
            db_path = candidate
        else:
            configured = list(load_catalog_paths(repo_root=PROJECT_ROOT).symbol_dbs)
            if configured:
                db_path = configured[0]
                total_dbs = len(configured)

        effective_db_path = resolve_write_db_path(
            db_path,
            prefix="symbol",
            index=db_index,
            total=total_dbs,
            repo_root=PROJECT_ROOT,
        )

        try:
            effective_db_path.parent.mkdir(parents=True, exist_ok=True)
            conn = connect_sqlite(effective_db_path)
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS symbol_index (
                    mpn TEXT NOT NULL,
                    library TEXT NOT NULL,
                    sexp TEXT NOT NULL
                )
                """
            )

            # Check if entry already exists
            cursor = conn.execute(
                "SELECT mpn, library FROM symbol_index WHERE mpn = ? AND library = ?",
                (mpn, library),
            )
            existing = cursor.fetchone()

            if existing and not overwrite:
                conn.close()
                return {
                    "success": False,
                    "message": f"Symbol {mpn} already exists in library {library}",
                    "errorDetails": "Use overwrite=True to replace the existing entry",
                    "mpn": mpn,
                    "library": library,
                }

            if existing and overwrite:
                # Update existing entry
                conn.execute(
                    "UPDATE symbol_index SET sexp = ? WHERE mpn = ? AND library = ?",
                    (sexp, mpn, library),
                )
                action = "Updated"
            else:
                # Insert new entry
                conn.execute(
                    "INSERT INTO symbol_index (mpn, library, sexp) VALUES (?, ?, ?)",
                    (mpn, library, sexp),
                )
                action = "Added"

            conn.commit()
            conn.close()

            logger.info(f"{action} symbol {mpn} in library {library}")
            return {
                "success": True,
                "message": f"{action} symbol {mpn} in library {library}",
                "mpn": mpn,
                "library": library,
                "dbPath": str(effective_db_path),
            }

        except sqlite3.Error as err:
            logger.error("SQLite error adding symbol entry: %s", err)
            return {
                "success": False,
                "message": "Failed to add symbol entry",
                "errorDetails": str(err),
            }

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

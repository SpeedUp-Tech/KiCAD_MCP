from __future__ import annotations

from skip import Schematic
import os
import copy
import json
import math
import uuid
import logging
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import sexpdata
from sexpdata import Symbol as SSymbol

from db_tools.symbols import get_symbol_sexp

from skip.eeschema.lib_symbol import LibSymbolsListWrapper
from skip.eeschema.schematic.symbol import Symbol, SymbolCollection
from skip.sexp.parser import ParsedValue
from skip.sexp.util import loadTree

from .grid_utils import snap_to_grid, snap_point_to_grid

PROJECT_ROOT = Path(__file__).resolve().parents[3]
LIBRARY_PATHS_CONFIG = PROJECT_ROOT / 'config' / 'library-paths.json'
DEFAULT_SYMBOL_SEARCH_PATHS = [
    PROJECT_ROOT / 'symbol_lib' / 'symbols',
    Path('/mnt/shared/symbol_lib/symbols'),
]
_LIBRARY_PATHS_CACHE: Dict[str, List[Path]] = {}
logger = logging.getLogger('kicad_interface')


def _resolve_config_path(entry: str) -> Optional[Path]:
    try:
        path_obj = Path(entry).expanduser()
        if not path_obj.is_absolute():
            path_obj = (PROJECT_ROOT / path_obj).resolve()
        return path_obj
    except Exception as exc:
        logger.warning("Invalid path '%s' in %s: %s", entry, LIBRARY_PATHS_CONFIG, exc)
        return None


def _get_configured_search_paths(key: str, defaults: Iterable[Path]) -> List[Path]:
    cached = _LIBRARY_PATHS_CACHE.get(key)
    if cached is not None:
        return cached

    paths: List[Path] = []
    if LIBRARY_PATHS_CONFIG.exists():
        try:
            config_data = json.loads(LIBRARY_PATHS_CONFIG.read_text(encoding='utf-8'))
            configured = config_data.get(key, [])
            if isinstance(configured, list):
                for entry in configured:
                    if not entry:
                        continue
                    resolved = _resolve_config_path(str(entry))
                    if resolved and resolved not in paths:
                        paths.append(resolved)
        except Exception as exc:
            logger.warning("Unable to read %s: %s", LIBRARY_PATHS_CONFIG, exc)

    if not paths:
        paths = [Path(p) for p in defaults if p]

    _LIBRARY_PATHS_CACHE[key] = paths
    return paths


def _collect_db_path_entry(entry: str, paths: List[Path]) -> None:
    if not entry:
        return
    resolved = _resolve_config_path(entry)
    if resolved and resolved not in paths:
        paths.append(resolved)


def _load_symbol_from_db(library_name: str, symbol_name: str) -> Optional[List[Any]]:
    sexp_text = get_symbol_sexp(library=library_name, mpn=symbol_name)
    if not sexp_text:
        return None
    try:
        return sexpdata.loads(sexp_text)
    except Exception as exc:
        logger.error("Failed to parse symbol %s:%s from configured DB: %s", library_name, symbol_name, exc)
        raise
    return None

ConnectionManager: Any | None = None
try:
    from .connection_schematic import ConnectionManager as _ConnectionManagerType
    ConnectionManager = _ConnectionManagerType
except Exception:
    ConnectionManager = None

try:
    from .connection_schematic import _iter_symbol_pins as _cm_iter_symbol_pins
    from .connection_schematic import _ensure_pin_metadata as _cm_ensure_pin_metadata
    from .connection_schematic import _get_pin_location as _cm_get_pin_location
except Exception:
    def _cm_iter_symbol_pins(symbol: Any) -> List[Any]:
        pins = getattr(symbol, 'pin', None)
        if pins is None:
            return []
        if isinstance(pins, list):
            return list(pins)
        elements = getattr(pins, '_elements', None)
        if isinstance(elements, list):
            return list(elements)
        try:
            return list(pins)
        except Exception:
            if getattr(pins, 'entity_type', None) == 'pin':
                return [pins]
            return []

    def _cm_ensure_pin_metadata(schematic: Schematic, symbol: Any, pin: Any) -> Tuple[str, str]:
        number = str(getattr(pin, 'number', '')).strip()
        name = str(getattr(pin, 'name', '')).strip()
        return number, name

    def _cm_get_pin_location(pin: Any) -> Optional[Any]:
        loc = getattr(pin, 'location', None)
        if loc is None:
            loc = getattr(pin, '_mcp_location', None)
        return loc


def _require_connection_manager() -> Any:
    if ConnectionManager is None:
        raise RuntimeError("ConnectionManager is not available")
    return ConnectionManager

_STANDARD_PROPERTY_NAMES = {'Reference', 'Value', 'Footprint', 'Datasheet'}
_PRIMITIVE_SYMBOL_NAMES = {'R', 'C', 'L', 'V', 'I', 'E', 'F', 'G', 'H'}
_DEFAULT_FONT_SIZE = 1.27
_PROPERTY_OFFSET = 2.54


def _atom_to_str(atom: Any) -> str:
    if isinstance(atom, sexpdata.Symbol):
        return atom.value()
    return str(atom)


def _is_entry(node: Any, name: str) -> bool:
    if not isinstance(node, list):
        return False
    if not node:
        return False
    first = node[0]
    if not isinstance(first, sexpdata.Symbol):
        return False
    return first.value() == name


def _collect_pin_numbers(symbol_entry: List[Any]) -> List[str]:
    numbers: List[str] = []
    seen: set[str] = set()
    stack: List[Any] = [symbol_entry]

    while stack:
        current = stack.pop()
        if isinstance(current, list):
            if current and isinstance(current[0], sexpdata.Symbol) and current[0].value() == 'pin':
                for child in current:
                    if (
                        isinstance(child, list)
                        and child
                        and isinstance(child[0], sexpdata.Symbol)
                        and child[0].value() == 'number'
                        and len(child) > 1
                    ):
                        number = _atom_to_str(child[1])
                        if number not in seen:
                            seen.add(number)
                            numbers.append(number)
                continue

            for child in current:
                stack.append(child)

    return numbers


def _extract_library_properties(symbol_entry: List[Any]) -> Dict[str, str]:
    properties: Dict[str, str] = {}
    for child in symbol_entry:
        if not (isinstance(child, list) and child):
            continue
        if isinstance(child[0], sexpdata.Symbol) and child[0].value() == 'property':
            if len(child) >= 3:
                name = _atom_to_str(child[1])
                value = _atom_to_str(child[2])
                properties[name] = value
    return properties


def _bool_symbol(value: bool, true_symbol: str = 'yes', false_symbol: str = 'no') -> SSymbol:
    return SSymbol(true_symbol if value else false_symbol)


def _coerce_bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {'yes', 'true', '1', 'on'}:
            return True
        if lowered in {'no', 'false', '0', 'off'}:
            return False
    return bool(value)


def _coerce_unit_identifier(unit: Any) -> Optional[str]:
    if unit is None:
        return None
    try:
        return str(int(unit))
    except (TypeError, ValueError):
        return str(unit)


def _is_primitive_symbol(symbol_name: str) -> bool:
    """Return True if the symbol comes from the generic primitive set."""
    if not symbol_name:
        return False
    return symbol_name.strip().upper() in _PRIMITIVE_SYMBOL_NAMES


def _is_value_visible(value_text: str, hidden: bool) -> bool:
    """Determine if the Value property should be rendered visibly."""
    normalized = value_text.strip().lower()
    if hidden:
        return False
    if not normalized or normalized == 'hide':
        return False
    return True


def _label_offsets_for_value(rotation: float, value_text: str, hidden: bool) -> Tuple[Tuple[float, float], Tuple[float, float]]:
    """
    Return ((ref_dx, ref_dy), (val_dx, val_dy)) for Reference/Value based on visibility.
    Reference sits on the anchor when the value is hidden/empty, otherwise the pair is
    placed diagonally to avoid overlap irrespective of rotation.
    """
    if _is_value_visible(value_text, hidden):
        return (
            _rotate_offset(-_PROPERTY_OFFSET, -_PROPERTY_OFFSET, rotation),
            _rotate_offset(_PROPERTY_OFFSET, _PROPERTY_OFFSET, rotation),
        )
    return (
        (0.0, 0.0),
        _rotate_offset(0.0, _PROPERTY_OFFSET, rotation),
    )


def _upright_text_rotation(rotation: float) -> float:
    """
    Map a symbol rotation to a readable text rotation.

    KiCad rotations are typically multiples of 90. For readability, we avoid
    upside-down text by folding the rotation into [0, 180).
    """
    try:
        return float(rotation) % 180.0
    except Exception:
        return 0.0


def _property_is_hidden(property_node: Optional[List[Any]]) -> bool:
    if not property_node:
        return False
    for entry in property_node:
        if _is_entry(entry, 'effects'):
            for effect in entry[1:]:
                if _is_entry(effect, 'hide'):
                    if len(effect) < 2:
                        return True
                    return _atom_to_str(effect[1]).strip().lower() in {'yes', 'true', '1'}
    return False


def _update_property_position(
    property_node: Optional[List[Any]],
    x: float,
    y: float,
    rotation: float,
    offset: Tuple[float, float],
) -> None:
    if not property_node:
        return
    at_node: Optional[List[Any]] = None
    for entry in property_node:
        if _is_entry(entry, 'at'):
            at_node = entry
            break
    if not at_node or len(at_node) < 4:
        return
    dx, dy = offset
    at_node[1] = round(x + dx, 6)
    at_node[2] = round(y + dy, 6)
    at_node[3] = round(rotation, 6)


def _refresh_reference_value_positions(symbol_node: List[Any], x: float, y: float, rotation: float) -> None:
    lib_id = ""
    for entry in symbol_node:
        if _is_entry(entry, 'lib_id') and len(entry) > 1:
            lib_id = _atom_to_str(entry[1])
            break
    symbol_name = lib_id.split(':', 1)[1] if ':' in lib_id else lib_id
    field_rotation = rotation
    if symbol_name.strip().upper() in {'R', 'C'}:
        field_rotation = _upright_text_rotation(rotation)

    ref_node = _find_property_node(symbol_node, 'Reference')
    val_node = _find_property_node(symbol_node, 'Value')
    value_text = ''
    if val_node and len(val_node) >= 3:
        value_text = _atom_to_str(val_node[2])
    hidden = _property_is_hidden(val_node)
    ref_offset, val_offset = _label_offsets_for_value(rotation, value_text, hidden)
    _update_property_position(ref_node, x, y, field_rotation, ref_offset)
    _update_property_position(val_node, x, y, field_rotation, val_offset)


def _refresh_symbol_collection(schematic: Schematic) -> None:
    symbol_nodes: List[Symbol] = []
    for index, node in enumerate(schematic.tree):
        if _is_entry(node, 'symbol'):
            parsed = ParsedValue(schematic.tree, node, [index], schematic)
            symbol_nodes.append(Symbol(parsed))
    schematic.symbol = SymbolCollection(schematic, symbol_nodes)


def _refresh_wire_collection(schematic: Schematic) -> None:
    if not hasattr(schematic, 'wire'):
        return
    wire_nodes = []
    for index, node in enumerate(schematic.tree):
        if _is_entry(node, 'wire'):
            parsed = ParsedValue(schematic.tree, node, [index], schematic)
            wire_nodes.append(schematic.wrap(parsed))
    # Replace elements in-place to keep existing collection wrapper
    try:
        schematic.wire._elements = wire_nodes
    except Exception:
        pass



def _find_property_node(symbol_node: List[Any], name: str) -> Optional[List[Any]]:
    for entry in symbol_node:
        if _is_entry(entry, 'property') and len(entry) >= 3 and _atom_to_str(entry[1]) == name:
            return entry
    return None


def _component_payload(symbol: Symbol) -> Dict[str, Any]:
    position = {}
    if hasattr(symbol, 'at') and symbol.at is not None:
        coords = list(symbol.at.value)
        if coords:
            position = {
                'x': float(coords[0]),
                'y': float(coords[1]) if len(coords) > 1 else 0.0,
                'rotation': float(coords[2]) if len(coords) > 2 else 0.0,
            }

    payload: Dict[str, Any] = {
        'reference': _reference_from_symbol(symbol),
        'unit': getattr(getattr(symbol, 'unit', None), 'value', None),
        'uuid': getattr(getattr(symbol, 'uuid', None), 'value', None),
        'libId': getattr(getattr(symbol, 'lib_id', None), 'value', None),
        'value': getattr(getattr(symbol.property, 'Value', None), 'value', None),
        'footprint': getattr(getattr(symbol.property, 'Footprint', None), 'value', None),
        'datasheet': getattr(getattr(symbol.property, 'Datasheet', None), 'value', None),
    }

    if position:
        payload['position'] = position

    return payload


def _find_similar_library_names(
    requested_library: str,
    search_roots: List[Path],
    max_suggestions: int = 3,
) -> List[str]:
    """
    Find similar library names in the search paths.
    Prioritizes case-insensitive exact matches, then other similar names.
    """
    available_libraries: List[str] = []

    # Collect all available library names from search roots
    for root in search_roots:
        if not root.exists() or not root.is_dir():
            continue
        try:
            for lib_file in root.glob('*.kicad_sym'):
                lib_name = lib_file.stem  # filename without extension
                available_libraries.append(lib_name)
        except (OSError, PermissionError):
            continue

    if not available_libraries:
        return []

    requested_lower = requested_library.lower()

    # First, check for case-insensitive exact match
    for lib_name in available_libraries:
        if lib_name.lower() == requested_lower and lib_name != requested_library:
            return [lib_name]  # Return immediately with the exact match

    # If no exact match, find similar names using simple heuristics
    suggestions: List[Tuple[str, int]] = []

    for lib_name in available_libraries:
        lib_lower = lib_name.lower()
        score = 0

        # Check if one is a substring of the other
        if requested_lower in lib_lower or lib_lower in requested_lower:
            score += 10

        # Check for common prefix
        common_prefix_len = 0
        for i, (c1, c2) in enumerate(zip(requested_lower, lib_lower)):
            if c1 == c2:
                common_prefix_len = i + 1
            else:
                break
        score += common_prefix_len

        # Only consider libraries with some similarity
        if score > 0:
            suggestions.append((lib_name, score))

    # Sort by score (descending) and return top suggestions
    suggestions.sort(key=lambda x: x[1], reverse=True)
    return [name for name, _ in suggestions[:max_suggestions]]


def _resolve_library_path(
    library: Optional[str],
    explicit_path: Optional[str],
    extra_search_paths: Optional[Iterable[str]] = None,
) -> Path:
    if explicit_path:
        explicit = Path(explicit_path).expanduser()
        if explicit.is_dir():
            explicit = explicit / f"{explicit.name}.kicad_sym"
        if explicit.suffix != '.kicad_sym':
            explicit = explicit.with_suffix('.kicad_sym')
        if explicit.is_file():
            return explicit
        raise FileNotFoundError(f"Symbol library file not found at {explicit}")

    if library is None:
        raise ValueError('Symbol library name or path is required')

    library_path_candidate = Path(library).expanduser()
    candidate_names: List[str] = []

    if library_path_candidate.is_file():
        return library_path_candidate

    if library_path_candidate.suffix == '.kicad_sym':
        candidate_names.append(library_path_candidate.name)
    elif os.sep in library or '/' in library:
        candidate_names.append(library_path_candidate.with_suffix('.kicad_sym').name)
        candidate_names.append(library_path_candidate.name)
    else:
        candidate_names.append(f'{library}.kicad_sym')
        candidate_names.append(library)

    search_roots: List[Path] = []
    if extra_search_paths:
        for path in extra_search_paths:
            if not path:
                continue
            search_roots.append(Path(path).expanduser())

    for default_path in _get_configured_search_paths('symbolSearchPaths', DEFAULT_SYMBOL_SEARCH_PATHS):
        if default_path and default_path.exists():
            search_roots.append(default_path)

    def _append_env(var: str) -> None:
        env_value = os.environ.get(var)
        if not env_value:
            return
        for segment in env_value.split(os.pathsep):
            if segment:
                search_roots.append(Path(segment).expanduser())

    for env_var in ('KICAD_SYMBOL_DIR', 'KICAD6_SYMBOL_DIR', 'KICAD7_SYMBOL_DIR', 'KICAD8_SYMBOL_DIR'):
        _append_env(env_var)

    search_roots.extend(
        [
            Path('/usr/share/kicad/symbols'),
            Path('/usr/local/share/kicad/symbols'),
            Path.home() / 'Documents' / 'KiCad' / 'symbols',
        ]
    )

    attempted: List[Path] = []
    for root in search_roots:
        if not root.exists():
            continue
        for candidate in candidate_names:
            target = root / candidate
            target_with_suffix = target if target.suffix == '.kicad_sym' else target.with_suffix('.kicad_sym')
            for path in {target, target_with_suffix}:
                if path.is_file():
                    return path
                attempted.append(path)

    # Library not found - try to provide helpful suggestions
    similar_names = _find_similar_library_names(library, search_roots)

    error_msg = f"Could not locate library '{library}'."

    if similar_names:
        if len(similar_names) == 1:
            error_msg += f" Did you mean '{similar_names[0]}'?"
        else:
            suggestions_str = "', '".join(similar_names)
            error_msg += f" Did you mean one of: '{suggestions_str}'?"
    else:
        # No suggestions found, provide the list of attempted paths
        error_msg += f" Checked: {', '.join(str(p) for p in attempted[:5])}"
        if len(attempted) > 5:
            error_msg += f" ... and {len(attempted) - 5} more locations"

    raise FileNotFoundError(error_msg)


def _load_symbol_definition(
    library_name: str,
    symbol_name: str,
    library_path_hint: Optional[str],
    extra_search_paths: Optional[Iterable[str]],
) -> List[Any]:
    db_symbol = _load_symbol_from_db(library_name, symbol_name)
    if db_symbol is not None:
        return db_symbol

    library_path = _resolve_library_path(library_name, library_path_hint, extra_search_paths)
    library_tree = loadTree(str(library_path))
    return _find_library_symbol(library_tree, symbol_name)


def _find_library_symbol(tree: List[Any], symbol_name: str) -> List[Any]:
    for entry in tree:
        if _is_entry(entry, 'symbol') and len(entry) >= 2:
            if _atom_to_str(entry[1]) == symbol_name:
                return entry
    raise ValueError(f"Symbol '{symbol_name}' not found in library")


def _get_sheet_uuid(schematic: Schematic) -> str:
    if hasattr(schematic, 'sheet') and hasattr(schematic.sheet, 'uuid'):
        return schematic.sheet.uuid.value

    for entry in schematic.tree:
        if _is_entry(entry, 'sheet'):
            for child in entry:
                if _is_entry(child, 'uuid') and len(child) > 1:
                    return _atom_to_str(child[1])

    return str(uuid.uuid4())


def _infer_project_name(schematic: Schematic) -> str:
    if hasattr(schematic, 'title_block') and hasattr(schematic.title_block, 'title'):
        title = schematic.title_block.title.value
        if title:
            return title
    return 'KiCAD-MCP-Project'


def _ensure_reference_available(schematic: Schematic, reference: str) -> None:
    if not hasattr(schematic, 'symbol'):
        return
    for existing in schematic.symbol:
        if existing.property.Reference.value == reference:
            raise ValueError(f"Component with reference '{reference}' already exists")


def _get_or_create_lib_symbols(
    schematic: Schematic,
) -> Tuple[List[Any], LibSymbolsListWrapper, int]:
    for idx, entry in enumerate(schematic.tree):
        if _is_entry(entry, 'lib_symbols'):
            wrapper = LibSymbolsListWrapper(ParsedValue(schematic.tree, entry, [idx], schematic))
            setattr(schematic, 'lib_symbols', wrapper)
            if hasattr(schematic, '_added_attribs') and 'lib_symbols' not in schematic._added_attribs:
                schematic._added_attribs.append('lib_symbols')
            return entry, wrapper, idx

    container: List[Any] = [SSymbol('lib_symbols')]
    schematic.tree.insert(len(schematic.tree), container)
    idx = schematic.tree.index(container)
    wrapper = LibSymbolsListWrapper(ParsedValue(schematic.tree, container, [idx], schematic))
    setattr(schematic, 'lib_symbols', wrapper)
    if hasattr(schematic, '_added_attribs') and 'lib_symbols' not in schematic._added_attribs:
        schematic._added_attribs.append('lib_symbols')
    return container, wrapper, idx


def _rotate_offset(dx: float, dy: float, rotation_deg: float) -> Tuple[float, float]:
    radians = math.radians(rotation_deg % 360)
    cos_theta = math.cos(radians)
    sin_theta = math.sin(radians)
    return (
        dx * cos_theta - dy * sin_theta,
        dx * sin_theta + dy * cos_theta,
    )


def _make_property(
    name: str,
    value: str,
    x: float,
    y: float,
    rotation: float,
    *,
    hide: bool = False,
    justify: Optional[str] = None,
) -> List[Any]:
    effects: List[Any] = [
        SSymbol('effects'),
        [SSymbol('font'), [SSymbol('size'), _DEFAULT_FONT_SIZE, _DEFAULT_FONT_SIZE]],
    ]
    if justify:
        effects.append([SSymbol('justify'), SSymbol(justify)])
    if hide:
        effects.append([SSymbol('hide'), SSymbol('yes')])

    return [
        SSymbol('property'),
        name,
        value,
        [SSymbol('at'), round(float(x), 6), round(float(y), 6), round(float(rotation), 6)],
        effects,
    ]


def _reference_from_symbol(symbol: Symbol) -> str:
    if hasattr(symbol, "property") and hasattr(symbol.property, "Reference"):
        return symbol.property.Reference.value
    if hasattr(symbol, "Reference") and hasattr(symbol.Reference, "value"):
        return symbol.Reference.value
    ref = getattr(symbol, "reference", None)
    return ref if isinstance(ref, str) else ""

class ComponentManager:
    """Manage components in a schematic"""

    @staticmethod
    def add_component(schematic: Schematic, component_def: dict):
        """Add a component to the schematic."""

        if not isinstance(component_def, dict):
            raise TypeError('component_def must be a dictionary')

        reference = str(component_def.get('reference', '')).strip()
        if not reference:
            raise ValueError('Component reference designator is required')

        raw_symbol = component_def.get('type') or component_def.get('symbol') or component_def.get('libId')
        if not raw_symbol:
            raise ValueError('Component definition must include a symbol type or libId')

        symbol_name = str(raw_symbol)
        library_name = component_def.get('library')
        lib_id = component_def.get('libId')

        if lib_id:
            lib_id = str(lib_id)
            if ':' in lib_id:
                lib_parts = lib_id.split(':', 1)
                library_name = library_name or lib_parts[0]
                symbol_name = lib_parts[1]
            else:
                symbol_name = lib_id
                library_name = library_name or 'Device'
                lib_id = f"{library_name}:{symbol_name}"
        else:
            if ':' in symbol_name:
                library_name, symbol_name = symbol_name.split(':', 1)
            else:
                library_name = library_name or 'Device'
            lib_id = f"{library_name}:{symbol_name}"

        library_name = library_name or 'Device'

        library_path_hint = component_def.get('libraryPath') or component_def.get('library_path')
        search_paths = component_def.get('librarySearchPaths') or component_def.get('searchPaths')

        library_symbol = _load_symbol_definition(library_name, symbol_name, library_path_hint, search_paths)
        library_properties = _extract_library_properties(library_symbol)
        pin_numbers = _collect_pin_numbers(library_symbol)

        _ensure_reference_available(schematic, reference)

        component_value = component_def.get('value')
        if component_value is None:
            component_value = library_properties.get('Value') or symbol_name
        component_value = str(component_value).strip()
        primitive_symbol = _is_primitive_symbol(symbol_name)
        value_hidden = not primitive_symbol
        if value_hidden:
            component_value = 'hide'

        footprint = component_def.get('footprint') or library_properties.get('Footprint') or ''
        datasheet = component_def.get('datasheet') or library_properties.get('Datasheet') or ''
        if datasheet == '~':
            datasheet = ''

        # Get coordinates and snap to grid for KiCAD compliance
        x_raw = float(component_def.get('x', 0.0))
        y_raw = float(component_def.get('y', 0.0))
        x = snap_to_grid(x_raw)
        y = snap_to_grid(y_raw)
        rotation = float(component_def.get('rotation', 0.0))
        unit = int(component_def.get('unit', 1))

        exclude_from_sim = _coerce_bool(
            component_def.get('excludeFromSim', component_def.get('exclude_from_sim')),
            False,
        )
        in_bom = _coerce_bool(
            component_def.get('inBom', component_def.get('in_bom')),
            True,
        )
        on_board = _coerce_bool(
            component_def.get('onBoard', component_def.get('on_board')),
            True,
        )
        dnp = _coerce_bool(component_def.get('dnp'), False)
        fields_autoplaced = _coerce_bool(
            component_def.get('fieldsAutoplaced', component_def.get('fields_autoplaced')),
            True,
        )

        project_name = component_def.get('project') or _infer_project_name(schematic)
        sheet_uuid = component_def.get('sheetUuid') or _get_sheet_uuid(schematic)
        instance_uuid = str(uuid.uuid4())
        component_uuid = str(uuid.uuid4())

        (ref_dx, ref_dy), (val_dx, val_dy) = _label_offsets_for_value(rotation, component_value, value_hidden)

        # Hide reference for power:GND symbols (e.g., #GND_1 should not be displayed)
        hide_reference = (lib_id == "power:GND")

        field_rotation = rotation
        if symbol_name.strip().upper() in {'R', 'C'}:
            field_rotation = _upright_text_rotation(rotation)

        properties: List[Any] = [
            _make_property('Reference', reference, x + ref_dx, y + ref_dy, field_rotation, hide=hide_reference),
            _make_property('Value', component_value, x + val_dx, y + val_dy, field_rotation, hide=value_hidden),
            _make_property('Footprint', str(footprint), x, y, rotation, hide=True),
            _make_property('Datasheet', str(datasheet), x, y, rotation, hide=True),
        ]

        custom_properties = component_def.get('properties') or {}
        if not isinstance(custom_properties, dict):
            raise TypeError('properties must be a mapping of property name to value')
        for key, value in custom_properties.items():
            if key in _STANDARD_PROPERTY_NAMES or value is None:
                continue
            properties.append(
                _make_property(str(key), str(value), x, y, rotation, hide=True)
            )

        lib_symbol_copy = copy.deepcopy(library_symbol)
        lib_symbol_copy[1] = lib_id

        lib_container, lib_wrapper, lib_index = _get_or_create_lib_symbols(schematic)
        if lib_id not in lib_wrapper:
            lib_container.append(lib_symbol_copy)
            setattr(
                schematic,
                'lib_symbols',
                LibSymbolsListWrapper(ParsedValue(schematic.tree, lib_container, [lib_index], schematic)),
            )

        component_node: List[Any] = [
            SSymbol('symbol'),
            [SSymbol('lib_id'), lib_id],
            [SSymbol('at'), round(x, 6), round(y, 6), round(rotation, 6)],
            [SSymbol('unit'), unit],
            [SSymbol('exclude_from_sim'), _bool_symbol(exclude_from_sim)],
            [SSymbol('in_bom'), _bool_symbol(bool(in_bom))],
            [SSymbol('on_board'), _bool_symbol(bool(on_board))],
            [SSymbol('dnp'), _bool_symbol(dnp)],
            [SSymbol('fields_autoplaced'), _bool_symbol(bool(fields_autoplaced))],
            [SSymbol('uuid'), component_uuid],
        ]

        component_node.extend(properties)

        for number in pin_numbers:
            component_node.append(
                [
                    SSymbol('pin'),
                    str(number),
                    [SSymbol('uuid'), str(uuid.uuid4())],
                ]
            )

        component_node.append(
            [
                SSymbol('instances'),
                [
                    SSymbol('project'),
                    project_name,
                    [
                        SSymbol('path'),
                        f"/{sheet_uuid}/{instance_uuid}",
                        [SSymbol('reference'), reference],
                        [SSymbol('unit'), unit],
                    ],
                ],
            ]
        )

        schematic.tree.append(component_node)
        node_index = len(schematic.tree) - 1
        parsed_symbol = ParsedValue(schematic.tree, component_node, [node_index], schematic)
        symbol_object = Symbol(parsed_symbol)
        schematic.symbol.append(symbol_object)

        logger.info(
            "Added component %s (lib_id=%s, value=%s) at (%s, %s)",
            reference,
            lib_id,
            component_value,
            x,
            y,
        )

        return symbol_object

    @staticmethod
    def remove_component(
        schematic: Schematic,
        component_ref: str,
        *,
        unit: Optional[Any] = None,
    ) -> Dict[str, Any]:
        """
        Remove component instances that match the supplied reference/unit.
        Also removes all connections (wires) attached to the component's pins.

        Returns (structured JSON):
            Dict with:
                - removedComponents: List of removed component payloads
                - note: Message about connection removal
                - removedConnections: List of connection objects with shape:
                    { "source": Endpoint, "target": Endpoint, "net": str, "summary": str }
                  where Endpoint is one of:
                    - { "kind": "pin", "reference": Ref, "pin": Pin, "unit"?: str, "pinType"?: str }
                    - { "kind": "label", "label": Name }
        """
        from .schematic_state import _extract_components, _build_connection_map

        if not isinstance(component_ref, str) or not component_ref.strip():
            raise ValueError('component_ref must be a non-empty string')

        desired_unit = _coerce_unit_identifier(unit)
        target_ref = component_ref.strip()

        matches: List[Symbol] = []
        for symbol in schematic.symbol:
            if _reference_from_symbol(symbol) != target_ref:
                continue
            if desired_unit is not None:
                symbol_unit = _coerce_unit_identifier(getattr(getattr(symbol, 'unit', None), 'value', None))
                if symbol_unit != desired_unit:
                    continue
            matches.append(symbol)

        if not matches:
            raise ValueError(f"Component '{target_ref}' not found")

        # Collect all pins from the component being removed
        # pin_position -> (symbol, pin_number)
        component_pins: Dict[Tuple[float, float], Tuple[Symbol, str]] = {}
        for symbol in matches:
            if hasattr(symbol, 'pin'):
                for pin in _cm_iter_symbol_pins(symbol):
                    pin_number, _ = _cm_ensure_pin_metadata(schematic, symbol, pin)
                    if not pin_number:
                        continue
                    loc = _cm_get_pin_location(pin)
                    if loc is None:
                        continue
                    x = round(float(loc.x), 1)
                    y = round(float(loc.y), 1)
                    component_pins[(x, y)] = (symbol, pin_number)

        # Build connection map BEFORE removal to report structured removedConnections
        components_snapshot = _extract_components(schematic)
        edges = _build_connection_map(schematic, components_snapshot)
        removed_connections_structured: List[Dict[str, Any]] = []

        def _to_endpoint(ep: Dict[str, Any]) -> Dict[str, Any]:
            if ep.get('kind') == 'pin':
                out: Dict[str, Any] = {"kind": "pin", "reference": str(ep.get('ref')), "pin": str(ep.get('pin'))}
                return out
            if ep.get('kind') == 'label':
                return {"kind": "label", "label": str(ep.get('name'))}
            return {"kind": "label", "label": str(ep)}

        for edge in edges:
            a = edge.get('a') or {}
            b = edge.get('b') or {}
            if (a.get('kind') == 'pin' and str(a.get('ref')) == target_ref) or (b.get('kind') == 'pin' and str(b.get('ref')) == target_ref):
                ep_a = _to_endpoint(a)
                ep_b = _to_endpoint(b)
                # Simple human summary without electrical type (types may be inferred client-side if needed)
                def _fmt(ep: Dict[str, Any]) -> str:
                    if ep.get('kind') == 'pin':
                        return f"{ep.get('reference')}.{ep.get('pin')}"
                    return str(ep.get('label'))
                summary = f"{_fmt(ep_a)} - {_fmt(ep_b)} [{edge.get('net')}]"
                removed_connections_structured.append({
                    'source': ep_a,
                    'target': ep_b,
                    'net': edge.get('net'),
                    'summary': summary,
                })

        # Remove all connections involving this component's pins
        # Use ConnectionManager.remove_connection to properly remove entire
        # connection paths (including all segments with multiple bends)
        manager = _require_connection_manager()
        for conn in removed_connections_structured:
            source_ep = conn.get('source', {})
            target_ep = conn.get('target', {})

            # Convert endpoints back to connection specs
            def _endpoint_to_spec(ep: Dict[str, Any]) -> Optional[Dict[str, Any]]:
                if ep.get('kind') == 'pin':
                    spec = {'reference': ep.get('reference'), 'pin': ep.get('pin')}
                    if 'unit' in ep:
                        spec['unit'] = ep['unit']
                    return spec
                elif ep.get('kind') == 'label':
                    return {'label': ep.get('label')}
                return None

            source_spec = _endpoint_to_spec(source_ep)
            target_spec = _endpoint_to_spec(target_ep)

            if source_spec and target_spec:
                try:
                    manager.remove_connection(schematic, source_spec, target_spec)
                except Exception as e:
                    # If removal fails (e.g., connection already broken), log and continue
                    logger.warning(f"Could not remove connection {conn.get('summary')}: {e}")
                    continue

        removed_payloads: List[Dict[str, Any]] = []
        for symbol in matches:
            parent = symbol.raw_parent
            if symbol.raw in parent:
                parent.remove(symbol.raw)
            payload = _component_payload(symbol)
            removed_payloads.append(payload)

        _refresh_symbol_collection(schematic)

        logger.info(
            "Removed %d component(s) matching %s%s with %d connection(s)",
            len(removed_payloads),
            target_ref,
            f" unit {desired_unit}" if desired_unit is not None else '',
            len(removed_connections_structured),
        )

        # Determine the note message
        if removed_connections_structured:
            note = "Connections were removed along with the removal of the component"
        else:
            note = "No connections were removed (component had no connections)"

        # Return the new structured format
        return {
            'removedComponents': removed_payloads,
            'note': note,
            'removedConnections': removed_connections_structured,
        }


    @staticmethod
    def update_component(
        schematic: Schematic,
        component_ref: str,
        updates: Dict[str, Any],
        *,
        unit: Optional[Any] = None,
    ) -> Dict[str, Any]:
        """Apply updates to a component and atomically re-route connections on placement change.

        Behavior:
        - If x/y/rotation (placement) changes, this function will:
          1) Snapshot the schematic tree for rollback
          2) Record all current connections (pins and labels) involving the component
          3) Remove all wires attached to the component’s pins
          4) Apply the requested updates (including optional newReference)
          5) Re-construct the recorded connections using Manhattan routing via
             ConnectionManager.connect_pins, so wires are re-routed cleanly
        - If any step fails, the schematic is fully rolled back to the snapshot and
          the exception is propagated. No partial updates are preserved.
        - The 'unit' argument (if provided) disambiguates multi-unit symbols.

        Returns:
            Dict with updated component information:
            {
                'component': { ...component payload... },
                'changedFields': [ 'x', 'y', 'rotation', 'reference', 'property:Key', ... ]
            }
        """

        if not isinstance(updates, dict):
            raise TypeError('updates must be a mapping of field names to values')
        if not updates:
            raise ValueError('updates cannot be empty')

        if not isinstance(component_ref, str) or not component_ref.strip():
            raise ValueError('component_ref must be a non-empty string')

        desired_unit = _coerce_unit_identifier(unit)
        target_ref = component_ref.strip()

        candidates: List[Symbol] = []
        for symbol in schematic.symbol:
            if _reference_from_symbol(symbol) != target_ref:
                continue
            if desired_unit is not None:
                symbol_unit = _coerce_unit_identifier(getattr(getattr(symbol, 'unit', None), 'value', None))
                if symbol_unit != desired_unit:
                    continue
            candidates.append(symbol)

        if not candidates:
            raise ValueError(f"Component '{target_ref}' not found")
        if len(candidates) > 1:
            raise ValueError(
                f"Multiple component units found for '{target_ref}'. Specify the unit to disambiguate."
            )

        symbol = candidates[0]
        symbol_node = symbol.raw

        current_at = list(getattr(symbol.at, 'value', [0.0, 0.0, 0.0])) or [0.0, 0.0, 0.0]
        if len(current_at) < 3:
            current_at.extend([0.0] * (3 - len(current_at)))

        requested_position = updates.get('position') if isinstance(updates.get('position'), dict) else None

        x = updates.get('x', requested_position.get('x') if requested_position else current_at[0])
        y = updates.get('y', requested_position.get('y') if requested_position else current_at[1])
        rotation = updates.get(
            'rotation',
            requested_position.get('rotation') if requested_position else current_at[2],
        )

        if x is not None:
            x = snap_to_grid(float(x))
        else:
            x = current_at[0]
        if y is not None:
            y = snap_to_grid(float(y))
        else:
            y = current_at[1]
        if rotation is not None:
            rotation = float(rotation)
        else:
            rotation = current_at[2]


        # Determine if placement (x, y, rotation) will change; if so, snapshot and remove connections
        placement_changed = ([x, y, rotation] != current_at)
        connections_to_restore: List[Tuple[str, Dict[str, Any]]] = []  # (source_pin, other_endpoint_spec)
        original_tree = copy.deepcopy(schematic.tree) if placement_changed else None

        if placement_changed:
            # Take a snapshot to allow rollback if anything fails during removal
            try:
                # Import here to avoid top-level import cycles
                from .schematic_state import _extract_components, _build_connection_map

                # Build full connection map and collect edges involving this reference
                components_snapshot = _extract_components(schematic)
                edges = _build_connection_map(schematic, components_snapshot)

                def _endpoint_to_spec(ep: Dict[str, Any]) -> Optional[Dict[str, Any]]:
                    kind = ep.get('kind')
                    if kind == 'pin':
                        return {'reference': str(ep.get('ref')), 'pin': str(ep.get('pin'))}
                    if kind == 'label':
                        name = ep.get('name')
                        if name is not None:
                            return {'label': str(name)}
                    return None

                # 1) Record all connections to restore later
                for edge in edges:
                    a = edge.get('a') or {}
                    b = edge.get('b') or {}

                    if a.get('kind') == 'pin' and str(a.get('ref')) == target_ref:
                        pin_num = str(a.get('pin'))
                        other_spec = _endpoint_to_spec(b)
                        if other_spec:
                            connections_to_restore.append((pin_num, other_spec))

                    elif b.get('kind') == 'pin' and str(b.get('ref')) == target_ref:
                        pin_num = str(b.get('pin'))
                        other_spec = _endpoint_to_spec(a)
                        if other_spec:
                            connections_to_restore.append((pin_num, other_spec))

                # 2) Remove all connections involving this component's pins
                #    Use ConnectionManager.remove_connection to properly remove entire
                #    connection paths (including all segments with multiple bends)

                manager = _require_connection_manager()
                for pin_num, other_spec in connections_to_restore:
                    try:
                        src_spec = {'reference': target_ref, 'pin': pin_num}
                        if desired_unit is not None:
                            src_spec['unit'] = desired_unit
                        manager.remove_connection(schematic, src_spec, other_spec)
                    except Exception as e:
                        # If removal fails (e.g., connection already broken), log and continue
                        logger.warning(f"Could not remove connection {target_ref}.{pin_num} to {other_spec}: {e}")
                        continue
            except Exception as e:
                # Roll back any partial removals and fail the update
                schematic.tree = copy.deepcopy(original_tree)
                _refresh_symbol_collection(schematic)
                _refresh_wire_collection(schematic)
                raise

        reference_updates = updates.get('newReference')
        if reference_updates is None and 'reference' in updates:
            reference_updates = updates['reference']

        new_reference: Optional[str] = None
        if reference_updates is not None:
            new_reference = str(reference_updates).strip()
            if not new_reference:
                raise ValueError('new reference must be a non-empty string')
            if new_reference != target_ref:
                for existing in schematic.symbol:
                    if existing is symbol:
                        continue
                    if _reference_from_symbol(existing) == new_reference:
                        # Roll back any prior removals and abort if we changed placement
                        if placement_changed and original_tree is not None:
                            schematic.tree = copy.deepcopy(original_tree)
                            _refresh_symbol_collection(schematic)
                            _refresh_wire_collection(schematic)
                        raise ValueError(f"Component reference '{new_reference}' already exists")

        changed_fields: List[str] = []

        # Update placement first – derived property positions will track this
        if [x, y, rotation] != current_at:
            symbol.at.value = [round(x, 6), round(y, 6), round(rotation, 6)]
            changed_fields.extend(['x', 'y', 'rotation'])

            _refresh_reference_value_positions(symbol_node, x, y, rotation)


        if new_reference and new_reference != target_ref:
            symbol.setAllReferences(new_reference)
            changed_fields.append('reference')

        if 'value' in updates and updates['value'] is not None:
            new_value = str(updates['value'])
            if getattr(symbol.property.Value, 'value', None) != new_value:
                symbol.property.Value.value = new_value
                changed_fields.append('value')
                _refresh_reference_value_positions(symbol_node, x, y, rotation)

        if 'datasheet' in updates and updates['datasheet'] is not None:
            new_datasheet = str(updates['datasheet'])
            if getattr(symbol.property.Datasheet, 'value', None) != new_datasheet:
                symbol.property.Datasheet.value = new_datasheet
                changed_fields.append('datasheet')

        if 'inBom' in updates:
            new_flag = _coerce_bool(updates['inBom'], symbol.in_bom.value)
            if symbol.in_bom.value != new_flag:
                symbol.in_bom.value = new_flag

                changed_fields.append('inBom')

        if 'onBoard' in updates:
            new_flag = _coerce_bool(updates['onBoard'], symbol.on_board.value)
            if symbol.on_board.value != new_flag:
                symbol.on_board.value = new_flag
                changed_fields.append('onBoard')

        if 'dnp' in updates:
            new_flag = _coerce_bool(updates['dnp'], symbol.dnp.value)
            if symbol.dnp.value != new_flag:
                symbol.dnp.value = new_flag
                changed_fields.append('dnp')

        properties_updates = updates.get('properties')
        if properties_updates is not None:
            if not isinstance(properties_updates, dict):
                raise TypeError('properties must be a mapping of property name to value')

            for key, value in properties_updates.items():
                if key in _STANDARD_PROPERTY_NAMES:
                    continue
                existing_node = _find_property_node(symbol_node, key)
                if value is None:
                    if existing_node and existing_node in symbol_node:
                        symbol_node.remove(existing_node)
                        changed_fields.append(f'property:{key}')
                else:
                    text_value = str(value)
                    if existing_node:
                        if existing_node[2] != text_value:
                            existing_node[2] = text_value
                            changed_fields.append(f'property:{key}')
                    else:
                        new_prop = _make_property(str(key), text_value, x, y, rotation, hide=True)
                        symbol_node.append(new_prop)
                        changed_fields.append(f'property:{key}')

        final_reference = new_reference or target_ref
        final_unit = _coerce_unit_identifier(getattr(getattr(symbol, 'unit', None), 'value', None))

        # Reconstruct connections after placement/reference updates
        if placement_changed and connections_to_restore:
            # Reconstruct under transaction semantics – on failure, roll back to original tree
            try:
                final_ref_for_connect = new_reference or target_ref
                manager = _require_connection_manager()
                for pin_num, other_spec in connections_to_restore:
                    src_spec_new = {'reference': final_ref_for_connect, 'pin': pin_num}
                    if desired_unit is not None:
                        src_spec_new['unit'] = desired_unit
                    manager.connect_pins(schematic, src_spec_new, other_spec)
            except Exception:
                # Roll back entire schematic tree and abort
                if original_tree is not None:
                    schematic.tree = copy.deepcopy(original_tree)
                    _refresh_symbol_collection(schematic)
                    _refresh_wire_collection(schematic)
                raise


        _refresh_symbol_collection(schematic)

        lookup_unit = final_unit if desired_unit is not None else None
        updated_symbol = ComponentManager.get_component(
            schematic,
            final_reference,
            unit=lookup_unit,
        )

        payload = _component_payload(updated_symbol) if updated_symbol else None
        return {
            'component': payload,
            'changedFields': sorted(set(changed_fields)),
        }


    @staticmethod
    def get_component(
        schematic: Schematic,
        component_ref: str,
        *,
        unit: Optional[Any] = None,
    ) -> Optional[Symbol]:
        """Get a component by reference designator and optional unit identifier."""

        if not isinstance(component_ref, str) or not component_ref.strip():
            raise ValueError('component_ref must be a non-empty string')

        desired_unit = _coerce_unit_identifier(unit)
        target_ref = component_ref.strip()

        for symbol in schematic.symbol:
            if _reference_from_symbol(symbol) != target_ref:
                continue
            if desired_unit is None:
                logger.debug("Found component %s", target_ref)
                return symbol
            symbol_unit = _coerce_unit_identifier(getattr(getattr(symbol, 'unit', None), 'value', None))
            if symbol_unit == desired_unit:
                logger.debug("Found component %s unit %s", target_ref, desired_unit)
                return symbol

        logger.warning("Component %s%s not found", target_ref, f" unit {desired_unit}" if desired_unit else '')
        return None

    @staticmethod
    def search_components(schematic: Schematic, query: str):
        """Search for components matching criteria (basic implementation)"""
        matching_components = []
        query_lower = query.lower()
        for symbol in schematic.symbol:
            reference_value = _reference_from_symbol(symbol)
            lib_identifier = ''
            if hasattr(symbol, 'lib_id'):
                lib_attr = symbol.lib_id
                if hasattr(lib_attr, 'value'):
                    lib_identifier = lib_attr.value
                else:
                    lib_identifier = str(lib_attr)
            property_value = ''
            if hasattr(symbol.property, 'Value'):
                property_value = symbol.property.Value.value

            fields = [reference_value, lib_identifier, property_value]
            if any(query_lower in str(field).lower() for field in fields):
                matching_components.append(symbol)
        logger.info(f"Found {len(matching_components)} components matching query '{query}'.")
        return matching_components

    @staticmethod
    def get_all_components(schematic: Schematic):
        """Get all components in schematic"""
        logger.debug(f"Retrieving all {len(schematic.symbol)} components.")
        return list(schematic.symbol)

if __name__ == '__main__':
    # Example Usage (for testing)
    from schematic import SchematicManager # Assuming schematic.py is in the same directory

    # Create a new schematic
    test_sch = SchematicManager.create_schematic("ComponentTestSchematic")

    # Add components
    comp1_def = {"type": "R", "reference": "R1", "value": "10k", "x": 100, "y": 100}
    comp2_def = {"type": "C", "reference": "C1", "value": "0.1uF", "x": 200, "y": 100, "library": "Device"}
    comp3_def = {"type": "LED", "reference": "D1", "x": 300, "y": 100, "library": "Device", "properties": {"Color": "Red"}}

    comp1 = ComponentManager.add_component(test_sch, comp1_def)
    comp2 = ComponentManager.add_component(test_sch, comp2_def)
    comp3 = ComponentManager.add_component(test_sch, comp3_def)

    # Get a component
    retrieved_comp = ComponentManager.get_component(test_sch, "C1")
    if retrieved_comp:
        logger.info(f"Retrieved component: {retrieved_comp.reference} ({retrieved_comp.value})")

    # Update a component
    ComponentManager.update_component(test_sch, "R1", {"value": "20k", "Tolerance": "5%"})

    # Search components
    matching_comps = ComponentManager.search_components(test_sch, "100") # Search by position
    logger.info(f"Search results for '100': {[c.reference for c in matching_comps]}")

    # Get all components
    all_comps = ComponentManager.get_all_components(test_sch)
    logger.info(f"All components: {[c.reference for c in all_comps]}")

    # Remove a component
    ComponentManager.remove_component(test_sch, "D1")
    all_comps_after_remove = ComponentManager.get_all_components(test_sch)
    logger.info(f"Components after removing D1: {[c.reference for c in all_comps_after_remove]}")

    # Save the schematic (optional)
    # SchematicManager.save_schematic(test_sch, "component_test.kicad_sch")

    # Clean up (if saved)
    # if os.path.exists("component_test.kicad_sch"):
    #     os.remove("component_test.kicad_sch")
    #     print("Cleaned up component_test.kicad_sch")

from __future__ import annotations

from skip import Schematic
import os
import copy
import math
import uuid
import logging
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import sexpdata
from sexpdata import Symbol as SSymbol

from skip.eeschema.lib_symbol import LibSymbolsListWrapper
from skip.eeschema.schematic.symbol import Symbol, SymbolCollection
from skip.sexp.parser import ParsedValue
from skip.sexp.util import loadTree

from .grid_utils import snap_to_grid, snap_point_to_grid

logger = logging.getLogger('kicad_interface')


_STANDARD_PROPERTY_NAMES = {'Reference', 'Value', 'Footprint', 'Datasheet'}
_DEFAULT_FONT_SIZE = 1.27
_PROPERTY_OFFSET = 2.54


def _atom_to_str(atom: Any) -> str:
    if isinstance(atom, sexpdata.Symbol):
        return atom.value()
    return str(atom)


def _is_entry(node: Any, name: str) -> bool:
    return (
        isinstance(node, list)
        and node
        and isinstance(node[0], sexpdata.Symbol)
        and node[0].value() == name
    )


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


def _refresh_symbol_collection(schematic: Schematic) -> None:
    symbol_nodes: List[Symbol] = []
    for index, node in enumerate(schematic.tree):
        if _is_entry(node, 'symbol'):
            parsed = ParsedValue(schematic.tree, node, [index], schematic)
            symbol_nodes.append(Symbol(parsed))
    schematic.symbol = SymbolCollection(schematic, symbol_nodes)


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

    raise FileNotFoundError(
        f"Could not locate library '{library}'. Checked: {', '.join(str(p) for p in attempted)}"
    )


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

        library_path = _resolve_library_path(library_name, library_path_hint, search_paths)
        library_tree = loadTree(str(library_path))
        library_symbol = _find_library_symbol(library_tree, symbol_name)
        library_properties = _extract_library_properties(library_symbol)
        pin_numbers = _collect_pin_numbers(library_symbol)

        _ensure_reference_available(schematic, reference)

        component_value = component_def.get('value')
        if component_value is None:
            component_value = library_properties.get('Value') or symbol_name
        component_value = str(component_value)

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

        ref_dx, ref_dy = _rotate_offset(0, -_PROPERTY_OFFSET, rotation)
        val_dx, val_dy = _rotate_offset(0, _PROPERTY_OFFSET, rotation)

        properties: List[Any] = [
            _make_property('Reference', reference, x + ref_dx, y + ref_dy, rotation),
            _make_property('Value', component_value, x + val_dx, y + val_dy, rotation),
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

        Returns:
            Dict with:
                - removedComponents: List of removed component payloads
                - note: Message about connection removal
                - removedConnections: List of formatted connection strings
        """
        from commands.connection_schematic import ConnectionManager

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
                for pin in symbol.pin:
                    if hasattr(pin, 'location'):
                        loc = pin.location
                        x = round(float(loc.x), 1)
                        y = round(float(loc.y), 1)
                        pin_number = str(getattr(pin, 'number', ''))
                        component_pins[(x, y)] = (symbol, pin_number)

        # Find all wires connected to this component and remove them directly
        removed_connection_strings = []
        wires_to_remove = []
        wire_data = []  # Store wire info before removal

        if hasattr(schematic, 'wire') and component_pins:
            from commands.connection_schematic import _build_net_info, _get_pin_type, _format_pin_with_type

            # Build a map of all pin positions to (reference, pin_number, pin_type)
            all_pin_info: Dict[Tuple[float, float], Tuple[str, str, str]] = {}
            if hasattr(schematic, 'symbol'):
                for other_symbol in schematic.symbol:
                    other_ref = _reference_from_symbol(other_symbol)
                    if hasattr(other_symbol, 'pin'):
                        for other_pin in other_symbol.pin:
                            if hasattr(other_pin, 'location'):
                                other_loc = other_pin.location
                                other_x = round(float(other_loc.x), 1)
                                other_y = round(float(other_loc.y), 1)
                                other_pin_num = str(getattr(other_pin, 'number', ''))
                                other_pin_type = _get_pin_type(other_pin)
                                all_pin_info[(other_x, other_y)] = (other_ref, other_pin_num, other_pin_type)

            # Find wires connected to the component and collect their data
            for wire in list(schematic.wire):
                try:
                    if hasattr(wire, 'points'):
                        points = wire.points
                        if len(points) >= 2:
                            # Extract wire endpoints
                            wire_points = []
                            for pt in points:
                                if hasattr(pt, 'value'):
                                    coords = pt.value
                                    if len(coords) >= 2:
                                        x = round(float(coords[0]), 1)
                                        y = round(float(coords[1]), 1)
                                        wire_points.append((x, y))

                            if len(wire_points) < 2:
                                continue

                            # Check if any endpoint is on the component being removed
                            component_endpoint = None
                            other_endpoint = None

                            for pt in [wire_points[0], wire_points[-1]]:
                                if pt in component_pins:
                                    component_endpoint = pt
                                elif pt in all_pin_info and pt not in component_pins:
                                    other_endpoint = pt

                            # If this wire connects to the component being removed
                            if component_endpoint:
                                # Get component pin info
                                comp_ref, comp_pin_num, comp_pin_type = all_pin_info.get(
                                    component_endpoint,
                                    (target_ref, '?', 'passive')
                                )
                                comp_pin_desc = _format_pin_with_type(comp_ref, comp_pin_num, comp_pin_type)

                                # Get other endpoint info
                                if other_endpoint and other_endpoint in all_pin_info:
                                    other_ref, other_pin_num, other_pin_type = all_pin_info[other_endpoint]
                                    other_pin_desc = _format_pin_with_type(other_ref, other_pin_num, other_pin_type)
                                else:
                                    other_pin_desc = "(unconnected)"

                                # Build net info
                                net_info = _build_net_info(schematic, wire_points)

                                # Format connection string
                                connection_str = f"{comp_pin_desc} - {other_pin_desc} [{net_info['net']}]"

                                # Store wire data before removal
                                wire_data.append({
                                    'wire': wire,
                                    'raw': wire.raw,
                                    'connection_str': connection_str
                                })
                                wires_to_remove.append(wire)
                except Exception as e:
                    logger.warning(f"Error analyzing wire for removal: {e}")
                    continue

            # Remove all wires at once
            for data in wire_data:
                raw_wire = data['raw']
                if raw_wire in schematic.tree:
                    schematic.tree.remove(raw_wire)
                removed_connection_strings.append(data['connection_str'])

            # Update the wire collection
            if hasattr(schematic, 'wire') and hasattr(schematic.wire, '_elements'):
                schematic.wire._elements = [
                    wire for wire in schematic.wire._elements if wire not in wires_to_remove
                ]

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
            len(removed_connection_strings),
        )

        # Determine the note message
        if removed_connection_strings:
            note = "Connections were removed along with the removal of the component"
        else:
            note = "No connections were removed (component had no connections)"

        # Return the new format
        return {
            'removedComponents': removed_payloads,
            'note': note,
            'removedConnections': removed_connection_strings,
        }


    @staticmethod
    def update_component(
        schematic: Schematic,
        component_ref: str,
        updates: Dict[str, Any],
        *,
        unit: Optional[Any] = None,
    ) -> Dict[str, Any]:
        """Apply field updates to a component instance and return the new payload."""

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
                        raise ValueError(f"Component reference '{new_reference}' already exists")

        changed_fields: List[str] = []

        # Update placement first – derived property positions will track this
        if [x, y, rotation] != current_at:
            symbol.at.value = [round(x, 6), round(y, 6), round(rotation, 6)]
            changed_fields.extend(['x', 'y', 'rotation'])

            ref_node = _find_property_node(symbol_node, 'Reference')
            val_node = _find_property_node(symbol_node, 'Value')
            ref_dx, ref_dy = _rotate_offset(0, -_PROPERTY_OFFSET, rotation)
            val_dx, val_dy = _rotate_offset(0, _PROPERTY_OFFSET, rotation)
            if ref_node and len(ref_node) >= 4 and _is_entry(ref_node[3], 'at'):
                ref_node[3][1] = round(x + ref_dx, 6)
                ref_node[3][2] = round(y + ref_dy, 6)
                ref_node[3][3] = round(rotation, 6)
            if val_node and len(val_node) >= 4 and _is_entry(val_node[3], 'at'):
                val_node[3][1] = round(x + val_dx, 6)
                val_node[3][2] = round(y + val_dy, 6)
                val_node[3][3] = round(rotation, 6)

        if new_reference and new_reference != target_ref:
            symbol.setAllReferences(new_reference)
            changed_fields.append('reference')

        if 'value' in updates and updates['value'] is not None:
            new_value = str(updates['value'])
            if getattr(symbol.property.Value, 'value', None) != new_value:
                symbol.property.Value.value = new_value
                changed_fields.append('value')

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

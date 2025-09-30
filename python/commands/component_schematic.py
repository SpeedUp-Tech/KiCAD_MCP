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
from skip.eeschema.schematic.symbol import Symbol
from skip.sexp.parser import ParsedValue
from skip.sexp.util import loadTree

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

        x = float(component_def.get('x', 0.0))
        y = float(component_def.get('y', 0.0))
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
    def remove_component(schematic: Schematic, component_ref: str):
        """Remove a component from the schematic by reference designator"""
        try:
            # kicad-skip doesn't have a direct remove_symbol method by reference.
            # We need to find the symbol and then remove it from the symbols list.
            symbol_to_remove = None
            for symbol in schematic.symbol:
                if _reference_from_symbol(symbol) == component_ref:
                    symbol_to_remove = symbol
                    break

            if symbol_to_remove:
                schematic.symbol.remove(symbol_to_remove)
                logger.info(f"Removed component {component_ref} from schematic.")
                return True
            else:
                logger.warning(f"Component with reference {component_ref} not found.")
                return False
        except Exception as e:
            logger.error(f"Error removing component {component_ref}: {e}")
            return False


    @staticmethod
    def update_component(schematic: Schematic, component_ref: str, new_properties: dict):
        """Update component properties by reference designator"""
        try:
            symbol_to_update = None
            for symbol in schematic.symbol:
                if _reference_from_symbol(symbol) == component_ref:
                    symbol_to_update = symbol
                    break

            if symbol_to_update:
                for key, value in new_properties.items():
                    if key in symbol_to_update.property:
                        symbol_to_update.property[key].value = value
                    else:
                         # Add as a new property if it doesn't exist
                         symbol_to_update.property.append(key, value)
                logger.info(f"Updated properties for component {component_ref}.")
                return True
            else:
                logger.warning(f"Component with reference {component_ref} not found.")
                return False
        except Exception as e:
            logger.error(f"Error updating component {component_ref}: {e}")
            return False

    @staticmethod
    def get_component(schematic: Schematic, component_ref: str):
        """Get a component by reference designator"""
        for symbol in schematic.symbol:
            if _reference_from_symbol(symbol) == component_ref:
                logger.debug(f"Found component with reference {component_ref}.")
                return symbol
        logger.warning(f"Component with reference {component_ref} not found.")
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

from __future__ import annotations

import logging
import uuid
import math
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple, Union, cast
import sexpdata


from skip import Schematic
from sexpdata import Symbol as SSymbol

from skip.eeschema.wire import WireWrapper
from skip.sexp.parser import ParsedValue
from skip.eeschema.schematic.symbol import Symbol
from skip.sexp.util import writeTree

from .grid_utils import snap_point_to_grid, round_to_grid
from python.router.manhattan import (
    COORD_KEY_PRECISION,
    RoutingObstacles,
    coord_key as _coord_key,
    safe_manhattan_route,
    segment_length as _segment_length,
)

logger = logging.getLogger('kicad_interface')


try:
    from skip.eeschema.schematic import symbol as _skip_symbol_module
except Exception:  # pragma: no cover - skip internals may be unavailable in stubs
    _skip_symbol_module = None

_SYMBOL_PIN_CTOR: Optional[Callable[[Any, Any], Any]] = None
if _skip_symbol_module is not None and hasattr(_skip_symbol_module, "SymbolPin"):
    _SYMBOL_PIN_CTOR = getattr(_skip_symbol_module, "SymbolPin")


_DEFAULT_WIRE_WIDTH = 0.254
_VALID_STROKE_TYPES = {'default', 'dash', 'dot'}
MIN_ROUTED_SEGMENT_MM = 0.5  # Minimum acceptable segment length

# Stub configuration
KICAD_SCHEMATIC_GRID_MM = 1.27  # Standard KiCAD grid spacing
DEFAULT_STUB_LENGTH_MM = 1.27  # 1 grid = 1.27mm

# Routing configuration for bbox expansion
BBOX_CLEARANCE_MM = 0.5 * KICAD_SCHEMATIC_GRID_MM  # 0.5 grid = 0.635mm clearance around symbols


def format_anonymous_net_name(net_id: Optional[Any]) -> str:
    """
    Provide a stable placeholder name for unnamed nets.

    The numeric id is derived from the local connectivity grouping so it remains
    consistent as long as the wire topology does not change.
    """
    if net_id is None:
        numeric_id = -1
    else:
        try:
            numeric_id = int(net_id)
        except (TypeError, ValueError):
            numeric_id = -1
    return f"Net {numeric_id}"


def _coerce_point(value: Any, *, label: str) -> Tuple[float, float]:
    if value is None:
        raise ValueError(f"{label} is required")

    if isinstance(value, (list, tuple)) and len(value) >= 2:
        x_val, y_val = value[0], value[1]
    elif isinstance(value, dict) and 'x' in value and 'y' in value:
        x_val, y_val = value['x'], value['y']
    else:
        raise TypeError(f"{label} must be a sequence or mapping with x/y")

    try:
        return float(x_val), float(y_val)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} coordinates must be numeric") from exc


def _normalise_points(
    start: Optional[Any],
    end: Optional[Any],
    points: Optional[Iterable[Any]],
) -> List[Tuple[float, float]]:
    normalised: List[Tuple[float, float]] = []

    if points is not None:
        for idx, raw in enumerate(points):
            normalised.append(_coerce_point(raw, label=f"points[{idx}]") )

        if len(normalised) < 2:
            raise ValueError('A wire must contain at least two distinct points')

        if start is not None:
            start_point = _coerce_point(start, label='startPoint')
            if normalised[0] != start_point:
                normalised.insert(0, start_point)

        if end is not None:
            end_point = _coerce_point(end, label='endPoint')
            if normalised[-1] != end_point:
                normalised.append(end_point)
    else:
        if start is None or end is None:
            raise ValueError('startPoint and endPoint are required when points are not provided')
        normalised = [
            _coerce_point(start, label='startPoint'),
            _coerce_point(end, label='endPoint'),
        ]

    # Ensure consecutive points do not collapse into zero-length segments
    filtered: List[Tuple[float, float]] = []
    for point in normalised:
        if not filtered or filtered[-1] != point:
            filtered.append(point)

    if len(filtered) < 2:
        raise ValueError('Wire must span at least two distinct coordinates')

    return filtered


def _build_wire_node(
    points: Sequence[Tuple[float, float]],
    *,
    width: float,
    stroke_type: str,
    wire_uuid: str,
) -> List[Any]:
    pts_expr: List[Any] = [SSymbol('pts')]
    for x_val, y_val in points:
        pts_expr.append([SSymbol('xy'), round(float(x_val), 6), round(float(y_val), 6)])

    node: List[Any] = [
        SSymbol('wire'),
        pts_expr,
        [
            SSymbol('stroke'),
            [SSymbol('width'), round(float(width), 6)],
            [SSymbol('type'), SSymbol(stroke_type)],
        ],
        [SSymbol('uuid'), str(wire_uuid)],
    ]

    return node


def _polyline_length(points: Sequence[Tuple[float, float]]) -> float:
    total = 0.0
    for start, end in zip(points, points[1:]):
        total += math.hypot(end[0] - start[0], end[1] - start[1])
    return total


def _extract_wire_points(wrapper: WireWrapper) -> List[Tuple[float, float]]:
    pts_entry = wrapper.raw[1] if len(wrapper.raw) > 1 else None
    points: List[Tuple[float, float]] = []
    if isinstance(pts_entry, list) and pts_entry:
        if pts_entry[0] == SSymbol('pts'):
            for candidate in pts_entry[1:]:
                if isinstance(candidate, list) and len(candidate) >= 3:
                    try:
                        points.append((float(candidate[1]), float(candidate[2])))
                    except (TypeError, ValueError):
                        continue
    return points


def _wire_payload(wrapper: WireWrapper) -> Dict[str, Any]:
    raw_points = _extract_wire_points(wrapper)
    return {
        'uuid': getattr(getattr(wrapper, 'uuid', None), 'value', None),
        'points': [[round(pt[0], 6), round(pt[1], 6)] for pt in raw_points],
        'width': getattr(getattr(wrapper.stroke, 'width', None), 'value', None),
        'strokeType': getattr(getattr(wrapper.stroke, 'type', None), 'value', None),
        'length': _polyline_length(raw_points) if len(raw_points) >= 2 else 0.0,
    }


def _validate_segment_lengths(points: Sequence[Sequence[float]], *, min_length: float, context: str) -> None:
    if len(points) < 2:
        raise ValueError(f"{context}: insufficient points to form a segment")
    for idx in range(len(points) - 1):
        a = (float(points[idx][0]), float(points[idx][1]))
        b = (float(points[idx + 1][0]), float(points[idx + 1][1]))
        length = _segment_length(a, b)
        if length < min_length:
            raise ValueError(
                f"{context}: segment {idx} length {length:.4f}mm is below the minimum {min_length:.2f}mm"
            )


def _get_pin_type(pin: Any) -> str:
    """Extract the electrical type of a pin."""
    if hasattr(pin, 'electrical_type'):
        try:
            etype = getattr(pin, 'electrical_type')
            if etype is not None:
                text = str(etype)
                if text and text.lower() != 'none':
                    return text
        except Exception:
            pass
    # Try to get from raw S-expression
    if hasattr(pin, 'raw') and isinstance(pin.raw, list):
        for item in pin.raw:
            if isinstance(item, str) and item in ['input', 'output', 'bidirectional', 'tri_state', 'passive', 'free', 'unspecified', 'power_in', 'power_out', 'open_collector', 'open_emitter', 'no_connect']:
                return item
    return 'passive'


def _format_pin_with_type(reference: str, pin_number: str, pin_type: str) -> str:
    """Format a pin reference with its electrical type."""
    return f"{reference}.{pin_number}({pin_type})"


def _collect_net_context(schematic: Schematic) -> Dict[str, Any]:
    """
    Build reusable connectivity indexes for a schematic.

    Returns a dictionary containing:
        - pin_locations: {(x, y): [{reference, pin, pinType}]}
        - label_locations: {(x, y): [{name, type}]}
        - power_locations: {(x, y): [name, ...]}
        - wire_points_by_idx: {wire_index: [(x, y), ...]}
        - all_wire_points: {(x, y): [wire_index, ...]}
        - wire_to_net: {wire_index: net_id}
        - point_to_net: {(x, y): net_id}
        - next_net_id: next unused net identifier
    """
    pin_locations: Dict[Tuple[float, float], List[Dict[str, Any]]] = defaultdict(list)
    if hasattr(schematic, 'symbol'):
        for symbol in getattr(schematic, 'symbol', []):
            try:
                reference = getattr(getattr(symbol.property, 'Reference', None), 'value', None)
                if not reference:
                    continue

                lib_id = getattr(getattr(symbol, 'lib_id', None), 'value', '') or ''
                if lib_id and 'power' in lib_id.lower():
                    continue

                if hasattr(symbol, 'pin'):
                    for pin in _iter_symbol_pins(symbol):
                        try:
                            pin_number, _ = _ensure_pin_metadata(schematic, symbol, pin)
                            if not pin_number:
                                continue
                            pin_type = _get_pin_type(pin)
                            loc = _get_pin_location(pin)
                            if loc is None:
                                continue
                            coord = _coord_key(loc.x, loc.y)
                            pin_locations[coord].append({
                                "reference": reference,
                                "pin": pin_number,
                                "pinType": pin_type,
                            })
                        except Exception as exc:
                            logger.debug("Skipping pin while building net context: %s", exc)
                            continue
            except Exception as exc:
                logger.debug("Skipping symbol while building net context: %s", exc)
                continue

    wire_points_by_idx: Dict[int, List[Tuple[float, float]]] = {}
    all_wire_points: Dict[Tuple[float, float], List[int]] = defaultdict(list)
    if hasattr(schematic, 'wire'):
        for wire_idx, wire in enumerate(getattr(schematic, 'wire', [])):
            points: List[Tuple[float, float]] = []
            try:
                if hasattr(wire, 'points'):
                    for point in getattr(wire, 'points', []):
                        if hasattr(point, 'value'):
                            coords = point.value
                            if len(coords) >= 2:
                                key = _coord_key(coords[0], coords[1])
                                points.append(key)
                                all_wire_points[key].append(wire_idx)
            except Exception as exc:
                logger.debug("Skipping wire while building net context: %s", exc)
            wire_points_by_idx[wire_idx] = points

    label_locations: Dict[Tuple[float, float], List[Dict[str, Any]]] = defaultdict(list)
    try:
        if hasattr(schematic, 'tree'):
            for elem in getattr(schematic, 'tree', []):
                if not isinstance(elem, list) or len(elem) < 2:
                    continue
                try:
                    head = elem[0]
                    head_val = head.value() if hasattr(head, 'value') else str(head)
                except Exception:
                    head_val = str(elem[0])

                if head_val not in {'hierarchical_label', 'global_label', 'label'}:
                    continue

                try:
                    name = str(elem[1]).strip()
                except Exception:
                    continue
                if not name:
                    continue

                at_node = None
                for sub in elem:
                    if isinstance(sub, list) and len(sub) >= 3:
                        try:
                            tag = sub[0].value() if hasattr(sub[0], 'value') else str(sub[0])
                        except Exception:
                            tag = str(sub[0])
                        if tag == 'at':
                            at_node = sub
                            break

                if at_node is None:
                    continue

                try:
                    x_val = float(at_node[1])
                    y_val = float(at_node[2])
                except (TypeError, ValueError):
                    continue

                coord = _coord_key(x_val, y_val)
                label_locations[coord].append({
                    "name": name,
                    "type": head_val,
                })
    except Exception as exc:
        logger.warning("Error while indexing schematic labels for net context: %s", exc)

    power_locations: Dict[Tuple[float, float], List[str]] = defaultdict(list)
    try:
        if hasattr(schematic, 'symbol'):
            for symbol in getattr(schematic, 'symbol', []):
                try:
                    lib_id = getattr(getattr(symbol, 'lib_id', None), 'value', '') or ''
                    if 'power' not in lib_id.lower():
                        continue

                    value_prop = getattr(getattr(symbol, 'property', None), 'Value', None)
                    power_name = getattr(value_prop, 'value', '') if value_prop is not None else ''
                    power_name = str(power_name).strip()
                    if not power_name:
                        continue

                    if hasattr(symbol, 'at') and getattr(symbol.at, 'value', None):
                        coords = list(symbol.at.value)
                        try:
                            x_val = float(coords[0]) if len(coords) > 0 else 0.0
                            y_val = float(coords[1]) if len(coords) > 1 else 0.0
                        except (TypeError, ValueError):
                            continue
                        coord = _coord_key(x_val, y_val)
                        if power_name not in power_locations[coord]:
                            power_locations[coord].append(power_name)
                except Exception as exc:
                    logger.debug("Skipping power symbol while building net context: %s", exc)
                    continue
    except Exception as exc:
        logger.warning("Error while indexing power symbols for net context: %s", exc)

    wire_to_net: Dict[int, int] = {}
    net_counter = 0

    for point, wire_indices in all_wire_points.items():
        if len(wire_indices) > 1:
            existing_nets = {wire_to_net[w] for w in wire_indices if w in wire_to_net}
            if existing_nets:
                net_id = min(existing_nets)
                for wire_idx in wire_indices:
                    wire_to_net[wire_idx] = net_id
                for w_idx, n_id in list(wire_to_net.items()):
                    if n_id in existing_nets and n_id != net_id:
                        wire_to_net[w_idx] = net_id
            else:
                for wire_idx in wire_indices:
                    wire_to_net[wire_idx] = net_counter
                net_counter += 1
        elif len(wire_indices) == 1:
            wire_idx = wire_indices[0]
            if wire_idx not in wire_to_net:
                wire_to_net[wire_idx] = net_counter
                net_counter += 1

    point_to_net: Dict[Tuple[float, float], int] = {}
    for point, wire_indices in all_wire_points.items():
        if wire_indices:
            net_id = wire_to_net.get(wire_indices[0])
            if net_id is not None:
                point_to_net[point] = net_id

    return {
        "pin_locations": pin_locations,
        "label_locations": label_locations,
        "power_locations": power_locations,
        "wire_points_by_idx": wire_points_by_idx,
        "all_wire_points": dict(all_wire_points),
        "wire_to_net": wire_to_net,
        "point_to_net": point_to_net,
        "next_net_id": net_counter,
    }


def _build_net_info_from_context(
    schematic: Schematic,
    wire_points: List[Tuple[float, float]],
    context: Dict[str, Any],
) -> Dict[str, Any]:
    """Build net information using a precomputed connectivity context."""
    wire_point_set = {_coord_key(x, y) for x, y in wire_points}

    point_to_net: Dict[Tuple[float, float], int] = context.get("point_to_net", {})
    net_id: Optional[int] = None
    for point in wire_point_set:
        if point in point_to_net:
            net_id = point_to_net[point]
            break

    if net_id is None:
        net_id = context.get("next_net_id", 0)

    connected_pins: List[str] = []
    pin_locations: Dict[Tuple[float, float], List[Dict[str, Any]]] = context.get("pin_locations", {})

    for point, pins in pin_locations.items():
        same_net = point_to_net.get(point) == net_id or point in wire_point_set
        if not same_net:
            continue
        for pin_data in pins:
            formatted = _format_pin_with_type(
                pin_data["reference"],
                pin_data["pin"],
                pin_data["pinType"],
            )
            if formatted not in connected_pins:
                connected_pins.append(formatted)

    connected_labels: List[str] = []
    label_locations: Dict[Tuple[float, float], List[Dict[str, Any]]] = context.get("label_locations", {})
    for point, labels in label_locations.items():
        same_net = point_to_net.get(point) == net_id or point in wire_point_set
        if not same_net:
            continue
        for label in labels:
            name = label.get("name")
            if name and name not in connected_labels:
                connected_labels.append(name)

    connected_power: List[str] = []
    power_locations: Dict[Tuple[float, float], List[str]] = context.get("power_locations", {})
    for point, powers in power_locations.items():
        same_net = point_to_net.get(point) == net_id or point in wire_point_set
        if not same_net:
            continue
        for power_name in powers:
            if power_name not in connected_power:
                connected_power.append(power_name)

    if connected_power:
        net_name = connected_power[0]
    elif connected_labels:
        net_name = connected_labels[0]
    else:
        net_name = format_anonymous_net_name(net_id)

    return {
        "net": net_name,
        "netConnections": connected_power + connected_labels + connected_pins,
        "connectedPower": connected_power,
        "connectedLabels": connected_labels,
        "connectedPins": connected_pins,
        "netId": net_id,
    }


def _build_net_info(schematic: Schematic, wire_points: List[Tuple[float, float]]) -> Dict[str, Any]:
    """
    Build net information by analyzing what's connected at the given wire points.

    Returns a dict with:
    - net: Net identifier (e.g., "Net 5", "GND", "VBAT")
    - netConnections: List of connected power/labels/pins (e.g., ["GND", "IO_LBL", "R1.1(passive)"])
    """
    context = _collect_net_context(schematic)
    return _build_net_info_from_context(schematic, wire_points, context)


def _find_wire_by_uuid(schematic: Schematic, wire_uuid: str) -> Optional[WireWrapper]:
    if not hasattr(schematic, 'wire'):
        return None
    for wire in schematic.wire:
        if getattr(getattr(wire, 'uuid', None), 'value', None) == wire_uuid:
            return wire
    return None


def _reference_from_symbol(symbol: Symbol) -> str:
    if hasattr(symbol, 'property') and hasattr(symbol.property, 'Reference'):
        return symbol.property.Reference.value
    reference = getattr(symbol, 'reference', None)
    if isinstance(reference, str):
        return reference
    return ''


def _coerce_unit_value(unit: Any) -> Optional[str]:
    if unit is None:
        return None
    try:
        return str(int(unit))
    except (TypeError, ValueError):
        return str(unit)


def _find_symbol(
    schematic: Schematic,
    reference: str,
    *,
    unit: Optional[Any] = None,
) -> Symbol:
    if not hasattr(schematic, 'symbol'):
        raise ValueError("Schematic contains no symbols")

    desired_unit = _coerce_unit_value(unit)
    matches: List[Symbol] = []

    for symbol in schematic.symbol:
        if _reference_from_symbol(symbol) != reference:
            continue

        if desired_unit is None:
            matches.append(symbol)
        else:
            symbol_unit = getattr(symbol, 'unit', None)
            symbol_unit_value = None
            if symbol_unit is not None and hasattr(symbol_unit, 'value'):
                symbol_unit_value = _coerce_unit_value(symbol_unit.value)
            if symbol_unit_value == desired_unit:
                matches.append(symbol)

    if not matches:
        suffix = f" unit '{unit}'" if unit is not None else ''
        raise ValueError(f"Component '{reference}'{suffix} not found in schematic")

    if len(matches) > 1:
        if desired_unit is None:
            raise ValueError(
                f"Multiple units of component '{reference}' found; please specify the unit"
            )

    return matches[0]


def _get_symbol_unit(symbol: Symbol) -> Optional[int]:
    """Extract the unit index for a symbol instance, if available."""
    unit_attr = getattr(symbol, 'unit', None)
    value = None
    if unit_attr is not None:
        value = getattr(unit_attr, 'value', unit_attr)
    if value is None:
        return None
    try:
        return int(str(value).strip())
    except (ValueError, TypeError):
        return None


def _extract_pin_identifiers_from_raw(pin_raw: Any) -> Tuple[str, str]:
    """Derive pin number and name from a raw pin S-expression."""
    number = ''
    name = ''
    if isinstance(pin_raw, list):
        for entry in pin_raw[1:]:
            if isinstance(entry, str) and not isinstance(entry, SSymbol):
                candidate = str(entry).strip()
                if candidate and not number:
                    number = candidate
            elif isinstance(entry, list) and entry:
                tag = _atom_to_str(entry[0])
                if tag == 'number' and len(entry) > 1 and not number:
                    number = str(entry[1]).strip()
                elif tag == 'name' and len(entry) > 1 and not name:
                    name = str(entry[1]).strip()
    return number, name


def _extract_pin_at_from_raw(pin_raw: Any) -> Tuple[Optional[float], Optional[float], float]:
    """Extract the pin offset (x, y) and rotation from a raw pin S-expression."""
    x = None
    y = None
    rotation = 0.0
    if isinstance(pin_raw, list):
        for entry in pin_raw[1:]:
            if isinstance(entry, list) and entry:
                tag = _atom_to_str(entry[0])
                if tag == 'at':
                    try:
                        if len(entry) >= 3:
                            x = float(entry[1])
                            y = float(entry[2])
                        if len(entry) >= 4:
                            rotation = float(entry[3])
                    except (TypeError, ValueError):
                        x = y = None
                        rotation = 0.0
    return x, y, rotation


def _iter_symbol_pins(symbol: Symbol) -> List[Any]:
    """Return a stable list of pin objects for a symbol, handling single-pin edge cases."""
    pins_attr = getattr(symbol, 'pin', None)
    if pins_attr is None:
        return []

    if isinstance(pins_attr, list):
        return list(pins_attr)

    elements = getattr(pins_attr, '_elements', None)
    if isinstance(elements, list) and elements:
        return list(elements)

    if getattr(pins_attr, 'entity_type', None) == 'pin':
        return [pins_attr]

    collected: List[Any] = []
    try:
        for candidate in pins_attr:
            if getattr(candidate, 'entity_type', None) == 'pin':
                collected.append(candidate)
    except Exception:
        collected = []

    if collected:
        return collected

    if isinstance(pins_attr, (str, bytes)):
        return []

    return [pins_attr]


def _find_library_symbol_node(
    schematic: Schematic,
    lib_id: str,
) -> Tuple[Optional[List[Any]], Optional[List[int]]]:
    located = _find_lib_symbols_node_with_path(schematic)
    if not located:
        return None, None
    lib_symbols_node, lib_symbols_path = located

    symbol_name_only = lib_id.split(':')[-1] if ':' in lib_id else lib_id

    for child_index, entry in enumerate(lib_symbols_node[1:], start=1):
        if not _is_entry(entry, 'symbol') or len(entry) < 2:
            continue
        sym_name = _atom_to_str(entry[1])
        if sym_name == lib_id or sym_name == symbol_name_only or (
            lib_id and sym_name.startswith(f"{lib_id}_")
        ) or (symbol_name_only and sym_name.startswith(f"{symbol_name_only}_")):
            return entry, lib_symbols_path + [child_index]
    return None, None


def _find_library_pin_node(
    symbol_node: List[Any],
    pin_number: str,
    base_path: List[int],
) -> Tuple[Optional[List[Any]], Optional[List[int]]]:
    """Search a library symbol node (and nested symbols) for a matching pin entry."""
    target = str(pin_number).strip()
    if not target:
        return None, None

    stack: List[Tuple[List[Any], List[int]]] = [(symbol_node, base_path)]
    while stack:
        current, path = stack.pop()
        if not isinstance(current, list):
            continue
        if _is_entry(current, 'pin'):
            number, _ = _extract_pin_identifiers_from_raw(current)
            if number.strip() == target:
                return current, path
        for idx, child in enumerate(current[1:], start=1):
            if isinstance(child, list):
                stack.append((child, path + [idx]))
    return None, None


def _enrich_pin_from_library(
    schematic: Schematic,
    symbol: Symbol,
    pin: Any,
    pin_number: str,
) -> Tuple[str, str]:
    """Ensure pin has number/name/location/electrical type by consulting library data."""
    number = str(getattr(pin, 'number', '')).strip()
    name = str(getattr(pin, 'name', '')).strip()

    if number and getattr(pin, '_mcp_pin_enriched', False):
        # Already hydrated
        return number, name

    lib_id = getattr(getattr(symbol, 'lib_id', None), 'value', '') or ''
    if not lib_id:
        return number or pin_number, name

    symbol_node, symbol_path = _find_library_symbol_node(schematic, lib_id)
    if not symbol_node or not symbol_path:
        return number or pin_number, name

    unit = _get_symbol_unit(symbol)
    search_nodes: List[Tuple[List[Any], List[int]]] = []
    nested_symbols: List[Tuple[List[Any], List[int]]] = []
    for child_index, child in enumerate(symbol_node[2:], start=2):
        if _is_entry(child, 'symbol'):
            nested_symbols.append((child, symbol_path + [child_index]))
    if nested_symbols:
        if unit is not None:
            preferred: List[Tuple[List[Any], List[int]]] = []
            remainder: List[Tuple[List[Any], List[int]]] = []
            suffix = f"_{unit}"
            suffix_alt = f"_{unit}_"
            for candidate, path in nested_symbols:
                node_name = _atom_to_str(candidate[1]) if len(candidate) > 1 else ''
                if node_name.endswith(suffix) or node_name.endswith(f"{suffix}_1") or node_name.endswith(suffix_alt):
                    preferred.append((candidate, path))
                else:
                    remainder.append((candidate, path))
            search_nodes.extend(preferred + remainder)
        else:
            search_nodes.extend(nested_symbols)
    else:
        search_nodes.append((symbol_node, symbol_path))

    lib_pin_node: Optional[List[Any]] = None
    lib_pin_path: Optional[List[int]] = None
    for candidate, path in search_nodes:
        lib_pin_node, lib_pin_path = _find_library_pin_node(candidate, pin_number or number, path)
        if lib_pin_node is not None and lib_pin_path is not None:
            break

    if lib_pin_node is None or lib_pin_path is None:
        return number or pin_number, name

    lib_number, lib_name = _extract_pin_identifiers_from_raw(lib_pin_node)
    if lib_number:
        number = lib_number
        try:
            setattr(pin, 'number', lib_number)
        except Exception:
            pass
    if lib_name and lib_name != '~':
        name = lib_name
        try:
            setattr(pin, 'name', lib_name)
        except Exception:
            pass

    # Electrical type (optional)
    if not hasattr(pin, 'electrical_type'):
        try:
            if len(lib_pin_node) > 1 and isinstance(lib_pin_node[1], sexpdata.Symbol):
                setattr(pin, 'electrical_type', lib_pin_node[1].value())
        except Exception:
            pass

    # Location (absolute) using symbol transform
    if _get_pin_location(pin) is None:
        hydrated = False
        ctor = _SYMBOL_PIN_CTOR
        if ctor is not None:
            try:
                lib_pin_parsed = ParsedValue(schematic.tree, lib_pin_node, lib_pin_path, schematic)
                wrapped: Any = ctor(pin, lib_pin_parsed)
                loc_value = getattr(wrapped, 'location', None)
                point = SimpleNamespace(
                    x=float(getattr(loc_value, 'x', 0.0)),
                    y=float(getattr(loc_value, 'y', 0.0)),
                    rotation=float(getattr(loc_value, 'rotation', 0.0)),
                )
                try:
                    pin.__dict__['_mcp_location'] = point
                except Exception:
                    setattr(pin, '_mcp_location', point)
                try:
                    setattr(pin, 'location', point)
                except Exception:
                    pass
                if not number:
                    number = str(getattr(wrapped, 'number', '')).strip()
                    setattr(pin, 'number', number)
                if not name:
                    name = str(getattr(wrapped, 'name', '')).strip()
                    setattr(pin, 'name', name)
                if not hasattr(pin, 'electrical_type'):
                    setattr(pin, 'electrical_type', getattr(wrapped, 'electrical_type', 'passive'))
                hydrated = True
            except Exception:
                hydrated = False

        if not hydrated:
            # Fall back to simple transform if SymbolPin instantiation fails or is unavailable
            pin_x, pin_y, _ = _extract_pin_at_from_raw(lib_pin_node)
            if pin_x is None or pin_y is None:
                pin_x = pin_y = 0.0

            at_vals = list(getattr(getattr(symbol, 'at', None), 'value', []) or [])
            origin_x = float(at_vals[0]) if len(at_vals) > 0 else 0.0
            origin_y = float(at_vals[1]) if len(at_vals) > 1 else 0.0
            rotation = float(at_vals[2]) if len(at_vals) > 2 else 0.0

            dx, dy = _rotate_offset(pin_x, pin_y, rotation)
            point = SimpleNamespace(
                x=float(round(origin_x + dx, 6)),
                y=float(round(origin_y + dy, 6)),
                rotation=float(rotation),
            )
            try:
                setattr(pin, 'location', point)
            except Exception:
                pass
            try:
                pin.__dict__['_mcp_location'] = point
            except Exception:
                setattr(pin, '_mcp_location', point)
            if not hasattr(pin, 'electrical_type'):
                setattr(pin, 'electrical_type', getattr(pin, 'electrical_type', 'passive'))

    try:
        setattr(pin, '_mcp_pin_enriched', True)
    except Exception:
        pass

    return number or pin_number, name


def _ensure_pin_metadata(
    schematic: Schematic,
    symbol: Symbol,
    pin: Any,
) -> Tuple[str, str]:
    """Return (number, name) for a pin, enriching from raw/library as required."""
    number = str(getattr(pin, 'number', '')).strip()
    name = str(getattr(pin, 'name', '')).strip()

    if not number or not name:
        raw = getattr(pin, 'raw', None)
        raw_number, raw_name = _extract_pin_identifiers_from_raw(raw)
        if raw_number and not number:
            number = raw_number
            try:
                setattr(pin, 'number', raw_number)
            except Exception:
                pass
        if raw_name and not name and raw_name != '~':
            name = raw_name
            try:
                setattr(pin, 'name', raw_name)
            except Exception:
                pass

    number, name = _enrich_pin_from_library(schematic, symbol, pin, number)
    if not number:
        number = ''
    if not name:
        name = ''
    return number, name


def _get_pin_location(pin: Any) -> Optional[Any]:
    """Retrieve a pin's cached or computed location."""
    loc = getattr(pin, 'location', None)
    if loc is None:
        loc = getattr(pin, '_mcp_location', None)
    return loc


def _resolve_pin(
    schematic: Schematic,
    pin_spec: Dict[str, Any],
    *,
    label: str,
) -> Tuple[Symbol, Any, Tuple[float, float]]:
    if not isinstance(pin_spec, dict):
        raise TypeError(f"{label} pin specification must be an object")

    reference = pin_spec.get('reference') or pin_spec.get('ref')
    if not reference:
        raise ValueError(f"{label} reference is required")

    pin_id = (
        pin_spec.get('pin')
        or pin_spec.get('pinNumber')
        or pin_spec.get('number')
        or pin_spec.get('pinName')
        or pin_spec.get('name')
    )
    if pin_id is None:
        raise ValueError(f"{label} pin identifier is required")

    symbol = _find_symbol(schematic, reference, unit=pin_spec.get('unit'))

    target_pin: Optional[Any] = None
    pin_id_normalised = str(pin_id).strip().lower()

    for pin in _iter_symbol_pins(symbol):
        number, name = _ensure_pin_metadata(schematic, symbol, pin)
        number_lc = number.strip().lower()
        name_lc = name.strip().lower()
        if not number_lc and not name_lc:
            continue
        if pin_id_normalised in {number, name}:
            target_pin = pin
            break

    if target_pin is None:
        raise ValueError(
            f"Pin '{pin_id}' not found on component '{reference}'"
        )

    loc = _get_pin_location(target_pin)
    if loc is None:
        # Attempt one final enrichment in case metadata set after selection
        number, _ = _ensure_pin_metadata(schematic, symbol, target_pin)
        if _get_pin_location(target_pin) is None and number:
            _enrich_pin_from_library(schematic, symbol, target_pin, number)
        loc = _get_pin_location(target_pin)
    if loc is None:
        raise ValueError(f"Pin '{pin_id}' on component '{reference}' has no location information")

    point = (float(loc.x), float(loc.y))

    return symbol, target_pin, point


def _find_hierarchical_label(
    schematic: Schematic,
    label_name: str,
) -> Tuple[str, Tuple[float, float]]:
    """
    Find a connection label by name and return its position.

    This searches, in order:
    - hierarchical_label nodes
    - global_label nodes
    - local label nodes (label)
    - power symbols (symbols from the "power" library whose Value matches)

    Args:
        schematic: The schematic to search
        label_name: The name of the label/power net to find

    Returns:
        Tuple of (matched_name, (x, y)) coordinates

    Raises:
        ValueError: If the label or power symbol is not found
    """
    if not hasattr(schematic, 'tree') or not isinstance(schematic.tree, list):
        raise ValueError("Schematic tree is not accessible")

    target = str(label_name).strip()

    # 1) Search hierarchical/global/local label nodes for a matching name
    for elem in schematic.tree:
        if not isinstance(elem, list) or len(elem) < 2:
            continue

        head = getattr(elem[0], 'value', None)
        if callable(head):
            head = elem[0].value()

        if head in {'hierarchical_label', 'global_label', 'label'}:
            current_name = str(elem[1]).strip()
            if current_name != target:
                continue

            # Find the 'at' element which contains position
            for sub in elem:
                if (
                    isinstance(sub, list)
                    and len(sub) >= 3
                    and hasattr(sub[0], 'value')
                    and sub[0].value() == 'at'
                ):
                    x = float(sub[1])
                    y = float(sub[2])
                    return (current_name, (x, y))

            # Found the label but no position - this shouldn't happen
            raise ValueError(
                f"Label '{label_name}' found but has no position information"
            )

    # 2) Search power symbols by value (e.g., GND, VCC, +5V)
    if hasattr(schematic, 'symbol'):
        for symbol in getattr(schematic, 'symbol'):
            try:
                lib_id = getattr(getattr(symbol, 'lib_id', None), 'value', '') or ''
                if 'power' not in lib_id.lower():
                    continue
                value = getattr(getattr(symbol, 'property', None), 'Value', None)
                value_str = getattr(value, 'value', '') if value is not None else ''
                if str(value_str).strip() != target:
                    continue
                # Position from the symbol's 'at' field
                if hasattr(symbol, 'at') and getattr(symbol.at, 'value', None):
                    coords = list(symbol.at.value)
                    x = float(coords[0]) if len(coords) > 0 else 0.0
                    y = float(coords[1]) if len(coords) > 1 else 0.0
                    return (target, (x, y))
            except Exception:
                continue

    raise ValueError(f"Label or power symbol '{label_name}' not found in schematic")


def _resolve_connection_point(
    schematic: Schematic,
    spec: Dict[str, Any],
    *,
    label: str,
) -> Tuple[Optional[Any], Optional[Any], Tuple[float, float], str]:
    """
    Resolve a connection point specification to coordinates.

    Supports component pins and pin-less connection anchors:
    - Pin spec: { reference: "R1", pin: "1", unit?: 1 }
    - Label spec: { label: "NET" } or { labelName: "NET" }
      where NET can be a hierarchical label, a global label, a local label, or
      the Value of a power symbol (e.g., "GND", "VCC", "+5V").

    Args:
        schematic: The schematic containing the connection point
        spec: Dictionary specifying either a pin or a label/power name
        label: Description for error messages (e.g., "source", "target")

    Returns:
        Tuple of (symbol_or_none, pin_or_none, (x, y), connection_type)
        - For pins: (Symbol, Pin, (x, y), "pin")
        - For labels/power: (None, None, (x, y), "label")

    Raises:
        TypeError: If spec is not a dictionary
        ValueError: If spec is ambiguous or invalid
    """
    if not isinstance(spec, dict):
        raise TypeError(f"{label} specification must be an object")

    # Check what type of connection point this is
    has_reference = bool(spec.get('reference') or spec.get('ref'))
    has_label = bool(spec.get('label') or spec.get('labelName'))

    # Validate that spec is not ambiguous
    if has_reference and has_label:
        raise ValueError(
            f"{label} specification is ambiguous: contains both 'reference' and 'label' fields. "
            "Please specify either a component pin OR a label/power name, not both."
        )

    if not has_reference and not has_label:
        raise ValueError(
            f"{label} specification is invalid: must contain either 'reference' (for pin) "
            "or 'label'/'labelName' (for hierarchical/global label or power symbol)"
        )

    # Resolve as component pin
    if has_reference:
        symbol, pin, point = _resolve_pin(schematic, spec, label=label)
        return (symbol, pin, point, "pin")

    # Resolve as hierarchical label
    label_name = spec.get('label') or spec.get('labelName')
    if not isinstance(label_name, str) or not label_name.strip():
        raise ValueError(f"{label} specification missing label name")
    _, point = _find_hierarchical_label(schematic, label_name)
    return (None, None, point, "label")


# --- Safe Manhattan routing that avoids pin/node collisions and avoids crossing symbol bodies ---
from .grid_utils import KICAD_SCHEMATIC_GRID_MM


def _collect_pin_coords(schematic: Schematic) -> List[Tuple[float, float]]:
    coords: List[Tuple[float, float]] = []
    try:
        if hasattr(schematic, 'symbol') and schematic.symbol is not None:
            for sym in schematic.symbol:
                if hasattr(sym, 'pin') and sym.pin is not None:
                    for pin in sym.pin:
                        if hasattr(pin, 'location') and pin.location is not None:
                            try:
                                x, y = _coord_key(pin.location.x, pin.location.y)
                                coords.append((x, y))
                            except Exception:
                                continue
    except Exception:
        pass
    return coords


def _collect_wire_vertices(schematic: Schematic) -> List[Tuple[float, float]]:
    pts: List[Tuple[float, float]] = []
    try:
        if hasattr(schematic, 'wire') and schematic.wire is not None:
            for w in schematic.wire:
                if hasattr(w, 'points') and w.points is not None:
                    for pt in w.points:
                        if hasattr(pt, 'value') and len(pt.value) >= 2:
                            try:
                                x, y = _coord_key(pt.value[0], pt.value[1])
                                pts.append((x, y))
                            except Exception:
                                continue
    except Exception:
        pass
    return pts


def _build_routing_obstacles(schematic: Schematic) -> RoutingObstacles:
    """Assemble precomputed geometry used by the standalone routing module."""
    return RoutingObstacles(
        pin_vertices=_collect_pin_coords(schematic),
        node_vertices=_collect_wire_vertices(schematic),
        symbol_bboxes=_collect_symbol_bboxes(schematic),
    )


def _is_entry(node: Any, name: str) -> bool:
    try:
        if not isinstance(node, list):
            return False
        if not node:
            return False
        first = node[0]
        if not hasattr(first, 'value'):
            return False
        return first.value() == name
    except Exception:
        return False


def _refresh_wire_collection(schematic: Schematic) -> None:
    """Rebuild schematic.wire wrappers to keep indices valid after edits."""
    try:
        if not hasattr(schematic, 'wire'):
            return
        wire_nodes: List[Any] = []
        for index, node in enumerate(getattr(schematic, 'tree', [])):
            if _is_entry(node, 'wire'):
                parsed = ParsedValue(schematic.tree, node, [index], schematic)
                wire_nodes.append(schematic.wrap(parsed))
        try:
            schematic.wire._elements = wire_nodes
        except Exception:
            pass
    except Exception as exc:
        logger.warning("Failed to refresh wire collection: %s", exc)




def _make_net_label_node(name: str, x: float, y: float) -> List[Any]:
    """Create a local net label node anchored at the provided coordinate."""
    snapped_x, snapped_y = snap_point_to_grid(float(x), float(y))
    rounded_x = round_to_grid(snapped_x)
    rounded_y = round_to_grid(snapped_y)

    effects_node: List[Any] = [
        SSymbol('effects'),
        [SSymbol('font'), [SSymbol('size'), 1.27, 1.27]],
        [SSymbol('justify'), SSymbol('left'), SSymbol('bottom')],
    ]

    return [
        SSymbol('label'),
        name,
        [SSymbol('at'), rounded_x, rounded_y, 0],
        [SSymbol('fields_autoplaced')],
        effects_node,
        [SSymbol('uuid'), str(uuid.uuid4())],
    ]


def _append_label_node(schematic: Schematic, node: List[Any]) -> None:
    """Append a label node to the schematic and update cached wrappers."""
    schematic.tree.append(node)
    try:
        node_index = len(schematic.tree) - 1
        parsed_label = ParsedValue(schematic.tree, node, [node_index], schematic)
        label_wrapper = schematic.wrap(parsed_label)
        label_collection = getattr(schematic, 'label', None)
        if label_collection is not None:
            try:
                label_collection.append(label_wrapper)
            except Exception:
                _refresh_label_collection(schematic)
    except Exception as exc:
        logger.debug("Unable to append label wrapper directly: %s", exc)
        _refresh_label_collection(schematic)


def _refresh_label_collection(schematic: Schematic) -> None:
    """Rebuild schematic.label wrappers to keep indices valid after edits."""
    try:
        label_collection = getattr(schematic, 'label', None)
        if label_collection is None:
            return

        label_nodes: List[Any] = []
        for index, node in enumerate(getattr(schematic, 'tree', [])):
            if _is_entry(node, 'label'):
                parsed = ParsedValue(schematic.tree, node, [index], schematic)
                label_nodes.append(schematic.wrap(parsed))

        try:
            label_collection._elements = label_nodes  # type: ignore[attr-defined]
        except Exception:
            try:
                label_collection.clear()
                label_collection.extend(label_nodes)
            except Exception:
                logger.debug("Unable to refresh schematic.label collection directly")
    except Exception as exc:
        logger.warning("Failed to refresh label collection: %s", exc)


class SchematicCompiler:
    """Utilities for materialising schematic nets into explicit labels."""

    @staticmethod
    def compile(schematic: Schematic) -> Dict[str, Any]:
        """
        Compile a schematic by attaching labels to unlabeled wire nets.

        Args:
            schematic: The schematic object to update in-memory.

        Returns:
            Dict[str, Any]: Summary of compilation results, including the labels
            created and the total number of nets that were analysed.
        """
        context = _collect_net_context(schematic)
        point_to_net: Dict[Tuple[float, float], int] = context.get("point_to_net", {})

        nets: Dict[int, Dict[str, Any]] = {}
        for point, net_id in point_to_net.items():
            record = nets.setdefault(net_id, {"points": set()})
            record["points"].add(point)

        if not nets:
            return {
                "labelsAdded": [],
                "totalNets": 0,
                "generatedLabelCount": 0,
                "skippedExistingLabels": 0,
            }

        created_labels: List[Dict[str, Any]] = []
        for net_id in sorted(nets.keys()):
            raw_points = nets[net_id]["points"]
            if not raw_points:
                continue
            net_points = sorted(raw_points)
            net_info = _build_net_info_from_context(schematic, net_points, context)

            connected_labels = net_info.get("connectedLabels", [])
            connected_power = net_info.get("connectedPower", [])
            if connected_labels or connected_power:
                continue

            net_name = net_info["net"]
            anchor_x, anchor_y = net_points[0]

            label_node = _make_net_label_node(net_name, anchor_x, anchor_y)
            _append_label_node(schematic, label_node)

            created_labels.append({
                "net": net_name,
                "position": {"x": anchor_x, "y": anchor_y},
                "connections": net_info.get("netConnections", []),
                "netId": net_info.get("netId", net_id),
            })

        if created_labels:
            _refresh_label_collection(schematic)

        return {
            "labelsAdded": created_labels,
            "totalNets": len(nets),
            "generatedLabelCount": len(created_labels),
            "skippedExistingLabels": len(nets) - len(created_labels),
        }


def _atom_to_str(atom: Any) -> str:
    try:
        if hasattr(atom, 'value') and callable(atom.value):
            return str(atom.value())
        return str(atom)
    except Exception:
        return str(atom)


def _find_lib_symbols_node(schematic: Schematic) -> Optional[List[Any]]:
    try:
        for node in getattr(schematic, 'tree', []):
            if _is_entry(node, 'lib_symbols'):
                return node
    except Exception:
        pass
    return None


def _find_lib_symbols_node_with_path(schematic: Schematic) -> Optional[Tuple[List[Any], List[int]]]:
    try:
        for idx, node in enumerate(getattr(schematic, 'tree', [])):
            if _is_entry(node, 'lib_symbols'):
                return node, [idx]
    except Exception:
        pass
    return None


def _extract_poly_points_from_lib_symbol(symbol_node: List[Any]) -> List[Tuple[float, float]]:
    """Collect all geometry points from the library symbol node (library space).

    Extracts points from:
    - Polylines (pts with xy coordinates)
    - Rectangles (start and end corners)
    - Circles (center ± radius to get bounding box)
    - Arcs (start, mid, end points)
    """
    pts: List[Tuple[float, float]] = []

    def visit(n: Any) -> None:
        if not isinstance(n, list):
            return

        # Extract polyline points
        if _is_entry(n, 'polyline'):
            for child in n:
                if _is_entry(child, 'pts'):
                    for item in child[1:]:
                        if isinstance(item, list) and item and _atom_to_str(item[0]) == 'xy' and len(item) >= 3:
                            try:
                                px = float(item[1])
                                py = float(item[2])
                                pts.append((px, py))
                            except Exception:
                                continue

        # Extract rectangle corners
        elif _is_entry(n, 'rectangle'):
            start_pt = None
            end_pt = None
            for child in n:
                if isinstance(child, list) and len(child) >= 3:
                    tag = _atom_to_str(child[0])
                    if tag == 'start':
                        try:
                            start_pt = (float(child[1]), float(child[2]))
                        except Exception:
                            pass
                    elif tag == 'end':
                        try:
                            end_pt = (float(child[1]), float(child[2]))
                        except Exception:
                            pass
            if start_pt:
                pts.append(start_pt)
            if end_pt:
                pts.append(end_pt)

        # Extract circle bounding box
        elif _is_entry(n, 'circle'):
            center = None
            radius = None
            for child in n:
                if isinstance(child, list) and len(child) >= 3:
                    tag = _atom_to_str(child[0])
                    if tag == 'center':
                        try:
                            center = (float(child[1]), float(child[2]))
                        except Exception:
                            pass
                elif isinstance(child, list) and len(child) >= 2:
                    tag = _atom_to_str(child[0])
                    if tag == 'radius':
                        try:
                            radius = float(child[1])
                        except Exception:
                            pass
            if center and radius:
                # Add 4 corners of bounding box
                pts.append((center[0] - radius, center[1] - radius))
                pts.append((center[0] + radius, center[1] - radius))
                pts.append((center[0] - radius, center[1] + radius))
                pts.append((center[0] + radius, center[1] + radius))

        # Extract arc points
        elif _is_entry(n, 'arc'):
            for child in n:
                if isinstance(child, list) and len(child) >= 3:
                    tag = _atom_to_str(child[0])
                    if tag in ('start', 'mid', 'end'):
                        try:
                            pts.append((float(child[1]), float(child[2])))
                        except Exception:
                            pass

        # Recurse into children
        else:
            for child in n:
                visit(child)

    visit(symbol_node)
    return pts


def _rotate_offset(dx: float, dy: float, rotation_deg: float) -> Tuple[float, float]:
    radians = math.radians(rotation_deg % 360)
    cos_theta = math.cos(radians)
    sin_theta = math.sin(radians)
    return (
        dx * cos_theta - dy * sin_theta,
        dx * sin_theta + dy * cos_theta,
    )


def _symbol_body_bbox_from_lib(schematic: Schematic, sym: Symbol) -> Optional[Tuple[float, float, float, float]]:
    """Compute the instance bbox of the symbol's body using its library geometry (no margin)."""
    try:
        lib_id = getattr(getattr(sym, 'lib_id', None), 'value', '') or ''
        if not lib_id:
            return None
        lib_symbols_node = _find_lib_symbols_node(schematic)
        if not lib_symbols_node:
            return None
        # Find matching library symbol definition
        symbol_name_only = lib_id.split(':')[-1] if ':' in lib_id else lib_id
        target_node: Optional[List[Any]] = None
        for child in lib_symbols_node:
            if not _is_entry(child, 'symbol') or len(child) < 2:
                continue
            sym_name = _atom_to_str(child[1])
            if sym_name == lib_id or sym_name.startswith(lib_id + '_') or sym_name == symbol_name_only:
                target_node = child
                break
        if target_node is None:
            return None
        lib_pts = _extract_poly_points_from_lib_symbol(target_node)
        if not lib_pts:
            return None
        # Transform library points to instance coordinates using (at x y rot)
        at_vals = list(getattr(getattr(sym, 'at', None), 'value', []) or [])
        x0 = float(at_vals[0]) if len(at_vals) > 0 else 0.0
        y0 = float(at_vals[1]) if len(at_vals) > 1 else 0.0
        rot = float(at_vals[2]) if len(at_vals) > 2 else 0.0
        xs: List[float] = []
        ys: List[float] = []
        for (lx, ly) in lib_pts:
            dx, dy = _rotate_offset(lx, ly, rot)
            xs.append(x0 + dx)
            ys.append(y0 + dy)
        return (
            round(min(xs), COORD_KEY_PRECISION),
            round(min(ys), COORD_KEY_PRECISION),
            round(max(xs), COORD_KEY_PRECISION),
            round(max(ys), COORD_KEY_PRECISION),
        )
    except Exception:
        return None



def _collect_symbol_bboxes(schematic: Schematic) -> List[Tuple[Tuple[float, float, float, float], Symbol]]:
    """Compute each symbol body's axis-aligned bbox from library geometry and expand by a fixed clearance.

    Fallback to pin-extents only if library geometry is unavailable. Power symbols are skipped.
    """
    boxes: List[Tuple[Tuple[float, float, float, float], Symbol]] = []
    clearance = BBOX_CLEARANCE_MM  # 0.5 grid clearance for routing
    try:
        if hasattr(schematic, 'symbol') and schematic.symbol is not None:
            for sym in schematic.symbol:
                # Skip power symbols as obstacles
                try:
                    lib_id = getattr(getattr(sym, 'lib_id', None), 'value', '') or ''
                    if lib_id and 'power' in lib_id.lower():
                        continue
                except Exception:
                    pass

                rect = _symbol_body_bbox_from_lib(schematic, sym)
                if rect is None:
                    # Fallback: derive bbox from pins
                    pts: List[Tuple[float, float]] = []
                    if hasattr(sym, 'pin') and sym.pin is not None:
                        for pin in _iter_symbol_pins(sym):
                            loc = _get_pin_location(pin)
                            if loc is not None:
                                try:
                                    pts.append((float(loc.x), float(loc.y)))
                                except Exception:
                                    continue
                    if not pts:
                        continue
                    xs = [p[0] for p in pts]
                    ys = [p[1] for p in pts]
                    rect = (min(xs), min(ys), max(xs), max(ys))
                # Expand by fixed clearance (no grid snapping - A* handles grid internally)
                xmin, ymin, xmax, ymax = rect
                xmin_expanded = xmin - clearance
                ymin_expanded = ymin - clearance
                xmax_expanded = xmax + clearance
                ymax_expanded = ymax + clearance
                boxes.append(((xmin_expanded, ymin_expanded, xmax_expanded, ymax_expanded), sym))
    except Exception:
        pass
    return boxes

class ConnectionManager:
    """Manage connections between components"""

    @staticmethod
    def add_wire(
        schematic: Schematic,
        start_point: Optional[Any],
        end_point: Optional[Any],
        properties: Optional[dict] = None,
    ) -> Union[WireWrapper, List[WireWrapper]]:
        """Add a wire between points, returning the resulting wrapper."""

        config = dict(properties or {})
        points_override = config.pop('points', None) or config.pop('pointList', None) or config.pop('segments', None)
        midpoints = config.pop('midpoints', None) or config.pop('viaPoints', None)

        if midpoints is not None:
            if points_override is None:
                points_override = []
            if not isinstance(points_override, list):
                raise TypeError('points must be provided as a list when using midpoints')
            points_override.extend(midpoints)

        width = config.pop('width', None)
        stroke_type = config.pop('strokeType', None) or config.pop('style', None) or 'default'
        wire_uuid = config.pop('uuid', None)

        if config:
            unknown = ', '.join(sorted(config.keys()))
            raise ValueError(f"Unsupported wire properties: {unknown}")

        normalised_points = _normalise_points(start_point, end_point, points_override)

        filtered_points: List[Tuple[float, float]] = []
        for point in normalised_points:
            candidate = (float(point[0]), float(point[1]))
            if not filtered_points or filtered_points[-1] != candidate:
                filtered_points.append(candidate)

        if len(filtered_points) < 2:
            raise ValueError('Wire must span at least two distinct coordinates')

        normalised_points = filtered_points
        _validate_segment_lengths(normalised_points, min_length=MIN_ROUTED_SEGMENT_MM, context='wire segment')

        if width is None:
            width = _DEFAULT_WIRE_WIDTH
        else:
            try:
                width = float(width)
            except (TypeError, ValueError) as exc:
                raise ValueError('width must be numeric') from exc
            if width <= 0:
                raise ValueError('width must be positive')

        stroke_type = str(stroke_type)
        if stroke_type.lower() not in _VALID_STROKE_TYPES:
            raise ValueError(f"strokeType must be one of: {', '.join(sorted(_VALID_STROKE_TYPES))}")
        stroke_type = stroke_type.lower()

        if wire_uuid is None:
            wire_uuid = str(uuid.uuid4())
        else:
            wire_uuid = str(wire_uuid)

        segment_pairs = list(zip(normalised_points[:-1], normalised_points[1:]))
        created_wires: List[WireWrapper] = []

        for segment_index, (segment_start, segment_end) in enumerate(segment_pairs):
            segment_uuid = wire_uuid if (segment_index == 0 and wire_uuid is not None) else str(uuid.uuid4())
            segment_points = [segment_start, segment_end]
            wire_node = _build_wire_node(
                segment_points,
                width=width,
                stroke_type=stroke_type,
                wire_uuid=segment_uuid,
            )

            schematic.tree.append(wire_node)
            node_index = len(schematic.tree) - 1
            parsed_wire = ParsedValue(schematic.tree, wire_node, [node_index], schematic)
            wire_wrapper = schematic.wrap(parsed_wire)

            if hasattr(schematic, 'wire'):
                schematic.wire.append(wire_wrapper)

            created_wires.append(wire_wrapper)

        logger.info(
            "Added %d wire segment(s), width=%s, stroke=%s",
            len(created_wires),
            width,
            stroke_type,
        )

        if len(created_wires) == 1:
            return created_wires[0]
        return created_wires

    @staticmethod
    def add_connection(schematic: Schematic, source_ref: str, source_pin: str, target_ref: str, target_pin: str):
        """Add a connection between component pins"""
        # kicad-skip handles connections implicitly through wires and labels.
        # This method would typically involve adding wires and potentially net labels
        # to connect the specified pins.
        # A direct 'add_connection' between pins isn't a standard kicad-skip operation
        # in the way it is in some other schematic tools.
        # We will need to implement this logic by finding the component pins
        # and adding wires/labels between their locations. This is more complex
        # and might require pin location information which isn't directly
        # exposed in a simple way by default in kicad-skip Symbol objects.

        # For now, this method will be a placeholder or require a more advanced
        # implementation based on how kicad-skip handles net connections.
        # A common approach is to add wires between graphical points and then
        # add net labels to define the net name.

        logger.warning(
            "Attempted to add connection between %s/%s and %s/%s. This requires advanced implementation.",
            source_ref,
            source_pin,
            target_ref,
            target_pin,
        )
        return False # Indicate not fully implemented yet

    @staticmethod
    def remove_connection(
        schematic: Schematic,
        source: Dict[str, Any],
        target: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Remove all wire segments forming a connection between two schematic points.

        Finds and removes all wire segments that form a connected path between the
        source and target points. Uses graph traversal (BFS) to identify all segments
        in the path, handling multi-segment connections with multiple bends correctly.

        Supports the same connection point types as connect_pins:
        - Pin to pin: Both source and target specify component pins
        - Pin to label: One specifies a pin, the other a label or power net name
        - Label to label: Both specify labels (hierarchical/global/local) or power net names

        Args:
            schematic: The schematic to modify
            source: Source connection point specification:
                    - Pin: { reference: "R1", pin: "1", unit?: 1 }
                    - Label: { label: "NET" } or { labelName: "NET" }
            target: Target connection point specification (same format as source)

        Returns:
            Dict with removal information (structured JSON):
            {
                "removed": {
                    "source": Endpoint,    # { kind: "pin", reference, pin, unit?, pinType? } or { kind: "label", label }
                    "target": Endpoint,    # same as source
                    "net": "Net 5",
                    "summary": "R1.1(passive) - R2.2(passive) [Net 5]"
                },
                "net": "Net 5",
                "netConnections": ["R1.1(passive)"]
            }

        Raises:
            ValueError: If connection points are invalid, no wires found at endpoints,
                       or no connected path exists between the points
            TypeError: If specifications are not dictionaries
        """

        # Resolve source and target connection points
        source_obj, source_pin, source_point, source_type = _resolve_connection_point(
            schematic,
            source,
            label='source',
        )
        target_obj, target_pin, target_point, target_type = _resolve_connection_point(
            schematic,
            target,
            label='target',
        )

        # Ensure wire wrappers reference the latest tree structure before analysis
        _refresh_wire_collection(schematic)

        if not hasattr(schematic, 'wire') or not len(schematic.wire):
            raise ValueError('Schematic contains no wires to remove')

        # Round points for spatial matching
        source_pt = _coord_key(*source_point)
        target_pt = _coord_key(*target_point)

        # Build a graph of wire connectivity
        # Each wire segment connects two points, so we build an adjacency list
        wire_graph = {}  # point -> list of (connected_point, wire)
        wire_points_map = {}  # wire -> list of points
        wire_raw_map = {}  # wire -> raw S-expression

        for wire in schematic.wire:
            try:
                if hasattr(wire, 'points'):
                    points = wire.points
                    if len(points) >= 2:
                        wire_points = []
                        for point in points:
                            if hasattr(point, 'value'):
                                coords = point.value
                                if len(coords) >= 2:
                                    x, y = _coord_key(coords[0], coords[1])
                                    wire_points.append((x, y))

                        if len(wire_points) >= 2:
                            wire_points_map[wire] = wire_points
                            wire_raw_map[wire] = wire.raw

                            # Add edges to the graph (wire segments connect their endpoints)
                            start_pt = wire_points[0]
                            end_pt = wire_points[-1]

                            if start_pt not in wire_graph:
                                wire_graph[start_pt] = []
                            if end_pt not in wire_graph:
                                wire_graph[end_pt] = []

                            wire_graph[start_pt].append((end_pt, wire))
                            wire_graph[end_pt].append((start_pt, wire))
            except Exception as e:
                logger.warning(f"Error analyzing wire for removal: {e}")
                continue

        # Find all wire segments that form a path from source to target using BFS
        if source_pt not in wire_graph:
            raise ValueError(f'No wires found at source point {source_pt}')
        if target_pt not in wire_graph:
            raise ValueError(f'No wires found at target point {target_pt}')

        # BFS to find path and collect all wires in the path
        from collections import deque

        queue = deque([(source_pt, [])]) # (current_point, wires_in_path)
        visited = {source_pt}
        wires_to_remove = None

        while queue:
            current_pt, path_wires = queue.popleft()

            if current_pt == target_pt:
                # Found the target! Collect all wires in this path
                wires_to_remove = path_wires
                break

            # Explore neighbors
            if current_pt in wire_graph:
                for next_pt, wire in wire_graph[current_pt]:
                    if next_pt not in visited:
                        visited.add(next_pt)
                        queue.append((next_pt, path_wires + [wire]))

        if wires_to_remove is None:
            raise ValueError(f'No connected path found between source and target points')

        # Collect wire points before removal for net analysis
        # Use the stored points instead of calling _extract_wire_points which accesses wrapper.raw
        all_wire_points_before = []
        for wire in wires_to_remove:
            all_wire_points_before.extend(wire_points_map[wire])

        # Build net info before removal
        net_info_before = _build_net_info(schematic, all_wire_points_before)

        # Remove the wires using the stored raw references
        # (raw references were collected earlier to avoid stale wrapper.raw access)
        removed_wrappers: List[WireWrapper] = []

        for wire in wires_to_remove:
            raw_wire = wire_raw_map[wire]
            if raw_wire in schematic.tree:
                schematic.tree.remove(raw_wire)
            removed_wrappers.append(wire)

        # Rebuild wire collection wrappers so future operations see consistent state
        _refresh_wire_collection(schematic)

        logger.info("Removed %d wire segment(s)", len(removed_wrappers))

        # Build net info after removal
        net_info_after = _build_net_info(schematic, all_wire_points_before)

        # Format source and target descriptions
        source_desc = ""
        target_desc = ""

        if source_type == "pin":
            if not isinstance(source_obj, Symbol):
                raise TypeError("Resolved source object is not a Symbol")
            symbol = source_obj
            reference = _reference_from_symbol(symbol)
            pin_id = (
                source.get('pin')
                or source.get('pinNumber')
                or source.get('number')
                or source.get('pinName')
                or source.get('name')
            )
            # Find the pin object to get its type
            pin_obj = None
            if hasattr(symbol, 'pin'):
                pin_id_normalised = str(pin_id).strip().lower()
                for pin in _iter_symbol_pins(symbol):
                    number, name = _ensure_pin_metadata(schematic, symbol, pin)
                    number_lc = number.strip().lower()
                    name_lc = name.strip().lower()
                    if pin_id_normalised in {number_lc, name_lc}:
                        pin_obj = pin
                        break

            if pin_obj is not None:
                pin_number = str(getattr(pin_obj, 'number', ''))
                pin_type = _get_pin_type(pin_obj)
                source_desc = _format_pin_with_type(reference, pin_number, pin_type)
            else:
                source_desc = f"{reference}.{pin_id}"
        elif source_type == "label":
            label_name = source.get('label') or source.get('labelName')
            source_desc = str(label_name)

        if target_type == "pin":
            if not isinstance(target_obj, Symbol):
                raise TypeError("Resolved target object is not a Symbol")
            symbol = target_obj
            reference = _reference_from_symbol(symbol)
            pin_id = (
                target.get('pin')
                or target.get('pinNumber')
                or target.get('number')
                or target.get('pinName')
                or target.get('name')
            )
            # Find the pin object to get its type
            pin_obj = None
            if hasattr(symbol, 'pin'):
                pin_id_normalised = str(pin_id).strip().lower()
                for pin in _iter_symbol_pins(symbol):
                    number, name = _ensure_pin_metadata(schematic, symbol, pin)
                    number_lc = number.strip().lower()
                    name_lc = name.strip().lower()
                    if pin_id_normalised in {number_lc, name_lc}:
                        pin_obj = pin
                        break

            if pin_obj is not None:
                pin_number = str(getattr(pin_obj, 'number', ''))
                pin_type = _get_pin_type(pin_obj)
                target_desc = _format_pin_with_type(reference, pin_number, pin_type)
            else:
                target_desc = f"{reference}.{pin_id}"
        elif target_type == "label":
            label_name = target.get('label') or target.get('labelName')
            target_desc = str(label_name)

        removed_str = f"{source_desc} - {target_desc} [{net_info_before['net']}]"

        # Build structured endpoint objects for removal summary
        def _endpoint_from_context(kind: str, spec: Dict[str, Any], symbol_obj: Any) -> Dict[str, Any]:
            if kind == "pin":
                ref = _reference_from_symbol(symbol_obj)
                pin_id = (
                    spec.get('pin')
                    or spec.get('pinNumber')
                    or spec.get('number')
                    or spec.get('pinName')
                    or spec.get('name')
                )
                # Try to locate actual pin to get pin number and type
                pin_number = str(pin_id)
                pin_type = None
                if hasattr(symbol_obj, 'pin'):
                    pid_norm = str(pin_id).strip().lower()
                    for p in _iter_symbol_pins(symbol_obj):
                        number, name = _ensure_pin_metadata(schematic, symbol_obj, p)
                        number_lc = number.strip().lower()
                        name_lc = name.strip().lower()
                        if pid_norm in {number_lc, name_lc}:
                            pin_number = number or str(pin_id)
                            pin_type = _get_pin_type(p)
                            break
                ep: Dict[str, Any] = {"kind": "pin", "reference": ref, "pin": str(pin_number)}
                if spec.get('unit') is not None:
                    ep['unit'] = _coerce_unit_value(spec.get('unit'))
                if pin_type:
                    ep['pinType'] = pin_type
                return ep
            else:
                label_name = spec.get('label') or spec.get('labelName')
                return {"kind": "label", "label": str(label_name)}

        removed_obj = {
            "source": _endpoint_from_context(source_type, source, source_obj),
            "target": _endpoint_from_context(target_type, target, target_obj),
            "net": net_info_before["net"],
            "summary": removed_str,
        }

        return {
            "removed": removed_obj,
            "net": net_info_after["net"],
            "netConnections": net_info_after["netConnections"]
        }

    @staticmethod
    def get_net_connections(schematic: Schematic, net_name: str):
        """Get all connections in a named net"""
        # kicad-skip represents nets implicitly through connected wires and net labels.
        # To get connections for a net, we would need to iterate through wires
        # and net labels to build a list of connected pins/points.
        # This requires traversing the schematic's graphical elements and understanding
        # how they form nets. This is an advanced implementation task.
        logger.warning("Attempted to get connections for net '%s'. This requires advanced implementation.", net_name)
        return [] # Return empty list for now

    @staticmethod
    def connect_pins(
        schematic: Schematic,
        source: Dict[str, Any],
        target: Dict[str, Any],
        *,
        wire: Optional[Dict[str, Any]] = None,
        routing: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Connect two schematic connection points by drawing an appropriate wire.

        Automatically routes wires using Manhattan (horizontal/vertical) pathfinding with
        intelligent obstacle avoidance. The routing algorithm:
        - Preserves the exact source/target coordinates while operating in real millimetres
        - Expands in Manhattan steps (1.27 mm stride) and penalises paths that approach symbol bodies
        - Adds clearance stubs so wires leave pins cleanly before turning
        - Minimises corner count first, then total length, while maintaining required clearances
        - Ensures wires never hug symbol edges or pass through component bodies

        Supports connecting:
        - Pin to pin: Both source and target specify component pins
        - Pin to label/power: One specifies a pin, the other a label or power net name
        - Label to label: Both specify labels (hierarchical/global/local) or power net names

        Args:
            schematic: The schematic to modify
            source: Source connection point specification:
                    - Pin: { reference: "R1", pin: "1", unit?: 1 }
                    - Label/Power: { label: "NET" } or { labelName: "NET" } where NET
                      can be a hierarchical label, a global label, a local label, or the
                      Value of a power symbol (e.g., "GND", "VCC", "+5V").
            target: Target connection point specification (same format as source)
            wire: Optional wire styling properties (width, strokeType, etc.)

        Returns:
            Dict with connection information (structured JSON):
            {
                "created": {
                    "source": Endpoint,    # { kind: "pin", reference, pin, unit?, pinType? } or { kind: "label", label }
                    "target": Endpoint,    # same as source
                    "net": "Net 7",       # resolved net name/id
                    "summary": "R1.1(passive) - R2.2(input) [Net 7]"  # human-friendly
                },
                "net": "Net 7",
                "netConnections": ["R1.1(passive)", "R2.2(input)"]
            }

        Raises:
            ValueError: If connection points are invalid, identical, or no valid route exists
            TypeError: If specifications are not dictionaries
        """

        # Resolve source and target connection points
        source_obj, source_pin, source_point, source_type = _resolve_connection_point(
            schematic,
            source,
            label='source',
        )
        target_obj, target_pin, target_point, target_type = _resolve_connection_point(
            schematic,
            target,
            label='target',
        )

        # Validate that we're not connecting something to itself
        if source_type == target_type:
            if source_type == "pin" and source_obj == target_obj:
                raise ValueError('Cannot connect a pin to itself')
            elif source_type == "label" and source_point == target_point:
                raise ValueError('Cannot connect a label to itself')

        properties = dict(wire or {})
        _routing_options = dict(routing or {})

        has_manual_points = any(
            key in properties and properties[key] is not None
            for key in ('points', 'pointList', 'segments')
        )

        if not has_manual_points:
            # Route directly from pin to pin
            # The A* algorithm uses soft penalties for symbol bboxes:
            # - Heavy penalty for routing inside bboxes
            # - Lighter penalty for routing near bboxes
            # - Hard blocks only on forbidden pin/node points
            # This allows routing even when pins are inside bboxes (e.g., LED cathode)
            route_start = (source_point[0], source_point[1])
            route_end = (target_point[0], target_point[1])

            obstacles = _build_routing_obstacles(schematic)
            route_points = safe_manhattan_route(
                route_start,
                route_end,
                obstacles=obstacles,
                grid_step=KICAD_SCHEMATIC_GRID_MM,
            )

            # Use the route points directly as the wire path, but ensure true endpoints are included
            adjusted_route: List[Tuple[float, float]] = []
            for idx, pt in enumerate(route_points):
                if idx == 0:
                    candidate = (float(source_point[0]), float(source_point[1]))
                else:
                    candidate = (float(pt[0]), float(pt[1]))
                if adjusted_route and adjusted_route[-1] == candidate:
                    continue
                adjusted_route.append(candidate)

            if not adjusted_route:
                adjusted_route = [(float(source_point[0]), float(source_point[1]))]

            final_point = (float(target_point[0]), float(target_point[1]))
            if adjusted_route[-1] != final_point:
                last = adjusted_route[-1]
                if last[0] != final_point[0] and last[1] != final_point[1]:
                    intermediate = (final_point[0], last[1])
                    if intermediate != last:
                        adjusted_route.append(intermediate)
                if adjusted_route[-1] != final_point:
                    adjusted_route.append(final_point)

            if len(adjusted_route) < 2:
                adjusted_route.append(final_point)

            properties['points'] = [[p[0], p[1]] for p in adjusted_route]
            _validate_segment_lengths(properties['points'], min_length=MIN_ROUTED_SEGMENT_MM, context='connect_pins routing')

        start = [source_point[0], source_point[1]]
        end = [target_point[0], target_point[1]]

        added = ConnectionManager.add_wire(
            schematic,
            start,
            end,
            properties=properties,
        )

        logger.info(
            "Connected %s to %s (%s to %s)",
            source.get('reference') or source.get('label') or source.get('labelName'),
            target.get('reference') or target.get('label') or target.get('labelName'),
            source_type,
            target_type,
        )

        # Build the connection description and structured endpoints
        wires = added if isinstance(added, list) else [added]

        # Collect all wire points for net analysis
        all_wire_points = []
        for wire_wrapper in wires:
            raw_points = _extract_wire_points(wire_wrapper)
            all_wire_points.extend(raw_points)

        # Build net information
        net_info = _build_net_info(schematic, all_wire_points)

        # Build structured endpoints and human summary
        def _pin_id_from_spec(spec: Dict[str, Any]) -> Any:
            return (
                spec.get('pin')
                or spec.get('pinNumber')
                or spec.get('number')
                or spec.get('pinName')
                or spec.get('name')
            )

        created_source: Dict[str, Any]
        created_target: Dict[str, Any]
        source_desc = ""
        target_desc = ""

        if source_type == "pin":
            if not isinstance(source_obj, Symbol):
                raise TypeError("Resolved source object is not a Symbol")
            symbol = source_obj
            ref = _reference_from_symbol(symbol)
            pin_id = _pin_id_from_spec(source)
            pin_obj = None
            if hasattr(symbol, 'pin'):
                pid_norm = str(pin_id).strip().lower()
                for p in _iter_symbol_pins(symbol):
                    number, name = _ensure_pin_metadata(schematic, symbol, p)
                    number_lc = number.strip().lower()
                    name_lc = name.strip().lower()
                    if pid_norm in {number_lc, name_lc}:
                        pin_obj = p
                        break
            pin_number = str(getattr(pin_obj, 'number', pin_id)) if pin_obj is not None else str(pin_id)
            pin_type = _get_pin_type(pin_obj) if pin_obj is not None else None
            created_source = {"kind": "pin", "reference": ref, "pin": str(pin_number)}
            if source.get('unit') is not None:
                created_source['unit'] = _coerce_unit_value(source.get('unit'))
            if pin_type:
                created_source['pinType'] = pin_type
            source_desc = _format_pin_with_type(ref, str(pin_number), pin_type or 'passive')
        else:
            label_name = source.get('label') or source.get('labelName')
            created_source = {"kind": "label", "label": str(label_name)}
            source_desc = str(label_name)

        if target_type == "pin":
            if not isinstance(target_obj, Symbol):
                raise TypeError("Resolved target object is not a Symbol")
            symbol = target_obj
            ref = _reference_from_symbol(symbol)
            pin_id = _pin_id_from_spec(target)
            pin_obj = None
            if hasattr(symbol, 'pin'):
                pid_norm = str(pin_id).strip().lower()
                for p in _iter_symbol_pins(symbol):
                    number, name = _ensure_pin_metadata(schematic, symbol, p)
                    number_lc = number.strip().lower()
                    name_lc = name.strip().lower()
                    if pid_norm in {number_lc, name_lc}:
                        pin_obj = p
                        break
            pin_number = str(getattr(pin_obj, 'number', pin_id)) if pin_obj is not None else str(pin_id)
            pin_type = _get_pin_type(pin_obj) if pin_obj is not None else None
            created_target = {"kind": "pin", "reference": ref, "pin": str(pin_number)}
            if target.get('unit') is not None:
                created_target['unit'] = _coerce_unit_value(target.get('unit'))
            if pin_type:
                created_target['pinType'] = pin_type
            target_desc = _format_pin_with_type(ref, str(pin_number), pin_type or 'passive')
        else:
            label_name = target.get('label') or target.get('labelName')
            created_target = {"kind": "label", "label": str(label_name)}
            target_desc = str(label_name)

        created_str = f"{source_desc} - {target_desc} [{net_info['net']}]"

        return {
            "created": {
                "source": created_source,
                "target": created_target,
                "net": net_info["net"],
                "summary": created_str,
            },
            "net": net_info["net"],
            "netConnections": net_info["netConnections"],
        }

if __name__ == '__main__':
    # Example Usage (for testing)
    from schematic import SchematicManager # Assuming schematic.py is in the same directory

    # Create a new schematic
    test_sch = SchematicManager.create_schematic("ConnectionTestSchematic")

    # Add some wires
    wire1 = ConnectionManager.add_wire(test_sch, [100, 100], [200, 100])
    wire2 = ConnectionManager.add_wire(test_sch, [200, 100], [200, 200])

    # Note: add_connection, remove_connection, get_net_connections are placeholders
    # and require more complex implementation based on kicad-skip's structure.

    # Example of how you might add a net label (requires finding a point on a wire)
    # from skip import Label
    # if wire1:
    #     net_label_pos = wire1.start # Or calculate a point on the wire
    #     net_label = test_sch.add_label(text="Net_01", at=net_label_pos)
    #     print(f"Added net label 'Net_01' at {net_label_pos}")

    # Save the schematic (optional)
    # SchematicManager.save_schematic(test_sch, "connection_test.kicad_sch")

    # Clean up (if saved)
    # if os.path.exists("connection_test.kicad_sch"):
    #     os.remove("connection_test.kicad_sch")
    #     print("Cleaned up connection_test.kicad_sch")

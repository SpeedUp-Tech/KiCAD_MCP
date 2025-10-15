from __future__ import annotations

import logging
import uuid
import math
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple, Union
import heapq


from skip import Schematic
from sexpdata import Symbol as SSymbol

from skip.eeschema.wire import WireWrapper
from skip.sexp.parser import ParsedValue
from skip.eeschema.schematic.symbol import Symbol

from .grid_utils import snap_to_grid, snap_point_to_grid, snap_points_to_grid

logger = logging.getLogger('kicad_interface')


_DEFAULT_WIRE_WIDTH = 0.254
_VALID_STROKE_TYPES = {'default', 'dash', 'dot'}


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
        # Snap to grid before writing to file
        x_snapped = snap_to_grid(x_val)
        y_snapped = snap_to_grid(y_val)
        pts_expr.append([SSymbol('xy'), round(x_snapped, 6), round(y_snapped, 6)])

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


def _get_pin_type(pin: Any) -> str:
    """Extract the electrical type of a pin."""
    if hasattr(pin, 'electrical_type'):
        return str(pin.electrical_type)
    # Try to get from raw S-expression
    if hasattr(pin, 'raw') and isinstance(pin.raw, list):
        for item in pin.raw:
            if isinstance(item, str) and item in ['input', 'output', 'bidirectional', 'tri_state', 'passive', 'free', 'unspecified', 'power_in', 'power_out', 'open_collector', 'open_emitter', 'no_connect']:
                return item
    return 'passive'


def _format_pin_with_type(reference: str, pin_number: str, pin_type: str) -> str:
    """Format a pin reference with its electrical type."""
    return f"{reference}.{pin_number}({pin_type})"


def _build_net_info(schematic: Schematic, wire_points: List[Tuple[float, float]]) -> Dict[str, Any]:
    """
    Build net information by analyzing what's connected at the given wire points.

    Returns a dict with:
    - net: Net identifier (e.g., "Net-5", "GND", "VBAT")
    - netConnections: List of connected pins/labels/power (e.g., ["R1.1(passive)", "R2.2(output)", "GND"])
    """
    from collections import defaultdict

    # Round wire points for spatial matching
    wire_point_set = {(round(x, 1), round(y, 1)) for x, y in wire_points}

    # Build spatial index of all pins
    pin_locations: Dict[Tuple[float, float], List[Tuple[str, str, str]]] = defaultdict(list)  # (x,y) -> [(ref, pin_num, pin_type)]

    if hasattr(schematic, 'symbol'):
        for symbol in schematic.symbol:
            try:
                reference = getattr(getattr(symbol.property, 'Reference', None), 'value', None)
                if not reference:
                    continue

                # Skip power symbols - they're handled separately
                lib_id = getattr(getattr(symbol, 'lib_id', None), 'value', '')
                if lib_id and 'power' in lib_id.lower():
                    continue

                if hasattr(symbol, 'pin'):
                    for pin in symbol.pin:
                        try:
                            pin_number = str(getattr(pin, 'number', ''))
                            if not pin_number:
                                continue

                            pin_type = _get_pin_type(pin)

                            if hasattr(pin, 'location'):
                                loc = pin.location
                                x = round(float(loc.x), 1)
                                y = round(float(loc.y), 1)
                                pin_locations[(x, y)].append((reference, pin_number, pin_type))
                        except Exception as e:
                            logger.warning(f"Error extracting pin: {e}")
                            continue
            except Exception as e:
                logger.warning(f"Error processing symbol: {e}")
                continue

    # Build spatial index of all wire points and group into nets
    all_wire_points: Dict[Tuple[float, float], List[int]] = defaultdict(list)

    if hasattr(schematic, 'wire'):
        for wire_idx, wire in enumerate(schematic.wire):
            try:
                if hasattr(wire, 'points'):
                    points = wire.points
                    if len(points) >= 2:
                        for point in points:
                            if hasattr(point, 'value'):
                                coords = point.value
                                if len(coords) >= 2:
                                    x = round(float(coords[0]), 1)
                                    y = round(float(coords[1]), 1)
                                    all_wire_points[(x, y)].append(wire_idx)
            except Exception as e:
                logger.warning(f"Error analyzing wire: {e}")
                continue

    # Build wire-to-net mapping
    wire_to_net: Dict[int, int] = {}
    net_counter = 0

    for point, wire_indices in all_wire_points.items():
        if len(wire_indices) > 1:
            existing_nets = set()
            for wire_idx in wire_indices:
                if wire_idx in wire_to_net:
                    existing_nets.add(wire_to_net[wire_idx])

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

    # Map points to nets
    point_to_net: Dict[Tuple[float, float], int] = {}
    for point, wire_indices in all_wire_points.items():
        if wire_indices:
            point_to_net[point] = wire_to_net.get(wire_indices[0], wire_indices[0])

    # Find which net our wire points belong to
    net_id = None
    for point in wire_point_set:
        if point in point_to_net:
            net_id = point_to_net[point]
            break

    if net_id is None:
        # New net
        net_id = net_counter

    # Collect all pins on this net
    connected_pins = []
    for point, pins in pin_locations.items():
        if point in point_to_net and point_to_net[point] == net_id:
            for reference, pin_number, pin_type in pins:
                connected_pins.append(_format_pin_with_type(reference, pin_number, pin_type))

    # Check for labels and power symbols on this net
    connected_labels = []
    connected_power = []

    # Determine net name
    if connected_power:
        net_name = connected_power[0]
    elif connected_labels:
        net_name = connected_labels[0]
    else:
        net_name = f"Net-{net_id}"

    return {
        "net": net_name,
        "netConnections": connected_pins + connected_labels + connected_power
    }


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

    target_pin = None
    pin_id_normalised = str(pin_id).strip().lower()

    for pin in symbol.pin:
        number = str(getattr(pin, 'number', '')).strip().lower()
        name = str(getattr(pin, 'name', '')).strip().lower()
        if pin_id_normalised in {number, name}:
            target_pin = pin
            break

    if target_pin is None:
        raise ValueError(
            f"Pin '{pin_id}' not found on component '{reference}'"
        )

    loc = target_pin.location
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
) -> Tuple[Optional[Any], Tuple[float, float], str]:
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
        Tuple of (symbol_or_none, (x, y), connection_type)
        - For pins: (Symbol, (x, y), "pin")
        - For labels/power: (None, (x, y), "label")

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
        return (symbol, point, "pin")

    # Resolve as hierarchical label
    label_name = spec.get('label') or spec.get('labelName')
    _, point = _find_hierarchical_label(schematic, label_name)
    return (None, point, "label")


def _build_manhattan_path(
    start: Tuple[float, float],
    end: Tuple[float, float],
    pattern: str = 'hv',
) -> List[Tuple[float, float]]:
    sx, sy = start
    ex, ey = end

    if sx == ex or sy == ey:
        if (sx, sy) == (ex, ey):
            raise ValueError('Pins share the same coordinates; cannot connect')
        return [start, end]

    pattern = pattern.lower()
    if pattern not in {'hv', 'vh'}:
        pattern = 'hv'

    if pattern == 'hv':
        corner = (ex, sy)
    else:
        corner = (sx, ey)

    if corner == start or corner == end:
        return [start, end]

    return [start, corner, end]

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
                                x = round(float(pin.location.x), 1)
                                y = round(float(pin.location.y), 1)
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
                                x = round(float(pt.value[0]), 1)
                                y = round(float(pt.value[1]), 1)
                                pts.append((x, y))
                            except Exception:
                                continue
    except Exception:
        pass
    return pts
def _is_entry(node: Any, name: str) -> bool:
    try:
        return isinstance(node, list) and node and hasattr(node[0], 'value') and node[0].value() == name
    except Exception:
        return False


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


def _extract_poly_points_from_lib_symbol(symbol_node: List[Any]) -> List[Tuple[float, float]]:
    """Collect all polyline points (xy ...) under the given library symbol node (library space)."""
    pts: List[Tuple[float, float]] = []

    def visit(n: Any) -> None:
        if not isinstance(n, list):
            return
        if _is_entry(n, 'polyline'):
            # find (pts ...)
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
        return (round(min(xs), 1), round(min(ys), 1), round(max(xs), 1), round(max(ys), 1))
    except Exception:
        return None



def _collect_symbol_bboxes(schematic: Schematic) -> List[Tuple[Tuple[float, float, float, float], Symbol]]:
    """Compute each symbol body's axis-aligned bbox from library geometry and expand by a fixed clearance.

    Fallback to pin-extents only if library geometry is unavailable. Power symbols are skipped.
    """
    boxes: List[Tuple[Tuple[float, float, float, float], Symbol]] = []
    clearance = KICAD_SCHEMATIC_GRID_MM  # fixed, non-configurable visual clearance
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
                        for pin in sym.pin:
                            if hasattr(pin, 'location') and pin.location is not None:
                                try:
                                    pts.append((float(pin.location.x), float(pin.location.y)))
                                except Exception:
                                    continue
                    if not pts:
                        continue
                    xs = [p[0] for p in pts]
                    ys = [p[1] for p in pts]
                    rect = (min(xs), min(ys), max(xs), max(ys))
                # Expand by fixed clearance
                xmin, ymin, xmax, ymax = rect
                xmin = round(xmin - clearance, 1)
                ymin = round(ymin - clearance, 1)
                xmax = round(xmax + clearance, 1)
                ymax = round(ymax + clearance, 1)
                boxes.append(((xmin, ymin, xmax, ymax), sym))
    except Exception:
        pass
    return boxes


def _point_on_axis_segment_interior(a: Tuple[float, float], b: Tuple[float, float], p: Tuple[float, float]) -> bool:
    ax, ay = a
    bx, by = b
    px, py = p
    if ax == bx and ay == by:
        return False
    # horizontal
    if ay == by and py == ay:
        lo, hi = (ax, bx) if ax <= bx else (bx, ax)
        return lo < px < hi
    # vertical
    if ax == bx and px == ax:
        lo, hi = (ay, by) if ay <= by else (by, ay)
        return lo < py < hi
    return False


def _segment_intersects_rect(a: Tuple[float, float], b: Tuple[float, float], rect: Tuple[float, float, float, float]) -> bool:
    x1, y1 = a
    x2, y2 = b
    xmin, ymin, xmax, ymax = rect
    # horizontal segment: treat touching rectangle boundary as intersection
    if y1 == y2:
        y = y1
        if not (ymin <= y <= ymax):
            return False
        lo, hi = (x1, x2) if x1 <= x2 else (x2, x1)
        return not (hi <= xmin or lo >= xmax)
    # vertical segment: treat touching rectangle boundary as intersection
    if x1 == x2:
        x = x1
        if not (xmin <= x <= xmax):
            return False
        lo, hi = (y1, y2) if y1 <= y2 else (y2, y1)
        return not (hi <= ymin or lo >= ymax)
    return False

def _astar_grid_route(
    schematic: Schematic,
    start: Tuple[float, float],
    end: Tuple[float, float],
    step: float,
    bboxes: List[Tuple[Tuple[float, float, float, float], Symbol]],
    forbidden_points: Iterable[Tuple[float, float]],
    max_expansions: int = 12000,
    bend_penalty: float = 0.2,
) -> Optional[List[Tuple[float, float]]]:
    """Bounded Manhattan A* on grid with rectangle obstacles and forbidden grid points.

    - Obstacles are the interiors of symbol body rectangles.
    - Start/end are always allowed, even if inside a body (to allow exiting/entering at pins).
    - Returns a list of points including start and end, or None if not found within limits.
    """
    sx, sy = start
    ex, ey = end

    # Convert to integer grid coordinates
    def to_grid(pt: Tuple[float, float]) -> Tuple[int, int]:
        return (int(round(pt[0] / step)), int(round(pt[1] / step)))

    def from_grid(g: Tuple[int, int]) -> Tuple[float, float]:
        return (round(g[0] * step, 2), round(g[1] * step, 2))

    gs = to_grid((sx, sy))
    ge = to_grid((ex, ey))

    # Search window bounds
    span_x = abs(gs[0] - ge[0])
    span_y = abs(gs[1] - ge[1])
    margin = max(10, max(span_x, span_y) + 6)  # fixed extra search band in grid units
    xmin = min(gs[0], ge[0]) - margin
    xmax = max(gs[0], ge[0]) + margin
    ymin = min(gs[1], ge[1]) - margin
    ymax = max(gs[1], ge[1]) + margin

    # Prepare obstacles in grid space
    rects: List[Tuple[float, float, float, float]] = [r for (r, _sym) in bboxes]

    def is_inside_rect_interior(x_mm: float, y_mm: float, rect: Tuple[float, float, float, float]) -> bool:
        xmin_r, ymin_r, xmax_r, ymax_r = rect
        return (xmin_r < x_mm < xmax_r) and (ymin_r < y_mm < ymax_r)

    forb_set = {to_grid(p) for p in set(forbidden_points) if p != start and p != end}

    def blocked(gx: int, gy: int) -> bool:
        if gx < xmin or gx > xmax or gy < ymin or gy > ymax:
            return True
        if (gx, gy) in forb_set and (gx, gy) not in {gs, ge}:
            return True
        x_mm = gx * step
        y_mm = gy * step
        # Allow start/end even if inside a rect interior
        if (gx, gy) in {gs, ge}:
            return False
        for rect in rects:
            if is_inside_rect_interior(x_mm, y_mm, rect):
                return True
        return False

    # A* search
    DIRS = [(1, 0), (-1, 0), (0, 1), (0, -1)]

    def heuristic(x: int, y: int) -> float:
        return abs(x - ge[0]) + abs(y - ge[1])

    start_state = (gs[0], gs[1], -1)  # (x, y, dir_idx)
    open_heap: List[Tuple[float, float, Tuple[int, int, int]]] = []
    heapq.heappush(open_heap, (heuristic(gs[0], gs[1]), 0.0, start_state))
    came_from: Dict[Tuple[int, int, int], Tuple[int, int, int]] = {}
    gscore: Dict[Tuple[int, int, int], float] = {start_state: 0.0}

    expansions = 0

    def state_key(x: int, y: int, d: int) -> Tuple[int, int, int]:
        return (x, y, d)

    while open_heap and expansions < max_expansions:
        f, g, (x, y, dprev) = heapq.heappop(open_heap)
        expansions += 1
        if (x, y) == ge:
            # Reconstruct path
            path: List[Tuple[int, int]] = [(x, y)]
            state = (x, y, dprev)
            while state in came_from:
                state = came_from[state]
                path.append((state[0], state[1]))
            path.reverse()
            # Convert to mm and simplify collinear
            pts = [from_grid(pt) for pt in path]
            simplified: List[Tuple[float, float]] = []
            for p in pts:
                if not simplified:
                    simplified.append(p)
                else:
                    simplified.append(p)
                    # drop middle if collinear
                    if len(simplified) >= 3:
                        a, b, c = simplified[-3], simplified[-2], simplified[-1]
                        if (a[0] == b[0] == c[0]) or (a[1] == b[1] == c[1]):
                            simplified.pop(-2)
            return simplified

        for i, (dx, dy) in enumerate(DIRS):
            nx, ny = x + dx, y + dy
            if blocked(nx, ny):
                continue
            # Cost: unit step + bend penalty for direction change
            cost = 1.0
            if dprev != -1 and dprev != i:
                cost += bend_penalty
            ng = g + cost
            ns = state_key(nx, ny, i)
            if ng < gscore.get(ns, float('inf')):
                gscore[ns] = ng
                came_from[ns] = (x, y, dprev)
                nf = ng + heuristic(nx, ny)
                heapq.heappush(open_heap, (nf, ng, ns))

    return None


def _safe_manhattan_route(
    schematic: Schematic,
    start: Tuple[float, float],
    end: Tuple[float, float],
    pattern_hint: str = 'hv',
    exclude_symbols: Optional[Iterable[Symbol]] = None,
) -> List[Tuple[float, float]]:
    # Work with grid-snapped coordinates throughout
    s = tuple(snap_point_to_grid(start[0], start[1]))
    e = tuple(snap_point_to_grid(end[0], end[1]))

    if s == e:
        raise ValueError('Pins share the same coordinates; cannot connect')

    pin_coords = set(_collect_pin_coords(schematic))
    node_coords = set(_collect_wire_vertices(schematic))
    bboxes = _collect_symbol_bboxes(schematic)
    excluded_set = set(exclude_symbols or [])

    # Allow using the endpoints themselves
    allowed_endpoints = { (round(s[0], 1), round(s[1], 1)), (round(e[0], 1), round(e[1], 1)) }

    forbidden_vertices = (pin_coords | node_coords) - allowed_endpoints

    def ok_route(points: List[Tuple[float, float]]) -> bool:
        # Snap all points to grid the same way add_wire will
        pts = snap_points_to_grid(points)
        pts_r = [(round(x, 1), round(y, 1)) for (x, y) in pts]
        # 1) internal vertices cannot coincide with forbidden nodes
        for v in pts_r[1:-1]:
            if v in forbidden_vertices:
                return False
        # 2) no segment may pass through another pin coordinate in its interior
        for a, b in zip(pts_r, pts_r[1:]):
            for pc in pin_coords - allowed_endpoints:
                if _point_on_axis_segment_interior(a, b, pc):
                    return False
        # 3) no segment may cross a symbol body bbox (approx) except for endpoint symbols
        for a, b in zip(pts_r, pts_r[1:]):
            for (rect, sym) in bboxes:
                if sym in excluded_set:
                    continue
                if _segment_intersects_rect(a, b, rect):
                    return False
        return True

    # 0) Straight segment
    if s[0] == e[0] or s[1] == e[1]:
        candidate = [s, e]
        if ok_route(candidate):
            return snap_points_to_grid(candidate)

    # 1) Try hinted pattern then alternate
    ordered = []
    ph = (pattern_hint or 'hv').lower()
    ordered.append(ph if ph in ('hv', 'vh') else 'hv')
    ordered.append('vh' if ordered[0] == 'hv' else 'hv')

    for pat in ordered:
        if pat == 'hv':
            corner = (e[0], s[1])
        else:
            corner = (s[0], e[1])
        candidate = [s, corner, e]
        if ok_route(candidate):
            return snap_points_to_grid(candidate)

    # 2) Corridor-based detour (choose a clear y for horizontal or clear x for vertical)
    step = KICAD_SCHEMATIC_GRID_MM
    x_lo, x_hi = (s[0], e[0]) if s[0] <= e[0] else (e[0], s[0])
    y_lo, y_hi = (s[1], e[1]) if s[1] <= e[1] else (e[1], s[1])

    def merged_bands_horiz():
        bands = []
        for (xmin, ymin, xmax, ymax), sym in bboxes:
            if sym in excluded_set:
                continue
            if xmax <= x_lo or xmin >= x_hi:
                continue
            bands.append((ymin, ymax))
        if not bands:
            return []
        bands.sort()
        merged = [bands[0]]
        for a, b in bands[1:]:
            ly0, ly1 = merged[-1]
            if a <= ly1:
                merged[-1] = (ly0, max(ly1, b))
            else:
                merged.append((a, b))
        return merged

    def merged_bands_vert():
        bands = []
        for (xmin, ymin, xmax, ymax), sym in bboxes:
            if sym in excluded_set:
                continue
            if ymax <= y_lo or ymin >= y_hi:
                continue
            bands.append((xmin, xmax))
        if not bands:
            return []
        bands.sort()
        merged = [bands[0]]
        for a, b in bands[1:]:
            lx0, lx1 = merged[-1]
            if a <= lx1:
                merged[-1] = (lx0, max(lx1, b))
            else:
                merged.append((a, b))
        return merged

    # Try horizontal corridor (choose y above/below merged bands)
    bands_y = merged_bands_horiz()
    if bands_y:
        top = max(b[1] for b in bands_y)
        bot = min(b[0] for b in bands_y)
        y_up = round(top + step, 2)
        y_dn = round(bot - step, 2)
        for yc in (y_up, y_dn):
            candidate = [s, (s[0], yc), (e[0], yc), e]
            if ok_route(candidate):
                return snap_points_to_grid(candidate)

    # Try vertical corridor (choose x left/right of merged bands)
    bands_x = merged_bands_vert()
    if bands_x:
        right = max(b[1] for b in bands_x)
        left = min(b[0] for b in bands_x)
        x_rt = round(right + step, 2)
        x_lt = round(left - step, 2)
        for xc in (x_rt, x_lt):
            candidate = [s, (xc, s[1]), (xc, e[1]), e]
            if ok_route(candidate):
                return snap_points_to_grid(candidate)

    # 3) Bounded A* fallback for multi-bend paths
    astar_route = _astar_grid_route(
        schematic,
        s,
        e,
        step,
        [(r, sym) for (r, sym) in bboxes if sym not in excluded_set],
        forbidden_vertices,
    )
    if astar_route is not None and ok_route(astar_route):
        return snap_points_to_grid(astar_route)

    # 4) Fallback: simple one-step escape stubs from start and from end

    escape_vectors = [ (0, step), (0, -step), (step, 0), (-step, 0) ]

    def try_escape(from_start: bool, pat: str) -> Optional[List[Tuple[float, float]]]:
        for dx, dy in escape_vectors:
            esc = ( (s[0] + dx, s[1] + dy) if from_start else (e[0] + dx, e[1] + dy) )
            esc = tuple(snap_point_to_grid(esc[0], esc[1]))
            # Build 3-segment route using the escape point
            if pat == 'hv':
                if from_start:
                    points = [s, esc, (e[0], esc[1]), e]
                else:
                    points = [s, (s[0], esc[1]), esc, e]
            else:  # 'vh'
                if from_start:
                    points = [s, esc, (esc[0], e[1]), e]
                else:
                    points = [s, (esc[0], s[1]), esc, e]
            if ok_route(points):
                return snap_points_to_grid(points)
        return None

    for pat in ordered:
        r = try_escape(True, pat) or try_escape(False, pat)
        if r is not None:
            return r

    # If all attempts failed, raise with guidance
    raise ValueError(
        'Routing failed: could not find a Manhattan path that avoids pin endpoints/nodes and symbol bodies. '
        'Try repositioning or rotating one of the components to provide clearance.'
    )



class ConnectionManager:
    """Manage connections between components"""

    @staticmethod
    def add_wire(
        schematic: Schematic,
        start_point: Optional[Any],
        end_point: Optional[Any],
        properties: Optional[dict] = None,
    ) -> WireWrapper:
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
        Remove wire(s) connecting two schematic connection points.

        Args:
            schematic: The schematic to modify
            source: Source connection point specification:
                    - Pin: { reference: "R1", pin: "1", unit?: 1 }
                    - Label: { label: "NET" }
            target: Target connection point specification (same format as source)

        Returns:
            Dict with removal information (structured JSON):
            {
                "removed": {
                    "source": Endpoint,    # { kind: "pin", reference, pin, unit?, pinType? } or { kind: "label", label }
                    "target": Endpoint,    # same as source
                    "net": "Net-5",
                    "summary": "R1.1(passive) - R2.2(passive) [Net-5]"
                },
                "net": "Net-5",
                "netConnections": ["R1.1(passive)"]
            }

        Raises:
            ValueError: If connection points are invalid or no wires found
            TypeError: If specifications are not dictionaries
        """

        # Resolve source and target connection points
        source_obj, source_point, source_type = _resolve_connection_point(
            schematic,
            source,
            label='source',
        )
        target_obj, target_point, target_type = _resolve_connection_point(
            schematic,
            target,
            label='target',
        )

        if not hasattr(schematic, 'wire') or not len(schematic.wire):
            raise ValueError('Schematic contains no wires to remove')

        # Round points for spatial matching
        source_pt = (round(source_point[0], 1), round(source_point[1], 1))
        target_pt = (round(target_point[0], 1), round(target_point[1], 1))

        # Find all wires that connect these two points
        # Store wire points AND raw references to avoid accessing stale wrapper.raw later
        wires_to_remove = []
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
                                    x = round(float(coords[0]), 1)
                                    y = round(float(coords[1]), 1)
                                    wire_points.append((x, y))

                        # Check if this wire connects source and target
                        if source_pt in wire_points and target_pt in wire_points:
                            wires_to_remove.append(wire)
                            wire_points_map[wire] = wire_points
                            wire_raw_map[wire] = wire.raw  # Store raw reference NOW before any modifications
                        # Also check if wire endpoints match
                        elif len(wire_points) >= 2:
                            if (wire_points[0] == source_pt and wire_points[-1] == target_pt) or \
                               (wire_points[0] == target_pt and wire_points[-1] == source_pt):
                                wires_to_remove.append(wire)
                                wire_points_map[wire] = wire_points
                                wire_raw_map[wire] = wire.raw  # Store raw reference NOW before any modifications
            except Exception as e:
                logger.warning(f"Error analyzing wire for removal: {e}")
                continue

        if not wires_to_remove:
            raise ValueError(f'No wires found connecting the specified points')

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

        schematic.wire._elements = [
            wire for wire in schematic.wire._elements if wire not in removed_wrappers
        ]

        logger.info("Removed %d wire segment(s)", len(removed_wrappers))

        # Build net info after removal
        net_info_after = _build_net_info(schematic, all_wire_points_before)

        # Format source and target descriptions
        source_desc = ""
        target_desc = ""

        if source_type == "pin":
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
                for pin in symbol.pin:
                    number = str(getattr(pin, 'number', '')).strip().lower()
                    name = str(getattr(pin, 'name', '')).strip().lower()
                    if pin_id_normalised in {number, name}:
                        pin_obj = pin
                        break

            if pin_obj:
                pin_number = str(getattr(pin_obj, 'number', ''))
                pin_type = _get_pin_type(pin_obj)
                source_desc = _format_pin_with_type(reference, pin_number, pin_type)
            else:
                source_desc = f"{reference}.{pin_id}"
        elif source_type == "label":
            label_name = source.get('label') or source.get('labelName')
            source_desc = str(label_name)

        if target_type == "pin":
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
                for pin in symbol.pin:
                    number = str(getattr(pin, 'number', '')).strip().lower()
                    name = str(getattr(pin, 'name', '')).strip().lower()
                    if pin_id_normalised in {number, name}:
                        pin_obj = pin
                        break

            if pin_obj:
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
                    for p in symbol_obj.pin:
                        number = str(getattr(p, 'number', '')).strip().lower()
                        name = str(getattr(p, 'name', '')).strip().lower()
                        if pid_norm in {number, name}:
                            pin_number = str(getattr(p, 'number', pin_id))
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
            routing: Optional routing hints (pattern: 'hv' or 'vh')

        Returns:
            Dict with connection information (structured JSON):
            {
                "created": {
                    "source": Endpoint,    # { kind: "pin", reference, pin, unit?, pinType? } or { kind: "label", label }
                    "target": Endpoint,    # same as source
                    "net": "Net-7",       # resolved net name/id
                    "summary": "R1.1(passive) - R2.2(input) [Net-7]"  # human-friendly
                },
                "net": "Net-7",
                "netConnections": ["R1.1(passive)", "R2.2(input)"]
            }

        Raises:
            ValueError: If connection points are invalid or identical
            TypeError: If specifications are not dictionaries
        """

        # Resolve source and target connection points
        source_obj, source_point, source_type = _resolve_connection_point(
            schematic,
            source,
            label='source',
        )
        target_obj, target_point, target_type = _resolve_connection_point(
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

        has_manual_points = any(
            key in properties and properties[key] is not None
            for key in ('points', 'pointList', 'segments')
        )

        if not has_manual_points:
            pattern = 'hv'
            if routing and isinstance(routing, dict):
                requested = routing.get('pattern') or routing.get('route')
                if isinstance(requested, str):
                    pattern = requested

            exclude_syms = []
            if source_type == 'pin' and source_obj is not None:
                exclude_syms.append(source_obj)
            if target_type == 'pin' and target_obj is not None:
                exclude_syms.append(target_obj)

            route_points = _safe_manhattan_route(
                schematic,
                tuple(source_point),
                tuple(target_point),
                pattern_hint=pattern,
                exclude_symbols=exclude_syms,
            )
            properties['points'] = [[p[0], p[1]] for p in route_points]

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
            symbol = source_obj
            ref = _reference_from_symbol(symbol)
            pin_id = _pin_id_from_spec(source)
            pin_obj = None
            if hasattr(symbol, 'pin'):
                pid_norm = str(pin_id).strip().lower()
                for p in symbol.pin:
                    number = str(getattr(p, 'number', '')).strip().lower()
                    name = str(getattr(p, 'name', '')).strip().lower()
                    if pid_norm in {number, name}:
                        pin_obj = p
                        break
            pin_number = str(getattr(pin_obj, 'number', pin_id)) if pin_obj else str(pin_id)
            pin_type = _get_pin_type(pin_obj) if pin_obj else None
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
            symbol = target_obj
            ref = _reference_from_symbol(symbol)
            pin_id = _pin_id_from_spec(target)
            pin_obj = None
            if hasattr(symbol, 'pin'):
                pid_norm = str(pin_id).strip().lower()
                for p in symbol.pin:
                    number = str(getattr(p, 'number', '')).strip().lower()
                    name = str(getattr(p, 'name', '')).strip().lower()
                    if pid_norm in {number, name}:
                        pin_obj = p
                        break
            pin_number = str(getattr(pin_obj, 'number', pin_id)) if pin_obj else str(pin_id)
            pin_type = _get_pin_type(pin_obj) if pin_obj else None
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

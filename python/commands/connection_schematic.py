from __future__ import annotations

import logging
import uuid
import math
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple, Union, cast
import heapq


from skip import Schematic
from sexpdata import Symbol as SSymbol

from skip.eeschema.wire import WireWrapper
from skip.sexp.parser import ParsedValue
from skip.eeschema.schematic.symbol import Symbol

from .grid_utils import snap_point_to_grid

logger = logging.getLogger('kicad_interface')


_DEFAULT_WIRE_WIDTH = 0.254
_VALID_STROKE_TYPES = {'default', 'dash', 'dot'}
COORD_KEY_PRECISION = 6
MIN_ROUTED_SEGMENT_MM = 0.5  # Minimum acceptable segment length
COLLISION_BUFFER_MM = 0.5    # Clearance radius used for collision checks

# Stub configuration
KICAD_SCHEMATIC_GRID_MM = 1.27  # Standard KiCAD grid spacing
DEFAULT_STUB_LENGTH_MM = 1.27  # 1 grid = 1.27mm

# Routing configuration
BBOX_CLEARANCE_MM = 0.5 * KICAD_SCHEMATIC_GRID_MM  # 0.5 grid = 0.635mm clearance around symbols
DISTANCE_PENALTY_WEIGHT = 2.0  # Penalty weight for getting close to symbols (reduced to allow tighter routing)
CLEARANCE_THRESHOLD_MM = 1.0 * KICAD_SCHEMATIC_GRID_MM  # Encourage staying 1 grid away from symbols
INSIDE_BBOX_PENALTY = 10.0  # Heavy penalty for routing inside symbol bboxes (but not hard-blocked)


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


def _coord_key(x: float, y: float) -> Tuple[float, float]:
    return (round(float(x), COORD_KEY_PRECISION), round(float(y), COORD_KEY_PRECISION))


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


def _segment_length(a: Tuple[float, float], b: Tuple[float, float]) -> float:
    return math.hypot(b[0] - a[0], b[1] - a[1])


def _point_segment_distance(point: Tuple[float, float], a: Tuple[float, float], b: Tuple[float, float]) -> float:
    px, py = point
    ax, ay = a
    bx, by = b
    dx = bx - ax
    dy = by - ay
    seg_len_sq = dx * dx + dy * dy
    if seg_len_sq <= 0.0:
        return math.hypot(px - ax, py - ay)
    t = ((px - ax) * dx + (py - ay) * dy) / seg_len_sq
    t = max(0.0, min(1.0, t))
    nearest_x = ax + t * dx
    nearest_y = ay + t * dy
    return math.hypot(px - nearest_x, py - nearest_y)


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

    # Normalise wire points for spatial matching
    wire_point_set = {_coord_key(x, y) for x, y in wire_points}

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
                                x, y = _coord_key(loc.x, loc.y)
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
                                    x, y = _coord_key(coords[0], coords[1])
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


def _calculate_stub_end(pin_obj: Any, stub_length: float = DEFAULT_STUB_LENGTH_MM) -> Tuple[float, float]:
    """
    Calculate the stub end point for a pin based on its rotation.

    The pin rotation indicates the direction the pin points outward from the symbol body.
    The stub extends in the same direction as the pin points.

    Args:
        pin_obj: Pin object with location attribute (x, y, rotation)
        stub_length: Length of stub in mm (default: 2.54mm = 2 grids)

    Returns:
        (stub_end_x, stub_end_y): Grid-snapped coordinates of the stub end point

    Pin rotation mapping:
        0° → Pin points RIGHT → stub extends in +X direction
        90° → Pin points UP → stub extends in +Y direction
        180° → Pin points LEFT → stub extends in -X direction
        270° → Pin points DOWN → stub extends in -Y direction
    """
    loc = pin_obj.location
    pin_x = float(loc.x)
    pin_y = float(loc.y)
    pin_rotation = float(loc.rotation)

    # Map rotation to direction vector
    if pin_rotation == 0:
        # Pin points RIGHT → stub extends RIGHT
        dx, dy = 1.0, 0.0
    elif pin_rotation == 90:
        # Pin points UP → stub extends UP
        dx, dy = 0.0, 1.0
    elif pin_rotation == 180:
        # Pin points LEFT → stub extends LEFT
        dx, dy = -1.0, 0.0
    elif pin_rotation == 270:
        # Pin points DOWN → stub extends DOWN
        dx, dy = 0.0, -1.0
    else:
        # Handle non-standard rotations (shouldn't happen in practice)
        angle_rad = math.radians(pin_rotation)
        dx = math.cos(angle_rad)
        dy = math.sin(angle_rad)

    # Calculate stub end point
    stub_end_x = pin_x + dx * stub_length
    stub_end_y = pin_y + dy * stub_length

    # Snap to grid
    return snap_point_to_grid(stub_end_x, stub_end_y)


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

def _distance_to_rect_edge(px: float, py: float, rect: Tuple[float, float, float, float]) -> float:
    """Calculate the shortest distance from a point to the edge of a rectangle.

    Args:
        px, py: Point coordinates
        rect: Rectangle as (xmin, ymin, xmax, ymax)

    Returns:
        Distance to nearest edge. Returns 0 if point is inside or on the rectangle.
    """
    xmin, ymin, xmax, ymax = rect

    # Distance in X direction
    if px < xmin:
        dx = xmin - px
    elif px > xmax:
        dx = px - xmax
    else:
        dx = 0.0  # Inside X range

    # Distance in Y direction
    if py < ymin:
        dy = ymin - py
    elif py > ymax:
        dy = py - ymax
    else:
        dy = 0.0  # Inside Y range

    # If both dx and dy are 0, point is inside rectangle
    if dx == 0.0 and dy == 0.0:
        return 0.0

    # Euclidean distance to nearest edge
    return math.sqrt(dx * dx + dy * dy)


def _segment_intersects_rect(a: Tuple[float, float], b: Tuple[float, float], rect: Tuple[float, float, float, float]) -> bool:
    """Check if a Manhattan segment intersects or touches a rectangle.

    Treats touching the rectangle boundary as an intersection to prevent wires
    from running along symbol edges.
    """
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


def _segment_runs_along_rect_edge(
    a: Tuple[float, float],
    b: Tuple[float, float],
    rect: Tuple[float, float, float, float],
    tolerance: float = 0.15,
) -> bool:
    """Check if a segment runs along (parallel to and touching) a rectangle edge.

    This is stricter than _segment_intersects_rect - it specifically detects when
    a segment is running along the boundary of a rectangle, which we want to prevent
    even for excluded symbols.

    Args:
        a: First endpoint (x, y)
        b: Second endpoint (x, y)
        rect: Rectangle (xmin, ymin, xmax, ymax)
        tolerance: Tolerance for floating point comparison (default 0.15 to account for rounding to 1 decimal place)

    Returns:
        True if the segment runs along any edge of the rectangle
    """
    x1, y1 = a
    x2, y2 = b
    xmin, ymin, xmax, ymax = rect

    # Horizontal segment
    if y1 == y2:
        y = y1
        # Check if segment is on top or bottom edge
        if abs(y - ymin) < tolerance or abs(y - ymax) < tolerance:
            lo, hi = (x1, x2) if x1 <= x2 else (x2, x1)
            # Check if segment overlaps with rectangle in x direction
            if not (hi <= xmin or lo >= xmax):
                return True

    # Vertical segment
    if x1 == x2:
        x = x1
        # Check if segment is on left or right edge
        if abs(x - xmin) < tolerance or abs(x - xmax) < tolerance:
            lo, hi = (y1, y2) if y1 <= y2 else (y2, y1)
            # Check if segment overlaps with rectangle in y direction
            if not (hi <= ymin or lo >= ymax):
                return True

    return False


def _manhattan_astar_route(
    schematic: Schematic,
    start: Tuple[float, float],
    end: Tuple[float, float],
    step: float,
    bboxes: List[Tuple[Tuple[float, float, float, float], Symbol]],
    forbidden_points: Iterable[Tuple[float, float]],
    max_expansions: int = 12000,
    bend_penalty: float = 1.0,  # Increased to prioritize fewer corners
) -> Optional[List[Tuple[float, float]]]:
    """Manhattan A* that operates directly in schematic millimetres.

    - Obstacles are the interiors of symbol body rectangles (handled as soft penalties).
    - Start/end positions are preserved exactly; no grid snapping is applied.
    - Returns a list of points including the true start and end, or None if no path is found.
    - Cost function prioritizes: 1) Fewer bends, 2) Shorter distance, 3) Clearance from bodies.
    """
    sx, sy = start
    ex, ey = end

    rects: List[Tuple[float, float, float, float]] = [r for (r, _sym) in bboxes]

    allowed_endpoints = {_coord_key(sx, sy), _coord_key(ex, ey)}
    forb_set = {
        _coord_key(px, py)
        for (px, py) in set(forbidden_points)
        if _coord_key(px, py) not in allowed_endpoints
    }

    span_steps_x = abs(sx - ex) / step if step else 0.0
    span_steps_y = abs(sy - ey) / step if step else 0.0
    margin_steps = max(10.0, max(span_steps_x, span_steps_y) + 6.0)
    margin_distance = margin_steps * step

    xmin = min(sx, ex) - margin_distance
    xmax = max(sx, ex) + margin_distance
    ymin = min(sy, ey) - margin_distance
    ymax = max(sy, ey) + margin_distance

    def blocked(px: float, py: float) -> bool:
        """Check if a millimetre-space node is outside the window or collides with pins/nodes."""
        if px < xmin or px > xmax or py < ymin or py > ymax:
            return True
        key = _coord_key(px, py)
        if key in forb_set:
            return True
        return False

    DIRS = [(1, 0), (-1, 0), (0, 1), (0, -1)]

    def heuristic(px: float, py: float) -> float:
        return abs(px - ex) + abs(py - ey)

    def state_key(px: float, py: float, direction: int) -> Tuple[Tuple[float, float], int]:
        return (_coord_key(px, py), direction)

    start_state = (sx, sy, -1)
    open_heap: List[Tuple[float, float, Tuple[float, float, int]]] = []
    heapq.heappush(open_heap, (heuristic(sx, sy), 0.0, start_state))
    came_from: Dict[Tuple[Tuple[float, float], int], Tuple[float, float, int]] = {}
    gscore: Dict[Tuple[Tuple[float, float], int], float] = {state_key(*start_state): 0.0}

    expansions = 0
    GOAL_EPS = 1e-6
    end_key = _coord_key(ex, ey)

    while open_heap and expansions < max_expansions:
        f, g, (x, y, dprev) = heapq.heappop(open_heap)
        expansions += 1

        current_key = _coord_key(x, y)
        if current_key == end_key:
            path: List[Tuple[float, float]] = [(x, y)]
            state = (x, y, dprev)
            skey = state_key(*state)
            while skey in came_from:
                state = came_from[skey]
                path.append((state[0], state[1]))
                skey = state_key(*state)
            path.reverse()

            simplified: List[Tuple[float, float]] = []
            for pt in path:
                if not simplified:
                    simplified.append(pt)
                    continue
                simplified.append(pt)
                if len(simplified) >= 3:
                    a, b, c = simplified[-3], simplified[-2], simplified[-1]
                    if (abs(a[0] - b[0]) <= GOAL_EPS and abs(b[0] - c[0]) <= GOAL_EPS) or (
                        abs(a[1] - b[1]) <= GOAL_EPS and abs(b[1] - c[1]) <= GOAL_EPS
                    ):
                        simplified.pop(-2)

            if _coord_key(*simplified[0]) != _coord_key(sx, sy):
                simplified.insert(0, (sx, sy))
            if _coord_key(*simplified[-1]) != end_key:
                simplified.append((ex, ey))

            return simplified

        for i, (dx, dy) in enumerate(DIRS):
            candidates: List[Tuple[float, float]] = []

            if dx != 0:
                nx = x + dx * step
                ny = y
                nx = round(nx, COORD_KEY_PRECISION)
                ny = round(ny, COORD_KEY_PRECISION)
                candidates.append((nx, ny))

                remaining_x = ex - x
                if remaining_x != 0.0 and (remaining_x > 0) == (dx > 0):
                    if abs(remaining_x) <= step + GOAL_EPS:
                        candidates.append((round(ex, COORD_KEY_PRECISION), ny))

            else:
                nx = x
                ny = y + dy * step
                nx = round(nx, COORD_KEY_PRECISION)
                ny = round(ny, COORD_KEY_PRECISION)
                candidates.append((nx, ny))

                remaining_y = ey - y
                if remaining_y != 0.0 and (remaining_y > 0) == (dy > 0):
                    if abs(remaining_y) <= step + GOAL_EPS:
                        candidates.append((nx, round(ey, COORD_KEY_PRECISION)))

            for nx, ny in candidates:
                if blocked(nx, ny):
                    continue

                cost = 1.0
                if dprev != -1 and dprev != i:
                    cost += bend_penalty

                min_distance = float('inf')
                is_inside_any_bbox = False

                for rect in rects:
                    dist = _distance_to_rect_edge(nx, ny, rect)
                    min_distance = min(min_distance, dist)
                    xmin_r, ymin_r, xmax_r, ymax_r = rect
                    if (xmin_r <= nx <= xmax_r) and (ymin_r <= ny <= ymax_r):
                        is_inside_any_bbox = True

                if is_inside_any_bbox:
                    cost += INSIDE_BBOX_PENALTY
                elif min_distance < CLEARANCE_THRESHOLD_MM:
                    distance_penalty = (CLEARANCE_THRESHOLD_MM - min_distance) * DISTANCE_PENALTY_WEIGHT
                    cost += distance_penalty

                ng = g + cost
                ns = state_key(nx, ny, i)
                if ng < gscore.get(ns, float('inf')):
                    gscore[ns] = ng
                    came_from[ns] = (x, y, dprev)
                    nf = ng + heuristic(nx, ny)
                    heapq.heappush(open_heap, (nf, ng, (nx, ny, i)))

    return None


def _safe_manhattan_route(
    schematic: Schematic,
    start: Tuple[float, float],
    end: Tuple[float, float],
    exclude_symbols: Optional[Iterable[Symbol]] = None,  # Kept for API compatibility but no longer used
) -> List[Tuple[float, float]]:
    """Simplified two-level routing logic using A* with soft penalties.

    Priority levels:
    1. Manhattan A* with distance penalties - Primary pathfinding algorithm
    2. Adaptive stubs - Fallback when A* fails

    Routing strategy:
    - Hard blocks: Only forbidden pin/node points and search bounds
    - Soft penalties: Symbol bboxes (heavy penalty inside, lighter penalty nearby)
    - This allows routing through tight spaces and handles pins inside bboxes
    - Minimized corners/segments (highest priority) and wire length (second priority)
    """
    # Preserve original endpoints; routing grid coordinates are derived on demand
    s = (float(start[0]), float(start[1]))
    e = (float(end[0]), float(end[1]))

    if s == e:
        raise ValueError('Pins share the same coordinates; cannot connect')

    pin_coords_set = set(_collect_pin_coords(schematic))
    node_coords_set = set(_collect_wire_vertices(schematic))
    bboxes = _collect_symbol_bboxes(schematic)

    # Allow using the endpoints themselves
    allowed_endpoints = {_coord_key(*s), _coord_key(*e)}

    forbidden_vertices_set = (pin_coords_set | node_coords_set) - allowed_endpoints
    forbidden_vertices = [tuple(v) for v in forbidden_vertices_set]
    pin_collision_points = [tuple(v) for v in pin_coords_set if v not in allowed_endpoints]

    def ok_route(points: List[Tuple[float, float]]) -> bool:
        """Validate that a route doesn't pass through forbidden pin/node points."""
        if len(points) < 2:
            return False

        # Internal vertices must keep clearance from forbidden points
        for pt in points[1:-1]:
            for fv in forbidden_vertices:
                if _segment_length(pt, fv) < COLLISION_BUFFER_MM:
                    return False

        # Each segment must keep clearance from collision points
        for a, b in zip(points, points[1:]):
            seg_len = _segment_length(a, b)
            if seg_len <= 0.0:
                # Zero-length segment is never acceptable
                return False
            for pc in pin_collision_points:
                if _point_segment_distance(pc, a, b) < COLLISION_BUFFER_MM:
                    return False

        return True

    step = KICAD_SCHEMATIC_GRID_MM

    # LEVEL 1: Manhattan A* - Primary routing algorithm
    # Pass ALL bboxes including excluded symbols - the A* will allow start/end to be inside them
    astar_route = _manhattan_astar_route(
        schematic,
        s,
        e,
        step,
        bboxes,  # Include all symbols - A* allows start/end inside rects
        forbidden_vertices,
        max_expansions=12000,
        bend_penalty=1.0,  # High penalty to minimize corners
    )
    if astar_route is not None and ok_route(astar_route):
        return astar_route

    # LEVEL 2: Adaptive stubs - Fallback when A* fails
    # Try escaping from start or end point in all four directions
    escape_vectors = [(0, step), (0, -step), (step, 0), (-step, 0)]

    def try_escape(from_start: bool) -> Optional[List[Tuple[float, float]]]:
        """Try to escape from start or end point and route via A* from the escape point."""
        for dx, dy in escape_vectors:
            esc = (
                (s[0] + dx, s[1] + dy)
                if from_start
                else (e[0] + dx, e[1] + dy)
            )
            esc = (
                round(esc[0], COORD_KEY_PRECISION),
                round(esc[1], COORD_KEY_PRECISION),
            )

            esc_rounded = _coord_key(*esc)

            # Skip if escape point is forbidden
            if esc_rounded in forbidden_vertices_set:
                continue

            # Try routing from/to the escape point using A*
            if from_start:
                # Route from escape point to end
                escape_route = _manhattan_astar_route(
                    schematic,
                    esc,
                    e,
                    step,
                    bboxes,  # Include all symbols
                    forbidden_vertices,
                    max_expansions=8000,
                    bend_penalty=1.0,
                )
                if escape_route is not None:
                    # Prepend the stub from start to escape
                    full_route = [s] + escape_route
                    if ok_route(full_route):
                        return full_route
            else:
                # Route from start to escape point
                escape_route = _manhattan_astar_route(
                    schematic,
                    s,
                    esc,
                    step,
                    bboxes,  # Include all symbols
                    forbidden_vertices,
                    max_expansions=8000,
                    bend_penalty=1.0,
                )
                if escape_route is not None:
                    # Append the stub from escape to end
                    full_route = escape_route + [e]
                    if ok_route(full_route):
                        return full_route
        return None

    # Try escaping from start or end
    r = try_escape(True) or try_escape(False)
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
                    "net": "Net-5",
                    "summary": "R1.1(passive) - R2.2(passive) [Net-5]"
                },
                "net": "Net-5",
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

        Automatically routes wires using Manhattan (horizontal/vertical) pathfinding with
        intelligent obstacle avoidance. The routing algorithm:
        - Adds stubs extending from component pins for proper clearance
        - Uses A* pathfinding to find optimal paths that avoid symbol bodies
        - Minimizes corners and wire length
        - Ensures wires never run along symbol edges or through symbol bodies
        - Snaps all coordinates to KiCAD's standard grid (1.27mm)

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
                    "net": "Net-7",       # resolved net name/id
                    "summary": "R1.1(passive) - R2.2(input) [Net-7]"  # human-friendly
                },
                "net": "Net-7",
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

            route_points = _safe_manhattan_route(
                schematic,
                route_start,
                route_end,
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
            if not isinstance(target_obj, Symbol):
                raise TypeError("Resolved target object is not a Symbol")
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

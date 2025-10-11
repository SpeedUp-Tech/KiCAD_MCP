from __future__ import annotations

import logging
import uuid
import math
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple, Union

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
    Find a hierarchical label in the schematic by name and return its position.

    Args:
        schematic: The schematic to search
        label_name: The name of the hierarchical label to find

    Returns:
        Tuple of (label_name, (x, y)) coordinates

    Raises:
        ValueError: If the label is not found
    """
    if not hasattr(schematic, 'tree') or not isinstance(schematic.tree, list):
        raise ValueError("Schematic tree is not accessible")

    label_name_normalized = str(label_name).strip()

    # Search through the schematic tree for hierarchical labels
    for elem in schematic.tree:
        if not isinstance(elem, list) or len(elem) < 2:
            continue

        # Check if this is a hierarchical_label element
        if hasattr(elem[0], 'value') and elem[0].value() == 'hierarchical_label':
            # The label name is the second element
            current_label_name = str(elem[1]).strip()

            if current_label_name == label_name_normalized:
                # Find the 'at' element which contains position
                for sub_elem in elem:
                    if (isinstance(sub_elem, list) and
                        len(sub_elem) >= 3 and
                        hasattr(sub_elem[0], 'value') and
                        sub_elem[0].value() == 'at'):
                        # Extract x, y coordinates (angle is at index 3)
                        x = float(sub_elem[1])
                        y = float(sub_elem[2])
                        return (current_label_name, (x, y))

                # Found the label but no position - this shouldn't happen
                raise ValueError(
                    f"Hierarchical label '{label_name}' found but has no position information"
                )

    raise ValueError(f"Hierarchical label '{label_name}' not found in schematic")


def _resolve_connection_point(
    schematic: Schematic,
    spec: Dict[str, Any],
    *,
    label: str,
) -> Tuple[Optional[Any], Tuple[float, float], str]:
    """
    Resolve a connection point specification to coordinates.

    Supports both component pins and hierarchical labels:
    - Pin spec: { reference: "R1", pin: "1", unit?: 1 }
    - Label spec: { label: "VBAT" } or { labelName: "VBAT" }

    Args:
        schematic: The schematic containing the connection point
        spec: Dictionary specifying either a pin or a label
        label: Description for error messages (e.g., "source", "target")

    Returns:
        Tuple of (symbol_or_none, (x, y), connection_type)
        - For pins: (Symbol, (x, y), "pin")
        - For labels: (None, (x, y), "label")

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
            "Please specify either a component pin OR a hierarchical label, not both."
        )

    if not has_reference and not has_label:
        raise ValueError(
            f"{label} specification is invalid: must contain either 'reference' (for pin) "
            "or 'label'/'labelName' (for hierarchical label)"
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
    def update_wire(
        schematic: Schematic,
        wire_uuid: str,
        updates: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Modify an existing wire's geometry or styling."""

        if not isinstance(wire_uuid, str) or not wire_uuid.strip():
            raise ValueError('wireUuid must be a non-empty string')
        if not isinstance(updates, dict):
            raise TypeError('updates must be a mapping')
        if not updates:
            raise ValueError('updates cannot be empty')

        wrapper = _find_wire_by_uuid(schematic, wire_uuid.strip())
        if wrapper is None:
            raise ValueError(f"Wire '{wire_uuid}' not found")

        config = dict(updates)
        start_point = config.pop('startPoint', None)
        end_point = config.pop('endPoint', None)
        points_override = (
            config.pop('points', None)
            or config.pop('pointList', None)
            or config.pop('segments', None)
        )
        midpoints = config.pop('midpoints', None) or config.pop('viaPoints', None)
        if midpoints is not None:
            if points_override is None:
                points_override = []
            if not isinstance(points_override, list):
                raise TypeError('points must be provided as a list when using midpoints')
            points_override.extend(midpoints)

        width = config.pop('width', None)
        stroke_type = config.pop('strokeType', None) or config.pop('style', None)

        if config:
            unknown = ', '.join(sorted(config.keys()))
            raise ValueError(f"Unsupported wire update fields: {unknown}")

        if points_override is not None or start_point is not None or end_point is not None:
            new_points = _normalise_points(start_point, end_point, points_override)
            pts_node = wrapper.raw[1] if len(wrapper.raw) > 1 else None
            replacement = [SSymbol('pts')]
            for x_val, y_val in new_points:
                replacement.append([SSymbol('xy'), round(x_val, 6), round(y_val, 6)])

            if isinstance(pts_node, list) and pts_node:
                pts_node[:] = replacement
            else:
                if len(wrapper.raw) > 1:
                    wrapper.raw[1] = replacement
                else:
                    wrapper.raw.insert(1, replacement)

        if width is not None:
            try:
                new_width = float(width)
            except (TypeError, ValueError) as exc:
                raise ValueError('width must be numeric') from exc
            if new_width <= 0:
                raise ValueError('width must be positive')
            wrapper.stroke.width.value = round(new_width, 6)

        if stroke_type is not None:
            stroke_value = str(stroke_type).lower()
            if stroke_value not in _VALID_STROKE_TYPES:
                raise ValueError(
                    f"strokeType must be one of: {', '.join(sorted(_VALID_STROKE_TYPES))}"
                )
            wrapper.stroke.type.value = stroke_value

        return _wire_payload(wrapper)

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
        connection_id: Union[str, Iterable[str]],
    ) -> List[Dict[str, Any]]:
        """Remove one or more wire segments that represent a connection."""

        if isinstance(connection_id, str):
            candidate_ids = [connection_id]
        elif isinstance(connection_id, Iterable):
            candidate_ids = list(connection_id)
        else:
            raise TypeError('connection_id must be a wire UUID or an iterable of UUIDs')

        normalised_ids = {str(uuid_value).strip() for uuid_value in candidate_ids if uuid_value}
        if not normalised_ids:
            raise ValueError('At least one wire UUID is required to remove a connection')

        if not hasattr(schematic, 'wire') or not len(schematic.wire):
            raise ValueError('Schematic contains no wires to remove')

        removed_wrappers: List[WireWrapper] = []
        removed_payloads: List[Dict[str, Any]] = []

        for wire in list(schematic.wire._elements):
            wire_uuid = getattr(getattr(wire, 'uuid', None), 'value', None)
            if wire_uuid in normalised_ids:
                # Capture payload BEFORE mutating the tree so geometry is intact
                try:
                    removed_payloads.append(_wire_payload(wire))
                except Exception:
                    # As a fallback, at least return the UUID if payload extraction fails
                    removed_payloads.append({
                        'uuid': wire_uuid,
                        'points': [],
                        'width': None,
                        'strokeType': None,
                        'length': 0.0,
                    })
                parent = wire.raw_parent
                if wire.raw in parent:
                    parent.remove(wire.raw)
                removed_wrappers.append(wire)

        if not removed_wrappers:
            raise ValueError('No wires matched the supplied UUIDs')

        schematic.wire._elements = [
            wire for wire in schematic.wire._elements if wire not in removed_wrappers
        ]

        logger.info("Removed %d wire segment(s)", len(removed_payloads))
        return removed_payloads

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
    ) -> WireWrapper:
        """
        Connect two schematic connection points by drawing an appropriate wire.

        Supports connecting:
        - Pin to pin: Both source and target specify component pins
        - Pin to hierarchical label: One specifies a pin, the other a label
        - Label to label: Both specify hierarchical labels

        Args:
            schematic: The schematic to modify
            source: Source connection point specification:
                    - Pin: { reference: "R1", pin: "1", unit?: 1 }
                    - Label: { label: "VBAT" } or { labelName: "VBAT" }
            target: Target connection point specification (same format as source)
            wire: Optional wire styling properties (width, strokeType, etc.)
            routing: Optional routing hints (pattern: 'hv' or 'vh')

        Returns:
            List of created wire wrapper(s)

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
                # Same symbol - need to check if it's the same pin
                # This is a simplified check; the original checked pin objects
                raise ValueError('Cannot connect a pin to itself')
            elif source_type == "label" and source_point == target_point:
                # Same label position means same label
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

            route_points = _build_manhattan_path(source_point, target_point, pattern=pattern)
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

        if isinstance(added, list):
            return added
        return [added]

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

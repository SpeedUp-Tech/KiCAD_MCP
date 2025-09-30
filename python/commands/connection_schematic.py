from __future__ import annotations

from skip import Schematic
import logging
import uuid
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from sexpdata import Symbol as SSymbol

from skip.eeschema.wire import WireWrapper
from skip.sexp.parser import ParsedValue
from skip.eeschema.schematic.symbol import Symbol

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
        pts_expr.append([SSymbol('xy'), round(x_val, 6), round(y_val, 6)])

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
    def remove_connection(schematic: Schematic, connection_id: str):
        """Remove a connection"""
        # Removing connections in kicad-skip typically means removing the wires
        # or net labels that form the connection.
        # This method would need to identify the relevant graphical elements
        # based on a connection identifier (which we would need to define).
        # This is also an advanced implementation task.
        logger.warning("Attempted to remove connection with ID %s. This requires advanced implementation.", connection_id)
        return False # Indicate not fully implemented yet

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
        """Connect two schematic pins by drawing an appropriate wire."""

        source_symbol, source_pin, source_point = _resolve_pin(
            schematic,
            source,
            label='source',
        )
        target_symbol, target_pin, target_point = _resolve_pin(
            schematic,
            target,
            label='target',
        )

        if source_symbol == target_symbol and source_pin == target_pin:
            raise ValueError('Cannot connect a pin to itself')

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

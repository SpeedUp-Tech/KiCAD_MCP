"""Footprint creation helpers for KiCAD interface"""

import os
import logging
from typing import Dict, Any, List

logger = logging.getLogger('kicad_interface')


def _fmt(value: float) -> str:
    """Format float for KiCAD textual output."""
    if isinstance(value, (int, float)):
        return f"{value:.4f}".rstrip('0').rstrip('.')
    return str(value)


class FootprintManager:
    """Generate KiCAD footprint files headlessly."""

    @staticmethod
    def create_footprint(params: Dict[str, Any]) -> Dict[str, Any]:
        try:
            library_path = params.get("libraryPath")
            footprint_name = params.get("footprintName")

            if not library_path or not footprint_name:
                return {
                    "success": False,
                    "message": "libraryPath and footprintName are required"
                }

            library_path = os.path.abspath(os.path.expanduser(library_path))
            os.makedirs(library_path, exist_ok=True)

            filename = f"{footprint_name}.kicad_mod"
            footprint_path = os.path.join(library_path, filename)

            outline = params.get("outline", [])
            pads: List[Dict[str, Any]] = params.get("pads") or []
            attributes = params.get("attributes", [])
            layers_default = params.get("defaultLayers", ["F.Cu", "F.Paste", "F.Mask"])

            if not pads:
                return {
                    "success": False,
                    "message": "At least one pad definition is required"
                }

            footprint_text = FootprintManager._build_footprint(
                footprint_name,
                pads,
                outline,
                attributes,
                layers_default,
                params
            )

            with open(footprint_path, "w", encoding="utf-8") as fp:
                fp.write(footprint_text)

            logger.info(f"Created footprint {footprint_name} at {footprint_path}")
            return {
                "success": True,
                "message": "Created footprint",
                "footprintPath": footprint_path,
                "footprintName": footprint_name
            }

        except Exception as exc:
            logger.error(f"Error creating footprint: {exc}")
            return {
                "success": False,
                "message": "Failed to create footprint",
                "errorDetails": str(exc)
            }

    @staticmethod
    def _build_footprint(
        footprint_name: str,
        pads: List[Dict[str, Any]],
        outline: List[Dict[str, Any]],
        attributes: List[str],
        default_layers: List[str],
        params: Dict[str, Any]
    ) -> str:
        lines: List[str] = []
        lines.append(f"(footprint \"{footprint_name}\"")
        lines.append("  (version 20221018)")
        lines.append("  (generator \"KiCAD-MCP\")")
        lines.append("  (layer \"F.Cu\")")

        if attributes:
            attr_line = "  (attr " + " ".join(attributes) + ")"
            lines.append(attr_line)

        ref_y = params.get("referenceY", 5.0)
        val_y = params.get("valueY", -5.0)

        lines.append(
            f"  (fp_text reference \"REF**\" (at 0 {_fmt(ref_y)}) (layer \"F.SilkS\") "
            "(effects (font (size 1 1))))"
        )
        lines.append(
            f"  (fp_text value \"{footprint_name}\" (at 0 {_fmt(val_y)}) (layer \"F.Fab\") "
            "(effects (font (size 1 1))))"
        )

        for segment in outline:
            shape = segment.get("type", "line")
            layer = segment.get("layer", "F.SilkS")
            width = _fmt(segment.get("width", 0.15))

            if shape == "line":
                start = segment.get("start", {"x": -5, "y": -5})
                end = segment.get("end", {"x": 5, "y": 5})
                lines.append(
                    "  (fp_line (start {} {}) (end {} {}) (layer \"{}\") (width {}))".format(
                        _fmt(start.get("x", -5)),
                        _fmt(start.get("y", -5)),
                        _fmt(end.get("x", 5)),
                        _fmt(end.get("y", 5)),
                        layer,
                        width,
                    )
                )
            elif shape == "circle":
                center = segment.get("center", {"x": 0, "y": 0})
                end = segment.get("end", {"x": 0, "y": 1})
                lines.append(
                    "  (fp_circle (center {} {}) (end {} {}) (layer \"{}\") (width {}))".format(
                        _fmt(center.get("x", 0)),
                        _fmt(center.get("y", 0)),
                        _fmt(end.get("x", 0)),
                        _fmt(end.get("y", 1)),
                        layer,
                        width,
                    )
                )

        for pad in pads:
            pad_number = str(pad.get("number"))
            pad_type = pad.get("type", "smd")
            pad_shape = pad.get("shape", "rect")
            at_x = _fmt(pad.get("x", 0.0))
            at_y = _fmt(pad.get("y", 0.0))
            rotation = _fmt(pad.get("rotation", 0.0))

            size = pad.get("size") or [1.0, 1.0]
            if isinstance(size, list) and len(size) == 2:
                size_x, size_y = _fmt(size[0]), _fmt(size[1])
            else:
                size_x = size_y = _fmt(pad.get("sizeX", 1.0))

            layer_list = pad.get("layers") or default_layers
            layers_str = " ".join([f'\"{layer}\"' for layer in layer_list])

            pad_line = (
                f"  (pad \"{pad_number}\" {pad_type} {pad_shape} "
                f"(at {at_x} {at_y} {rotation}) (size {size_x} {size_y}) (layers {layers_str})"
            )

            drill = pad.get("drill")
            if pad_type == "thru_hole" and drill:
                if isinstance(drill, dict):
                    drill_shape = drill.get("shape", "circular")
                    if drill_shape == "oval":
                        dx = _fmt(drill.get("x", drill.get("width", 0.6)))
                        dy = _fmt(drill.get("y", drill.get("height", 0.4)))
                        pad_line += f" (drill oval {dx} {dy})"
                    else:
                        pad_line += f" (drill {_fmt(drill.get('size', 0.6))})"
                else:
                    pad_line += f" (drill {_fmt(drill)})"

            pad_properties = pad.get("properties", {})
            if pad_properties.get("roundrect_rratio"):
                pad_line += f" (roundrect_rratio {_fmt(pad_properties['roundrect_rratio'])})"

            if pad.get("net"):
                pad_line += f" (net 0 \"{pad['net']}\")"

            pad_line += ")"
            lines.append(pad_line)

        lines.append(")")
        return "\n".join(lines) + "\n"

#!/usr/bin/env python3
"""
KiCAD Python Interface Script for Model Context Protocol

This script handles communication between the MCP TypeScript server
and KiCAD's Python API (pcbnew). It receives commands via stdin as
JSON and returns responses via stdout also as JSON.
"""

import sys
import json
import traceback
import logging
import os
import subprocess
import shutil
import tempfile
from pathlib import Path
from typing import Dict, Any, Optional, List, Tuple
from datetime import datetime, timezone

# Ensure repo root is on sys.path so package imports like `python.router` succeed
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
class JsonStdoutHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        try:
            # Build a minimal, safe JSON log frame without relying on external formatters
            msg = record.getMessage()
            level = record.levelname.lower()
            # Use ISO8601 UTC timestamp; avoid complex operations that could raise
            ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
            frame = {
                "type": "log",
                "level": level,
                "message": msg,
                "time": ts,
            }
            sys.stdout.write(json.dumps(frame, default=str) + "\n")
            sys.stdout.flush()
        except Exception:
            # Avoid recursive logging on handler failure; swallow errors
            try:
                fallback = {"type": "log", "level": "error", "message": "log_emit_failed"}
                sys.stdout.write(json.dumps(fallback) + "\n")
                sys.stdout.flush()
            except Exception:
                pass

# Configure logging
def _create_logging_handlers() -> list[logging.Handler]:
    handlers: list[logging.Handler] = [JsonStdoutHandler()]
    log_dir = Path("/kicad_logs")
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        date_suffix = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        log_path = log_dir / f"kicad_interface-{date_suffix}.log"
        handlers.insert(0, logging.FileHandler(log_path))
    except (OSError, PermissionError) as exc:
        sys.stderr.write(f"WARNING: unable to write log to {log_dir}: {exc}\n")

    return handlers

logging.basicConfig(
    level=logging.WARNING,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=_create_logging_handlers(),
)

# Suppress noisy debug output from kicad-skip internals unless explicitly requested.
logging.getLogger('skip').setLevel(logging.WARNING)
logger = logging.getLogger('kicad_interface')

# Log Python environment details
logger.info(f"Python version: {sys.version}")
logger.info(f"Python executable: {sys.executable}")
logger.info(f"Python path: {sys.path}")

# Add KiCAD Python paths
kicad_paths = [
    os.path.join(os.path.dirname(sys.executable), 'Lib', 'site-packages'),
    os.path.dirname(sys.executable)
]
for path in kicad_paths:
    if path not in sys.path:
        logger.info(f"Adding KiCAD path: {path}")
        sys.path.append(path)

# Import KiCAD's Python API
try:
    logger.info("Attempting to import pcbnew module...")
    import pcbnew  # type: ignore
    logger.info(f"Successfully imported pcbnew module from: {pcbnew.__file__}")
    logger.info(f"pcbnew version: {pcbnew.GetBuildVersion()}")
except ImportError as e:
    logger.error(f"Failed to import pcbnew module: {e}")
    logger.error(f"Current sys.path: {sys.path}")
    error_response = {
        "success": False,
        "message": "Failed to import pcbnew module",
        "errorDetails": f"Error: {str(e)}\nPython path: {sys.path}"
    }
    print(json.dumps(error_response))
    sys.exit(1)
except Exception as e:
    logger.error(f"Unexpected error importing pcbnew: {e}")
    logger.error(traceback.format_exc())
    error_response = {
        "success": False,
        "message": "Error importing pcbnew module",
        "errorDetails": str(e)
    }
    print(json.dumps(error_response))
    sys.exit(1)

# Import command handlers
try:
    logger.info("Importing command handlers...")
    from commands.pcb.project import ProjectCommands
    from commands.pcb.board import BoardCommands
    from commands.pcb.component import ComponentCommands
    from commands.pcb.routing import RoutingCommands
    from commands.pcb.design_rules import DesignRuleCommands
    from commands.pcb.export import ExportCommands
    from commands.kicad_schematics.schematic import SchematicManager
    from commands.kicad_schematics.component_schematic import ComponentManager
    from commands.kicad_schematics.connection_schematic import ConnectionManager, SchematicCompiler
    from commands.database_tools.library_schematic import LibraryManager
    from commands.database_tools.footprint import FootprintManager
    from commands.kicad_schematics.blueprint_to_hierarchical import generate_hierarchical_schematic
    from commands.kicad_schematics.schematic_state import get_schematic_state
    from commands.database_tools.component_search import ComponentSearchCommands
    from commands.kicad_schematics.erc_utils import prepare_module_erc_artifacts
    from commands.database_tools.library_export import export_project_libraries
    from python.spice_tools.testbench_runner import run_use_case
    from python.spice_tools.harness_sanity import harness_sanity_check
    logger.info("Successfully imported all command handlers")
except ImportError as e:
    logger.error(f"Failed to import command handlers: {e}")
    error_response = {
        "success": False,
        "message": "Failed to import command handlers",
        "errorDetails": str(e)
    }
    print(json.dumps(error_response))
    sys.exit(1)

def _resolve_kicad_cli() -> str:
    """Locate the kicad-cli executable."""
    env_candidate = os.environ.get("KICAD_CLI")
    if env_candidate:
        candidate_path = Path(env_candidate)
        if candidate_path.exists():
            return str(candidate_path)
        resolved = shutil.which(env_candidate)
        if resolved:
            return resolved

    which_candidate = shutil.which("kicad-cli")
    if which_candidate:
        return which_candidate
    raise FileNotFoundError(
        "kicad-cli executable not found. Set KICAD_CLI environment variable or ensure it is in PATH."
    )


def _run_kicad_cli(
    args: List[str],
    cwd: Optional[str] = None,
    env: Optional[Dict[str, str]] = None,
) -> Tuple[subprocess.CompletedProcess[str], str]:
    """Run a kicad-cli command and return the completed process and executable path."""
    executable = _resolve_kicad_cli()
    command = [executable, *args]
    logger.debug(f"Running kicad-cli command: {' '.join(command)}")
    completed = subprocess.run(command, capture_output=True, text=True, cwd=cwd, env=env)
    return completed, executable


class KiCADInterface:
    """Main interface class to handle KiCAD operations"""

    def __init__(self):
        """Initialize the interface and command handlers"""
        self.board = None
        self.project_filename = None

        logger.info("Initializing command handlers...")

        # Initialize command handlers
        self.project_commands = ProjectCommands(self.board)
        self.board_commands = BoardCommands(self.board)
        self.component_commands = ComponentCommands(self.board)
        self.routing_commands = RoutingCommands(self.board)
        self.design_rule_commands = DesignRuleCommands(self.board)
        self.export_commands = ExportCommands(self.board)
        self.symbol_library = LibraryManager()
        self.footprint_manager = FootprintManager()
        self.component_search_commands = ComponentSearchCommands()

        # Schematic-related classes don't need board reference
        # as they operate directly on schematic files

        # Command routing dictionary
        self.command_routes = {
            # Project commands
            "create_project": self.project_commands.create_project,
            "open_project": self.project_commands.open_project,
            "save_project": self.project_commands.save_project,
            "get_project_info": self.project_commands.get_project_info,
            "set_project_properties": self.project_commands.set_project_properties,
            "create_backup": self.project_commands.create_backup,
            "archive_project": self.project_commands.archive_project,
            "import_project": self.project_commands.import_project,

            # Board commands
            "set_board_size": self.board_commands.set_board_size,
            "add_layer": self.board_commands.add_layer,
            "set_active_layer": self.board_commands.set_active_layer,
            "get_board_info": self.board_commands.get_board_info,
            "get_layer_list": self.board_commands.get_layer_list,
            "get_board_2d_view": self.board_commands.get_board_2d_view,
            "add_board_outline": self.board_commands.add_board_outline,
            "add_mounting_hole": self.board_commands.add_mounting_hole,
            "add_text": self.board_commands.add_text,

            # Component commands
            "place_component": self.component_commands.place_component,
            "move_component": self.component_commands.move_component,
            "rotate_component": self.component_commands.rotate_component,
            "delete_component": self.component_commands.delete_component,
            "edit_component": self.component_commands.edit_component,
            "get_component_properties": self.component_commands.get_component_properties,
            "get_component_list": self.component_commands.get_component_list,
            "place_component_array": self.component_commands.place_component_array,
            "align_components": self.component_commands.align_components,
            "duplicate_component": self.component_commands.duplicate_component,
            "search_mpn_part": self.component_search_commands.search_mpn_part,

            # Routing commands
            "add_net": self.routing_commands.add_net,
            "route_trace": self.routing_commands.route_trace,
            "add_via": self.routing_commands.add_via,
            "delete_trace": self.routing_commands.delete_trace,
            "get_nets_list": self.routing_commands.get_nets_list,
            "create_netclass": self.routing_commands.create_netclass,
            "add_copper_pour": self.routing_commands.add_copper_pour,
            "route_differential_pair": self.routing_commands.route_differential_pair,

            # Design rule commands
            "set_design_rules": self.design_rule_commands.set_design_rules,
            "get_design_rules": self.design_rule_commands.get_design_rules,
            "run_drc": self.design_rule_commands.run_drc,
            "get_drc_violations": self.design_rule_commands.get_drc_violations,

            # Export commands
            "export_gerber": self.export_commands.export_gerber,
            "export_pdf": self.export_commands.export_pdf,
            "export_svg": self.export_commands.export_svg,
            "export_3d": self.export_commands.export_3d,
            "export_bom": self.export_commands.export_bom,

            # Library commands
            "create_symbol": self.symbol_library.create_symbol,
            "create_footprint": self.footprint_manager.create_footprint,
            "get_symbol_pinout": self.symbol_library.get_symbol_pinout,

            # Schematic commands
            "create_schematic": self._handle_create_schematic,
            "load_schematic": self._handle_load_schematic,
            "add_schematic_component": self._handle_add_schematic_component,
            "update_schematic_component": self._handle_update_schematic_component,
            "remove_schematic_component": self._handle_remove_schematic_component,
            "add_schematic_wire": self._handle_add_schematic_wire,
            "remove_schematic_connection": self._handle_remove_schematic_connection,
            "connect_schematic_pins": self._handle_connect_schematic_pins,
            "run_module_erc": self._handle_run_module_erc,
            "run_skidl_erc": self._handle_run_skidl_erc,
            "compile_schematic": self._handle_compile_schematic,
            "list_schematic_libraries": self._handle_list_schematic_libraries,
            "export_schematic_pdf": self._handle_export_schematic_pdf,
            "export_schematic_svg": self._handle_export_schematic_svg,
            "run_erc": self._handle_run_erc,
            "export_schematic_netlist": self._handle_export_netlist,
            "export_schematic_bom": self._handle_export_schematic_bom,
            "generate_hierarchical_schematic": self._handle_generate_hierarchical_schematic,
            "get_schematic_state": self._handle_get_schematic_state,

            # SPICE commands
            "run_spice_simulation_testcase": self._handle_run_spice_simulation_testcase,
            "run_spice_harness_sanity_check": self._handle_run_spice_harness_sanity_check,
        }

        logger.info("KiCAD interface initialized")

    def handle_command(self, command: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """Route command to appropriate handler"""
        logger.info(f"Handling command: {command}")
        logger.debug(f"Command parameters: {params}")

        try:
            # Get the handler for the command
            handler = self.command_routes.get(command)

            if handler:
                # Execute the command
                result = handler(params)
                logger.debug(f"Command result: {result}")

                # Update board reference if command was successful
                if result.get("success", False):
                    if command == "create_project" or command == "open_project":
                        logger.info("Updating board reference...")
                        # In headless contexts pcbnew.GetBoard() may return None.
                        # Prefer the board instance managed by ProjectCommands.
                        self.board = self.project_commands.board
                        self._update_command_handlers()

                return result
            else:
                logger.error(f"Unknown command: {command}")
                return {
                    "success": False,
                    "message": f"Unknown command: {command}",
                    "errorDetails": "The specified command is not supported"
                }

        except Exception as e:
            # Get the full traceback
            traceback_str = traceback.format_exc()
            logger.error(f"Error handling command {command}: {str(e)}\n{traceback_str}")
            return {
                "success": False,
                "message": f"Error handling command: {command}",
                "errorDetails": f"{str(e)}\n{traceback_str}"
            }

    def _update_command_handlers(self):
        """Update board reference in all command handlers"""
        logger.debug("Updating board reference in command handlers")
        self.project_commands.board = self.board
        self.board_commands.board = self.board
        self.component_commands.board = self.board
        self.routing_commands.board = self.board
        self.design_rule_commands.board = self.board
        self.export_commands.board = self.board

    # Schematic command handlers
    def _handle_create_schematic(self, params):
        """Create a new schematic"""
        logger.info("Creating schematic")
        try:
            project_name = params.get("projectName")
            path = params.get("path", ".")
            metadata = params.get("metadata", {})

            if not project_name:
                return {"success": False, "message": "Project name is required"}

            schematic = SchematicManager.create_schematic(project_name, metadata)
            file_path = f"{path}/{project_name}.kicad_sch"
            success = SchematicManager.save_schematic(schematic, file_path)

            return {"success": success, "file_path": file_path}
        except Exception as e:
            logger.error(f"Error creating schematic: {str(e)}")
            return {"success": False, "message": str(e)}

    def _handle_load_schematic(self, params):
        """Load an existing schematic"""
        logger.info("Loading schematic")
        try:
            filename = params.get("filename")

            if not filename:
                return {"success": False, "message": "Filename is required"}

            schematic = SchematicManager.load_schematic(filename)
            success = schematic is not None

            if success:
                metadata = SchematicManager.get_schematic_metadata(schematic)
                return {"success": success, "metadata": metadata}
            else:
                return {"success": False, "message": "Failed to load schematic"}
        except Exception as e:
            logger.error(f"Error loading schematic: {str(e)}")
            return {"success": False, "message": str(e)}

    def _handle_add_schematic_component(self, params):
        """Add a component to a schematic"""
        logger.info("Adding component to schematic")
        try:
            schematic_path = params.get("schematicPath")
            component = params.get("component", {})

            if not schematic_path:
                return {"success": False, "message": "Schematic path is required"}
            if not component:
                return {"success": False, "message": "Component definition is required"}

            schematic = SchematicManager.load_schematic(schematic_path)
            if not schematic:
                return {"success": False, "message": "Failed to load schematic"}

            try:
                component_obj = ComponentManager.add_component(schematic, component)
            except (ValueError, FileNotFoundError, TypeError) as exc:
                logger.warning(f"Component definition rejected: {exc}")
                return {"success": False, "message": str(exc)}
            except Exception as exc:
                logger.error(f"Unexpected error adding component: {exc}")
                return {"success": False, "message": str(exc)}

            save_ok = SchematicManager.save_schematic(schematic, schematic_path)
            if not save_ok:
                return {"success": False, "message": "Component added but failed to save schematic"}

            at_value = getattr(component_obj, 'at', None)
            position = {}
            if at_value is not None and hasattr(at_value, 'value'):
                coords = at_value.value
                if isinstance(coords, list) and len(coords) >= 2:
                    position = {
                        "x": float(coords[0]),
                        "y": float(coords[1]),
                        "rotation": float(coords[2]) if len(coords) > 2 else 0.0,
                    }

            component_info = {
                "reference": component_obj.property.Reference.value,
                "value": component_obj.property.Value.value if hasattr(component_obj.property, 'Value') else None,
                "libId": component_obj.lib_id.value if hasattr(component_obj, 'lib_id') else None,
                "unit": component_obj.unit.value if hasattr(component_obj, 'unit') else None,
                "footprint": component_obj.property.Footprint.value if hasattr(component_obj.property, 'Footprint') else None,
            }
            if position:
                component_info["position"] = position

            # Extract pin information
            pins = []
            if hasattr(component_obj, 'pin'):
                from python.commands.kicad_schematics.connection_schematic import _iter_symbol_pins, _ensure_pin_metadata, _get_pin_type

                for pin in _iter_symbol_pins(component_obj):
                    pin_number, pin_name = _ensure_pin_metadata(schematic, component_obj, pin)
                    if not pin_number:
                        continue

                    pin_type = _get_pin_type(pin) or 'passive'

                    pins.append({
                        'number': pin_number,
                        'name': pin_name or '',
                        'type': pin_type,
                    })

            component_info["pins"] = pins

            # state_text = get_schematic_state(schematic, output_format="text")
            # return {"success": True, "component": component_info, "CurrentSchematicStates": state_text}
            return {"success": True, "component": component_info}

        except Exception as e:
            logger.error(f"Error adding component to schematic: {str(e)}")
            return {"success": False, "message": str(e)}

    def _handle_update_schematic_component(self, params):
        """Update a component instance in a schematic."""
        logger.info("Updating component in schematic")
        try:
            schematic_path = params.get("schematicPath")
            reference = params.get("reference") or params.get("componentRef")
            updates = params.get("updates") or params.get("fields")
            unit = params.get("unit")

            if not schematic_path:
                return {"success": False, "message": "Schematic path is required"}
            if not reference:
                return {"success": False, "message": "Component reference is required"}
            if not isinstance(updates, dict) or not updates:
                return {"success": False, "message": "updates must be a non-empty object"}

            schematic = SchematicManager.load_schematic(schematic_path)
            if not schematic:
                return {"success": False, "message": "Failed to load schematic"}

            try:
                result = ComponentManager.update_component(
                    schematic,
                    reference,
                    updates,
                    unit=unit,
                )
            except (ValueError, TypeError) as exc:
                logger.warning(f"Component update rejected: {exc}")
                return {"success": False, "message": str(exc)}
            except Exception as exc:
                logger.error(f"Unexpected error updating component: {exc}")
                return {"success": False, "message": str(exc)}

            if not SchematicManager.save_schematic(schematic, schematic_path):
                return {
                    "success": False,
                    "message": "Component updated but failed to save schematic",
                }

            # state_text = get_schematic_state(schematic, output_format="text")
            return {
                "success": True,
                "component": result.get("component"),
                "changedFields": result.get("changedFields", []),
            #     "CurrentSchematicStates": state_text,
            }
        
        except Exception as exc:
            logger.error(f"Error updating component: {exc}")
            return {"success": False, "message": str(exc)}

    def _handle_remove_schematic_component(self, params):
        """Remove component(s) from a schematic."""
        logger.info("Removing component from schematic")
        try:
            schematic_path = params.get("schematicPath")
            reference = params.get("reference") or params.get("componentRef")
            unit = params.get("unit")

            if not schematic_path:
                return {"success": False, "message": "Schematic path is required"}
            if not reference:
                return {"success": False, "message": "Component reference is required"}

            schematic = SchematicManager.load_schematic(schematic_path)
            if not schematic:
                return {"success": False, "message": "Failed to load schematic"}

            try:
                result = ComponentManager.remove_component(
                    schematic,
                    reference,
                    unit=unit,
                )
            except (ValueError, TypeError) as exc:
                logger.warning(f"Component removal rejected: {exc}")
                return {"success": False, "message": str(exc)}
            except Exception as exc:
                logger.error(f"Unexpected error removing component: {exc}")
                return {"success": False, "message": str(exc)}

            if not SchematicManager.save_schematic(schematic, schematic_path):
                return {
                    "success": False,
                    "message": "Component removed but failed to save schematic",
                }

            # Extract the new return format
            removed_components = result.get('removedComponents', [])
            note = result.get('note', '')
            removed_connections = result.get('removedConnections', [])

            # state_text = get_schematic_state(schematic, output_format="text")
            return {
                "success": True,
                "removedComponents": removed_components,
                "note": note,
                "removedConnections": removed_connections,
                # "CurrentSchematicStates": state_text,
            }
        except Exception as exc:
            logger.error(f"Error removing component: {exc}")
            return {"success": False, "message": str(exc)}

    def _handle_add_schematic_wire(self, params):
        """Add a wire to a schematic"""
        logger.info("Adding wire to schematic")
        try:
            schematic_path = params.get("schematicPath")
            start_point = params.get("startPoint")
            end_point = params.get("endPoint")
            points = params.get("points") or params.get("segments") or params.get("pointList")
            midpoints = params.get("midpoints") or params.get("viaPoints")
            width = params.get("width")
            stroke_type = params.get("strokeType") or params.get("style")
            wire_uuid = params.get("uuid")
            wire_options = params.get("wireOptions") or params.get("wire") or {}

            if not schematic_path:
                return {"success": False, "message": "Schematic path is required"}
            points_supplied = bool(points)
            if isinstance(wire_options, dict):
                if any(wire_options.get(key) is not None for key in ('points', 'pointList', 'segments')):
                    points_supplied = True

            if not start_point and not points_supplied:
                return {"success": False, "message": "Start point is required when points are not provided"}
            if not end_point and not points_supplied:
                return {"success": False, "message": "End point is required when points are not provided"}

            schematic = SchematicManager.load_schematic(schematic_path)
            if not schematic:
                return {"success": False, "message": "Failed to load schematic"}

            wire_properties = {}
            if isinstance(wire_options, dict):
                for key in (
                    'points',
                    'pointList',
                    'segments',
                    'midpoints',
                    'viaPoints',
                    'width',
                    'strokeType',
                    'style',
                    'uuid',
                ):
                    if wire_options.get(key) is not None:
                        wire_properties[key] = wire_options[key]

            if points is not None:
                wire_properties['points'] = points
            if midpoints is not None:
                wire_properties['midpoints'] = midpoints
            if width is not None:
                wire_properties['width'] = width
            if stroke_type is not None:
                wire_properties['strokeType'] = stroke_type
            if wire_uuid is not None:
                wire_properties['uuid'] = wire_uuid

            try:
                wire = ConnectionManager.add_wire(
                    schematic,
                    start_point,
                    end_point,
                    properties=wire_properties,
                )
            except (ValueError, TypeError) as exc:
                logger.warning(f"Wire definition rejected: {exc}")
                return {"success": False, "message": str(exc)}
            except Exception as exc:
                logger.error(f"Unexpected error adding wire: {exc}")
                return {"success": False, "message": str(exc)}

            save_ok = SchematicManager.save_schematic(schematic, schematic_path)
            if not save_ok:
                return {"success": False, "message": "Wire added but failed to save schematic"}

            if isinstance(wire, list):
                wires = wire
            else:
                wires = [wire]

            def _wire_dump(wrapper):
                points = [[pt.value[0], pt.value[1]] for pt in wrapper.points]
                return {
                    "uuid": wrapper.uuid.value,
                    "points": points,
                    "width": wrapper.stroke.width.value,
                    "strokeType": wrapper.stroke.type.value,
                    "length": wrapper.length,
                }

            segment_payloads = [_wire_dump(wrapper) for wrapper in wires]
            total_length = sum(segment["length"] for segment in segment_payloads)

            path_points = []
            for idx, payload in enumerate(segment_payloads):
                segment_points = payload["points"]
                if idx == 0:
                    path_points.extend(segment_points)
                else:
                    path_points.extend(segment_points[1:])

            response = {
                "wire": segment_payloads[0],
                "segments": segment_payloads,
                "segmentCount": len(segment_payloads),
                "totalLength": total_length,
                "path": path_points,
            }

            return {"success": True, **response}
        except Exception as e:
            logger.error(f"Error adding wire to schematic: {str(e)}")
            return {"success": False, "message": str(e)}

    def _handle_remove_schematic_connection(self, params):
        """Remove wire(s) connecting two schematic connection points."""
        logger.info("Removing schematic connection")
        try:
            schematic_path = params.get("schematicPath")
            source = params.get("source")
            target = params.get("target")

            if not schematic_path:
                return {"success": False, "message": "Schematic path is required"}

            if not source or not target:
                return {"success": False, "message": "Source and target connection point definitions are required"}

            schematic = SchematicManager.load_schematic(schematic_path)
            if not schematic:
                return {"success": False, "message": "Failed to load schematic"}

            try:
                result = ConnectionManager.remove_connection(schematic, source, target)
            except (ValueError, TypeError) as exc:
                logger.warning(f"Connection removal rejected: {exc}")
                return {"success": False, "message": str(exc)}
            except Exception as exc:
                logger.error(f"Unexpected error removing connection: {exc}")
                return {"success": False, "message": str(exc)}

            if not SchematicManager.save_schematic(schematic, schematic_path):
                return {
                    "success": False,
                    "message": "Connection removed but failed to save schematic",
                }

            # state_text = get_schematic_state(schematic, output_format="text")
            # return {"success": True, **result, "CurrentSchematicStates": state_text}
            return {"success": True, **result}
        
        except Exception as exc:
            logger.error(f"Error removing schematic connection: {exc}")
            return {"success": False, "message": str(exc)}

    def _handle_connect_schematic_pins(self, params):
        """Connect two schematic connection points (pins, labels, power) by drawing wire(s) between them"""
        logger.info("Connecting schematic pins/labels/power")
        try:
            schematic_path = params.get("schematicPath")
            source_pin = params.get("source")
            target_pin = params.get("target")
            wire_options = params.get("wireOptions") or params.get("wire")

            if not schematic_path:
                return {"success": False, "message": "Schematic path is required"}
            if not source_pin or not target_pin:
                return {"success": False, "message": "Source and target connection point definitions are required"}

            schematic = SchematicManager.load_schematic(schematic_path)
            if not schematic:
                return {"success": False, "message": "Failed to load schematic"}

            try:
                result = ConnectionManager.connect_pins(
                    schematic,
                    source_pin,
                    target_pin,
                    wire=wire_options,
                )
            except (ValueError, TypeError) as exc:
                logger.warning(f"Pin connection rejected: {exc}")
                return {"success": False, "message": str(exc)}
            except Exception as exc:
                logger.error(f"Unexpected error connecting pins: {exc}")
                return {"success": False, "message": str(exc)}

            save_ok = SchematicManager.save_schematic(schematic, schematic_path)
            if not save_ok:
                return {"success": False, "message": "Pins connected but failed to save schematic"}

            # state_text = get_schematic_state(schematic, output_format="text")
            # return {"success": True, **result, "CurrentSchematicStates": state_text}
            return {"success": True, **result}
        except Exception as e:
            logger.error(f"Error connecting schematic pins: {str(e)}")
            return {"success": False, "message": str(e)}

    def _handle_compile_schematic(self, params):
        """Compile a schematic by materializing unlabeled nets into explicit net labels."""
        logger.info("Compiling schematic nets into explicit labels")
        try:
            schematic_path = params.get("schematicPath")
            output_path = params.get("outputPath")

            if not schematic_path:
                return {"success": False, "message": "Schematic path is required"}

            schematic = SchematicManager.load_schematic(schematic_path)
            if not schematic:
                return {"success": False, "message": "Failed to load schematic"}

            try:
                compile_result = SchematicCompiler.compile(schematic)
            except Exception as exc:
                logger.error(f"Schematic compilation failed: {exc}")
                return {"success": False, "message": str(exc)}

            if output_path:
                output_path = os.path.abspath(output_path)
            else:
                directory, filename = os.path.split(schematic_path)
                base, ext = os.path.splitext(filename)
                if not ext:
                    ext = ".kicad_sch"
                compiled_name = f"{base}_compiled{ext}"
                output_path = os.path.join(directory, compiled_name)

            output_dir = os.path.dirname(output_path)
            if output_dir and not os.path.exists(output_dir):
                os.makedirs(output_dir, exist_ok=True)

            if not SchematicManager.save_schematic(schematic, output_path):
                return {"success": False, "message": "Failed to save compiled schematic"}

            try:
                library_export = export_project_libraries(schematic, schematic_path)
            except Exception as exc:  # noqa: BLE001
                logger.error("Failed to export project libraries: %s", exc)
                return {"success": False, "message": f"Failed to export project libraries: {exc}"}

            labels_added = compile_result.get("labelsAdded", [])
            response = {
                "success": True,
                "sourcePath": schematic_path,
                "outputPath": output_path,
                "labelsAdded": labels_added,
                "generatedLabelCount": compile_result.get("generatedLabelCount", len(labels_added)),
                "totalNets": compile_result.get("totalNets", 0),
                "skippedExistingLabels": compile_result.get("skippedExistingLabels", 0),
                "libraryExport": library_export,
            }
            return response
        except Exception as exc:
            logger.error(f"Error compiling schematic: {exc}")
            return {"success": False, "message": str(exc)}

    def _handle_run_module_erc(self, params):
        """Run erc test on module level schematic sheet."""
        logger.info("Running module ERC workflow")
        try:
            module_path = params.get("schematicPath")
            if not module_path:
                return {"success": False, "message": "schematicPath is required"}

            module_path = os.path.abspath(os.path.expanduser(module_path))
            if not os.path.exists(module_path):
                return {"success": False, "message": f"Module schematic not found: {module_path}"}

            report_path_param = params.get("reportPath")
            if "extraArgs" in params and params.get("extraArgs"):
                return {
                    "success": False,
                    "message": "extraArgs is not supported for module ERC",
                }

            with tempfile.TemporaryDirectory(prefix="module_erc_") as temp_dir:
                try:
                    artifacts = prepare_module_erc_artifacts(module_path, temp_dir)
                except Exception as exc:
                    logger.error(f"Failed to prepare module ERC artifacts: {exc}")
                    return {"success": False, "message": str(exc)}

                harness_path = artifacts["harnessPath"]
                compiled_path = artifacts["compiledPath"]
                library_export = artifacts.get("libraryExport") or {}
                library_config_dir = (
                    library_export.get("configHome")
                    or library_export.get("baseDir")
                )

                args = ["sch", "erc", harness_path]

                report_path_abs = None
                if report_path_param:
                    report_path_abs = os.path.abspath(os.path.expanduser(report_path_param))
                    report_dir = os.path.dirname(report_path_abs)
                    if report_dir:
                        Path(report_dir).mkdir(parents=True, exist_ok=True)
                    args.extend(["--output", report_path_abs])

                cli_env = None
                if library_config_dir and os.path.isdir(library_config_dir):
                    cli_env = os.environ.copy()
                    cli_env["KICAD_CONFIG_HOME"] = library_config_dir

                try:
                    result, executable = _run_kicad_cli(args, env=cli_env)
                except FileNotFoundError as exc:
                    logger.error(str(exc))
                    return {"success": False, "message": str(exc)}

                success = result.returncode == 0
                message_text = (result.stderr or result.stdout or "").strip()

                report_content = None
                if report_path_abs and os.path.exists(report_path_abs):
                    try:
                        report_content = Path(report_path_abs).read_text(
                            encoding="utf-8", errors="replace"
                        ).strip()
                    except OSError as exc:
                        logger.warning(f"Unable to read ERC report at {report_path_abs}: {exc}")
                    else:
                        if report_content:
                            message_text = (
                                f"{message_text}\n\n{report_content}"
                                if message_text
                                else report_content
                            )

                target_dir_abs = os.path.dirname(module_path)
                shutil.copyfile(compiled_path, Path(target_dir_abs) / Path(compiled_path).name)
                shutil.copyfile(harness_path, Path(target_dir_abs) / Path(harness_path).name)

                return {
                    "success": success,
                    "message": message_text,
                    "stdout": (result.stdout or "").strip(),
                    "reportPath": report_path_abs,
                    "reportContent": report_content,
                    "libraryExport": library_export,
                }
        except Exception as exc:
            logger.error(f"Error running module ERC: {exc}")
            return {"success": False, "message": str(exc)}

    def _handle_run_skidl_erc(self, params):
        """Execute a SKiDL ERC harness script using the KiCAD conda Python interpreter."""
        logger.info("Running SKiDL ERC harness")
        try:
            skidl_path = params.get("skidlPath")
            if not skidl_path:
                return {"success": False, "message": "skidlPath is required"}

            skidl_path = os.path.abspath(os.path.expanduser(skidl_path))
            if not os.path.exists(skidl_path):
                return {"success": False, "message": f"SKiDL file not found: {skidl_path}"}

            skidl_dir = os.path.dirname(skidl_path) or None
            command = [sys.executable, skidl_path]
            logger.debug("Executing SKiDL ERC script: %s", " ".join(command))

            try:
                env = os.environ.copy()
                project_root = str(PROJECT_ROOT)
                existing_pythonpath = env.get("PYTHONPATH")
                if existing_pythonpath:
                    env["PYTHONPATH"] = os.pathsep.join(
                        [project_root, existing_pythonpath]
                    )
                else:
                    env["PYTHONPATH"] = project_root
                env.setdefault("PROJECT_ROOT", project_root)

                symbol_dir_candidates = [
                    env.get("KICAD_SYMBOL_DIR"),
                    str(Path(project_root) / "symbol_lib" / "symbols"),
                    str(Path("/usr/share/kicad") / "symbols"),
                ]
                resolved_symbol_dir = None
                for candidate in symbol_dir_candidates:
                    if candidate and Path(candidate).exists():
                        resolved_symbol_dir = candidate
                        break
                if resolved_symbol_dir:
                    for env_var in [
                        "KICAD_SYMBOL_DIR",
                        "KICAD6_SYMBOL_DIR",
                        "KICAD7_SYMBOL_DIR",
                        "KICAD8_SYMBOL_DIR",
                        "KICAD9_SYMBOL_DIR",
                    ]:
                        env.setdefault(env_var, resolved_symbol_dir)

                result = subprocess.run(
                    command,
                    capture_output=True,
                    text=True,
                    cwd=skidl_dir,
                    env=env,
                    check=False,
                )
            except OSError as exc:
                logger.error("Failed to run SKiDL ERC script: %s", exc)
                return {"success": False, "message": f"Failed to run SKiDL file: {exc}"}

            stdout = (result.stdout or "").strip()
            stderr = (result.stderr or "").strip()
            combined_message = "\n\n".join(filter(None, [stdout, stderr]))
            success = result.returncode == 0
            if not combined_message:
                combined_message = (
                    "SKiDL ERC completed successfully"
                    if success
                    else f"SKiDL ERC exited with code {result.returncode}"
                )

            return {
                "success": success,
                "message": combined_message,
                "stdout": stdout,
                "stderr": stderr,
                "exitCode": result.returncode,
                "command": command,
                "workingDirectory": skidl_dir,
            }
        except Exception as exc:
            logger.error("Error running SKiDL ERC: %s", exc)
            return {"success": False, "message": str(exc)}

    def _handle_list_schematic_libraries(self, params):
        """List available symbol libraries"""
        logger.info("Listing schematic libraries")
        try:
            search_paths = params.get("searchPaths")

            libraries = LibraryManager.list_available_libraries(search_paths)
            return {"success": True, "libraries": libraries}
        except Exception as e:
            logger.error(f"Error listing schematic libraries: {str(e)}")
            return {"success": False, "message": str(e)}

    def _handle_export_schematic_pdf(self, params):
        """Export schematic to PDF"""
        logger.info("Exporting schematic to PDF")
        try:
            schematic_path = params.get("schematicPath")
            output_path = params.get("outputPath")

            if not schematic_path:
                return {"success": False, "message": "Schematic path is required"}
            if not output_path:
                return {"success": False, "message": "Output path is required"}

            import subprocess
            try:
                result, executable = _run_kicad_cli(
                    [
                        "sch",
                        "export",
                        "pdf",
                        schematic_path,
                        "--output",
                        output_path
                    ]
                )
            except FileNotFoundError as exc:
                logger.error(str(exc))
                return {"success": False, "message": str(exc)}

            success = result.returncode == 0
            message = result.stderr.strip() if result.stderr else ""

            return {
                "success": success,
                "message": message,
                "stdout": result.stdout.strip(),
                "executable": executable,
                "outputPath": output_path if success else None
            }
        except Exception as e:
            logger.error(f"Error exporting schematic to PDF: {str(e)}")
            return {"success": False, "message": str(e)}

    def _handle_export_schematic_svg(self, params):
        """Export schematic to SVG"""
        logger.info("Exporting schematic to SVG")
        try:
            schematic_path = params.get("schematicPath")
            output_path = params.get("outputPath")
            extra_args = params.get("extraArgs", [])

            if not schematic_path:
                return {"success": False, "message": "Schematic path is required"}
            if not output_path:
                return {"success": False, "message": "Output path is required"}

            args = [
                "sch",
                "export",
                "svg",
                schematic_path,
                "--output",
                output_path,
            ]
            if isinstance(extra_args, list):
                args.extend(str(arg) for arg in extra_args)
            elif extra_args:
                args.append(str(extra_args))

            try:
                result, executable = _run_kicad_cli(args)
            except FileNotFoundError as exc:
                logger.error(str(exc))
                return {"success": False, "message": str(exc)}

            success = result.returncode == 0
            message = result.stderr.strip() if result.stderr else ""

            return {
                "success": success,
                "message": message,
                "stdout": result.stdout.strip(),
                "executable": executable,
                "outputPath": output_path if success else None,
            }
        except Exception as e:
            logger.error(f"Error exporting schematic to SVG: {str(e)}")
            return {"success": False, "message": str(e)}

    def _handle_run_erc(self, params):
        """Run schematic electrical rules check headlessly."""
        logger.info("Running ERC via kicad-cli")
        try:
            schematic_path = params.get("schematicPath")
            output_path = params.get("reportPath")
            extra_args = params.get("extraArgs", [])

            if not schematic_path:
                return {"success": False, "message": "schematicPath is required"}

            args = ["sch", "erc", schematic_path]
            if output_path:
                output_path = os.path.abspath(os.path.expanduser(output_path))
                output_dir = os.path.dirname(output_path)
                if output_dir:
                    Path(output_dir).mkdir(parents=True, exist_ok=True)
                args.extend(["--output", output_path])
            if isinstance(extra_args, list):
                args.extend(str(arg) for arg in extra_args)

            try:
                result, executable = _run_kicad_cli(args)
            except FileNotFoundError as exc:
                logger.error(str(exc))
                return {"success": False, "message": str(exc)}

            success = result.returncode == 0
            message_text = (result.stderr or result.stdout or "").strip()
            report_content = None
            if output_path and os.path.exists(output_path):
                try:
                    report_content = Path(output_path).read_text(
                        encoding="utf-8", errors="replace"
                    ).strip()
                except OSError as exc:
                    logger.warning(f"Unable to read ERC report at {output_path}: {exc}")
                else:
                    if report_content:
                        message_text = (
                            f"{message_text}\n\n{report_content}"
                            if message_text
                            else report_content
                        )
            return {
                "success": success,
                "message": message_text,
                "stdout": (result.stdout or "").strip(),
                "reportPath": output_path if output_path else None,
                "reportContent": report_content,
                "executable": executable
            }
        except Exception as e:
            logger.error(f"Error running ERC: {str(e)}")
            return {"success": False, "message": str(e)}

    def _handle_export_netlist(self, params):
        """Export schematic netlist headlessly."""
        logger.info("Exporting schematic netlist via kicad-cli")
        try:
            schematic_path = params.get("schematicPath")
            output_path_param = params.get("outputPath")
            netlist_format = params.get("format")

            if not schematic_path:
                return {"success": False, "message": "schematicPath is required"}

            if "extraArgs" in params and params.get("extraArgs"):
                return {
                    "success": False,
                    "message": "extraArgs is not supported for export_schematic_netlist",
                }

            schematic_path = os.path.abspath(os.path.expanduser(schematic_path))
            if not os.path.exists(schematic_path):
                return {
                    "success": False,
                    "message": f"Schematic not found: {schematic_path}",
                }

            schematic_dir = os.path.dirname(schematic_path)
            raw_filename = os.path.basename(schematic_path)
            raw_name, raw_ext = os.path.splitext(raw_filename)
            if not raw_ext:
                raw_ext = ".kicad_sch"

            compiled_path = os.path.join(schematic_dir, f"{raw_name}_compiled{raw_ext}")

            schematic = SchematicManager.load_schematic(schematic_path)
            if schematic is None:
                return {
                    "success": False,
                    "message": f"Failed to load schematic: {schematic_path}",
                }

            try:
                SchematicCompiler.compile(schematic)
            except Exception as exc:
                logger.error(f"Schematic compilation failed: {exc}")
                return {"success": False, "message": str(exc)}

            compiled_dir = os.path.dirname(compiled_path)
            if compiled_dir and not os.path.exists(compiled_dir):
                Path(compiled_dir).mkdir(parents=True, exist_ok=True)

            if not SchematicManager.save_schematic(schematic, compiled_path):
                return {
                    "success": False,
                    "message": f"Failed to save compiled schematic: {compiled_path}",
                }

            if output_path_param:
                output_path = os.path.abspath(os.path.expanduser(output_path_param))
            else:
                output_path = os.path.join(schematic_dir, f"{raw_name}.net")

            output_dir = os.path.dirname(output_path)
            if output_dir and not os.path.exists(output_dir):
                Path(output_dir).mkdir(parents=True, exist_ok=True)

            args = ["sch", "export", "netlist", compiled_path, "--output", output_path]
            if netlist_format:
                args.extend(["--format", netlist_format])

            try:
                result, executable = _run_kicad_cli(args)
            except FileNotFoundError as exc:
                logger.error(str(exc))
                return {"success": False, "message": str(exc)}

            success = result.returncode == 0
            return {
                "success": success,
                "message": result.stderr.strip() if result.stderr else "",
                "stdout": result.stdout.strip(),
                "outputPath": output_path if success else None,
                "executable": executable,
            }
        except Exception as e:
            logger.error(f"Error exporting schematic netlist: {str(e)}")
            return {"success": False, "message": str(e)}

    def _handle_export_schematic_bom(self, params):
        """Export schematic BOM using kicad-cli."""
        logger.info("Exporting schematic BOM via kicad-cli")
        try:
            schematic_path = params.get("schematicPath")
            output_path = params.get("outputPath")
            bom_format = params.get("format")
            template = params.get("template")
            extra_args = params.get("extraArgs", [])

            if not schematic_path:
                return {"success": False, "message": "schematicPath is required"}
            if not output_path:
                return {"success": False, "message": "outputPath is required"}

            args = ["sch", "export", "bom", schematic_path, "--output", output_path]
            if bom_format:
                args.extend(["--format", bom_format])
            if template:
                args.extend(["--template", template])
            args.extend(extra_args)

            try:
                result, executable = _run_kicad_cli(args)
            except FileNotFoundError as exc:
                logger.error(str(exc))
                return {"success": False, "message": str(exc)}

            success = result.returncode == 0
            return {
                "success": success,
                "message": result.stderr.strip() if result.stderr else "",
                "stdout": result.stdout.strip(),
                "outputPath": output_path if success else None,
                "executable": executable
            }
        except Exception as e:
            logger.error(f"Error exporting schematic BOM: {str(e)}")
            return {"success": False, "message": str(e)}

    def _handle_generate_hierarchical_schematic(self, params):
        """Generate a hierarchical KiCAD schematic from a blueprint JSON file."""
        logger.info("Generating hierarchical schematic from blueprint")
        try:
            blueprint_path = params.get("blueprintPath")
            output_dir = params.get("outputDir")

            if not blueprint_path:
                return {"success": False, "message": "blueprintPath is required"}
            if not output_dir:
                return {"success": False, "message": "outputDir is required"}

            # Call the generate_hierarchical_schematic function
            result = generate_hierarchical_schematic(blueprint_path, output_dir)

            return {
                "success": True,
                "message": "Hierarchical schematic generated successfully",
                "result": result
            }
        except FileNotFoundError as e:
            logger.error(f"Blueprint file not found: {str(e)}")
            return {"success": False, "message": f"Blueprint file not found: {str(e)}"}
        except json.JSONDecodeError as e:
            logger.error(f"Invalid blueprint JSON: {str(e)}")
            return {"success": False, "message": f"Invalid blueprint JSON: {str(e)}"}
        except Exception as e:
            logger.error(f"Error generating hierarchical schematic: {str(e)}")
            logger.error(traceback.format_exc())
            return {"success": False, "message": str(e)}

    def _handle_get_schematic_state(self, params):
        """Get high-level schematic state representation (JSON by default)."""
        logger.info("Getting schematic state")
        try:
            schematic_path = params.get("schematicPath")
            show_details = params.get("showDetails", False)
            output_format = str(params.get("outputFormat", "json")).lower()

            if not schematic_path:
                return {"success": False, "message": "schematicPath is required"}

            schematic = SchematicManager.load_schematic(schematic_path)
            if not schematic:
                return {"success": False, "message": "Failed to load schematic"}

            state = get_schematic_state(
                schematic,
                show_details=show_details,
                output_format=output_format,
            )

            return {
                "success": True,
                "format": output_format,
                "state": state,
            }
        except Exception as e:
            logger.error(f"Error getting schematic state: {str(e)}")
            logger.error(traceback.format_exc())
            return {"success": False, "message": str(e)}

    def _handle_run_spice_simulation_testcase(self, params):
        """Execute a SPICE harness use-case against a DUT."""
        logger.info("Running SPICE simulation testcase")
        try:
            schema_path = params.get("schemaPath") or params.get("testbenchPath") or params.get("schema_path")
            harness_path = params.get("harnessPath") or params.get("harness_path")
            use_case_name = params.get("useCaseName") or params.get("use_case_name")
            dut_path = params.get("dutPath") or params.get("dut_path")
            dut_module_name = params.get("dutModuleName") or params.get("dut_module_name")

            missing = [
                name for name, value in (
                    ("schemaPath", schema_path),
                    ("harnessPath", harness_path),
                    ("useCaseName", use_case_name),
                    ("dutPath", dut_path),
                    ("dutModuleName", dut_module_name),
                ) if not value
            ]
            if missing:
                return {"success": False, "message": f"Missing required parameter(s): {', '.join(missing)}"}

            result = run_use_case(
                schema_path=schema_path,
                harness_path=harness_path,
                use_case_name=use_case_name,
                dut_path=dut_path,
                dut_module_name=dut_module_name,
            )
            return {"success": True, "result": result}
        except Exception as exc:
            logger.error(f"Error running SPICE simulation testcase: {exc}")
            logger.error(traceback.format_exc())
            return {"success": False, "message": str(exc)}

    def _handle_run_spice_harness_sanity_check(self, params):
        """Run a transient sanity check on a SPICE harness using a dummy DUT."""
        logger.info("Running SPICE harness sanity check")
        try:
            harness_path = params.get("harnessPath") or params.get("harness_path")
            if not harness_path:
                return {"success": False, "message": "harnessPath is required"}

            use_case_name = params.get("useCaseName") or params.get("use_case_name")
            step_s_raw = params.get("stepS")
            if step_s_raw is None:
                step_s_raw = params.get("step_s")
            end_s_raw = params.get("endS")
            if end_s_raw is None:
                end_s_raw = params.get("end_s")
            temp_c_raw = params.get("tempC")
            if temp_c_raw is None:
                temp_c_raw = params.get("temp_c")
            voltage_limit_raw = params.get("voltageLimit")
            if voltage_limit_raw is None:
                voltage_limit_raw = params.get("voltage_limit")
            current_limit_raw = params.get("currentLimit")
            if current_limit_raw is None:
                current_limit_raw = params.get("current_limit")

            result = harness_sanity_check(
                harness_path=harness_path,
                use_case_name=use_case_name,
                step_s=float(step_s_raw) if step_s_raw is not None else 1e-5,
                end_s=float(end_s_raw) if end_s_raw is not None else 5e-3,
                temp_c=float(temp_c_raw) if temp_c_raw is not None else 25.0,
                voltage_limit=float(voltage_limit_raw) if voltage_limit_raw is not None else 1e3,
                current_limit=float(current_limit_raw) if current_limit_raw is not None else 1e3,
            )
            return {"success": True, "result": result}
        except Exception as exc:
            logger.error(f"Error running SPICE harness sanity check: {exc}")
            logger.error(traceback.format_exc())
            return {"success": False, "message": str(exc)}

def main():
    """Main entry point"""
    logger.info("Starting KiCAD interface...")
    interface = KiCADInterface()

    try:
        logger.info("Processing commands from stdin...")
        # Process commands from stdin
        for line in sys.stdin:
            try:
                # Parse command
                logger.debug(f"Received input: {line.strip()}")
                command_data = json.loads(line)
                command = command_data.get("command")
                params = command_data.get("params", {})

                if not command:
                    logger.error("Missing command field")
                    response = {
                        "success": False,
                        "message": "Missing command",
                        "errorDetails": "The command field is required"
                    }
                else:
                    # Handle command
                    response = interface.handle_command(command, params)

                # Send response
                logger.debug(f"Sending response: {response}")
                _frame = {"type": "response", "payload": response}
                print(json.dumps(_frame, default=str))
                sys.stdout.flush()

            except json.JSONDecodeError as e:
                logger.error(f"Invalid JSON input: {str(e)}")
                response = {
                    "success": False,
                    "message": "Invalid JSON input",
                    "errorDetails": str(e)
                }
                _frame = {"type": "response", "payload": response}
                print(json.dumps(_frame, default=str))
                sys.stdout.flush()

    except KeyboardInterrupt:
        logger.info("KiCAD interface stopped")
        sys.exit(0)

    except Exception as e:
        logger.error(f"Unexpected error: {str(e)}\n{traceback.format_exc()}")
        sys.exit(1)

if __name__ == "__main__":
    main()

"""Generic behavioral validation for SPICE models.

This module provides model-agnostic validation that works without custom harnesses.
It creates a generic test environment, runs DC/transient analysis, and checks
for convergence and basic electrical sanity.

Usage:
    from python.spice_tools.behavioral_validation import validate_model_behavior
    
    problems = validate_model_behavior(
        model_path="path/to/model.spice.lib",
        subckt_name="IP2312-4V35",
    )
    if problems:
        print("Model has issues:", problems)
"""

from __future__ import annotations

import re
import logging
from pathlib import Path
from typing import Any, Dict, List, Sequence

import numpy as np

from python.spice_tools.utils import (
    disable_inspice_cache,
    disable_skidl_file_logging,
    _build_subckt_template,
)

disable_inspice_cache()
disable_skidl_file_logging()

_logger = logging.getLogger(__name__)


def _is_internal_node(node_name: str) -> bool:
    """Check if a node is internal to a subcircuit (skip limit checks)."""
    name = node_name.lower()
    # Internal nodes in subcircuits are prefixed with 'x' (e.g., xdut.n_tmr)
    # We only check interface nodes, not internal behavioral nodes
    return "." in name and name.startswith("x")


def _guess_pin_type(pin_name: str) -> str:
    """Guess pin type from name for generic biasing."""
    name = pin_name.upper()
    
    # Ground pins
    if name in ("GND", "VSS", "AGND", "DGND", "EP", "PAD", "0"):
        return "ground"
    
    # Power input pins
    if any(p in name for p in ("VIN", "VCC", "VDD", "VBUS", "VSUP", "VPWR")):
        return "power_in"
    
    # Power output pins
    if any(p in name for p in ("VOUT", "SW", "LX", "PH")):
        return "power_out"
    
    # Battery/load pins
    if any(p in name for p in ("BAT", "BATT", "CELL", "LOAD")):
        return "battery"
    
    # Feedback/sense pins
    if any(p in name for p in ("FB", "SENSE", "CS", "ISNS")):
        return "feedback"
    
    # Enable/control pins
    if any(p in name for p in ("EN", "CE", "SHDN", "CTRL")):
        return "control"
    
    # LED/indicator pins
    if any(p in name for p in ("LED", "STAT", "CHG", "D1", "D2")):
        return "indicator"
    
    # NTC/temperature pins
    if any(p in name for p in ("NTC", "TEMP", "TS", "THM")):
        return "ntc"
    
    # Programming/config pins
    if any(p in name for p in ("PROG", "ISET", "ICHG", "ILIM", "RSET")):
        return "programming"
    
    # ========== DISCRETE COMPONENTS ==========
    
    # MOSFET gate (G)
    if name == "G" or name.startswith("GATE"):
        return "mosfet_gate"
    
    # MOSFET drain (D)
    if name == "D" or name.startswith("DRAIN"):
        return "mosfet_drain"
    
    # MOSFET source (S, S1, S2, S3, etc.)
    if name == "S" or name.startswith("S") and (len(name) == 1 or name[1:].isdigit()) or name.startswith("SOURCE"):
        return "mosfet_source"
    
    # Diode anode (A)
    if name == "A" or name.startswith("ANODE"):
        return "diode_anode"
    
    # Diode cathode (K)
    if name == "K" or name.startswith("CATHODE"):
        return "diode_cathode"
    
    # BJT base (B)
    if name == "B" or name.startswith("BASE"):
        return "bjt_base"
    
    # BJT collector (C) - be careful not to match capacitor references
    if name == "C" or name.startswith("COLLECTOR"):
        return "bjt_collector"
    
    # BJT emitter (E)
    if name == "E" or name.startswith("EMITTER"):
        return "bjt_emitter"
    
    # Default: unknown
    return "unknown"


def _create_generic_harness(
    subckt_name: str,
    pin_names: List[str],
    power_voltage: float = 5.0,
    battery_voltage: float = 3.7,
) -> tuple:
    """
    Create a generic test harness for any model.
    
    Returns:
        (circuit, pin_nets, ground_net)
    """
    from skidl.pyspice import Circuit, Net, Part, V, R, C
    from skidl import SPICE
    import skidl.part as skidl_part
    
    circuit = Circuit()
    pin_nets: Dict[str, Net] = {}
    
    with circuit:
        # Find ground pin
        gnd_pin = None
        for pin in pin_names:
            if _guess_pin_type(pin) == "ground":
                gnd_pin = pin
                break
        
        if gnd_pin is None:
            # Use last pin as ground (common convention for EP/PAD)
            gnd_pin = pin_names[-1]
        
        # Create ground net
        GND = Net("0")
        pin_nets[gnd_pin] = GND
        
        # Create nets and biasing for each pin
        for pin in pin_names:
            if pin == gnd_pin:
                continue
            
            pin_type = _guess_pin_type(pin)
            net = Net(pin)
            pin_nets[pin] = net
            
            if pin_type == "power_in":
                # Voltage source for power input
                v = V(ref=f"V_{pin}", dc_value=power_voltage)
                v_node = Net(f"{pin}_SRC")
                v_node += v["p"]
                GND += v["n"]
                # Small series resistance
                r = R(ref=f"R_{pin}_SRC", value=0.05)
                v_node += r[1]
                net += r[2]
                
            elif pin_type == "battery":
                # Battery model: voltage source + ESR
                v = V(ref=f"V_{pin}", dc_value=battery_voltage)
                bat_int = Net(f"{pin}_INT")
                bat_int += v["p"]
                GND += v["n"]
                r = R(ref=f"R_{pin}_ESR", value=0.05)
                net += r[1]
                bat_int += r[2]
                
            elif pin_type == "power_out":
                # Load resistor to ground
                r = R(ref=f"R_{pin}_LOAD", value=10.0)
                net += r[1]
                GND += r[2]
                # Bypass cap
                c = C(ref=f"C_{pin}", value=10e-6)
                net += c[1]
                GND += c[2]
                
            elif pin_type == "indicator":
                # Pull-up resistor (LED indicators are typically open-drain)
                r = R(ref=f"R_{pin}_PU", value=1000)
                v_node = Net(f"{pin}_VCC")
                # Connect to power rail if available
                v = V(ref=f"V_{pin}_PU", dc_value=power_voltage)
                v_node += v["p"]
                GND += v["n"]
                v_node += r[1]
                net += r[2]
                
            elif pin_type == "ntc":
                # NTC bias: resistor to ground (sized for typical NTC operation)
                # Most ICs bias NTC with 20-50uA, need R that gives V > threshold
                r = R(ref=f"R_{pin}", value=40000)  # 40k gives ~0.8V with 20uA
                net += r[1]
                GND += r[2]
                
            elif pin_type == "programming":
                # Programming resistor to ground
                r = R(ref=f"R_{pin}", value=45000)  # Typical ICHG resistor
                net += r[1]
                GND += r[2]
                
            elif pin_type == "control":
                # Enable pins: pull high for normal operation
                r = R(ref=f"R_{pin}", value=10000)
                v_node = Net(f"{pin}_VCC")
                v = V(ref=f"V_{pin}", dc_value=power_voltage)
                v_node += v["p"]
                GND += v["n"]
                v_node += r[1]
                net += r[2]
                
            elif pin_type == "feedback":
                # Feedback: resistor divider or just to ground
                r = R(ref=f"R_{pin}", value=100000)
                net += r[1]
                GND += r[2]
            
            # ========== DISCRETE COMPONENTS ==========
            
            elif pin_type == "mosfet_gate":
                # Gate: bias near threshold (0V for NMOS off, -Vth for PMOS off)
                # Use 0V (ground-referenced) for safe default
                r = R(ref=f"R_{pin}_GATE", value=10000)
                net += r[1]
                GND += r[2]
            
            elif pin_type == "mosfet_drain":
                # Drain: power rail through load resistor
                v = V(ref=f"V_{pin}_DRAIN", dc_value=power_voltage)
                v_node = Net(f"{pin}_VDD")
                v_node += v["p"]
                GND += v["n"]
                r = R(ref=f"R_{pin}_LOAD", value=100)
                v_node += r[1]
                net += r[2]
            
            elif pin_type == "mosfet_source":
                # Source: ground reference (for NMOS) or low impedance
                r = R(ref=f"R_{pin}_SRC", value=0.1)
                net += r[1]
                GND += r[2]
            
            elif pin_type == "diode_anode":
                # Anode: current-limited source
                v = V(ref=f"V_{pin}_ANODE", dc_value=power_voltage)
                v_node = Net(f"{pin}_VCC")
                v_node += v["p"]
                GND += v["n"]
                r = R(ref=f"R_{pin}_LIM", value=1000)
                v_node += r[1]
                net += r[2]
            
            elif pin_type == "diode_cathode":
                # Cathode: ground reference
                r = R(ref=f"R_{pin}_K", value=0.1)
                net += r[1]
                GND += r[2]
            
            elif pin_type == "bjt_base":
                # Base: current-limited bias
                r = R(ref=f"R_{pin}_BASE", value=10000)
                net += r[1]
                GND += r[2]
            
            elif pin_type == "bjt_collector":
                # Collector: power through load
                v = V(ref=f"V_{pin}_VCC", dc_value=power_voltage)
                v_node = Net(f"{pin}_VCC")
                v_node += v["p"]
                GND += v["n"]
                r = R(ref=f"R_{pin}_LOAD", value=1000)
                v_node += r[1]
                net += r[2]
            
            elif pin_type == "bjt_emitter":
                # Emitter: ground or small resistor
                r = R(ref=f"R_{pin}_E", value=10)
                net += r[1]
                GND += r[2]
                
            else:
                # Unknown: shunt to ground with high-value resistor
                r = R(ref=f"R_{pin}_SHUNT", value=1e6)
                net += r[1]
                GND += r[2]
            
            # Add small bypass cap to all non-ground pins for transient stability
            c = C(ref=f"C_{pin}_BYPASS", value=1e-12)
            net += c[1]
            GND += c[2]
    
    return circuit, pin_nets, GND


def validate_model_behavior(
    model_path: str | Path,
    subckt_name: str,
    power_voltage: float = 5.0,
    battery_voltage: float = 3.7,
    dc_timeout_s: float = 10.0,
    transient_step_s: float = 1e-5,
    transient_end_s: float = 1e-3,
    voltage_limit: float = 100.0,
    current_limit: float = 100.0,
) -> List[str]:
    """
    Validate a SPICE model's behavioral correctness using generic test conditions.
    
    This creates a generic harness based on pin names, runs DC and transient
    analysis, and checks for convergence and electrical sanity.
    
    Args:
        model_path: Path to the SPICE model file
        subckt_name: Name of the subcircuit to validate
        power_voltage: Voltage to apply to power input pins (default 5V)
        battery_voltage: Voltage for battery/load pins (default 3.7V)
        dc_timeout_s: Timeout for DC operating point (unused, for future)
        transient_step_s: Time step for transient simulation
        transient_end_s: End time for transient simulation
        voltage_limit: Max allowed voltage before flagging as issue
        current_limit: Max allowed current before flagging as issue
    
    Returns:
        List of problems found. Empty list means validation passed.
    """
    problems: List[str] = []
    path = Path(model_path)
    
    if not path.exists():
        return [f"Model file not found: {path}"]
    
    # Parse the library to get subcircuit info
    try:
        from skidl.pyspice import SpiceLibrary
        lib = SpiceLibrary(str(path), scan=True)
    except Exception as e:
        return [f"Failed to parse library: {e}"]
    
    subckts = list(getattr(lib, "subcircuits", []) or [])
    if subckt_name not in subckts:
        available = ", ".join(sorted(subckts)) if subckts else "none"
        return [f"Subcircuit {subckt_name!r} not found. Available: {available}"]
    
    # Get pin names from the subcircuit
    try:
        lib_entry = lib[subckt_name]
        pin_names = getattr(lib_entry, "pin_names", None) or []
        if not pin_names:
            nodes = getattr(lib_entry, "_nodes", None) or []
            pin_names = [getattr(n, "name", str(i+1)) for i, n in enumerate(nodes)]
    except Exception as e:
        return [f"Failed to get subcircuit info: {e}"]
    
    if not pin_names:
        return [f"No pins found for subcircuit {subckt_name}"]
    
    _logger.info(f"Validating {subckt_name} with pins: {pin_names}")
    
    # Create generic harness
    try:
        from skidl.pyspice import Circuit, Net
        from skidl import SPICE
        import skidl.part as skidl_part
        
        circuit, pin_nets, GND = _create_generic_harness(
            subckt_name, pin_names, power_voltage, battery_voltage
        )
        
        # Instantiate the model under test
        with circuit:
            template = _build_subckt_template(lib, subckt_name, lib_entry)
            part = template.copy(dest=skidl_part.NETLIST, circuit=circuit, ref="XDUT")
            
            # Connect pins by position (SPICE convention)
            for idx, pin in enumerate(part.pins):
                pin_name = pin_names[idx] if idx < len(pin_names) else f"PIN{idx+1}"
                if pin_name in pin_nets:
                    pin_nets[pin_name] += pin
                else:
                    # Fallback: connect to ground through high-R
                    from skidl.pyspice import R
                    r = R(ref=f"R_FALLBACK_{idx}", value=1e9)
                    net = Net(f"FALLBACK_{idx}")
                    net += r[1], pin
                    GND += r[2]
    
    except Exception as e:
        return [f"Failed to create test harness: {e}"]
    
    # Run DC operating point analysis with timeout
    dc_ok = False
    try:
        import signal
        import sys
        import io
        from InSpice.Spice.Simulator import Simulator
        from skidl.tools.spice.spice import gen_netlist
        
        def _timeout_handler(signum, frame):
            raise TimeoutError("DC operating point timed out")
        
        netlist = gen_netlist(circuit, title=f"validate_{subckt_name}_dc")
        sim = Simulator.factory().simulation(netlist, temperature=25)
        
        # Capture stderr to get ngspice error messages
        old_stderr = sys.stderr
        sys.stderr = captured_stderr = io.StringIO()
        
        # Set timeout (10 seconds for DC analysis)
        old_handler = signal.signal(signal.SIGALRM, _timeout_handler)
        signal.alarm(10)
        try:
            dc_analysis = sim.operating_point()
            dc_ok = True
        finally:
            signal.alarm(0)
            signal.signal(signal.SIGALRM, old_handler)
            sys.stderr = old_stderr
            
        # Check if there were any errors in stderr
        stderr_output = captured_stderr.getvalue()
        if "error" in stderr_output.lower():
            for line in stderr_output.strip().split('\n'):
                if line.strip():
                    problems.append(f"Simulator: {line.strip()}")
        
        # Check for reasonable values (skip internal subcircuit nodes)
        for node in dc_analysis.nodes:
            if _is_internal_node(node):
                continue
            try:
                val = float(dc_analysis[node])
                if abs(val) > voltage_limit:
                    problems.append(f"DC: Node {node} has excessive voltage: {val:.2f}V")
            except:
                pass
        
        for branch in dc_analysis.branches:
            try:
                val = float(dc_analysis[branch])
                if abs(val) > current_limit:
                    problems.append(f"DC: Branch {branch} has excessive current: {val:.2f}A")
            except:
                pass
                
    except TimeoutError:
        problems.append("DC: Operating point timed out (>10s) - model may have convergence issues")
    except Exception as e:
        problems.append(f"DC operating point failed: {e}")
    
    # Run short transient analysis with timeout
    if dc_ok:
        try:
            import signal
            from python.spice_tools.utils import run_transient
            
            def _tran_timeout_handler(signum, frame):
                raise TimeoutError("Transient simulation timed out")
            
            # Set timeout (10 seconds for transient)
            old_handler = signal.signal(signal.SIGALRM, _tran_timeout_handler)
            signal.alarm(10)
            try:
                analysis = run_transient(
                    circuit=circuit,
                    title=f"validate_{subckt_name}_tran",
                    step_s=transient_step_s,
                    end_s=transient_end_s,
                    temp_c=25.0,
                )
            finally:
                signal.alarm(0)
                signal.signal(signal.SIGALRM, old_handler)
            
            times = np.array(analysis.time)
            
            # Check simulation completed
            if len(times) < 10:
                problems.append(f"Transient: Too few time points ({len(times)})")
            
            # Check for NaN/Inf in results (skip internal subcircuit nodes)
            for node in analysis.nodes:
                if _is_internal_node(node):
                    continue
                try:
                    vals = np.array(analysis[node])
                    if not np.isfinite(vals).all():
                        problems.append(f"Transient: Node {node} has NaN/Inf values")
                    if np.max(np.abs(vals)) > voltage_limit:
                        problems.append(f"Transient: Node {node} exceeds voltage limit")
                except:
                    pass
            
            for branch in analysis.branches:
                try:
                    vals = np.array(analysis[branch])
                    if not np.isfinite(vals).all():
                        problems.append(f"Transient: Branch {branch} has NaN/Inf values")
                    if np.max(np.abs(vals)) > current_limit:
                        problems.append(f"Transient: Branch {branch} exceeds current limit")
                except:
                    pass
                    
        except TimeoutError:
            problems.append("Transient: Simulation timed out (>10s) - model may have convergence issues")
        except Exception as e:
            err_str = str(e).lower()
            if "timestep too small" in err_str:
                problems.append("Transient: Simulation failed with 'timestep too small' - model has convergence issues")
            elif "singular matrix" in err_str:
                problems.append("Transient: Simulation failed with 'singular matrix' - model has topology issues")
            else:
                problems.append(f"Transient simulation failed: {e}")
    
    return problems


__all__ = ["validate_model_behavior", "_guess_pin_type"]

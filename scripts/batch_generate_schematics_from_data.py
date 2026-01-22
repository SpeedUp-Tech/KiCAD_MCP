from __future__ import annotations

import argparse
import ast
import json
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


@dataclass(frozen=True)
class ExportSettings:
    export_svg: bool = True
    verify: bool = True
    auto_cut_problem_nets: bool = True
    auto_cut_strategy: str = "edge"
    auto_cut_iterations: int = 2
    auto_cut_max_nets: int = 8
    auto_cut_min_crossings: int = 1
    auto_cut_min_max_length_mm: float | None = None
    auto_cut_min_max_backtrack_mm: float | None = None


@dataclass(frozen=True)
class Case:
    task_id: str
    run_id: str
    module_id: str
    subcircuit_name: str
    skidl_module_path: str
    output_schematic_path: str
    note: str | None = None


@dataclass
class CaseResult:
    task_id: str
    run_id: str
    module_id: str
    subcircuit_name: str
    skidl_module_path: str
    output_schematic_path: str
    status: str
    success: bool
    verification_passed: bool | None = None
    svg_path: str | None = None
    message: str | None = None
    traceback: str | None = None
    duration_s: float = 0.0
    note: str | None = None


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return path.read_text(encoding="utf-8", errors="replace")


def find_subcircuit_names(module_path: Path) -> list[str]:
    source = _read_text(module_path)
    try:
        tree = ast.parse(source, filename=str(module_path))
    except SyntaxError:
        return []

    names: list[str] = []
    for node in tree.body:
        if not isinstance(node, ast.FunctionDef):
            continue
        for decorator in node.decorator_list:
            if isinstance(decorator, ast.Name) and decorator.id == "SubCircuit":
                names.append(node.name)
                break
            if isinstance(decorator, ast.Attribute) and decorator.attr == "SubCircuit":
                names.append(node.name)
                break
            if isinstance(decorator, ast.Call):
                func = decorator.func
                if isinstance(func, ast.Name) and func.id == "SubCircuit":
                    names.append(node.name)
                    break
                if isinstance(func, ast.Attribute) and func.attr == "SubCircuit":
                    names.append(node.name)
                    break
    return names


def infer_subcircuit_name(module_path: Path, module_id: str) -> tuple[str, str | None]:
    discovered = find_subcircuit_names(module_path)
    if module_id in discovered:
        return module_id, None
    if len(discovered) == 1:
        return discovered[0], f"Using only discovered SubCircuit '{discovered[0]}'"
    if len(discovered) > 1:
        return discovered[0], f"Multiple SubCircuits found; using '{discovered[0]}'"
    return module_id, "No SubCircuit decorator found; using file stem"


def discover_cases(data_dir: Path, output_dir: Path) -> list[Case]:
    tasks_root = data_dir / "tasks" if (data_dir / "tasks").is_dir() else data_dir
    cases: list[Case] = []

    if not tasks_root.is_dir():
        return cases

    for task_dir in sorted([p for p in tasks_root.iterdir() if p.is_dir()], key=lambda p: p.name):
        task_id = task_dir.name
        runs_dir = task_dir / "runs"
        if not runs_dir.is_dir():
            continue

        for run_dir in sorted([p for p in runs_dir.iterdir() if p.is_dir()], key=lambda p: p.name):
            run_id = run_dir.name
            modules_dir = run_dir / "skidl" / "modules"
            if not modules_dir.is_dir():
                continue

            for module_file in sorted(modules_dir.rglob("*.py"), key=lambda p: p.as_posix()):
                if "__pycache__" in module_file.parts:
                    continue
                if module_file.name.startswith("__"):
                    continue

                module_id = module_file.stem.upper()
                subcircuit_name, note = infer_subcircuit_name(module_file, module_id)
                out_sch = (
                    output_dir
                    / task_id
                    / "runs"
                    / run_id
                    / "kicad"
                    / "modules"
                    / f"{module_id}.kicad_sch"
                )
                cases.append(
                    Case(
                        task_id=task_id,
                        run_id=run_id,
                        module_id=module_id,
                        subcircuit_name=subcircuit_name,
                        skidl_module_path=str(module_file.resolve()),
                        output_schematic_path=str(out_sch.resolve()),
                        note=note,
                    )
                )

    return cases


def _classify_failure(message: str, *, export_svg: bool) -> str:
    msg = (message or "").lower()
    if export_svg and ("svg" in msg or "kicad-cli" in msg):
        return "Export failed"
    return "Generate failed"


def _process_case(case: Case, settings: ExportSettings) -> CaseResult:
    start = time.time()
    try:
        from python.netlist_schematic_pipeline import generate_schematic_from_skidl_module

        result: dict[str, Any] = generate_schematic_from_skidl_module(
            skidl_module_path=case.skidl_module_path,
            subcircuit_name=case.subcircuit_name,
            output_path=case.output_schematic_path,
            export_svg=settings.export_svg,
            verify=settings.verify,
            auto_cut_problem_nets=settings.auto_cut_problem_nets,
            auto_cut_strategy=settings.auto_cut_strategy,
            auto_cut_iterations=settings.auto_cut_iterations,
            auto_cut_max_nets=settings.auto_cut_max_nets,
            auto_cut_min_crossings=settings.auto_cut_min_crossings,
            auto_cut_min_max_length_mm=settings.auto_cut_min_max_length_mm,
            auto_cut_min_max_backtrack_mm=settings.auto_cut_min_max_backtrack_mm,
        )

        success = bool(result.get("success", False))
        verification_passed = None
        if settings.verify:
            verification_passed = bool(result.get("verification_passed", False))

        if success:
            if settings.verify and verification_passed is False:
                status = "Verification failed"
            else:
                status = "Good"
        else:
            status = _classify_failure(str(result.get("message", "")), export_svg=settings.export_svg)

        duration_s = time.time() - start
        return CaseResult(
            task_id=case.task_id,
            run_id=case.run_id,
            module_id=case.module_id,
            subcircuit_name=case.subcircuit_name,
            skidl_module_path=case.skidl_module_path,
            output_schematic_path=case.output_schematic_path,
            status=status,
            success=success,
            verification_passed=verification_passed,
            svg_path=result.get("svg_path"),
            message=result.get("message") or result.get("verification_warning"),
            traceback=result.get("traceback"),
            duration_s=duration_s,
            note=case.note,
        )
    except Exception as exc:
        import traceback

        duration_s = time.time() - start
        return CaseResult(
            task_id=case.task_id,
            run_id=case.run_id,
            module_id=case.module_id,
            subcircuit_name=case.subcircuit_name,
            skidl_module_path=case.skidl_module_path,
            output_schematic_path=case.output_schematic_path,
            status="Generate failed",
            success=False,
            message=str(exc),
            traceback=traceback.format_exc(),
            duration_s=duration_s,
            note=case.note,
        )


def _group_results(results: list[CaseResult]) -> dict[str, dict[str, list[CaseResult]]]:
    grouped: dict[str, dict[str, list[CaseResult]]] = {}
    for res in results:
        grouped.setdefault(res.task_id, {}).setdefault(res.run_id, []).append(res)
    for task_id in grouped:
        for run_id in grouped[task_id]:
            grouped[task_id][run_id].sort(key=lambda r: r.module_id)
    return grouped


def _is_skipped(status: str) -> bool:
    return status.startswith("Skipped")


def _compute_stats(
    results: list[CaseResult],
    grouped: dict[str, dict[str, list[CaseResult]]],
    *,
    settings: ExportSettings,
) -> dict[str, int]:
    total_cases = len(results)
    total_cases_verified_ok = sum(1 for r in results if r.verification_passed is True) if settings.verify else 0
    total_cases_failed_generate = sum(1 for r in results if (r.success is False) and not _is_skipped(r.status))
    total_cases_verification_failed = sum(1 for r in results if r.status == "Verification failed") if settings.verify else 0

    tasks_total = len(grouped)
    tasks_completed = 0
    for runs in grouped.values():
        task_results = [r for run in runs.values() for r in run]
        if task_results and all(r.status == "Good" for r in task_results):
            tasks_completed += 1

    return {
        "tasks_total": tasks_total,
        "tasks_completed": tasks_completed,
        "total_cases_processed": total_cases,
        "total_cases_verified_ok": total_cases_verified_ok,
        "total_cases_failed_generate": total_cases_failed_generate,
        "total_cases_verification_failed": total_cases_verification_failed,
    }


def _render_text_report(
    grouped: dict[str, dict[str, list[CaseResult]]],
    *,
    data_dir: Path,
    output_dir: Path,
    max_processes: int,
    settings: ExportSettings,
) -> str:
    all_results = [r for task in grouped.values() for run in task.values() for r in run]
    stats = _compute_stats(all_results, grouped, settings=settings)

    lines: list[str] = []
    lines.append("Batch schematic export report")
    lines.append(f"Data dir: {data_dir}")
    lines.append(f"Output dir: {output_dir}")
    lines.append(f"Max processes: {max_processes}")
    lines.append(
        "Settings: "
        + ", ".join(
            [
                f"export_svg={settings.export_svg}",
                f"verify={settings.verify}",
                f"auto_cut_problem_nets={settings.auto_cut_problem_nets}",
                f"auto_cut_strategy={settings.auto_cut_strategy}",
                f"auto_cut_iterations={settings.auto_cut_iterations}",
                f"auto_cut_max_nets={settings.auto_cut_max_nets}",
                f"auto_cut_min_crossings={settings.auto_cut_min_crossings}",
            ]
        )
    )
    lines.append("")
    lines.append("Summary")
    lines.append(f"- Tasks completed: {stats['tasks_completed']}/{stats['tasks_total']}")
    lines.append(f"- Total cases processed: {stats['total_cases_processed']}")
    lines.append(f"- Total cases succeed with verified=True: {stats['total_cases_verified_ok']}")
    lines.append(f"- Total cases failed to generate a schematic: {stats['total_cases_failed_generate']}")
    if settings.verify:
        lines.append(f"- Total cases verification failed: {stats['total_cases_verification_failed']}")
    lines.append("")
    lines.append("Per-task results")
    for task_id in sorted(grouped.keys()):
        lines.append(f"task_id: {task_id}")
        for run_id in sorted(grouped[task_id].keys()):
            lines.append(f"  run_id: {run_id}")
            for res in grouped[task_id][run_id]:
                lines.append(f"    {res.module_id}: {res.status}")
    lines.append("")
    return "\n".join(lines)


def _write_report_files(output_dir: Path, report: dict[str, Any], text_report: str) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "schematic_export_report.json"
    txt_path = output_dir / "schematic_export_report.txt"
    json_path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    txt_path.write_text(text_report, encoding="utf-8")
    return json_path, txt_path


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Batch-generate KiCad schematics (.kicad_sch + SVG) from SKiDL modules under a data/ tree.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Example:\n"
            "  conda run -n kicad python scripts/batch_generate_schematics_from_data.py \\\n"
            "    --data-dir data --output-dir exported/batch_schematics --max-processes 4\n"
        ),
    )
    parser.add_argument(
        "--data-dir",
        default=str(ROOT / "data"),
        help="Path to data root (default: ./data). If it contains a tasks/ folder, that is used as the tasks root.",
    )
    parser.add_argument(
        "--output-dir",
        required=True,
        help="Directory to write exported schematics into (mirrors task_id/runs/run_id/kicad/modules/...).",
    )
    parser.add_argument(
        "--max-processes",
        type=int,
        default=max(1, (os.cpu_count() or 1) // 2),
        help="Max parallel processes (default: half of CPU count).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Only scan and write a report; do not generate schematics.",
    )

    parser.add_argument("--export-svg", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--verify", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--auto-cut-problem-nets", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--auto-cut-strategy", choices=["net", "edge"], default="edge")
    parser.add_argument("--auto-cut-iterations", type=int, default=2)
    parser.add_argument("--auto-cut-max-nets", type=int, default=8)
    parser.add_argument("--auto-cut-min-crossings", type=int, default=1)
    parser.add_argument("--auto-cut-min-max-length-mm", type=float, default=None)
    parser.add_argument("--auto-cut-min-max-backtrack-mm", type=float, default=None)

    args = parser.parse_args()

    if os.environ.get("CONDA_DEFAULT_ENV") != "kicad":
        print(
            f"Warning: CONDA_DEFAULT_ENV={os.environ.get('CONDA_DEFAULT_ENV')!r}; expected 'kicad'.",
            file=sys.stderr,
        )

    data_dir = Path(args.data_dir).expanduser().resolve()
    output_dir = Path(args.output_dir).expanduser().resolve()
    max_processes = max(1, int(args.max_processes))

    settings = ExportSettings(
        export_svg=bool(args.export_svg),
        verify=bool(args.verify),
        auto_cut_problem_nets=bool(args.auto_cut_problem_nets),
        auto_cut_strategy=str(args.auto_cut_strategy),
        auto_cut_iterations=int(args.auto_cut_iterations),
        auto_cut_max_nets=int(args.auto_cut_max_nets),
        auto_cut_min_crossings=int(args.auto_cut_min_crossings),
        auto_cut_min_max_length_mm=args.auto_cut_min_max_length_mm,
        auto_cut_min_max_backtrack_mm=args.auto_cut_min_max_backtrack_mm,
    )

    cases = discover_cases(data_dir, output_dir)
    if not cases:
        report = {
            "success": True,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "data_dir": str(data_dir),
            "output_dir": str(output_dir),
            "max_processes": max_processes,
            "settings": asdict(settings),
            "stats": {
                "tasks_total": 0,
                "tasks_completed": 0,
                "total_cases_processed": 0,
                "total_cases_verified_ok": 0,
                "total_cases_failed_generate": 0,
            },
            "results": [],
        }
        grouped: dict[str, dict[str, list[CaseResult]]] = {}
        text_report = _render_text_report(
            grouped,
            data_dir=data_dir,
            output_dir=output_dir,
            max_processes=max_processes,
            settings=settings,
        )
        json_path, txt_path = _write_report_files(output_dir, report, text_report)
        print(f"No cases found. Wrote reports: {json_path} {txt_path}")
        return 0

    results: list[CaseResult] = []
    if args.dry_run:
        results = [
            CaseResult(
                task_id=c.task_id,
                run_id=c.run_id,
                module_id=c.module_id,
                subcircuit_name=c.subcircuit_name,
                skidl_module_path=c.skidl_module_path,
                output_schematic_path=c.output_schematic_path,
                status="Skipped (dry-run)",
                success=False,
                note=c.note,
            )
            for c in cases
        ]
    else:
        with ProcessPoolExecutor(max_workers=max_processes) as executor:
            futures = {executor.submit(_process_case, case, settings): case for case in cases}
            total = len(futures)
            completed = 0
            for future in as_completed(futures):
                completed += 1
                res = future.result()
                results.append(res)
                print(
                    f"[{completed}/{total}] {res.task_id}/{res.run_id} {res.module_id}: {res.status}"
                )

    grouped = _group_results(results)

    stats = _compute_stats(results, grouped, settings=settings)

    report = {
        "success": True,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "data_dir": str(data_dir),
        "output_dir": str(output_dir),
        "max_processes": max_processes,
        "settings": asdict(settings),
        "stats": stats,
        "results": [asdict(r) for r in sorted(results, key=lambda r: (r.task_id, r.run_id, r.module_id))],
    }

    text_report = _render_text_report(
        grouped,
        data_dir=data_dir,
        output_dir=output_dir,
        max_processes=max_processes,
        settings=settings,
    )
    json_path, txt_path = _write_report_files(output_dir, report, text_report)
    print(f"Wrote reports: {json_path} {txt_path}")

    ok = stats["total_cases_failed_generate"] == 0 and (
        stats["total_cases_verification_failed"] == 0 if settings.verify else True
    )
    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())

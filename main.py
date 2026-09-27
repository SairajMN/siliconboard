"""CLI entry point. doctor first: nothing runs until the toolchain and keys are real."""

from __future__ import annotations

import argparse
import difflib
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

import llm
from agents.report import ReportAgent
from agents.simrunner import SimRunnerAgent
from agents.spec import SpecAgent  # noqa: F401  kept: doctor and docs reference the first agent
from board import Board, DesignStatus
from orchestrator import STOP, Orchestrator
from tools import IMAGE, draw_vcd, human_time, read_vcd, run_eda

ROOT = Path(__file__).resolve().parent


def mask(key: str) -> str:
    return f"{key[:4]}...{key[-2:]}" if len(key) > 8 else "(short)"


def docker_problem() -> str | None:
    probe = subprocess.run(["docker", "info"], capture_output=True, text=True)
    if probe.returncode != 0:
        return "daemon not running: start Docker Desktop"
    listed = subprocess.run(["docker", "images", "-q", IMAGE], capture_output=True, text=True)
    if listed.returncode != 0 or not listed.stdout.strip():
        return f"{IMAGE} missing: docker build -t {IMAGE} docker/eda"
    return None


def doctor() -> int:
    print(f"python   {platform.python_version()} on {platform.machine()}")

    total = 0
    # derived from the chains so a new provider can never be added without doctor knowing
    providers = list(dict.fromkeys(
        route.partition(":")[0] for route in llm.HEAVY + llm.LIGHT
    ))
    for provider in providers:
        found = llm.keys(provider)
        total += len(found)
        for slot, key in found:
            print(f"key {provider}:{slot}  {mask(key)} ({len(key)} chars)")
        if not found:
            print(f"key {provider}:  none (fallback chain skips it)")
    if not total:
        names = " / ".join(f"{p.upper()}_API_KEY" for p in providers)
        print(f"keys     none found: put {names} in .env")

    problem = docker_problem()
    if problem is not None:
        print(f"docker   {problem}")
        return 1

    rc, out = run_eda(
        ["bash", "-c", "yosys -V | head -n 1; verilator --version | head -n 1; iverilog -V 2>&1 | head -n 1"],
        ROOT,
    )
    if rc != 0:
        print(f"tools    exited {rc}: {out.strip()}")
        return 1
    for line in out.strip().splitlines():
        if line.strip():
            print(f"tool     {line.strip()}")

    print(f"\ndoctor: ready{'' if total else ' (except keys)'}")
    return 0


def _report(board: Board, run_dir: Path) -> None:
    for line in board.event_log:
        print(f"event   {line}")
    for call in board.llm_calls:
        print(f"llm     {call.role} {call.model} slot={call.key_slot} in={call.tokens_in} out={call.tokens_out} cached={call.cached}")
    print(f"status  {board.status.value}")
    print(f"board   {run_dir / 'board.json'}")


def acquire_lock(run_dir: Path) -> bool:
    """Two runs writing one run dir interleave their event logs; the audit trail must reject that."""
    run_dir.mkdir(parents=True, exist_ok=True)
    lock = run_dir / "run.lock"
    if lock.exists():
        holder = lock.read_text().strip()
        if holder.isdigit() and Path(f"/proc/{holder}").exists():
            print(f"refusing: pid {holder} is already writing {run_dir}")
            return False
        # a lock from a machine with no /proc, or from a dead process, is stale
        alive = subprocess.run(["ps", "-p", holder], capture_output=True, text=True).returncode == 0
        if holder.isdigit() and alive:
            print(f"refusing: pid {holder} is already writing {run_dir}")
            return False
    lock.write_text(str(os.getpid()))
    return True


def run_pipeline(spec_file: Path, run_id: str, stop_after: str | None, inject: str | None = None,
                 cross_sim: bool = False, waves: bool = False) -> int:
    run_dir = ROOT / "runs" / run_id
    if not acquire_lock(run_dir):
        return 1
    llm.set_cache_dir(run_dir / "llm")
    # both are opt-in evidence modes, so they stay off for ordinary runs
    SimRunnerAgent.cross_sim = cross_sim
    SimRunnerAgent.waves = waves

    if (run_dir / "board.json").exists():
        board = Board.load(run_dir)
        print(f"resumed {run_dir / 'board.json'} at {board.status.value}")
    else:
        board = Board(request=spec_file.read_text())

    result = Orchestrator(board, run_dir, inject=inject).run(stop_after)

    # narration runs only on a finished design, and its failure never reverses a tool verdict
    if board.finished and board.status != DesignStatus.FAILED and board.run_report is None:
        narrator = ReportAgent()
        narrator.run_dir = run_dir
        narrated = narrator.run(board)
        if not narrated.ok:
            print(f"report  failed: {narrated.err}")
    board.save(run_dir)

    if board.spec:
        print(f"module  {board.spec.name}, {len(board.spec.io_ports)} ports")
    if board.latest_rtl:
        print(f"rtl     v{board.latest_rtl.version}, {len(board.latest_rtl.source_code.splitlines())} lines")
    if board.synthesis_report and board.synthesis_report.cell_count is not None:
        print(f"synth   {board.synthesis_report.cell_count} cells")
    if board.run_report is not None:
        print(f"report  {run_dir / 'report.md'}")
    if result is not None and not result.ok:
        print(f"failed  {result.err}")
    _report(board, run_dir)

    stopped_cleanly = result is not None and result.ok
    finished_cleanly = result is None and board.finished and board.status != DesignStatus.FAILED
    return 0 if stopped_cleanly or finished_cleanly else 1


def run_spec(request_file: Path, run_id: str) -> int:
    return run_pipeline(request_file, run_id, "spec")


def run_diff(run_id: str) -> int:
    run_dir = ROOT / "runs" / run_id
    if not (run_dir / "board.json").exists():
        print(f"no board.json in {run_dir}")
        return 1
    board = Board.load(run_dir)
    if len(board.rtl_history) < 2:
        print(f"only {len(board.rtl_history)} RTL version(s), nothing to diff")
        return 1
    prev, current = board.rtl_history[-2], board.rtl_history[-1]
    lines = list(
        difflib.unified_diff(
            prev.source_code.splitlines(),
            current.source_code.splitlines(),
            fromfile=f"{prev.filename} v{prev.version}",
            tofile=f"{current.filename} v{current.version}",
            lineterm="",
        )
    )
    print("\n".join(lines))
    changed = sum(1 for ln in lines if ln.startswith(("+", "-")) and not ln.startswith(("+++", "---")))
    print(f"\n{changed} changed lines (v{prev.version} -> v{current.version})")
    for bug in board.bug_history:
        print(f"bug: {bug.failing_check} — {bug.summary}")
        print(f"evidence: {bug.evidence_quote}")
    return 0


def run_replay(run_id: str) -> int:
    """Re-render a finished run from its board.json. No LLM, no EDA tool, no network."""
    run_dir = ROOT / "runs" / run_id
    if not (run_dir / "board.json").exists():
        print(f"no board.json in {run_dir}")
        return 1
    board = Board.load(run_dir)

    if board.run_report is None:
        print("no stored report; this run never reached narration")
        return 1

    (run_dir / "report.md").write_text(board.run_report.markdown_summary.rstrip() + "\n")
    print(f"replayed {run_id} from board.json, {len(board.llm_calls)} recorded llm calls, none made now")
    _report(board, run_dir)
    return 0


def wave_viewer() -> str | None:
    for name in ("surfer", "gtkwave", "vcd2fst"):
        if shutil.which(name):
            return name
    return None


NO_VIEWER = (
    "\nno waveform viewer installed, so --draw is the only picture."
    "\nfor a real GUI one, surfer (github.com/thesurver/surfer) ships as a .dmg on"
    "\nits releases page - there is no homebrew cask, and gtkwave is disabled."
)


def run_waves(run_id: str | None, open_it: bool, draw: bool, columns: int) -> int:
    """List or inspect the VCDs a run produced. Neither needs a viewer installed."""
    runs_dir = ROOT / "runs"
    found = []
    for run_dir in sorted(runs_dir.glob("*")):
        waves = sorted((run_dir / "waves").glob("*.vcd"))
        if waves:
            found.append((run_dir.name, run_dir, waves))

    if run_id is None:
        if not found:
            print("no waveforms yet. add --waves to a run:")
            print(f"  {sys.executable} main.py run --spec examples/counter/spec.txt --run-id w1 --waves")
            return 1
        print(f"{'run':14} {'status':10} {'sim':16} {'size':>7}  file")
        for name, run_dir, waves in found:
            board_file = run_dir / "board.json"
            status = sim = "-"
            if board_file.exists():
                try:
                    board = Board.load(run_dir)
                    status = board.status.value
                    last = board.sim_history[-1] if board.sim_history else None
                    if last is not None:
                        sim = ("PASS" if last.passed else "FAIL") + f" {last.total_checks}/{last.total_checks or '?'} checks"
                except Exception:
                    pass
            print(f"{name:14} {status:10} {sim:16} {waves[0].stat().st_size // 1024:5d}K  {waves[0].relative_to(ROOT)}")
        viewer = wave_viewer()
        target = found[-1][2][0]
        if viewer:
            print(f"\nopen with:  {viewer} {target.relative_to(ROOT)}")
            print(f"or draw it:  main.py waves --run-id {found[-1][0]} --draw")
        else:
            print(NO_VIEWER)
            print(f"or draw it:  main.py waves --run-id {found[-1][0]} --draw")
        return 0

    matches = [w for w in found if w[0] == run_id]
    if not matches:
        print(f"no waveform for {run_id}; runs with one: {', '.join(n for n, _, _ in found) or 'none'}")
        return 1
    _, _, waves = matches[0]
    vcd = waves[0]
    info = read_vcd(vcd, with_values=True)
    print(f"{vcd.relative_to(ROOT)}  {info['bytes'] // 1024}K")
    print(f"timescale {info['timescale']}   span {human_time(info['start'], info['timescale'])} -> {human_time(info['end'], info['timescale'])}")
    if draw:
        print()
        for line in draw_vcd(info, columns=columns):
            print("  " + line)
        return 0
    print(f"\n{'width':>5}  {'scope':<10} signal")
    for sig in info["signals"]:
        print(f"{sig['width']:5d}  {sig['scope'] or '-':<10} {sig['name']}")
    viewer = wave_viewer()
    if viewer and open_it:
        subprocess.Popen([viewer, str(vcd)])
        print(f"\nopened in {viewer}")
    elif viewer:
        print(f"\nopen with:  {viewer} {vcd.relative_to(ROOT)}")
    else:
        print(NO_VIEWER)
        print(f"or draw it:  main.py waves --run-id {run_id} --draw")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="siliconboard")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("doctor", help="check python, keys, docker image and EDA tool versions")

    spec_cmd = sub.add_parser("spec", help="run only the Spec Agent against a request file")
    spec_cmd.add_argument("--file", type=Path, required=True)
    spec_cmd.add_argument("--run-id", default="spec")

    run_cmd = sub.add_parser("run", help="run the pipeline from the spec (or resume an existing run)")
    run_cmd.add_argument("--spec", type=Path, required=True)
    run_cmd.add_argument("--run-id", required=True)
    run_cmd.add_argument("--stop-after", choices=list(STOP))
    run_cmd.add_argument("--inject-bug", choices=["reset"], default=None,
                         help="sabotage the generated RTL once, to exercise the debug loop on demand")
    run_cmd.add_argument("--cross-sim", action="store_true",
                         help="re-run the testbench under icarus too; disagreement fails the run")
    run_cmd.add_argument("--waves", action="store_true",
                         help="dump a VCD waveform alongside the run for the demo")

    diff_cmd = sub.add_parser("diff", help="diff the last two RTL versions of a run, with its bug reports")
    diff_cmd.add_argument("--run-id", required=True)

    replay_cmd = sub.add_parser("replay", help="re-render a finished run from its board.json, making no llm calls")
    replay_cmd.add_argument("--run-id", required=True)

    waves_cmd = sub.add_parser("waves", help="list or inspect the waveform a run dumped")
    waves_cmd.add_argument("--run-id")
    waves_cmd.add_argument("--open", action="store_true", help="launch the viewer on it")
    waves_cmd.add_argument("--draw", action="store_true", help="draw it in the terminal instead")
    waves_cmd.add_argument("--columns", type=int, default=76)

    args = parser.parse_args(argv)
    if args.command == "doctor":
        return doctor()
    if args.command == "spec":
        return run_spec(args.file, args.run_id)
    if args.command == "run":
        return run_pipeline(args.spec, args.run_id, args.stop_after, args.inject_bug,
                            cross_sim=args.cross_sim, waves=args.waves)
    if args.command == "diff":
        return run_diff(args.run_id)
    if args.command == "replay":
        return run_replay(args.run_id)
    if args.command == "waves":
        return run_waves(args.run_id, args.open, args.draw, args.columns)
    return 2


if __name__ == "__main__":
    sys.exit(main())
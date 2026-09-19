"""Everything that talks to the EDA container, and every parser that decides a fact."""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

from board import DesignSpec, RTLArtifact, TestbenchArtifact

IMAGE = "siliconboard/eda:1.0"


def run_eda(cmd: list[str], cwd: Path, timeout: int = 300) -> tuple[int, str]:
    full = [
        "docker", "run", "--rm",
        "-v", f"{Path(cwd).resolve()}:/work",
        "-w", "/work",
        IMAGE,
        *cmd,
    ]
    try:
        p = subprocess.run(full, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return 124, f"timeout after {timeout}s: {' '.join(cmd)}"
    return p.returncode, p.stdout + p.stderr


def simulate(work: Path, rtl_file: str, tb_file: str, top: str) -> tuple[int, str]:
    shutil.rmtree(work / "obj_dir", ignore_errors=True)
    rc, log = run_eda(
        ["verilator", "--binary", "--timing", "-Wno-fatal", "--top-module", top, rtl_file, tb_file], work
    )
    if rc != 0:
        return rc, log
    # a testbench without $finish would otherwise spin forever inside the container
    return run_eda([f"./obj_dir/V{top}"], work, timeout=120)


def lint(work: Path, filename: str, top: str) -> tuple[bool, list[str], str]:
    # -Wno-fatal keeps style warnings from failing the run; the exit code then means errors only.
    rc, log = run_eda(["verilator", "--lint-only", "-Wall", "-Wno-fatal", "--top-module", top, filename], work)
    return rc == 0, problem_lines(log), log


def verdict(log: str) -> bool | None:
    hits = re.findall(r"^(PASS|FAIL)\s*$", log, re.M)
    return hits[0] == "PASS" if len(hits) == 1 else None


def check_counts(log: str) -> tuple[int | None, int | None]:
    m = re.search(r"^SUMMARY checks=(\d+) failed=(\d+)", log, re.M)
    return (int(m.group(1)), int(m.group(2))) if m else (None, None)


def cells_from_stat(log: str) -> int | None:
    m = re.search(r"Number of cells:\s+(\d+)", log)
    return int(m.group(1)) if m else None


def slack_from_sta(log: str) -> float | None:
    m = re.search(r"worst slack\s+(?:max|min)?\s*(-?\d+\.?\d*)", log)
    return float(m.group(1)) if m else None


def problem_lines(log: str, limit: int = 20) -> list[str]:
    pat = re.compile(r"(%Error|Error|error:|Warning|warning:)", re.I)
    return [ln.strip() for ln in log.splitlines() if pat.search(ln)][:limit]


def stage(run_dir: Path, rtl: RTLArtifact, tb: TestbenchArtifact | None = None) -> Path:
    work = run_dir / "work"
    work.mkdir(parents=True, exist_ok=True)
    (work / rtl.filename).write_text(rtl.source_code.rstrip("\n") + "\n")
    if tb is not None:
        (work / tb.filename).write_text(tb.source_code.rstrip("\n") + "\n")
    return work


_IDENT = re.compile(r"^[A-Za-z_]\w*$")


def spec_problems(spec: DesignSpec) -> list[str]:
    problems: list[str] = []
    if not _IDENT.match(spec.name):
        problems.append(f"module name {spec.name!r} is not a Verilog identifier")
    if not spec.io_ports:
        problems.append("no I/O ports extracted, the request is too vague")
    seen: set[str] = set()
    for port in spec.io_ports:
        if not _IDENT.match(port.name):
            problems.append(f"port name {port.name!r} is not a Verilog identifier")
        if port.name in seen:
            problems.append(f"port {port.name!r} declared twice")
        seen.add(port.name)
        if port.width is not None and port.width < 1:
            problems.append(f"port {port.name!r} has width {port.width}")
    return problems


_DECL = re.compile(
    r"\b(input|output|inout)\b\s*(?:wire|reg|logic)?\s*(?:signed\s*)?(\[[^\]]*\])?\s*([A-Za-z_]\w*)"
)
_MODULE = re.compile(r"\bmodule\s+([A-Za-z_]\w*)")
_NUM_RANGE = re.compile(r"\[\s*(\d+)\s*:\s*(\d+)\s*\]")


def rtl_ports(source: str) -> dict[str, tuple[str, int | None]]:
    ports: dict[str, tuple[str, int | None]] = {}
    for m in _DECL.finditer(source):
        rng = m.group(2)
        if rng is None:
            width: int | None = 1
        else:
            nums = _NUM_RANGE.match(rng)
            width = abs(int(nums.group(1)) - int(nums.group(2))) + 1 if nums else None
        ports[m.group(3)] = (m.group(1), width)
    return ports


def rtl_module_name(source: str) -> str | None:
    m = _MODULE.search(source)
    return m.group(1) if m else None


def spec_mismatch(spec: DesignSpec, rtl: RTLArtifact) -> list[str]:
    problems: list[str] = []
    name = rtl_module_name(rtl.source_code)
    if name is None:
        return ["no module declaration found in RTL"]
    if name != rtl.module_name:
        problems.append(f"module name {name!r} != artifact {rtl.module_name!r}")
    if name != spec.name:
        problems.append(f"module name {name!r} != spec {spec.name!r}")

    want = {p.name: p for p in spec.io_ports}
    got = rtl_ports(rtl.source_code)
    # ponytail: regex port scan, swap for a real parser if non-ANSI style shows up
    for port_name, port in want.items():
        if port_name not in got:
            problems.append(f"port {port_name!r} missing from RTL")
            continue
        direction, width = got[port_name]
        if direction != port.direction:
            problems.append(f"port {port_name!r} is {direction}, spec says {port.direction}")
        if port.width is not None and width is not None and port.width != width:
            problems.append(f"port {port_name!r} width {width} != spec {port.width}")
    for port_name in got:
        if port_name not in want:
            problems.append(f"port {port_name!r} in RTL but not in spec")
    return problems
"""Everything that talks to the EDA container, and every parser that decides a fact."""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

from board import DesignSpec, RTLArtifact, TestbenchArtifact

IMAGE = "siliconboard/eda:1.0"


def run_eda(cmd: list[str], cwd: Path, timeout: int = 300, image: str = IMAGE) -> tuple[int, str]:
    full = [
        "docker", "run", "--rm",
        "-v", f"{Path(cwd).resolve()}:/work",
        "-w", "/work",
        image,
        *cmd,
    ]
    try:
        p = subprocess.run(full, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return 124, f"timeout after {timeout}s: {' '.join(cmd)}"
    return p.returncode, p.stdout + p.stderr


def simulate(
    work: Path, rtl_file: str, tb_file: str, top: str, trace: bool = False
) -> tuple[int, str]:
    shutil.rmtree(work / "obj_dir", ignore_errors=True)
    trace_flags = ["--trace", "--trace-structs"] if trace else []
    rc, log = run_eda(
        ["verilator", "--binary", "--timing", "-Wno-fatal", *trace_flags,
         "--top-module", top, rtl_file, tb_file], work
    )
    if rc != 0:
        return rc, log
    # a testbench without $finish would otherwise spin forever inside the container
    return run_eda([f"./obj_dir/V{top}"], work, timeout=120)


def lint(work: Path, filenames: str | list[str], top: str, timing: bool = False) -> tuple[bool, list[str], str]:
    # -Wno-fatal keeps style warnings from failing the run; the exit code then means errors only.
    # a testbench drives the clock with delays, so it needs the same --timing the sim runner uses:
    # without it verilator reports NEEDTIMINGOPT and a working testbench looks broken
    files = [filenames] if isinstance(filenames, str) else list(filenames)
    flags = ["--timing"] if timing else []
    rc, log = run_eda(["verilator", "--lint-only", "-Wall", "-Wno-fatal", *flags, "--top-module", top, *files], work)
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
    # OpenSTA's report_checks prints "   3.74   slack (MET)"; older scripts print "worst slack"
    m = re.search(r"(-?\d+\.?\d*)\s+slack \((?:MET|VIOLATED)\)", log)
    if m is None:
        m = re.search(r"worst slack\s+(?:max|min)?\s*(-?\d+\.?\d*)", log)
    return float(m.group(1)) if m else None


# FAIL lines carry the got/want values the TB rules mandate; PASS lines are bare
_CHECK_LINE = re.compile(r"^CHECK\s+(\S+)\s+(PASS|FAIL)(?:\s+got=\S+\s+want=\S+)?\s*$", re.M)
_FAIL_LINE = re.compile(r"^CHECK\s+(\S+)\s+FAIL\s+got=(\S+)\s+want=(\S+)", re.M)
_NUMBER = re.compile(r"\d+(?:\.\d+)?")
_STAMP = re.compile(r"\d{4}-\d{2}-\d{2}T[\d:.+Z-]+|\b\d{2}:\d{2}:\d{2}\b")


def check_results(log: str) -> dict[str, bool]:
    return {name: word == "PASS" for name, word, *_ in _CHECK_LINE.findall(log)}


def fail_lines(log: str) -> list[tuple[str, str, str]]:
    """(check, got, want) for every failing check that printed evidence values."""
    return _FAIL_LINE.findall(log)


def unbacked_numbers(text: str, facts: str) -> list[str]:
    """The report may only quote numbers that exist on the Board. Timestamps are not facts."""
    body = re.sub(r"(?m)^\s*\d+\.\s", " ", text).replace(",", "")
    claimed = set(_NUMBER.findall(body))
    known = set(_NUMBER.findall(_STAMP.sub(" ", facts)))
    return sorted(claimed - known)


_ESCAPED_QUOTE = re.compile(r'\\"')


def source_shape_problems(source: str, label: str) -> list[str]:
    """Catches the ways a model returns valid JSON that is not usable Verilog source."""
    problems: list[str] = []
    lines = source.splitlines()
    if len(lines) < 3:
        problems.append(f"{label} is {len(lines)} line(s): the file must be laid out one statement per line")
    if _ESCAPED_QUOTE.search(source):
        problems.append(rf'{label} contains literal \" escapes instead of real quotes')
    head = lines[0].strip() if lines else ""
    if head and not head.startswith(("`", "//", "/*", "module")):
        problems.append(f"{label} starts with prose, not a timescale, comment, or module declaration")
    return problems


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


_RESET_IF = [
    (re.compile(r"if\s*\(\s*[!~]\s*(?:rst|reset)(?:_n|_b|n|b)?\s*\)"), "if (1'b0)"),
    (re.compile(r"if\s*\(\s*(?:rst|reset)(?:_n|_b|n|b)?\s*==\s*(?:1'b[01]|[01])\s*\)"), "if (1'b0)"),
    (re.compile(r"if\s*\(\s*(?:rst|reset)(?:_n|_b|n|b)?\s*\)"), "if (1'b0)"),
]


def sabotage_reset(source: str) -> str:
    """Test-injection helper: neutralise the first reset condition so a real simulation must fail."""
    for pattern, replacement in _RESET_IF:
        sabotaged, count = pattern.subn(replacement, source, count=1)
        if count:
            return sabotaged
    raise ValueError("no reset condition found to sabotage")


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

def simulate_iverilog(work: Path, rtl_file: str, tb_file: str, top: str) -> tuple[int, str]:
    """A second, independent engine. Two simulators agreeing is the strongest
    evidence available that a testbench is not fooling itself."""
    rc, log = run_eda(["iverilog", "-g2005", "-s", top, "-o", "sim.vvp", rtl_file, tb_file], work)
    if rc != 0:
        return rc, log
    return run_eda(["vvp", "sim.vvp"], work, timeout=120)


def with_vcd(tb_source: str, vcd_name: str) -> str:
    """Neither engine writes a waveform on its own: verilator also needs --trace."""
    if "$dumpfile" in tb_source:
        return tb_source
    return re.sub(
        r"(initial\s+begin\b)",
        rf'\1\n    $dumpfile("{vcd_name}");\n    $dumpvars(0);',
        tb_source,
        count=1,
    )

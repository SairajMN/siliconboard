"""CLI entry point. doctor first: nothing runs until the toolchain and keys are real."""

from __future__ import annotations

import argparse
import platform
import subprocess
import sys
from pathlib import Path

import llm
from agents.spec import SpecAgent
from board import Board
from tools import IMAGE, run_eda

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
    for provider in ("gemini", "groq", "nvidia"):
        found = llm.keys(provider)
        total += len(found)
        for slot, key in found:
            print(f"key {provider}:{slot}  {mask(key)} ({len(key)} chars)")
        if not found:
            print(f"key {provider}:  none (fallback chain skips it)")
    if not total:
        print("keys     none found: put GEMINI_API_KEY_1..4 / GROQ_API_KEY_1..4 / NVIDIA_API_KEY in .env")

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


def run_spec(request_file: Path, run_id: str) -> int:
    board = Board(request=request_file.read_text())
    run_dir = ROOT / "runs" / run_id
    llm.set_cache_dir(run_dir / "llm")

    result = SpecAgent().run(board)
    board.save(run_dir)

    if result.ok and board.spec:
        spec = board.spec
        print(f"module   {spec.name}")
        print(f"ports    {', '.join(f'{p.name}:{p.direction}{p.width or ''}' for p in spec.io_ports)}")
        print(f"clock    {spec.clock_domain}, target {spec.target_freq_mhz} MHz")
        for line in spec.assumptions:
            print(f"assumed  {line}")
    else:
        print(f"failed   {result.err}")

    for call in board.llm_calls:
        print(f"llm      {call.role} {call.model} slot={call.key_slot} in={call.tokens_in} out={call.tokens_out}")
    print(f"board    {run_dir / 'board.json'}")
    return 0 if result.ok else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="siliconboard")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("doctor", help="check python, keys, docker image and EDA tool versions")

    spec_cmd = sub.add_parser("spec", help="run the Spec Agent against a request file")
    spec_cmd.add_argument("--file", type=Path, required=True)
    spec_cmd.add_argument("--run-id", default="spec")

    args = parser.parse_args(argv)
    if args.command == "doctor":
        return doctor()
    if args.command == "spec":
        return run_spec(args.file, args.run_id)
    return 2


if __name__ == "__main__":
    sys.exit(main())
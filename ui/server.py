"""A small local web UI over the pipeline: watch a run, read its output.

Deliberately stdlib-only. The project runs on two dependencies and no build step,
so the UI is a ThreadingHTTPServer plus one static page, not a framework.

It binds 127.0.0.1 and nothing else. Reaching it means an ssh tunnel, which is
the whole security story: no new public port, no certificate to rotate, and the
only credential involved is the ssh key the box already trusts. Even on loopback
a shared token is required, so another account on the box cannot drive it.

    python ui/server.py                 # prints a URL containing the token
    python ui/server.py --port 8080 --expose   # opt-in; binds all interfaces
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import re
import secrets
import shutil
import socketserver
import subprocess
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import ui.timeline as timeline  # noqa: E402  (needs ROOT on the path first)
from board import Board  # noqa: E402
from tools import draw_vcd, human_time, read_vcd  # noqa: E402

STATIC = Path(__file__).resolve().parent / "static"
RUNS = ROOT / "runs"
EXAMPLES = ROOT / "examples"
GOLDEN = ROOT / "demo" / "golden"

RUN_ID = re.compile(r"^[A-Za-z0-9._-]{1,40}$")
TOKEN = os.environ.get("SB_UI_TOKEN") or secrets.token_urlsafe(18)

# a run id is a directory name we build paths from, so it is validated, not trusted
def valid_run_id(run_id: str) -> bool:
    return bool(RUN_ID.match(run_id or "")) and ".." not in run_id


def load_board(run_id: str) -> Board | None:
    if not valid_run_id(run_id):
        return None
    for base in (RUNS, GOLDEN):
        path = base / run_id / "board.json"
        if path.exists():
            try:
                return Board.load(base / run_id)
            except Exception:
                return None
    return None


def run_dir_of(run_id: str) -> Path | None:
    if not valid_run_id(run_id):
        return None
    for base in (RUNS, GOLDEN):
        if (base / run_id / "board.json").exists():
            return base / run_id
    return None


def artifacts_of(run_id: str) -> list[dict[str, str]]:
    """Every file a run left behind, labelled for the output tabs."""
    directory = run_dir_of(run_id)
    if directory is None:
        return []
    found: list[dict[str, str]] = []
    for label, pattern in (
        ("report.md", "report.md"),
        ("board.json", "board.json"),
        ("waves/*.vcd", "waves/*.vcd"),
        ("work/*.v", "work/*.v"),
    ):
        for path in sorted(directory.glob(pattern)):
            found.append({"label": path.name, "path": str(path.relative_to(directory)), "group": label})
    return found
def runs_index() -> list[dict]:
    """Newest first. Each row carries its own input, so history pairs input to output."""
    rows = []
    for base, kind in ((RUNS, "run"), (GOLDEN, "sample")):
        if not base.exists():
            continue
        for directory in base.iterdir():
            if not directory.is_dir() or not (directory / "board.json").exists():
                continue
            try:
                board = Board.load(directory)
            except Exception:
                continue
            sim = board.latest_sim
            timing = board.timing_report
            synth = board.synthesis_report
            request = (board.request or "").strip().splitlines()
            rows.append({
                "run_id": directory.name,
                "kind": kind,
                "status": board.status.value,
                "finished": board.finished,
                "created_at": board.created_at.isoformat(),
                "updated_at": board.updated_at.isoformat(),
                "input": request[0] if request else "",
                "module": board.spec.name if board.spec else None,
                "checks": sim.total_checks if sim else None,
                "failed": sim.failed_checks if sim else None,
                "sim_passed": sim.passed if sim else None,
                "cells": synth.cell_count if synth else None,
                "slack_ns": timing.worst_slack_ns if timing else None,
                "met_target": timing.met_target if timing else None,
                "mtime": (directory / "board.json").stat().st_mtime,
            })
    rows.sort(key=lambda row: (row["kind"] == "sample", -row["mtime"]))
    return rows


def examples_index() -> list[dict]:
    out = []
    if EXAMPLES.exists():
        for directory in sorted(EXAMPLES.iterdir()):
            spec = directory / "spec.txt"
            if spec.exists():
                out.append({"id": directory.name, "title": directory.name, "text": spec.read_text().strip()})
    return out


DOCTOR_CACHE: dict = {"at": 0.0, "value": None}


def system_info() -> dict:
    """Keys and tool versions, the same facts `main.py doctor` prints."""
    if time.time() - DOCTOR_CACHE["at"] < 60 and DOCTOR_CACHE["value"] is not None:
        return DOCTOR_CACHE["value"]
    info: dict = {"keys": {}, "tools": [], "ready": False, "note": None}
    try:
        import llm
        providers = list(dict.fromkeys(route.partition(":")[0] for route in llm.HEAVY + llm.LIGHT))
        total = 0
        for provider in providers:
            found = llm.keys(provider)
            total += len(found)
            info["keys"][provider] = len(found)
        info["key_total"] = total
    except Exception as exc:  # a missing key ring must not take the page down
        info["note"] = f"keys unavailable: {exc}"
    probe = subprocess.run(
        [sys.executable, "main.py", "doctor"], cwd=ROOT, capture_output=True, text=True, timeout=120
    )
    for line in probe.stdout.splitlines():
        line = line.strip()
        if line.startswith("tool "):
            info["tools"].append(line[5:])
        if line.startswith("docker "):
            info["note"] = line[7:]
    info["ready"] = probe.returncode == 0
    DOCTOR_CACHE.update(at=time.time(), value=info)
    return info
def child_env() -> dict:
    """systemd injects /etc/siliconboard/env; a manual start must do the same."""
    env = dict(os.environ)
    for path in (Path("/etc/siliconboard/env"), ROOT / ".env"):
        if not path.is_file():
            continue
        for raw in path.read_text().splitlines():
            name, sep, value = raw.strip().partition("=")
            if sep and name and not name.startswith("#"):
                env.setdefault(name.strip(), value.strip().strip('"').strip("'"))
    return env


ACTIVE: dict[str, subprocess.Popen] = {}


def active_runs() -> dict[str, int]:
    return {run_id: proc.pid for run_id, proc in ACTIVE.items() if proc.poll() is None}


def start_run(run_id: str, text: str, cross_sim: bool, waves: bool) -> tuple[int, str]:
    """One run at a time. Two writers in one run dir would interleave the audit trail."""
    if not valid_run_id(run_id):
        return 400, "run id must be letters, digits, dot, dash or underscore"
    if (RUNS / run_id / "board.json").exists():
        return 409, f"run {run_id} already exists, pick another id"
    if not text.strip():
        return 400, "the request is empty"
    busy = active_runs()
    if busy:
        return 409, f"a run is already in flight: {', '.join(busy)}"

    directory = RUNS / run_id
    directory.mkdir(parents=True, exist_ok=True)
    spec = directory / "input.txt"
    spec.write_text(text.rstrip() + "\n")

    command = [sys.executable, "main.py", "run", "--spec", str(spec), "--run-id", run_id]
    if cross_sim:
        command.append("--cross-sim")
    if waves:
        command.append("--waves")
    log = (directory / "ui.log").open("w")
    ACTIVE[run_id] = subprocess.Popen(
        command, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, env=child_env()
    )
    return 202, run_id


def wave_ascii(run_id: str, columns: int = 76) -> dict | None:
    directory = run_dir_of(run_id)
    vcds = sorted((directory / "waves").glob("*.vcd")) if directory else []
    if not vcds:
        return None
    path = vcds[0]
    info = read_vcd(path, with_values=True)
    return {
        "file": str(path.relative_to(directory)),
        "bytes": info["bytes"],
        "timescale": info["timescale"],
        "span": f"{human_time(info['start'], info['timescale'])} to {human_time(info['end'], info['timescale'])}",
        "signals": info["signals"],
        "lines": draw_vcd(info, columns=columns),
    }


def now_seconds() -> int:
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    return now.hour * 3600 + now.minute * 60 + now.second
class Server(ThreadingHTTPServer):
    daemon_threads = True

    def server_bind(self) -> None:
        # HTTPServer.server_bind calls socket.getfqdn(), a reverse-DNS lookup that
        # stalls startup for seconds on a host with no PTR record. We never read
        # server_name, so bind the socket and skip the lookup.
        socketserver.TCPServer.server_bind(self)
        host, port = self.server_address[:2]
        self.server_name, self.server_port = host, port


class Handler(BaseHTTPRequestHandler):
    server_version = "siliconboard-ui"

    def log_message(self, fmt: str, *args) -> None:  # keep the console readable
        if "/api/" in (self.path or ""):
            sys.stderr.write(f"  {self.address_string()} {fmt % args}\n")

    # ---------------------------------------------------------------- plumbing
    def _json(self, payload, code: int = 200) -> None:
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _error(self, code: int, message: str) -> None:
        self._json({"error": message}, code)

    def _authorized(self, query: dict) -> bool:
        given = (query.get("t") or [self.headers.get("X-SB-Token") or ""])[0]
        return secrets.compare_digest(str(given), TOKEN)

    def _static(self, name: str) -> None:
        target = (STATIC / name.lstrip("/")).resolve()
        if STATIC.resolve() not in target.parents and target != STATIC.resolve():
            return self._error(404, "not found")
        if not target.is_file():
            return self._error(404, "not found")
        body = target.read_bytes()
        mime, _ = mimetypes.guess_type(str(target))
        self.send_response(200)
        self.send_header("Content-Type", mime or "application/octet-stream")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _artifact(self, run_id: str, query: dict) -> None:
        """Serve one file from inside a run dir, resolved so it cannot escape it."""
        relative = (query.get("path") or [""])[0]
        directory = run_dir_of(run_id)
        if directory is None:
            return self._error(404, "no such run")
        target = (directory / relative).resolve()
        if directory.resolve() not in target.parents or not target.is_file():
            return self._error(404, "no such artifact")
        body = target.read_bytes()
        mime, _ = mimetypes.guess_type(str(target))
        self.send_response(200)
        self.send_header("Content-Type", mime or "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Content-Disposition", f'inline; filename="{target.name}"')
        self.end_headers()
        self.wfile.write(body)

    def _log_tail(self, run_id: str, limit: int = 40) -> str:
        directory = run_dir_of(run_id)
        log = directory / "ui.log" if directory else None
        if log is None or not log.is_file():
            return ""
        return "\n".join(log.read_text(errors="replace").splitlines()[-limit:])

    def _stream(self, run_id: str) -> None:
        """Server-sent events: the same timeline the CLI prints, once a second."""
        if load_board(run_id) is None:
            return self._error(404, "no such run")
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        try:
            while True:
                board = load_board(run_id)
                if board is None:
                    break
                payload = timeline.build(board, run_id, now=now_seconds())
                payload["active"] = active_runs()
                self.wfile.write(b"data: " + json.dumps(payload).encode() + b"\n\n")
                self.wfile.flush()
                if board.finished:
                    break
                time.sleep(1)
        except (BrokenPipeError, ConnectionResetError):
            pass  # the tab was closed or the tunnel dropped; nothing to do
# ---------------------------------------------------------------- routes
    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path, query = parsed.path, parse_qs(parsed.query)

        if path in ("/", "/index.html"):
            return self._static("index.html")
        if path.startswith("/static/"):
            return self._static(path[len("/static/"):])

        if not path.startswith("/api/"):
            return self._error(404, "not found")
        if not self._authorized(query):
            return self._error(401, "missing or wrong token: reopen the URL the server printed")

        if path == "/api/system":
            return self._json(system_info())
        if path == "/api/runs":
            return self._json({"runs": runs_index(), "active": active_runs()})
        if path == "/api/examples":
            return self._json({"examples": examples_index()})
        if path == "/api/stream":
            return self._stream((query.get("run") or [""])[0])

        parts = path.strip("/").split("/")
        if len(parts) >= 3 and parts[1] == "runs":
            run_id = parts[2]
            if len(parts) == 3:
                board = load_board(run_id)
                if board is None:
                    return self._error(404, "no such run")
                payload = timeline.build(board, run_id, now=now_seconds())
                payload["artifacts"] = artifacts_of(run_id)
                payload["log_tail"] = self._log_tail(run_id)
                return self._json(payload)
            if len(parts) == 4 and parts[3] == "waves":
                drawn = wave_ascii(run_id, int((query.get("columns") or [76])[0]))
                return self._json(drawn) if drawn else self._error(404, "this run dumped no waveform")
            if len(parts) == 4 and parts[3] == "file":
                return self._artifact(run_id, query)
        return self._error(404, "not found")

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        if not self._authorized(query):
            return self._error(401, "missing or wrong token")

        length = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            return self._error(400, "body must be JSON")

        if parsed.path == "/api/run":
            code, message = start_run(
                str(body.get("run_id") or "").strip(),
                str(body.get("request") or ""),
                bool(body.get("cross_sim")),
                bool(body.get("waves")),
            )
            if code >= 400:
                return self._error(code, message)
            return self._json({"run_id": message}, code)
        return self._error(404, "not found")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="siliconboard-ui")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--expose", action="store_true",
                        help="bind all interfaces (adds a public port; pair it with the SG rule and the token)")
    args = parser.parse_args(argv)

    host = "0.0.0.0" if args.expose else args.host
    shown = "127.0.0.1" if args.expose else host
    server = Server((host, args.port), Handler)
    print(f"siliconboard ui on http://{shown}:{args.port}/?t={TOKEN}", flush=True)
    if args.expose:
        print("  --expose: reachable from other hosts; keep the token and pin the security group")
    else:
        print(f"  loopback only. tunnel it:  ssh -L {args.port}:127.0.0.1:{args.port} -i <key> ubuntu@<box>")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
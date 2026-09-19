"""The one agent shape, plus the two helpers every agent shares."""

from __future__ import annotations

import re
from pathlib import Path

from pydantic import BaseModel

from board import Board

NO_INVENTION = (
    "If the provided data does not determine a field, output the literal string UNKNOWN for that "
    "field and explain why in one line in `notes`. Never state a number that does not appear in "
    "the input. Never describe a test result, a tool output, or a log line that you were not given. "
    "Return ONLY the JSON object: no markdown fences, no prose before or after."
)

NO_SYSTEMVERILOG = (
    "Verilog-2005 only. Banned: logic, always_ff, always_comb, always_latch, enum, struct, "
    "packed interface ports, unique/priority case, # delays in synthesizable logic, "
    "initial blocks for synthesis, and any system task other than $display in testbenches."
)

KEEP = re.compile(r"FAIL|ERROR|%Error|CHECK|Warning|error:|Summary", re.I)


class AgentResult(BaseModel):
    ok: bool
    err: str | None = None
    log: str | None = None
    note: str | None = None


class BaseAgent:
    name = "agent"
    system = ""
    run_dir: Path | None = None

    def run(self, board: Board) -> AgentResult:
        raise NotImplementedError


def build_prompt(*sections: tuple[str, str]) -> str:
    return "\n\n".join(f"## {title}\n{body}" for title, body in sections)


def trim_log(text: str, head: int = 40, tail: int = 80, limit: int = 6000) -> str:
    lines = text.splitlines()
    if len(lines) <= head + tail:
        picked = lines
    else:
        dropped = len(lines) - head - tail
        picked = lines[:head] + [f"... {dropped} lines omitted ..."] + lines[-tail:]
    flagged = [ln for ln in lines if KEEP.search(ln) and ln not in picked]
    out = "\n".join(picked + (["--- flagged lines ---", *flagged] if flagged else []))
    return out if len(out) <= limit else out[:limit] + "\n... truncated ..."
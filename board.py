"""The Board: one Pydantic object holding everything known about a design run."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field


class DesignStatus(str, Enum):
    DRAFTING_SPEC = "drafting_spec"
    GENERATING_RTL = "generating_rtl"
    LINTING = "linting"
    WRITING_TESTBENCH = "writing_testbench"
    SIMULATING = "simulating"
    DEBUGGING = "debugging"
    SYNTHESIZING = "synthesizing"
    ANALYZING_TIMING = "analyzing_timing"
    PLACE_AND_ROUTE = "place_and_route"
    DONE = "done"
    DONE_WITH_WARNING = "done_with_warning"
    FAILED = "failed"


class Port(BaseModel):
    name: str
    direction: Literal["input", "output", "inout"]
    width: int | None = None


class DesignSpec(BaseModel):
    name: str
    description: str
    io_ports: list[Port]
    clock_domain: str = "clk"
    target_freq_mhz: float | None = None
    behavior_notes: str
    assumptions: list[str] = Field(default_factory=list)


class RTLArtifact(BaseModel):
    module_name: str
    filename: str
    source_code: str
    version: int


class LintResult(BaseModel):
    clean: bool
    issues: list[str] = Field(default_factory=list)
    raw_log: str = ""


class TestbenchArtifact(BaseModel):
    filename: str
    source_code: str
    test_vectors_description: str


class SimResult(BaseModel):
    passed: bool | None
    total_checks: int | None = None
    failed_checks: int | None = None
    raw_log: str = ""
    ambiguity: str | None = None
    rtl_version: int | None = None


class BugReport(BaseModel):
    summary: str
    likely_cause: str
    failing_check: str
    suggested_fix: str
    evidence_quote: str
    blames: Literal["rtl", "testbench"] = "rtl"
    notes: str = ""


class SynthesisReport(BaseModel):
    cell_count: int | None = None
    area_um2: float | None = None
    raw_log: str = ""


class TimingReport(BaseModel):
    met_target: bool
    worst_slack_ns: float | None = None
    critical_path: str = ""
    explanation: str = ""
    verified: bool = False


class RunReport(BaseModel):
    markdown_summary: str


class LLMCall(BaseModel):
    role: str
    model: str
    key_slot: int
    tokens_in: int | None = None
    tokens_out: int | None = None
    cached: bool = False


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Board(BaseModel):
    status: DesignStatus = DesignStatus.DRAFTING_SPEC
    request: str = ""
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)

    spec: DesignSpec | None = None
    rtl_history: list[RTLArtifact] = Field(default_factory=list)
    lint: LintResult | None = None
    testbench: TestbenchArtifact | None = None
    sim_history: list[SimResult] = Field(default_factory=list)
    bug_history: list[BugReport] = Field(default_factory=list)
    synthesis_report: SynthesisReport | None = None
    timing_report: TimingReport | None = None
    run_report: RunReport | None = None

    retry_count: int = 0
    max_retries: int = 3
    stage_retries: dict[str, int] = Field(default_factory=dict)
    ambiguity: str | None = None
    event_log: list[str] = Field(default_factory=list)
    llm_calls: list[LLMCall] = Field(default_factory=list)

    @property
    def latest_rtl(self) -> RTLArtifact | None:
        return self.rtl_history[-1] if self.rtl_history else None

    @property
    def latest_sim(self) -> SimResult | None:
        return self.sim_history[-1] if self.sim_history else None

    @property
    def latest_bug(self) -> BugReport | None:
        return self.bug_history[-1] if self.bug_history else None

    @property
    def finished(self) -> bool:
        return self.status in (DesignStatus.DONE, DesignStatus.DONE_WITH_WARNING, DesignStatus.FAILED)

    def log(self, message: str) -> None:
        self.event_log.append(f"{_now():%H:%M:%S} [{self.status.value}] {message}")

    def save(self, run_dir: Path) -> None:
        self.updated_at = _now()
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "board.json").write_text(self.model_dump_json(indent=2))

    @classmethod
    def load(cls, run_dir: Path) -> "Board":
        return cls.model_validate_json((run_dir / "board.json").read_text())
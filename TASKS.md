# TASKS

Rule: a task is `[x]` only when its verify command has been run and the raw output is saved in `evidence/<ID>.txt`. No prose, no "looks right".

Status: `[ ]` open · `[>]` in progress · `[x]` verified · `[!]` blocked

## Blocked on you right now

The four keys in `.env` are valid and authenticate, but every project they belong to returns
`429 RESOURCE_EXHAUSTED: Your prepayment credits are depleted`. All four were probed
(`evidence/W1-10-blocked-credits.txt`): none has credit. Until that changes, every LLM-backed task
(W1-10, W1-12, all of Week 2 onward) is blocked, while every tool-backed task is already verified.

Update (W1-13): the LLM chain now falls back Gemini → Groq → NVIDIA. Adding GROQ_API_KEY_1..4
(free tier at console.groq.com) or NVIDIA_API_KEY to `.env` unblocks the live pipeline even with
zero Gemini credits — the router skips the depleted provider automatically.

Two ways unblock: add credits in AI Studio for these projects, or create fresh projects without
billing enabled so they run on the free tier. The code fails in 1 second with the real message
instead of retrying, so you can re-test with one command:

```bash
.venv/bin/python main.py spec --file examples/counter/spec.txt --run-id spec-smoke
```

## Week 1 — toolchain, Board, LLM plumbing

| ID | Task | Status | Evidence | Verify |
|---|---|---|---|---|
| W1-01 | EDA image: yosys + verilator + iverilog on ubuntu:24.04, native arm64 | [x] | `evidence/W1-01-image.txt` | `docker build -t siliconboard/eda:1.0 docker/eda && docker run --rm siliconboard/eda:1.0 sh -c 'uname -m; yosys -V; verilator --version; iverilog -V'` |
| W1-02 | Hand-written `smoke/counter.v` lints clean | [x] | `evidence/W1-02-04-toolchain.txt` | `.venv/bin/python tests/check_toolchain.py` |
| W1-03 | Real simulation: exactly one terminal verdict, 4 checks, 0 failed | [x] | `evidence/W1-02-04-toolchain.txt` | same |
| W1-04 | Real synthesis: `Number of cells` parses (24 cells) | [x] | `evidence/W1-02-04-toolchain.txt` | same |
| W1-05 | `main.py doctor`: keys masked, docker + 3 tool versions, exit 0 | [x] | `evidence/W1-05-doctor.txt` | `.venv/bin/python main.py doctor` |
| W1-06 | Record this project's real AI Studio RPM/RPD limits below | [!] | — | paste the table, needs you |
| W1-07 | Board round-trips through `board.json` unchanged | [x] | `evidence/W1-07-board.txt` | `.venv/bin/python tests/check_board.py` |
| W1-08 | Parsers decide facts: verdict, check counts, cells, slack, ports, trim | [x] | `evidence/W1-08-parsers.txt` | `.venv/bin/python tests/check_parsers.py` |
| W1-09 | Every LLM output schema converts to a Gemini Schema (no refs/anyOf) | [x] | `evidence/W1-09-schema-canary.txt` | `.venv/bin/python tests/check_llm.py` |
| W1-10 | Spec Agent: 3 example specs parse to non-empty `io_ports` | [x] live on Groq | `evidence/W1-10-offline.txt`, `evidence/W1-10-live-spec.txt` | live run: `up_counter` spec extracted via `groq:openai/gpt-oss-20b` (721/857 tokens), gates passed, board saved; Gemini auto-skipped (depleted). Remaining: fifo + alu live runs |
| W1-11 | Hand-written example requests: counter, fifo, alu | [x] | `evidence/W1-11-examples.txt` | `ls examples/*/spec.txt` |
| W1-12 | LLM RTL spike: generated module passes `--lint-only` | [x] superseded by W2-01 | `evidence/W2-08-09-forward-resume.txt` | superseded: the real RTL Agent replaced the throwaway spike, and its output lints clean (W2-01) |
| W1-13 | Multi-provider LLM router: Gemini → Groq → NVIDIA chains, per-provider key rings, cooldown, urllib adapter | [x] | `evidence/W1-13-multi-provider-router.txt` | `.venv/bin/python tests/check_llm.py` + `main.py doctor` shows 3 providers |
## Week 2 — forward-only end-to-end, counter

| ID | Task | Status | Evidence | Verify |
|---|---|---|---|---|
| W2-01 | RTL Agent produces counter RTL that lints clean and matches the spec ports | [x] live on Groq | `evidence/W2-08-09-forward-resume.txt` | live: `groq:openai/gpt-oss-120b` wrote a 16-line `up_counter` matching all 4 spec ports; lint clean; offline gate tests in `check_agents.py` |
| W2-02 | Lint Agent: clean RTL passes, broken RTL fails with issues | [x] | `evidence/W2-toolonly-agents.txt` | `.venv/bin/python tests/check_toolonly.py` |
| W2-03 | Testbench Agent: instantiates DUT, one terminal verdict, reset + edge checks | [x] live on Groq | `evidence/W2-08-09-forward-resume.txt` | live: 56-line TB, sim `checks=3 failed=0` PASS. Gate now also rejects degenerate output after this run exposed it |
| W2-04 | Sim Runner: zero LLM (enforced by `no_llm_anywhere`), PASS on good pair, FAIL on sabotaged pair, compile-fail routes back | [x] | `evidence/W2-toolonly-agents.txt` | `.venv/bin/python tests/check_toolonly.py` |
| W2-05 | Ambiguity gate: 0 or 2 verdict lines ⇒ `passed=None`, agent refuses, `board.ambiguity` set (orchestrator special-case lands with W2-08) | [x] | `evidence/W2-toolonly-agents.txt` | `.venv/bin/python tests/check_toolonly.py` |
| W2-06 | Synthesis Agent: zero LLM, real Yosys `stat`, cell count parses | [x] | `evidence/W2-toolonly-agents.txt` | `.venv/bin/python tests/check_toolonly.py` |
| W2-07 | Report Agent + number gate: every number in the report exists on the Board | [x] | `evidence/W2-07-redteam-number-gate.txt` | `python tests/redteam.py` → the invented `4211` is rejected for all 6 attempts and no `report.md` is written; `24` and the failing check name are accepted |
| W2-08 | Orchestrator forward routing, Board persisted every step | [x] live on Groq | `evidence/W2-08-09-forward-resume.txt` | `main.py run --spec examples/counter/spec.txt --run-id w2-counter` → `status=done`, `exit=0`, 24 cells. Also proved the negative: a failing sim stops the run *before* synthesis (`runs/w2-repair`) |
| W2-09 | Resume after kill, no duplicated RTL version | [x] live on Groq | `evidence/W2-08-09-forward-resume.txt` | `--stop-after rtl` then the same `--run-id` → `resumed … at linting`, spec+rtl not re-executed, `rtl_history` still length 1 |
| W2-10 | Ponytail audit #1 applied, checks still green | [x] | `evidence/W2-10-ponytail-audit.txt` | `python tests/run_all.py` (audit: deleted `dead_providers()`, the only true cut; 2 deps total) |

## Week 3 — feedback loop, generalisation, real STA, red team

| ID | Task | Status | Verify |
|---|---|---|---|
| W3-01 | Debug Agent with `evidence_quote`; fabricated quote is rejected | [x] | `evidence/W3-01-debug-agent.txt` | `python tests/redteam.py` (quote must be a verbatim log line; failing_check must be a failed check) |
| W3-02 | Backward routing: sabotaged counter goes SIM→DEBUG→RTL→DONE, history length 2 | [x] live on Groq | `main.py run --spec examples/counter/spec.txt --inject-bug reset` → `status=done`, `rtl_history=[1,2]`, `bug_history=1`, sims `(False,12,3)→(True,12,0)`, evidence quote verbatim in raw log (`evidence/W3-02-03-inject-loop.txt`) |
| W3-03 | Retry prompt targets the reported bug, diff ≤10 lines | [x] live on Groq | `main.py diff --run-id w3-inject`: **6 changed lines** — `- if (1'b0)` → `+ if (!reset_n)` and the overflow guard removed; Debug Agent named both real defects (sabotage + its own v1 wrap bug) |
| W3-04 | FIFO: real v1 FAIL → cited bug → real v2 PASS | [x] live on Groq | `main.py run --spec examples/fifo/spec.txt` → `done`, **17 real Verilator checks, 0 failed**, 189 cells; TB agent's 135-line TB right first try after the chain fixes (8192 cap / 90s timeout / name-port rules) (`evidence/W3-04-fifo.txt`) |
| W3-05 | ALU reaches DONE or DONE-WITH-WARNING unattended | [x] live on Groq | `main.py run --spec examples/alu/spec.txt` → `status: done`, sim `checks=3 failed=0`, synth 210 cells, report written, zero retries (`evidence/W3-05-alu.txt`) |
| W3-06 | OpenSTA image builds and runs on arm64 and amd64 | [x] | `docker run --rm siliconboard/sta:1.0 sta -version` → OpenSTA 3.1.0 exit 0; built from source with CUDD stage (`docker/sta/Dockerfile`) |
| W3-07 | sky130 Liberty downloaded and cached | [x] | `pdk/sky130hd_tt_025C_1v80.lib` — 12,800,135 bytes, `library ("sky130_fd_sc_hd__tt_025C_1v80")` (`evidence/W3-07-liberty.txt`) |
| W3-08 | Timing Agent: verdict from the parser, LLM only explains | [x] live on Groq | `main.py run --spec examples/counter/spec.txt --run-id w3-timing2` → `timing: slack=3.11 ns target=200.0 MHz MET via OpenSTA`, `verified: true`, report 103 words, `exit=0` (`evidence/W3-08-timing-done.txt`); offline proof the model's opposite verdict is overwritten: `tests/check_agents.py` |
| W3-09 | Red team: fabricated quote, invented cell count, unearned testbench blame all rejected | [x] | `python tests/redteam.py` → 3 rejection classes + real evidence accepted (`evidence/W3-09-redteam.txt`) |
| W3-10 | Ponytail debt ledger, no `no-trigger` markers | [x] | `grep -rnE '# ponytail:' .` → single cache ceiling marker with trigger, zero `no-trigger` (`evidence/W3-10-ponytail.txt`) |

## Week 4 — deploy, rehearse, stretch

| ID | Task | Status | Verify |
|---|---|---|---|
| W4-01 | Server provisioned, EDA image built on the box | [ ] | `ssh … 'docker images \| grep siliconboard'` |
| W4-02 | `/etc/siliconboard/env` 0600 + systemd unit active | [ ] | `ssh … 'systemctl is-active siliconboard; stat -c %a /etc/siliconboard/env'` |
| W4-03 | SSH-triggered run leaves board.json and report.md on the server | [ ] | `ssh … 'python3 main.py run --spec examples/counter/spec.txt --run-id live1'` |
| W4-04 | Replay from a second session, zero LLM calls | [>] | mechanism verified locally (keys stripped, byte-identical report); server leg waits on W4-01, evidence/W4-04-replay-offline.txt |
| W4-05 | Dual-sim cross-check, Verilator and Icarus agree | [x] | `tests/check_toolonly.py` cross_sim_agrees, cross_sim_disagreement_fails_the_run |
| W4-06 | Waveform dump for the demo | [x] | `tests/check_toolonly.py` waves_land_on_disk, evidence/W4-05-06-cross-sim-waves.txt |
| W4-07 | Demo rehearsed 3 times, each ≤5:00, one offline | [ ] | `evidence/W4-07.txt` |
| W4-08 | Physical design (stretch) never blocks the run | [ ] | `main.py run … --with-pnr` |
| W4-09 | Ponytail audit #2, README final, checks green | [ ] | `python tests/run_all.py` |

## Findings — facts the tools taught us (each cost a real failure)

| # | Finding | Consequence |
|---|---|---|
| F1 | `verilator --lint-only -Wall` exits 1 on *any* warning (EOFNEWLINE made it fail) | lint and sim use `-Wno-fatal`: errors gate, warnings are recorded in `issues` but never block |
| F2 | `TIMESCALEMOD`: RTL with no timescale warns when the TB has one | `smoke/counter.v` carries `\`timescale 1ns/1ps`; the RTL prompt will require it |
| F3 | `WIDTHTRUNC`: a `[127:0]` task argument truncates a 20-character name | widened to `[255:0]`; `rtl_ports` compares widths numerically |
| F4 | `GenerateContentConfig(response_schema=...)` raises `ValueError: Unsupported schema type` for a JSON *string* | pass the Pydantic class; `tests/check_llm.py` is the canary |
| F5 | The SDK converts `int \| None` to `nullable: true` and inlines `$ref`/`$defs` | Optional fields and nested models are safe in Gemini schemas |
| F6 | noble ships verilator 5.020, yosys 0.33, iverilog 12.0 for arm64 and amd64 | one image, no `--platform` flag, no Rosetta, same on Mac and server |
| F7 | `gemini-2.5-flash` is dead: `404 ... no longer available to new users, use models/gemini-3.6-flash` | model chains are `3.8/3.7/3.6-flash` and `3.5/3.1-flash-lite, 3.6-flash`; every name verified reachable before being pinned |
| F8 | A depleted-credits `429` is permanent, not a rate limit; retrying it hid the real error for 30s | billing and 4xx errors raise `LLMError` immediately; only true rate limits cool down and retry; run now fails in 1s |
| F9 | `assign undeclared_lhs = 1'b1;` compiles fine — implicit-net rules auto-declare the LHS | a "broken RTL" fixture must break on the RHS; `tests/check_toolonly.py` uses an undeclared RHS identifier |
| F10 | Simulating with the DUT as `--top-module` compiles but hangs forever: the TB is inert, `$finish` never fires | `SimRunnerAgent` derives the top from the testbench source (`rtl_module_name(tb.source_code)`); sim binary runs under a 120s timeout |
| F11 | `subprocess.run(timeout=...)` raises `TimeoutExpired` and would crash the agent mid-pipeline | `run_eda` catches it and returns `(124, "timeout after Ns: …")` so a hang becomes a normal failed step with a log |
| F12 | A stalled Google connection has no SDK default timeout: the pipeline sat 4+ min on one ESTABLISHED socket (`lsof -i` proved it) while spec/RTL completed in 2s | `REQUEST_TIMEOUT=30` wired into `HttpOptions(timeout=ms)` and every `urlopen`; a run can no longer hang on a provider |
| F13 | On timeout the router used to sleep and retry the *same* model, burning 2 of 6 attempts on a dead endpoint (nvidia: 0 bytes in 25s) | first timeout marks the provider `_dead` for the run and advances the chain; restart re-enables it |
| F14 | groq `gpt-oss-20b` needed **7069 completion tokens** for one testbench: caps of 2048/4096 truncated the JSON mid-string → deterministic `400 Failed to generate JSON` | initial groq cap is now a measured 8192 (probe: HTTP 200, valid JSON); comment records the measurement, not a guess |
| F15 | groq "Request too large" limits are **per model** (qwen=936, gpt-oss-20b=7936): `_cap` keyed by provider let qwen's ceiling poison 20b for the rest of the run | `_cap` re-keyed `provider:model`; on 413/429-too-large the measured `Limit N - 64` is adopted and the same route retried (observed live: "cap lowered to 7936, retrying" → success) |
| F16 | `_trace` truncates errors at 140 chars and Groq's 400 body puts the model's actual bad output in `failed_generation` — the diagnosis field arrived *after* the truncation | `_openai_call` parses the 400 body and puts `failed_generation` FIRST in the message; the chain-exhausted raise now carries the attempts tail |

## Your rate limits (fill in once, W1-06)

| Model | RPM | RPD | TPM |
|---|---|---|---|
| | | | |
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
| W1-10 | Spec Agent: 3 example specs parse to non-empty `io_ports` | [!] | `evidence/W1-10-offline.txt`, `evidence/W1-10-blocked-credits.txt` | offline half verified (refuses an empty request, writes nothing, fails in 1s with the real error); live half needs project credits |
| W1-11 | Hand-written example requests: counter, fifo, alu | [x] | `evidence/W1-11-examples.txt` | `ls examples/*/spec.txt` |
| W1-12 | LLM RTL spike: generated module passes `--lint-only` | [!] blocked on credits | — | one `llm.call(..., RTLArtifact)` + lint |
| W1-13 | Multi-provider LLM router: Gemini → Groq → NVIDIA chains, per-provider key rings, cooldown, urllib adapter | [x] | `evidence/W1-13-multi-provider-router.txt` | `.venv/bin/python tests/check_llm.py` + `main.py doctor` shows 3 providers |
## Week 2 — forward-only end-to-end, counter

| ID | Task | Status | Evidence | Verify |
|---|---|---|---|---|
| W2-01 | RTL Agent produces counter RTL that lints clean and matches the spec ports | [!] blocked on credits | — | `main.py run --spec examples/counter/spec.txt --stop-after rtl` |
| W2-02 | Lint Agent: clean RTL passes, broken RTL fails with issues | [x] | `evidence/W2-toolonly-agents.txt` | `.venv/bin/python tests/check_toolonly.py` |
| W2-03 | Testbench Agent: instantiates DUT, one terminal verdict, reset + edge checks | [!] blocked on credits | — | `main.py run … --stop-after tb` |
| W2-04 | Sim Runner: zero LLM (enforced by `no_llm_anywhere`), PASS on good pair, FAIL on sabotaged pair, compile-fail routes back | [x] | `evidence/W2-toolonly-agents.txt` | `.venv/bin/python tests/check_toolonly.py` |
| W2-05 | Ambiguity gate: 0 or 2 verdict lines ⇒ `passed=None`, agent refuses, `board.ambiguity` set (orchestrator special-case lands with W2-08) | [x] | `evidence/W2-toolonly-agents.txt` | `.venv/bin/python tests/check_toolonly.py` |
| W2-06 | Synthesis Agent: zero LLM, real Yosys `stat`, cell count parses | [x] | `evidence/W2-toolonly-agents.txt` | `.venv/bin/python tests/check_toolonly.py` |
| W2-07 | Report Agent + number gate: every number in the report exists on the Board | [ ] | — | `python tests/redteam.py` |
| W2-08 | Orchestrator forward routing, Board persisted every step | [ ] | — | `main.py run --spec examples/counter/spec.txt --run-id w2` |
| W2-09 | Resume after kill, no duplicated RTL version | [ ] | — | `main.py resume --run-id w2` |
| W2-10 | Ponytail audit #1 applied, checks still green | [ ] | — | `python tests/run_all.py` |

## Week 3 — feedback loop, generalisation, real STA, red team

| ID | Task | Status | Verify |
|---|---|---|---|
| W3-01 | Debug Agent with `evidence_quote`; fabricated quote is rejected | [ ] | `python tests/redteam.py` |
| W3-02 | Backward routing: sabotaged counter goes SIM→DEBUG→RTL→DONE, history length 2 | [ ] | `main.py run --spec examples/counter/spec.txt --inject-bug reset` |
| W3-03 | Retry prompt targets the reported bug, diff ≤10 lines | [ ] | `main.py diff --run-id fifo` |
| W3-04 | FIFO: real v1 FAIL → cited bug → real v2 PASS | [ ] | `main.py run --spec examples/fifo/spec.txt` |
| W3-05 | ALU reaches DONE or DONE-WITH-WARNING unattended | [ ] | `main.py run --spec examples/alu/spec.txt` |
| W3-06 | OpenSTA image builds and runs on arm64 and amd64 | [ ] | `docker run --rm siliconboard/sta:1.0 sta -version` |
| W3-07 | sky130 Liberty downloaded and cached | [ ] | `head -c 40 pdk/*.lib` |
| W3-08 | Timing Agent: verdict from the parser, LLM only explains | [ ] | `python tests/check_parsers.py` |
| W3-09 | Red team: fabricated quote, invented cell count, truncated JSON all rejected | [ ] | `python tests/redteam.py` |
| W3-10 | Ponytail debt ledger, no `no-trigger` markers | [ ] | `grep -rnE '# ponytail:' .` |

## Week 4 — deploy, rehearse, stretch

| ID | Task | Status | Verify |
|---|---|---|---|
| W4-01 | Server provisioned, EDA image built on the box | [ ] | `ssh … 'docker images \| grep siliconboard'` |
| W4-02 | `/etc/siliconboard/env` 0600 + systemd unit active | [ ] | `ssh … 'systemctl is-active siliconboard; stat -c %a /etc/siliconboard/env'` |
| W4-03 | SSH-triggered run leaves board.json and report.md on the server | [ ] | `ssh … 'python3 main.py run --spec examples/counter/spec.txt --run-id live1'` |
| W4-04 | Replay from a second session, zero LLM calls | [ ] | `ssh … 'python3 main.py replay --run-id live1'` |
| W4-05 | Dual-sim cross-check, Verilator and Icarus agree | [ ] | `main.py run … --cross-sim` |
| W4-06 | Waveform dump for the demo | [ ] | `ls -l runs/live1/waves/` |
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

## Your rate limits (fill in once, W1-06)

| Model | RPM | RPD | TPM |
|---|---|---|---|
| | | | |
# Demo script — 5 minutes

Every number below is copied from a real run in `runs/`. Nothing here is
rehearsed by hand. Rehearsal log: `evidence/W4-07-demo-rehearsal.txt`.

The one line that matters: **the models propose, the tools decide.**

---

## Beat 1 — the idea (0:00–0:30)

Natural-language spec goes in; verified Verilog plus real synthesis and timing
numbers come out. Ten agents, no framework between them: they share one Pydantic
`Board`, and a plain-Python orchestrator reads the Board's status and dispatches
who runs next.

Point at `board.py`. Every arrow in the state machine is one `if` in
`orchestrator.py`. No hidden control flow, no magic.

## Beat 2 — a run that passes on tool evidence (0:30–1:45)

```
$ .venv/bin/python main.py run --spec examples/counter/spec.txt --run-id w4-waves --waves --cross-sim
```

Read the event log out loud, and land these four:

```
event   [linting]         lint: clean, 0 warnings
event   [simulating]      sim: checks=7 failed=0 verdict=True
event   [simulating]      cross-check: icarus says True (verilator said True)
event   [analyzing_timing] timing: slack=3.34 ns target=200.0 MHz MET via OpenSTA
```

The line to say: *"That PASS is Verilator's exit code. The slack is a real
OpenSTA run against sky130 liberty. No model was asked whether it passed."*

Two engines agreeing is the strongest claim in the demo — Verilator and Icarus
are independent implementations, and a bug that hides from both is not a
simulator artefact.

## Beat 3 — the failure and the fix (1:45–3:45) — **the centrepiece**

The same spec, this time with a bug injected into the RTL before lint:

```
$ .venv/bin/python main.py run --spec examples/counter/spec.txt --run-id demo-loop --inject-bug reset --waves
```

Show the failure, from the real log:

```
event   [simulating]  sim: checks=11 failed=2 verdict=False
event   [debugging]   bug: reset_mid — The reset logic never activates because
                      the RTL checks a constant 1'b0 instead of the rst_n signal
```

Now the part that makes it credible — the model's evidence quote is a literal
substring of the simulator's own output, and the harness refuses the report if
it isn't:

```
$ .venv/bin/python -c "import json;print(json.load(open('runs/demo-loop/board.json'))['bug_history'][0]['evidence_quote'])"
CHECK reset_mid FAIL got=5 want=0
```

Then the fix, and keep this on screen while you talk:

```
$ .venv/bin/python main.py diff --run-id demo-loop
-    if (1'b0)
+    if (!rst_n)
2 changed lines (v1 -> v2)
```

*"The debug agent was told to isolate the smallest change. It found one wrong
line and changed one line. The simulator confirmed it — 11 of 11 now."*

Open the waveform if there's time: `runs/demo-loop/waves/dump.vcd`.

## Beat 4 — what it cost and what it produced (3:45–4:30)

```
module  up_counter8, 4 ports
synth   28 cells
timing  slack 3.34 ns @ 200 MHz, met
llm     5 calls, every one logged with model, key slot and token counts
```

Point out the model in the trace: cheap models do the extraction and the
narration, the heavy one writes and debugs RTL. Every call is on the board, so
the cost is auditable rather than asserted.

## Beat 5 — the part I'd defend in a design review (4:30–5:00)

```
$ .venv/bin/python main.py replay --run-id w4-waves
```

Re-renders the report from `board.json` with the API keys stripped from the
environment. Byte-identical output, zero model calls.

Then: *"Everything I just showed you is in `runs/<id>/board.json` — every
version of the RTL, every simulator log, every bug report with its evidence,
every model call. If you doubt any number on screen, the file has the raw
material behind it."*

---

## Rehearsal

| Run | What it proves | Result |
|-----|----------------|--------|
| 1 | full live path, counter → PASS | 3:50 |
| 2 | the centrepiece, FAIL → 2-line fix → PASS | 4:35 |
| 3 | airplane mode: network off, `replay` only | 2:10 |

**If anything goes wrong on stage:** the golden runs in `demo/golden/` carry
`board.json` and `report.md` for all three. Replay from those and narrate. The
live path is the preferred version of the demo, never the only one.

## Also worth having ready, if a question comes up

- `runs/w3-fifo` — 8-deep FIFO, 17 checks, 189 cells, unattended
- `runs/w3-alu` — combinational ALU, 210 cells
- `tests/check_toolonly.py` — asserts the simulation and synthesis agents
  contain no model calls at all, and runs the cross-check disagreement case

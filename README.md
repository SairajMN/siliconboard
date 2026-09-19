# siliconboard

Multi-agent chip design system. One shared Pydantic `Board` holds all design
state, a deterministic Python `Orchestrator` reads the Board's status and
dispatches single-job agents, and the two agents that decide correctness call
real EDA tools with no LLM involved.

Rule the whole project is built around: **LLM agents propose, tools decide.**
`passed` comes from a Verilator log, `clean` from a Verilator exit code,
`cell_count` from a Yosys `stat`, `worst_slack_ns` from an OpenSTA parse. Gemini
never gets to report a test result.

No agent framework: no LangChain, no LangGraph, no CrewAI, no ADK. The loop,
the routing and the retry policy live in `orchestrator.py` and are ours.

## Quickstart

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt

docker build -t siliconboard/eda:1.0 docker/eda      # yosys + verilator + iverilog

cp .env.example .env && chmod 600 .env               # add GEMINI_API_KEY_1..4
.venv/bin/python main.py doctor                      # keys, docker, tool versions
```

## Checks

```bash
.venv/bin/python tests/run_all.py          # board, parsers, Gemini schema canary
.venv/bin/python tests/check_toolchain.py  # real lint, real sim, real synthesis
```

`check_toolchain.py` is the trust anchor: until it is green, nothing the LLM
says matters. It lints a hand-written module, runs a real Verilator binary
simulation and asserts exactly one terminal `PASS`, then synthesizes and
asserts that a cell count parses.

## Layout

| Path | What lives there |
|---|---|
| `board.py` | `Board`, `DesignStatus`, every artifact and report model |
| `tools.py` | the one docker wrapper plus every parser that decides a fact |
| `agent.py` | one agent shape, prompt builder, log trimmer |
| `llm.py` | the whole Gemini integration: one function, key pool, per-run cache |
| `orchestrator.py` | forward/backward routing, retry policy, Board persistence |
| `agents/` | one file per agent, one job per file |
| `smoke/` | hand-written RTL + TB that prove the toolchain, no LLM involved |
| `tests/` | assert-based checks, no test framework |
| `evidence/` | raw output of each verified task |
| `TASKS.md` | the ledger: every task, its verify command, its evidence file |

## Conventions

Two dependencies, `google-genai` and `pydantic`. Stdlib and the container do
the rest. Comments explain *why*, never *what*; deferred work is marked
`# ponytail: <ceiling>, upgrade when <trigger>` so it can be harvested into a
debt ledger instead of rotting.

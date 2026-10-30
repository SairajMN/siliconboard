# siliconboard

Multi-agent chip design system. One shared Pydantic `Board` holds all design
state, a deterministic Python `Orchestrator` reads the Board's status and
dispatches single-job agents, and the two agents that decide correctness call
real EDA tools with no LLM involved.

Rule the whole project is built around: **LLM agents propose, tools decide.**
`passed` comes from a Verilator log, `clean` from a Verilator exit code,
`cell_count` from a Yosys `stat`, `worst_slack_ns` from an OpenSTA parse. The model
never gets to report a test result. Gemini is the primary provider, but the
router falls back through Groq and NVIDIA, so a credit or rate-limit wall on one
provider doesn't stop a run.

No agent framework: no LangChain, no LangGraph, no CrewAI, no ADK. The loop,
the routing and the retry policy live in `orchestrator.py` and are ours.

## Quickstart

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt

docker build -t siliconboard/eda:1.0 docker/eda      # yosys + verilator + iverilog

cp .env.example .env && chmod 600 .env               # add GEMINI_API_KEY_1..4
.venv/bin/python main.py doctor                      # keys, docker, tool versions
```
## Call Flow — End-to-End Request

```mermaid
sequenceDiagram
    participant M as Mentor Browser
    participant CF as Cloudflare Edge
    participant TU as Tunnel (cloudflared)
    participant UI as Python Server (127.0.0.1:8080)
    participant H as LLM Harness
    participant EDA as EDA Container

    M->>CF: GET https://...trycloudflare.com
    CF-->>M: HTML + password prompt
    M->>CF: POST /api/login {password}
    CF->>TU: forward (outbound)
    TU->>UI: POST /api/login
    UI->>UI: check_password() - constant-time compare
    UI-->>TU: 200 ok + sb_session cookie
    TU-->>CF-->>M: token saved in sessionStorage
    M->>CF: POST /api/run {spec, run-id}
    CF->>TU->>UI: POST /api/run
    UI->>UI: verify cookie
    UI->>H: start_run()
    H->>EDA: lint / sim / synth / sta
    EDA-->>H: tool results (PASS/FAIL, cells, slack)
    H->>H: write board.json entries
    UI-->>TU-->>CF-->>M: run-id returned
    M->>CF: GET /api/runs
    CF->>TU->>UI: GET /api/runs
    UI->>UI: read runs/<id>/board.json
    UI-->>TU-->>CF-->>M: timeline + output tabs
```

## Running a design

```bash
.venv/bin/python main.py run --spec examples/fifo/spec.txt --run-id demo1 \
    --cross-sim --waves                      # two simulators, waveform dumped
.venv/bin/python main.py diff --run-id demo1 # what the debug loop changed
.venv/bin/python main.py replay --run-id demo1   # re-render the report, no LLM calls
.venv/bin/python main.py waves                  # which runs dumped a waveform, and how to open it
```

Every run leaves `runs/<id>/board.json` — the whole design history, one entry per
RTL version, simulation, bug report and tool log — so any claim the system makes
about its own work can be checked against what actually happened.

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
| `llm.py` | the whole LLM integration: one function, model fallback chains, key rings, per-run cache |
| `orchestrator.py` | forward/backward routing, retry policy, Board persistence |
| `agents/` | one file per agent, one job per file |
| `smoke/` | hand-written RTL + TB that prove the toolchain, no LLM involved |
| `tests/` | assert-based checks, no test framework |
| `evidence/` | raw output of each verified task |
| `demo/` | the 5-minute script and the golden run snapshots it replays |
| `deploy/` | one-command laptop deploy (`deploy.sh`), server bootstrap, systemd unit, compose file |
| `ui/` | web UI: per-agent timeline renderer, stdlib HTTP server, one static page, ssh tunnel helper |
| `TASKS.md` | the ledger: every task, its verify command, its evidence file |
| `PONYTAIL-DEBT.md` | every shortcut still in the tree, with the trigger that retires it |

## Seeing the design

A run is four things you can open: the report, the board, a VCD waveform, and —
over ssh — the web UI.

### Web UI

The server (`ui/server.py`, stdlib only) binds **127.0.0.1:8080 and nothing
else**. Two credentials, never both at once:

- **no `SB_UI_PASSWORD`** — the shared token in the printed URL (local dev)
- **`SB_UI_PASSWORD` set** — only the password, via `POST /api/login`:
  constant-time compare, five wrong tries lock the gate for a minute, and the
  token stops working entirely. The session is an `HttpOnly; SameSite=Strict`
  cookie; the password never appears in a URL or in storage.

There are two ways to reach that loopback port:

```bash
bash ui/tunnel.sh              # laptop: ssh tunnel, prints the local url
bash ui/tunnel.sh --phone      # also reachable from a phone/tablet on your Wi-Fi
```

or, for a link anyone can open, the public door — an **outbound Cloudflare
quick tunnel** that `deploy/deploy.sh` installs and health-checks:

```bash
UI_PASSWORD='...' bash deploy/deploy.sh     # set/rotate the password + publish
```

It gives you an `https://….trycloudflare.com` URL while the box still listens
on **127.0.0.1 only**: no inbound port, no security-group rule (still just
`tcp/22` from your IP), TLS terminated at the edge. The password is the whole
security boundary — anyone with the URL but not the password gets `401`.

What it gives you:

- **live workflow** — which agent is working right now, how long each agent
  took, what it produced (module, ports, checks, cells, slack), streamed from
  `board.json` as the run writes it
- **first-use instructions** on the page, suggested specs you can click, and a
  golden run you can browse before spending a cent on tokens
- **output in multiple formats** — report.md, board.json, syntax-highlighted
  Verilog, ASCII waveform, tool logs — all read from the run's own artifacts
- **history** — every run paired with the input that produced it

`deploy/deploy.sh` installs and health-checks the `siliconboard-ui` unit
(loopback only) plus the `siliconboard-tunnel` unit when publishing. Keys
never reach the browser.

### Terminal

A run is also three files you can open without any UI: the report, the board,
and — with `--waves` — a VCD waveform.

```bash
.venv/bin/python main.py run --spec examples/counter/spec.txt --run-id w1 --waves
.venv/bin/python main.py waves                      # every run that dumped one
.venv/bin/python main.py waves --run-id w1          # signals, widths, time span
.venv/bin/python main.py waves --run-id w1 --draw   # draw it here, no viewer needed
.venv/bin/python main.py waves --run-id w1 --open   # launch a GUI viewer, if one is installed
```

`--draw` is the default answer: it parses the VCD itself and prints a timing
diagram in the terminal, so nothing has to be installed. Buses also get their
value sequence printed underneath, so `count` reads as `0 1 2 … 255` rather
than a smear of overlapping labels.

There is no `brew install` for a VCD viewer that works — `surfer` has no cask
and `gtkwave` is disabled in Homebrew. If you want a real GUI, `surfer`
(the VCD viewer at `github.com/thesurver/surfer`) ships a `.dmg` on its
releases page. `--open` uses `surfer`, `gtkwave` or `vcd2fst` if any is present.

`--draw` is behaviour over time, not a schematic. For an 8-bit counter a
picture of 28 cells would tell you less than `main.py diff` does.

## Conventions

## If-Struct — Routing & Conditional Logic

```mermaid
flowchart TD
    Start([API Request])

    AuthGate{SB_UI_PASSWORD configured?}

    NoAuth[Token mode: accept ?t= shared URL token]
    YesAuth[Password mode: POST /api/login required]

    CookieValid{sb_session cookie valid?}

    Locked{Fails >= 5?}

    Lockout[Gate locked: 60 seconds 401 to all]

    WrongPW[Wrong password: fails++ 401 with tries left]

    CorrectPW[Password matches: constant-time issue sb_session cookie]

    ProtectedPath{Route matched?}

    PublicGET[GET / -> 200 public landing]

    AuthProbe[GET /api/auth -> {mode: password}]

    RunAPI[POST /api/run -> start_run() agent loop]

    RunsAPI[GET /api/runs -> read board.json]

    Denied[401 password required]

    Start --> AuthGate
    AuthGate -->|no| NoAuth
    AuthGate -->|yes| YesAuth
    YesAuth --> CookieValid
    CookieValid -->|yes| ProtectedPath
    CookieValid -->|no| Locked
    Locked -->|yes| Lockout
    Locked -->|no| WrongPW
    WrongPW -->|fails < 5| WrongPW
    WrongPW -->|fails >= 5| Locked
    CorrectPW --> ProtectedPath
    ProtectedPath -->|GET /| PublicGET
    ProtectedPath -->|GET /api/auth| AuthProbe
    ProtectedPath -->|POST /api/run| RunAPI
    ProtectedPath -->|GET /api/runs| RunsAPI
    ## Harness Detail — The Agent Loop

```mermaid
flowchart TB
    Start((Run Start))
    Spec[Spec Agent<br/>plain English -> design spec]
    RTL[RTL Agent<br/>spec -> Verilog]
    Lint[Lint Agent<br/>Verilog -> lint pass]
    TB[Testbench Agent<br/>module -> testbench]
    Sim[Simulate Agent<br/>TB -> iverilog run]
    Debug[Debug Agent<br/>failure -> patch RTL]
    Synth[Synthesize Agent<br/>Verilog -> Yosys stat]
    STA[Timing Agent<br/>netlist -> OpenSTA slack]
    Report[Report Agent<br/>artifacts -> report.md]
    End((Run End))

    Start --> Spec
    Spec --> RTL
    RTL --> Lint
    Lint -->|fail| Debug
    Lint -->|pass| TB
    TB --> Sim
    Sim -->|fail| Debug
    Sim -->|pass| Synth
    Debug --> RTL
    Synth --> STA
    STA -->|fail| Debug
    STA -->|pass| Report
    Report --> End

    subgraph Tools[Tools decide, LLM proposes]
        Lint
        Sim
        Synth
        STA
    end
```

---\n\n## Conventions

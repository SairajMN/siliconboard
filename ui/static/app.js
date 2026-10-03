/* siliconboard ui. No framework, no build: the server is stdlib, this is one file.
   The token travels in the URL the server prints and is reused on every call. */

(() => {
  "use strict";

  const $ = (sel, root = document) => root.querySelector(sel);
  const params = new URLSearchParams(location.search);
  let TOKEN = params.get("t") || sessionStorage.getItem("sb-token") || "";
  if (params.get("t")) sessionStorage.setItem("sb-token", params.get("t"));

  const state = { runId: null, stream: null, timeline: null, tab: "report" };

  function api(path, options = {}) {
    const url = new URL(path, location.origin);
    if (TOKEN) url.searchParams.set("t", TOKEN);
    return fetch(url, options).then(async (res) => {
      const body = await res.json().catch(() => ({}));
      if (res.status === 401) { unlock(); throw new Error("token required"); }
      if (!res.ok) throw new Error(body.error || `${res.status} ${res.statusText}`);
      return body;
    });
  }

  /* Show the token prompt. Any 401 lands here, so a stale or missing token is
     recoverable in place instead of a page of red console errors. */
  function unlock() {
    $("#tok").hidden = false;
    $("#hint").textContent = "Locked — paste the access token below.";
    $("#hint").className = "hint warn";
    $("#tokin").focus();
  }

  const fileUrl = (runId, path) =>
    `/api/runs/${encodeURIComponent(runId)}/file?path=${encodeURIComponent(path)}&t=${encodeURIComponent(TOKEN)}`;

  function seconds(value) {
    if (value === null || value === undefined) return "";
    if (value < 60) return `${value}s`;
    return `${Math.floor(value / 60)}m ${String(value % 60).padStart(2, "0")}s`;
  }

  function esc(text) {
    return String(text ?? "").replace(/[&<>"']/g, (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  }

  function el(tag, attrs = {}, children = []) {
    const node = document.createElement(tag);
    for (const [key, value] of Object.entries(attrs)) {
      if (key === "class") node.className = value;
      else if (key === "text") node.textContent = value;
      else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
      else node.setAttribute(key, value);
    }
    for (const child of [].concat(children)) {
      if (child != null) node.append(child.nodeType ? child : document.createTextNode(child));
    }
    return node;
  }

  /* ------------------------------------------------------------------ boot */
  function boot() {
    $("#tok-btn").addEventListener("click", () => {
      const value = $("#tokin").value.trim();
      if (!value) return;
      TOKEN = value;
      sessionStorage.setItem("sb-token", value);
      $("#tok").hidden = true;
      $("#hint").textContent = "Token saved. Runs on the box. Leaves runs/<id>/board.json and a report.";
      $("#hint").className = "hint";
      loadSystem(); loadExamples(); loadRuns();
    });
    if (!TOKEN) {
      unlock();
    }
    if (!localStorage.getItem("sb-seen")) {
      $("#overlay").hidden = false;
    }
    $("#ob-close").addEventListener("click", () => {
      $("#overlay").hidden = true;
      localStorage.setItem("sb-seen", "1");
    });
    $("#help-btn").addEventListener("click", () => { $("#overlay").hidden = false; });
    $("#new-btn").addEventListener("click", () => {
      state.runId = null;
      state.timeline = null;
      if (state.stream) state.stream.close();
      $("#req").value = "";
      $("#runid").value = "";
      $("#work").hidden = true;
      $("#empty").hidden = false;
      $("#req").focus();
      paintRail();
    });
    $("#run-btn").addEventListener("click", startRun);
    $("#refresh").addEventListener("click", () => state.runId && openRun(state.runId));
    if (TOKEN) {          // no token: the prompt is up, don't fire 401s at the console
      loadSystem();
      loadExamples();
      loadRuns();
    }
  }

  function loadSystem() {
    api("/api/system").then((info) => {
      const keys = Object.entries(info.keys || {}).map(([p, n]) => `${p} ${n}`).join(" · ");
      const tools = (info.tools || []).length;
      $("#sys").innerHTML = `${esc(keys || "no keys")} <b>·</b> ${tools ? tools + " tools" : "tools ?"}`;
      if (!info.ready && info.note) {
        $("#hint").textContent = "doctor: " + info.note;
        $("#hint").className = "hint warn";
      }
    }).catch(() => { $("#sys").textContent = ""; });
  }
/* ------------------------------------------------------------------ rail */
  let EXAMPLES = [];
  let RUNS = [];

  function loadExamples() {
    api("/api/examples").then((data) => {
      EXAMPLES = data.examples || [];
      paintRail();
    }).catch(() => paintRail());
  }

  function loadRuns() {
    api("/api/runs").then((data) => {
      RUNS = data.runs || [];
      paintRail();
    }).catch(() => paintRail());
  }

  function statusChip(row) {
    const map = { done: "ok", failed: "err", done_with_warning: "warn" };
    const cls = row.finished ? (map[row.status] || "") : "run";
    const label = row.finished ? row.status.replace(/_/g, " ") : "running";
    return el("span", { class: `chip ${cls}`, text: label });
  }

  function runRow(row) {
    const bits = [];
    if (row.module) bits.push(row.module);
    if (row.checks != null) bits.push(`sim ${row.failed ?? 0} fail / ${row.checks}`);
    if (row.cells != null) bits.push(`${row.cells} cells`);
    const btn = el("button", { class: "row-btn", type: "button", "aria-current": String(state.runId === row.run_id) }, [
      el("span", { class: "t", text: row.run_id }),
      el("span", { class: "d" }, [
        bits.join(" · ") || row.input.slice(0, 40) || "—",
      ]),
    ]);
    btn.addEventListener("click", () => openRun(row.run_id));
    return btn;
  }

  function group(title, note, rows) {
    if (!rows.length) return null;
    return el("div", {}, [
      el("div", { class: "group-title" }, [el("span", { text: title }), note ? el("span", { text: note }) : null]),
      ...rows.map(runRow),
    ]);
  }

  function paintRail() {
    const suggest = $("#suggest");
    suggest.innerHTML = "";
    if (EXAMPLES.length) {
      const chips = el("div", { class: "chips" });
      for (const example of EXAMPLES) {
        const chip = el("button", { class: "chip-btn", type: "button", text: example.id });
        chip.addEventListener("click", () => {
          $("#req").value = example.text;
          $("#runid").value = example.id + "-" + Math.random().toString(36).slice(2, 6);
          $("#req").focus();
          $("#hint").textContent = `Loaded the ${example.id} example. Press Run pipeline.`;
          $("#hint").className = "hint";
        });
        chips.append(chip);
      }
      suggest.append(el("div", { class: "group-title" }, [el("span", { text: "Suggested inputs" })]), chips);
    }

    const samples = $("#samples");
    samples.innerHTML = "";
    const golden = group("Sample output", "no tokens spent", RUNS.filter((r) => r.kind === "sample"));
    if (golden) samples.append(golden);

    const hist = $("#hist");
    hist.innerHTML = "";
    const real = RUNS.filter((r) => r.kind === "run");
    if (real.length) hist.append(group("History", `${real.length}`, real));
    else hist.append(el("div", { class: "empty-note", text: "No runs yet. Your first run lands here with its input." }));
  }
/* ------------------------------------------------------------------ run */
  function setHint(text, warn) {
    $("#hint").textContent = text;
    $("#hint").className = warn ? "hint warn" : "hint";
  }

  function startRun() {
    if (!TOKEN) return setHint("No token: reopen the URL the server printed.", true);
    const request = $("#req").value.trim();
    const runId = ($("#runid").value.trim() || "ui-" + Date.now().toString(36));
    if (!request) return setHint("Write what the block should do first.", true);
    const btn = $("#run-btn");
    btn.disabled = true;
    btn.textContent = "Starting…";
    api("/api/run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ run_id: runId, request, cross_sim: $("#cross").checked, waves: $("#waves").checked }),
    }).then(() => {
      setHint(`Running ${runId}. The agents below update live.`);
      openRun(runId);
      loadRuns();
    }).catch((err) => setHint(err.message, true))
      .finally(() => { btn.disabled = false; btn.textContent = "Run pipeline"; });
  }

  function openRun(runId) {
    state.runId = runId;
    $("#work").hidden = false;
    $("#empty").hidden = true;
    paintRail();
    if (state.stream) state.stream.close();
    loadOnce(runId);
    if (TOKEN) {
      const stream = new EventSource(
        `/api/stream?run=${encodeURIComponent(runId)}&t=${encodeURIComponent(TOKEN)}`);
      state.stream = stream;
      stream.onmessage = (event) => {
        const data = JSON.parse(event.data);
        render(data);
        if (data.finished) { stream.close(); loadRuns(); loadOutputs(runId); }
      };
      stream.onerror = () => stream.close(); // server ends the stream when the run is done
    }
    document.title = `${runId} · siliconboard`;
  }

  function loadOnce(runId) {
    api(`/api/runs/${encodeURIComponent(runId)}`)
      .then((data) => { render(data); if (data.finished) loadOutputs(runId); })
      .catch((err) => setHint(err.message, true));
  }

  function render(data) {
    state.timeline = data;
    const chip = statusChip(data);
    const header = $("#work-state");
    header.className = chip.className;
    header.textContent = chip.textContent;
    $("#work-id").textContent = data.run_id;
    const first = (data.input || "").trim().split("\n")[0];
    $("#work-sub").textContent = [data.summary.module, first].filter(Boolean).join(" — ").slice(0, 160);
    renderStrip(data);
    renderTimeline(data);
  }

  function renderStrip(data) {
    const s = data.summary || {};
    const cells = [];
    const add = (key, value, cls) => value != null && value !== "" && cells.push(el("div", { class: "cell" }, [
      el("div", { class: "k", text: key }),
      el("div", { class: "v " + (cls || ""), text: String(value) }),
    ]));
    add("sim", s.sim_passed == null ? null : (s.sim_passed ? "PASS" : "FAIL"), s.sim_passed ? "ok" : "err");
    add("checks", s.checks == null ? null : `${s.checks - (s.failed_checks || 0)}/${s.checks}`);
    add("cross-sim", s.cross_agreed == null ? null : (s.cross_agreed ? "agreed" : "disagreed"),
      s.cross_agreed === false ? "err" : "ok");
    add("cells", s.cells);
    add("slack", s.slack_ns == null ? null : `${s.slack_ns} ns`);
    add("rtl versions", s.rtl_versions);
    add("bugs found", s.bugs);
    add("llm calls", s.llm_calls);
    $("#strip").replaceChildren(...cells);
  }

  function renderTimeline(data) {
    const list = $("#timeline");
    list.replaceChildren(...(data.stages || []).map(stepNode));
  }

  function stepNode(stage) {
    const item = el("li", { class: `step ${stage.state}` });
    const timing = stage.state === "running"
      ? `⏱ ${seconds(stage.seconds) || "…"}`
      : stage.seconds == null ? "" : seconds(stage.seconds);
    item.append(el("div", { class: "step-head" }, [
      el("span", { class: "dot" }),
      el("span", { class: "step-name", text: stage.label }),
      el("span", { class: "step-agent", text: stage.agent }),
      el("span", { class: "step-time", text: timing }),
    ]));

    const body = el("div", { class: "step-body" });
    for (const line of stage.lines || []) body.append(el("div", { class: "step-line", text: line }));
    if ((stage.details || []).length) {
      const dl = el("dl", { class: "kv" });
      for (const [key, value] of stage.details) {
        dl.append(el("dt", { text: key }), el("dd", { text: value }));
      }
      body.append(dl);
    }
    for (const call of stage.llm || []) {
      const tokens = call.tokens_in == null ? "" : ` · ${call.tokens_in}→${call.tokens_out ?? "?"} tok`;
      body.append(el("div", { class: "llm" }, [
        `${call.model} [slot ${call.slot}]${tokens}`,
        call.cached ? el("span", { class: "cached", text: " · cached" }) : null,
      ]));
    }
    if (body.childNodes.length) item.append(body);
    return item;
  }
/* ------------------------------------------------------------------ output */
  let OUTPUT = { report: null, json: null, verilog: [], waves: null, wavesError: null };

  function loadOutputs(runId) {
    const arts = (state.timeline && state.timeline.artifacts) || [];
    OUTPUT = { report: null, json: null, verilog: [], waves: null, wavesError: null };

    const report = arts.find((a) => a.label === "report.md");
    if (report) {
      fetch(fileUrl(runId, report.path)).then((r) => r.text())
        .then((text) => { OUTPUT.report = text; if (state.tab === "report") paintTab(); });
    }
    const board = arts.find((a) => a.label === "board.json");
    if (board) {
      fetch(fileUrl(runId, board.path)).then((r) => r.text()).then((text) => {
        try { OUTPUT.json = JSON.stringify(JSON.parse(text), null, 2); } catch { OUTPUT.json = text; }
        if (state.tab === "json") paintTab();
      });
    }
    const sources = arts.filter((a) => a.path.startsWith("work/") && a.path.endsWith(".v"));
    sources.forEach((a, index) => fetch(fileUrl(runId, a.path)).then((r) => r.text()).then((text) => {
      OUTPUT.verilog[index] = { name: a.label, text };
      if (state.tab === "verilog") paintTab();
    }));
    const wave = arts.find((a) => a.path.endsWith(".vcd"));
    if (wave) {
      api(`/api/runs/${encodeURIComponent(runId)}/waves`)
        .then((drawn) => { OUTPUT.waves = drawn; if (state.tab === "waves") paintTab(); })
        .catch((err) => { OUTPUT.wavesError = err.message; if (state.tab === "waves") paintTab(); });
    }
    paintTabs();
  }

  function tabList() {
    const tabs = [{ id: "report", label: "Report" }, { id: "verilog", label: "Verilog" }];
    if (OUTPUT.waves || OUTPUT.wavesError) tabs.push({ id: "waves", label: "Waveform" });
    tabs.push({ id: "evidence", label: "Evidence" }, { id: "json", label: "board.json" }, { id: "files", label: "Files" });
    return tabs;
  }

  function paintTabs() {
    const tabs = tabList();
    if (!tabs.some((t) => t.id === state.tab)) state.tab = tabs[0].id;
    const bar = $("#tabs");
    bar.replaceChildren(...tabs.map((t) => {
      const button = el("button", {
        class: "tab", type: "button", role: "tab", text: t.label,
        "aria-selected": String(state.tab === t.id),
      });
      button.addEventListener("click", () => { state.tab = t.id; paintTabs(); });
      return button;
    }));
    paintTab();
  }

  function paintTab() {
    const panel = $("#panel");
    panel.replaceChildren();
    const data = state.timeline || {};
    if (state.tab === "report") paintReport(panel, data);
    else if (state.tab === "verilog") paintVerilog(panel);
    else if (state.tab === "waves") paintWaves(panel, data);
    else if (state.tab === "evidence") paintEvidence(panel, data);
    else if (state.tab === "json") panel.append(codeBlock(OUTPUT.json || "loads once the run finishes"));
    else if (state.tab === "files") paintFiles(panel, data);
  }

  function bar(children) {
    return el("div", { class: "bar" }, children);
  }

  function downloadButton(label, text, filename, mime) {
    const button = el("button", { class: "ghost tiny", type: "button", text: label });
    button.addEventListener("click", () => {
      const blob = new Blob([text], { type: mime || "text/plain" });
      const a = el("a", { href: URL.createObjectURL(blob), download: filename });
      document.body.append(a); a.click(); a.remove();
    });
    return button;
  }

  function codeBlock(text) {
    return el("pre", { text });
  }
/* ------------------------------------------------------------------ painters */
  function inline(text) {
    return esc(text)
      .replace(/`([^`]+)`/g, "<code>$1</code>")
      .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
  }

  function mdToHtml(md) {
    const lines = md.split("\n");
    const out = [];
    const cells = (row) => row.replace(/^\s*\|/, "").replace(/\|\s*$/, "").split("|").map((c) => c.trim());
    let i = 0;
    while (i < lines.length) {
      const line = lines[i];
      if (line.includes("|") && i + 1 < lines.length && /^[\s|:-]+$/.test(lines[i + 1]) && lines[i + 1].includes("-")) {
        const rows = [];
        while (i < lines.length && lines[i].includes("|")) rows.push(lines[i++]);
        let html = "<table><thead><tr>" + cells(rows[0]).map((c) => `<th>${inline(c)}</th>`).join("") + "</tr></thead><tbody>";
        for (const row of rows.slice(2)) html += "<tr>" + cells(row).map((c) => `<td>${inline(c)}</td>`).join("") + "</tr>";
        out.push(html + "</tbody></table>");
        continue;
      }
      const heading = line.match(/^(#{1,6})\s+(.*)$/);
      if (heading) {
        const level = Math.min(heading[1].length, 3);
        out.push(`<h${level}>${inline(heading[2])}</h${level}>`);
        i++; continue;
      }
      if (/^\s*[-*]\s+/.test(line)) {
        out.push("<ul>");
        while (i < lines.length && /^\s*[-*]\s+/.test(lines[i])) {
          out.push(`<li>${inline(lines[i].replace(/^\s*[-*]\s+/, ""))}</li>`); i++;
        }
        out.push("</ul>"); continue;
      }
      if (/^\s*(-{3,}|\*{3,})\s*$/.test(line)) { out.push("<hr>"); i++; continue; }
      if (!line.trim()) { i++; continue; }
      const para = [line]; i++;
      while (i < lines.length && lines[i].trim() && !/^(#{1,6}\s|\s*[-*]\s)/.test(lines[i]) && !lines[i].includes("|")) {
        para.push(lines[i]); i++;
      }
      out.push(`<p>${inline(para.join(" "))}</p>`);
    }
    return out.join("\n");
  }

  function copyButton(text) {
    const button = el("button", { class: "ghost tiny", type: "button", text: "Copy" });
    button.addEventListener("click", () => {
      navigator.clipboard?.writeText(text).then(() => { button.textContent = "Copied"; setTimeout(() => (button.textContent = "Copy"), 1200); });
    });
    return button;
  }

  function paintReport(panel, data) {
    if (OUTPUT.report == null) {
      panel.append(el("p", { class: "empty-note", text: data.finished ? "No report (the run stopped before narration)." : "Report is written once the design is finished." }));
      return;
    }
    panel.append(bar([
      downloadButton("Download .md", OUTPUT.report, `${data.run_id}-report.md`, "text/markdown"),
      copyButton(OUTPUT.report),
    ]));
    const md = el("div", { class: "md" });
    md.innerHTML = mdToHtml(OUTPUT.report);
    panel.append(md);
  }

  function paintVerilog(panel) {
    const sources = OUTPUT.verilog.filter(Boolean);
    if (!sources.length) {
      panel.append(el("p", { class: "empty-note", text: "No Verilog on disk yet." }));
      return;
    }
    for (const source of sources) {
      panel.append(bar([
        el("span", { class: "sp", text: source.name }),
        downloadButton("Download", source.text, source.name, "text/plain"),
        copyButton(source.text),
      ]));
      panel.append(codeBlock(source.text));
    }
  }
  function paintWaves(panel, data) {
    if (OUTPUT.wavesError) {
      panel.append(el("p", { class: "empty-note", text: OUTPUT.wavesError }));
      return;
    }
    if (!OUTPUT.waves) {
      panel.append(el("p", { class: "empty-note", text: "This run dumped no waveform. Re-run with the waveform box ticked." }));
      return;
    }
    const waves = OUTPUT.waves;
    const vcd = (data.artifacts || []).find((a) => a.path.endsWith(".vcd"));
    const actions = [el("span", { class: "sp", text: `${waves.file} · ${waves.timescale} · ${waves.span}` })];
    if (vcd) {
      actions.push(el("a", { class: "ghost tiny", href: fileUrl(data.run_id, vcd.path), download: vcd.label, text: "Download .vcd" }));
    }
    panel.append(bar(actions));
    panel.append(codeBlock((waves.lines || []).join("\n")));
    const sig = el("div", { class: "sig" });
    for (const signal of waves.signals || []) {
      sig.append(el("span", { class: "w", text: String(signal.width) }), el("span", { text: signal.name }));
    }
    panel.append(el("h3", { class: "cap", text: "Signals" }), sig);
  }

  function paintEvidence(panel, data) {
    const stages = (data.stages || []).filter((s) => s.state !== "queued");
    const table = el("table");
    table.innerHTML = "<thead><tr><th>agent</th><th>time</th><th>did</th></tr></thead>";
    const body = el("tbody");
    for (const stage of stages) {
      const did = (stage.details || []).map(([k, v]) => `${k}: ${v}`).join("; ") || (stage.lines || []).join("; ");
      body.append(el("tr", {}, [
        el("td", { text: stage.agent }), el("td", { text: seconds(stage.seconds) || "—" }), el("td", { text: did }),
      ]));
    }
    table.append(body);
    panel.append(table);

    const calls = stages.flatMap((s) => s.llm || []);
    if (calls.length) {
      panel.append(el("h3", { class: "cap", text: "Model usage" }));
      const usage = el("table");
      usage.innerHTML = "<thead><tr><th>model</th><th>slot</th><th>tokens in</th><th>tokens out</th><th>cached</th></tr></thead>";
      const ubody = el("tbody");
      for (const call of calls) {
        ubody.append(el("tr", {}, [
          el("td", { text: call.model }), el("td", { text: String(call.slot) }),
          el("td", { text: String(call.tokens_in ?? "?") }), el("td", { text: String(call.tokens_out ?? "?") }),
          el("td", { text: call.cached ? "yes" : "no" }),
        ]));
      }
      usage.append(ubody);
      panel.append(usage);
    }
    panel.append(el("h3", { class: "cap", text: "Event log" }));
    panel.append(codeBlock((data.events || []).join("\n")));
  }

  function paintFiles(panel, data) {
    const artifacts = data.artifacts || [];
    if (!artifacts.length) {
      panel.append(el("p", { class: "empty-note", text: "Nothing written yet." }));
      return;
    }
    const grid = el("div", { class: "files" });
    for (const artifact of artifacts) {
      grid.append(
        el("span", { class: "g", text: artifact.group }),
        el("a", { href: fileUrl(data.run_id, artifact.path), download: artifact.label, text: artifact.path }),
      );
    }
    panel.append(grid);
  }

  boot();
})();
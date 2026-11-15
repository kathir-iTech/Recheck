const cases = [
  ["normal", "Normal operation", "The controller replied and the station advanced once."],
  ["before_action_retry", "Crash before action · retry", "The first controller exited before acting. The replacement applied the command once."],
  ["before_action_suppressed", "Crash before action · no retry", "The controller exited before acting. Suppressing the retry left the carton in place."],
  ["after_action_blind_retry", "Crash after action · blind retry", "The station had already advanced. Retrying the same command moved it a second time."],
  ["after_action_protected_retry", "Crash after action · protected", "The station recognized the original command ID. The retry added no second advance."],
  ["after_reply_control", "Crash after reply", "The caller received an acknowledgement before the controller exited."]
];
const labels = {pass: "Release check passed", regression: "Release blocked", incomplete: "Test incomplete", canceled: "Run canceled"};
const descriptions = Object.fromEntries(cases.map(([id, name, reason]) => [id, {name, reason}]));
const forteCases = [
  ["after_action_blind_retry", "Blind retry"],
  ["after_action_protected_retry", "Protected retry"]
];
const stationCases = [
  [forteCases[0][0], "In-memory command history"],
  [forteCases[1][0], "Persistent command history"]
];
const modeNames = {suite: "Recovery suite", observer_outage: "Observer outage", forte: "FORTE comparison",
                   station_restart: "Station restart"};
const $ = id => document.getElementById(id);
const embeddedEvidence = $("embedded-evidence");
const defaultCase = "after_action_blind_retry";
let savedCase = defaultCase;
try {
  if (!embeddedEvidence) savedCase = localStorage.getItem("recheck-case") || defaultCase;
} catch {}
const state = {
  online: false, busy: false, posting: false, report: null, lastRun: null,
  selected: descriptions[savedCase] ? savedCase : defaultCase,
  history: []
};
const releaseCases = new Set(["before_action_retry", "after_action_protected_retry"]);
let replayTimer;

function stopReplay() {
  clearInterval(replayTimer);
  replayTimer = undefined;
  $("replay").textContent = "Replay checkpoints";
}

function element(tag, text, className) {
  const node = document.createElement(tag);
  if (text) node.textContent = text;
  if (className) node.className = className;
  return node;
}

async function request(path, body) {
  const response = await fetch(path, body === undefined ? {cache: "no-store"} : {
    method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)
  });
  if (!response.ok) {
    const failure = await response.json().catch(() => ({}));
    throw Error(failure.error || "The local runner is unavailable.");
  }
  return response.json();
}

function notice(message) {
  $("notice").textContent = message;
  $("notice").hidden = !message;
}

function rowFor(id) {
  if (controllerComparison()) {
    const keys = state.report.mode === "station_restart" ? ["volatile", "durable"] : ["blind", "protected"];
    const key = id === forteCases[0][0] ? keys[0] : id === forteCases[1][0] ? keys[1] : null;
    const result = key && state.report[key];
    if (!result) return undefined;
    const [baseline, before_retry, final] = result.readbacks;
    const [original_pid, replacement_pid] = result.controller_pids;
    return {...result, case: id, baseline, before_retry, final, original_pid, replacement_pid,
            verdict: result.release_blocked ? "regression" : "pass"};
  }
  return state.report?.cases?.find(row => row.case === id);
}

function controllerComparison(report = state.report) {
  return report?.mode === "forte" || report?.mode === "station_restart";
}

function renderControls() {
  $("connection").textContent = embeddedEvidence ? "Recorded QNX run" :
    state.online ? "Local runner ready" : "Captured evidence";
  const busy = !state.online || state.busy || state.posting;
  $("run-forte").disabled = busy || !state.forteAvailable;
  $("run-station").disabled = busy || !state.stationAvailable;
  $("run").disabled = busy || !releaseCases.size;
  $("outage").disabled = busy;
  $("policy-export").disabled = !releaseCases.size;
  $("cancel").hidden = !state.busy;
  $("export").disabled = !state.report;
}

function renderPolicy() {
  const report = state.report;
  const selected = report?.selected_cases;
  const same = selected?.length === releaseCases.size && selected.every(id => releaseCases.has(id));
  const result = $("policy-result");
  result.textContent = !releaseCases.size ? "Select at least one case." :
    report?.verdict === "suite_verified" && same && !report.sample ?
      (report.release_blocked ? "Selected release blocked" : "Selected release passed") :
      "Run the selected policy to get a verdict.";
}

function setupPolicy() {
  for (const [id, title] of cases) {
    const label = element("label", "", "policy-case");
    const input = element("input");
    input.type = "checkbox";
    input.checked = releaseCases.has(id);
    input.addEventListener("change", () => {
      if (input.checked) releaseCases.add(id);
      else releaseCases.delete(id);
      renderControls();
      renderPolicy();
    });
    label.append(input, element("span", title));
    $("policy-cases").append(label);
  }
}

function selectCase(id, focus = false) {
  stopReplay();
  state.selected = id;
  $("step").value = $("step").max;
  try {
    if (!embeddedEvidence) localStorage.setItem("recheck-case", id);
  } catch {}
  render();
  if (focus) {
    const button = controllerComparison() ?
      $("comparison-results").querySelector('[aria-pressed="true"]') :
      $("cases").querySelector('[aria-current="true"]');
    button?.focus();
  }
}

function renderCases() {
  const isForte = controllerComparison();
  const sidebar = $("cases").closest("aside");
  sidebar.hidden = isForte;
  sidebar.parentElement.classList.toggle("forte-workspace", isForte);
  if (isForte) return;
  const buttons = cases.map(([id, title]) => {
    const button = element("button", "", "case");
    button.type = "button";
    button.setAttribute("aria-current", String(state.selected === id));
    const result = rowFor(id)?.verdict;
    button.append(
      element("span", title, "case-label"),
      element("span", "", "case-status " + (result || ""))
    );
    button.setAttribute("aria-label", title + (result ? ", " + labels[result] : ""));
    button.addEventListener("click", () => selectCase(id, true));
    return button;
  });
  $("cases").replaceChildren(...buttons);
}

function renderComparison() {
  const station = state.report?.mode === "station_restart";
  const entries = (station ? stationCases : forteCases).map(([id, title]) => [rowFor(id), title]);
  $("comparison").hidden = entries.some(([row]) => !row);
  if ($("comparison").hidden) return;
  const isForte = state.report?.mode === "forte";
  $("comparison-title").textContent = station ? "Same command, two outcomes" :
    isForte ? "Same FORTE workflow, two retry policies" : "Same lost reply, different outcomes";
  $("comparison-description").textContent = station ?
    "Select a result to see what happened when the controller retried after the station restarted." : isForte ?
    "A fresh controller sends the same command. The station’s own readback decides whether the release passes." :
    "The controller exits after the carton moves. The station’s own readback decides the result.";

  const buttons = entries.map(([row, title]) => {
    const button = element("button", "", "comparison-case " + row.verdict);
    button.type = "button";
    button.setAttribute("aria-pressed", String(state.selected === row.case));
    const count = row.observed_advances;
    const result = element("span", "", "comparison-result");
    result.append(element("strong", String(count)), element("span", count === 1 ? "advance" : "advances"));
    button.append(element("span", title, "comparison-label"), result,
                  element("span", row.release_blocked ? "Release blocked" : "Release passed", "comparison-verdict"));
    button.addEventListener("click", () => {
      selectCase(row.case);
      $("comparison-results").querySelector('[aria-pressed="true"]')?.focus();
    });
    return button;
  });
  $("comparison-results").replaceChildren(...buttons);
}

function addLane(name, cells) {
  const lane = element("div", "", "lane");
  lane.append(element("div", name, "lane-label"));
  for (const [title, detail, emphasis] of cells) {
    const cell = element("div", "", emphasis ? "event emphasis" : "event");
    cell.append(element("b", title), element("span", detail));
    lane.append(cell);
  }
  $("timeline").append(lane);
}

function renderTimeline(row) {
  $("timeline").replaceChildren();
  $("command").textContent = row ? "Command " + row.command_id : "";
  $("scrubber").hidden = !row;
  if (!row) {
    $("timeline").append(element("p", "A completed case will show the controller events and station readbacks here."));
    return;
  }
  const before = row.before_retry - row.baseline;
  const noReply = row.caller_reply === "ESRCH";
  const crashAfterReply = row.case === "after_reply_control";
  if (controllerComparison()) {
    addLane("FORTE on QNX", [
      ["Command sent", "FORTE process " + row.original_pid],
      ["Station acknowledged", "FORTE exited before the block returned", true],
      ["Same ID retried", "Replacement process " + row.replacement_pid]
    ]);
  } else addLane("QNX caller", [
    ["Command sent", "Controller PID " + row.original_pid],
    [noReply ? "Reply lost" : "Reply received", noReply ? "ESRCH · controller exited" : crashAfterReply ? "Controller exited after reply" : "Acknowledged", noReply],
    [row.replacement_pid ? "Same ID retried" : "No retry", row.replacement_pid ? "Replacement PID " + row.replacement_pid : ""]
  ]);
  addLane("Station observer", [
    ["Baseline read", "0 advances for this case"],
    ["First readback", before + (before === 1 ? " advance" : " advances")],
    ["Final readback", row.observed_advances + (row.observed_advances === 1 ? " advance" : " advances"), row.release_blocked]
  ]);
  if (row.station_restart) {
    const [original, replacement] = row.station_restart.process_ids;
    addLane("Station process", [
      ["Write committed", "Original station PID " + original],
      ["Station replaced", "New station PID " + replacement, true],
      ["Count restored before retry", row.station_restart.readback_after_restart + " advance"]
    ]);
  }
}

function drawStation(count) {
  const canvas = $("station");
  const context = canvas.getContext("2d");
  const width = canvas.width, height = canvas.height;
  context.clearRect(0, 0, width, height);
  context.strokeStyle = "#304137";
  context.lineWidth = 1;
  for (let x = 16; x < width; x += 32) {
    context.beginPath(); context.moveTo(x, 0); context.lineTo(x, height); context.stroke();
  }
  for (let y = 14; y < height; y += 32) {
    context.beginPath(); context.moveTo(0, y); context.lineTo(width, y); context.stroke();
  }
  const track = [74, 81, 568, 99];
  context.fillStyle = "#35493d";
  context.fillRect(...track);
  context.strokeStyle = "#91a58d";
  context.strokeRect(...track);
  context.strokeStyle = "#546d5b";
  context.lineWidth = 3;
  for (let x = 90; x < 640; x += 22) {
    context.beginPath(); context.moveTo(x, 89); context.lineTo(x, 173); context.stroke();
  }
  const slots = [151, 357, 558];
  context.setLineDash([7, 6]);
  context.strokeStyle = "#b6d481";
  context.strokeRect(312, 86, 90, 90);
  context.setLineDash([]);
  context.textAlign = "center";
  if (count === null) return;
  const x = slots[Math.min(count, 2)];
  context.fillStyle = count === 2 ? "#f3aa77" : "#d9ee8c";
  context.fillRect(x - 36, 95, 72, 72);
  context.strokeStyle = "#283328";
  context.lineWidth = 2;
  context.strokeRect(x - 36, 95, 72, 72);
  context.beginPath(); context.moveTo(x - 36, 117); context.lineTo(x + 36, 117); context.stroke();
  context.fillStyle = "#293c2b";
  context.fillRect(x - 13, 130, 26, 20);
  context.fillStyle = "#d9ee8c";
  context.font = "700 22px monospace";
  context.fillText(String(count), x, 146);
}

function renderTechnical(row) {
  const report = state.report;
  const entries = report ? [
    ["Target", report.target || "QNX recovery runner"],
    ["Command transport", report.command_transport],
    ["Observer", report.observer],
    ["Controller runtime", report.upstream],
    ["FORTE commit", report.upstream_commit],
    ["Workflow", report.scenario],
    ["QNX image SHA-256", row?.image_sha256 || report.image_sha256],
    ["Run ID", report.run_id],
    ["Station readbacks", row && [row.baseline, row.before_retry, row.final].join(" → ")],
    ["Interleaved command IDs", report.history && "Earlier command replay and conflicting payload verified"]
  ] : [];
  $("technical").replaceChildren(...entries.filter(([, value]) => value)
    .flatMap(([key, value]) => [element("dt", key), element("dd", value)]));
}

function renderHistory() {
  const buttons = state.history.map(run => {
    const time = new Date(run.started_at).toLocaleString();
    const button = element("button", "", "history-row subtle");
    button.type = "button";
    button.setAttribute("aria-current", String(state.report?.run_id === run.run_id));
    button.append(element("strong", modeNames[run.mode] || "Recovery test"),
                  element("span", time));
    button.addEventListener("click", async () => {
      try {
        showReport(await request("/api/runs/" + run.run_id));
      } catch (error) { notice(error.message); }
    });
    return button;
  });
  $("history").replaceChildren(...(buttons.length ? buttons : [element("p", "Your completed runs appear here.")]));
}

function render() {
  const report = state.report;
  const row = rowFor(state.selected);
  const isForte = controllerComparison();
  const station = report?.mode === "station_restart";
  const forteCase = (station ? stationCases : forteCases).find(([id]) => id === state.selected);
  const outcome = ["incomplete", "canceled"].includes(report?.verdict) ? report.verdict :
    row?.verdict || "";
  $("source").textContent = embeddedEvidence ? "Recorded FORTE run · QNX 8.0" :
    report?.sample ? "Captured QNX run" : isForte ? "Local FORTE run · QNX 8.0" :
    report?.run_id === state.lastRun && report ? "Latest local run" : report ? "Saved local run" : "QNX recovery test";
  $("case-title").textContent = report?.mode === "observer_outage" ? "Observer outage" :
    isForte ? (row ? forteCase[1] : modeNames[report.mode]) : descriptions[state.selected]?.name || "Command recovery";
  $("verdict").textContent = labels[outcome] || "Awaiting evidence";
  $("verdict").className = "verdict " + (outcome || "");
  $("observed").replaceChildren(document.createTextNode((row?.observed_advances ?? "—") + " "), element("small", "/ 1"));
  const forteReason = row && (row.final === row.before_retry ?
    "The replacement process sent the same command ID. The retry added no advance." :
    "The replacement process sent the same command ID. The station count changed from " + row.before_retry + " to " + row.final + ".");
  $("reason").textContent = report?.reason || (row ? (isForte ? forteReason : descriptions[row.case].reason) :
    (outcome === "canceled" ? "The test was canceled before verification." : "Run QNX to collect a fresh result."));
  $("compare").hidden = !["after_action_blind_retry", "before_action_suppressed"].includes(row?.case);
  $("compare").replaceChildren(
    document.createTextNode(station ? "View persistent history " : isForte ? "View protected retry " : "Compare with correction "),
    element("span", "→")
  );
  renderControls();
  renderPolicy();
  renderComparison();
  renderCases();
  renderTimeline(row);
  renderStation(row);
  renderTechnical(row);
  renderHistory();
}

function renderStation(row) {
  if (!row) {
    $("station-checkpoint").textContent = "Waiting for evidence";
    drawStation(null);
    return;
  }
  const checkpoints = [
    ["Baseline", 0],
    ["After first attempt", row.before_retry - row.baseline]
  ];
  if (row.station_restart) {
    checkpoints.push(["After station restart", row.station_restart.readback_after_restart]);
  }
  checkpoints.push(["Final readback", row.observed_advances]);
  const step = $("step");
  if (!step.dataset.case || step.dataset.case !== row.case || step.max !== String(checkpoints.length - 1)) {
    step.dataset.case = row.case;
    step.max = String(checkpoints.length - 1);
    step.value = step.max;
  }
  const [label, advances] = checkpoints[Number(step.value)];
  $("step-label").textContent = label;
  $("station-checkpoint").textContent = label;
  $("step").setAttribute("aria-valuetext", label + ", " + advances + " advances");
  $("observed").replaceChildren(document.createTextNode(advances + " "), element("small", "/ 1"));
  drawStation(advances);
}

function showReport(report, newRun = false) {
  stopReplay();
  if (controllerComparison(report) && (newRun || !forteCases.some(([id]) => id === state.selected))) {
    state.selected = forteCases[0][0];
  }
  state.report = report;
  $("step").value = $("step").max;
  notice("");
  render();
}

function runningMessage(mode) {
  if (mode === "station_restart") return "Restarting both station processes and checking what each one remembers…";
  return mode === "forte" ?
    "Running both FORTE policies on QNX. Waiting for independent station readbacks…" :
    "QNX is running the recovery test. Waiting for station evidence…";
}

async function sync() {
  if (state.syncing) return;
  state.syncing = true;
  try {
    const remote = await request("/api/state");
    if (!remote.release_selection) {
      state.online = false;
      renderControls();
      notice("Reopen Recheck from the desktop shortcut to load the release policy runner.");
      return;
    }
    state.online = true;
    state.forteAvailable = remote.forte_comparison === true;
    state.stationAvailable = remote.station_restart === true;
    const wasBusy = state.busy;
    state.busy = Boolean(remote.active);
    state.history = remote.history;
    const latest = remote.latest || remote.history[0]?.run_id;
    if (latest && latest !== state.lastRun) {
      const report = await request("/api/runs/" + latest);
      state.lastRun = latest;
      showReport(report, state.waiting);
      state.waiting = false;
    } else {
      renderControls();
      if (!state.busy && state.waiting) {
        state.waiting = false;
        notice("The runner stopped before it produced a report.");
      } else if (wasBusy && !state.busy) notice("");
    }
    if (state.busy) notice(runningMessage(remote.active));
    else if (remote.error) notice(remote.error);
  } catch (error) {
    state.online = false;
    state.busy = false;
    renderControls();
    if (state.waiting) notice(error.message);
  } finally {
    state.syncing = false;
  }
}

async function start(mode) {
  if (state.posting || state.busy) return;
  state.posting = true;
  renderControls();
  try {
    await request("/api/run", mode === "suite" ? {mode, cases: [...releaseCases]} : {mode});
    state.waiting = true;
    state.busy = true;
    notice(runningMessage(mode));
  } catch (error) {
    notice(error.message);
  } finally {
    state.posting = false;
    renderControls();
  }
}

$("run").addEventListener("click", () => start("suite"));
$("run-forte").addEventListener("click", () => start("forte"));
$("run-station").addEventListener("click", () => start("station_restart"));
$("step").addEventListener("input", () => {
  stopReplay();
  renderStation(rowFor(state.selected));
});
$("replay").addEventListener("click", () => {
  if (replayTimer !== undefined) {
    stopReplay();
    return;
  }
  const row = rowFor(state.selected);
  if (!row) return;
  const step = $("step");
  step.value = step.min;
  renderStation(row);
  $("replay").textContent = "Pause replay";
  replayTimer = setInterval(() => {
    step.stepUp();
    renderStation(row);
    if (step.value === step.max) stopReplay();
  }, 800);
});
$("outage").addEventListener("click", () => start("observer_outage"));
$("compare").addEventListener("click", () => selectCase(state.selected === "before_action_suppressed" ?
  "before_action_retry" : "after_action_protected_retry", true));
$("cancel").addEventListener("click", async () => {
  try { await request("/api/cancel", {}); }
  catch (error) { notice(error.message); }
});
function downloadJson(value, name) {
  const url = URL.createObjectURL(new Blob([JSON.stringify(value, null, 2) + "\n"], {type: "application/json"}));
  const link = element("a");
  link.href = url;
  link.download = name;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
$("export").addEventListener("click", () => downloadJson(state.report, "recheck-" + (state.report.run_id || "captured") + ".json"));
$("policy-export").addEventListener("click", () => downloadJson({cases: [...releaseCases]}, "recheck-policy.json"));

async function boot() {
  render();
  try {
    state.report = {...await request("sample.json"), sample: true};
    render();
  } catch (error) {
    notice("Captured evidence could not be opened.");
  }
  while (true) {
    await sync();
    await new Promise(resolve => setTimeout(resolve, 800));
  }
}

if (embeddedEvidence) {
  document.body.classList.add("recorded");
  $("run-station").hidden = true;
  $("source-link").hidden = false;
  $("policy-title").closest("section").hidden = true;
  $("history").closest("details").hidden = true;
  showReport(JSON.parse(embeddedEvidence.textContent));
} else {
  setupPolicy();
  boot();
}

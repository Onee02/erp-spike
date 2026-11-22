const $ = (id) => document.getElementById(id);

async function api(url, options = {}, timeoutMs = 10000) {
  const r = await fetch(url, { ...options, signal: AbortSignal.timeout(timeoutMs) });
  if (!r.ok) throw new Error(`Request failed (${r.status})`);
  return r.json();
}

async function loadWhale() {
  try {
    const w = await api("/admin/whale");
    $("whale-state").textContent = w.present ? `in the database (${w.orders.toLocaleString("en-US")} orders)` : "removed from the database";
    $("whale-remove").disabled = !w.present;
    $("whale-add").disabled = w.present;
  } catch (e) {
    $("whale-state").textContent = `Whale service unavailable: ${e.message}`;
    $("whale-remove").disabled = $("whale-add").disabled = true;
  }
}

async function whaleAction(kind, busyText) {
  $("whale-remove").disabled = $("whale-add").disabled = true;
  $("whale-msg").textContent = busyText;
  try {
    await api(`/admin/whale/${kind}`, { method: "POST" }, 120000);
    $("whale-msg").textContent = kind === "remove" ? "Whale removed." : "Whale added.";
  } catch (e) {
    $("whale-msg").textContent = e.message;
  } finally {
    loadWhale();
  }
}

async function loadSim() {
  try {
    const s = await api("/admin/simulation");
    $("sim-state").textContent = s.stopping ? "stopping: waiting for current whale reports" : s.running ? "running: the whale is generating reports" : "off";
    $("sim-toggle").textContent = s.running ? "Stop simulation" : "Simulate month-end";
    $("sim-toggle").className = s.running ? "ghost" : "";
    $("sim-toggle").disabled = s.stopping;
  } catch (e) {
    $("sim-state").textContent = `Whale service unavailable: ${e.message}`;
    $("sim-toggle").disabled = true;
  }
}

async function loadMetrics(group) {
  try {
    const m = await api(`/admin/metrics/${group}`);
    const s = group === "whale" ? m.whale : m.small_tenants;
    $("metrics-" + group).textContent = `${s.requests} sampled requests; p95 ${s.p95_ms ?? "—"} ms; p99 ${s.p99_ms ?? "—"} ms; ${s.errors} errors`;
  } catch (e) {
    $("metrics-" + group).textContent = `Unavailable: ${e.message}`;
  }
}

$("whale-remove").addEventListener("click", () => whaleAction("remove", "Removing whale data..."));
$("whale-add").addEventListener("click", () => whaleAction("add", "Adding whale data (about 15 seconds)..."));
$("sim-toggle").addEventListener("click", async () => {
  const running = $("sim-toggle").textContent.startsWith("Stop");
  $("sim-toggle").disabled = true;
  try {
    await api(`/admin/simulation/${running ? "stop" : "start"}`, { method: "POST" });
  } catch (e) {
    $("sim-state").textContent = e.message;
  } finally {
    loadSim();
  }
});

loadWhale();
loadSim();
loadMetrics("small");
loadMetrics("whale");
setInterval(() => { loadSim(); loadMetrics("small"); loadMetrics("whale"); }, 2000);

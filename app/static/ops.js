const $ = (id) => document.getElementById(id);

async function loadWhale() {
  const w = await (await fetch("/admin/whale")).json();
  $("whale-state").textContent = w.present ? `jest w bazie (${w.orders.toLocaleString("pl-PL")} zamówień)` : "usunięty z bazy";
  $("whale-remove").disabled = !w.present;
  $("whale-add").disabled = w.present;
}

async function whaleAction(kind, busyText) {
  $("whale-remove").disabled = $("whale-add").disabled = true;
  $("whale-msg").textContent = busyText;
  await fetch(`/admin/whale/${kind}`, { method: "POST" });
  $("whale-msg").textContent = kind === "remove" ? "Wieloryb usunięty." : "Wieloryb dodany.";
  loadWhale();
}

async function loadSim() {
  const s = await (await fetch("/admin/simulation")).json();
  $("sim-state").textContent = s.running ? "trwa: wieloryb liczy raporty" : "wyłączona";
  $("sim-toggle").textContent = s.running ? "Zakończ symulację" : "Symuluj koniec miesiąca";
  $("sim-toggle").className = s.running ? "ghost" : "";
}

$("whale-remove").addEventListener("click", () => whaleAction("remove", "Usuwam dane wieloryba..."));
$("whale-add").addEventListener("click", () => whaleAction("add", "Dodaję dane wieloryba (kilkanaście sekund)..."));
$("sim-toggle").addEventListener("click", async () => {
  const running = $("sim-toggle").textContent.startsWith("Zakończ");
  await fetch(`/admin/simulation/${running ? "stop" : "start"}`, { method: "POST" });
  loadSim();
});

loadWhale();
loadSim();

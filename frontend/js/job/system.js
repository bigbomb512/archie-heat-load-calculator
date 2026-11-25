// Step 4, "Design the system": the saved cooling load as the AC system design starts from it.
// Reads GET /api/ac-design-input (backend/ac_design_input_service.py; contract in docs/ac-design-input.md).
// Nothing here estimates a load: a value the calculation didn't save stays "Not calculated".
import { getJson, escapeHtml, fmt } from "./api.js";

const query = (projectId) => `?project_id=${encodeURIComponent(projectId)}`;
const STATE = {
  current: ["Current", "ok"],
  stale: ["Out of date", "warn"],
  building_only: ["Building only", "warn"],
  none: ["Not calculated", "muted"],
};

const kw = (value) => (Number.isFinite(Number(value)) && value !== null ? `${fmt(value)} kW` : "Not calculated");
const when = (peak) =>
  [peak?.month, Number.isInteger(peak?.hour) ? `${String(peak.hour).padStart(2, "0")}:00` : ""].filter(Boolean).join(" ");
const temp = (value) => (Number.isFinite(Number(value)) && value !== null ? `${fmt(value)} °C` : "—");

function conditions(input) {
  const c = input.design_conditions || {};
  const rows = [
    ["Location", c.location || "—"],
    ["Outdoor (summer design)", `${temp(c.outdoor_db_c)} dry bulb · ${temp(c.outdoor_wb_c)} wet bulb`],
    ["Indoor", `${temp(c.indoor_db_c)}${Number.isFinite(Number(c.indoor_rh)) && c.indoor_rh !== null ? ` · ${fmt(Number(c.indoor_rh) * (c.indoor_rh <= 1 ? 100 : 1), 0)}% RH` : ""}`],
  ];
  return `<dl class="job-system-facts">${rows.map(([label, value]) => `<div><dt>${escapeHtml(label)}</dt><dd>${escapeHtml(value)}</dd></div>`).join("")}</dl>${c.source ? `<p class="job-caption">${escapeHtml(c.source)}</p>` : ""}`;
}

function roomsTable(input) {
  const rooms = (input.rooms || []).filter((room) => room.cooled);
  if (!rooms.length) return `<p class="job-muted">No cooled rooms have a saved load yet.</p>`;
  return `<div class="job-table-wrap"><table class="job-results-table" data-system-rooms>
    <caption class="job-sr-only">Cooling load of each cooled room</caption>
    <thead><tr><th scope="col">Room</th><th scope="col">Area</th><th scope="col">Its own peak</th><th scope="col">At the house's peak</th><th scope="col">People</th></tr></thead>
    <tbody>${rooms
      .map(
        (room) => `<tr data-system-room="${escapeHtml(room.id)}"><th scope="row">${escapeHtml(room.name)}${room.level ? `<small>${escapeHtml(room.level)}</small>` : ""}${room.member_ids?.length ? `<small>Joined from ${room.member_ids.length} pieces</small>` : ""}</th>
        <td>${Number.isFinite(Number(room.area_m2)) && room.area_m2 !== null ? `${fmt(room.area_m2)} m²` : "—"}</td>
        <td>${kw(room.peak?.kw)}${room.peak?.kw != null && when(room.peak) ? `<small>${escapeHtml(when(room.peak))}</small>` : ""}</td>
        <td>${kw(room.at_house_peak_kw)}</td>
        <td>${room.people ?? "—"}</td></tr>`,
      )
      .join("")}</tbody></table></div>
    <p class="job-caption">Size each room's outlet or zone from its own peak. Size the central unit from the house's peak, not the sum of the rooms' own peaks: rooms peak at different times.</p>`;
}

// Extension point for the AC system design (ducts, zones, outlets, unit selection). It receives the same JSON that
// GET /api/ac-design-input returns. Until a design is built into this step it says so plainly.
export function designPanel(input) {
  const ready = input.status?.ready;
  return `<section class="job-panel" data-system-design>
    <h2>System design</h2>
    <p>${ready
      ? "Designing the ducts, zones and units from these loads isn't part of this version yet. Download the design input below to use the loads in your design tool."
      : "The system is designed from a current load. Update the load first."}</p>
  </section>`;
}

function download(input) {
  const name = String(input.project?.address || input.project?.name || "job")
    .replace(/[^\w.-]+/g, "-")
    .replace(/^-+|-+$/g, "");
  const blob = new Blob([JSON.stringify(input, null, 2)], { type: "application/json" });
  const link = Object.assign(document.createElement("a"), { href: URL.createObjectURL(blob), download: `${name || "job"}-ac-design-input.json` });
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(link.href), 1000);
}

export async function renderSystem(projectId) {
  const [input, status] = await Promise.all([
    getJson(`/api/ac-design-input${query(projectId)}`),
    getJson(`/api/job-status${query(projectId)}`).catch(() => ({})),
  ]);
  const state = input.status?.state || "none";
  const [badge, tone] = STATE[state] || STATE.none;
  const totals = input.totals || {};
  const stale = state !== "current";
  const open = Number(input.open_items?.count) || 0;
  const content = `
    <section class="job-panel job-system-summary" data-system-state="${escapeHtml(state)}">
      <h1>What the design starts from</h1>
      <p><span class="job-answer-badge ${tone}" data-system-badge>${escapeHtml(badge)}</span></p>
      ${stale ? `<p data-system-reason>${escapeHtml(input.status?.reason || "")} <a href="#/job/${encodeURIComponent(projectId)}/results">Go to Load and report</a></p>` : ""}
      <div class="job-system-totals">
        <div><span class="job-answer-label">Whole house at its peak</span><strong class="job-answer-total" data-system-total>${kw(totals.peak_kw)}</strong>
          <span class="job-answer-facts">${[when(totals) ? `Peak ${when(totals)}` : "", totals.sensible_kw != null ? `Sensible ${fmt(totals.sensible_kw)} kW` : "", totals.latent_kw != null ? `Latent ${fmt(totals.latent_kw)} kW` : ""].filter(Boolean).map(escapeHtml).join(" · ")}</span></div>
        <div><span class="job-answer-label">Suggested system</span><strong class="job-system-suggestion">${escapeHtml(totals.suggested_system || "—")}</strong>
          <span class="job-answer-facts">No safety margin added.</span></div>
      </div>
      ${conditions(input)}
      <p><button type="button" class="job-button secondary" data-system-download>Download the design input (JSON)</button></p>
    </section>
    ${designPanel(input)}
    <section class="job-panel"><h2>Each cooled room</h2>${roomsTable(input)}</section>
    ${(input.not_counted || []).length ? `<section class="job-panel" data-system-not-counted><h2>Meant to be cooled, not in the load yet</h2><ul>${input.not_counted.map((room) => `<li><strong>${escapeHtml(room.name)}</strong>${room.level ? ` · ${escapeHtml(room.level)}` : ""}: ${escapeHtml(room.reason || "")}</li>`).join("")}</ul><p><a href="#/job/${encodeURIComponent(projectId)}/house">Check the house</a></p></section>` : ""}
    ${(input.not_cooled || []).length ? `<section class="job-panel" data-system-not-cooled><h2>Not cooled</h2><p>${(input.not_cooled || []).map((room) => escapeHtml(room.name)).join(", ")}</p></section>` : ""}
    ${(input.assumptions || []).length ? `<details class="job-panel job-more"><summary>Assumed in these loads (${input.assumptions.length})</summary><ul>${input.assumptions.map((row) => `<li>${escapeHtml(row)}</li>`).join("")}</ul></details>` : ""}`;
  return {
    status,
    statusLine: stale
      ? "The design needs a current load."
      : open
        ? `The load is current, but ${open} ${open === 1 ? "item could change it" : "items could change it"}: check the house before relying on the design.`
        : "The load is ready for the system design.",
    content,
    bind() {
      document.querySelector("[data-system-download]")?.addEventListener("click", () => download(input));
    },
  };
}

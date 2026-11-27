import { getJson, sendJson, escapeHtml, fmt, navigate } from "./api.js";

// Quick fixes: correct what the drawings got wrong (a room's name, use, area, people, cooled or not; its windows'
// glass, blinds and shade) and update the load, without reading the drawings again (backend/quick_fix_service.py).

const query = (projectId) => `?project_id=${encodeURIComponent(projectId)}`;

function number(value, digits = 1) {
  return value == null || value === "" || !Number.isFinite(Number(value))
    ? ""
    : String(Number(Number(value).toFixed(digits)));
}

function roomRow(room, uses) {
  const id = escapeHtml(room.room_id);
  const renamed = room.name !== room.label;
  const useOptions = Object.entries(uses)
    .filter(([key]) => key !== "not_a_room")
    .map(
      ([key, label]) =>
        `<option value="${escapeHtml(key)}" ${room.use_id === key ? "selected" : ""}>${escapeHtml(label)}</option>`,
    )
    .join("");
  const cooled = room.can_cool
    ? `<input type="checkbox" data-field="cooled" ${room.cooled ? "checked" : ""} aria-label="${escapeHtml(room.name)} is cooled">`
    : `<span class="job-muted" title="Change its use to cool it">Not by its use</span>`;
  return `<tr data-room="${id}">
    <th scope="row"><input class="job-fix-name" data-field="name" value="${escapeHtml(room.name)}" maxlength="80" aria-label="Name of ${escapeHtml(room.label)}">
      ${renamed ? `<small class="job-row-source">On the drawings: ${escapeHtml(room.label)}</small>` : ""}
      ${room.level ? `<small class="job-row-source">${escapeHtml(room.level)}</small>` : ""}</th>
    <td><select data-field="use" aria-label="Use of ${escapeHtml(room.name)}"><option value="">${room.use_id ? "" : "Choose a use"}</option>${useOptions}</select></td>
    <td><input class="job-fix-number" data-field="area_m2" type="number" min="0.5" max="20000" step="0.1" inputmode="decimal" value="${number(room.area_m2)}" aria-label="Area of ${escapeHtml(room.name)} in m²">
      ${room.area_label ? `<small class="job-row-source">${escapeHtml(room.area_label)}</small>` : ""}</td>
    <td><input class="job-fix-number" data-field="people" type="number" min="0" max="5000" step="1" inputmode="numeric" value="${number(room.people, 0)}" placeholder="—" aria-label="People in ${escapeHtml(room.name)}"></td>
    <td class="job-fix-check">${cooled}</td>
  </tr>`;
}

function glassControl(current, presets, label) {
  const answer = current || {};
  const preset = presets.find(
    (row) => answer.u_value_w_m2k === row.u_value_w_m2k && answer.shgc === row.shgc,
  );
  const value = !current ? "drawings" : preset ? preset.id : "other";
  return `<div class="job-fix-glass">
    <select data-field="glass" aria-label="Glass in ${escapeHtml(label)}">
      <option value="drawings" ${value === "drawings" ? "selected" : ""}>As the drawings give</option>
      ${presets.map((row) => `<option value="${escapeHtml(row.id)}" ${value === row.id ? "selected" : ""}>${escapeHtml(row.label)}: U ${row.u_value_w_m2k}, SHGC ${row.shgc}</option>`).join("")}
      <option value="other" ${value === "other" ? "selected" : ""}>Other: type the U-value and SHGC</option>
    </select>
    <div class="job-fix-other" ${value === "other" ? "" : "hidden"}>
      <input class="job-fix-number" data-field="glass_u" type="number" min="0.5" max="7" step="0.1" value="${value === "other" ? number(answer.u_value_w_m2k, 2) : ""}" placeholder="U" aria-label="Glass U-value in ${escapeHtml(label)}, W/m²K">
      <input class="job-fix-number" data-field="glass_shgc" type="number" min="0.05" max="0.95" step="0.01" value="${value === "other" ? number(answer.shgc, 2) : ""}" placeholder="SHGC" aria-label="Glass SHGC in ${escapeHtml(label)}">
      <input data-field="glass_source" value="${value === "other" ? escapeHtml(answer.source || "") : ""}" placeholder="From (window schedule, certificate…)" aria-label="Where the glass values for ${escapeHtml(label)} come from">
    </div>
  </div>`;
}

function shadeControl(current, kinds, label) {
  // current: undefined (no answer: as the drawings give), null (answered: no shade) or {kind, depth_m, gap_m}.
  const shade = current || null;
  return `<div class="job-fix-shade">
    <select data-field="shade_kind" aria-label="Shade over the windows of ${escapeHtml(label)}">
      <option value="" ${current === undefined ? "selected" : ""}>As the drawings give</option>
      <option value="none" ${current === null ? "selected" : ""}>No shade</option>
      ${kinds.map((kind) => `<option value="${escapeHtml(kind)}" ${shade?.kind === kind ? "selected" : ""}>${escapeHtml(kind[0].toUpperCase() + kind.slice(1))}</option>`).join("")}
    </select>
    <span class="job-fix-other" ${shade ? "" : "hidden"}>
      <input class="job-fix-number" data-field="shade_depth" type="number" min="0.1" max="10" step="0.05" value="${shade ? number(shade.depth_m, 2) : ""}" placeholder="Depth m" aria-label="Shade depth in metres, ${escapeHtml(label)}">
      <input class="job-fix-number" data-field="shade_gap" type="number" min="0" max="5" step="0.05" value="${shade?.gap_m != null ? number(shade.gap_m, 2) : ""}" placeholder="Gap m" aria-label="Gap from the top of the glass to the shade in metres, ${escapeHtml(label)}">
    </span>
  </div>`;
}

function windowRow(target, label, summary, fix, data) {
  const blinds = data.blinds
    .map((row) => `<option value="${escapeHtml(row.id)}" ${fix.blinds === row.id ? "selected" : ""}>${escapeHtml(row.label)}</option>`)
    .join("");
  const now = summary
    ? `<small class="job-row-source">${summary.count} window${summary.count === 1 ? "" : "s"}, ${fmt(summary.area_m2)} m²; now ${escapeHtml(summary.glass.join("; ") || "no glass recorded")}${summary.blinds.length ? `; ${escapeHtml(summary.blinds.join("; "))}` : ""}${summary.shade.length ? `; ${escapeHtml(summary.shade.join("; "))}` : ""}</small>`
    : `<small class="job-row-source">Used for every room without its own answer.</small>`;
  return `<tr data-window="${escapeHtml(target)}">
    <th scope="row">${escapeHtml(label)}${now}</th>
    <td>${glassControl(fix.glass, data.glass_presets, label)}</td>
    <td><select data-field="blinds" aria-label="Inside blinds in ${escapeHtml(label)}"><option value="" ${fix.blinds ? "" : "selected"}>As the drawings give</option>${blinds}</select></td>
    <td>${shadeControl(fix.shade, data.shade_kinds, label)}</td>
  </tr>`;
}

function content(data, projectId) {
  const rooms = data.rooms.map((room) => roomRow(room, data.uses)).join("");
  const windowRooms = data.rooms.filter((room) => room.windows.count > 0);
  const windows = [
    windowRow("*", "Every window", null, data.every_window || {}, data),
    ...windowRooms.map((room) => windowRow(room.label, room.name, room.windows, room.window_fix || {}, data)),
  ].join("");
  return `<section class="job-panel" aria-labelledby="fix-title">
      <h1 id="fix-title">Fix rooms and windows</h1>
      <p>Correct what the drawings got wrong. Nothing is read again: saving updates the load in a few seconds.
      Renaming a room changes the name shown here and in the report; set its use and people in their own columns.</p>
    </section>
    <form id="job-fix-form" novalidate>
      <section class="job-panel" aria-labelledby="fix-rooms-title">
        <h2 id="fix-rooms-title">Rooms</h2>
        <div class="job-table-wrap"><table class="job-fix-table">
          <thead><tr><th scope="col">Room</th><th scope="col">Use</th><th scope="col">Area m²</th><th scope="col">People</th><th scope="col">Cooled</th></tr></thead>
          <tbody>${rooms || `<tr><td colspan="5">No rooms yet.</td></tr>`}</tbody>
        </table></div>
      </section>
      <section class="job-panel" aria-labelledby="fix-windows-title">
        <h2 id="fix-windows-title">Windows</h2>
        <p class="job-caption">Glass, inside blinds and an awning, verandah or eave over a room's windows. A room's answer applies to all its windows.</p>
        <div class="job-table-wrap"><table class="job-fix-table">
          <thead><tr><th scope="col">Where</th><th scope="col">Glass</th><th scope="col">Inside blinds</th><th scope="col">Shade over the windows</th></tr></thead>
          <tbody>${windows}</tbody>
        </table></div>
      </section>
      <div class="job-fix-actions">
        <button class="job-button" type="submit" id="job-fix-save">Save and update the load</button>
        <a class="job-link-button" href="#/job/${encodeURIComponent(projectId)}/${data.status?.results_ready ? "results" : "rooms"}">Cancel</a>
        <p class="job-note" role="status" id="job-fix-status"></p>
      </div>
    </form>`;
}

// An empty box is sent empty, not as 0, so the server asks for the number instead of saying 0 is out of range.
const typedNumber = (text) => (text === "" || text === undefined ? "" : Number(text));

function value(row, field) {
  const input = row.querySelector(`[data-field="${field}"]`);
  if (!input) return undefined;
  return input.type === "checkbox" ? input.checked : input.value.trim();
}

// Only what the operator changed is sent; the server checks all of it before saving any.
export function collectChanges(form, data) {
  const rooms = [];
  for (const row of form.querySelectorAll("tr[data-room]")) {
    const room = data.rooms.find((item) => item.room_id === row.dataset.room);
    if (!room) continue;
    const change = { room_id: room.room_id };
    const name = value(row, "name");
    if (name !== room.name) change.name = name;
    const use = value(row, "use");
    if (use && use !== room.use_id) change.use = use;
    const area = value(row, "area_m2");
    if (area !== "" && Number(area) !== Number(number(room.area_m2))) change.area_m2 = Number(area);
    const people = value(row, "people");
    if (people !== "" && Number(people) !== Number(number(room.people, 0))) change.people = Number(people);
    const cooled = value(row, "cooled");
    if (cooled !== undefined && cooled !== room.cooled) change.cooled = cooled;
    if (Object.keys(change).length > 1) rooms.push(change);
  }
  const windows = [];
  for (const row of form.querySelectorAll("tr[data-window]")) {
    const target = row.dataset.window;
    const fix = target === "*" ? data.every_window || {} : data.rooms.find((item) => item.label === target)?.window_fix || {};
    const change = { room: target };
    const glass = value(row, "glass");
    if (glass === "drawings") {
      if (fix.glass) change.glass = "drawings";
    } else if (glass === "other") {
      const typed = { u_value_w_m2k: typedNumber(value(row, "glass_u")), shgc: typedNumber(value(row, "glass_shgc")), source: value(row, "glass_source") };
      if (!fix.glass || typed.u_value_w_m2k !== fix.glass.u_value_w_m2k || typed.shgc !== fix.glass.shgc || typed.source !== (fix.glass.source || ""))
        change.glass = typed;
    } else {
      const preset = data.glass_presets.find((item) => item.id === glass);
      if (!fix.glass || fix.glass.u_value_w_m2k !== preset.u_value_w_m2k || fix.glass.shgc !== preset.shgc)
        change.glass = { preset: glass };
    }
    const blinds = value(row, "blinds");
    if (blinds && blinds !== fix.blinds) change.blinds = blinds;
    else if (!blinds && fix.blinds) change.blinds = "drawings";
    const kind = value(row, "shade_kind");
    if (kind === "none") {
      if (fix.shade !== null) change.shade = "none";
    } else if (kind) {
      const shade = { kind, depth_m: typedNumber(value(row, "shade_depth")) };
      const gap = value(row, "shade_gap");
      if (gap !== "") shade.gap_m = Number(gap);
      const old = fix.shade || {};
      if (old.kind !== shade.kind || old.depth_m !== shade.depth_m || (old.gap_m ?? "") !== (shade.gap_m ?? "")) change.shade = shade;
    } else if (fix.shade !== undefined) {
      change.shade = "drawings";
    }
    if (Object.keys(change).length > 1) windows.push(change);
  }
  return { rooms, windows };
}

async function runCalculation(projectId, phase, line, label) {
  let job = await sendJson("/api/job-calculation", { project_id: projectId, phase, reviewer: "Quick fixes" });
  const started = Date.now();
  while (["queued", "running"].includes(job.status)) {
    if (Date.now() - started > 600000) throw new Error("The update is still running. Come back to the job to see it finish.");
    line.textContent = label;
    await new Promise((resolve) => setTimeout(resolve, 2000));
    job = await getJson(`/api/job-calculation${query(projectId)}`);
  }
  if (job.status !== "done") throw new Error(job.error || "The load didn't update.");
}

export async function renderFix(projectId) {
  const data = await getJson(`/api/quick-fix${query(projectId)}`);
  return {
    status: data.status,
    statusLine: "Fix a room or its windows, then save: the drawings aren't read again.",
    content: content(data, projectId),
    bind: () => {
      const form = document.querySelector("#job-fix-form");
      const line = document.querySelector("#job-fix-status");
      const save = document.querySelector("#job-fix-save");
      form.querySelectorAll('[data-field="glass"]').forEach((select) => {
        select.addEventListener("change", () => {
          select.closest(".job-fix-glass").querySelector(".job-fix-other").hidden = select.value !== "other";
        });
      });
      form.querySelectorAll('[data-field="shade_kind"]').forEach((select) => {
        select.addEventListener("change", () => {
          select.closest(".job-fix-shade").querySelector(".job-fix-other").hidden = !select.value || select.value === "none";
        });
      });
      // Unsaved fixes aren't lost by accident: closing or reloading the tab, or leaving for another screen, asks first.
      let dirty = false;
      const warnUnload = (event) => { if (dirty) { event.preventDefault(); event.returnValue = ""; } };
      const guardLinks = (event) => {
        const link = event.target.closest("a[href]");
        if (!dirty || !link || form.contains(link)) return;
        if (!window.confirm("Leave without saving your fixes? They will be lost.")) event.preventDefault();
      };
      const stop = () => {
        window.removeEventListener("beforeunload", warnUnload);
        document.removeEventListener("click", guardLinks, true);
        window.removeEventListener("hashchange", stop);
      };
      form.addEventListener("input", () => { dirty = true; });
      window.addEventListener("beforeunload", warnUnload);
      document.addEventListener("click", guardLinks, true);
      window.addEventListener("hashchange", stop);
      form.addEventListener("submit", async (event) => {
        event.preventDefault();
        const changes = collectChanges(form, data);
        if (!changes.rooms.length && !changes.windows.length) {
          line.textContent = "Nothing has changed.";
          return;
        }
        save.disabled = true;
        line.textContent = "Saving…";
        let saved = null;
        try {
          saved = await sendJson("/api/quick-fix", { project_id: projectId, ...changes, reviewer: "Quick fixes" });
          dirty = false;
          line.textContent = `Saved: ${saved.applied.join(" ")} Updating the load…`;
          const status = data.status || {};
          if (status.phase1_complete || status.result_exists)
            await runCalculation(projectId, "phase1_base_room", line, "Updating the building load…");
          if (status.phase2_complete || status.results_ready || status.result_exists) {
            await runCalculation(projectId, "full", line, "Updating the final load…");
            navigate(projectId, "results");
          } else {
            navigate(projectId, "rooms");
          }
        } catch (error) {
          // Once the fixes are saved, a failed update must not read as a failed save: saving again only retries it.
          line.textContent = saved
            ? `Saved, but the load didn't update: ${error.message} Save again to retry the update.`
            : `Not saved: ${error.message}`;
          save.disabled = false;
        }
      });
    },
  };
}

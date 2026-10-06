// Contractor view: the default project screen for a mechanical contractor.
//
// Three steps on one screen: reading the drawings (drawing confirmation and the
// AI drawing check), checking rooms and answering the few questions that need
// the contractor, and the result with what is and isn't included. It uses the
// same endpoints and functions as the engineer screens, which stay one click
// away ("Engineer tools"), or open directly with ?engineer=1 or ?operator=1.
(() => {
  const params = new URLSearchParams(location.search);
  const enabled = params.get("engineer") !== "1" && params.get("operator") !== "1";
  const root = document.getElementById("vJob");
  const body = document.getElementById("jobBody");
  if (!root || !body) return;

  const DONE = new Set(["applied", "below_accuracy_bar", "applied_fallback", "blocked", "contractor_answered_not_sure",
                        "not_applicable", "needs_contractor_answer"]);
  const COMPONENTS = [["people", "People"], ["lighting", "Lighting"], ["equipment_refrigeration", "Equipment"],
                      ["envelope", "Walls, roof and glazing"], ["outside_air", "Fresh air"], ["infiltration", "Air leakage"]];
  const POLL_MS = 15000;
  const state = {projectId: null, active: false, engineer: false, skipWaiting: false, builtTasks: false,
                 resolvedFor: "", poll: null, busyFor: null, forceCheck: false, report: null, message: ""};

  const esc = value => String(value ?? "").replace(/[&<>"']/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
  const kw = value => (value == null || !isFinite(value)) ? "—" : Number(value).toFixed(1);
  const storage = {
    get(key) { try { return localStorage.getItem(key) || ""; } catch (_) { return ""; } },
    set(key, value) { try { localStorage.setItem(key, value); } catch (_) { /* private mode */ } },
  };

  async function getJson(url) {
    const response = await fetch(url);
    const data = await response.json();
    if (!response.ok || data.error) throw new Error(data.error || "Request failed.");
    return data;
  }
  async function sendJson(url, payload) {
    const response = await fetch(url, {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(payload)});
    const data = await response.json();
    if (!response.ok || data.error) throw new Error(data.message || data.error || "Request failed.");
    return data;
  }

  // ------------------------------------------------------------------ steps
  function setStep(step) {
    root.querySelectorAll("[data-job-step]").forEach(item => {
      const order = ["read", "check", "result"];
      const here = order.indexOf(step), mine = order.indexOf(item.dataset.jobStep);
      item.classList.toggle("is-current", mine === here);
      item.classList.toggle("is-done", mine < here);
      if (mine === here) item.setAttribute("aria-current", "step"); else item.removeAttribute("aria-current");
    });
  }

  function renderReading(title, detail, extra = "", step = "read") {
    setStep(step);
    body.innerHTML = `<div class="job-card job-progress" data-job-reading>
      <div class="job-spinner" aria-hidden="true"></div>
      <div><h3>${esc(title)}</h3><p>${esc(detail)}</p>${extra}</div></div>`;
  }

  function renderError(message) {
    body.innerHTML = `<div class="job-card job-error" role="alert"><h3>Something needs attention</h3><p>${esc(message)}</p>
      <div class="job-actions"><button class="btn key" type="button" data-job-retry>Try again</button>
      <button class="btn ghost" type="button" data-job-engineer>Open engineer tools</button></div></div>`;
    body.querySelector("[data-job-retry]").addEventListener("click", () => refresh());
    body.querySelector("[data-job-engineer]").addEventListener("click", leave);
  }

  // ------------------------------------------------------------------ flow
  const live = projectId => state.active && state.projectId === projectId;

  async function refresh() {
    const projectId = state.projectId;
    // One update per project at a time; an update left over from another project must not block this one.
    if (!state.active || state.busyFor === projectId) return;
    state.busyFor = projectId;
    clearTimeout(state.poll);
    if (!body.firstElementChild) renderReading("Opening your job", "Loading the drawings and anything already worked out.");
    try {
      if (!DATA?.has_reasoning_packet) {
        renderReading("Preparing your drawings", "Finding the floor plans and getting the pages ready. This takes about three minutes.");
        await confirmSelection();
        if (!live(projectId)) return;
        if (requiredElement("btnContinue").textContent !== "Drawings confirmed") {
          throw new Error("The drawing pages could not be prepared. Open engineer tools to check which pages are included.");
        }
        DATA.has_reasoning_packet = true;
        if (state.analysis) state.analysis.has_reasoning_packet = true;
      }
      let tasks = (await getJson(`/api/autonomous-tasks?project_id=${encodeURIComponent(projectId)}&view=labels`)).tasks || [];
      if (!live(projectId)) return;
      if (!tasks.length && !state.builtTasks) {
        state.builtTasks = true;
        renderReading("Starting the drawing check", "Setting up the measurements and checks for your drawings.");
        tasks = (await sendJson("/api/autonomous-tasks", {project_id: projectId, action: "run_all"})).tasks || [];
      }
      if (!live(projectId)) return;
      const waiting = tasks.filter(row => row.status === "waiting_for_reply");
      if (waiting.length && !state.skipWaiting) {
        const done = tasks.filter(row => DONE.has(row.status)).length;
        renderReading("Checking your drawings",
          `Rooms, walls, windows and equipment are being read from the drawings (${done} of ${tasks.length} checks done). ` +
          "This page updates by itself; you can leave it open.",
          `<button class="btn ghost mini" type="button" data-job-skip>Continue with what's ready</button>`);
        body.querySelector("[data-job-skip]").addEventListener("click", () => { state.skipWaiting = true; refresh(); });
        state.poll = setTimeout(refresh, POLL_MS);
        return;
      }
      await showCheck(tasks);
    } catch (error) {
      if (live(projectId)) renderError(error.message);
    } finally {
      if (state.busyFor === projectId) state.busyFor = null;
    }
  }

  function tasksFingerprint(tasks) {
    return tasks.map(row => `${row.task}:${row.target}:${row.status}`).sort().join("|");
  }

  async function showCheck(tasks) {
    const projectId = state.projectId;
    const questions = tasks.filter(row => row.task === "P5_roof" && row.status === "needs_contractor_answer");
    const fingerprint = tasksFingerprint(tasks);
    let model = await getJson(`/api/ai-preliminary-model?project_id=${encodeURIComponent(projectId)}&view=workspace`);
    if (!live(projectId)) return;
    const resolvedKey = `toki.contractor.resolved.${projectId}`;
    const candidates = model.room_scope?.candidates || [];
    if (!questions.length && (!candidates.length || storage.get(resolvedKey) !== fingerprint) && state.resolvedFor !== fingerprint) {
      renderReading("Measuring rooms and checking inputs", "Working out each room's area, use, people, lighting and equipment. This takes one to two minutes.", "", "check");
      state.resolvedFor = fingerprint;
      await guidedResolveModelInputs();
      if (!live(projectId)) return;
      if (!/Coverage hydrated/.test(requiredElement("guidedModelInputsStatus").textContent)) {
        throw new Error(requiredElement("guidedModelInputsStatus").textContent || "The rooms could not be prepared.");
      }
      storage.set(resolvedKey, fingerprint);
      model = await getJson(`/api/ai-preliminary-model?project_id=${encodeURIComponent(projectId)}&view=workspace`);
    }
    if (!live(projectId)) return;
    const report = model.hourly_ai_preliminary_load_report || {};
    const scope = model.room_scope || {};
    if (!questions.length && report.label && scope.status === "confirmed" && storage.get(resolvedKey) === fingerprint && !state.forceCheck) {
      return showResult(model);
    }
    renderCheck(questions, model);
  }

  function areaSource(row) {
    if (row.area_m2 == null) return "No area yet";
    const origin = String(row.area_origin || "");
    if (origin === "ai_determined") return "Measured from the drawings by AI";
    if (origin.startsWith("printed") || origin === "pdf_evidence") return "Printed on the drawings";
    if (origin === "reviewer_traced") return "Traced";
    return "From the drawings";
  }

  function renderCheck(questions, model) {
    setStep("check");
    const scope = model.room_scope || {};
    const rows = scope.candidates || [];
    const uses = Object.entries(scope.uses || {}).filter(([id]) => id !== "not_a_room");
    const name = storage.get("toki.contractor.name");
    body.innerHTML = `
      ${questions.length ? `<div class="job-card" data-job-questions><h3>A few questions</h3>
        <p class="job-hint">The drawings don't show these. Answer what you know; "Not sure" is fine.</p>
        ${questions.map(row => `<fieldset class="job-question" data-job-question="${esc(row.target)}">
          <legend>${esc(row.room_label || "This room")}: is there a floor or another tenancy above it, or is it the roof?</legend>
          <label><input type="radio" name="q-${esc(row.target)}" value="floor_tenancy_above"> Floor or tenancy above</label>
          <label><input type="radio" name="q-${esc(row.target)}" value="roof_directly_above"> Roof directly above</label>
          <label><input type="radio" name="q-${esc(row.target)}" value="not_sure"> Not sure</label>
        </fieldset>`).join("")}
        <div class="job-actions"><button class="btn key" type="button" data-job-answer>Save answers</button></div>
        <p class="job-status" role="status" data-job-question-status></p></div>` : ""}
      <div class="job-card" data-job-rooms>
        <h3>Rooms in this job</h3>
        <p class="job-hint">Untick anything that shouldn't be cooled. Areas come from the drawings; if one looks wrong, open engineer tools to trace it.</p>
        ${rows.length ? `<table class="job-table"><thead><tr><th scope="col">Include</th><th scope="col">Room</th><th scope="col">Area</th><th scope="col">Where it came from</th></tr></thead><tbody>
          ${rows.map(row => {
            const noArea = row.status === "no_area", needsUse = row.status === "needs_use";
            return `<tr data-job-room="${esc(row.key)}">
              <td><input type="checkbox" aria-label="Include ${esc(row.label)}" data-job-include ${row.include && !noArea ? "checked" : ""} ${noArea ? "disabled" : ""}></td>
              <th scope="row">${esc(row.label)}${needsUse ? `<label class="job-use">What is this room used for?<select data-job-use><option value="">Choose…</option>${uses.map(([id, label]) => `<option value="${esc(id)}">${esc(label)}</option>`).join("")}</select></label>` : ""}</th>
              <td>${row.area_m2 != null ? `${Number(row.area_m2).toFixed(1)} m²` : "—"}</td>
              <td>${esc(areaSource(row))}${noArea ? ` · <button class="link-button" type="button" data-job-trace>Trace it</button>` : ""}</td></tr>`;
          }).join("")}</tbody></table>` : `<p>No rooms were found on the drawings yet. Open engineer tools to trace them.</p>`}
        <div class="job-confirm">
          <label>Your name<input data-job-name autocomplete="name" placeholder="For the record" value="${esc(name)}"></label>
          <button class="btn key" type="button" data-job-calculate ${rows.length ? "" : "disabled"}>Calculate cooling load</button>
        </div>
        <p class="job-status" role="status" data-job-status>${esc(state.message)}</p>
      </div>`;
    state.message = "";
    body.querySelector("[data-job-answer]")?.addEventListener("click", () => saveAnswers(questions));
    body.querySelector("[data-job-calculate]")?.addEventListener("click", () => calculate(model));
    body.querySelectorAll("[data-job-trace]").forEach(button => button.addEventListener("click", () => {
      const key = button.closest("[data-job-room]").dataset.jobRoom;
      leave();
      openRoomForTracing(key);
    }));
  }

  async function saveAnswers(questions) {
    const status = body.querySelector("[data-job-question-status]");
    const answers = questions.map(row => ({row, value: body.querySelector(`[data-job-question="${CSS.escape(row.target)}"] input:checked`)?.value}))
      .filter(item => item.value);
    if (!answers.length) { status.textContent = "Choose an answer for at least one question."; return; }
    status.textContent = "Saving…";
    try {
      for (const {row, value} of answers) {
        await sendJson("/api/autonomous-tasks", {project_id: state.projectId, action: "answer_roof", task: "P5_roof", target: row.target, answer: value});
      }
      state.message = "Answers saved.";
      refresh();
    } catch (error) {
      status.textContent = `Could not save: ${error.message}`;
    }
  }

  async function calculate(model) {
    const status = body.querySelector("[data-job-status]");
    const name = body.querySelector("[data-job-name]").value.trim();
    if (!name) { status.textContent = "Enter your name first; it is recorded with the room list."; body.querySelector("[data-job-name]").focus(); return; }
    storage.set("toki.contractor.name", name);
    const button = body.querySelector("[data-job-calculate]");
    button.disabled = true;
    try {
      for (const row of body.querySelectorAll("[data-job-room]")) {
        const use = row.querySelector("[data-job-use]")?.value;
        if (use) {
          status.textContent = "Saving room uses…";
          await sendJson("/api/room-use-resolution", {project_id: state.projectId, action: "apply_override", room_id: row.dataset.jobRoom,
            taxonomy_id: use, reviewer: name, note: "Use chosen in the contractor room list."});
        }
      }
      status.textContent = "Calculating…";
      let latest = model;
      if (body.querySelector("[data-job-use] option:checked:not([value=''])")) {
        latest = await sendJson("/api/ai-preliminary-model", {project_id: state.projectId, action: "assemble", settings: aiPreliminarySettings(), response_view: "workspace"});
      }
      const rows = [...body.querySelectorAll("[data-job-room]")].map(row => {
        const include = row.querySelector("[data-job-include]").checked;
        return {key: row.dataset.jobRoom, include, reason: include ? "" : "Not cooled (contractor)"};
      });
      await sendJson("/api/ai-preliminary-model", {project_id: state.projectId, action: "confirm_room_scope", reviewer: name, response_view: "workspace",
        candidate_fingerprint: latest.room_scope?.candidate_fingerprint || "", rows, settings: aiPreliminarySettings()});
      const result = await sendJson("/api/ai-preliminary-model", {project_id: state.projectId, action: "calculate", settings: aiPreliminarySettings(), response_view: "workspace"});
      if (typeof drawAiPreliminary === "function") drawAiPreliminary(result);
      state.forceCheck = false;
      showResult(result);
    } catch (error) {
      status.textContent = `Could not calculate: ${error.message}`;
      button.disabled = false;
    }
  }

  // ------------------------------------------------------------------ result
  function exclusionLines(report) {
    // Plain-language groups: one line per kind of missing load, room names only when it isn't every room.
    const prefixes = {
      roof_solar: "Sun on the roof", roof_exposure: "Roof (not checked yet)", unclassified_wall_boundaries: "Walls (not classified yet)",
      external_wall_orientation: "Sun on outside walls", external_wall_edge: "Outside walls", area_only_walls: "Walls (not assessed for this drawing type)",
      area_only_roof: "Roof (not assessed for this drawing type)",
    };
    const groups = {
      extract_air: "Exhaust and make-up air (needs the rangehood and exhaust rates)", make_up_air: "Exhaust and make-up air (needs the rangehood and exhaust rates)",
      infiltration: "Air leakage through doors and gaps",
      vapour_gain: "Moisture from cooking and dishwashing", steam_gain: "Moisture from cooking and dishwashing",
      process_latent_load: "Moisture from cooking and dishwashing",
      minimum_supply_air: "Air system design (supply, spill and transfer air) — set later by the engineer",
      spill_air: "Air system design (supply, spill and transfer air) — set later by the engineer",
      transfer_air: "Air system design (supply, spill and transfer air) — set later by the engineer",
      envelope: "Walls, roof and glazing",
    };
    const names = new Map([...(report.room_names || []), ...(report.room_peaks || []), ...(report.scenario_results?.[0]?.rooms || [])]
      .map(room => [room.room_id, room.name]));
    const allRooms = new Set([...names.values()].filter(Boolean));
    const grouped = new Map();
    for (const item of [...(report.known_exclusions || []), ...(report.unresolved_room_inputs || [])]) {
      const id = item.component_id || "";
      if (/^boundary_edge/.test(id) || ["Internal boundary", "Adjacent tenancy boundary", "Faces an enclosed mall/walkway"].includes(item.component)) continue;
      const label = Object.entries(prefixes).find(([prefix]) => id.startsWith(prefix))?.[1] || groups[item.component_type] || item.component || "Other items";
      if (!grouped.has(label)) grouped.set(label, new Set());
      const room = names.get(item.room_id) || item.room_name;
      if (room) grouped.get(label).add(room);
    }
    return [...grouped.entries()].sort(([a], [b]) => a.localeCompare(b)).map(([label, rooms]) =>
      rooms.size && rooms.size < allRooms.size ? `${label} — ${[...rooms].join(", ")}` : label);
  }

  function roomRows(report) {
    const areas = new Map((report.confirmed_rooms || []).map(room => [room.label, room]));
    const peaks = report.room_peaks || (report.scenario_results?.[0]?.rooms || [])
      .map(room => ({room_id: room.room_id, name: room.name, design_total_kw: room.peak?.design_total_kw}));
    return peaks.map(room => {
      const total = room.design_total_kw;
      const area = areas.get(room.name)?.area_m2;
      return {name: room.name, area, kw: total, wPerM2: area ? (total * 1000) / area : null,
              source: areas.has(room.name) ? areaSource(areas.get(room.name)) : ""};
    });
  }

  function showResult(model) {
    setStep("result");
    const report = model.hourly_ai_preliminary_load_report || {};
    state.report = report;
    const peak = report.included_scope_peak || {};
    const total = report.final_design_total_kw ?? peak.design_total_kw;
    const rooms = roomRows(report);
    const components = peak.components || {};
    const excluded = exclusionLines(report);
    const refrigeration = report.refrigeration_process_exclusions || [];
    const basis = report.design_conditions_basis || {};
    const hour = peak.display_hour ?? peak.hour;
    const factor = Number(report.safety_factor || 1);
    body.innerHTML = `<div class="job-card job-result" data-job-result>
      <p class="job-draft-banner">Draft estimate from the drawings — not engineering-reviewed. Check it before using it for equipment selection.</p>
      <div class="job-total"><span class="job-total-number" data-job-total>${kw(total)}</span><span class="job-total-unit">kW total cooling</span></div>
      <p class="job-hint">Peak ${hour != null ? `at ${hour > 12 ? hour - 12 : hour} ${hour >= 12 ? "pm" : "am"} on the design day` : "on the design day"}${factor > 1 ? `, including a ${Math.round((factor - 1) * 100)}% allowance` : ""}.</p>
      <h3>By room</h3>
      <table class="job-table" data-job-room-loads><thead><tr><th scope="col">Room</th><th scope="col">Area</th><th scope="col">Cooling</th><th scope="col">W/m²</th></tr></thead><tbody>
        ${rooms.map(room => `<tr><th scope="row">${esc(room.name)}</th><td>${room.area != null ? `${Number(room.area).toFixed(1)} m²` : "—"}</td><td>${kw(room.kw)} kW</td><td>${room.wPerM2 != null ? Math.round(room.wPerM2) : "—"}</td></tr>`).join("")}
      </tbody></table>
      <h3>What's in the total</h3>
      <ul class="job-list" data-job-included>${COMPONENTS.filter(([id]) => components[id]).map(([id, label]) => `<li><span>${esc(label)}</span><b>${kw(components[id].total_kw)} kW</b></li>`).join("")}</ul>
      ${excluded.length || refrigeration.length ? `<h3>Not included yet</h3>
        <ul class="job-list job-excluded" data-job-excluded>${excluded.map(line => `<li>${esc(line)}</li>`).join("")}
        ${refrigeration.map(item => `<li>${esc(item.room_name || "Cold room")} — refrigeration, sized separately</li>`).join("")}</ul>` : ""}
      ${basis.design_day ? `<h3>Weather and site used</h3><ul class="job-list job-basis">${[basis.design_day?.label, basis.sun?.label, basis.site?.label].filter(Boolean).map(line => `<li>${esc(line)}</li>`).join("")}</ul>` : ""}
      <div class="job-actions job-no-print">
        <button class="btn key" type="button" data-job-print>Print or save as PDF</button>
        <button class="btn ghost" type="button" data-job-csv>Download room loads (CSV)</button>
        <button class="btn ghost" type="button" data-job-change>Change rooms or answers</button>
      </div></div>`;
    body.querySelector("[data-job-print]").addEventListener("click", () => window.print());
    body.querySelector("[data-job-csv]").addEventListener("click", () => downloadCsv(rooms, total));
    body.querySelector("[data-job-change]").addEventListener("click", () => { state.forceCheck = true; refresh(); });
  }

  function downloadCsv(rooms, total) {
    const cell = value => `"${String(value ?? "").replace(/"/g, '""')}"`;
    const lines = [["Room", "Area (m2)", "Cooling (kW)", "W/m2", "Area source"].map(cell).join(",")]
      .concat(rooms.map(room => [room.name, room.area != null ? Number(room.area).toFixed(1) : "", kw(room.kw),
                                 room.wPerM2 != null ? Math.round(room.wPerM2) : "", room.source].map(cell).join(",")))
      .concat([["Total (draft)", "", kw(total), "", ""].map(cell).join(",")]);
    const blob = new Blob([lines.join("\r\n") + "\r\n"], {type: "text/csv"});
    const link = document.createElement("a");
    link.href = URL.createObjectURL(blob);
    link.download = `${(DATA?.name || "job").replace(/\.pdf$/i, "")} - cooling loads.csv`;
    document.body.appendChild(link);
    link.click();
    setTimeout(() => { URL.revokeObjectURL(link.href); link.remove(); }, 0);
  }

  // ------------------------------------------------------------------ enter / leave
  function enter(data) {
    if (!enabled || state.engineer) return;
    const projectId = data?.id || DATA?.id;
    if (!projectId) return;
    if (data?.sheets) state.analysis = data;
    if (state.projectId !== projectId) {
      clearTimeout(state.poll);
      body.innerHTML = "";
      Object.assign(state, {projectId, skipWaiting: false, builtTasks: false, resolvedFor: "", forceCheck: false, message: ""});
    }
    state.active = true;
    document.getElementById("jobTitle").textContent = String(data?.name || DATA?.name || "Your job").replace(/\.pdf$/i, "");
    show("vJob");
    requiredElement("topTitle").textContent = "Cooling load";
    requiredElement("topSub").textContent = data?.name || DATA?.name || "";
    document.getElementById("btnSimpleView")?.classList.add("hide");
    refresh();
  }

  function leave() {
    state.active = false;
    state.engineer = true;
    clearTimeout(state.poll);
    // The engineer screen was not loaded while the contractor view was open; load it now.
    if (state.analysis) showResults(state.analysis); else show("vRes");
    requiredElement("topTitle").textContent = "Engineer tools";
    if (enabled) document.getElementById("btnSimpleView")?.classList.remove("hide");
  }

  document.getElementById("btnJobEngineer")?.addEventListener("click", leave);
  document.getElementById("btnSimpleView")?.addEventListener("click", () => { state.engineer = false; enter(DATA); });

  window.ContractorFlow = {
    enabled,
    isActive: () => state.active,
    takesOver: () => enabled && !state.engineer,
    onProjectShown(data) { if (enabled && !state.engineer) enter(data); },
    onProjectLeft() { state.active = false; clearTimeout(state.poll); document.getElementById("btnSimpleView")?.classList.add("hide"); },
  };
})();

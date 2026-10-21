import { getJson, sendJson, escapeHtml, fmt, navigate } from "./api.js";
import { planSvg, lineLegend } from "./plan.js";

const query = (projectId) => `?project_id=${encodeURIComponent(projectId)}`;
const activeCalculations = new Set();

function isNumber(value) {
  return typeof value === "number" && Number.isFinite(value);
}

function formatKw(value) {
  return isNumber(value) ? `${fmt(value)} kW` : "Not calculated yet";
}

function sourceLabel(row) {
  return (
    {
      pdf_evidence: "Printed on the drawings",
      accepted_finding: "Drawing evidence",
      controlled_preliminary_profile: "Typical value",
      unresolved: "Not resolved",
      edited: "Entered by you",
    }[row?.origin] ||
    row?.source ||
    "Not resolved"
  );
}

function inputRows(model, room) {
  const data = (model.phase2_room_inputs || []).find(
    (item) => item.room_id === room.room_id,
  ) || { people: {}, lights: {}, equipment: [], air: [] };
  const people = data.people?.count;
  const sensible = data.people?.sensible_w_per_person?.value;
  const latent = data.people?.latent_w_per_person?.value;
  const peopleDetail = [
    sensible == null ? "" : `${fmt(sensible)} W sensible/person`,
    latent == null ? "" : `${fmt(latent)} W latent/person`,
  ].filter(Boolean);
  const peopleText =
    people?.value == null
      ? "Not resolved"
      : `${fmt(people.value, 0)} ${people.unit || "people"}${peopleDetail.length ? `; ${peopleDetail.join(", ")}` : ""}`;
  const lights = data.lights?.load;
  const lightText =
    lights?.value == null
      ? "Not resolved"
      : `${fmt(lights.value)} ${lights.unit || "W"}${data.lights?.type ? `; ${String(data.lights.type).replaceAll("_", " ")}` : ""}`;
  const airRows = (data.air || []).map((item) => [
    item.label ||
      String(item.air_path_type || "Fresh air").replaceAll("_", " "),
    item.value == null
      ? "Not resolved"
      : `${fmt(item.value)} ${item.unit || ""}`.trim(),
    sourceLabel(item),
    item.rationale || item.source || item.status,
  ]);
  return {
    data,
    rows: [
      ["People", peopleText, sourceLabel(people), people?.rationale],
      ["Lights", lightText, sourceLabel(lights), lights?.rationale],
      ...(airRows.length
        ? airRows
        : [
            [
              "Fresh air and infiltration",
              "Not resolved",
              "Not resolved",
              "No room-specific airflow value was found.",
            ],
          ]),
    ],
  };
}

function roomCard(room, plan, model) {
  const { data, rows } = inputRows(model, room);
  const phase1 = formatKw(room.phase1_kw);
  const adds =
    isNumber(room.final_kw) && isNumber(room.phase1_kw)
      ? `${fmt(room.final_kw - room.phase1_kw)} kW`
      : "Not calculated yet";
  const roomTotal = formatKw(room.final_kw);
  const operating = rows
    .map(
      ([label, value, source, reason]) => `<tr>
      <th scope="row">${escapeHtml(label)}</th>
      <td title="${escapeHtml(reason || source)}">${escapeHtml(value)}</td>
      <td>${escapeHtml(source)}</td>
    </tr>`,
    )
    .join("");
  const roomEquipment = model.equipment_coverage
    ? model.equipment_coverage.records.filter((item) => item.room === room.name).map((item) => ({
        id: item.id, value: item.value, pages: item.pages, catalogue_resolution: item,
      }))
    : data.equipment || [];
  const equipment = roomEquipment.length
    ? `<ul class="job-equipment-list">${roomEquipment
        .map((item) => equipmentItem(item, false))
        .join("")}</ul>`
    : `<p class="job-muted">Equipment will appear here when it is found in the drawings.</p>`;
  return `<details class="job-operating-room" ${room.room_id === plan.rooms?.find((item) => item.kind !== "refrigeration")?.room_id ? "open" : ""}>
    <summary><b>${escapeHtml(room.name)}</b><span>${room.area_m2 == null ? "Area not available" : `${fmt(room.area_m2)} m²`}</span><span>Phase 1 ${escapeHtml(phase1)}</span><span>Phase 2 adds ${escapeHtml(adds)}</span></summary>
    <div class="job-operating-detail">
      <h2>${escapeHtml(room.name)}</h2>
      <p class="job-room-use">${escapeHtml(roomUse(room))}</p>
      <div class="job-key-facts">
        <div><span>Empty-room load</span><strong>${escapeHtml(phase1)}</strong></div>
        <div><span>Operating loads</span><strong>${escapeHtml(adds)}</strong></div>
        <div><span>Room total</span><strong>${escapeHtml(roomTotal)}</strong></div>
      </div>
      <h3>People, lights and fresh air</h3>
      <div class="job-table-wrap"><table class="job-operating-table">
        <thead><tr><th scope="col">Load</th><th scope="col">Value</th><th scope="col">Where it came from</th></tr></thead>
        <tbody>${operating}</tbody>
      </table></div>
      <h3>Equipment</h3>${equipment}
      <p class="job-caption">Each item says where its value came from: data sheet, matched model, typical or generic value. Items with no value or no room aren't counted; the room keeps its typical equipment allowance.</p>
    </div>
  </details>`;
}

function roomUse(room) {
  return room.kind === "refrigeration" ? "Refrigeration" : "Comfort-HVAC room";
}

function scheduleEditor(schedule) {
  return `<fieldset><legend>Equipment operating hours</legend>
    <p>Enter hours from 0 to 24 and the operating fraction from 0 to 1. Enter fraction 0 for days it is off. Sunday also applies to public holidays.${schedule ? " Leave all hours blank to keep the saved schedule." : ""}</p>
    ${["weekday", "saturday", "sunday"].map((day) => `<div class="job-fields">
      <b>${day === "sunday" ? "Sunday and public holidays" : day === "weekday" ? "Weekdays" : "Saturday"}</b>
      <label class="job-field"><span>Start hour</span><input name="${day}_start" type="number" min="0" max="24" step="0.25"></label>
      <label class="job-field"><span>End hour</span><input name="${day}_end" type="number" min="0" max="24" step="0.25"></label>
      <label class="job-field"><span>Operating fraction</span><input name="${day}_fraction" type="number" min="0" max="1" step="0.01"></label>
    </div>`).join("")}</fieldset>`;
}

function editedSchedule(form, existing) {
  const days = ["weekday", "saturday", "sunday"];
  if (existing && days.every((day) => ["start", "end", "fraction"].every((field) => form.elements[`${day}_${field}`].value === ""))) return existing;
  const schedule = {};
  for (const day of days) {
    const values = ["start", "end", "fraction"].map((field) => form.elements[`${day}_${field}`].value);
    if (values[2] === "") throw new Error("Provide an operating fraction for each day type.");
    const fraction = Number(values[2]);
    if (fraction === 0) { schedule[day] = Array(24).fill(0); continue; }
    if (values[0] === "" || values[1] === "") throw new Error("Provide start and end hours for each operating day.");
    const [start, end] = values.map(Number);
    schedule[day] = Array.from({length: 24}, (_, hour) => {
      const overlap = (lo, hi) => Math.max(0, Math.min(hour + 1, hi) - Math.max(hour, lo));
      return fraction * (end >= start ? overlap(start, end) : overlap(start, 24) + overlap(0, end));
    });
  }
  schedule.holiday = [...schedule.sunday];
  return schedule;
}

const COUNTED = new Set(["data_sheet", "matched_model", "typical", "generic"]);

// One line on how the equipment was valued; it never stops the calculation (user decision 2026-10-20).
function equipmentSummary(coverage) {
  if (!coverage) return "";
  const count = (status) => Number(coverage.summary?.[status] || 0);
  const parts = [["data_sheet", "from data sheets"], ["matched_model", "matched models"], ["typical", "typical values"],
    ["generic", "generic values"], ["no_value", "with no value yet"], ["no_room", "with no room"]]
    .filter(([status]) => count(status)).map(([status, words]) => `${count(status)} ${words}`);
  const text = parts.length ? `Equipment: ${parts.join(", ")}.` : "";
  return [coverage.catalogue_error, text, coverage.reading_note].filter(Boolean).join(" ");
}

function equipmentItem(item, editable = true) {
  const resolution = item.catalogue_resolution || {};
  const value = item.decided_value || item.value || {};
  const code = value.code ? `${value.code} ` : "";
  const label = resolution.label || "No value yet";
  const usable = COUNTED.has(resolution.status);
  const contribution = usable
    ? `${fmt(resolution.sensible_total_w)} W sensible; ${fmt(resolution.latent_total_w)} W latent before the use pattern${resolution.heat_basis ? ` (${resolution.heat_basis})` : ""}`
    : resolution.status === "excluded" ? "Not counted as equipment heat" : "Not counted";
  const source = resolution.source || item.evidence || "";
  const controls = `<div class="job-editor-actions">
    <button class="job-button secondary" data-edit-equipment="${escapeHtml(item.id)}">Edit equipment</button>
    <button class="job-button secondary" data-equipment="${escapeHtml(item.id)}" data-decision="rejected">Reject item</button>
    </div>
    <form data-equipment-editor="${escapeHtml(item.id)}" hidden>
      <label class="job-field"><span>Room</span><input name="room" value="${escapeHtml(value.room || resolution.room || "")}" required></label>
      <label class="job-field"><span>Quantity</span><input name="quantity" type="number" min="1" step="1" value="${escapeHtml(value.quantity ?? "")}" required></label>
      <label class="job-field"><span>Sensible heat to room (W/unit)</span><input name="sensible" type="number" min="0" step="any" value="${escapeHtml(value.sensible_to_room_w ?? "")}" required></label>
      <label class="job-field"><span>Latent heat to room (W/unit)</span><input name="latent" type="number" min="0" step="any" value="${escapeHtml(value.latent_to_room_w ?? "")}" required></label>
      <label class="job-field"><span>Manufacturer reference or measurement</span><input name="reference" value="${escapeHtml(value.heat_reference || "")}" required></label>
      ${scheduleEditor(value.equipment_schedule || resolution.schedule)}
      <button class="job-button secondary">Save equipment</button><p role="status"></p>
    </form>`;
  return `<li data-equipment-status="${escapeHtml(resolution.status || "no_value")}">
    <strong>${escapeHtml(code + (value.name || item.label || resolution.name || "Equipment"))}${value.quantity > 1 ? ` × ${escapeHtml(value.quantity)}` : ""}</strong>
    <b>${label}</b>
    <span>${escapeHtml(resolution.room || value.room || item.suggested_room || "Room not identified")}; quantity ${escapeHtml(resolution.quantity ?? value.quantity ?? "unknown")}; ${escapeHtml(contribution)}</span>
    <small>Drawing pages ${(item.pages || []).join(", ") || "not cited"}. ${escapeHtml(source)}</small>
    ${resolution.source_row ? `<small>${escapeHtml(resolution.typical_id ? "Typical values sheet" : "Catalogue")} row ${escapeHtml(resolution.source_row)}${resolution.use_pattern ? `; use pattern ${escapeHtml(resolution.use_pattern)}` : ""}.</small>` : ""}
    ${(item.schedule_notes || []).map((note) => `<small data-schedule-note>${escapeHtml(note)}</small>`).join("")}
    ${(item.possibly_same_as || []).length ? `<small data-possible-duplicate>Possibly the same as ${escapeHtml(item.possibly_same_as.map((row) => row.label).join(" or "))}; reject it if so.</small>` : ""}
    ${resolution.note ? `<p>${escapeHtml(resolution.note)}</p>` : ""}
    ${(resolution.reasons || []).map((reason) => `<small>${escapeHtml(reason)}</small>`).join("")}
    ${editable ? controls : ""}
  </li>`;
}

function unresolvedNeeds(workflow) {
  const answers = workflow.answers || {};
  return (workflow.findings || []).filter(
    (row) =>
      row.subskill_id === "information_needs" &&
      row.field === "needs" &&
      !["accepted", "rejected", "answered"].includes(row.status) &&
      !answers[row.id],
  );
}

function needsMarkup(needs) {
  if (!needs.length) {
    return `<p>No required operating-load answers are waiting. Typical values remain visible with their source.</p>`;
  }
  return needs
    .map(
      (
        need,
      ) => `<form class="job-need-form" data-finding="${escapeHtml(need.id)}" data-kind="${escapeHtml(need.kind || need.value?.kind || "")}">
      <p>${escapeHtml(need.question || need.label || need.reason || "An operating-load detail needs an answer.")}</p>
      <label class="job-field"><span>Answer</span><input name="value" required></label>
      <label class="job-field"><span>Where this came from</span><input name="source" placeholder="Drawing page or source" required></label>
      <button class="job-button secondary">Save answer</button><p class="job-note" role="status"></p>
    </form>`,
    )
    .join("");
}

function calculationStatus(job, status, needs, projectId) {
  if (activeCalculations.has(projectId))
    return "The final room loads are being calculated.";
  if (["queued", "running"].includes(job?.status))
    return "The final room loads are being calculated.";
  if (status.results_ready && !status.result_stale)
    return "Operating loads are complete. Results are ready.";
  if (needs.length)
    return `${needs.length} operating-load ${needs.length === 1 ? "answer is" : "answers are"} needed to finish the result.`;
  return "The AI is assembling people, lights, equipment and airflow for each room.";
}

export async function renderOperating(projectId) {
  const [plan, model, workflow, equipment, calculation] = await Promise.all([
    getJson(`/api/job-plan${query(projectId)}`),
    getJson(`/api/ai-preliminary-model${query(projectId)}&view=workspace`),
    getJson(`/api/skill-workflow${query(projectId)}`).catch(() => ({})),
    getJson(
      `/api/page-extraction${query(projectId)}&kind=equipment_appliances`,
    ).catch(() => ({})),
    getJson(`/api/job-calculation${query(projectId)}`).catch(() => ({})),
  ]);
  const status = { ...(plan.status || {}) };
  if (model.hourly_ai_preliminary_load_report_stale) status.result_stale = true;
  const coverage = equipment.equipment_coverage || model.equipment_coverage;
  const coverageMessage = equipmentSummary(coverage);
  const catalogueWarnings = (coverage?.catalogue_diagnostics || []).map((row) => `Row ${row.row}: ${row.reasons.join(" ")}`).join(" ");
  const needs = unresolvedNeeds(workflow);
  const equipmentItems = equipment.findings || [];
  const equipmentMarkup = equipmentItems.length
    ? `<ul class="job-equipment-list">${equipmentItems.map((item) => equipmentItem(item)).join("")}</ul>`
    : `<p class="job-muted">No equipment items have been read yet.</p>`;
  const rooms = (plan.rooms || []).filter(
    (room) => room.kind !== "refrigeration" && room.included,
  );
  const content = `<div class="job-columns job-rooms-layout">
    <section class="job-panel job-room-plan" aria-labelledby="operating-plan-title">
      <h2 id="operating-plan-title">Rooms and operating loads</h2>
      ${planSvg(plan)}${lineLegend()}
      <div class="job-operating-list">${rooms.map((room) => roomCard(room, plan, model)).join("")}</div>
    </section>
    <section class="job-panel job-operating-needs" aria-labelledby="operating-needs-title">
      <h2 id="operating-needs-title">Operating-load questions <span>(${needs.length})</span></h2>
      ${needsMarkup(needs)}
      <h3>Equipment found in the drawings</h3>
      ${coverageMessage ? `<p role="status" data-equipment-coverage>${escapeHtml(coverageMessage)}</p>` : ""}
      ${catalogueWarnings ? `<details><summary>Catalogue entries needing correction</summary><p>${escapeHtml(catalogueWarnings)}</p></details>` : ""}
      ${coverage?.assessment_error ? `<p>${escapeHtml(coverage.assessment_error)}</p>` : ""}
      ${equipmentMarkup}
      ${status.results_ready && status.result_stale ? `<button class="job-button" id="job-update-equipment-result">Update final load</button><p>The saved report is retained until you recalculate.</p>` : ""}
      <p class="job-note" id="job-full-calculation" role="status"></p>
    </section>
  </div>`;
  return {
    status,
    statusLine: coverageMessage || calculationStatus(calculation, status, needs, projectId),
    content,
    bind: () => {
      const calculationRunning = ["queued", "running"].includes(
        calculation.status,
      );
      const startCalculation = async () => {
        if (activeCalculations.has(projectId)) return;
        activeCalculations.add(projectId);
        const line = document.querySelector("#job-full-calculation");
        const button = document.querySelector("#job-update-equipment-result");
        if (button) button.disabled = true;
        if (line) line.textContent = "Calculating the final room loads…";
        try {
          let job = await sendJson("/api/job-calculation", { project_id: projectId, phase: "full", reviewer: "Job workspace" });
          while (["queued", "running"].includes(job.status)) {
            await new Promise((resolve) => setTimeout(resolve, 2000));
            job = await getJson(`/api/job-calculation${query(projectId)}`);
          }
          if (job.status !== "done") throw new Error(job.error || "The final load calculation did not finish.");
          navigate(projectId, "results");
        } catch (error) {
          if (line) line.textContent = `Could not finish the final load: ${error.message}`;
        } finally {
          activeCalculations.delete(projectId);
          if (button) button.disabled = false;
        }
      };
      if (status.phase1_complete && Number(status.needs_you?.phase2 || 0) === 0 && !calculationRunning
          && !status.results_ready) startCalculation();
      document.querySelector("#job-update-equipment-result")?.addEventListener("click", startCalculation);
      document.querySelectorAll(".job-need-form").forEach((form) => {
        form.addEventListener("submit", async (event) => {
          event.preventDefault();
          const button = form.querySelector("button");
          button.disabled = true;
          try {
            await sendJson("/api/skill-workflow", {
              project_id: projectId,
              action: "answer_need",
              finding_id: form.dataset.finding,
              kind: form.dataset.kind,
              value: form.elements.value.value,
              source: form.elements.source.value,
              reviewer: "Job workspace",
            });
            navigate(projectId, "operating");
          } catch (error) {
            form.querySelector(".job-note").textContent = error.message;
            button.disabled = false;
          }
        });
      });
      document.querySelectorAll("[data-edit-equipment]").forEach((button) => {
        button.addEventListener("click", () => {
          const form = button.closest("li").querySelector("form");
          form.hidden = !form.hidden;
        });
      });
      document.querySelectorAll("[data-equipment-editor]").forEach((form) => {
        form.addEventListener("submit", async (event) => {
          event.preventDefault();
          const button = form.querySelector("button");
          button.disabled = true;
          try {
            const original = equipmentItems.find((item) => item.id === form.dataset.equipmentEditor);
            const value = { ...(original.decided_value || original.value), room: form.elements.room.value,
              quantity: Number(form.elements.quantity.value), sensible_to_room_w: Number(form.elements.sensible.value),
              latent_to_room_w: Number(form.elements.latent.value), heat_reference: form.elements.reference.value,
              equipment_schedule: editedSchedule(form, original.decided_value?.equipment_schedule || original.value?.equipment_schedule || original.catalogue_resolution?.schedule) };
            await sendJson("/api/page-extraction", { project_id: projectId, action: "review", kind: "equipment_appliances",
              finding_id: original.id, decision: "accepted", value, reviewer: "Job workspace" });
            navigate(projectId, "operating");
          } catch (error) {
            form.querySelector("[role=status]").textContent = error.message;
          } finally { button.disabled = false; }
        });
      });
      document.querySelectorAll("[data-equipment]").forEach((button) => {
        button.addEventListener("click", async () => {
          button.disabled = true;
          try {
            let value;
            if (button.dataset.decision === "accepted") {
              value = JSON.parse(button.dataset.value || "{}");
              const rating = document.querySelector(
                `[data-rating-for="${CSS.escape(button.dataset.equipment)}"]`,
              )?.value;
              if (rating) value.rated_power = Number(rating);
            }
            await sendJson("/api/page-extraction", {
              project_id: projectId,
              action: "review",
              kind: "equipment_appliances",
              finding_id: button.dataset.equipment,
              decision: button.dataset.decision,
              ...(value ? { value } : {}),
              reviewer: "Job workspace",
            });
            navigate(projectId, "operating");
          } catch (error) {
            button.disabled = false;
            button.textContent = error.message;
          }
        });
      });
    },
  };
}

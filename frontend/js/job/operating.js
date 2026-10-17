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
    "AI estimate"
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
  const equipment = (data.equipment || []).length
    ? `<ul class="job-equipment-list">${data.equipment
        .map((item) => equipmentItem(item))
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
      <p class="job-caption">Typical values and estimates are shown with their source. They do not stop the calculation.</p>
    </div>
  </details>`;
}

function roomUse(room) {
  return room.kind === "refrigeration" ? "Refrigeration" : "Comfort-HVAC room";
}

function equipmentItem(item) {
  const value = item.value || {};
  const rating = item.heat?.heat_w ?? value.rated_power;
  const source =
    item.heat?.rated_source || item.evidence || "Source not recorded";
  const ratingInput =
    item.status === "proposed"
      ? `<label class="job-field"><span>Data-sheet rating in watts, if known</span><input type="number" min="0" step="1" data-rating-for="${escapeHtml(item.id)}" value="${escapeHtml(value.rated_power || "")}"></label>
      <div class="job-editor-actions"><button class="job-button secondary" data-equipment="${escapeHtml(item.id)}" data-decision="accepted" data-value="${escapeHtml(JSON.stringify(value))}">Accept or save rating</button><button class="job-button secondary" data-equipment="${escapeHtml(item.id)}" data-decision="rejected">Reject item</button></div>`
      : `<small>${escapeHtml(item.status || "Recorded")}</small>`;
  return `<li>
    <strong>${escapeHtml(value.name || item.label || "Equipment")}</strong>
    <span>${escapeHtml(item.suggested_room || "Room not identified")}; ${rating == null ? "rating not found" : `${fmt(rating)} W`}</span>
    <small>Drawing pages ${(item.pages || []).join(", ") || "not cited"}. ${escapeHtml(source)}</small>
    ${ratingInput}
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
  const status = plan.status || {};
  const needs = unresolvedNeeds(workflow);
  const equipmentItems = equipment.findings || [];
  const equipmentMarkup = equipmentItems.length
    ? `<ul class="job-equipment-list">${equipmentItems.map(equipmentItem).join("")}</ul>`
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
      <h2 id="operating-needs-title">What still needs an answer <span>(${status.needs_you?.phase2 || 0})</span></h2>
      ${needsMarkup(needs)}
      <h3>Equipment found in the drawings</h3>
      ${equipmentMarkup}
      <p class="job-note" id="job-full-calculation" role="status"></p>
    </section>
  </div>`;
  return {
    status,
    statusLine: calculationStatus(calculation, status, needs, projectId),
    content,
    bind: () => {
      const hasCurrentResult = status.results_ready && !status.result_stale;
      const calculationRunning = ["queued", "running"].includes(
        calculation.status,
      );
      if (
        status.phase1_complete &&
        Number(status.needs_you?.phase2 || 0) === 0 &&
        !calculationRunning &&
        !hasCurrentResult &&
        !activeCalculations.has(projectId)
      ) {
        activeCalculations.add(projectId);
        const line = document.querySelector("#job-full-calculation");
        if (line) line.textContent = "Calculating the final room loads…";
        (async () => {
          try {
            let job = await sendJson("/api/job-calculation", {
              project_id: projectId,
              phase: "full",
              reviewer: "Job workspace",
            });
            while (["queued", "running"].includes(job.status)) {
              await new Promise((resolve) => setTimeout(resolve, 2000));
              job = await getJson(`/api/job-calculation${query(projectId)}`);
            }
            if (job.status !== "done") {
              throw new Error(
                job.error || "The final load calculation did not finish.",
              );
            }
            navigate(projectId, "results");
          } catch (error) {
            activeCalculations.delete(projectId);
            if (line)
              line.textContent = `Could not finish the final load: ${error.message}`;
          }
        })();
      }
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

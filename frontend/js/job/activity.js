import { escapeHtml } from "./api.js";

const expanded = new Map();
const skillLabel = (row) => row.label || String(row.id || "Evidence review").replaceAll("_", " ");
const running = (value) => value?.step_state === "active";
const finished = new Set(["resolved", "provisional", "needs_review", "completed", "done", "excluded", "not_applicable"]);
const hidden = new Set(["not_in_scope", "not_enabled"]);
const labels = {
  running: "Working", queued: "Waiting", not_started: "Waiting", blocked: "Waiting for evidence",
  resolved: "Checked", provisional: "Finished with assumptions", needs_review: "Finished with exceptions",
  completed: "Finished", done: "Finished", failed: "Stopped", interrupted: "Interrupted", stale: "Out of date",
  excluded: "Excluded", not_applicable: "Not needed", applied: "Answer applied", applied_fallback: "Estimate applied",
  below_accuracy_bar: "Estimate applied", waiting_for_reply: "Answer rejected; correction needed",
  rejected: "Answer rejected", provider_failed: "AI request failed", unavailable: "Evidence unavailable",
};
const textState = (value) => labels[value] || "Recorded";

function taskRow(role, label, state, detail = "", active = false) {
  return `<li class="job-activity-task${active ? " is-active" : ""}">
    <span class="job-caption">${escapeHtml(role)}</span>
    <strong>${escapeHtml(label)}</strong>
    <span class="job-activity-state">${escapeHtml(state)}</span>
    ${detail ? `<p>${escapeHtml(detail)}</p>` : ""}</li>`;
}

function skillRow(row, activeRun, rows) {
  const active = row.status === "running" && activeRun;
  const state = row.status === "running" && !activeRun ? "Not currently running" : textState(row.status);
  const blockers = (row.blocker_ids?.length ? row.blocker_ids : row.prerequisites || [])
    .map((id) => rows.find((other) => other.id === id))
    .filter((other) => other && !finished.has(other.status))
    .map((other) => other.label || "earlier evidence");
  const summary = row.summary || {};
  const detail = active ? "Reading evidence and preparing an answer."
    : finished.has(row.status) ? (summary.record_count != null
      ? `${Number(summary.record_count) || 0} records returned; ${Number(summary.citation_count) || 0} citations; ${Number(summary.unresolved_count) || 0} unresolved items.` : "Answer saved.")
    : blockers.length ? `Waiting for ${blockers.join(", ")}.` : "";
  return taskRow(row.parent_label || "Skill specialist", skillLabel(row), state, detail, active);
}

export function activityMarkup(data, projectId = "") {
  const open = expanded.get(projectId) || new Set();
  const skills = (data.envelope?.subskills || []).filter((row) => !hidden.has(row.status));
  const activeSkills = skills.filter((row) => row.status === "running" && running(data.envelope));
  const investigations = running(data.supervisor) ? data.supervisor.active_investigations || [] : [];
  const active = [];
  if (running(data.reading)) active.push(taskRow("Drawing reader", "Read drawing pages", "Working",
    (data.reading.page_count ?? data.reading.total) != null
      ? `${Number(data.reading.pages_read ?? data.reading.read) || 0} of ${Number(data.reading.page_count ?? data.reading.total)} pages read.`
      : "Reading the drawing pages.", true));
  if (running(data.equipment)) active.push(taskRow("Equipment reader", "Find equipment evidence", "Working",
    "Saving equipment evidence for operating loads.", true));
  if (running(data.rooms)) active.push(taskRow("Drawing specialist", data.rooms.current_task_label || "Prepare drawing tasks", "Working",
    data.rooms.request_seconds != null ? `Waiting for the AI for ${data.rooms.request_seconds} s.` : "Preparing the next drawing check.", true));
  active.push(...activeSkills.map((row) => skillRow(row, true, skills)));
  if (running(data.envelope) && !activeSkills.length) active.push(taskRow("Skill workflow", "Review room names and site", "Working",
    "Preparing evidence or checking returned answers.", true));
  if (running(data.supervisor)) {
    active.push(taskRow("Supervisor", "Check the empty-room model", "Working",
      data.supervisor.step_label || "Reviewing the model for missing evidence and contradictions.", true));
    active.push(...investigations.map((row) => taskRow("Specialist investigation", row.label || "Drawing check", "Assigned; awaiting answer",
      row.message || (row.pages?.length ? `Pages ${row.pages.join(", ")}` : "Checking drawing evidence."), true)));
  }
  if (running(data.calculation)) active.push(taskRow("Calculation engine", "Calculate empty-room loads", "Working",
    "Calculating from the saved, checked room inputs.", true));
  const completed = skills.filter((row) => finished.has(row.status)).length;
  const recent = data.supervisor?.recent_activity || [];
  return `<h3>AI activity</h3>
    <p class="job-caption">Actual workflow tasks and saved answers. Updates every five seconds.</p>
    ${data.refreshProblem ? '<p class="job-note" role="status">Refresh unavailable. Showing the last confirmed activity.</p>' : ""}
    <ul class="job-activity-list" aria-label="Current AI activity">${active.join("") || '<li class="job-caption">No active tasks reported. See the progress steps for completion or blockers.</li>'}</ul>
    ${skills.length ? `<details data-activity-details="skills" ${open.has("skills") ? "open" : ""}><summary>Skill tasks: ${completed} of ${skills.length} finished</summary>
      <ul class="job-activity-list">${skills.map((row) => skillRow(row, running(data.envelope), skills)).join("")}</ul></details>` : ""}
    ${recent.length ? `<details data-activity-details="recent" ${open.has("recent") ? "open" : ""}><summary>Recent specialist answers</summary>
      <ul class="job-activity-list">${recent.map((row) => taskRow("Specialist", row.label, textState(row.status), row.cycle ? `Investigation cycle ${row.cycle}.` : "")).join("")}</ul></details>` : ""}`;
}

// Preserve open disclosures and focus while polling replaces only the activity content.
export function bindActivity(container, projectId) {
  container?.addEventListener("toggle", (event) => {
    const key = event.target.dataset.activityDetails;
    if (!key) return;
    const open = expanded.get(projectId) || new Set();
    if (event.target.open) open.add(key); else open.delete(key);
    expanded.set(projectId, open);
  }, true);
}

export function updateActivity(container, data, projectId) {
  if (!container) return;
  const open = new Set([...container.querySelectorAll("details[open]")].map((node) => node.dataset.activityDetails));
  const focused = document.activeElement?.closest("details[data-activity-details]")?.dataset.activityDetails;
  container.innerHTML = activityMarkup(data, projectId);
  for (const node of container.querySelectorAll("details")) {
    node.open = open.has(node.dataset.activityDetails);
    if (focused === node.dataset.activityDetails) node.querySelector("summary")?.focus({ preventScroll: true });
  }
}

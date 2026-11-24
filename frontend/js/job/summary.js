// The answer card and the job's one to-do list, shown at the top of every job step.
// Reads /api/job-summary (backend/job_summary_service.py). Numbers come only from the saved result: a load that
// hasn't been calculated stays "Not calculated" with the reason.
import { labelBadge, getJson, sendJson, escapeHtml, fmt } from "./api.js";

const query = (projectId) => `?project_id=${encodeURIComponent(projectId)}`;
const SHOWN = 4;
const SEVERITY = {
  blocks: "Not in the total",
  changes_number: "Changes the total",
  check: "Check",
};
const STATE_BADGE = {
  current: ["Current", "ok"],
  stale: ["Out of date", "warn"],
  building_only: ["Building only", "warn"],
  working: ["Updating", "info"],
  none: ["Not calculated", "muted"],
};
let lastShown = { projectId: "", html: "" };
// What the last fix saved, shown once after the step is drawn again.
let flash = { projectId: "", text: "" };

function storageGet(key) {
  try {
    return sessionStorage.getItem(key);
  } catch {
    return null;
  }
}
function storageSet(key, value) {
  try {
    sessionStorage.setItem(key, value);
  } catch {
    /* private window: the list just starts closed */
  }
}

function hourText(hour) {
  return Number.isInteger(hour) ? `${String(hour).padStart(2, "0")}:00` : "";
}

function answerCard(summary, projectId) {
  const answer = summary.answer || {};
  const state = answer.state || "none";
  const [badge, tone] = STATE_BADGE[state] || STATE_BADGE.none;
  const hasTotal = Number.isFinite(Number(answer.total_kw)) && state !== "none";
  const when = [answer.month, hourText(answer.hour)].filter(Boolean).join(" ");
  const figure = hasTotal
    ? `<strong class="job-answer-total" data-answer-total>${fmt(answer.total_kw)} kW</strong>`
    : `<strong class="job-answer-total none" data-answer-total>Not calculated</strong>`;
  const facts = [
    hasTotal && when ? `Peak ${escapeHtml(when)}` : "",
    hasTotal && answer.suggested_system ? `Suggested system: ${escapeHtml(answer.suggested_system)}` : "",
    Number.isInteger(answer.rooms_found) && answer.rooms_found > 0
      ? `${answer.rooms_counted ?? 0} of ${answer.rooms_found} cooled rooms counted`
      : "",
  ].filter(Boolean);
  const completeness = summary.completeness || {};
  const counts = summary.todos?.length || 0;
  const recalc = (summary.todos || []).find((todo) => todo.kind === "update_load");
  const recalcAction = recalc?.actions?.find((action) => action.type === "recalculate");
  return `<div class="job-answer" data-answer-state="${escapeHtml(state)}">
    <div class="job-answer-figure">
      <span class="job-answer-label">${escapeHtml(answer.headline || "Cooling load")}</span>
      ${figure}
      ${hasTotal ? labelBadge(summary.result_label) : ""}
      ${facts.length ? `<span class="job-answer-facts">${facts.join(" · ")}</span>` : ""}
    </div>
    <div class="job-answer-status">
      <p><span class="job-answer-badge ${tone}" data-answer-badge>${escapeHtml(badge)}</span>${
        completeness.level === "partial"
          ? ` <span class="job-answer-badge warn" data-completeness>Partial</span>`
          : completeness.level === "complete"
            ? ` <span class="job-answer-badge ok" data-completeness>Complete</span>`
            : ""
      }</p>
      <p class="job-answer-note" data-answer-note>${escapeHtml(answer.note || completeness.text || "")}</p>
      ${answer.note && completeness.text ? `<p class="job-answer-note">${escapeHtml(completeness.text)}</p>` : ""}
    </div>
    <div class="job-answer-actions">
      ${recalcAction ? actionButton(recalcAction, recalc, "primary") : ""}
      ${
        counts
          ? `<button type="button" class="job-button secondary" data-todo-toggle aria-controls="job-todo-list" aria-expanded="false">${counts} thing${counts === 1 ? "" : "s"} to check</button>`
          : `<span class="job-answer-done" data-todo-none>Nothing left to check</span>`
      }
    </div>
  </div>`;
}

function actionButton(action, todo, style = "secondary") {
  return `<button type="button" class="job-button ${style === "primary" ? "" : "secondary"} job-todo-act" data-action="${escapeHtml(action.type)}" data-todo-id="${escapeHtml(todo.id)}"${action.value !== undefined ? ` data-value="${escapeHtml(String(action.value))}"` : ""}>${escapeHtml(action.label || "Fix")}</button>`;
}

function actionControls(todo, projectId, uses) {
  const room = todo.room || {};
  return (todo.actions || [])
    .map((action) => {
      if (action.type === "area")
        return `<span class="job-todo-field"><label><span class="job-sr-only">Area of ${escapeHtml(room.name || "the room")} in m²</span><input type="number" inputmode="decimal" min="0.5" step="0.1" data-input="area" placeholder="m²"${Number(room.area_m2) > 0 ? ` value="${Number(room.area_m2)}"` : ""}></label><span>m²</span>${actionButton(action, todo)}</span>`;
      if (action.type === "rename")
        return `<details class="job-todo-more"><summary>${escapeHtml(action.label || "Rename")}</summary><span class="job-todo-field"><label><span class="job-sr-only">New name for ${escapeHtml(room.name || "the room")}</span><input type="text" maxlength="80" data-input="name" value="${escapeHtml(room.name || "")}"></label>${actionButton({ ...action, label: "Save name" }, todo)}</span></details>`;
      if (action.type === "use")
        return `<details class="job-todo-more"><summary>${escapeHtml(action.label || "Change use")}</summary><span class="job-todo-field"><label><span class="job-sr-only">Use of ${escapeHtml(room.name || "the room")}</span><select data-input="use">${Object.entries(uses || {})
          .map(([id, label]) => `<option value="${escapeHtml(id)}"${id === room.use_id ? " selected" : ""}>${escapeHtml(label)}</option>`)
          .join("")}</select></label>${actionButton({ ...action, label: "Save use" }, todo)}</span></details>`;
      if (action.type === "link") {
        const step = action.step || todo.step || "drawings";
        return `<a class="job-todo-link" href="#/job/${encodeURIComponent(projectId)}/${encodeURIComponent(step)}">${escapeHtml(action.label || "Open")}</a>`;
      }
      if (action.type === "recalculate") return "";
      return actionButton(action, todo);
    })
    .join("");
}

function todoItem(todo, projectId, uses, index) {
  const impact = todo.impact && Number.isFinite(Number(todo.impact.kw)) ? `<span class="job-todo-impact" data-todo-impact>${escapeHtml(todo.impact.text || `${fmt(todo.impact.kw)} kW`)}</span>` : "";
  return `<li class="job-todo ${escapeHtml(todo.severity || "check")}" data-todo="${escapeHtml(todo.id)}" data-todo-kind="${escapeHtml(todo.kind || "")}"${index >= SHOWN ? " data-todo-extra hidden" : ""}>
    <span class="job-todo-severity">${escapeHtml(SEVERITY[todo.severity] || "Check")}</span>
    <div class="job-todo-body">
      <p class="job-todo-title"><strong>${escapeHtml(todo.title || "")}</strong>${impact}</p>
      ${todo.detail ? `<p class="job-todo-detail">${escapeHtml(todo.detail)}</p>` : ""}
      <div class="job-todo-actions">${actionControls(todo, projectId, uses)}</div>
      <p class="job-todo-message" data-todo-message role="status"></p>
    </div>
  </li>`;
}

function todoList(summary, projectId) {
  const todos = (summary.todos || []).filter((todo) => todo.kind !== "update_load" || !(todo.actions || []).some((a) => a.type === "recalculate"));
  if (!todos.length) return "";
  const more = todos.length - SHOWN;
  return `<div class="job-todos" id="job-todo-list" data-todo-list hidden>
    <h2>To do, most important first</h2>
    <ol>${todos.map((todo, index) => todoItem(todo, projectId, summary.uses, index)).join("")}</ol>
    ${more > 0 ? `<button type="button" class="job-button secondary" data-todo-more>Show ${more} more</button>` : ""}
  </div>`;
}

export function summaryHtml(summary, projectId) {
  return `${answerCard(summary, projectId)}${todoList(summary, projectId)}`;
}

// Counts on the step bar and on the "Check the house" tabs. The to-do list files items under the screen that fixes
// them (rooms, operating, …); "Check the house" holds the rooms, people-and-equipment and windows screens.
const STEP_OF = { rooms: "house", operating: "house", fix: "house" };
function stepBadges(summary) {
  const byStep = summary.counts?.by_step || {};
  const counts = {};
  Object.entries(byStep).forEach(([key, count]) => {
    counts[key] = (counts[key] || 0) + count;
    if (STEP_OF[key]) counts[STEP_OF[key]] = (counts[STEP_OF[key]] || 0) + count;
  });
  const badge = (key, count) =>
    ` <span class="job-step-todo" data-step-todo="${escapeHtml(key)}" aria-label="${count} to check">${count}</span>`;
  document.querySelectorAll(".job-step").forEach((step) => {
    step.querySelector(".job-step-todo")?.remove();
    const key = (step.getAttribute("href") || "").split("/").pop();
    if (counts[key] && !STEP_OF[key]) step.querySelector(".job-step-copy b")?.insertAdjacentHTML("beforeend", badge(key, counts[key]));
  });
  document.querySelectorAll(".job-subtab").forEach((tab) => {
    tab.querySelector(".job-step-todo")?.remove();
    const key = (tab.getAttribute("href") || "").split("/").pop();
    if (byStep[key]) tab.insertAdjacentHTML("beforeend", badge(`tab-${key}`, byStep[key]));
  });
}

async function runCalculation(projectId, phase, message) {
  let job = await sendJson("/api/job-calculation", { project_id: projectId, phase, reviewer: "To-do list" });
  const started = Date.now();
  while (["queued", "running"].includes(job.status)) {
    if (Date.now() - started > 600000) throw new Error("The update is still running. Come back to the job to see it finish.");
    message.textContent = "Updating the load… this takes about half a minute.";
    await new Promise((resolve) => setTimeout(resolve, 2000));
    job = await getJson(`/api/job-calculation${query(projectId)}`);
  }
  if (job.status !== "done") throw new Error(job.error || "The load didn't update.");
}

function refreshPage() {
  window.dispatchEvent(new HashChangeEvent("hashchange"));
}

async function act(button, summary, projectId, host) {
  const todo = (summary.todos || []).find((row) => row.id === button.dataset.todoId);
  if (!todo) return;
  const type = button.dataset.action;
  const action = (todo.actions || []).find((row) => row.type === type) || {};
  const item = button.closest("[data-todo]") || host;
  const message = item.querySelector("[data-todo-message]") || host.querySelector("[data-answer-note]");
  const roomId = todo.room?.room_id;
  let change = null;
  if (type === "area") {
    const value = Number(item.querySelector('[data-input="area"]')?.value);
    if (!Number.isFinite(value) || value <= 0) {
      message.textContent = "Type the area in m² first.";
      return;
    }
    change = { room_id: roomId, area_m2: value };
  } else if (type === "cooled") {
    change = { room_id: roomId, cooled: button.dataset.value === "true" };
  } else if (type === "use") {
    change = { room_id: roomId, use: item.querySelector('[data-input="use"]')?.value };
  } else if (type === "rename") {
    const name = item.querySelector('[data-input="name"]')?.value.trim();
    if (!name) {
      message.textContent = "Type a name first.";
      return;
    }
    change = { room_id: roomId, name };
  }
  const join = type === "join" ? { action: "join", room_ids: action.room_ids || [], name: action.name } : null;
  host.querySelectorAll(".job-todo-act").forEach((control) => (control.disabled = true));
  try {
    if (change) {
      const saved = await sendJson("/api/quick-fix", { project_id: projectId, rooms: [change], reviewer: "To-do list" });
      message.textContent = `Saved. ${(saved.applied || []).join(" ")} Update the load to see the new total.`;
      flash = { projectId, text: message.textContent };
    } else if (join) {
      const saved = await sendJson("/api/room-join", { project_id: projectId, ...join, reviewer: "To-do list" });
      message.textContent = `Joined. ${(saved.applied || []).join(" ")} Update the load to see the new total.`;
      flash = { projectId, text: message.textContent };
    } else if (type === "retry") {
      await sendJson(action.endpoint, { project_id: projectId, ...(action.body || {}) });
      message.textContent = "Started again. It keeps going if you leave this page.";
      flash = { projectId, text: message.textContent };
    } else if (type === "recalculate") {
      await runCalculation(projectId, action.phase || "full", message);
      message.textContent = "The load is up to date.";
      flash = { projectId, text: message.textContent };
    }
    refreshPage();
  } catch (error) {
    message.textContent = error.message;
    message.setAttribute("role", "alert");
    host.querySelectorAll(".job-todo-act").forEach((control) => (control.disabled = false));
  }
}

function bind(host, summary, projectId) {
  const list = host.querySelector("[data-todo-list]");
  const toggle = host.querySelector("[data-todo-toggle]");
  const key = `toki-todos-open:${projectId}`;
  const setOpen = (open) => {
    if (!list || !toggle) return;
    list.hidden = !open;
    toggle.setAttribute("aria-expanded", String(open));
    toggle.textContent = open ? "Hide the list" : `${list.querySelectorAll("[data-todo]").length} thing${list.querySelectorAll("[data-todo]").length === 1 ? "" : "s"} to check`;
  };
  setOpen(storageGet(key) === "1");
  toggle?.addEventListener("click", () => {
    const open = list.hidden;
    setOpen(open);
    storageSet(key, open ? "1" : "0");
  });
  host.querySelector("[data-todo-more]")?.addEventListener("click", (event) => {
    host.querySelectorAll("[data-todo-extra]").forEach((row) => (row.hidden = false));
    event.currentTarget.remove();
  });
  host.querySelectorAll(".job-todo-act").forEach((button) =>
    button.addEventListener("click", () => act(button, summary, projectId, host)),
  );
}

// Fills the page's [data-job-summary] slot. isCurrent() is false once another screen has been drawn.
export async function loadJobSummary(projectId, isCurrent = () => true) {
  const host = document.querySelector("[data-job-summary]");
  if (!host) return;
  if (lastShown.projectId === projectId && lastShown.html && !host.innerHTML) {
    host.innerHTML = lastShown.html;
    host.hidden = false;
  }
  try {
    const summary = await getJson(`/api/job-summary${query(projectId)}`);
    if (!isCurrent() || !host.isConnected) return;
    const html = summaryHtml(summary, projectId);
    const saved = flash.projectId === projectId && flash.text
      ? `<p class="job-summary-flash" data-summary-flash role="status">${escapeHtml(flash.text)}</p>` : "";
    flash = { projectId: "", text: "" };
    host.innerHTML = saved + html;
    host.hidden = false;
    lastShown = { projectId, html };
    bind(host, summary, projectId);
    stepBadges(summary);
  } catch (error) {
    if (!isCurrent() || !host.isConnected) return;
    host.innerHTML = `<p class="job-answer-note">The summary couldn't load: ${escapeHtml(error.message)}</p>`;
  }
}

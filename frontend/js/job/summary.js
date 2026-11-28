// The answer card and the job's one to-do list, shown at the top of every job step.
// Reads /api/job-summary (backend/job_summary_service.py). Numbers come only from the saved result: a load that
// hasn't been calculated stays "Not calculated" with the reason.
import { labelBadge, getJson, sendJson, escapeHtml, fmt } from "./api.js";

const query = (projectId) => `?project_id=${encodeURIComponent(projectId)}`;
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
  // One line: the figure, its badges and the facts. The sentences behind the badges are under "Why" (2026-11-28:
  // the card repeated "building only" and "partial" in three places); with no figure yet, the note is the answer.
  const headline = String(answer.headline || "Cooling load");
  const shortHeadline = headline.replace(/\s*\([^)]*\)\s*$/, "") || headline;
  const notes = [answer.note, answer.note && completeness.text ? completeness.text : ""].filter(Boolean);
  const noteText = answer.note || completeness.text || "";
  const why = hasTotal && noteText
    ? `<details class="job-answer-why"><summary>Why</summary><p class="job-answer-note" data-answer-note>${escapeHtml(noteText)}</p>${
        notes.length > 1 ? `<p class="job-answer-note">${escapeHtml(notes[1])}</p>` : ""}</details>`
    : `<p class="job-answer-note" data-answer-note>${escapeHtml(noteText)}</p>`;
  return `<div class="job-answer" data-answer-state="${escapeHtml(state)}">
    <div class="job-answer-figure">
      <span class="job-answer-label"${shortHeadline !== headline ? ` title="${escapeHtml(headline)}"` : ""}>${escapeHtml(shortHeadline)}</span>
      ${figure}
    </div>
    <div class="job-answer-status">
      <p class="job-answer-badges">${hasTotal ? labelBadge(summary.result_label) : ""}<span class="job-answer-badge ${tone}" data-answer-badge${noteText ? ` title="${escapeHtml(noteText)}"` : ""}>${escapeHtml(badge)}</span>${
        completeness.level === "partial"
          ? `<span class="job-answer-badge warn" data-completeness${completeness.text ? ` title="${escapeHtml(completeness.text)}"` : ""}>Partial</span>`
          : completeness.level === "complete"
            ? `<span class="job-answer-badge ok" data-completeness>Complete</span>`
            : ""
      }${facts.length ? `<span class="job-answer-facts">${facts.join(" · ")}</span>` : ""}</p>
      ${why}
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

// The to-do list is one form (user decision 2026-11-28): every question is stated plainly with two options, the
// recommended one already chosen, and any missing detail (an area) asked in the same place. One "Save all and update
// the load" saves every answer and recalculates once, so the result comes back clean. Items with nothing to answer
// here are one line each: what's still being worked out, and what to look at on another screen.

function roomLabel(todo) {
  const room = todo.room || {};
  return escapeHtml(room.name || todo.title || "the room");
}

function radio(name, value, label, checked, extra = "") {
  return `<label class="job-choice"><input type="radio" name="${escapeHtml(name)}" value="${escapeHtml(value)}"${checked ? " checked" : ""}${extra}><span>${label}</span></label>`;
}

// What the question is, its two options and which is recommended; null when the item has nothing to answer here.
function question(todo, uses) {
  const actions = todo.actions || [];
  const find = (type) => actions.find((action) => action.type === type);
  const area = find("area");
  const cooled = find("cooled");
  const join = find("join");
  const use = find("use");
  const rename = find("rename");
  // A room still being worked out has nothing to answer here: it is listed under "Not in the total yet", never offered
  // as "Not cooled" (a bedroom waiting for its walls isn't a question).
  if (todo.kind === "room_not_counted" && !area) return null;
  const name = `q-${todo.id}`;
  const recommendedTag = ` <em class="job-choice-tag">recommended</em>`;
  let options = "";
  if (area) {
    const room = todo.room || {};
    options = radio(name, "area", `Type its area <span class="job-todo-field"><input type="number" inputmode="decimal" min="0.5" step="0.1" data-input="area" aria-label="Area of ${roomLabel(todo)} in m²" placeholder="m²"${Number(room.area_m2) > 0 ? ` value="${Number(room.area_m2)}"` : ""}> m²</span>${recommendedTag}`, true)
      + (cooled ? radio(name, "cooled", escapeHtml(cooled.label || "Not a cooled room"), false) : "");
  } else if (join) {
    options = radio(name, "join", `${escapeHtml(join.label || "Join into one room")}${recommendedTag}`, true)
      + radio(name, "keep", "Keep them separate", false);
  } else if (cooled) {
    // Counted as cooled but probably outdoor: not cooling it is recommended. Not cooled, worth a check: leave it.
    const recommendChange = todo.severity !== "check";
    options = radio(name, "cooled", `${escapeHtml(cooled.label || "Change")}${recommendChange ? recommendedTag : ""}`, recommendChange)
      + radio(name, "keep", `Leave it as it is${recommendChange ? "" : recommendedTag}`, !recommendChange);
  } else if (!use && !rename) {
    return null;
  }
  const more = [
    use ? `<label class="job-todo-field"><span>Use</span><select data-input="use"><option value="">Keep its use</option>${Object.entries(uses || {})
      .map(([id, label]) => `<option value="${escapeHtml(id)}">${escapeHtml(label)}</option>`).join("")}</select></label>` : "",
    rename ? `<label class="job-todo-field"><span>Name</span><input type="text" maxlength="80" data-input="name" placeholder="${escapeHtml((todo.room || {}).name || "")}"></label>` : "",
  ].filter(Boolean).join("");
  return { options, more };
}

function questionItem(todo, uses) {
  const q = question(todo, uses);
  const impact = todo.impact && Number.isFinite(Number(todo.impact.kw)) ? `<span class="job-todo-impact" data-todo-impact>${escapeHtml(todo.impact.text || `${fmt(todo.impact.kw)} kW`)}</span>` : "";
  return `<li class="job-todo job-question ${escapeHtml(todo.severity || "check")}" data-todo="${escapeHtml(todo.id)}" data-todo-kind="${escapeHtml(todo.kind || "")}">
    <span class="job-todo-severity job-sr-only">${escapeHtml(SEVERITY[todo.severity] || "Check")}</span>
    <p class="job-todo-title"><strong>${escapeHtml(todo.title || "")}</strong>${impact}</p>
    ${todo.detail ? `<p class="job-todo-detail">${escapeHtml(todo.detail)}</p>` : ""}
    ${q.options ? `<div class="job-choices" role="radiogroup" aria-label="${escapeHtml(todo.title || "")}">${q.options}</div>` : ""}
    ${q.more ? `<details class="job-todo-more"><summary>Change its use or name</summary><div class="job-todo-fields">${q.more}</div></details>` : ""}
  </li>`;
}

function groupReason(detail) {
  const text = String(detail || "")
    .replace(/^Its\s+/i, "")
    .replace(/,?\s*so it has no load yet\.?$/i, "")
    .replace(/^It\s+was\s+/i, "")
    .replace(/\.$/, "");
  return text.charAt(0).toLowerCase() + text.slice(1);
}

function todoList(summary, projectId) {
  const todos = (summary.todos || []).filter((todo) => todo.kind !== "update_load" || !(todo.actions || []).some((a) => a.type === "recalculate"));
  if (!todos.length) return "";
  const asked = new Set();
  const questions = [];
  const waiting = [];
  const elsewhere = [];
  todos.forEach((todo) => {
    if (question(todo, summary.uses)) {
      questions.push(todo);
      if (todo.room?.room_id) asked.add(todo.room.room_id);
    }
  });
  todos.forEach((todo) => {
    if (questions.includes(todo)) return;
    // A room already asked about isn't listed again as "not in the total" (it was both, and contradicted itself).
    if (todo.kind === "room_not_counted") {
      if (!asked.has(todo.room?.room_id)) waiting.push(todo);
      return;
    }
    elsewhere.push(todo);
  });
  // Still being worked out: one line per reason, rooms as links.
  const reasons = new Map();
  waiting.forEach((todo) => {
    const reason = groupReason(todo.detail);
    if (!reasons.has(reason)) reasons.set(reason, []);
    reasons.get(reason).push(todo);
  });
  const waitingHtml = reasons.size
    ? `<section class="job-todo-section waiting"><h3>Not in the total yet</h3><ul class="job-todo-lines">${[...reasons]
        .map(([reason, rows]) => `<li>${rows.length} room${rows.length === 1 ? "" : "s"}: ${escapeHtml(reason)} — ${rows
          .map((todo) => `<a data-todo="${escapeHtml(todo.id)}" href="#/job/${encodeURIComponent(projectId)}/rooms/${encodeURIComponent(todo.room?.room_id || "")}">${escapeHtml(todo.room?.name || todo.title)}</a>`).join(", ")}</li>`)
        .join("")}</ul></section>`
    : "";
  const elsewhereHtml = elsewhere.length
    ? `<section class="job-todo-section elsewhere"><h3>Also worth a look</h3><ul class="job-todo-lines">${elsewhere
        .map((todo) => {
          const link = (todo.actions || []).find((action) => action.type === "link");
          const again = (todo.actions || []).find((action) => action.type === "retry");
          const step = link ? link.step || todo.step || "drawings" : "";
          return `<li data-todo="${escapeHtml(todo.id)}" data-todo-kind="${escapeHtml(todo.kind || "")}"><span>${escapeHtml(todo.title || "")}</span>${
            link ? ` <a href="#/job/${encodeURIComponent(projectId)}/${encodeURIComponent(step)}">${escapeHtml(link.label || "Open")}</a>` : ""}${
            again ? ` ${actionButton(again, todo)}` : ""}<p class="job-todo-message" data-todo-message role="status"></p></li>`;
        })
        .join("")}</ul></section>`
    : "";
  const form = questions.length
    ? `<form class="job-todo-form" data-todo-form novalidate>
        <ol class="job-todo-questions">${questions.map((todo) => questionItem(todo, summary.uses)).join("")}</ol>
        <div class="job-todo-submit">
          <button type="submit" class="job-button" data-todo-save>Save all and update the load</button>
          <p class="job-todo-message" data-todo-form-message role="status"></p>
        </div>
      </form>`
    : "";
  return `<div class="job-todos" id="job-todo-list" data-todo-list hidden>
    <h2>Things to check${questions.length ? ` <span class="job-todo-count">${questions.length} to answer</span>` : ""}</h2>
    ${form}${waitingHtml}${elsewhereHtml}
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

// Saves every answer in the form, then recalculates once (the server's "update the load" action says which phase).
async function saveAll(form, summary, projectId, host) {
  const message = form.querySelector("[data-todo-form-message]");
  const rooms = [];
  const joins = [];
  const skipped = [];
  (summary.todos || []).forEach((todo) => {
    const item = form.querySelector(`[data-todo="${CSS.escape(todo.id)}"]`);
    if (!item) return;
    const roomId = todo.room?.room_id;
    const choice = item.querySelector("input[type=radio]:checked")?.value;
    const actions = todo.actions || [];
    if (choice === "area") {
      const value = Number(item.querySelector('[data-input="area"]')?.value);
      if (Number.isFinite(value) && value > 0) rooms.push({ room_id: roomId, area_m2: value });
      else skipped.push(todo.room?.name || todo.title);
    } else if (choice === "cooled") {
      const action = actions.find((row) => row.type === "cooled");
      rooms.push({ room_id: roomId, cooled: action?.value === true || action?.value === "true" });
    } else if (choice === "join") {
      const action = actions.find((row) => row.type === "join");
      joins.push({ action: "join", room_ids: action?.room_ids || [], name: action?.name });
    }
    const use = item.querySelector('[data-input="use"]')?.value;
    if (use) rooms.push({ room_id: roomId, use });
    const name = item.querySelector('[data-input="name"]')?.value.trim();
    if (name) rooms.push({ room_id: roomId, name });
  });
  if (!rooms.length && !joins.length) {
    message.textContent = skipped.length ? `Type the area of ${skipped.join(", ")} first.` : "Nothing to save: every answer is left as it is.";
    return;
  }
  form.querySelectorAll("button, input, select").forEach((control) => (control.disabled = true));
  try {
    const applied = [];
    if (rooms.length) {
      const saved = await sendJson("/api/quick-fix", { project_id: projectId, rooms, reviewer: "To-do list" });
      applied.push(...(saved.applied || []));
    }
    for (const join of joins) {
      const saved = await sendJson("/api/room-join", { project_id: projectId, ...join, reviewer: "To-do list" });
      applied.push(...(saved.applied || []));
    }
    const fresh = await getJson(`/api/job-summary${query(projectId)}`).catch(() => ({}));
    const recalc = (fresh.todos || []).find((todo) => todo.kind === "update_load")?.actions?.find((action) => action.type === "recalculate");
    if (recalc) {
      await runCalculation(projectId, recalc.phase || "full", message);
      message.textContent = "Saved and updated.";
    }
    flash = { projectId, text: `Saved. ${applied.join(" ")}${recalc ? " The load is up to date." : ""}${skipped.length ? ` Still needs an area: ${skipped.join(", ")}.` : ""}`.trim() };
    refreshPage();
  } catch (error) {
    message.textContent = error.message;
    message.setAttribute("role", "alert");
    form.querySelectorAll("button, input, select").forEach((control) => (control.disabled = false));
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
  const form = host.querySelector("[data-todo-form]");
  form?.addEventListener("submit", (event) => {
    event.preventDefault();
    saveAll(form, summary, projectId, host);
  });
  // Typing an area picks "Type its area".
  form?.querySelectorAll('[data-input="area"]').forEach((input) =>
    input.addEventListener("input", () => {
      const choice = input.closest(".job-choice")?.querySelector("input[type=radio]");
      if (choice) choice.checked = true;
    }),
  );
  host.querySelectorAll(".job-todo-act").forEach((button) =>
    button.addEventListener("click", () => act(button, summary, projectId, host)),
  );
}

// Fills the page's [data-job-summary] slot. isCurrent() is false once another screen has been drawn.
// The room count every screen shows comes from here, so the summary card, Rooms and Results always agree
// (2026-11-24: one job read "16 of 21", "5 rooms" and "3 rooms" on three screens).
function fillCounts(summary, projectId) {
  const answer = summary.answer || {};
  const found = Number(answer.rooms_found);
  const counted = Number(answer.rooms_counted);
  const missing = answer.rooms_not_counted || [];
  document.querySelectorAll("[data-rooms-counted]").forEach((node) => {
    if (Number.isFinite(found) && found > 0)
      node.textContent = counted === found ? `All ${found} cooled rooms counted` : `${counted} of ${found} cooled rooms counted`;
  });
  document.querySelectorAll("[data-uncounted]").forEach((panel) => {
    if (!missing.length) {
      panel.hidden = true;
      panel.innerHTML = "";
      return;
    }
    // One line per reason, not one per room (2026-11-28: 16 rows read "walls and windows still being worked out").
    const groups = new Map();
    missing.forEach((room) => {
      const reason = String(room.reason || "not counted yet");
      if (!groups.has(reason)) groups.set(reason, []);
      groups.get(reason).push(room);
    });
    const link = (room) => `<a href="#/job/${encodeURIComponent(projectId)}/rooms/${encodeURIComponent(room.room_id)}">${escapeHtml(room.name)}</a>`;
    panel.innerHTML = `<h2>${missing.length} of ${found} cooled ${found === 1 ? "room isn't" : "rooms aren't"} counted yet</h2>
      <ul class="job-uncounted-groups">${[...groups].map(([reason, rooms]) =>
        rooms.length === 1
          ? `<li>${link(rooms[0])}: ${escapeHtml(reason)}</li>`
          : `<li><b>${escapeHtml(reason.charAt(0).toUpperCase() + reason.slice(1))} (${rooms.length}):</b> ${rooms.map(link).join(", ")}</li>`).join("")}</ul>`;
    panel.hidden = false;
  });
}

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
    fillCounts(summary, projectId);
  } catch (error) {
    if (!isCurrent() || !host.isConnected) return;
    host.innerHTML = `<p class="job-answer-note">The summary couldn't load: ${escapeHtml(error.message)}</p>`;
  }
}

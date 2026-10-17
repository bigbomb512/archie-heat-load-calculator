import { getJson, sendJson, escapeHtml } from "./api.js";
import { planSvg, lineLegend } from "./plan.js";

const query = (projectId) => `?project_id=${encodeURIComponent(projectId)}`;
const STOPPED = new Set(["failed", "error", "blocked", "interrupted"]);
const RUNNING = new Set([
  "working",
  "running",
  "in_progress",
  "queued",
  "pending",
  "reading",
]);

function stateOf(data) {
  const status = String(data?.status || data?.state || "").toLowerCase();
  if (
    ["done", "complete", "completed", "confirmed", "ready"].includes(status)
  ) {
    return "done";
  }
  if (STOPPED.has(status) || data?.enabled === false) return "waiting";
  if (RUNNING.has(status)) return "active";
  return "waiting";
}

// A refused or expired AI key stops every page; say so in plain words instead of the provider's raw error.
function pageReadingProblem(data) {
  const problem = String(data?.problem || data?.error || "");
  if (/\b401\b|api key|invalid_token|unauthori[sz]ed/i.test(problem)) {
    return "The AI key on this computer was refused (it may have expired). Replace the key, restart Archie, then try again.";
  }
  if (/\b402\b|credit|insufficient/i.test(problem)) {
    return "The AI account is out of credit. Add credit, then try again.";
  }
  return "Reading the pages stopped before it finished. Try again to continue.";
}

function stageLine(data, kind, status, plan) {
  if (kind === "pages") {
    if (STOPPED.has(String(data?.status || "").toLowerCase())) {
      return pageReadingProblem(data);
    }
    const read = Number(data?.pages_read ?? data?.read ?? 0);
    const total = Number(data?.page_count ?? data?.total ?? 0);
    return total
      ? `${read} of ${total} pages read.`
      : "Reading the drawing pages.";
  }
  if (kind === "equipment") {
    return data?.status === "done"
      ? `${data.findings?.length || 0} equipment items found.`
      : "Looking for equipment in the drawing set.";
  }
  if (kind === "rooms") {
    const traced = (plan.rooms || []).filter((room) => room.has_outline);
    const stillFinding = (plan.rooms || []).filter(
      (room) => room.state === "finding_area",
    );
    const names = traced.map((room) => room.name);
    if (names.length) {
      const found = `${names.join(", ")} traced.`;
      return stillFinding.length
        ? `${found} Working out the area of ${stillFinding.map((room) => room.name).join(", ")}.`
        : found;
    }
    return data?.status === "done"
      ? "The room outlines are ready."
      : "Reading the room labels and plan outlines.";
  }
  if (kind === "envelope") {
    if (data?.status === "failed") {
      return "Working out the walls, roof and floor stopped part-way. What it found so far is kept.";
    }
    return data?.status === "done" || data?.status === "complete"
      ? "Wall, roof, floor and window details are ready."
      : "Working through the room boundaries and materials.";
  }
  if (status.phase1_complete) return "Saved empty-room loads are available.";
  const reason = plan.phase1_report?.blocked_reasons?.[0];
  const leftOut = plan.phase1_report?.phase1_coverage?.rooms_left_out || [];
  if (reason) return String(reason);
  if (leftOut.length) {
    return `Still resolving ${leftOut.map((room) => room.label || room.name).join(", ")}.`;
  }
  return "Waiting for room areas and boundary details.";
}

function stage(title, data, kind, status, plan, retryEndpoint = "") {
  const state =
    kind === "calculation"
      ? status.phase1_complete
        ? "done"
        : RUNNING.has(String(data?.status || "").toLowerCase())
          ? "active"
          : "waiting"
      : stateOf(data);
  const failed = STOPPED.has(String(data?.status || "").toLowerCase());
  const copy = stageLine(data, kind, status, plan);
  const retry =
    failed && retryEndpoint
      ? `<button class="job-button secondary" data-retry="${retryEndpoint}">Try again</button><p class="job-note" role="status"></p>`
      : "";
  return `<li class="job-stage ${state}"><span class="job-stage-icon" aria-hidden="true">${state === "done" ? "✓" : ""}</span><span><strong>${title}</strong><p>${escapeHtml(copy)}</p>${retry}</span></li>`;
}

// The first step of a new job: the PDF is read (titles, drawing types, previews) and its drawing pages prepared.
function preparationStage(newJob) {
  if (!newJob) return "";
  if (newJob.drawings_ready) {
    const pages = Number(newJob.pages);
    return `<li class="job-stage done"><span class="job-stage-icon" aria-hidden="true">✓</span><span><strong>Prepare the drawing set</strong><p>${pages ? `${pages} pages ready.` : "The drawing pages are ready."}</p></span></li>`;
  }
  const running = newJob.state === "running";
  const line = running
    ? newJob.step_label || "Reading the PDF."
    : newJob.state === "failed"
      ? "Preparing the drawing set stopped before it finished. Try again to continue."
      : newJob.analysed
        ? "The drawing pages haven't been prepared yet."
        : "This PDF hasn't been read yet.";
  const button = running
    ? ""
    : `<button class="job-button${newJob.state === "failed" ? " secondary" : ""}" data-start-new-job>${newJob.state === "failed" ? "Try again" : "Read the PDF"}</button><p class="job-note" role="status"></p>`;
  return `<li class="job-stage ${running ? "active" : "waiting"}" data-preparation-stage><span class="job-stage-icon" aria-hidden="true"></span><span><strong>Prepare the drawing set</strong><p data-preparation-line>${escapeHtml(line)}</p>${button}</span></li>`;
}

function facts(status, plan) {
  const named = [...new Set((plan.rooms || []).map((room) => room.name))];
  const weather =
    status.weather_location ||
    status.weather_ai_pick ||
    status.weather_suggestion;
  const weatherWhy = status.weather_location
    ? "Selected for this job"
    : status.weather_ai_pick
      ? `Picked by the AI${status.weather_ai_why ? `: ${status.weather_ai_why}` : ""}`
      : weather
        ? "Suggested from the site details"
        : "Not identified yet";
  const site = status.address || status.found_site || "Not identified yet";
  const above =
    status.above && status.above !== "not_sure"
      ? status.above.replaceAll("_", " ")
      : "Not identified yet";
  return `<dl class="job-facts">
    <div><dt>Site</dt><dd>${escapeHtml(site)}</dd></div>
    <div><dt>Weather</dt><dd>${escapeHtml(weather || "Not identified yet")}</dd><small class="job-caption">${escapeHtml(weatherWhy)}</small></div>
    <div><dt>Rooms named on the drawings</dt><dd>${escapeHtml(named.length ? named.join(", ") : "Still reading the drawing set")}</dd></div>
    <div><dt>Above the tenancy</dt><dd>${escapeHtml(above)}</dd></div>
  </dl>`;
}

function statusSentence(data, status, plan) {
  if (status.phase1_complete)
    return "The empty-room loads are ready. Continue when you are.";
  const order = [
    ["pages", data.reading],
    ["equipment", data.equipment],
    ["rooms", data.rooms],
    ["envelope", data.envelope],
    ["calculation", data.calculation],
  ];
  const current = order.find(([kind, value]) => {
    if (kind === "calculation") return !status.phase1_complete;
    return value && stateOf(value) !== "done";
  });
  if (!current) return "The drawing review is ready to continue.";
  const [kind, value] = current;
  if (STOPPED.has(String(value?.status || "").toLowerCase())) {
    if (kind === "pages") return pageReadingProblem(value);
    return kind === "envelope"
      ? "Working out the walls, roof and floor stopped. Try again to continue."
      : "A drawing step stopped. Try again to continue.";
  }
  const copy = stageLine(value, kind, status, plan);
  if (kind === "pages")
    return `${copy} You can close this page and come back later.`;
  if (kind === "rooms") return `${copy} The review keeps going if you leave.`;
  if (kind === "envelope")
    return `${copy} The review keeps going if you leave.`;
  if (kind === "calculation")
    return `${copy} The review keeps going if you leave.`;
  return `${copy} The review keeps going if you leave.`;
}

const preparing = (newJob) => Boolean(newJob && !newJob.drawings_ready);
const REFRESH_MS = 4000;

// While the PDF is being read, keep the step's line current and redraw the screen when it finishes.
function watchPreparation(projectId, newJob) {
  if (newJob?.state !== "running") return;
  const hash = location.hash;
  const tick = async () => {
    if (location.hash !== hash || !document.querySelector("[data-preparation-stage]")) return;
    const next = await getJson(`/api/new-job${query(projectId)}`).catch(() => null);
    if (location.hash !== hash) return;
    if (next?.state === "running") {
      const line = document.querySelector("[data-preparation-line]");
      if (line && next.step_label) line.textContent = next.step_label;
      setTimeout(tick, REFRESH_MS);
    } else {
      window.dispatchEvent(new HashChangeEvent("hashchange"));
    }
  };
  setTimeout(tick, REFRESH_MS);
}

export async function renderDrawings(project, projectId) {
  const [plan, reading, equipment, rooms, envelope, calculation, newJob] =
    await Promise.all([
      getJson(`/api/job-plan${query(projectId)}`),
      getJson(`/api/page-reading${query(projectId)}`).catch(() => null),
      getJson(
        `/api/page-extraction${query(projectId)}&kind=equipment_appliances`,
      ).catch(() => null),
      getJson(`/api/drawing-tasks${query(projectId)}`).catch(() => null),
      getJson(`/api/skill-workflow${query(projectId)}`).catch(() => null),
      getJson(`/api/job-calculation${query(projectId)}`).catch(() => null),
      getJson(`/api/new-job${query(projectId)}`).catch(() => null),
    ]);
  const status = plan.status || {};
  const data = { reading, equipment, rooms, envelope, calculation };
  const content = `<div class="job-columns">
    <section class="job-panel job-progress-panel" aria-labelledby="doing-title">
      <h2 id="doing-title">What the AI is doing</h2>
      <ol class="job-stage-list">
        ${preparationStage(newJob)}
        ${stage("Read every page", reading, "pages", status, plan, "/api/page-reading")}
        ${stage("Find equipment", equipment, "equipment", status, plan)}
        ${stage("Trace the rooms and walls", rooms, "rooms", status, plan, "/api/drawing-tasks")}
        ${stage("Work out walls, roof, floor and windows", envelope, "envelope", status, plan, "/api/skill-workflow")}
        ${stage("Calculate each room’s empty-room load", calculation, "calculation", status, plan, "/api/job-calculation")}
      </ol>
      <p class="job-caption">The review keeps going in the background. You can close this page and come back later.</p>
    </section>
    <section class="job-panel job-found-panel" aria-labelledby="found-title">
      <h2 id="found-title">Found so far</h2>
      ${planSvg(plan)}
      ${lineLegend()}
      ${facts(status, plan)}
    </section>
  </div>`;
  return {
    content,
    status,
    statusLine: preparing(newJob)
      ? newJob.state === "failed"
        ? "Preparing the drawing set stopped. Try again to continue."
        : newJob.state === "running"
          ? "Toki is reading the PDF. You can close this page; it keeps going."
          : "Read the PDF to start this job."
      : statusSentence(data, status, plan),
    plan,
    bind: () => {
      watchPreparation(projectId, newJob);
      document.querySelector("[data-start-new-job]")?.addEventListener("click", async (event) => {
        const button = event.currentTarget;
        button.disabled = true;
        try {
          await sendJson("/api/new-job", { project_id: projectId });
          window.dispatchEvent(new HashChangeEvent("hashchange"));
        } catch (error) {
          button.disabled = false;
          const note = button.parentElement?.querySelector(".job-note");
          if (note) note.textContent = error.message;
        }
      });
      document.querySelectorAll("[data-retry]").forEach((button) => {
        button.addEventListener("click", async () => {
          button.disabled = true;
          try {
            const endpoint = button.dataset.retry;
            const body =
              endpoint === "/api/job-calculation"
                ? {
                    project_id: projectId,
                    phase: "phase1_base_room",
                    reviewer: "Job workspace",
                  }
                : {
                    project_id: projectId,
                    action: "retry",
                    ...(endpoint === "/api/page-extraction"
                      ? { kind: "equipment_appliances" }
                      : {}),
                  };
            await sendJson(endpoint, body);
            location.reload();
          } catch (error) {
            button.disabled = false;
            const note = button.parentElement?.querySelector(".job-note");
            if (note) note.textContent = error.message;
          }
        });
      });
    },
  };
}

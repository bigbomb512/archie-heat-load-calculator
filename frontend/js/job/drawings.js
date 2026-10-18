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
    ["done", "complete", "completed", "confirmed", "ready", "needs_review"].includes(status)
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

const pageOf = (target) => {
  const match = String(target || "").match(/page-(\d+)/);
  return match ? ` on page ${match[1]}` : "";
};

function duration(seconds) {
  const value = Math.max(0, Math.round(Number(seconds) || 0));
  if (value < 60) return `${value} s`;
  const minutes = Math.floor(value / 60);
  const rest = value % 60;
  return rest ? `${minutes} min ${rest} s` : `${minutes} min`;
}

// What the drawing-task job is doing right now, from the progress it saves before and after each AI call.
function drawingTaskProgress(data) {
  if (!data?.current_task_label) {
    return data?.step_label ? `${data.step_label}.` : "Getting the drawing tasks ready.";
  }
  const parts = [
    `${data.current_task_label}${pageOf(data.current_target)}: task ${data.task_number} of ${data.tasks_in_round} in round ${data.rounds || 1}.`,
  ];
  if (Number.isFinite(Number(data.request_seconds)) && data.request_seconds !== null) {
    const limit = Number(data.request_limit_s);
    parts.push(
      `Waiting for the AI for ${duration(data.request_seconds)}${limit && data.request_seconds >= 60 ? ` (it stops waiting at ${duration(limit)})` : ""}.`,
    );
  }
  const answered = Number(data.answered) || 0;
  if (answered) parts.push(`${answered} task${answered === 1 ? "" : "s"} answered so far.`);
  return parts.join(" ");
}

// Drawing tasks the AI couldn't answer because a call failed (not because of anything on the drawing).
const CALL_FAILURES = new Set(["connection", "timeout", "service_error"]);
const callFailures = (data) =>
  (data?.unresolved || []).filter((row) => CALL_FAILURES.has(row.kind));

function unansweredLine(data) {
  const failed = callFailures(data);
  const connection = failed.filter((row) => row.kind === "connection").length;
  const named = failed.map((row) => `${row.label}${pageOf(row.target)}`).join("; ");
  const why = data?.connection_stopped
    ? "the connection to the AI service kept failing, so the calls were stopped"
    : connection === failed.length
      ? "the connection to the AI service failed"
      : "the AI's calls failed or timed out";
  const notTried = Number(data?.not_tried) || 0;
  return `${failed.length} drawing task${failed.length === 1 ? "" : "s"} got no answer because ${why}: ${named}.${notTried ? ` ${notTried} more ${notTried === 1 ? "was" : "were"} not tried.` : ""} Nothing was concluded about the drawings. Try again to send them again.`;
}

// The failed envelope step(s), by name and reason, and what is redone on retry.
function envelopeFailureLine(data) {
  const steps = data?.failed_steps || [];
  if (!steps.length) {
    return "Working out the walls, roof and floor stopped part-way. What it found so far is kept.";
  }
  const named = steps.map((row) => `${row.label}: ${row.reason}.`).join(" ");
  const uses = [...new Set(steps.flatMap((row) => row.uses_it || []))];
  return `${named} The other steps finished and are kept. Try again redoes ${steps.map((row) => row.label.toLowerCase()).join(" and ")}${uses.length ? `, and ${uses.join(", ").toLowerCase()} if ${steps.length === 1 ? "its" : "their"} answer changes` : ""}.`;
}

function stageLine(data, kind, status, plan) {
  if (kind === "pages") {
    if (STOPPED.has(String(data?.status || "").toLowerCase())) {
      return pageReadingProblem(data);
    }
    const read = Number(data?.pages_read ?? data?.read ?? 0);
    const total = Number(data?.page_count ?? data?.total ?? 0);
    if (stateOf(data) === "done") {
      return total ? `${total} of ${total} pages read.` : "Every page has been read.";
    }
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
    if (stateOf(data) === "active") return drawingTaskProgress(data);
    if (stateOf(data) === "done" && callFailures(data).length) return unansweredLine(data);
    if (STOPPED.has(String(data?.status || "").toLowerCase())) {
      return "Tracing the rooms stopped before it finished. What it found so far is kept.";
    }
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
    if (data?.status === "needs_review") {
      return "Wall, roof, floor and window details are ready. Values the drawings don't give are labelled assumptions.";
    }
    if (data?.status === "running" || data?.status === "queued") {
      return "Working through the room boundaries and materials.";
    }
    if (data?.status === "failed") return envelopeFailureLine(data);
    return data?.status === "done" || data?.status === "complete"
      ? "Wall, roof, floor and window details are ready."
      : "Working through the room boundaries and materials.";
  }
  if (kind === "calculation") {
    if (status.phase1_complete) return "Saved empty-room loads are available.";
    const reportReason = plan.phase1_report?.blocked_reasons?.[0];
    const leftOut = plan.phase1_report?.phase1_coverage?.rooms_left_out || [];
    if (reportReason) return String(reportReason);
    if (leftOut.length) {
      return `Calculated with ${leftOut.map((room) => room.label || room.name).join(", ")} left out.`;
    }
    if (RUNNING.has(String(data?.status || "").toLowerCase())) {
      return data?.step_label || "Calculating each room’s empty-room load.";
    }
    if (data?.offerStart) {
      return "The empty-room load hasn't started. Calculate now runs it from the saved drawing evidence.";
    }
    if (data?.pending) return "Starting the empty-room load calculation.";
    if (STOPPED.has(String(data?.status || "").toLowerCase())) {
      return data?.error || "The empty-room load calculation stopped. Try again to continue.";
    }
    if (data?.status === "done") {
      return "The drawings changed since the last calculation. The empty-room load is recalculated when the drawing review finishes.";
    }
    return "Starts on its own when the drawing review finishes.";
  }
  if (status.phase1_complete) return "Saved empty-room loads are available.";
  return "The empty-room load calculation has not started yet.";
}

function stage(title, data, kind, status, plan, retryEndpoint = "") {
  const state =
    kind === "calculation"
      ? status.phase1_complete
        ? "done"
        : RUNNING.has(String(data?.status || "").toLowerCase()) || data?.pending
          ? "active"
          : "waiting"
      : kind === "rooms" && stateOf(data) === "done" && callFailures(data).length
        ? "waiting"
        : stateOf(data);
  const failed =
    STOPPED.has(String(data?.status || "").toLowerCase()) ||
    (kind === "rooms" && stateOf(data) === "done" && callFailures(data).length > 0);
  const copy = stageLine(data, kind, status, plan);
  const startNow =
    kind === "calculation" && data?.offerStart
      ? `<button class="job-button secondary" data-calculate-now>Calculate now</button><p class="job-note" role="status"></p>`
      : "";
  const retry =
    failed && retryEndpoint
      ? `<button class="job-button secondary" data-retry="${retryEndpoint}">Try again</button><p class="job-note" role="status"></p>`
      : "";
  const problem =
    kind === "rooms" && data?.last_problem && stateOf(data) === "active"
      ? `<p class="job-caption" data-stage-problem>${escapeHtml(data.last_problem)}</p>`
      : "";
  return `<li class="job-stage ${state}" data-stage="${kind}"><span class="job-stage-icon" aria-hidden="true">${state === "done" ? "✓" : ""}</span><span><strong>${title}</strong><p>${escapeHtml(copy)}</p>${problem}${retry}${startNow}</span></li>`;
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
  // A stage that stopped and needs Try again comes first, then the one working right now, then the first unfinished.
  const unfinished = ([kind, value]) => {
    if (kind === "calculation") return !status.phase1_complete;
    if (kind === "rooms" && stateOf(value) === "done" && callFailures(value).length) return true;
    return value && stateOf(value) !== "done";
  };
  const current =
    order.find(([, value]) => STOPPED.has(String(value?.status || "").toLowerCase())) ||
    order.find(([kind, value]) => kind !== "calculation" && stateOf(value) === "active") ||
    order.find(unfinished);
  if (!current) return "The drawing review is ready to continue.";
  const [kind, value] = current;
  if (kind === "rooms" && stateOf(value) === "done") {
    return `Some drawing tasks got no answer because ${value.connection_stopped || callFailures(value).every((row) => row.kind === "connection") ? "the connection to the AI service failed" : "the AI's calls failed"}. Try again under Trace the rooms and walls.`;
  }
  if (STOPPED.has(String(value?.status || "").toLowerCase())) {
    if (kind === "pages") return pageReadingProblem(value);
    return kind === "envelope"
      ? `${(value.failed_steps || []).map((row) => row.label).join(" and ") || "Working out the walls, roof and floor"} stopped. Try again to continue; finished steps are kept.`
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
const REFRESH_MS = 5000;
let watchGeneration = 0;
// The server starts Phase 1 when the envelope review finishes. The few seconds between the review finishing and the
// calculation job appearing are still "working", so the screen keeps checking instead of going quiet.
const PHASE1_START_GRACE_MS = 60000;
const reviewFinished = (data) =>
  ["done", "waiting"].includes(stateOf(data.envelope)) &&
  ["completed", "needs_review", "failed", "blocked", "provisional"].includes(String(data.envelope?.status || ""));

function phase1Pending(data, status) {
  return !status.phase1_complete && reviewFinished(data) && !RUNNING.has(String(data.calculation?.status || ""))
    && !STOPPED.has(String(data.calculation?.status || "")) && data.calculation?.status !== "done";
}

const pendingSince = new Map();

// Marks the calculation stage as waiting for the server's start, and after the grace period offers "Calculate now"
// (a job whose review finished before the server started Phase 1 by itself never gets an automatic start).
function withCalculationHints(projectId, data, status) {
  if (!phase1Pending(data, status)) {
    pendingSince.delete(projectId);
    return data;
  }
  if (!pendingSince.has(projectId)) pendingSince.set(projectId, Date.now());
  const offerStart = Date.now() - pendingSince.get(projectId) >= PHASE1_START_GRACE_MS;
  return { ...data, calculation: { ...(data.calculation || {}), pending: !offerStart, offerStart } };
}

// The light status endpoints behind the stage list (the plan itself is only re-read when a stage finishes).
async function fetchStages(projectId) {
  const [reading, equipment, rooms, envelope, calculation, newJob] = await Promise.all([
    getJson(`/api/page-reading${query(projectId)}`).catch(() => null),
    getJson(`/api/page-extraction${query(projectId)}&kind=equipment_appliances`).catch(() => null),
    getJson(`/api/drawing-tasks${query(projectId)}`).catch(() => null),
    getJson(`/api/skill-workflow${query(projectId)}`).catch(() => null),
    getJson(`/api/job-calculation${query(projectId)}`).catch(() => null),
    getJson(`/api/new-job${query(projectId)}`).catch(() => null),
  ]);
  return { reading, equipment, rooms, envelope, calculation, newJob };
}

function stagesMarkup(data, status, plan) {
  return `${preparationStage(data.newJob)}
        ${stage("Read every page", data.reading, "pages", status, plan, "/api/page-reading")}
        ${stage("Find equipment", data.equipment, "equipment", status, plan)}
        ${stage("Trace the rooms and walls", data.rooms, "rooms", status, plan, "/api/drawing-tasks")}
        ${stage("Work out walls, roof, floor and windows", data.envelope, "envelope", status, plan, "/api/skill-workflow")}
        ${stage("Calculate each room’s empty-room load", data.calculation, "calculation", status, plan, "/api/job-calculation")}`;
}

function sentenceFor(data, status, plan) {
  const newJob = data.newJob;
  if (preparing(newJob)) {
    return newJob.state === "failed"
      ? "Preparing the drawing set stopped. Try again to continue."
      : newJob.state === "running"
        ? "Toki is reading the PDF. You can close this page; it keeps going."
        : "Read the PDF to start this job.";
  }
  return statusSentence(data, status, plan);
}

// Which stages are finished and which one is working; when this changes, the plan and facts are re-read too.
function stageShape(data) {
  const kinds = ["reading", "equipment", "rooms", "envelope", "calculation"];
  const shape = kinds.map((kind) => `${kind}:${stateOf(data[kind])}`);
  shape.push(`prep:${data.newJob?.state || ""}`);
  return shape.join("|");
}

function anyWorking(data) {
  if (data.newJob?.state === "running") return true;
  if (data.calculation?.pending) return true;
  return ["reading", "equipment", "rooms", "envelope", "calculation"].some(
    (kind) => stateOf(data[kind]) === "active",
  );
}

function bindStageButtons(projectId, container) {
  container.querySelector("[data-calculate-now]")?.addEventListener("click", async (event) => {
    const button = event.currentTarget;
    button.disabled = true;
    try {
      await sendJson("/api/job-calculation", { project_id: projectId, phase: "phase1_base_room", reviewer: "Job workspace" });
      pendingSince.delete(projectId);
      window.dispatchEvent(new HashChangeEvent("hashchange"));
    } catch (error) {
      button.disabled = false;
      const note = button.parentElement?.querySelector(".job-note");
      if (note) note.textContent = error.message;
    }
  });
  container.querySelector("[data-start-new-job]")?.addEventListener("click", async (event) => {
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
  container.querySelectorAll("[data-retry]").forEach((button) => {
    button.addEventListener("click", async () => {
      button.disabled = true;
      try {
        const endpoint = button.dataset.retry;
        const body =
          endpoint === "/api/job-calculation"
            ? { project_id: projectId, phase: "phase1_base_room", reviewer: "Job workspace" }
            : {
                project_id: projectId,
                action: "retry",
                ...(endpoint === "/api/page-extraction" ? { kind: "equipment_appliances" } : {}),
              };
        await sendJson(endpoint, body);
        window.dispatchEvent(new HashChangeEvent("hashchange"));
      } catch (error) {
        button.disabled = false;
        const note = button.parentElement?.querySelector(".job-note");
        if (note) note.textContent = error.message;
      }
    });
  });
}

// While any stage is working, re-read the stage statuses every few seconds and redraw the list in place. Nothing is
// started or restarted here. It stops when the person leaves this screen, when a newer redraw starts its own watch,
// or when nothing is working; a stage finishing redraws the whole screen once (so the plan and facts update too).
function watchStages(projectId, data, status, plan) {
  const generation = ++watchGeneration;
  if (!anyWorking(data)) return;
  const hash = location.hash;
  const list = document.querySelector(".job-stage-list");
  let shape = stageShape(data);
  const stillHere = () =>
    generation === watchGeneration && location.hash === hash && list && document.contains(list);
  const tick = async () => {
    if (!stillHere()) return;
    const next = withCalculationHints(projectId, await fetchStages(projectId), status);
    if (!stillHere()) return;
    if (stageShape(next) !== shape) {
      window.dispatchEvent(new HashChangeEvent("hashchange"));
      return;
    }
    shape = stageShape(next);
    list.innerHTML = stagesMarkup(next, status, plan);
    bindStageButtons(projectId, list);
    const sentence = document.querySelector(".job-status");
    if (sentence) sentence.textContent = sentenceFor(next, status, plan);
    if (anyWorking(next)) setTimeout(tick, REFRESH_MS);
  };
  setTimeout(tick, REFRESH_MS);
}

export async function renderDrawings(project, projectId) {
  const [plan, stages] = await Promise.all([
    getJson(`/api/job-plan${query(projectId)}`),
    fetchStages(projectId),
  ]);
  const status = plan.status || {};
  const data = withCalculationHints(projectId, stages, status);
  const content = `<div class="job-columns">
    <section class="job-panel job-progress-panel" aria-labelledby="doing-title">
      <h2 id="doing-title">What the AI is doing</h2>
      <ol class="job-stage-list">
        ${stagesMarkup(data, status, plan)}
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
    statusLine: sentenceFor(data, status, plan),
    plan,
    bind: () => {
      bindStageButtons(projectId, document.querySelector(".job-progress-panel") || document);
      watchStages(projectId, data, status, plan);
    },
  };
}

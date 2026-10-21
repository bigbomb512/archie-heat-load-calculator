import { getJson, sendJson, escapeHtml } from "./api.js";
import { planSvg, lineLegend } from "./plan.js";

const query = (projectId) => `?project_id=${encodeURIComponent(projectId)}`;
// Where each step is comes from the server (job_runner.step_state): waiting, active, stopping, done or failed,
// whatever word the step's own file uses.
const stepOf = (data) => String(data?.step_state || "waiting");
const working = (data) => ["active", "stopping"].includes(stepOf(data));
const stopped = (data) => stepOf(data) === "failed";

function stateOf(data) {
  if (stepOf(data) === "done") return "done";
  if (data?.enabled === false) return "waiting";
  return working(data) ? "active" : "waiting";
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
    return "The room and site review stopped part-way. Its saved findings are kept.";
  }
  const named = steps.map((row) => `${row.label}: ${row.reason}.`).join(" ");
  const uses = [...new Set(steps.flatMap((row) => row.uses_it || []))];
  return `${named} The other steps finished and are kept. Try again redoes ${steps.map((row) => row.label.toLowerCase()).join(" and ")}${uses.length ? `, and ${uses.join(", ").toLowerCase()} if ${steps.length === 1 ? "its" : "their"} answer changes` : ""}.`;
}

function stageLine(data, kind, status, plan) {
  if (stepOf(data) === "stopping") return data.recovery?.reason || "Waiting for the current AI request to finish or time out, then resuming unfinished work.";
  if (kind !== "calculation" && data?.recovery?.label === "Check and resume" && data?.recovery?.reason && !working(data)) return data.recovery.reason;
  if (kind === "pages") {
    if (stopped(data)) {
      return pageReadingProblem(data);
    }
    const read = Number(data?.pages_read ?? data?.read ?? 0);
    const total = Number(data?.page_count ?? data?.total ?? 0);
    if (stateOf(data) === "done" && data?.partial) return `${read} of ${total} pages read. Some pages could not be read; finished pages are kept.`;
    if (stateOf(data) === "done") {
      return total ? `${total} of ${total} pages read.` : "Every page has been read.";
    }
    return total
      ? `${read} of ${total} pages read.`
      : "Reading the drawing pages.";
  }
  if (kind === "equipment") {
    return stepOf(data) === "done"
      ? `${data.findings?.length || 0} equipment items found.`
      : "Looking for equipment in the drawing set.";
  }
  if (kind === "rooms") {
    if (stateOf(data) === "active") return drawingTaskProgress(data);
    if (stateOf(data) === "done" && callFailures(data).length) return unansweredLine(data);
    if (stateOf(data) === "done" && data?.recovery?.label === "Try again") return data.recovery.reason;
    if (stopped(data)) {
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
    return stepOf(data) === "done"
      ? "The room outlines are ready."
      : "Reading the room labels and plan outlines.";
  }
  if (kind === "envelope") {
    if (data?.status === "needs_review") {
      return "Room names and site details have been reviewed.";
    }
    if (working(data)) {
      return "Reviewing room names and site details.";
    }
    if (stopped(data)) return envelopeFailureLine(data);
    if (data?.status === "stale") {
      return "Finished, but the drawings or inputs changed afterwards. Try again to work them out from the current drawings; the earlier result is kept until then.";
    }
    return stepOf(data) === "done"
      ? "Room names and site details have been reviewed."
      : "Reviewing room names and site details.";
  }
  if (kind === "supervisor") {
    const copy = working(data) ? data.step_label || "Checking the empty-room model."
      : stepOf(data) === "done" ? data.outcome === "needs_information"
        ? "The model checks finished, but no cooled room has enough supported inputs yet. See the remaining exceptions."
        : "The model checks are finished; remaining uncertainties are listed with the result."
      : stopped(data) ? "Checking the empty-room model stopped. Resume to keep the saved answers."
      : data?.recovery?.reason || "Starts after the room and site review.";
    const cycle = Number(data?.cycle) || 0;
    const gaps = `${Number(data?.gaps_found) || 0} gaps found · ${Number(data?.gaps_fixed) || 0} fixed`;
    const price = data?.spend;
    const spend = price ? ` · US$${Number(price.cost_usd || 0).toFixed(4)} spent${price.unpriced_calls ? " (some calls have no reported price)" : ""}` : "";
    return `${copy}${cycle ? ` Investigation cycle ${cycle} of ${data.max_cycles || 3}. ${gaps}${spend}.` : spend}`;
  }
  if (kind === "calculation") {
    if (status.phase1_complete) return "Saved empty-room loads are available.";
    const reportReason = plan.phase1_report?.blocked_reasons?.[0];
    const leftOut = plan.phase1_report?.phase1_coverage?.rooms_left_out || [];
    if (reportReason) return String(reportReason);
    if (leftOut.length) {
      return `Calculated with ${leftOut.map((room) => room.label || room.name).join(", ")} left out.`;
    }
    if (working(data)) {
      return data?.step_label || "Calculating each room’s empty-room load.";
    }
    if (data?.offerStart) {
      return "The empty-room load hasn't started. Calculate now runs it from the saved drawing evidence.";
    }
    if (data?.readiness?.ready && stepOf(data) !== "done" && !stopped(data)) return "Starting the empty-room load calculation.";
    if (data?.readiness?.reasons?.length) return data.readiness.reasons.join(" ");
    if (stopped(data)) {
      return data?.error || data?.problem || "The empty-room load calculation stopped. Try again to continue.";
    }
    if (stepOf(data) === "done") {
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
        : working(data) || data?.readiness?.ready && !data.offerStart && !stopped(data) && stepOf(data) !== "done"
          ? "active"
          : "waiting"
      : stateOf(data) === "done" && data?.recovery?.label === "Try again"
        ? "waiting"
        : stateOf(data);
  const copy = stageLine(data, kind, status, plan);
  const startNow = kind === "calculation" && data?.offerStart
    ? `<button class="job-button secondary" data-calculate-now>Calculate now</button><p class="job-note" role="status"></p>` : "";
  const retry = kind === "calculation"
    ? stopped(data) && data?.readiness?.ready && retryEndpoint
      ? `<button class="job-button secondary" data-retry="${retryEndpoint}">Try again</button><p class="job-note" role="status"></p>` : ""
    : recoveryButton(data, retryEndpoint, kind);
  const exceptions = kind === "supervisor" && data?.exceptions?.length
    ? `<ul class="job-note" data-supervisor-exceptions>${data.exceptions.map((row) => `<li>${escapeHtml(row.message)}</li>`).join("")}</ul>` : "";
  const problem =
    kind === "rooms" && data?.last_problem && stateOf(data) === "active"
      ? `<p class="job-caption" data-stage-problem>${escapeHtml(data.last_problem)}</p>`
      : "";
  return `<li class="job-stage ${state}" data-stage="${kind}"><span class="job-stage-icon" aria-hidden="true">${state === "done" ? "✓" : ""}</span><span><strong>${title}</strong><p>${escapeHtml(copy)}</p>${problem}${exceptions}${retry}${startNow}</span></li>`;
}

const recoveryNotes = new Map();
const pendingRequests = new Set();

function recoveryButton(data, endpoint, kind) {
  const control = data?.recovery;
  if (!endpoint || !control?.label) return "";
  const key = `${data.recovery_project_id}:${endpoint}`;
  const busy = pendingRequests.has(key);
  const disabled = busy || !control.action;
  return `<button class="job-button secondary" data-recover="${endpoint}" data-action="${control.action || ""}" data-kind="${kind}" data-run-id="${escapeHtml(data.job_id || data.run_id || data.recovery_run_id || "")}" ${disabled ? "disabled" : ""}>${escapeHtml(busy ? "Requesting recovery…" : control.label)}</button><p class="job-note" role="status">${escapeHtml(recoveryNotes.get(key) || "")}</p>`;
}

// Preparation may be owned by the initial analysis job or by its page-preparation child.
function preparationStage(newJob) {
  if (!newJob) return "";
  const ready = newJob.drawings_ready && stepOf(newJob) === "done";
  const state = ready ? "done" : working(newJob) ? "active" : "waiting";
  const line = ready ? `${Number(newJob.pages) || "Drawing"} pages ready.`
    : stepOf(newJob) === "stopping" ? newJob.recovery?.reason || "Waiting for preparation to stop, then resuming."
    : stepOf(newJob) === "active" ? newJob.step_label || "Reading the PDF."
    : newJob.error || newJob.recovery?.reason || "Prepare the drawing set to continue.";
  return `<li class="job-stage ${state}" data-preparation-stage><span class="job-stage-icon" aria-hidden="true">${ready ? "✓" : ""}</span><span><strong>Prepare the drawing set</strong><p data-preparation-line>${escapeHtml(line)}</p>${ready ? "" : recoveryButton(newJob, "/api/new-job", "preparation")}</span></li>`;
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
    ["supervisor", data.supervisor],
    ["calculation", data.calculation],
  ];
  // Report the active prerequisite before downstream waiting states.
  const unfinished = ([kind, value]) => {
    if (kind === "calculation") return !status.phase1_complete;
    if (kind === "rooms" && stateOf(value) === "done" && callFailures(value).length) return true;
    return value && stateOf(value) !== "done";
  };
  const current =
    order.find(([, value]) => stateOf(value) === "active" || value?.recovery?.label === "Try again") ||
    order.find(unfinished);
  if (!current) return "The drawing review is ready to continue.";
  const [kind, value] = current;
  if (kind === "rooms" && stateOf(value) === "done" && callFailures(value).length) {
    return `Some drawing tasks got no answer because ${value.connection_stopped || callFailures(value).every((row) => row.kind === "connection") ? "the connection to the AI service failed" : "the AI's calls failed"}. Try again under Trace the rooms and walls.`;
  }
  if (stopped(value) && value?.recovery?.label === "Try again") {
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

const preparing = (newJob) => Boolean(newJob && (!newJob.drawings_ready || working(newJob) || stopped(newJob)));
const REFRESH_MS = 5000;
let watchGeneration = 0;
// Calculation availability comes from the server, never from a browser timer.
function withCalculationHints(projectId, data, status) {
  return { ...data, calculation: { ...(data.calculation || {}),
    offerStart: !status.phase1_complete && data.calculation?.readiness?.ready === true && data.calculation?.offer_start === true } };
}

const confirmedStages = new Map();
async function fetchStages(projectId) {
  const entries = [
    ["reading", `/api/page-reading${query(projectId)}`],
    ["equipment", `/api/page-extraction${query(projectId)}&kind=equipment_appliances`],
    ["rooms", `/api/drawing-tasks${query(projectId)}`],
    ["envelope", `/api/skill-workflow${query(projectId)}`],
    ["supervisor", `/api/phase1-supervisor${query(projectId)}`],
    ["calculation", `/api/job-calculation${query(projectId)}`],
    ["newJob", `/api/new-job${query(projectId)}`],
  ];
  const responses = await Promise.allSettled(entries.map(([, url]) => getJson(url)));
  const data = { ...(confirmedStages.get(projectId) || {}) };
  data.refreshProblem = responses.some((reply) => reply.status === "rejected");
  responses.forEach((reply, index) => {
    if (reply.status === "fulfilled") {
      const name = entries[index][0];
      const previous = data[name];
      const next = { ...reply.value, recovery_project_id: projectId };
      const previousRun = previous?.job_id || previous?.run_id;
      const nextRun = next.job_id || next.run_id;
      if (stepOf(previous) === "stopping" && stepOf(next) === "active" && previousRun && previousRun === nextRun) return;
      const key = `${projectId}:${entries[index][1].split("?")[0]}`;
      if (stepOf(previous) === "stopping" && stepOf(next) === "active") recoveryNotes.set(key, "Resumed unfinished work.");
      if (next.recovery?.label === "") recoveryNotes.delete(key);
      data[name] = next;
    }
  });
  confirmedStages.set(projectId, data);
  return data;
}

function stagesMarkup(data, status, plan) {
  return `${preparationStage(data.newJob)}
        ${stage("Read every page", data.reading, "pages", status, plan, "/api/page-reading")}
        ${stage("Find equipment", data.equipment, "equipment", status, plan, "/api/page-extraction")}
        ${stage("Trace the rooms and walls", data.rooms, "rooms", status, plan, "/api/drawing-tasks")}
        ${stage("Review room names and site", data.envelope, "envelope", status, plan, "/api/skill-workflow")}
        ${stage("Check the empty-room model", data.supervisor, "supervisor", status, plan, "/api/phase1-supervisor")}
        ${stage("Calculate each room’s empty-room load", data.calculation, "calculation", status, plan, "/api/job-calculation")}`;
}

function sentenceFor(data, status, plan) {
  if (data.refreshProblem) return "Unable to refresh status. Showing the last confirmed progress; checking again shortly.";
  const newJob = data.newJob;
  if (preparing(newJob)) {
    return stepOf(newJob) === "stopping" ? "Restarting preparation; waiting for in-flight work to stop."
      : stopped(newJob)
      ? "Preparing the drawing set stopped. Try again to continue."
      : stepOf(newJob) === "active"
        ? "Toki is reading the PDF. You can close this page; it keeps going."
        : "Read the PDF to start this job.";
  }
  return statusSentence(data, status, plan);
}

// Which stages are finished and which one is working; when this changes, the plan and facts are re-read too.
function stageShape(data) {
  const kinds = ["reading", "equipment", "rooms", "envelope", "supervisor", "calculation"];
  const shape = kinds.map((kind) => `${kind}:${data[kind]?.status || ""}:${JSON.stringify(data[kind]?.recovery || {})}:${JSON.stringify(data[kind]?.readiness || {})}:${JSON.stringify([data[kind]?.cycle, data[kind]?.gaps_fixed, data[kind]?.exceptions, data[kind]?.spend])}:${data[kind]?.offerStart || false}`);
  shape.push(`prep:${stepOf(data.newJob)}`);
  return shape.join("|");
}

function anyWorking(data) {
  if (stepOf(data.newJob) === "active") return true;
  if (data.refreshProblem || data.calculation?.readiness?.ready && !["done", "failed"].includes(stepOf(data.calculation))) return true;
  return ["reading", "equipment", "rooms", "envelope", "supervisor", "calculation"].some(
    (kind) => stateOf(data[kind]) === "active",
  );
}

function bindStageButtons(projectId, container) {
  container.querySelector("[data-calculate-now]")?.addEventListener("click", async (event) => {
    const button = event.currentTarget;
    button.disabled = true;
    try {
      await sendJson("/api/job-calculation", { project_id: projectId, phase: "phase1_base_room", reviewer: "Job workspace" });
      window.dispatchEvent(new HashChangeEvent("hashchange"));
    } catch (error) {
      button.disabled = false;
      const note = button.parentElement?.querySelector(".job-note");
      if (note) note.textContent = error.message;
    }
  });
  container.querySelectorAll("[data-recover]").forEach((button) => {
    button.addEventListener("click", async () => {
      const endpoint = button.dataset.recover;
      const key = `${projectId}:${endpoint}`;
      if (pendingRequests.has(key)) return;
      pendingRequests.add(key);
      button.disabled = true;
      button.textContent = button.dataset.action === "restart" ? "Requesting restart…" : "Checking…";
      try {
        const reply = await sendJson(endpoint, { project_id: projectId, action: button.dataset.action,
          ...(button.dataset.action === "restart" && button.dataset.runId ? { expected_run_id: button.dataset.runId } : {}),
          ...(button.dataset.kind === "envelope" ? { scope: "phase1_envelope" } : {}),
          ...(button.dataset.kind === "equipment" ? { kinds: ["equipment_appliances"] } : {}) });
        const name = { preparation: "newJob", pages: "reading", equipment: "equipment", rooms: "rooms", envelope: "envelope", supervisor: "supervisor" }[button.dataset.kind];
        const saved = confirmedStages.get(projectId) || {};
        confirmedStages.set(projectId, { ...saved, [name]: { ...reply, recovery_project_id: projectId } });
        recoveryNotes.set(key, reply.recovery?.reason || (stepOf(reply) === "stopping"
          ? "Restart requested. Waiting for in-flight work to finish or time out."
          : reply.deduplicated ? "This step is already running." : "Resumed unfinished work; checking progress."));
      } catch (error) {
        recoveryNotes.set(key, error.message);
      } finally {
        pendingRequests.delete(key);
        window.dispatchEvent(new HashChangeEvent("hashchange"));
      }
    });
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
// or after completion; waiting stages continue polling so external recovery is visible. Stage changes refresh the plan too.
function watchStages(projectId, data, status, plan) {
  const generation = ++watchGeneration;
  if (status.phase1_complete && !anyWorking(data)) return;
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
    if (!status.phase1_complete || anyWorking(next)) setTimeout(tick, REFRESH_MS);
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

import { getJson, route, navigate, escapeHtml } from "./api.js";
import { renderFrame } from "./frame.js";
import { renderDrawings } from "./drawings.js";
import { renderRooms } from "./rooms.js";
import { renderOperating } from "./operating.js";
import { renderResults } from "./results.js";
import { renderJobs, renderNewJob } from "./jobs.js";
import { renderJobDetails } from "./editors.js";
import { renderFix } from "./fix.js";
import { renderSystem } from "./system.js";
import { loadJobSummary } from "./summary.js";

const root = document.querySelector("#jobApp");
let generation = 0;
let shownHash = null;
// The last status each job's screens returned: the frame drawn while the next screen loads uses it, so the header and
// steps stay put instead of a blank "Loading" page (results took 6 s and the design step 15 s, 2026-11-24).
const lastStatus = new Map();
const LOADING = `<section class="job-panel job-loading" aria-busy="true"><p role="status">Loading this step…</p>
  <div class="job-skeleton" aria-hidden="true"><i></i><i></i><i></i></div></section>`;
async function render() {
  const current = ++generation;
  root.hidden = false;
  // A redraw of the screen already showing (a step finished) keeps it until the new one is ready.
  if (location.hash !== shownHash) {
    // Another step of the same job keeps its header until the new step's frame is drawn (below).
    const jobOf = (hash) => (hash || "").split("/").slice(0, 3).join("/");
    const main = root.querySelector("main");
    if (location.hash.startsWith("#/job/") && jobOf(location.hash) === jobOf(shownHash) && main && root.querySelector(".job-header")) {
      main.setAttribute("data-loading", "screen");
      main.setAttribute("aria-busy", "true");
    } else {
      root.innerHTML = `<main class="job-main" data-loading="screen"><p role="status">Loading the job…</p></main>`;
    }
  }
  shownHash = location.hash;
  try {
    const routeState = route();
    if (routeState.step === "jobs") {
      const page = await renderJobs();
      if (current !== generation) return;
      root.innerHTML = page.content;
      page.bind?.();
      return;
    }
    if (routeState.step === "new-job") {
      const page = renderNewJob();
      root.innerHTML = page.content;
      page.bind?.();
      return;
    }
    const projects = await getJson("/api/projects");
    if (current !== generation) return;
    let projectId = routeState.projectId;
    if (!projectId && routeState.step !== "jobs") {
      const first = projects[0];
      if (first) {
        navigate(first.id, "drawings");
        return;
      }
    }
    if (!projectId) {
      root.innerHTML = `<main class="job-main"><h1>Jobs</h1><p>Choose a job to continue.</p>${projects.map((project) => `<p><a href="#/job/${encodeURIComponent(project.id)}/drawings">${escapeHtml(project.name)}</a></p>`).join("")}</main>`;
      return;
    }
    const project = projects.flatMap((item) => [item, ...(item.earlier_runs || [])]).find((item) => String(item.id) === projectId) || {
      id: projectId,
      name: "Job",
    };
    let step = routeState.step || "drawings";
    // "Check the house" opens on its Rooms tab.
    if (step === "house") step = "rooms";
    if (step === "system" && !project.results_ready) {
      navigate(projectId, project.phase2_complete ? "results" : "rooms");
      return;
    }
    if (step === "operating" && !project.phase1_complete) {
      navigate(projectId, "rooms");
      return;
    }
    if (step === "results" && !project.results_ready) {
      navigate(projectId, project.phase2_complete ? "operating" : "operating");
      return;
    }
    if (root.querySelector('main[data-loading="screen"]') || !root.querySelector(".job-header")) {
      // Drawn at once from what the jobs list knows; the screen replaces it when ready.
      const known = lastStatus.get(projectId) || { name: project.display_name || project.name, phase1_complete: project.phase1_complete,
        phase2_complete: project.phase2_complete, results_ready: project.results_ready, stage: project.stage };
      root.innerHTML = renderFrame({ project, projectId, step: step === "details" ? "drawings" : step, status: known,
        content: LOADING, statusLine: "Loading this step…" }).replace('id="main"', 'id="main" data-loading="screen"');
    }
    const screen =
      step === "rooms"
        ? await renderRooms(project, projectId, routeState.roomId)
        : step === "operating"
          ? await renderOperating(projectId)
          : step === "results"
            ? await renderResults(projectId)
            : step === "details"
              ? await renderJobDetails(projectId)
              : step === "fix"
                ? await renderFix(projectId)
                : step === "system"
                  ? await renderSystem(projectId)
                  : await renderDrawings(project, projectId);
    if (step === "details") step = "drawings";
    if (current !== generation) return;
    if (screen.status) lastStatus.set(projectId, screen.status);
    root.innerHTML = renderFrame({
      project,
      projectId,
      step,
      status: screen.status,
      content: screen.content,
      statusLine: screen.statusLine,
    });
    screen.bind?.();
    loadJobSummary(projectId, () => current === generation);
  } catch (error) {
    if (current !== generation) return;
    root.innerHTML = `<main class="job-main"><section class="job-error" role="alert"><h1>Could not load this job</h1><p>${escapeHtml(error.message)}</p><a href="#/jobs">Jobs</a></section></main>`;
  }
}
// The skip link moves focus to the step without touching the URL: the app routes by the hash, so "#main" would be
// read as a page (found by keyboard test, 2026-11-24).
document.addEventListener("click", (event) => {
  const skip = event.target.closest?.(".job-skip");
  if (!skip) return;
  event.preventDefault();
  const main = document.getElementById("main");
  main?.focus();
  main?.scrollIntoView({ block: "start" });
});
window.addEventListener("hashchange", render);
window.addEventListener("popstate", render);
render();

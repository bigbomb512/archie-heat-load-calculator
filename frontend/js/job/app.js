import { getJson, route, navigate, escapeHtml } from "./api.js";
import { renderFrame } from "./frame.js";
import { renderDrawings } from "./drawings.js";
import { renderRooms } from "./rooms.js";
import { renderOperating } from "./operating.js";
import { renderResults } from "./results.js";
import { renderJobs, renderNewJob } from "./jobs.js";
import { renderJobDetails } from "./editors.js";

const root = document.querySelector("#jobApp");
let generation = 0;
let shownHash = null;
async function render() {
  const current = ++generation;
  root.hidden = false;
  // A redraw of the screen already showing (a step finished) keeps it until the new one is ready.
  if (location.hash !== shownHash) {
    root.innerHTML = `<main class="job-main"><p role="status">Loading the job…</p></main>`;
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
    const project = projects.find((item) => String(item.id) === projectId) || {
      id: projectId,
      name: "Job",
    };
    let step = routeState.step || "drawings";
    if (step === "operating" && !project.phase1_complete) {
      navigate(projectId, "rooms");
      return;
    }
    if (step === "results" && !project.results_ready) {
      navigate(projectId, project.phase2_complete ? "operating" : "operating");
      return;
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
              : await renderDrawings(project, projectId);
    if (step === "details") step = "drawings";
    if (current !== generation) return;
    root.innerHTML = renderFrame({
      project,
      projectId,
      step,
      status: screen.status,
      content: screen.content,
      statusLine: screen.statusLine,
    });
    screen.bind?.();
  } catch (error) {
    if (current !== generation) return;
    root.innerHTML = `<main class="job-main"><section class="job-error" role="alert"><h1>Could not load this job</h1><p>${escapeHtml(error.message)}</p><a href="#/jobs">Jobs</a></section></main>`;
  }
}
window.addEventListener("hashchange", render);
window.addEventListener("popstate", render);
render();

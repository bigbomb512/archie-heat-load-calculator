import { escapeHtml, navigate } from "./api.js";

const STEPS = [
  ["drawings", "Drawings", "Read the drawing set"],
  ["rooms", "Rooms", "Phase 1, empty-room load"],
  ["operating", "Operating loads", "Phase 2"],
  ["results", "Results", "After Phase 2"],
];
export function renderFrame({
  project,
  projectId,
  step,
  status,
  content,
  statusLine,
  actionLabel,
  action,
}) {
  const params = new URLSearchParams(location.search);
  const done = {
    drawings: Boolean(status?.stage && status.stage !== "drawings"),
    rooms: Boolean(status?.phase1_complete),
    operating: Boolean(status?.phase2_complete),
    results: Boolean(status?.results_ready && !status?.result_stale),
  };
  const active =
    step === "phase1" ? "rooms" : step === "phase2" ? "operating" : step;
  const toolsMenu =
    params.has("engineer") || params.has("operator")
      ? `<details class="job-tools-menu"><summary aria-label="More tools">⋯</summary><div><a href="/?engineer=1#/job/${encodeURIComponent(projectId)}/drawings">Detailed tools</a><a href="/?operator=1#/job/${encodeURIComponent(projectId)}/drawings">Manual AI task review</a></div></details>`
      : "";
  const header = `<header class="job-header"><div class="job-head-inner">
    <a href="#/jobs" aria-label="Jobs"><img class="job-wordmark" src="/frontend/assets/images/toki-wordmark-exact.png" alt="Toki"></a>
    <a href="#/jobs">Jobs</a>
    <div class="job-name"><strong>${escapeHtml(status?.name || project?.display_name || project?.name || "Job")}</strong><span>${escapeHtml(project?.site || project?.address || status?.address || status?.found_site || "Site not added yet")}</span></div>
    <a class="job-details" href="#/job/${encodeURIComponent(projectId)}/details">Job details</a>${toolsMenu}
  </div><nav class="job-steps" aria-label="Job steps">${STEPS.map(
    ([key, label, sub], index) => {
      const available =
        key === "drawings" ||
        (key === "rooms" && (done.drawings || done.rooms)) ||
        (key === "operating" && done.rooms) ||
        (key === "results" && done.operating);
      const current = key === active;
      const locked = !available && !current;
      const href = `#/job/${encodeURIComponent(projectId)}/${key}`;
      const tag = locked ? "span" : "a";
      const link = locked
        ? `aria-disabled="true" title="${key === "results" ? "Available after Phase 2 is complete" : "Available after the previous step is complete"}"`
        : `href="${href}" ${current ? 'aria-current="step"' : ""}`;
      return `<${tag} class="job-step ${done[key] ? "done" : ""} ${current ? "current" : ""} ${locked ? "locked" : ""}" ${link}>
      <span class="job-step-num">${index + 1}</span><span class="job-step-copy"><b>${label}</b><small>${key === "results" && !done.results ? "After Phase 2" : sub}</small></span></${tag}>`;
    },
  ).join("")}</nav></header>`;
  const statusBlock = `<div class="job-status-row"><p class="job-status" role="status">${escapeHtml(statusLine || "Reviewing the drawing set. You can close this page and return later.")}</p>${actionLabel ? `<button class="job-button" id="jobPrimaryAction">${escapeHtml(actionLabel)}</button>` : ""}</div>`;
  return `${header}<main class="job-main">${statusBlock}${content}</main>`;
}
export function bindFrameAction(projectId, step, handler) {
  const button = document.querySelector("#jobPrimaryAction");
  if (button)
    button.addEventListener("click", () => handler?.(projectId, step));
}

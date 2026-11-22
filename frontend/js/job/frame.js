import { escapeHtml, navigate } from "./api.js";

// Four steps in the user's terms. "Check the house" holds what used to be three screens (rooms, people and
// equipment, windows and names); their old routes still work and open the matching tab.
const STEPS = [
  ["drawings", "Read the drawings", "Rooms, walls and windows found"],
  ["house", "Check the house", "Rooms, people and equipment"],
  ["results", "Load and report", "The cooling load and the report"],
  ["system", "Design the system", "Sizing from the load"],
];
const HOUSE_TABS = [
  ["rooms", "Rooms"],
  ["operating", "People and equipment"],
  ["fix", "Windows and names"],
];
const HOUSE_VIEWS = new Set(["house", "rooms", "operating", "fix", "phase1", "phase2"]);
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
    house: Boolean(status?.phase1_complete && status?.phase2_complete),
    results: Boolean(status?.results_ready && !status?.result_stale),
    system: false,
  };
  const active = HOUSE_VIEWS.has(step) ? "house" : step;
  const view = step === "phase1" || step === "house" ? "rooms" : step === "phase2" ? "operating" : step;
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
        (key === "house" && (done.drawings || status?.phase1_complete)) ||
        (key === "results" && status?.phase2_complete) ||
        (key === "system" && status?.results_ready);
      const current = key === active;
      const locked = !available && !current;
      const href = `#/job/${encodeURIComponent(projectId)}/${key}`;
      const tag = locked ? "span" : "a";
      const link = locked
        ? `aria-disabled="true" title="${({ results: "Available once people and equipment are checked", system: "Available once the cooling load is calculated" })[key] || "Available after the previous step is complete"}"`
        : `href="${href}" ${current ? 'aria-current="step"' : ""}`;
      return `<${tag} class="job-step ${done[key] ? "done" : ""} ${current ? "current" : ""} ${locked ? "locked" : ""}" ${link}>
      <span class="job-step-num">${index + 1}</span><span class="job-step-copy"><b>${label}</b><small>${key === "results" && !status?.phase2_complete ? "After checking the house" : sub}</small></span></${tag}>`;
    },
  ).join("")}</nav></header>`;
  const statusBlock = `<div class="job-status-row"><p class="job-status" role="status">${escapeHtml(statusLine || "Reviewing the drawing set. You can close this page and return later.")}</p>${actionLabel ? `<button class="job-button" id="jobPrimaryAction">${escapeHtml(actionLabel)}</button>` : ""}</div>`;
  const summarySlot = `<section class="job-summary" data-job-summary aria-label="The answer so far" hidden></section>`;
  const tabs = active === "house"
    ? `<nav class="job-subtabs" aria-label="Check the house">${HOUSE_TABS.map(([key, label]) => {
        const locked = key === "operating" && !status?.phase1_complete;
        return locked
          ? `<span class="job-subtab locked" aria-disabled="true" title="Available once the building load is calculated">${label}</span>`
          : `<a class="job-subtab" href="#/job/${encodeURIComponent(projectId)}/${key}"${key === view ? ' aria-current="page"' : ""}>${label}</a>`;
      }).join("")}</nav>`
    : "";
  return `${header}<main class="job-main">${summarySlot}${tabs}${statusBlock}${content}</main>`;
}
export function bindFrameAction(projectId, step, handler) {
  const button = document.querySelector("#jobPrimaryAction");
  if (button)
    button.addEventListener("click", () => handler?.(projectId, step));
}

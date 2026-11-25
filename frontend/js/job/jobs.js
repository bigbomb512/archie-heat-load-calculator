import { getJson, sendJson, escapeHtml, navigate } from "./api.js";
const needCount = (project) =>
  Object.values(project.needs_you || {}).reduce(
    (sum, value) => sum + (Number(value) || 0),
    0,
  );
const updatedDate = (value) => {
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? String(value || "")
    : date.toLocaleDateString("en-AU", {
        day: "numeric",
        month: "short",
        year: "numeric",
      });
};
const listHeader = () =>
  `<header class="job-header"><div class="job-head-inner"><a href="#/jobs" aria-label="Jobs"><img class="job-wordmark" src="/frontend/assets/images/archie-wordmark.svg" alt="Archie"></a><a href="#/jobs">Jobs</a></div></header>`;
const jobStage = (project) => project.results_ready || project.phase2_complete
  ? "results" : project.phase1_complete ? "operating" : project.drawings_confirmed ? "rooms" : "drawings";
const stageLabel = (stage) => ({ results: "Results ready", operating: "People and equipment", rooms: "Rooms", drawings: "Drawings" })[stage];
const loadLabel = (project) => {
  const value = Number(project.cooling_load_kw);
  if (project.cooling_load_kw == null || !Number.isFinite(value)) return "—";
  // A saved total of zero means no room was counted, not a room that needs no cooling.
  if (value <= 0) return "No rooms counted yet";
  const load = `${value.toFixed(1)} kW`;
  return project.cooling_load_kind === "building_only" ? `Building only · ${load}`
    : `${load}${project.result_stale ? " · out of date" : ""}`;
};
const jobRow = (project) => {
  const stage = jobStage(project);
  const address = project.address || project.found_site;
  // A name someone gave the job wins; then the site; then the drawing file it was made from.
  const fileName = String(project.name || "").replace(/\.pdf$/i, "");
  const named = project.display_name && project.display_name.replace(/\.pdf$/i, "") !== fileName ? project.display_name : "";
  const title = named || address || project.name;
  const second = named ? address : address ? project.name : "";
  const subtitle = second ? `<small>${escapeHtml(second)}</small>` : "";
  const archive = project.can_edit === false ? "" : `<button class="job-link-button" type="button" data-archive-job="${escapeHtml(project.id)}" data-archived="${project.archived ? "true" : "false"}">${project.archived ? "Unarchive" : "Archive"}</button>`;
  return `<tr data-job-id="${escapeHtml(project.id)}"><th><a href="#/job/${encodeURIComponent(project.id)}/${stage}">${escapeHtml(title)}</a>${subtitle}</th><td>${stageLabel(stage)}</td><td class="job-load-value">${escapeHtml(loadLabel(project))}</td><td>${needCount(project) || ""}</td><td>${escapeHtml(updatedDate(project.created_at || project.updated_at))}</td><td class="job-row-actions"><button class="job-link-button" type="button" data-export-job="${escapeHtml(project.id)}">Export</button>${archive}</td></tr>`;
};
const groupedRows = (projects) => projects.map((project) => {
  const earlier = project.earlier_runs || [];
  return jobRow(project) + (earlier.length ? `<tr class="job-earlier-row"><td colspan="6"><details><summary>${earlier.length} earlier ${earlier.length === 1 ? "run" : "runs"}</summary><table class="job-jobs-table job-earlier-table"><thead><tr><th>Job</th><th>Where it is</th><th>Cooling load</th><th><abbr title="Things only you can answer before the load can be finished">Needs you</abbr></th><th>Uploaded</th><th>Actions</th></tr></thead><tbody>${earlier.map(jobRow).join("")}</tbody></table></details></td></tr>` : "");
}).join("");
const allJobs = (groups) => groups.flatMap((group) => [group, ...(group.earlier_runs || [])]);
const jobsMarkup = (projects) => {
  const active = projects.filter((project) => !project.archived);
  const archived = projects.filter((project) => project.archived);
  const count = allJobs(archived).length;
  const table = (groups) => `<div class="job-panel job-table-wrap"><table class="job-jobs-table"><thead><tr><th>Job</th><th>Where it is</th><th>Cooling load</th><th><abbr title="Things only you can answer before the load can be finished">Needs you</abbr></th><th>Uploaded</th><th>Actions</th></tr></thead><tbody>${groupedRows(groups)}</tbody></table></div>`;
  return `${active.length ? table(active) : `<section class="job-panel"><h2>No active jobs</h2><p>Upload the architect’s PDF to start.</p><a class="job-button" href="#/new-job">New job</a></section>`}${count ? `<details class="job-archived"><summary>Show archived (${count})</summary>${table(archived)}</details>` : ""}`;
};
export async function renderJobs() {
  const projects = await getJson("/api/projects");
  const body = `${listHeader()}<main class="job-main job-list-inner"><div class="job-status-row"><h1>Jobs</h1><div class="job-actions"><label class="job-button secondary" for="job-import">Import a job</label><input id="job-import" type="file" accept=".zip,application/zip" hidden><a class="job-button" href="#/new-job">New job</a></div></div><p class="job-note" role="status" data-jobs-status></p><div class="job-filter"><label for="job-filter-input">Find a job</label><input id="job-filter-input" type="search" data-job-filter placeholder="Address, name or drawing file…" autocomplete="off" spellcheck="false"><span class="job-caption" role="status" data-job-filter-count></span></div><div data-jobs-list>${jobsMarkup(projects)}</div></main>`;
  return { content: body, bind: bindJobFiles };
}

// A job saved to one file (drawings, readings, results) to move it to another computer, and loaded back from one.
// Narrows the list as you type: a job (with its earlier runs) stays when its name, address or file name matches.
function bindJobFilter() {
  const input = document.querySelector("[data-job-filter]");
  const count = document.querySelector("[data-job-filter-count]");
  const apply = () => {
    const words = input.value.trim().toLowerCase().split(/\s+/).filter(Boolean);
    let shown = 0;
    document.querySelectorAll(".job-jobs-table:not(.job-earlier-table) > tbody > tr[data-job-id]").forEach((row) => {
      const earlier = row.nextElementSibling?.classList.contains("job-earlier-row") ? row.nextElementSibling : null;
      const text = `${row.textContent} ${earlier?.textContent || ""}`.toLowerCase();
      const match = words.every((word) => text.includes(word));
      row.hidden = !match;
      if (earlier) earlier.hidden = !match;
      shown += match ? 1 : 0;
    });
    count.textContent = words.length ? (shown ? `${shown} job${shown === 1 ? "" : "s"} shown` : "No job matches. Try part of the address or the file name.") : "";
  };
  input?.addEventListener("input", apply);
  return apply;
}

function bindJobFiles() {
  const refilter = bindJobFilter();
  const status = document.querySelector("[data-jobs-status]");
  document.querySelector("[data-jobs-list]")?.addEventListener("click", async (event) => {
    const archiveButton = event.target.closest("[data-archive-job]");
    if (archiveButton) {
      archiveButton.disabled = true;
      try {
        await sendJson("/api/project-archive", { project_id: archiveButton.dataset.archiveJob, archived: archiveButton.dataset.archived !== "true" });
        document.querySelector("[data-jobs-list]").innerHTML = jobsMarkup(await getJson("/api/projects"));
        refilter?.();
        status.textContent = "Job list updated.";
      } catch (error) {
        status.textContent = `The job couldn’t be updated: ${error.message}`;
        archiveButton.disabled = false;
      }
      return;
    }
    const button = event.target.closest("[data-export-job]");
    if (button) {
      button.disabled = true;
      status.textContent = "Saving the job to a file…";
      try {
        const saved = await sendJson("/api/project-export", { project_id: button.dataset.exportJob });
        status.innerHTML = `Job file ready: <a href="${escapeHtml(saved.artifact_url)}" download>download it</a>.`;
      } catch (error) {
        status.textContent = `The job couldn’t be exported: ${error.message}`;
      } finally {
        button.disabled = false;
      }
    }
  });
  document.querySelector("#job-import")?.addEventListener("change", async (event) => {
    const file = event.currentTarget.files?.[0];
    if (!file) return;
    status.textContent = `Loading ${file.name}…`;
    try {
      const bytes = new Uint8Array(await file.arrayBuffer());
      let binary = "";
      for (let index = 0; index < bytes.length; index += 0x8000) {
        binary += String.fromCharCode(...bytes.subarray(index, index + 0x8000));
      }
      const imported = await sendJson("/api/project-import", { archive_base64: btoa(binary) });
      navigate(imported.id, "drawings");
    } catch (error) {
      status.textContent = `The job file couldn’t be loaded: ${error.message}`;
    }
  });
}
// A drawing sheet with a folded corner and a plan drawn on it: the thing the person is about to hand over.
const SHEET_ICON = `<svg class="job-drop-icon" viewBox="0 0 96 112" aria-hidden="true">
  <path class="sheet" d="M8 4h58l22 22v82H8z"/>
  <path class="fold" d="M66 4v22h22"/>
  <path class="plan" d="M22 44h52v46H22zM22 66h24M46 44v22M58 66v24M58 78h16"/>
</svg>`;

function uploadMarkup() {
  return `${listHeader()}<main class="job-main job-list-inner job-new">
    <a class="job-link-button" href="#/jobs">Back to Jobs</a>
    <h1>New job</h1>
    <p class="job-new-lede">Give Archie the architect’s drawing set as one PDF. The AI reads every page.</p>
    <button type="button" class="job-drop" id="job-drop" aria-describedby="job-drop-help">
      ${SHEET_ICON}
      <span class="job-drop-title" data-drop-title>Drop the PDF here</span>
      <span class="job-drop-help" id="job-drop-help" data-drop-help>or click to choose it from your Downloads</span>
      <span class="job-drop-progress" aria-hidden="true"><i data-drop-bar></i></span>
    </button>
    <input id="job-pdf" type="file" accept="application/pdf,.pdf" tabindex="-1" aria-hidden="true">
    <p id="job-upload-progress" role="status" class="job-sr-only"></p>
    <p id="job-upload-error" role="alert" class="job-upload-error"></p>
  </main>`;
}

const isPdf = (file) =>
  Boolean(file) &&
  (String(file.type).includes("pdf") || file.name.toLowerCase().endsWith(".pdf"));

// Chrome and Edge can open the file chooser in Downloads; other browsers open it where it was last used.
async function choosePdf(input) {
  if (typeof window.showOpenFilePicker === "function") {
    try {
      const [handle] = await window.showOpenFilePicker({
        startIn: "downloads",
        multiple: false,
        types: [{ description: "PDF drawing set", accept: { "application/pdf": [".pdf"] } }],
      });
      return handle ? await handle.getFile() : null;
    } catch (error) {
      if (error?.name === "AbortError") return null;
    }
  }
  // The input's own change handler uploads the file chosen here.
  input.value = "";
  input.click();
  return null;
}

export function renderNewJob() {
  return {
    content: uploadMarkup(),
    bind: () => {
      const input = document.querySelector("#job-pdf");
      const zone = document.querySelector("#job-drop");
      const title = zone.querySelector("[data-drop-title]");
      const help = zone.querySelector("[data-drop-help]");
      const bar = zone.querySelector("[data-drop-bar]");
      const progress = document.querySelector("#job-upload-progress");
      const error = document.querySelector("#job-upload-error");
      let busy = false;

      const show = (state, heading, line, share = 0) => {
        zone.dataset.state = state;
        title.textContent = heading;
        help.textContent = line;
        bar.style.width = `${Math.round(share * 100)}%`;
        progress.textContent = state === "idle" ? "" : `${heading} ${line}`.trim();
      };
      const reset = () => {
        busy = false;
        zone.disabled = false;
        show("idle", "Drop the PDF here", "or click to choose it from your Downloads");
      };

      const upload = (file) => {
        if (busy) return;
        error.textContent = "";
        if (!isPdf(file)) {
          error.textContent = file
            ? `${file.name} isn’t a PDF. Choose the drawing set as one PDF.`
            : "";
          return;
        }
        busy = true;
        zone.disabled = true;
        const size = `${(file.size / 1048576).toFixed(1)} MB`;
        show("uploading", "Uploading the PDF", `${file.name}, ${size}, 0%`);
        const data = new FormData();
        data.append("pdf", file, file.name);
        const xhr = new XMLHttpRequest();
        xhr.open("POST", "/api/upload");
        xhr.upload.onprogress = (event) => {
          if (!event.lengthComputable) return;
          const share = event.loaded / event.total;
          show("uploading", "Uploading the PDF", `${file.name}, ${size}, ${Math.round(share * 100)}%`, share);
        };
        xhr.onerror = () => {
          reset();
          error.textContent = "The upload didn’t reach Archie. Check the connection and try again.";
        };
        xhr.onload = async () => {
          let result = {};
          try {
            result = JSON.parse(xhr.responseText);
          } catch {
            result = {};
          }
          if (xhr.status < 200 || xhr.status >= 300 || !result.id) {
            reset();
            error.textContent = result.error || "The PDF couldn’t be uploaded. Try again.";
            return;
          }
          show("starting", "Uploaded", "Starting to read the drawings…", 1);
          try {
            await sendJson("/api/new-job", { project_id: result.id });
            navigate(result.id, "drawings");
          } catch (err) {
            reset();
            error.textContent = `The PDF was uploaded, but reading it didn’t start: ${err.message} Open the job from Jobs to try again.`;
          }
        };
        xhr.send(data);
      };

      zone.addEventListener("click", async () => {
        if (busy) return;
        const file = await choosePdf(input);
        if (file) upload(file);
      });
      input.addEventListener("change", () => upload(input.files?.[0]));
      zone.addEventListener("dragenter", (event) => {
        event.preventDefault();
        if (!busy) show("dragging", "Release to upload", "One PDF with the whole drawing set");
      });
      zone.addEventListener("dragover", (event) => event.preventDefault());
      zone.addEventListener("dragleave", (event) => {
        if (!busy && !zone.contains(event.relatedTarget)) reset();
      });
      zone.addEventListener("drop", (event) => {
        event.preventDefault();
        if (busy) return;
        reset();
        upload(event.dataTransfer.files?.[0]);
      });
      // A PDF dropped anywhere on the page counts, so a near miss doesn't open the PDF in the browser instead.
      const page = document.querySelector(".job-new");
      page.addEventListener("dragover", (event) => event.preventDefault());
      page.addEventListener("drop", (event) => {
        event.preventDefault();
        if (!zone.contains(event.target)) upload(event.dataTransfer.files?.[0]);
      });
    },
  };
}

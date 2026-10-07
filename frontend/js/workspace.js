// Job workspace: the default project screen (docs/UI_REBUILD_PLAN.md, phase 1).
//
// A left rail of tabs (Project, Drawings, Rooms, Results) with a status per tab,
// one focused page per tab, and Calculate always visible. Each tab loads only
// its own data; the rail comes from one call (/api/job-status). The engineer
// screen stays available as "Engineer review" (or with ?engineer=1). ?operator=1 opens the
// Drawings tab with the AI step (one check at a time) for the Toki team.
// Addresses: #/job/<project id>/<tab>.
(() => {
  const params = new URLSearchParams(location.search);
  const enabled = params.get("engineer") !== "1";
  const root = document.getElementById("vJob");
  const body = document.getElementById("jobBody");
  const rail = document.getElementById("wsTabs");
  const subtabNav = document.getElementById("wsSubtabs");
  if (!root || !body || !rail) return;

  const TABS = [
    {id: "project", label: "Project", subtabs: [
      {id: "job-site", key: "job_site", label: "Job & site"},
      {id: "tenancy-context", key: "tenancy_context", label: "Tenancy context"},
    ]},
    {id: "drawings", label: "Drawings"},
    {id: "rooms", label: "Rooms", subtabs: [
      {id: "room-details", key: "room_details", label: "Room details"},
      {id: "measurements", key: "measurements", label: "Measurements"},
    ]},
    {id: "walls", label: "Walls & roof", subtabs: [
      {id: "walls", key: "walls", label: "Walls"},
      {id: "roof", key: "roof", label: "Roof"},
    ]},
    {id: "windows", label: "Windows"},
    {id: "results", label: "Results"},
  ];
  const STATE_TEXT = {done: "Complete", check: "Review", needed: "To do", working: "In progress", todo: "Not yet", error: "Error"};
  const STATE_ICON = {done: "✓", check: "●", needed: "●", working: "◌", todo: "○", error: "!"};
  const BUILDING_TYPES = [["", "Choose…"], ["food_tenancy", "Food tenancy (café, restaurant)"], ["retail", "Retail shop"],
                          ["office", "Office"], ["medical", "Medical / consulting"], ["other", "Other"]];
  const POLL_MS = 15000;
  const state = {projectId: null, analysis: null, status: null, tab: "project", subtab: "", active: false, engineer: false,
                 include: new Map(), poll: null, busy: new Set(), message: "", pageReviewOpen: false, envelopeCtx: null,
                 wallsRoomId: "", wallsTraceId: "", windowsRoomId: "", windowsTraceId: "",
                 projectDraft: null, envelopeDrafts: {}};

  const esc = value => String(value ?? "").replace(/[&<>"']/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
  const kw = value => (value == null || !isFinite(value)) ? "—" : Number(value).toFixed(1);
  const storage = {
    get(key) { try { return localStorage.getItem(key) || ""; } catch (_) { return ""; } },
    set(key, value) { try { localStorage.setItem(key, value); } catch (_) { /* private mode */ } },
  };
  const userName = () => storage.get("toki.workspace.name");
  const live = (projectId, tab = null) => state.active && state.projectId === projectId && (tab === null || state.tab === tab);

  async function getJson(url) {
    const response = await fetch(url);
    const data = await response.json();
    if (!response.ok || data.error) throw new Error(data.message || data.error || "Request failed.");
    return data;
  }
  async function sendJson(url, payload) {
    const response = await fetch(url, {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(payload)});
    const data = await response.json();
    if (!response.ok || data.error) throw new Error(data.message || data.error || "Request failed.");
    return data;
  }
  const modelUrl = () => `/api/ai-preliminary-model?project_id=${encodeURIComponent(state.projectId)}&view=workspace`;

  // ------------------------------------------------------------------ rail and header
  async function loadStatus() {
    const projectId = state.projectId;
    const status = await getJson(`/api/job-status?project_id=${encodeURIComponent(projectId)}`);
    if (state.projectId !== projectId) return null;
    state.status = status;
    renderRail();
    return status;
  }

  function renderRail() {
    const status = state.status || {tabs: {}};
    document.getElementById("jobTitle").textContent = status.name || state.analysis?.name || "Your job";
    document.getElementById("jobAddress").textContent = status.address || status.found_site || "Address not added yet";
    rail.innerHTML = TABS.map(tab => {
      const info = railStatus(tab, status);
      const current = tab.id === state.tab;
      const child = current && tab.subtabs?.find(row => row.id === state.subtab)?.id;
      const destination = `#/job/${encodeURIComponent(state.projectId)}/${tab.id}${child ? `/${child}` : ""}`;
      return `<li><a href="${destination}" class="ws-tab is-${esc(info.state)}${current ? " is-current" : ""}"
        data-ws-tab="${tab.id}" ${current ? 'aria-current="page"' : ""}>
        <span class="ws-tab-icon" aria-hidden="true">${STATE_ICON[info.state] || "○"}</span>
        <span class="ws-tab-text"><b>${esc(tab.label)}</b><small>${esc(info.detail || STATE_TEXT[info.state] || "")}</small></span>
        <span class="visually-hidden">${esc(STATE_TEXT[info.state] || "")}</span></a></li>`;
    }).join("");
    const select = document.getElementById("wsSectionSelect");
    select.innerHTML = TABS.map(tab => { const info = railStatus(tab, status); return `<option value="${tab.id}" ${tab.id === state.tab ? "selected" : ""}>${esc(tab.label)} — ${esc(STATE_TEXT[info.state] || "Not yet")}</option>`; }).join("");
    document.getElementById("wsTotal").textContent = kw(status.total_kw);
    document.getElementById("wsTotalNote").textContent = status.total_kw == null ? "Not calculated yet"
      : status.result_stale ? "Out of date — calculate again" : "Draft total cooling";
    renderSubtabs();
  }

  function railStatus(tab, status = state.status || {}) {
    const raw = status.tabs?.[tab.id] || {state: "todo"};
    const children = tab.subtabs?.map(child => status.subtabs?.[tab.id]?.[child.key]).filter(Boolean) || [];
    if (!children.length) {
      if (raw.state === "todo" || (raw.state === "check" && ["windows", "results"].includes(tab.id)))
        return {...raw, state: "needed"};
      return raw;
    }
    const needed = children.filter(row => row.state === "needed");
    const review = children.filter(row => row.state === "check");
    const working = children.filter(row => row.state === "working");
    if (needed.length) {
      const addCount = needed.reduce((sum, row) => sum + (Number(row.count) || 1), 0);
      const reviewCount = review.reduce((sum, row) => sum + (Number(row.count) || 1), 0);
      return {state: "needed", detail: `${addCount} item${addCount === 1 ? "" : "s"} to add${reviewCount ? ` · ${reviewCount} to review` : ""}`};
    }
    if (review.length) {
      const count = review.reduce((sum, row) => sum + (Number(row.count) || 1), 0);
      return {state: "check", detail: `${count} item${count === 1 ? "" : "s"} to review`};
    }
    if (working.length || raw.state === "working") return {state: "working", detail: raw.detail || "In progress"};
    if (children.every(row => row.state === "done")) return {state: "done", detail: "All sections complete"};
    return raw;
  }

  function renderSubtabs() {
    const main = TABS.find(row => row.id === state.tab);
    if (!subtabNav || !main?.subtabs) { if (subtabNav) { subtabNav.hidden = true; subtabNav.innerHTML = ""; } return; }
    const childStatuses = state.status?.subtabs?.[main.id] || {};
    subtabNav.hidden = false;
    subtabNav.innerHTML = main.subtabs.map(child => {
      const info = childStatuses[child.key] || {state: "todo"};
      const current = child.id === state.subtab;
      return `<a class="ws-subtab is-${esc(info.state)}" href="#/job/${encodeURIComponent(state.projectId)}/${main.id}/${child.id}"
        aria-label="${esc(child.label)}, ${esc(STATE_TEXT[info.state] || "Not yet")}" ${current ? 'aria-current="page"' : ""}><span class="ws-subtab-status" aria-hidden="true"></span>${esc(child.label)}
        <small class="ws-subtab-state">${esc(STATE_TEXT[info.state] || "Not yet")}</small></a>`;
    }).join("");
  }

  function go(tab) {
    location.hash = `#/job/${encodeURIComponent(state.projectId)}/${tab}`;
  }

  function defaultSubtab(tab, requested = "") {
    const main = TABS.find(row => row.id === tab);
    if (!main?.subtabs?.length) return "";
    return main.subtabs.some(row => row.id === requested) ? requested : main.subtabs[0].id;
  }

  function goSubtab(tab, child) {
    location.hash = `#/job/${encodeURIComponent(state.projectId)}/${tab}/${child}`;
  }

  function nextButton(tab) {
    const index = TABS.findIndex(row => row.id === tab);
    const next = TABS[index + 1];
    return next ? `<div class="ws-next"><button class="btn key" type="button" data-ws-go="${next.id}">Next: ${esc(next.label)} →</button></div>` : "";
  }

  function wireCommon() {
    body.querySelectorAll("[data-ws-go]").forEach(button => button.addEventListener("click", () => go(button.dataset.wsGo)));
  }

  function progress(title, detail) {
    body.innerHTML = `<div class="ws-card ws-progress" data-ws-progress><div class="ws-spinner" aria-hidden="true"></div>
      <div><h3>${esc(title)}</h3><p>${esc(detail)}</p></div></div>`;
  }

  function renderTab() {
    clearTimeout(state.poll);
    renderRail();
    body.innerHTML = "";
    const tab = state.tab;
    const render = {project: renderProject, drawings: renderDrawings, rooms: renderRooms, walls: renderWalls,
                    windows: renderWindows, results: renderResults}[tab] || renderProject;
    render().catch(error => { if (state.tab === tab) showTabError(error); });
    document.querySelector(".work")?.scrollTo?.(0, 0);
  }

  function showTabError(error) {
    body.innerHTML = `<div class="ws-card ws-error" role="alert"><h3>Something went wrong</h3><p>${esc(error.message)}</p>
      <button class="btn ghost" type="button" data-ws-retry>Try again</button></div>`;
    body.querySelector("[data-ws-retry]")?.addEventListener("click", renderTab);
  }

  // ------------------------------------------------------------------ Project
  async function renderProject() {
    const projectId = state.projectId;
    const status = state.status || await loadStatus();
    if (!live(projectId, "project")) return;
    state.projectDraft = state.projectDraft || {name: status.name || "", address: status.address || "",
      building_type: status.building_type || "", above: status.above || "", person: userName()};
    const draft = state.projectDraft;
    const found = status.found_site;
    const jobSite = state.subtab !== "tenancy-context";
    body.innerHTML = `<div class="ws-card" data-ws-project>
      <h2>${jobSite ? "Job &amp; site" : "Tenancy context"}</h2>
      <p class="ws-hint">Tell us what you know about the job. Anything you leave blank, Toki works out from the drawings where it can.</p>
      <form class="ws-form" data-ws-project-form>
        ${jobSite ? `<label>Job name<input name="name" autocomplete="off" value="${esc(draft.name)}"></label>
          <label>Site address<input name="address" autocomplete="street-address" placeholder="Street, suburb, state" value="${esc(draft.address)}"></label>
          ${found && !draft.address ? `<p class="ws-found">Found on the drawings: <b>${esc(found)}</b> <button class="link-button" type="button" data-ws-use-found>Use this</button></p>` : ""}
          <label>Building type<select name="building_type">${BUILDING_TYPES.map(([value, label]) => `<option value="${value}" ${draft.building_type === value ? "selected" : ""}>${esc(label)}</option>`).join("")}</select></label>
          <p class="ws-fine">Weather and sun: a generic Australian design day for now. Site-specific weather needs the AIRAH design data, which isn't connected yet.</p>`
          : `<fieldset><legend>What's above this tenancy?</legend>
            ${[["roof", "The roof"], ["floor", "Another floor or tenancy"], ["not_sure", "Not sure"]].map(([value, label]) =>
              `<label class="ws-radio"><input type="radio" name="above" value="${value}" ${draft.above === value ? "checked" : ""}> ${label}</label>`).join("")}
          </fieldset>`}
        <label>Your name <small>(optional; recorded with your changes)</small><input name="person" autocomplete="name" value="${esc(draft.person)}"></label>
        <div class="ws-actions"><button class="btn key" type="submit">Save</button><span class="ws-status" role="status" data-ws-project-status>${esc(state.message)}</span></div>
      </form>
      ${nextButton("project")}</div>`;
    state.message = "";
    const form = body.querySelector("[data-ws-project-form]");
    form.addEventListener("input", event => {
      const field = event.target;
      if (field.name && field.name in draft) draft[field.name] = field.type === "radio" ? (field.checked ? field.value : draft[field.name]) : field.value;
    });
    body.querySelector("[data-ws-use-found]")?.addEventListener("click", () => {
      form.elements.address.value = found; draft.address = found;
    });
    form.addEventListener("submit", async event => {
      event.preventDefault();
      const line = body.querySelector("[data-ws-project-status]");
      storage.set("toki.workspace.name", draft.person.trim());
      line.textContent = "Saving…";
      try {
        state.status = await sendJson("/api/job-setup", {project_id: projectId, name: draft.name,
          address: draft.address, building_type: draft.building_type, above: draft.above, edited_by: userName()});
        renderRail();
        line.textContent = "Saved.";
      } catch (error) { line.textContent = `Could not save: ${error.message}`; line.classList.add("is-error"); line.setAttribute("role", "alert"); }
    });
    wireCommon();
  }

  // ------------------------------------------------------------------ Drawings
  // Preparing the pages runs on the server as one job (save the page choice, prepare the drawings, rebuild
  // the checks). The page only starts and watches it, so closing or reloading the page doesn't interrupt it.
  const PREPARE_POLL_MS = 3000;
  const PREPARE_DETAIL = "Getting the drawing pages ready. This can take five to seven minutes on a large set. You can leave this page; it carries on, and you can come back.";
  function preparePages(projectId, title, pages) {
    if (state.preparing?.projectId !== projectId) {
      clearTimeout(state.poll);
      const run = {projectId, title, step: ""};
      const statusUrl = `/api/prepare-pages?project_id=${encodeURIComponent(projectId)}`;
      run.promise = (async () => {
        let job = pages ? await sendJson("/api/prepare-pages", {project_id: projectId, pages, requested_by: userName()})
                        : await getJson(statusUrl);
        while (job.status === "running" || job.status === "queued") {
          run.step = job.step_label || "";
          const line = body.querySelector("[data-ws-progress] [data-ws-progress-step]");
          if (line && state.preparing === run) line.textContent = run.step;
          await new Promise(resolve => setTimeout(resolve, PREPARE_POLL_MS));
          job = await getJson(statusUrl);
        }
        if (job.status !== "done") throw new Error(job.error || "The drawing pages could not be prepared. Engineer review shows which pages are included.");
        await refreshAnalysis(projectId);
      })().finally(() => { if (state.preparing === run) state.preparing = null; });
      state.preparing = run;
    }
    return state.preparing;
  }

  // After the server prepared the pages, take the new page data (and the saved page choice) into the app.
  async function refreshAnalysis(projectId) {
    const data = await getJson(`/api/analysis?id=${encodeURIComponent(projectId)}`);
    if (DATA?.id !== projectId) return;
    DATA = Object.assign({}, DATA, data);
    if (Array.isArray(data.selected_pages) && data.selected_pages.length) PICK = new Set(data.selected_pages);
    state.analysis = Object.assign({}, state.analysis || {}, data);
    requiredElement("btnContinue").textContent = data.has_reasoning_packet ? "Drawings confirmed" : "Confirm selected drawings";
  }

  async function renderDrawings() {
    const projectId = state.projectId;
    if (state.preparing?.projectId !== projectId) {
      // A run started earlier (another visit, or before a reload) is watched rather than started again.
      const job = await getJson(`/api/prepare-pages?project_id=${encodeURIComponent(projectId)}`).catch(() => ({}));
      if (!live(projectId, "drawings")) return;
      if (job.status === "running" || job.status === "queued") preparePages(projectId, "Preparing your drawing pages", null);
      else if (!DATA?.has_reasoning_packet) preparePages(projectId, "Preparing your drawing pages", pageDecisions(PICK));
    }
    if (state.preparing?.projectId === projectId) {
      const run = state.preparing;
      body.innerHTML = `<div class="ws-card ws-progress" data-ws-progress><div class="ws-spinner" aria-hidden="true"></div>
        <div><h3>${esc(run.title)}</h3><p data-ws-progress-step role="status">${esc(run.step)}</p><p>${esc(PREPARE_DETAIL)}</p></div></div>`;
      await run.promise;
      if (!live(projectId, "drawings")) return;
    }
    let status = await loadStatus();
    if (!live(projectId, "drawings")) return;
    if (!status.checks?.total) {
      progress("Starting the drawing check", "Setting up the measurements and checks for your drawings.");
      await sendJson("/api/autonomous-tasks", {project_id: projectId, action: "run_all"});
      status = await loadStatus();
      if (!live(projectId, "drawings")) return;
    }
    const tasks = operatorMode() ? (await getJson(`/api/autonomous-tasks?project_id=${encodeURIComponent(projectId)}`)).tasks || [] : null;
    if (!live(projectId, "drawings")) return;
    const checks = status.checks || {total: 0, waiting: 0, blocked: 0};
    const done = checks.total - checks.waiting - checks.blocked;
    const pendingReview = checks.waiting > 0 || checks.blocked > 0;
    const drawingMessage = checks.waiting
      ? "The Toki team is reviewing drawing details. You can continue with the rooms and measurements below."
      : checks.blocked
        ? "Some drawing checks need attention from the Toki team. Review the available room information while they are resolved."
        : "Drawing analysis is complete. Review any items marked To do or Review in Rooms, Walls & roof, and Windows.";
    body.innerHTML = `<div class="ws-card" data-ws-drawings>
      <h2>Drawing analysis</h2>
      <p class="ws-hint">Toki selects likely plan pages and reads the information needed for this job. Check the room, wall and window tabs for details that need your review.</p>
      <p data-ws-checks role="status">${esc(operatorMode() && pendingReview ? `${done} of ${checks.total} checks resolved. Answer the remaining Toki checks below.` : drawingMessage)}</p>
      ${pendingReview && !operatorMode() ? `<p class="ws-fine">You can continue with the information already available. The Toki team will resolve the remaining drawing checks.
        <button class="link-button" type="button" data-ws-operator-on>Open Toki team review</button></p>` : ""}
      ${tasks ? operatorMarkup(tasks) : ""}
      ${pagesMarkup()}
      ${nextButton("drawings")}</div>`;
    wireCommon();
    wirePages(projectId);
    body.querySelector("[data-ws-operator-on]")?.addEventListener("click", () => { setOperatorMode(true); renderDrawings().catch(showTabError); });
    if (tasks) wireOperator(projectId, tasks);
    // The contractor's view refreshes itself; the operator's doesn't, so a half-pasted reply is never wiped.
    if (pendingReview && !operatorMode()) state.poll = setTimeout(() => { if (live(projectId, "drawings")) renderDrawings().catch(() => {}); }, POLL_MS);
  }

  // ------------------------------------------------------------------ Drawings: pages used
  function pagesMarkup() {
    const sheets = DATA?.sheets || [];
    if (!sheets.length || typeof PICK === "undefined") return "";
    const main = sheets.filter(sheet => sheet.relevant && PICK.has(sheet.page));
    const shown = state.showAllPages ? sheets : sheets.filter(sheet => PICK.has(sheet.page) || state.pagePick?.has(sheet.page));
    const picked = state.pagePick || PICK;
    const changed = state.pagePick && (state.pagePick.size !== PICK.size || [...state.pagePick].some(page => !PICK.has(page)));
    const title = sheet => (sheet.title && !/^including amendments/i.test(sheet.title) ? sheet.title : String(sheet.type || "Drawing").replaceAll("_", " "));
    return `<section class="ws-page-review" aria-label="Drawing pages">
      <h3>Pages Toki selected</h3>
      <p class="ws-hint">Using ${PICK.size} of ${sheets.length} pages. Main plans (${main.map(sheet => sheet.page).join(", ") || "none"}) are measured; other selected pages provide reference information.</p>
      <details class="ws-page-controls" ${state.pageReviewOpen ? "open" : ""}>
        <summary>Review or change selected pages</summary>
        <p class="ws-fine">Only change this if a relevant plan is missing or an unrelated page is included.</p>
        <ul class="ws-pages" data-ws-pages>${shown.map(sheet => `<li class="${sheet.relevant ? "is-main" : ""}">
          <label><input type="checkbox" data-ws-page="${esc(sheet.page)}" ${picked.has(sheet.page) ? "checked" : ""}>
            ${sheet.thumbnail ? `<img src="${esc(sheet.thumbnail)}" alt="" loading="lazy">` : `<span class="ws-page-blank" aria-hidden="true"></span>`}
            <span><b>Page ${esc(sheet.page)}</b>${sheet.relevant ? " · main plan" : ""}<small>${esc(title(sheet))}</small></span></label></li>`).join("")}</ul>
        <div class="ws-actions">
          <button class="link-button" type="button" data-ws-all-pages>${state.showAllPages ? "Show only the pages used" : `Show all ${sheets.length} pages`}</button>
          ${changed ? `<button class="btn key" type="button" data-ws-use-pages>Use these pages</button><span class="ws-fine">The drawing analysis will be rebuilt for the new selection.</span>` : ""}
          <span class="ws-status" role="status" data-ws-pages-status></span>
        </div>
      </details>
    </section>`;
  }

  function wirePages(projectId) {
    body.querySelector(".ws-page-controls")?.addEventListener("toggle", event => { state.pageReviewOpen = event.target.open; });
    body.querySelector("[data-ws-all-pages]")?.addEventListener("click", () => { state.showAllPages = !state.showAllPages; renderDrawings().catch(showTabError); });
    body.querySelectorAll("[data-ws-page]").forEach(box => box.addEventListener("change", () => {
      state.pageReviewOpen = true;
      state.pagePick = state.pagePick || new Set(PICK);
      const page = Number(box.dataset.wsPage);
      box.checked ? state.pagePick.add(page) : state.pagePick.delete(page);
      renderDrawings().catch(showTabError);
    }));
    body.querySelector("[data-ws-use-pages]")?.addEventListener("click", async () => {
      if (!state.pagePick?.size) { body.querySelector("[data-ws-pages-status]").textContent = "Keep at least one page."; return; }
      PICK = new Set(state.pagePick);
      state.pagePick = null;
      preparePages(projectId, "Updating the drawing pages", pageDecisions(PICK));
      await renderDrawings();
    });
  }

  // ------------------------------------------------------------------ Drawings: the AI step (Toki team)
  // One check at a time: copy the prompt, attach the images, paste ChatGPT's reply, check and apply.
  const TASK_ORDER = ["P1_site", "S3_title_transcription", "P2_north", "P0_dimensions", "P0_wall_styles", "P0_room_names",
                      "P0_room_outlines", "S1_printed_areas", "P3_boundaries", "P4_openings", "P5_roof", "P6_kitchen"];
  const TASK_NAME = {
    P1_site: "Find the site address", S3_title_transcription: "Read the title block", P2_north: "Read the north arrow",
    P0_dimensions: "Read a printed dimension (sets the scale)", P0_wall_styles: "Pick out the wall line styles",
    P0_room_names: "Name the rooms", P0_room_outlines: "Outline the rooms that have no walls drawn",
    S1_printed_areas: "Read the printed room areas", P3_boundaries: "Say what each wall faces",
    P4_openings: "Measure the shopfront glazing", P5_roof: "What's above the rooms", P6_kitchen: "List the kitchen equipment",
  };
  const operatorMode = () => params.get("operator") === "1" || storage.get("toki.workspace.operator") === "1";
  function setOperatorMode(on) { storage.set("toki.workspace.operator", on ? "1" : ""); }
  const taskKey = row => `${row.task}:${row.target}`;
  function taskTitle(row) {
    const room = row.packet?.room?.room_label || row.room_label;
    const page = String(row.target).match(/^page-(\d+)(?:-dimension-(\d+))?/);
    const where = room ? room : page ? `page ${page[1]}${page[2] ? `, dimension ${page[2]}` : ""}` : row.target === "project" ? "" : String(row.target).replaceAll("-", " ");
    return `${TASK_NAME[row.task] || row.task}${where ? ` — ${where}` : ""}`;
  }
  const minutes = seconds => seconds >= 90 ? `${Math.round(seconds / 60)} min` : `${Math.round(seconds)} s`;
  const timeOf = row => (row.reply_attempts || []).reduce((sum, attempt) => sum + (Number(attempt.operator_seconds) || 0), 0);
  function sortTasks(tasks) {
    const rank = row => (TASK_ORDER.indexOf(row.task) + 1 || 99);
    return [...tasks].sort((a, b) => rank(a) - rank(b) || String(a.target).localeCompare(String(b.target), undefined, {numeric: true}));
  }
  function currentTask(tasks) {
    const waiting = sortTasks(tasks).filter(row => row.status === "waiting_for_reply");
    state.opSkip = state.opSkip || new Set();
    let next = waiting.find(row => !state.opSkip.has(taskKey(row)));
    if (!next && waiting.length) { state.opSkip.clear(); next = waiting[0]; }
    return next || null;
  }

  function operatorMarkup(tasks) {
    const row = currentTask(tasks);
    const waiting = tasks.filter(item => item.status === "waiting_for_reply");
    const blocked = sortTasks(tasks).filter(item => item.status === "blocked");
    const asked = tasks.filter(item => item.status === "needs_contractor_answer");
    const finished = sortTasks(tasks).filter(item => !["waiting_for_reply", "blocked", "needs_contractor_answer"].includes(item.status));
    const timed = tasks.filter(item => timeOf(item) > 0);
    const total = timed.reduce((sum, item) => sum + timeOf(item), 0);
    if (row && !state.opShownAt?.[taskKey(row)]) state.opShownAt = {...(state.opShownAt || {}), [taskKey(row)]: Date.now()};
    const images = row?.images || [];
    const error = row?.validation?.valid === false ? (row.validation.error || row.block_reason) : row?.block_reason;
    const card = row ? `<section class="ws-op-task" data-ws-op-task data-task="${esc(row.task)}" data-target="${esc(row.target)}" aria-labelledby="wsOpTitle">
        <p class="ws-op-count">Check ${sortTasks(waiting).findIndex(item => taskKey(item) === taskKey(row)) + 1} of ${waiting.length} to answer</p>
        <h3 id="wsOpTitle">${esc(taskTitle(row))}</h3>
        ${row.accuracy?.accuracy == null ? "" : `<p class="ws-fine">This check scored ${(row.accuracy.accuracy * 100).toFixed(0)}% on the answer keys (${esc(row.accuracy.scored)} cases).</p>`}
        <ol class="ws-steps">
          <li><b>Copy the prompt</b> and paste it into a new ChatGPT chat.
            <div class="ws-actions"><button class="btn ghost" type="button" data-ws-copy-prompt>Copy prompt</button>
            <details><summary>Show the prompt</summary><pre class="ws-prompt" data-ws-prompt>${esc(row.prompt)}</pre></details></div></li>
          ${images.length ? `<li><b>Attach ${images.length === 1 ? "the image" : `all ${images.length} images`}</b> to the same message (copy, download, or drag them in).
            <ul class="ws-op-images">${images.map((image, index) => `<li><img src="${esc(image.url)}" alt="Image ${index + 1} for this check" data-ws-op-image>
              <span><button class="link-button" type="button" data-ws-copy-image="${esc(image.url)}">Copy</button> ·
              <a href="${esc(image.url)}" download="${esc(`${row.task}-${row.target}-${image.name}`)}">Download</a></span></li>`).join("")}</ul></li>` : ""}
          <li><b>Paste ChatGPT's reply</b> here, then press Check and apply.
            <textarea class="ws-reply" data-ws-reply rows="7" spellcheck="false" aria-label="ChatGPT's reply">${esc(state.opDraft?.[taskKey(row)] || "")}</textarea></li>
        </ol>
        <div class="ws-op-meta">
          <label>Model used <input data-ws-model value="${esc(storage.get("toki.workspace.model"))}" maxlength="160" placeholder="e.g. GPT-5 Thinking"></label>
          <label class="ws-check"><input type="checkbox" data-ws-stand-in> This reply is a test stand-in, not a real model reply</label>
        </div>
        ${error ? `<p class="ws-banner is-warn" role="alert" data-ws-op-error>${esc(error)}</p>` : ""}
        <div class="ws-actions"><button class="btn key" type="button" data-ws-apply>Check and apply</button>
          ${waiting.length > 1 ? `<button class="btn ghost" type="button" data-ws-skip>Skip for now</button>` : ""}
          <span class="ws-status" role="status" data-ws-op-status>${esc(state.opMessage || "")}</span></div>
      </section>` : `<p class="ws-banner" data-ws-op-done>${esc(state.opMessage || "")} No checks are waiting for a reply.</p>`;
    state.opMessage = "";
    return `<div class="ws-op" data-ws-operator-panel>
      <div class="ws-op-head"><h3>AI step <small>(Toki team)</small></h3>
        <span class="ws-fine" data-ws-op-time>${timed.length ? `${minutes(total)} spent on ${timed.length} check${timed.length === 1 ? "" : "s"} · about ${minutes(total / timed.length)} each` : ""}</span>
        ${params.get("operator") === "1" ? "" : `<button class="link-button" type="button" data-ws-operator-off>Hide the AI step</button>`}</div>
      ${card}
      ${blocked.length ? `<h4>Waiting on earlier checks</h4><ul class="ws-list ws-bullets" data-ws-op-blocked>${blocked.map(item => `<li>${esc(taskTitle(item))} — ${esc(item.block_reason || "waiting")}</li>`).join("")}</ul>` : ""}
      ${asked.length ? `<p class="ws-fine" data-ws-op-asked>${asked.length} roof question${asked.length === 1 ? " is" : "s are"} answered by the contractor on the Project tab ("What's above the tenancy").</p>` : ""}
      ${finished.length ? `<details class="ws-op-finished"><summary>Done (${finished.length})</summary><ul class="ws-list" data-ws-op-finished>${finished.map(item => `<li><span>${esc(taskTitle(item))}</span>
        <span class="ws-fine">${esc(item.stand_in ? "Stand-in (test)" : item.quality_label || item.status.replaceAll("_", " "))}${timeOf(item) ? ` · ${minutes(timeOf(item))}` : ""}</span></li>`).join("")}</ul></details>` : ""}
    </div>`;
  }

  function wireOperator(projectId, tasks) {
    const card = body.querySelector("[data-ws-op-task]");
    body.querySelector("[data-ws-operator-off]")?.addEventListener("click", () => { setOperatorMode(false); renderDrawings().catch(showTabError); });
    if (!card) return;
    const key = `${card.dataset.task}:${card.dataset.target}`;
    const row = tasks.find(item => taskKey(item) === key);
    const line = card.querySelector("[data-ws-op-status]");
    const reply = card.querySelector("[data-ws-reply]");
    reply.addEventListener("input", () => { state.opDraft = {...(state.opDraft || {}), [key]: reply.value}; });
    card.querySelector("[data-ws-model]").addEventListener("change", event => storage.set("toki.workspace.model", event.target.value.trim()));
    card.querySelector("[data-ws-copy-prompt]").addEventListener("click", async () => {
      try { await navigator.clipboard.writeText(row.prompt || ""); line.textContent = "Prompt copied."; }
      catch (_) { card.querySelector("details").open = true; line.textContent = "The browser blocked copying: select the prompt text and copy it."; }
    });
    card.querySelectorAll("[data-ws-copy-image]").forEach(button => button.addEventListener("click", async () => {
      try {
        const blob = await (await fetch(button.dataset.wsCopyImage)).blob();
        await navigator.clipboard.write([new ClipboardItem({[blob.type || "image/png"]: blob})]);
        line.textContent = "Image copied: paste it into the ChatGPT message.";
      } catch (_) { line.textContent = "The browser blocked copying the image: use Download, or drag the image into ChatGPT."; }
    }));
    card.querySelector("[data-ws-skip]")?.addEventListener("click", () => {
      state.opSkip.add(key);
      renderDrawings().catch(showTabError);
    });
    card.querySelector("[data-ws-apply]").addEventListener("click", async event => {
      const text = reply.value.trim();
      if (!text) { line.textContent = "Paste ChatGPT's reply first."; reply.focus(); return; }
      event.target.disabled = true;
      line.textContent = "Checking the reply…";
      const seconds = Math.round((Date.now() - (state.opShownAt?.[key] || Date.now())) / 1000);
      try {
        const data = await sendJson("/api/autonomous-tasks", {project_id: projectId, action: "validate_apply", task: row.task, target: row.target,
          reply: text, model_note: card.querySelector("[data-ws-model]").value.trim(), stand_in: card.querySelector("[data-ws-stand-in]").checked,
          operator_seconds: seconds});
        const after = (data.tasks || []).find(item => taskKey(item) === key);
        if (after?.validation?.valid === false || after?.status === "waiting_for_reply") {
          state.opMessage = "The reply didn't pass the check; see the reason, fix or re-ask, and paste again.";
        } else {
          delete state.opDraft?.[key];
          delete state.opShownAt?.[key];
          state.opMessage = `Applied: ${taskTitle(row)}${after?.stand_in ? " (stand-in, test only)" : ""}.`;
        }
        await loadStatus();
        if (live(projectId, "drawings")) await renderDrawings();
      } catch (error) { line.textContent = `Could not apply the reply: ${error.message}`; event.target.disabled = false; }
    });
  }

  // ------------------------------------------------------------------ Rooms
  function areaSource(row) {
    if (row.area_m2 == null) return "No area yet";
    const origin = String(row.area_origin || "");
    if (origin === "edited") return "Edited by you";
    if (origin === "ai_determined") return "Measured by AI";
    if (origin.startsWith("printed") || origin === "pdf_evidence") return "Printed on the drawings";
    if (origin === "reviewer_traced") return "Measured on the plan";
    return "From the drawings";
  }

  async function loadRooms() {
    let model = await getJson(modelUrl());
    if (!(model.room_scope?.candidates || []).length) {
      // A job without AI room outlines still has room names; building the draft model lists them.
      model = await sendJson("/api/ai-preliminary-model", {project_id: state.projectId, action: "assemble", settings: aiPreliminarySettings(), response_view: "workspace"});
    }
    state.roomsModel = model;
    state.roomsMarker = state.status?.checks?.marker;
    return model;
  }

  // Rows = rooms found on the drawings, with typed areas applied at once (the draft model is rebuilt on Calculate).
  function roomRowsWithTyped(model) {
    const typed = new Map((state.status?.area_overrides || []).map(row => [row.room_id, row]));
    const traced = state.status?.traced_rooms || {};
    const rows = (model.room_scope?.candidates || []).map(row => typed.has(row.key)
      ? {...row, area_m2: typed.get(row.key).area_m2, area_origin: "edited"}
      : traced[row.key] ? {...row, area_m2: traced[row.key].area_m2, area_origin: traced[row.key].source === "traced" ? "reviewer_traced" : "ai_determined"}
      : row);
    const known = new Set(rows.map(row => row.key));
    for (const row of typed.values()) {  // added in the workspace; in the model after the next Calculate
      if (!known.has(row.room_id)) rows.push({key: row.room_id, label: row.room_label, level: row.level_name,
                                              area_m2: row.area_m2, area_origin: "edited", include: true, status: "added"});
    }
    return rows;
  }

  // ------------------------------------------------------------------ Rooms: measure a room on the plan
  // Guided: 1 click the room's corners (they snap to the wall lines), 2 set the scale from a printed
  // dimension (never from the page scale alone), 3 save. Uses the same trace API as Engineer review.
  const SCALE_TOLERANCE = 0.02;  // the server's rule: a dimension must agree with the stated page scale within 2%
  const dist = (a, b) => Math.hypot(a[0] - b[0], a[1] - b[1]);
  const shoelace = points => Math.abs(points.slice(0, -1).reduce((sum, a, i) => sum + a[0] * points[i + 1][1] - points[i + 1][0] * a[1], 0)) / 2;
  const SCALE_OK = new Set(["agreed", "declared_scale_rejected"]);

  async function renderMeasure() {
    const m = state.measure, projectId = state.projectId;
    if (!m.ctx) {
      progress(`Opening the plan for ${m.label}`, "Loading the plan pages and the wall lines to snap to.");
      m.ctx = await getJson(`/api/reviewer-room-geometry?project_id=${encodeURIComponent(projectId)}`);
      if (state.measure !== m || !live(projectId, "rooms")) return;
      const pages = measurePages(m.ctx);
      if (!pages.length) throw new Error("No plan page with a full-resolution image is ready for measuring. Engineer review shows the page status.");
      const room = (m.ctx.rooms || []).find(row => row.room_id === m.key);
      const existing = (m.ctx.reviewer_room_geometry?.records || []).find(row => row.room_id === m.key && pages.some(page => page.page === row.page));
      // The main plan carries the printed dimensions the scale needs; room names are often on another sheet.
      m.namePages = (room?.source_pages || []).filter(page => pages.some(row => row.page === page));
      m.page = existing?.page || pages.find(row => row.proposed_role === "main_floor_plan")?.page || m.namePages[0] || pages[0].page;
    }
    if (!(m.ctx.rooms || []).some(row => row.room_id === m.key)) {
      throw new Error(`${m.label} isn't in the plan's room list yet. Press Calculate once (it refreshes the room list), then measure it.`);
    }
    if (!m.snap || m.snap.page?.page !== m.page) {
      progress(`Opening page ${m.page}`, "Loading the wall lines to snap to.");
      m.snap = await getJson(`/api/plan-snap?project_id=${encodeURIComponent(projectId)}&page=${m.page}`);
      if (state.measure !== m || !live(projectId, "rooms")) return;
      Object.assign(m, {points: [], snapped: [], closed: false, dim: [], dimMm: "", dim2: [], dim2Mm: "", reuse: null, step: "corners",
                        view: null, error: "", hover: null});
      const reuse = pageCalibration(m);
      if (reuse) m.reuse = reuse;
    }
    const page = measurePages(m.ctx).find(row => row.page === m.page);
    const pageOptions = measurePages(m.ctx).map(row => `<option value="${row.page}" ${row.page === m.page ? "selected" : ""}>Page ${row.page}${row.title ? ` — ${esc(row.title)}` : ""}${row.proposed_role === "main_floor_plan" ? " (main plan)" : ""}</option>`).join("");
    const W = Number(page.image_width_px), H = Number(page.image_height_px);
    m.view = m.view || {x: 0, y: 0, w: W, h: H};
    const lines = (m.snap.lines || []).map(line => `<line x1="${line.start_px[0]}" y1="${line.start_px[1]}" x2="${line.end_px[0]}" y2="${line.end_px[1]}"/>`).join("");
    body.innerHTML = `<div class="ws-card ws-measure" data-ws-measure>
      <div class="ws-measure-head">
        <button class="link-button" type="button" data-ws-measure-back>← Rooms</button>
        <h2>Measure ${esc(m.label)}</h2>
        <label class="ws-measure-page"><span class="visually-hidden">Plan page</span><select data-ws-measure-page>${pageOptions}</select></label>
      </div>
      <div class="ws-measure-body">
        <aside class="ws-measure-steps" data-ws-measure-steps aria-live="polite"></aside>
        <div class="ws-plan" data-ws-plan>
          <svg data-ws-plan-svg role="application" tabindex="0" viewBox="${m.view.x} ${m.view.y} ${m.view.w} ${m.view.h}"
               aria-label="Plan page ${m.page}. Click to add a point; arrow keys move the cursor, Enter adds a point, Backspace removes the last one.">
            <image href="${esc(page.preview_url)}" width="${W}" height="${H}" preserveAspectRatio="none"/>
            <g class="ws-plan-lines">${lines}</g>
            <g data-ws-plan-overlay></g>
          </svg>
          <div class="ws-plan-tools" role="toolbar" aria-label="Plan view">
            <button class="btn ghost" type="button" data-ws-zoom="in" aria-label="Zoom in">+</button>
            <button class="btn ghost" type="button" data-ws-zoom="out" aria-label="Zoom out">−</button>
            <button class="btn ghost" type="button" data-ws-zoom="fit" aria-label="Show the whole page">⤢</button>
          </div>
          <p class="ws-plan-hint">Scroll to zoom, drag to move.</p>
        </div>
      </div></div>`;
    wireMeasure(page);
    fitPlan();
    drawMeasure();
    requestAnimationFrame(() => { if (state.measure === m) { fitPlan(); drawMeasure(); } });
  }

  // The steps and the whole plan on one screen: the plan takes the height left below the steps.
  function fitPlan() {
    const svg = body.querySelector("[data-ws-plan-svg]");
    if (!svg) return;
    svg.style.maxHeight = `${Math.max(320, Math.floor(window.innerHeight - svg.getBoundingClientRect().top - 12))}px`;
  }
  window.addEventListener("resize", () => { if (state.measure && body.querySelector("[data-ws-plan-svg]")) { fitPlan(); drawMeasure(); } });

  function measurePages(ctx) {
    return (ctx.pages || []).filter(row => Number(row.image_width_px) > 0 && Number(row.image_height_px) > 0 && row.preview_url
                                       && row.preview_matches_vector_coordinates !== false);
  }

  // A scale already set from a printed dimension on this page (when another room was measured) can be reused.
  function pageCalibration(m) {
    const rows = (m.ctx.reviewer_room_geometry?.records || []).filter(row => row.page === m.page && SCALE_OK.has(row.calibration?.status)
      && (row.calibration?.dimension_points_image_px || []).length === 2 && row.calibration?.dimension_value_mm > 0);
    const row = rows.find(item => item.room_id === m.key) || rows[0];
    if (!row) return null;
    const c = row.calibration;
    return {from: row.room_label || row.room_id, points: c.dimension_points_image_px, mm: c.dimension_value_mm,
            second: c.second_dimension ? {points: c.second_dimension.points_image_px, mm: c.second_dimension.value_mm} : null,
            mmPerPx: c.mm_per_px};
  }

  function declaredMmPerPx(page) {
    const denominator = Number(page.scale_denominator), pxPerPt = Number(page.image_px_per_pt);
    return denominator > 0 && pxPerPt > 0 ? denominator * (25.4 / 72) / pxPerPt : null;
  }

  // Mirrors ai/reviewer_room_geometry.calibration, so Save is offered only when the server will accept the scale.
  function measureScale(m, page) {
    if (m.useReuse && m.reuse) return {ok: true, mmPerPx: m.reuse.mmPerPx, text: `Scale from the printed ${Number(m.reuse.mm).toLocaleString()} mm dimension (set when ${m.reuse.from} was measured).`};
    const mm = Number(m.dimMm);
    if (m.dim.length < 2 || !(mm > 0)) return {ok: false};
    const measured = mm / dist(m.dim[0], m.dim[1]);
    const declared = declaredMmPerPx(page);
    const scaleText = page.declared_scale || (page.scale_denominator ? `1:${page.scale_denominator}` : "the page scale");
    if (!declared) return {ok: false, warn: "This page has no stated scale to check the dimension against. Use a page with a scale (for example 1:100)."};
    const diff = Math.abs(measured - declared) / declared;
    if (diff <= SCALE_TOLERANCE) return {ok: true, mmPerPx: measured, text: `The dimension agrees with the ${scaleText} scale (${(diff * 100).toFixed(1)}% difference).`};
    const mm2 = Number(m.dim2Mm);
    if (m.dim2.length === 2 && mm2 > 0) {
      const second = mm2 / dist(m.dim2[0], m.dim2[1]);
      const cross = Math.abs(measured - second) / ((measured + second) / 2);
      if (cross <= SCALE_TOLERANCE) return {ok: true, mmPerPx: (measured + second) / 2, text: `Two printed dimensions agree with each other (${(cross * 100).toFixed(1)}%), so they set the scale instead of ${scaleText}.`};
      return {ok: false, needSecond: true, warn: `The two dimensions disagree by ${(cross * 100).toFixed(1)}%. Check you clicked the ends of each dimension line and typed the printed numbers.`};
    }
    return {ok: false, needSecond: true, warn: `This dimension is ${(diff * 100).toFixed(1)}% off the ${scaleText} scale. Check you clicked the two ends of the dimension line and typed its number. If it's right, measure a second printed dimension to confirm.`};
  }

  function drawMeasure() {
    const m = state.measure, page = measurePages(m.ctx).find(row => row.page === m.page);
    const svg = body.querySelector("[data-ws-plan-svg]"), overlay = body.querySelector("[data-ws-plan-overlay]"), steps = body.querySelector("[data-ws-measure-steps]");
    if (!svg || !overlay || !steps) return;
    svg.setAttribute("viewBox", `${m.view.x} ${m.view.y} ${m.view.w} ${m.view.h}`);
    const unit = m.view.w / Math.max(1, svg.getBoundingClientRect().width || 800);  // image px per screen px
    const r = 6 * unit;
    const saved = (m.ctx.reviewer_room_geometry?.records || []).find(row => row.room_id === m.key && row.page === m.page);
    const poly = points => points.map(point => point.join(",")).join(" ");
    const dimLine = (points, cls) => points.length ? `<polyline class="${cls}" points="${poly(points)}" style="stroke-width:${3 * unit}px"/>${points.map(point => `<circle class="${cls}" cx="${point[0]}" cy="${point[1]}" r="${r}"/>`).join("")}` : "";
    const scale = measureScale(m, page);
    const areaM2 = m.closed && scale.ok ? shoelace(m.points) * scale.mmPerPx * scale.mmPerPx / 1e6 : null;
    overlay.innerHTML = `${saved && !m.points.length ? `<polygon class="ws-saved-outline" points="${poly(saved.points_image_px || [])}" style="stroke-width:${3 * unit}px"/>` : ""}
      ${m.points.length ? `<${m.closed ? "polygon" : "polyline"} class="ws-outline" points="${poly(m.closed ? m.points.slice(0, -1) : m.points)}" style="stroke-width:${3 * unit}px"/>` : ""}
      ${m.points.slice(0, m.closed ? -1 : undefined).map((point, index) => `<circle class="ws-corner${index === 0 ? " is-first" : ""}" cx="${point[0]}" cy="${point[1]}" r="${index === 0 && !m.closed && m.points.length > 2 ? r * 1.8 : r}"/>`).join("")}
      ${dimLine(m.dim, "ws-dim")}${dimLine(m.dim2, "ws-dim2")}
      ${m.hover ? `<circle class="ws-hover${m.hover.snapped ? " is-snapped" : ""}" cx="${m.hover.point[0]}" cy="${m.hover.point[1]}" r="${r * 1.4}"/>` : ""}`;
    const done = {corners: m.closed, scale: m.closed && scale.ok, save: false};
    const current = !m.closed ? "corners" : !scale.ok ? "scale" : "save";
    const pill = (id, text) => `<li class="${current === id ? "is-current" : ""}${done[id] ? " is-done" : ""}">${text}</li>`;
    const scaleBody = m.reuse && m.useReuse !== false && !m.dim.length
      ? `<p>The scale on this page was already set from a printed dimension (${Number(m.reuse.mm).toLocaleString()} mm, when ${esc(m.reuse.from)} was measured).
           <button class="btn ghost" type="button" data-ws-reuse>Use it</button> <button class="link-button" type="button" data-ws-rescale>Set it again</button></p>`
      : `<p>Click both ends of a printed dimension line (the longer the better), then type the number printed on it.</p>
         <div class="ws-measure-row"><label>Printed dimension (mm) <input type="number" inputmode="numeric" min="1" step="1" data-ws-dim-mm value="${esc(m.dimMm)}" placeholder="e.g. 11825"></label>
           <span class="ws-fine">${m.dim.length}/2 ends clicked${m.dim.length ? ` · <button class="link-button" type="button" data-ws-dim-clear="dim">Clear</button>` : ""}</span></div>
         ${scale.needSecond || m.dim2.length ? `<div class="ws-measure-row ws-second"><b>Second dimension</b>
           <label>Printed dimension (mm) <input type="number" inputmode="numeric" min="1" step="1" data-ws-dim2-mm value="${esc(m.dim2Mm)}"></label>
           <span class="ws-fine">${m.dim2.length}/2 ends clicked${m.dim2.length ? ` · <button class="link-button" type="button" data-ws-dim-clear="dim2">Clear</button>` : ""}</span></div>` : ""}
         ${scale.warn ? `<p class="ws-banner is-warn">${esc(scale.warn)}</p>` : ""}`;
    const bodies = {
      corners: `<p>Click each corner of ${esc(m.label)}, going round the room. Corners snap to the wall lines (blue). Click the first corner again to finish.</p>
        <div class="ws-measure-row"><span class="ws-fine">${m.points.length} corner${m.points.length === 1 ? "" : "s"}</span>
          ${m.points.length >= 3 ? `<button class="btn ghost" type="button" data-ws-close>Finish outline</button>` : ""}
          ${m.points.length ? `<button class="link-button" type="button" data-ws-undo>Undo last corner</button><button class="link-button" type="button" data-ws-restart>Start again</button>` : ""}</div>`,
      scale: `${scaleBody}<div class="ws-measure-row"><button class="link-button" type="button" data-ws-undo>Undo</button><button class="link-button" type="button" data-ws-restart>Start again</button></div>`,
      save: `<div class="ws-measure-row"><p class="ws-measure-area" data-ws-measure-area><span>${areaM2?.toFixed(1)}</span> m²</p>
          <span class="ws-ok">${esc(scale.text || "")}</span></div>
        ${m.typedArea != null ? `<p class="ws-fine">This replaces the ${esc(m.typedArea)} m² you typed.</p>` : ""}
        <div class="ws-measure-row"><label>Your name <input data-ws-measure-name value="${esc(userName())}" autocomplete="name"></label>
          <button class="btn key" type="button" data-ws-measure-save ${m.saving ? "disabled" : ""}>${m.saving ? "Saving… (about 15 s)" : `Save ${esc(m.label)}`}</button>
          <button class="link-button" type="button" data-ws-undo>Undo</button><button class="link-button" type="button" data-ws-restart>Start again</button></div>`,
    };
    steps.innerHTML = `
      <div class="ws-measure-progress-row"><ol class="ws-measure-progress">${pill("corners", "Outline the room")}${pill("scale", "Set the scale from a printed dimension")}${pill("save", "Save")}</ol>
        ${m.namePages?.length && !m.namePages.includes(m.page) ? `<span class="ws-fine" data-ws-name-pages>${esc(m.label)}'s name is printed on page ${m.namePages.join(", ")}.</span>` : ""}</div>
      <div class="ws-measure-current" data-ws-step="${current}">
        ${saved && !m.points.length ? `<p class="ws-banner">Already measured on this page${saved.calibration?.mm_per_px ? `: ${(shoelace(saved.points_image_px) * saved.calibration.mm_per_px ** 2 / 1e6).toFixed(1)} m² (dashed green)` : ""}. Measuring again replaces it.</p>` : ""}
        ${bodies[current]}
        ${m.error ? `<p class="ws-banner is-warn" role="alert" data-ws-measure-error>${esc(m.error)}</p>` : ""}
      </div>`;
    wireMeasureSteps(page);
  }

  function snapPoint(m, point, unit) {
    const snap = m.snap || {};
    const tolerance = Math.max(Number(snap.snap_tolerance_px) || 8, 0);
    let best = null;
    for (const row of snap.endpoints || []) {
      const d = dist(point, row.point_px);
      if (d <= tolerance && (!best || d < best.d)) best = {point: row.point_px, lineId: row.line_id, d};
    }
    for (const row of snap.intersections || []) {
      const d = dist(point, row.point_px);
      if (d <= tolerance && (!best || d < best.d)) best = {point: row.point_px, lineId: row.line_ids[0], d};
    }
    return best ? {point: [...best.point], lineId: best.lineId, snapped: true} : {point: point.map(value => Math.round(value * 10) / 10), lineId: null, snapped: false};
  }

  function addMeasurePoint(point, page) {
    const m = state.measure, svg = body.querySelector("[data-ws-plan-svg]");
    const unit = m.view.w / Math.max(1, svg.getBoundingClientRect().width || 800);
    const W = Number(page.image_width_px), H = Number(page.image_height_px);
    point = [Math.max(0, Math.min(W, point[0])), Math.max(0, Math.min(H, point[1]))];
    m.error = "";
    if (!m.closed) {
      if (m.points.length >= 3 && dist(point, m.points[0]) <= 12 * unit) return closeOutline();
      const snapped = snapPoint(m, point, unit);
      m.points.push(snapped.point);
      m.snapped.push(snapped.lineId);
    } else if (!(m.useReuse && m.reuse)) {
      m.step = "scale";
      const scale = measureScale(m, page);
      const key = m.dim.length < 2 ? "dim" : (scale.needSecond || m.dim2.length) && m.dim2.length < 2 ? "dim2" : "dim";
      if (key === "dim" && m.dim.length >= 2) m.dim = [];
      m[key].push(point);
    }
    drawMeasure();
  }

  function closeOutline() {
    const m = state.measure;
    if (m.points.length < 3) return;
    m.points.push([...m.points[0]]);
    m.snapped.push(m.snapped[0]);
    m.closed = true;
    m.step = "scale";
    drawMeasure();
  }

  function wireMeasure(page) {
    const m = state.measure, svg = body.querySelector("[data-ws-plan-svg]");
    const W = Number(page.image_width_px), H = Number(page.image_height_px);
    const toImage = event => {
      const point = svg.createSVGPoint();
      point.x = event.clientX; point.y = event.clientY;
      const result = point.matrixTransform(svg.getScreenCTM().inverse());
      return [result.x, result.y];
    };
    const clampView = view => {
      view.w = Math.max(W / 40, Math.min(W, view.w)); view.h = view.w * H / W;
      view.x = Math.max(0, Math.min(W - view.w, view.x)); view.y = Math.max(0, Math.min(H - view.h, view.y));
      return view;
    };
    const zoomAt = (factor, centre) => {
      const v = m.view, nw = Math.max(W / 40, Math.min(W, v.w * factor));
      const fx = (centre[0] - v.x) / v.w, fy = (centre[1] - v.y) / v.h;
      m.view = clampView({x: centre[0] - fx * nw, y: centre[1] - fy * nw * H / W, w: nw, h: nw * H / W});
      drawMeasure();
    };
    body.querySelector("[data-ws-measure-back]").addEventListener("click", () => { state.measure = null; renderRooms().catch(showTabError); });
    body.querySelector("[data-ws-measure-page]").addEventListener("change", event => { m.page = Number(event.target.value); m.snap = null; m.useReuse = undefined; renderMeasure().catch(showTabError); });
    body.querySelectorAll("[data-ws-zoom]").forEach(button => button.addEventListener("click", () => {
      const v = m.view, centre = [v.x + v.w / 2, v.y + v.h / 2];
      if (button.dataset.wsZoom === "fit") { m.view = {x: 0, y: 0, w: W, h: H}; drawMeasure(); }
      else zoomAt(button.dataset.wsZoom === "in" ? 0.6 : 1 / 0.6, centre);
    }));
    svg.addEventListener("wheel", event => { event.preventDefault(); zoomAt(event.deltaY < 0 ? 0.8 : 1.25, toImage(event)); }, {passive: false});
    let drag = null;
    svg.addEventListener("pointerdown", event => { drag = {x: event.clientX, y: event.clientY, view: {...m.view}, moved: false}; svg.setPointerCapture?.(event.pointerId); });
    svg.addEventListener("pointermove", event => {
      if (drag) {
        const dx = event.clientX - drag.x, dy = event.clientY - drag.y;
        if (Math.hypot(dx, dy) > 4) drag.moved = true;
        if (drag.moved) {
          const unit = drag.view.w / Math.max(1, svg.getBoundingClientRect().width);
          m.view = clampView({...drag.view, x: drag.view.x - dx * unit, y: drag.view.y - dy * unit});
          drawMeasure();
          return;
        }
      }
      if (!m.closed) {
        const unit = m.view.w / Math.max(1, svg.getBoundingClientRect().width);
        m.hover = snapPoint(m, toImage(event), unit);
        const hover = body.querySelector(".ws-hover");
        if (hover) { hover.setAttribute("cx", m.hover.point[0]); hover.setAttribute("cy", m.hover.point[1]); hover.classList.toggle("is-snapped", m.hover.snapped); }
        else drawMeasure();
      }
    });
    svg.addEventListener("pointerup", event => {
      const wasDrag = drag?.moved;
      drag = null;
      if (!wasDrag && event.button === 0) addMeasurePoint(toImage(event), page);
    });
    svg.addEventListener("pointerleave", () => { if (m.hover) { m.hover = null; drawMeasure(); } });
    svg.addEventListener("keydown", event => {
      const step = (event.shiftKey ? 1 : 10) * m.view.w / Math.max(1, svg.getBoundingClientRect().width);
      const cursor = m.cursor || [m.view.x + m.view.w / 2, m.view.y + m.view.h / 2];
      const moves = {ArrowLeft: [-step, 0], ArrowRight: [step, 0], ArrowUp: [0, -step], ArrowDown: [0, step]};
      if (moves[event.key]) {
        event.preventDefault();
        m.cursor = [cursor[0] + moves[event.key][0], cursor[1] + moves[event.key][1]];
        m.hover = m.closed ? {point: m.cursor, snapped: false} : snapPoint(m, m.cursor, 1);
        drawMeasure();
      } else if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        addMeasurePoint(cursor, page);
      } else if (event.key === "Backspace") {
        event.preventDefault();
        undoMeasure();
      }
    });
  }

  function undoMeasure() {
    const m = state.measure;
    if (m.dim2.length) m.dim2.pop();
    else if (m.dim.length) m.dim.pop();
    else if (m.closed) { m.points.pop(); m.snapped.pop(); m.closed = false; m.step = "corners"; }
    else { m.points.pop(); m.snapped.pop(); }
    drawMeasure();
  }

  function wireMeasureSteps(page) {
    const m = state.measure, steps = body.querySelector("[data-ws-measure-steps]");
    steps.querySelector("[data-ws-close]")?.addEventListener("click", closeOutline);
    steps.querySelector("[data-ws-undo]")?.addEventListener("click", undoMeasure);
    steps.querySelector("[data-ws-restart]")?.addEventListener("click", () => {
      Object.assign(m, {points: [], snapped: [], closed: false, dim: [], dimMm: "", dim2: [], dim2Mm: "", step: "corners", error: "", useReuse: undefined});
      drawMeasure();
    });
    steps.querySelector("[data-ws-reuse]")?.addEventListener("click", () => { m.useReuse = true; drawMeasure(); });
    steps.querySelector("[data-ws-rescale]")?.addEventListener("click", () => { m.useReuse = false; m.reuse = null; drawMeasure(); });
    steps.querySelectorAll("[data-ws-dim-clear]").forEach(button => button.addEventListener("click", () => { m[button.dataset.wsDimClear] = []; drawMeasure(); }));
    for (const [selector, key] of [["[data-ws-dim-mm]", "dimMm"], ["[data-ws-dim2-mm]", "dim2Mm"]]) {
      steps.querySelector(selector)?.addEventListener("change", event => { m[key] = event.target.value.trim(); drawMeasure(); });
    }
    steps.querySelector("[data-ws-measure-save]")?.addEventListener("click", () => saveMeasure(page));
  }

  async function saveMeasure(page) {
    const m = state.measure, projectId = state.projectId;
    const name = body.querySelector("[data-ws-measure-name]")?.value.trim() || "";
    if (!name) { m.error = "Type your name or initials: every measurement records who made it."; return drawMeasure(); }
    storage.set("toki.workspace.name", name);
    const reuse = m.useReuse && m.reuse;
    const dims = reuse ? {points: m.reuse.points, mm: Number(m.reuse.mm), second: m.reuse.second} : {points: m.dim, mm: Number(m.dimMm),
      second: m.dim2.length === 2 && Number(m.dim2Mm) > 0 ? {points: m.dim2, mm: Number(m.dim2Mm)} : null};
    m.saving = true; m.error = "";
    drawMeasure();
    try {
      const data = await sendJson("/api/reviewer-room-geometry", {action: "save", project_id: projectId, room_id: m.key, page: m.page,
        points_image_px: m.points, snapped_line_ids: m.snapped, dimension_points_image_px: dims.points, dimension_value_mm: dims.mm,
        second_dimension_points_image_px: dims.second?.points || [], second_dimension_value_mm: dims.second?.mm || null,
        reviewer: name, note: "Measured in the job workspace.", source_pdf_fingerprint: m.ctx.source_pdf_fingerprint,
        vector_page_fingerprint: m.snap.vector_page_fingerprint});
      const record = (data.reviewer_room_geometry?.records || []).find(row => row.room_id === m.key && row.page === m.page);
      if (!SCALE_OK.has(record?.calibration?.status)) throw new Error("The scale wasn't accepted; check the printed dimension and try again.");
      const area = shoelace(record.points_image_px) * record.calibration.mm_per_px ** 2 / 1e6;
      // The person just measured the room: the measurement replaces an area they typed earlier.
      if (m.typedArea != null) await sendJson("/api/room-area-override", {project_id: projectId, room_id: m.key, label: m.label, area_m2: null, edited_by: name});
      state.measure = null;
      const update = data.trace_save_update || {};
      const retained = update.kept_walls?.length || 0, reset = update.reset_edge_indices?.length || 0;
      const dropped = update.dropped_openings?.length || 0;
      const details = update.kept_walls || update.dropped_openings || reset
        ? ` ${retained} wall decision${retained === 1 ? "" : "s"} kept, ${reset} reset${dropped ? `, ${dropped} opening${dropped === 1 ? "" : "s"} dropped` : ""}.` : "";
      state.message = `${m.label} measured: ${area.toFixed(1)} m²${m.typedArea != null ? ` (replaces the ${m.typedArea} m² you typed)` : ""}.${details} Calculate to update the result.`
        + (data.task_refresh_error ? ` Drawing checks could not refresh: ${data.task_refresh_error}` : "");
      await loadStatus();
      if (live(projectId, "rooms")) await renderRooms();
    } catch (error) {
      if (state.measure !== m) return;
      m.saving = false;
      m.error = `Could not save: ${error.message}`;
      if (live(projectId, "rooms")) drawMeasure();
    }
  }

  // Ceiling height: typed (shown as the value) or the current drawing/default height (shown greyed as the hint).
  const HEIGHT_SOURCE = {edited: "Edited by you", preliminary_fallback: "Typical height", contractor_override: "Entered in Engineer review",
                         project_evidence: "From the drawings", scoped_project_evidence: "From the drawings", ai_estimated: "AI-read"};
  function heightCell(row) {
    const current = state.status?.room_heights?.[row.key];
    const metres = current?.ceiling_height_mm != null ? (current.ceiling_height_mm / 1000).toFixed(2) : "";
    const typed = current?.origin === "edited";
    const source = current ? (HEIGHT_SOURCE[current.origin] || "From the drawings") : "Set on Calculate";
    return `<input class="ws-area" type="number" inputmode="decimal" min="1.8" max="15" step="0.05" data-ws-height
      aria-label="Ceiling height of ${esc(row.label)} in metres" value="${typed ? metres : ""}" placeholder="${esc(metres || "2.70")}">
      <small class="ws-height-source">${esc(source)}</small>`;
  }

  // The floor-plan page the rooms come from: an added room is cited on it.
  function planPage(rows) {
    const pages = rows.flatMap(row => (row.source_pages || []).slice(0, 1)).filter(page => Number.isInteger(page) && page > 0);
    if (pages.length) return pages.sort((a, b) => pages.filter(p => p === b).length - pages.filter(p => p === a).length)[0];
    const sheet = (state.analysis?.sheets || []).find(row => row.plan_role === "main_floor_plan" && row.relevant)
      || (state.analysis?.sheets || []).find(row => row.type === "floor_plan");
    return sheet?.page || null;
  }

  async function renderRooms() {
    const projectId = state.projectId;
    if (state.measure?.projectId === projectId) return renderMeasure();
    if (!DATA?.has_reasoning_packet) {
      body.innerHTML = `<div class="ws-card"><h2>Rooms</h2><p>The drawing pages are being prepared first.</p>
        <button class="btn key" type="button" data-ws-go="drawings">Go to Drawings</button></div>`;
      return wireCommon();
    }
    if (state.roomsModel && state.roomsMarker !== state.status?.checks?.marker) state.roomsModel = null;  // new AI results since
    if (!state.roomsModel) progress("Loading the rooms", "Reading the rooms found on your drawings.");
    const model = state.roomsModel || await loadRooms();
    if (!state.status) await loadStatus();
    if (!live(projectId, "rooms")) return;
    const rows = roomRowsWithTyped(model);
    const uses = Object.entries(model.room_scope?.uses || {}).filter(([id]) => id !== "not_a_room");
    const levelOf = row => /^unassigned level$/i.test(row.level || "") ? "" : (row.level || "");
    const noLevels = rows.every(row => !levelOf(row));
    const detailsTab = state.subtab !== "measurements";
    const roomTabClass = detailsTab ? "ws-room-details" : "ws-room-measurements";
    const roomTabTitle = detailsTab ? "Room details" : "Measurements";
    const roomTabHint = detailsTab ? "Check the rooms found on the drawings, choose what each is used for, and select the rooms that are cooled."
      : "Review each room's area and ceiling height. Type a value to change it, or clear it to return to the drawing value.";
    body.innerHTML = `<div class="ws-card ${roomTabClass}" data-ws-rooms>
      <h2>${roomTabTitle}</h2>
      <p class="ws-hint">${roomTabHint}</p>
      <div class="ws-table-wrap"><table class="ws-table${noLevels ? " ws-no-levels" : ""}">
        <thead><tr><th scope="col">Cool</th><th scope="col">Room</th><th scope="col" class="ws-col-level">Level</th><th scope="col">Area (m²)</th><th scope="col">Where the area came from</th><th scope="col">Ceiling height (m)</th><th scope="col"><span class="visually-hidden">Actions</span></th></tr></thead>
        <tbody>${rows.map(row => {
          const include = state.include.has(row.key) ? state.include.get(row.key) : (row.include || row.area_m2 != null);
          const needsUse = row.status === "needs_use";
          return `<tr data-ws-room="${esc(row.key)}" data-label="${esc(row.label)}" data-level="${esc(row.level || "")}">
            <td><input type="checkbox" data-ws-include aria-label="Cool ${esc(row.label)}" ${include ? "checked" : ""}></td>
            <th scope="row">${esc(row.label)}${needsUse ? `<label class="ws-use">What is it used for?<select data-ws-use><option value="">Choose…</option>${uses.map(([id, label]) => `<option value="${esc(id)}">${esc(label)}</option>`).join("")}</select></label>` : ""}</th>
            <td class="ws-col-level">${esc(levelOf(row))}</td>
            <td><input class="ws-area" type="number" inputmode="decimal" min="0.1" step="0.1" data-ws-area aria-label="Area of ${esc(row.label)} in m²"
                 value="${row.area_m2 != null ? Number(row.area_m2).toFixed(1) : ""}" placeholder="Type area"></td>
            <td><span class="ws-chip is-${esc(String(row.area_origin || "none").replace(/[^a-z_]/gi, "_"))}">${esc(areaSource(row))}</span></td>
            <td>${heightCell(row)}</td>
            <td><button class="link-button" type="button" data-ws-trace>Measure on the plan</button></td></tr>`;
        }).join("") || `<tr><td colspan="7">No rooms were found on the drawings yet. They appear here once the drawing check has read the room names; Engineer review can trace them meanwhile.</td></tr>`}</tbody></table></div>
      <form class="ws-add" data-ws-add><b>Add a room the drawings missed</b>
        <label>Name<input name="label" required autocomplete="off"></label>
        <label>Used as<select name="use" required><option value="">Choose…</option>${uses.map(([id, label]) => `<option value="${esc(id)}">${esc(label)}</option>`).join("")}</select></label>
        <label>Starting area (m²)<input name="area" type="number" min="0.1" step="0.1" required></label>
        <button class="btn ghost" type="submit">Add room</button></form>
      <p class="ws-status" role="status" data-ws-rooms-status>${esc(state.message)}</p>
      ${nextButton("rooms")}</div>`;
    state.message = "";
    wireRooms(projectId, rows);
    wireCommon();
  }

  function wireRooms(projectId, rows) {
    const status = body.querySelector("[data-ws-rooms-status]");
    const after = async (message, status) => {
      state.message = message;
      if (status) { state.status = status; renderRail(); } else await loadStatus();
      if (live(projectId, "rooms")) await renderRooms();
    };
    body.querySelectorAll("[data-ws-room]").forEach(row => {
      const key = row.dataset.wsRoom, label = row.dataset.label;
      row.querySelector("[data-ws-include]")?.addEventListener("change", event => {
        state.include.set(key, event.target.checked);
        status.textContent = event.target.checked ? `${label} will be cooled.` : `${label} won't be included.`;
      });
      row.querySelector("[data-ws-area]")?.addEventListener("change", async event => {
        const raw = event.target.value.trim();
        const area = raw === "" ? null : Number(raw);
        if (area !== null && !(area > 0)) { status.textContent = "Type the area as a number of m², or clear it."; return; }
        status.textContent = `Saving ${label}…`;
        try {
          const saved = await sendJson("/api/room-area-override", {project_id: projectId, room_id: key, label, level_name: row.dataset.level,
                                                                   area_m2: area, edited_by: userName()});
          if (area !== null) state.include.set(key, true);
          await after(area === null ? `${label}: back to the drawing value.` : `${label}: ${area} m² saved.`, saved);
        } catch (error) { status.textContent = `Could not save ${label}: ${error.message}`; status.classList.add("is-error"); status.setAttribute("role", "alert"); }
      });
      row.querySelector("[data-ws-height]")?.addEventListener("change", async event => {
        const raw = event.target.value.trim();
        const metres = raw === "" ? null : Number(raw);
        if (metres !== null && !(metres >= 1.8 && metres <= 15)) { status.textContent = "Type the ceiling height in metres (1.8 to 15), or clear it."; return; }
        status.textContent = `Saving ${label}…`;
        try {
          const saved = await sendJson("/api/room-height-override", {project_id: projectId, room_key: key, label, level_name: row.dataset.level,
                                                                     ceiling_height_mm: metres === null ? null : Math.round(metres * 1000), edited_by: userName()});
          await after(metres === null ? `${label}: ceiling height back to the drawing value.` : `${label}: ceiling height ${metres} m saved.`, saved);
        } catch (error) { status.textContent = `Could not save ${label}: ${error.message}`; status.classList.add("is-error"); status.setAttribute("role", "alert"); }
      });
      row.querySelector("[data-ws-use]")?.addEventListener("change", async event => {
        if (!event.target.value) return;
        status.textContent = `Saving the use of ${label}…`;
        try {
          await sendJson("/api/room-use-resolution", {project_id: projectId, action: "apply_override", room_id: key,
            taxonomy_id: event.target.value, reviewer: userName() || "Contractor", note: "Use chosen in the job workspace."});
          state.roomsModel = null;
          await after(`Use saved for ${label}.`);
        } catch (error) { status.textContent = `Could not save the use: ${error.message}`; status.classList.add("is-error"); status.setAttribute("role", "alert"); }
      });
      row.querySelector("[data-ws-trace]")?.addEventListener("click", () => {
        state.measure = {projectId, key, label, typedArea: (state.status?.area_overrides || []).find(item => item.room_id === key)?.area_m2 ?? null};
        renderRooms().catch(showTabError);
      });
    });
    body.querySelector("[data-ws-add]")?.addEventListener("submit", async event => {
      event.preventDefault();
      const form = event.target, label = form.elements.label.value.trim(), use = form.elements.use.value, area = Number(form.elements.area.value);
      if (!label || !use || !(area > 0)) { status.textContent = "Give the room a name, what it's used as, and an area in m²."; return; }
      status.textContent = `Adding ${label}…`;
      try {
        const added = await sendJson("/api/reviewer-room-geometry", {action: "add_room", project_id: projectId, label,
          level_name: "Unassigned level", taxonomy_id: use, reviewer: userName() || "Contractor", page: planPage(rows),
          note: "Added in the job workspace."});
        const room = (added.rooms || []).find(row => row.reviewer_added && String(row.label).toLowerCase() === label.toLowerCase());
        const saved = await sendJson("/api/room-area-override", {project_id: projectId, room_id: room?.room_id || "", label,
                                                                 area_m2: area, edited_by: userName()});
        state.include.set(saved.room_id || room?.room_id, true);
        await after(`${label} added.`, saved);
      } catch (error) { status.textContent = `Could not add the room: ${error.message}`; status.classList.add("is-error"); status.setAttribute("role", "alert"); }
    });
  }

  // ------------------------------------------------------------------ Calculate
  // Calculate runs on the server as one job (apply answers, rebuild the model, confirm the cooled rooms,
  // calculate; prepare the inputs first when they're missing). The page starts and watches it, so closing
  // or reloading the page doesn't stop it, and reopening the job shows the progress again.
  const CALC_POLL_MS = 2000;
  async function calculate() {
    const projectId = state.projectId;
    const note = document.getElementById("wsCalcNote");
    if (!state.status?.rooms?.with_area) {
      note.textContent = "Add room areas first.";
      go("rooms");
      return;
    }
    try {
      const job = await sendJson("/api/job-calculation", {project_id: projectId, reviewer: userName() || "Contractor",
                                                          include: Object.fromEntries(state.include)});
      await watchCalculation(projectId, job);
    } catch (error) {
      if (state.projectId === projectId) note.textContent = `Could not calculate: ${error.message}`;
    }
  }

  async function watchCalculation(projectId, job) {
    if (state.calculating === projectId) return;
    state.calculating = projectId;
    const button = document.getElementById("wsCalculate");
    const note = document.getElementById("wsCalcNote");
    const started = Date.now();
    button.disabled = true;
    try {
      while (job.status === "running" || job.status === "queued") {
        if (state.projectId !== projectId) return;
        const seconds = Math.round((Date.now() - started) / 1000);
        note.textContent = `${job.step_label || "Calculating"}…${seconds >= 5 ? ` (${seconds} s)` : ""} You can leave this page; it carries on.`;
        await new Promise(resolve => setTimeout(resolve, CALC_POLL_MS));
        job = await getJson(`/api/job-calculation?project_id=${encodeURIComponent(projectId)}`);
      }
      if (job.status !== "done") throw new Error(job.error || "The calculation didn't finish.");
      if (state.projectId !== projectId) return;
      note.textContent = "";
      state.roomsModel = null;  // the model was rebuilt
      await loadStatus();
      if (state.tab === "results") renderTab(); else go("results");
    } catch (error) {
      if (state.projectId === projectId) note.textContent = `Could not calculate: ${error.message}`;
    } finally {
      if (state.calculating === projectId) state.calculating = null;
      if (state.projectId === projectId) button.disabled = false;
    }
  }

  // Reopening a job while its calculation still runs on the server shows the progress again.
  async function resumeCalculation(projectId) {
    const job = await getJson(`/api/job-calculation?project_id=${encodeURIComponent(projectId)}`).catch(() => ({}));
    if (state.projectId !== projectId) return;
    if (job.status === "running" || job.status === "queued") watchCalculation(projectId, job);
    else if (job.status === "interrupted" || job.status === "failed") {
      const note = document.getElementById("wsCalcNote");
      if (note && !note.textContent) note.textContent = `The last calculation didn't finish: ${job.error || "press Calculate again."}`;
    }
  }

  // ------------------------------------------------------------------ Results
  function exclusionLines(report) {
    const prefixes = {
      roof_solar: "Sun on the roof", roof_exposure: "Roof (not checked yet)", unclassified_wall_boundaries: "Walls (not classified yet)",
      external_wall_orientation: "Sun on outside walls", external_wall_edge: "Outside walls",
      area_only_walls: "Walls (no outline: area only)", area_only_roof: "Roof (no outline: area only)",
    };
    const groups = {
      extract_air: "Exhaust and make-up air (needs the rangehood and exhaust rates)", make_up_air: "Exhaust and make-up air (needs the rangehood and exhaust rates)",
      infiltration: "Air leakage through doors and gaps",
      vapour_gain: "Moisture from cooking and dishwashing", steam_gain: "Moisture from cooking and dishwashing", process_latent_load: "Moisture from cooking and dishwashing",
      minimum_supply_air: "Air system design (supply, spill and transfer air) — set later by the engineer",
      spill_air: "Air system design (supply, spill and transfer air) — set later by the engineer",
      transfer_air: "Air system design (supply, spill and transfer air) — set later by the engineer",
      envelope: "Walls, roof and glazing",
    };
    const names = new Map([...(report.room_names || []), ...(report.room_peaks || [])].map(room => [room.room_id, room.name]));
    const allRooms = new Set([...names.values()].filter(Boolean));
    const grouped = new Map();
    for (const item of [...(report.known_exclusions || []), ...(report.unresolved_room_inputs || [])]) {
      const id = item.component_id || "";
      if (/^boundary_edge/.test(id) || ["Internal boundary", "Adjacent tenancy boundary", "Faces an enclosed mall/walkway"].includes(item.component)) continue;
      const label = Object.entries(prefixes).find(([prefix]) => id.startsWith(prefix))?.[1] || groups[item.component_type] || item.component || "Other items";
      if (!grouped.has(label)) grouped.set(label, new Set());
      const room = names.get(item.room_id) || item.room_name;
      if (room) grouped.get(label).add(room);
    }
    // The specific walls and roof lines already say why the envelope is missing for those rooms.
    const envelope = grouped.get(groups.envelope);
    if (envelope) {
      const specific = [prefixes.area_only_walls, prefixes.area_only_roof, prefixes.unclassified_wall_boundaries, prefixes.roof_exposure];
      const covered = new Set(specific.flatMap(label => [...(grouped.get(label) || [])]));
      if ((covered.size && [...envelope].every(room => covered.has(room))) || (!envelope.size && covered.size)) grouped.delete(groups.envelope);
    }
    return [...grouped.entries()].sort(([a], [b]) => a.localeCompare(b)).map(([label, rooms]) =>
      rooms.size && rooms.size < allRooms.size ? `${label} — ${[...rooms].join(", ")}` : label);
  }

  function roomRows(report) {
    const areas = new Map((report.confirmed_rooms || []).map(room => [room.label, room]));
    return (report.room_peaks || []).map(room => {
      const area = areas.get(room.name)?.area_m2;
      return {name: room.name, area, kw: room.design_total_kw, wPerM2: area ? (room.design_total_kw * 1000) / area : null,
              source: areas.has(room.name) ? areaSource(areas.get(room.name)) : ""};
    });
  }

  async function renderResults() {
    const projectId = state.projectId;
    progress("Loading the result", "");
    const model = state.lastResult || await getJson(modelUrl());
    state.lastResult = null;
    if (!live(projectId, "results")) return;
    const report = model.hourly_ai_preliminary_load_report || {};
    const peak = report.included_scope_peak || {};
    const total = peak.final_design_total_kw ?? peak.design_total_kw;
    if (total == null) {
      body.innerHTML = `<div class="ws-card" data-ws-results><h2>Results</h2><p>Not calculated yet. When the rooms have areas, press <b>Calculate</b>.</p>
        <button class="btn key" type="button" data-ws-calc-here>Calculate</button></div>`;
      body.querySelector("[data-ws-calc-here]").addEventListener("click", calculate);
      return;
    }
    const rooms = roomRows(report);
    const components = peak.components || {};
    const excluded = exclusionLines(report);
    // One line per cold room: the report can list the same room twice when it is found on two sheets.
    const refrigeration = [...new Map((report.refrigeration_process_exclusions || [])
      .map(item => [`${item.room_name || ""}|${item.level || ""}`.toLowerCase(), item])).values()];
    const basis = report.design_conditions_basis || {};
    const hour = peak.display_hour ?? peak.hour;
    const factor = Number(peak.safety_factor || 1);
    const COMPONENTS = [["people", "People"], ["lighting", "Lighting"], ["equipment_refrigeration", "Equipment"],
                        ["envelope", "Walls, roof and glazing"], ["outside_air", "Fresh air"], ["infiltration", "Air leakage"]];
    body.innerHTML = `<div class="ws-card ws-result" data-ws-results>
      ${state.status?.result_stale ? `<p class="ws-banner is-warn">Inputs changed since this result. Press <b>Calculate</b> to update it.</p>` : ""}
      <p class="ws-banner">Draft estimate from the drawings — not engineering-reviewed. Check it before using it for equipment selection.</p>
      <div class="ws-total-big"><span data-ws-total>${kw(total)}</span><span>kW total cooling</span></div>
      <p class="ws-hint">Peak ${hour != null ? `at ${hour > 12 ? hour - 12 : hour} ${hour >= 12 ? "pm" : "am"} on the design day` : "on the design day"}${factor > 1 ? `, including a ${Math.round((factor - 1) * 100)}% allowance` : ""}.</p>
      <h3>By room</h3>
      <div class="ws-table-wrap"><table class="ws-table" data-ws-room-loads><thead><tr><th scope="col">Room</th><th scope="col">Area</th><th scope="col">Cooling</th><th scope="col">W/m²</th><th scope="col">Area from</th></tr></thead><tbody>
        ${rooms.map(room => `<tr><th scope="row">${esc(room.name)}</th><td>${room.area != null ? `${Number(room.area).toFixed(1)} m²` : "—"}</td><td>${kw(room.kw)} kW</td><td>${room.wPerM2 != null ? Math.round(room.wPerM2) : "—"}</td><td>${esc(room.source)}</td></tr>`).join("")}
      </tbody></table></div>
      <h3>What's in the total</h3>
      <ul class="ws-list" data-ws-included>${COMPONENTS.filter(([id]) => components[id]).map(([id, label]) => `<li><span>${esc(label)}</span><b>${kw(components[id].total_kw)} kW</b></li>`).join("")}</ul>
      ${excluded.length || refrigeration.length ? `<h3>Not included yet</h3><ul class="ws-list ws-bullets" data-ws-excluded>${excluded.map(line => `<li>${esc(line)}</li>`).join("")}
        ${refrigeration.map(item => `<li>${esc(item.room_name || "Cold room")} — refrigeration, sized separately</li>`).join("")}</ul>` : ""}
      ${basis.design_day ? `<h3>Weather used</h3><p class="ws-fine">A generic Australian design day (not specific to this site yet).</p>` : ""}
      <div class="ws-actions ws-no-print">
        <button class="btn key" type="button" data-ws-print>Print or save as PDF</button>
        <button class="btn ghost" type="button" data-ws-csv>Download room loads (CSV)</button>
      </div></div>`;
    body.querySelector("[data-ws-print]").addEventListener("click", () => window.print());
    body.querySelector("[data-ws-csv]").addEventListener("click", () => downloadCsv(rooms, total));
  }

  function downloadCsv(rooms, total) {
    const cell = value => `"${String(value ?? "").replace(/"/g, '""')}"`;
    const lines = [["Room", "Area (m2)", "Cooling (kW)", "W/m2", "Area from"].map(cell).join(",")]
      .concat(rooms.map(room => [room.name, room.area != null ? Number(room.area).toFixed(1) : "", kw(room.kw),
                                 room.wPerM2 != null ? Math.round(room.wPerM2) : "", room.source].map(cell).join(",")))
      .concat([["Total (draft)", "", kw(total), "", ""].map(cell).join(",")]);
    const link = document.createElement("a");
    link.href = URL.createObjectURL(new Blob([lines.join("\r\n") + "\r\n"], {type: "text/csv"}));
    link.download = `${(state.status?.name || DATA?.name || "job").replace(/\.pdf$/i, "")} - cooling loads.csv`;
    document.body.appendChild(link);
    link.click();
    setTimeout(() => { URL.revokeObjectURL(link.href); link.remove(); }, 0);
  }

  // ------------------------------------------------------------------ Walls and windows
  async function loadEnvelopeContext() {
    const data = await getJson(`/api/reviewer-room-geometry?project_id=${encodeURIComponent(state.projectId)}`);
    state.envelopeCtx = data;
    return data;
  }

  function envelopeRooms() { return state.envelopeCtx?.rooms || []; }
  function envelopeRecords(roomId) {
    return (state.envelopeCtx?.reviewer_room_geometry?.records || [])
      .filter(row => row.room_id === roomId && row.freshness === "current");
  }
  function selectedEnvelopeTrace(roomId, traceId) {
    const rows = envelopeRecords(roomId);
    return rows.find(row => row.trace_id === traceId) || rows[0] || null;
  }
  const BOUNDARY_LABEL = {external: "Outside", mall: "Enclosed mall", adjacent_tenancy: "Neighbouring tenancy", internal: "Internal", unknown: "Not set"};
  const ROOF_LABEL = {exposed: "Exposed to the roof", not_exposed: "Another tenancy above", unknown: "Not set"};
  function sourceLabel(value) {
    return value === "reviewer" ? "Edited by you" : value === "ai_determined" ? "AI-determined"
      : value === "ai_fallback" ? "AI-determined (below accuracy bar)" : "Not set";
  }
  function sourceChip(value) { return `<span class="ws-chip is-${value === "reviewer" ? "edited" : value ? "ai" : "none"}">${esc(sourceLabel(value))}</span>`; }
  function measureRoom(room) {
    state.measure = {projectId: state.projectId, key: room.room_id, label: room.label,
      typedArea: (state.status?.area_overrides || []).find(item => item.room_id === room.room_id)?.area_m2 ?? null};
    goSubtab("rooms", "measurements");
  }
  function classifyPayload(trace, reviewer, changes = {}) {
    return {action: "classify_envelope", project_id: state.projectId, trace_id: trace.trace_id,
      reviewer, declaration_source: "reviewer", edges: changes.edges || trace.edges || [],
      roof: changes.roof || trace.roof || "unknown", openings: changes.openings || trace.openings || [],
      openings_none_edges: changes.openings_none_edges ?? trace.openings_none_edges ?? [],
      confirm_roof: changes.confirm_roof ?? false, confirmed_edges: changes.confirmed_edges || []};
  }
  async function refreshEnvelopeUi(tab) {
    await loadStatus();
    await loadEnvelopeContext();
    if (live(state.projectId, tab)) await (tab === "walls" ? renderWalls() : renderWindows());
  }
  async function renderWalls() {
    const projectId = state.projectId;
    const ctx = state.envelopeCtx || await loadEnvelopeContext();
    if (!live(projectId, "walls")) return;
    const rooms = ctx.rooms || [];
    const outlinedRooms = rooms.filter(row => envelopeRecords(row.room_id).length);
    if (!state.wallsRoomId || !outlinedRooms.some(row => row.room_id === state.wallsRoomId))
      state.wallsRoomId = outlinedRooms[0]?.room_id || "";
    let selectedRoom = outlinedRooms.find(row => row.room_id === state.wallsRoomId);
    let traces = selectedRoom ? envelopeRecords(selectedRoom.room_id) : [];
    if (!state.wallsTraceId || !traces.some(row => row.trace_id === state.wallsTraceId)) state.wallsTraceId = traces[0]?.trace_id || "";
    const trace = selectedEnvelopeTrace(state.wallsRoomId, state.wallsTraceId);
    if (trace && !selectedRoom) selectedRoom = rooms.find(row => row.room_id === trace.room_id);
    const summaryEdges = state.status?.envelope?.[state.wallsRoomId]?.edges || [];
    const page = trace && (ctx.pages || []).find(row => row.page === trace.page);
    const areaOnly = rooms.filter(row => !envelopeRecords(row.room_id).length);
    const selectedEdges = trace?.edges || [];
    const roofTab = state.subtab === "roof";
    if (trace && !state.envelopeDrafts[trace.trace_id]) {
      state.envelopeDrafts[trace.trace_id] = {edges: selectedEdges.map(edge => ({index: edge.index, boundary: edge.boundary})),
        roof: trace.roof || "unknown", accepted_edges: []};
    }
    const draft = trace ? state.envelopeDrafts[trace.trace_id] : {edges: [], roof: "unknown"};
    const draftBoundary = index => draft.edges.find(edge => edge.index === index)?.boundary || "unknown";
    const colors = {external: "#d44", mall: "#8e44ad", adjacent_tenancy: "#e67e22", internal: "#3976a8", unknown: "#777"};
    const outline = trace && page ? `<div class="ws-envelope-plan"><img src="${esc(page.preview_url)}" alt="Plan page ${trace.page}">
      <svg viewBox="0 0 ${page.image_width_px} ${page.image_height_px}" role="img" aria-label="${esc(selectedRoom?.label)} wall outline on page ${trace.page}">
      ${trace.points_image_px.slice(0, -1).map((point, i) => { const end = trace.points_image_px[i + 1];
        return `<line data-wall-edge="${i}" x1="${point[0]}" y1="${point[1]}" x2="${end[0]}" y2="${end[1]}" stroke="${colors[draftBoundary(i)] || colors.unknown}" class="${i === Number(state.selectedWallEdge) ? "is-selected" : ""}"/>`;}).join("")}</svg></div>` : "";
    body.innerHTML = `<div class="ws-card" data-ws-walls><h2>${roofTab ? "Roof" : "Walls"}</h2>
      <p class="ws-hint">${roofTab ? "For each room, record whether another tenancy or the roof is directly above it." : "Classify what each traced room wall faces. Area-only rooms stay unassessed until they are measured."}</p>
      ${outlinedRooms.length ? `<label>Room <select data-ws-wall-room>${outlinedRooms.map(room => `<option value="${esc(room.room_id)}" ${room.room_id === state.wallsRoomId ? "selected" : ""}>${esc(room.label)}${room.level_name ? ` — ${esc(room.level_name)}` : ""}</option>`).join("")}</select></label>` : `<p>${roofTab ? "Add a room outline on the Walls tab before recording what is above it." : "Add room outlines on the Rooms tab to review walls and roof."}</p>`}
      ${traces.length > 1 ? `<label>Outline <select data-ws-wall-trace>${traces.map((row, i) => `<option value="${esc(row.trace_id)}" ${row.trace_id === trace?.trace_id ? "selected" : ""}>Page ${row.page} — part ${i + 1}</option>`).join("")}</select></label>` : ""}
      ${!roofTab && areaOnly.length ? `<section class="ws-area-only"><h3>Rooms without an outline</h3><p>Walls and roof aren't assessed until each room is measured.</p>${areaOnly.map(room => `<div>${esc(room.label)} — ${state.status?.traced_rooms?.[room.room_id]?.area_m2 ?? "Area only"} <button class="link-button" type="button" data-ws-measure-room="${esc(room.room_id)}">Measure on the plan</button></div>`).join("")}</section>` : ""}
      ${trace ? `${roofTab ? "" : outline}${roofTab ? "" : `<div class="ws-envelope-legend" aria-label="Wall boundary colours"><span><i style="--wall:#d44"></i>Outside</span><span><i style="--wall:#8e44ad"></i>Enclosed mall</span><span><i style="--wall:#e67e22"></i>Neighbouring tenancy</span><span><i style="--wall:#3976a8"></i>Internal</span><span><i style="--wall:#777"></i>Not set</span></div><div class="ws-table-wrap"><table class="ws-table"><thead><tr><th>Wall</th><th>Length</th><th>Boundary</th><th>Source</th><th></th></tr></thead><tbody>
        ${selectedEdges.map(edge => { const source = trace.edge_sources?.[String(edge.index)] || trace.envelope_declaration_source || trace.declaration_source || "";
          const length = summaryEdges.find(row => row.trace_id === trace.trace_id && row.index === edge.index)?.length_m ?? edge.length_m;
          const accepted = draft.accepted_edges?.includes(edge.index);
          return `<tr data-wall-row="${edge.index}"><th scope="row">Wall ${edge.index + 1}</th><td>${length != null ? `${Number(length).toFixed(2)} m` : "—"}</td><td><select data-ws-boundary="${edge.index}" aria-label="What wall ${edge.index + 1} of ${esc(selectedRoom?.label)} faces">${Object.entries(BOUNDARY_LABEL).map(([value,label]) => `<option value="${value}" ${draftBoundary(edge.index) === value ? "selected" : ""}>${label}</option>`).join("")}</select></td><td>${sourceChip(source)}</td><td>${source.startsWith("ai") ? `<button class="link-button" type="button" data-ws-accept-wall="${edge.index}" ${accepted ? "disabled" : ""}>${accepted ? "Accepted" : "Accept AI"}</button>` : ""}</td></tr>`;}).join("")}
        </tbody></table></div>
        <div class="ws-envelope-tools"><label>Set all not-set walls to <select data-ws-all-boundary>${Object.entries(BOUNDARY_LABEL).filter(([v]) => v !== "unknown").map(([v,l]) => `<option value="${v}">${l}</option>`).join("")}</select></label><button class="btn ghost" type="button" data-ws-set-all>Apply</button></div>
        <p class="ws-hint">Mall: an enclosed shopping-centre walkway — a boundary, but no sun</p>`}${roofTab ? `<label>What is above this room? <select data-ws-roof>${Object.entries(ROOF_LABEL).map(([value,label]) => `<option value="${value}" ${draft.roof === value ? "selected" : ""}>${label}</option>`).join("")}</select> ${sourceChip(trace.roof_source || trace.envelope_declaration_source)}</label>` : ""}
        <label>Your name or initials <input data-ws-envelope-reviewer autocomplete="name" value="${esc(userName())}" required></label>
        <div class="ws-actions"><button class="btn key" type="button" data-ws-save-walls>Save walls &amp; roof</button><span role="status" data-ws-envelope-status>${esc(state.message)}</span></div>` : ""}
      ${nextButton("walls")}</div>`;
    state.message = "";
    body.querySelector("[data-ws-wall-room]")?.addEventListener("change", event => { state.wallsRoomId = event.target.value; state.wallsTraceId = ""; renderWalls(); });
    body.querySelector("[data-ws-wall-trace]")?.addEventListener("change", event => { state.wallsTraceId = event.target.value; renderWalls(); });
    body.querySelectorAll("[data-ws-measure-room]").forEach(button => button.addEventListener("click", () => measureRoom(rooms.find(row => row.room_id === button.dataset.wsMeasureRoom))));
    body.querySelectorAll("[data-ws-boundary]").forEach(select => select.addEventListener("change", () => {
      const index = Number(select.dataset.wsBoundary);
      draft.edges = draft.edges.map(edge => edge.index === index ? {...edge, boundary: select.value} : edge);
      body.querySelector(`[data-wall-edge="${index}"]`)?.setAttribute("stroke", colors[select.value] || colors.unknown);
    }));
    body.querySelector("[data-ws-roof]")?.addEventListener("change", event => { draft.roof = event.target.value; });
    body.querySelectorAll("[data-wall-edge]").forEach(line => line.addEventListener("click", () => {
      state.selectedWallEdge = Number(line.dataset.wallEdge);
      body.querySelectorAll("[data-wall-row]").forEach(row => row.classList.toggle("is-selected", Number(row.dataset.wallRow) === state.selectedWallEdge));
      body.querySelector(`[data-ws-boundary="${state.selectedWallEdge}"]`)?.focus();
    }));
    body.querySelectorAll("[data-ws-accept-wall]").forEach(button => button.addEventListener("click", () => {
      const select = body.querySelector(`[data-ws-boundary="${button.dataset.wsAcceptWall}"]`); select.dataset.accepted = "true";
      const index = Number(button.dataset.wsAcceptWall);
      if (!draft.accepted_edges.includes(index)) draft.accepted_edges.push(index);
      button.textContent = "Accepted"; button.disabled = true;
    }));
    body.querySelector("[data-ws-set-all]")?.addEventListener("click", () => {
      const value = body.querySelector("[data-ws-all-boundary]").value;
      body.querySelectorAll("[data-ws-boundary]").forEach(select => { if (select.value === "unknown") select.value = value; });
      draft.edges = selectedEdges.map(edge => ({index: edge.index, boundary: body.querySelector(`[data-ws-boundary="${edge.index}"]`).value}));
    });
    body.querySelector("[data-ws-save-walls]")?.addEventListener("click", async () => {
      const reviewer = body.querySelector("[data-ws-envelope-reviewer]").value.trim();
      const note = body.querySelector("[data-ws-envelope-status]");
      if (!reviewer) { note.textContent = "Enter your name or initials to save these decisions."; return; }
      storage.set("toki.workspace.name", reviewer);
      if (!roofTab) draft.edges = selectedEdges.map(edge => ({index: edge.index, boundary: body.querySelector(`[data-ws-boundary="${edge.index}"]`).value}));
      if (roofTab) draft.roof = body.querySelector("[data-ws-roof]").value;
      const edges = draft.edges;
      const roof = draft.roof;
      const confirmed = [...new Set([...(draft.accepted_edges || []), ...[...body.querySelectorAll("[data-ws-boundary][data-accepted='true']")].map(select => Number(select.dataset.wsBoundary))])];
      note.textContent = "Saving…";
      try {
        await sendJson("/api/reviewer-room-geometry", classifyPayload(trace, reviewer, {edges, roof,
          confirm_roof: roof !== trace.roof, confirmed_edges: confirmed}));
        delete state.envelopeDrafts[trace.trace_id];
        state.message = "Saved. Calculate to update the result.";
        await refreshEnvelopeUi("walls");
      } catch (error) { note.textContent = error.message; note.classList.add("is-error"); note.setAttribute("role", "alert"); }
    });
    wireCommon();
  }

  function openingSource(opening) { return sourceChip(opening.declaration_source || opening.source); }
  async function renderWindows() {
    const projectId = state.projectId;
    const ctx = state.envelopeCtx || await loadEnvelopeContext();
    if (!live(projectId, "windows")) return;
    const rooms = (ctx.rooms || []).filter(room => envelopeRecords(room.room_id).length);
    if (!state.windowsRoomId || !rooms.some(row => row.room_id === state.windowsRoomId)) state.windowsRoomId = rooms[0]?.room_id || "";
    const room = rooms.find(row => row.room_id === state.windowsRoomId);
    const traces = room ? envelopeRecords(room.room_id) : [];
    if (!state.windowsTraceId || !traces.some(row => row.trace_id === state.windowsTraceId)) state.windowsTraceId = traces[0]?.trace_id || "";
    const trace = selectedEnvelopeTrace(state.windowsRoomId, state.windowsTraceId);
    const page = trace && (ctx.pages || []).find(row => row.page === trace.page);
    const outsideEdges = (trace?.edges || []).filter(edge => ["external", "mall"].includes(edge.boundary));
    const choices = ctx.glazing_choices || {}, shading = ctx.shading_categories || {};
    const openings = trace?.openings || [];
    const rows = openings.map((opening, i) => `<fieldset class="ws-opening" data-ws-opening="${i}"><legend>${esc(opening.opening_id || `Opening ${i + 1}`)} ${openingSource(opening)}</legend>
      <label>Wall<select data-opening-field="edge_index" data-index="${i}">${outsideEdges.map(edge => `<option value="${edge.index}" ${opening.edge_index === edge.index ? "selected" : ""}>Wall ${edge.index + 1} (${BOUNDARY_LABEL[edge.boundary]})</option>`).join("")}</select></label>
      <label>Width (m)<input type="number" min="0.05" step="0.01" value="${esc(opening.width_m)}" data-opening-field="width_m" data-index="${i}"></label>
      <label>Sill (m)<input type="number" min="0" step="0.01" value="${esc(opening.sill_height_m)}" data-opening-field="sill_height_m" data-index="${i}"></label>
      <label>Head (m)<input type="number" min="0.05" step="0.01" value="${esc(opening.head_height_m)}" data-opening-field="head_height_m" data-index="${i}"></label>
      <label>Elevation page<input type="number" min="1" step="1" value="${esc(opening.elevation_page || "")}" data-opening-field="elevation_page" data-index="${i}"></label>
      <label>Glazing<select data-opening-field="glazing_choice" data-index="${i}">${Object.entries(choices).map(([id,item]) => `<option value="${id}" ${opening.glazing_choice === id ? "selected" : ""}>${esc(item.label || id)}</option>`).join("")}</select></label>
      <label>Shading<select data-opening-field="shading_category" data-index="${i}">${Object.entries(shading).map(([id,item]) => `<option value="${id}" ${opening.shading_category === id ? "selected" : ""}>${esc(item.label || id)}</option>`).join("")}</select></label>
      <button class="link-button" type="button" data-ws-remove-opening="${i}">Remove opening</button></fieldset>`).join("");
    const noGlazing = trace?.openings_none_edges || [];
    const outline = trace && page ? `<div class="ws-envelope-plan"><img src="${esc(page.preview_url)}" alt="Plan page ${trace.page}"><svg viewBox="0 0 ${page.image_width_px} ${page.image_height_px}" aria-hidden="true">
      ${trace.points_image_px.slice(0, -1).map((point,i) => { const end=trace.points_image_px[i+1]; return `<line x1="${point[0]}" y1="${point[1]}" x2="${end[0]}" y2="${end[1]}" stroke="${["external","mall"].includes(trace.edges?.[i]?.boundary) ? "#8e44ad" : "#777"}"/>`;}).join("")}</svg></div>` : "";
    body.innerHTML = `<div class="ws-card" data-ws-windows><h2>Windows</h2><p class="ws-hint">Record glazing on outside and enclosed-mall walls. If a wall has no glazing, mark it explicitly.</p>
      ${rooms.length ? `<label>Room <select data-ws-window-room>${rooms.map(row => `<option value="${esc(row.room_id)}" ${row.room_id === state.windowsRoomId ? "selected" : ""}>${esc(row.label)}</option>`).join("")}</select></label>` : `<p>No outlined rooms yet. Measure rooms on the Rooms tab first.</p>`}
      ${traces.length > 1 ? `<label>Outline <select data-ws-window-trace>${traces.map((row,i) => `<option value="${esc(row.trace_id)}" ${row.trace_id === trace?.trace_id ? "selected" : ""}>Page ${row.page} — part ${i+1}</option>`).join("")}</select></label>` : ""}
      ${trace ? `${outline}<h3>Outside and mall walls</h3>${outsideEdges.map(edge => `<label class="ws-check"><input type="checkbox" data-ws-no-glazing="${edge.index}" ${noGlazing.includes(edge.index) ? "checked" : ""}> Wall ${edge.index + 1}: no glazing</label>`).join("") || `<p>No walls are currently classified as outside or enclosed mall. Set them on Walls &amp; roof.</p>`}
        <h3>Openings</h3>${rows}<button class="btn ghost" type="button" data-ws-add-opening ${outsideEdges.length ? "" : "disabled"}>Add opening</button>
        <p class="ws-hint">If a ceiling height is missing, set it on <button class="link-button" type="button" data-ws-go="rooms">Rooms</button>.</p>
        <label>Your name or initials <input data-ws-window-reviewer autocomplete="name" value="${esc(userName())}" required></label>
        <div class="ws-actions"><button class="btn key" type="button" data-ws-save-windows>Save windows</button><span role="status" data-ws-window-status>${esc(state.message)}</span></div>` : ""}${nextButton("windows")}</div>`;
    state.message = "";
    const setRoom = value => { state.windowsRoomId = value; state.windowsTraceId = ""; renderWindows(); };
    body.querySelector("[data-ws-window-room]")?.addEventListener("change", event => setRoom(event.target.value));
    body.querySelector("[data-ws-window-trace]")?.addEventListener("change", event => { state.windowsTraceId = event.target.value; renderWindows(); });
    body.querySelectorAll("[data-opening-field]").forEach(input => input.addEventListener("change", () => {
      const opening = openings[Number(input.dataset.index)];
      opening[input.dataset.openingField] = ["edge_index", "elevation_page"].includes(input.dataset.openingField) ? Number(input.value) :
        ["width_m", "sill_height_m", "head_height_m"].includes(input.dataset.openingField) ? Number(input.value) : input.value;
    }));
    body.querySelectorAll("[data-ws-no-glazing]").forEach(input => input.addEventListener("change", () => {
      const index = Number(input.dataset.wsNoGlazing);
      trace.openings_none_edges = [...new Set([...(trace.openings_none_edges || []).filter(value => value !== index), ...(input.checked ? [index] : [])])];
      if (input.checked) trace.openings = (trace.openings || []).filter(row => row.edge_index !== index);
    }));
    body.querySelectorAll("[data-ws-remove-opening]").forEach(button => button.addEventListener("click", () => {
      trace.openings.splice(Number(button.dataset.wsRemoveOpening), 1); renderWindows();
    }));
    body.querySelector("[data-ws-add-opening]")?.addEventListener("click", () => {
      const existingIds = new Set((trace.openings || []).map(opening => opening.opening_id));
      let nextNumber = 1;
      while (existingIds.has(`Opening ${nextNumber}`)) nextNumber += 1;
      const nextOpenings = [...(trace.openings || []), {opening_id: `Opening ${nextNumber}`, edge_index: outsideEdges[0].index,
        width_m: "", sill_height_m: "", head_height_m: "", elevation_page: "", glazing_choice: Object.keys(choices)[0] || "retail",
        shading_category: Object.keys(shading)[0] || "unshaded"}];
      trace.openings = nextOpenings;
      trace.openings_none_edges = (trace.openings_none_edges || []).filter(index => index !== outsideEdges[0].index);
      renderWindows().catch(showTabError);
    });
    body.querySelector("[data-ws-save-windows]")?.addEventListener("click", async () => {
      const reviewer = body.querySelector("[data-ws-window-reviewer]").value.trim();
      const note = body.querySelector("[data-ws-window-status]");
      if (!reviewer) { note.textContent = "Enter your name or initials to save these decisions."; return; }
      storage.set("toki.workspace.name", reviewer); note.textContent = "Saving…";
      try {
        await sendJson("/api/reviewer-room-geometry", classifyPayload(trace, reviewer, {openings: trace.openings || [],
          openings_none_edges: trace.openings_none_edges || []}));
        state.message = "Saved. Calculate to update the result.";
        await refreshEnvelopeUi("windows");
      } catch (error) {
        note.textContent = error.message;
        note.insertAdjacentHTML("afterend", `<p class="ws-error" role="alert">${esc(error.message)}</p>${/ceiling height/i.test(error.message) ? '<button class="link-button" type="button" data-ws-go="rooms">Set ceiling height on Rooms</button>' : ""}`);
        wireCommon();
      }
    });
    wireCommon();
  }

  // ------------------------------------------------------------------ routing, enter and leave
  function parseHash() {
    const match = location.hash.match(/^#\/job\/([^/]+)(?:\/([a-z]+))?(?:\/([a-z-]+))?\/?$/);
    return match ? {projectId: decodeURIComponent(match[1]), tab: match[2] || "", subtab: match[3] || ""} : null;
  }

  function defaultTab() {
    const tabs = state.status?.tabs || {};
    if (params.get("operator") === "1") return "drawings";
    if (tabs.results?.state === "done") return "results";
    return TABS.find(tab => ["needed", "working"].includes(tabs[tab.id]?.state) && tab.id !== "project")?.id || "rooms";
  }

  async function enter(data) {
    if (!enabled || state.engineer) return;
    const projectId = data?.id || DATA?.id;
    if (!projectId) return;
    if (state.projectId !== projectId) {
      clearTimeout(state.poll);
      body.innerHTML = "";
      Object.assign(state, {projectId, status: null, tab: "", subtab: "", include: new Map(), message: "", lastResult: null, roomsModel: null,
        projectDraft: null, envelopeDrafts: {}});
      const note = document.getElementById("wsCalcNote");
      if (note) note.textContent = "";
    }
    if (data?.sheets) state.analysis = data;
    state.active = true;
    document.body.classList.add("ws-open");
    show("vJob");
    requiredElement("topTitle").textContent = "Job";
    requiredElement("topSub").textContent = data?.name || DATA?.name || "";
    requiredElement("btnRestart").classList.add("hide");
    document.getElementById("btnSimpleView")?.classList.add("hide");
    renderRail();
    try { await loadStatus(); } catch (_) { /* the tab shows its own error */ }
    if (state.projectId !== projectId) return;
    resumeCalculation(projectId);
    const route = parseHash();
    const tab = route?.projectId === projectId && TABS.some(row => row.id === route.tab) ? route.tab : defaultTab();
    const subtab = defaultSubtab(tab, route?.subtab || "");
    if (route?.projectId === projectId && route.tab === tab && route.subtab === subtab) {
      state.tab = tab; state.subtab = subtab; renderTab();
    } else location.replace(`#/job/${encodeURIComponent(projectId)}/${tab}${subtab ? `/${subtab}` : ""}`);
  }

  function leave() {
    state.active = false;
    state.engineer = true;
    clearTimeout(state.poll);
    document.body.classList.remove("ws-open");
    if (state.analysis) showResults(state.analysis); else show("vRes");
    requiredElement("topTitle").textContent = "Engineer review";
    if (enabled) document.getElementById("btnSimpleView")?.classList.remove("hide");
  }

  window.addEventListener("hashchange", () => {
    const route = parseHash();
    if (!route || !enabled) return;
    if (route.projectId !== state.projectId || !state.active) {
      state.engineer = false;
      if (typeof openProject === "function") openProject(route.projectId);
      return;
    }
    if (TABS.some(row => row.id === route.tab)) {
      const nextSubtab = defaultSubtab(route.tab, route.subtab || "");
      if (route.subtab !== nextSubtab && TABS.find(row => row.id === route.tab)?.subtabs?.length) {
        location.replace(`#/job/${encodeURIComponent(state.projectId)}/${route.tab}/${nextSubtab}`);
        return;
      }
      if (route.tab !== state.tab || nextSubtab !== state.subtab) {
        state.tab = route.tab;
        state.subtab = nextSubtab;
        renderTab();
      } else if (!body.firstElementChild) renderTab();
    }
  });

  document.getElementById("wsCalculate")?.addEventListener("click", calculate);
  document.getElementById("wsSectionSelect")?.addEventListener("change", event => go(event.target.value));
  document.getElementById("btnJobEngineer")?.addEventListener("click", leave);
  document.getElementById("wsAllProjects")?.addEventListener("click", () => {
    state.active = false;
    document.body.classList.remove("ws-open");
    history.replaceState(null, "", location.pathname + location.search);
    reset();
  });
  document.getElementById("btnSimpleView")?.addEventListener("click", () => { state.engineer = false; enter(DATA); });

  // Opening a job address directly (bookmark, refresh, or the "Toki team" link with ?operator=1).
  const initial = parseHash();
  if (initial && typeof openProject === "function") setTimeout(() => openProject(initial.projectId), 0);

  window.JobWorkspace = {
    enabled,
    isActive: () => state.active,
    takesOver: () => enabled && !state.engineer,
    onProjectShown(data) { if (enabled && !state.engineer) enter(data); },
    onProjectLeft() {
      state.active = false;
      clearTimeout(state.poll);
      document.body.classList.remove("ws-open");
      document.getElementById("btnSimpleView")?.classList.add("hide");
    },
  };
})();

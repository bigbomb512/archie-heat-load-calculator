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
  if (!root || !body || !rail) return;

  const TABS = [
    {id: "project", label: "Project"},
    {id: "drawings", label: "Drawings"},
    {id: "rooms", label: "Rooms"},
    {id: "results", label: "Results"},
  ];
  const STATE_TEXT = {done: "Done", check: "Check", needed: "Needed", working: "Working", todo: "Not yet"};
  const STATE_ICON = {done: "✓", check: "●", needed: "!", working: "◌", todo: "○"};
  const BUILDING_TYPES = [["", "Choose…"], ["food_tenancy", "Food tenancy (café, restaurant)"], ["retail", "Retail shop"],
                          ["office", "Office"], ["medical", "Medical / consulting"], ["other", "Other"]];
  const POLL_MS = 15000;
  const state = {projectId: null, analysis: null, status: null, tab: "project", active: false, engineer: false,
                 include: new Map(), poll: null, busy: new Set(), message: ""};

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
      const info = status.tabs?.[tab.id] || {state: "todo"};
      const current = tab.id === state.tab;
      return `<li><a href="#/job/${encodeURIComponent(state.projectId)}/${tab.id}" class="ws-tab is-${esc(info.state)}${current ? " is-current" : ""}"
        data-ws-tab="${tab.id}" ${current ? 'aria-current="page"' : ""}>
        <span class="ws-tab-icon" aria-hidden="true">${STATE_ICON[info.state] || "○"}</span>
        <span class="ws-tab-text"><b>${esc(tab.label)}</b><small>${esc(info.detail || STATE_TEXT[info.state] || "")}</small></span>
        ${info.detail ? `<span class="visually-hidden">${esc(STATE_TEXT[info.state] || "")}</span>` : ""}</a></li>`;
    }).join("");
    const select = document.getElementById("wsSectionSelect");
    select.innerHTML = TABS.map(tab => `<option value="${tab.id}" ${tab.id === state.tab ? "selected" : ""}>${esc(tab.label)} — ${esc(STATE_TEXT[status.tabs?.[tab.id]?.state] || "Not yet")}</option>`).join("");
    document.getElementById("wsTotal").textContent = kw(status.total_kw);
    document.getElementById("wsTotalNote").textContent = status.total_kw == null ? "Not calculated yet"
      : status.result_stale ? "Out of date — calculate again" : "Draft total cooling";
  }

  function go(tab) {
    location.hash = `#/job/${encodeURIComponent(state.projectId)}/${tab}`;
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
    const render = {project: renderProject, drawings: renderDrawings, rooms: renderRooms, results: renderResults}[tab] || renderProject;
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
    const found = status.found_site;
    body.innerHTML = `<div class="ws-card" data-ws-project>
      <h2>Project</h2>
      <p class="ws-hint">Tell us what you know about the job. Anything you leave blank, Toki works out from the drawings where it can.</p>
      <form class="ws-form" data-ws-project-form>
        <label>Job name<input name="name" autocomplete="off" value="${esc(status.name)}"></label>
        <label>Site address<input name="address" autocomplete="street-address" placeholder="Street, suburb, state" value="${esc(status.address)}"></label>
        ${found && !status.address ? `<p class="ws-found">Found on the drawings: <b>${esc(found)}</b> <button class="link-button" type="button" data-ws-use-found>Use this</button></p>` : ""}
        <label>Building type<select name="building_type">${BUILDING_TYPES.map(([value, label]) => `<option value="${value}" ${status.building_type === value ? "selected" : ""}>${esc(label)}</option>`).join("")}</select></label>
        <fieldset><legend>What's above this tenancy?</legend>
          ${[["roof", "The roof"], ["floor", "Another floor or tenancy"], ["not_sure", "Not sure"]].map(([value, label]) =>
            `<label class="ws-radio"><input type="radio" name="above" value="${value}" ${status.above === value ? "checked" : ""}> ${label}</label>`).join("")}
        </fieldset>
        <label>Your name <small>(optional; recorded with your changes)</small><input name="person" autocomplete="name" value="${esc(userName())}"></label>
        <div class="ws-actions"><button class="btn key" type="submit">Save</button><span class="ws-status" role="status" data-ws-project-status>${esc(state.message)}</span></div>
      </form>
      <p class="ws-fine">Weather and sun: a generic Australian design day for now. Site-specific weather needs the AIRAH design data, which isn't connected yet.</p>
      ${nextButton("project")}</div>`;
    state.message = "";
    const form = body.querySelector("[data-ws-project-form]");
    body.querySelector("[data-ws-use-found]")?.addEventListener("click", () => { form.elements.address.value = found; });
    form.addEventListener("submit", async event => {
      event.preventDefault();
      const line = body.querySelector("[data-ws-project-status]");
      storage.set("toki.workspace.name", form.elements.person.value.trim());
      line.textContent = "Saving…";
      try {
        state.status = await sendJson("/api/job-setup", {project_id: projectId, name: form.elements.name.value,
          address: form.elements.address.value, building_type: form.elements.building_type.value,
          above: form.elements.above.value || "", edited_by: userName()});
        renderRail();
        line.textContent = "Saved.";
      } catch (error) {
        line.textContent = `Could not save: ${error.message}`;
      }
    });
    wireCommon();
  }

  // ------------------------------------------------------------------ Drawings
  // Preparing the pages runs once per job at a time; a re-render (tab switch, refresh) waits for the same run.
  function preparePages(projectId, title, rebuildChecks) {
    if (state.preparing?.projectId !== projectId) {
      clearTimeout(state.poll);
      const promise = (async () => {
        await confirmSelection();
        if (requiredElement("btnContinue").textContent !== "Drawings confirmed") {
          throw new Error("The drawing pages could not be prepared. Engineer review shows which pages are included.");
        }
        DATA.has_reasoning_packet = true;
        if (state.analysis) state.analysis.has_reasoning_packet = true;
        if (rebuildChecks) await sendJson("/api/autonomous-tasks", {project_id: projectId, action: "run_all"});
      })().finally(() => { if (state.preparing?.promise === promise) state.preparing = null; });
      state.preparing = {projectId, title, promise};
    }
    return state.preparing;
  }

  async function renderDrawings() {
    const projectId = state.projectId;
    if (state.preparing?.projectId === projectId || !DATA?.has_reasoning_packet) {
      const run = state.preparing?.projectId === projectId ? state.preparing : preparePages(projectId, "Preparing your drawing pages", false);
      progress(run.title, "Getting the drawing pages ready. This can take five to seven minutes on a large set; keep this page open until it finishes.");
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
    const pct = checks.total ? Math.round((done / checks.total) * 100) : 0;
    const blockedNote = checks.blocked ? ` ${checks.blocked} more start${checks.blocked === 1 ? "s" : ""} when earlier checks are answered.` : "";
    body.innerHTML = `<div class="ws-card" data-ws-drawings>
      <h2>Drawings</h2>
      <h3>Drawing check</h3>
      <div class="ws-meter" role="progressbar" aria-label="Drawing check" aria-valuemin="0" aria-valuemax="${checks.total}" aria-valuenow="${done}"><span style="width:${pct}%"></span></div>
      <p data-ws-checks><b>${done} of ${checks.total} checks done.</b>
        ${checks.waiting ? (operatorMode() ? `Answer them below, one at a time.${blockedNote}`
            : `Toki is reading rooms, walls, windows and equipment from the drawings. For now the Toki team completes this step; this page updates by itself.${blockedNote}`)
          : checks.blocked ? `${checks.blocked} check${checks.blocked === 1 ? "" : "s"} couldn't run; Engineer review has the details.` : "All checks are done."}</p>
      ${checks.waiting && !operatorMode() ? `<p class="ws-fine">You don't have to wait: if you know the room areas, type them on the Rooms tab.
        <button class="link-button" type="button" data-ws-operator-on>Toki team: answer the checks here</button></p>` : ""}
      ${tasks ? operatorMarkup(tasks) : ""}
      ${pagesMarkup()}
      ${nextButton("drawings")}</div>`;
    wireCommon();
    wirePages(projectId);
    body.querySelector("[data-ws-operator-on]")?.addEventListener("click", () => { setOperatorMode(true); renderDrawings().catch(showTabError); });
    if (tasks) wireOperator(projectId, tasks);
    // The contractor's view refreshes itself; the operator's doesn't, so a half-pasted reply is never wiped.
    if (checks.waiting && !operatorMode()) state.poll = setTimeout(() => { if (live(projectId, "drawings")) renderDrawings().catch(() => {}); }, POLL_MS);
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
    return `<h3>Pages used</h3>
      <p class="ws-hint">${PICK.size} of ${sheets.length} pages are used. The main plans (${main.map(sheet => sheet.page).join(", ") || "none"}) are measured; the rest are read for reference. Untick a page that isn't part of this job.</p>
      <ul class="ws-pages" data-ws-pages>${shown.map(sheet => `<li class="${sheet.relevant ? "is-main" : ""}">
        <label><input type="checkbox" data-ws-page="${esc(sheet.page)}" ${picked.has(sheet.page) ? "checked" : ""}>
          ${sheet.thumbnail ? `<img src="${esc(sheet.thumbnail)}" alt="" loading="lazy">` : `<span class="ws-page-blank" aria-hidden="true"></span>`}
          <span><b>Page ${esc(sheet.page)}</b>${sheet.relevant ? " · main plan" : ""}<small>${esc(title(sheet))}</small></span></label></li>`).join("")}</ul>
      <div class="ws-actions">
        <button class="link-button" type="button" data-ws-all-pages>${state.showAllPages ? "Show only the pages used" : `Show all ${sheets.length} pages`}</button>
        ${changed ? `<button class="btn key" type="button" data-ws-use-pages>Use these pages</button><span class="ws-fine">Takes five to seven minutes; the drawing check is rebuilt for the new pages, and answered checks are kept where the pages didn't change.</span>` : ""}
        <span class="ws-status" role="status" data-ws-pages-status></span>
      </div>`;
  }

  function wirePages(projectId) {
    body.querySelector("[data-ws-all-pages]")?.addEventListener("click", () => { state.showAllPages = !state.showAllPages; renderDrawings().catch(showTabError); });
    body.querySelectorAll("[data-ws-page]").forEach(box => box.addEventListener("change", () => {
      state.pagePick = state.pagePick || new Set(PICK);
      const page = Number(box.dataset.wsPage);
      box.checked ? state.pagePick.add(page) : state.pagePick.delete(page);
      renderDrawings().catch(showTabError);
    }));
    body.querySelector("[data-ws-use-pages]")?.addEventListener("click", async () => {
      if (!state.pagePick?.size) { body.querySelector("[data-ws-pages-status]").textContent = "Keep at least one page."; return; }
      PICK = new Set(state.pagePick);
      state.pagePick = null;
      preparePages(projectId, "Updating the drawing pages", true);
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
    if (origin === "reviewer_traced") return "Traced";
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
    const rows = (model.room_scope?.candidates || []).map(row => typed.has(row.key)
      ? {...row, area_m2: typed.get(row.key).area_m2, area_origin: "edited"} : row);
    const known = new Set(rows.map(row => row.key));
    for (const row of typed.values()) {  // added in the workspace; in the model after the next Calculate
      if (!known.has(row.room_id)) rows.push({key: row.room_id, label: row.room_label, level: row.level_name,
                                              area_m2: row.area_m2, area_origin: "edited", include: true, status: "added"});
    }
    return rows;
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
    body.innerHTML = `<div class="ws-card" data-ws-rooms>
      <h2>Rooms</h2>
      <p class="ws-hint">Check the area of each room. Type an area or ceiling height to change it (it's marked "Edited by you"), clear it to go back to the drawing value, and untick rooms that aren't cooled.</p>
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
            <td><button class="link-button" type="button" data-ws-trace>Trace on the plan</button></td></tr>`;
        }).join("") || `<tr><td colspan="7">No rooms were found on the drawings yet. They appear here once the drawing check has read the room names; Engineer review can trace them meanwhile.</td></tr>`}</tbody></table></div>
      <form class="ws-add" data-ws-add><b>Add a room the drawings missed</b>
        <label>Name<input name="label" required autocomplete="off"></label>
        <label>Used as<select name="use" required><option value="">Choose…</option>${uses.map(([id, label]) => `<option value="${esc(id)}">${esc(label)}</option>`).join("")}</select></label>
        <label>Area (m²)<input name="area" type="number" min="0.1" step="0.1" required></label>
        <button class="btn ghost" type="submit">Add</button></form>
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
      row.querySelector("[data-ws-include]").addEventListener("change", event => {
        state.include.set(key, event.target.checked);
        status.textContent = event.target.checked ? `${label} will be cooled.` : `${label} won't be included.`;
      });
      row.querySelector("[data-ws-area]").addEventListener("change", async event => {
        const raw = event.target.value.trim();
        const area = raw === "" ? null : Number(raw);
        if (area !== null && !(area > 0)) { status.textContent = "Type the area as a number of m², or clear it."; return; }
        status.textContent = `Saving ${label}…`;
        try {
          const saved = await sendJson("/api/room-area-override", {project_id: projectId, room_id: key, label, level_name: row.dataset.level,
                                                                   area_m2: area, edited_by: userName()});
          if (area !== null) state.include.set(key, true);
          await after(area === null ? `${label}: back to the drawing value.` : `${label}: ${area} m² saved.`, saved);
        } catch (error) { status.textContent = `Could not save ${label}: ${error.message}`; }
      });
      row.querySelector("[data-ws-height]").addEventListener("change", async event => {
        const raw = event.target.value.trim();
        const metres = raw === "" ? null : Number(raw);
        if (metres !== null && !(metres >= 1.8 && metres <= 15)) { status.textContent = "Type the ceiling height in metres (1.8 to 15), or clear it."; return; }
        status.textContent = `Saving ${label}…`;
        try {
          const saved = await sendJson("/api/room-height-override", {project_id: projectId, room_key: key, label, level_name: row.dataset.level,
                                                                     ceiling_height_mm: metres === null ? null : Math.round(metres * 1000), edited_by: userName()});
          await after(metres === null ? `${label}: ceiling height back to the drawing value.` : `${label}: ceiling height ${metres} m saved.`, saved);
        } catch (error) { status.textContent = `Could not save ${label}: ${error.message}`; }
      });
      row.querySelector("[data-ws-use]")?.addEventListener("change", async event => {
        if (!event.target.value) return;
        status.textContent = `Saving the use of ${label}…`;
        try {
          await sendJson("/api/room-use-resolution", {project_id: projectId, action: "apply_override", room_id: key,
            taxonomy_id: event.target.value, reviewer: userName() || "Contractor", note: "Use chosen in the job workspace."});
          state.roomsModel = null;
          await after(`Use saved for ${label}.`);
        } catch (error) { status.textContent = `Could not save the use: ${error.message}`; }
      });
      row.querySelector("[data-ws-trace]").addEventListener("click", () => { leave(); openRoomForTracing(key); });
    });
    body.querySelector("[data-ws-add]").addEventListener("submit", async event => {
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
      } catch (error) { status.textContent = `Could not add the room: ${error.message}`; }
    });
  }

  // ------------------------------------------------------------------ Calculate
  async function calculate() {
    const projectId = state.projectId;
    const button = document.getElementById("wsCalculate");
    const note = document.getElementById("wsCalcNote");
    if (!state.status?.rooms?.with_area) {
      note.textContent = "Add room areas first.";
      go("rooms");
      return;
    }
    button.disabled = true;
    const reviewer = userName() || "Contractor";
    const confirmAndCalculate = async () => {
      // Rebuild the draft model so typed areas and added rooms are in it.
      const model = await sendJson("/api/ai-preliminary-model", {project_id: projectId, action: "assemble", settings: aiPreliminarySettings(), response_view: "workspace"});
      state.roomsModel = model;
      const rows = (model.room_scope?.candidates || []).map(row => {
        const include = (state.include.has(row.key) ? state.include.get(row.key) : (row.include || row.area_m2 != null)) && row.area_m2 != null;
        return {key: row.key, include, reason: include ? "" : "Not cooled"};
      });
      await sendJson("/api/ai-preliminary-model", {project_id: projectId, action: "confirm_room_scope", reviewer, response_view: "workspace",
        candidate_fingerprint: model.room_scope?.candidate_fingerprint || "", rows, settings: aiPreliminarySettings()});
      return sendJson("/api/ai-preliminary-model", {project_id: projectId, action: "calculate", settings: aiPreliminarySettings(), response_view: "workspace"});
    };
    try {
      if (state.status.above) await sendJson("/api/job-setup", {project_id: projectId});  // answers any new roof questions
      note.textContent = "Calculating… this can take a minute.";
      let result;
      try {
        result = await confirmAndCalculate();
      } catch (error) {
        if (!/missing required|out of date|resolve model inputs|stale/i.test(error.message)) throw error;
        note.textContent = "Preparing the calculation: room uses, heights, people and equipment. About two to three minutes the first time.";
        await guidedResolveModelInputs();
        if (!/Coverage hydrated/.test(requiredElement("guidedModelInputsStatus").textContent)) {
          throw new Error("The inputs couldn't be prepared. Check that every room you want cooled has an area.");
        }
        note.textContent = "Calculating…";
        result = await confirmAndCalculate();
      }
      if (typeof drawAiPreliminary === "function") drawAiPreliminary(result);
      note.textContent = "";
      await loadStatus();
      if (state.projectId === projectId) {
        state.lastResult = result;
        if (state.tab === "results") renderTab(); else go("results");
      }
    } catch (error) {
      note.textContent = `Could not calculate: ${error.message}`;
    } finally {
      button.disabled = false;
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
    // The specific "no outline" walls and roof lines already say why the envelope is missing for those rooms.
    const envelope = grouped.get(groups.envelope);
    if (envelope) {
      const covered = new Set([...(grouped.get(prefixes.area_only_walls) || []), ...(grouped.get(prefixes.area_only_roof) || [])]);
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

  // ------------------------------------------------------------------ routing, enter and leave
  function parseHash() {
    const match = location.hash.match(/^#\/job\/([^/]+)(?:\/([a-z]+))?/);
    return match ? {projectId: decodeURIComponent(match[1]), tab: match[2] || ""} : null;
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
      Object.assign(state, {projectId, status: null, tab: "", include: new Map(), message: "", lastResult: null, roomsModel: null});
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
    const route = parseHash();
    const tab = route?.projectId === projectId && TABS.some(row => row.id === route.tab) ? route.tab : defaultTab();
    if (route?.projectId === projectId && route.tab === tab) { state.tab = tab; renderTab(); }
    else location.replace(`#/job/${encodeURIComponent(projectId)}/${tab}`);
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
    if (TABS.some(row => row.id === route.tab) && route.tab !== state.tab) { state.tab = route.tab; renderTab(); }
    else if (route.tab === state.tab && !body.firstElementChild) renderTab();
  });

  // Leaving while pages are prepared would stop the browser from finishing the preparation.
  window.addEventListener("beforeunload", event => { if (state.preparing) { event.preventDefault(); event.returnValue = ""; } });

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

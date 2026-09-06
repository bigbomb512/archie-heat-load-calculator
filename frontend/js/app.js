function requiredElement(id){
  const element = document.getElementById(id);
  if (!element) throw new Error(`Frontend template is missing #${id}`);
  return element;
}

function optionalElement(id){
  return document.getElementById(id);
}
let DATA = null, FILTER = "rel", PICK = new Set(), CUR = null, DEBUG = false, PACKET = null, ROOM_SUGGESTIONS = [];

/* ---- theme -------------------------------------------------------------
   Dark by default. A saved choice wins; otherwise follow the system. The
   attribute is what CSS keys off, so the whole palette swaps in one place. */
const root = document.documentElement;
const THEME_KEY = "archie-theme";

function applyTheme(name){
  root.setAttribute("data-theme", name);
  requiredElement("theme").setAttribute("aria-label",
    name === "dark" ? "Switch to light mode" : "Switch to dark mode");
}

applyTheme(localStorage.getItem(THEME_KEY) ||
  (window.matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark"));

requiredElement("theme").addEventListener("click", () => {
  const next = root.getAttribute("data-theme") === "dark" ? "light" : "dark";
  applyTheme(next);
  localStorage.setItem(THEME_KEY, next);
});

/* Follow the system only while the user has not chosen for themselves. */
window.matchMedia("(prefers-color-scheme: light)").addEventListener("change", (event) => {
  if (!localStorage.getItem(THEME_KEY)) applyTheme(event.matches ? "light" : "dark");
});

/* ---------------- upload ---------------- */
const drop = requiredElement("drop");
["dragenter","dragover"].forEach(e => drop.addEventListener(e, ev => { ev.preventDefault(); drop.classList.add("hot"); }));
["dragleave","drop"].forEach(e => drop.addEventListener(e, ev => { ev.preventDefault(); drop.classList.remove("hot"); }));
drop.addEventListener("drop", ev => { const f = ev.dataTransfer.files[0]; if (f) upload(f); });
requiredElement("pdf").addEventListener("change", ev => { if (ev.target.files[0]) upload(ev.target.files[0]); });
requiredElement("navNew").addEventListener("click", reset);
requiredElement("btnRestart").addEventListener("click", reset);

function reset(){ location.reload(); }

function upload(file){
  if (!/\.pdf$/i.test(file.name)) return toast("Not a PDF", "Upload the drawing set as a PDF file.");
  show("vFile"); requiredElement("btnRestart").classList.remove("hide");
  requiredElement("fName").textContent = file.name;
  requiredElement("fMeta").textContent = (file.size/1048576).toFixed(1) + " MB";
  requiredElement("fState").textContent = "Uploading";
  requiredElement("topTitle").textContent = "New analysis";
  requiredElement("topSub").textContent = file.name;

  const body = new FormData(); body.append("pdf", file, file.name);
  const xhr = new XMLHttpRequest();
  xhr.open("POST", "/api/upload");
  xhr.upload.addEventListener("progress", e => {
    if (e.lengthComputable) requiredElement("fBar").style.width = Math.round(e.loaded/e.total*100) + "%";
  });
  xhr.addEventListener("load", () => {
    let res; try { res = JSON.parse(xhr.responseText); } catch { res = {}; }
    if (xhr.status >= 400 || res.error) return failUpload(res.error || "The server could not accept that file.");
    requiredElement("fBar").style.width = "100%";
    requiredElement("fState").textContent = "Ready";
    requiredElement("fMeta").textContent = `${res.pages} pages · ${(res.size_bytes/1048576).toFixed(1)} MB`;
    DATA = { id: res.id, name: res.name, pages: res.pages };
    requiredElement("btnAnalyse").classList.remove("hide");
    requiredElement("btnAnalyse").disabled = false;
    loadProjects();
  });
  xhr.addEventListener("error", () => failUpload("The upload did not reach the server."));
  xhr.send(body);
}

function failUpload(msg){
  show("vUpload"); requiredElement("btnRestart").classList.add("hide");
  toast("Upload failed", msg);
}

/* ---------------- analyse ---------------- */
requiredElement("btnAnalyse").addEventListener("click", analyse);

async function analyse(){
  if (!DATA) return;
  show("vRun");
  requiredElement("btnAnalyse").disabled = true;
  requiredElement("runSub").textContent = `Reviewing ${DATA.pages} pages`;
  requiredElement("topTitle").textContent = "Analysing";
  const stop = runSteps();
  paintSpectrum([], DATA.pages, true);

  try {
    const res = await fetch("/api/analyse", {
      method:"POST", headers:{"Content-Type":"application/json"},
      body: JSON.stringify({ id: DATA.id }),
    });
    const data = await res.json();
    if (!res.ok || data.error) throw new Error(data.error || "Analysis failed.");
    stop();
    try {
      showResults(data);
    } catch (error) {
      console.error("Analysis display failed", { error, stage: "showResults" });
      toast("Analysis display failed", "The PDF was analysed, but the page could not render. Refresh and try again.");
    }
  } catch (err) {
    stop(); show("vFile"); requiredElement("btnAnalyse").disabled = false;
    toast("Analysis failed", err.message);
  }
}

function runSteps(){
  // Advance through the stages, but hold on the last one rather than ticking it
  // off early — the run is not finished until the server says so.
  const ids = ["s1","s2","s3","s4"]; let i = 0;
  ids.forEach(id => requiredElement(id).className = "step");
  requiredElement("s1").className = "step now";
  const t = setInterval(() => {
    if (i >= ids.length - 1) return clearInterval(t);
    requiredElement(ids[i]).className = "step done";
    i++;
    requiredElement(ids[i]).className = "step now";
  }, 1600);
  return () => { clearInterval(t); ids.forEach(id => requiredElement(id).className = "step done"); };
}

/* ---------------- results ---------------- */
function showResults(data){
  DATA = Object.assign({}, DATA, data);
  PACKET = data.chatgpt_packet || null;
  PICK = new Set(data.sheets.filter(s => s.selected_by_default || s.relevant).map(s => s.page));
  FILTER = "rel";
  DEBUG = false;
  show("vRes");
  requiredElement("btnAnalyse").classList.add("hide");
  requiredElement("topTitle").textContent = "Analysis complete";
  requiredElement("topSub").textContent = data.name;
  requiredElement("rTotal").textContent = data.pages_analysed;
  requiredElement("rRel").textContent = data.relevant_count;
  requiredElement("debugPanel").classList.add("hide");
  requiredElement("visionPanel").classList.add("hide");
  requiredElement("designRequirementsPanel").classList.add("hide");
  requiredElement("visionLinks").innerHTML = "";
  requiredElement("visionStatus").textContent = "Waiting for vision JSON";
  requiredElement("btnDebug").textContent = "Open debug view";
  requiredElement("fRel").classList.add("on"); requiredElement("fAll").classList.remove("on");
  requiredElement("btnConfirm").disabled = false;
  requiredElement("btnConfirmTop").classList.remove("hide");
  drawSummary(); drawReviewList(); drawGrid(); drawAside(); loadProjects();
  if (PACKET?.zip || PACKET?.prompt) showVisionPanel();
  if (data.has_reasoning_packet) showDesignRequirements(data.design_requirements);
}

requiredElement("fRel").addEventListener("click", () => { FILTER = "rel"; requiredElement("fRel").classList.add("on"); requiredElement("fAll").classList.remove("on"); drawGrid(); });
requiredElement("fAll").addEventListener("click", () => { FILTER = "all"; requiredElement("fAll").classList.add("on"); requiredElement("fRel").classList.remove("on"); drawGrid(); });
requiredElement("btnDebug").addEventListener("click", toggleDebug);
requiredElement("btnContinue").addEventListener("click", confirmSelection);

function toggleDebug(){
  DEBUG = !DEBUG;
  requiredElement("debugPanel").classList.toggle("hide", !DEBUG);
  requiredElement("btnDebug").textContent = DEBUG ? "Hide debug view" : "Open debug view";
  if (DEBUG) drawGrid();
}

function drawSummary(){
  const selected = DATA.sheets.filter(s => PICK.has(s.page));
  const floorPlans = selected.filter(s => s.plan_role === "main_floor_plan").length;
  const rcps = selected.filter(s => s.type === "reflected_ceiling_plan").length;
  const supportPages = selected.filter(s => isSupportingContext(s)).length;
  const dimensions = selected.reduce((total, s) => total + (s.dimension_count || 0), 0);
  const rooms = selected.reduce((total, s) => total + (s.room_count || 0), 0);
  const reviewItems = reviewIssues();

  requiredElement("sumPlans").textContent = floorPlans + rcps;
  requiredElement("sumDimensions").textContent = dimensions;
  requiredElement("sumRooms").textContent = rooms;
  requiredElement("scaleStatus").textContent = scaleSummary(selected, dimensions);

  if (!selected.length){
    requiredElement("statusText").textContent = "Needs page selection";
    requiredElement("statusSub").textContent = "No drawings were selected automatically.";
    requiredElement("summaryTitle").textContent = "Needs page selection";
    requiredElement("summaryLead").textContent = "Open debug view and include the drawings required for HVAC design.";
    requiredElement("nextActionTitle").textContent = "Select at least one useful drawing";
    requiredElement("nextActionText").textContent = "The AI stage needs confirmed floor plans or RCPs before it can continue.";
    return;
  }
  if (!selected.some(s => s.relevant)){
    requiredElement("statusText").textContent = "Needs drawing selection";
    requiredElement("statusSub").textContent = "Only supporting context was selected automatically.";
    requiredElement("summaryTitle").textContent = "Needs top-down drawings";
    requiredElement("summaryLead").textContent = "A legend or schedule can help ChatGPT decode symbols, but it cannot replace the floor plan, RCP, or HVAC drawing.";
    requiredElement("nextActionTitle").textContent = "Select a design drawing";
    requiredElement("nextActionText").textContent = "Open debug view and include the useful top-down plan pages before creating the ChatGPT packet.";
    return;
  }

  requiredElement("statusText").textContent = reviewItems.length ? "Needs checking" : "Ready for AI packet";
  requiredElement("statusSub").textContent = reviewItems.length
    ? `${reviewItems.length} item${reviewItems.length === 1 ? "" : "s"} should be checked before AI use.`
    : "The selected drawings are ready for a ChatGPT upload packet.";
  requiredElement("summaryTitle").textContent = reviewItems.length ? "Review required before AI" : "Ready for ChatGPT packet";
  requiredElement("summaryLead").textContent = supportPages
    ? `Archie found the core drawing context plus ${supportPages} supporting legend/schedule page${supportPages === 1 ? "" : "s"}.`
    : "Archie found the core drawing context and hid the page-by-page evidence in debug view.";
  requiredElement("nextActionTitle").textContent = "Create ChatGPT packet";
  requiredElement("nextActionText").textContent = "This creates spatial OCR, rebuilds the AI packet, copies selected screenshots, and prepares a prompt you can upload to ChatGPT.";
}

function scaleSummary(selected, dimensions){
  const scales = [...new Set(selected.map(s => s.scale).filter(Boolean))];
  if (scales.length === 1) return `Scale found: ${scales[0]}`;
  if (scales.length > 1) return "Multiple scales found; confirm per page.";
  if (dimensions) return "No scale found, but direct dimensions are present.";
  return "No scale or direct dimensions found yet.";
}

function reviewIssues(){
  if (!DATA) return [];
  const issues = [];
  const selected = DATA.sheets.filter(s => PICK.has(s.page));
  if (!selected.length){
    issues.push({title:"No selected drawings", detail:"Open debug view and include the useful floor plan or RCP pages.", page:null, action:"Open debug view"});
  }
  if (selected.length && !selected.some(s => s.relevant)){
    issues.push({title:"No selected design drawings", detail:"Supporting legends and schedules need at least one floor plan, RCP, or HVAC plan.", page:null, action:"Open debug view"});
  }

  selected.forEach(s => {
    if (s.plan_role === "main_floor_plan" && s.level_status === "needs_confirmation"){
      issues.push({title:`Page ${s.page}: floor level unclear`, detail:"Confirm which floor this top-down plan belongs to.", page:s.page, action:"View page"});
    }
    if (!s.scale && !s.dimension_count && (s.type === "floor_plan" || s.type === "reflected_ceiling_plan")){
      issues.push({title:`Page ${s.page}: scale or dimensions unclear`, detail:"No scale or written dimensions were extracted from this drawing.", page:s.page, action:"View page"});
    }
    if ((s.confidence || 0) < 0.72){
      issues.push({title:`Page ${s.page}: low confidence`, detail:"This page was included but should be checked before AI design use.", page:s.page, action:"View page"});
    }
  });

  DATA.sheets.filter(s => s.kept_for_review && !PICK.has(s.page) && s.review_bucket === "unclassified").slice(0, 5).forEach(s => {
    issues.push({title:`Page ${s.page}: possible context not selected`, detail:"The app could not prove this page was irrelevant. Check it only if the result seems incomplete.", page:s.page, action:"View page"});
  });

  (DATA.warnings || []).forEach(w => issues.push({title:"Document warning", detail:w, page:null, action:""}));
  return issues;
}

function drawReviewList(){
  const issues = reviewIssues();
  requiredElement("reviewList").innerHTML = issues.length ? issues.map(item => `
    <article class="review-item">
      <div>
        <b>${esc(item.title)}</b>
        <span>${esc(item.detail)}</span>
      </div>
      ${item.page ? `<button class="btn ghost mini" data-review-page="${item.page}">${esc(item.action)}</button>` : ""}
    </article>`).join("") : `
    <div class="review-empty">
      <b>No urgent checks found</b>
      <span>You can create the ChatGPT packet, or open debug view to inspect the page evidence.</span>
    </div>`;

  requiredElement("reviewList").querySelectorAll("[data-review-page]").forEach(button =>
    button.addEventListener("click", () => zoom(+button.dataset.reviewPage)));
}

function visible(){
  return FILTER === "all" ? DATA.sheets : DATA.sheets.filter(s => s.kept_for_review || PICK.has(s.page));
}

function drawGrid(){
  const rows = visible();
  requiredElement("grid").innerHTML = rows.length ? rows.map((s, i) => {
    const on = PICK.has(s.page);
    return `<article class="card ${on ? "pick" : "off"}" style="animation-delay:${Math.min(i*26,320)}ms">
      <div class="shot" data-zoom="${s.page}">
        ${s.thumbnail
          ? `<img src="${s.thumbnail}" alt="Page ${s.page}" loading="lazy">`
          : `<div class="nofile">No preview</div>`}
        <span class="no">P${s.page}</span>
      </div>
      <div class="txt">
        <div class="ttl">${esc(s.title)}</div>
        <div class="why">${esc(s.reason)}</div>
        ${pageFacts(s)}
        <div class="bar">
          <span class="conf">${pageStatus(s, on)}</span>
          <button class="tog ${on ? "on" : ""}" data-pick="${s.page}">${on ? "Included" : "Include"}</button>
        </div>
      </div>
    </article>`;
  }).join("") : emptyGrid();

  const jump = requiredElement("grid").querySelector("[data-toall]");
  if (jump) jump.addEventListener("click", () => requiredElement("fAll").click());
  requiredElement("grid").querySelectorAll("[data-pick]").forEach(b =>
    b.addEventListener("click", () => togglePick(+b.dataset.pick)));
  requiredElement("grid").querySelectorAll("[data-zoom]").forEach(b =>
    b.addEventListener("click", () => zoom(+b.dataset.zoom)));
}

function pageFacts(s){
  const facts = [];
  if (s.thermal_role && s.thermal_role !== "not_calculation_evidence") facts.push(esc(s.thermal_role.replaceAll("_", " ")));
  if (isSupportingContext(s)) facts.push("supporting context");
  if (s.plan_role && s.plan_role !== "main_floor_plan") facts.push(esc(s.plan_role.replaceAll("_", " ")));
  if (s.scale) facts.push(`Scale ${esc(s.scale)}`);
  if (s.dimension_count) facts.push(`${s.dimension_count} dimensions`);
  if (s.room_count) facts.push(`${s.room_count} rooms`);
  if (s.level_name) facts.push(esc(s.level_name));
  else if (s.relevant && s.level_status === "needs_confirmation") facts.push("floor needs label");
  if (s.visual?.likely_view) facts.push(esc(s.visual.likely_view.replaceAll("_", " ")));
  if (s.visual?.plan_confidence) facts.push(`Plan ${Math.round(s.visual.plan_confidence * 100)}%`);
  return facts.length ? `<div class="facts">${facts.map(f => `<span>${f}</span>`).join("")}</div>` : "";
}

function isSupportingContext(s){
  return ["symbol_key_context", "equipment_schedule_context"].includes(s.packet_role);
}

function pageStatus(s, selected){
  if (s.relevant) return Math.round(s.confidence * 100) + "% match";
  if (isSupportingContext(s) && selected) return "supporting context";
  return selected ? "included as reference" : "not selected";
}

function emptyGrid(){
  return `<div class="blank">
    <h3>No pages matched the HVAC rules</h3>
    <p>Archie only auto-selects a page when the drawing title identifies it — a floor
       plan, a reflected ceiling plan, existing mechanical services, or a related
       legend or schedule. This set did not use those titles, so nothing was selected
       automatically.</p>
    <p>Open all ${DATA.pages_analysed} pages and include the drawings you need. Your
       choices are saved with the project.</p>
    <button class="btn key" data-toall>Browse all ${DATA.pages_analysed} pages</button>
  </div>`;
}

function togglePick(page){
  PICK.has(page) ? PICK.delete(page) : PICK.add(page);
  drawSummary(); drawReviewList(); drawGrid(); drawAside();
}

function drawAside(){
  const picked = DATA.sheets.filter(s => s.relevant || PICK.has(s.page));
  const chosen = DATA.sheets.filter(s => PICK.has(s.page));
  const avg = chosen.length
    ? Math.round(chosen.reduce((a,s) => a + (s.confidence||0), 0) / chosen.length * 100) : 0;

  requiredElement("asideBody").innerHTML = `
    ${(DATA.warnings||[]).map(w => `<div class="note">${esc(w)}</div>`).join("")}
    <div class="blk">
      <div class="kv"><span>Pages analysed</span><b>${DATA.pages_analysed}</b></div>
      <div class="kv"><span>Useful pages found</span><b class="hi">${DATA.relevant_count}</b></div>
      <div class="kv"><span>Selected for AI</span><b>${PICK.size}</b></div>
      <div class="kv"><span>Average match</span><b>${avg}%</b></div>
    </div>
    <div class="blk">
      <div class="micro">Document map</div>
      <div class="spectrum" id="spectrum"></div>
      <div class="legend"><span>1</span><span>${DATA.pages_analysed}</span></div>
    </div>
    <div class="blk">
      <div class="micro">Selected drawings</div>
      <div class="chips">${
        picked.length
          ? picked.map(s => `<button class="chip ${PICK.has(s.page)?"":"mute"}" data-jump="${s.page}">${s.page}</button>`).join("")
          : `<span style="color:var(--grey);font-size:12.5px">None</span>`}</div>
    </div>
    <div class="blk">
      <div class="micro">Current selection</div>
      ${chosen.length ? chosen.map(s => `
        <div class="rsn"><b>Page ${s.page} — ${esc(s.title)}</b><span>${esc(pageSummary(s))}</span></div>`).join("")
        : `<p style="color:var(--grey);font-size:12.5px;margin:0">No pages selected.</p>`}
    </div>`;

  paintSpectrum(DATA.sheets, DATA.pages_analysed, false);
  requiredElement("asideBody").querySelectorAll("[data-jump]").forEach(b =>
    b.addEventListener("click", () => zoom(+b.dataset.jump)));
}

function pageSummary(s){
  const facts = [];
  if (s.scale) facts.push(`scale ${s.scale}`);
  if (s.dimension_count) facts.push(`${s.dimension_count} dimensions`);
  if (s.room_count) facts.push(`${s.room_count} rooms`);
  return facts.length ? `${s.reason} Found ${facts.join(", ")}.` : s.reason;
}

function paintSpectrum(sheets, total, scanning){
  const host = optionalElement("spectrum"); if (!host) return;
  const rel = new Map(sheets.map(s => [s.page, s]));
  let html = "";
  for (let p = 1; p <= total; p++){
    const s = rel.get(p);
    const on = s && (s.relevant || PICK.has(p));
    html += `<span class="tick ${on ? "rel" : ""} ${on && !PICK.has(p) ? "off" : ""} ${p===CUR?"cur":""}"
              data-jump="${p}" title="Page ${p}"></span>`;
  }
  host.innerHTML = html + (scanning ? `<span class="sweep"></span>` : "");
  host.querySelectorAll("[data-jump]").forEach(t =>
    t.addEventListener("click", () => zoom(+t.dataset.jump)));
}

/* ---------------- lightbox ---------------- */
function zoom(page){
  const s = DATA.sheets.find(x => x.page === page);
  if (!s || !s.thumbnail) return;
  CUR = page;
  const lb = document.createElement("div");
  lb.className = "lb";
  lb.innerHTML = `
    <div class="lbtop">
      <span class="num">Page ${s.page}</span><b>${esc(s.title)}</b>
      <button class="btn ghost" style="margin-left:auto">Close</button>
    </div>
    <img src="${s.thumbnail}" alt="Page ${s.page}">
    <div class="why">${esc(s.reason)}</div>`;
  const close = () => { lb.remove(); document.removeEventListener("keydown", onKey); };
  const onKey = (e) => { if (e.key === "Escape") close(); };
  lb.addEventListener("click", e => { if (e.target === lb || e.target.tagName === "BUTTON") close(); });
  document.addEventListener("keydown", onKey);
  document.body.appendChild(lb);
}

/* ---------------- confirm ---------------- */
requiredElement("btnConfirm").addEventListener("click", confirmSelection);
requiredElement("btnConfirmTop").addEventListener("click", confirmSelection);
requiredElement("btnVisionSubmit").addEventListener("click", submitVisionResponse);
requiredElement("btnAddHeatSource").addEventListener("click", () => addHeatSource());
requiredElement("btnAddZone").addEventListener("click", () => addZone());
requiredElement("btnSaveRequirements").addEventListener("click", saveDesignRequirements);
requiredElement("btnBuildThermalModel").addEventListener("click", () => saveThermalModel("build"));
requiredElement("btnApplyThermalModel").addEventListener("click", () => saveThermalModel("save"));
requiredElement("btnBuildCalculatorDraft").addEventListener("click", () => saveCalculatorDraft("build"));
requiredElement("btnApplyCalculatorDraft").addEventListener("click", () => saveCalculatorDraft("apply"));
requiredElement("btnCalculateVentilation").addEventListener("click", calculateVentilation);
requiredElement("btnBuildHourlyModel").addEventListener("click", () => saveHourlyModel("build"));
requiredElement("btnAddFloor").addEventListener("click", () => addHourlyFloor());
requiredElement("btnAddHourlyZone").addEventListener("click", () => addHourlyZone());
requiredElement("btnAddHourlyRoom").addEventListener("click", () => addHourlyRoom());
requiredElement("btnSaveHourlyModel").addEventListener("click", () => saveHourlyModel("save"));
requiredElement("btnCalculateHourlyLoad").addEventListener("click", calculateHourlyLoad);
requiredElement("btnAddConstruction").addEventListener("click", () => addEnvelopeConstruction());
requiredElement("btnAddBoundary").addEventListener("click", () => addEnvelopeBoundary());
requiredElement("btnSaveEnvelope").addEventListener("click", saveEnvelope);
requiredElement("btnMigrateEnvelope").addEventListener("click", migrateEnvelope);

async function confirmSelection(){
  if (!DATA || !PICK.size) return toast("Nothing selected", "Include at least one page before confirming.");
  requiredElement("btnConfirm").disabled = true;
  const pages = DATA.sheets.filter(s => PICK.has(s.page)).map(s => ({
    page: s.page, detected_type: s.type,
    decision: s.plan_role === "main_floor_plan" ? "Confirm as floor plan"
            : s.type === "reflected_ceiling_plan" ? "Confirm as RCP"
            : s.relevant ? "Confirm as detected" : "Keep as reference",
    scale_confirmed: false, note: "",
  }));
  try {
    const res = await fetch("/api/decisions", {
      method:"POST", headers:{"Content-Type":"application/json"},
      body: JSON.stringify({ id: DATA.id, source_pdf: DATA.name,
                             reviewed_at: new Date().toISOString(), pages }),
    });
    const data = await res.json();
    if (!res.ok || data.error) throw new Error(data.error || "Could not save the selection.");
    PACKET = data.chatgpt_packet || null;
    DATA.chatgpt_packet = PACKET;
    const links = [`<a href="${data.ai_input_url}" target="_blank" rel="noopener">ai_input.json</a>`];
    if (PACKET?.prompt) links.push(`<a href="${PACKET.prompt}" target="_blank" rel="noopener">prompt.md</a>`);
    if (PACKET?.manifest) links.push(`<a href="${PACKET.manifest}" target="_blank" rel="noopener">manifest.json</a>`);
    if (PACKET?.zip) links.push(`<a href="${PACKET.zip}" target="_blank" rel="noopener">Download ChatGPT packet</a>`);
    toast("Selection confirmed",
      `${pages.length} page${pages.length===1?"":"s"} packaged for one ChatGPT vision review. ` +
      links.join(" · "));
    showVisionPanel();
  } catch (err) { toast("Could not confirm", err.message); }
  requiredElement("btnConfirm").disabled = false;
}

function showVisionPanel(){
  requiredElement("visionPanel").classList.remove("hide");
  requiredElement("visionStatus").textContent = "Upload the complete packet to ChatGPT, then paste its structured vision JSON.";
  requiredElement("visionLinks").innerHTML = PACKET ? `
    <article class="review-item">
      <div>
        <b>ChatGPT vision packet ready</b>
        <span>It includes screenshots, PDF text, vector candidates, and dimension evidence.</span>
      </div>
      ${PACKET.zip ? `<a class="btn ghost mini" href="${PACKET.zip}" target="_blank" rel="noopener">Download packet</a>` : ""}
    </article>` : "";
}

async function submitVisionResponse(){
  if (!DATA?.id) return toast("No project selected", "Open or analyse a project first.");
  const visionJson = requiredElement("visionJson").value.trim();
  if (!visionJson) return toast("No vision JSON", "Paste the JSON returned by ChatGPT before submitting.");

  requiredElement("btnVisionSubmit").disabled = true;
  requiredElement("visionStatus").textContent = "Validating vision JSON and creating reasoning packet…";
  try {
    const res = await fetch("/api/vision-response", {
      method:"POST", headers:{"Content-Type":"application/json"},
      body: JSON.stringify({
        project_id: DATA.id,
        vision_json: visionJson,
        source_label: "manual_chatgpt",
      }),
    });
    const data = await res.json();
    if (!res.ok || data.error) throw new Error(data.error || "Could not process the vision response.");
    drawVisionResult(data);
    toast("Reasoning packet created", `Geometry status: ${esc(data.geometry_verification_status)}.`);
  } catch (err) {
    requiredElement("visionStatus").textContent = "Vision response failed.";
    toast("Could not process vision JSON", err.message);
  }
  requiredElement("btnVisionSubmit").disabled = false;
}

function drawVisionResult(data){
  const status = data.geometry_verification_status || "geometry_not_vision_verified";
  requiredElement("visionStatus").textContent = `${status} · ${data.issue_count || 0} validation issue${data.issue_count === 1 ? "" : "s"}`;
  const links = [
    ["vision_response.json", data.vision_response_url],
    ["vision_validation.json", data.vision_validation_url],
    ["coordinate_review.json", data.coordinate_review_url],
    ["reasoning prompt", data.reasoning_prompt_url],
    ["reasoning manifest", data.reasoning_manifest_url],
    ["reasoning packet zip", data.reasoning_zip_url],
  ].filter(([, url]) => url);
  requiredElement("visionLinks").innerHTML = `
    <article class="review-item">
      <div>
        <b>Reasoning packet ${status === "geometry_vision_layered" ? "ready" : "created with warnings"}</b>
        <span>${status === "geometry_vision_layered"
          ? "Layered geometry passed validation and is ready for reasoning review."
          : "Review validation issues before treating geometry as design-ready."}</span>
      </div>
    </article>
    ${links.map(([label, url]) => `
      <article class="review-item">
        <div><b>${esc(label)}</b><span>${esc(url)}</span></div>
        <a class="btn ghost mini" href="${url}" target="_blank" rel="noopener">Open</a>
      </article>`).join("")}`;
  showDesignRequirements(data.requirements, data.requirements_readiness);
  loadDesignRequirements();
}

const requirementFields = {
  space_usage: "reqSpaceUsage", occupancy: "reqOccupancy", operating_hours: "reqOperatingHours",
  indoor_cooling_setpoint_c: "reqCooling", indoor_heating_setpoint_c: "reqHeating",
  outdoor_summer_db_c: "reqSummer", outdoor_winter_db_c: "reqWinter",
  fresh_air_basis: "reqFreshAir", exhaust_basis: "reqExhaust",
  ceiling_height_mm: "reqCeilingHeight", ceiling_void_height_mm: "reqCeilingVoid",
  existing_services: "reqExistingServices", code_basis: "reqCodeBasis", designer_notes: "reqDesignerNotes",
  cooking_activity: "reqCookingActivity", hood_requirement: "reqHoodRequirement",
  exhaust_outcome: "reqExhaustOutcome", make_up_air_requirement: "reqMakeUpAir",
};

const verificationFields = {
  occupancy: ["reqOccupancyStatus", "reqOccupancySource"],
  design_conditions: ["reqConditionsStatus", "reqConditionsSource"],
  outside_air: ["reqFreshAirStatus", "reqFreshAirSource"],
  exhaust: ["reqExhaustStatus", "reqExhaustSource"],
  heat_sources: ["reqHeatSourcesStatus", "reqHeatSourcesSource"],
  ceiling: ["reqCeilingStatus", "reqCeilingSource"],
  existing_services: ["reqServicesStatus", "reqServicesSource"],
};

const serviceConstraintFields = {
  electrical_capacity: "reqElectricalCapacity",
  condensate_route: "reqCondensateRoute",
  outdoor_unit_location: "reqOutdoorUnitLocation",
  riser_or_base_building_services: "reqRiserServices",
  maintenance_access: "reqMaintenanceAccess",
};

const coolingLoadConditionFields = {
  indoor_cooling_wet_bulb_c: "reqCoolingWetBulb",
  outdoor_summer_wet_bulb_c: "reqSummerWetBulb",
  atmospheric_pressure_kpa: "reqAtmosphericPressure",
  verification_status: "reqCoolingLoadStatus",
  source: "reqCoolingLoadSource",
};

function addHeatSource(source = {}){
  const row = document.createElement("div");
  row.className = "heat-source";
  row.innerHTML = `<input class="heat-name" placeholder="Equipment name" value="${esc(source.name || "")}">
    <input class="heat-quantity" type="number" min="0" step="1" placeholder="Qty" value="${source.quantity ?? ""}">
    <input class="heat-watts" type="number" min="0" step="1" placeholder="Watts each" value="${source.watts ?? ""}">
    <select class="heat-status"><option value="provisional">Provisional</option><option value="confirmed">Confirmed</option></select>
    <input class="heat-source-note" placeholder="Source" value="${esc(source.source || "")}">
    <button class="btn ghost mini" type="button">Remove</button>`;
  row.querySelector(".heat-status").value = source.verification_status || "provisional";
  row.querySelector("button").addEventListener("click", () => row.remove());
  requiredElement("heatSources").appendChild(row);
}

function nextZoneId(){
  const ids = [...requiredElement("zones").querySelectorAll(".zone-editor")].map(zone => zone.dataset.zoneId);
  let number = 1;
  while (ids.includes(`zone_${String(number).padStart(3, "0")}`)) number++;
  return `zone_${String(number).padStart(3, "0")}`;
}

function readableArea(value){
  const match = String(value || "").replace(",", "").match(/\d+(?:\.\d+)?/);
  return match ? Number(match[0]) : null;
}

function suggestedZone(suggestion, index){
  return {
    zone_id: `zone_${String(index + 1).padStart(3, "0")}`,
    name: suggestion.label,
    usage: "",
    source_room_labels: [suggestion.label],
    area_m2: readableArea(suggestion.area),
    occupancy: null,
    operating_hours: "",
    indoor_cooling_setpoint_c: null,
    indoor_heating_setpoint_c: null,
    ceiling_height_mm: null,
    heat_sources: [],
    ventilation_requirements: {},
  };
}

function addZoneHeatSource(container, source = {}){
  const row = document.createElement("div");
  row.className = "zone-heat-source";
  row.innerHTML = `<input class="zone-heat-name" placeholder="Equipment name" value="${esc(source.name || "")}">
    <select class="zone-heat-kind"><option value="appliance">Appliance</option><option value="refrigeration">Refrigeration</option><option value="other">Other</option></select>
    <input class="zone-heat-quantity" type="number" min="0" step="1" placeholder="Qty" value="${source.quantity ?? ""}">
    <input class="zone-heat-watts" type="number" min="0" step="1" placeholder="Heat W each" value="${source.watts ?? ""}">
    <input class="zone-heat-diversity" type="number" min="0" max="1" step="0.01" placeholder="Diversity" value="${source.diversity_factor ?? ""}">
    <input class="zone-heat-space-gain" type="number" min="0" max="1" step="0.01" placeholder="Space gain" value="${source.space_gain_factor ?? ""}">
    <select class="zone-heat-status"><option value="provisional">Provisional</option><option value="confirmed">Confirmed</option></select>
    <input class="zone-heat-note" placeholder="Source" value="${esc(source.source || "")}">
    <button class="btn ghost mini" type="button">Remove</button>`;
  row.querySelector(".zone-heat-status").value = source.verification_status || "provisional";
  row.querySelector(".zone-heat-kind").value = source.kind || "appliance";
  row.querySelector("button").addEventListener("click", () => row.remove());
  container.appendChild(row);
}

function addEnvelopeSurface(container, surface = {}){
  const row = document.createElement("div");
  row.className = "envelope-surface";
  row.innerHTML = `<input class="surface-id" placeholder="Surface ID" value="${esc(surface.surface_id || "")}">
    <select class="surface-kind"><option value="opaque_wall">Opaque wall</option><option value="roof">Roof</option><option value="glazing">Glazing</option><option value="other">Other</option></select>
    <select class="surface-orientation"><option value="N">N</option><option value="NE">NE</option><option value="E">E</option><option value="SE">SE</option><option value="S">S</option><option value="SW">SW</option><option value="W">W</option><option value="NW">NW</option><option value="horizontal">Horizontal</option><option value="internal">Internal</option></select>
    <input class="surface-area" type="number" min="0" step="0.1" placeholder="Area m²" value="${surface.area_m2 ?? ""}">
    <input class="surface-u" type="number" min="0" step="0.01" placeholder="U W/m²K" value="${surface.u_value_w_m2k ?? ""}">
    <input class="surface-solar" type="number" min="0" step="1" placeholder="Solar W/m²" value="${surface.solar_design_w_m2 ?? ""}">
    <input class="surface-gain" type="number" min="0" max="1" step="0.01" placeholder="Solar factor" value="${surface.solar_gain_factor ?? ""}">
    <input class="surface-shading" type="number" min="0" max="1" step="0.01" placeholder="Shading" value="${surface.shading_factor ?? ""}">
    <select class="surface-status"><option value="provisional">Provisional</option><option value="confirmed">Confirmed</option></select>
    <input class="surface-source" placeholder="Source" value="${esc(surface.source || "")}">
    <button class="btn ghost mini" type="button">Remove</button>`;
  row.querySelector(".surface-kind").value = surface.kind || "opaque_wall";
  row.querySelector(".surface-orientation").value = surface.orientation || "N";
  row.querySelector(".surface-status").value = surface.verification_status || "provisional";
  row.querySelector("button").addEventListener("click", () => row.remove());
  container.appendChild(row);
}

function addZone(zone = {}){
  const item = document.createElement("details");
  item.className = "zone-editor";
  item.dataset.zoneId = zone.zone_id || nextZoneId();
  item.innerHTML = `<summary><span class="zone-title"></span><span class="zone-warning"></span></summary>
    <div class="zone-fields">
      <label>Zone name<input class="zone-name" type="text" placeholder="e.g. sales area" value="${esc(zone.name || "")}"></label>
      <label>Zone use<input class="zone-usage" type="text" placeholder="e.g. retail, kitchen, storage" value="${esc(zone.usage || "")}"></label>
      <label>PDF room labels<textarea class="zone-room-labels" rows="2" placeholder="Comma-separated labels from the drawing">${esc((zone.source_room_labels || []).join(", "))}</textarea></label>
      <label>Area (m²)<input class="zone-area" type="number" min="0" step="0.1" value="${zone.area_m2 ?? ""}"></label>
      <label>Peak occupancy<input class="zone-occupancy" type="number" min="1" step="1" value="${zone.occupancy ?? ""}"></label>
      <label>Operating hours override<input class="zone-hours" type="text" placeholder="Uses project-wide value when blank" value="${esc(zone.operating_hours || "")}"></label>
      <label>Cooling setpoint override (°C)<input class="zone-cooling" type="number" min="0" step="0.1" placeholder="Uses project-wide value" value="${zone.indoor_cooling_setpoint_c ?? ""}"></label>
      <label>Heating setpoint override (°C)<input class="zone-heating" type="number" min="0" step="0.1" placeholder="Uses project-wide value" value="${zone.indoor_heating_setpoint_c ?? ""}"></label>
      <label>Ceiling height override (mm)<input class="zone-ceiling" type="number" min="1000" step="1" placeholder="Uses project-wide value" value="${zone.ceiling_height_mm ?? ""}"></label>
    </div>
    <div class="zone-load-fields">
      <label>People sensible W/person<input class="zone-people-sensible" type="number" min="0" step="0.1" value="${zone.cooling_load?.people_sensible_w_per_person ?? ""}"></label>
      <label>People latent W/person<input class="zone-people-latent" type="number" min="0" step="0.1" value="${zone.cooling_load?.people_latent_w_per_person ?? ""}"></label>
      <label>People diversity<input class="zone-people-diversity" type="number" min="0" max="1" step="0.01" value="${zone.cooling_load?.people_diversity_factor ?? ""}"></label>
      <label>Lighting W/m²<input class="zone-lighting-density" type="number" min="0" step="0.1" value="${zone.cooling_load?.lighting_w_m2 ?? ""}"></label>
      <label>Lighting diversity<input class="zone-lighting-diversity" type="number" min="0" max="1" step="0.01" value="${zone.cooling_load?.lighting_diversity_factor ?? ""}"></label>
      <label>Outside air (L/s)<input class="zone-outside-air" type="number" min="0" step="0.1" value="${zone.cooling_load?.outside_air_lps ?? ""}"></label>
      <label>Safety factor<input class="zone-safety" type="number" min="1" step="0.01" value="${zone.cooling_load?.safety_factor ?? ""}"></label>
      <label>Load-input status<select class="zone-load-status"><option value="missing">Missing</option><option value="provisional">Provisional</option><option value="confirmed">Confirmed</option></select></label>
      <label>Load-input source<textarea class="zone-load-source" rows="2" placeholder="Designer load basis">${esc(zone.cooling_load?.source || "")}</textarea></label>
      <label class="zone-internal"><input class="zone-envelope-na" type="checkbox" ${zone.cooling_load?.envelope_not_applicable ? "checked" : ""}> Internal zone: no exposed envelope</label>
    </div>
    <div class="zone-ventilation-fields">
      <label>Process type<select class="zone-vent-process"><option value="none">None</option><option value="retail">Retail</option><option value="office">Office</option><option value="toilet">Toilet</option><option value="kitchen">Kitchen</option><option value="baking">Baking</option><option value="other">Other</option></select></label>
      <label>Approved basis name<input class="zone-vent-basis-name" placeholder="Standard/table/version" value="${esc(zone.ventilation_requirements?.basis_name || "")}"></label>
      <label>Approved basis source<input class="zone-vent-basis-source" placeholder="Clause, table, designer record" value="${esc(zone.ventilation_requirements?.basis_source || "")}"></label>
      <label>Outside-air method<select class="zone-vent-method"><option value="occupancy">Occupancy</option><option value="area">Area</option><option value="fixed">Fixed minimum</option><option value="combined">Combined</option></select></label>
      <label>People rate (L/s/person)<input class="zone-vent-people-rate" type="number" min="0" step="0.01" value="${zone.ventilation_requirements?.people_rate_lps_per_person ?? ""}"></label>
      <label>Area rate (L/s/m²)<input class="zone-vent-area-rate" type="number" min="0" step="0.01" value="${zone.ventilation_requirements?.area_rate_lps_per_m2 ?? ""}"></label>
      <label>Fixed minimum (L/s)<input class="zone-vent-fixed-minimum" type="number" min="0" step="0.1" value="${zone.ventilation_requirements?.fixed_minimum_lps ?? ""}"></label>
      <label>Process exhaust<select class="zone-vent-exhaust-requirement"><option value="unknown">Unknown</option><option value="not_required">Not required</option><option value="required">Required</option></select></label>
      <label>Process exhaust (L/s)<input class="zone-vent-exhaust" type="number" min="0" step="0.1" value="${zone.ventilation_requirements?.process_exhaust_lps ?? ""}"></label>
      <label>Hood type/duty<input class="zone-vent-hood" placeholder="If applicable" value="${esc(zone.ventilation_requirements?.hood_type_or_duty || "")}"></label>
      <label>Recirculable<select class="zone-vent-recirculable"><option value="unknown">Unknown</option><option value="yes">Yes</option><option value="no">No</option></select></label>
      <label>Transfer-air credit (L/s)<input class="zone-vent-transfer" type="number" min="0" step="0.1" value="${zone.ventilation_requirements?.allowable_transfer_air_lps ?? ""}"></label>
      <label>Outside-air credit (L/s)<input class="zone-vent-outside-credit" type="number" min="0" step="0.1" value="${zone.ventilation_requirements?.allowable_outside_air_credit_lps ?? ""}"></label>
      <label>Design supply incl. OA (L/s)<input class="zone-vent-supply" type="number" min="0" step="0.1" value="${zone.ventilation_requirements?.design_supply_lps_including_outside_air ?? ""}"></label>
      <label>Return/relief (L/s)<input class="zone-vent-return" type="number" min="0" step="0.1" value="${zone.ventilation_requirements?.return_or_relief_lps ?? ""}"></label>
      <label>Dedicated make-up air (L/s)<input class="zone-vent-make-up" type="number" min="0" step="0.1" value="${zone.ventilation_requirements?.dedicated_make_up_air_lps ?? ""}"></label>
      <label>Ventilation status<select class="zone-vent-status"><option value="missing">Missing</option><option value="provisional">Provisional</option><option value="confirmed">Confirmed</option></select></label>
      <label>Ventilation source<textarea class="zone-vent-source" rows="2" placeholder="Designer basis and assumptions">${esc(zone.ventilation_requirements?.source || "")}</textarea></label>
    </div>
    <div class="zone-heat-head"><b>Zone internal heat sources</b><button class="btn ghost mini zone-add-heat" type="button">Add source</button></div>
    <div class="zone-heat-sources"></div>
    <div class="zone-heat-head"><b>Envelope and solar surfaces</b><button class="btn ghost mini zone-add-surface" type="button">Add surface</button></div>
    <div class="envelope-surfaces"></div>
    <div class="zone-actions"><span>Stable ID: <code>${esc(item.dataset.zoneId)}</code></span><button class="btn ghost mini zone-remove" type="button">Remove zone</button></div>`;
  const sources = item.querySelector(".zone-heat-sources");
  const surfaces = item.querySelector(".envelope-surfaces");
  (zone.heat_sources || []).forEach(source => addZoneHeatSource(sources, source));
  (zone.cooling_load?.envelope_surfaces || []).forEach(surface => addEnvelopeSurface(surfaces, surface));
  item.querySelector(".zone-load-status").value = zone.cooling_load?.verification_status || "missing";
  item.querySelector(".zone-vent-process").value = zone.ventilation_requirements?.process_type || "none";
  item.querySelector(".zone-vent-method").value = zone.ventilation_requirements?.outside_air_method || "combined";
  item.querySelector(".zone-vent-exhaust-requirement").value = zone.ventilation_requirements?.process_exhaust_requirement || "unknown";
  item.querySelector(".zone-vent-recirculable").value = zone.ventilation_requirements?.recirculable || "unknown";
  item.querySelector(".zone-vent-status").value = zone.ventilation_requirements?.verification_status || "missing";
  item.querySelector(".zone-add-heat").addEventListener("click", () => addZoneHeatSource(sources));
  item.querySelector(".zone-add-surface").addEventListener("click", () => addEnvelopeSurface(surfaces));
  item.querySelector(".zone-remove").addEventListener("click", () => item.remove());
  item.addEventListener("toggle", () => {
    if (!item.open) return;
    requiredElement("zones").querySelectorAll(".zone-editor[open]").forEach(other => {
      if (other !== item) other.open = false;
    });
  });
  item.addEventListener("input", () => refreshZoneLabel(item));
  item.addEventListener("change", () => refreshZoneLabel(item));
  requiredElement("zones").appendChild(item);
  refreshZoneLabel(item);
  return item;
}

function refreshZoneLabel(item){
  const zone = readZone(item);
  item.querySelector(".zone-title").textContent = zone.name || "Unnamed HVAC zone";
  const missing = [!zone.usage && "usage", zone.area_m2 === null && "area", zone.occupancy === null && "occupancy", !zone.heat_sources.length && "heat sources"].filter(Boolean);
  item.querySelector(".zone-warning").textContent = missing.length ? `Needs: ${missing.join(", ")}` : "Core inputs recorded";
}

function readZone(item){
  return {
    zone_id: item.dataset.zoneId,
    name: item.querySelector(".zone-name").value.trim(),
    usage: item.querySelector(".zone-usage").value.trim(),
    source_room_labels: item.querySelector(".zone-room-labels").value.split(",").map(label => label.trim()).filter(Boolean),
    area_m2: blankToNull(item.querySelector(".zone-area").value),
    occupancy: blankToNull(item.querySelector(".zone-occupancy").value),
    operating_hours: item.querySelector(".zone-hours").value.trim(),
    indoor_cooling_setpoint_c: blankToNull(item.querySelector(".zone-cooling").value),
    indoor_heating_setpoint_c: blankToNull(item.querySelector(".zone-heating").value),
    ceiling_height_mm: blankToNull(item.querySelector(".zone-ceiling").value),
    heat_sources: [...item.querySelectorAll(".zone-heat-source")].map(row => ({
      name: row.querySelector(".zone-heat-name").value.trim(),
      quantity: blankToNull(row.querySelector(".zone-heat-quantity").value),
      watts: blankToNull(row.querySelector(".zone-heat-watts").value),
      kind: row.querySelector(".zone-heat-kind").value,
      diversity_factor: blankToNull(row.querySelector(".zone-heat-diversity").value),
      space_gain_factor: blankToNull(row.querySelector(".zone-heat-space-gain").value),
      verification_status: row.querySelector(".zone-heat-status").value,
      source: row.querySelector(".zone-heat-note").value.trim(),
    })),
    cooling_load: {
      people_sensible_w_per_person: blankToNull(item.querySelector(".zone-people-sensible").value),
      people_latent_w_per_person: blankToNull(item.querySelector(".zone-people-latent").value),
      people_diversity_factor: blankToNull(item.querySelector(".zone-people-diversity").value),
      lighting_w_m2: blankToNull(item.querySelector(".zone-lighting-density").value),
      lighting_diversity_factor: blankToNull(item.querySelector(".zone-lighting-diversity").value),
      outside_air_lps: blankToNull(item.querySelector(".zone-outside-air").value),
      safety_factor: blankToNull(item.querySelector(".zone-safety").value),
      envelope_not_applicable: item.querySelector(".zone-envelope-na").checked,
      verification_status: item.querySelector(".zone-load-status").value,
      source: item.querySelector(".zone-load-source").value.trim(),
      envelope_surfaces: [...item.querySelectorAll(".envelope-surface")].map(row => ({
        surface_id: row.querySelector(".surface-id").value.trim(),
        kind: row.querySelector(".surface-kind").value,
        orientation: row.querySelector(".surface-orientation").value,
        area_m2: blankToNull(row.querySelector(".surface-area").value),
        u_value_w_m2k: blankToNull(row.querySelector(".surface-u").value),
        solar_design_w_m2: blankToNull(row.querySelector(".surface-solar").value),
        solar_gain_factor: blankToNull(row.querySelector(".surface-gain").value),
        shading_factor: blankToNull(row.querySelector(".surface-shading").value),
        verification_status: row.querySelector(".surface-status").value,
        source: row.querySelector(".surface-source").value.trim(),
      })),
    },
    ventilation_requirements: {
      process_type: item.querySelector(".zone-vent-process").value,
      basis_name: item.querySelector(".zone-vent-basis-name").value.trim(),
      basis_source: item.querySelector(".zone-vent-basis-source").value.trim(),
      outside_air_method: item.querySelector(".zone-vent-method").value,
      people_rate_lps_per_person: blankToNull(item.querySelector(".zone-vent-people-rate").value),
      area_rate_lps_per_m2: blankToNull(item.querySelector(".zone-vent-area-rate").value),
      fixed_minimum_lps: blankToNull(item.querySelector(".zone-vent-fixed-minimum").value),
      process_exhaust_requirement: item.querySelector(".zone-vent-exhaust-requirement").value,
      process_exhaust_lps: blankToNull(item.querySelector(".zone-vent-exhaust").value),
      hood_type_or_duty: item.querySelector(".zone-vent-hood").value.trim(),
      recirculable: item.querySelector(".zone-vent-recirculable").value,
      allowable_transfer_air_lps: blankToNull(item.querySelector(".zone-vent-transfer").value),
      allowable_outside_air_credit_lps: blankToNull(item.querySelector(".zone-vent-outside-credit").value),
      design_supply_lps_including_outside_air: blankToNull(item.querySelector(".zone-vent-supply").value),
      return_or_relief_lps: blankToNull(item.querySelector(".zone-vent-return").value),
      dedicated_make_up_air_lps: blankToNull(item.querySelector(".zone-vent-make-up").value),
      verification_status: item.querySelector(".zone-vent-status").value,
      source: item.querySelector(".zone-vent-source").value.trim(),
    },
  };
}

function blankToNull(value){ return value === "" ? null : Number(value); }

function readDesignRequirements(){
  const result = {};
  Object.entries(requirementFields).forEach(([key, id]) => {
    const element = requiredElement(id);
    result[key] = element.type === "number" ? blankToNull(element.value) : element.value.trim();
  });
  result.verification = Object.fromEntries(Object.entries(verificationFields).map(([category, [statusId, sourceId]]) => [category, {
    status: requiredElement(statusId).value,
    source: requiredElement(sourceId).value.trim(),
  }]));
  result.service_constraints = Object.fromEntries(Object.entries(serviceConstraintFields).map(([key, id]) => [key, requiredElement(id).value.trim()]));
  result.heat_sources = [...requiredElement("heatSources").querySelectorAll(".heat-source")].map(row => ({
    name: row.querySelector(".heat-name").value.trim(),
    quantity: blankToNull(row.querySelector(".heat-quantity").value),
    watts: blankToNull(row.querySelector(".heat-watts").value),
    verification_status: row.querySelector(".heat-status").value,
    source: row.querySelector(".heat-source-note").value.trim(),
  }));
  result.zones = [...requiredElement("zones").querySelectorAll(".zone-editor")].map(readZone);
  result.cooling_load_conditions = Object.fromEntries(Object.entries(coolingLoadConditionFields).map(([key, id]) => {
    const element = requiredElement(id);
    return [key, element.type === "number" ? blankToNull(element.value) : element.value.trim()];
  }));
  return result;
}

function showDesignRequirements(requirements = {}, readiness = {}, roomSuggestions = ROOM_SUGGESTIONS, heatLoadReport = {}, heatLoadStatus = "not_calculated", ventilationReport = {}, ventilationStatus = "not_calculated", heatLoadReportUrl = ""){
  requiredElement("designRequirementsPanel").classList.remove("hide");
  ROOM_SUGGESTIONS = roomSuggestions || [];
  Object.entries(requirementFields).forEach(([key, id]) => {
    requiredElement(id).value = requirements[key] ?? "";
  });
  Object.entries(verificationFields).forEach(([category, [statusId, sourceId]]) => {
    const verification = requirements.verification?.[category] || {};
    requiredElement(statusId).value = verification.status || "missing";
    requiredElement(sourceId).value = verification.source || "";
  });
  Object.entries(serviceConstraintFields).forEach(([key, id]) => {
    requiredElement(id).value = requirements.service_constraints?.[key] || "";
  });
  Object.entries(coolingLoadConditionFields).forEach(([key, id]) => {
    requiredElement(id).value = requirements.cooling_load_conditions?.[key] ?? "";
  });
  requiredElement("heatSources").innerHTML = "";
  (requirements.heat_sources || []).forEach(addHeatSource);
  requiredElement("zones").innerHTML = "";
  const zones = requirements.zones?.length ? requirements.zones : ROOM_SUGGESTIONS.map(suggestedZone);
  zones.forEach(addZone);
  const missing = readiness.missing_inputs || [];
  const provisional = readiness.provisional_inputs || [];
  const errors = readiness.input_errors || [];
  const incompleteZones = readiness.incomplete_zone_count || 0;
  requiredElement("requirementsStatus").textContent = readiness.status
    ? `${readiness.status} · ${[missing.length && "Missing: " + missing.join(", "), provisional.length && "Provisional: " + provisional.join(", "), incompleteZones && `${incompleteZones} zone${incompleteZones === 1 ? "" : "s"} need inputs`, errors.length && "Fix: " + errors.join(", ")].filter(Boolean).join(" · ") || "All required design inputs confirmed."}`
    : "Complete design inputs to unlock final engineering work.";
  const hasLegacyCooling = Object.keys(heatLoadReport || {}).length > 0;
  requiredElement("requirementsLinks").innerHTML = hasLegacyCooling
    ? `<article class="review-item"><div><b>Legacy cooling report (read-only)</b><span>${esc(heatLoadStatus)} · new calculations use the hourly cooling report.</span></div>${heatLoadReportUrl ? `<a class="btn ghost mini" href="${esc(heatLoadReportUrl)}" target="_blank" rel="noopener">Open</a>` : ""}</article>`
    : "";
  drawVentilationReport(ventilationReport, ventilationStatus);
  loadThermalModel();
  loadCalculatorDraft();
  loadEnvelope();
  loadHourlyModel();
  loadHourlyLoadReport();
}

function addEnvelopeConstruction(record = {}){
  const row = document.createElement("div");
  row.className = "envelope-construction";
  row.innerHTML = `<input class="env-id" placeholder="Construction ID" value="${esc(record.record_id || "")}">
    <input class="env-title" placeholder="Construction title" value="${esc(record.title || "")}">
    <select class="env-kind"><option value="opaque_wall">Wall</option><option value="roof">Roof</option><option value="floor">Floor</option><option value="ceiling">Ceiling</option><option value="partition">Partition</option></select>
    <input class="env-u" type="number" min="0.001" step="0.001" placeholder="U W/m²K" value="${record.u_value_w_m2k ?? ""}">
    <input class="env-abs" type="number" min="0" max="1" step="0.01" placeholder="Absorptivity" value="${record.absorptivity ?? ""}">
    <select class="env-status"><option value="missing">Missing</option><option value="provisional">Provisional</option><option value="confirmed">Confirmed</option></select>
    <input class="env-source" placeholder="Reviewed source" value="${esc(record.source || "")}">
    <button class="btn ghost mini" type="button">Remove</button>`;
  row.querySelector(".env-kind").value = record.kind || "opaque_wall";
  row.querySelector(".env-status").value = record.review_status || "missing";
  row.querySelector("button").addEventListener("click", () => row.remove());
  requiredElement("envelopeConstructions").appendChild(row);
}

function addEnvelopeBoundary(surface = {}){
  const row = document.createElement("div");
  row.className = "envelope-boundary";
  row.innerHTML = `<input class="boundary-id" placeholder="Surface ID" value="${esc(surface.surface_id || "")}">
    <input class="boundary-zone" placeholder="Owner zone ID" value="${esc(surface.owner_zone_id || "")}">
    <select class="boundary-kind"><option value="opaque_wall">Wall</option><option value="roof">Roof</option><option value="floor">Floor</option><option value="ceiling">Ceiling</option><option value="partition">Partition</option><option value="glazing">Glazing (stored)</option></select>
    <select class="boundary-orientation"><option value="N">N</option><option value="NE">NE</option><option value="E">E</option><option value="SE">SE</option><option value="S">S</option><option value="SW">SW</option><option value="W">W</option><option value="NW">NW</option><option value="horizontal">Horizontal</option><option value="internal">Internal</option></select>
    <input class="boundary-area" type="number" min="0.001" step="0.01" placeholder="Area m²" value="${surface.area_m2 ?? ""}">
    <input class="boundary-construction" placeholder="Construction ID" value="${esc(surface.construction_id || "")}">
    <select class="boundary-method"><option value="external">External</option><option value="fixed_adjacent_temperature">Fixed adjacent temp</option><option value="outdoor_offset">Outdoor offset (stored)</option><option value="proportional_ambient_difference">Proportional (stored)</option></select>
    <input class="boundary-temp" type="number" step="0.1" placeholder="Adjacent °C" value="${surface.adjacent_temperature_c ?? ""}">
    <select class="boundary-status"><option value="missing">Missing</option><option value="provisional">Provisional</option><option value="confirmed">Confirmed</option></select>
    <input class="boundary-source" placeholder="Reviewed source" value="${esc(surface.source || "")}">
    <label class="boundary-solar"><input class="boundary-solar-enabled" type="checkbox" ${surface.manual_solar?.enabled ? "checked" : ""}> Manual solar</label>
    <input class="boundary-solar-design" type="number" min="0" step="0.1" placeholder="Solar W/m²" value="${surface.manual_solar?.solar_design_w_m2 ?? ""}">
    <input class="boundary-solar-gain" type="number" min="0" max="1" step="0.01" placeholder="Gain factor" value="${surface.manual_solar?.solar_gain_factor ?? ""}">
    <input class="boundary-solar-shade" type="number" min="0" max="1" step="0.01" placeholder="Shade factor" value="${surface.manual_solar?.shading_factor ?? ""}">
    <select class="boundary-solar-status"><option value="missing">Missing</option><option value="provisional">Provisional</option><option value="confirmed">Confirmed</option></select>
    <input class="boundary-solar-source" placeholder="Manual solar source" value="${esc(surface.manual_solar?.source || "")}">
    <button class="btn ghost mini" type="button">Remove</button>`;
  row.querySelector(".boundary-kind").value = surface.kind || "opaque_wall";
  row.querySelector(".boundary-orientation").value = surface.orientation || "N";
  row.querySelector(".boundary-method").value = surface.boundary_method || "external";
  row.querySelector(".boundary-status").value = surface.review_status || "missing";
  row.querySelector(".boundary-solar-status").value = surface.manual_solar?.review_status || "missing";
  row.querySelector("button").addEventListener("click", () => row.remove());
  requiredElement("envelopeSurfaces").appendChild(row);
}

function parseEnvelopeRecords(id, label){
  const raw = requiredElement(id).value.trim();
  if (!raw) return [];
  try {
    const value = JSON.parse(raw);
    if (!Array.isArray(value)) throw new Error("must be an array");
    return value;
  } catch (error) {
    throw new Error(`${label} must be valid JSON array: ${error.message}`);
  }
}

function readEnvelope(){
  return {
    envelope_library: {
      constructions: [...document.querySelectorAll(".envelope-construction")].map(row => ({
        record_id: row.querySelector(".env-id").value.trim(), title: row.querySelector(".env-title").value.trim(), revision: 1,
        kind: row.querySelector(".env-kind").value, u_value_w_m2k: blankToNull(row.querySelector(".env-u").value), absorptivity: blankToNull(row.querySelector(".env-abs").value),
        review_status: row.querySelector(".env-status").value, source: row.querySelector(".env-source").value.trim(), citations: [],
      })),
      windows: parseEnvelopeRecords("envelopeWindows", "Window records"), shading_records: parseEnvelopeRecords("envelopeShading", "Shading records"),
    },
    envelope_model: {
      active_for_calculation: requiredElement("envelopeActive").checked,
      surfaces: [...document.querySelectorAll(".envelope-boundary")].map(row => ({
        surface_id: row.querySelector(".boundary-id").value.trim(), owner_zone_id: row.querySelector(".boundary-zone").value.trim(), owner_room_id: "",
        kind: row.querySelector(".boundary-kind").value, orientation: row.querySelector(".boundary-orientation").value,
        area_m2: blankToNull(row.querySelector(".boundary-area").value), construction_id: row.querySelector(".boundary-construction").value.trim(), window_id: "", shading_record_ids: [],
        boundary_method: row.querySelector(".boundary-method").value, adjacent_temperature_c: blankToNull(row.querySelector(".boundary-temp").value),
        review_status: row.querySelector(".boundary-status").value, source: row.querySelector(".boundary-source").value.trim(), citations: [],
        manual_solar: {enabled: row.querySelector(".boundary-solar-enabled").checked, solar_design_w_m2: blankToNull(row.querySelector(".boundary-solar-design").value), solar_gain_factor: blankToNull(row.querySelector(".boundary-solar-gain").value), shading_factor: blankToNull(row.querySelector(".boundary-solar-shade").value), review_status: row.querySelector(".boundary-solar-status").value, source: row.querySelector(".boundary-solar-source").value.trim(), citations: []},
      })),
    },
  };
}

function showEnvelope(library = {}, model = {}, readiness = {}){
  requiredElement("envelopeConstructions").innerHTML = "";
  (library.constructions || []).forEach(addEnvelopeConstruction);
  requiredElement("envelopeWindows").value = JSON.stringify(library.windows || [], null, 2);
  requiredElement("envelopeShading").value = JSON.stringify(library.shading_records || [], null, 2);
  requiredElement("envelopeSurfaces").innerHTML = "";
  (model.surfaces || []).forEach(addEnvelopeBoundary);
  requiredElement("envelopeActive").checked = Boolean(model.active_for_calculation);
  const rows = [
    ...(readiness.included || []).map(item => ["Included", item]),
    ...(readiness.blocked || []).map(item => ["Blocked", item]),
    ...(readiness.stored_not_calculated || []).map(item => ["Stored only", item]),
  ];
  requiredElement("envelopeStatus").textContent = readiness.active_for_calculation ? `${readiness.status || "review required"} · reviewed model active` : "Legacy envelope remains active until reviewed model is saved and activated.";
  requiredElement("envelopeReadiness").innerHTML = rows.length ? rows.map(([state, item]) => `<article class="review-item"><div><b>${esc(state)} · ${esc(item.surface_id)}</b><span>${esc(item.kind || "surface")} · ${esc(item.reason || "reviewed steady-state opaque input")}</span></div></article>`).join("") : "<div class=\"review-empty\"><b>No reviewed envelope surfaces</b><span>Add reviewed records or seed legacy values as provisional.</span></div>";
}

async function loadEnvelope(){
  if (!DATA?.id) return;
  try {
    const [libraryRes, modelRes] = await Promise.all([
      fetch("/api/envelope-library?project_id=" + encodeURIComponent(DATA.id)), fetch("/api/envelope-model?project_id=" + encodeURIComponent(DATA.id)),
    ]);
    const library = await libraryRes.json(), model = await modelRes.json();
    if (libraryRes.ok && modelRes.ok && !library.error && !model.error) showEnvelope(library.envelope_library, model.envelope_model, model.readiness);
  } catch {}
}

async function saveEnvelope(){
  if (!DATA?.id) return;
  requiredElement("btnSaveEnvelope").disabled = true;
  requiredElement("envelopeStatus").textContent = "Saving reviewed envelope records…";
  try {
    const envelope = readEnvelope();
    let response = await fetch("/api/envelope-library", {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({project_id: DATA.id, envelope_library: envelope.envelope_library})});
    let data = await response.json();
    if (!response.ok || data.error) throw new Error(data.error || "Could not save envelope library.");
    response = await fetch("/api/envelope-model", {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({project_id: DATA.id, envelope_model: envelope.envelope_model})});
    data = await response.json();
    if (!response.ok || data.error) throw new Error(data.error || "Could not save envelope model.");
    showEnvelope(envelope.envelope_library, data.envelope_model, data.readiness);
    toast("Envelope saved", "Cooling reports using this project are now stale until recalculated.");
  } catch (error) {
    requiredElement("envelopeStatus").textContent = "Could not save envelope records.";
    toast("Envelope save failed", error.message);
  }
  requiredElement("btnSaveEnvelope").disabled = false;
}

async function migrateEnvelope(){
  if (!DATA?.id) return;
  requiredElement("btnMigrateEnvelope").disabled = true;
  try {
    const response = await fetch("/api/envelope-model", {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({project_id: DATA.id, action:"migrate_legacy"})});
    const data = await response.json();
    if (!response.ok || data.error) throw new Error(data.error || "Could not migrate legacy envelope inputs.");
    await loadEnvelope();
    toast("Legacy inputs seeded", "Migrated records are provisional and the legacy calculation path remains active.");
  } catch (error) {
    toast("Envelope migration failed", error.message);
  }
  requiredElement("btnMigrateEnvelope").disabled = false;
}

async function loadThermalModel(){
  if (!DATA?.id) return;
  try {
    const res = await fetch("/api/thermal-model?project_id=" + encodeURIComponent(DATA.id));
    const data = await res.json();
    if (res.ok && !data.error) showThermalModel(data.evidence, data.model, data.evidence_url, data.model_url, data.drawing_coverage, data.drawing_coverage_url, data.building_evidence, data.building_evidence_url);
  } catch {}
}

function showThermalModel(evidence = {}, model = {}, evidenceUrl = "", modelUrl = "", coverage = {}, coverageUrl = "", building = {}, buildingUrl = ""){
  const facts = evidence.facts || [];
  requiredElement("thermalFacts").innerHTML = facts.length ? facts.map(fact => `
    <article class="review-item"><div><b>${esc(fact.field.replaceAll("_", " "))}</b><span>${esc(typeof fact.value === "object" ? fact.value.name : fact.value)} ${esc(fact.unit || "")}</span><small>Page ${esc(fact.evidence?.[0]?.page || "?")} · ${esc(fact.evidence?.[0]?.excerpt || "drawing evidence")}</small></div>
    <label>Value <input class="thermal-fact-value" data-field="${esc(fact.field)}" value="${esc(typeof fact.value === "object" ? fact.value.name : fact.value)}"></label><label>Decision <select class="thermal-fact-decision" data-field="${esc(fact.field)}"><option value="accept">Accept</option><option value="edit">Edit</option><option value="reject">Reject</option><option value="not_applicable">Not applicable</option></select></label>
  </article>`).join("") : "";
  const coverageItems = coverage.coverage_exceptions || model.drawing_coverage?.coverage_exceptions || [];
  const levels = coverage.levels || model.drawing_coverage?.levels || [];
  requiredElement("thermalReviewItems").innerHTML = (model.review_items || []).concat(coverageItems).map(item => `<article class="review-item"><div><b>${item.level_name ? "Drawing coverage · " + esc(item.level_name) : "Needs confirmation"}</b><span>${esc(item.question)}</span></div><label>Decision <select class="thermal-review-decision" data-item="${esc(item.item_id)}"><option value="missing">Keep open</option><option value="accept">Accept</option><option value="edit">Edit</option><option value="reject">Reject</option><option value="not_applicable">Not applicable</option></select></label></article>`).join("");
  const levelSummary = levels.map(level => `${level.level_name}: ${level.proposed_purpose || "purpose unknown"} (${level.page_numbers?.join(", ") || "no pages"})`).join(" · ");
  const buildingSummary = ["spaces", "surfaces", "openings", "constructions", "lighting", "equipment"].map(key => `${key} ${building[key]?.length || 0}`).join(" · ");
  requiredElement("thermalModelLinks").innerHTML = (levelSummary ? `<article class="review-item"><div><b>Linked drawing evidence</b><span>${esc(levelSummary)}</span></div></article>` : "") + (buildingUrl ? `<article class="review-item"><div><b>Building evidence</b><span>${esc(buildingSummary)}</span></div><a class="btn ghost mini" href="${buildingUrl}" target="_blank" rel="noopener">Open</a></article>` : "") + [["drawing_coverage.json", coverageUrl], ["thermal_evidence.json", evidenceUrl], ["thermal_model.json", modelUrl]]
    .filter(([, url]) => url).map(([label, url]) => `<article class="review-item"><div><b>${esc(label)}</b></div><a class="btn ghost mini" href="${url}" target="_blank" rel="noopener">Open</a></article>`).join("");
  requiredElement("thermalModelStatus").textContent = model.status
    ? `${model.status} · ${model.evidence_summary?.direct_fact_count || 0} direct facts · ${model.evidence_summary?.exception_count || 0} exceptions to review`
    : "Build a draft thermal model from the reviewed drawing packet.";
}

function thermalDecisions(){
  const values = Object.fromEntries([...document.querySelectorAll(".thermal-fact-value")].map(input => [input.dataset.field, input.value]));
  return {
    facts: Object.fromEntries([...document.querySelectorAll(".thermal-fact-decision")].map(select => [select.dataset.field, {decision: select.value, value: values[select.dataset.field]}])),
    review_items: Object.fromEntries([...document.querySelectorAll(".thermal-review-decision")].map(select => [select.dataset.item, {decision: select.value}])),
  };
}

async function saveThermalModel(action){
  if (!DATA?.id) return;
  const button = action === "build" ? requiredElement("btnBuildThermalModel") : requiredElement("btnApplyThermalModel");
  button.disabled = true;
  requiredElement("thermalModelStatus").textContent = action === "build" ? "Building cited thermal-model draft…" : "Applying reviewed facts and refreshing reasoning packet…";
  try {
    const res = await fetch("/api/thermal-model", {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({project_id: DATA.id, action, decisions: thermalDecisions()}),
    });
    const data = await res.json();
    if (!res.ok || data.error) throw new Error(data.error || "Could not save thermal model.");
    showThermalModel(data.thermal_evidence, data.thermal_model, data.thermal_evidence_url, data.thermal_model_url, data.drawing_coverage, data.drawing_coverage_url, data.building_evidence, data.building_evidence_url);
    if (action === "save") showDesignRequirements(data.requirements, data.requirements_readiness || {}, [], {}, data.heat_load_status, {}, data.ventilation_status);
    toast(action === "build" ? "Thermal model drafted" : "Reviewed facts applied", action === "build" ? "Direct evidence is pre-filled; review the listed exceptions." : "The reasoning packet was refreshed and earlier reports are stale.");
  } catch (error) {
    requiredElement("thermalModelStatus").textContent = "Could not update thermal model.";
    toast("Thermal model failed", error.message);
  }
  button.disabled = false;
}

async function loadCalculatorDraft(){
  if (!DATA?.id) return;
  try {
    const res = await fetch("/api/calculator-draft?project_id=" + encodeURIComponent(DATA.id));
    const data = await res.json();
    if (res.ok && !data.error) showCalculatorDraft(data.calculator_draft || {}, data.artifact_url || "");
  } catch {}
}

function showCalculatorDraft(draft = {}, artifactUrl = ""){
  const groups = [
    ["floors", "Floors and drawing coverage"], ["zones", "Zones and rooms"], ["rooms", "Zones and rooms"],
    ["room_inputs", "Directly supported room inputs"], ["schedules", "Schedules"], ["envelope", "Envelope and opening candidates"],
  ];
  const decisions = draft.decisions || {};
  const rendered = groups.map(([key, title]) => {
    const rows = draft.candidates?.[key] || [];
    if (!rows.length) return "";
    return `<section class="draft-group"><div class="draft-group-title">${esc(title)}</div>${rows.map(item => calculatorDraftCandidateMarkup(item, decisions[item.candidate_id] || {})).join("")}</section>`;
  }).join("");
  requiredElement("calculatorDraftCandidates").innerHTML = rendered || (draft.status === "not_built" ? "" : `<article class="review-empty"><b>No source-backed calculator candidates found</b><span>Missing data stays in the review queue; Archie has not guessed any records.</span></article>`);
  const reviewItems = draft.review_items || [];
  requiredElement("calculatorDraftReviewItems").innerHTML = reviewItems.map(item => `<article class="review-item draft-review"><div><b>${esc(item.scope || "Review")} · ${esc(item.affected_id || "project")}</b><span>${esc(item.reason || item.question || "Evidence required.")}</span><small>${citationText(item.citations)}</small></div><label>Decision <select class="calculator-draft-decision" data-candidate="${esc(item.item_id)}"><option value="pending">Keep open</option><option value="needs_evidence">Needs evidence</option><option value="reject">Reject</option></select></label></article>`).join("");
  const summary = draft.apply_summary || {};
  const counts = [
    summary.created?.length && `${summary.created.length} created`,
    summary.already_present?.length && `${summary.already_present.length} already present`,
    summary.skipped_conflicts?.length && `${summary.skipped_conflicts.length} conflict${summary.skipped_conflicts.length === 1 ? "" : "s"} skipped`,
    summary.unresolved?.length && `${summary.unresolved.length} unresolved`,
  ].filter(Boolean).join(" · ");
  requiredElement("calculatorDraftSummary").innerHTML = (counts || artifactUrl) ? `<article class="review-item"><div><b>Draft status</b><span>${esc(counts || "No accepted proposals have been applied.")}</span></div>${artifactUrl ? `<a class="btn ghost mini" href="${esc(artifactUrl)}" target="_blank" rel="noopener">Open draft JSON</a>` : ""}</article>` : "";
  requiredElement("calculatorDraftStatus").textContent = draft.status === "not_built" || !draft.status
    ? "Build a proposal queue after the thermal model and drawing evidence are ready."
    : `${draft.status} · ${(Object.values(draft.candidates || {}).flat()).length} source-backed proposals · ${reviewItems.length} items needing review`;
}

function calculatorDraftCandidateMarkup(item, savedDecision){
  const action = savedDecision.decision || "pending";
  const value = savedDecision.value || item.value || {};
  return `<article class="review-item draft-candidate" data-candidate="${esc(item.candidate_id)}"><div><b>${esc(item.kind)} · ${esc(item.candidate_id)}</b><span>${esc(item.reason || "Source-backed proposal")}</span><small>${citationText(item.citations)} · confidence ${esc(item.confidence || "unknown")} · target ${esc(item.target_artifact || "")}</small><details><summary>Review/edit proposed record</summary><textarea class="calculator-draft-value" spellcheck="false">${esc(JSON.stringify(value, null, 2))}</textarea></details></div><label>Decision <select class="calculator-draft-decision" data-candidate="${esc(item.candidate_id)}"><option value="pending">Pending review</option><option value="accept">Accept</option><option value="edit">Edit</option><option value="reject">Reject</option><option value="needs_evidence">Needs evidence</option></select></label></article>`;
}

function citationText(citations = []){
  const citation = citations[0] || {};
  return citation.reference ? `${citation.reference}${citation.page ? ` · page ${citation.page}` : ""}${citation.excerpt ? ` · ${citation.excerpt}` : ""}` : "No citation";
}

function calculatorDraftDecisions(){
  const decisions = {};
  document.querySelectorAll(".calculator-draft-decision[data-candidate]").forEach(select => {
    const candidateId = select.dataset.candidate;
    const action = select.value;
    if (action === "pending") return;
    const row = select.closest(".draft-candidate");
    const decision = {decision: action};
    if (action === "edit") {
      const textarea = row?.querySelector(".calculator-draft-value");
      try { decision.value = JSON.parse(textarea?.value || "{}"); }
      catch (_) { throw new Error(`Edited proposal ${candidateId} must be valid JSON.`); }
    }
    decisions[candidateId] = decision;
  });
  return decisions;
}

async function saveCalculatorDraft(action){
  if (!DATA?.id) return;
  const button = action === "build" ? requiredElement("btnBuildCalculatorDraft") : requiredElement("btnApplyCalculatorDraft");
  button.disabled = true;
  requiredElement("calculatorDraftStatus").textContent = action === "build" ? "Building a source-backed calculator proposal queue…" : "Applying accepted proposals without overwriting authored records…";
  try {
    const payload = {project_id: DATA.id, action};
    if (action === "apply") payload.decisions = calculatorDraftDecisions();
    const res = await fetch("/api/calculator-draft", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(payload)});
    const data = await res.json();
    if (!res.ok || data.error) throw new Error(data.error || "Could not update calculator draft.");
    showCalculatorDraft(data.calculator_draft || {}, data.artifact_url || data.artifact_links?.calculator_draft || "");
    if (action === "apply") {
      await Promise.all([loadHourlyModel(), loadEnvelope(), loadHourlyLoadReport()]);
      toast("Accepted proposals applied", `${data.apply_summary?.created?.length || 0} records created; conflicts and unresolved evidence remain visible.`);
    } else {
      toast("Calculator draft built", "No calculator artifacts or cooling results were changed.");
    }
  } catch (error) {
    requiredElement("calculatorDraftStatus").textContent = "Could not update calculator draft.";
    toast("Calculator draft failed", error.message);
  }
  button.disabled = false;
}

async function loadDesignRequirements(){
  if (!DATA?.id) return;
  try {
    const res = await fetch("/api/design-requirements?project_id=" + encodeURIComponent(DATA.id));
    const data = await res.json();
    if (res.ok && !data.error) showDesignRequirements(data.requirements, data.readiness, data.room_suggestions, data.heat_load_report, data.heat_load_status, data.ventilation_report, data.ventilation_status, data.heat_load_report_url);
  } catch {}
}

async function saveDesignRequirements(){
  if (!DATA?.id) return;
  requiredElement("btnSaveRequirements").disabled = true;
  requiredElement("requirementsStatus").textContent = "Saving design inputs and refreshing reasoning packet…";
  try {
    const res = await fetch("/api/design-requirements", {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({project_id: DATA.id, requirements: readDesignRequirements()}),
    });
    const data = await res.json();
    if (!res.ok || data.error) throw new Error(data.error || "Could not save design inputs.");
    showDesignRequirements(data.requirements, data.requirements_readiness, data.room_suggestions, {}, data.heat_load_status, {}, data.ventilation_status);
    const links = [["design_requirements.json", data.requirements_url], ["refreshed reasoning packet", data.reasoning_zip_url]]
      .filter(([, url]) => url);
    requiredElement("requirementsLinks").innerHTML = links.map(([label, url]) => `<article class="review-item"><div><b>${esc(label)}</b></div><a class="btn ghost mini" href="${url}" target="_blank" rel="noopener">Open</a></article>`).join("");
    toast("Design inputs saved", "The reasoning packet was refreshed with designer-provided requirements.");
  } catch (error) {
    requiredElement("requirementsStatus").textContent = "Could not save design inputs.";
    toast("Design inputs failed", error.message);
  }
  requiredElement("btnSaveRequirements").disabled = false;
}

function addHourlyFloor(floor = {}){
  const row = document.createElement("div");
  row.className = "hierarchy-row";
  row.innerHTML = `<input class="hourly-floor-id" placeholder="Floor ID" value="${esc(floor.floor_id || "")}">
    <input class="hourly-floor-name" placeholder="Floor name" value="${esc(floor.name || "")}">
    <input class="hourly-floor-elevation" type="number" step="0.01" placeholder="Elevation m (optional)" value="${floor.elevation_m ?? ""}">
    <select class="hourly-floor-status"><option value="missing">Missing</option><option value="provisional">Provisional</option><option value="confirmed">Confirmed</option></select>
    <input class="hourly-floor-source" placeholder="Reviewed source" value="${esc(floor.source || "")}">
    <button class="btn ghost mini" type="button">Remove</button>`;
  row.querySelector(".hourly-floor-status").value = floor.verification_status || "missing";
  row.querySelector("button").addEventListener("click", () => row.remove());
  requiredElement("hourlyFloors").appendChild(row);
}

function addHourlyZone(zone = {}){
  const row = document.createElement("div");
  row.className = "hierarchy-row";
  row.innerHTML = `<input class="hourly-zone-id" placeholder="Zone ID" value="${esc(zone.zone_id || "")}">
    <input class="hourly-zone-name" placeholder="Zone name" value="${esc(zone.name || "")}">
    <input class="hourly-zone-floor" placeholder="Floor ID" value="${esc(zone.floor_id || "")}">
    <select class="hourly-zone-status"><option value="missing">Missing</option><option value="provisional">Provisional</option><option value="confirmed">Confirmed</option></select>
    <input class="hourly-zone-source" placeholder="Reviewed source" value="${esc(zone.source || "")}">
    <button class="btn ghost mini" type="button">Remove</button>`;
  row.querySelector(".hourly-zone-status").value = zone.verification_status || "missing";
  row.querySelector("button").addEventListener("click", () => row.remove());
  requiredElement("hourlyZones").appendChild(row);
}

const ROOM_COMPONENT_TYPES = [
  ["infiltration", "Infiltration", "airflow"], ["minimum_supply_air", "Minimum supply air", "airflow"],
  ["extract_air", "Extract air", "airflow"], ["spill_air", "Spill air", "airflow"],
  ["transfer_air", "Transfer air", "airflow"], ["make_up_air", "Make-up air", "airflow"],
  ["vapour_gain", "Vapour gain", "moisture"], ["steam_gain", "Steam gain", "moisture"],
  ["process_latent_load", "Process latent load", "moisture"],
];

function defaultRoomComponents(){
  return ROOM_COMPONENT_TYPES.map(([component_type]) => ({
    component_id: component_type, component_type, value: null, unit: "", source_room_id: "", source: "", citations: [],
    verification_status: "missing", calculation_status: "not_assessed",
  }));
}

function normaliseRoomComponents(components = []){
  const supplied = new Set((components || []).map(item => item.component_type));
  return [...(components || []), ...defaultRoomComponents().filter(item => !supplied.has(item.component_type))];
}

function roomComponentMarkup(component = {}){
  const typeOptions = ROOM_COMPONENT_TYPES.map(([value, label]) => `<option value="${value}">${label}</option>`).join("");
  const citation = component.citations?.[0]?.reference || "";
  return `<div class="room-component">
    <input class="room-component-id" placeholder="Component ID" value="${esc(component.component_id || "")}">
    <select class="room-component-type">${typeOptions}</select>
    <select class="room-component-state"><option value="not_assessed">Not assessed</option><option value="not_present_confirmed">Not present, confirmed</option><option value="stored_not_calculated">Stored, not calculated</option></select>
    <input class="room-component-value" type="number" min="0" step="any" placeholder="Raw value" value="${component.value ?? ""}">
    <input class="room-component-unit" placeholder="Unit (e.g. L/s)" value="${esc(component.unit || "")}">
    <input class="room-component-source-room" placeholder="Source room ID (transfer only)" value="${esc(component.source_room_id || "")}">
    <select class="room-component-status"><option value="missing">Missing</option><option value="provisional">Provisional</option><option value="confirmed">Confirmed</option></select>
    <input class="room-component-source" placeholder="Evidence source" value="${esc(component.source || "")}">
    <input class="room-component-citation" placeholder="Citation reference (optional)" value="${esc(citation)}">
  </div>`;
}

function addHourlyRoom(room = {}){
  const card = document.createElement("details");
  card.className = "room-input-card";
  card.open = true;
  const cooling = room.cooling_load || {};
  const conditions = room.cooling_load_conditions || {};
  const components = normaliseRoomComponents(room.unapproved_components);
  card.innerHTML = `<summary><span>${esc(room.name || room.room_id || "New room")}</span><small>Topology, supported cooling inputs, and excluded room inputs</small></summary>
    <div class="hierarchy-row">
      <input class="hourly-room-id" placeholder="Room ID" value="${esc(room.room_id || "")}">
      <input class="hourly-room-name" placeholder="Room name" value="${esc(room.name || "")}">
      <input class="hourly-room-zone" placeholder="Zone ID" value="${esc(room.zone_id || "")}">
      <select class="hourly-room-mapping"><option value="inferred">Inferred</option><option value="confirmed">Confirmed</option></select>
      <select class="hourly-room-status"><option value="missing">Missing</option><option value="provisional">Provisional</option><option value="confirmed">Confirmed</option></select>
      <input class="hourly-room-source" placeholder="Reviewed source" value="${esc(room.source || "")}">
      <button class="btn ghost mini" type="button">Remove</button>
    </div>
    <div class="room-input-grid">
      <fieldset><legend>Supported cooling inputs</legend>
        <label>Area m²<input class="room-area" type="number" min="0" step="any" value="${room.area_m2 ?? ""}"></label>
        <label>Occupancy<input class="room-occupancy" type="number" min="0" step="any" value="${room.occupancy ?? ""}"></label>
        <label>Cooling setpoint °C<input class="room-setpoint" type="number" step="any" value="${room.indoor_cooling_setpoint_c ?? ""}"></label>
        <label>People sensible W/person<input class="room-people-sensible" type="number" min="0" step="any" value="${cooling.people_sensible_w_per_person ?? ""}"></label>
        <label>People latent W/person<input class="room-people-latent" type="number" min="0" step="any" value="${cooling.people_latent_w_per_person ?? ""}"></label>
        <label>People diversity<input class="room-people-diversity" type="number" min="0" step="any" value="${cooling.people_diversity_factor ?? ""}"></label>
        <label>Lighting W/m²<input class="room-lighting" type="number" min="0" step="any" value="${cooling.lighting_w_m2 ?? ""}"></label>
        <label>Lighting diversity<input class="room-lighting-diversity" type="number" min="0" step="any" value="${cooling.lighting_diversity_factor ?? ""}"></label>
        <label>Outside-air cooling L/s<input class="room-outside-air" type="number" min="0" step="any" value="${cooling.outside_air_lps ?? ""}"></label>
        <label>Safety factor<input class="room-safety" type="number" min="0" step="any" value="${cooling.safety_factor ?? ""}"></label>
        <label>Cooling input status<select class="room-cooling-status"><option value="missing">Missing</option><option value="provisional">Provisional</option><option value="confirmed">Confirmed</option></select></label>
        <label>Cooling input source<input class="room-cooling-source" value="${esc(cooling.source || "")}"></label>
        <label>Indoor cooling WB °C<input class="room-wet-bulb" type="number" step="any" value="${conditions.indoor_cooling_wet_bulb_c ?? ""}"></label>
        <label>Condition status<select class="room-condition-status"><option value="missing">Missing</option><option value="provisional">Provisional</option><option value="confirmed">Confirmed</option></select></label>
        <label>Condition source<input class="room-condition-source" value="${esc(conditions.source || "")}"></label>
      </fieldset>
      <fieldset><legend>Schedules</legend>
        <label>People schedule<input class="room-people-schedule" value="${esc(room.schedule_assignments?.people || "")}"></label>
        <label>Lighting schedule<input class="room-lighting-schedule" value="${esc(room.schedule_assignments?.lighting || "")}"></label>
        <label>Outside-air schedule<input class="room-outside-air-schedule" value="${esc(room.schedule_assignments?.outside_air || "")}"></label>
        <p class="fine">Heat-source and solar schedules remain tied to their existing reviewed source/surface records.</p>
      </fieldset>
      <fieldset class="room-airflow-components"><legend>Airflow declarations — stored, not calculated</legend>${components.filter(item => ROOM_COMPONENT_TYPES.find(row => row[0] === item.component_type)?.[2] === "airflow").map(roomComponentMarkup).join("")}</fieldset>
      <fieldset class="room-moisture-components"><legend>Moisture and process declarations — stored, not calculated</legend>${components.filter(item => ROOM_COMPONENT_TYPES.find(row => row[0] === item.component_type)?.[2] === "moisture").map(roomComponentMarkup).join("")}</fieldset>
    </div>`;
  card.querySelector(".hourly-room-mapping").value = room.mapping_status || "inferred";
  card.querySelector(".hourly-room-status").value = room.verification_status || "missing";
  card.querySelector(".room-cooling-status").value = cooling.verification_status || "missing";
  card.querySelector(".room-condition-status").value = conditions.verification_status || "missing";
  card.querySelectorAll(".room-component").forEach((row, index) => {
    const componentId = row.querySelector(".room-component-id").value;
    const component = components.find(item => item.component_id === componentId) || components[index];
    row.querySelector(".room-component-type").value = component.component_type || "infiltration";
    row.querySelector(".room-component-state").value = component.calculation_status || "not_assessed";
    row.querySelector(".room-component-status").value = component.verification_status || "missing";
  });
  card.querySelector("button").addEventListener("click", () => card.remove());
  requiredElement("hourlyRooms").appendChild(card);
}

function showHourlyModel(model = {}, readiness = {}){
  requiredElement("hourlyFloors").innerHTML = "";
  requiredElement("hourlyZones").innerHTML = "";
  requiredElement("hourlyRooms").innerHTML = "";
  (model.floors || []).forEach(addHourlyFloor);
  (model.zones || []).forEach(addHourlyZone);
  (model.rooms || []).forEach(addHourlyRoom);
  const issues = readiness.issues || [];
  requiredElement("hourlyModelStatus").textContent = readiness.status
    ? `${readiness.status} · ${model.floors?.length || 0} floors · ${model.zones?.length || 0} zones · ${model.rooms?.length || 0} rooms${issues.length ? ` · ${issues.length} readiness items` : ""}`
    : "Seed or load the room model to review topology.";
  drawRoomInputCoverage(model);
}

function drawRoomInputCoverage(model = {}){
  const rows = (model.rooms || []).map(room => {
    const components = normaliseRoomComponents(room.unapproved_components);
    const stored = components.filter(item => item.calculation_status === "stored_not_calculated");
    const unassessed = components.filter(item => item.calculation_status === "not_assessed");
    if (!stored.length && !unassessed.length) {
      return `<article class="review-item readiness-review_ready"><div><b>${esc(room.room_id)} · reviewed supported room scope</b><span>All airflow and moisture categories are confirmed not present.</span></div></article>`;
    }
    const storedText = stored.map(item => `${item.component_type}${item.value !== null && item.value !== undefined ? ` (${item.value} ${item.unit})` : ""}`).join(", ");
    const unassessedText = unassessed.map(item => item.component_type).join(", ");
    return `<article class="review-item readiness-draft"><div><b>${esc(room.room_id)} · room input coverage incomplete</b><span>${storedText ? `Stored, excluded: ${storedText}. ` : ""}${unassessedText ? `Assess: ${unassessedText}.` : ""}</span></div></article>`;
  });
  requiredElement("roomInputCoverage").innerHTML = rows.length ? `<div class="panel-head hierarchy-heading"><div><div class="micro">Room input coverage</div><h3>Known exclusions and unresolved categories</h3></div></div>${rows.join("")}` : "";
}

function readHourlyModel(){
  const floors = [...requiredElement("hourlyFloors").querySelectorAll(".hierarchy-row")].map(row => ({
    floor_id: row.querySelector(".hourly-floor-id").value.trim(), name: row.querySelector(".hourly-floor-name").value.trim(),
    elevation_m: blankToNull(row.querySelector(".hourly-floor-elevation").value), verification_status: row.querySelector(".hourly-floor-status").value,
    source: row.querySelector(".hourly-floor-source").value.trim(), citations: [],
  }));
  const zones = [...requiredElement("hourlyZones").querySelectorAll(".hierarchy-row")].map(row => ({
    zone_id: row.querySelector(".hourly-zone-id").value.trim(), name: row.querySelector(".hourly-zone-name").value.trim(), floor_id: row.querySelector(".hourly-zone-floor").value.trim(),
    verification_status: row.querySelector(".hourly-zone-status").value, source: row.querySelector(".hourly-zone-source").value.trim(), citations: [],
  }));
  const roomsById = new Map((HOURLY_MODEL.rooms || []).map(room => [room.room_id, room]));
  const rooms = [...requiredElement("hourlyRooms").querySelectorAll(".room-input-card")].map(card => {
    const row = requiredElement("hourlyRooms") && card.querySelector(".hierarchy-row");
    const roomId = row.querySelector(".hourly-room-id").value.trim();
    const existing = roomsById.get(roomId) || {};
    const load = {...(existing.cooling_load || {})};
    const conditions = {...(existing.cooling_load_conditions || {})};
    const existingComponentsById = new Map((existing.unapproved_components || []).map(item => [item.component_id, item]));
    const components = [...card.querySelectorAll(".room-component")].map(component => {
      const citation = component.querySelector(".room-component-citation").value.trim();
      const componentId = component.querySelector(".room-component-id").value.trim();
      const original = existingComponentsById.get(componentId);
      const originalCitation = original?.citations?.[0]?.reference || "";
      return {
        component_id: componentId, component_type: component.querySelector(".room-component-type").value,
        calculation_status: component.querySelector(".room-component-state").value,
        value: blankToNull(component.querySelector(".room-component-value").value), unit: component.querySelector(".room-component-unit").value.trim(),
        source_room_id: component.querySelector(".room-component-source-room").value.trim(), verification_status: component.querySelector(".room-component-status").value,
        source: component.querySelector(".room-component-source").value.trim(),
        citations: citation === originalCitation ? (original?.citations || []) : (citation ? [{reference: citation, page: null, excerpt: ""}] : []),
      };
    });
    Object.assign(load, {
      people_sensible_w_per_person: blankToNull(card.querySelector(".room-people-sensible").value), people_latent_w_per_person: blankToNull(card.querySelector(".room-people-latent").value),
      people_diversity_factor: blankToNull(card.querySelector(".room-people-diversity").value), lighting_w_m2: blankToNull(card.querySelector(".room-lighting").value),
      lighting_diversity_factor: blankToNull(card.querySelector(".room-lighting-diversity").value), outside_air_lps: blankToNull(card.querySelector(".room-outside-air").value),
      safety_factor: blankToNull(card.querySelector(".room-safety").value), verification_status: card.querySelector(".room-cooling-status").value,
      source: card.querySelector(".room-cooling-source").value.trim(),
    });
    Object.assign(conditions, {
      indoor_cooling_wet_bulb_c: blankToNull(card.querySelector(".room-wet-bulb").value), verification_status: card.querySelector(".room-condition-status").value,
      source: card.querySelector(".room-condition-source").value.trim(),
    });
    return {
      ...existing, room_id: roomId, name: row.querySelector(".hourly-room-name").value.trim(), zone_id: row.querySelector(".hourly-room-zone").value.trim(),
      mapping_status: row.querySelector(".hourly-room-mapping").value, verification_status: row.querySelector(".hourly-room-status").value,
      source: row.querySelector(".hourly-room-source").value.trim(), citations: [],
      area_m2: blankToNull(card.querySelector(".room-area").value), occupancy: blankToNull(card.querySelector(".room-occupancy").value),
      indoor_cooling_setpoint_c: blankToNull(card.querySelector(".room-setpoint").value), cooling_load: load, cooling_load_conditions: conditions,
      schedule_assignments: {...(existing.schedule_assignments || {}), people: card.querySelector(".room-people-schedule").value.trim(), lighting: card.querySelector(".room-lighting-schedule").value.trim(), outside_air: card.querySelector(".room-outside-air-schedule").value.trim()},
      unapproved_components: components,
    };
  });
  return {...HOURLY_MODEL, schema_version: 3, floors, zones, rooms};
}

let HOURLY_MODEL = {floors: [], zones: [], rooms: []};

async function loadHourlyModel(){
  if (!DATA?.id) return;
  try {
    const res = await fetch(`/api/hourly-load-model?project_id=${encodeURIComponent(DATA.id)}`);
    const data = await res.json();
    if (!res.ok || data.error) return;
    HOURLY_MODEL = data.hourly_load_model || HOURLY_MODEL;
    showHourlyModel(HOURLY_MODEL, data.readiness || {});
  } catch (_) { /* The main design-input workflow remains usable offline. */ }
}

async function saveHourlyModel(action){
  if (!DATA?.id) return;
  requiredElement("btnSaveHourlyModel").disabled = true;
  try {
    const payload = action === "build" ? {project_id: DATA.id, action} : {project_id: DATA.id, action, hourly_load_model: readHourlyModel()};
    const res = await fetch("/api/hourly-load-model", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(payload)});
    const data = await res.json();
    if (!res.ok || data.error) throw new Error(data.error || "Could not save the hourly hierarchy.");
    HOURLY_MODEL = data.hourly_load_model;
    showHourlyModel(HOURLY_MODEL, data.readiness || {});
    toast(action === "build" ? "Hourly model seeded" : "Hierarchy saved", "Review topology status before calculating cooling loads.");
  } catch (error) {
    requiredElement("hourlyModelStatus").textContent = "Could not save the hourly hierarchy.";
    toast("Hourly hierarchy failed", error.message);
  }
  requiredElement("btnSaveHourlyModel").disabled = false;
}

function drawCoolingReadiness(readiness = {}, artifacts = {}){
  const issues = readiness.issues || [];
  const groups = issues.reduce((result, item) => {
    (result[item.scope || "project"] ||= []).push(item);
    return result;
  }, {});
  requiredElement("coolingReadiness").innerHTML = Object.entries(groups).map(([scope, rows]) => `<section class="readiness-group"><h4>${esc(scope)}</h4>${rows.map(item => {
    const artifact = artifacts[item.source_artifact] || {};
    const citations = item.citations || [];
    const source = [item.source_artifact, item.input_source, ...citations.map(citation => citation.label || citation.reference || citation.url || "citation")].filter(Boolean).join(" · ");
    const link = artifact.artifact_url
      ? `<a class="btn ghost mini" href="${esc(artifact.artifact_url)}" target="_blank" rel="noopener">Open evidence</a>`
      : "";
    return `<article class="review-item readiness-${esc(item.status)}"><div><b>${esc(item.scope)} · ${esc(item.affected_id)}</b><span>${esc(item.reason)}</span><small>${esc(source)}</small></div>${link}</article>`;
  }).join("")}</section>`).join("");
}

function drawHourlyLoadReport(report = {}, artifactStatus = "not_calculated"){
  const status = report.status || artifactStatus;
  requiredElement("hourlyReportStatus").textContent = artifactStatus === "stale"
    ? "Cooling Load Report is stale. Refresh the changed inputs and calculate again."
    : `${status} · ${report.scope_summary?.complete_scope ? "complete room scope" : "included room scope only"}`;
  drawCoolingReadiness(report.readiness || {}, report.input_artifacts || {});
  const scenarios = report.scenario_results || [];
  const scopeRows = [
    ...(report.known_exclusions || []).map(item => `<article class="heat-load-result"><b>${esc(item.room_id)} · known excluded room input</b><span>${esc(`${item.component_type}: ${item.value} ${item.unit}`)} · ${esc(item.source || "source required")}</span></article>`),
    ...(report.unresolved_room_inputs || []).map(item => `<article class="heat-load-result"><b>${esc(item.room_id)} · unresolved room input</b><span>Assess ${esc(item.component_type)} before calling this a complete room scope.</span></article>`),
  ];
  requiredElement("hourlyLoadResults").innerHTML = [...scenarios.map(scenario => {
    const peak = scenario.included_scope_peak || {};
    const blocked = scenario.scope_summary?.blocked_rooms || [];
    return `<article class="heat-load-result"><b>${esc(scenario.title || scenario.scenario_id)} · ${esc(scenario.status)}</b>
      <span>Included-scope subtotal peak ${Number(peak.design_total_kw || 0).toFixed(2)} kW${scenario.scope_summary?.complete_scope ? "" : " · not a complete project duty"}</span>
      ${blocked.length ? `<span>Omitted rooms: ${esc(blocked.map(item => item.room_id).join(", "))}</span>` : ""}</article>`;
  }), ...scopeRows].join("");
}

async function loadHourlyLoadReport(){
  if (!DATA?.id) return;
  try {
    const res = await fetch(`/api/hourly-load-report?project_id=${encodeURIComponent(DATA.id)}`);
    const data = await res.json();
    if (!res.ok || data.error) return;
    drawHourlyLoadReport(data.hourly_load_report || {}, data.status || "not_calculated");
  } catch (_) { /* Report is optional until all hourly artifacts exist. */ }
}

async function calculateHourlyLoad(){
  if (!DATA?.id) return;
  requiredElement("btnCalculateHourlyLoad").disabled = true;
  requiredElement("hourlyReportStatus").textContent = "Calculating the hourly cooling report…";
  try {
    const ids = requiredElement("hourlyScenarioIds").value.split(",").map(value => value.trim()).filter(Boolean);
    const res = await fetch("/api/hourly-load-report", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({project_id: DATA.id, selected_scenario_ids: ids})});
    const data = await res.json();
    if (!res.ok || data.error) throw new Error(data.error || "Could not calculate the cooling report.");
    drawHourlyLoadReport(data.hourly_load_report, data.status);
    toast("Cooling report calculated", `Status: ${data.hourly_load_report.status}.`);
  } catch (error) {
    requiredElement("hourlyReportStatus").textContent = "Could not calculate the cooling report.";
    toast("Cooling report failed", error.message);
  }
  requiredElement("btnCalculateHourlyLoad").disabled = false;
}

async function calculateVentilation(){
  if (!DATA?.id) return;
  requiredElement("btnCalculateVentilation").disabled = true;
  requiredElement("ventilationStatus").textContent = "Calculating preliminary ventilation…";
  try {
    const res = await fetch("/api/ventilation", {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({project_id: DATA.id, requirements: readDesignRequirements()}),
    });
    const data = await res.json();
    if (!res.ok || data.error) throw new Error(data.error || "Could not calculate ventilation.");
    drawVentilationReport(data.ventilation_report, data.ventilation_status);
    const links = [["ventilation_report.json", data.ventilation_report_url], ["refreshed reasoning packet", data.reasoning_zip_url]]
      .filter(([, url]) => url);
    requiredElement("requirementsLinks").innerHTML = links.map(([label, url]) => `<article class="review-item"><div><b>${esc(label)}</b></div><a class="btn ghost mini" href="${url}" target="_blank" rel="noopener">Open</a></article>`).join("");
    toast("Ventilation calculated", `Status: ${data.ventilation_report.status}.`);
  } catch (error) {
    requiredElement("ventilationStatus").textContent = "Could not calculate ventilation.";
    toast("Ventilation calculation failed", error.message);
  }
  requiredElement("btnCalculateVentilation").disabled = false;
}

function drawVentilationReport(report = {}, reportStatus = "not_calculated"){
  if (!report?.zone_results?.length) {
    requiredElement("ventilationStatus").textContent = reportStatus === "stale" ? "Ventilation report is stale. Calculate again after reviewing inputs." : "Enter zone ventilation inputs, then calculate a preliminary breakdown.";
    requiredElement("ventilationResults").innerHTML = "";
    return;
  }
  requiredElement("ventilationStatus").textContent = `${report.status} · ${report.calculated_zone_count} calculated · ${report.blocked_zone_count} blocked · Outside air ${Number(report.total_outside_air_lps || 0).toFixed(1)} L/s · Exhaust ${Number(report.total_process_exhaust_lps || 0).toFixed(1)} L/s`;
  requiredElement("ventilationResults").innerHTML = report.zone_results.map(zone => {
    if (zone.status === "blocked") return `<article class="heat-load-result"><b>${esc(zone.zone_name)}</b><span>Blocked: ${esc((zone.blocked_reasons || []).join(", "))}</span></article>`;
    const balance = zone.air_balance?.status === "evaluated" ? ` · Net balance ${Number(zone.air_balance.net_lps).toFixed(1)} L/s` : " · Air balance not evaluated";
    const warning = zone.warnings?.length ? `<span>${esc(zone.warnings.join(" "))}</span>` : "";
    return `<article class="heat-load-result"><b>${esc(zone.zone_name)} · ${esc(zone.status)}</b><span>Outside air ${Number(zone.outside_air.required_lps).toFixed(1)} L/s (${esc(zone.outside_air.governing_component)}) · Process exhaust ${Number(zone.process_exhaust_lps).toFixed(1)} L/s · Make-up air ${Number(zone.make_up_air.required_lps).toFixed(1)} L/s${balance}</span>${warning}</article>`;
  }).join("");
}

/* ---------------- projects ---------------- */
async function loadProjects(){
  try {
    const res = await fetch("/api/projects");
    const list = await res.json();
    if (!Array.isArray(list) || !list.length) return;
    requiredElement("projects").innerHTML = list.map(p => `
      <button class="proj ${DATA && p.id === DATA.id ? "on" : ""}" data-open="${esc(p.id)}">
        <b>${esc(p.name)}</b>
        <span>${p.analysed ? p.relevant + " of " + p.pages + " pages" : p.pages + " pages · not analysed"}</span>
      </button>`).join("");
    requiredElement("projects").querySelectorAll("[data-open]").forEach(b =>
      b.addEventListener("click", () => openProject(b.dataset.open)));
  } catch {}
}

async function openProject(id){
  show("vRun");
  requiredElement("topTitle").textContent = "Opening saved project";
  requiredElement("topSub").textContent = "Loading or rebuilding its analysis";
  requiredElement("runSub").textContent = "Preparing drawing pages";
  paintSpectrum([], 0, true);
  try {
    const res = await fetch("/api/analysis?id=" + encodeURIComponent(id));
    const data = await res.json();
    if (!res.ok || data.error) throw new Error(data.error || "That project has no saved analysis.");
    DATA = { id: data.id, name: data.name, pages: data.pages_analysed };
    requiredElement("btnRestart").classList.remove("hide");
    requiredElement("btnAnalyse").classList.add("hide");
    showResults(data);
  } catch (err) {
    DATA = null;
    show("vUpload");
    requiredElement("btnRestart").classList.add("hide");
    requiredElement("btnAnalyse").classList.add("hide");
    requiredElement("topTitle").textContent = "Project unavailable";
    requiredElement("topSub").textContent = "Upload the original PDF to analyse it again";
    toast("Could not open project", err.message);
  }
}

/* ---------------- utils ---------------- */
function show(id){
  ["vUpload","vFile","vRun","vRes"].forEach(v => requiredElement(v).classList.toggle("hide", v !== id));
}
function esc(v){
  return String(v ?? "").replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
}
function toast(title, body){
  document.querySelector(".toast")?.remove();
  const el = document.createElement("div");
  el.className = "toast";
  el.innerHTML = `<h4>${esc(title)}</h4><p>${body}</p>`;
  el.addEventListener("click", () => el.remove());
  document.body.appendChild(el);
  setTimeout(() => el.remove(), 12000);
}

loadProjects();

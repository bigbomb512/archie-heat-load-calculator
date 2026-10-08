(() => {
  const panel = document.getElementById("autonomousTasksPanel");
  if (!panel) return;
  const operatorMode = new URLSearchParams(location.search).get("operator") === "1";
  panel.hidden = !operatorMode;
  if (operatorMode) {
    panel.open = true;
    let parent = panel.parentElement;
    while (parent) {
      if (parent.tagName === "DETAILS") parent.open = true;
      parent.classList?.remove("hide");
      parent = parent.parentElement;
    }
  }
  const list = document.getElementById("autonomousTasksList");
  const status = document.getElementById("autonomousTasksStatus");
  const contractorQuestions = document.getElementById("contractorRoofQuestions");
  const contractorQuestionStatus = document.getElementById("contractorRoofQuestionStatus");
  const kitchenResults = document.getElementById("kitchenEquipmentResults");
  let activeProject = "";
  let response = null;

  const escapeHtml = value => String(value ?? "").replace(/[&<>"']/g, char => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[char]));
  const labelFor = row => row.stand_in ? "Stand-in (test)"
    : row.applied_value?.applied_source === "reviewer" || row.source === "reviewer" ? "Reviewer"
    : row.status === "below_accuracy_bar" || row.status === "below_accuracy"
    ? "AI-determined (below accuracy bar)"
    : row.status === "applied_fallback" ? (row.applied_value?.label || (row.task === "P1_site" ? "Assumed (rule-based site fallback)" : "Assumed (typical for the building type)"))
      : row.source === "reviewer" ? "Reviewer" : row.status === "applied" ? "AI-determined" : "";

  function render(data) {
    response = data;
    if (!operatorMode) {
      const questions = (data.tasks || []).filter(row => row.task === "P5_roof" && row.status === "needs_contractor_answer");
      const answered = (data.tasks || []).filter(row => row.task === "P5_roof" && row.status === "contractor_answered_not_sure");
      const applied = (data.tasks || []).filter(row => row.task === "P5_roof" && row.applied_value);
      contractorQuestions.innerHTML = [...questions.map(row => `<article class="panel-card" data-contractor-roof-question data-target="${escapeHtml(row.target)}"><h4>Roof above ${escapeHtml(row.room_label || "this shop")}</h4><p>${escapeHtml(row.question || "Is there a floor or another tenancy directly above this shop, or is it the roof?")}</p><fieldset><legend>Choose one answer</legend><label><input type="radio" name="roof-answer-${escapeHtml(row.target)}" value="floor_tenancy_above"> Floor/tenancy above</label><label><input type="radio" name="roof-answer-${escapeHtml(row.target)}" value="roof_directly_above"> Roof directly above</label><label><input type="radio" name="roof-answer-${escapeHtml(row.target)}" value="not_sure"> Not sure</label></fieldset><button type="button" class="btn key mini" data-submit-roof-answer>Save answer</button><p class="fine" role="status" aria-live="polite" data-roof-answer-status></p></article>`), ...answered.map(row => `<article class="panel-card" data-roof-not-assessed><h4>Roof above ${escapeHtml(row.room_label || "this shop")}</h4><p>${escapeHtml(row.message || "Not sure — roof exposure remains unknown and not assessed.")}</p></article>`), ...applied.map(row => `<article class="panel-card" data-roof-answer-recorded><h4>Roof above ${escapeHtml(row.applied_value.room || row.target)}</h4><p>${row.applied_value.roof === "exposed" ? "Roof directly above" : "Floor/tenancy above"} · ${escapeHtml(row.applied_value.label || "Roof exposure recorded")}</p></article>`)].join("");
      if (kitchenResults) kitchenResults.innerHTML = (data.tasks || []).filter(row => row.task === "P6_kitchen" && row.applied_value).map(row => {
        const items = row.applied_value.items || [];
        const list = items.length ? `<ul>${items.map(item => `<li>${escapeHtml(String(item.type || "").replaceAll("_", " "))} × ${escapeHtml(item.count)} · p. ${escapeHtml(item.page)}</li>`).join("")}</ul>` : "<p>No kitchen equipment was identified.</p>";
        return `<article class="panel-card" data-kitchen-equipment-result><h4>Kitchen equipment identified from drawings (heat not yet assessed)</h4>${list}<p class="fine">Equipment identification only; no heat contribution has been calculated.</p></article>`;
      }).join("");
      return;
    }
    const tasks = data.tasks || [];
    status.textContent = tasks.length ? `${tasks.filter(row => row.status === "waiting_for_reply").length} waiting for a reply · ${tasks.filter(row => row.status === "blocked").length} blocked · apply bar ${Math.round(data.auto_apply_bar * 100)}%.` : "No tasks are ready. Run all tasks to build bounded packets.";
    const skippedPages = (data.page_selection_notes || data.skipped_pages || []).map(row =>
      `<article class="panel-card" data-page-selection-note data-page="${escapeHtml(row.page)}"><h5>Room-outline page ${escapeHtml(row.page)} · ${escapeHtml(row.status || "skipped")}</h5><p>${escapeHtml(row.reason)}</p></article>`
    ).join("");
    list.innerHTML = skippedPages + tasks.map(row => {
      const displayStatus = row.status === "below_accuracy_bar" ? "Applied · below accuracy bar" :
        row.status === "applied_fallback" ? "Applied · fallback" : row.status.replaceAll("_", " ");
      const images = (row.images || []).map(image => `<li><a href="${escapeHtml(image.url)}" download>${escapeHtml(image.name)}</a></li>`).join("");
      const result = row.applied_value && Object.keys(row.applied_value).length ? `<p data-task-value>${escapeHtml(JSON.stringify(row.applied_value))}</p><p class="fine" data-task-source>${escapeHtml(labelFor(row))}</p>` : "";
      const standInLabel = !result && row.stand_in ? `<p class="fine" data-task-source>Stand-in (test)</p>` : "";
      const error = row.block_reason && row.validation?.valid !== false ? `<p class="error" role="alert" data-task-error>${escapeHtml(row.block_reason)}</p>` : "";
      const validationError = row.validation?.valid === false ? `<p class="error" role="alert">${escapeHtml(row.validation.error || row.block_reason)}</p>` : "";
      const crossCheck = row.cross_check?.status ? `<p class="fine" data-task-cross-check>Rule-based cross-check: ${escapeHtml(row.cross_check.status)}${row.cross_check.status === "disagrees" ? ` · ${escapeHtml(row.cross_check.rule_based_candidate?.text || "candidate unavailable")}` : ""}.</p>` : "";
      const awaiting = ["waiting_for_reply", "blocked"].includes(row.status);
      return `<article class="panel-card" data-task-card data-task="${escapeHtml(row.task)}" data-target="${escapeHtml(row.target)}"><h5>${escapeHtml(row.task)} · ${escapeHtml(row.target)}</h5><p data-task-status>${escapeHtml(displayStatus)}</p>${row.accuracy?.accuracy == null ? `<p class="fine">Accuracy: not scored yet.</p>` : `<p class="fine">Answer-key accuracy: ${(row.accuracy.accuracy * 100).toFixed(1)}% (${row.accuracy.scored} cases).</p>`}${result}${standInLabel}${error}${validationError}${crossCheck}<label>Prompt<textarea data-task-prompt rows="5" readonly>${escapeHtml(row.prompt || "No prompt: this task is blocked.")}</textarea></label><button class="btn ghost mini" type="button" data-copy-prompt ${row.prompt ? "" : "disabled"}>Copy prompt</button>${images ? `<p>Images to attach:</p><ul>${images}</ul>` : ""}${awaiting && row.prompt ? `<label>Paste JSON reply<textarea data-task-reply rows="4" spellcheck="false"></textarea></label><label>Model note<input data-model-note maxlength="160" placeholder="Model/version used"></label><label><input data-stand-in type="checkbox"> This reply is a stand-in/test result</label><button class="btn key mini" type="button" data-validate-apply>Validate &amp; apply</button>` : ""}</article>`;
    }).join("");
  }

  async function request(method, body = null) {
    if (!activeProject) throw new Error("Open a project before running AI tasks.");
    const response = await fetch(method === "GET" ? `/api/autonomous-tasks?project_id=${encodeURIComponent(activeProject)}${operatorMode ? "" : "&view=labels"}` : "/api/autonomous-tasks", {
      method, headers: method === "GET" ? undefined : {"Content-Type":"application/json"},
      body: body ? JSON.stringify({...body, project_id: activeProject}) : undefined,
    });
    const data = await response.json();
    if (!response.ok || data.error) throw new Error(data.error || "AI task request failed.");
    render(data);
    return data;
  }

  document.getElementById("autonomousTasksRunAll").addEventListener("click", async () => {
    try { status.textContent = "Building task packets…"; await request("POST", {action:"run_all"}); }
    catch (error) { status.textContent = error.message; }
  });
  list.addEventListener("click", async event => {
    const card = event.target.closest("[data-task-card]");
    if (event.target.closest("[data-copy-prompt]")) {
      try { await navigator.clipboard.writeText(card.querySelector("[data-task-prompt]").value); status.textContent = "Prompt copied. Attach the listed images when the task requests them."; }
      catch (_) { status.textContent = "Copy was blocked by the browser. Select and copy the prompt text."; }
    }
    if (event.target.closest("[data-validate-apply]")) {
      const button = event.target.closest("[data-validate-apply]");
      button.disabled = true;
      try {
        const data = await request("POST", {action:"validate_apply", task:card.dataset.task, target:card.dataset.target,
          reply:card.querySelector("[data-task-reply]").value, model_note:card.querySelector("[data-model-note]").value,
          stand_in:card.querySelector("[data-stand-in]").checked});
        const record = (data.tasks || []).find(row => row.task === card.dataset.task && row.target === card.dataset.target);
        if (record?.validation?.valid === false) status.textContent = `Validation failed: ${record.validation.error || record.block_reason || "Check the reply and try again."}`;
        else status.textContent = record?.stand_in
          ? "Stand-in (test) reply validated; it is not a model result."
          : "Reply validated; applied values and source labels are updated.";
      } catch (error) { status.textContent = error.message; button.disabled = false; }
    }
  });
  contractorQuestions?.addEventListener("click", async event => {
    const button = event.target.closest("[data-submit-roof-answer]");
    if (!button) return;
    const card = button.closest("[data-contractor-roof-question]");
    const choice = card.querySelector("input[type=radio]:checked");
    const line = card.querySelector("[data-roof-answer-status]");
    if (!choice) { line.textContent = "Choose one answer before saving."; return; }
    button.disabled = true;
    line.textContent = "Saving answer…";
    try {
      await request("POST", {action:"answer_roof", task:"P5_roof", target:card.dataset.target, answer:choice.value});
      contractorQuestionStatus.textContent = choice.value === "not_sure" ? "Saved. Roof exposure remains unknown and is not assessed." : "Saved as our answer.";
    } catch (error) { line.textContent = error.message; button.disabled = false; }
  });

  async function observeProject() {
    const projectId = typeof DATA !== "undefined" && DATA?.id ? DATA.id : "";
    if (!projectId || projectId === activeProject) return;
    activeProject = projectId;
    try { await request("GET"); }
    catch (error) { status.textContent = error.message; }
  }
  setInterval(observeProject, 600);

  // This extension annotates the existing site-location result without
  // changing the siteLocation* renderer, which has separate ownership.
  const annotateSiteResult = () => {
    const host = document.getElementById("siteLocationResults");
    const task = response?.tasks?.find(row => row.task === "P1_site" && row.applied_value);
    if (!host || !task || host.querySelector("[data-ai-site-source]")) return;
    const text = task.applied_value.applied_site_text || task.applied_value.site_text || "";
    const source = task.applied_value.applied_source === "reviewer" ? "Reviewer" : labelFor(task);
    const line = document.createElement("p");
    line.dataset.aiSiteSource = "";
    line.className = "fine";
    line.textContent = `AI task site: ${text} · ${source} (text only; no coordinates determined).`;
    host.append(line);
  };
  const annotateAiResult = () => {
    const host = document.getElementById("aiPreliminaryResults");
    const rows = (response?.tasks || []).filter(row => row.applied_value &&
      (row.status === "below_accuracy_bar" || String(row.quality_label || "").includes("below accuracy bar")));
    if (!host || !rows.length || host.querySelector("[data-task-accuracy-labels]")) return;
    const note = document.createElement("p");
    note.dataset.taskAccuracyLabels = "";
    note.className = "fine";
    note.textContent = `Applied AI tasks below the accuracy bar: ${rows.map(row => `${row.task} · ${row.target}`).join("; ")} — AI-determined (below accuracy bar).`;
    host.append(note);
  };
  new MutationObserver(annotateSiteResult).observe(document.getElementById("siteLocationResults") || document.body, {childList:true, subtree:true});
  new MutationObserver(annotateAiResult).observe(document.getElementById("aiPreliminaryResults") || document.body, {childList:true, subtree:true});
  setInterval(() => { annotateSiteResult(); annotateAiResult(); }, 1000);
})();

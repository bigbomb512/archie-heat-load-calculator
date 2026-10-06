import { expect, test } from "@playwright/test";

const analysis = {
  id: "demo-project",
  name: "Demo drawing set.pdf",
  pages_analysed: 2,
  relevant_count: 1,
  selected_count: 1,
  warnings: [],
  sheets: [
    {
      page: 1,
      type: "floor_plan",
      title: "Ground Floor Plan",
      reason: "Matched drawing title: floor plan.",
      confidence: 0.94,
      relevant: true,
      selected_by_default: true,
      packet_role: "",
      plan_role: "main_floor_plan",
      kept_for_review: true,
      review_bucket: "primary",
      scale: "1:100",
      dimension_count: 8,
      room_count: 2,
      level_name: "Ground Floor",
      level_status: "detected",
      visual: {},
      thumbnail: "",
    },
  ],
};

async function mockApi(page) {
  await page.route("**/api/test-mode/status", route => route.fulfill({ json: { enabled: false } }));
  await page.route("**/api/vision-response/no-ai", route => route.fulfill({json:{status:"created_without_ai_evidence",
    has_reasoning_packet:true, requirements:{zones:[]}, requirements_readiness:{status:"draft"}}}));
  await page.route("**/api/room-inference?project_id=demo-project", route => route.fulfill({ json: {
    status: "completed", candidate_count: 2,
  } }));
  await page.route("**/api/vision-extraction?project_id=demo-project", route => route.fulfill({ json: {
    settings: {}, available_groups: [], provider_configured: false,
  } }));
  await page.route("**/api/vision-response-history?project_id=demo-project", route => route.fulfill({ json: { attempts: [{
    attempt_id: "20261002T010203Z_abcd1234", created_at: "2026-10-02T12:02:03+1100", outcome: "accepted",
    outcome_detail: "Reply parsed and the reasoning packet was rebuilt.", model_note: "ChatGPT model note", page_list: [1, 2],
    packet_fingerprint: "abcdef1234567890", prompt_fingerprint: "123456abcdef7890", result_counts: {validation_issues: 0},
    raw_reply_url: "/api/artifact?project_id=demo-project&artifact=chatgpt_runs%2Ftest%2Fraw_reply.txt",
  }] } }));
  await page.route("**/api/ai-preliminary-model?project_id=demo-project**", route => route.fulfill({ json: {} }));
  await page.route("**/api/room-use-resolution?project_id=demo-project", route => route.fulfill({ json: {} }));
  await page.route("**/api/au-ventilation-rules?project_id=demo-project", route => route.fulfill({json:{
    id:"demo-project", project_regulatory_context:{building_approval_application_date:"", building_class:"unknown", building_use:"", project_specific_ventilation_basis:""},
    ruleset_status:"candidate", ventilation_rules_resolution:{status:"needs_review", jurisdiction:"", ncc_edition:"", zone_results:[], conflicts:[]},
  }}));
  await page.route("**/api/ceiling-volume-resolution?project_id=demo-project", route => route.fulfill({ json: {} }));
  await page.route("**/api/internal-gains-resolution?project_id=demo-project", route => route.fulfill({ json: {} }));
  await page.route("**/api/window-scan?project_id=demo-project", route => route.fulfill({ json: {} }));
  await page.route("**/api/project-health?project_id=demo-project", route => route.fulfill({ json: { status: "review_required", issues: [] } }));
  await page.route("**/api/audit-log?project_id=demo-project&limit=20", route => route.fulfill({ json: { events: [] } }));
  await page.route("**/api/projects", route => route.fulfill({ json: [
    { id: "demo-project", name: analysis.name, pages: 2, relevant: 1, analysed: true },
  ] }));
  await page.route("**/api/upload", route => route.fulfill({ json: {
    id: "demo-project", name: analysis.name, pages: 2, size_bytes: 1024,
  } }));
  await page.route("**/api/analyse", route => route.fulfill({ json: analysis }));
  await page.route("**/api/decisions", route => route.fulfill({ json: {
    id: "demo-project",
    ai_input_url: "/api/artifact?project_id=demo-project&artifact=ai_input.json",
    chatgpt_packet: {},
  } }));
  await page.route("**/api/analysis?id=demo-project", route => route.fulfill({ json: analysis }));
  await page.route("**/api/hourly-load-model?project_id=demo-project", route => route.fulfill({ json: { hourly_load_model: {floors: [], zones: [], rooms: []}, readiness: {status: "review_required", issues: []} } }));
  await page.route("**/api/hourly-load-report?project_id=demo-project", route => route.fulfill({ json: { hourly_load_report: {}, status: "not_calculated" } }));
  await page.route("**/api/model-input-resolution", route => route.fulfill({ json: {
    id: "demo-project", status: "current", coverage_summary: {total: 2, resolved: 1, provisional: 1, needs_review: 0, excluded: 0, complete: true},
    model_input_resolution: {records: [], review_queue: [], coverage_summary: {total: 2, resolved: 1, provisional: 1, needs_review: 0, excluded: 0, complete: true}},
    value_resolution: {records: [], coverage_summary: {}}, review_queue: [], stale_reasons: [],
  } }));
}

const designRequirements = {
  space_usage: "Retail tenancy",
  occupancy: 12,
  operating_hours: "Mon-Fri 08:00-18:00",
  indoor_cooling_setpoint_c: 24,
  indoor_heating_setpoint_c: 20,
  outdoor_summer_db_c: 35,
  outdoor_winter_db_c: 5,
  fresh_air_basis: "AS 1668 basis",
  exhaust_basis: "No process exhaust is required.",
  cooking_activity: "none",
  hood_requirement: "not_required",
  exhaust_outcome: "not_required",
  make_up_air_requirement: "not_required",
  ceiling_height_mm: 3200,
  ceiling_void_height_mm: 400,
  heat_sources: [],
  zones: [],
  existing_services: "Existing services confirmed.",
  service_constraints: {},
  code_basis: "NCC and AS 1668",
  verification: {
    occupancy: { status: "confirmed", source: "Client brief" },
    design_conditions: { status: "confirmed", source: "Designer basis" },
    outside_air: { status: "confirmed", source: "AS 1668" },
    exhaust: { status: "not_applicable", source: "Client confirmation" },
    heat_sources: { status: "missing", source: "" },
    ceiling: { status: "confirmed", source: "Architectural plan" },
    existing_services: { status: "confirmed", source: "Site survey" },
  },
};

const coolingRequirements = {
  ...designRequirements,
  cooling_load_conditions: {
    indoor_cooling_wet_bulb_c: 18,
    outdoor_summer_wet_bulb_c: 24,
    atmospheric_pressure_kpa: 101.325,
    verification_status: "confirmed",
    source: "Designer summer basis",
  },
  zones: [{
    zone_id: "zone_001", name: "Sales area", usage: "Retail sales", source_room_labels: ["Sales"],
    area_m2: 30, occupancy: 18, heat_sources: [{ name: "Display fridge", kind: "refrigeration", quantity: 1, watts: 700, diversity_factor: 1, space_gain_factor: 0.8, verification_status: "confirmed", source: "Manufacturer schedule" }],
    cooling_load: {
      people_sensible_w_per_person: 75, people_latent_w_per_person: 55, people_diversity_factor: 0.8,
      lighting_w_m2: 10, lighting_diversity_factor: 0.9, outside_air_lps: 90, safety_factor: 1.1,
      envelope_not_applicable: true, envelope_surfaces: [], verification_status: "confirmed", source: "Designer calculation basis",
    },
  }],
};

const ventilationRequirements = {
  ...designRequirements,
  zones: [{
    zone_id: "zone_001", name: "Sales area", usage: "Retail sales", source_room_labels: ["Sales"], area_m2: 30, occupancy: 18,
    ventilation_requirements: {
      process_type: "none", basis_name: "Project ventilation basis", basis_source: "Designer record",
      outside_air_method: "combined", people_rate_lps_per_person: 5, area_rate_lps_per_m2: 2, fixed_minimum_lps: null,
      process_exhaust_requirement: "not_required", process_exhaust_lps: null, hood_type_or_duty: "", recirculable: "yes",
      allowable_transfer_air_lps: 0, allowable_outside_air_credit_lps: 0,
      design_supply_lps_including_outside_air: 100, return_or_relief_lps: 100, dedicated_make_up_air_lps: 0,
      verification_status: "confirmed", source: "Designer ventilation basis",
    },
  }],
};

test("confirmation unlocks only after analysis has selected pages", async ({ page }) => {
  await mockApi(page);
  let noAiCalls = 0;
  await page.route("**/api/vision-response/no-ai", async route => {
    noAiCalls += 1;
    return route.fulfill({json:{status:"created_without_ai_evidence", has_reasoning_packet:true,
      requirements:{zones:[]}, requirements_readiness:{status:"draft"}}});
  });
  await page.route("**/api/vision-response-history?project_id=demo-project", route => route.fulfill({json:{attempts:[{
    attempt_id:"no-ai-1", outcome:"no_ai_evidence", outcome_detail:"No AI reply — started without AI evidence.",
    model_note:"No AI reply — started without AI evidence", packet_pages:[1], attached_pages:[],
    packet_fingerprint:"a".repeat(64), prompt_fingerprint:"b".repeat(64), result_counts:{},
  }]}}));
  await page.goto("/");

  await page.locator("#pdf").setInputFiles({
    name: "test-drawing-set.pdf",
    mimeType: "application/pdf",
    buffer: Buffer.from("%PDF-1.4 test fixture"),
  });
  await expect(page.locator("#fState")).toHaveText("Ready");
  await expect(page.locator("#btnContinue")).toBeDisabled();

  await page.locator("#btnAnalyse").click();
  await expect(page.locator("#topTitle")).toHaveText("Analysis complete");
  await expect(page.locator("#btnContinue")).toBeEnabled();

  await page.locator("#btnContinue").click();
  await expect(page.locator("#visionPanel")).toBeVisible();
  await expect(page.locator("#btnContinue")).toHaveText("Drawings confirmed");
  await expect(page.locator("#btnContinue")).toBeDisabled();
  await expect(page.locator("#designRequirementsPanel")).toBeVisible();
  await expect(page.locator("#noAiEvidenceNotice")).toBeVisible();
  await expect(page.locator("#noAiEvidenceNotice")).toContainText("Tracing and manual inputs work");
  await expect(page.locator("#visionHistory")).toContainText("NO_AI_EVIDENCE");
  expect(noAiCalls).toBe(1);
});

test("reopening a no-AI project restores its notice and reviewed-workspace status", async ({ page }) => {
  await mockApi(page);
  await page.goto("/");
  await page.evaluate(project => showResults({...project, has_reasoning_packet:true,
    vision_evidence_source:"none", design_requirements:{zones:[]}}), analysis);
  await expect(page.locator("#visionPanel")).toBeVisible();
  await expect(page.locator("#noAiEvidenceNotice")).toBeVisible();
  await expect(page.locator("#statusText")).toHaveText("Reviewed workspace ready");
  await expect(page.locator("#summaryTitle")).toHaveText("Reviewed workspace ready");
  await expect(page.locator("#btnContinue")).toHaveText("Drawings confirmed");
  await expect(page.locator("#btnContinue")).toBeDisabled();
});

test("confirmation does not show the no-AI notice when a real reply is preserved", async ({ page }) => {
  await mockApi(page);
  await page.route("**/api/vision-response/no-ai", route => route.fulfill({json:{
    status:"real_reply_preserved", has_reasoning_packet:true,
  }}));
  await page.goto("/");
  await page.locator("#pdf").setInputFiles({
    name:"real-reply-project.pdf", mimeType:"application/pdf", buffer:Buffer.from("%PDF-1.4 test"),
  });
  await page.locator("#btnAnalyse").click();
  await page.locator("#btnContinue").click();
  await expect(page.locator("#workflowSkeleton")).toBeVisible();
  await expect(page.locator("#noAiEvidenceNotice")).toBeHidden();
});

test("analysis gives one clear next action before exposing advanced workflow", async ({ page }) => {
  await mockApi(page);
  await page.goto("/");
  await page.locator("#pdf").setInputFiles({
    name: "workflow-drawing-set.pdf",
    mimeType: "application/pdf",
    buffer: Buffer.from("%PDF-1.4 workflow fixture"),
  });
  await page.locator("#btnAnalyse").click();
  await expect(page.getByRole("button", { name: "Settings" })).toHaveCount(0);
  await expect(page.locator("#btnApproveSafetyFactor")).toHaveCount(0);
  await expect(page.locator("#btnConfirm, #btnConfirmTop")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Confirm selected drawings", exact: true })).toHaveCount(1);
  await expect(page.locator("#btnContinue")).toHaveText("Confirm selected drawings");
  await expect(page.locator("#workflowSkeleton")).toBeHidden();
  await page.locator("#btnContinue").click();
  await expect(page.locator("#workflowSkeleton")).toBeVisible();
  await expect(page.locator("#workflowStageList .workflow-stage-card")).toHaveCount(9);
  await expect(page.locator("#workflowStageList .workflow-stage-card").first()).toContainText("Evidence");
  await expect(page.locator("#workflowStageList .workflow-stage-card").last()).toContainText("Calculate and deliver");
  await expect(page.locator("#workflowOverallStatus")).toHaveText("Draft in progress");
  await page.locator('[data-workflow-action="envelope"]').click();
  await expect(page.locator("#workflowSkeletonNotice")).toContainText("Stage actions run");
});

test("contractor saves Australian ventilation rules context once and sees safe unmatched status", async ({ page }) => {
  let saved;
  await page.route("**/api/au-ventilation-rules*", async route => {
    if (route.request().method() === "POST") {
      saved = route.request().postDataJSON().project_regulatory_context;
      return route.fulfill({json:{
        id:"demo-project", project_regulatory_context:saved, ruleset_status:"candidate",
        ventilation_rules_resolution:{status:"rules_unavailable", jurisdiction:"NSW", ncc_edition:"NCC edition unresolved", zone_results:[], conflicts:["No released NCC adoption record covers NSW."]},
      }});
    }
    return route.fulfill({json:{
      id:"demo-project", project_regulatory_context:{building_approval_application_date:"", building_class:"unknown", building_use:"", project_specific_ventilation_basis:""},
      ruleset_status:"candidate", ventilation_rules_resolution:{status:"needs_review", jurisdiction:"NSW", ncc_edition:"NCC edition unresolved", zone_results:[], conflicts:[]},
    }});
  });
  await page.goto("/");
  await page.evaluate(() => {
    DATA = {id:"demo-project"};
    show("vRes");
    requiredElement("designRequirementsPanel").classList.remove("hide");
    drawAustralianVentilationRules({
      project_regulatory_context:{}, ruleset_status:"candidate",
      ventilation_rules_resolution:{status:"needs_review", jurisdiction:"NSW", ncc_edition:"NCC edition unresolved", zone_results:[], conflicts:[]},
    });
  });
  await page.locator("#buildingApprovalDate").fill("2025-03-15");
  await page.locator("#nccBuildingClass").selectOption("class_6");
  await page.locator("#projectBuildingUse").fill("Restaurant fit-out");
  await page.locator("#btnSaveVentilationRulesContext").click();
  await expect.poll(() => saved?.building_approval_application_date).toBe("2025-03-15");
  expect(saved.building_class).toBe("class_6");
  expect(saved.building_use).toBe("Restaurant fit-out");
  await expect(page.locator("#ventilationRulesStatus")).toContainText("NSW");
  await expect(page.locator("#ventilationRulesStatus")).toContainText("no reviewed ventilation rates yet");
});

test("cooling outside-air flow records its own source and review status", async ({ page }) => {
  await page.goto("/");
  const result = await page.evaluate(() => {
    const item = addZone({zone_id:"zone_oa", name:"Dining", area_m2:40, occupancy:24});
    item.querySelector(".zone-outside-air").value = "180";
    item.querySelector(".zone-outside-air-source").value = "Mechanical schedule M-201";
    item.querySelector(".zone-outside-air-status").value = "provisional";
    item.querySelector(".zone-outside-air-reference").value = "standard_air_1_2kg_da_m3";
    return {zone: readZone(item), help: item.querySelector(".zone-outside-air-help").textContent};
  });
  expect(result.help).toContain("does not determine code compliance");
  const zone = result.zone;
  expect(zone.cooling_load.outside_air_lps).toBe(180);
  expect(zone.cooling_load.outside_air_source).toBe("Mechanical schedule M-201");
  expect(zone.cooling_load.outside_air_verification_status).toBe("provisional");
  expect(zone.cooling_load.outside_air_flow_reference_basis).toBe("standard_air_1_2kg_da_m3");
});

test("guided resolver reports contractor-friendly skill stages then refreshes coverage", async ({ page }) => {
  await mockApi(page);
  await page.route("**/api/airflow-resolution", route => route.fulfill({json: {
    status: "draft", airflow_resolution: {status: "draft", summary: {resolved: 0, provisional: 0, blocked: 1}, records: [{
      owner_room_id: "room-dining", air_path_type: "outside_air", status: "blocked", value: 40, unit: "L/s", origin: "project_evidence",
      unresolved_fields: ["records for the same physical airflow path disagree on value, unit, or direction"], conflicts: ["airflow-duplicate"],
    }]},
  }}));
  let statusChecks = 0;
  await page.route("**/api/skill-workflow**", async route => {
    if (route.request().method() === "POST") {
      return route.fulfill({ json: { status: "running", stages: [] } });
    }
    statusChecks += 1;
    return route.fulfill({ json: {
      status: "needs_review",
      stages: [
        { label: "Drawing set and page mapping", status: "needs_review" },
        { label: "Rooms, geometry, and gains", status: "needs_review" },
      ],
    } });
  });
  await page.goto("/");
  await page.locator("#pdf").setInputFiles({
    name: "guided-workflow.pdf", mimeType: "application/pdf", buffer: Buffer.from("%PDF-1.4 guided fixture"),
  });
  await page.locator("#btnAnalyse").click();
  await page.locator("#btnContinue").click();
  await page.locator("#btnGuidedResolveModelInputs").click();
  await expect(page.locator("#guidedModelInputsStatus")).toContainText("Coverage hydrated");
  await page.evaluate(() => resolveAirflow());
  await expect(page.locator("#airflowResolutionResults")).toContainText("disagree on value");
  await expect(page.locator("#airflowResolutionResults")).toContainText("conflicts with airflow-duplicate");
  expect(statusChecks).toBe(1);
});

async function openGuidedResolver(page){
  await page.goto("/");
  await page.locator("#pdf").setInputFiles({
    name: "guided-workflow.pdf", mimeType: "application/pdf", buffer: Buffer.from("%PDF-1.4 guided fixture"),
  });
  await page.locator("#btnAnalyse").click();
  await page.locator("#btnContinue").click();
}

test("guided resolver still resolves model inputs when the evidence workflow is stale", async ({ page }) => {
  await mockApi(page);
  const resolverActions = [];
  await page.route("**/api/model-input-resolution", route => {
    resolverActions.push(route.request().postDataJSON()?.action);
    return route.fulfill({ json: {
      id: "demo-project", status: "current", coverage_summary: {total: 1, resolved: 1, provisional: 0, needs_review: 0, excluded: 0, complete: true},
      model_input_resolution: {records: [], review_queue: [], coverage_summary: {total: 1, resolved: 1, provisional: 0, needs_review: 0, excluded: 0, complete: true}},
      value_resolution: {records: [], coverage_summary: {}}, review_queue: [], stale_reasons: [],
    } });
  });
  await page.route("**/api/skill-workflow**", route => route.fulfill({ json: {
    status: "stale", stages: [], subskills: [],
    stale_reasons: ["A current domain artifact used by one or more skill proposals changed."],
    remediation: "Some evidence tasks need review.",
  } }));
  await openGuidedResolver(page);
  await page.locator("#btnGuidedResolveModelInputs").click();
  await expect(page.locator("#guidedModelInputsStatus")).toContainText("Coverage hydrated with evidence exceptions (stale)");
  await expect(page.locator("#guidedModelInputsStatus")).toContainText("Some evidence tasks need review.");
  expect(resolverActions).toEqual(["resolve"]);
  await expect(page.locator("#btnGuidedResolveModelInputs")).toBeEnabled();
});

test("guided resolver does not call the resolver when the evidence workflow cannot start", async ({ page }) => {
  await mockApi(page);
  let resolverCalls = 0;
  await page.route("**/api/model-input-resolution", route => {
    resolverCalls += 1;
    return route.fulfill({ json: {} });
  });
  await page.route("**/api/skill-workflow**", route => route.request().method() === "POST"
    ? route.fulfill({ status: 409, json: { error: "A workflow run is already active for this project." } })
    : route.fulfill({ json: { status: "idle", stages: [] } }));
  await openGuidedResolver(page);
  await page.locator("#btnGuidedResolveModelInputs").click();
  await expect(page.locator("#guidedModelInputsStatus")).toContainText("Resolver could not complete");
  expect(resolverCalls).toBe(0);
});

test("local test workspace runs and resets an isolated draft walkthrough", async ({ page }) => {
  await mockApi(page);
  await page.route("**/api/test-mode/status", route => route.fulfill({json: {
    enabled: true, available: true, fixture_name: "Local full-building fixture",
    fixture_warning: "Synthetic test-only areas are not extracted from the PDF.", active_run: null,
  }}));
  await page.route("**/api/test-mode/run", route => route.fulfill({json: {run: {
    run_id: "test-123", status: "running", current_stage: "analyse_pdf", stages: [],
  }}}));
  await page.route("**/api/test-mode/run/test-123", route => route.fulfill({json: {run: {
    run_id: "test-123", status: "completed", current_stage: "completed",
    stages: [{id: "analyse_pdf", status: "complete"}, {id: "room_inference", status: "complete"}],
    coverage: {rooms: {total: 8, resolved: 8}, surfaces: {total: 12, resolved: 7}, openings: {total: 5, resolved: 3}, air_systems: {total: 1, resolved: 0}, plant_systems: {total: 0, resolved: 0}},
    provisional_count: 4, blocked_count: 2, excluded_count: 1,
    report_status: "calculated_provisional", report_label: "TEST RUN — AI preliminary estimate — not engineering reviewed or validated",
    report_summary: {peak_total_kw: 48.2, peak_hour: 15},
    skill_tasks: [{id: "room_boundaries_areas", status: "failed", validation_check: "proposal_fields_mismatch",
      validation_detail: "Missing required proposal field.",
      raw_output_url: "/api/test-mode/artifact/test-123?path=workspace%2Fskill_workflow_runs%2Frun%2Fattempts%2Froom_boundaries_areas%2F1%2Fraw_output.txt",
      prompt_url: "/api/test-mode/artifact/test-123?path=workspace%2Fskill_workflow_runs%2Frun%2Fattempts%2Froom_boundaries_areas%2F1%2Fprompt.txt"}],
  }}}));
  await page.route("**/api/test-mode/reset", route => route.fulfill({json: {reset: true, run_id: "test-123"}}));
  await page.goto("/");
  await expect(page.locator("#testWorkspacePanel")).toBeVisible();
  await expect(page.locator("#testWorkspaceFixture")).toContainText("synthetic workflow test");
  await expect(page.locator("#testWorkspaceFixture")).toContainText("real-PDF Codex AI test");
  await expect(page.locator("#testWorkspaceFixture")).toContainText("Local full-building fixture");
  await page.locator('[data-open="demo-project"]').click();
  await expect(page.locator("#vRes")).toBeVisible();
  await expect(page.locator("#testWorkspacePanel")).toBeVisible();
  await page.locator("#btnTestWorkspaceRun").click();
  await expect(page.locator("#testWorkspaceStatus")).toContainText("Walkthrough complete");
  await expect(page.locator("#testWorkspaceSummary")).toContainText("Rooms: 8/8");
  await expect(page.locator("#testWorkspaceSummary")).toContainText("48.20 kW");
  await expect(page.locator("#testWorkspaceSummary")).toContainText("not engineering reviewed or validated");
  await expect(page.locator("#testWorkspaceSkillAttempts")).toContainText("proposal_fields_mismatch");
  await expect(page.locator("#testWorkspaceSkillAttempts")).toContainText("Missing required proposal field.");
  await expect(page.locator("#testWorkspaceSkillAttempts a")).toHaveCount(2);
  await page.locator("#btnTestWorkspaceReset").click();
  await expect(page.locator("#testWorkspaceStatus")).toContainText("Test run reset");
});

test("design-input verification controls save with the reasoning packet", async ({ page }) => {
  const errors = [];
  page.on("pageerror", error => errors.push(error.message));
  await mockApi(page);
  await page.route("**/api/design-requirements**", async route => {
    const request = route.request();
    if (request.method() === "GET") {
      return route.fulfill({ json: { id: "demo-project", requirements: designRequirements, readiness: {
        status: "brief_allowed", missing_inputs: ["major appliance/equipment heat loads"], provisional_inputs: [], input_errors: [],
      }, room_suggestions: [{ label: "Sales area", area: "30m2", source_page: 1 }] } });
    }
    const body = request.postDataJSON();
    expect(body.requirements.occupancy).toBe(18);
    expect(body.requirements.verification.occupancy.status).toBe("confirmed");
    expect(body.requirements.ceiling_height_mm).toBe(3200);
    expect(body.requirements.zones).toEqual(expect.arrayContaining([expect.objectContaining({
      zone_id: "zone_001", name: "Sales area", usage: "Retail sales", area_m2: 30, occupancy: 18,
    })]));
    return route.fulfill({ json: {
      requirements: { ...designRequirements, occupancy: 18 },
      requirements_readiness: { status: "brief_allowed", missing_inputs: ["major appliance/equipment heat loads"], provisional_inputs: [], input_errors: [] },
      requirements_url: "/output/design_requirements.json",
      reasoning_zip_url: "/output/reasoning_packet.zip",
    } });
  });

  await page.goto("/");
  await page.evaluate(() => { show("vRes"); showDesignRequirements({}, {}, [{ label: "Sales area", area: "30m2", source_page: 1 }]); });
  await page.getByRole("button", {name: "2 Confirm project inputs"}).click();
  await page.locator("#reqOccupancy").fill("18");
  await page.locator("#reqOccupancyStatus").selectOption("confirmed");
  await page.locator("#reqOccupancySource").fill("Client brief");
  await page.locator("#reqCeilingHeight").fill("3200");
  await page.locator(".zone-editor summary").click();
  await page.locator(".zone-usage").fill("Retail sales");
  await page.locator(".zone-occupancy").fill("18");
  await page.locator(".zone-add-heat").click();
  await page.locator(".zone-heat-name").fill("Display fridge");
  await page.locator(".zone-heat-quantity").fill("1");
  await page.locator(".zone-heat-watts").fill("700");
  await page.locator(".zone-heat-note").fill("Manufacturer schedule");
  await page.locator(".zone-heat-status").selectOption("confirmed");
  await page.evaluate(() => { DATA = { id: "demo-project" }; });
  await page.locator("#btnSaveRequirements").click();

  await expect(page.locator("#requirementsLinks")).toContainText("refreshed reasoning packet");
  expect(errors).toEqual([]);
});

test("guided contractor workflow reveals only the current stage", async ({ page }) => {
  await mockApi(page);
  await page.goto("/");
  await page.evaluate(requirements => {
    DATA = { id: "demo-project" };
    show("vRes");
    showDesignRequirements(requirements, {}, []);
  }, coolingRequirements);

  await expect(page.locator("#calculatorDraftHeading")).toBeVisible();
  await expect(page.locator("#designRequirementsForm")).toBeHidden();
  await page.getByRole("button", {name: "2 Confirm project inputs"}).click();
  await expect(page.locator("#designRequirementsForm")).toBeVisible();
  await expect(page.locator("#calculatorInputSection")).toBeHidden();
  await page.getByRole("button", {name: "Show all tools"}).click();
  await expect(page.locator("#calculatorInputSection")).toBeVisible();
  await expect(page.locator("#btnShowAllWorkflowTools")).toHaveText("Return to guided workflow");
});

test("hourly cooling workflow displays a labelled partial draft", async ({ page }) => {
  const errors = [];
  page.on("pageerror", error => errors.push(error.message));
  await mockApi(page);
  const hourlyModel = {
    schema_version: 2,
    floors: [{floor_id: "level_01", name: "Level 1", elevation_m: 0, verification_status: "confirmed", source: "A-101", citations: []}],
    zones: [{zone_id: "zone_001", name: "Sales zone", floor_id: "level_01", verification_status: "confirmed", source: "Engineer", citations: []}],
    rooms: [{room_id: "room_001", name: "Sales area", zone_id: "zone_001", mapping_status: "confirmed", verification_status: "confirmed", source: "Engineer", source_zone_id: "zone_001", source_room_labels: [], area_m2: 30, occupancy: 18, indoor_cooling_setpoint_c: 24, heat_sources: [], cooling_load: {}, cooling_load_conditions: {}, schedule_assignments: {people: "", lighting: "", outside_air: "", equipment: {}, solar: {}}}],
  };
  const weatherProvenance = {
    wet_bulb_basis: "legacy_unverified",
    dry_bulb: {status: "provisional", source: "UQ/NIWA Parramatta draft data", citations: [{reference: "ACDB Table 1"}]},
    wet_bulb: {status: "provisional", source: "UQ/NIWA Parramatta draft data", citations: [{reference: "ACDB Table 1"}]},
  };
  await page.route("**/api/hourly-load-model?project_id=demo-project", route => route.fulfill({json: {hourly_load_model: hourlyModel, readiness: {status: "confirmed", issues: []}}}));
  await page.route("**/api/hourly-load-model", async route => {
    const body = route.request().postDataJSON();
    expect(body.action).toBe("save");
    expect(body.hourly_load_model.schema_version).toBe(4);
    expect(body.hourly_load_model.floors[0]).toEqual(expect.objectContaining({floor_id: "level_01", verification_status: "confirmed"}));
    expect(body.hourly_load_model.zones[0]).toEqual(expect.objectContaining({zone_id: "zone_001", floor_id: "level_01"}));
    expect(body.hourly_load_model.rooms[0]).toEqual(expect.objectContaining({room_id: "room_001", zone_id: "zone_001", mapping_status: "confirmed"}));
    expect(body.hourly_load_model.rooms[0].cooling_load_conditions).toEqual(expect.objectContaining({indoor_wet_bulb_basis: "thermodynamic"}));
    expect(body.hourly_load_model.rooms[0].cooling_load).toEqual(expect.objectContaining({outside_air_flow_reference_basis: "standard_air_1_2kg_da_m3"}));
    expect(body.hourly_load_model.rooms[0].unapproved_components).toEqual(expect.arrayContaining([expect.objectContaining({component_id: "infiltration", calculation_status: "stored_not_calculated", value: 0.25, unit: "ACH", source: "Site note"})]));
    return route.fulfill({json: {hourly_load_model: body.hourly_load_model, readiness: {status: "confirmed", issues: []}}});
  });
  await page.route("**/api/hourly-load-report?project_id=demo-project", route => route.fulfill({json: {hourly_load_report: {}, status: "not_calculated"}}));
  await page.route("**/api/hourly-load-report", async route => {
    const body = route.request().postDataJSON();
    expect(body.selected_scenario_ids).toEqual(["summer_day"]);
    return route.fulfill({ json: {
      status: "current",
      hourly_load_report: {
        status: "draft",
        readiness: {status: "draft", issues: [{status: "blocked", scope: "room", affected_id: "room_002", reason: "people schedule assignment", source_artifact: "hourly_load_model", citations: []}]},
        input_artifacts: {hourly_load_model: {artifact_url: "/output/hourly_load_model.json"}},
        scope_summary: {complete_scope: false, blocked_rooms: [{room_id: "room_002", reasons: ["people schedule assignment"]}]},
        known_exclusions: [{room_id: "room_001", component_type: "infiltration", value: 0.25, unit: "ACH", source: "Site note"}],
        unresolved_room_inputs: [{room_id: "room_001", component_type: "steam_gain"}],
        scenario_results: [{
          scenario_id: "summer_day", title: "Summer design day", status: "draft",
          included_scope_peak: {design_total_kw: 4.2},
          scope_summary: {complete_scope: false, blocked_rooms: [{room_id: "room_002", reasons: ["people schedule assignment"]}]},
          rooms: [{
            room_id: "room_001", name: "Dining",
            peak: {hour: 14, components: {
              outside_air: {
                sensible_kw: 13.47, latent_kw: -5.35, total_kw: 8.12,
                inputs: {
                  flow_lps: 1050, flow_source: "140 scheduled seats × 7.5 L/s/person", flow_verification_status: "provisional",
                  flow_reference_basis: "standard_air_1_2kg_da_m3", flow_reference_status: "declared_basis_unverified", flow_reference_state: {dry_air_density_kg_m3: 1.2},
                  indoor_db_c: 24, indoor_wb_c: 18, indoor_wet_bulb_basis: "legacy_unverified",
                  outdoor_db_c: 35.3, outdoor_wb_c: 20.2, outdoor_wet_bulb_basis: "legacy_unverified", atmospheric_pressure_kpa: 100.159,
                  outdoor_weather_provenance: weatherProvenance,
                },
              },
              infiltration: {
                sensible_kw: 0.12, latent_kw: 0.04, total_kw: 0.16,
                inputs: {
                  resolved_flow_lps: 6, applied_flow_lps: 6, room_volume_m3: 60, schedule_factor: 1,
                  raw_signed_sensible_kw: 0.12, raw_signed_latent_kw: 0.04, outdoor_wet_bulb_basis: "legacy_unverified",
                  flow_reference_basis: "outdoor_design_condition", flow_reference_status: "calculation_assumption_unverified",
                  outdoor_weather_provenance: weatherProvenance,
                },
              },
              transfer_air: {
                sensible_kw: -0.42, latent_kw: -0.10, total_kw: -0.52,
                inputs: {
                  flow_lps: 50, source_room_id: "kitchen", method_validation: "draft_pending_engineer_review",
                  flow_source: "Air balance AB-1", flow_citations: [{reference: "Air balance AB-1"}], flow_verification_status: "provisional",
                  source_room_conditions: {room_id: "kitchen", dry_bulb_c: 26, wet_bulb_c: 19, wet_bulb_basis: "thermodynamic", verification_status: "provisional"},
                  target_room_conditions: {room_id: "dining", dry_bulb_c: 24, wet_bulb_c: 18, wet_bulb_basis: "legacy_unverified", verification_status: "provisional"},
                },
              },
            }},
          }],
        }],
      },
    } });
  });

  await page.goto("/");
  await page.evaluate(requirements => {
    DATA = { id: "demo-project" };
    show("vRes");
    showDesignRequirements(requirements, {}, []);
  }, coolingRequirements);
  await page.getByRole("button", {name: "3 Complete the building model"}).click();
  const infiltration = page.locator(".room-component").first();
  await infiltration.locator(".room-component-state").selectOption("stored_not_calculated");
  await infiltration.locator(".room-component-value").fill("0.25");
  await infiltration.locator(".room-component-unit").fill("ACH");
  await infiltration.locator(".room-component-status").selectOption("confirmed");
  await infiltration.locator(".room-component-source").fill("Site note");
  await page.locator(".room-outside-air-reference").selectOption("standard_air_1_2kg_da_m3");
  await page.locator(".room-wet-bulb-basis").selectOption("thermodynamic");
  await page.locator("#btnSaveHourlyModel").click();
  await page.getByRole("button", {name: "4 Calculate cooling"}).click();
  await page.locator("#hourlyScenarioIds").fill("summer_day");
  await page.locator("#btnCalculateHourlyLoad").click();

  await expect(page.locator("#hourlyReportStatus")).toContainText("included room scope only");
  await expect(page.locator("#hourlyLoadResults")).toContainText("not a complete project duty");
  await expect(page.locator("#coolingReadiness")).toContainText("people schedule assignment");
  await expect(page.locator("#coolingReadiness")).toContainText("Open evidence");
  await expect(page.locator("#hourlyLoadResults")).toContainText("known excluded room input");
  await expect(page.locator("#hourlyLoadResults")).toContainText("unresolved room input");
  await expect(page.locator("#hourlyLoadResults")).toContainText("Outside-air cooling at each room governing hour");
  await expect(page.locator("#hourlyLoadResults")).toContainText("sensible 13.47 kW, latent -5.35 kW");
  await expect(page.locator("#hourlyLoadResults")).toContainText("legacy / unverified wet-bulb basis");
  await expect(page.locator("#hourlyLoadResults")).toContainText("DB: UQ/NIWA Parramatta draft data (provisional) · ACDB Table 1");
  await expect(page.locator("#hourlyLoadResults")).toContainText("source: 140 scheduled seats × 7.5 L/s/person");
  await page.getByText("Outside-air cooling at each room governing hour").click();
  await expect(page.locator("#hourlyLoadResults")).toContainText("Flow reference: standard air at 1.2 kg dry air/m³ (declared; source not independently verified)");
  await expect(page.locator("#hourlyLoadResults")).toContainText("Transfer-air cooling at each room governing hour");
  await page.getByText("Transfer-air cooling at each room governing hour").click();
  await expect(page.locator("#hourlyLoadResults")).toContainText("from kitchen (26.0°C DB / 19.0°C WB");
  await expect(page.locator("#hourlyLoadResults")).toContainText("Air balance AB-1");
  await page.getByText("Infiltration at each room governing hour").click();
  await expect(page.locator("#hourlyLoadResults")).toContainText("UQ/NIWA Parramatta draft data (provisional)");
  await expect(page.locator("#hourlyLoadResults")).toContainText("wet-bulb basis: legacy_unverified");
  await expect(page.locator("#hourlyLoadResults")).toContainText("Flow reference: outdoor design-air state (calculation assumption; source basis not verified)");
  expect(errors).toEqual([]);
});

test("heating report exposes psychrometric source and wet-bulb basis", async ({ page }) => {
  await mockApi(page);
  await page.goto("/");
  await page.evaluate(() => drawHeatingLoadReport({
    status: "draft",
    readiness: {status: "draft", issues: []},
    scenario_results: [{
      scenario_id: "winter_case", title: "Winter case", status: "draft",
      included_scope_peak: {design_total_kw: 7.2}, scope_summary: {complete_scope: false, blocked_rooms: []},
      rooms: [{room_id: "room_01", name: "Dining", status: "draft", peak: {
        hour: 6, design_total_kw: 7.2, safety_allowance_kw: 0.7,
        components: {
          heating_outside_air: {sensible_kw: 2.1, total_kw: 2.1, inputs: {
            flow_lps: 1050, indoor_heating_setpoint_source: "Heating brief HB-1", indoor_heating_setpoint_citations: [{reference: "HB-1"}],
            outdoor_db_c: 0, outdoor_wb_c: -2, outdoor_wet_bulb_basis: "thermodynamic", atmospheric_pressure_kpa: 100.4,
            flow_source: "Air schedule AS-1", flow_verification_status: "provisional",
            flow_reference_basis: "outdoor_design_condition", flow_reference_status: "calculation_assumption_unverified",
            weather_provenance: {
              wet_bulb_basis: "thermodynamic",
              dry_bulb: {value: 0, status: "provisional", source: "Parramatta winter record", citations: [{reference: "WX-2"}]},
              wet_bulb: {value: -2, status: "provisional", source: "Parramatta winter record", citations: [{reference: "WX-2"}]},
              pressure: {value: 100.4, status: "provisional", source: "Station pressure", citations: [{reference: "WX-2"}]},
            },
          }},
        },
      }}],
    }],
  }, "current"));
  await expect(page.locator("#heatingLoadResults")).toContainText("Outside air: 2.10 kW");
  await expect(page.locator("#heatingLoadResults")).toContainText("Parramatta winter record (provisional)");
  await expect(page.locator("#heatingLoadResults")).toContainText("basis thermodynamic");
  await expect(page.locator("#heatingLoadResults")).toContainText("Air schedule AS-1");
  await expect(page.locator("#heatingLoadResults")).toContainText("flow reference outdoor design-air state (calculation assumption; source basis not verified)");
});

test("AHU peak report exposes outdoor, return, coil, and pressure provenance", async ({ page }) => {
  await mockApi(page);
  await page.goto("/");
  await page.evaluate(() => drawAhuAirside({status: "draft", scenario_results: [{
    scenario_id: "summer_case", status: "draft", included_scope_peak: {design_total_kw: 8.4},
    ahus: [{ahu_id: "ahu_01", name: "Dining AHU", status: "draft", system_type: "single_zone_constant_volume", number_off: 1, peak: {
      hour: 15, design_total_kw: 8.4, coil_total_kw: 8.4, coil_sensible_kw: 6.7, coil_latent_kw: 1.7, coil_condensate_kg_s: 0.0008, psychrometric_provenance: {
        outdoor: {
          dry_bulb: {value: 35, status: "provisional", source: "Design weather pack", citations: [{reference: "WX-DB"}]},
          wet_bulb: {value: 23, status: "provisional", source: "Design weather pack", citations: [{reference: "WX-WB"}]},
          wet_bulb_basis: "thermodynamic",
        },
        pressure: {value: 101.325, status: "confirmed", source: "Station pressure", citations: [{reference: "WX-P"}]},
        return_air: {wet_bulb_basis: "psychrometer", source: "Return sensor schedule", review_status: "confirmed", citations: [{reference: "RA-1"}]},
        coil_leaving: {wet_bulb_basis: "thermodynamic", source: "Coil schedule", review_status: "confirmed", citations: [{reference: "CL-1"}]},
        supply_airflow_reference: {basis: "assumed_at_mixed_air_coil_inlet", state: {dry_bulb_c: 25.2, pressure_kpa: 101.325}, verified: false},
        coil_sensible_split: {basis: "dry_air_specific_heat_only", specific_heat_kj_kg_da_k: 1.006, latent_definition: "total coil duty minus reported sensible duty", review_status: "unvalidated_component_convention"},
      },
    }}],
  }]}, "draft", {}, {count: 1}, {airflow_count: 3}));
  await expect(page.locator("#ahuAirsideResults")).toContainText("Design weather pack (provisional) · WX-DB");
  await expect(page.locator("#ahuAirsideResults")).toContainText("thermodynamic");
  await expect(page.locator("#ahuAirsideResults")).toContainText("Station pressure (confirmed) · WX-P");
  await expect(page.locator("#ahuAirsideResults")).toContainText("Return sensor schedule (confirmed) · RA-1");
  await expect(page.locator("#ahuAirsideResults")).toContainText("Coil schedule (confirmed) · CL-1");
  await expect(page.locator("#ahuAirsideResults")).toContainText("0.80 g/s condensate");
  await expect(page.locator("#ahuAirsideResults")).toContainText("6.70 kW sensible / 1.70 kW latent");
  await expect(page.locator("#ahuAirsideResults")).toContainText("Sensible/latent split: dry_air_specific_heat_only · cp 1.006 kJ/(kg dry air·K); latent is total minus sensible (unvalidated_component_convention)");
  await expect(page.locator("#ahuAirsideResults")).toContainText("Supply airflow basis: assumed_at_mixed_air_coil_inlet");
  await expect(page.locator("#ahuAirsideResults")).toContainText("assumption — confirm before design use");
});

test("evidence-to-calculator bridge saves, previews, and applies reviewed proposals", async ({ page }) => {
  const errors = [];
  page.on("pageerror", error => errors.push(error.message));
  await mockApi(page);
  let revision = 1;
  const floorCandidate = {
    candidate_id: "floor_ground_123", kind: "floor",
    value: {floor_id: "floor_ground", name: "Ground", elevation_m: null},
    reason: "Review the proposed drawing level.", target_artifact: "hourly_load_model",
    source: "drawing-set.pdf", confidence: "high",
    citations: [{reference: "drawing-set.pdf", page: 1, excerpt: "Ground floor plan"}],
    dependencies: [], fingerprint: "candidate-fingerprint",
  };
  const draft = {schema_version: 2, revision, status: "review_required", candidates: {floors: [floorCandidate], zones: [], rooms: [], room_inputs: [], schedules: [], envelope: []}, review_items: [], decisions: {}, apply_summary: {}};
  await page.route("**/api/calculator-draft", async route => {
    const request = route.request();
    if (request.method() === "GET") return route.fulfill({json: {id: "demo-project", calculator_draft: draft, status: "current"}});
    const body = request.postDataJSON();
    if (body.action === "save_review") {
      expect(body.decisions.floor_ground_123).toEqual(expect.objectContaining({decision: "accept", reviewer: "ENG-1"}));
      revision = 2;
      return route.fulfill({json: {calculator_draft: {...draft, revision, decisions: body.decisions}, status: "current"}});
    }
    if (body.action === "preview_apply") {
      expect(body.expected_revision).toBe(2);
      return route.fulfill({json: {calculator_draft: {...draft, revision: 2, decisions: {floor_ground_123: {decision: "accept", reviewer: "ENG-1"}}}, preview_token: "preview-1", preview: {created: [{candidate_id: "floor_ground_123"}]}, apply_summary: {}}});
    }
    expect(body.action).toBe("apply");
    expect(body.preview_token).toBe("preview-1");
    return route.fulfill({json: {calculator_draft: {...draft, revision: 3, decisions: {floor_ground_123: {decision: "accept", reviewer: "ENG-1"}}, apply_summary: {created: [{candidate_id: "floor_ground_123"}], reports_marked_stale: ["hourly_load_report.json"]}}, apply_summary: {created: [{candidate_id: "floor_ground_123"}], reports_marked_stale: ["hourly_load_report.json"]}, changed_artifacts: ["hourly_load_model"]}});
  });
  await page.goto("/");
  await page.evaluate(input => { DATA = {id: "demo-project"}; show("vRes"); requiredElement("designRequirementsPanel").classList.remove("hide"); showCalculatorDraft(input); }, draft);
  const candidate = page.locator(".draft-candidate");
  await candidate.locator("details").evaluate(element => { element.open = true; });
  await candidate.locator('[data-field="reviewer"]').fill("ENG-1");
  await candidate.locator(".calculator-draft-decision").selectOption("accept");
  await page.locator("#btnSaveCalculatorReview").click();
  await expect(page.locator("#calculatorDraftStatus")).toContainText("review_required");
  await page.locator("#btnPreviewCalculatorDraft").click();
  await expect(page.locator("#calculatorDraftSummary")).toContainText("created");
  await page.locator("#btnApplyCalculatorDraft").click();
  await expect(page.locator("#calculatorDraftSummary")).toContainText("Reports stale");
  expect(errors).toEqual([]);
});

function cardHDraft(){
  const candidate = (candidate_id, kind, name) => ({candidate_id, kind, value: {[kind === "floor" ? "name" : "name"]: name, [`${kind}_id`]: candidate_id}, reason: `Review ${name}`, citations: [{reference: "A-01", page: 1, excerpt: name}], target_artifact: "hourly_load_model", confidence: "high"});
  return {schema_version: 2, revision: 1, status: "review_required", candidates: {
    floors: [candidate("floor-1", "floor", "Ground")], zones: [candidate("zone-1", "zone", "Bar"), candidate("zone-2", "zone", "Kitchen")],
    rooms: [candidate("room-1", "room", "Bar"), candidate("room-2", "room", "Kitchen")], room_inputs: [], schedules: [], envelope: [],
  }, review_items: [], decisions: {}, apply_summary: {}};
}

async function renderCardHDraft(page, draft = cardHDraft()){
  await page.goto("/");
  await page.evaluate(input => { DATA = {id: "demo-project"}; show("vRes"); requiredElement("designRequirementsPanel").classList.remove("hide"); showCalculatorDraft(input); }, draft);
}

test("draft session reviewer fills candidate attribution and per-row reviewer wins", async ({ page }) => {
  await mockApi(page);
  const draft = cardHDraft(); let saved;
  await page.route("**/api/calculator-draft", async route => {
    saved = route.request().postDataJSON().decisions;
    return route.fulfill({json: {calculator_draft: {...draft, decisions: saved}, status: "current"}});
  });
  await renderCardHDraft(page, draft);
  await page.locator("#calculatorDraftReviewer").fill("Shared Reviewer");
  await page.locator("#calculatorDraftReviewSource").fill("Review meeting");
  const rows = page.locator(".draft-candidate");
  await rows.nth(0).locator(".calculator-draft-decision").selectOption("accept");
  await rows.nth(1).locator(".calculator-draft-decision").selectOption("accept");
  await rows.nth(3).locator(".calculator-draft-decision").selectOption("accept");
  await rows.nth(3).locator("details").evaluate(element => { element.open = true; });
  await rows.nth(3).locator('[data-field="reviewer"]').fill("Row Reviewer");
  await page.locator("#btnSaveCalculatorReview").click();
  await expect.poll(() => saved).toBeTruthy();
  expect(saved["floor-1"].reviewer).toBe("Shared Reviewer");
  expect(saved["zone-1"].reviewer).toBe("Shared Reviewer");
  expect(saved["zone-1"].source).toBe("Review meeting");
  expect(saved["room-1"].reviewer).toBe("Row Reviewer");
});

test("draft save without any reviewer is blocked inline before the API request", async ({ page }) => {
  await mockApi(page); let requests = 0;
  await page.route("**/api/calculator-draft", route => { requests++; return route.fulfill({json: {}}); });
  const draft = cardHDraft(); await renderCardHDraft(page, draft);
  await page.locator(".draft-candidate .calculator-draft-decision").first().selectOption("accept");
  await page.locator("#btnSaveCalculatorReview").click();
  await expect(page.locator("#calculatorDraftStatus")).toContainText("Enter a reviewer name (top of the panel) before saving.");
  expect(requests).toBe(0);
});

test("accept all changes only pending candidates in its group", async ({ page }) => {
  await mockApi(page); const draft = cardHDraft(); await renderCardHDraft(page, draft);
  const zoneGroup = page.locator('[data-draft-group="zones"]');
  await zoneGroup.locator('.calculator-draft-decision[data-candidate="zone-2"]').selectOption("reject");
  await zoneGroup.locator("[data-accept-draft-group]").click();
  await expect(zoneGroup.locator('.calculator-draft-decision[data-candidate="zone-1"]')).toHaveValue("accept");
  await expect(zoneGroup.locator('.calculator-draft-decision[data-candidate="zone-2"]')).toHaveValue("reject");
  await expect(page.locator('[data-draft-group="rooms"] .calculator-draft-decision').first()).toHaveValue("pending");
});

test("saved reviewer source and citations are restored for a second save", async ({ page }) => {
  await mockApi(page); const draft = cardHDraft(); let saveCount = 0; const payloads = [];
  await page.route("**/api/calculator-draft", async route => {
    const payload = route.request().postDataJSON(); payloads.push(payload); saveCount++;
    return route.fulfill({json: {calculator_draft: {...draft, revision: saveCount + 1, decisions: payload.decisions}, status: "current"}});
  });
  await renderCardHDraft(page, draft);
  await page.locator("#calculatorDraftReviewer").fill("Shared");
  const row = page.locator(".draft-candidate").first();
  await row.locator("details").evaluate(element => { element.open = true; });
  await row.locator('[data-field="source"]').fill("Marked-up plan");
  await row.locator('[data-field="citation_reference"]').fill("R-17");
  await row.locator('[data-field="citation_excerpt"]').fill("Reviewed dimensions");
  await row.locator(".calculator-draft-decision").selectOption("accept");
  await page.locator("#btnSaveCalculatorReview").click();
  await expect.poll(() => saveCount).toBe(1);
  await page.locator("#btnSaveCalculatorReview").click();
  await expect.poll(() => saveCount).toBe(2);
  expect(payloads[1].decisions["floor-1"]).toEqual(expect.objectContaining({reviewer: "Shared", source: "Marked-up plan", citations: [{reference: "R-17", page: null, excerpt: "Reviewed dimensions"}]}));
  await row.locator("details").evaluate(element => { element.open = true; });
  await expect(row.locator('[data-field="source"]')).toHaveValue("Marked-up plan");
  await expect(row.locator('[data-field="citation_reference"]')).toHaveValue("R-17");
});

test("draft candidate headings show readable names while retaining IDs", async ({ page }) => {
  await mockApi(page); await renderCardHDraft(page);
  const zone = page.locator('[data-candidate="zone-1"]');
  await expect(zone.locator("b")).toContainText("zone · Bar");
  await expect(zone.locator(".draft-candidate-id")).toHaveText("zone-1");
});

test("preview with unsaved decisions saves then previews in one click", async ({ page }) => {
  await mockApi(page); const draft = cardHDraft(); const actions = [];
  await page.route("**/api/calculator-draft", async route => {
    const payload = route.request().postDataJSON(); actions.push(payload.action);
    if (payload.action === "save_review") return route.fulfill({json: {calculator_draft: {...draft, revision: 2, decisions: payload.decisions}, status: "current"}});
    return route.fulfill({json: {calculator_draft: {...draft, revision: 2, decisions: {}}, preview_token: "preview", preview: {created: []}, status: "current"}});
  });
  await renderCardHDraft(page, draft);
  await page.locator("#calculatorDraftReviewer").fill("Reviewer");
  await page.locator(".draft-candidate .calculator-draft-decision").first().selectOption("accept");
  await page.locator("#btnPreviewCalculatorDraft").click();
  await expect.poll(() => actions).toEqual(["save_review", "preview_apply"]);
  await expect(page.locator("#calculatorDraftSummary")).toContainText("created");
});

test("zero-created apply shows unresolved reasons in the summary above candidates", async ({ page }) => {
  await mockApi(page); const draft = cardHDraft();
  await page.route("**/api/calculator-draft", route => route.fulfill({json: {calculator_draft: draft, status: "current", apply_summary: {
    created: [], populated_fields: [], already_present: [], skipped_conflicts: [], missing_dependencies: [],
    unresolved: [{candidate_id: "zone-1", reason: "A zone cannot be applied without a reviewed floor assignment."}], reports_marked_stale: [],
  }}}));
  await renderCardHDraft(page, draft);
  await page.evaluate(() => { DRAFT_PREVIEW_TOKEN = "preview"; });
  await page.locator("#btnApplyCalculatorDraft").click();
  const summary = page.locator("#calculatorDraftSummary");
  await expect(summary).toContainText("Apply changed nothing: no records were created or filled in.");
  await expect(summary).toContainText("Zone Bar: A zone cannot be applied without a reviewed floor assignment.");
  expect(await summary.evaluate(element => element.compareDocumentPosition(document.querySelector("#calculatorDraftCandidates")) & Node.DOCUMENT_POSITION_FOLLOWING)).toBeTruthy();
});

test("server draft failure remains visible inline with its message", async ({ page }) => {
  await mockApi(page);
  await page.route("**/api/calculator-draft", route => route.fulfill({status: 422, json: {error: "Reviewer attribution is required: floor-1"}}));
  const draft = cardHDraft(); await renderCardHDraft(page, draft);
  await page.locator("#calculatorDraftReviewer").fill("Reviewer");
  await page.locator(".draft-candidate .calculator-draft-decision").first().selectOption("accept");
  await page.locator("#btnSaveCalculatorReview").click();
  await expect(page.locator("#calculatorDraftStatus")).toContainText("Could not update calculator draft: Reviewer attribution is required: floor-1");
});

test("stale calculator draft explains upgrade and requires rebuilding before review", async ({ page }) => {
  await mockApi(page);
  const draft = {schema_version: 2, revision: 4, status: "review_required", candidates: {floors: [], zones: [], rooms: [], room_inputs: [], schedules: [], envelope: []}, review_items: [], decisions: {}};
  await page.goto("/");
  await page.evaluate(input => {
    show("vRes");
    showCalculatorDraft({...input, artifact_status: "stale", stale_reasons: ["This saved draft predates room and trace freshness tracking. Rebuild after upgrade. Decisions on unchanged candidates are retained; changed or new candidates return to review."]});
  }, draft);
  await expect(page.locator("#calculatorDraftStatus")).toContainText("predates room and trace freshness tracking");
  await expect(page.locator("#btnBuildCalculatorDraft")).toBeEnabled();
  await expect(page.locator("#btnSaveCalculatorReview")).toBeDisabled();
  await expect(page.locator("#btnPreviewCalculatorDraft")).toBeDisabled();
  await expect(page.locator("#btnApplyCalculatorDraft")).toBeDisabled();
});

test("background draft rebuild explains revision conflict and reload requirement", async ({ page }) => {
  await mockApi(page);
  const candidate = {candidate_id: "floor-ground", kind: "floor", value: {floor_id: "floor-ground", name: "Ground"},
    reason: "Review floor", target_artifact: "hourly_load_model", citations: [{reference: "A-01", page: 1, excerpt: "Ground"}],
    dependencies: [], fingerprint: "floor-fingerprint"};
  const draft = {schema_version: 2, revision: 3, status: "review_required", candidates: {floors: [candidate], zones: [], rooms: [], room_inputs: [], schedules: [], envelope: []}, review_items: [], decisions: {}};
  await page.route("**/api/calculator-draft", route => route.fulfill({status: 409, json: {
    error: "The draft changed. Reload and review the latest version.", code: "revision_conflict", conflict: true, action: "reload_review_preview",
  }}));
  await page.goto("/");
  await page.evaluate(input => { DATA = {id: "demo-project"}; show("vRes"); requiredElement("designRequirementsPanel").classList.remove("hide"); showCalculatorDraft(input); }, draft);
  await page.locator(".draft-candidate details").evaluate(element => { element.open = true; });
  await page.locator('.draft-candidate [data-field="reviewer"]').fill("ENG-1");
  await page.locator(".draft-candidate .calculator-draft-decision").selectOption("accept");
  await page.locator("#btnSaveCalculatorReview").click();
  await expect(page.locator("#calculatorDraftStatus")).toContainText("New evidence rebuilt this draft while you were reviewing");
  await expect(page.locator("#calculatorDraftStatus")).toContainText("Reload the draft");
});

test("reviewer can trace, calibrate, save, and reload a proposal-only room boundary", async ({ page }) => {
  const posts = [];
  let context = {
    id: "demo-project", source_pdf_fingerprint: "pdf-fixture",
    rooms: [{room_id:"room-shop",label:"Shop",level_name:"Ground",needs_trace:true}],
    pages: [{page:1,title:"Ground Plan",drawing_number:"A-01",declared_scale:"1:100",scale_denominator:100,
      image_width_px:1000,image_height_px:800,image_px_per_pt:3.5277777778,preview_url:"/fake-plan.svg",preview_width_px:1000,preview_height_px:800,preview_matches_vector_coordinates:true,
      vector_page_fingerprint:"vector-fixture"}],
    reviewer_room_geometry:{schema_version:1,records:[]},
  };
  const trace = {trace_id:"trace-shop",room_id:"room-shop",room_label:"Shop",level_name:"Ground",page:1,
    points_image_px:[[100,100],[300,100],[300,300],[100,300],[100,100]],snapped_line_ids:[null,null,null,null,null],
    calibration:{status:"agreed",mm_per_px:10,difference_percent:0,dimension_points_image_px:[[100,600],[300,600]],dimension_value_mm:2000},
    reviewer:"QA-1",note:"Synthetic browser fixture",freshness:"current"};
  await page.route("**/api/reviewer-room-geometry?project_id=demo-project", route => route.fulfill({json:context}));
  await page.route("**/api/plan-snap?project_id=demo-project&page=1", route => route.fulfill({json:{...context.pages[0],page:context.pages[0],lines:[],endpoints:[],intersections:[],snap_tolerance_px:8,source_pdf_fingerprint:"pdf-fixture",vector_page_fingerprint:"vector-fixture"}}));
  await page.route("**/api/reviewer-room-geometry", async route => {
    const body=route.request().postDataJSON(); posts.push(body);
    context={...context,reviewer_room_geometry:{schema_version:1,records:[trace]}};
    return route.fulfill({json:{...context,calculation_input_evidence:{},geometry_resolution:{}}});
  });
  await page.route("**/api/calculation-input-evidence?project_id=demo-project", route => route.fulfill({json:{status:"current",calculation_input_evidence:{fingerprint:"evidence-fixture",candidates:[],geometry_resolution:{entities:[{kind:"room_geometry_proof",geometry_status:"geometry_proposed",label:"Shop",source:{page:1},value:{area_m2:4,calibration:{mm_per_px:10}}}],summary:{active_room_area_count:0},deterministic_proof_diagnostics:{pages:[],rooms:[]}},opening_register:{openings:[]}},summary:{candidate_count:1,status_counts:{proposed:1},category_counts:{}},component_interpretations:{}}}));
  await page.route("**/fake-plan.svg", route => route.fulfill({contentType:"image/svg+xml",body:'<svg xmlns="http://www.w3.org/2000/svg" width="1000" height="800"><rect width="1000" height="800" fill="#eee"/><path d="M100 100h200v200H100z" fill="none" stroke="#222" stroke-width="3"/></svg>'}));
  await page.setViewportSize({width:390,height:844});
  await page.goto("/");
  await page.evaluate(() => { DATA={id:"demo-project"}; show("vRes"); requiredElement("designRequirementsPanel").classList.remove("hide"); showCalculationInputEvidence({fingerprint:"initial",geometry_resolution:{entities:[],review_items:[],summary:{},deterministic_proof_diagnostics:{pages:[],rooms:[]}},candidates:[]}, {}, "current"); });
  await page.locator("[data-geometry-room]").selectOption("room-shop");
  await page.locator("[data-geometry-page]").selectOption("1");
  await expect(page.locator("[data-geometry-svg]")).toBeVisible();
  const svg=page.locator("[data-geometry-svg]");
  await expect(page.locator(".reviewer-geometry-resolution")).toContainText("1000 × 800 px");
  const naturalSize=await page.evaluate(async()=>{const image=new Image();image.src=document.querySelector("[data-plan-preview]").getAttribute("href");await image.decode();return [image.naturalWidth,image.naturalHeight];});
  expect(naturalSize).toEqual([1000,800]);
  await page.locator('[data-geometry-zoom="in"]').click();
  const zoomBox=await svg.getAttribute("viewBox");expect(zoomBox.split(" ")).toHaveLength(4);expect(Number(zoomBox.split(" ")[2])).toBeCloseTo(1000/1.5,5);
  const zoomBounds=await svg.boundingBox();await svg.click({position:{x:zoomBounds.width/2,y:zoomBounds.height/2}});
  expect(Math.abs(Number(await page.locator(".trace-vertex").getAttribute("cx"))-500)).toBeLessThan(2);expect(Math.abs(Number(await page.locator(".trace-vertex").getAttribute("cy"))-400)).toBeLessThan(2);
  await page.locator("[data-geometry-reset]").click();
  await page.locator('[data-geometry-mode="pan"]').click();
  const panBox=await svg.boundingBox();await page.mouse.move(panBox.x+100,panBox.y+100);await page.mouse.down();await page.mouse.move(panBox.x+150,panBox.y+140);await page.mouse.up();
  const pannedBox=(await svg.getAttribute("viewBox")).split(" ").map(Number);expect(pannedBox[0]).not.toBe(Number(zoomBox.split(" ")[0]));
  await page.locator('[data-geometry-zoom="reset"]').click();
  await page.locator('[data-geometry-mode="boundary"]').click();
  await svg.focus(); await page.keyboard.press("Enter");
  await expect(page.locator(".reviewer-geometry-help")).toContainText("Boundary: 1 corners");
  await page.locator("[data-geometry-reset]").click();
  for (const [x,y] of [[.1,.125],[.3,.125],[.3,.375],[.1,.375]]) { const box=await svg.boundingBox(); await svg.click({position:{x:box.width*x,y:box.height*y}}); }
  await expect(page.locator(".reviewer-geometry-help")).toContainText("Boundary: 4 corners");
  await page.locator("[data-geometry-close]").click();
  await page.locator('[data-geometry-mode="dimension"]').click();
  for (const [x,y] of [[.1,.75],[.3,.75]]) { const box=await svg.boundingBox(); await svg.click({position:{x:box.width*x,y:box.height*y}}); }
  await page.locator("[data-geometry-dimension]").fill("2000");
  await page.locator("[data-geometry-reviewer]").fill("QA-1");
  await page.locator("[data-geometry-save]").click();
  await expect.poll(()=>posts.length).toBe(1);
  expect(posts[0].points_image_px).toHaveLength(5);
  expect(posts[0].snapped_line_ids).toEqual([null,null,null,null,null]);
  expect(posts[0].dimension_value_mm).toBe(2000);
  await expect(page.locator("#calculationEvidenceSummary")).toContainText("geometry_proposed");
  await expect(page.locator("#calculationEvidenceSummary")).toContainText("4 m² derived area");
  await expect(page.locator("#calculationEvidenceSummary")).toContainText("not activated");
  await expect(page.locator(".reviewer-geometry-result")).toContainText("4.000 m² proposed");
  const mobileWidth=await page.evaluate(()=>({viewport:document.documentElement.clientWidth,workspace:document.querySelector(".reviewer-room-geometry").scrollWidth}));
  expect(mobileWidth.workspace).toBeLessThanOrEqual(mobileWidth.viewport);
});

test("draft offers traced-geometry acceptance only for a linked current proof", async ({ page }) => {
  await page.goto("/");
  const markup = await page.evaluate(() => {
    const base = {candidate_id:"room-1",kind:"room",value:{room_id:"room-1",name:"Shop",geometry_status:"geometry_proposed",geometry_reference:"proof-1"},citations:[],dependencies:[],reason:"Review geometry"};
    const current = calculatorDraftCandidateMarkup({...base,reviewer_geometry_proof:{proof_id:"proof-1",trace_id:"trace-1",area_m2:20,reviewer:"QA-1",calibration:{status:"agreed"}}},{});
    const absent = calculatorDraftCandidateMarkup(base,{});
    return {current,absent};
  });
  expect(markup.current).toContain("Accept traced geometry");
  expect(markup.absent).not.toContain("Accept traced geometry");
  expect(markup.current).toContain("trace-1");
  expect(markup.current).toContain("20");
});

test("AI input assembly saves one project scope and calculates from an immutable snapshot", async ({ page }) => {
  const errors = [];
  page.on("pageerror", error => errors.push(error.message));
  await mockApi(page);
  const context = {
    schema_version: 1,
    site: {country: "AU", locality: "Sydney", state: "NSW", climate_zone: "5", source: "Client brief", citations: []},
    building_use: "retail", room_uses: {},
    conditioned_scope: {status: "missing", mode: "all_rooms", room_ids: [], source: "", citations: []}, reviewer: "",
  };
  const snapshot = {
    schema_version: 2, input_fingerprint: "snapshot-abc123", status: "draft", policy_version: "au-cooling-v1", source_pack_version: "au-retail-v1",
    included_room_ids: ["room_001"], excluded_room_ids: [],
    resolved_inputs: [{input_id: "input-1", target: "rooms.room_001.occupancy", value: 10, unit: "people", resolution_status: "derived_evidence", source: "Approved pack", policy_rule: "area × density", derivation: {formula: "occupancy = area × density"}}],
    issues: [{status: "draft", affected_id: "room_001", reason: "Infiltration is not assessed and excluded.", source_artifact: "hourly_load_model.json"}],
  };
  await page.route("**/api/calculator-inputs**", async route => {
    const request = route.request();
    if (request.method() === "GET") return route.fulfill({json: {calculator_input_set: {}, project_context: context, calculator_input_overrides: {revision: 0, records: []}, status: "blocked"}});
    const body = request.postDataJSON();
    if (body.action === "save_context") {
      expect(body.project_context.conditioned_scope).toEqual(expect.objectContaining({status: "confirmed", mode: "all_rooms", source: "Client cooling brief"}));
      return route.fulfill({json: {project_context: {...body.project_context, revision: 1}, status: "current"}});
    }
    if (body.action === "assemble") {
      expect(body.selected_scenario_ids).toEqual(["summer_day"]);
      return route.fulfill({json: {calculator_input_set: snapshot, status: "draft"}});
    }
    return route.fulfill({json: {status: "not_available", message: "No worker configured."}});
  });
  await page.route("**/api/hourly-load-report", async route => {
    const body = route.request().postDataJSON();
    expect(body.input_set_fingerprint).toBe("snapshot-abc123");
    return route.fulfill({json: {status: "current", hourly_load_report: {status: "draft", readiness: {status: "draft", issues: []}, scope_summary: {complete_scope: false}, scenario_results: []}}});
  });
  await page.goto("/");
  await page.evaluate(requirements => { DATA = {id: "demo-project"}; show("vRes"); showDesignRequirements(requirements, {}, []); }, coolingRequirements);
  await page.getByRole("button", {name: "4 Calculate cooling"}).click();
  await page.locator("#hourlyScenarioIds").fill("summer_day");
  await page.locator("#calculatorInputSection details").first().evaluate(element => { element.open = true; });
  await page.locator("#contextScopeMode").selectOption("all_rooms");
  await page.locator("#contextScopeSource").fill("Client cooling brief");
  await page.locator("#contextScopeCitation").fill("Brief section 2");
  await page.locator("#btnSaveProjectContext").click();
  await page.locator("#btnAssembleCalculatorInputs").click();
  await expect(page.locator("#calculatorInputStatus")).toContainText("immutable snapshot");
  await expect(page.locator("#calculatorInputSummary")).toContainText("Derived from evidence");
  await expect(page.locator("#calculatorInputIssues")).toContainText("Infiltration");
  await page.locator("#btnCalculateHourlyLoad").click();
  expect(errors).toEqual([]);
});

test("ventilation calculation displays outside-air and exhaust evidence", async ({ page }) => {
  const errors = [];
  page.on("pageerror", error => errors.push(error.message));
  await mockApi(page);
  await page.route("**/api/ventilation", async route => {
    const body = route.request().postDataJSON();
    expect(body.requirements.zones[0].ventilation_requirements.people_rate_lps_per_person).toBe(5);
    return route.fulfill({ json: {
      ventilation_status: "current",
      ventilation_report_url: "/output/ventilation_report.json",
      reasoning_zip_url: "/output/reasoning_packet.zip",
      ventilation_report: {
        status: "calculated", calculated_zone_count: 1, blocked_zone_count: 0,
        total_outside_air_lps: 90, total_process_exhaust_lps: 0,
        zone_results: [{ zone_name: "Sales area", status: "calculated", process_exhaust_lps: 0,
          outside_air: { required_lps: 90, governing_component: "occupancy" },
          make_up_air: { required_lps: 0 }, air_balance: { status: "evaluated", net_lps: 0 }, warnings: [],
        }],
      },
    } });
  });

  await page.goto("/");
  await page.evaluate(requirements => {
    DATA = { id: "demo-project" };
    show("vRes");
    showDesignRequirements(requirements, {}, []);
  }, ventilationRequirements);
  await page.getByRole("button", {name: "5 Review and package"}).click();
  await page.locator("#btnCalculateVentilation").click();

  await expect(page.locator("#ventilationStatus")).toContainText("Outside air 90.0 L/s");
  await expect(page.locator("#ventilationResults")).toContainText("Make-up air 0.0 L/s");
  expect(errors).toEqual([]);
});

test("reviewed envelope editor saves a confirmed opaque boundary", async ({ page }) => {
  const errors = [];
  page.on("pageerror", error => errors.push(error.message));
  await mockApi(page);
  const emptyLibrary = { schema_version: 1, updated_at: "", constructions: [], windows: [], shading_records: [] };
  const emptyModel = { schema_version: 1, updated_at: "", active_for_calculation: false, surfaces: [] };
  const readiness = { status: "review_required", active_for_calculation: false, included: [], blocked: [], stored_not_calculated: [] };
  await page.route("**/api/envelope-library**", async route => {
    if (route.request().method() === "GET") return route.fulfill({ json: { envelope_library: emptyLibrary, readiness } });
    const body = route.request().postDataJSON();
    expect(body.envelope_library.constructions[0]).toEqual(expect.objectContaining({ record_id: "wall-a", kind: "opaque_wall", u_value_w_m2k: 0.5, review_status: "confirmed" }));
    return route.fulfill({ json: { envelope_library: body.envelope_library, readiness } });
  });
  await page.route("**/api/envelope-model**", async route => {
    if (route.request().method() === "GET") return route.fulfill({ json: { envelope_model: emptyModel, readiness } });
    const body = route.request().postDataJSON();
    expect(body.envelope_model.active_for_calculation).toBe(true);
    expect(body.envelope_model.surfaces[0]).toEqual(expect.objectContaining({ surface_id: "north-wall", owner_zone_id: "zone_001", boundary_method: "external", construction_id: "wall-a", review_status: "confirmed" }));
    return route.fulfill({ json: { envelope_model: body.envelope_model, readiness: { ...readiness, active_for_calculation: true } } });
  });
  await page.goto("/");
  await page.evaluate(requirements => { DATA = { id: "demo-project" }; show("vRes"); showDesignRequirements(requirements, {}, []); }, coolingRequirements);
  await page.getByRole("button", {name: "3 Complete the building model"}).click();
  await page.locator("#btnAddConstruction").click();
  await page.locator(".envelope-construction .env-id").fill("wall-a");
  await page.locator(".envelope-construction .env-title").fill("Reviewed wall");
  await page.locator(".envelope-construction .env-u").fill("0.5");
  await page.locator(".envelope-construction .env-status").selectOption("confirmed");
  await page.locator(".envelope-construction .env-source").fill("Engineer reviewed schedule");
  await page.locator("#btnAddBoundary").click();
  await page.locator(".envelope-boundary .boundary-id").fill("north-wall");
  await page.locator(".envelope-boundary .boundary-zone").fill("zone_001");
  await page.locator(".envelope-boundary .boundary-area").fill("10");
  await page.locator(".envelope-boundary .boundary-construction").fill("wall-a");
  await page.locator(".envelope-boundary .boundary-status").selectOption("confirmed");
  await page.locator(".envelope-boundary .boundary-source").fill("Reviewed facade drawing");
  await page.locator("#envelopeActive").check();
  await page.locator("#btnSaveEnvelope").click();
  await expect(page.locator("#envelopeStatus")).toContainText("reviewed model active");
  expect(errors).toEqual([]);
});

function envelopeTraceContext({calibrationStatus = "agreed", edges = null, roof = "unknown"} = {}){
  const context = mechanicalTraceContext([{room_id:"room-use:level-2:shop",label:"Shop",level_name:"Level 2",needs_trace:false}]);
  const trace = {trace_id:"trace-shop-envelope",room_id:"room-use:level-2:shop",room_label:"Shop",level_name:"Level 2",page:5,
    points_image_px:[[100,100],[300,100],[300,300],[100,300],[100,100]],snapped_line_ids:[null,null,null,null,null],
    calibration:{status:calibrationStatus,mm_per_px:calibrationStatus === "agreed" ? 10 : null,dimension_points_image_px:[[100,600],[300,600]],dimension_value_mm:2000},
    reviewer:"QA-1",note:"Plan trace",freshness:"current",edges:edges || Array.from({length:4},(_,index)=>({index,boundary:"unknown"})),roof};
  context.reviewer_room_geometry.records=[trace];
  return {context,trace};
}

test("reviewer can classify calibrated trace edges and roof, and the picker shows saved status", async ({page}) => {
  const {context,trace}=envelopeTraceContext(); const posts=[];
  await page.route("**/api/reviewer-room-geometry?project_id=demo-project",route=>route.fulfill({json:context}));
  await openTraceWorkspace(page);
  await page.evaluate(()=>loadReviewerRoomGeometryWorkspace());
  const workspace=page.locator("#reviewerRoomGeometryWorkspace");
  await workspace.locator("[data-geometry-room]").selectOption(trace.room_id);
  await workspace.locator("[data-geometry-page]").selectOption("5");
  await expect(workspace.locator("[data-envelope-edge-class]")).toHaveCount(4);
  await workspace.locator('[data-envelope-edge-select="0"]').click();
  await expect(workspace.locator('[data-envelope-edge-select="0"]')).toHaveAttribute("aria-pressed","true");
  await workspace.locator('[data-envelope-edge-class="0"]').selectOption("external");
  await workspace.locator("[data-envelope-roof]").selectOption("exposed");
  await workspace.locator("[data-envelope-reviewer]").fill("QA-2");
  await page.route("**/api/reviewer-room-geometry",async route=>{
    const body=route.request().postDataJSON();posts.push(body);
    const saved={...trace,edges:body.edges,roof:body.roof,envelope_reviewer:body.reviewer,envelope_declared_at:"2026-10-04T00:00:00Z"};
    context.reviewer_room_geometry.records=[saved];
    return route.fulfill({json:context});
  });
  await workspace.locator("[data-envelope-save]").click();
  await expect.poll(()=>posts.length).toBe(1);
  expect(posts[0]).toEqual(expect.objectContaining({action:"classify_envelope",trace_id:trace.trace_id,reviewer:"QA-2",roof:"exposed"}));
  expect(posts[0].edges).toHaveLength(4);
  expect(posts[0].edges[0]).toEqual({index:0,boundary:"external"});
  await expect(workspace.locator("[data-envelope-saved]")).toContainText("QA-2");
  await expect(workspace.locator("[data-envelope-saved]")).toContainText("Reviewer");
  await expect(workspace.locator("[data-geometry-room] option:checked")).toContainText("walls: 1 of 4 classified · roof exposed");
});

test("mixed envelope declarations show provenance separately for each value", async ({page})=>{
  const {context,trace}=envelopeTraceContext({edges:[{index:0,boundary:"external"},{index:1,boundary:"unknown"},{index:2,boundary:"unknown"},{index:3,boundary:"unknown"}],roof:"exposed"});
  trace.edge_sources={"0":"reviewer"};trace.roof_source="ai_determined";trace.envelope_reviewer="Archie AI";trace.declaration_source="ai_determined";
  await page.route("**/api/reviewer-room-geometry?project_id=demo-project",route=>route.fulfill({json:context}));
  await openTraceWorkspace(page);await page.evaluate(()=>loadReviewerRoomGeometryWorkspace());
  const workspace=page.locator("#reviewerRoomGeometryWorkspace");
  await workspace.locator("[data-geometry-room]").selectOption(trace.room_id);
  await workspace.locator("[data-geometry-page]").selectOption("5");
  await expect(workspace.locator('[data-envelope-edge-select="0"]')).toContainText("Reviewer");
  await expect(workspace.locator(".reviewer-envelope-roof")).toContainText("AI-determined");
});

test("reviewer can declare page north, add a cited shopfront, and see unresolved envelope wording", async ({page})=>{
  const {context,trace}=envelopeTraceContext();
  context.glazing_choices={retail:{label:"Preliminary single glazing (pack au-preliminary-v3): U 5.8, SHGC 0.45",u_value_w_m2k:5.8,shgc:0.45}};
  context.shading_categories={unshaded:1,partial:.65,deep:.3};
  context.glazing_frame_fraction=.15;
  const posts=[];
  await page.route("**/api/reviewer-room-geometry?project_id=demo-project",route=>route.fulfill({json:context}));
  await openTraceWorkspace(page);
  await page.evaluate(()=>loadReviewerRoomGeometryWorkspace());
  const workspace=page.locator("#reviewerRoomGeometryWorkspace");
  await workspace.locator("[data-geometry-room]").selectOption(trace.room_id);
  await workspace.locator("[data-geometry-page]").selectOption("5");
  await workspace.locator('[data-envelope-edge-class="0"]').selectOption("external");
  await workspace.locator("[data-envelope-reviewer]").fill("QA-3");
  await workspace.locator("[data-north-bearing]").fill("25");
  await page.route("**/api/reviewer-room-geometry",async route=>{
    const body=route.request().postDataJSON();posts.push(body);
    if(body.action==="declare_north"){
      context.page_north={"5":{page:5,plan_up_azimuth_deg:25,source:"reviewer_typed_page_up_bearing",reviewer:"QA-3",declared_at:"now"}};
      context.reviewer_room_geometry.page_north=context.page_north;
      trace.edge_facings={"0":"S"};
    }else{
      trace.edges=body.edges;trace.openings=body.openings;trace.roof=body.roof;trace.envelope_reviewer=body.reviewer;trace.envelope_declared_at="now";
      context.reviewer_room_geometry.records=[trace];
    }
    return route.fulfill({json:context});
  });
  await workspace.locator("[data-north-save]").click();
  await expect.poll(()=>posts.length).toBe(1);
  expect(posts[0]).toEqual(expect.objectContaining({action:"declare_north",page:5,plan_up_azimuth_deg:25,reviewer:"QA-3"}));
  await expect(workspace.locator('[data-envelope-edge-select="0"]')).toContainText("faces S");
  await workspace.locator("[data-envelope-edge-class='0']").selectOption("external");
  await workspace.locator("[data-opening-edge]").selectOption("0");
  await workspace.locator("[data-opening-width]").evaluate(input=>{input.value="1.5";input.dispatchEvent(new Event("input",{bubbles:true}));});
  await workspace.locator("[data-opening-sill]").fill("0.9");
  await workspace.locator("[data-opening-head]").fill("2.65");
  await workspace.locator("[data-opening-page]").fill("26");
  await workspace.locator("[data-opening-glazing]").selectOption("retail");
  await expect(workspace.locator("[data-opening-glazing] option")).toHaveCount(1);
  await expect(workspace.locator("[data-opening-glazing] option").first()).toHaveText("Preliminary single glazing (pack au-preliminary-v3): U 5.8, SHGC 0.45");
  await workspace.locator("[data-opening-shading]").selectOption("partial");
  await workspace.locator("[data-opening-add]").click();
  await expect(workspace.locator("[data-opening-row]")).toContainText("elevation p.26");
  await workspace.locator("[data-envelope-save]").click();
  await expect.poll(()=>posts.length).toBe(2);
  expect(posts[1].openings).toEqual([expect.objectContaining({edge_index:0,width_m:1.5,elevation_page:26,glazing_choice:"retail",shading_category:"partial"})]);
  await expect(workspace.locator("[data-north-saved]")).toContainText("25.00°");
  await page.evaluate(()=>{
    drawAiPreliminary({settings:{},run:{},hourly_ai_preliminary_load_report:{
      label:"Preliminary cooling",included_scope_peak:{design_total_kw:12},room_names:[{room_id:"room-shop",name:"Shop"}],
      unresolved_room_inputs:[
        {room_id:"room-shop",component:"external walls — orientation not assessed (no façade solar)",component_id:"external_wall_orientation_0"},
        {room_id:"room-shop",component:"Glazing sun — orientation not assessed",component_id:"glazing_solar_front"}
      ],known_exclusions:[],preliminary_surface_summary:{included:1,openings_included:1}
    },room_scope:{}});
  });
  await expect(page.locator("#aiPreliminaryResults")).toContainText("Not included in this total");
  await expect(page.locator("#aiPreliminaryResults")).toContainText("Glazing sun — orientation not assessed — Shop");
  await expect(page.locator("#aiPreliminaryResults")).toContainText("Openings: 1 included");
});

test("internal-room shortcut classifies every edge and roof, and classification errors remain visible", async ({page})=>{
  const {context,trace}=envelopeTraceContext({calibrationStatus:"unresolved"});
  await page.route("**/api/reviewer-room-geometry?project_id=demo-project",route=>route.fulfill({json:context}));
  await openTraceWorkspace(page);
  await page.evaluate(()=>loadReviewerRoomGeometryWorkspace());
  const workspace=page.locator("#reviewerRoomGeometryWorkspace");
  await workspace.locator("[data-geometry-room]").selectOption(trace.room_id);
  await workspace.locator("[data-geometry-page]").selectOption("5");
  await expect(workspace.locator("[data-envelope-classification]")).toContainText("Envelope can only be classified on a calibrated room trace.");
  await expect(workspace.locator("[data-envelope-save]")).toHaveCount(0);

  trace.calibration={...trace.calibration,status:"agreed",mm_per_px:10};
  await page.evaluate(()=>loadReviewerRoomGeometryWorkspace());
  await workspace.locator("[data-geometry-room]").selectOption(trace.room_id);
  await workspace.locator("[data-geometry-page]").selectOption("5");
  await workspace.locator("[data-envelope-internal]").click();
  await expect(workspace.locator('[data-envelope-edge-class="0"]')).toHaveValue("internal");
  await expect(workspace.locator("[data-envelope-roof]")).toHaveValue("not_exposed");
  await workspace.locator("[data-envelope-reviewer]").fill("QA");
  await page.route("**/api/reviewer-room-geometry",route=>route.fulfill({status:400,json:{error:"Envelope can only be classified on a calibrated room trace."}}));
  await workspace.locator("[data-envelope-save]").click();
  await expect(workspace.locator("[data-geometry-error]")).toHaveText("Envelope can only be classified on a calibrated room trace.");
});

test("re-saving a classified trace warns that its envelope declarations reset", async ({page})=>{
  const edges=Array.from({length:4},(_,index)=>({index,boundary:"internal"}));
  const {context,trace}=envelopeTraceContext({edges,roof:"not_exposed"});
  await page.route("**/api/reviewer-room-geometry?project_id=demo-project",route=>route.fulfill({json:context}));
  await openTraceWorkspace(page);
  await page.evaluate(()=>loadReviewerRoomGeometryWorkspace());
  const workspace=page.locator("#reviewerRoomGeometryWorkspace");
  await workspace.locator("[data-geometry-room]").selectOption(trace.room_id);
  await workspace.locator("[data-geometry-page]").selectOption("5");
  await page.route("**/api/reviewer-room-geometry",async route=>{
    const body=route.request().postDataJSON();
    const reset={...trace,points_image_px:body.points_image_px,edges:Array.from({length:4},(_,index)=>({index,boundary:"unknown"})),roof:"unknown"};
    context.reviewer_room_geometry.records=[reset];
    return route.fulfill({json:context});
  });
  await workspace.locator("[data-geometry-save]").click();
  await expect(workspace.locator("[data-envelope-reset]")).toContainText("Re-saving this trace reset its prior wall, roof, and opening declarations");
});

test("analysis reaches results without browser errors", async ({ page }) => {
  const errors = [];
  page.on("pageerror", error => errors.push(error.message));
  page.on("console", message => {
    if (message.type() === "error") errors.push(message.text());
  });
  await mockApi(page);

  await page.goto("/");
  await page.locator("#pdf").setInputFiles({
    name: "demo.pdf",
    mimeType: "application/pdf",
    buffer: Buffer.from("%PDF-1.4\n%%EOF"),
  });
  await expect(page.locator("#btnAnalyse")).toBeVisible();
  await page.locator("#btnAnalyse").click();

  await expect(page.locator("#summaryTitle")).toHaveText("Ready for ChatGPT packet");
  await expect(page.locator("#btnConfirm, #btnConfirmTop")).toHaveCount(0);
  await expect(page.locator("#btnContinue")).toBeEnabled();
  expect(errors).toEqual([]);
});

test("saved project opens into results", async ({ page }) => {
  const errors = [];
  page.on("pageerror", error => errors.push(error.message));
  await mockApi(page);

  await page.goto("/");
  await page.locator("[data-open='demo-project']").click();

  await expect(page.locator("#summaryTitle")).toHaveText("Ready for ChatGPT packet");
  await expect(page.locator("#btnContinue")).toBeEnabled();
  expect(errors).toEqual([]);
});

test("vision reply history is shown in the app after opening the review workflow", async ({ page }) => {
  await mockApi(page);
  await page.goto("/");
  await page.locator("[data-open='demo-project']").click();
  await page.locator("#btnContinue").click();
  await expect(page.locator("#visionPanel")).toBeVisible();
  await expect(page.locator("#visionHistory")).toContainText("ACCEPTED");
  await expect(page.locator("#visionHistory")).toContainText("ChatGPT model note");
  await expect(page.locator("#visionHistory")).toContainText("Packet pages: 1, 2");
  await expect(page.locator("#visionHistory a")).toHaveText("Open raw reply");
});

test("annual report shows monthly subtotals and incomplete-hour counts", async ({ page }) => {
  await mockApi(page);
  await page.goto("/");
  await page.evaluate(() => drawAnnualEnergy({
    status: "draft", scope_summary: {complete_scope: false, included_room_ids: ["room_001"]},
    cooling: {status: "draft", annual_energy_kwh: 2, peak_kw: 1, monthly_kwh: {"1": 2}, monthly_incomplete_hours: {"1": 3}, blocked_reasons: ["Room room_001 has missing cooling hours."]},
    heating: {status: "not selected", monthly_kwh: {}, monthly_incomplete_hours: {}},
    ahu: {status: "not selected"}, plant: {status: "not selected"}, readiness: {issues: []},
  }, "current", {}));
  await expect(page.locator("#annualResults")).toContainText("Cooling incomplete hours");
  await expect(page.locator("#annualResults")).toContainText("Incomplete hours are excluded");
  await expect(page.locator("#annualResults")).toContainText("3");
});

test("selected design weather displays the wet-bulb basis and dew-point derivation", async ({ page }) => {
  await mockApi(page);
  await page.goto("/");
  await page.evaluate(() => drawSiteDesignWeather({status: "draft_ready", site_design_weather_resolution: {
    design_basis: "comfort", cooling: {selection: "contractor_selected", selected: {
      record_id: "weather-cooling", publisher: "AIRAH", station_reference: "Sydney", release_version: "fixture-1",
      citation: "DA09 fixture", expiry: "2099-01-01", profile: {hours: [{wet_bulb_basis: "thermodynamic"}]},
      conversion: {"0": {wet_bulb_method_id: "thermodynamic_ashrae_eq33_iapws_water_ice_v3"}},
    }}, heating: {candidates: [], selected: {}, conflicts: []},
  }}));
  await expect(page.locator("#siteDesignWeatherResults")).toContainText("Wet-bulb basis: thermodynamic");
  await expect(page.locator("#siteDesignWeatherResults")).toContainText("1 hour(s) derived from dew point");
});

test("frontend assets do not cache and accept query strings", async ({ request }) => {
  const home = await request.get("/");
  const script = await request.get("/frontend/js/app.js?smoke=1");

  expect(home.headers()["cache-control"]).toContain("no-store");
  expect(script.ok()).toBeTruthy();
  expect(script.headers()["cache-control"]).toContain("no-store");
});

test('project list failure offers a working inline retry', async ({ page }) => {
  let attempts = 0;
  await page.route('**/api/projects', route => {
    attempts += 1;
    return attempts === 1
      ? route.fulfill({ status: 503, json: { error: 'Unavailable' } })
      : route.fulfill({ json: [{ id: 'retry-project', name: 'Recovered project.pdf', pages: 4, analysed: false }] });
  });
  await page.goto('/');
  await expect(page.getByText('Could not load your projects.', { exact: false })).toBeVisible();
  await page.getByRole('button', { name: 'Retry', exact: true }).click();
  await expect(page.getByRole('button', { name: /Recovered project.pdf/ })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Retry', exact: true })).toHaveCount(0);
});

test('project list distinguishes records with otherwise identical labels', async ({ page }) => {
  await page.route('**/api/test-mode/status', route => route.fulfill({ json: { enabled: false } }));
  await page.route('**/api/projects', route => route.fulfill({ json: [
    { id: 'drawing-set-12345678', name: 'Same drawings.pdf', pages: 4, analysed: true, relevant: 3, revision: 'rev-v7', updated_at: '2026-09-01' },
    { id: 'drawing-set-87654321', name: 'Same drawings.pdf', pages: 4, analysed: true, relevant: 3, revision: 'rev-v7', updated_at: '2026-09-01' },
  ] }));
  await page.goto('/');

  await expect(page.locator('#projects [data-open="drawing-set-12345678"]')).toContainText('ID 12345678');
  await expect(page.locator('#projects [data-open="drawing-set-87654321"]')).toContainText('ID 87654321');
});

test('current project navigation returns keyboard focus to the workspace', async ({ page }) => {
  await page.route('**/api/projects', route => route.fulfill({ json: [] }));
  await page.goto('/');
  await page.getByRole('button', { name: 'Current project', exact: true }).click();
  await expect(page.locator('#main')).toBeFocused();
});

test('room-input panels show stale state and accept overrides after re-resolution', async ({ page }) => {
  const posts = [];
  await mockApi(page);
  await page.route('**/api/ceiling-volume-resolution', async route => {
    if (route.request().method() !== 'POST') return route.continue();
    const body = route.request().postDataJSON();
    posts.push(body);
    return route.fulfill({ json: {
      status: 'draft_ready', stale_reasons: [], ceiling_volume_resolution: {records: [{
        room_id: body.room_id, original_label: 'Kitchen', level_name: 'Ground',
        ceiling_height_mm: Number(body.ceiling_height_mm), volume_m3: 56, origin: 'contractor_override',
        confidence_score: 1, status: 'resolved', evidence: [], override: {height_mm: Number(body.ceiling_height_mm)},
      }]},
    }});
  });
  await page.route('**/api/internal-gains-resolution', async route => {
    if (route.request().method() !== 'POST') return route.continue();
    const body = route.request().postDataJSON();
    posts.push(body);
    return route.fulfill({ json: {
      status: 'provisional', stale_reasons: [], internal_gains_resolution: {records: [{
        room_id: body.room_id, original_label: 'Kitchen', level: 'Ground', space_scope: 'comfort_hvac',
        occupancy_count: Number(body.value), lighting_load_w: 0, equipment: [], schedule_id: 'schedule-kitchen',
        confidence_score: 1, confidence_band: 'high', status: 'provisional', evidence: [],
        override: {occupancy_count: {value: Number(body.value)}}, unresolved_fields: [],
      }]},
    }});
  });
  await page.goto('/');
  await page.locator("[data-open='demo-project']").click();
  await page.getByRole('button', {name: 'Confirm selected drawings'}).click();
  await page.waitForLoadState('networkidle');
  await page.evaluate(() => {
    setContractorWorkflowStage('calculate', {focus: false});
    document.querySelector('details.workflow-advanced-tools').open = true;
    loadAiPreliminary = async () => {};
  });
  await expect(page.locator('#internalGainsResolutionStatus')).toContainText('Resolve internal gains');
  await page.evaluate(() => {
    const ceiling = {room_id: 'ceiling-kitchen', original_label: 'Kitchen', level_name: 'Ground',
      ceiling_height_mm: 2800, volume_m3: 50, origin: 'preliminary_fallback', confidence_score: 0.35,
      status: 'stale', evidence: [], rationale: 'Retained override', override: {height_mm: 2800}};
    const internal = {room_id: 'internal-kitchen', original_label: 'Kitchen', level: 'Ground',
      space_scope: 'comfort_hvac', occupancy_count: 20, lighting_load_w: 0, equipment: [],
      schedule_id: 'schedule-kitchen', confidence_score: 0.35, confidence_band: 'low', status: 'stale',
      evidence: [], unresolved_fields: [], override: {occupancy_count: {value: 20}}};
    drawCeilingVolumeResolution({status: 'stale', stale_reasons: ['Ceiling inputs changed.'],
      ceiling_volume_resolution: {records: [ceiling], stale_reasons: ['Ceiling inputs changed.']}});
    drawInternalGainsResolution({status: 'stale', stale_reasons: ['Room inputs changed.'],
      internal_gains_resolution: {records: [internal]}});
  });
  await expect(page.locator('#ceilingVolumeResolutionStatus')).toContainText('stale');
  await expect(page.locator('#ceilingVolumeResolutionStatus')).toContainText('Ceiling inputs changed');
  await expect(page.locator('#internalGainsResolutionStatus')).toContainText('stale');
  await expect(page.locator('#internalGainsResolutionStatus')).toContainText('Room inputs changed');

  await page.evaluate(() => {
    drawCeilingVolumeResolution({status: 'draft_ready', stale_reasons: [], ceiling_volume_resolution: {records: [{
      room_id: 'ceiling-kitchen', original_label: 'Kitchen', level_name: 'Ground', ceiling_height_mm: 2800,
      volume_m3: 50, origin: 'preliminary_fallback', confidence_score: 0.35, status: 'provisional', evidence: [],
    }]}});
    drawInternalGainsResolution({status: 'provisional', stale_reasons: [], internal_gains_resolution: {records: [{
      room_id: 'internal-kitchen', original_label: 'Kitchen', level: 'Ground', space_scope: 'comfort_hvac',
      occupancy_count: 20, lighting_load_w: 0, equipment: [], schedule_id: 'schedule-kitchen',
      confidence_score: 0.35, confidence_band: 'low', status: 'provisional', evidence: [], unresolved_fields: [],
    }]}});
  });
  await page.locator('[data-ceiling-room-id="ceiling-kitchen"] [data-ceiling-height]').fill('3100');
  await page.locator('[data-ceiling-room-id="ceiling-kitchen"] [data-ceiling-reviewer]').fill('Engineer');
  await page.locator('[data-ceiling-room-id="ceiling-kitchen"] [data-ceiling-override]').click();
  await expect.poll(() => posts.length).toBe(1);
  await page.evaluate(() => drawInternalGainsResolution({status: 'provisional', stale_reasons: [], internal_gains_resolution: {records: [{
    room_id: 'internal-kitchen', original_label: 'Kitchen', level: 'Ground', space_scope: 'comfort_hvac',
    occupancy_count: 20, lighting_load_w: 0, equipment: [], schedule_id: 'schedule-kitchen',
    confidence_score: 0.5, confidence_band: 'low', status: 'provisional', evidence: [], unresolved_fields: [],
  }]}}));
  await page.evaluate(() => document.querySelector('#visionPanel').classList.remove('hide'));
  await page.locator('[data-internal-gains-room-id="internal-kitchen"] [data-internal-reviewer]').fill('Engineer');
  await page.locator('[data-internal-gains-room-id="internal-kitchen"] [data-internal-occupancy]').fill('24');
  await page.locator('[data-internal-gains-room-id="internal-kitchen"] [data-internal-override]').click();
  await expect.poll(() => posts.length).toBe(2);
  expect(posts.map(item => item.action)).toEqual(['apply_override', 'apply_override']);
  expect(posts.map(item => item.reviewer)).toEqual(['Engineer', 'Engineer']);
});

test("AI preliminary result shows unassessed components beneath the total", async ({ page }) => {
  await page.goto("/");
  await page.evaluate(() => {
    drawValueResolution = () => {};
    drawAiPreliminary({
      settings: {}, run: {},
      hourly_ai_preliminary_load_report: {
        label: "AI preliminary estimate", included_scope_peak: {design_total_kw: 30.75},
        scenario_results: [{rooms: [
          {room_id: "bar-id", name: "Bar"},
          {room_id: "kitchen-id", name: "Kitchen"},
          {room_id: "shop-id", name: "Shop"},
        ]}],
        unresolved_room_inputs: [
          {room_id: "bar-id", component_type: "make_up_air"},
          {room_id: "kitchen-id", component_type: "make_up_air"},
          {room_id: "shop-id", component_type: "make_up_air"},
          {room_id: "bar-id", component_type: "envelope", component_id: "roof_solar", component: "Roof sun — not assessed"},
          {room_id: "shop-id", component_type: "envelope", component_id: "unclassified_wall_boundaries", component: "Walls — boundary not classified"},
        ],
      },
    });
  });
  const result = page.locator("#aiPreliminaryResults");
  await expect(result).toContainText("Included-scope peak: 30.75 kW");
  await expect(result).toContainText("Not included in this total");
  await expect(result).toContainText("Make-up air — Bar, Kitchen, Shop");
  await expect(result).toContainText("Roof sun — not assessed — Bar");
  await expect(result).toContainText("Walls — boundary not classified — Shop");
  await expect(result).not.toContainText("Envelope —");
  await expect(result).not.toContainText("bar-id");
});

test("draft summary counts reasons beyond the first eight and names unassigned room inputs", async ({ page }) => {
  await mockApi(page); const draft = cardHDraft();
  draft.candidates.room_inputs = [{candidate_id: "ceiling-1", kind: "ceiling", value: {room_id: ""}, reason: "Confirm room allocation",
    citations: [{reference: "A-02", page: 22, excerpt: "CH:2600MM"}], target_artifact: "hourly_load_model", confidence: "medium"}];
  draft.apply_summary = {created: [], populated_fields: [], already_present: [], skipped_conflicts: [], missing_dependencies: [],
    unresolved: Array.from({length: 11}, (_, index) => ({candidate_id: index ? `zone-${index}` : "ceiling-1", reason: "Missing reviewed fields: room_id"}))};
  await renderCardHDraft(page, draft);
  const summary = page.locator("#calculatorDraftSummary");
  await expect(summary).toContainText("+ 3 more not shown");
  await expect(summary).toContainText("Ceiling CH:2600MM — room not assigned: Missing reviewed fields: room_id");
  await expect(page.locator('[data-candidate="ceiling-1"] b')).toContainText("ceiling · CH:2600MM — room not assigned");
});

test("apply that only fills fields is not reported as changing nothing", async ({ page }) => {
  await mockApi(page); const draft = cardHDraft();
  await page.route("**/api/calculator-draft", route => route.fulfill({json: {calculator_draft: draft, status: "current", apply_summary: {
    created: [], populated_fields: [{candidate_id: "room-1", fields: ["area_m2"]}], already_present: [], skipped_conflicts: [],
    missing_dependencies: [], unresolved: [], reports_marked_stale: [],
  }}}));
  await renderCardHDraft(page, draft);
  await page.evaluate(() => { DRAFT_PREVIEW_TOKEN = "preview"; });
  await page.locator("#btnApplyCalculatorDraft").click();
  const summary = page.locator("#calculatorDraftSummary");
  await expect(summary).toContainText("1 fields populated");
  await expect(summary).not.toContainText("Apply changed nothing");
});

function roomScopeState(status = "not_confirmed"){
  return {status, candidate_fingerprint: "fp-1", confirmation: status === "confirmed" ? {reviewer: "QA", confirmed_at: "2026-10-03"} : null,
    uses: {retail: "Retail / showroom", office: "Office / meeting", not_a_room: "Not a room (false detection)"},
    candidates: [
      {key: "room-use:level-2:office", label: "Office", level: "Level 2", area_m2: 9, area_origin: "pdf_evidence", source_pages: [5], status: "calculated", include: true, exclude_reason: ""},
      {key: "room-use:level-2:retail-space", label: "Retail space", level: "Level 2", area_m2: 27.9, area_origin: "pdf_evidence", source_pages: [25], status: "calculated", include: true, exclude_reason: ""},
      {key: "room-use:level-2:service-counter", label: "Service Counter", level: "Level 2", area_m2: null, area_origin: "", source_pages: [5], status: "needs_use", include: true, exclude_reason: ""},
    ]};
}

test("AI-determined P0 areas are labelled in room confirmation and the draft report", async ({page}) => {
  await mockApi(page);
  const state = roomScopeState();
  state.candidates = [{key:"room-use:ground:shop", label:"Shop", level:"Ground", area_m2:216.1,
    area_origin:"ai_determined", area_quality_label:"AI-determined (below accuracy bar)", source_pages:[20],
    status:"calculated", include:true}];
  await renderRoomScope(page, state);
  await expect(page.locator("#roomScopeConfirmation")).toContainText("AI-determined 216.1 m² · below accuracy bar");
  await page.evaluate(() => drawAiPreliminary({settings:{}, run:{}, room_scope:{}, hourly_ai_preliminary_load_report:{
    label:"AI preliminary estimate", included_scope_peak:{design_total_kw:33.8},
    confirmed_rooms:[{label:"Shop",area_m2:216.1,area_origin:"ai_determined",area_quality_label:"AI-determined (below accuracy bar)",source_pages:[20]}]}}));
  await expect(page.locator("[data-confirmed-rooms]")).toContainText("Shop — AI-determined 216.1 m² · below accuracy bar · p. 20");
});

test("operator panel exposes bounded P0, P3 and P4 tasks", async ({page}) => {
  await mockApi(page);
  const tasks = [
    {task:"P0_dimensions",target:"page-20-dimension-1",status:"waiting_for_reply",prompt:"Read one dimension",packet:{page:20},images:[{name:"dimension.png",url:"/dimension.png"}]},
    {task:"P0_wall_styles",target:"page-20",status:"waiting_for_reply",prompt:"Identify wall styles",packet:{page:20},images:[{name:"styles.png",url:"/styles.png"}]},
    {task:"P0_room_names",target:"page-20",status:"waiting_for_reply",prompt:"Name enclosed areas",packet:{page:20},images:[{name:"areas.png",url:"/areas.png"}]},
    {task:"P0_room_outlines",target:"page-20",status:"waiting_for_reply",prompt:"Outline open rooms",packet:{page:20},images:[{name:"plan.png",url:"/plan.png"}]},
    {task:"P3_boundaries",target:"shop-page-20",status:"waiting_for_reply",prompt:"Classify boundaries",packet:{room:"Shop"},images:[{name:"boundary.png",url:"/boundary.png"}]},
    {task:"P4_openings",target:"shop-edge-1-page-26",status:"waiting_for_reply",prompt:"Read storefront glazing",packet:{room:"Shop"},images:[{name:"elevation.png",url:"/elevation.png"}]},
  ];
  const skipped_pages = [
    {page:21,reason:"Skipped second view of ground level; page 20 was selected as the main geometry plan."},
    {page:22,reason:"Skipped joinery/shop-detail drawing and large detail scale 1:10; room outlines use the main geometry plan."},
  ];
  await page.route("**/api/autonomous-tasks**", route => route.request().method() === "GET"
    ? route.fulfill({json:{id:"demo-project",auto_apply_bar:.85,supported_tasks:tasks.map(row=>row.task),tasks,skipped_pages}})
    : route.fulfill({json:{id:"demo-project",tasks}}));
  await page.goto("/?operator=1");
  await page.evaluate(() => {DATA={id:"demo-project"};show("vRes");});
  const panel=page.locator("#autonomousTasksPanel");
  await expect(panel).toBeVisible();
  for (const task of ["P0_dimensions","P0_wall_styles","P0_room_names","P0_room_outlines","P3_boundaries","P4_openings"])
    await expect(panel.locator(`[data-task="${task}"]`)).toBeVisible();
  await expect(panel.locator('[data-page-selection-note][data-page="21"]')).toContainText("second view");
  await expect(panel.locator('[data-page-selection-note][data-page="22"]')).toContainText("1:10");
});

test("project load renders room confirmation from compact preliminary data and keeps actions available", async ({ page }) => {
  await mockApi(page);
  const roomScope = roomScopeState();
  roomScope.candidates = [
    {key: "room-use:level-2:bar", label: "Bar", level: "Level 2", area_m2: 25, area_origin: "reviewer_trace", source_pages: [20], status: "calculated", include: true, exclude_reason: ""},
    {key: "room-use:level-2:kitchen", label: "Kitchen", level: "Level 2", area_m2: null, area_origin: "", source_pages: [21], status: "no_area", include: false, exclude_reason: ""},
  ];
  const compact = {id: "demo-project", settings: {}, run: {}, status: "freshness_pending", freshness_pending: true, stale_reasons: [],
    value_resolution: {record_count: 0, research_job_count: 0, records: [], coverage_summary: {}},
    room_scope: roomScope, artifact_links: {},
    hourly_ai_preliminary_load_report: {label: "AI preliminary estimate", included_scope_peak: {design_total_kw: 4.2},
      room_names: [{room_id: "room-bar", name: "Bar"}], known_exclusions: [], unresolved_room_inputs: [],
      review_queue: [], confirmed_rooms: [], preliminary_surface_summary: {included: 0, blocked: 0, excluded: 0}}};
  const loadedUrls = [];
  const actions = [];
  let releaseFreshness;
  const freshnessGate = new Promise(resolve => { releaseFreshness = resolve; });
  await page.route("**/api/ai-preliminary-model?project_id=demo-project**", route => {
    const url = route.request().url(); loadedUrls.push(url);
    if (url.includes("check_freshness=1")) return freshnessGate.then(() => route.fulfill({json: {
      ...compact, status: "current", freshness_pending: false,
    }}));
    return route.fulfill({json: compact});
  });
  await page.route("**/api/ai-preliminary-model", route => {
    const body = route.request().postDataJSON();
    actions.push(body);
    const confirmed = {...roomScope, status: "confirmed", confirmation: {reviewer: "QA", confirmed_at: "2026-10-04"}};
    return route.fulfill({json: {...compact, room_scope: confirmed,
      hourly_ai_preliminary_load_report: {...compact.hourly_ai_preliminary_load_report,
        confirmed_rooms: [{label: "Bar", area_m2: 25, area_origin: "reviewer_trace", source_pages: [20]}],
        room_scope_confirmation: {reviewer: "QA"}}}});
  });
  const kitchenId = "room-use:level-2:kitchen";
  await page.route("**/api/reviewer-room-geometry?project_id=demo-project", route => route.fulfill({json: mechanicalTraceContext([
    {room_id: kitchenId, label: "Kitchen", level_name: "Level 2", needs_trace: true},
  ])}));
  await page.route("**/fake-plan.svg", route => route.fulfill({contentType: "image/svg+xml", body: '<svg xmlns="http://www.w3.org/2000/svg" width="1000" height="800"><rect width="1000" height="800" fill="#eee"/></svg>'}));

  await page.goto("/");
  await page.evaluate(() => { DATA = {id: "demo-project"}; show("vRes");
    requiredElement("workflowSkeleton").classList.remove("hide");
    requiredElement("visionPanel").classList.remove("hide");
    requiredElement("designRequirementsPanel").classList.remove("hide");
    showCalculationInputEvidence({fingerprint: "initial", geometry_resolution: {entities: [], review_items: [], summary: {}, deterministic_proof_diagnostics: {pages: [], rooms: []}}, candidates: []}, {}, "current"); });
  await page.evaluate(() => loadAiPreliminary());
  expect(loadedUrls[0]).toContain("view=workspace");
  const block = page.locator("#roomScopeConfirmation");
  await expect(block).toContainText("Bar");
  await expect(block.locator('[data-room-scope-key="room-use:level-2:kitchen"] [data-room-scope-trace]')).toBeVisible();
  await expect(block.locator("p[data-room-scope-status]")).toContainText("Checking whether the draft is up to date…");
  await expect(block.locator("[data-room-scope-confirm]")).toBeDisabled();
  await expect(block.locator("[data-room-scope-calculate]")).toBeDisabled();
  await expect.poll(() => loadedUrls.some(url => url.includes("check_freshness=1"))).toBe(true);
  releaseFreshness();
  await expect(page.locator("#aiPreliminaryStatus")).toContainText("current");
  await expect(block.locator("[data-room-scope-confirm]")).toBeEnabled();
  await expect(block.locator("p[data-room-scope-status]")).not.toContainText("Checking whether");
  await expect(block.locator("[data-room-scope-calculate]")).toBeDisabled();
  await block.locator("[data-room-scope-reviewer]").fill("QA");
  await block.locator("[data-room-scope-confirm]").click();
  await expect.poll(() => actions.at(-1)?.action).toBe("confirm_room_scope");
  expect(actions.at(-1).response_view).toBe("workspace");
  await block.locator("[data-room-scope-calculate]").click();
  await expect.poll(() => actions.at(-1)?.action).toBe("calculate");
  expect(actions.at(-1).response_view).toBe("workspace");
  await block.locator('[data-room-scope-key="room-use:level-2:kitchen"] [data-room-scope-trace]').click();
  await expect(page.locator("#reviewerRoomGeometryWorkspace [data-geometry-room]")).toHaveValue(kitchenId);
});

test("freshness check failure re-enables confirmed room-scope actions and explains the error", async ({page}) => {
  await mockApi(page);
  const confirmed = roomScopeState("confirmed");
  const compact = {id:"demo-project",settings:{},run:{},status:"freshness_pending",freshness_pending:true,
    room_scope:confirmed,artifact_links:{},hourly_ai_preliminary_load_report:{label:"Draft",included_scope_peak:{design_total_kw:1},known_exclusions:[],unresolved_room_inputs:[],review_queue:[],confirmed_rooms:[]}};
  let releaseFreshness;
  const freshnessGate=new Promise(resolve=>{releaseFreshness=resolve;});
  await page.route("**/api/ai-preliminary-model?project_id=demo-project**", route => {
    const url=route.request().url();
    return url.includes("check_freshness=1")
      ? freshnessGate.then(()=>route.fulfill({status:503,json:{error:"temporary database error"}}))
      : route.fulfill({json:compact});
  });
  await page.goto("/");
  await page.evaluate(() => { DATA={id:"demo-project"}; show("vRes"); loadAiPreliminary(); });
  const block=page.locator("#roomScopeConfirmation");
  await expect(block.locator("[data-room-scope-confirm]")).toBeDisabled();
  await expect(block.locator("[data-room-scope-calculate]")).toBeDisabled();
  releaseFreshness();
  await expect(block.locator("p[data-room-scope-status]")).toContainText("Could not check draft freshness: temporary database error");
  await expect(block.locator("[data-room-scope-confirm]")).toBeEnabled();
  await expect(block.locator("[data-room-scope-calculate]")).toBeEnabled();
});

test("guided area blocker names untraced rooms and opens the first trace target", async ({page}) => {
  await mockApi(page);
  const roomId="room-use:level-2:bar";
  const kitchenId="room-use:level-2:kitchen";
  const shopId="room-use:level-2:shop";
  let roomScopeWasEmptyAtResolve = false;
  await page.route("**/api/model-input-resolution", async route => {
    roomScopeWasEmptyAtResolve = await page.locator("#roomScopeConfirmation").evaluate(node => !node.innerHTML.trim());
    return route.fulfill({status:422,json:{
    code:"room_area_unresolved", error:"No room has a validated area.", message:"No room has a validated area.",
    remediation:"For Bar, Kitchen, Shop: trace and calibrate each room, accept its area in the calculator draft, then rebuild model inputs.",
    affected_component_ids:[roomId,kitchenId,shopId],
  }});
  });
  await page.route("**/api/skill-workflow**", route => route.fulfill({json:{status:"needs_review",stages:[],subskills:[]}}));
  const traceContext=mechanicalTraceContext([{room_id:roomId,label:"Bar",level_name:"Level 2",needs_trace:true}]);
  await page.route("**/api/calculation-input-evidence?project_id=demo-project", route => route.fulfill({json:{status:"current",
    calculation_input_evidence:{fingerprint:"e",candidates:[],geometry_resolution:{entities:[],summary:{},deterministic_proof_diagnostics:{pages:[],rooms:[]}},opening_register:{openings:[]}},
    summary:{candidate_count:0,status_counts:{},category_counts:{}},component_interpretations:{}}}));
  await page.route("**/api/reviewer-room-geometry?project_id=demo-project", route => route.fulfill({json:traceContext}));
  await page.route("**/fake-plan.svg", route => route.fulfill({contentType:"image/svg+xml",body:'<svg xmlns="http://www.w3.org/2000/svg" width="1000" height="800"><rect width="1000" height="800" fill="#eee"/></svg>'}));
  await page.goto("/");
  await page.evaluate(() => {DATA={id:"demo-project"};show("vRes");requiredElement("visionPanel").classList.remove("hide");requiredElement("designRequirementsPanel").classList.remove("hide");requiredElement("workflowSkeleton").classList.remove("hide");});
  await page.locator("#btnGuidedResolveModelInputs").click();
  const status=page.locator("#guidedModelInputsStatus");
  await expect(status).toContainText("For Bar, Kitchen, Shop: trace and calibrate each room");
  expect(roomScopeWasEmptyAtResolve).toBe(true);
  await expect(page.locator("#roomScopeConfirmation")).toBeEmpty();
  await expect(status.locator("[data-guided-trace-rooms]")).toHaveText("Trace rooms");
  await status.locator("[data-guided-trace-rooms]").click();
  await expect(page.locator("#reviewerRoomGeometryWorkspace [data-geometry-room]")).toHaveValue(roomId);
});

async function renderRoomScope(page, state){
  await page.goto("/");
  await page.evaluate(input => {
    DATA = {id: "demo-project"}; show("vRes"); requiredElement("workflowSkeleton").classList.remove("hide");
    requiredElement("visionPanel").classList.remove("hide");
    drawValueResolution = () => {};
    drawAiPreliminary({settings: {}, run: {}, hourly_ai_preliminary_load_report: {}, room_scope: input});
  }, state);
}

test("room confirmation lists rooms with area, source and page and blocks calculation until confirmed", async ({ page }) => {
  await mockApi(page);
  let confirmPayload;
  await page.route("**/api/ai-preliminary-model", async route => {
    const payload = route.request().postDataJSON();
    if (payload.action === "confirm_room_scope") {
      confirmPayload = payload;
      return route.fulfill({json: {settings: {}, run: {}, hourly_ai_preliminary_load_report: {}, room_scope: roomScopeState("confirmed")}});
    }
    return route.fulfill({json: {settings: {}, run: {}, room_scope: roomScopeState("confirmed"), hourly_ai_preliminary_load_report: {
      label: "AI preliminary estimate", included_scope_peak: {design_total_kw: 3.1},
      confirmed_rooms: [{label: "Office", level: "Level 2", area_m2: 9, area_origin: "pdf_evidence", source_pages: [5]}],
      room_scope_confirmation: {reviewer: "QA"},
    }}});
  });
  await renderRoomScope(page, roomScopeState());
  const block = page.locator("#roomScopeConfirmation");
  await expect(block).toBeVisible();
  await expect(block).toContainText("Not confirmed yet");
  await expect(block.locator('[data-room-scope-key="room-use:level-2:office"]')).toContainText("9 m² · printed on drawing · p. 5");
  await expect(block.locator('[data-room-scope-key="room-use:level-2:service-counter"]')).toContainText("No room use yet");
  await expect(block.locator("[data-room-scope-calculate]")).toBeDisabled();
  await block.locator('[data-room-scope-key="room-use:level-2:retail-space"] [data-room-scope-include]').uncheck();
  await block.locator('[data-room-scope-key="room-use:level-2:retail-space"] [data-room-scope-reason]').fill("Render text, not a room");
  await block.locator('[data-room-scope-key="room-use:level-2:service-counter"] [data-room-scope-include]').uncheck();
  await block.locator("[data-room-scope-reviewer]").fill("QA");
  await block.locator("[data-room-scope-confirm]").click();
  await expect.poll(() => confirmPayload?.reviewer).toBe("QA");
  expect(confirmPayload.candidate_fingerprint).toBe("fp-1");
  expect(confirmPayload.rows).toEqual([
    {key: "room-use:level-2:office", include: true, reason: ""},
    {key: "room-use:level-2:retail-space", include: false, reason: "Render text, not a room"},
    {key: "room-use:level-2:service-counter", include: false, reason: ""},
  ]);
  await expect(block).toContainText("Confirmed by QA");
  await expect(block.locator("[data-room-scope-calculate]")).toBeEnabled();
  await block.locator("[data-room-scope-calculate]").click();
  await expect(page.locator("[data-confirmed-rooms]")).toContainText("Office — 9 m² · printed on drawing · p. 5");
  await expect(block).toContainText("Draft load calculated: 3.1 kW");
});

test("room confirmation needs a reviewer and saves a chosen use before reassembling", async ({ page }) => {
  await mockApi(page);
  const calls = [];
  await page.route("**/api/room-use-resolution", route => { calls.push(["room-use", route.request().postDataJSON()]); return route.fulfill({json: {records: []}}); });
  await page.route("**/api/ai-preliminary-model", route => { calls.push(["preliminary", route.request().postDataJSON()]);
    return route.fulfill({json: {settings: {}, run: {}, hourly_ai_preliminary_load_report: {}, room_scope: roomScopeState()}}); });
  await renderRoomScope(page, roomScopeState());
  const block = page.locator("#roomScopeConfirmation");
  await block.locator("[data-room-scope-confirm]").click();
  await expect(block).toContainText("Enter a reviewer name before confirming the room list.");
  expect(calls).toEqual([]);
  await block.locator("[data-room-scope-reviewer]").fill("QA");
  const counter = block.locator('[data-room-scope-key="room-use:level-2:service-counter"]');
  await counter.locator("[data-room-scope-use]").selectOption("retail");
  await counter.locator("[data-room-scope-save-use]").click();
  await expect.poll(() => calls.map(([kind, body]) => `${kind}:${body.action}`)).toEqual(["room-use:apply_override", "preliminary:assemble"]);
  expect(calls[0][1]).toEqual(expect.objectContaining({room_id: "room-use:level-2:service-counter", taxonomy_id: "retail", reviewer: "QA"}));
  await expect(block).toContainText("Use saved for Service Counter");
});

function mechanicalTraceContext(rooms = []){
  return {id: "demo-project", source_pdf_fingerprint: "pdf-fixture", rooms,
    pages: [{page: 5, title: "Ductwork plan", drawing_number: "M-210", declared_scale: "1:50", scale_denominator: 50, level_name: "Level 2",
      image_width_px: 1000, image_height_px: 800, image_px_per_pt: 3.5277777778, preview_url: "/fake-plan.svg", preview_width_px: 1000,
      preview_height_px: 800, preview_matches_vector_coordinates: true, vector_page_fingerprint: "vector-fixture",
      fallback_plan: true, fallback_reason: "No architectural floor plan in this set; tracing on a services plan."}],
    room_uses: {retail: "Retail / showroom", office: "Office / meeting"}, levels: ["Level 2", "Unassigned level"],
    reviewer_room_geometry: {schema_version: 1, records: []}};
}

async function openTraceWorkspace(page){
  await page.route("**/api/calculation-input-evidence?project_id=demo-project", route => route.fulfill({json: {status: "current", calculation_input_evidence: {fingerprint: "e", candidates: [], geometry_resolution: {entities: [], summary: {}, deterministic_proof_diagnostics: {pages: [], rooms: []}}, opening_register: {openings: []}}, summary: {candidate_count: 0, status_counts: {}, category_counts: {}}, component_interpretations: {}}}));
  await page.route("**/api/plan-snap?project_id=demo-project&page=5", route => route.fulfill({json: {lines: [], endpoints: [], intersections: [], snap_tolerance_px: 8, source_pdf_fingerprint: "pdf-fixture", vector_page_fingerprint: "vector-fixture"}}));
  await page.route("**/fake-plan.svg", route => route.fulfill({contentType: "image/svg+xml", body: '<svg xmlns="http://www.w3.org/2000/svg" width="1000" height="800"><rect width="1000" height="800" fill="#eee"/></svg>'}));
  await page.goto("/");
  await page.evaluate(() => { DATA = {id: "demo-project"}; show("vRes"); requiredElement("designRequirementsPanel").classList.remove("hide"); showCalculationInputEvidence({fingerprint: "initial", geometry_resolution: {entities: [], review_items: [], summary: {}, deterministic_proof_diagnostics: {pages: [], rooms: []}}, candidates: []}, {}, "current"); });
}

test("mechanical-only set offers services plans and lets a reviewer add a room with a required use", async ({ page }) => {
  let context = mechanicalTraceContext(); const posts = [];
  await page.route("**/api/reviewer-room-geometry?project_id=demo-project", route => route.fulfill({json: context}));
  await page.route("**/api/reviewer-room-geometry", route => {
    const body = route.request().postDataJSON(); posts.push(body);
    context = mechanicalTraceContext([{room_id: "room-use:level-2:kiosk", label: "Kiosk", level_name: "Level 2", needs_trace: true, reviewer_added: true, source: "room_inference_proposal"}]);
    return route.fulfill({json: context});
  });
  await openTraceWorkspace(page);
  const workspace = page.locator("#reviewerRoomGeometryWorkspace");
  await expect(workspace).toContainText("Plan pages are available, but no rooms were detected");
  const form = workspace.locator(".reviewer-add-room");
  await expect(form).toHaveAttribute("open", "");
  await form.locator("[data-add-room-label]").fill("Kiosk");
  await form.locator("[data-add-room-reviewer]").fill("QA");
  await form.locator("[data-add-room]").click();
  await expect(form.locator("[data-add-room-result]")).toHaveText("Choose a room use for the new room.");
  expect(posts).toEqual([]);
  await expect(form.locator("[data-add-room-level]")).toHaveValue("Level 2");
  await form.locator("[data-add-room-use]").selectOption("retail");
  await form.locator("[data-add-room]").click();
  await expect.poll(() => posts.length).toBe(1);
  expect(posts[0]).toEqual(expect.objectContaining({action: "add_room", label: "Kiosk", level_name: "Level 2", taxonomy_id: "retail", reviewer: "QA"}));
  await expect(workspace.locator("[data-geometry-room]")).toHaveValue("room-use:level-2:kiosk");
  await expect(workspace.locator("[data-geometry-room] option:checked")).toContainText("added by reviewer");
  await expect(workspace.locator("[data-remove-room]")).toBeVisible();
  await workspace.locator("[data-geometry-page]").selectOption("5");
  await expect(workspace.locator("[data-geometry-page] option:checked")).toContainText("services plan (no architectural plan in set)");
  await expect(workspace.locator(".reviewer-geometry-warning").first()).toContainText("tracing on a services plan");
});

test("trace picker shows calibrated area and explains stale or uncalibrated traces", async ({page}) => {
  const context=mechanicalTraceContext([
    {room_id:"room-current",label:"Bar",level_name:"Level 2",needs_trace:false,traced_area_m2:30.78},
    {room_id:"room-stale",label:"Kitchen",level_name:"Level 2",needs_trace:true,trace_issue:"Trace is stale: Source PDF changed."},
    {room_id:"room-uncalibrated",label:"Shop",level_name:"Level 2",needs_trace:true,trace_issue:"Trace area unavailable: declared scale missing."},
  ]);
  await page.route("**/api/reviewer-room-geometry?project_id=demo-project",route=>route.fulfill({json:context}));
  await openTraceWorkspace(page);
  await page.evaluate(()=>loadReviewerRoomGeometryWorkspace());
  const picker=page.locator("#reviewerRoomGeometryWorkspace [data-geometry-room]");
  await expect(picker.locator("option[value='room-current']")).toContainText("Bar");
  await expect(picker.locator("option[value='room-current']")).toContainText("traced 30.78 m²");
  await expect(picker.locator("option[value='room-current']")).not.toContainText("area unresolved");
  await expect(picker.locator("option[value='room-stale']")).toContainText("area unresolved — Trace is stale: Source PDF changed.");
  await expect(picker.locator("option[value='room-uncalibrated']")).toContainText("area unresolved — Trace area unavailable: declared scale missing.");
});

test("services plan without printed dimensions tells the reviewer to upload the architectural drawings", async ({ page }) => {
  const context = mechanicalTraceContext([{room_id: "room-use:level-2:kiosk", label: "Kiosk", level_name: "Level 2", needs_trace: true, reviewer_added: true}]);
  context.pages[0] = {...context.pages[0], printed_dimensions_found: false,
    calibration_warning: "No printed building dimensions were found on this services plan, so a room trace here cannot be calibrated and saved. Upload the architectural drawings for this tenancy to trace its rooms."};
  await page.route("**/api/reviewer-room-geometry?project_id=demo-project", route => route.fulfill({json: context}));
  await openTraceWorkspace(page);
  const workspace = page.locator("#reviewerRoomGeometryWorkspace");
  await workspace.locator("[data-geometry-room]").selectOption("room-use:level-2:kiosk");
  await workspace.locator("[data-geometry-page]").selectOption("5");
  await expect(workspace.locator(".reviewer-geometry-blocking")).toContainText("Upload the architectural drawings for this tenancy");
});

test("room confirmation lists untraced rooms as not in the total and cannot include them", async ({ page }) => {
  await mockApi(page);
  const state = roomScopeState();
  state.candidates.push({key: "room-use:level-2:kitchen", label: "Kitchen", level: "Level 2", area_m2: null, area_origin: "",
    source_pages: [21], status: "no_area", include: false, exclude_reason: ""});
  let confirmPayload;
  await page.route("**/api/ai-preliminary-model", route => { confirmPayload = route.request().postDataJSON();
    return route.fulfill({json: {settings: {}, run: {}, hourly_ai_preliminary_load_report: {}, room_scope: state}}); });
  await renderRoomScope(page, state);
  const block = page.locator("#roomScopeConfirmation");
  await expect(block.locator("[data-room-scope-untraced]")).toContainText("1 room has no area yet and is not in the total: Kitchen");
  const kitchen = block.locator('[data-room-scope-key="room-use:level-2:kitchen"]');
  await expect(kitchen).toContainText("Not traced — not included in the total.");
  await expect(kitchen.locator("[data-room-scope-include]")).toBeDisabled();
  await expect(kitchen.locator("[data-room-scope-trace]")).toBeVisible();
  await block.locator('[data-room-scope-key="room-use:level-2:service-counter"] [data-room-scope-include]').uncheck();
  await block.locator("[data-room-scope-reviewer]").fill("QA");
  await block.locator("[data-room-scope-confirm]").click();
  await expect.poll(() => confirmPayload?.action).toBe("confirm_room_scope");
  expect(confirmPayload.rows).toContainEqual({key: "room-use:level-2:kitchen", include: false, reason: ""});
});

async function showSiteLocationPanel(page, data){
  await page.goto("/");
  await page.evaluate(input => {
    DATA = {id: "demo-project"}; show("vRes");
    let node = requiredElement("siteLocationSection");
    while (node) { node.classList?.remove("hide"); if (node.tagName === "DETAILS") node.open = true; node = node.parentElement; }
    requiredElement("siteCitedLocation").open = true;
    drawSiteLocation(input);
  }, data);
}

test("project location shows the tenancy site clue and excluded consultant address, and saves a cited map position", async ({ page }) => {
  await mockApi(page);
  const inferred = {status: "awaiting_address_confirmation", site_location_resolution: {status: "awaiting_address_confirmation", pdf_context: {
    address_candidates: [], excluded_address_candidates: [{address: "211-223 Pacific Hwy, North Sydney NSW 2060", source: {page: 3},
      reason: "Next to consultant contact details (phone, email or office); likely a consultant's address, not the site."}],
    site_name_candidates: [{site_name: "Melrose Central", tenancy: "MZ01,M38", confidence: 0.75, source: {page: 20, drawing_number: "26.02", excerpt: "TENANCY MZ01,M38, MELROSE CENTRAL"}}]},
    geocode: {candidates: []}, location: {}}};
  let payload;
  await page.route("**/api/site-location-resolution", route => { payload = route.request().postDataJSON();
    return route.fulfill({json: {status: "location_resolved", site_location_resolution: {status: "location_resolved", confirmed_address: payload.confirmed_address,
      pdf_context: inferred.site_location_resolution.pdf_context, geocode: {candidates: []},
      location: {latitude_deg: -33.81, longitude_deg: 151.07, state: "NSW", locality: "Melrose Park", timezone: "Australia/Sydney", basis: "reviewer_cited_map", elevation: {}},
      cited_location: {source: "Map service", citation: "https://maps.example/lemon-tree-av", reviewer: "QA"}}}}); });
  await showSiteLocationPanel(page, inferred);
  const panel = page.locator("#siteLocationSection");
  await expect(panel.locator("[data-site-name-clue]")).toContainText("Melrose Central · tenancy MZ01,M38");
  await expect(panel.locator("[data-excluded-address]")).toContainText("Pacific Hwy");
  await panel.locator("#siteLocationAddress").fill("Shop G38/22 Lemon Tree Av, Melrose Park NSW 2114");
  await panel.locator("#siteCitedLatitude").fill("-33.81");
  await panel.locator("#siteCitedLongitude").fill("151.07");
  await panel.locator("#siteCitedState").selectOption("NSW");
  await panel.locator("#siteCitedLocality").fill("Melrose Park");
  await panel.locator("#siteCitedSource").fill("Map service");
  await panel.locator("#siteCitedCitation").fill("https://maps.example/lemon-tree-av");
  await panel.locator("#siteCitedReviewer").fill("QA");
  await panel.locator("#btnSaveCitedSiteLocation").click();
  await expect.poll(() => payload?.action).toBe("set_cited_location");
  expect(payload).toMatchObject({confirmed_address: "Shop G38/22 Lemon Tree Av, Melrose Park NSW 2114", latitude_deg: -33.81, longitude_deg: 151.07,
    state: "NSW", locality: "Melrose Park", source: "Map service", citation: "https://maps.example/lemon-tree-av", reviewer: "QA"});
  await expect(panel.locator("[data-resolved-location]")).toContainText("from a reviewer-cited map position (Map service, https://maps.example/lemon-tree-av, QA)");
});

test("the draft result says which design day, sun values and site it used", async ({ page }) => {
  await mockApi(page);
  await renderRoomScope(page, {candidates: []});
  await page.evaluate(() => drawAiPreliminary({settings: {}, run: {}, room_scope: {}, hourly_ai_preliminary_load_report: {
    label: "AI preliminary estimate — not engineering reviewed or validated", included_scope_peak: {design_total_kw: 37.4},
    design_conditions_basis: {
      design_day: {site_specific: false, label: "Generic Australian cooling design day (assumption pack au-preliminary-v3) — not site-specific"},
      sun: {site_specific: false, label: "Generic preliminary sun values by façade direction (assumption pack au-preliminary-v3, N/E/S/W) — not site-specific; no flat-roof sun"},
      site: {confirmed: true, label: "Site: Shop G38/22 Lemon Tree Av, Melrose Park NSW 2114 (reviewer-cited map position)"}}}}));
  const basis = page.locator("#aiPreliminaryResults [data-design-conditions-basis]");
  await expect(basis).toContainText("Generic Australian cooling design day");
  await expect(basis).toContainText("not site-specific; no flat-roof sun");
  await expect(basis).toContainText("Site: Shop G38/22 Lemon Tree Av");
});

test("AI task operator panel is hidden from contractors and validates manual replies with visible errors", async ({page}) => {
  await page.context().grantPermissions(["clipboard-read", "clipboard-write"]);
  await mockApi(page);
  let gets = 0, summaryGets = 0, posts = [];
  let taskState = {id:"demo-project", auto_apply_bar:.85, supported_tasks:["P1_site","P2_north","P5_roof"], tasks:[{
    task:"P1_site", target:"project", status:"waiting_for_reply", prompt:"Return JSON only", accuracy:{accuracy:.667,scored:3},
    packet:{excerpts:[{page:2,text:"TENANCY G12"}]}, images:[], block_reason:"", validation:{}, applied_value:{}, source:"",
  }]};
  await page.route("**/api/autonomous-tasks**", async route => {
    if (route.request().method() === "GET") {
      if (route.request().url().includes("view=labels")) {
        summaryGets++;
        return route.fulfill({json:{id:"demo-project",auto_apply_bar:.85,tasks:[{task:"P1_site",target:"project",status:"below_accuracy_bar",source:"ai_determined",applied_value:{site_text:"TENANCY G12",applied_site_text:"TENANCY G12",applied_source:"ai_determined"}}]}});
      }
      gets++; return route.fulfill({json:taskState});
    }
    const body = route.request().postDataJSON(); posts.push(body);
    if (body.reply.includes("not printed")) {
      taskState = {...taskState, tasks:[{...taskState.tasks[0], block_reason:"Site text must be an exact substring of a supplied excerpt on the cited page.", validation:{valid:false,error:"Site text must be an exact substring of a supplied excerpt on the cited page."}}]};
    } else {
      taskState = {...taskState, tasks:[{...taskState.tasks[0], status:"below_accuracy_bar", source:"ai_determined", stand_in:body.stand_in,
        block_reason:"", applied_value:{site_text:"TENANCY G12",applied_site_text:"TENANCY G12",applied_source:"ai_determined"}, validation:{valid:true}}]};
    }
    return route.fulfill({json:taskState});
  });
  await page.goto("/");
  await expect(page.locator("#autonomousTasksPanel")).toBeHidden();
  await page.evaluate(() => { DATA = {id:"demo-project"}; show("vRes"); });
  await expect.poll(() => summaryGets).toBeGreaterThan(0);
  await expect(page.locator("#siteLocationResults")).toContainText("AI-determined (below accuracy bar)");
  await page.goto("/?operator=1");
  await page.evaluate(() => { DATA = {id:"demo-project"}; show("vRes"); });
  const panel = page.locator("#autonomousTasksPanel");
  await expect(panel).toBeVisible();
  await expect.poll(() => gets).toBeGreaterThan(0);
  await panel.getByRole("button", {name:"Copy prompt"}).click();
  await expect(panel).toContainText("Prompt copied");
  const card = panel.locator("[data-task-card]");
  await card.locator("[data-task-reply]").fill('{"site":{"text":"not printed","page":2,"kind":"street_address"},"consultant_addresses":[]}');
  await card.locator("[data-validate-apply]").click();
  await expect(card.locator("[role=alert]")).toContainText("exact substring");
  await expect(panel.locator("#autonomousTasksStatus")).toContainText("Validation failed");
  await card.locator("[data-task-reply]").fill('{"site":{"text":"TENANCY G12","page":2,"kind":"tenancy_in_centre"},"consultant_addresses":[]}');
  await card.locator("[data-stand-in]").check();
  await card.locator("[data-validate-apply]").click();
  await expect(card.locator("[data-task-source]")).toHaveText("Stand-in (test)");
  await expect(panel.locator("#autonomousTasksStatus")).toContainText("Stand-in (test)");
  expect(posts).toHaveLength(2);
  expect(posts[1].stand_in).toBe(true);
});

test("contractor sees the roof exposure question on the normal project screen and can answer it", async ({page}) => {
  await mockApi(page);
  const posts=[];
  await page.route("**/api/autonomous-tasks**", async route => {
    if (route.request().method() === "GET") {
      return route.fulfill({json:{id:"demo-project",auto_apply_bar:.85,tasks:[{
        task:"P5_roof",target:"room-use:ground:shop",status:"needs_contractor_answer",room_label:"Shop",
        question:"Is there a floor or another tenancy directly above this shop, or is it the roof?"},
        {task:"P6_kitchen",target:"kitchen",status:"below_accuracy_bar",source:"ai_determined",applied_value:{
          label:"Kitchen equipment identified from drawings (heat not yet assessed)",heat_assessed:false,
          items:[{type:"rangehood_canopy",count:1,page:22}]}}]}});
    }
    const body=route.request().postDataJSON();posts.push(body);
    return route.fulfill({json:{id:"demo-project",auto_apply_bar:.85,tasks:[{
      task:"P5_roof",target:body.target,status:"contractor_answered_not_sure",room_label:"Shop",
      message:"Not sure — roof exposure remains unknown and not assessed."},
      {task:"P6_kitchen",target:"kitchen",status:"below_accuracy_bar",source:"ai_determined",applied_value:{
        label:"Kitchen equipment identified from drawings (heat not yet assessed)",heat_assessed:false,
        items:[{type:"rangehood_canopy",count:1,page:22}]}}]}});
  });
  await page.goto("/");
  await page.evaluate(()=>{DATA={id:"demo-project"};show("vRes");});
  const question=page.locator("#contractorRoofQuestions [data-contractor-roof-question]");
  await expect(question).toBeVisible();
  await expect(question).toContainText("Is there a floor or another tenancy directly above this shop, or is it the roof?");
  await expect(page.locator("#autonomousTasksPanel")).toBeHidden();
  await question.getByLabel("Not sure").check();
  await question.getByRole("button",{name:"Save answer"}).click();
  await expect.poll(()=>posts.length).toBe(1);
  expect(posts[0]).toEqual(expect.objectContaining({action:"answer_roof",task:"P5_roof",target:"room-use:ground:shop",answer:"not_sure"}));
  await expect(page.locator("#contractorRoofQuestionStatus")).toHaveText("Saved. Roof exposure remains unknown and is not assessed.");
  await expect(page.locator("#contractorRoofQuestions [data-roof-not-assessed]")).toContainText("Not sure — roof exposure remains unknown and not assessed.");
  const equipment = page.locator("#kitchenEquipmentResults [data-kitchen-equipment-result]");
  await expect(equipment).toContainText("Kitchen equipment identified from drawings (heat not yet assessed)");
  await expect(equipment).toContainText("rangehood canopy × 1 · p. 22");
  await expect(equipment).toContainText("no heat contribution has been calculated");
});

test("saving AI envelope values offers explicit reviewer confirmation controls", async ({page}) => {
  await mockApi(page);
  const {context,trace}=envelopeTraceContext({edges:[{index:0,boundary:"external"},{index:1,boundary:"unknown"},{index:2,boundary:"unknown"},{index:3,boundary:"unknown"}],roof:"exposed"});
  trace.edge_sources={"0":"ai_determined"};trace.roof_source="ai_determined";
  const posts=[];
  await page.route("**/api/reviewer-room-geometry?project_id=demo-project",route=>route.fulfill({json:context}));
  await page.route("**/api/reviewer-room-geometry",async route=>{
    const body=route.request().postDataJSON();posts.push(body);
    const saved={...trace,envelope_reviewer:body.reviewer,envelope_declared_at:"2026-10-05T00:00:00Z",
      edge_sources:{"0":body.confirmed_edges.includes(0)?"reviewer":"ai_determined"},
      roof_source:body.confirm_roof?"reviewer":"ai_determined"};
    context.reviewer_room_geometry.records=[saved];
    return route.fulfill({json:context});
  });
  await openTraceWorkspace(page);
  const workspace=page.locator("#reviewerRoomGeometryWorkspace");
  await workspace.locator("[data-geometry-room]").selectOption(trace.room_id);
  await workspace.locator("[data-geometry-page]").selectOption("5");
  await expect(workspace.locator("[data-envelope-confirm-edge='0']")).toBeVisible();
  await expect(workspace.locator("[data-envelope-confirm-roof]")).toBeVisible();
  await workspace.locator("[data-envelope-reviewer]").fill("QA");
  await workspace.locator("[data-envelope-save]").click();
  await expect.poll(()=>posts.length).toBe(1);
  expect(posts[0].confirmed_edges).toEqual([]);
  expect(posts[0].confirm_roof).toBe(false);
  await workspace.locator("[data-envelope-confirm-edge='0']").check();
  await workspace.locator("[data-envelope-confirm-roof]").check();
  await workspace.locator("[data-envelope-save]").click();
  await expect.poll(()=>posts.length).toBe(2);
  expect(posts[1].confirmed_edges).toEqual([0]);
  expect(posts[1].confirm_roof).toBe(true);
});

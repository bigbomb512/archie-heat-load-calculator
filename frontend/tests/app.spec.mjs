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
  await page.route("**/api/ai-preliminary-model?project_id=demo-project", route => route.fulfill({ json: {} }));
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
          {room_id: "kitchen-id", component_type: "envelope"},
        ],
      },
    });
  });
  const result = page.locator("#aiPreliminaryResults");
  await expect(result).toContainText("Included-scope peak: 30.75 kW");
  await expect(result).toContainText("Not included in this total");
  await expect(result).toContainText("Make-up air — Bar, Kitchen, Shop");
  await expect(result).toContainText("Envelope — Kitchen");
  await expect(result).not.toContainText("bar-id");
});

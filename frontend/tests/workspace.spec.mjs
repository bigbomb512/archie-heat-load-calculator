import { expect, test } from "@playwright/test";
import { copyFileSync, existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

// Job workspace (docs/UI_REBUILD_PLAN.md phase 1): left rail of tabs, one focused page, Calculate always visible.
const analysis = {
  id: "job-1", name: "Corner cafe.pdf", pages_analysed: 38, relevant_count: 3, selected_count: 3, warnings: [],
  has_reasoning_packet: true,
  sheets: [{page: 20, type: "floor_plan", title: "Dimension Plan", relevant: true, selected_by_default: true, plan_role: "main_floor_plan",
            confidence: 0.9, reason: "", review_bucket: "primary", scale: "1:100", visual: {}, thumbnail: ""},
           {page: 27, type: "elevation", title: "Internal Elevation", relevant: false, selected_by_default: true, plan_role: "reference_context",
            confidence: 0.7, reason: "", review_bucket: "reference", scale: "1:50", visual: {}, thumbnail: ""},
           {page: 31, type: "section", title: "including amendments of the relevant Building Code", relevant: false, selected_by_default: false,
            plan_role: "", confidence: 0.3, reason: "", review_bucket: "other", scale: "", visual: {}, thumbnail: ""}],
};

const report = {
  label: "AI preliminary estimate", confirmed_rooms: [{label: "Kitchen", area_m2: 104.9, area_origin: "edited"}, {label: "Shop", area_m2: 216.1, area_origin: "ai_determined"}],
  included_scope_peak: {final_design_total_kw: 34.1354, display_hour: 14, safety_factor: 1.1, components: {
    people: {total_kw: 8.4}, lighting: {total_kw: 4.1}, equipment_refrigeration: {total_kw: 3.2}, envelope: {total_kw: 0}, outside_air: {total_kw: 15.3}}},
  room_names: [{room_id: "r-k", name: "Kitchen"}, {room_id: "r-s", name: "Shop"}],
  room_peaks: [{room_id: "r-k", name: "Kitchen", design_total_kw: 12.7}, {room_id: "r-s", name: "Shop", design_total_kw: 14.3}],
  known_exclusions: [{room_id: "r-k", component_type: "extract_air", component: "Extract air"},
                     {room_id: "r-s", component_id: "boundary_edge_2", component: "Internal boundary"}],
  unresolved_room_inputs: [{room_id: "r-k", component_type: "infiltration"}, {room_id: "r-s", component_type: "infiltration"},
                           {room_id: "r-k", component_id: "area_only_walls", component_type: "envelope"}, {room_id: "r-k", component_type: "envelope"},
                           {room_id: "r-s", component_id: "unclassified_wall_boundaries", component_type: "envelope"}, {room_id: "r-s", component_type: "envelope"}],
  refrigeration_process_exclusions: [{room_name: "Coolroom"}, {room_name: "Freezer", level: "Unassigned level"}, {room_name: "Freezer", level: "Unassigned level"}],
  design_conditions_basis: {design_day: {label: "Generic"}},
};

function tab(state, detail = "", count = undefined) { return {state, detail, ...(count === undefined ? {} : {count})}; }

function baseStatus(over = {}) {
  return {id: "job-1", name: "Corner cafe", address: "", found_site: "TENANCY 7, CENTRAL MALL", building_type: "", above: "",
          total_kw: null, result_stale: false, drawings_confirmed: true, checks: {total: 11, waiting: 0, blocked: 0, marker: "m1"},
          rooms: {total: 2, included: 1, with_area: 1}, area_overrides: [],
          tabs: {project: tab("needed", "Add the site address and what's above the tenancy."), drawings: tab("done"),
                 rooms: tab("check", "1 of 2 rooms have an area"), walls: tab("needed"), windows: tab("todo"),
                 results: tab("todo", "Not calculated yet.")},
          subtabs: {project: {job_site: tab("check", "Review the address found on the drawings", 1), tenancy_context: tab("needed", "Tell us what's above", 1)},
                    rooms: {room_details: tab("check", "Review room use", 1), measurements: tab("needed", "Add an area", 1)},
                    walls: {walls: tab("needed", "Classify walls", 2), roof: tab("check", "Review the roof", 1)}}, ...over};
}

const scope = {status: "not_confirmed", candidate_fingerprint: "fp", uses: {office: "Office", kitchen: "Kitchen"},
  candidates: [{key: "room-use:unassigned-level:kitchen", label: "Kitchen", level: "Unassigned level", area_m2: null, area_origin: "", include: false, status: "no_area", source_pages: [20]},
               {key: "room-use:unassigned-level:shop", label: "Shop", level: "Unassigned level", area_m2: 216.1, area_origin: "ai_determined", include: true, status: "calculated", source_pages: [20]}]};

const PNG = Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==", "base64");

// A 1000 × 700 px plan at 1:100 with 2.5 px per point: 14.11 mm per px. Walls: a 400 × 300 px box from (100, 100).
const PLAN_PAGE = {page: 20, title: "Dimension Plan", proposed_role: "main_floor_plan", image_width_px: 1000, image_height_px: 700,
  preview_url: "/api/artifact?plan.png", preview_matches_vector_coordinates: true, scale_denominator: 100, image_px_per_pt: 2.5, declared_scale: "1:100"};
const SNAP = {page: PLAN_PAGE, snap_tolerance_px: 8, source_pdf_fingerprint: "pdf-fp", vector_page_fingerprint: "page-fp",
  lines: [{line_id: "top", start_px: [100, 100], end_px: [500, 100]}, {line_id: "right", start_px: [500, 100], end_px: [500, 400]},
          {line_id: "bottom", start_px: [500, 400], end_px: [100, 400]}, {line_id: "left", start_px: [100, 400], end_px: [100, 100]}],
  endpoints: [[100, 100, "top"], [500, 100, "right"], [500, 400, "bottom"], [100, 400, "left"]].map(([x, y, id]) => ({point_px: [x, y], line_id: id})),
  intersections: []};
const MM_PER_PX = 100 * 25.4 / 72 / 2.5;
function geometryContext(records = []) {
  return {source_pdf_fingerprint: "pdf-fp", pages: [PLAN_PAGE, {...PLAN_PAGE, page: 21, title: "Ceiling Plan", proposed_role: "reflected_ceiling_plan"}],
          rooms: [{room_id: "room-use:unassigned-level:kitchen", label: "Kitchen", level_name: "Unassigned level", source_pages: [21]},
                  {room_id: "room-use:unassigned-level:shop", label: "Shop", level_name: "Unassigned level", source_pages: [20]}],
          reviewer_room_geometry: {records}};
}

async function mockJob(page, {status, model = () => ({room_scope: scope}), onPost = () => null, tasks = () => [], geometry = () => geometryContext(),
                               prepare = () => ({status: "none"}), calculation = () => ({status: "none"}),
                               skill = () => ({status: "blocked", blocked_reason: "Project consent is required before PDF evidence can be sent.", stages: [], findings: []}),
                               vision = () => ({settings: {owner_opt_in: false, selected_group_ids: []}, selection: {page_count: 3, group_count: 2}, provider_configured: true}),
                               reading = () => ({status: "none", enabled: true, page_count: 0, read: 0, failed: 0, main_plans: {}, pages: []})}) {
  const posts = [];
  await page.route("**/api/test-mode/status", route => route.fulfill({json: {enabled: false}}));
  await page.route("**/api/projects", route => route.fulfill({json: [{id: "job-1", name: analysis.name, pages: 38, relevant: 3, analysed: true}]}));
  await page.route("**/api/analysis?id=job-1", route => route.fulfill({json: analysis}));
  await page.route("**/api/job-status**", route => route.fulfill({json: status()}));
  const record = kind => async route => {
    const body = route.request().postDataJSON();
    posts.push([kind, body]);
    const reply = onPost(kind, body);
    if (reply?.httpStatus) return route.fulfill({status: reply.httpStatus, json: reply.json || {message: reply.message}});
    return route.fulfill({json: reply || (kind === "model" ? model() : status())});
  };
  await page.route("**/api/job-setup", record("setup"));
  await page.route("**/api/room-area-override", record("area"));
  await page.route("**/api/room-height-override", record("height"));
  await page.route("**/api/reviewer-room-geometry**", route => route.request().method() === "GET"
    ? route.fulfill({json: geometry()}) : record("geometry")(route));
  await page.route("**/api/autonomous-tasks**", async route => {
    const url = route.request().url();
    if (url.includes("/api/autonomous-tasks/image")) return route.fulfill({contentType: "image/png", body: PNG});
    if (route.request().method() === "GET") return route.fulfill({json: {tasks: tasks()}});
    return record("tasks")(route);
  });
  await page.route("**/api/skill-workflow**", async route => {
    if (route.request().method() === "GET") return route.fulfill({json: skill()});
    return record("skill")(route);
  });
  await page.route("**/api/page-reading**", async route => {
    if (route.request().method() === "GET") return route.fulfill({json: reading()});
    return record("reading")(route);
  });
  await page.route("**/api/vision-extraction**", async route => {
    if (route.request().method() === "GET") return route.fulfill({json: vision()});
    return record("vision")(route);
  });
  await page.route("**/api/decisions", record("decisions"));
  // Server-side Calculate: POST starts it, GET reports it.
  await page.route("**/api/job-calculation**", async route => {
    if (route.request().method() === "POST") { posts.push(["calculation", route.request().postDataJSON()]); return route.fulfill({json: calculation("start")}); }
    return route.fulfill({json: calculation("status")});
  });
  // Server-side page preparation: POST starts it, GET reports it. Tests replace prepareJob to script a run.
  await page.route("**/api/prepare-pages**", async route => {
    if (route.request().method() === "POST") { posts.push(["prepare", route.request().postDataJSON()]); return route.fulfill({json: prepare("start")}); }
    return route.fulfill({json: prepare("status")});
  });
  await page.route("**/api/plan-snap**", route => route.fulfill({json: SNAP}));
  await page.route("**/api/artifact**", route => route.fulfill({contentType: "image/png", body: PNG}));
  await page.route("**/api/vision-response/no-ai", route => route.fulfill({json: {status: "already_started_without_ai", has_reasoning_packet: true}}));
  await page.route("**/api/ai-preliminary-model**", route => route.request().method() === "POST" ? record("model")(route) : route.fulfill({json: model()}));
  return posts;
}

async function openJob(page, path = "/") {
  await page.goto(path);
  await page.locator("[data-open='job-1']").click();
}

test("a job opens in the workspace: rail with a status per tab, the projects list hidden, and an address per tab", async ({ page }) => {
  await mockJob(page, {status: () => baseStatus()});
  await openJob(page);
  await expect(page.locator("#vJob")).toBeVisible();
  await expect(page.locator("#vRes")).toBeHidden();
  await expect(page.locator("aside.side")).toBeHidden();
  await expect(page.locator(".ws-tab")).toHaveText([/Project.*1 item to add · 1 to review/s, /Drawings/, /Rooms.*1 item to add · 1 to review/s,
    /Walls & roof/, /Windows/, /Results.*Not calculated yet/s]);
  await expect(page.locator("[data-ws-tab='project'] .ws-tab-icon")).toHaveText("●");
  await expect(page.locator("[data-ws-tab='project']")).toHaveClass(/is-needed/);
  await expect(page.locator("[data-ws-tab='project']")).not.toHaveClass(/is-error/);
  await expect(page.locator("#jobTitle")).toHaveText("Corner cafe");
  await expect(page.locator("#jobAddress")).toHaveText("TENANCY 7, CENTRAL MALL");
  await expect(page.locator("#wsTotal")).toHaveText("—");
  await expect(page).toHaveURL(/#\/job\/job-1\/walls\/walls$/);   // first pending tab in the ordered rail, after Project
  await expect(page.locator("[data-ws-tab='walls']")).toHaveAttribute("aria-current", "page");
  await expect(page.locator("#wsSubtabs")).toContainText("Walls");
  await expect(page.locator("#wsSubtabs")).toContainText("To do");
  await expect(page.locator("#wsSubtabs")).toContainText("Roof");
  await expect(page.locator("#wsSubtabs")).toContainText("Review");
  await page.locator("[data-ws-tab='project']").click();
  await expect(page).toHaveURL(/#\/job\/job-1\/project\/job-site$/);
  await expect(page.locator("[data-ws-project]")).toBeVisible();
  await page.goBack();
  await expect(page.locator("[data-ws-walls]")).toBeVisible();
});

test("Project: the job details are saved once and the rail updates", async ({ page }) => {
  let saved = false;
  const posts = await mockJob(page, {status: () => saved ? baseStatus({address: "1 Main St, Ryde NSW 2112", above: "floor", tabs: {...baseStatus().tabs, project: tab("done")},
    subtabs: {...baseStatus().subtabs, project: {job_site: tab("done", "Saved", 0), tenancy_context: tab("done", "Saved", 0)}}}) : baseStatus(),
                                     onPost: kind => { if (kind === "setup") saved = true; return null; }});
  await page.goto("/#/job/job-1/project");
  await expect(page.locator("[data-ws-project]")).toBeVisible();
  await page.getByRole("button", {name: "Use this"}).click();
  await expect(page.locator("[name=address]")).toHaveValue("TENANCY 7, CENTRAL MALL");
  await page.locator("[name=address]").fill("1 Main St, Ryde NSW 2112");
  await page.locator("[name=building_type]").selectOption("food_tenancy");
  await page.getByRole("link", {name: /Tenancy context/}).click();
  await page.getByLabel("Another floor or tenancy").check();
  await page.locator("[name=person]").fill("Sam");
  await page.getByRole("button", {name: "Save"}).click();
  await expect(page.locator("[data-ws-project-status]")).toHaveText("Saved.");
  expect(posts.find(([kind]) => kind === "setup")[1]).toMatchObject({project_id: "job-1", address: "1 Main St, Ryde NSW 2112",
    building_type: "food_tenancy", above: "floor", edited_by: "Sam"});
  await expect(page.locator("[data-ws-tab='project'] .ws-tab-icon")).toHaveText("✓");
  await expect(page.locator("#jobAddress")).toHaveText("1 Main St, Ryde NSW 2112");
});

test("sub-tab links support direct links, fallback, and browser back and forward", async ({page}) => {
  await mockJob(page, {status: () => baseStatus()});
  await page.goto("/#/job/job-1/rooms/measurements");
  await expect(page.locator("[data-ws-rooms]")).toHaveClass(/ws-room-measurements/);
  await page.locator(".ws-subtab").filter({hasText: "Room details"}).click();
  await expect(page).toHaveURL(/#\/job\/job-1\/rooms\/room-details$/);
  await expect(page.locator("[data-ws-rooms]")).toHaveClass(/ws-room-details/);
  await page.goBack();
  await expect(page.locator("[data-ws-rooms]")).toHaveClass(/ws-room-measurements/);
  await page.goForward();
  await expect(page.locator("[data-ws-rooms]")).toHaveClass(/ws-room-details/);
  await page.goto("/#/job/job-1/rooms/not-a-subtab");
  await expect(page).toHaveURL(/#\/job\/job-1\/rooms\/room-details$/);
});

test("Rooms sub-tabs group room details separately from measurements", async ({page}) => {
  const model = {...scope, candidates: scope.candidates.map(row => row.label === "Kitchen" ? {...row, include: true, status: "needs_use"} : row)};
  await mockJob(page, {status: () => baseStatus(), model: () => ({room_scope: model})});
  await page.goto("/#/job/job-1/rooms/room-details");
  const kitchen = page.locator("[data-ws-room='room-use:unassigned-level:kitchen']");
  await expect(kitchen.locator("[data-ws-include]")).toBeVisible();
  await expect(kitchen.locator("[data-ws-use]")).toBeVisible();
  await expect(kitchen.locator("[data-ws-area]")).toBeHidden();
  await expect(kitchen.locator(".ws-chip")).toHaveCSS("background-color", "rgb(225, 236, 245)");
  await page.locator(".ws-subtab").filter({hasText: "Measurements"}).click();
  await expect(kitchen.locator("[data-ws-area]")).toBeVisible();
  await expect(kitchen.locator("[data-ws-height]")).toBeVisible();
  await expect(kitchen.locator("[data-ws-trace]")).toBeVisible();
  await expect(kitchen.locator("[data-ws-include]")).toBeHidden();
});

test("Project save failures remain red and actionable", async ({page}) => {
  await mockJob(page, {status: () => baseStatus(), onPost: kind => kind === "setup"
    ? {httpStatus: 400, message: "Address could not be saved."} : null});
  await page.goto("/#/job/job-1/project/job-site");
  await page.locator("[name=address]").fill("1 Main Street");
  await page.getByRole("button", {name: "Save"}).click();
  const error = page.locator("[data-ws-project-status]");
  await expect(error).toHaveAttribute("role", "alert");
  await expect(error).toHaveClass(/is-error/);
  await expect(error).toContainText("Could not save");
});

test("Drawings: shows analysis status, keeps page changes available on request, and has a Toki-team override", async ({ page }) => {
  await mockJob(page, {status: () => baseStatus({checks: {total: 11, waiting: 9, blocked: 1, marker: "m"},
                                                  tabs: {...baseStatus().tabs, drawings: tab("working", "Toki is reviewing the drawings.")}}),
                       tasks: () => [{task: "P1_site", target: "project", status: "waiting_for_reply", prompt: "Find the site.", images: []}]});
  await page.goto("/#/job/job-1/drawings");
  await expect(page.locator("[data-ws-checks]")).toContainText("The Toki team is reviewing drawing details");
  await expect(page.locator("[data-ws-checks]")).not.toContainText("of 11 checks done");
  await expect(page.locator("[data-ws-checks]")).toContainText("continue with the rooms and measurements");
  await expect(page.locator("[data-ws-tab='drawings'] .ws-tab-icon")).toHaveText("◌");
  await expect(page.locator("[data-ws-operator-panel]")).toHaveCount(0);
  await expect(page.locator("[data-ws-drawings]")).toContainText("Pages Toki selected");
  await expect(page.locator("[data-ws-drawings]")).toContainText("Using 2 of 3 pages");
  await expect(page.locator("[data-ws-pages]")).not.toBeVisible();
  await page.locator(".ws-page-controls summary").click();
  await expect(page.locator("[data-ws-pages] li")).toHaveCount(2);
  await expect(page.locator("[data-ws-pages] li.is-main")).toContainText("Page 20 · main plan");
  await page.getByRole("button", {name: "Show all 3 pages"}).click();
  await expect(page.locator("[data-ws-pages] li")).toHaveCount(3);
  await expect(page.locator("[data-ws-pages] li").nth(2)).toContainText("section");  // boilerplate titles fall back to the sheet type
  await page.getByRole("button", {name: "Open Toki team review"}).click();
  await expect(page.locator("[data-ws-operator-panel]")).toBeVisible();
  await expect(page.locator("#wsOpTitle")).toHaveText("Find the site address");
  await page.getByRole("button", {name: "Hide the AI step"}).click();
  await expect(page.locator("[data-ws-operator-panel]")).toHaveCount(0);
});

test("Drawings: completed analysis points contractors to review items instead of a check count", async ({page}) => {
  await mockJob(page, {status: () => baseStatus({checks: {total: 11, waiting: 0, blocked: 0, marker: "done"}}),
    skill: () => ({status: "completed", read_only: false, stages: [], findings: [], subskills: []}),
    vision: () => ({settings: {owner_opt_in: true, selected_group_ids: ["plans"]}, selection: {page_count: 3, group_count: 1}})});
  await page.goto("/#/job/job-1/drawings");
  await expect(page.locator("[data-ws-checks]")).toContainText("Drawing analysis is complete");
  await expect(page.locator("[data-ws-checks]")).toContainText("Review");
  await expect(page.locator("[data-ws-checks]")).not.toContainText("11 checks");
  await expect(page.locator("[data-ws-operator-panel]")).toHaveCount(0);
});

test("PDF review: consent starts the skills review and findings show page evidence before approval", async ({page}) => {
  let approved = false;
  let consented = false;
  const proposal = {id: "room_boundaries_areas:geometry_candidates:0", subskill_id: "room_boundaries_areas",
    field: "geometry_candidates", value: {label: "Shop", area_m2: 42}, units: "m²", pages: [20],
    citations: [{page: 20, excerpt: "Shop 42 m2"}], confidence: 0.87, status: "proposed", inferences: [], unresolved_fields: []};
  const run = () => consented
    ? ({status: "needs_review", stages: [{label: "Rooms, geometry, and gains", status: "needs_review"}],
       findings: [{...proposal, status: approved ? "accepted" : "proposed", reviewer: approved ? "Operator" : ""}]})
    : ({status: "blocked", blocked_reason: "Project consent is required before PDF evidence can be sent.", stages: [], findings: []});
  const consent = {settings: {owner_opt_in: false, selected_group_ids: []}, selection: {page_count: 3, group_count: 2}, provider_configured: true};
  await mockJob(page, {status: () => baseStatus(), skill: run, vision: () => consent,
    onPost: (kind, body) => {
      if (kind === "vision" && body.action === "save_settings") { consent.settings.owner_opt_in = body.settings.owner_opt_in; consented = body.settings.owner_opt_in; return {settings: consent.settings, selection: consent.selection, provider_configured: true}; }
      if (kind === "skill" && body.action === "review_finding") { approved = body.decision === "accepted"; return run(); }
      return null;
    }});
  await page.goto("/#/job/job-1/drawings");
  await expect(page.locator("[data-ws-pdf-review]")).toContainText("Project consent is required");
  await page.locator("[data-ws-pdf-consent]").check();
  await expect(page.locator("[data-ws-pdf-consent]")).toBeChecked();
  await expect(page.locator(".ws-skill-finding")).toContainText("Shop");
  await expect(page.locator(".ws-skill-finding")).toContainText("Page 20: Shop 42 m2");
  await page.getByRole("button", {name: "Accept value"}).click();
  await expect(page.locator(".ws-skill-finding")).toContainText("accepted");
});

test("PDF review: evidence labels, missing-value editing, and conflict choice are explicit", async ({page}) => {
  const decisions = [];
  const findings = [
    {id: "room_identity_use:rooms:0", subskill_id: "room_identity_use", field: "room use", target: "Shop",
      value: "shop", evidence: "supported", pages: [20], citations: [{page: 20, excerpt: "SHOP"}], status: "proposed", alternatives: []},
    {id: "room_identity_use:missing:ceiling_height:0", subskill_id: "ceiling_height_volume", field: "ceiling_height",
      value: null, evidence: "missing", missing: true, pages: [], citations: [], status: "missing", alternatives: []},
    {id: "room_identity_use:rooms:1", subskill_id: "room_identity_use", field: "room use", target: "Kitchen",
      value: "shop", evidence: "conflicting", pages: [20], citations: [{page: 20, excerpt: "KITCHEN"}], status: "proposed", alternatives: ["kitchen", "shop"]},
  ];
  const skill = () => ({status: "needs_review", read_only: false, stages: [], findings: findings.map(row => ({...row,
    status: decisions.includes(row.id) ? "accepted" : row.status}))});
  await mockJob(page, {status: () => baseStatus(), skill, vision: () => ({settings: {owner_opt_in: true, selected_group_ids: ["plans"]}, selection: {page_count: 1, group_count: 1}}),
    onPost: (kind, body) => { if (kind === "skill" && body.action === "review_finding") { decisions.push(body.finding_id); return skill(); } return null; }});
  await page.goto("/#/job/job-1/drawings");
  await expect(page.locator("[data-ws-evidence-count]")).toContainText("1 supported · 1 missing · 1 conflicting");
  await expect(page.locator("[data-ws-finding='room_identity_use:rooms:0'] [data-ws-evidence]")).toHaveText("supported");
  const missing = page.locator("[data-ws-finding='room_identity_use:missing:ceiling_height:0']");
  await expect(missing.locator("[data-ws-finding-accept]")).toHaveCount(0);
  await missing.locator("[data-ws-finding-edit]").fill("3.2");
  await missing.getByRole("button", {name: "Save value"}).click();
  expect(decisions).toContain("room_identity_use:missing:ceiling_height:0");
  // Reviewed findings drop out of the open count.
  await expect(page.locator("[data-ws-evidence-count]")).toHaveText("Open findings: 1 supported · 1 conflicting");
  const conflict = page.locator("[data-ws-finding='room_identity_use:rooms:1']");
  await expect(conflict.locator("[data-ws-finding-accept]")).toHaveCount(0);
  await conflict.getByRole("button", {name: "kitchen"}).evaluate(button => button.click());
  await expect(conflict.locator("[data-ws-finding-edit]")).toHaveValue('"kitchen"');
  await conflict.getByRole("button", {name: "Save chosen value"}).click();
  expect(decisions).toContain("room_identity_use:rooms:1");
});

test("PDF review: a missing or conflicting finding can be closed without a value", async ({page}) => {
  const rejected = [];
  const findings = [
    {id: "surface_area:missing:area:0", subskill_id: "surface_area", field: "area", value: null, evidence: "missing",
      missing: true, pages: [], citations: [], status: "missing", alternatives: []},
    {id: "room_identity_use:rooms:1", subskill_id: "room_identity_use", field: "rooms", target: "Kitchen", value: "shop",
      evidence: "conflicting", pages: [20], citations: [], status: "proposed", alternatives: ["kitchen", "shop"]},
  ];
  const skill = () => ({status: "needs_review", read_only: false, stages: [], findings: findings.map(row => ({...row,
    status: rejected.includes(row.id) ? "rejected" : row.status}))});
  await mockJob(page, {status: () => baseStatus(), skill, vision: () => ({settings: {owner_opt_in: true}, selection: {page_count: 1, group_count: 1}}),
    onPost: (kind, body) => { if (kind === "skill" && body.action === "review_finding" && body.decision === "rejected") { rejected.push(body.finding_id); return skill(); } return null; }});
  await page.goto("/#/job/job-1/drawings");
  await page.locator("[data-ws-finding='surface_area:missing:area:0']").getByRole("button", {name: "Leave missing"}).click();
  await expect(page.locator("[data-ws-finding='surface_area:missing:area:0']")).toContainText("rejected");
  await page.locator("[data-ws-finding='room_identity_use:rooms:1']").getByRole("button", {name: "Reject"}).click();
  expect(rejected).toEqual(["surface_area:missing:area:0", "room_identity_use:rooms:1"]);
  await expect(page.locator("[data-ws-evidence-count]")).toHaveCount(0);
});

test("Drawings: every page read by AI shows main plans per level and what each page holds", async ({page}) => {
  let state = {status: "done", enabled: true, seconds: 250, page_count: 3, read: 2, failed: 1, main_plans: {ground: [2], mezzanine: [3]},
    pages: [{page: 1, status: "failed", reason: "usage limit reached"},
            {page: 2, status: "read", page_type: "floor_plan", title: "PROPOSED FLOOR PLAN", level: "Ground",
             information: [{kind: "equipment_appliances", what: "75 inch TV", evidence: "north wall"}, {kind: "room_geometry", what: "Shop 120 m2", evidence: ""}]},
            {page: 3, status: "read", page_type: "floor_plan", title: "MEZZANINE PLAN", level: "Mezzanine", information: []}]};
  const posts = [];
  await mockJob(page, {status: () => baseStatus(), reading: () => state,
    onPost: (kind, body) => { if (kind === "reading") { posts.push(body); if (body.action === "set_enabled") state = {...state, enabled: body.enabled}; return state; } return null; }});
  await page.goto("/#/job/job-1/drawings");
  const section = page.locator("[data-ws-page-reading]");
  await expect(section.locator("[data-ws-main-plans]")).toHaveText("Main floor plan: Ground p2 · Mezzanine p3");
  await expect(section.locator("[data-ws-page-reading-status]")).toHaveText("Read all 2 pages in 4 min.");
  await section.getByText("What's on each page (2 of 3 read)").click();
  await expect(section.locator("[data-ws-read-page='1']")).toContainText("not read: usage limit reached");
  await section.locator("[data-ws-read-page='2'] summary").click();
  await expect(section.locator("[data-ws-read-page='2']")).toContainText("Equipment: 75 inch TV (north wall)");
  await expect(section.locator("[data-ws-read-page='2'] summary")).toContainText("Areas and sizes");
  await section.locator("[data-ws-read-page='3'] summary").click();
  await expect(section.locator("[data-ws-read-page='3']")).toContainText("No heat-load information on this page.");
  // A refresh of the tab keeps the opened list and page open.
  await page.evaluate(() => window.dispatchEvent(new HashChangeEvent("hashchange")));
  await expect(section.locator("[data-ws-read-page='2']")).toContainText("Equipment: 75 inch TV (north wall)");
  await expect(section.locator("[data-ws-read-page='2'] details")).toHaveAttribute("open", "");
  await section.locator("[data-ws-page-reading-enabled]").uncheck();
  await expect(page.locator("[data-ws-page-reading-status]")).toContainText("Switched off for this job");
  expect(posts).toEqual([{project_id: "job-1", action: "set_enabled", enabled: false}]);
});

test("Drawings: a failed or blocked page reading says why and offers a retry", async ({page}) => {
  const posts = [];
  await mockJob(page, {status: () => baseStatus(),
    reading: () => ({status: "failed", enabled: true, problem: "2 of 38 pages couldn't be read (pages 4, 9). First reason: timeout Retry to read them.",
                     page_count: 38, read: 36, failed: 2, main_plans: {}, pages: []}),
    onPost: (kind, body) => { if (kind === "reading") posts.push(body); return null; }});
  await page.goto("/#/job/job-1/drawings");
  await expect(page.locator("[data-ws-page-reading-status]")).toContainText("2 of 38 pages couldn't be read");
  await expect(page.locator("[data-ws-page-reading-status]")).toHaveClass(/is-warn/);
  await page.locator("[data-ws-page-reading-start]").click();
  expect(posts[0]).toMatchObject({project_id: "job-1", action: "retry"});
});

test("Results: open PDF review findings are clearly excluded from the displayed number", async ({page}) => {
  await mockJob(page, {status: () => baseStatus(),
    skill: () => ({status: "needs_review", findings: [
      {id: "a", status: "proposed", evidence: "supported"}, {id: "b", status: "missing", evidence: "missing"},
      {id: "c", status: "accepted", evidence: "supported"},
    ]}), model: () => ({hourly_ai_preliminary_load_report: report})});
  await page.goto("/#/job/job-1/results");
  await expect(page.locator("[data-ws-open-findings]")).toContainText("2 PDF review findings are still open");
  await expect(page.locator("[data-ws-open-findings]")).toContainText("We have not used these values in this number");
});

test("PDF review browser states on a temporary Butcher Buffet copy", async ({page}) => {
  const projects = JSON.parse(readFileSync(new URL("../../output/web_projects.json", import.meta.url), "utf8"));
  const neededFiles = ["ai_input.json", "drawing_coverage.json", "spatial_ocr.json", "vector_geometry.json", "packet.json"];
  const source = Object.values(projects).filter(row => /20260226 Butcher Buffet/i.test(row.name || "") && row.review_dir)
    .sort((a, b) => String(b.updated_at || "").localeCompare(String(a.updated_at || "")))
    .find(row => neededFiles.every(file => existsSync(join(row.review_dir, file))));
  test.skip(!source, "No saved Butcher Buffet project is available for this browser check.");
  const copiedReview = mkdtempSync(join(tmpdir(), "archie-card-r2-bb-copy-"));
  const screenshots = join(process.cwd(), "../output/card_r2_browser");
  mkdirSync(screenshots, {recursive: true});
  try {
    for (const file of neededFiles) {
      copyFileSync(join(source.review_dir, file), join(copiedReview, file));
    }
    let consented = false, accepted = false;
    const roomUse = {id: "room_identity_use:rooms:0", subskill_id: "room_identity_use", field: "Shop room use",
      target: "Shop", value: "shop", evidence: "supported", pages: [20], citations: [{page: 20, excerpt: "SHOP"}],
      status: "proposed", alternatives: [], units: ""};
    let response = {status: "blocked", blocked_reason: "Project consent is required before PDF evidence can be sent.", read_only: true, stages: [], findings: [], subskills: []};
    const skill = () => response;
    const vision = () => ({settings: {owner_opt_in: consented, selected_group_ids: ["plans"]}, selection: {page_count: 3, group_count: 1}, provider_configured: !consented || response.error_code !== "skill_provider_unavailable"});
    const posts = await mockJob(page, {status: () => baseStatus({name: source.name}), skill, vision,
      onPost: (kind, body) => {
        if (kind === "skill" && body.action === "review_finding") {
          accepted = body.finding_id === roomUse.id && body.decision === "accepted";
          response = {...response, status: "needs_review", read_only: false,
            findings: [{...roomUse, status: accepted ? "accepted" : "proposed", reviewer: accepted ? "Operator" : ""}]};
          return response;
        }
        return null;
      }});
    await page.route("**/api/projects", route => route.fulfill({json: [{id: "job-1", name: source.name, pages: source.pages, relevant: 3, analysed: true}]}));
    await page.route("**/api/analysis?id=job-1", route => route.fulfill({json: {...analysis, name: source.name, filename: source.name, file_name: source.name}}));
    await page.route("**/api/job-status**", route => route.fulfill({json: baseStatus({name: source.name})}));
    await page.goto("/#/job/job-1/drawings");
    await expect(page.locator("[data-ws-pdf-review-status]")).toContainText("blocked");
    await page.screenshot({path: join(screenshots, "01-bb-no-consent.png"), fullPage: true});

    consented = true;
    response = {status: "blocked", blocked_reason: "The configured AI provider is unavailable. Set up credentials, then retry.",
      error_code: "skill_provider_unavailable", read_only: false, stages: [], findings: [], subskills: []};
    await page.reload();
    await expect(page.locator("[data-ws-pdf-review]")).toContainText("provider is unavailable");
    await page.screenshot({path: join(screenshots, "02-bb-provider-unavailable.png"), fullPage: true});

    response = {status: "needs_review", read_only: false, stages: [{label: "Rooms, geometry, and gains", status: "needs_review"}],
      findings: [
        roomUse,
        {id: "ceiling_height_volume:heights:0", subskill_id: "ceiling_height_volume", field: "ceiling height", value: 3200,
          evidence: "inferred", units: "mm", pages: [20], citations: [{page: 20, excerpt: "derived from section scale"}],
          inferences: ["Measured from section scale"], formula: "scaled section measurement", status: "proposed", alternatives: []},
        {id: "surface_area:missing:area:0", subskill_id: "surface_area", field: "surface area", value: null,
          evidence: "missing", missing: true, pages: [], citations: [], status: "missing", alternatives: []},
        {id: "glazing_properties:openings:0", subskill_id: "glazing_properties", field: "glazing type", value: "clear",
          evidence: "conflicting", pages: [23], citations: [{page: 23, excerpt: "Clear/low-e notation differs across elevations"}],
          status: "proposed", alternatives: ["clear", "low-e"]},
      ], subskills: [{id: "room_identity_use", status: "needs_review", evidence_reviewed: {pages: [20, 23], provider: "test-provider", model: "test-model"}}]};
    await page.reload();
    await expect(page.locator("[data-ws-evidence-count]")).toContainText("supported");
    await expect(page.locator("[data-ws-evidence-count]")).toContainText("inferred");
    await expect(page.locator("[data-ws-evidence-count]")).toContainText("missing");
    await expect(page.locator("[data-ws-evidence-count]")).toContainText("conflicting");
    await expect(page.locator("[data-ws-checks]")).toContainText("PDF review has finished. Some findings below still need our review.");
    await expect(page.locator("[data-ws-checks]")).not.toContainText("Drawing analysis is complete");
    await page.screenshot({path: join(screenshots, "03-bb-findings.png"), fullPage: true});

    await page.locator(`[data-ws-finding='${roomUse.id}']`).getByRole("button", {name: "Accept value"}).click();
    await expect(page.locator(`[data-ws-finding='${roomUse.id}']`)).toContainText("accepted");
    expect(accepted).toBe(true);
    expect(posts.some(([kind]) => kind === "calculation")).toBe(false);
    await page.screenshot({path: join(screenshots, "04-bb-room-use-accepted.png"), fullPage: true});
  } finally {
    rmSync(copiedReview, {recursive: true, force: true});
  }
});

test("PDF review: manual P0–P6 is an explicit fallback, not an automatic second review", async ({page}) => {
  const posts = await mockJob(page, {status: () => baseStatus()});
  await page.goto("/#/job/job-1/drawings");
  await expect(page.locator("[data-ws-manual-fallback]")).toBeVisible();
  expect(posts.some(([kind, body]) => kind === "tasks" && body.action === "run_all")).toBe(false);
  await page.locator("[data-ws-manual-fallback]").click();
  await expect(page.locator("[data-ws-operator-panel]")).toBeVisible();
  expect(posts.some(([kind, body]) => kind === "tasks" && body.action === "run_all")).toBe(true);
});

test("Drawings: a saved page selection is shown after reopening the job", async ({ page }) => {
  await mockJob(page, {status: () => baseStatus()});
  await page.route("**/api/analysis?id=job-1", route => route.fulfill({json: {...analysis, selected_pages: [20]}}));
  await page.goto("/#/job/job-1/drawings");
  await page.locator(".ws-page-controls summary").click();
  await expect(page.locator("[data-ws-pages] li")).toHaveCount(1);
  await expect(page.locator("[data-ws-pages]")).toContainText("Page 20");
  await page.getByRole("button", {name: "Show all 3 pages"}).click();
  await expect(page.locator("[data-ws-page='27']")).not.toBeChecked();
});

test("Drawings: changing the pages used runs one server job, and switching tabs meanwhile watches it instead of starting another", async ({ page }) => {
  let job = {status: "none"};
  let polls = 0;
  const posts = await mockJob(page, {status: () => baseStatus(), prepare: kind => {
    if (kind === "start") job = {status: "running", step: "pages", step_label: "Saving the page choice and reading the pages"};
    else if (job.status === "running" && ++polls >= 3) job = {...job, step: "drawings", step_label: "Preparing the drawings"};
    return job;
  }});
  await page.goto("/#/job/job-1/drawings");
  await page.locator(".ws-page-controls summary").click();
  await expect(page.locator("[data-ws-use-pages]")).toHaveCount(0);
  await page.locator("[data-ws-page='27']").uncheck();
  await expect(page.locator("[data-ws-use-pages]")).toBeVisible();
  await page.locator("[data-ws-page='27']").check();
  await expect(page.locator("[data-ws-use-pages]")).toHaveCount(0);   // back to the confirmed pages: nothing to do
  await page.locator("[data-ws-page='27']").uncheck();
  await page.locator("[data-ws-use-pages]").click();
  await expect(page.locator("[data-ws-progress]")).toContainText("Updating the drawing pages");
  await expect(page.locator("[data-ws-progress-step]")).toHaveText("Saving the page choice and reading the pages");
  await expect(page.locator("[data-ws-progress]")).toContainText("You can leave this page; it carries on");
  // Switching away and back while the server prepares the pages watches the same run.
  await page.locator("[data-ws-tab='project']").click();
  await expect(page.locator("[data-ws-project]")).toBeVisible();
  await page.locator("[data-ws-tab='drawings']").click();
  await expect(page.locator("[data-ws-progress]")).toContainText("Updating the drawing pages");
  await expect(page.locator("[data-ws-progress-step]")).toHaveText("Preparing the drawings", {timeout: 15000});
  job = {status: "done", step: "checks"};
  await expect(page.locator("[data-ws-drawings]")).toBeVisible({timeout: 10000});
  const starts = posts.filter(([kind]) => kind === "prepare");
  expect(starts).toHaveLength(1);
  expect(starts[0][1].project_id).toBe("job-1");
  expect(starts[0][1].pages.map(row => row.page)).toEqual([20]);
  expect(starts[0][1].pages[0]).toMatchObject({decision: "Confirm as floor plan", detected_type: "floor_plan"});
  // The browser no longer drives the steps itself.
  expect(posts.filter(([kind]) => kind === "decisions" || kind === "tasks")).toHaveLength(0);
});

test("Drawings: reopening a job while the server still prepares its pages shows the progress and starts nothing new", async ({ page }) => {
  let job = {status: "running", step: "checks", step_label: "Setting up the drawing checks"};
  const posts = await mockJob(page, {status: () => baseStatus(), prepare: () => job});
  await page.goto("/#/job/job-1/drawings");
  await expect(page.locator("[data-ws-progress-step]")).toHaveText("Setting up the drawing checks");
  job = {status: "done"};
  await expect(page.locator("[data-ws-drawings]")).toBeVisible({timeout: 10000});
  expect(posts.filter(([kind]) => kind === "prepare")).toHaveLength(0);
});

test("Drawings: a job whose pages were never prepared starts the server job; a failure is shown with its reason and can be retried", async ({ page }) => {
  let job = {status: "none"};
  const posts = await mockJob(page, {status: () => baseStatus(), prepare: kind => {
    if (kind === "start") job = {status: "failed", step: "drawings", error: "The drawings could not be prepared from these pages."};
    return job;
  }});
  await page.route("**/api/analysis?id=job-1", route => route.fulfill({json: {...analysis, has_reasoning_packet: false}}));
  await page.goto("/#/job/job-1/drawings");
  await expect(page.locator(".ws-error")).toContainText("The drawings could not be prepared from these pages.");
  expect(posts.filter(([kind]) => kind === "prepare")).toHaveLength(1);
  await page.route("**/api/analysis?id=job-1", route => route.fulfill({json: {...analysis, has_reasoning_packet: true}}));
  job = {status: "none"};
  await page.getByRole("button", {name: "Try again"}).click();
  await expect.poll(() => posts.filter(([kind]) => kind === "prepare").length).toBe(2);
});

test("AI step: ?operator=1 opens the checks one at a time, in order; a bad reply is kept with the reason, a good one moves on", async ({ page, context }) => {
  await context.grantPermissions(["clipboard-read", "clipboard-write"]);
  const records = {
    "P2_north:page-20": {task: "P2_north", target: "page-20", status: "waiting_for_reply", prompt: "Which way is north?",
                         images: [{name: "image-1.png", url: "/api/autonomous-tasks/image?project_id=job-1&task=P2_north&target=page-20&run_id=r&name=image-1.png"},
                                  {name: "image-2.png", url: "/api/autonomous-tasks/image?project_id=job-1&task=P2_north&target=page-20&run_id=r&name=image-2.png"}]},
    "P1_site:project": {task: "P1_site", target: "project", status: "waiting_for_reply", prompt: "Which excerpt names the site?", images: [],
                        accuracy: {accuracy: 0.9, scored: 4}},
    "P6_kitchen:kitchen": {task: "P6_kitchen", target: "kitchen", status: "blocked", block_reason: "Waiting for room outlines (P0).", packet: {room: {room_label: "Kitchen"}}},
    "P5_roof:shop": {task: "P5_roof", target: "shop", status: "needs_contractor_answer"},
  };
  let attempts = 0;
  const posts = await mockJob(page, {
    status: () => baseStatus({checks: {total: 4, waiting: Object.values(records).filter(row => row.status === "waiting_for_reply").length, blocked: 1, marker: "m"}}),
    tasks: () => Object.values(records),
    onPost: (kind, body) => {
      if (kind !== "tasks" || body.action !== "validate_apply") return null;
      attempts += 1;
      const row = records[`${body.task}:${body.target}`];
      if (attempts === 1) Object.assign(row, {validation: {valid: false, error: "Reply must be valid JSON."}, block_reason: "Reply must be valid JSON."});
      else Object.assign(row, {status: "applied", validation: {valid: true}, block_reason: "", quality_label: "AI-determined", stand_in: body.stand_in,
                               reply_attempts: [{operator_seconds: 30}, {operator_seconds: 95}]});
      return {tasks: Object.values(records)};
    },
  });
  await page.goto("/?operator=1#/job/job-1");
  await expect(page).toHaveURL(/#\/job\/job-1\/drawings$/);
  await expect(page.locator("#vJob")).toBeVisible();
  await expect(page.locator("[data-ws-operator-off]")).toHaveCount(0);   // always on with ?operator=1
  const task = page.locator("[data-ws-op-task]");
  await expect(page.locator("#wsOpTitle")).toHaveText("Find the site address");   // site before north, whatever the list order
  await expect(task.locator(".ws-op-count")).toHaveText("Check 1 of 2 to answer");
  await expect(task).toContainText("scored 90% on the answer keys (4 cases)");
  await expect(page.locator("[data-ws-op-blocked]")).toHaveText("List the kitchen equipment — Kitchen — Waiting for room outlines (P0).");
  await expect(page.locator("[data-ws-op-asked]")).toContainText("1 roof question is answered by the contractor on the Project tab");
  await page.getByRole("button", {name: "Copy prompt"}).click();
  await expect(task.locator("[data-ws-op-status]")).toHaveText("Prompt copied.");
  expect(await page.evaluate(() => navigator.clipboard.readText())).toBe("Which excerpt names the site?");
  await task.locator("[data-ws-model]").fill("GPT-test");
  await task.locator("[data-ws-model]").press("Tab");
  await page.getByRole("button", {name: "Check and apply"}).click();
  await expect(task.locator("[data-ws-op-status]")).toHaveText("Paste ChatGPT's reply first.");
  await task.locator("[data-ws-reply]").fill("{not json");
  await page.getByRole("button", {name: "Check and apply"}).click();
  await expect(page.locator("[data-ws-op-error]")).toHaveText("Reply must be valid JSON.");
  await expect(page.locator("#wsOpTitle")).toHaveText("Find the site address");
  await expect(page.locator("[data-ws-reply]")).toHaveValue("{not json");           // the pasted reply is kept
  await page.locator("[data-ws-reply]").fill('{"site": null, "consultant_addresses": []}');
  await page.locator("[data-ws-stand-in]").check();
  await page.getByRole("button", {name: "Check and apply"}).click();
  await expect(page.locator("#wsOpTitle")).toHaveText("Read the north arrow — page 20");
  await expect(page.locator("[data-ws-op-status]")).toHaveText("Applied: Find the site address (stand-in, test only).");
  const applied = posts.filter(([kind, body]) => kind === "tasks" && body.action === "validate_apply").map(([, body]) => body);
  expect(applied).toHaveLength(2);
  expect(applied[1]).toMatchObject({project_id: "job-1", task: "P1_site", target: "project", reply: '{"site": null, "consultant_addresses": []}',
                                    model_note: "GPT-test", stand_in: true});
  expect(typeof applied[1].operator_seconds).toBe("number");
  await expect(page.locator("[data-ws-op-task] [data-ws-model]")).toHaveValue("GPT-test");   // remembered for the next check
  await expect(page.locator("[data-ws-op-image]")).toHaveCount(2);
  await expect(page.locator("[data-ws-op-task] a[download]").first()).toHaveAttribute("download", "P2_north-page-20-image-1.png");
  await page.locator("[data-ws-copy-image]").first().click();
  await expect(page.locator("[data-ws-op-status]")).toHaveText(/Image copied|blocked copying the image/);
  await expect(page.locator("[data-ws-op-time]")).toHaveText("2 min spent on 1 check · about 2 min each");
  await page.locator(".ws-op-finished summary").click();
  await expect(page.locator("[data-ws-op-finished]")).toContainText("Find the site address");
  await expect(page.locator("[data-ws-op-finished]")).toContainText("Stand-in (test) · 2 min");
});

test("AI step: Skip for now moves to the next check and comes back round", async ({ page }) => {
  const tasks = ["page-20", "page-21"].map(target => ({task: "P2_north", target, status: "waiting_for_reply", prompt: `North on ${target}?`, images: []}));
  await mockJob(page, {status: () => baseStatus(), tasks: () => tasks});
  await page.goto("/?operator=1#/job/job-1/drawings");
  await expect(page.locator("#wsOpTitle")).toHaveText("Read the north arrow — page 20");
  await page.locator("[data-ws-reply]").fill("draft for page 20");
  await page.getByRole("button", {name: "Skip for now"}).click();
  await expect(page.locator("#wsOpTitle")).toHaveText("Read the north arrow — page 21");
  await expect(page.locator(".ws-op-count")).toHaveText("Check 2 of 2 to answer");
  await page.getByRole("button", {name: "Skip for now"}).click();
  await expect(page.locator("#wsOpTitle")).toHaveText("Read the north arrow — page 20");
  await expect(page.locator("[data-ws-reply]")).toHaveValue("draft for page 20");
});

test("Rooms: a typed area is saved at once, shown as Edited by you, and nothing is rebuilt until Calculate", async ({ page }) => {
  let overrides = [];
  const posts = await mockJob(page, {
    status: () => baseStatus({area_overrides: overrides, rooms: {total: 2, included: overrides.length + 1, with_area: overrides.length + 1}}),
    onPost: (kind, body) => { if (kind === "area") overrides = body.area_m2 == null ? [] : [{room_id: body.room_id, room_label: body.label, area_m2: body.area_m2}]; return null; },
  });
  await page.goto("/#/job/job-1/rooms/measurements");
  const kitchen = page.locator("[data-ws-room='room-use:unassigned-level:kitchen']");
  await expect(kitchen.locator(".ws-chip")).toHaveText("No area yet");
  await expect(kitchen.locator("td").nth(1)).toHaveText("");  // "Unassigned level" is not shown
  await kitchen.locator("[data-ws-area]").fill("104.9");
  await kitchen.locator("[data-ws-area]").press("Tab");
  await expect(page.locator("[data-ws-rooms-status]")).toHaveText("Kitchen: 104.9 m² saved.");
  await expect(kitchen.locator(".ws-chip")).toHaveText("Edited by you");
  await expect(kitchen.locator("[data-ws-include]")).toBeChecked();
  expect(posts.filter(([kind]) => kind === "area")[0][1]).toMatchObject({room_id: "room-use:unassigned-level:kitchen", label: "Kitchen", area_m2: 104.9});
  expect(posts.some(([kind, body]) => kind === "model" && body.action === "assemble")).toBe(false);
  await kitchen.locator("[data-ws-area]").fill("");
  await kitchen.locator("[data-ws-area]").press("Tab");
  await expect(page.locator("[data-ws-rooms-status]")).toHaveText("Kitchen: back to the drawing value.");
  await expect(kitchen.locator(".ws-chip")).toHaveText("No area yet");
});

test("Rooms: a typed ceiling height is saved in millimetres, shown as Edited by you, and marks the result out of date", async ({ page }) => {
  let typed = null;
  const posts = await mockJob(page, {
    status: () => baseStatus({total_kw: 30, result_stale: typed != null,
      room_heights: {"room-use:unassigned-level:shop": typed ? {ceiling_height_mm: typed, origin: "edited"} : {ceiling_height_mm: 2700, origin: "preliminary_fallback"}}}),
    onPost: (kind, body) => { if (kind === "height") typed = body.ceiling_height_mm; return null; },
  });
  await page.goto("/#/job/job-1/rooms/measurements");
  const shop = page.locator("[data-ws-room='room-use:unassigned-level:shop']");
  await expect(shop.locator("[data-ws-height]")).toHaveValue("");
  await expect(shop.locator("[data-ws-height]")).toHaveAttribute("placeholder", "2.70");
  await expect(shop.locator(".ws-height-source")).toHaveText("Typical height");
  await expect(page.locator("[data-ws-room='room-use:unassigned-level:kitchen'] .ws-height-source")).toHaveText("Set on Calculate");
  await shop.locator("[data-ws-height]").fill("1.2");
  await shop.locator("[data-ws-height]").press("Tab");
  await expect(page.locator("[data-ws-rooms-status]")).toHaveText("Type the ceiling height in metres (1.8 to 15), or clear it.");
  await shop.locator("[data-ws-height]").fill("3.2");
  await shop.locator("[data-ws-height]").press("Tab");
  await expect(page.locator("[data-ws-rooms-status]")).toHaveText("Shop: ceiling height 3.2 m saved.");
  await expect(shop.locator("[data-ws-height]")).toHaveValue("3.20");
  await expect(shop.locator(".ws-height-source")).toHaveText("Edited by you");
  await expect(page.locator("#wsTotalNote")).toHaveText("Out of date — calculate again");
  const sent = posts.filter(([kind]) => kind === "height");
  expect(sent).toHaveLength(1);
  expect(sent[0][1]).toMatchObject({project_id: "job-1", room_key: "room-use:unassigned-level:shop", label: "Shop", ceiling_height_mm: 3200});
});

test("Rooms: a room the drawings missed is added with its use, on the plan page, and its area", async ({ page }) => {
  let overrides = [];
  const posts = await mockJob(page, {
    status: () => baseStatus({area_overrides: overrides}),
    onPost: (kind, body) => {
      if (kind === "geometry") return {rooms: [{room_id: "room-use:unassigned-level:office", label: "Office", reviewer_added: true}]};
      if (kind === "area") overrides = [{room_id: body.room_id, room_label: body.label, area_m2: body.area_m2}];
      return null;
    },
  });
  await page.goto("/#/job/job-1/rooms");
  const form = page.locator("[data-ws-add]");
  await form.locator("[name=label]").fill("Office");
  await form.locator("[name=use]").selectOption("office");
  await form.locator("[name=area]").fill("10");
  await form.getByRole("button", {name: "Add"}).click();
  await expect(page.locator("[data-ws-rooms-status]")).toHaveText("Office added.");
  await expect(page.locator("[data-ws-room='room-use:unassigned-level:office'] .ws-chip")).toHaveText("Edited by you");
  expect(posts.find(([kind]) => kind === "geometry")[1]).toMatchObject({action: "add_room", label: "Office", taxonomy_id: "office", page: 20});
  expect(posts.find(([kind]) => kind === "area")[1]).toMatchObject({room_id: "room-use:unassigned-level:office", area_m2: 10});
});

test("Calculate runs one server job with the person's room choices, shows its steps, and opens the plain result", async ({ page }) => {
  let calculated = false;
  let job = {status: "none"};
  let polls = 0;
  const posts = await mockJob(page, {
    status: () => calculated ? baseStatus({total_kw: 34.1354, tabs: {...baseStatus().tabs, results: tab("done")}}) : baseStatus(),
    model: () => calculated ? {room_scope: {...scope, status: "confirmed"}, hourly_ai_preliminary_load_report: report} : {room_scope: scope},
    calculation: kind => {
      if (kind === "start") job = {status: "running", step: "model", step_label: "Building the model from your rooms"};
      else if (job.status === "running" && ++polls >= 2) { job = {status: "done", total_kw: 34.1354}; calculated = true; }
      return job;
    },
  });
  await page.goto("/#/job/job-1/rooms");
  await page.locator("[data-ws-room='room-use:unassigned-level:shop'] [data-ws-include]").uncheck();
  await page.locator("[data-ws-room='room-use:unassigned-level:shop'] [data-ws-include]").check();
  await page.locator("#wsCalculate").click();
  await expect(page.locator("#wsCalcNote")).toContainText("Building the model from your rooms…");
  await expect(page.locator("#wsCalcNote")).toContainText("You can leave this page; it carries on.");
  await expect(page.locator("#wsCalculate")).toBeDisabled();
  await expect(page).toHaveURL(/#\/job\/job-1\/results$/, {timeout: 15000});
  await expect(page.locator("#wsCalculate")).toBeEnabled();
  await expect(page.locator("[data-ws-total]")).toHaveText("34.1");
  await expect(page.locator("#wsTotal")).toHaveText("34.1");
  const starts = posts.filter(([kind]) => kind === "calculation");
  expect(starts).toHaveLength(1);
  expect(starts[0][1]).toEqual({project_id: "job-1", reviewer: "Contractor",
    include: {"room-use:unassigned-level:shop": true}});
  expect(posts.filter(([kind]) => kind === "model")).toHaveLength(0);   // the browser no longer drives the steps
  await expect(page.locator("[data-ws-room-loads]")).toContainText("Kitchen");
  await expect(page.locator("[data-ws-room-loads]")).toContainText("12.7 kW");
  const excluded = page.locator("[data-ws-excluded]");
  await expect(excluded).toContainText("Exhaust and make-up air");
  await expect(excluded).toContainText("Air leakage through doors and gaps");
  await expect(excluded).not.toContainText("Internal boundary");
  await expect(excluded).toContainText("Coolroom — refrigeration, sized separately");
  await expect(excluded.locator("li", {hasText: "Freezer — refrigeration"})).toHaveCount(1);
  await expect(excluded).toContainText("Walls (no outline: area only) — Kitchen");
  await expect(excluded).toContainText("Walls (not classified yet) — Shop");
  await expect(excluded.locator("li", {hasText: /^Walls, roof and glazing/})).toHaveCount(0);
});

test("Calculate: reopening a job mid-calculation shows the progress; a failure shows its reason", async ({ page }) => {
  let job = {status: "running", step: "inputs", step_label: "Preparing the inputs (room uses, heights, people and equipment)"};
  const posts = await mockJob(page, {status: () => baseStatus(), calculation: () => job});
  await page.goto("/#/job/job-1/project");
  await expect(page.locator("#wsCalcNote")).toContainText("Preparing the inputs (room uses, heights, people and equipment)…");
  await expect(page.locator("#wsCalculate")).toBeDisabled();
  job = {status: "failed", step: "rooms", error: "No room has an area yet. Add room areas on the Rooms tab, then calculate."};
  await expect(page.locator("#wsCalcNote")).toHaveText("Could not calculate: No room has an area yet. Add room areas on the Rooms tab, then calculate.", {timeout: 10000});
  await expect(page.locator("#wsCalculate")).toBeEnabled();
  expect(posts.filter(([kind]) => kind === "calculation")).toHaveLength(0);
});

test("Calculate without any room area sends the contractor to Rooms instead of failing", async ({ page }) => {
  const posts = await mockJob(page, {status: () => baseStatus({rooms: {total: 2, included: 0, with_area: 0}, tabs: {...baseStatus().tabs, rooms: tab("needed", "No room has an area yet.")}})});
  await page.goto("/#/job/job-1/project");
  await page.locator("#wsCalculate").click();
  await expect(page.locator("#wsCalcNote")).toHaveText("Add room areas first.");
  await expect(page).toHaveURL(/#\/job\/job-1\/rooms$/);
  expect(posts.filter(([kind]) => kind === "model" || kind === "calculation")).toHaveLength(0);
});

test("Engineer review opens the full screen; Back to the job returns", async ({ page }) => {
  await mockJob(page, {status: () => baseStatus()});
  await openJob(page);
  await expect(page.locator("#vJob")).toBeVisible();
  await page.getByRole("button", {name: "Engineer review"}).click();
  await expect(page.locator("#vRes")).toBeVisible();
  await expect(page.locator("aside.side")).toBeVisible();
  await page.getByRole("button", {name: "Back to the job"}).click();
  await expect(page.locator("#vJob")).toBeVisible();
});

test("?engineer=1 opens projects on the engineer screen", async ({ page }) => {
  await mockJob(page, {status: () => baseStatus()});
  await openJob(page, "/?engineer=1");
  await expect(page.locator("#vRes")).toBeVisible();
  await expect(page.locator("#vJob")).toBeHidden();
});

test("on a phone the rail becomes a section menu and Calculate stays at hand", async ({ page }) => {
  await page.setViewportSize({width: 375, height: 812});
  await mockJob(page, {status: () => baseStatus()});
  await page.goto("/#/job/job-1/rooms");
  await expect(page.locator("[data-ws-rooms]")).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(375);  // the rooms table scrolls in its own box
  await expect(page.locator("[data-ws-rooms] th.ws-col-level")).toBeHidden();  // no room has a level
  await expect(page.locator("#wsTabs")).toBeHidden();
  await expect(page.locator("#wsSectionSelect")).toBeVisible();
  await page.locator("#wsSectionSelect").selectOption("project");
  await expect(page).toHaveURL(/#\/job\/job-1\/project\/job-site$/);
  await expect(page.locator("#wsCalculate")).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(375);
});

test("AI step fits a phone: the prompt wraps and the images shrink", async ({ page }) => {
  await page.setViewportSize({width: 375, height: 812});
  const long = JSON.stringify(Array.from({length: 40}, (_, index) => ({page: index, text: "TENANCY_MZ01_M38_MELROSE_CENTRAL_" + "X".repeat(60)})));
  await mockJob(page, {status: () => baseStatus(), tasks: () => [{task: "P2_north", target: "page-20", status: "waiting_for_reply", prompt: long,
    images: [{name: "image-1.png", url: "/api/autonomous-tasks/image?name=image-1.png"}]}]});
  await page.goto("/?operator=1#/job/job-1/drawings");
  await page.locator("[data-ws-op-task] summary").click();
  await expect(page.locator("[data-ws-prompt]")).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(375);
});

// Clicks a point given in plan image pixels, through the real SVG transform.
async function clickPlan(page, x, y) {
  const box = await page.locator("[data-ws-plan-svg]").evaluate((svg, [px, py]) => {
    const point = svg.createSVGPoint(); point.x = px; point.y = py;
    const screen = point.matrixTransform(svg.getScreenCTM());
    return [screen.x, screen.y];
  }, [x, y]);
  await page.mouse.click(box[0], box[1]);
}

test("Measure a room: corners snap to the walls, a printed dimension sets the scale, and the saved area replaces a typed one", async ({ page }) => {
  let records = [];
  let overrides = [{room_id: "room-use:unassigned-level:kitchen", room_label: "Kitchen", area_m2: 105}];
  await page.setViewportSize({width: 1280, height: 1500});   // the whole plan on screen, so every click reaches it
  const posts = await mockJob(page, {
    status: () => baseStatus({area_overrides: overrides, traced_rooms: Object.fromEntries(records.map(row => [row.room_id, {area_m2: 23.9, source: "traced", pages: [20]}]))}),
    geometry: () => geometryContext(records),
    onPost: (kind, body) => {
      if (kind === "area") overrides = body.area_m2 == null ? [] : overrides;
      if (kind !== "geometry") return null;
      records = [{room_id: body.room_id, room_label: "Kitchen", page: body.page, points_image_px: body.points_image_px,
                  calibration: {status: "agreed", mm_per_px: body.dimension_value_mm / Math.abs(body.dimension_points_image_px[1][0] - body.dimension_points_image_px[0][0]),
                                dimension_points_image_px: body.dimension_points_image_px, dimension_value_mm: body.dimension_value_mm}}];
      return {reviewer_room_geometry: {records}};
    },
  });
  await page.goto("/#/job/job-1/rooms/measurements");
  await page.locator("[data-ws-room='room-use:unassigned-level:kitchen'] [data-ws-trace]").click();
  await expect(page.locator("[data-ws-measure] h2")).toHaveText("Measure Kitchen");
  await expect(page.locator("[data-ws-measure-page]")).toHaveValue("20");             // the main plan, not the page with the name
  await expect(page.locator("[data-ws-name-pages]")).toContainText("Kitchen's name is printed on page 21");
  const top = async () => (await page.locator("[data-ws-plan-svg]").boundingBox()).y;
  const top0 = await top();
  // Corners: each click within the snap tolerance lands on the wall corner.
  await clickPlan(page, 103, 97);
  await clickPlan(page, 497, 104);
  await clickPlan(page, 504, 396);
  await expect(page.locator("[data-ws-close]")).toBeVisible();
  expect(await top()).toBe(top0);                                                       // the plan doesn't move as the steps change
  await clickPlan(page, 300, 250);                                                      // a stray click, then Undo
  await page.locator("[data-ws-undo]").click();
  await clickPlan(page, 96, 402);
  await clickPlan(page, 101, 101);                                                      // the first corner again closes the outline
  await expect(page.locator("[data-ws-step]")).toHaveAttribute("data-ws-step", "scale");
  await expect(page.locator(".ws-measure-progress li").first()).toHaveClass(/is-done/);
  // Scale: a dimension that disagrees with 1:100 is refused and a second one is asked for.
  await clickPlan(page, 100, 600);
  await clickPlan(page, 500, 600);
  await page.locator("[data-ws-dim-mm]").fill("6000");
  await page.locator("[data-ws-dim-mm]").press("Tab");
  await expect(page.locator("[data-ws-step] .ws-banner.is-warn")).toContainText("off the 1:100 scale");
  await expect(page.locator("[data-ws-measure-save]")).toHaveCount(0);
  await page.locator("[data-ws-dim-clear='dim']").click();
  await clickPlan(page, 100, 600);
  await clickPlan(page, 500, 600);
  await page.locator("[data-ws-dim-mm]").fill(String(Math.round(400 * MM_PER_PX)));
  await page.locator("[data-ws-dim-mm]").press("Tab");
  await expect(page.locator("[data-ws-step]")).toHaveAttribute("data-ws-step", "save");
  await expect(page.locator("[data-ws-measure-area] span")).toHaveText((400 * 300 * MM_PER_PX ** 2 / 1e6).toFixed(1));
  await expect(page.locator("[data-ws-step]")).toContainText("This replaces the 105 m² you typed.");
  await page.locator("[data-ws-measure-name]").fill("");
  await page.locator("[data-ws-measure-save]").click();
  await expect(page.locator("[data-ws-measure-error]")).toContainText("Type your name");
  await page.locator("[data-ws-measure-name]").fill("Sam");
  await page.locator("[data-ws-measure-save]").click();
  await expect(page.locator("[data-ws-rooms]")).toBeVisible();
  await expect(page.locator("[data-ws-rooms-status]")).toContainText("Kitchen measured:");
  await expect(page.locator("[data-ws-rooms-status]")).toContainText("(replaces the 105 m² you typed)");
  const saved = posts.find(([kind, body]) => kind === "geometry" && body.action === "save")[1];
  expect(saved).toMatchObject({project_id: "job-1", room_id: "room-use:unassigned-level:kitchen", page: 20, reviewer: "Sam",
    snapped_line_ids: ["top", "right", "bottom", "left", "top"], source_pdf_fingerprint: "pdf-fp", vector_page_fingerprint: "page-fp",
    dimension_value_mm: Math.round(400 * MM_PER_PX)});
  expect(saved.points_image_px).toEqual([[100, 100], [500, 100], [500, 400], [100, 400], [100, 100]]);
  expect(posts.find(([kind]) => kind === "area")[1]).toMatchObject({room_id: "room-use:unassigned-level:kitchen", area_m2: null});
  await expect(page.locator("[data-ws-room='room-use:unassigned-level:kitchen'] .ws-chip")).toHaveText("Measured on the plan");
});

test("Measure a room: a scale set on the page for another room can be reused, and Back returns to the list", async ({ page }) => {
  const other = {room_id: "room-use:unassigned-level:shop", room_label: "Shop", page: 20, points_image_px: [[100, 100], [500, 100], [500, 400], [100, 400], [100, 100]],
                 calibration: {status: "agreed", mm_per_px: MM_PER_PX, dimension_points_image_px: [[100, 600], [500, 600]], dimension_value_mm: 400 * MM_PER_PX}};
  await page.setViewportSize({width: 1280, height: 1500});
  const posts = await mockJob(page, {status: () => baseStatus(), geometry: () => geometryContext([other])});
  await page.goto("/#/job/job-1/rooms/measurements");
  await page.locator("[data-ws-room='room-use:unassigned-level:kitchen'] [data-ws-trace]").click();
  for (const [x, y] of [[100, 100], [500, 100], [500, 400], [100, 400]]) await clickPlan(page, x, y);
  await page.locator("[data-ws-close]").click();
  await expect(page.locator("[data-ws-step]")).toContainText("when Shop was measured");
  await page.getByRole("button", {name: "Use it"}).click();
  await expect(page.locator("[data-ws-step]")).toHaveAttribute("data-ws-step", "save");
  await expect(page.locator("[data-ws-step]")).toContainText("set when Shop was measured");
  await page.locator("[data-ws-measure-back]").click();
  await expect(page.locator("[data-ws-rooms]")).toBeVisible();
  expect(posts.filter(([kind]) => kind === "geometry")).toHaveLength(0);
});

function envelopeFixture() {
  const roomId = "room-use:unassigned-level:shop";
  const trace = {trace_id: "shop-trace", room_id: roomId, room_label: "Shop", page: 20, freshness: "current",
    points_image_px: [[100,100],[500,100],[500,400],[100,400],[100,100]],
    calibration: {status: "agreed", mm_per_px: MM_PER_PX},
    edges: [{index: 0, boundary: "external"}, {index: 1, boundary: "unknown"}, {index: 2, boundary: "internal"}, {index: 3, boundary: "unknown"}],
    edge_sources: {"0": "ai_determined"}, roof: "unknown", roof_source: "", openings: [], openings_none_edges: []};
  const context = geometryContext([trace]);
  context.pages = [{...PLAN_PAGE, preview_url: "/api/artifact?plan.png"}];
  context.glazing_choices = {retail: {label: "Preliminary single glazing"}};
  context.shading_categories = {unshaded: {label: "No external shade"}};
  return {roomId, trace, context};
}

test("Walls & roof: show the plan, accept an AI wall, edit boundaries and save the roof decision", async ({page}) => {
  const {context} = envelopeFixture();
  const posts = await mockJob(page, {status: () => baseStatus({envelope: {
    [context.rooms[1].room_id]: {edges: [{trace_id: "shop-trace", index: 0, length_m: 4.0}]},
  }}), geometry: () => context});
  await page.goto("/#/job/job-1/walls");
  await expect(page.locator("[data-ws-walls]")).toBeVisible();
  await expect(page.locator(".ws-envelope-plan img")).toBeVisible();
  await expect(page.getByRole("row", {name: /Wall 1/})).toContainText("AI-determined");
  await expect(page.getByRole("row", {name: /Wall 1/})).toContainText("4.00 m");
  await page.getByRole("button", {name: "Accept AI"}).click();
  await page.locator('[data-ws-boundary="1"]').selectOption("adjacent_tenancy");
  await page.locator(".ws-subtab").filter({hasText: "Roof"}).click();
  await expect(page.locator("[data-ws-wall-room]")).toHaveValue(context.rooms[1].room_id);
  await page.locator("[data-ws-roof]").selectOption("not_exposed");
  await page.locator("[data-ws-envelope-reviewer]").fill("Sam");
  await page.getByRole("button", {name: "Save walls & roof"}).click();
  await expect.poll(() => posts.some(([kind, body]) => kind === "geometry" && body.action === "classify_envelope")).toBeTruthy();
  const body = posts.find(([kind, row]) => kind === "geometry" && row.action === "classify_envelope")[1];
  expect(body).toMatchObject({trace_id: "shop-trace", reviewer: "Sam", roof: "not_exposed", confirm_roof: true,
    confirmed_edges: [0], openings: [], openings_none_edges: []});
  expect(body.edges).toContainEqual({index: 1, boundary: "adjacent_tenancy"});
  // The backend keeps unchanged wall sources and marks only changed boundaries as reviewer decisions.
  expect(body.edges).toEqual([{index: 0, boundary: "external"}, {index: 1, boundary: "adjacent_tenancy"},
    {index: 2, boundary: "internal"}, {index: 3, boundary: "unknown"}]);
  await page.setViewportSize({width: 375, height: 812});
  await expect(page.locator("#wsSectionSelect")).toBeVisible();
  await expect(page.locator('#wsSectionSelect option[value="walls"]')).toContainText("Walls & roof");
  const widths = await page.evaluate(() => ({body: document.body.scrollWidth, viewport: window.innerWidth}));
  expect(widths.body).toBeLessThanOrEqual(widths.viewport);
});

test("Walls & roof: area-only rooms open the existing measure view", async ({page}) => {
  const {context} = envelopeFixture();
  const posts = await mockJob(page, {status: () => baseStatus(), geometry: () => context});
  await page.goto("/#/job/job-1/walls");
  await expect(page.locator(".ws-area-only")).toContainText("Kitchen");
  await page.getByRole("button", {name: "Measure on the plan"}).click();
  await expect(page.locator("[data-ws-measure]")).toBeVisible();
  await expect(page.locator("[data-ws-measure]")).toContainText("Kitchen");
  expect(posts.filter(([kind]) => kind === "geometry")).toHaveLength(0);
});

test("Windows: mark a wall no-glazing and add an opening through classify_envelope", async ({page}) => {
  const {context, trace} = envelopeFixture();
  trace.edges[1].boundary = "mall";
  const posts = await mockJob(page, {status: () => baseStatus(), geometry: () => context});
  await page.goto("/#/job/job-1/windows");
  await expect(page.locator("[data-ws-windows]")).toBeVisible();
  await page.locator('[data-ws-no-glazing="1"]').check();
  await page.getByRole("button", {name: "Add opening"}).click();
  await page.locator('[data-opening-field="width_m"]').fill("2.4");
  await page.locator('[data-opening-field="sill_height_m"]').fill("0.9");
  await page.locator('[data-opening-field="head_height_m"]').fill("2.1");
  await page.locator('[data-opening-field="elevation_page"]').fill("26");
  await page.locator("[data-ws-window-reviewer]").fill("Sam");
  await page.getByRole("button", {name: "Save windows"}).click();
  await expect.poll(() => posts.some(([kind, body]) => kind === "geometry" && body.action === "classify_envelope")).toBeTruthy();
  const body = posts.find(([kind, row]) => kind === "geometry" && row.action === "classify_envelope")[1];
  expect(body.openings_none_edges).toEqual([1]);
  expect(body.openings[0]).toMatchObject({edge_index: 0, width_m: 2.4, sill_height_m: 0.9, head_height_m: 2.1, elevation_page: 26});
  expect(body.edges).toEqual(trace.edges);
});

test("Windows: removing one opening then adding another uses a free ID", async ({page}) => {
  const {context, trace} = envelopeFixture();
  trace.edges[1].boundary = "mall";
  trace.openings = [
    {opening_id: "Opening 1", edge_index: 0, width_m: 1, sill_height_m: 0, head_height_m: 1,
      elevation_page: 26, glazing_choice: "retail", shading_category: "unshaded"},
    {opening_id: "Opening 2", edge_index: 1, width_m: 1, sill_height_m: 0, head_height_m: 1,
      elevation_page: 26, glazing_choice: "retail", shading_category: "unshaded"},
  ];
  const posts = await mockJob(page, {status: () => baseStatus(), geometry: () => context});
  await page.goto("/#/job/job-1/windows");
  await page.getByRole("button", {name: "Remove opening"}).nth(1).click();
  await page.getByRole("button", {name: "Add opening"}).click();
  await expect(page.locator(".ws-opening legend").nth(1)).toContainText("Opening 2");
  await page.locator('[data-opening-field="width_m"][data-index="1"]').fill("1.2");
  await page.locator('[data-opening-field="sill_height_m"][data-index="1"]').fill("0");
  await page.locator('[data-opening-field="head_height_m"][data-index="1"]').fill("1.2");
  await page.locator('[data-opening-field="elevation_page"][data-index="1"]').fill("26");
  await page.locator("[data-ws-window-reviewer]").fill("Sam");
  await page.getByRole("button", {name: "Save windows"}).click();
  await expect.poll(() => posts.some(([kind]) => kind === "geometry")).toBeTruthy();
  const openings = posts.find(([kind]) => kind === "geometry")[1].openings;
  expect(openings.map(row => row.opening_id)).toEqual(["Opening 1", "Opening 2"]);
  expect(new Set(openings.map(row => row.opening_id)).size).toBe(2);
});

test("Windows: edit and remove existing openings, preserve the unchanged wall and roof decisions", async ({page}) => {
  const {context, trace} = envelopeFixture();
  trace.edges[1].boundary = "mall";
  trace.roof = "not_exposed";
  trace.roof_source = "reviewer";
  trace.openings = [{opening_id: "shopfront", edge_index: 0, width_m: 2.1, sill_height_m: 0.9, head_height_m: 2.4,
    elevation_page: 26, glazing_choice: "retail", shading_category: "unshaded", declaration_source: "ai_determined"},
    {opening_id: "remove-me", edge_index: 1, width_m: 1.2, sill_height_m: 0, head_height_m: 2.1,
    elevation_page: 26, glazing_choice: "retail", shading_category: "unshaded", declaration_source: "reviewer"}];
  const posts = await mockJob(page, {status: () => baseStatus(), geometry: () => context});
  await page.goto("/#/job/job-1/windows");
  await page.locator('[data-opening-field="width_m"][data-index="0"]').fill("2.25");
  await page.locator('[data-opening-field="head_height_m"][data-index="0"]').fill("2.5");
  await page.getByRole("button", {name: "Remove opening"}).nth(1).click();
  await page.locator("[data-ws-window-reviewer]").fill("Sam");
  await page.getByRole("button", {name: "Save windows"}).click();
  await expect.poll(() => posts.some(([kind]) => kind === "geometry")).toBeTruthy();
  const body = posts.find(([kind]) => kind === "geometry")[1];
  expect(body.openings).toHaveLength(1);
  expect(body.openings[0]).toMatchObject({opening_id: "shopfront", width_m: 2.25, head_height_m: 2.5});
  expect(body).toMatchObject({roof: "not_exposed", edges: trace.edges, openings_none_edges: []});
});

test("Windows: server validation text is shown verbatim with a Rooms link for missing ceiling height", async ({page}) => {
  const {context, trace} = envelopeFixture();
  trace.edges[1].boundary = "mall";
  trace.openings = [{opening_id: "shopfront", edge_index: 0, width_m: 2, sill_height_m: 0.9, head_height_m: 2.4,
    elevation_page: 26, glazing_choice: "retail", shading_category: "unshaded"}];
  const posts = await mockJob(page, {status: () => baseStatus(), geometry: () => context,
    onPost: kind => kind === "geometry" ? {httpStatus: 400, message: "Missing ceiling height for Shop."} : null});
  await page.goto("/#/job/job-1/windows");
  await page.locator("[data-ws-window-reviewer]").fill("Sam");
  await page.getByRole("button", {name: "Save windows"}).click();
  await expect(page.getByRole("alert")).toContainText("Missing ceiling height for Shop.");
  await page.getByRole("button", {name: "Set ceiling height on Rooms"}).click();
  await expect(page.locator("[data-ws-rooms]")).toBeVisible();
  expect(posts.filter(([kind]) => kind === "geometry")).toHaveLength(1);
});

test("Workspace: after an envelope save, tab badges refresh and the previous result is marked stale", async ({page}) => {
  const {context} = envelopeFixture();
  const trace = context.reviewer_room_geometry.records[0];
  let saved = false;
  const posts = await mockJob(page, {status: () => baseStatus({result_stale: saved, total_kw: 34.1,
    tabs: {...baseStatus().tabs, walls: tab(saved ? "done" : "needed"), windows: tab(saved ? "check" : "todo"),
      results: tab("done", saved ? "Out of date — calculate again." : "")},
    subtabs: {...baseStatus().subtabs, walls: {walls: tab(saved ? "done" : "needed", "", saved ? 0 : 2),
      roof: tab(saved ? "done" : "needed", "", saved ? 0 : 1)}}}), geometry: () => context,
    model: () => ({room_scope: {...scope, status: "confirmed"}, hourly_ai_preliminary_load_report: report}),
    onPost: kind => { if (kind === "geometry") {
      saved = true;
      trace.edges.forEach(edge => { if (edge.boundary === "unknown") edge.boundary = "internal"; });
      trace.roof = "not_exposed"; trace.roof_source = "reviewer";
    } return null; }});
  await page.goto("/#/job/job-1/walls");
  await page.locator("[data-ws-envelope-reviewer]").fill("Sam");
  await page.getByRole("button", {name: "Save walls & roof"}).click();
  await expect(page.locator("[data-ws-tab='walls'] .ws-tab-icon")).toHaveText("✓");
  await expect(page.locator("[data-ws-tab='windows'] .ws-tab-icon")).toHaveText("●");
  await page.locator("[data-ws-tab='results']").click();
  await expect(page.locator("[data-ws-results]")).toContainText("Inputs changed since this result");
  expect(posts.filter(([kind]) => kind === "geometry")).toHaveLength(1);
});

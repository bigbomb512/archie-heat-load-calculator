import { expect, test } from "@playwright/test";

// The contractor view is the default project screen; the engineer screen stays behind "Engineer tools".
const analysis = {
  id: "job-1", name: "Corner cafe.pdf", pages_analysed: 3, relevant_count: 1, selected_count: 1, warnings: [],
  has_reasoning_packet: true,
  sheets: [{page: 1, type: "floor_plan", title: "Floor Plan", relevant: true, selected_by_default: true, plan_role: "main_floor_plan",
            confidence: 0.9, reason: "", review_bucket: "primary", scale: "1:100", visual: {}, thumbnail: ""}],
};

const report = {
  label: "AI preliminary estimate — not engineering reviewed or validated",
  final_design_total_kw: 34.1354,
  included_scope_peak: {design_total_kw: 34.1354, display_hour: 14, components: {
    people: {total_kw: 8.44}, lighting: {total_kw: 4.07}, equipment_refrigeration: {total_kw: 3.23},
    envelope: {total_kw: 0}, outside_air: {total_kw: 15.29}}},
  safety_factor: 1.1,
  room_names: [{room_id: "r-kitchen", name: "Kitchen"}, {room_id: "r-shop", name: "Shop"}],
  room_peaks: [{room_id: "r-kitchen", name: "Kitchen", design_total_kw: 12.7031}, {room_id: "r-shop", name: "Shop", design_total_kw: 14.2681}],
  confirmed_rooms: [{label: "Kitchen", area_m2: 104.1, area_origin: "ai_determined"}, {label: "Shop", area_m2: 217.3, area_origin: "ai_determined"}],
  known_exclusions: [
    {room_id: "r-kitchen", component_type: "extract_air", component: "Extract air"},
    {room_id: "r-shop", component_type: "envelope", component_id: "boundary_edge_3", component: "Internal boundary"},
  ],
  unresolved_room_inputs: [{room_id: "r-kitchen", component_type: "infiltration"}, {room_id: "r-shop", component_type: "infiltration"}],
  refrigeration_process_exclusions: [{room_name: "Coolroom", reason: "Refrigeration load required."}],
  design_conditions_basis: {design_day: {label: "Generic Australian cooling design day"}},
};

const fingerprint = tasks => tasks.map(row => `${row.task}:${row.target}:${row.status}`).sort().join("|");

async function mockJob(page, {tasks, model, onPost = () => null}) {
  await page.route("**/api/test-mode/status", route => route.fulfill({json: {enabled: false}}));
  await page.route("**/api/projects", route => route.fulfill({json: [{id: "job-1", name: analysis.name, pages: 3, relevant: 1, analysed: true}]}));
  await page.route("**/api/analysis?id=job-1", route => route.fulfill({json: analysis}));
  await page.route("**/api/autonomous-tasks**", route => {
    if (route.request().method() === "POST") {
      const body = route.request().postDataJSON();
      const reply = onPost("tasks", body);
      return route.fulfill({json: reply || {id: "job-1", tasks: tasks()}});
    }
    return route.fulfill({json: {id: "job-1", tasks: tasks()}});
  });
  await page.route("**/api/ai-preliminary-model**", route => {
    if (route.request().method() === "POST") {
      const body = route.request().postDataJSON();
      return route.fulfill({json: onPost("model", body) || model()});
    }
    return route.fulfill({json: model()});
  });
}

async function openJob(page, resolvedTasks) {
  await page.addInitScript(([key, value]) => { try { localStorage.setItem(key, value); } catch (_) {} },
    ["toki.contractor.resolved.job-1", fingerprint(resolvedTasks)]);
  await page.goto("/");
  await page.locator("[data-open='job-1']").click();
}

test("a finished job opens straight to the plain result, not the engineer screen", async ({ page }) => {
  const tasks = [{task: "P0_room_names", target: "page-20", status: "below_accuracy_bar"}];
  await mockJob(page, {tasks: () => tasks, model: () => ({room_scope: {status: "confirmed", candidates: [{key: "r-kitchen", label: "Kitchen"}]},
                                                              hourly_ai_preliminary_load_report: report})});
  await openJob(page, tasks);
  await expect(page.locator("#vJob")).toBeVisible();
  await expect(page.locator("#vRes")).toBeHidden();
  await expect(page.locator("[data-job-total]")).toHaveText("34.1");
  await expect(page.locator("[data-job-room-loads]")).toContainText("Kitchen");
  await expect(page.locator("[data-job-room-loads]")).toContainText("12.7 kW");
  await expect(page.locator("[data-job-room-loads]")).toContainText("122");
  await expect(page.locator("[data-job-included]")).toContainText("Fresh air");
  const excluded = page.locator("[data-job-excluded]");
  await expect(excluded).toContainText("Exhaust and make-up air");
  await expect(excluded).toContainText("Air leakage through doors and gaps");
  await expect(excluded).toContainText("Coolroom — refrigeration, sized separately");
  await expect(excluded).not.toContainText("Internal boundary");
  await expect(excluded).not.toContainText("(Kitchen, Shop)");
  await expect(page.locator("[data-job-step='result']")).toHaveAttribute("aria-current", "step");
  await expect(page.getByText("Prepare Codex handoff")).toBeHidden();

  const download = page.waitForEvent("download");
  await page.getByRole("button", {name: "Download room loads (CSV)"}).click();
  const file = await download;
  expect(file.suggestedFilename()).toBe("Corner cafe - cooling loads.csv");
  const csv = await (await import("node:fs/promises")).readFile(await file.path(), "utf8");
  expect(csv).toContain('"Kitchen","104.1","12.7","122","Measured from the drawings by AI"');
  expect(csv).toContain('"Total (draft)","","34.1","",""');
});

test("while the drawing check is running the contractor sees progress and can continue with what's ready", async ({ page }) => {
  const tasks = [{task: "P0_dimensions", target: "page-20-dimension-1", status: "applied"},
                 {task: "P1_site", target: "project", status: "waiting_for_reply"}];
  await mockJob(page, {tasks: () => tasks, model: () => ({room_scope: {status: "draft", uses: {office: "Office"},
    candidates: [{key: "r-shop", label: "Shop", area_m2: 216.1, area_origin: "ai_determined", include: true, status: "ok"}]}})});
  await openJob(page, tasks);
  await expect(page.locator("[data-job-reading]")).toContainText("Checking your drawings");
  await expect(page.locator("[data-job-reading]")).toContainText("1 of 2 checks done");
  await expect(page.locator("[data-job-step='read']")).toHaveAttribute("aria-current", "step");
  await page.getByRole("button", {name: "Continue with what's ready"}).click();
  await expect(page.locator("[data-job-rooms]")).toContainText("Shop");
  await expect(page.locator("[data-job-rooms]")).toContainText("216.1 m²");
  await expect(page.locator("[data-job-step='check']")).toHaveAttribute("aria-current", "step");
});

test("the contractor answers the roof question, confirms the rooms and gets the result", async ({ page }) => {
  let answered = false;
  const posts = [];
  const tasks = () => [{task: "P0_room_names", target: "page-20", status: "below_accuracy_bar"},
                       {task: "P5_roof", target: "room-use:shop", room_label: "Shop", status: answered ? "applied" : "needs_contractor_answer"}];
  const scope = {status: "draft", candidate_fingerprint: "fp-1",
                 candidates: [{key: "r-shop", label: "Shop", area_m2: 216.1, area_origin: "ai_determined", include: true, status: "ok"},
                              {key: "r-store", label: "Store", area_m2: 6, area_origin: "ai_determined", include: true, status: "ok"}]};
  await mockJob(page, {tasks, model: () => ({room_scope: scope}), onPost: (kind, body) => {
    posts.push([kind, body]);
    if (kind === "tasks" && body.action === "answer_roof") { answered = true; return null; }
    if (kind === "model" && body.action === "calculate") return {room_scope: {...scope, status: "confirmed"}, hourly_ai_preliminary_load_report: report};
    return null;
  }});
  answered = true;
  const afterAnswer = tasks();
  answered = false;
  await openJob(page, afterAnswer);
  const question = page.locator("[data-job-question='room-use:shop']");
  await expect(question).toContainText("Shop: is there a floor or another tenancy above it, or is it the roof?");
  await question.getByLabel("Floor or tenancy above").check();
  await page.getByRole("button", {name: "Save answers"}).click();
  await expect(page.locator("[data-job-questions]")).toHaveCount(0);
  expect(posts.find(([kind, body]) => kind === "tasks" && body.action === "answer_roof")[1])
    .toMatchObject({task: "P5_roof", target: "room-use:shop", answer: "floor_tenancy_above"});

  await page.getByRole("button", {name: "Calculate cooling load"}).click();
  await expect(page.locator("[data-job-status]")).toContainText("Enter your name first");
  await page.locator("[data-job-room='r-store'] [data-job-include]").uncheck();
  await page.locator("[data-job-name]").fill("Sam");
  await page.getByRole("button", {name: "Calculate cooling load"}).click();
  await expect(page.locator("[data-job-total]")).toHaveText("34.1");
  const confirm = posts.find(([kind, body]) => kind === "model" && body.action === "confirm_room_scope")[1];
  expect(confirm).toMatchObject({reviewer: "Sam", candidate_fingerprint: "fp-1",
    rows: [{key: "r-shop", include: true, reason: ""}, {key: "r-store", include: false, reason: "Not cooled (contractor)"}]});
  expect(posts.some(([kind, body]) => kind === "model" && body.action === "calculate")).toBe(true);
});

test("engineer tools open the full screen and the contractor can come back", async ({ page }) => {
  const tasks = [{task: "P0_room_names", target: "page-20", status: "below_accuracy_bar"}];
  await mockJob(page, {tasks: () => tasks, model: () => ({room_scope: {status: "confirmed", candidates: [{key: "r-kitchen", label: "Kitchen"}]},
                                                              hourly_ai_preliminary_load_report: report})});
  await openJob(page, tasks);
  await expect(page.locator("[data-job-total]")).toHaveText("34.1");
  await page.getByRole("button", {name: "Engineer tools"}).click();
  await expect(page.locator("#vRes")).toBeVisible();
  await expect(page.locator("#vJob")).toBeHidden();
  await expect(page.locator("#btnContinue")).toHaveText("Drawings confirmed");
  await page.getByRole("button", {name: "Back to the simple view"}).click();
  await expect(page.locator("#vJob")).toBeVisible();
  await expect(page.locator("[data-job-total]")).toHaveText("34.1");
});

test("?engineer=1 opens projects on the engineer screen", async ({ page }) => {
  const tasks = [];
  await mockJob(page, {tasks: () => tasks, model: () => ({})});
  await page.goto("/?engineer=1");
  await page.locator("[data-open='job-1']").click();
  await expect(page.locator("#vRes")).toBeVisible();
  await expect(page.locator("#vJob")).toBeHidden();
});

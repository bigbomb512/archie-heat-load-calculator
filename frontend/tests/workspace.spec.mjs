import { expect, test } from "@playwright/test";

// Job workspace (docs/UI_REBUILD_PLAN.md phase 1): left rail of tabs, one focused page, Calculate always visible.
const analysis = {
  id: "job-1", name: "Corner cafe.pdf", pages_analysed: 38, relevant_count: 3, selected_count: 3, warnings: [],
  has_reasoning_packet: true,
  sheets: [{page: 20, type: "floor_plan", title: "Dimension Plan", relevant: true, selected_by_default: true, plan_role: "main_floor_plan",
            confidence: 0.9, reason: "", review_bucket: "primary", scale: "1:100", visual: {}, thumbnail: ""}],
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
                           {room_id: "r-k", component_id: "area_only_walls", component_type: "envelope"}, {room_id: "r-k", component_type: "envelope"}],
  refrigeration_process_exclusions: [{room_name: "Coolroom"}, {room_name: "Freezer", level: "Unassigned level"}, {room_name: "Freezer", level: "Unassigned level"}],
  design_conditions_basis: {design_day: {label: "Generic"}},
};

function tab(state, detail = "") { return {state, detail}; }

function baseStatus(over = {}) {
  return {id: "job-1", name: "Corner cafe", address: "", found_site: "TENANCY 7, CENTRAL MALL", building_type: "", above: "",
          total_kw: null, result_stale: false, drawings_confirmed: true, checks: {total: 11, waiting: 0, blocked: 0, marker: "m1"},
          rooms: {total: 2, included: 1, with_area: 1}, area_overrides: [],
          tabs: {project: tab("needed", "Add the site address and what's above the tenancy."), drawings: tab("done"),
                 rooms: tab("check", "1 of 2 rooms have an area"), results: tab("todo", "Not calculated yet.")}, ...over};
}

const scope = {status: "not_confirmed", candidate_fingerprint: "fp", uses: {office: "Office", kitchen: "Kitchen"},
  candidates: [{key: "room-use:unassigned-level:kitchen", label: "Kitchen", level: "Unassigned level", area_m2: null, area_origin: "", include: false, status: "no_area", source_pages: [20]},
               {key: "room-use:unassigned-level:shop", label: "Shop", level: "Unassigned level", area_m2: 216.1, area_origin: "ai_determined", include: true, status: "calculated", source_pages: [20]}]};

async function mockJob(page, {status, model = () => ({room_scope: scope}), onPost = () => null}) {
  const posts = [];
  await page.route("**/api/test-mode/status", route => route.fulfill({json: {enabled: false}}));
  await page.route("**/api/projects", route => route.fulfill({json: [{id: "job-1", name: analysis.name, pages: 38, relevant: 3, analysed: true}]}));
  await page.route("**/api/analysis?id=job-1", route => route.fulfill({json: analysis}));
  await page.route("**/api/job-status**", route => route.fulfill({json: status()}));
  const record = kind => async route => {
    const body = route.request().postDataJSON();
    posts.push([kind, body]);
    const reply = onPost(kind, body);
    return route.fulfill({json: reply || (kind === "model" ? model() : status())});
  };
  await page.route("**/api/job-setup", record("setup"));
  await page.route("**/api/room-area-override", record("area"));
  await page.route("**/api/room-height-override", record("height"));
  await page.route("**/api/reviewer-room-geometry", record("geometry"));
  await page.route("**/api/autonomous-tasks", record("tasks"));
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
  await expect(page.locator(".ws-tab")).toHaveText([/Project.*Add the site address/s, /Drawings/, /Rooms.*1 of 2 rooms have an area/s, /Results.*Not calculated yet/s]);
  await expect(page.locator("[data-ws-tab='project'] .ws-tab-icon")).toHaveText("!");
  await expect(page.locator("#jobTitle")).toHaveText("Corner cafe");
  await expect(page.locator("#jobAddress")).toHaveText("TENANCY 7, CENTRAL MALL");
  await expect(page.locator("#wsTotal")).toHaveText("—");
  await expect(page).toHaveURL(/#\/job\/job-1\/rooms$/);   // first tab that needs something, after Project
  await expect(page.locator("[data-ws-tab='rooms']")).toHaveAttribute("aria-current", "page");
  await page.locator("[data-ws-tab='project']").click();
  await expect(page).toHaveURL(/#\/job\/job-1\/project$/);
  await expect(page.locator("[data-ws-project]")).toBeVisible();
  await page.goBack();
  await expect(page.locator("[data-ws-rooms]")).toBeVisible();
});

test("Project: the job details are saved once and the rail updates", async ({ page }) => {
  let saved = false;
  const posts = await mockJob(page, {status: () => saved ? baseStatus({address: "1 Main St, Ryde NSW 2112", above: "floor", tabs: {...baseStatus().tabs, project: tab("done")}}) : baseStatus(),
                                     onPost: kind => { if (kind === "setup") saved = true; return null; }});
  await page.goto("/#/job/job-1/project");
  await expect(page.locator("[data-ws-project]")).toBeVisible();
  await page.getByRole("button", {name: "Use this"}).click();
  await expect(page.locator("[name=address]")).toHaveValue("TENANCY 7, CENTRAL MALL");
  await page.locator("[name=address]").fill("1 Main St, Ryde NSW 2112");
  await page.locator("[name=building_type]").selectOption("food_tenancy");
  await page.getByLabel("Another floor or tenancy").check();
  await page.locator("[name=person]").fill("Sam");
  await page.getByRole("button", {name: "Save"}).click();
  await expect(page.locator("[data-ws-project-status]")).toHaveText("Saved.");
  expect(posts.find(([kind]) => kind === "setup")[1]).toMatchObject({project_id: "job-1", address: "1 Main St, Ryde NSW 2112",
    building_type: "food_tenancy", above: "floor", edited_by: "Sam"});
  await expect(page.locator("[data-ws-tab='project'] .ws-tab-icon")).toHaveText("✓");
  await expect(page.locator("#jobAddress")).toHaveText("1 Main St, Ryde NSW 2112");
});

test("Drawings: progress of the drawing check and a link for the Toki team to answer it", async ({ page }) => {
  await mockJob(page, {status: () => baseStatus({checks: {total: 11, waiting: 10, blocked: 0, marker: "m"},
                                                  tabs: {...baseStatus().tabs, drawings: tab("working", "1 of 11 checks done")}})});
  await page.goto("/#/job/job-1/drawings");
  await expect(page.locator("[data-ws-checks]")).toContainText("1 of 11 checks done.");
  await expect(page.locator("[data-ws-checks]")).toContainText("the Toki team completes this step");
  await expect(page.locator("[data-ws-operator]")).toHaveAttribute("href", "/?operator=1#/job/job-1");
  await expect(page.locator("[data-ws-tab='drawings'] .ws-tab-icon")).toHaveText("◌");
});

test("Rooms: a typed area is saved at once, shown as Edited by you, and nothing is rebuilt until Calculate", async ({ page }) => {
  let overrides = [];
  const posts = await mockJob(page, {
    status: () => baseStatus({area_overrides: overrides, rooms: {total: 2, included: overrides.length + 1, with_area: overrides.length + 1}}),
    onPost: (kind, body) => { if (kind === "area") overrides = body.area_m2 == null ? [] : [{room_id: body.room_id, room_label: body.label, area_m2: body.area_m2}]; return null; },
  });
  await page.goto("/#/job/job-1/rooms");
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
  await page.goto("/#/job/job-1/rooms");
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

test("Calculate rebuilds the model, confirms the cooled rooms and opens the plain result", async ({ page }) => {
  let calculated = false;
  const posts = await mockJob(page, {
    status: () => calculated ? baseStatus({total_kw: 34.1354, tabs: {...baseStatus().tabs, results: tab("done")}}) : baseStatus(),
    model: () => calculated ? {room_scope: {...scope, status: "confirmed"}, hourly_ai_preliminary_load_report: report} : {room_scope: scope},
    onPost: (kind, body) => { if (kind === "model" && body.action === "calculate") { calculated = true; return {room_scope: scope, hourly_ai_preliminary_load_report: report}; } return null; },
  });
  await page.goto("/#/job/job-1/rooms");
  await page.locator("[data-ws-room='room-use:unassigned-level:shop'] [data-ws-include]").uncheck();
  await page.locator("[data-ws-room='room-use:unassigned-level:shop'] [data-ws-include]").check();
  await page.locator("#wsCalculate").click();
  await expect(page).toHaveURL(/#\/job\/job-1\/results$/);
  await expect(page.locator("[data-ws-total]")).toHaveText("34.1");
  await expect(page.locator("#wsTotal")).toHaveText("34.1");
  const actions = posts.filter(([kind]) => kind === "model").map(([, body]) => body.action);
  expect(actions).toEqual(["assemble", "confirm_room_scope", "calculate"]);
  const confirm = posts.find(([kind, body]) => kind === "model" && body.action === "confirm_room_scope")[1];
  expect(confirm).toMatchObject({reviewer: "Contractor", candidate_fingerprint: "fp", rows: [
    {key: "room-use:unassigned-level:kitchen", include: false, reason: "Not cooled"},
    {key: "room-use:unassigned-level:shop", include: true, reason: ""}]});
  await expect(page.locator("[data-ws-room-loads]")).toContainText("Kitchen");
  await expect(page.locator("[data-ws-room-loads]")).toContainText("12.7 kW");
  const excluded = page.locator("[data-ws-excluded]");
  await expect(excluded).toContainText("Exhaust and make-up air");
  await expect(excluded).toContainText("Air leakage through doors and gaps");
  await expect(excluded).not.toContainText("Internal boundary");
  await expect(excluded).toContainText("Coolroom — refrigeration, sized separately");
  await expect(excluded.locator("li", {hasText: "Freezer — refrigeration"})).toHaveCount(1);
  await expect(excluded).toContainText("Walls (no outline: area only) — Kitchen");
  await expect(excluded.locator("li", {hasText: /^Walls, roof and glazing/})).toHaveCount(0);
});

test("Calculate without any room area sends the contractor to Rooms instead of failing", async ({ page }) => {
  const posts = await mockJob(page, {status: () => baseStatus({rooms: {total: 2, included: 0, with_area: 0}, tabs: {...baseStatus().tabs, rooms: tab("needed", "No room has an area yet.")}})});
  await page.goto("/#/job/job-1/project");
  await page.locator("#wsCalculate").click();
  await expect(page.locator("#wsCalcNote")).toHaveText("Add room areas first.");
  await expect(page).toHaveURL(/#\/job\/job-1\/rooms$/);
  expect(posts.filter(([kind]) => kind === "model")).toHaveLength(0);
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
  await expect(page).toHaveURL(/#\/job\/job-1\/project$/);
  await expect(page.locator("#wsCalculate")).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(375);
});

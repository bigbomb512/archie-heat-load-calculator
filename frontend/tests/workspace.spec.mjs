import { expect, test } from "@playwright/test";

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

const PNG = Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==", "base64");

async function mockJob(page, {status, model = () => ({room_scope: scope}), onPost = () => null, tasks = () => []}) {
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
  await page.route("**/api/autonomous-tasks**", async route => {
    const url = route.request().url();
    if (url.includes("/api/autonomous-tasks/image")) return route.fulfill({contentType: "image/png", body: PNG});
    if (route.request().method() === "GET") return route.fulfill({json: {tasks: tasks()}});
    return record("tasks")(route);
  });
  await page.route("**/api/decisions", record("decisions"));
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

test("Drawings: progress of the drawing check, the pages used, and a switch for the Toki team", async ({ page }) => {
  await mockJob(page, {status: () => baseStatus({checks: {total: 11, waiting: 9, blocked: 1, marker: "m"},
                                                  tabs: {...baseStatus().tabs, drawings: tab("working", "1 of 11 checks done")}}),
                       tasks: () => [{task: "P1_site", target: "project", status: "waiting_for_reply", prompt: "Find the site.", images: []}]});
  await page.goto("/#/job/job-1/drawings");
  await expect(page.locator("[data-ws-checks]")).toContainText("1 of 11 checks done.");
  await expect(page.locator("[data-ws-checks]")).toContainText("the Toki team completes this step");
  await expect(page.locator("[data-ws-checks]")).toContainText("1 more starts when earlier checks are answered.");
  await expect(page.locator("[data-ws-tab='drawings'] .ws-tab-icon")).toHaveText("◌");
  await expect(page.locator("[data-ws-operator-panel]")).toHaveCount(0);
  // Pages used: the confirmed pages, main plans marked; the rest on request.
  await expect(page.locator("[data-ws-pages] li")).toHaveCount(2);
  await expect(page.locator("[data-ws-pages] li.is-main")).toContainText("Page 20 · main plan");
  await page.getByRole("button", {name: "Show all 3 pages"}).click();
  await expect(page.locator("[data-ws-pages] li")).toHaveCount(3);
  await expect(page.locator("[data-ws-pages] li").nth(2)).toContainText("section");  // boilerplate titles fall back to the sheet type
  await page.getByRole("button", {name: "Toki team: answer the checks here"}).click();
  await expect(page.locator("[data-ws-operator-panel]")).toBeVisible();
  await expect(page.locator("#wsOpTitle")).toHaveText("Find the site address");
  await page.getByRole("button", {name: "Hide the AI step"}).click();
  await expect(page.locator("[data-ws-operator-panel]")).toHaveCount(0);
});

test("Drawings: a saved page selection is shown after reopening the job", async ({ page }) => {
  await mockJob(page, {status: () => baseStatus()});
  await page.route("**/api/analysis?id=job-1", route => route.fulfill({json: {...analysis, selected_pages: [20]}}));
  await page.goto("/#/job/job-1/drawings");
  await expect(page.locator("[data-ws-pages] li")).toHaveCount(1);
  await expect(page.locator("[data-ws-pages]")).toContainText("Page 20");
  await page.getByRole("button", {name: "Show all 3 pages"}).click();
  await expect(page.locator("[data-ws-page='27']")).not.toBeChecked();
});

test("Drawings: changing the pages used prepares them again and rebuilds the checks", async ({ page }) => {
  const posts = await mockJob(page, {status: () => baseStatus()});
  await page.goto("/#/job/job-1/drawings");
  await expect(page.locator("[data-ws-use-pages]")).toHaveCount(0);
  await page.locator("[data-ws-page='27']").uncheck();
  await expect(page.locator("[data-ws-use-pages]")).toBeVisible();
  await page.locator("[data-ws-page='27']").check();
  await expect(page.locator("[data-ws-use-pages]")).toHaveCount(0);   // back to the confirmed pages: nothing to do
  await page.locator("[data-ws-page='27']").uncheck();
  let release;
  const slow = new Promise(resolve => { release = resolve; });
  await page.route("**/api/decisions", async route => { posts.push(["decisions", route.request().postDataJSON()]); await slow; return route.fulfill({json: {}}); });
  await page.locator("[data-ws-use-pages]").click();
  await expect(page.locator("[data-ws-progress]")).toContainText("Updating the drawing pages");
  // Switching away and back while the pages are prepared waits for the same run instead of starting another.
  await page.locator("[data-ws-tab='project']").click();
  await expect(page.locator("[data-ws-project]")).toBeVisible();
  await page.locator("[data-ws-tab='drawings']").click();
  await expect(page.locator("[data-ws-progress]")).toContainText("Updating the drawing pages");
  release();
  await expect.poll(() => posts.filter(([kind]) => kind === "tasks").length).toBe(1);
  expect(posts.filter(([kind]) => kind === "decisions")).toHaveLength(1);
  const decisions = posts.find(([kind]) => kind === "decisions")[1];
  expect(decisions.pages.map(row => row.page)).toEqual([20]);
  expect(posts.find(([kind]) => kind === "tasks")[1]).toMatchObject({project_id: "job-1", action: "run_all"});
  await expect(page.locator("[data-ws-drawings]")).toBeVisible();
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

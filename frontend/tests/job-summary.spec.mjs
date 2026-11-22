import { expect, test } from "@playwright/test";

// The answer card and the one to-do list at the top of every job step (js/job/summary.js).
const projectId = "summary-job";

const plan = {
  project_id: projectId,
  status: { id: projectId, name: "Summary job", results_ready: true, result_stale: false, phase1_complete: true, phase2_complete: true, needs_you: {} },
  pages: [],
  outlines: [],
  rooms: [],
  final_report: {},
};

function todo(id, kind, severity, title, extra = {}) {
  return { id, kind, severity, title, detail: `${title} detail.`, step: "rooms", room: null, impact: null, actions: [], ...extra };
}

const summary = (overrides = {}) => ({
  project_id: projectId,
  answer: { state: "current", headline: "Cooling load", total_kw: 21.6568, sensible_kw: 18.2, latent_kw: 3.4, month: "January", hour: 17,
            suggested_system: "1 × 22 kW ducted", rooms_counted: 16, rooms_found: 21, note: "" },
  completeness: { level: "partial", text: "Partial: 3 rooms aren't counted and 6 things need checking." },
  todos: [
    todo("area:bed", "room_area", "blocks", "Type the area of Bedroom 1", {
      room: { room_id: "room-use:unassigned-level:bedroom-1", name: "Bedroom 1", level: "", area_m2: null, use_id: "house_bedroom", cooled: true },
      actions: [{ type: "area", label: "Save area" }, { type: "cooled", value: false, label: "Not a cooled room" }],
    }),
    todo("outdoor:bal", "outdoor_cooled", "changes_number", "Balcony is counted as a cooled room", {
      room: { room_id: "room-use:ground-floor:balcony", name: "Balcony", level: "Ground Floor", area_m2: 16.7, use_id: "house_living", cooled: true },
      impact: { kw: 2.3, text: "Adds 2.3 kW to the total now" },
      actions: [{ type: "cooled", value: false, label: "Not cooled" }],
    }),
    todo("size:master", "room_size", "check", "Master bedroom is only 3.6 m²", {
      room: { room_id: "room-use:first-floor:master-bedroom", name: "Master bedroom", level: "First Floor", area_m2: 3.6, use_id: "house_bedroom", cooled: true },
      actions: [{ type: "rename", label: "Rename" }, { type: "use", label: "Change use" }],
    }),
    todo("tasks", "drawing_task", "check", "Couldn't trace the outline of Dining and Corridor", {
      step: "drawings", actions: [{ type: "retry", endpoint: "/api/drawing-tasks", body: { action: "resume" }, label: "Try again" }],
    }),
    todo("equipment", "equipment", "changes_number", "1 piece of equipment has no room", { step: "operating", actions: [{ type: "link", step: "operating", label: "See the equipment" }] }),
    todo("site", "site", "check", "Confirm the site address", { step: "details", actions: [{ type: "link", step: "details", label: "Job details" }] }),
  ],
  counts: { total: 6, blocks: 1, changes_number: 2, check: 3, by_step: { rooms: 3, drawings: 1, operating: 1 } },
  uses: { house_bedroom: "House bedroom", house_living: "House living area", outdoor: "Outdoor area" },
  ...overrides,
});

async function open(page, data = summary(), step = "rooms") {
  const posts = [];
  await page.route("**/api/projects", (route) => route.fulfill({ json: [{ id: projectId, name: "Summary job", phase1_complete: true, phase2_complete: true, results_ready: true }] }));
  await page.route("**/api/job-plan?**", (route) => route.fulfill({ json: plan }));
  await page.route("**/api/result-status?**", (route) => route.fulfill({ json: {} }));
  await page.route("**/api/job-summary?**", (route) => route.fulfill({ json: typeof data === "function" ? data() : data }));
  await page.route("**/api/quick-fix", (route) => {
    posts.push({ url: "/api/quick-fix", body: route.request().postDataJSON() });
    return route.fulfill({ json: { applied: ["Balcony: not cooled."] } });
  });
  await page.route("**/api/drawing-tasks", (route) => {
    posts.push({ url: "/api/drawing-tasks", body: route.request().postDataJSON() });
    return route.fulfill({ json: { status: "running" } });
  });
  await page.goto(`/?preview=1#/job/${projectId}/${step}`);
  await expect(page.locator("[data-answer-total]")).toBeVisible();
  return posts;
}

test("the answer card shows the saved total, where it peaks, the suggestion and how complete it is", async ({ page }) => {
  await open(page);
  const card = page.locator(".job-answer");
  await expect(card.locator("[data-answer-total]")).toHaveText("21.7 kW");
  await expect(card).toContainText("Peak January 17:00");
  await expect(card).toContainText("Suggested system: 1 × 22 kW ducted");
  await expect(card).toContainText("16 of 21 cooled rooms counted");
  await expect(card.locator("[data-answer-badge]")).toHaveText("Current");
  await expect(card.locator("[data-completeness]")).toHaveText("Partial");
  await expect(card).toContainText("3 rooms aren't counted");
  // The step bar shows how many items each step holds.
  // "Check the house" holds the rooms and people-and-equipment items; each tab shows its own.
  await expect(page.locator('[data-step-todo="house"]')).toHaveText("4");
  await expect(page.locator('[data-step-todo="drawings"]')).toHaveText("1");
  await expect(page.locator('[data-step-todo="tab-rooms"]')).toHaveText("3");
  await expect(page.locator('[data-step-todo="tab-operating"]')).toHaveText("1");
});

test("the card is on every step", async ({ page }) => {
  await open(page, summary(), "drawings");
  await expect(page.locator("[data-job-summary] [data-answer-total]")).toHaveText("21.7 kW");
});

test("no result shows Not calculated, never a number", async ({ page }) => {
  await open(page, summary({ answer: { state: "none", headline: "Cooling load", total_kw: null, rooms_counted: 0, rooms_found: 0, note: "Not calculated yet: the drawings are still being read." },
                             completeness: { level: "none", text: "" }, todos: [], counts: { total: 0, by_step: {} } }));
  await expect(page.locator("[data-answer-total]")).toHaveText("Not calculated");
  await expect(page.locator("[data-answer-note]")).toContainText("the drawings are still being read");
  await expect(page.locator("[data-todo-none]")).toHaveText("Nothing left to check");
  await expect(page.locator(".job-answer")).not.toContainText("kW");
});

test("an out-of-date load offers to update it from the card", async ({ page }) => {
  const stale = summary({
    answer: { ...summary().answer, state: "stale", note: "Out of date: rooms were changed after it was calculated." },
    todos: [todo("stale", "update_load", "blocks", "The load is out of date", { actions: [{ type: "recalculate", phase: "full", label: "Update the load" }] }), ...summary().todos],
  });
  await open(page, stale);
  await expect(page.locator("[data-answer-badge]")).toHaveText("Out of date");
  await expect(page.locator('.job-answer [data-action="recalculate"]')).toHaveText("Update the load");
  // The update isn't repeated as a row in the list.
  await page.locator("[data-todo-toggle]").click();
  await expect(page.locator('[data-todo="stale"]')).toHaveCount(0);
});

test("the list opens, is ordered as given, and shows four before Show more", async ({ page }) => {
  await open(page);
  const list = page.locator("[data-todo-list]");
  await expect(list).toBeHidden();
  await page.locator("[data-todo-toggle]").click();
  await expect(list).toBeVisible();
  await expect(page.locator("[data-todo-toggle]")).toHaveAttribute("aria-expanded", "true");
  await expect(list.locator("[data-todo]:visible")).toHaveCount(4);
  await expect(list.locator("[data-todo]").first()).toContainText("Type the area of Bedroom 1");
  await expect(list.locator('[data-todo="area:bed"] .job-todo-severity')).toHaveText("Not in the total");
  await expect(list.locator('[data-todo="outdoor:bal"] [data-todo-impact]')).toHaveText("Adds 2.3 kW to the total now");
  await expect(list.locator('[data-todo="size:master"] [data-todo-impact]')).toHaveCount(0);
  await list.locator("[data-todo-more]").click();
  await expect(list.locator("[data-todo]:visible")).toHaveCount(6);
  await expect(list.locator('[data-todo="equipment"] a')).toHaveAttribute("href", `#/job/${projectId}/operating`);
});

test("fixes save through the quick-fix endpoint with the room's id", async ({ page }) => {
  const posts = await open(page);
  await page.locator("[data-todo-toggle]").click();
  const area = page.locator('[data-todo="area:bed"]');
  await area.locator('[data-action="area"]').click();
  await expect(area.locator("[data-todo-message]")).toHaveText("Type the area in m² first.");
  await area.locator('[data-input="area"]').fill("12.5");
  await area.locator('[data-action="area"]').click();
  await expect(page.locator("[data-summary-flash]")).toContainText("Saved.");
  expect(posts[0]).toEqual({ url: "/api/quick-fix", body: { project_id: projectId, rooms: [{ room_id: "room-use:unassigned-level:bedroom-1", area_m2: 12.5 }], reviewer: "To-do list" } });
});

test("not cooled, rename and retry send the right requests", async ({ page }) => {
  const posts = await open(page);
  await page.locator("[data-todo-toggle]").click();
  await page.locator('[data-todo="outdoor:bal"] [data-action="cooled"]').click();
  await expect.poll(() => posts.length).toBe(1);
  expect(posts[0].body.rooms).toEqual([{ room_id: "room-use:ground-floor:balcony", cooled: false }]);
  // After a save the step is drawn again with what was saved (the list stays open).
  await expect(page.locator("[data-summary-flash]")).toContainText("Saved. Balcony: not cooled.");
  await expect(page.locator("[data-todo-list]")).toBeVisible();
  const master = page.locator('[data-todo="size:master"]');
  await master.locator("summary", { hasText: "Rename" }).click();
  await master.locator('[data-input="name"]').fill("Ensuite");
  await master.locator('[data-action="rename"]').click();
  await expect.poll(() => posts.length).toBe(2);
  expect(posts[1].body.rooms).toEqual([{ room_id: "room-use:first-floor:master-bedroom", name: "Ensuite" }]);
  await expect(page.locator("[data-summary-flash]")).toBeVisible();
  await expect(page.locator('[data-todo="size:master"] details[open]')).toHaveCount(0);
  await page.locator('[data-todo="tasks"] [data-action="retry"]').click();
  await expect.poll(() => posts.length).toBe(3);
  expect(posts[2]).toEqual({ url: "/api/drawing-tasks", body: { project_id: projectId, action: "resume" } });
});

test("split pieces join from the list through the join endpoint", async ({ page }) => {
  const posts = [];
  const data = summary({
    todos: [todo("split:ff:ensuite", "split_room", "check", "Ensuite was read as 5 pieces (11.4 m² together)", {
      actions: [{ type: "join", room_ids: ["e", "e1", "e2"], name: "Ensuite", label: "Join into one room" }, { type: "link", step: "rooms", label: "Check on the plan" }],
    })],
    counts: { total: 1, check: 1, by_step: { rooms: 1 } },
  });
  await page.route("**/api/room-join", (route) => {
    posts.push(route.request().postDataJSON());
    return route.fulfill({ json: { applied: ["Ensuite (3 pieces) is one room."] } });
  });
  await open(page, data);
  await page.locator("[data-todo-toggle]").click();
  await page.locator('[data-action="join"]').click();
  await expect(page.locator("[data-summary-flash]")).toContainText("Joined. Ensuite (3 pieces) is one room.");
  expect(posts[0]).toEqual({ project_id: projectId, action: "join", room_ids: ["e", "e1", "e2"], name: "Ensuite", reviewer: "To-do list" });
});

test("a summary that fails to load says so without breaking the step", async ({ page }) => {
  await page.route("**/api/projects", (route) => route.fulfill({ json: [{ id: projectId, name: "Summary job", phase1_complete: true }] }));
  await page.route("**/api/job-plan?**", (route) => route.fulfill({ json: plan }));
  await page.route("**/api/job-summary?**", (route) => route.fulfill({ status: 400, json: { error: "Project was not found." } }));
  await page.goto(`/?preview=1#/job/${projectId}/rooms`);
  await expect(page.locator("[data-job-summary]")).toContainText("The summary couldn't load: Project was not found.");
  await expect(page.locator(".job-steps")).toBeVisible();
});

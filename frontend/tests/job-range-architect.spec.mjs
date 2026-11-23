import { expect, test } from "@playwright/test";

const projectId = "range-architect-job";
const plan = {
  project_id: projectId,
  status: { id: projectId, dwelling: true, results_ready: true, result_stale: false, phase1_complete: true, phase2_complete: true, needs_you: { phase2: 0 } },
  pages: [], outlines: [],
  rooms: [{ room_id: "room-1", name: "Family", display_name: "Family", kind: "comfort", included: true, area_m2: 40, final_kw: 21.66 }],
  final_report: { included_scope_peak: { final_design_total_kw: 21.66, safety_factor: 1 }, room_peaks: [] },
};

const calculatedRange = {
  status: "calculated", saved_kw: 21.66, unit: "1 × 24 kW ducted", low_kw: 17.38, high_kw: 25.42,
  basis: "One unknown changed at a time, each through the full calculation.",
  unknowns: [
    { key: "windows", label: "Window glass", assumed: "The NatHERS default window.", open: true, answered: false, swing_kw: 4.28, changes_unit: true, low_kw: 17.38, high_kw: 21.66,
      cases: [{ case_id: "double_low_e", label: "Double-glazed low-e (U 2.9, SHGC 0.40)", status: "calculated", kw: 17.38, delta_kw: -4.28, delta_pct: -19.8, unit: "1 × 18 kW ducted", changes_unit: true },
              { case_id: "single_tinted", label: "Single tinted (U 6.0, SHGC 0.45)", status: "not_calculated", reason: "ValueError: boom" }] },
    { key: "wall", label: "External wall construction", assumed: "", open: true, answered: false, swing_kw: 3.76, changes_unit: true, low_kw: 20.9, high_kw: 25.42,
      cases: [{ case_id: "brick_veneer_uninsulated", label: "Uninsulated brick veneer (U 1.9)", status: "calculated", kw: 25.42, delta_kw: 3.76, delta_pct: 17.4, unit: "2 × 14 kW ducted", changes_unit: true }] },
  ],
};

const questions = (status = "open") => ({
  items: [
    { id: "windows", label: "Window glass", question: "What glazing is specified?", answer_kind: "glazing", status, swing_kw: 4.28, changes_unit: true, why: "Double-glazed low-e: 17.4 kW (-4.3)." },
    { id: "wall", label: "External wall construction", question: "What are the walls made of?", answer_kind: "construction", status: "open", swing_kw: 3.76, changes_unit: true, why: "" },
    { id: "north", label: "True north", question: "Which way is true north?", answer_kind: "north", status: "answered", swing_kw: null, changes_unit: false, why: "" },
  ],
  open_count: 2, unit_changing_open: 2,
  constructions: { wall: [{ id: "frame-walls:brick-veneer", name: "Brick Veneer", u_value_w_m2k: 1.9 }], roof: [] },
  email: { subject: "Questions about the house", body: "1. What glazing is specified?\n2. What are the walls made of?" },
});

async function open(page, { ranges = [calculatedRange], list = questions() } = {}) {
  const posts = [];
  let call = 0;
  await page.route("**/api/projects", (route) => route.fulfill({ json: [{ id: projectId, name: "Range job", results_ready: true }] }));
  await page.route("**/api/job-plan?**", (route) => route.fulfill({ json: plan }));
  await page.route("**/api/result-status?**", (route) => route.fulfill({ json: {} }));
  await page.route("**/api/setpoint-comparison?**", (route) => route.fulfill({ json: { status: "not_calculated", reason: "n/a" } }));
  await page.route("**/api/heating-summary?**", (route) => route.fulfill({ json: { status: "not_calculated", reason: "n/a" } }));
  await page.route("**/api/assumption-range?**", (route) => route.fulfill({ json: ranges[Math.min(call++, ranges.length - 1)] }));
  await page.route("**/api/architect-questions**", (route) => {
    if (route.request().method() === "POST") {
      posts.push(route.request().postDataJSON());
      return route.fulfill({ json: { ...questions("asked"), summary: "1 question(s) marked asked." } });
    }
    return route.fulfill({ json: list });
  });
  await page.goto(`/?preview=1#/job/${projectId}/results`);
  return posts;
}

test("the range shows under the total and per unknown, with unit changes and failed cases named", async ({ page }) => {
  await open(page);
  const line = page.locator("[data-range-line]");
  await expect(line).toContainText("Could be 17.4–25.4 kW depending on the window glass and external wall construction");
  await expect(line).toContainText("Some answers would change the unit");
  const panel = page.locator("[data-range-panel]");
  await expect(panel.locator('[data-range-case="double_low_e"]')).toContainText("17.4 kW");
  await expect(panel.locator('[data-range-case="double_low_e"]')).toContainText("-4.3 kW (-19.8%)");
  await expect(panel.locator('[data-range-case="double_low_e"] [data-changes-unit]')).toBeVisible();
  await expect(panel.locator('[data-range-case="single_tinted"]')).toContainText("Not calculated: ValueError: boom");
  await expect(panel.locator('[data-range-case="brick_veneer_uninsulated"]')).toContainText("+3.8 kW (+17.4%)");
});

test("while the range runs it says so, then shows the result", async ({ page }) => {
  await page.clock.install();
  await open(page, { ranges: [{ status: "running", done: 2, total: 6 }, calculatedRange] });
  await expect(page.locator("[data-range-line]")).toContainText("2 of 6 calculations");
  await expect(page.locator('[data-range-state="running"]')).toBeVisible();
  await page.clock.runFor(5500);
  await expect(page.locator("[data-range-line]")).toContainText("Could be 17.4–25.4 kW");
});

test("inputs changed since the result: not calculated with the reason, never a number", async ({ page }) => {
  await open(page, { ranges: [{ status: "inputs_changed", reason: "The inputs have changed since this load was calculated." }] });
  await expect(page.locator("[data-range-line]")).toContainText("Range not calculated: The inputs have changed");
  await expect(page.locator('[data-range-state="inputs_changed"]')).toContainText("Not calculated.");
  await expect(page.locator("[data-range-panel] table")).toHaveCount(0);
});

test("ask the architect: ranked items, email link, marking asked, and answer forms", async ({ page }) => {
  const posts = await open(page);
  const panel = page.locator("[data-architect-panel]");
  await expect(panel).toContainText("2 questions still open, 2 of which could change the unit");
  await expect(panel.locator("[data-architect-item]")).toHaveCount(3);
  await expect(panel.locator('[data-architect-item="north"] [data-architect-status]')).toHaveText("Answered");
  await expect(panel.locator('[data-architect-item="north"] form')).toHaveCount(0);
  const href = await panel.locator("[data-architect-mailto]").getAttribute("href");
  expect(decodeURIComponent(href)).toContain("1. What glazing is specified?");
  await panel.locator('[data-architect-toggle="windows"]').click();
  await expect(panel.locator('[data-architect-item="windows"] [data-architect-status]')).toHaveText("Asked – waiting");
  await expect(panel.locator("[data-architect-status-line]")).toContainText("marked asked");
  expect(posts[0]).toMatchObject({ project_id: projectId, action: "mark_asked", ids: ["windows"] });
  const wall = panel.locator('[data-architect-item="wall"]');
  await wall.locator("summary").click();
  await expect(wall.locator('select[name="construction_id"] option')).toHaveCount(2);
  await wall.locator('select[name="construction_id"]').selectOption("frame-walls:brick-veneer");
  await wall.getByRole("button", { name: "Save the answer" }).click();
  await expect.poll(() => posts.length).toBe(2);
  expect(posts[1]).toMatchObject({ action: "answer", id: "wall", construction_id: "frame-walls:brick-veneer", source: "architect" });
});

test("a stale result shows neither the range nor the architect list", async ({ page }) => {
  plan.status.result_stale = true;
  try {
    await open(page);
    await expect(page.locator("[data-range-panel]")).toHaveCount(0);
    await expect(page.locator("[data-architect-panel]")).toHaveCount(0);
  } finally {
    plan.status.result_stale = false;
  }
});

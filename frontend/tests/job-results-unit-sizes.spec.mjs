import { expect, test } from "@playwright/test";

// The Results page shows the unit sizes the contractor's report suggests, and "Create the report" packages the job.
const projectId = "results-unit-sizes-job";
const hourlyTotals = Array.from({ length: 24 }, (_, hour) => (hour === 15 ? 13.2 : 4 + hour / 4));

const plan = {
  project_id: projectId,
  status: { id: projectId, results_ready: true, result_stale: false, phase1_complete: true, phase2_complete: true, dwelling: true, needs_you: { phase2: 0 } },
  pages: [],
  outlines: [],
  rooms: [
    { room_id: "room-1", name: "Living", display_name: "Living", kind: "comfort", included: true, area_m2: 46, final_kw: 9.1 },
    { room_id: "room-2", name: "Bed 2", display_name: "Bed 2", kind: "comfort", included: true, area_m2: 11, final_kw: 0.8 },
  ],
  final_report: {
    included_scope_peak: { final_design_total_kw: 13.2, safety_factor: 1 },
    room_peaks: [{ source_room_id: "room-1", name: "Living" }, { source_room_id: "room-2", name: "Bed 2" }],
    design_day_profile: {
      total_kw: hourlyTotals,
      groups: hourlyTotals.map((total) => ({ sun: total - 1, people: 1 })),
      peak_hour: 15,
      peak_kw: 13.2,
      month: "January",
      group_labels: [{ key: "sun", label: "Sun" }, { key: "people", label: "People" }],
    },
  },
};

const unitSizes = {
  status: "calculated",
  dwelling: true,
  ducted: { load_kw: 13.2, units: 1, size_kw: 14, suggested: "14 kW", note: "" },
  rooms: [
    { name: "Living", level: "Ground Floor", month: "January", hour: 15, load_kw: 9.8, units: 2, size_kw: 5, suggested: "2 × 5 kW", note: "Larger than one wall split: 10 kW ducted is the usual choice (or 2 × 5 kW wall splits)." },
    { name: "Bed 2", level: "First Floor", month: "February", hour: 17, load_kw: 0.84, units: 1, size_kw: 2, suggested: "2 kW", note: "Small load: often served from a neighbouring room or a ducted outlet." },
  ],
  rule: "The smallest nominal size at or above the load, with no margin added.",
  rating_note: "Nominal capacities are rated at standard test conditions.",
  wall_split_kw: [2, 2.5, 3.5, 9.5],
  ducted_kw: [7.1, 14, 24],
  sources: ["One manufacturer's range."],
};

async function openResults(page, { sizes = unitSizes, status = plan.status } = {}) {
  await page.route("**/api/projects", (route) => route.fulfill({ json: [{ id: projectId, name: "Unit sizes job", phase1_complete: true, phase2_complete: true, results_ready: true }] }));
  await page.route("**/api/job-plan?**", (route) => route.fulfill({ json: { ...plan, status } }));
  await page.route("**/api/result-status?**", (route) => route.fulfill({ json: {} }));
  await page.route("**/api/unit-sizes?**", (route) => route.fulfill({ json: sizes }));
  await page.route("**/api/setpoint-comparison?**", (route) => route.fulfill({ json: { status: "not_calculated", reason: "Not needed here." } }));
  await page.route("**/api/heating-summary?**", (route) => route.fulfill({ json: { status: "not_calculated", reason: "Not needed here." } }));
  await page.route("**/api/assumption-range?**", (route) => route.fulfill({ json: { status: "not_calculated" } }));
  await page.route("**/api/architect-questions?**", (route) => route.fulfill({ json: { questions: [] } }));
  await page.goto(`/?preview=1#/job/${projectId}/results`);
}

test("unit sizes: one ducted system for the house, or a split per room, with the sizing rule", async ({ page }) => {
  await openResults(page);
  const panel = page.locator("[data-unit-sizes-panel]");
  await expect(panel.getByRole("heading", { name: "Unit sizes to consider" })).toBeVisible();
  await expect(panel.locator("[data-unit-ducted]")).toContainText("One ducted system for the whole house: nominal size 14 kW");
  await expect(panel.locator("[data-unit-ducted]")).toContainText("13.2 kW at its busiest hour");
  const rows = panel.locator("[data-unit-rooms] tbody tr");
  await expect(rows).toHaveCount(2);
  await expect(rows.first()).toContainText("Living");
  await expect(rows.first()).toContainText("January 15:00");
  await expect(rows.first().locator("[data-unit-size]")).toHaveText("2 × 5 kW");
  await expect(rows.first()).toContainText("10 kW ducted is the usual choice");
  await expect(rows.nth(1).locator("[data-unit-size]")).toHaveText("2 kW");
  await expect(rows.nth(1)).toContainText("Small load");
  await expect(panel).toContainText("with no margin added");
  await expect(panel).toContainText("wall splits 2, 2.5, 3.5, 9.5 kW; ducted 7.1, 14, 24 kW");
  // Unit sizes sit after the room loads they come from, and the design-day chart is on the page too.
  expect(await page.locator("#each-room-title").evaluate((heading) => Boolean(heading.compareDocumentPosition(document.querySelector("[data-unit-sizes-panel]")) & Node.DOCUMENT_POSITION_FOLLOWING))).toBe(true);
  await expect(page.locator("[data-design-day] svg")).toBeVisible();
  await expect(page.locator("[data-design-day] [data-peak-label]")).toHaveText("Peak 13.2 kW at 15:00");
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
});

test("unit sizes say why they aren't shown when the server has none", async ({ page }) => {
  await openResults(page, { sizes: { status: "stale", reason: "The final load is out of date; update it first." } });
  const panel = page.locator("[data-unit-sizes-panel]");
  await expect(panel.locator("[data-unit-sizes-missing]")).toHaveText("Not shown. The final load is out of date; update it first.");
  await expect(panel.locator("[data-unit-rooms]")).toHaveCount(0);
});

test("an out-of-date result shows no unit sizes and no report button", async ({ page }) => {
  let asked = false;
  await page.route("**/api/unit-sizes?**", (route) => { asked = true; return route.fulfill({ json: unitSizes }); });
  await openResults(page, { status: { ...plan.status, result_stale: true } });
  await expect(page.getByText("Update the final load before creating the report.")).toBeVisible();
  await expect(page.locator("[data-unit-sizes-panel]")).toHaveCount(0);
  await expect(page.locator("[data-create-report]")).toHaveCount(0);
  expect(asked).toBe(false);
});

test("Create the report packages the job's final load and offers the PDF and HTML", async ({ page }) => {
  let posted;
  await page.route("**/api/report-package", async (route) => {
    posted = route.request().postDataJSON();
    await route.fulfill({ json: { pdf_url: "/reports/job.pdf", html_url: "/reports/job.html" } });
  });
  await openResults(page);
  const button = page.locator("[data-create-report]");
  await expect(button).toHaveText("Create the report");
  await button.click();
  const status = page.locator("[data-report-status]");
  await expect(status).toContainText("Report ready");
  await expect(status.getByRole("link", { name: "Download the PDF" })).toHaveAttribute("href", "/reports/job.pdf");
  await expect(status.getByRole("link", { name: "open it in a new tab" })).toHaveAttribute("href", "/reports/job.html");
  expect(posted).toMatchObject({ project_id: projectId, report_type: "cooling", source: "job" });
  await expect(button).toBeEnabled();
});

test("Create the report says why it failed and can be tried again", async ({ page }) => {
  let calls = 0;
  await page.route("**/api/report-package", (route) => {
    calls += 1;
    return route.fulfill({ status: 400, json: { error: "the final load is out of date; update it first" } });
  });
  await openResults(page);
  const button = page.locator("[data-create-report]");
  await button.click();
  await expect(page.locator("[data-report-status]")).toContainText("The report couldn’t be created");
  await expect(button).toBeEnabled();
  await button.click();
  await expect.poll(() => calls).toBe(2);
});

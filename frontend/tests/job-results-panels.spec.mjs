import { expect, test } from "@playwright/test";

const projectId = "results-panels-job";
const groups = ["air", "sun", "people", "lights", "equipment", "conduction", "fan"];
const hourlyTotals = Array.from({ length: 24 }, (_, hour) => hour === 17 ? 37.52 : 12 + hour / 2);
const roomTotals = Array.from({ length: 24 }, (_, hour) => hour === 17 ? 8.4 : 2 + hour / 10);

const plan = {
  project_id: projectId,
  status: { id: projectId, results_ready: true, result_stale: false, phase1_complete: true, phase2_complete: true, needs_you: { phase2: 0 } },
  pages: [],
  outlines: [],
  rooms: [
    { room_id: "room-1", name: "Bedroom", display_name: "Bedroom · Level 1", kind: "comfort", included: true, area_m2: 25, final_kw: 8.4 },
    { room_id: "room-2", name: "Study", display_name: "Study · Level 2", kind: "comfort", included: true, area_m2: 18, final_kw: 5.1 },
  ],
  final_report: {
    included_scope_peak: { final_design_total_kw: 37.52, safety_factor: 1 },
    room_peaks: [
      { source_room_id: "room-1", name: "Bedroom", hour: 17, hourly: { total_kw: roomTotals, groups: Array.from({ length: 24 }, () => ({})) }, own_peak: { kw: 8.8, month: "February", hour: 16 } },
      { source_room_id: "room-2", name: "Study" },
    ],
    design_day_profile: {
      total_kw: hourlyTotals,
      groups: hourlyTotals.map((total) => ({ air: total - 6, sun: 1, people: 1, lights: 1, equipment: 1, conduction: 1, fan: 1 })),
      peak_hour: 17,
      peak_kw: 37.52,
      month: "February",
      group_labels: groups.map((key) => ({ key, label: ({ air: "Fresh air", sun: "Sun", people: "People", lights: "Lights", equipment: "Equipment", conduction: "Conduction", fan: "Fan heat" })[key], color: "#163B50" })),
    },
  },
};

const comparison = {
  status: "calculated",
  relative_humidity: 0.5,
  variants: [
    { setpoint_c: 24, baseline: true, status: "baseline", total_kw: 37.5159, sensible_kw: 31.97, latent_kw: 5.55, month: "February", hour: 17, delta_kw: 0, delta_pct: 0 },
    { setpoint_c: 22.5, status: "calculated", total_kw: 38.3266, sensible_kw: 32.77, latent_kw: 5.55, month: "February", hour: 17, delta_kw: 0.8107, delta_pct: 2.16 },
    { setpoint_c: 22, status: "calculated", total_kw: 38.8, sensible_kw: 33.25, latent_kw: 5.55, month: "February", hour: 17, delta_kw: 1.28, delta_pct: 3.41 },
  ],
  rooms: [
    { room_id: "comparison-1", source_room_id: "room-1", name: "Bedroom", peaks: { "24": { kw: 8.4, hour: 17, month: "February" }, "22.5": { kw: 8.7, hour: 17, month: "February" }, "22": { kw: 8.9, hour: 17, month: "February" } } },
    { room_id: "comparison-2", source_room_id: "room-2", name: "Study", peaks: { "24": { kw: 5.1, hour: 16, month: "February" }, "22.5": { kw: 5.3, hour: 16, month: "February" }, "22": { kw: 5.5, hour: 16, month: "February" } } },
  ],
};

const unavailableHeating = {
  status: "not_calculated",
  reason: "No heating report has been calculated for this job.",
  next_step: "Calculate the winter load after reviewing the rooms.",
  gate: { status: "placeholder", approved: false, message: "The heating method needs engineering approval." },
};

async function openResults(page, { setpoints = comparison, heating = unavailableHeating } = {}) {
  await page.route("**/api/projects", (route) => route.fulfill({ json: [{ id: projectId, name: "Panel test job", phase1_complete: true, phase2_complete: true, results_ready: true }] }));
  await page.route("**/api/job-plan?**", (route) => route.fulfill({ json: plan }));
  await page.route("**/api/result-status?**", (route) => route.fulfill({ json: {} }));
  await page.route("**/api/setpoint-comparison?**", (route) => route.fulfill({ json: setpoints }));
  await page.route("**/api/heating-summary?**", (route) => route.fulfill({ json: heating }));
  await page.goto(`/?preview=1#/job/${projectId}/results`);
  await expect(page.locator("[data-setpoint-body]")).not.toContainText("Loading the comparison");
  await expect(page.locator("[data-heating-body]")).not.toContainText("Loading the heating result");
}

test("calculated setpoints show formatted changes and room display names after Each room", async ({ page }) => {
  await openResults(page);
  const panel = page.locator("[data-setpoint-panel]");
  await expect(panel.getByRole("heading", { name: "Indoor setpoint comparison" })).toBeVisible();
  expect(await page.locator("#each-room-title").evaluate((heading, other) => Boolean(heading.compareDocumentPosition(document.querySelector(other)) & Node.DOCUMENT_POSITION_FOLLOWING), "[data-setpoint-panel]")).toBe(true);
  await expect(panel.locator("[data-setpoint-table] tbody tr")).toHaveCount(3);
  await expect(panel.locator('[data-setpoint="22.5"]')).toContainText("+0.8 kW");
  await expect(panel.locator('[data-setpoint="22.5"]')).toContainText("+2.2%");
  const details = panel.locator("[data-setpoint-rooms]");
  await details.locator("summary").click();
  await expect(details).toHaveAttribute("open", "");
  await expect(details.locator("tbody tr")).toHaveCount(2);
  await expect(details.locator("tbody")).toContainText("Bedroom · Level 1");
  await expect(details.locator("tbody")).toContainText("Study · Level 2");
});

test("unavailable setpoint comparison shows its reason without a table", async ({ page }) => {
  await openResults(page, { setpoints: { status: "not_calculated", reason: "Rooms use different indoor cooling conditions." } });
  const panel = page.locator("[data-setpoint-panel]");
  await expect(panel).toContainText("Not calculated");
  await expect(panel).toContainText("Rooms use different indoor cooling conditions.");
  await expect(panel.locator("table")).toHaveCount(0);
});

test("unavailable variant explains its reason and a missing total is not zero", async ({ page }) => {
  const setpoints = structuredClone(comparison);
  setpoints.variants[1] = { setpoint_c: 22.5, status: "not_calculated", reason: "No valid room peak." };
  delete setpoints.variants[2].total_kw;
  await openResults(page, { setpoints });
  const rows = page.locator("[data-setpoint-table] tbody tr");
  await expect(rows.nth(1)).toContainText("Not calculated: No valid room peak.");
  await expect(rows.nth(2).locator("td").first()).toHaveText("Not calculated");
  await expect(rows.nth(2).locator("td").first()).not.toContainText("0 kW");
});

test("unavailable heating shows the gate and keeps report creation disabled", async ({ page }) => {
  await openResults(page);
  const panel = page.locator("[data-heating-panel]");
  await expect(panel).toContainText("Not calculated");
  await expect(panel).toContainText(unavailableHeating.reason);
  await expect(panel).toContainText("Next step:");
  await expect(panel).toContainText(unavailableHeating.next_step);
  await expect(panel).toContainText(unavailableHeating.gate.message);
  await expect(panel).not.toContainText(/\b0(?:\.0)? kW\b/);
  // Offered only once a heating report exists; the panel above says why there isn't one.
  await expect(page.locator("[data-create-heating-report]")).toBeHidden();
  await expect(page.locator("[data-heating-report-note]")).toBeHidden();
});

test("stale heating lists its reasons", async ({ page }) => {
  await openResults(page, { heating: { status: "stale", reason: "The saved heating result is out of date.", stale_reasons: ["A room setpoint changed.", "The outdoor temperature changed."], next_step: "Recalculate heating." } });
  const panel = page.locator("[data-heating-panel]");
  await expect(panel).toContainText("Not calculated");
  await expect(panel.locator("li")).toHaveCount(2);
  await expect(panel).toContainText("A room setpoint changed.");
  await expect(panel).toContainText("The outdoor temperature changed.");
  await expect(page.locator("[data-create-heating-report]")).toBeHidden();
});

test("current draft heating renders components and creates a heating report", async ({ page }) => {
  const heating = {
    status: "current", total_kw: 12.3, outdoor_design_c: 4.2, indoor_design_c: 21, draft: true,
    gate: { status: "placeholder", approved: false, message: "Engineering approval is pending." },
    rooms: [{ name: "Bedroom", peak_kw: 2, components: { "Walls, roof and floor conduction": 1.2, "Fresh air": 0.5, "Air leakage": 0.3 } }],
  };
  let posted;
  await page.route("**/api/report-package", async (route) => {
    posted = route.request().postDataJSON();
    await route.fulfill({ json: { pdf_url: "/reports/heating.pdf", html_url: "/reports/heating.html" } });
  });
  await openResults(page, { heating });
  const panel = page.locator("[data-heating-panel]");
  await expect(panel.locator("[data-heating-total]")).toContainText("12.3 kW");
  await expect(panel).toContainText("4.2 °C");
  await expect(panel).toContainText("21.0 °C");
  await expect(panel.locator("[data-heating-draft]")).toContainText("Draft.");
  await expect(panel.locator("[data-heating-draft]")).toContainText(heating.gate.message);
  const table = panel.locator("[data-heating-rooms]");
  for (const name of Object.keys(heating.rooms[0].components)) await expect(table.locator("thead")).toContainText(name);
  await expect(table.locator("tbody tr").first()).toContainText("Bedroom");
  await expect(table.locator("tbody tr").first()).toContainText("1.2");
  await expect(table.locator("tbody tr").first()).toContainText("0.5");
  await expect(table.locator("tbody tr").first()).toContainText("0.3");
  const button = page.locator("[data-create-heating-report]");
  await expect(button).toBeEnabled();
  await button.click();
  await expect(page.locator("[data-report-status]")).toContainText("Heating report ready");
  expect(posted).toMatchObject({ project_id: projectId, report_type: "heating" });
});

test("design-day chart, room disclosures, and mobile width", async ({ page }) => {
  await openResults(page);
  const day = page.locator("[data-design-day]");
  await expect(day.locator("svg")).toBeVisible();
  await expect(day.locator("[data-peak-label]")).toHaveText("Peak 37.5 kW at 17:00");
  for (const label of ["Fresh air", "Sun", "People", "Lights", "Equipment", "Conduction", "Fan heat"]) await expect(day.locator(".job-component-legend")).toContainText(label);
  const roomRows = page.locator(".job-room-row");
  const firstToggle = roomRows.first().locator("[data-room-toggle]");
  const firstDetail = page.locator(`#${await firstToggle.getAttribute("aria-controls")}`);
  await expect(firstDetail).toBeHidden();
  await firstToggle.click();
  await expect(firstToggle).toHaveAttribute("aria-expanded", "true");
  await expect(firstDetail).toBeVisible();
  await expect(firstDetail.locator("svg")).toBeVisible();
  await expect(firstDetail).toContainText("peak 8.4 kW at 17:00");
  await firstToggle.click();
  await expect(firstToggle).toHaveAttribute("aria-expanded", "false");
  await expect(firstDetail).toBeHidden();
  const secondToggle = roomRows.nth(1).locator("[data-room-toggle]");
  await secondToggle.click();
  await expect(page.locator(`#${await secondToggle.getAttribute("aria-controls")}`)).toContainText("Hour-by-hour profile not calculated");
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
});

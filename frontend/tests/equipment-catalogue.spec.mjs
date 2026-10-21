import { expect, test } from "@playwright/test";

const records = [
  { id: "pc", name: "Office PC", value: { name: "Office PC", quantity: 2 }, pages: [2],
    status: "typical", label: "Typical value TV-0048", room: "Cafe", quantity: 2, sensible_total_w: 160, latent_total_w: 0,
    typical_id: "TV-0048", source_row: 50, use_pattern: "WHILE_OPEN", source: "https://example.org/office-pc", schedule: Object.fromEntries(["weekday", "saturday", "sunday", "holiday"].map(day => [day, Array(24).fill(.5)])), reasons: [] },
  { id: "oven", name: "Combi oven", value: { name: "Combi oven", quantity: 1 }, pages: [3],
    status: "no_value", label: "No value yet", room: "Cafe", quantity: 1,
    note: "No typical or generic value for this kind of item yet; the room's typical equipment allowance covers it.",
    reasons: ["Quantity not shown on the drawings: one assumed."] },
  { id: "humidifier", name: "Humidifier", value: { name: "Humidifier", quantity: 1 }, pages: [3],
    status: "data_sheet", label: "Data sheet", room: "Cafe", quantity: 1, sensible_total_w: 0, latent_total_w: 200, source: "Manufacturer heat data", reasons: [] },
];

async function mount(page, options = {}) {
  const coverage = { records, summary: { data_sheet: 1, typical: 1, no_value: 1 }, coverage_complete: false,
    catalogue_error: options.error || "", catalogue_diagnostics: [] };
  await page.route("**/api/**", route => {
    const path = new URL(route.request().url()).pathname;
    const payloads = {
      "/api/job-plan": { rooms: [{ room_id: "cafe", name: "Cafe", included: true, phase1_kw: 1, area_m2: 50 }], pages: [],
        status: { phase1_complete: true, results_ready: true, result_stale: false } },
      "/api/ai-preliminary-model": { equipment_coverage: coverage, phase2_room_inputs: [], hourly_ai_preliminary_load_report_stale: options.stale || false },
      "/api/page-extraction": { findings: records.map(row => ({ id: row.id, value: row.value, pages: row.pages, catalogue_resolution: row })), equipment_coverage: coverage },
      "/api/skill-workflow": { findings: [] }, "/api/job-calculation": { status: "done" },
    };
    return route.fulfill({ json: payloads[path] || {} });
  });
  await page.route("**/equipment-preview", route => route.fulfill({ contentType: "text/html", body: '<html><head><meta charset="utf-8"><link rel="stylesheet" href="/frontend/css/job.css"></head><body class="job-preview"><main class="job-app" id="preview"></main></body></html>' }));
  await page.goto("/equipment-preview");
  await page.evaluate(async () => {
    const { renderOperating } = await import("/frontend/js/job/operating.js");
    const result = await renderOperating("fixture");
    document.querySelector("#preview").innerHTML = `<p>${result.statusLine}</p>${result.content}`;
    // The fixture has a saved result, so mounting never starts paid or background work.
    if (!result.status.result_stale) result.bind();
    window.previewStatus = result.status;
  });
}

test("each item says where its value came from, and items with no value are listed, not counted", async ({ page }) => {
  const errors = [];page.on("pageerror", error => errors.push(error.message));
  await mount(page);
  const list = page.locator(".job-operating-needs");
  await expect(list.getByText("Typical value TV-0048", { exact: true })).toBeVisible();
  await expect(list.getByText("No value yet", { exact: true })).toBeVisible();
  await expect(list.getByText("Data sheet", { exact: true })).toBeVisible();
  await expect(list.locator("[data-equipment-coverage]")).toHaveText("Equipment: 1 from data sheets, 1 typical values, 1 with no value yet.");
  await expect(list.locator('[data-equipment-status="no_value"]')).toContainText("the room's typical equipment allowance covers it");
  await expect(list.locator('[data-equipment-status="no_value"]')).toContainText("Not counted");
  await expect(list.locator('[data-equipment-status="typical"]')).toContainText("Typical values sheet row 50; use pattern WHILE_OPEN.");
  await expect(list.locator('[data-equipment-status="data_sheet"]')).toContainText("0.0 W sensible; 200.0 W latent");
  await page.screenshot({ path: "test-results/equipment-catalogue-desktop.png", fullPage: true });
  await expect(list.locator('[data-equipment-status="typical"]').getByRole("button", {name:"Edit equipment"})).toBeVisible();
  expect(errors).toEqual([]);
});

test("operator can save heat emissions and hours without JSON entry", async ({ page }) => {
  await mount(page);
  const item = page.locator('.job-operating-needs [data-equipment-status="no_value"]');
  await item.getByRole("button", { name: "Edit equipment" }).click();
  const form = item.locator("form");
  await form.locator('[name="sensible"]').fill("100");await form.locator('[name="latent"]').fill("20");
  await form.locator('[name="reference"]').fill("Manufacturer test sheet p2");
  for (const day of ["weekday", "saturday", "sunday"]) {
    await form.locator(`[name="${day}_start"]`).fill("8");
    await form.locator(`[name="${day}_end"]`).fill("18");
    await form.locator(`[name="${day}_fraction"]`).fill("0.5");
  }
  const sent = page.waitForRequest(request => request.method() === "POST" && request.url().includes("page-extraction"));
  await form.getByRole("button", { name: "Save equipment" }).click();
  const request = await sent;const value = request.postDataJSON().value;
  expect(value.sensible_to_room_w).toBe(100);expect(value.latent_to_room_w).toBe(20);
  expect(value.equipment_schedule.weekday[7]).toBe(0);expect(value.equipment_schedule.weekday[8]).toBe(.5);
  expect(value.equipment_schedule.holiday).toEqual(value.equipment_schedule.sunday);
});

test("a workbook problem is shown on mobile without stopping, and a changed workbook makes the result out of date", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await mount(page, { error: "Equipment catalogue cannot be read.", stale: true });
  await expect(page.locator("[data-equipment-coverage]")).toHaveText(
    "Equipment catalogue cannot be read. Equipment: 1 from data sheets, 1 typical values, 1 with no value yet.");
  await expect(page.getByRole("button", { name: "Update final load" })).toBeVisible();
  expect(await page.evaluate(() => window.previewStatus.result_stale)).toBe(true);
  await page.screenshot({ path: "test-results/equipment-catalogue-mobile.png", fullPage: true });
});

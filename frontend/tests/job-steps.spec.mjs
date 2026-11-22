import { expect, test } from "@playwright/test";

// The four steps: Read the drawings → Check the house (Rooms / People and equipment / Windows and names tabs)
// → Load and report → Design the system (js/job/frame.js, js/job/system.js).
const projectId = "steps-job";
const status = { id: projectId, name: "Steps job", stage: "results", drawings_confirmed: true, phase1_complete: true,
                 phase2_complete: true, results_ready: true, result_stale: false, needs_you: {} };
const plan = { project_id: projectId, status, pages: [], outlines: [], rooms: [], final_report: {}, phase1_report: {} };

const designInput = (overrides = {}) => ({
  schema: "toki.ac-design-input",
  version: 1,
  project: { id: projectId, name: "Steps job.pdf", address: "1 Main St, Ryde", dwelling: true },
  status: { state: "current", reason: "", ready: true },
  design_conditions: { location: "Sydney", outdoor_db_c: 31.1, outdoor_wb_c: 19.8, indoor_db_c: 24, indoor_wb_c: 18, indoor_rh: 0.56, source: "AIRAH Technical Handbook" },
  totals: { peak_kw: 21.6568, sensible_kw: 21.3, latent_kw: 0.35, month: "January", hour: 17, suggested_system: "24 kW ducted" },
  rooms: [
    { id: "join:ens", name: "Ensuite", level: "First Floor", area_m2: 11.4, cooled: true, people: 1, member_ids: ["e1", "e2", "e3"],
      peak: { kw: 1.2, month: "January", hour: 15 }, at_house_peak_kw: 0.9, hourly_kw: null },
    { id: "fam", name: "Family room", level: "Ground Floor", area_m2: 35.5, cooled: true, people: 4, member_ids: [],
      peak: { kw: 6.8, month: "January", hour: 17 }, at_house_peak_kw: 6.8, hourly_kw: null },
    { id: "bed", name: "Bedroom 1", level: "Ground Floor", area_m2: null, cooled: true, people: 1, member_ids: [], peak: { kw: null }, at_house_peak_kw: null },
  ],
  not_cooled: [{ id: "bal", name: "Balcony", level: "Ground Floor", area_m2: 16.7, reason: "Outdoor area" }],
  not_counted: [{ id: "mb", name: "Master bedroom", level: "First Floor", area_m2: 3.6, reason: "Its walls and windows weren't worked out from the drawings." }],
  open_items: { count: 5, titles: [] },
  assumptions: ["Walls use the preliminary U-value."],
  ...overrides,
});

async function mock(page, { input = designInput(), jobStatus = status } = {}) {
  await page.route("**/api/projects", (route) => route.fulfill({ json: [{ id: projectId, name: "Steps job", ...jobStatus }] }));
  await page.route("**/api/job-plan?**", (route) => route.fulfill({ json: { ...plan, status: jobStatus } }));
  await page.route("**/api/job-status?**", (route) => route.fulfill({ json: jobStatus }));
  await page.route("**/api/result-status?**", (route) => route.fulfill({ json: {} }));
  await page.route("**/api/job-summary?**", (route) => route.fulfill({ json: { answer: { state: "current", total_kw: 21.7 }, todos: [], counts: { by_step: {} } } }));
  await page.route("**/api/ac-design-input?**", (route) => route.fulfill({ json: input }));
  await page.route("**/api/quick-fix?**", (route) => route.fulfill({ json: { rooms: [], uses: {}, blinds: [], shade_kinds: [], glass_presets: [], every_window: {}, status: jobStatus } }));
}

test("the step bar reads in four plain steps", async ({ page }) => {
  await mock(page);
  await page.goto(`/?preview=1#/job/${projectId}/rooms`);
  const steps = page.locator(".job-steps .job-step");
  await expect(steps).toHaveCount(4);
  await expect(steps.nth(0)).toContainText("Read the drawings");
  await expect(steps.nth(1)).toContainText("Check the house");
  await expect(steps.nth(2)).toContainText("Load and report");
  await expect(steps.nth(3)).toContainText("Design the system");
  await expect(steps.nth(1)).toHaveAttribute("aria-current", "step");
  await expect(page.locator(".job-steps")).not.toContainText(/Phase [12]/);
});

test("Check the house holds the rooms, people-and-equipment and windows tabs; old links open the matching tab", async ({ page }) => {
  await mock(page);
  await page.goto(`/?preview=1#/job/${projectId}/house`);
  const tabs = page.locator(".job-subtabs .job-subtab");
  await expect(tabs).toHaveText(["Rooms", "People and equipment", "Windows and names"]);
  await expect(page.locator('.job-subtab[aria-current="page"]')).toHaveText("Rooms");
  await expect(tabs.nth(1)).toHaveAttribute("href", `#/job/${projectId}/operating`);
  await page.goto(`/?preview=1#/job/${projectId}/fix`);
  await expect(page.locator('.job-subtab[aria-current="page"]')).toHaveText("Windows and names");
  await expect(page.locator('.job-step[aria-current="step"]')).toContainText("Check the house");
});

test("people and equipment wait for the building load", async ({ page }) => {
  await mock(page, { jobStatus: { ...status, phase1_complete: false, phase2_complete: false, results_ready: false, stage: "rooms" } });
  await page.goto(`/?preview=1#/job/${projectId}/rooms`);
  await expect(page.locator(".job-subtab.locked")).toHaveText("People and equipment");
  await expect(page.locator(".job-subtab.locked")).not.toHaveAttribute("href", /.*/);
  await expect(page.locator(".job-step.locked").filter({ hasText: "Design the system" })).toHaveCount(1);
});

test("Design the system shows the load it starts from, room by room, and downloads it", async ({ page }) => {
  await mock(page);
  await page.goto(`/?preview=1#/job/${projectId}/system`);
  await expect(page.locator("[data-system-badge]")).toHaveText("Current");
  await expect(page.locator("[data-system-total]")).toHaveText("21.7 kW");
  await expect(page.locator(".job-system-summary")).toContainText("24 kW ducted");
  await expect(page.locator(".job-system-summary")).toContainText("31.1 °C dry bulb · 19.8 °C wet bulb");
  await expect(page.locator(".job-system-summary")).toContainText("56% RH");
  const ensuite = page.locator('[data-system-room="join:ens"]');
  await expect(ensuite).toContainText("Joined from 3 pieces");
  await expect(ensuite).toContainText("1.2 kW");
  await expect(ensuite).toContainText("January 15:00");
  await expect(ensuite).toContainText("0.9 kW");
  // A room with no saved load says so, never 0.
  await expect(page.locator('[data-system-room="bed"]')).toContainText("Not calculated");
  await expect(page.locator('[data-system-room="bed"]')).not.toContainText("0.0 kW");
  await expect(page.locator("[data-system-not-cooled]")).toContainText("Balcony");
  // A room meant to be cooled but missing from the load isn't filed as "not cooled".
  await expect(page.locator("[data-system-not-counted]")).toContainText("Master bedroom · First Floor: Its walls and windows weren't worked out");
  await expect(page.locator("[data-system-not-cooled]")).not.toContainText("Master bedroom");
  await expect(page.locator(".job-status")).toContainText("The load is current, but 5 items could change it");
  const download = page.waitForEvent("download");
  await page.locator("[data-system-download]").click();
  const file = await download;
  expect(file.suggestedFilename()).toBe("1-Main-St-Ryde-ac-design-input.json");
});

test("an out-of-date load says the design needs a current one", async ({ page }) => {
  await mock(page, { input: designInput({ status: { state: "stale", ready: false, reason: "Out of date: rooms were changed after it was calculated." } }) });
  await page.goto(`/?preview=1#/job/${projectId}/system`);
  await expect(page.locator("[data-system-badge]")).toHaveText("Out of date");
  await expect(page.locator("[data-system-reason]")).toContainText("rooms were changed");
  await expect(page.locator("[data-system-reason] a")).toHaveAttribute("href", `#/job/${projectId}/results`);
  await expect(page.locator("[data-system-design]")).toContainText("Update the load first");
});

test("Design the system waits for a calculated load", async ({ page }) => {
  await mock(page, { jobStatus: { ...status, results_ready: false, phase2_complete: false } });
  await page.goto(`/?preview=1#/job/${projectId}/system`);
  await expect(page).toHaveURL(new RegExp(`#/job/${projectId}/rooms$`));
});

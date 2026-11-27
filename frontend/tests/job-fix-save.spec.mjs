import { expect, test } from "@playwright/test";

// The quick-fix screen (js/job/fix.js): only what the operator changed is sent, the load is updated after a save, and a
// refused save or a failed update says why and lets the operator try again.
const projectId = "fix-save-job";
const doneStatus = { id: projectId, name: "Fix job", stage: "results", drawings_confirmed: true, phase1_complete: true,
                     phase2_complete: true, results_ready: true, result_stale: false, result_exists: true, needs_you: {} };

const screen = (status = doneStatus) => ({
  rooms: [
    { room_id: "r-living", label: "Living", name: "Living", level: "Ground Floor", area_m2: 30.04, area_label: "Printed 30 m²",
      use_id: "living", people: 4, kind: "comfort", can_cool: true, cooled: true,
      windows: { count: 2, area_m2: 6.2, glass: ["U 5.8, SHGC 0.7"], blinds: [], shade: [] }, window_fix: {} },
    { room_id: "r-bed", label: "Bed 1", name: "Bed 1", level: "First Floor", area_m2: 12, area_label: "",
      use_id: "bedroom", people: 2, kind: "comfort", can_cool: true, cooled: true,
      windows: { count: 0, area_m2: 0, glass: [], blinds: [], shade: [] }, window_fix: {} },
    { room_id: "r-garage", label: "Garage", name: "Garage", level: "Ground Floor", area_m2: 36, area_label: "",
      use_id: "garage", people: null, kind: "not_cooled", can_cool: false, cooled: false,
      windows: { count: 0, area_m2: 0, glass: [], blinds: [], shade: [] }, window_fix: {} },
  ],
  uses: { living: "Living room", bedroom: "Bedroom", garage: "Garage", not_a_room: "Not a room" },
  blinds: [{ id: "light", label: "Light-coloured blinds" }, { id: "dark", label: "Dark blinds" }],
  shade_kinds: ["awning", "verandah", "eave"],
  glass_presets: [{ id: "single_clear", label: "Single clear", u_value_w_m2k: 5.8, shgc: 0.7 },
                  { id: "double_lowe", label: "Double low-E", u_value_w_m2k: 2.9, shgc: 0.4 }],
  every_window: {},
  status,
});

async function openFix(page, { status = doneStatus, save, calculation } = {}) {
  const calls = { saves: [], calculations: [] };
  await page.route("**/api/projects", (route) => route.fulfill({ json: [{ id: projectId, name: "Fix job", ...status }] }));
  await page.route("**/api/job-plan?**", (route) => route.fulfill({ json: { project_id: projectId, status, pages: [], outlines: [], rooms: [], final_report: {}, phase1_report: {} } }));
  await page.route("**/api/job-status?**", (route) => route.fulfill({ json: status }));
  await page.route("**/api/result-status?**", (route) => route.fulfill({ json: {} }));
  await page.route("**/api/job-summary?**", (route) => route.fulfill({ json: { answer: { state: "current", total_kw: 12 }, todos: [], counts: { by_step: {} } } }));
  await page.route("**/api/quick-fix?**", (route) => route.fulfill({ json: screen(status) }));
  await page.route("**/api/quick-fix", (route) => {
    calls.saves.push(route.request().postDataJSON());
    return save ? save(route) : route.fulfill({ json: { applied: ["Living is shown as Lounge."] } });
  });
  await page.route("**/api/job-calculation**", (route) => {
    if (route.request().method() === "POST") calls.calculations.push(route.request().postDataJSON().phase);
    return calculation ? calculation(route) : route.fulfill({ json: { status: "done" } });
  });
  await page.goto(`/?preview=1#/job/${projectId}/fix`);
  await expect(page.locator("#job-fix-form")).toBeVisible();
  return calls;
}

const living = (page) => page.locator('tr[data-room="r-living"]');

test("only the changed fields are sent, then the building and final loads update and Results opens", async ({ page }) => {
  const calls = await openFix(page);
  await living(page).locator('[data-field="name"]').fill("Lounge");
  await living(page).locator('[data-field="area_m2"]').fill("31.5");
  await page.locator('tr[data-room="r-bed"] [data-field="cooled"]').uncheck();
  const windows = page.locator('tr[data-window="Living"]');
  await windows.locator('[data-field="glass"]').selectOption("double_lowe");
  await windows.locator('[data-field="blinds"]').selectOption("light");
  await windows.locator('[data-field="shade_kind"]').selectOption("awning");
  await expect(windows.locator(".job-fix-shade .job-fix-other")).toBeVisible();
  await windows.locator('[data-field="shade_depth"]').fill("0.6");
  await page.locator("#job-fix-save").click();
  await expect(page).toHaveURL(new RegExp(`#/job/${projectId}/results$`));
  expect(calls.saves).toEqual([{
    project_id: projectId,
    rooms: [{ room_id: "r-living", name: "Lounge", area_m2: 31.5 }, { room_id: "r-bed", cooled: false }],
    windows: [{ room: "Living", glass: { preset: "double_lowe" }, blinds: "light", shade: { kind: "awning", depth_m: 0.6 } }],
    reviewer: "Quick fixes",
  }]);
  expect(calls.calculations).toEqual(["phase1_base_room", "full"]);
});

test("an unchanged form sends nothing", async ({ page }) => {
  const calls = await openFix(page);
  // The printed area is shown rounded; leaving it as shown is not a change.
  await expect(living(page).locator('[data-field="area_m2"]')).toHaveValue("30");
  await page.locator("#job-fix-save").click();
  await expect(page.locator("#job-fix-status")).toHaveText("Nothing has changed.");
  expect(calls.saves).toEqual([]);
  expect(calls.calculations).toEqual([]);
});

test("a typed glass needs its values and source, sent as typed", async ({ page }) => {
  const calls = await openFix(page);
  const every = page.locator('tr[data-window="*"]');
  await every.locator('[data-field="glass"]').selectOption("other");
  await expect(every.locator(".job-fix-glass .job-fix-other")).toBeVisible();
  await every.locator('[data-field="glass_u"]').fill("3.1");
  await every.locator('[data-field="glass_shgc"]').fill("0.45");
  await every.locator('[data-field="glass_source"]').fill("Window schedule A-501");
  await page.locator("#job-fix-save").click();
  await expect(page).toHaveURL(new RegExp(`#/job/${projectId}/results$`));
  expect(calls.saves[0].windows).toEqual([{ room: "*", glass: { u_value_w_m2k: 3.1, shgc: 0.45, source: "Window schedule A-501" } }]);
});

test("a refused save shows the server's reason, starts no update, and can be sent again", async ({ page }) => {
  const calls = await openFix(page, { save: (route) => route.fulfill({ status: 400, json: { error: "The area of Living in m² must be between 0.5 and 20000." } }) });
  await living(page).locator('[data-field="area_m2"]').fill("0.1");
  const save = page.locator("#job-fix-save");
  await save.click();
  await expect(page.locator("#job-fix-status")).toHaveText("Not saved: The area of Living in m² must be between 0.5 and 20000.");
  await expect(save).toBeEnabled();
  expect(calls.calculations).toEqual([]);
  await expect(page).toHaveURL(new RegExp(`#/job/${projectId}/fix$`));
  await living(page).locator('[data-field="area_m2"]').fill("12");
  await save.click();
  await expect.poll(() => calls.saves.length).toBe(2);
  expect(calls.saves[1].rooms).toEqual([{ room_id: "r-living", area_m2: 12 }]);
});

test("a failed update after a save says so and stays on the screen", async ({ page }) => {
  const calls = await openFix(page, { calculation: (route) => route.fulfill({ json: { status: "failed", error: "The weather file is missing." } }) });
  await living(page).locator('[data-field="people"]').fill("5");
  await page.locator("#job-fix-save").click();
  // The fixes were saved: the message must not say they weren't.
  await expect(page.locator("#job-fix-status")).toHaveText("Saved, but the load didn't update: The weather file is missing. Save again to retry the update.");
  await expect(page.locator("#job-fix-save")).toBeEnabled();
  await expect(page).toHaveURL(new RegExp(`#/job/${projectId}/fix$`));
  expect(calls.saves).toHaveLength(1);
  expect(calls.calculations).toEqual(["phase1_base_room"]);
});

test("empty typed glass and shade boxes are sent empty, not as 0", async ({ page }) => {
  const calls = await openFix(page, { save: (route) => route.fulfill({ status: 400, json: { error: "Enter the glass U-value (W/m²K) as a number." } }) });
  const every = page.locator('tr[data-window="*"]');
  await every.locator('[data-field="glass"]').selectOption("other");
  await every.locator('[data-field="shade_kind"]').selectOption("eave");
  await page.locator("#job-fix-save").click();
  await expect(page.locator("#job-fix-status")).toHaveText("Not saved: Enter the glass U-value (W/m²K) as a number.");
  expect(calls.saves[0].windows).toEqual([{ room: "*", glass: { u_value_w_m2k: "", shgc: "", source: "" }, shade: { kind: "eave", depth_m: "" } }]);
  expect(calls.calculations).toEqual([]);
});

test("before the building load exists, a save goes back to Rooms without calculating", async ({ page }) => {
  const status = { ...doneStatus, stage: "rooms", phase1_complete: false, phase2_complete: false, results_ready: false, result_exists: false };
  const calls = await openFix(page, { status });
  await living(page).locator('[data-field="use"]').selectOption("bedroom");
  await page.locator("#job-fix-save").click();
  await expect(page).toHaveURL(new RegExp(`#/job/${projectId}/rooms$`));
  expect(calls.saves[0].rooms).toEqual([{ room_id: "r-living", use: "bedroom" }]);
  expect(calls.calculations).toEqual([]);
});

test("leaving with unsaved fixes asks first; staying keeps them", async ({ page }) => {
  await openFix(page);
  await living(page).locator('[data-field="name"]').fill("Lounge");
  let asked = "";
  page.once("dialog", (dialog) => { asked = dialog.message(); return dialog.dismiss(); });
  await page.getByRole("link", { name: "Rooms", exact: true }).click();
  expect(asked).toContain("Leave without saving your fixes?");
  await expect(page).toHaveURL(new RegExp(`#/job/${projectId}/fix$`));
  await expect(living(page).locator('[data-field="name"]')).toHaveValue("Lounge");
});

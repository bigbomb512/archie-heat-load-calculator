import { expect, test } from "@playwright/test";

// Rooms the drawing reader split into pieces: joined on the Rooms tab, drawn with one label, one row in the results.
const projectId = "joins-job";
const status = { id: projectId, name: "Joins job", stage: "results", drawings_confirmed: true, phase1_complete: true,
                 phase2_complete: true, results_ready: true, result_stale: false, needs_you: {} };
const square = (x, y, w, h) => [[x, y], [x + w, y], [x + w, y + h], [x, y + h]];
const room = (room_id, name, area, extra = {}) => ({ room_id, name, display_name: name, level: "First Floor", area_m2: area,
  kind: "comfort", included: true, state: "done", final_kw: 1, phase1_kw: 0.5, use: { taxonomy_id: "house_wet" }, ...extra });
const outline = (room_id, name, points, extra = {}) => ({ room_id, name, display_name: name, level: "First Floor", page: 1,
  points, label_point: [points[0][0] + (points[1][0] - points[0][0]) / 2, points[0][1] + (points[2][1] - points[1][1]) / 2],
  line_style: "solid", kind: "comfort", state: "done", ...extra });

function plan({ joined = false } = {}) {
  const join = joined ? { join_id: "join:ens", join_name: "Ensuite" } : {};
  return {
    project_id: projectId, status,
    pages: [{ page: 1, level_name: "First Floor", title: "First Floor Plan" }],
    rooms: [
      room("bed", "Bedroom 2", 15.2, { use: { taxonomy_id: "house_bedroom" }, final_kw: 1.8 }),
      room("e1", "Ensuite (1)", 1.7, join),
      room("e2", "Ensuite (2)", 2.6, join),
      room("e3", "Ensuite", 5.1, join),
    ],
    outlines: [
      outline("bed", "Bedroom 2", square(0, 0, 400, 300)),
      outline("e1", "Ensuite (1)", square(400, 0, 200, 100), join),
      outline("e2", "Ensuite (2)", square(400, 100, 200, 100), join),
      outline("e3", "Ensuite", square(400, 200, 300, 260), join),
    ],
    joined_rooms: joined ? [{ id: "join:ens", name: "Ensuite", level: "First Floor", member_ids: ["e1", "e2", "e3"],
                             area_m2: 9.4, use: "house_wet", cooled: true, people: 1, final_kw: 2.2, final_w_m2: 234 }] : [],
    final_report: { included_scope_peak: { final_design_total_kw: 4.0 },
                    room_peaks: [{ source_room_id: "bed", name: "Bedroom 2", design_total_kw: 1.8 },
                                 ...(joined ? [{ source_room_id: "join:ens", name: "Ensuite", member_ids: ["e1", "e2", "e3"], design_total_kw: 2.2 }] : [])] },
    phase1_report: {},
    room_use_options: { house_wet: "House wet area", house_bedroom: "House bedroom" },
  };
}

async function mock(page, data) {
  const posts = [];
  await page.route("**/api/projects", (route) => route.fulfill({ json: [{ id: projectId, name: "Joins job", ...status }] }));
  await page.route("**/api/job-plan?**", (route) => route.fulfill({ json: typeof data === "function" ? data() : data }));
  await page.route("**/api/result-status?**", (route) => route.fulfill({ json: {} }));
  await page.route("**/api/setpoint-comparison?**", (route) => route.fulfill({ json: { status: "not_calculated", reason: "Not in this test." } }));
  await page.route("**/api/heating-summary?**", (route) => route.fulfill({ json: { status: "not_calculated", reason: "Not in this test." } }));
  await page.route("**/api/job-summary?**", (route) => route.fulfill({ json: { answer: { state: "current", total_kw: 4 }, todos: [], counts: { by_step: {} } } }));
  await page.route("**/api/room-join", (route) => {
    posts.push(route.request().postDataJSON());
    return route.fulfill({ json: { applied: ["Ensuite (1), Ensuite (2) and Ensuite are one room: Ensuite."] } });
  });
  return posts;
}

test("pieces of one room are offered for joining with their total area", async ({ page }) => {
  const posts = await mock(page, plan());
  await page.goto(`/?preview=1#/job/${projectId}/rooms`);
  const panel = page.locator("[data-join-panel]");
  await expect(panel).toContainText("Ensuite · First Floor was read as 3 pieces (9.4 m² together)");
  await expect(panel).not.toContainText("Bedroom 2 ·");
  await panel.locator("[data-join-name]").fill("Main ensuite");
  await panel.getByRole("button", { name: "Join into one room" }).click();
  await expect.poll(() => posts.length).toBe(1);
  expect(posts[0]).toEqual({ project_id: projectId, reviewer: "Check the house", action: "join", room_ids: ["e1", "e2", "e3"], name: "Main ensuite" });
});

test("a joined room is drawn with one label and can be split again", async ({ page }) => {
  const posts = await mock(page, plan({ joined: true }));
  await page.goto(`/?preview=1#/job/${projectId}/rooms`);
  const svg = page.locator(".job-room-plan svg.job-plan");
  await expect(svg.locator(".job-plan-name", { hasText: "Ensuite" })).toHaveCount(1);
  await expect(svg.locator(".job-plan-name", { hasText: /Ensuite \(/ })).toHaveCount(0);
  await expect(svg.locator("polygon.joined")).toHaveCount(3);
  await expect(page.locator(".job-room-plan figcaption")).toContainText("2 rooms");
  const panel = page.locator("[data-join-panel]");
  await expect(panel).toContainText("Ensuite · First Floor: one room joined from 3 pieces, 9.4 m²");
  await panel.getByRole("button", { name: "Split into pieces" }).click();
  await expect.poll(() => posts.length).toBe(1);
  expect(posts[0]).toEqual({ project_id: projectId, reviewer: "Check the house", action: "split", join_id: "join:ens" });
});

test("Load and report lists a joined room once, with its own load", async ({ page }) => {
  await mock(page, plan({ joined: true }));
  await page.goto(`/?preview=1#/job/${projectId}/results`);
  const rows = page.locator(".job-room-row");
  await expect(rows).toHaveCount(2);
  await expect(rows.filter({ hasText: "Ensuite" })).toContainText("2.2 kW");
  await expect(rows.filter({ hasText: "Ensuite" })).toContainText("9.4");
  await expect(page.locator(".job-room-row", { hasText: "Ensuite (1)" })).toHaveCount(0);
});

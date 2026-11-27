import { expect, test } from "@playwright/test";

// Wall types printed in the drawings' legends are listed with the What-we-need-to-find questions as notes for review;
// the calculation doesn't use them (ai/construction_u_values.plan_wall_legend).
const projectId = "wall-legend-job";
const status = { id: projectId, name: "Wall legend job", stage: "operating", drawings_confirmed: true, phase1_complete: true,
                 phase2_complete: false, results_ready: false, result_stale: false, needs_you: {} };

async function openOperating(page, workflow) {
  await page.route("**/api/projects", (route) => route.fulfill({ json: [{ id: projectId, name: "Wall legend job", ...status }] }));
  await page.route("**/api/job-plan?**", (route) => route.fulfill({ json: { project_id: projectId, status, pages: [], outlines: [], rooms: [], final_report: {}, phase1_report: {} } }));
  await page.route("**/api/job-status?**", (route) => route.fulfill({ json: status }));
  await page.route("**/api/job-summary?**", (route) => route.fulfill({ json: { answer: { state: "none" }, todos: [], counts: { by_step: {} } } }));
  await page.route("**/api/ai-preliminary-model?**", (route) => route.fulfill({ json: {} }));
  await page.route("**/api/skill-workflow?**", (route) => route.fulfill({ json: workflow }));
  await page.route("**/api/page-extraction?**", (route) => route.fulfill({ json: { findings: [] } }));
  await page.route("**/api/job-calculation?**", (route) => route.fulfill({ json: { status: "none" } }));
  await page.goto(`/?preview=1#/job/${projectId}/operating`);
  await expect(page.getByRole("heading", { name: "Rooms and operating loads" })).toBeVisible();
}

test("wall-legend notes are listed once per wall type with their pages, for review only", async ({ page }) => {
  const note = { source: "drawing text", status: "needs_review", note: "Match this legend to a specific wall and a cited U-value." };
  await openOperating(page, {
    findings: [],
    answers: {},
    answer_options: {
      observed_wall_constructions: [
        { page: 12, construction: "Dincel wall", ...note },
        { page: 4, construction: "Brick cavity wall", ...note },
        { page: 3, construction: "Dincel wall", ...note },
        { page: 4, construction: "Stud wall", ...note },
      ],
    },
  });
  const panel = page.locator(".job-operating-needs [data-wall-legend]");
  await expect(panel.getByRole("heading", { name: "Wall types printed on the drawings" })).toBeVisible();
  await expect(panel.locator("[data-wall-legend-label]")).toContainText("For review, not used in the calculation.");
  const items = panel.locator("[data-wall-legend-item]");
  await expect(items).toHaveText(["Dincel wall (pages 3, 12)", "Brick cavity wall (page 4)", "Stud wall (page 4)"]);
  // The notes are not questions: the open-question count is unchanged.
  await expect(page.locator("#operating-needs-title")).toContainText("(0)");
});

test("no wall-legend block when the drawings print none", async ({ page }) => {
  await openOperating(page, { findings: [], answers: {}, answer_options: { observed_wall_constructions: [] } });
  await expect(page.locator(".job-operating-needs")).toBeVisible();
  await expect(page.locator("[data-wall-legend]")).toHaveCount(0);
});

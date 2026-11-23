import { expect, test } from "@playwright/test";

const projectId = "label-job";
const plan = {
  project_id: projectId,
  status: { results_ready: true, result_stale: false, phase1_complete: true, phase2_complete: true, needs_you: { phase2: 0 } },
  pages: [], outlines: [], rooms: [],
  final_report: { included_scope_peak: { final_design_total_kw: 21.7 }, room_peaks: [] },
};

async function open(page, label) {
  await page.route("**/api/projects", route => route.fulfill({ json: [{ id: projectId, name: "Label job", results_ready: true }] }));
  await page.route("**/api/job-plan?**", route => route.fulfill({ json: plan }));
  await page.route("**/api/result-status?**", route => route.fulfill({ json: {} }));
  await page.route("**/api/result-label?**", route => route.fulfill({ json: label }));
  await page.route("**/api/job-summary?**", route => route.fulfill({ json: {
    answer: { state: "current", headline: "Cooling load", total_kw: 21.7, rooms_counted: 1, rooms_found: 1 },
    result_label: label, completeness: { level: "complete", text: "" }, todos: [], counts: { total: 0, by_step: {} },
  } }));
  await page.route("**/api/assumption-range?**", route => route.fulfill({ json: { status: "not_started" } }));
  await page.route("**/api/architect-questions?**", route => route.fulfill({ json: { items: [], open_count: 0, constructions: {}, email: { subject: "", body: "" } } }));
  await page.route("**/api/setpoint-comparison?**", route => route.fulfill({ json: { status: "not_calculated" } }));
  await page.route("**/api/heating-summary?**", route => route.fulfill({ json: { status: "not_calculated" } }));
  await page.goto(`/?preview=1#/job/${projectId}/results`);
}

for (const label of [
  { status: "preliminary", text: "Preliminary (2 answers that could change the unit still open)", reason: "The range isn't worked out yet." },
  { status: "design", text: "Design", reason: "One input is still assumed." },
  { status: "preliminary", text: "Preliminary (out of date)", reason: "Update the load." },
]) {
  test(`shows ${label.text} beside the result and in the summary card`, async ({ page }) => {
    await open(page, label);
    await expect(page.locator(".job-result-summary [data-result-label]")).toHaveText(label.text);
    await expect(page.locator("[data-job-summary] [data-result-label]")).toHaveText(label.text);
    await expect(page.locator(".job-result-summary [data-result-label]")).toHaveAttribute("title", label.reason);
    await expect(page.locator(".job-result-summary h1")).toContainText(label.status === "design" ? "Design cooling load" : "Preliminary cooling load");
  });
}

test("no saved cooling result shows no label badge", async ({ page }) => {
  plan.status.results_ready = false;
  plan.final_report = {};
  try {
    await open(page, { status: "not_calculated", text: "Not calculated", count: 0, open: [], reason: "No final cooling load." });
    await expect(page.locator("[data-result-label]")).toHaveCount(0);
    await expect(page.getByRole("heading", { name: "Results are locked" })).toBeVisible();
  } finally {
    plan.status.results_ready = true;
    plan.final_report = { included_scope_peak: { final_design_total_kw: 21.7 }, room_peaks: [] };
  }
});

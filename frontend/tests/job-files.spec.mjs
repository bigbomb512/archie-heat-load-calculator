import { test, expect } from "@playwright/test";

// The report on Results, and moving a job between computers from Jobs: brought over from the old app before it
// was removed (2026-10-19). The server calls are mocked; nothing is built or written.

const JOB = { id: "job-1", name: "Cafe.pdf", display_name: "Cafe", address: "Shop 7", phase1_complete: true,
              phase2_complete: true, results_ready: true, created_at: "2026-10-19T09:00:00+11:00" };

function plan(stale = false) {
  return {
    status: { id: "job-1", name: "Cafe", stage: "results", phase1_complete: true, phase2_complete: true,
              results_ready: true, result_stale: stale, needs_you: {} },
    rooms: [{ room_id: "shop", name: "Shop", level: "", area_m2: 50, state: "done", phase1_kw: 2.5,
              final_kw: 4.2, final_w_m2: 84, kind: "comfort", included: true }],
    outlines: [], pages: [], rooms_without_outline: [],
    final_report: { included_scope_peak: { final_design_total_kw: 4.2, design_total_kw: 4.2, safety_factor: 1.1 },
                    room_peaks: [{ name: "Shop", design_total_kw: 4.2, components: { people: 1.2 } }] },
    phase1_report: {},
  };
}

async function mockResults(page, { stale = false, report } = {}) {
  const posts = [];
  await page.route("**/api/projects", (route) => route.fulfill({ json: [JOB] }));
  await page.route("**/api/job-plan**", (route) => route.fulfill({ json: plan(stale) }));
  await page.route("**/api/result-status**", (route) => route.fulfill({ json: { assumptions: [] } }));
  await page.route("**/api/report-package", (route) => {
    posts.push(route.request().postDataJSON());
    return report
      ? route.fulfill({ json: report })
      : route.fulfill({ status: 400, json: { error: "The final load must be calculated first." } });
  });
  return posts;
}

test("Results creates the cooling report and offers the PDF and the page", async ({ page }) => {
  const posts = await mockResults(page, {
    report: { status: "current", html_url: "/files/report.html", pdf_url: "/files/report.pdf" },
  });
  await page.goto("/#/job/job-1/results");
  await page.getByRole("button", { name: "Create the report" }).click();
  const status = page.locator("[data-report-status]");
  await expect(status.getByRole("link", { name: "Download the PDF" })).toHaveAttribute("href", "/files/report.pdf");
  await expect(status.getByRole("link", { name: "open it in a new tab" })).toHaveAttribute("href", "/files/report.html");
  expect(posts).toEqual([{ project_id: "job-1", report_type: "cooling" }]);
});

test("a report that can't be built says why, and an out-of-date result asks for an update first", async ({ page }) => {
  await mockResults(page);
  await page.goto("/#/job/job-1/results");
  await page.getByRole("button", { name: "Create the report" }).click();
  await expect(page.locator("[data-report-status]")).toHaveText(
    "The report couldn’t be created: The final load must be calculated first.",
  );
  await page.unrouteAll({ behavior: "ignoreErrors" });
  await mockResults(page, { stale: true });
  await page.goto("/#/job/job-1/results");
  await page.reload();
  await expect(page.getByText("Update the final load before creating the report.")).toBeVisible();
  await expect(page.getByRole("button", { name: "Create the report" })).toHaveCount(0);
});

test("Jobs exports a job to a file and imports one, opening it", async ({ page }) => {
  const posts = { export: [], import: [] };
  await page.route("**/api/projects", (route) => route.fulfill({ json: [JOB] }));
  await page.route("**/api/project-export", (route) => {
    posts.export.push(route.request().postDataJSON());
    return route.fulfill({ json: { artifact_url: "/files/cafe.zip", archive_fingerprint: "abc" } });
  });
  await page.route("**/api/project-import", (route) => {
    posts.import.push(route.request().postDataJSON());
    return route.fulfill({ json: { id: "job-2" } });
  });
  await page.route("**/api/job-plan**", (route) => route.fulfill({ json: plan() }));
  await page.goto("/#/jobs");
  await page.getByRole("button", { name: "Export" }).click();
  await expect(page.locator("[data-jobs-status]").getByRole("link", { name: "download it" })).toHaveAttribute(
    "href", "/files/cafe.zip");
  expect(posts.export).toEqual([{ project_id: "job-1" }]);
  await page.locator("#job-import").setInputFiles({ name: "cafe.zip", mimeType: "application/zip", buffer: Buffer.from("PK zip") });
  await expect(page).toHaveURL(/#\/job\/job-2\/drawings/);
  expect(posts.import).toEqual([{ archive_base64: Buffer.from("PK zip").toString("base64") }]);
});

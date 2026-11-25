import { expect, test } from "@playwright/test";

const job = (id, address, load, extra = {}) => ({
  id, name: `${id}.pdf`, address, cooling_load_kw: load,
  cooling_load_kind: load == null ? "none" : "final", result_stale: false,
  can_edit: true, created_at: "2026-11-01", needs_you: { phase1: 1 }, ...extra,
});

async function openJobs(page) {
  const jobs = [
    job("new-run", "139A Burns Road, Turramurra NSW 2074", 37.52, {
      earlier_runs: [job("old-run", "139A Burns Road, Turramurra", 30.15, { result_stale: true })],
    }),
    job("building", "1 Main St, Ryde", 12.34, { cooling_load_kind: "building_only" }),
    job("unknown", "", null),
  ];
  const group = () => {
    const active = jobs.filter((item) => !item.archived).map((item) => ({
      ...item, earlier_runs: (item.earlier_runs || []).filter((earlier) => !earlier.archived),
    }));
    const archived = jobs.flatMap((item) => [item, ...(item.earlier_runs || [])])
      .filter((item) => item.archived).map((item) => ({ ...item, earlier_runs: [] }));
    return [...active, ...archived];
  };
  await page.route("**/api/projects", (route) => route.fulfill({ json: group() }));
  await page.route("**/api/project-archive", (route) => {
    const { project_id, archived } = route.request().postDataJSON();
    const row = jobs.flatMap((item) => [item, ...(item.earlier_runs || [])]).find((item) => item.id === project_id);
    row.archived = archived;
    return route.fulfill({ json: { project_id, archived } });
  });
  await page.goto("/?preview=1#/jobs");
}

test("groups runs and shows saved cooling loads in matching columns", async ({ page }) => {
  await openJobs(page);
  await expect(page.locator(".job-jobs-table").first().locator("thead").first()).toContainText("Cooling load");
  const current = page.locator('[data-job-id="new-run"]');
  await expect(current.locator("th")).toContainText("139A Burns Road, Turramurra NSW 2074");
  await expect(current.locator("small")).toHaveText("new-run.pdf");
  await expect(current.locator(".job-load-value")).toHaveText("37.5 kW");
  await expect(page.locator('[data-job-id="old-run"]')).toBeHidden();
  await page.getByText("1 earlier run").click();
  await expect(page.locator('[data-job-id="old-run"]')).toBeVisible();
  await expect(page.locator('[data-job-id="old-run"] .job-load-value')).toHaveText("30.1 kW · out of date");
  await expect(page.locator('[data-job-id="building"] .job-load-value')).toHaveText("Building only · 12.3 kW");
  await expect(page.locator('[data-job-id="unknown"] .job-load-value')).toHaveText("—");
  await expect(page.locator('[data-job-id="unknown"] th a')).toHaveText("unknown.pdf");
});

test("a saved total of zero reads as no rooms counted", async ({ page }) => {
  await page.route("**/api/projects", (route) => route.fulfill({ json: [job("zero", "2 Main St, Ryde", 0, { cooling_load_kind: "building_only" })] }));
  await page.goto("/?preview=1#/jobs");
  await expect(page.locator('[data-job-id="zero"] .job-load-value')).toHaveText("No rooms counted yet");
});

test("archive hides a job and the archived toggle reveals it", async ({ page }) => {
  await openJobs(page);
  await page.locator('[data-job-id="building"] [data-archive-job]').click();
  await expect(page.locator('[data-job-id="building"]')).toBeHidden();
  await expect(page.getByText("Show archived (1)")).toBeVisible();
  await page.getByText("Show archived (1)").click();
  await expect(page.locator('[data-job-id="building"]')).toBeVisible();
  await page.locator('[data-job-id="building"] [data-archive-job]').click();
  await expect(page.getByText("Show archived (1)")).toHaveCount(0);
  await expect(page.locator('[data-job-id="building"]')).toBeVisible();
});

import { test, expect } from "@playwright/test";

test("entry page opens the job workflow on desktop and mobile", async ({ page }) => {
  for (const width of [1280, 390]) {
    await page.setViewportSize({ width, height: 800 });
    await page.goto("/frontend/landing.html");
    await expect(page.getByRole("heading", { name: "Pick up where the drawing set left off." })).toBeVisible();
    await expect(page.getByRole("listitem")).toHaveCount(4);
    await expect(page.getByText("Current calculation scope: hourly design-day cooling.")).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await page.getByRole("link", { name: "Open jobs", exact: true }).first().click();
    await expect(page).toHaveURL(/\/#\/jobs$/);
  }
});

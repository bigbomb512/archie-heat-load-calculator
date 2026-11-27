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

test("dark entry page keeps text and actions readable on desktop and mobile", async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem("archie.landing.theme", "dark"));
  for (const width of [1280, 390]) {
    await page.setViewportSize({ width, height: 800 });
    await page.goto("/frontend/landing.html");
    await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);

    const ratios = await page.evaluate(() => {
      const root = getComputedStyle(document.documentElement);
      const page = root.getPropertyValue("--page").trim();
      const sheet = root.getPropertyValue("--sheet").trim();
      const button = getComputedStyle(document.querySelector(".primary"));
      const luminance = (hex) => {
        const channels = hex.match(/[\da-f]{2}/gi).map((channel) => parseInt(channel, 16) / 255);
        const linear = channels.map((value) => value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4);
        return linear[0] * 0.2126 + linear[1] * 0.7152 + linear[2] * 0.0722;
      };
      const contrast = (a, b) => {
        const values = [luminance(a), luminance(b)].sort((x, y) => y - x);
        return (values[0] + 0.05) / (values[1] + 0.05);
      };
      const rgbToHex = (rgb) => `#${rgb.match(/\d+/g).slice(0, 3).map((value) => Number(value).toString(16).padStart(2, "0")).join("")}`;
      return {
        text: ["--ink", "--muted", "--action"].flatMap((token) => [page, sheet].map((background) => contrast(root.getPropertyValue(token).trim(), background))),
        button: contrast(rgbToHex(button.color), rgbToHex(button.backgroundColor)),
      };
    });
    expect(Math.min(...ratios.text, ratios.button)).toBeGreaterThanOrEqual(4.5);
  }
});

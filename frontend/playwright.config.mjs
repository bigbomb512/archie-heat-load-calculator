import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./tests",
  timeout: 15_000,
  use: {
    baseURL: "http://127.0.0.1:4173",
    headless: true,
  },
  webServer: {
    command: "cd .. && python3 -m backend.web_app --port 4173",
    url: "http://127.0.0.1:4173",
    reuseExistingServer: false,
  },
});

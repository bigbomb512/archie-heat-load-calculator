import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./tests",
  timeout: 15_000,
  use: {
    baseURL: "http://127.0.0.1:4173",
    headless: true,
  },
  webServer: {
    command: "cd .. && ARCHIE_PAGE_READING=off ARCHIE_DESIGN_WEATHER_TABLE=/nonexistent ARCHIE_U_VALUE_TABLE=/nonexistent OPENROUTER_API_KEY= OPENAI_API_KEY= ANTHROPIC_API_KEY= DEEPSEEK_API_KEY= python3 -m backend.web_app --port 4173",
    url: "http://127.0.0.1:4173",
    reuseExistingServer: false,
  },
});

import { defineConfig, devices } from "@playwright/test";

const frontendPort = 3001;

export default defineConfig({
  testDir: "./tests/e2e",
  fullyParallel: false,
  retries: process.env.CI ? 2 : 0,
  workers: process.env.CI ? 1 : undefined,
  reporter: process.env.CI ? "github" : "list",
  use: {
    baseURL: `http://localhost:${frontendPort}`,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],
  webServer: process.env.EPICK_E2E_EXTERNAL_SERVER
    ? undefined
    : {
        command: `npm run dev -- --port ${frontendPort}`,
        url: `http://localhost:${frontendPort}`,
        reuseExistingServer: !process.env.CI,
        timeout: 120_000,
      },
});

import { defineConfig, devices } from '@playwright/test'

const CI = !!process.env.CI

// E2E config. A dev-server `webServer` block is added when the first
// navigating spec lands (auth flow); the bootstrap smoke spec needs no server.
export default defineConfig({
  testDir: './playwright',
  fullyParallel: true,
  forbidOnly: CI,
  retries: CI ? 2 : 0,
  reporter: 'list',
  use: {
    baseURL: 'http://localhost:5173',
    trace: 'on-first-retry',
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
})

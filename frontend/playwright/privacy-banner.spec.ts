/**
 * E2E journey for Slice 02: Privacy Mode Banner.
 *
 * Prerequisite: `make dev` with a fresh DB, ADEPTUS_ADMIN_USER and
 * ADEPTUS_ADMIN_PASSWORD env vars set (see .env.example).
 *
 * Guard: set E2E_STACK=1 to opt-in to the navigating tests that require the
 * compose stack to be running on http://localhost:5173. Without this env var
 * (i.e. in unit CI where no backend is available) the test is skipped so
 * `make test` stays green without a live stack.
 *
 * Journey (mirrors acceptance-criteria step 4 and the E2E section of the
 * Test Plan in the slice spec):
 *   1. Log in as admin.
 *   2. Create an engagement with "Cloud LLM enabled" toggled on.
 *   3. Navigate to its workspace; assert banner contains "Cloud enabled".
 *   4. Create a second engagement with default settings (local only).
 *   5. Navigate to its workspace; assert banner contains "Local only".
 */

import { test, expect, type Page } from '@playwright/test'

// Skip the whole suite unless the compose/sandbox stack is explicitly available.
const STACK_AVAILABLE = !!process.env.E2E_STACK

// ---------------------------------------------------------------------------
// Credentials. Usernames may default (they are not secret); passwords must be
// supplied via env when the stack is live, so the spec fails loudly rather than
// guessing weak defaults against a real backend.
// ---------------------------------------------------------------------------

function requiredPassword(name: string): string {
  const value = process.env[name]
  if (STACK_AVAILABLE && !value) {
    throw new Error(`${name} must be set when E2E_STACK=1`)
  }
  return value ?? ''
}

const ADMIN_USERNAME = process.env.ADEPTUS_ADMIN_USER ?? 'admin'
const ADMIN_PASSWORD = requiredPassword('ADEPTUS_ADMIN_PASSWORD')

const CLOUD_ENGAGEMENT_NAME = `Privacy Cloud E2E ${Date.now()}`
const LOCAL_ENGAGEMENT_NAME = `Privacy Local E2E ${Date.now()}`

// ---------------------------------------------------------------------------
// Helper: log in via the login form.
// ---------------------------------------------------------------------------

async function loginAs(page: Page, username: string, password: string) {
  await page.goto('/login')
  await page.getByLabel(/username/i).fill(username)
  await page.getByLabel(/password/i).fill(password)
  await page.getByRole('button', { name: /log in/i }).click()
  // Wait for redirect to /engagements after successful login.
  await page.waitForURL('**/engagements', { timeout: 10_000 })
}

// ---------------------------------------------------------------------------
// Full journey
// ---------------------------------------------------------------------------

test.describe('Privacy Mode Banner journey', () => {
  test(
    'cloud-enabled engagement shows Cloud enabled banner; default engagement shows Local only banner',
    async ({ page }) => {
      // Guard: skip when compose stack is not running.
      test.skip(!STACK_AVAILABLE, 'Set E2E_STACK=1 to run against the compose stack')

      // ------------------------------------------------------------------
      // Step 1: Log in as admin.
      // ------------------------------------------------------------------
      await loginAs(page, ADMIN_USERNAME, ADMIN_PASSWORD)
      await expect(page).toHaveURL(/\/engagements/)

      // ------------------------------------------------------------------
      // Step 2: Create an engagement with "Cloud LLM enabled" toggled on.
      // ------------------------------------------------------------------
      await page.getByRole('button', { name: /new engagement/i }).click()

      const dialog = page.getByRole('dialog')
      await expect(dialog).toBeVisible()

      await dialog.getByLabel(/name/i).fill(CLOUD_ENGAGEMENT_NAME)
      await dialog.getByLabel(/scope/i).fill('10.0.0.0/8')

      // Flip the "Cloud LLM enabled" toggle on (it defaults to off / local_only).
      const cloudToggle = dialog.getByRole('switch', { name: /cloud llm enabled/i })
      await expect(cloudToggle).toBeVisible()
      await cloudToggle.click()

      await dialog.getByRole('button', { name: /create/i }).click()

      // Dialog closes on success; card appears in the list.
      await expect(dialog).not.toBeVisible()
      await expect(page.getByText(CLOUD_ENGAGEMENT_NAME)).toBeVisible()

      // ------------------------------------------------------------------
      // Step 3: Navigate to the cloud engagement workspace and assert banner.
      // ------------------------------------------------------------------
      // Click the "Open" link next to the newly created engagement.
      await page.getByText(CLOUD_ENGAGEMENT_NAME).locator('..').getByRole('link', { name: /open/i }).click()
      await page.waitForURL('**/workspace', { timeout: 10_000 })

      // The privacy banner must contain "Cloud enabled".
      const cloudBanner = page.getByRole('status')
      await expect(cloudBanner).toContainText('Cloud enabled', { timeout: 5_000 })

      // ------------------------------------------------------------------
      // Step 4: Go back to the engagements list and create a default engagement.
      // ------------------------------------------------------------------
      await page.goto('/engagements')
      await expect(page).toHaveURL(/\/engagements/)

      await page.getByRole('button', { name: /new engagement/i }).click()

      const dialog2 = page.getByRole('dialog')
      await expect(dialog2).toBeVisible()

      await dialog2.getByLabel(/name/i).fill(LOCAL_ENGAGEMENT_NAME)
      await dialog2.getByLabel(/scope/i).fill('172.16.0.0/12')
      // Leave the toggle unchecked (default local_only).

      await dialog2.getByRole('button', { name: /create/i }).click()

      await expect(dialog2).not.toBeVisible()
      await expect(page.getByText(LOCAL_ENGAGEMENT_NAME)).toBeVisible()

      // ------------------------------------------------------------------
      // Step 5: Navigate to the local engagement workspace and assert banner.
      // ------------------------------------------------------------------
      await page.getByText(LOCAL_ENGAGEMENT_NAME).locator('..').getByRole('link', { name: /open/i }).click()
      await page.waitForURL('**/workspace', { timeout: 10_000 })

      // The privacy banner must contain "Local only".
      const localBanner = page.getByRole('status')
      await expect(localBanner).toContainText('Local only', { timeout: 5_000 })
    },
  )
})

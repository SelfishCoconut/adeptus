/**
 * E2E journey for Slice 04: Tool Runner panel (httpx, light path).
 *
 * Prerequisite: `make dev` with a fresh DB plus `make sandbox` (Juice Shop on
 * http://localhost:3000), ADEPTUS_ADMIN_USER / ADEPTUS_ADMIN_PASSWORD set.
 *
 * Guard: set E2E_STACK=1 to opt-in to the navigating test that requires the
 * compose + sandbox stack on http://localhost:5173. Without it (unit CI, no
 * backend) the test is skipped so `make test` stays green without a live stack.
 *
 * Journey (mirrors the E2E section of the Test Plan and the acceptance
 * criteria):
 *   1. Log in as admin.
 *   2. Create an engagement and open its workspace.
 *   3. Select the httpx tool in the Tool Runner.
 *   4. Choose the `quick` preset.
 *   5. Run against the sandbox (http://localhost:3000).
 *   6. Assert streamed output appears in the console.
 *   7. Assert the exit-code badge shows a completed run (exit 0).
 */

import { test, expect, type Page } from '@playwright/test'

const STACK_AVAILABLE = !!process.env.E2E_STACK

function requiredPassword(name: string): string {
  const value = process.env[name]
  if (STACK_AVAILABLE && !value) {
    throw new Error(`${name} must be set when E2E_STACK=1`)
  }
  return value ?? ''
}

const ADMIN_USERNAME = process.env.ADEPTUS_ADMIN_USER ?? 'admin'
const ADMIN_PASSWORD = requiredPassword('ADEPTUS_ADMIN_PASSWORD')

const ENGAGEMENT_NAME = `Tool Runner E2E ${Date.now()}`

async function loginAs(page: Page, username: string, password: string) {
  await page.goto('/login')
  await page.getByLabel(/username/i).fill(username)
  await page.getByLabel(/password/i).fill(password)
  await page.getByRole('button', { name: /log in/i }).click()
  await page.waitForURL('**/engagements', { timeout: 10_000 })
}

test.describe('Tool Runner panel journey', () => {
  test('admin runs httpx quick preset against the sandbox and sees exit 0', async ({ page }) => {
    test.skip(!STACK_AVAILABLE, 'Set E2E_STACK=1 to run against the compose stack')

    // Step 1: Log in.
    await loginAs(page, ADMIN_USERNAME, ADMIN_PASSWORD)

    // Step 2: Create an engagement and open its workspace.
    await page.getByRole('button', { name: /new engagement/i }).click()
    const dialog = page.getByRole('dialog')
    await expect(dialog).toBeVisible()
    await dialog.getByLabel(/name/i).fill(ENGAGEMENT_NAME)
    await dialog.getByLabel(/scope/i).fill('127.0.0.1/32')
    await dialog.getByRole('button', { name: /create/i }).click()
    await expect(dialog).not.toBeVisible()

    await page.getByRole('link', { name: /open/i }).first().click()
    await page.waitForURL('**/workspace', { timeout: 10_000 })

    // Step 3: Select the httpx tool.
    await page.getByLabel(/^tool$/i).selectOption('httpx/run_httpx')

    // Step 4: Choose the quick preset.
    await page.getByLabel(/preset/i).selectOption('quick')

    // The target field pre-fills with the sandbox URL.
    await expect(page.getByLabel('target')).toHaveValue('http://localhost:3000')

    // Step 5: Run.
    await page.getByRole('button', { name: /^run$/i }).click()

    // Step 6: Streamed output appears in the console.
    await expect(page.getByTestId('tool-output')).not.toBeEmpty()

    // Step 7: The exit-code badge shows a completed run.
    await expect(page.getByText(/completed · exit 0/i)).toBeVisible({ timeout: 15_000 })
  })
})

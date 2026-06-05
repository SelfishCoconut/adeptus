/**
 * E2E journey for Slice 10: admin views the tamper-evident audit log (§14).
 *
 * Prerequisite: `make dev` with a fresh DB plus `make sandbox` (Juice Shop on
 * http://localhost:3000), ADEPTUS_ADMIN_USER / ADEPTUS_ADMIN_PASSWORD set.
 *
 * Guard: set E2E_STACK=1 to opt-in to the navigating test that requires the
 * compose + sandbox stack on http://localhost:5173. Without it (unit CI, no
 * backend) the test is skipped so `make test` stays green without a live stack.
 *
 * Journey (mirrors the E2E section of the Test Plan + acceptance criteria):
 *   1. Log in as admin.
 *   2. Create an engagement and open its workspace.
 *   3. Perform an audited engagement action (run the httpx tool against the sandbox,
 *      which emits a `tool_run` audit entry).
 *   4. Expand the admin-only "Audit log" panel.
 *   5. Assert the new entry appears with its action, and the `Self-approved` column
 *      header exists (values populated by Slice 16).
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

const ENGAGEMENT_NAME = `Audit Log E2E ${Date.now()}`

async function loginAs(page: Page, username: string, password: string) {
  await page.goto('/login')
  await page.getByLabel(/username/i).fill(username)
  await page.getByLabel(/password/i).fill(password)
  await page.getByRole('button', { name: /log in/i }).click()
  await page.waitForURL('**/engagements', { timeout: 10_000 })
}

test.describe('Audit log panel journey', () => {
  test('admin runs a tool then sees the audit entry in the admin audit panel', async ({
    page,
  }) => {
    test.skip(!STACK_AVAILABLE, 'Set E2E_STACK=1 to run against the compose stack')

    // Step 1: Log in as admin.
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

    // Step 3: Run the httpx tool against the sandbox to emit a tool_run audit entry.
    await page.getByLabel(/^tool$/i).selectOption('httpx/run_httpx')
    await page.getByLabel(/preset/i).selectOption('quick')
    await page.getByRole('button', { name: /^run$/i }).click()
    await expect(page.getByText(/completed · exit 0/i)).toBeVisible({ timeout: 15_000 })

    // Step 4: Expand the admin-only Audit log panel.
    await page.getByText('Audit log').click()

    // Step 5: The audit table shows the tool_run entry and the Self-approved header.
    const auditTable = page.getByRole('table')
    await expect(auditTable.getByText('tool_run').first()).toBeVisible({ timeout: 10_000 })
    await expect(auditTable.getByText('Self-approved')).toBeVisible()
  })
})

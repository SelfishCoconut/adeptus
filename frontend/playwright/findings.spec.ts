/**
 * E2E journey for engagement findings (Slice 19).
 *
 * Opt-in via E2E_STACK=1 (skipped in unit CI so `make test-frontend` stays green
 * without a live backend — same pattern as graph.spec.ts / engagements.spec.ts).
 *
 * One journey (slice-19 test plan): log in, open an engagement's Findings tab,
 * add a finding (severity High), flip verification to Verified, flip remediation
 * to Fixed, then delete it (it disappears from the list).
 *
 * Selectors:
 *   - Right pane region:   role=region name="Graph"
 *   - Findings tab:        role=button name="Findings"
 *   - New finding button:  role=button name="New finding"
 *   - Dialog fields:       label "Title" / "Severity" / "Description"
 *   - Status pickers:      label "Verification status" / "Remediation status"
 *
 * Prerequisite: `make dev` (compose stack), ADEPTUS_ADMIN_USER /
 * ADEPTUS_ADMIN_PASSWORD env vars set.
 */

import { test, expect, type Page, type Locator } from '@playwright/test'

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

async function loginAs(page: Page, username: string, password: string) {
  await page.goto('/login')
  await page.getByLabel(/username/i).fill(username)
  await page.getByLabel(/password/i).fill(password)
  await page.getByRole('button', { name: /log in/i }).click()
  await page.waitForURL('**/engagements', { timeout: 10_000 })
}

/** Create an engagement, open its workspace, switch to the Findings tab. */
async function openFindingsTab(page: Page, name: string): Promise<Locator> {
  await page.getByRole('button', { name: /new engagement/i }).click()
  const dialog = page.getByRole('dialog')
  await expect(dialog).toBeVisible()
  await dialog.getByLabel(/name/i).fill(name)
  await dialog.getByLabel(/scope/i).fill('10.0.0.0/24')
  await dialog.getByRole('button', { name: /create/i }).click()
  await expect(dialog).not.toBeVisible()

  await page.getByRole('link', { name: /open/i }).first().click()
  await page.waitForURL('**/workspace', { timeout: 10_000 })

  const pane = page.getByRole('region', { name: 'Graph' })
  await expect(pane).toBeVisible({ timeout: 8_000 })
  await pane.getByRole('button', { name: 'Findings' }).click()
  return pane
}

test.describe('Findings lifecycle journey', () => {
  test('admin adds a finding, advances its statuses, and deletes it', async ({ page }) => {
    test.skip(!STACK_AVAILABLE, 'Set E2E_STACK=1 to run against the compose stack')

    await loginAs(page, ADMIN_USERNAME, ADMIN_PASSWORD)
    const pane = await openFindingsTab(page, `Findings E2E ${Date.now()}`)

    // Empty state.
    await expect(pane.getByText('No findings yet — add one.')).toBeVisible({ timeout: 5_000 })

    // Create a finding with severity High.
    await pane.getByRole('button', { name: 'New finding' }).click()
    const dialog = page.getByRole('dialog')
    await expect(dialog.getByRole('heading', { name: 'New finding' })).toBeVisible()
    await dialog.getByLabel('Title').fill('Reflected XSS on /search')
    await dialog.getByLabel('Severity').selectOption('high')
    await dialog.getByLabel('Description').fill('q parameter is reflected unescaped')
    await dialog.getByRole('button', { name: 'Create' }).click()
    await expect(dialog).not.toBeVisible({ timeout: 8_000 })

    const row = pane.getByRole('row').filter({ hasText: 'Reflected XSS on /search' })
    await expect(row).toBeVisible({ timeout: 8_000 })
    await expect(row.getByText('High')).toBeVisible()

    // Flip verification → Verified, remediation → Fixed.
    await row.getByLabel('Verification status').selectOption('verified')
    await expect(row.getByLabel('Verification status')).toHaveValue('verified', { timeout: 8_000 })

    await row.getByLabel('Remediation status').selectOption('fixed')
    await expect(row.getByLabel('Remediation status')).toHaveValue('fixed', { timeout: 8_000 })

    // Both statuses persist across a page reload.
    await page.reload()
    await page.waitForURL('**/workspace', { timeout: 10_000 })
    const pane2 = page.getByRole('region', { name: 'Graph' })
    await pane2.getByRole('button', { name: 'Findings' }).click()
    const row2 = pane2.getByRole('row').filter({ hasText: 'Reflected XSS on /search' })
    await expect(row2.getByLabel('Verification status')).toHaveValue('verified')
    await expect(row2.getByLabel('Remediation status')).toHaveValue('fixed')

    // Delete — it disappears from the list.
    await row2.getByRole('button', { name: 'Delete' }).click()
    await expect(
      pane2.getByText('Reflected XSS on /search'),
    ).not.toBeVisible({ timeout: 8_000 })
  })
})

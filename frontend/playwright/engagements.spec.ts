/**
 * E2E journey for Slice 01: Engagement CRUD + Membership.
 *
 * Prerequisite: `make dev` with a fresh DB, ADEPTUS_ADMIN_USER and
 * ADEPTUS_TEST_USER env vars set (see .env.example).
 *
 * Guard: set E2E_STACK=1 to opt-in to the navigating tests that require the
 * compose stack to be running on http://localhost:5173. Without this env var
 * (i.e. in unit CI where no backend is available) the test is skipped so
 * `make test` stays green without a live stack.
 *
 * Journey (mirrors acceptance-criteria steps 1–6 and the E2E section of the
 * Test Plan at line 416 of the slice spec):
 *   1. Log in as admin.
 *   2. Create an engagement named "E2E Test Engagement".
 *   3. Open the engagement workspace and navigate to the membership panel.
 *   4. Invite the test user (seeded from ADEPTUS_TEST_USER).
 *   5. Verify the test user appears in the members list.
 *   6. Log out.
 *   7. Log in as the test user.
 *   8. Verify the engagement is visible in the test user's engagements list.
 *   9. Open the workspace stub for that engagement.
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
const TEST_USERNAME = process.env.ADEPTUS_TEST_USER_USERNAME ?? 'testuser'
const TEST_PASSWORD = requiredPassword('ADEPTUS_TEST_USER_PASSWORD')

const ENGAGEMENT_NAME = `E2E Test Engagement ${Date.now()}`

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

test.describe('Engagement CRUD + Membership journey', () => {
  test('admin creates engagement, invites test user; test user sees the engagement', async ({
    page,
  }) => {
    // Guard: skip when compose stack is not running.
    test.skip(!STACK_AVAILABLE, 'Set E2E_STACK=1 to run against the compose stack')

    // ------------------------------------------------------------------
    // Step 1: Log in as admin.
    // ------------------------------------------------------------------
    await loginAs(page, ADMIN_USERNAME, ADMIN_PASSWORD)
    await expect(page).toHaveURL(/\/engagements/)

    // ------------------------------------------------------------------
    // Step 2: Create a new engagement.
    // ------------------------------------------------------------------
    await page.getByRole('button', { name: /new engagement/i }).click()

    const dialog = page.getByRole('dialog')
    await expect(dialog).toBeVisible()

    await dialog.getByLabel(/name/i).fill(ENGAGEMENT_NAME)
    await dialog.getByLabel(/scope/i).fill('192.168.1.0/24')
    await dialog.getByRole('button', { name: /create/i }).click()

    // Dialog closes on success; card appears in the list.
    await expect(dialog).not.toBeVisible()
    await expect(page.getByText(ENGAGEMENT_NAME)).toBeVisible()

    // ------------------------------------------------------------------
    // Step 3: Open the engagement workspace.
    // ------------------------------------------------------------------
    await page.getByRole('link', { name: /open/i }).first().click()
    await page.waitForURL('**/workspace', { timeout: 10_000 })

    // ------------------------------------------------------------------
    // Step 4: Open the membership panel and invite the test user.
    // ------------------------------------------------------------------
    const inviteInput = page.getByLabel(/invite member/i)
    await expect(inviteInput).toBeVisible()

    await inviteInput.fill(TEST_USERNAME)
    await page.getByRole('button', { name: /invite/i }).click()

    // ------------------------------------------------------------------
    // Step 5: Verify the test user appears in the members list.
    // ------------------------------------------------------------------
    await expect(page.getByText(TEST_USERNAME)).toBeVisible({ timeout: 5_000 })

    // ------------------------------------------------------------------
    // Step 6: Log out.
    // ------------------------------------------------------------------
    await page.getByRole('button', { name: /logout/i }).click()
    await page.waitForURL('**/login', { timeout: 10_000 })

    // ------------------------------------------------------------------
    // Step 7: Log in as the test user.
    // ------------------------------------------------------------------
    await loginAs(page, TEST_USERNAME, TEST_PASSWORD)
    await expect(page).toHaveURL(/\/engagements/)

    // ------------------------------------------------------------------
    // Step 8: Verify the engagement is visible to the test user.
    // ------------------------------------------------------------------
    await expect(page.getByText(ENGAGEMENT_NAME)).toBeVisible({ timeout: 5_000 })

    // ------------------------------------------------------------------
    // Step 9: Open the workspace stub for the engagement.
    // ------------------------------------------------------------------
    await page.getByRole('link', { name: /open/i }).first().click()
    await page.waitForURL('**/workspace', { timeout: 10_000 })
    // The workspace shell renders — confirm we landed on a workspace page.
    await expect(page).toHaveURL(/\/engagements\/.+\/workspace/)
  })
})

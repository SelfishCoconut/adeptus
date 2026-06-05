/**
 * E2E journey for Slice 14: cloud LLM + pattern-friction egress (§5.1 / §5.5 / §17.5).
 *
 * Prerequisite: `make dev` with a fresh DB. To keep CI deterministic and avoid a real cloud
 * call, the engagement is cloud-enabled with a fake ADEPTUS_ANTHROPIC_API_KEY and
 * ADEPTUS_ANTHROPIC_BASE_URL pointed at a stub that streams a fixed reply (the external-service
 * rule — no real Anthropic call in CI).
 *
 * Guard: set E2E_STACK=1 to opt-in to the navigating test that requires the compose stack on
 * http://localhost:5173. Without it the test is skipped so `make test` stays green.
 *
 * Journey (mirrors the E2E section of the Test Plan):
 *   1. Log in as admin.
 *   2. Create a CLOUD-ENABLED engagement (Slice 02 toggle) and open its workspace.
 *   3. Send a message containing a secret pattern → the friction modal appears before egress.
 *   4. Cancel → nothing sent, the composer keeps the text.
 *   5. Send again → Send anyway → the reply streams in (sent unmodified).
 *   6. Create a LOCAL-ONLY engagement, send the same secret → NO modal appears.
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

// A secret-shaped prompt the egress scanner flags (synthetic; not a real credential).
const SECRET_PROMPT = 'here is the key AKIAIOSFODNN7EXAMPLE and password=hunter2' // gitleaks:allow

async function loginAs(page: Page, username: string, password: string) {
  await page.goto('/login')
  await page.getByLabel(/username/i).fill(username)
  await page.getByLabel(/password/i).fill(password)
  await page.getByRole('button', { name: /log in/i }).click()
  await page.waitForURL('**/engagements', { timeout: 10_000 })
}

async function createEngagementAndOpen(page: Page, name: string, cloud: boolean) {
  await page.getByRole('button', { name: /new engagement/i }).click()
  const dialog = page.getByRole('dialog')
  await expect(dialog).toBeVisible()
  await dialog.getByLabel(/name/i).fill(name)
  await dialog.getByLabel(/scope/i).fill('127.0.0.1/32')
  if (cloud) {
    // The Slice-02 "Cloud LLM enabled" switch flips privacy_mode to cloud_enabled.
    await dialog.getByLabel(/cloud llm enabled/i).click()
  }
  await dialog.getByRole('button', { name: /create/i }).click()
  await expect(dialog).not.toBeVisible()
  await page.getByRole('link', { name: /open/i }).first().click()
  await page.waitForURL('**/workspace', { timeout: 10_000 })
}

test.describe('Cloud egress pattern-friction journey (Slice 14)', () => {
  test('a secret-bearing cloud send is gated behind the friction modal', async ({ page }) => {
    test.skip(!STACK_AVAILABLE, 'Set E2E_STACK=1 to run against the compose stack')

    await loginAs(page, ADMIN_USERNAME, ADMIN_PASSWORD)
    await createEngagementAndOpen(page, `Egress E2E ${Date.now()}`, true)

    // The amber cloud banner is pinned above the panes the whole time (§5.5 / §17.5).
    await expect(page.getByTestId('privacy-mode-banner')).toBeVisible()

    const chatPane = page.getByRole('region', { name: /ai chat/i })
    const composer = chatPane.getByLabel(/message the ai/i)

    // Step 3: typing a secret + Send shows the friction modal BEFORE anything is sent.
    await composer.fill(SECRET_PROMPT)
    await chatPane.getByRole('button', { name: /^send$/i }).click()
    const modal = page.getByRole('dialog')
    await expect(modal).toBeVisible()
    await expect(modal.getByText(/may contain a secret/i)).toBeVisible()

    // Step 4: Cancel keeps the composer text and sends nothing.
    await modal.getByRole('button', { name: /cancel/i }).click()
    await expect(modal).not.toBeVisible()
    await expect(composer).toHaveValue(SECRET_PROMPT)
    await expect(chatPane.getByText(SECRET_PROMPT)).toHaveCount(0)

    // Step 5: Send again → Send anyway → the message is sent unmodified and the reply streams.
    await chatPane.getByRole('button', { name: /^send$/i }).click()
    await page.getByRole('dialog').getByRole('button', { name: /send anyway/i }).click()
    await expect(chatPane.getByText(SECRET_PROMPT)).toBeVisible()
    await expect(page.getByTestId('chat-message-list')).not.toBeEmpty({ timeout: 15_000 })
  })

  test('the same secret on a local-only engagement shows NO modal', async ({ page }) => {
    test.skip(!STACK_AVAILABLE, 'Set E2E_STACK=1 to run against the compose stack')

    await loginAs(page, ADMIN_USERNAME, ADMIN_PASSWORD)
    await createEngagementAndOpen(page, `Local E2E ${Date.now()}`, false)

    const chatPane = page.getByRole('region', { name: /ai chat/i })
    await chatPane.getByLabel(/message the ai/i).fill(SECRET_PROMPT)
    await chatPane.getByRole('button', { name: /^send$/i }).click()

    // No egress to gate on local_only — the message sends directly, no modal (§5.5).
    await expect(page.getByRole('dialog')).toHaveCount(0)
    await expect(chatPane.getByText(SECRET_PROMPT)).toBeVisible()
  })
})

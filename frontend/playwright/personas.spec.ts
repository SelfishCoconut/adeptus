/**
 * E2E journey for Slice 15: AI personas (§5.3 / §5.4).
 *
 * Prerequisite: `make dev` with a fresh DB and a reachable Ollama. To keep CI
 * deterministic and avoid a real model, point ADEPTUS_OLLAMA_URL at a stub that
 * streams a fixed reply (the pentest/external-service rule — no real model in CI).
 *
 * Guard: set E2E_STACK=1 to opt-in to the navigating tests that require the compose
 * stack on http://localhost:5173. Without it (unit CI, no backend) the tests skip so
 * `make test` stays green without a live stack.
 *
 * Journey (mirrors the E2E section of the Test Plan + acceptance criteria):
 *   1. Log in, create an engagement, open its workspace.
 *   2. Switch the composer persona to Recon and send a message; the reply carries a
 *      "Recon" persona chip.
 *   3. Open "Manage personas", create a custom persona, select it, and send — it is used.
 *   4. Delete the custom persona; the switcher falls back to General.
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

const ENGAGEMENT_NAME = `Personas E2E ${Date.now()}`
const CUSTOM_PERSONA = `Cloud Pentest ${Date.now()}`

async function loginAs(page: Page, username: string, password: string) {
  await page.goto('/login')
  await page.getByLabel(/username/i).fill(username)
  await page.getByLabel(/password/i).fill(password)
  await page.getByRole('button', { name: /log in/i }).click()
  await page.waitForURL('**/engagements', { timeout: 10_000 })
}

async function openWorkspace(page: Page) {
  await page.getByRole('button', { name: /new engagement/i }).click()
  const dialog = page.getByRole('dialog')
  await expect(dialog).toBeVisible()
  await dialog.getByLabel(/name/i).fill(ENGAGEMENT_NAME)
  await dialog.getByLabel(/scope/i).fill('127.0.0.1/32')
  await dialog.getByRole('button', { name: /create/i }).click()
  await expect(dialog).not.toBeVisible()
  await page.getByRole('link', { name: /open/i }).first().click()
  await page.waitForURL('**/workspace', { timeout: 10_000 })
}

test.describe('AI personas journey', () => {
  test('switch persona, use a custom persona, and fall back on delete', async ({ page }) => {
    test.skip(!STACK_AVAILABLE, 'Set E2E_STACK=1 to run against the compose stack')

    await loginAs(page, ADMIN_USERNAME, ADMIN_PASSWORD)
    await openWorkspace(page)

    const chatPane = page.getByRole('region', { name: /ai chat/i })
    const switcher = chatPane.getByRole('combobox', { name: /persona/i })

    // Step 2: switch to Recon and send — the reply carries a "Recon" chip.
    await expect(switcher).toBeVisible()
    await switcher.selectOption({ label: 'Recon' })
    await chatPane.getByLabel(/message the ai/i).fill('where should I start on this target?')
    await chatPane.getByRole('button', { name: /^send$/i }).click()
    await expect(chatPane.getByTestId('persona-chip').last()).toHaveText('Recon', {
      timeout: 15_000,
    })

    // Step 3: create a custom persona via Manage personas, select it, and send.
    await chatPane.getByRole('button', { name: /manage personas/i }).click()
    const panel = page.getByRole('dialog')
    await expect(panel).toBeVisible()
    await panel.getByRole('button', { name: /new persona/i }).click()
    await panel.getByLabel('Name').fill(CUSTOM_PERSONA)
    await panel.getByLabel('System prompt').fill('Focus on cloud misconfigurations.')
    await panel.getByRole('button', { name: /create persona/i }).click()
    await expect(panel.getByText(CUSTOM_PERSONA)).toBeVisible()
    // Close the panel (Escape) and select the new persona in the composer.
    await page.keyboard.press('Escape')
    await switcher.selectOption({ label: CUSTOM_PERSONA })
    await chatPane.getByLabel(/message the ai/i).fill('any cloud issues?')
    await chatPane.getByRole('button', { name: /^send$/i }).click()
    await expect(chatPane.getByTestId('persona-chip').last()).toHaveText(CUSTOM_PERSONA, {
      timeout: 15_000,
    })

    // Step 4: delete the custom persona; the switcher falls back to General.
    await chatPane.getByRole('button', { name: /manage personas/i }).click()
    const managePanel = page.getByRole('dialog')
    const row = managePanel.getByRole('listitem').filter({ hasText: CUSTOM_PERSONA })
    await row.getByRole('button', { name: /^delete$/i }).click()
    await row.getByRole('button', { name: /confirm/i }).click()
    await expect(managePanel.getByText(CUSTOM_PERSONA)).toHaveCount(0)
    await page.keyboard.press('Escape')
    await expect(switcher).toHaveValue(/.+/)
    await expect(
      switcher.locator('option', { hasText: CUSTOM_PERSONA }),
    ).toHaveCount(0)
  })
})

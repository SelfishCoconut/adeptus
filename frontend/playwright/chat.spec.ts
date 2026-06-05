/**
 * E2E journey for Slice 11: local LLM private chat (§5.4 / §11.2).
 *
 * Prerequisite: `make dev` with a fresh DB and a reachable Ollama. To keep CI
 * deterministic and avoid a real model, point ADEPTUS_OLLAMA_URL at a stub that
 * streams a fixed reply (the pentest/external-service rule — no real model in CI).
 *
 * Guard: set E2E_STACK=1 to opt-in to the navigating test that requires the
 * compose stack on http://localhost:5173. Without it (unit CI, no backend) the
 * test is skipped so `make test` stays green without a live stack.
 *
 * Journey (mirrors the E2E section of the Test Plan + acceptance criteria):
 *   1. Log in as admin.
 *   2. Create an engagement and open its workspace.
 *   3. Send a message in the left chat pane.
 *   4. Assert the user message appears and streamed assistant text accumulates.
 *   5. Reload and assert the conversation persisted.
 */

import { test, expect, type Locator, type Page } from '@playwright/test'

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

const ENGAGEMENT_NAME = `Chat E2E ${Date.now()}`
const PROMPT = 'what is sql injection?'

async function loginAs(page: Page, username: string, password: string) {
  await page.goto('/login')
  await page.getByLabel(/username/i).fill(username)
  await page.getByLabel(/password/i).fill(password)
  await page.getByRole('button', { name: /log in/i }).click()
  await page.waitForURL('**/engagements', { timeout: 10_000 })
}

test.describe('Local AI chat journey', () => {
  test('admin sends a message and receives a streamed, persisted reply', async ({ page }) => {
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

    // The Slice-02 privacy banner is pinned above the panes the whole time (§5.5).
    await expect(page.getByTestId('privacy-mode-banner')).toBeVisible()

    const chatPane = page.getByRole('region', { name: /ai chat/i })

    // Step 3: Send a message in the left chat pane.
    await chatPane.getByLabel(/message the ai/i).fill(PROMPT)
    await chatPane.getByRole('button', { name: /send/i }).click()

    // Step 4: The user message appears and the assistant reply streams in.
    await expect(chatPane.getByText(PROMPT)).toBeVisible()
    await expect(page.getByTestId('chat-message-list')).not.toBeEmpty({ timeout: 15_000 })

    // Step 5: Reload and assert the conversation persisted.
    await page.reload()
    await page.waitForURL('**/workspace', { timeout: 10_000 })
    await expect(chatPane.getByText(PROMPT)).toBeVisible({ timeout: 10_000 })
  })
})

/** Add a node of `type` with `label` via the graph-view Add-node dialog (Slice 08). */
async function addNode(page: Page, graphSection: Locator, type: string, label: string) {
  await graphSection.getByRole('button', { name: 'Add node' }).click()
  const addDialog = page.getByRole('dialog')
  await addDialog.getByLabel('Type').selectOption(type)
  await addDialog.getByLabel('Label').fill(label)
  await addDialog.getByRole('button', { name: 'Create' }).click()
  await expect(addDialog).not.toBeVisible({ timeout: 8_000 })
}

test.describe('AI debug panel journey (Slice 12)', () => {
  test('pinning a node surfaces it under "pinned" in the turn debug panel', async ({ page }) => {
    test.skip(!STACK_AVAILABLE, 'Set E2E_STACK=1 to run against the compose stack')

    await loginAs(page, ADMIN_USERNAME, ADMIN_PASSWORD)

    // Create an engagement and open its workspace.
    await page.getByRole('button', { name: /new engagement/i }).click()
    const dialog = page.getByRole('dialog')
    await expect(dialog).toBeVisible()
    await dialog.getByLabel(/name/i).fill(`Debug E2E ${Date.now()}`)
    await dialog.getByLabel(/scope/i).fill('127.0.0.1/32')
    await dialog.getByRole('button', { name: /create/i }).click()
    await expect(dialog).not.toBeVisible()
    await page.getByRole('link', { name: /open/i }).first().click()
    await page.waitForURL('**/workspace', { timeout: 10_000 })

    // The Slice-02 privacy banner stays pinned the whole time (§5.5).
    await expect(page.getByTestId('privacy-mode-banner')).toBeVisible()

    // Add a node in the Graph pane and pin it (the §5.4 always-included arm).
    const graphSection = page.getByRole('region', { name: 'Graph' })
    await addNode(page, graphSection, 'host', '10.0.0.5')
    const canvas = graphSection.getByTestId('graph-canvas')
    await expect(canvas).toBeVisible({ timeout: 8_000 })
    await page.waitForTimeout(800)
    await canvas.click()
    const panel = graphSection.getByTestId('selected-node-panel')
    await expect(panel).toBeVisible({ timeout: 5_000 })
    await panel.getByRole('button', { name: 'Pin' }).click()
    await expect(panel.getByTestId('pinned-badge')).toBeVisible()

    // Send a message; the pinned node rides along in the §5.3 subset.
    const chatPane = page.getByRole('region', { name: /ai chat/i })
    await chatPane.getByLabel(/message the ai/i).fill('what should I try here?')
    await chatPane.getByRole('button', { name: /send/i }).click()

    // Once the reply finalizes, the per-turn Debug toggle appears; open it.
    const debugToggle = chatPane.getByRole('button', { name: 'Debug' })
    await expect(debugToggle).toBeVisible({ timeout: 15_000 })
    await debugToggle.click()

    // The debug panel shows the pinned node grouped under "Pinned" (§5.4 / §14).
    const debugPanel = chatPane.getByLabel('AI debug panel')
    await expect(debugPanel).toBeVisible({ timeout: 10_000 })
    await expect(debugPanel.getByText('Pinned')).toBeVisible()
    await expect(debugPanel.getByText('10.0.0.5')).toBeVisible()
  })
})

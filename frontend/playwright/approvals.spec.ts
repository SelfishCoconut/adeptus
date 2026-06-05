/**
 * E2E journey for Slice 17: soft scope enforcement (§5.2 scope arm).
 *
 * Prerequisite: `make dev` with a fresh DB, plus the deterministic Ollama stub at
 * `playwright/support/ollama-stub.py` reachable as the backend's `ADEPTUS_OLLAMA_URL`
 * (a real model can't be relied on to emit a *specific* propose_command tool-call, so
 * CLAUDE.md's "no real model in tests" rule applies — see the stub's docstring).
 *
 * The stub always proposes a light, otherwise-autonomous `httpx/run_httpx` against
 * `http://juice-shop:3000`. This engagement's scope is `10.0.0.0/24`, which EXCLUDES
 * juice-shop, so the proposal classifies `out_of_scope` (the only reason → an approval
 * card) yet the approved run targets juice-shop and is therefore sandbox-legal.
 *
 * Guard: set E2E_STACK=1 to opt in. Without it (unit CI, no backend) the test skips so
 * `make test` stays green without a live stack. NOTE: CI does not bring up the stack or
 * the stub, so this journey runs on-demand locally — like every other Playwright spec.
 *
 * Journey (mirrors the E2E acceptance criteria):
 *   1. Log in as admin.
 *   2. Create an engagement whose scope excludes juice-shop and open its workspace.
 *   3. Send a message; the stubbed model proposes an out-of-scope httpx command.
 *   4. Assert the inline approval card appears with the "target is outside the declared
 *      scope" reason and the host/scope context line.
 *   5. Approve it and assert the card flips to "Approved by @...".
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

const ENGAGEMENT_NAME = `Scope E2E ${Date.now()}`
const PROMPT = 'recon the target'

async function loginAs(page: Page, username: string, password: string) {
  await page.goto('/login')
  await page.getByLabel(/username/i).fill(username)
  await page.getByLabel(/password/i).fill(password)
  await page.getByRole('button', { name: /log in/i }).click()
  await page.waitForURL('**/engagements', { timeout: 10_000 })
}

test.describe('Soft scope enforcement journey (Slice 17)', () => {
  test('an out-of-scope proposal gates with the scope reason, then approves and runs', async ({
    page,
  }) => {
    test.skip(!STACK_AVAILABLE, 'Set E2E_STACK=1 to run against the compose stack')

    // Step 1: Log in.
    await loginAs(page, ADMIN_USERNAME, ADMIN_PASSWORD)

    // Step 2: Create an engagement whose scope EXCLUDES juice-shop, then open it.
    await page.getByRole('button', { name: /new engagement/i }).click()
    const dialog = page.getByRole('dialog')
    await expect(dialog).toBeVisible()
    await dialog.getByLabel(/name/i).fill(ENGAGEMENT_NAME)
    await dialog.getByLabel(/scope/i).fill('10.0.0.0/24')
    await dialog.getByRole('button', { name: /create/i }).click()
    await expect(dialog).not.toBeVisible()
    await page.getByRole('link', { name: /open/i }).first().click()
    await page.waitForURL('**/workspace', { timeout: 10_000 })

    // The Slice-02 privacy banner stays pinned the whole time (§5.5).
    await expect(page.getByTestId('privacy-mode-banner')).toBeVisible()

    // Step 3: Send a message; the stubbed model proposes an out-of-scope httpx command.
    const chatPane = page.getByRole('region', { name: /ai chat/i })
    await chatPane.getByLabel(/message the ai/i).fill(PROMPT)
    await chatPane.getByRole('button', { name: /send/i }).click()

    // Step 4: The inline approval card appears with the out_of_scope reason + context.
    const card = chatPane.getByTestId('approval-card')
    await expect(card).toBeVisible({ timeout: 15_000 })
    await expect(card.getByText('target is outside the declared scope')).toBeVisible()
    await expect(card.getByTestId('scope-context')).toContainText('juice-shop is not in scope')
    await expect(card.getByTestId('scope-context')).toContainText('10.0.0.0/24')

    // Step 5: Approve → the card flips to "Approved by @..." and the buttons disappear.
    await card.getByRole('button', { name: 'Approve' }).click()
    await expect(card.getByTestId('approval-decision')).toContainText('Approved by @', {
      timeout: 10_000,
    })
    await expect(card.getByRole('button', { name: 'Approve' })).toHaveCount(0)
  })
})

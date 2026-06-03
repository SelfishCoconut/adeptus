/**
 * E2E journey for Slice 06: Kill switch — Stop a running heavy run.
 *
 * This is the headline user-visible journey for the per-tool kill feature:
 *   1. Log in as admin.
 *   2. Create an engagement and open its workspace.
 *   3. Select the ``run_httpx_heavy`` tool and start a long run.
 *   4. Wait for the run to be admitted (Running... spinner or output appears).
 *   5. Click the **Stop** button.
 *   6. Assert the **Killed** badge appears in the console.
 *
 * Selectors (from ToolOutputConsole.tsx):
 *   - Stop button:   data-testid="stop-button"  (visible while !isDone)
 *   - Killed badge:  data-testid="killed-badge" (rendered when killed && isDone)
 *   - Tool output:   data-testid="tool-output"
 *   - Running spinner: role="status" with text "Running…"
 *
 * Prerequisite: `make dev` + `make sandbox` (Juice Shop on http://localhost:3000),
 * ADEPTUS_ADMIN_USER / ADEPTUS_ADMIN_PASSWORD set, ProjectDiscovery httpx binary
 * installed in the backend container PATH.
 *
 * Guard: set E2E_STACK=1 to opt-in. Without it (unit CI, no backend) the test is
 * skipped so `make test-frontend` stays green without a live stack — same pattern
 * used by tool-runner.spec.ts and tool-queue.spec.ts.
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

const ENGAGEMENT_NAME = `Kill Switch E2E ${Date.now()}`

// Long enough hold that the run is reliably still running when we click Stop.
const HOLD_SECONDS = '20'

async function loginAs(page: Page, username: string, password: string) {
  await page.goto('/login')
  await page.getByLabel(/username/i).fill(username)
  await page.getByLabel(/password/i).fill(password)
  await page.getByRole('button', { name: /log in/i }).click()
  await page.waitForURL('**/engagements', { timeout: 10_000 })
}

test.describe('Kill switch journey', () => {
  test(
    'admin starts a heavy run then clicks Stop and sees the Killed badge',
    async ({ page }) => {
      // Guard: skip when the compose + sandbox stack is not available.
      test.skip(!STACK_AVAILABLE, 'Set E2E_STACK=1 to run against the compose stack')

      // ------------------------------------------------------------------
      // Step 1: Log in as admin.
      // ------------------------------------------------------------------
      await loginAs(page, ADMIN_USERNAME, ADMIN_PASSWORD)
      await expect(page).toHaveURL(/\/engagements/)

      // ------------------------------------------------------------------
      // Step 2: Create a new engagement and open its workspace.
      // ------------------------------------------------------------------
      await page.getByRole('button', { name: /new engagement/i }).click()

      const dialog = page.getByRole('dialog')
      await expect(dialog).toBeVisible()

      await dialog.getByLabel(/name/i).fill(ENGAGEMENT_NAME)
      await dialog.getByLabel(/scope/i).fill('127.0.0.1/32')
      await dialog.getByRole('button', { name: /create/i }).click()

      await expect(dialog).not.toBeVisible()
      await expect(page.getByText(ENGAGEMENT_NAME)).toBeVisible()

      await page.getByRole('link', { name: /open/i }).first().click()
      await page.waitForURL('**/workspace', { timeout: 10_000 })

      // ------------------------------------------------------------------
      // Step 3: Select the run_httpx_heavy tool and set a long hold.
      // The form renders a <select id="tool-runner-tool"> with option values
      // in the format "server_name/tool_name".
      // ------------------------------------------------------------------
      const toolSelect = page.locator('#tool-runner-tool')
      await expect(toolSelect).toBeVisible({ timeout: 8_000 })
      await toolSelect.selectOption('httpx/run_httpx_heavy')

      // Fill the target field (pre-fills with sandbox URL; confirm or set explicitly).
      const targetInput = page.locator('#tool-runner-arg-target')
      await expect(targetInput).toBeVisible({ timeout: 5_000 })
      await targetInput.fill('http://localhost:3000')

      // Set hold_seconds high enough that the run is still active when we click Stop.
      const holdInput = page.locator('#tool-runner-arg-hold_seconds')
      await expect(holdInput).toBeVisible({ timeout: 3_000 })
      await holdInput.fill(HOLD_SECONDS)

      // ------------------------------------------------------------------
      // Step 4: Start the heavy run.
      // ------------------------------------------------------------------
      await page.getByRole('button', { name: /^run$/i }).click()

      // Wait until the run is admitted and the Running… spinner or Stop button
      // is visible. We look for the Stop button (data-testid="stop-button")
      // which appears while the run is live (running, queued, or awaiting_decision).
      // Generous timeout: the httpx probe must start before the hold begins.
      const stopButton = page.getByTestId('stop-button')
      await expect(stopButton).toBeVisible({ timeout: 20_000 })

      // Also assert the Running… spinner is showing (the run is admitted, not queued).
      // ToolOutputConsole renders role="status" with text "Running…" when !isDone
      // and not in the queued branch.
      await expect(
        page.getByRole('status').filter({ hasText: /Running/i }),
      ).toBeVisible({ timeout: 5_000 })

      // ------------------------------------------------------------------
      // Step 5: Click the Stop button.
      // ------------------------------------------------------------------
      await stopButton.click()

      // ------------------------------------------------------------------
      // Step 6: Assert the Killed badge appears.
      //
      // ToolOutputConsole renders:
      //   <Badge variant="destructive" data-testid="killed-badge">Killed</Badge>
      // when isDone && killed.
      //
      // Generous timeout: the background task must receive CancelledError,
      // persist 'killed', broadcast the WS killed chunk, and the React state
      // machine must update. Allow ~10 s.
      // ------------------------------------------------------------------
      await expect(page.getByTestId('killed-badge')).toBeVisible({ timeout: 10_000 })

      // The Stop button should no longer be visible once the run is terminal.
      await expect(stopButton).not.toBeVisible()
    },
  )
})

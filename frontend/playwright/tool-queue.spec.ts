/**
 * E2E journey for Slice 05: Tool Queue — concurrency serialization.
 *
 * Critical user journey: proves that starting two heavy runs against the same
 * sandbox host results in the second run showing a "Queued — position 1" badge,
 * and that it begins streaming once the first run completes.
 *
 * Prerequisite: `make dev` + `make sandbox` (Juice Shop on http://localhost:3000),
 * ADEPTUS_ADMIN_USER / ADEPTUS_ADMIN_PASSWORD set, ProjectDiscovery httpx binary
 * installed in the backend container PATH.
 *
 * Guard: set E2E_STACK=1 to opt-in.  Without it the test is skipped so
 * `make test` stays green without a live stack (matches the existing pattern in
 * tool-runner.spec.ts and engagements.spec.ts).
 *
 * Journey:
 *   1. Log in as admin.
 *   2. Create a new engagement and open its workspace.
 *   3. Select the ``run_httpx_heavy`` tool.
 *   4. Start the first heavy run against http://localhost:3000.
 *   5. Start a second heavy run against the same host.
 *   6. Assert the second run's output console shows the "Queued — position 1" badge.
 *   7. Wait for the first run to finish (queue strip transitions to 0 queued or
 *      second run transitions away from queued).
 *   8. Assert the second run begins streaming (console shows output or the
 *      running spinner instead of the queued badge).
 *
 * Selector rationale (from ToolOutputConsole.tsx):
 *   - Queued badge: <Badge role="status" aria-label="Queued — position N">
 *     → page.getByRole('status', { name: /Queued — position 1/i })
 *   - Queue strip: data-testid="queue-counts" (inside ToolQueueStrip.tsx)
 *   - Tool output console: data-testid="tool-output"
 *   - Running spinner: role="status" with text "Running…"
 *
 * hold_seconds=3 gives a long enough window that the queued badge is reliably
 * observable without making the test excessively slow.  The test has generous
 * timeouts because the sandbox httpx probe can take a few seconds.
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

const ENGAGEMENT_NAME = `Tool Queue E2E ${Date.now()}`

// The heavy tool holds for this many seconds.  Long enough to observe the
// queued state reliably; short enough to keep the test under ~30 s.
const HOLD_SECONDS = '3'

async function loginAs(page: Page, username: string, password: string) {
  await page.goto('/login')
  await page.getByLabel(/username/i).fill(username)
  await page.getByLabel(/password/i).fill(password)
  await page.getByRole('button', { name: /log in/i }).click()
  await page.waitForURL('**/engagements', { timeout: 10_000 })
}

test.describe('Tool Queue journey', () => {
  test(
    'second heavy run shows "Queued — position 1" then begins streaming when first finishes',
    async ({ page }) => {
      // Guard: skip when compose stack is not running.
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

      // Open the engagement workspace.
      await page.getByRole('link', { name: /open/i }).first().click()
      await page.waitForURL('**/workspace', { timeout: 10_000 })

      // ------------------------------------------------------------------
      // Step 3: Select the run_httpx_heavy tool.
      // The form renders a <select id="tool-runner-tool"> with option values
      // in the format "server_name/tool_name".
      // ------------------------------------------------------------------
      const toolSelect = page.locator('#tool-runner-tool')
      await expect(toolSelect).toBeVisible({ timeout: 8_000 })
      await toolSelect.selectOption('httpx/run_httpx_heavy')

      // The form now shows the arg fields for run_httpx_heavy.
      // target pre-fills with http://localhost:3000 (SANDBOX_TARGET in ToolRunnerForm.tsx).
      const targetInput = page.locator('#tool-runner-arg-target')
      await expect(targetInput).toBeVisible({ timeout: 5_000 })
      // Confirm target is pre-filled or fill it explicitly.
      await targetInput.fill('http://localhost:3000')

      // Set hold_seconds so the slot is held long enough to observe queuing.
      const holdInput = page.locator('#tool-runner-arg-hold_seconds')
      await expect(holdInput).toBeVisible({ timeout: 3_000 })
      await holdInput.fill(HOLD_SECONDS)

      // ------------------------------------------------------------------
      // Step 4: Start the first heavy run.
      // ------------------------------------------------------------------
      await page.getByRole('button', { name: /^run$/i }).click()

      // Wait for the first run's output console to reflect it is running
      // (either the "Running…" spinner or the output pre appears).
      // We wait for the tool-output element or running status to appear.
      await expect(
        page.getByRole('status').filter({ hasText: /Running/i })
          .or(page.getByTestId('tool-output')),
      ).toBeVisible({ timeout: 15_000 })

      // ------------------------------------------------------------------
      // Step 5: Start the second heavy run against the same host.
      // The first run is still holding the slot, so the second will queue.
      // Re-fill the form (hold_seconds might reset) and click Run again.
      // ------------------------------------------------------------------
      // The target should still be set; re-confirm hold_seconds.
      await holdInput.fill(HOLD_SECONDS)
      await page.getByRole('button', { name: /^run$/i }).click()

      // ------------------------------------------------------------------
      // Step 6: Assert the second run's console shows "Queued — position 1".
      //
      // ToolOutputConsole renders:
      //   <Badge role="status" aria-label="Queued — position 1" ...>
      //     Queued — position 1
      //   </Badge>
      //
      // The second run becomes the active run after the second click
      // (onRunStarted in ToolRunnerForm fires with the new run's id).
      // ------------------------------------------------------------------
      await expect(
        page.getByRole('status', { name: /Queued — position 1/i }),
      ).toBeVisible({ timeout: 15_000 })

      // The queue strip should also reflect activity.
      // ToolQueueStrip renders data-testid="queue-counts" with "N running / M queued"
      // only when there is activity.  It may show 1 running / 1 queued or similar.
      await expect(page.getByTestId('queue-counts')).toBeVisible({ timeout: 5_000 })
      await expect(page.getByTestId('queue-counts')).toContainText(/queued/i)

      // ------------------------------------------------------------------
      // Step 7: Wait for the first run to finish.
      // The second run should transition away from queued.
      // We wait until the "Queued — position 1" badge is no longer visible.
      // Generous timeout: hold_seconds=3 + httpx probe + scheduling overhead.
      // ------------------------------------------------------------------
      await expect(
        page.getByRole('status', { name: /Queued — position 1/i }),
      ).not.toBeVisible({ timeout: 40_000 })

      // ------------------------------------------------------------------
      // Step 8: Assert the second run begins streaming.
      // After being admitted, the console transitions to the running/done state:
      // either a "Running…" spinner (if still in-flight) or the completed badge
      // or the tool-output pre (streaming has started).
      // ------------------------------------------------------------------
      await expect(
        page.getByRole('status').filter({ hasText: /Running/i })
          .or(page.getByTestId('tool-output'))
          .or(page.getByText(/completed · exit/i)),
      ).toBeVisible({ timeout: 15_000 })
    },
  )
})

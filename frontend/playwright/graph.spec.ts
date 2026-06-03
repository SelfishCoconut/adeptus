/**
 * E2E journey for Slice 07: Graph data model — node lifecycle.
 *
 * Journey:
 *   1. Log in as admin.
 *   2. Create an engagement and open its workspace.
 *   3. Assert the Graph section shows the empty state.
 *   4. Add a `host` node via NodeEditDialog; assert it appears in the list.
 *   5. Edit the node's label; assert the new label is shown.
 *   6. Delete the node; assert it disappears from the live list.
 *   7. Open History (Show history toggle); assert the deleted node is listed.
 *   8. Click Undo; assert the node reappears in the live list.
 *
 * Selectors (all role/text — no testids added):
 *   - Graph section:     aria-label="Graph"
 *   - Empty state:       text "No graph entities yet — add one."
 *   - Add node button:   role=button name="Add node"
 *   - Dialog title:      role=heading name="Add Node" / "Edit Node"
 *   - Type select:       label "Type"  (id="node-type")
 *   - Label input:       label "Label" (id="node-label")
 *   - Submit (create):   role=button name="Create"
 *   - Submit (save):     role=button name="Save"
 *   - Edit row button:   role=button name="Edit" (scoped to row)
 *   - Delete row button: role=button name="Delete" (scoped to row)
 *   - Show history btn:  role=button name="Show history"
 *   - History empty:     text "No deleted entities."
 *   - Undo button:       role=button name="Undo" (scoped to history row)
 *
 * Guard: set E2E_STACK=1 to opt-in. Without it (unit CI, no backend) the test
 * is skipped so `make test-frontend` stays green without a live stack — the same
 * pattern used by kill-switch.spec.ts and engagements.spec.ts.
 *
 * Prerequisite: `make dev` (compose stack), ADEPTUS_ADMIN_USER /
 * ADEPTUS_ADMIN_PASSWORD env vars set.
 */

import { test, expect, type Page } from '@playwright/test'

// ---------------------------------------------------------------------------
// Guard
// ---------------------------------------------------------------------------

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

const ENGAGEMENT_NAME = `Graph E2E ${Date.now()}`

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

async function loginAs(page: Page, username: string, password: string) {
  await page.goto('/login')
  await page.getByLabel(/username/i).fill(username)
  await page.getByLabel(/password/i).fill(password)
  await page.getByRole('button', { name: /log in/i }).click()
  await page.waitForURL('**/engagements', { timeout: 10_000 })
}

// ---------------------------------------------------------------------------
// Journey
// ---------------------------------------------------------------------------

test.describe('Graph node lifecycle journey', () => {
  test(
    'admin adds, edits, deletes, and undoes a graph node',
    async ({ page }) => {
      // Guard: skip when the compose + sandbox stack is not available.
      test.skip(!STACK_AVAILABLE, 'Set E2E_STACK=1 to run against the compose stack')

      // --------------------------------------------------------------------
      // Step 1: Log in as admin.
      // --------------------------------------------------------------------
      await loginAs(page, ADMIN_USERNAME, ADMIN_PASSWORD)
      await expect(page).toHaveURL(/\/engagements/)

      // --------------------------------------------------------------------
      // Step 2: Create a new engagement and open its workspace.
      // --------------------------------------------------------------------
      await page.getByRole('button', { name: /new engagement/i }).click()

      const dialog = page.getByRole('dialog')
      await expect(dialog).toBeVisible()

      await dialog.getByLabel(/name/i).fill(ENGAGEMENT_NAME)
      await dialog.getByLabel(/scope/i).fill('10.0.0.0/24')
      await dialog.getByRole('button', { name: /create/i }).click()

      await expect(dialog).not.toBeVisible()
      await expect(page.getByText(ENGAGEMENT_NAME)).toBeVisible()

      await page.getByRole('link', { name: /open/i }).first().click()
      await page.waitForURL('**/workspace', { timeout: 10_000 })

      // Scope the remaining selectors to the Graph section of the workspace.
      const graphSection = page.getByRole('region', { name: 'Graph' })
      await expect(graphSection).toBeVisible({ timeout: 8_000 })

      // --------------------------------------------------------------------
      // Step 3: Assert the empty state is shown.
      // --------------------------------------------------------------------
      await expect(
        graphSection.getByText('No graph entities yet — add one.'),
      ).toBeVisible({ timeout: 5_000 })

      // --------------------------------------------------------------------
      // Step 4: Add a host node.
      // --------------------------------------------------------------------
      await graphSection.getByRole('button', { name: 'Add node' }).click()

      const addDialog = page.getByRole('dialog')
      await expect(addDialog).toBeVisible()
      await expect(addDialog.getByRole('heading', { name: 'Add Node' })).toBeVisible()

      // Type defaults to "host" — confirm or set explicitly.
      await addDialog.getByLabel('Type').selectOption('host')
      await addDialog.getByLabel('Label').fill('10.0.0.5')
      await addDialog.getByRole('button', { name: 'Create' }).click()

      // Dialog closes on success.
      await expect(addDialog).not.toBeVisible({ timeout: 8_000 })

      // Node appears in the live list.
      await expect(graphSection.getByText('10.0.0.5')).toBeVisible({ timeout: 8_000 })

      // Empty state is gone.
      await expect(
        graphSection.getByText('No graph entities yet — add one.'),
      ).not.toBeVisible()

      // --------------------------------------------------------------------
      // Step 5: Edit the node's label.
      // --------------------------------------------------------------------
      // Click the Edit button in the row that contains "10.0.0.5".
      const liveRow = graphSection.getByRole('row').filter({ hasText: '10.0.0.5' })
      await liveRow.getByRole('button', { name: 'Edit' }).click()

      const editDialog = page.getByRole('dialog')
      await expect(editDialog).toBeVisible()
      await expect(editDialog.getByRole('heading', { name: 'Edit Node' })).toBeVisible()

      // Clear and fill new label.
      const labelInput = editDialog.getByLabel('Label')
      await labelInput.clear()
      await labelInput.fill('10.0.0.99')
      await editDialog.getByRole('button', { name: 'Save' }).click()

      // Dialog closes on success.
      await expect(editDialog).not.toBeVisible({ timeout: 8_000 })

      // New label appears; old label gone.
      await expect(graphSection.getByText('10.0.0.99')).toBeVisible({ timeout: 8_000 })
      await expect(graphSection.getByText('10.0.0.5')).not.toBeVisible()

      // --------------------------------------------------------------------
      // Step 6: Delete the node.
      // --------------------------------------------------------------------
      const editedRow = graphSection.getByRole('row').filter({ hasText: '10.0.0.99' })
      await editedRow.getByRole('button', { name: 'Delete' }).click()

      // Node disappears from the live list.
      await expect(graphSection.getByText('10.0.0.99')).not.toBeVisible({ timeout: 8_000 })

      // Empty state reappears.
      await expect(
        graphSection.getByText('No graph entities yet — add one.'),
      ).toBeVisible({ timeout: 5_000 })

      // --------------------------------------------------------------------
      // Step 7: Open History and assert the deleted node is listed.
      // --------------------------------------------------------------------
      await graphSection.getByRole('button', { name: 'Show history' }).click()

      // Panel expands; deleted node entry appears.
      await expect(graphSection.getByText('10.0.0.99')).toBeVisible({ timeout: 8_000 })

      // The history empty state should NOT be shown (there is one deleted node).
      await expect(graphSection.getByText('No deleted entities.')).not.toBeVisible()

      // --------------------------------------------------------------------
      // Step 8: Undo — assert the node reappears in the live list.
      // --------------------------------------------------------------------
      const historyRow = graphSection.getByRole('row').filter({ hasText: '10.0.0.99' })
      await historyRow.getByRole('button', { name: 'Undo' }).click()

      // Node reappears in the live list after undo.
      // The live GraphNodeList re-fetches on mutation success.
      await expect(graphSection.getByText('10.0.0.99')).toBeVisible({ timeout: 8_000 })

      // The deleted node is no longer in the history panel (history is now empty).
      await expect(graphSection.getByText('No deleted entities.')).toBeVisible({
        timeout: 5_000,
      })
    },
  )
})

/**
 * E2E journeys for the engagement graph.
 *
 * Two journeys, both opt-in via E2E_STACK=1 (skipped in unit CI so
 * `make test-frontend` stays green without a live backend — same pattern as
 * kill-switch.spec.ts / engagements.spec.ts):
 *
 *   A. Slice 07 — node lifecycle in the LIST view (add / edit / delete /
 *      history / undo). Since Slice 08 made the force-directed canvas the
 *      default view, this journey first toggles to "List" so the Slice 07
 *      keyboard-accessible affordances (and these selectors) keep working
 *      (Slice 08 Risk 5 — accessibility must not regress).
 *
 *   B. Slice 08 — the live Cytoscape canvas: add a node, assert the canvas
 *      region renders (not the empty state), select the node, pin it, reload
 *      and confirm the pin persists, then Edit / Delete from the selected-node
 *      panel. A multi-node render is asserted at the end.
 *
 * Note on edges: there is no edge-authoring UI (edge create/delete are
 * API-only on the Slice 07 surface; canvas-draw edge authoring is deferred —
 * Slice 22). So these journeys add nodes only, matching the actual UI.
 *
 * Canvas selectors (role/text/testid):
 *   - Graph section:      role=region name="Graph"
 *   - View toggle:        role=button name="Graph" / "List"
 *   - Live canvas:        data-testid="graph-canvas"
 *   - Empty state:        data-testid="graph-canvas-empty" / text "No graph entities yet — add one."
 *   - Add node button:    role=button name="Add node"   (graph-view toolbar)
 *   - Selected panel:     data-testid="selected-node-panel"
 *   - Pin / Unpin:        role=button name="Pin" / "Unpin"
 *   - Pinned badge:       data-testid="pinned-badge"
 *
 * Prerequisite: `make dev` (compose stack), ADEPTUS_ADMIN_USER /
 * ADEPTUS_ADMIN_PASSWORD env vars set.
 */

import { test, expect, type Page, type Locator } from '@playwright/test'

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

/** Create an engagement and open its workspace; returns the Graph region. */
async function createEngagementAndOpenGraph(page: Page, name: string): Promise<Locator> {
  await page.getByRole('button', { name: /new engagement/i }).click()

  const dialog = page.getByRole('dialog')
  await expect(dialog).toBeVisible()
  await dialog.getByLabel(/name/i).fill(name)
  await dialog.getByLabel(/scope/i).fill('10.0.0.0/24')
  await dialog.getByRole('button', { name: /create/i }).click()

  await expect(dialog).not.toBeVisible()
  await expect(page.getByText(name)).toBeVisible()

  await page.getByRole('link', { name: /open/i }).first().click()
  await page.waitForURL('**/workspace', { timeout: 10_000 })

  const graphSection = page.getByRole('region', { name: 'Graph' })
  await expect(graphSection).toBeVisible({ timeout: 8_000 })
  return graphSection
}

/** Add a node of `type` with `label` via the graph-view Add-node dialog. */
async function addNode(page: Page, graphSection: Locator, type: string, label: string) {
  await graphSection.getByRole('button', { name: 'Add node' }).click()
  const addDialog = page.getByRole('dialog')
  await expect(addDialog).toBeVisible()
  await addDialog.getByLabel('Type').selectOption(type)
  await addDialog.getByLabel('Label').fill(label)
  await addDialog.getByRole('button', { name: 'Create' }).click()
  await expect(addDialog).not.toBeVisible({ timeout: 8_000 })
}

// ---------------------------------------------------------------------------
// Journey A — Slice 07 node lifecycle (List view)
// ---------------------------------------------------------------------------

test.describe('Graph node lifecycle journey (list view)', () => {
  test('admin adds, edits, deletes, and undoes a graph node', async ({ page }) => {
    test.skip(!STACK_AVAILABLE, 'Set E2E_STACK=1 to run against the compose stack')

    await loginAs(page, ADMIN_USERNAME, ADMIN_PASSWORD)
    const graphSection = await createEngagementAndOpenGraph(
      page,
      `Graph E2E list ${Date.now()}`,
    )

    // Empty state shows in the default (graph) view, then switch to List so the
    // Slice 07 row-based affordances are exercised (Risk 5).
    await expect(
      graphSection.getByText('No graph entities yet — add one.'),
    ).toBeVisible({ timeout: 5_000 })
    await graphSection.getByRole('button', { name: 'List' }).click()

    // Add a host node.
    await graphSection.getByRole('button', { name: 'Add node' }).click()
    const addDialog = page.getByRole('dialog')
    await expect(addDialog.getByRole('heading', { name: 'Add Node' })).toBeVisible()
    await addDialog.getByLabel('Type').selectOption('host')
    await addDialog.getByLabel('Label').fill('10.0.0.5')
    await addDialog.getByRole('button', { name: 'Create' }).click()
    await expect(addDialog).not.toBeVisible({ timeout: 8_000 })
    await expect(graphSection.getByText('10.0.0.5')).toBeVisible({ timeout: 8_000 })

    // Edit the node's label.
    const liveRow = graphSection.getByRole('row').filter({ hasText: '10.0.0.5' })
    await liveRow.getByRole('button', { name: 'Edit' }).click()
    const editDialog = page.getByRole('dialog')
    await expect(editDialog.getByRole('heading', { name: 'Edit Node' })).toBeVisible()
    const labelInput = editDialog.getByLabel('Label')
    await labelInput.clear()
    await labelInput.fill('10.0.0.99')
    await editDialog.getByRole('button', { name: 'Save' }).click()
    await expect(editDialog).not.toBeVisible({ timeout: 8_000 })
    await expect(graphSection.getByText('10.0.0.99')).toBeVisible({ timeout: 8_000 })

    // Delete the node.
    const editedRow = graphSection.getByRole('row').filter({ hasText: '10.0.0.99' })
    await editedRow.getByRole('button', { name: 'Delete' }).click()
    await expect(graphSection.getByText('10.0.0.99')).not.toBeVisible({ timeout: 8_000 })

    // History → Undo restores it to the live list.
    await graphSection.getByRole('button', { name: 'Show history' }).click()
    await expect(graphSection.getByText('10.0.0.99')).toBeVisible({ timeout: 8_000 })
    const historyRow = graphSection.getByRole('row').filter({ hasText: '10.0.0.99' })
    await historyRow.getByRole('button', { name: 'Undo' }).click()
    await expect(graphSection.getByText('No deleted entities.')).toBeVisible({
      timeout: 5_000,
    })
  })
})

// ---------------------------------------------------------------------------
// Journey B — Slice 08 canvas: render, select, pin (persist), edit, delete
// ---------------------------------------------------------------------------

test.describe('Graph canvas visualization journey', () => {
  test('admin renders, pins (persists), edits and deletes via the canvas', async ({
    page,
  }) => {
    test.skip(!STACK_AVAILABLE, 'Set E2E_STACK=1 to run against the compose stack')

    await loginAs(page, ADMIN_USERNAME, ADMIN_PASSWORD)
    const graphSection = await createEngagementAndOpenGraph(
      page,
      `Graph E2E canvas ${Date.now()}`,
    )

    // Default view is the force-directed canvas; empty state first.
    await expect(graphSection.getByTestId('graph-canvas-empty')).toBeVisible({
      timeout: 5_000,
    })

    // Add a single host node — it becomes the centred node on the canvas.
    await addNode(page, graphSection, 'host', '10.0.0.5')

    // The live canvas region renders (empty state gone).
    const canvas = graphSection.getByTestId('graph-canvas')
    await expect(canvas).toBeVisible({ timeout: 8_000 })
    await expect(graphSection.getByTestId('graph-canvas-empty')).not.toBeVisible()
    // Give Cytoscape a beat to lay out + fit the single node to the viewport.
    await page.waitForTimeout(800)

    // Select the centred node by tapping the canvas centre.
    await canvas.click()
    const panel = graphSection.getByTestId('selected-node-panel')
    await expect(panel).toBeVisible({ timeout: 5_000 })

    // Pin it.
    await panel.getByRole('button', { name: 'Pin' }).click()
    await expect(panel.getByTestId('pinned-badge')).toBeVisible()
    await expect(panel.getByRole('button', { name: 'Unpin' })).toBeVisible()

    // Reload — the pin (localStorage) must persist for this engagement.
    await page.reload()
    await page.waitForURL('**/workspace', { timeout: 10_000 })
    const graphSection2 = page.getByRole('region', { name: 'Graph' })
    const canvas2 = graphSection2.getByTestId('graph-canvas')
    await expect(canvas2).toBeVisible({ timeout: 8_000 })
    await page.waitForTimeout(800)
    await canvas2.click()
    const panel2 = graphSection2.getByTestId('selected-node-panel')
    await expect(panel2).toBeVisible({ timeout: 5_000 })
    await expect(panel2.getByRole('button', { name: 'Unpin' })).toBeVisible()
    await expect(panel2.getByTestId('pinned-badge')).toBeVisible()

    // Edit the node's label from the panel (reuses NodeEditDialog).
    await panel2.getByRole('button', { name: 'Edit' }).click()
    const editDialog = page.getByRole('dialog')
    await expect(editDialog.getByRole('heading', { name: 'Edit Node' })).toBeVisible()
    const labelInput = editDialog.getByLabel('Label')
    await labelInput.clear()
    await labelInput.fill('10.0.0.42')
    await editDialog.getByRole('button', { name: 'Save' }).click()
    await expect(editDialog).not.toBeVisible({ timeout: 8_000 })
    await page.waitForTimeout(800)

    // Delete the node from the panel — panel disappears, empty state returns.
    await canvas2.click()
    const panel3 = graphSection2.getByTestId('selected-node-panel')
    await expect(panel3).toBeVisible({ timeout: 5_000 })
    await panel3.getByRole('button', { name: 'Delete' }).click()
    await expect(graphSection2.getByTestId('selected-node-panel')).not.toBeVisible({
      timeout: 8_000,
    })
    await expect(graphSection2.getByTestId('graph-canvas-empty')).toBeVisible({
      timeout: 8_000,
    })

    // Multi-node render: two nodes both land on the canvas.
    await addNode(page, graphSection2, 'host', 'host-a')
    await addNode(page, graphSection2, 'service', 'svc-b')
    await expect(graphSection2.getByTestId('graph-canvas')).toBeVisible({ timeout: 8_000 })
    await expect(graphSection2.getByTestId('graph-canvas-empty')).not.toBeVisible()
  })
})

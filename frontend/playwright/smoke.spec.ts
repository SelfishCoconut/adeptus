import { test, expect } from '@playwright/test'

// Bootstrap placeholder so `playwright test` resolves before real E2E specs
// (auth journey) exist. Uses no `page` fixture, so it needs no browser binary.
test('playwright harness is wired', () => {
  expect(1 + 1).toBe(2)
})

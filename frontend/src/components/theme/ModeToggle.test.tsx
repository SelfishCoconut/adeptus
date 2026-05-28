import { beforeEach, describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { ThemeProvider } from './ThemeProvider'
import { ModeToggle } from './ModeToggle'

describe('ModeToggle', () => {
  beforeEach(() => {
    localStorage.clear()
    document.documentElement.classList.remove('light', 'dark')
  })

  it('toggles the theme class on <html> and persists the choice', async () => {
    const user = userEvent.setup()
    render(
      <ThemeProvider defaultTheme="light">
        <ModeToggle />
      </ThemeProvider>,
    )

    expect(document.documentElement.classList.contains('light')).toBe(true)

    await user.click(screen.getByRole('button', { name: /toggle theme/i }))

    expect(document.documentElement.classList.contains('dark')).toBe(true)
    expect(document.documentElement.classList.contains('light')).toBe(false)
    expect(localStorage.getItem('adeptus-ui-theme')).toBe('dark')
  })
})

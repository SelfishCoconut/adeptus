import { describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { McpServersPage } from './McpServersPage'

// Mock the child component — behavior is tested in McpServerTable.test.tsx.
vi.mock('@/features/mcp/components/McpServerTable', () => ({
  McpServerTable: () => <div data-testid="mcp-server-table" />,
}))

function renderPage() {
  return render(
    <MemoryRouter>
      <McpServersPage />
    </MemoryRouter>,
  )
}

describe('McpServersPage', () => {
  it('renders the MCP Servers heading', () => {
    renderPage()
    expect(screen.getByRole('heading', { name: 'MCP Servers' })).toBeInTheDocument()
  })

  it('renders the McpServerTable', () => {
    renderPage()
    expect(screen.getByTestId('mcp-server-table')).toBeInTheDocument()
  })
})

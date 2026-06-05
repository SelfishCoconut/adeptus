import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { ReactNode } from 'react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { ManagePersonasPanel } from './ManagePersonasPanel'
import { api } from '@/shared/api'

vi.mock('@/shared/api', () => ({
  api: { GET: vi.fn(), POST: vi.fn(), PATCH: vi.fn(), DELETE: vi.fn() },
}))

const mockGet = vi.mocked(api.GET)
const mockPost = vi.mocked(api.POST)
const mockPatch = vi.mocked(api.PATCH)
const mockDelete = vi.mocked(api.DELETE)

const persona = (id: string, name: string, isBuiltin: boolean, slug: string | null) => ({
  id,
  name,
  system_prompt: `${name} prompt`,
  is_builtin: isBuiltin,
  slug,
  created_at: '2026-01-01T00:00:00Z',
})

function renderPanel() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  function Wrapper({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={client}>{children}</QueryClientProvider>
  }
  return render(<ManagePersonasPanel open onOpenChange={vi.fn()} />, { wrapper: Wrapper })
}

beforeEach(() => {
  mockGet.mockReset()
  mockPost.mockReset()
  mockPatch.mockReset()
  mockDelete.mockReset()
  mockGet.mockResolvedValue({
    data: {
      items: [persona('general-id', 'General', true, 'general'), persona('c1', 'Mine', false, null)],
    },
    response: { status: 200 },
  } as never)
})

describe('ManagePersonasPanel', () => {
  it('shows built-ins as read-only (no edit/delete) and custom personas with actions', async () => {
    renderPanel()
    const general = await screen.findByText('General')
    const builtinRow = general.closest('li') as HTMLElement
    expect(within(builtinRow).getByText('Built-in')).toBeInTheDocument()
    expect(within(builtinRow).queryByRole('button', { name: /edit/i })).toBeNull()
    expect(within(builtinRow).queryByRole('button', { name: /delete/i })).toBeNull()

    const customRow = screen.getByText('Mine').closest('li') as HTMLElement
    expect(within(customRow).getByRole('button', { name: /edit/i })).toBeInTheDocument()
    expect(within(customRow).getByRole('button', { name: /delete/i })).toBeInTheDocument()
  })

  it('disables the create submit until name and prompt are both filled', async () => {
    renderPanel()
    await screen.findByText('General')
    await userEvent.click(screen.getByRole('button', { name: /new persona/i }))

    const submit = screen.getByRole('button', { name: /create persona/i })
    expect(submit).toBeDisabled()
    await userEvent.type(screen.getByLabelText('Name'), 'Cloud')
    expect(submit).toBeDisabled()
    await userEvent.type(screen.getByLabelText('System prompt'), 'be cloudy')
    expect(submit).toBeEnabled()
  })

  it('edits a custom persona via PATCH', async () => {
    mockPatch.mockResolvedValue({
      data: persona('c1', 'Mine', false, null),
      response: { status: 200 },
    } as never)
    renderPanel()
    await screen.findByText('Mine')
    await userEvent.click(screen.getByRole('button', { name: /edit/i }))

    const prompt = screen.getByLabelText('System prompt')
    await userEvent.clear(prompt)
    await userEvent.type(prompt, 'updated prompt')
    await userEvent.click(screen.getByRole('button', { name: /save changes/i }))

    await waitFor(() =>
      expect(mockPatch).toHaveBeenCalledWith('/api/v1/personas/{persona_id}', {
        params: { path: { persona_id: 'c1' } },
        body: { name: 'Mine', system_prompt: 'updated prompt' },
      }),
    )
  })

  it('confirms then deletes a custom persona', async () => {
    mockDelete.mockResolvedValue({ response: { status: 204 } } as never)
    renderPanel()
    await screen.findByText('Mine')
    await userEvent.click(screen.getByRole('button', { name: /delete/i }))
    // Two-step: a confirm button appears.
    await userEvent.click(screen.getByRole('button', { name: /confirm/i }))

    await waitFor(() =>
      expect(mockDelete).toHaveBeenCalledWith('/api/v1/personas/{persona_id}', {
        params: { path: { persona_id: 'c1' } },
      }),
    )
  })

  it('shows an inline error when create hits a name conflict (409)', async () => {
    mockPost.mockResolvedValue({ error: { detail: 'conflict' }, response: { status: 409 } } as never)
    renderPanel()
    await screen.findByText('General')
    await userEvent.click(screen.getByRole('button', { name: /new persona/i }))
    await userEvent.type(screen.getByLabelText('Name'), 'Mine')
    await userEvent.type(screen.getByLabelText('System prompt'), 'dup')
    await userEvent.click(screen.getByRole('button', { name: /create persona/i }))

    expect(await screen.findByRole('alert')).toHaveTextContent(/already have a persona/i)
  })
})

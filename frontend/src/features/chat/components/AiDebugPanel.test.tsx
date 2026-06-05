import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { ReactNode } from 'react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor, within } from '@testing-library/react'
import { AiDebugPanel } from './AiDebugPanel'
import { api } from '@/shared/api'

vi.mock('@/shared/api', () => ({
  api: { GET: vi.fn(), POST: vi.fn() },
}))

const mockGet = vi.mocked(api.GET)

const ENGAGEMENT_ID = '00000000-0000-0000-0000-000000000001'
const MESSAGE_ID = 'assistant-1'

type FetchResult = { data?: unknown; error?: unknown; response: { status: number } }
const resolveGet = (value: FetchResult) => mockGet.mockResolvedValue(value as never)

function renderPanel() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  const Wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  )
  render(<AiDebugPanel engagementId={ENGAGEMENT_ID} messageId={MESSAGE_ID} />, {
    wrapper: Wrapper,
  })
}

const debugRecord = (overrides: Record<string, unknown> = {}) => ({
  message_id: MESSAGE_ID,
  model: 'qwen3.5:9b',
  status: 'complete',
  nodes: [
    { id: 'n1', type: 'host', label: '10.0.0.5', reasons: ['pinned'] },
    { id: 'n2', type: 'endpoint', label: '/login', reasons: ['keyword'] },
  ],
  edges: [{ id: 'e1', source_id: 'n1', target_id: 'n2', relation: 'hosts' }],
  context_block: '## Relevant graph subset',
  raw_prompt: '[system]\nyou are...\n\n[user]\nwhat about /login?',
  model_output: 'Try default creds.',
  ...overrides,
})

beforeEach(() => {
  mockGet.mockReset()
})

describe('AiDebugPanel', () => {
  it('renders nodes grouped by inclusion reason', async () => {
    resolveGet({ data: debugRecord(), response: { status: 200 } })
    renderPanel()

    await waitFor(() => expect(screen.getByText('Pinned')).toBeInTheDocument())
    expect(screen.getByText('Keyword match')).toBeInTheDocument()
    expect(screen.getByText('10.0.0.5')).toBeInTheDocument()
    expect(screen.getByText('/login')).toBeInTheDocument()
  })

  it('shows the node/edge counts', async () => {
    resolveGet({ data: debugRecord(), response: { status: 200 } })
    renderPanel()

    await waitFor(() => expect(screen.getByText(/2 nodes · 1 edge injected/i)).toBeInTheDocument())
  })

  it('renders the empty-subset state when nothing matched', async () => {
    resolveGet({
      data: debugRecord({ nodes: [], edges: [] }),
      response: { status: 200 },
    })
    renderPanel()

    await waitFor(() =>
      expect(screen.getByText(/no graph entities matched this turn/i)).toBeInTheDocument(),
    )
    expect(screen.getByText(/0 nodes · 0 edges injected/i)).toBeInTheDocument()
  })

  it('collapses the raw prompt behind a details toggle', async () => {
    resolveGet({ data: debugRecord(), response: { status: 200 } })
    renderPanel()

    const summary = await screen.findByText('Raw prompt')
    const details = summary.closest('details')
    expect(details).not.toBeNull()
    // Closed by default — the large prompt blob is not expanded until the user opens it.
    expect(details).not.toHaveAttribute('open')
    expect(within(details as HTMLElement).getByText(/what about \/login\?/)).toBeInTheDocument()
  })

  it('surfaces a load error', async () => {
    resolveGet({ error: { detail: 'not found' }, response: { status: 404 } })
    renderPanel()

    await waitFor(() => expect(screen.getByRole('alert')).toBeInTheDocument())
  })

  it('renders the parsed plan and claims sections (§14)', async () => {
    resolveGet({
      data: debugRecord({
        plan: [
          { step: 'Enumerate the login endpoint', status: 'done' },
          { step: 'Test for SQL injection', status: 'in_progress' },
        ],
        claims: [{ text: 'service is likely Apache', certainty: 60, node_id: null }],
      }),
      response: { status: 200 },
    })
    renderPanel()

    await waitFor(() => expect(screen.getByText('Parsed plan')).toBeInTheDocument())
    expect(screen.getByText('Enumerate the login endpoint')).toBeInTheDocument()
    expect(screen.getByText('Parsed claims')).toBeInTheDocument()
    expect(screen.getByText('(60% certain)')).toBeInTheDocument()
  })

  it('shows empty states for a turn with no plan or claims', async () => {
    resolveGet({ data: debugRecord(), response: { status: 200 } })
    renderPanel()

    await waitFor(() => expect(screen.getByText('Parsed plan')).toBeInTheDocument())
    expect(screen.getByTestId('plan-panel-empty')).toBeInTheDocument()
    expect(screen.getByText(/no certainty claims parsed/i)).toBeInTheDocument()
  })

  it('shows the unstripped model output including the metadata block', async () => {
    resolveGet({
      data: debugRecord({
        model_output: 'Try default creds.\n<adeptus-meta>\n{"plan": []}\n</adeptus-meta>',
      }),
      response: { status: 200 },
    })
    renderPanel()

    const summary = await screen.findByText('Model output')
    const details = summary.closest('details')
    expect(within(details as HTMLElement).getByText(/adeptus-meta/)).toBeInTheDocument()
  })
})

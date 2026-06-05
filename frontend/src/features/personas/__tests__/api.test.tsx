import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { ReactNode } from 'react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { renderHook, waitFor } from '@testing-library/react'
import {
  PersonaNameConflictError,
  personaKeys,
  useCreatePersona,
  useDeletePersona,
  usePersonas,
  useUpdatePersona,
} from '../api'
import { api } from '@/shared/api'

vi.mock('@/shared/api', () => ({
  api: { GET: vi.fn(), POST: vi.fn(), PATCH: vi.fn(), DELETE: vi.fn() },
}))

const mockGet = vi.mocked(api.GET)
const mockPost = vi.mocked(api.POST)
const mockPatch = vi.mocked(api.PATCH)
const mockDelete = vi.mocked(api.DELETE)

type FetchResult = { data?: unknown; error?: unknown; response?: { status: number } }
const resolveGet = (v: FetchResult) => mockGet.mockResolvedValue(v as never)

const persona = (id: string, name: string, isBuiltin: boolean, slug: string | null) => ({
  id,
  name,
  system_prompt: `${name} prompt`,
  is_builtin: isBuiltin,
  slug,
  created_at: '2026-01-01T00:00:00Z',
})

function createWrapper() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  return function Wrapper({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={client}>{children}</QueryClientProvider>
  }
}

beforeEach(() => {
  mockGet.mockReset()
  mockPost.mockReset()
  mockPatch.mockReset()
  mockDelete.mockReset()
})

describe('personaKeys', () => {
  it('namespaces the list key', () => {
    expect(personaKeys.list()).toEqual(['personas', 'list'])
  })
})

describe('usePersonas', () => {
  it('returns built-ins plus the callers own personas', async () => {
    resolveGet({
      data: {
        items: [
          persona('b1', 'General', true, 'general'),
          persona('b2', 'Recon', true, 'recon'),
          persona('c1', 'Mine', false, null),
        ],
      },
      response: { status: 200 },
    })

    const { result } = renderHook(() => usePersonas(), { wrapper: createWrapper() })
    await waitFor(() => expect(result.current.isSuccess).toBe(true))

    const names = result.current.data?.items.map((p) => p.name)
    expect(names).toEqual(['General', 'Recon', 'Mine'])
    expect(mockGet).toHaveBeenCalledWith('/api/v1/personas')
  })

  it('surfaces a load failure as an error', async () => {
    resolveGet({ error: { detail: 'boom' }, response: { status: 500 } })
    const { result } = renderHook(() => usePersonas(), { wrapper: createWrapper() })
    await waitFor(() => expect(result.current.isError).toBe(true))
  })
})

describe('useCreatePersona', () => {
  it('posts the create body', async () => {
    mockPost.mockResolvedValue({
      data: persona('c1', 'Cloud', false, null),
      response: { status: 201 },
    } as never)

    const { result } = renderHook(() => useCreatePersona(), { wrapper: createWrapper() })
    const created = await result.current.mutateAsync({ name: 'Cloud', systemPrompt: 'p' })

    expect(created.name).toBe('Cloud')
    expect(mockPost).toHaveBeenCalledWith('/api/v1/personas', {
      body: { name: 'Cloud', system_prompt: 'p' },
    })
  })

  it('surfaces a 409 as PersonaNameConflictError', async () => {
    mockPost.mockResolvedValue({ error: { detail: 'conflict' }, response: { status: 409 } } as never)
    const { result } = renderHook(() => useCreatePersona(), { wrapper: createWrapper() })
    await expect(
      result.current.mutateAsync({ name: 'Dup', systemPrompt: 'p' }),
    ).rejects.toBeInstanceOf(PersonaNameConflictError)
  })
})

describe('useUpdatePersona', () => {
  it('patches only the provided fields', async () => {
    mockPatch.mockResolvedValue({
      data: persona('c1', 'A', false, null),
      response: { status: 200 },
    } as never)

    const { result } = renderHook(() => useUpdatePersona(), { wrapper: createWrapper() })
    await result.current.mutateAsync({ id: 'c1', systemPrompt: 'a2' })

    expect(mockPatch).toHaveBeenCalledWith('/api/v1/personas/{persona_id}', {
      params: { path: { persona_id: 'c1' } },
      body: { system_prompt: 'a2' },
    })
  })

  it('surfaces a 409 as PersonaNameConflictError', async () => {
    mockPatch.mockResolvedValue({
      error: { detail: 'conflict' },
      response: { status: 409 },
    } as never)
    const { result } = renderHook(() => useUpdatePersona(), { wrapper: createWrapper() })
    await expect(
      result.current.mutateAsync({ id: 'c1', name: 'Taken' }),
    ).rejects.toBeInstanceOf(PersonaNameConflictError)
  })
})

describe('useDeletePersona', () => {
  it('deletes by id and invalidates the list', async () => {
    mockDelete.mockResolvedValue({ response: { status: 204 } } as never)
    const { result } = renderHook(() => useDeletePersona(), { wrapper: createWrapper() })
    await result.current.mutateAsync('c1')
    expect(mockDelete).toHaveBeenCalledWith('/api/v1/personas/{persona_id}', {
      params: { path: { persona_id: 'c1' } },
    })
  })

  it('surfaces a delete failure as an error', async () => {
    mockDelete.mockResolvedValue({ error: { detail: 'nope' }, response: { status: 404 } } as never)
    const { result } = renderHook(() => useDeletePersona(), { wrapper: createWrapper() })
    await expect(result.current.mutateAsync('c1')).rejects.toBeInstanceOf(Error)
  })
})

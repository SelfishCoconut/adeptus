import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, type Persona, type PersonaList } from '@/shared/api'

// --- Query keys ---

export const personaKeys = {
  all: ['personas'] as const,
  list: () => ['personas', 'list'] as const,
}

/**
 * Thrown when create/update is refused with a 409 because the caller already has a custom
 * persona with this name (§5.3 per-user name uniqueness). Lets the form surface an inline
 * field error instead of a generic failure.
 */
export class PersonaNameConflictError extends Error {
  constructor() {
    super('You already have a persona with this name')
    this.name = 'PersonaNameConflictError'
  }
}

// --- Queries ---

/**
 * List the personas available to the caller: the four global built-ins plus the caller's
 * own custom personas (§5.3 / §5.4). Built-ins first, then customs newest-first (server
 * order). Drives the composer switcher and the manage panel.
 */
export function usePersonas(options?: { enabled?: boolean }) {
  return useQuery<PersonaList>({
    queryKey: personaKeys.list(),
    enabled: options?.enabled ?? true,
    queryFn: async () => {
      const { data, error } = await api.GET('/api/v1/personas')
      if (error || !data) throw new Error('Failed to load personas')
      return data
    },
  })
}

// --- Mutations ---

export interface CreatePersonaInput {
  name: string
  systemPrompt: string
}

export interface UpdatePersonaInput {
  id: string
  /** Only provided fields are sent; omit a field to leave it unchanged. */
  name?: string
  systemPrompt?: string
}

/** Create a custom persona owned by the caller; surfaces a 409 as PersonaNameConflictError. */
export function useCreatePersona() {
  const queryClient = useQueryClient()
  return useMutation<Persona, Error, CreatePersonaInput>({
    mutationFn: async ({ name, systemPrompt }) => {
      const { data, error, response } = await api.POST('/api/v1/personas', {
        body: { name, system_prompt: systemPrompt },
      })
      if (error || !data) {
        if (response?.status === 409) throw new PersonaNameConflictError()
        throw new Error('Failed to create persona')
      }
      return data
    },
    onSettled: () => {
      void queryClient.invalidateQueries({ queryKey: personaKeys.list() })
    },
  })
}

/** Edit one of the caller's own custom personas; surfaces a 409 as PersonaNameConflictError. */
export function useUpdatePersona() {
  const queryClient = useQueryClient()
  return useMutation<Persona, Error, UpdatePersonaInput>({
    mutationFn: async ({ id, name, systemPrompt }) => {
      const { data, error, response } = await api.PATCH('/api/v1/personas/{persona_id}', {
        params: { path: { persona_id: id } },
        body: {
          ...(name !== undefined ? { name } : {}),
          ...(systemPrompt !== undefined ? { system_prompt: systemPrompt } : {}),
        },
      })
      if (error || !data) {
        if (response?.status === 409) throw new PersonaNameConflictError()
        throw new Error('Failed to update persona')
      }
      return data
    },
    onSettled: () => {
      void queryClient.invalidateQueries({ queryKey: personaKeys.list() })
    },
  })
}

/** Delete one of the caller's own custom personas. */
export function useDeletePersona() {
  const queryClient = useQueryClient()
  return useMutation<void, Error, string>({
    mutationFn: async (id) => {
      const { error } = await api.DELETE('/api/v1/personas/{persona_id}', {
        params: { path: { persona_id: id } },
      })
      if (error) throw new Error('Failed to delete persona')
    },
    onSettled: () => {
      void queryClient.invalidateQueries({ queryKey: personaKeys.list() })
    },
  })
}

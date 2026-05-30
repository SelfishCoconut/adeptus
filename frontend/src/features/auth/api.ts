import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, type LoginRequest, type UserMe } from '@/shared/api'

export const meQueryKey = ['auth', 'me'] as const

// The `me` query is the session anchor for the whole app. A 401 resolves to
// null ("not authenticated") rather than throwing, so ProtectedRoute can
// branch on the data without treating expiry as an error.
export function useMe() {
  return useQuery<UserMe | null>({
    queryKey: meQueryKey,
    queryFn: async () => {
      const { data, response } = await api.GET('/api/v1/auth/me')
      if (response.status === 401) return null
      if (!data) throw new Error('Failed to load the current user')
      return data
    },
    retry: false,
    staleTime: 30_000,
  })
}

export function useLogin() {
  const queryClient = useQueryClient()
  return useMutation<UserMe, Error, LoginRequest>({
    mutationFn: async (credentials) => {
      const { data, error, response } = await api.POST('/api/v1/auth/login', {
        body: credentials,
      })
      if (error || !data) {
        throw new Error(
          response.status === 401 ? 'Invalid username or password' : 'Login failed, try again',
        )
      }
      return data
    },
    onSuccess: (user) => {
      queryClient.setQueryData(meQueryKey, user)
    },
  })
}

export function useLogout() {
  const queryClient = useQueryClient()
  return useMutation<void, Error, void>({
    mutationFn: async () => {
      const { error, response } = await api.POST('/api/v1/auth/logout')
      // 401 = already logged out; treat as success so logout is idempotent.
      if (error && response.status !== 401) {
        throw new Error('Logout failed, try again')
      }
    },
    onSuccess: () => {
      // Drop ALL cached server data on logout, not just `me`, so nothing
      // (including engagement-scoped queries added by later slices) survives
      // in memory for the next user on a shared machine.
      queryClient.clear()
    },
  })
}

export function useAcceptTerms() {
  const queryClient = useQueryClient()
  return useMutation<UserMe, Error, void>({
    mutationFn: async () => {
      const { data, error } = await api.POST('/api/v1/auth/accept-terms')
      if (error || !data) {
        throw new Error('Failed to record terms acceptance')
      }
      return data
    },
    onSuccess: (user) => {
      queryClient.setQueryData(meQueryKey, user)
    },
  })
}

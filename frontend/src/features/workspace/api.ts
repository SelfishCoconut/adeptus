import { useQuery } from '@tanstack/react-query'
import { api } from '@/shared/api'

export const healthQueryKey = ['health'] as const

// Polls the backend liveness probe; the top-bar dot reflects this.
export function useHealth() {
  return useQuery({
    queryKey: healthQueryKey,
    queryFn: async () => {
      const { data, error } = await api.GET('/api/v1/health')
      if (error || !data) {
        throw new Error('Backend unreachable')
      }
      return data
    },
    refetchInterval: 30_000,
    retry: false,
  })
}

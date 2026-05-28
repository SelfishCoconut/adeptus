import { create } from 'zustand'
import type { UserMe } from '@/shared/api'

interface AuthState {
  user: UserMe | null
  setUser: (user: UserMe | null) => void
}

export const useAuthStore = create<AuthState>((set) => ({
  user: null,
  setUser: (user) => set({ user }),
}))

import createClient from 'openapi-fetch'
import type { paths } from './schema'

// baseUrl is the backend ORIGIN only — the /api/v1 prefix is already part of
// the generated path keys. Leave VITE_API_BASE_URL empty for same-origin
// (dev Vite proxy / prod reverse proxy). credentials:'include' so the opaque
// session cookie is sent on every request.
export const api = createClient<paths>({
  baseUrl: import.meta.env.VITE_API_BASE_URL ?? '',
  credentials: 'include',
})

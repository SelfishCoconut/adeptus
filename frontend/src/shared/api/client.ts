import createClient from 'openapi-fetch'
import type { paths } from './schema'

// baseUrl is the backend ORIGIN only — the /api/v1 prefix is already part of
// the generated path keys. Leave VITE_API_BASE_URL empty for same-origin
// (dev Vite proxy / prod reverse proxy). credentials:'include' so the opaque
// session cookie is sent on every request.
//
// SECURITY: VITE_API_BASE_URL MUST resolve to the same origin that serves this
// frontend. A cross-origin value sends the session cookie cross-site on every
// request and only works if the backend deliberately enables CORS
// allow-credentials for that origin — do not set one without that decision.
export const api = createClient<paths>({
  baseUrl: import.meta.env.VITE_API_BASE_URL ?? '',
  credentials: 'include',
})

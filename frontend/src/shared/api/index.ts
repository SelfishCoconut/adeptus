import type { components } from './schema'

export { api } from './client'
export type { paths, components } from './schema'

export type UserMe = components['schemas']['UserMe']
export type LoginRequest = components['schemas']['LoginRequest']

import type { components } from './schema'

export { api } from './client'
export type { paths, components } from './schema'

export type UserMe = components['schemas']['UserMe']
export type LoginRequest = components['schemas']['LoginRequest']

// PrivacyMode is derived from EngagementDetail rather than a top-level schema
// because openapi-typescript inlines enum types on the parent object. The
// EngagementCreate.required array in openapi.json does NOT include privacy_mode
// (it's ["name", "scope"] only); the generated schema.ts marks it as optional
// on EngagementCreate. This is expected generator behaviour — consumers always
// send the field but the server default (local_only) is the safe fallback.
export type PrivacyMode = components['schemas']['EngagementDetail']['privacy_mode']
export type EngagementSummary = components['schemas']['EngagementSummary']
export type EngagementDetail = components['schemas']['EngagementDetail']
export type EngagementCreate = components['schemas']['EngagementCreate']
export type EngagementUpdate = components['schemas']['EngagementUpdate']
export type MemberEntry = components['schemas']['MemberEntry']
export type AddMemberRequest = components['schemas']['AddMemberRequest']

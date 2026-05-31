import type { components } from './schema'

export { api } from './client'
export type { paths, components } from './schema'

export type UserMe = components['schemas']['UserMe']
export type LoginRequest = components['schemas']['LoginRequest']

export type PrivacyMode = components['schemas']['EngagementDetail']['privacy_mode']
export type EngagementSummary = components['schemas']['EngagementSummary']
export type EngagementDetail = components['schemas']['EngagementDetail']
export type EngagementCreate = components['schemas']['EngagementCreate']
export type EngagementUpdate = components['schemas']['EngagementUpdate']
export type MemberEntry = components['schemas']['MemberEntry']
export type AddMemberRequest = components['schemas']['AddMemberRequest']

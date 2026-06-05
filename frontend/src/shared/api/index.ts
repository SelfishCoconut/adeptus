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
export type McpServerInfo = components['schemas']['McpServerInfo']
export type McpToolDeclaration = components['schemas']['McpToolDeclaration']
export type ToolRunCreate = components['schemas']['ToolRunCreate']
export type ToolRunResult = components['schemas']['ToolRunResult']
export type ToolDescriptor = components['schemas']['ToolDescriptor']
export type ToolPreset = components['schemas']['ToolPreset']
export type ToolRunPage = components['schemas']['ToolRunPage']
export type ToolQueueSnapshot = components['schemas']['ToolQueueSnapshot']
export type QueuedRun = components['schemas']['QueuedRun']
export type TimeoutDecision = components['schemas']['TimeoutDecision']
export type EngagementPauseRequest = components['schemas']['EngagementPauseRequest']
export type EngagementPauseState = components['schemas']['EngagementPauseState']
export type AuditAction = components['schemas']['AuditAction']
export type AuditEntry = components['schemas']['AuditEntryRead']
export type AuditPage = components['schemas']['AuditPage']
export type ChatMessage = components['schemas']['ChatMessageRead']
export type ChatMessageCreate = components['schemas']['ChatMessageCreate']
export type ChatMessagePage = components['schemas']['ChatMessagePage']
export type ChatRole = components['schemas']['ChatRole']
export type ChatMessageStatus = components['schemas']['ChatMessageStatus']
export type SendChatMessageResult = components['schemas']['SendChatMessageResult']
export type ChatTurnDebug = components['schemas']['ChatTurnDebug']
export type GraphSubsetNode = components['schemas']['GraphSubsetNode']
export type GraphSubsetEdge = components['schemas']['GraphSubsetEdge']
export type GraphSubsetReason = components['schemas']['GraphSubsetReason']
export type PlanStep = components['schemas']['PlanStep']
export type PlanStepStatus = components['schemas']['PlanStepStatus']
export type Claim = components['schemas']['Claim']
export type Persona = components['schemas']['Persona']
export type PersonaList = components['schemas']['PersonaList']
export type PersonaCreate = components['schemas']['PersonaCreate']
export type PersonaUpdate = components['schemas']['PersonaUpdate']
export type ApprovalRequest = components['schemas']['ApprovalRequestRead']
export type ApprovalRequestPage = components['schemas']['ApprovalRequestPage']
export type ApprovalStatus = components['schemas']['ApprovalStatus']
export type ApprovalReason = components['schemas']['ApprovalReason']
export type ApprovalConflict = components['schemas']['ApprovalConflict']

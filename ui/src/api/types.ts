/** Short names for the contract schemas (generated in schema.gen.ts; never hand-written shapes). */
import type { components } from './schema.gen'

type S = components['schemas']

export type Me = S['Me']
export type User = S['User']
export type UserDetail = S['UserDetail']
export type EffectiveRole = S['EffectiveRole']
export type Team = S['Team']
export type Role = S['Role']
export type PermissionInfo = S['PermissionInfo']
export type RoleBinding = S['RoleBinding']
export type GroupMappingConfig = S['GroupMappingConfig']
export type GroupMappingDryRun = S['GroupMappingDryRun']
export type WhoCan = S['WhoCan']
export type AuthSession = S['AuthSession']
export type ApiToken = S['ApiToken']
export type AuthSettings = S['AuthSettings']
export type AuditSettings = S['AuditSettings']
export type AuditVerify = S['AuditVerify']

export type Agent = S['Agent']
export type AgentProfile = S['AgentProfile']
export type AgentVersion = S['AgentVersion']
export type Avatar = S['Avatar']
export type Skill = S['Skill']
export type SkillVersion = S['SkillVersion']
export type Playbook = S['Playbook']
export type Objective = S['Objective']
export type ObjectiveCheck = S['ObjectiveCheck']
export type Assignment = S['Assignment']

export type Mission = S['Mission']
export type MissionStatus = S['MissionStatus']
export type MissionCreate = S['MissionCreate']
export type DraftPlan = S['DraftPlan']
export type Job = S['Job']
export type TakeOverWorkspace = S['TakeOverWorkspace']
export type TakeOverFile = S['TakeOverFile']
export type TakeOverFileContent = S['TakeOverFileContent']
export type BrowserRecordingInput = S['BrowserRecordingInput']
export type JobStatus = S['JobStatus']
export type Artifact = S['Artifact']
export type Replay = S['Replay']
export type StepCore = S['StepCore']
export type DataClass = S['DataClass']
export type Autonomy = S['Autonomy']
/** mission-plan.schema without the `$defs` openapi-typescript copies from the bundled JSON Schema. */
export type MissionPlan = Omit<S['mission-plan.schema'], '$defs'>
export type PlanStep = StepCore & {
  status?: string
  jobs?: {
    total?: number
    queued?: number
    running?: number
    succeeded?: number
    failed?: number
    needs_attention?: number
  }
}

export type ReviewItem = S['ReviewItem']
export type ReviewList = S['ReviewList']
export type ProposedAction = S['ProposedAction']
export type Question = S['Question']
export type MissionSuggestion = S['MissionSuggestion']
export type Preview = S['Preview']
export type DiffPreview = S['DiffPreview']
export type IssuePreview = S['IssuePreview']
export type IssueUpdatePreview = S['IssueUpdatePreview']
export type CommentPreview = S['CommentPreview']
export type MailPreview = S['MailPreview']
export type EventPreview = S['EventPreview']
export type ChatMessagePreview = S['ChatMessagePreview']
export type FilePreview = S['FilePreview']
export type JsonPreview = S['JsonPreview']
export type RunsAs = S['RunsAs']

export type Task = S['Task']
export type Trigger = S['Trigger']
export type Session = S['Session']

export type Connector = S['Connector']
export type ConnectorHealthInfo = S['ConnectorHealthInfo']
export type CredentialInfo = S['CredentialInfo']
export type Operation = S['Operation']
export type ConnectorDraft = S['ConnectorDraft']
export type ConnectorTestResult = S['ConnectorTestResult']
export type PersonalConnection = S['PersonalConnection']
export type Channel = S['Channel']

export type InferenceHealth = S['InferenceHealth']
export type InferenceModel = S['InferenceModel']
export type InferenceNode = S['InferenceNode']
export type Usage = S['Usage']
export type UsageSeries = S['UsageSeries']
export type Budget = S['Budget']
export type Provider = S['Provider']
export type RoutingPolicy = S['RoutingPolicy']

export type Signal = S['Signal']
export type Proposal = S['Proposal']
export type EvalRun = S['EvalRun']
export type EvalScores = S['EvalScores']
export type Analytics = S['Analytics']

export type AuditEvent = S['AuditEvent']
export type Guardrails = S['Guardrails']
export type Guardrail = S['Guardrail']
export type StreamMessage = S['StreamMessage']

/**
 * openapi-typescript renders the bundled JSON Schema's `$defs` as a required property of
 * `mission-plan.schema`; it is not part of the document. Cast at the request boundary only.
 */
export function toContractPlan(plan: MissionPlan): S['mission-plan.schema'] {
  return plan as S['mission-plan.schema']
}

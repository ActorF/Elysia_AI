/**
 * Validate explicit sequential Work plans and their owner-bound control requests.
 *
 * Each step keeps its original call, result, and one-use write approval together.
 * These detached DTOs describe Backend state; they never grant filesystem access
 * or infer consent from plan text. Physical worker ownership is sampled separately
 * from runner state, so a cancelled plan can retain a draining native operation.
 */

import { codePointLength, trimProtocolBlankCharacters } from './protocol-text.js'
import { parseWorkWorkspaceGetParams } from './work-protocol.js'
import {
  parseWorkToolApprovalState, parseWorkToolCall, parseWorkToolConfirmParams,
  parseWorkToolResultForCall,
  type WorkToolApproval, type WorkToolCall, type WorkToolResult,
} from './work-tool-protocol.js'

export type WorkPlanStatus = 'pending' | 'running' | 'paused' | 'waiting' | 'succeeded' | 'failed' | 'cancelled'
export type WorkPlanStepStatus = Exclude<WorkPlanStatus, 'paused'>
export type WorkPlanAction = 'start' | 'pause' | 'resume' | 'cancel' | 'next'

/** One immutable call with its current outcome or exact pending write approval. */
export interface WorkPlanStep {
  stepId: string
  call: WorkToolCall
  status: WorkPlanStepStatus
  result: WorkToolResult | null
  approval: WorkToolApproval | null
}

/** A bounded owned plan, including physical capacity retained after cancellation. */
export interface WorkPlan {
  planId: string
  title: string
  state: WorkPlanStatus
  steps: WorkPlanStep[]
  busy: boolean
}

/** The current canonical Chat's plan, or absence of an owned plan. */
export interface WorkPlanState { kind: 'work.plan'; plan: WorkPlan | null }
export interface WorkPlanGetParams { chatId: string }
export interface WorkPlanCreateParams { chatId: string; title: string; calls: WorkToolCall[] }
export interface WorkPlanControlParams { chatId: string; planId: string; action: WorkPlanAction }
export interface WorkPlanConfirmParams { chatId: string; planId: string; approvalId: string; granted: boolean }

/** Inspect and control explicit Work plans without implicit write permission. */
export interface WorkPlanClient {
  /** Read only the plan belonging to the canonical active Work Chat. */
  getWorkPlan(chatId: string): Promise<WorkPlanState>
  /** Create one bounded list of exact calls without starting its worker. */
  createWorkPlan(request: WorkPlanCreateParams): Promise<WorkPlanState>
  /** Apply one explicit control to the current plan identified by its owner token. */
  controlWorkPlan(request: WorkPlanControlParams): Promise<WorkPlanState>
  /** Consume only the displayed current step's one-use approval decision. */
  confirmWorkPlan(request: WorkPlanConfirmParams): Promise<WorkPlanState>
}

const PLAN_STATES: readonly WorkPlanStatus[] = ['pending', 'running', 'paused', 'waiting', 'succeeded', 'failed', 'cancelled']
const LIVE_STATES: readonly WorkPlanStatus[] = ['pending', 'running', 'paused', 'waiting']
const ACTIONS: readonly WorkPlanAction[] = ['start', 'pause', 'resume', 'cancel', 'next']
const MAX_PLAN_BYTES = 768 * 1024
const MAX_STEPS = 16
const utf8 = new TextEncoder()

function record(value: unknown, keys: readonly string[]): Record<string, unknown> {
  if (typeof value !== 'object' || value === null || Array.isArray(value)
    || Object.keys(value).length !== keys.length || keys.some((key) => !Object.hasOwn(value, key))) {
    throw new TypeError('Work plan object has invalid fields.')
  }
  return value as Record<string, unknown>
}

function identifier(value: unknown, prefix: 'plan' | 'step'): string {
  if (typeof value !== 'string' || !new RegExp(`^${prefix}_[0-9a-f]{32}$`, 'u').test(value)) {
    throw new TypeError('Work plan identifier is invalid.')
  }
  return value
}

function title(value: unknown): string {
  if (typeof value !== 'string' || value.length > 400 || codePointLength(value) > 200
    || trimProtocolBlankCharacters(value).length === 0 || value.includes('\0')
    || /[\ud800-\udbff](?![\udc00-\udfff])|(?<![\ud800-\udbff])[\udc00-\udfff]/u.test(value)) {
    throw new TypeError('Work plan title is invalid.')
  }
  return value
}

function calls(value: unknown): WorkToolCall[] {
  if (!Array.isArray(value) || value.length < 1 || value.length > MAX_STEPS) {
    throw new TypeError('Work plan requires one to sixteen calls.')
  }
  return Array.from(value, parseWorkToolCall)
}

function separators(value: unknown): number {
  if (Array.isArray(value)) {
    return Math.max(0, value.length - 1) + value.reduce<number>((count, item) => count + separators(item), 0)
  }
  if (typeof value === 'object' && value !== null) {
    const values = Object.values(value)
    return values.length + Math.max(0, values.length - 1)
      + values.reduce<number>((count, item) => count + separators(item), 0)
  }
  return 0
}

function bounded(value: unknown): void {
  // Only call this after all leaves have passed their own shape, depth, and byte
  // bounds. Python uses a space after every comma and colon; compact JavaScript
  // serialization alone would accept snapshots beyond the runner's byte cap.
  if (utf8.encode(JSON.stringify(value)).byteLength + separators(value) > MAX_PLAN_BYTES) {
    throw new TypeError('Work plan exceeds its byte limit.')
  }
}

function step(value: unknown): WorkPlanStep {
  const raw = record(value, ['stepId', 'call', 'status', 'result', 'approval'])
  const call = parseWorkToolCall(raw.call)
  if (typeof raw.status !== 'string' || raw.status === 'paused'
    || !PLAN_STATES.includes(raw.status as WorkPlanStatus)) {
    throw new TypeError('Work plan step status is invalid.')
  }
  const status = raw.status as WorkPlanStepStatus
  const result = raw.result === null ? null
    : parseWorkToolResultForCall({ kind: 'work.result', result: raw.result }, call).result
  const approval = raw.approval === null ? null
    : parseWorkToolApprovalState({ kind: 'work.approval', approval: raw.approval }).approval
  if (approval !== null && JSON.stringify(approval.call) !== JSON.stringify(call)) {
    throw new TypeError('Work plan approval does not match its exact step call.')
  }
  if (status === 'waiting') {
    if (approval === null || result !== null) throw new TypeError('Waiting Work plan step is inconsistent.')
  } else if (approval !== null) {
    throw new TypeError('Work plan approval belongs only to a waiting step.')
  }
  if (status === 'pending' || status === 'running') {
    if (result !== null) throw new TypeError('Active Work plan step cannot have a terminal result.')
  } else if (status !== 'waiting') {
    const expected = status === 'succeeded' ? ['succeeded', 'partial']
      : status === 'failed' ? ['denied', 'failed'] : ['cancelled']
    if (result === null || !expected.includes(result.status)) {
      throw new TypeError('Work plan step does not match its terminal result.')
    }
  }
  return { stepId: identifier(raw.stepId, 'step'), call, status, result, approval }
}

/** Validate detached plan snapshots while preserving legal worker publication races. */
export function parseWorkPlanState(value: unknown): WorkPlanState {
  const state = record(value, ['kind', 'plan'])
  if (state.kind !== 'work.plan') throw new TypeError('Work plan result kind is invalid.')
  if (state.plan === null) return { kind: 'work.plan', plan: null }
  const raw = record(state.plan, ['planId', 'title', 'state', 'steps', 'busy'])
  if (typeof raw.state !== 'string' || !PLAN_STATES.includes(raw.state as WorkPlanStatus)
    || typeof raw.busy !== 'boolean' || !Array.isArray(raw.steps)
    || raw.steps.length < 1 || raw.steps.length > MAX_STEPS) {
    throw new TypeError('Work plan state is invalid.')
  }
  const steps = Array.from(raw.steps, step)
  if (new Set(steps.map((item) => item.stepId)).size !== steps.length
    || steps.filter((item) => item.status === 'running' || item.status === 'waiting').length > 1) {
    throw new TypeError('Work plan steps have invalid sequential ownership.')
  }
  const plan: WorkPlan = {
    planId: identifier(raw.planId, 'plan'), title: title(raw.title),
    state: raw.state as WorkPlanStatus, steps, busy: raw.busy,
  }
  // The manager appends busy after taking the runner snapshot. A waiting worker
  // may still drain, start may precede marking a step running, and cancellation
  // can retain an admitted write. These valid races forbid deriving state from
  // busy or requiring global plan state to equal the current step's status.
  bounded({ planId: plan.planId, title: plan.title, state: plan.state, steps: plan.steps })
  return { kind: 'work.plan', plan }
}

/** Validate one active Chat identifier for a read-only plan lookup. */
export function parseWorkPlanGetParams(value: unknown): WorkPlanGetParams {
  return parseWorkWorkspaceGetParams(value)
}

/** Validate bounded explicit calls without starting them or authorizing any writes. */
export function parseWorkPlanCreateParams(value: unknown): WorkPlanCreateParams {
  const raw = record(value, ['chatId', 'title', 'calls'])
  const request = {
    ...parseWorkWorkspaceGetParams({ chatId: raw.chatId }), title: title(raw.title), calls: calls(raw.calls),
  }
  bounded(request)
  return request
}

/** Validate a control token and closed action; the Backend checks its live owner. */
export function parseWorkPlanControlParams(value: unknown): WorkPlanControlParams {
  const raw = record(value, ['chatId', 'planId', 'action'])
  if (typeof raw.action !== 'string' || !ACTIONS.includes(raw.action as WorkPlanAction)) {
    throw new TypeError('Work plan control action is invalid.')
  }
  return {
    ...parseWorkWorkspaceGetParams({ chatId: raw.chatId }),
    planId: identifier(raw.planId, 'plan'), action: raw.action as WorkPlanAction,
  }
}

/** Validate one exact plan-bound approval choice with no persistent permission. */
export function parseWorkPlanConfirmParams(value: unknown): WorkPlanConfirmParams {
  const raw = record(value, ['chatId', 'planId', 'approvalId', 'granted'])
  return {
    ...parseWorkToolConfirmParams({ chatId: raw.chatId, approvalId: raw.approvalId, granted: raw.granted }),
    planId: identifier(raw.planId, 'plan'),
  }
}

/** Keep conflicting Chat and Workspace actions blocked until live or physical ownership ends. */
export function workPlanHasLease(plan: WorkPlan | null): boolean {
  return plan !== null && (plan.busy || LIVE_STATES.includes(plan.state))
}

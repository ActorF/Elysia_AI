/**
 * Validate the bounded Work DTOs shared by Electron and the local Backend.
 *
 * This module has no dependency on the general protocol parser, so Work
 * validation can be tested separately and reused at IPC boundaries. Callers
 * translate TypeError into their boundary's fixed protocol error category.
 */

import {
  codePointLength,
  hasNonBlankCodePoint,
} from './protocol-text.js'

/** Persisted access scope for one Work Chat; null revokes all file access. */
export interface WorkWorkspaceState {
  kind: 'work.workspace'
  chatId: string
  workspacePath: string | null
}

/** Identify the Work Chat whose selected scope is requested. */
export interface WorkWorkspaceGetParams {
  chatId: string
}

/** A root selected by native Main code, or null to revoke the existing scope. */
export interface WorkWorkspaceBindParams extends WorkWorkspaceGetParams {
  workspacePath: string | null
}

/** Renderer capabilities expose a chooser, never an arbitrary path setter. */
export interface WorkWorkspaceClient {
  /** Read the canonical scope currently saved for one Work Chat. */
  getWorkWorkspace(chatId: string): Promise<WorkWorkspaceState>
  /** Ask the native chooser to bind one directory, returning null on cancellation. */
  chooseWorkWorkspace(chatId: string): Promise<WorkWorkspaceState | null>
  /** Revoke the Chat's current scope and return canonical unbound state. */
  clearWorkWorkspace(chatId: string): Promise<WorkWorkspaceState>
}

function exactRecord(
  value: unknown,
  keys: readonly string[],
  context: string,
): Record<string, unknown> {
  if (
    typeof value !== 'object'
    || value === null
    || Array.isArray(value)
    || Object.keys(value).length !== keys.length
    || keys.some((key) => !Object.hasOwn(value, key))
  ) {
    throw new TypeError(`${context} has invalid fields.`)
  }
  return value as Record<string, unknown>
}

function chatIdentifier(value: unknown): string {
  if (
    typeof value !== 'string'
    || codePointLength(value) > 128
    || !/^chat_[A-Za-z0-9_-]{1,123}$/u.test(value)
  ) {
    throw new TypeError('Work Chat identifier is invalid.')
  }
  return value
}

function workspacePath(value: unknown): string | null {
  if (value === null) {
    return null
  }
  if (
    typeof value !== 'string'
    || !hasNonBlankCodePoint(value)
    || codePointLength(value) > 32_767
    || Array.from(value).some((character) => {
      const codePoint = character.codePointAt(0) ?? 0
      return codePoint < 32 || codePoint === 127
    })
  ) {
    throw new TypeError('Work Workspace path is invalid.')
  }
  // Python owns filesystem canonicalization and traversal checks. Electron
  // only accepts bounded display strings and paths from the native chooser.
  return value
}

/** Validate an exact scope-read request, rejecting unknown or missing fields. */
export function parseWorkWorkspaceGetParams(value: unknown): WorkWorkspaceGetParams {
  const params = exactRecord(value, ['chatId'], 'Work Workspace request')
  return { chatId: chatIdentifier(params.chatId) }
}

/** Validate a native-selected scope assignment or an explicit unbind request. */
export function parseWorkWorkspaceBindParams(value: unknown): WorkWorkspaceBindParams {
  const params = exactRecord(value, ['chatId', 'workspacePath'], 'Work Workspace binding')
  return {
    chatId: chatIdentifier(params.chatId),
    workspacePath: workspacePath(params.workspacePath),
  }
}

/** Validate canonical scope state before publishing it to a renderer. */
export function parseWorkWorkspaceState(value: unknown): WorkWorkspaceState {
  const state = exactRecord(value, ['kind', 'chatId', 'workspacePath'], 'Work Workspace state')
  if (state.kind !== 'work.workspace') {
    throw new TypeError('Work Workspace result kind is invalid.')
  }
  return {
    kind: 'work.workspace',
    chatId: chatIdentifier(state.chatId),
    workspacePath: workspacePath(state.workspacePath),
  }
}

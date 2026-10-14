/** Share pure desktop preview/state contracts without granting renderer access to Node or native adapters. */

/** Canonical authority supplied by Main, independently of renderer request fields. */
export interface WorkDesktopOwner {
  chatId: string
  projectId: string | null
  workspacePath: string | null
  mode: 'chat' | 'work'
  ready: boolean
  busy: boolean
}

/** Closed desktop operations implemented by this user-controlled foundation. */
export type WorkDesktopActionName = 'file.open' | 'app.open' | 'clipboard.read' | 'clipboard.write' | 'window.select'
/** Fixed failure categories cannot disclose native exceptions or private paths. */
export type WorkDesktopErrorCode = 'unavailable' | 'busy' | 'context_changed' | 'path_denied' | 'file_changed'
  | 'invalid_text' | 'expired' | 'approval_required' | 'native_failed' | 'audit_failed'

/** Display-only review data excludes native source IDs and absolute paths. */
export type WorkDesktopPreview =
  | { action: 'file.open'; name: string; relativePath: string; sizeBytes: number; sha256: string; impact: string }
  | { action: 'app.open'; application: 'notepad'; impact: string }
  | { action: 'clipboard.write'; text: string; sizeBytes: number; impact: string }

/** One exact change reviewed by the user; its token grants no permanent permission. */
export interface WorkDesktopApproval {
  approvalId: string
  expiresAt: string
  risk: 'low' | 'medium'
  preview: WorkDesktopPreview
}

/** One metadata-only native window choice; this token grants no capture capability. */
export interface WorkDesktopWindow { selectionId: string; title: string }

/** A truthful result preserves whether a native effect happened if audit later failed. */
export interface WorkDesktopAction {
  actionId: string
  action: WorkDesktopActionName
  status: 'succeeded' | 'denied' | 'failed' | 'partial'
  error: WorkDesktopErrorCode | null
  applied: boolean | null
}

/** Bounded redacted decision metadata persisted only by an injected Main audit sink. */
export interface WorkDesktopAuditEntry {
  version: 1
  actionId: string
  timestamp: string
  chatId: string
  projectId: string | null
  action: WorkDesktopActionName
  phase: 'prepared' | 'admitted' | 'denied' | 'succeeded' | 'failed'
  summary: { sizeBytes?: number; sha256?: string; relativePath?: string; application?: 'notepad' }
  error: WorkDesktopErrorCode | null
}

/** Detached public state is safe to render as plain text and contains no hidden native handles. */
export interface WorkDesktopState {
  version: 1
  owner: { chatId: string; projectId: string | null } | null
  revision: number
  busy: boolean
  pending: WorkDesktopApproval | null
  clipboardText: string | null
  windows: WorkDesktopWindow[]
  selectedWindow: WorkDesktopWindow | null
  lastAction: WorkDesktopAction | null
}

/** Renderer verbs correspond only to visible user gestures; native paths and handles are absent. */
export interface WorkDesktopClient {
  /** Read display state without a clipboard read, window scan, or native effect. */
  getWorkDesktopState(chatId: string): Promise<WorkDesktopState>
  /** Select one safe Workspace document and review its immutable-copy preview. */
  chooseDesktopDocument(chatId: string): Promise<WorkDesktopState>
  /** Review the fixed no-argument Windows Notepad action. */
  previewDesktopNotepad(chatId: string): Promise<WorkDesktopState>
  /** Read bounded clipboard text only for this explicit user action. */
  readDesktopClipboard(chatId: string): Promise<WorkDesktopState>
  /** Review the exact text that a later single decision may write. */
  previewDesktopClipboard(request: { chatId: string; text: string }): Promise<WorkDesktopState>
  /** Enumerate only window-name choices without thumbnails or capture. */
  listDesktopWindows(chatId: string): Promise<WorkDesktopState>
  /** Bind only one current opaque metadata choice, with no capture permission. */
  selectDesktopWindow(request: { chatId: string; selectionId: string }): Promise<WorkDesktopState>
  /** Approve once or deny one currently displayed exact desktop preview. */
  decideDesktopAction(request: { chatId: string; approvalId: string; granted: boolean }): Promise<WorkDesktopState>
  /** Read redacted desktop decisions without exposing document or clipboard contents. */
  getDesktopAudit(chatId: string): Promise<{ entries: WorkDesktopAuditEntry[]; truncated: boolean }>
}

/** A verified immutable review copy supplied to Main, never to the renderer. */
export interface WorkDesktopDocument { actionId: string; name: string; bytes: Uint8Array; sha256: string }

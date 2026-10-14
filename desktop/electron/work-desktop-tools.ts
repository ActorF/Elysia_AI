/**
 * Own narrow user-triggered desktop actions behind Main-only native callbacks.
 *
 * Picker paths, source IDs, verified document bytes, and executable paths stay
 * private. Public previews are owner-bound, short-lived, and single-use. No
 * method accepts model text as an action, shell command, URL, or capture request.
 */

import { createHash, randomUUID } from 'node:crypto'
import { constants } from 'node:fs'
import { lstat, open, realpath } from 'node:fs/promises'
import path from 'node:path'

const MAX_TEXT_BYTES = 32 * 1024
const MAX_DOCUMENT_BYTES = 16 * 1024 * 1024
const APPROVAL_TTL_MS = 5 * 60 * 1000
const DOCUMENT_EXTENSIONS = new Set(['.txt', '.md', '.csv', '.json', '.pdf', '.docx'])
const SENSITIVE_NAMES = new Set(['.git', '.ssh', '.aws', '.azure', '.gcp', '.gnupg', '.kube', '.venv', 'node_modules',
  'secrets', 'credentials', 'id_rsa', 'id_dsa', 'id_ecdsa', 'id_ed25519', 'known_hosts', 'authorized_keys', 'private_key', 'private-key'])
const PRIVATE_SUFFIXES = new Set(['.pem', '.key', '.p12', '.pfx', '.crt', '.cer', '.der', '.jks', '.keystore', '.kdbx'])

function sensitiveComponent(component: string): boolean {
  // Match the Python file-tool policy across the complete selected ancestry,
  // including case-fold spellings such as paßword and ſecrets. Normalization
  // tightens this deny list only; it never changes the path opened on disk.
  const lower = component.normalize('NFKC').toLowerCase().replaceAll('ß', 'ss')
  const extension = path.extname(lower)
  // Adding an allowed document suffix to a known key filename does not make
  // its copied credential content an eligible native review document.
  const keyAlias = DOCUMENT_EXTENSIONS.has(extension) && SENSITIVE_NAMES.has(lower.slice(0, -extension.length))
  return lower.startsWith('.env') || SENSITIVE_NAMES.has(lower) || keyAlias || PRIVATE_SUFFIXES.has(extension)
    || /(?:^|[._-])(?:secrets?|credentials?|passwords?|tokens?)(?:$|[._-])/u.test(lower)
    || /secret|credential|private[-_]?key/u.test(lower)
}

import type {
  WorkDesktopOwner, WorkDesktopActionName, WorkDesktopErrorCode, WorkDesktopPreview,
  WorkDesktopApproval, WorkDesktopWindow, WorkDesktopAction, WorkDesktopAuditEntry,
  WorkDesktopState, WorkDesktopDocument,
} from './work-desktop-protocol.js'
export type * from './work-desktop-protocol.js'

/** Native adapters are injected so tests never launch apps, alter a real clipboard, or capture screens. */
export interface WorkDesktopDependencies {
  /** Return the canonical active Chat, Project, selected root, and external busy facts. */
  getOwner(): WorkDesktopOwner
  /** Choose one local file through Main's native picker, or null on cancellation. */
  chooseFile(): Promise<string | null>
  /** Open a Main-managed verified review copy, without reopening the mutable source path. */
  openDocument(document: WorkDesktopDocument): Promise<void>
  /** Return a trusted local Windows system directory, or null when unavailable. */
  trustedSystemRoot(): string | null
  /** Launch exactly the supplied fixed Notepad executable with an empty argument list. */
  launchNotepad(executable: string, arguments_: readonly []): Promise<void>
  /** Read plain text only after the dedicated user gesture. */
  readClipboard(): string | Promise<string>
  /** Replace plain text only after its complete exact preview was approved once. */
  writeClipboard(text: string): void | Promise<void>
  /** Enumerate window names/IDs without thumbnails, icons, audio, or capture streams. */
  listWindows(): Promise<Array<{ sourceId: string; title: string }>>
  /** Durably append one redacted record; admission fails closed if this rejects. */
  audit(entry: WorkDesktopAuditEntry): void | Promise<void>
  platform: NodeJS.Platform
  /** Inject wall time for display/audit without using it to extend approval lifetime. */
  wallNow?(): Date
  /** Inject monotonic time; rollback invalidates tokens rather than extending them. */
  monotonicNow?(): number
}

interface Authority { owner: WorkDesktopOwner; key: string; epoch: number }
interface FileReview { sourcePath: string; identity: string; rootIdentity: string; document: WorkDesktopDocument }
interface Pending {
  approval: WorkDesktopApproval
  authority: Authority
  actionId: string
  started: number
  file?: FileReview
  executable?: string
}
interface WindowChoice { public: WorkDesktopWindow; sourceId: string; authority: Authority; started: number }

class BoundaryError extends Error {
  constructor(readonly code: WorkDesktopErrorCode) { super(`Desktop action refused: ${code}.`) }
}

function record(value: unknown, keys: readonly string[]): Record<string, unknown> {
  if (typeof value !== 'object' || value === null || Array.isArray(value)
    || Object.keys(value).length !== keys.length || keys.some((key) => !Object.hasOwn(value, key))) {
    throw new TypeError('Desktop request fields are invalid.')
  }
  return value as Record<string, unknown>
}

function id(value: unknown, prefix: 'chat' | 'project' | 'desktop_approval' | 'desktop_window'): string {
  const suffix = prefix === 'chat' || prefix === 'project' ? '[A-Za-z0-9_-]+' : '[0-9a-f]{32}'
  if (typeof value !== 'string' || value.length > 128 || !new RegExp(`^${prefix}_${suffix}$`, 'u').test(value)) {
    throw new TypeError('Desktop request identifier is invalid.')
  }
  return value
}

function unicode(value: unknown, maximum: number, controls = false): string {
  if (typeof value !== 'string' || value.length > maximum || /\p{Surrogate}/u.test(value)
    || Array.from(value).some((character) => {
      const point = character.codePointAt(0) ?? 0
      return point === 127 || (point < 32 && (!controls || ![9, 10, 13].includes(point)))
    })) throw new TypeError('Desktop text is invalid.')
  return value
}

function clipboardText(value: unknown, allowBlank = false): string {
  const text = unicode(value, MAX_TEXT_BYTES, true)
  if ((!allowBlank && Array.from(text).every((character) => character.trim() === '' || character === '\u0085'))
    || Buffer.byteLength(text, 'utf8') > MAX_TEXT_BYTES) throw new TypeError('Desktop clipboard text is invalid.')
  return text
}

function opaque(prefix: string): string { return `${prefix}_${randomUUID().replaceAll('-', '')}` }
function hash(bytes: string | Uint8Array): string { return createHash('sha256').update(bytes).digest('hex') }
function identity(value: { dev: bigint; ino: bigint; size: bigint; mtimeNs: bigint }): string {
  return JSON.stringify([value.dev, value.ino, value.size, value.mtimeNs].map(String))
}
function rootIdentity(value: { dev: bigint; ino: bigint }): string { return JSON.stringify([value.dev.toString(), value.ino.toString()]) }
function within(root: string, target: string): boolean {
  const relative = path.relative(root, target)
  return relative !== '..' && !relative.startsWith(`..${path.sep}`) && !path.isAbsolute(relative)
}

/** Reject extra paths, commands, or inferred tool parameters at a native IPC boundary. */
export function parseWorkDesktopChatRequest(value: unknown): { chatId: string } {
  return { chatId: id(record(value, ['chatId']).chatId, 'chat') }
}

/** Require complete bounded text for a distinct clipboard-write preview. */
export function parseWorkDesktopClipboardWriteRequest(value: unknown): { chatId: string; text: string } {
  const raw = record(value, ['chatId', 'text'])
  return { chatId: id(raw.chatId, 'chat'), text: clipboardText(raw.text) }
}

/** Accept one explicit boolean decision without changing the already reviewed action. */
export function parseWorkDesktopDecisionRequest(value: unknown): { chatId: string; approvalId: string; granted: boolean } {
  const raw = record(value, ['chatId', 'approvalId', 'granted'])
  if (typeof raw.granted !== 'boolean') throw new TypeError('Desktop approval choice is invalid.')
  return { chatId: id(raw.chatId, 'chat'), approvalId: id(raw.approvalId, 'desktop_approval'), granted: raw.granted }
}

/** Select only an opaque window choice issued to this current Work owner. */
export function parseWorkDesktopWindowSelectionRequest(value: unknown): { chatId: string; selectionId: string } {
  const raw = record(value, ['chatId', 'selectionId'])
  return { chatId: id(raw.chatId, 'chat'), selectionId: id(raw.selectionId, 'desktop_window') }
}

/** Serialize explicit native gestures and revoke every transient capability when its owner changes. */
export class WorkDesktopController {
  private revision = 0
  private epoch = 0
  private ownerKey: string | null = null
  private operation: object | null = null
  private pending: Pending | null = null
  private clipboard: string | null = null
  private windows = new Map<string, WindowChoice>()
  private selectedWindow: WorkDesktopWindow | null = null
  private lastAction: WorkDesktopAction | null = null

  /** Bind trusted Main adapters without selecting a window, reading a clipboard, or opening a file. */
  constructor(private readonly dependencies: WorkDesktopDependencies) {}

  /** Revoke previews and selected metadata while retaining physical in-flight ownership until it drains. */
  clear(): void {
    this.epoch += 1; this.revision += 1; this.ownerKey = null
    this.pending = null; this.clipboard = null; this.windows.clear()
    this.selectedWindow = null; this.lastAction = null
  }

  /** Report actual in-flight native/audit work for Main's maintenance and shutdown gate. */
  hasActiveWork(): boolean { return this.operation !== null }

  /** Return detached bounded display state after synchronizing canonical ownership. */
  getState(): WorkDesktopState {
    const { owner } = this.context()
    return structuredClone({ version: 1, owner: owner.ready && owner.mode === 'work'
      ? { chatId: owner.chatId, projectId: owner.projectId } : null,
      revision: this.revision, busy: this.hasActiveWork(),
      pending: this.pending?.approval ?? null, clipboardText: this.clipboard,
      windows: [...this.windows.values()].map((choice) => choice.public),
      selectedWindow: this.selectedWindow, lastAction: this.lastAction })
  }

  /** Prepare a selected safe document as a verified review copy, without launching its default app. */
  async chooseFile(value: unknown): Promise<WorkDesktopState> {
    const { chatId } = parseWorkDesktopChatRequest(value)
    return this.prepare(chatId, async (authority, actionId) => {
      if (authority.owner.workspacePath === null) throw new BoundaryError('path_denied')
      const selected = await this.dependencies.chooseFile()
      this.requireCurrent(authority)
      if (selected === null) return null
      const file = await this.readDocument(authority, selected, actionId)
      return { file, preview: { action: 'file.open', name: file.document.name,
        relativePath: path.relative(authority.owner.workspacePath!, file.sourcePath).split(path.sep).join('/'),
        sizeBytes: file.document.bytes.byteLength, sha256: file.document.sha256,
        impact: 'Open a verified review copy. Edits in its app do not change the Workspace original.' } }
    })
  }

  /** Prepare only Windows Notepad with no arguments from a Main-trusted system directory. */
  async previewNotepad(value: unknown): Promise<WorkDesktopState> {
    const { chatId } = parseWorkDesktopChatRequest(value)
    return this.prepare(chatId, async () => {
      const root = this.dependencies.trustedSystemRoot()
      if (this.dependencies.platform !== 'win32' || root === null) throw new BoundaryError('unavailable')
      unicode(root, 32767)
      if (!/^[A-Za-z]:\\/u.test(root) || path.win32.normalize(root) !== root
        || root.split('\\').some((component) => component === '.' || component === '..')) throw new BoundaryError('unavailable')
      return { executable: path.win32.join(root, 'System32', 'notepad.exe'),
        preview: { action: 'app.open', application: 'notepad', impact: 'Open Windows Notepad without a file or command arguments.' } }
    })
  }

  /** Prepare the complete exact text once; this does not read or modify the existing clipboard. */
  async previewClipboardWrite(value: unknown): Promise<WorkDesktopState> {
    const { chatId, text } = parseWorkDesktopClipboardWriteRequest(value)
    return this.prepare(chatId, async () => ({ preview: { action: 'clipboard.write', text,
      sizeBytes: Buffer.byteLength(text, 'utf8'), impact: 'Replace the system clipboard text with exactly the text shown. Previous clipboard text is not saved.' } }))
  }

  /** Read bounded plain text only for this dedicated user action, with no automatic clipboard polling. */
  async readClipboard(value: unknown): Promise<WorkDesktopState> {
    const { chatId } = parseWorkDesktopChatRequest(value)
    return this.run(chatId, 'clipboard.read', {}, async (authority) => {
      const raw = await this.dependencies.readClipboard()
      let text: string
      try { text = clipboardText(raw, true) } catch { throw new BoundaryError('invalid_text') }
      this.requireCurrent(authority)
      this.clipboard = text
      return { sizeBytes: Buffer.byteLength(text, 'utf8'), sha256: hash(text) }
    }, false)
  }

  /** List bounded metadata choices, keeping native source handles private and never requesting thumbnails. */
  async listWindows(value: unknown): Promise<WorkDesktopState> {
    const { chatId } = parseWorkDesktopChatRequest(value)
    const authority = this.begin(chatId)
    try {
      const sources = await this.dependencies.listWindows()
      this.requireCurrent(authority)
      if (!Array.isArray(sources) || sources.length > 100) throw new BoundaryError('unavailable')
      this.windows.clear(); this.selectedWindow = null
      const sourceIds = new Set<string>()
      for (const source of sources) {
        const title = unicode(source.title, 512).trim()
        const sourceId = unicode(source.sourceId, 512).trim()
        if (title === '' || sourceId === '' || sourceIds.has(sourceId)) throw new BoundaryError('unavailable')
        sourceIds.add(sourceId)
        const selectionId = opaque('desktop_window')
        this.windows.set(selectionId, { public: { selectionId, title }, sourceId, authority, started: this.tick() })
      }
      this.revision += 1
    } catch (error: unknown) {
      this.windows.clear()
      throw error instanceof BoundaryError ? error : new BoundaryError('native_failed')
    } finally { this.operation = null }
    return this.getState()
  }

  /** Consume one selected metadata choice after rechecking that its current native window still matches. */
  async selectWindow(value: unknown): Promise<WorkDesktopState> {
    const { chatId, selectionId } = parseWorkDesktopWindowSelectionRequest(value)
    const choice = this.windows.get(selectionId)
    if (choice === undefined || this.expired(choice.started)) throw new BoundaryError('expired')
    const authority = this.begin(chatId)
    this.windows.clear()
    try {
      if (choice.authority.key !== authority.key || choice.authority.epoch !== authority.epoch) throw new BoundaryError('context_changed')
      const current = await this.dependencies.listWindows()
      this.requireCurrent(authority)
      if (!Array.isArray(current) || current.length > 100
        || !current.some((item) => item.sourceId === choice.sourceId && item.title === choice.public.title)) {
        throw new BoundaryError('context_changed')
      }
      if (this.expired(choice.started)) throw new BoundaryError('expired')
    } catch (error: unknown) {
      throw error instanceof BoundaryError ? error : new BoundaryError('native_failed')
    } finally { this.operation = null }
    return this.run(chatId, 'window.select', { sha256: hash(choice.public.title) }, async (current) => {
      this.requireCurrent(current)
      this.selectedWindow = choice.public
      return { sha256: hash(choice.public.title) }
    }, false)
  }

  /** Consume approve or deny exactly once, revalidating owner/file state before any native effect. */
  async decide(value: unknown): Promise<WorkDesktopState> {
    const { chatId, approvalId, granted } = parseWorkDesktopDecisionRequest(value)
    const authority = this.begin(chatId)
    const pending = this.pending
    this.pending = null
    this.operation = null
    if (pending === null || pending.approval.approvalId !== approvalId) throw new BoundaryError('approval_required')
    if (pending.authority.key !== authority.key || pending.authority.epoch !== authority.epoch) throw new BoundaryError('context_changed')
    const preview = pending.approval.preview
    const summary = this.summary(preview)
    if (this.expired(pending.started)) {
      const expired = this.begin(chatId)
      try {
        await this.audit(expired, pending.actionId, preview.action, 'failed', summary, 'expired')
      } finally { this.operation = null }
      throw new BoundaryError('expired')
    }
    if (!granted) {
      const denied = this.begin(chatId)
      try {
        await this.audit(denied, pending.actionId, preview.action, 'denied', summary)
        if (this.current(denied)) this.publish(pending.actionId, preview.action, 'denied', null, false)
      } catch {
        if (this.current(denied)) this.publish(pending.actionId, preview.action, 'denied', 'audit_failed', false)
      } finally { this.operation = null }
      return this.getState()
    }
    return this.run(chatId, preview.action, summary, async (current, nativeStarted) => {
      if (this.expired(pending.started)) throw new BoundaryError('expired')
      if (preview.action === 'file.open') {
        const checked = await this.readDocument(current, pending.file!.sourcePath, pending.actionId)
        if (checked.identity !== pending.file!.identity || checked.rootIdentity !== pending.file!.rootIdentity
          || checked.document.sha256 !== pending.file!.document.sha256) throw new BoundaryError('file_changed')
        this.requireCurrent(current)
        if (this.expired(pending.started)) throw new BoundaryError('expired')
        // Opening a managed immutable copy avoids a final check-then-open
        // source-path race in shell.openPath after the caller's approved hash.
        nativeStarted()
        await this.dependencies.openDocument(pending.file!.document)
      } else if (preview.action === 'app.open') {
        nativeStarted()
        await this.dependencies.launchNotepad(pending.executable!, [])
      } else { nativeStarted(); await this.dependencies.writeClipboard(preview.text) }
      return summary
    }, true, pending.actionId)
  }

  private context(): { owner: WorkDesktopOwner; key: string } {
    const owner = this.dependencies.getOwner()
    id(owner.chatId, 'chat')
    if (owner.projectId !== null) id(owner.projectId, 'project')
    if (owner.workspacePath !== null) {
      unicode(owner.workspacePath, 32767)
      if (!path.isAbsolute(owner.workspacePath)) throw new BoundaryError('unavailable')
    }
    const key = JSON.stringify([owner.chatId, owner.projectId, owner.workspacePath])
    if (!owner.ready || owner.mode !== 'work') {
      if (this.ownerKey !== null) this.clear()
    } else if (this.ownerKey !== key) { this.clear(); this.ownerKey = key }
    return { owner: { ...owner }, key }
  }

  private begin(chatId: string): Authority {
    const { owner, key } = this.context()
    if (!owner.ready || owner.mode !== 'work' || owner.chatId !== chatId) throw new BoundaryError('unavailable')
    if (owner.busy || this.operation !== null) throw new BoundaryError('busy')
    this.operation = {}; this.lastAction = null; this.revision += 1
    return { owner, key, epoch: this.epoch }
  }

  private current(authority: Authority): boolean {
    const context = this.context()
    return this.epoch === authority.epoch && context.key === authority.key
      && context.owner.ready && context.owner.mode === 'work' && !context.owner.busy
  }

  private requireCurrent(authority: Authority): void {
    if (!this.current(authority)) throw new BoundaryError('context_changed')
  }

  private tick(): number {
    const tick = this.dependencies.monotonicNow?.() ?? performance.now()
    if (!Number.isFinite(tick) || tick < 0) throw new BoundaryError('expired')
    return tick
  }

  private expired(started: number): boolean {
    const now = this.tick()
    return now < started || now - started >= APPROVAL_TTL_MS
  }

  private async prepare(chatId: string, factory: (authority: Authority, actionId: string) => Promise<{
    preview: WorkDesktopPreview; file?: FileReview; executable?: string
  } | null>): Promise<WorkDesktopState> {
    const authority = this.begin(chatId)
    this.pending = null
    const actionId = opaque('desktop_action')
    try {
      const prepared = await factory(authority, actionId)
      this.requireCurrent(authority)
      if (prepared !== null) {
        await this.audit(authority, actionId, prepared.preview.action, 'prepared', this.summary(prepared.preview))
        this.requireCurrent(authority)
        const started = this.tick()
        this.pending = { ...prepared, authority, actionId, started, approval: {
          approvalId: opaque('desktop_approval'), preview: prepared.preview,
          risk: prepared.preview.action === 'clipboard.write' ? 'medium' : 'low',
          expiresAt: new Date(this.wall().getTime() + APPROVAL_TTL_MS).toISOString(),
        } }
        this.revision += 1
      }
    } catch (error: unknown) {
      throw error instanceof BoundaryError ? error : new BoundaryError('native_failed')
    } finally { this.operation = null }
    return this.getState()
  }

  private summary(preview: WorkDesktopPreview): WorkDesktopAuditEntry['summary'] {
    if (preview.action === 'file.open') return { relativePath: preview.relativePath, sizeBytes: preview.sizeBytes, sha256: preview.sha256 }
    if (preview.action === 'app.open') return { application: 'notepad' }
    return { sizeBytes: preview.sizeBytes, sha256: hash(preview.text) }
  }

  private wall(): Date {
    const time = this.dependencies.wallNow?.() ?? new Date()
    if (!Number.isFinite(time.getTime())) throw new BoundaryError('unavailable')
    return time
  }

  private async audit(authority: Authority, actionId: string, action: WorkDesktopActionName,
    phase: WorkDesktopAuditEntry['phase'], summary: WorkDesktopAuditEntry['summary'], error: WorkDesktopErrorCode | null = null): Promise<void> {
    try {
      await this.dependencies.audit({ version: 1, actionId, timestamp: this.wall().toISOString(),
        chatId: authority.owner.chatId, projectId: authority.owner.projectId, action, phase, summary: { ...summary }, error })
    } catch { throw new BoundaryError('audit_failed') }
  }

  private publish(actionId: string, action: WorkDesktopActionName, status: WorkDesktopAction['status'],
    error: WorkDesktopErrorCode | null, applied: boolean | null): void {
    this.lastAction = { actionId, action, status, error, applied }; this.revision += 1
  }

  private async run(chatId: string, action: WorkDesktopActionName, summary: WorkDesktopAuditEntry['summary'],
    effect: (authority: Authority, nativeStarted: () => void) => Promise<WorkDesktopAuditEntry['summary']>, changesDesktop: boolean,
    actionId = opaque('desktop_action')): Promise<WorkDesktopState> {
    const authority = this.begin(chatId)
    let applied: boolean | null = false
    try {
      await this.audit(authority, actionId, action, 'admitted', summary)
      this.requireCurrent(authority)
      const completed = await effect(authority, () => { if (changesDesktop) applied = null })
      applied = changesDesktop
      await this.audit(authority, actionId, action, 'succeeded', completed)
      if (this.current(authority)) this.publish(actionId, action, 'succeeded', null, applied)
    } catch (error: unknown) {
      const code = error instanceof BoundaryError ? error.code : 'native_failed'
      if (code !== 'audit_failed') {
        try { await this.audit(authority, actionId, action, 'failed', summary, code) } catch { /* The fixed failure remains truthful without a durable outcome. */ }
      }
      if (this.current(authority)) this.publish(actionId, action, applied === true ? 'partial' : 'failed', code, applied)
    } finally { this.operation = null }
    return this.getState()
  }

  private async readDocument(authority: Authority, selected: string, actionId: string): Promise<FileReview> {
    const root = authority.owner.workspacePath
    if (root === null) throw new BoundaryError('path_denied')
    unicode(selected, 32767)
    if (!path.isAbsolute(selected)) throw new BoundaryError('path_denied')
    const rootStat = await lstat(root, { bigint: true })
    const rootReal = await realpath(root)
    const source = path.resolve(selected)
    const sourceReal = await realpath(source)
    const relative = path.relative(rootReal, sourceReal)
    const components = relative.split(path.sep)
    if (!rootStat.isDirectory() || rootStat.isSymbolicLink() || !within(rootReal, sourceReal)
      || sourceReal !== source || sourceReal.split(path.sep).some(sensitiveComponent)
      || components.some((component) => component === ''
        || component !== component.trim() || component.endsWith('.') || /[<>:"|?*]/u.test(component)
        || /^(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)/iu.test(component))
      || !DOCUMENT_EXTENSIONS.has(path.extname(source).toLowerCase())) throw new BoundaryError('path_denied')
    let cursor = rootReal
    for (const component of components) {
      cursor = path.join(cursor, component)
      if ((await lstat(cursor, { bigint: true })).isSymbolicLink()) throw new BoundaryError('path_denied')
    }
    const leaf = await lstat(source, { bigint: true })
    if (!leaf.isFile() || leaf.isSymbolicLink() || leaf.nlink !== 1n || leaf.size > BigInt(MAX_DOCUMENT_BYTES)) throw new BoundaryError('path_denied')
    const handle = await open(source, constants.O_RDONLY | (constants.O_NOFOLLOW ?? 0))
    try {
      const before = await handle.stat({ bigint: true })
      if (!before.isFile() || identity(before) !== identity(leaf)) throw new BoundaryError('file_changed')
      const buffer = Buffer.alloc(Number(before.size))
      let offset = 0
      while (offset < buffer.length) {
        const read = await handle.read(buffer, offset, buffer.length - offset, offset)
        if (read.bytesRead === 0) throw new BoundaryError('file_changed')
        offset += read.bytesRead
      }
      const after = await handle.stat({ bigint: true })
      if (identity(after) !== identity(before) || identity(await lstat(source, { bigint: true })) !== identity(before)
        || await realpath(source) !== sourceReal || rootIdentity(await lstat(root, { bigint: true })) !== rootIdentity(rootStat)) throw new BoundaryError('file_changed')
      const extension = path.extname(source).toLowerCase()
      if (buffer.subarray(0, 2).toString('ascii') === 'MZ' || buffer.subarray(0, 2).toString('ascii') === '#!') throw new BoundaryError('path_denied')
      if (extension === '.pdf' ? !buffer.subarray(0, 5).equals(Buffer.from('%PDF-'))
        : extension === '.docx' ? !buffer.subarray(0, 4).equals(Buffer.from([0x50, 0x4b, 3, 4]))
          : buffer.includes(0)) throw new BoundaryError('path_denied')
      if (extension !== '.pdf' && extension !== '.docx') {
        try { new TextDecoder('utf-8', { fatal: true }).decode(buffer) } catch { throw new BoundaryError('path_denied') }
      }
      this.requireCurrent(authority)
      return { sourcePath: source, identity: identity(before), rootIdentity: rootIdentity(rootStat),
        document: { actionId, name: path.basename(source), bytes: new Uint8Array(buffer), sha256: hash(buffer) } }
    } finally { await handle.close() }
  }
}

/** Validate redacted stored decisions without accepting payload text or native control capabilities. */
export function parseWorkDesktopAuditEntry(value: unknown): WorkDesktopAuditEntry {
  const raw = record(value, ['version', 'actionId', 'timestamp', 'chatId', 'projectId', 'action', 'phase', 'summary', 'error'])
  const actions: WorkDesktopActionName[] = ['file.open', 'app.open', 'clipboard.read', 'clipboard.write', 'window.select']
  const phases: WorkDesktopAuditEntry['phase'][] = ['prepared', 'admitted', 'denied', 'succeeded', 'failed']
  const errors: WorkDesktopErrorCode[] = ['unavailable', 'busy', 'context_changed', 'path_denied', 'file_changed',
    'invalid_text', 'expired', 'approval_required', 'native_failed', 'audit_failed']
  if (raw.version !== 1 || typeof raw.actionId !== 'string' || !/^desktop_action_[0-9a-f]{32}$/u.test(raw.actionId)
    || typeof raw.timestamp !== 'string' || !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$/u.test(raw.timestamp)
    || !Number.isFinite(Date.parse(raw.timestamp)) || new Date(raw.timestamp).toISOString() !== raw.timestamp
    || !actions.includes(raw.action as WorkDesktopActionName) || !phases.includes(raw.phase as WorkDesktopAuditEntry['phase'])
    || (raw.error !== null && !errors.includes(raw.error as WorkDesktopErrorCode))) throw new TypeError('Desktop audit fields are invalid.')
  const action = raw.action as WorkDesktopActionName
  const phase = raw.phase as WorkDesktopAuditEntry['phase']
  const keys = action === 'file.open' ? ['relativePath', 'sizeBytes', 'sha256'] : action === 'app.open' ? ['application']
    : action === 'window.select' ? ['sha256'] : action === 'clipboard.read' && phase !== 'succeeded' ? [] : ['sizeBytes', 'sha256']
  const summary = record(raw.summary, keys)
  if ((phase === 'failed' && raw.error === null) || (phase !== 'failed' && raw.error !== null)) {
    throw new TypeError('Desktop audit outcome is inconsistent.')
  }
  if ('application' in summary && summary.application !== 'notepad') throw new TypeError('Desktop application is invalid.')
  if ('sizeBytes' in summary && (typeof summary.sizeBytes !== 'number' || !Number.isSafeInteger(summary.sizeBytes)
    || summary.sizeBytes < 0 || summary.sizeBytes > (action === 'file.open' ? MAX_DOCUMENT_BYTES : MAX_TEXT_BYTES))) {
    throw new TypeError('Desktop audit size is invalid.')
  }
  if ('sha256' in summary && (typeof summary.sha256 !== 'string' || !/^[0-9a-f]{64}$/u.test(summary.sha256))) {
    throw new TypeError('Desktop audit digest is invalid.')
  }
  if ('relativePath' in summary) {
    const relative = unicode(summary.relativePath, 4096)
    if (relative === '' || relative.split('/').some((part) => part === '' || part === '.' || part === '..'
      || part !== part.trim() || part.endsWith('.') || /[<>:"\\|?*]/u.test(part))) throw new TypeError('Desktop audit path is invalid.')
  }
  return { version: 1, actionId: raw.actionId, timestamp: raw.timestamp, chatId: id(raw.chatId, 'chat'),
    projectId: raw.projectId === null ? null : id(raw.projectId, 'project'), action, phase,
    summary: { ...summary } as WorkDesktopAuditEntry['summary'], error: raw.error as WorkDesktopErrorCode | null }
}

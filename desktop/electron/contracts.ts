/**
 * Define the narrow API shared by the React renderer and Electron shell.
 *
 * The raw Python wire protocol lives in protocol.ts. This file contains only
 * the smaller capability-safe surface exposed to the sandboxed renderer.
 */

export type BackendStatus =
  | 'starting'
  | 'handshaking'
  | 'initializing'
  | 'ready'
  | 'stopping'
  | 'stopped'
  | 'error'

export interface BackendSnapshot {
  revision: number
  status: BackendStatus
  protocolName?: string
  protocolVersion?: number
  serverVersion?: string
  capabilities: string[]
  modelName?: string
  models: string[]
  chatId?: string
  chatTitle?: string
  error?: string
}

export interface ChatRequest {
  chatId: string
  message: string
}

export interface SelectedFile {
  name: string
  sizeBytes: number
}

export type BackendEvent =
  | {
      type: 'snapshot'
      snapshot: BackendSnapshot
    }
  | {
      type: 'chat-chunk'
      requestId: string
      chatId: string
      chunk: string
    }
  | {
      type: 'chat-complete'
      requestId: string
      chatId: string
      reply: string
    }
  | {
      type: 'chat-error'
      requestId: string
      chatId: string
      code: string
      message: string
      retryable: boolean
    }
  | {
      type: 'progress'
      requestId: string
      operation: string
      completed: number
      total: number | null
      message: string | null
    }
  | {
      type: 'permission'
      requestId: string | null
      permissionId: string
      capability: string
      reason: string
      scopes: string[]
    }
  | {
      type: 'protocol-event'
      name: string
      requestId: string | null
      data: Record<string, unknown>
    }

export interface DesktopApi {
  getSnapshot(): Promise<BackendSnapshot>
  restartBackend(): Promise<BackendSnapshot>
  sendMessage(request: ChatRequest): Promise<{ requestId: string }>
  selectModel(modelName: string): Promise<BackendSnapshot>
  chooseFiles(): Promise<SelectedFile[]>
  setCharacterPanelOpen(open: boolean): Promise<void>
  onBackendEvent(listener: (event: BackendEvent) => void): () => void
}

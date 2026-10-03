/**
 * Expose a deliberately small desktop API to the sandboxed React renderer.
 */

import { contextBridge, ipcRenderer, webUtils } from 'electron'

import type {
  ArchiveChatRequest,
  ArchiveProjectRequest,
  AttachmentScope,
  AttachmentSelectionResult,
  AttachmentState,
  BackendEvent,
  BackendSnapshot,
  ChatRequest,
  ChatSessionState,
  CreateChatRequest,
  CreateProjectRequest,
  DesktopApi,
  DesktopThemePreference,
  DesktopSettingsState,
  KnowledgeExportResult,
  KnowledgeOperationReceipt,
  KnowledgeState,
  MicrophonePermissionStatus,
  MoveChatToProjectRequest,
  PinChatRequest,
  ProjectState,
  RenameChatRequest,
  RetryChatRequest,
  UpdateProjectRequest,
  UpdateDesktopSettingsRequest,
  UpdateVoiceSettingsRequest,
  VoiceCaptureReceipt,
  VoiceCaptureRequest,
  VoiceSettingsState,
  VoiceTranscriptionRequest,
} from './contracts.js'
import type {
  DesktopPetState,
  UpdateDesktopPetRequest,
} from './desktop-pet-contracts.js'
import type { DataStorageViewState } from './data-storage-contracts.js'
import type {
  PresenceNotificationState,
  UpdatePresenceNotificationRequest,
} from './presence-notification-contracts.js'

const MAX_DROPPED_ATTACHMENT_FILES = 10
const TRUSTED_SPEECH_PLAY_CHANNEL = 'elysia:trusted-speech-play:v1'
const TRUSTED_SPEECH_CANCEL_CHANNEL = 'elysia:trusted-speech-cancel:v1'
const TRUSTED_SPEECH_SETTLED_CHANNEL = 'elysia:trusted-speech-settled:v1'
const TRUSTED_SPEECH_MAX_WAV_BYTES = 8 * 1024 * 1024
const TRUSTED_SPEECH_SAMPLE_RATE_HZ = 32_000
const TRUSTED_SPEECH_MAX_DURATION_SECONDS = 120
const TRUSTED_SPEECH_MAX_OUTPUT_DEVICE_ID_CODE_POINTS = 2_048
const TRUSTED_PLAYBACK_ID_PATTERN = (
  /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/u
)

interface TrustedAudioBuffer {
  readonly duration: number
  readonly numberOfChannels: number
  readonly sampleRate: number
}

interface TrustedAudioBufferSource {
  buffer: TrustedAudioBuffer | null
  onended: (() => void) | null
  connect(destination: unknown): void
  disconnect(): void
  start(): void
  stop(): void
}

interface TrustedGainNode {
  readonly gain: { value: number }
  connect(destination: unknown): void
  disconnect(): void
}

interface TrustedAudioContext {
  readonly destination: unknown
  close(): Promise<void>
  createBufferSource(): TrustedAudioBufferSource
  createGain(): TrustedGainNode
  decodeAudioData(bytes: ArrayBuffer): Promise<TrustedAudioBuffer>
  resume(): Promise<void>
  setSinkId?(sinkId: string): Promise<void>
}

interface TrustedAudioContextConstructor {
  new(options?: { sampleRate?: number }): TrustedAudioContext
}

interface TrustedPlayback {
  graph: TrustedAudioGraph | null
  readonly playbackId: string
  settled: boolean
  source: TrustedAudioBufferSource | null
  started: boolean
}

interface TrustedAudioGraph {
  readonly context: TrustedAudioContext
  readonly gain: TrustedGainNode
  discardPromise: Promise<void> | null
}

let trustedPlayback: TrustedPlayback | null = null
let trustedAudioGraph: TrustedAudioGraph | null = null
let trustedAudioGraphCloseBarrier: Promise<void> = Promise.resolve()

function trustedAudioContextConstructor(): TrustedAudioContextConstructor | null {
  const audioGlobal = globalThis as unknown as {
    AudioContext?: TrustedAudioContextConstructor
    webkitAudioContext?: TrustedAudioContextConstructor
  }
  return audioGlobal.AudioContext ?? audioGlobal.webkitAudioContext ?? null
}

function trustedSpeechMetadata(value: unknown): {
  playbackId: string
  sequence: number
  byteLength: number
} | null {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) {
    return null
  }
  const metadata = value as Record<string, unknown>
  if (
    Object.keys(metadata).length !== 3
    || typeof metadata.playbackId !== 'string'
    || !TRUSTED_PLAYBACK_ID_PATTERN.test(metadata.playbackId)
    || !Number.isSafeInteger(metadata.sequence)
    || (metadata.sequence as number) < 0
    || (metadata.sequence as number) > 0xffff_ffff
    || !Number.isSafeInteger(metadata.byteLength)
    || (metadata.byteLength as number) < 46
    || (metadata.byteLength as number) > TRUSTED_SPEECH_MAX_WAV_BYTES
  ) {
    return null
  }
  return {
    playbackId: metadata.playbackId,
    sequence: metadata.sequence as number,
    byteLength: metadata.byteLength as number,
  }
}

function isCanonicalTrustedWav(bytes: Uint8Array): boolean {
  if (bytes.byteLength < 46 || bytes.byteLength > TRUSTED_SPEECH_MAX_WAV_BYTES) {
    return false
  }
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength)
  const ascii = (offset: number, expected: string): boolean => {
    for (let index = 0; index < expected.length; index += 1) {
      if (bytes[offset + index] !== expected.charCodeAt(index)) {
        return false
      }
    }
    return true
  }
  const dataBytes = view.getUint32(40, true)
  return (
    ascii(0, 'RIFF')
    && view.getUint32(4, true) + 8 === bytes.byteLength
    && ascii(8, 'WAVE')
    && ascii(12, 'fmt ')
    && view.getUint32(16, true) === 16
    && view.getUint16(20, true) === 1
    && view.getUint16(22, true) === 1
    && view.getUint32(24, true) === TRUSTED_SPEECH_SAMPLE_RATE_HZ
    && view.getUint32(28, true) === TRUSTED_SPEECH_SAMPLE_RATE_HZ * 2
    && view.getUint16(32, true) === 2
    && view.getUint16(34, true) === 16
    && ascii(36, 'data')
    && dataBytes > 0
    && dataBytes % 2 === 0
    && dataBytes + 44 === bytes.byteLength
    && dataBytes <= (
      TRUSTED_SPEECH_SAMPLE_RATE_HZ
      * 2
      * TRUSTED_SPEECH_MAX_DURATION_SECONDS
    )
  )
}

async function routeTrustedSpeechOutput(
  graph: TrustedAudioGraph,
  playback: TrustedPlayback,
): Promise<number> {
  const settingsValue = await ipcRenderer.invoke('voice:settings-get') as unknown
  if (
    typeof settingsValue !== 'object'
    || settingsValue === null
    || Array.isArray(settingsValue)
    || !Object.hasOwn(settingsValue, 'outputDeviceId')
  ) {
    throw new Error('Trusted speech output settings were rejected.')
  }
  const outputDeviceId = (
    settingsValue as Record<string, unknown>
  ).outputDeviceId
  if (
    outputDeviceId !== null
    && (
      typeof outputDeviceId !== 'string'
      || [...outputDeviceId].length < 1
      || [...outputDeviceId].length
        > TRUSTED_SPEECH_MAX_OUTPUT_DEVICE_ID_CODE_POINTS
      || /\p{Cc}/u.test(outputDeviceId)
      || outputDeviceId === 'default'
      || outputDeviceId === 'communications'
    )
  ) {
    throw new Error('Trusted speech output settings were rejected.')
  }
  if (trustedPlayback !== playback) {
    throw new Error('Trusted speech playback is no longer current.')
  }
  const globalSettingsValue = await ipcRenderer.invoke('settings:get') as unknown
  if (
    typeof globalSettingsValue !== 'object'
    || globalSettingsValue === null
    || Array.isArray(globalSettingsValue)
    || !Object.hasOwn(globalSettingsValue, 'activeSettings')
  ) {
    throw new Error('Trusted speech volume settings were rejected.')
  }
  const activeSettings = (
    globalSettingsValue as Record<string, unknown>
  ).activeSettings
  if (
    typeof activeSettings !== 'object'
    || activeSettings === null
    || Array.isArray(activeSettings)
    || !Object.hasOwn(activeSettings, 'speechVolumePercent')
  ) {
    throw new Error('Trusted speech volume settings were rejected.')
  }
  const speechVolumePercent = (
    activeSettings as Record<string, unknown>
  ).speechVolumePercent
  if (
    typeof speechVolumePercent !== 'number'
    || !Number.isSafeInteger(speechVolumePercent)
    || speechVolumePercent < 0
    || speechVolumePercent > 100
  ) {
    throw new Error('Trusted speech volume settings were rejected.')
  }
  if (trustedPlayback !== playback) {
    throw new Error('Trusted speech playback is no longer current.')
  }
  if (graph.context.setSinkId === undefined) {
    if (outputDeviceId !== null) {
      throw new Error('Trusted speech output routing is unavailable.')
    }
    return speechVolumePercent / 100
  }
  await graph.context.setSinkId(outputDeviceId ?? '')
  if (trustedPlayback !== playback) {
    throw new Error('Trusted speech playback is no longer current.')
  }
  return speechVolumePercent / 100
}

function enqueueTrustedAudioContextClose(
  context: TrustedAudioContext,
  gain: TrustedGainNode | null,
): Promise<void> {
  const previousClose = trustedAudioGraphCloseBarrier
  const close = previousClose.then(async (): Promise<void> => {
    if (gain !== null) {
      try {
        gain.disconnect()
      } catch {
        // The failed or unpublished graph cannot be reused after this point.
      }
    }
    try {
      await context.close()
    } catch {
      // A rejected close still leaves this context permanently quarantined.
    }
  })
  trustedAudioGraphCloseBarrier = close
  return close
}

async function createTrustedAudioGraph(
  Context: TrustedAudioContextConstructor,
): Promise<TrustedAudioGraph> {
  let context: TrustedAudioContext | null = null
  let gain: TrustedGainNode | null = null
  try {
    context = new Context({ sampleRate: TRUSTED_SPEECH_SAMPLE_RATE_HZ })
    gain = context.createGain()
    gain.connect(context.destination)
    const graph: TrustedAudioGraph = {
      context,
      gain,
      discardPromise: null,
    }
    trustedAudioGraph = graph
    return graph
  } catch {
    if (context !== null) {
      // Construction failure can race Main admitting a replacement after
      // cancellation, so its unpublished context participates in the same
      // close barrier as a graph that failed later during playback setup.
      await enqueueTrustedAudioContextClose(context, gain)
    }
    throw new Error('Trusted speech output graph could not be created.')
  }
}

async function acquireTrustedAudioGraph(
  Context: TrustedAudioContextConstructor,
): Promise<TrustedAudioGraph> {
  // Cancellation lets Main admit its replacement immediately. Wait for every
  // quarantined native graph to close before inspecting or creating the next
  // graph so Windows never owns two overlapping output-device lifetimes.
  await trustedAudioGraphCloseBarrier
  return trustedAudioGraph ?? createTrustedAudioGraph(Context)
}

function discardTrustedAudioGraph(graph: TrustedAudioGraph): Promise<void> {
  if (trustedAudioGraph === graph) {
    trustedAudioGraph = null
  }
  if (graph.discardPromise !== null) {
    return graph.discardPromise
  }
  // A failed native graph must finish closing before Main receives settlement
  // and admits another clip. This prevents overlapping Windows sink lifetimes.
  graph.discardPromise = enqueueTrustedAudioContextClose(
    graph.context,
    graph.gain,
  )
  return graph.discardPromise
}

async function settleTrustedPlayback(
  playback: TrustedPlayback,
  status: 'played' | 'failed',
  discardGraph: boolean,
): Promise<void> {
  if (trustedPlayback !== playback || playback.settled) {
    return
  }
  // Fix terminal ownership before cleanup so a completed clip cannot become
  // current again or suppress its one terminal settlement.
  playback.settled = true
  trustedPlayback = null
  if (playback.source !== null) {
    playback.source.onended = null
  }
  try {
    playback.source?.disconnect()
  } catch {
    // The result is already fixed; native cleanup details are not observable.
  }
  if (discardGraph && playback.graph !== null) {
    await discardTrustedAudioGraph(playback.graph)
  }
  ipcRenderer.send(
    TRUSTED_SPEECH_SETTLED_CHANNEL,
    Object.freeze({ playbackId: playback.playbackId, status }),
  )
}

ipcRenderer.on(
  TRUSTED_SPEECH_PLAY_CHANNEL,
  (_event, metadataValue: unknown, bytesValue: unknown): void => {
    const metadata = trustedSpeechMetadata(metadataValue)
    const bytes = bytesValue instanceof Uint8Array
      ? Uint8Array.from(bytesValue)
      : null
    const Context = trustedAudioContextConstructor()
    if (
      metadata === null
      || bytes === null
      || bytes.byteLength !== metadata.byteLength
      || !isCanonicalTrustedWav(bytes)
      || Context === null
      || trustedPlayback !== null
    ) {
      if (metadata !== null) {
        ipcRenderer.send(
          TRUSTED_SPEECH_SETTLED_CHANNEL,
          Object.freeze({ playbackId: metadata.playbackId, status: 'failed' }),
        )
      }
      return
    }

    const playback: TrustedPlayback = {
      graph: null,
      playbackId: metadata.playbackId,
      settled: false,
      source: null,
      started: false,
    }
    // Reserve admission synchronously. Promise continuation boundaries inside
    // context acquisition must not let two trusted messages share one graph.
    trustedPlayback = playback
    const ownedBytes = Uint8Array.from(bytes).buffer as ArrayBuffer
    void (async (): Promise<void> => {
      try {
        const graph = await acquireTrustedAudioGraph(Context)
        if (trustedPlayback !== playback) {
          return
        }
        playback.graph = graph
        await graph.context.resume()
        const volume = await routeTrustedSpeechOutput(graph, playback)
        const audio = await graph.context.decodeAudioData(ownedBytes)
        if (
          trustedPlayback !== playback
          || audio.numberOfChannels !== 1
          || audio.sampleRate !== TRUSTED_SPEECH_SAMPLE_RATE_HZ
          || !Number.isFinite(audio.duration)
          || audio.duration <= 0
          || audio.duration > TRUSTED_SPEECH_MAX_DURATION_SECONDS
        ) {
          throw new Error('Trusted speech decode was rejected.')
        }
        const source = graph.context.createBufferSource()
        playback.source = source
        source.buffer = audio
        graph.gain.gain.value = volume
        source.connect(graph.gain)
        source.onended = () => {
          void settleTrustedPlayback(playback, 'played', false)
        }
        source.start()
        playback.started = true
      } catch {
        if (trustedPlayback === playback) {
          await settleTrustedPlayback(
            playback,
            'failed',
            playback.graph !== null,
          )
        }
      }
    })()
  },
)

ipcRenderer.on(
  TRUSTED_SPEECH_CANCEL_CHANNEL,
  (_event, playbackId: unknown): void => {
    const playback = trustedPlayback
    if (
      playback === null
      || typeof playbackId !== 'string'
      || playbackId !== playback.playbackId
    ) {
      return
    }
    try {
      if (playback.source !== null) {
        // Some Web Audio implementations dispatch `ended` synchronously from
        // stop(). Remove it first so cancellation owns the one settlement.
        playback.source.onended = null
        playback.source.stop()
      }
    } catch {
      // A decode-pending or already-ended source still settles as cancelled.
    }
    // Setup can be waiting inside settings, sink routing, or decoding. Reusing
    // that graph would let the stale promise mutate a later clip's output, so
    // quarantine it until close completes unless audio actually started.
    void settleTrustedPlayback(playback, 'failed', !playback.started)
  },
)

const desktopApi: DesktopApi = {
  rendererReady: () =>
    ipcRenderer.invoke(
      'window:renderer-ready',
    ) as Promise<void>,

  setThemePreference: (theme: DesktopThemePreference) =>
    ipcRenderer.invoke(
      'window:set-theme',
      theme,
    ) as Promise<void>,

  getDesktopPetState: () =>
    ipcRenderer.invoke(
      'desktop-pet:get-state',
    ) as Promise<DesktopPetState>,

  updateDesktopPet: (request: UpdateDesktopPetRequest) =>
    ipcRenderer.invoke(
      'desktop-pet:update',
      request,
    ) as Promise<DesktopPetState>,

  chooseDesktopPetModelDirectory: () =>
    ipcRenderer.invoke(
      'desktop-pet:choose-model-directory',
    ) as Promise<DesktopPetState>,

  refreshDesktopPetModels: () =>
    ipcRenderer.invoke(
      'desktop-pet:refresh-models',
    ) as Promise<DesktopPetState>,

  getPresenceNotificationState: () =>
    ipcRenderer.invoke(
      'presence-notifications:get-state',
    ) as Promise<PresenceNotificationState>,

  updatePresenceNotifications: (request: UpdatePresenceNotificationRequest) =>
    ipcRenderer.invoke(
      'presence-notifications:update',
      request,
    ) as Promise<PresenceNotificationState>,

  setPresenceVoiceActive: (active: boolean) =>
    ipcRenderer.invoke(
      'presence-notifications:set-voice-active',
      active,
    ) as Promise<void>,

  getSnapshot: () =>
    ipcRenderer.invoke(
      'backend:get-snapshot',
    ) as Promise<BackendSnapshot>,

  restartBackend: () =>
    ipcRenderer.invoke(
      'backend:restart',
    ) as Promise<BackendSnapshot>,

  getDataStorageState: () =>
    ipcRenderer.invoke(
      'data-storage:get-state',
    ) as Promise<DataStorageViewState>,

  refreshDataStorageUsage: () =>
    ipcRenderer.invoke(
      'data-storage:refresh-usage',
    ) as Promise<DataStorageViewState>,

  chooseAndMoveDataDirectory: (expectedRevision: number) =>
    ipcRenderer.invoke(
      'data-storage:choose-and-move',
      expectedRevision,
    ) as Promise<DataStorageViewState>,

  clearTemporaryData: (
    expectedRevision: number,
    scanToken: string,
  ) =>
    ipcRenderer.invoke(
      'data-storage:clear-temporary',
      expectedRevision,
      scanToken,
    ) as Promise<DataStorageViewState>,

  openDataDirectory: () =>
    ipcRenderer.invoke(
      'data-storage:open-directory',
    ) as Promise<void>,

  getSettings: () =>
    ipcRenderer.invoke(
      'settings:get',
    ) as Promise<DesktopSettingsState>,

  updateSettings: (request: UpdateDesktopSettingsRequest) =>
    ipcRenderer.invoke(
      'settings:update',
      request,
    ) as Promise<DesktopSettingsState>,

  getVoiceSettings: () =>
    ipcRenderer.invoke(
      'voice:settings-get',
    ) as Promise<VoiceSettingsState>,

  updateVoiceSettings: (request: UpdateVoiceSettingsRequest) =>
    ipcRenderer.invoke(
      'voice:settings-update',
      request,
    ) as Promise<VoiceSettingsState>,

  submitVoiceCapture: (request: VoiceCaptureRequest) =>
    ipcRenderer.invoke(
      'voice:capture-complete',
      request,
    ) as Promise<VoiceCaptureReceipt>,

  beginVoiceTranscription: (request: VoiceTranscriptionRequest) =>
    ipcRenderer.invoke(
      'voice:transcription-start',
      request,
    ) as Promise<{ requestId: string }>,

  stopVoiceTranscription: (requestId: string) =>
    ipcRenderer.invoke(
      'voice:transcription-stop',
      requestId,
    ) as Promise<void>,

  getMicrophonePermissionStatus: () =>
    ipcRenderer.invoke(
      'voice:microphone-permission-status',
    ) as Promise<MicrophonePermissionStatus>,

  openMicrophonePrivacySettings: () =>
    ipcRenderer.invoke(
      'voice:open-microphone-settings',
    ) as Promise<void>,

  sendMessage: (request: ChatRequest) =>
    ipcRenderer.invoke(
      'backend:send-message',
      request,
    ) as Promise<{ requestId: string }>,

  retryMessage: (request: RetryChatRequest) =>
    ipcRenderer.invoke(
      'backend:retry-message',
      request,
    ) as Promise<{ requestId: string }>,

  stopGeneration: (requestId: string) =>
    ipcRenderer.invoke(
      'backend:stop-generation',
      requestId,
    ) as Promise<void>,

  stopSpeechPlayback: (requestId: string, chatId: string) =>
    ipcRenderer.invoke(
      'voice:stop-speech-playback',
      requestId,
      chatId,
    ) as Promise<void>,

  copyText: (text: string) =>
    ipcRenderer.invoke(
      'desktop:copy-text',
      text,
    ) as Promise<void>,

  openExternalUrl: (url: string) =>
    ipcRenderer.invoke(
      'desktop:open-external-url',
      url,
    ) as Promise<void>,

  listChats: (includeArchived: boolean) =>
    ipcRenderer.invoke(
      'chat:list',
      includeArchived,
    ) as Promise<ChatSessionState>,

  createChat: (request: CreateChatRequest) =>
    ipcRenderer.invoke(
      'chat:create',
      request,
    ) as Promise<ChatSessionState>,

  openChat: (chatId: string) =>
    ipcRenderer.invoke(
      'chat:open',
      chatId,
    ) as Promise<ChatSessionState>,

  renameChat: (request: RenameChatRequest) =>
    ipcRenderer.invoke(
      'chat:rename',
      request,
    ) as Promise<ChatSessionState>,

  setChatPinned: (request: PinChatRequest) =>
    ipcRenderer.invoke(
      'chat:pin',
      request,
    ) as Promise<ChatSessionState>,

  setChatArchived: (request: ArchiveChatRequest) =>
    ipcRenderer.invoke(
      'chat:archive',
      request,
    ) as Promise<ChatSessionState>,

  deleteChat: (chatId: string) =>
    ipcRenderer.invoke(
      'chat:delete',
      chatId,
    ) as Promise<ChatSessionState>,

  listProjects: () =>
    ipcRenderer.invoke(
      'project:list',
    ) as Promise<ProjectState>,

  createProject: (request: CreateProjectRequest) =>
    ipcRenderer.invoke(
      'project:create',
      request,
    ) as Promise<ProjectState>,

  openProject: (projectId: string) =>
    ipcRenderer.invoke(
      'project:open',
      projectId,
    ) as Promise<ProjectState>,

  updateProject: (request: UpdateProjectRequest) =>
    ipcRenderer.invoke(
      'project:update',
      request,
    ) as Promise<ProjectState>,

  chooseProjectWorkspace: (projectId: string) =>
    ipcRenderer.invoke(
      'project:choose-workspace',
      projectId,
    ) as Promise<ProjectState | null>,

  clearProjectWorkspace: (projectId: string) =>
    ipcRenderer.invoke(
      'project:clear-workspace',
      projectId,
    ) as Promise<ProjectState>,

  setProjectArchived: (request: ArchiveProjectRequest) =>
    ipcRenderer.invoke(
      'project:archive',
      request,
    ) as Promise<ProjectState>,

  moveChatToProject: (request: MoveChatToProjectRequest) =>
    ipcRenderer.invoke(
      'project:move-chat',
      request,
    ) as Promise<ProjectState>,

  selectModel: (modelName: string) =>
    ipcRenderer.invoke(
      'backend:select-model',
      modelName,
    ) as Promise<BackendSnapshot>,

  listAttachments: (scope: AttachmentScope) =>
    ipcRenderer.invoke(
      'attachment:list',
      scope,
    ) as Promise<AttachmentState>,

  chooseAttachments: (scope: AttachmentScope) =>
    ipcRenderer.invoke(
      'attachment:choose',
      scope,
    ) as Promise<AttachmentSelectionResult>,

  acceptDroppedAttachments: (
    scope: AttachmentScope,
    files: File[],
  ) => {
    if (
      !Array.isArray(files)
      || files.length === 0
      || files.length > MAX_DROPPED_ATTACHMENT_FILES
    ) {
      return Promise.reject(new Error(
        `Drop between 1 and ${MAX_DROPPED_ATTACHMENT_FILES} real local files.`,
      ))
    }
    let sourcePaths: string[]
    try {
      sourcePaths = files.map((file) => webUtils.getPathForFile(file))
    } catch {
      return Promise.reject(new Error('Dropped file selection is invalid.'))
    }
    if (sourcePaths.some((sourcePath) => sourcePath.length === 0)) {
      return Promise.reject(new Error('Drop only real local files.'))
    }
    return ipcRenderer.invoke(
      'attachment:add-dropped',
      { scope, sourcePaths },
    ) as Promise<AttachmentState>
  },

  removeAttachment: (
    scope: AttachmentScope,
    attachmentId: string,
  ) =>
    ipcRenderer.invoke(
      'attachment:remove',
      { scope, attachmentId },
    ) as Promise<AttachmentState>,

  listProjectKnowledge: (projectId: string) =>
    ipcRenderer.invoke(
      'knowledge:list',
      projectId,
    ) as Promise<KnowledgeState>,

  chooseProjectSources: (projectId: string) =>
    ipcRenderer.invoke(
      'knowledge:choose-sources',
      projectId,
    ) as Promise<KnowledgeOperationReceipt | null>,

  replaceProjectSource: (
    projectId: string,
    sourceId: string,
  ) =>
    ipcRenderer.invoke(
      'knowledge:replace-source',
      { projectId, sourceId },
    ) as Promise<KnowledgeOperationReceipt | null>,

  reindexProjectSource: (
    projectId: string,
    sourceId: string,
  ) =>
    ipcRenderer.invoke(
      'knowledge:reindex-source',
      { projectId, sourceId },
    ) as Promise<KnowledgeOperationReceipt>,

  deleteProjectSource: (
    projectId: string,
    sourceId: string,
  ) =>
    ipcRenderer.invoke(
      'knowledge:delete-source',
      { projectId, sourceId },
    ) as Promise<KnowledgeOperationReceipt>,

  rebuildProjectKnowledge: (projectId: string) =>
    ipcRenderer.invoke(
      'knowledge:rebuild',
      projectId,
    ) as Promise<KnowledgeOperationReceipt>,

  revokeProjectKnowledge: (projectId: string) =>
    ipcRenderer.invoke(
      'knowledge:revoke',
      projectId,
    ) as Promise<KnowledgeOperationReceipt>,

  recoverProjectKnowledge: (projectId: string) =>
    ipcRenderer.invoke(
      'knowledge:recover',
      projectId,
    ) as Promise<KnowledgeOperationReceipt>,

  stopKnowledgeOperation: (requestId: string) =>
    ipcRenderer.invoke(
      'knowledge:stop-operation',
      requestId,
    ) as Promise<void>,

  exportProjectSource: (
    projectId: string,
    sourceId: string,
  ) =>
    ipcRenderer.invoke(
      'knowledge:export-source',
      { projectId, sourceId },
    ) as Promise<KnowledgeExportResult | null>,

  setCharacterPanelOpen: (open: boolean) =>
    ipcRenderer.invoke(
      'window:set-character-panel',
      open,
    ) as Promise<void>,

  onDesktopPetStateChanged: (
    listener: (state: DesktopPetState) => void,
  ) => {
    const handler = (
      _event: Electron.IpcRendererEvent,
      state: DesktopPetState,
    ): void => {
      listener(state)
    }

    ipcRenderer.on('desktop-pet:state-changed', handler)
    return () => {
      ipcRenderer.removeListener('desktop-pet:state-changed', handler)
    }
  },

  onPresenceNotificationStateChanged: (
    listener: (state: PresenceNotificationState) => void,
  ) => {
    const handler = (
      _event: Electron.IpcRendererEvent,
      state: PresenceNotificationState,
    ): void => {
      listener(state)
    }

    ipcRenderer.on('presence-notifications:state-changed', handler)
    return () => {
      ipcRenderer.removeListener(
        'presence-notifications:state-changed',
        handler,
      )
    }
  },

  onBackendEvent: (
    listener: (event: BackendEvent) => void,
  ) => {
    const handler = (
      _event: Electron.IpcRendererEvent,
      event: BackendEvent,
    ): void => {
      listener(event)
    }

    ipcRenderer.on('backend:event', handler)
    return () => {
      ipcRenderer.removeListener('backend:event', handler)
    }
  },
}

contextBridge.exposeInMainWorld(
  'elysiaDesktop',
  desktopApi,
)

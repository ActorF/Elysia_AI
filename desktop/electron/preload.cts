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
import {
  advanceSpeechMouthEnvelope,
  CLOSED_SPEECH_MOUTH_ENVELOPE,
  type SpeechMouthEnvelope,
} from './speech-mouth.js'

const MAX_DROPPED_ATTACHMENT_FILES = 10
const TRUSTED_SPEECH_PLAY_CHANNEL = 'elysia:trusted-speech-play:v1'
const TRUSTED_SPEECH_CANCEL_CHANNEL = 'elysia:trusted-speech-cancel:v1'
const TRUSTED_SPEECH_SETTLED_CHANNEL = 'elysia:trusted-speech-settled:v1'
const TRUSTED_SPEECH_MAX_WAV_BYTES = 8 * 1024 * 1024
const TRUSTED_SPEECH_SAMPLE_RATE_HZ = 32_000
const TRUSTED_SPEECH_MAX_DURATION_SECONDS = 120
const TRUSTED_SPEECH_MAX_OUTPUT_DEVICE_ID_CODE_POINTS = 2_048
const TRUSTED_SPEECH_MOUTH_INTERVAL_MS = 50
const CHARACTER_VISUAL_REFRESH_EVENT = 'elysia:character-visual-refresh'
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

interface TrustedAnalyserNode {
  fftSize: number
  connect(destination: unknown): void
  disconnect(): void
  getByteTimeDomainData(samples: Uint8Array): void
}

interface TrustedAudioContext {
  readonly destination: unknown
  close(): Promise<void>
  createAnalyser(): TrustedAnalyserNode
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
  analyser: TrustedAnalyserNode | null
  readonly context: TrustedAudioContext
  envelope: SpeechMouthEnvelope
  readonly playbackId: string
  gain: TrustedGainNode | null
  mouthSamples: Uint8Array | null
  mouthTimer: ReturnType<typeof setTimeout> | null
  outputGain: number
  source: TrustedAudioBufferSource | null
  started: boolean
}

interface TrustedVisualDocument {
  readonly documentElement: {
    readonly dataset: Record<string, string | undefined>
  } | null
  querySelector(selector: string): unknown
}

let trustedPlayback: TrustedPlayback | null = null

function trustedVisualDocument(): TrustedVisualDocument | null {
  try {
    const visualGlobal = globalThis as unknown as {
      document?: TrustedVisualDocument
    }
    return visualGlobal.document ?? null
  } catch {
    // Renderer-owned DOM access is optional decoration. A revoked or unusual
    // document proxy must never escape into the trusted audio state machine.
    return null
  }
}

function publishTrustedSpeechVisual(
  playback: TrustedPlayback | null,
  active: boolean,
): void {
  try {
    const visualDocument = trustedVisualDocument()
    const root = visualDocument?.documentElement
    if (root === null || root === undefined) {
      return
    }
    root.dataset.characterSpeechActive = active ? 'true' : 'false'
    root.dataset.characterMouth = active && playback !== null
      ? playback.envelope.cue
      : 'closed'
  } catch {
    // Dataset publication is a best-effort visual sink. Treat hostile DOM
    // accessors exactly like a missing character surface and keep audio live.
  }
}

function characterSpeechVisualIsVisible(): boolean {
  try {
    const visualDocument = trustedVisualDocument()
    const root = visualDocument?.documentElement
    if (visualDocument === null || root === null || root === undefined) {
      return false
    }
    return root.dataset.characterPerformance === 'animated'
      && visualDocument.querySelector(
        '.character-artwork[data-character-state="speaking"]'
        + '[data-character-performance="animated"]'
        + '[data-character-asset="speech-atlas"]',
      ) !== null
  } catch {
    // Visibility uncertainty fails visually closed. Sampling is unnecessary
    // when Preload cannot prove that an animated speaking surface is present.
    return false
  }
}

function stopTrustedSpeechMouthSampling(
  playback: TrustedPlayback,
  active: boolean,
): void {
  const timer = playback.mouthTimer
  playback.mouthTimer = null
  if (timer !== null) {
    try {
      clearTimeout(timer)
    } catch {
      // Timer cleanup is best effort after the visual sampler is disowned.
    }
  }
  playback.envelope = CLOSED_SPEECH_MOUTH_ENVELOPE
  publishTrustedSpeechVisual(playback, active)
}

function scheduleTrustedSpeechMouthSample(playback: TrustedPlayback): void {
  if (
    trustedPlayback !== playback
    || !playback.started
    || playback.analyser === null
    || playback.mouthSamples === null
    || !characterSpeechVisualIsVisible()
  ) {
    stopTrustedSpeechMouthSampling(
      playback,
      trustedPlayback === playback && playback.started,
    )
    return
  }
  if (playback.mouthTimer !== null) {
    return
  }
  try {
    playback.mouthTimer = setTimeout(() => {
      playback.mouthTimer = null
      if (
        trustedPlayback !== playback
        || !playback.started
        || playback.analyser === null
        || playback.mouthSamples === null
        || !characterSpeechVisualIsVisible()
      ) {
        stopTrustedSpeechMouthSampling(
          playback,
          trustedPlayback === playback && playback.started,
        )
        return
      }
      try {
        playback.analyser.getByteTimeDomainData(playback.mouthSamples)
        playback.envelope = advanceSpeechMouthEnvelope(
          playback.mouthSamples,
          playback.outputGain,
          playback.envelope,
        )
        publishTrustedSpeechVisual(playback, true)
      } catch {
        // Visual analysis is optional: an analyser failure closes the mouth
        // without interrupting or rerouting the trusted audio itself.
        stopTrustedSpeechMouthSampling(playback, true)
        return
      }
      scheduleTrustedSpeechMouthSample(playback)
    }, TRUSTED_SPEECH_MOUTH_INTERVAL_MS)
  } catch {
    // An unavailable scheduler disables only visual sampling. The already
    // connected Web Audio graph and its terminal event remain authoritative.
    stopTrustedSpeechMouthSampling(playback, true)
  }
}

function refreshTrustedSpeechMouthSampling(): void {
  const playback = trustedPlayback
  if (playback === null || !playback.started) {
    publishTrustedSpeechVisual(null, false)
    return
  }
  if (!characterSpeechVisualIsVisible()) {
    stopTrustedSpeechMouthSampling(playback, true)
    return
  }
  scheduleTrustedSpeechMouthSample(playback)
}

try {
  const trustedVisualGlobal = globalThis as unknown as {
    addEventListener?: (type: string, listener: () => void) => void
  }
  trustedVisualGlobal.addEventListener?.(
    CHARACTER_VISUAL_REFRESH_EVENT,
    refreshTrustedSpeechMouthSampling,
  )
} catch {
  // Custom-event registration is an optional renderer optimization. Initial
  // visibility and terminal playback handling remain functional without it.
}
publishTrustedSpeechVisual(null, false)

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
  context: TrustedAudioContext,
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
  if (context.setSinkId === undefined) {
    if (outputDeviceId !== null) {
      throw new Error('Trusted speech output routing is unavailable.')
    }
    return speechVolumePercent / 100
  }
  await context.setSinkId(outputDeviceId ?? '')
  if (trustedPlayback !== playback) {
    throw new Error('Trusted speech playback is no longer current.')
  }
  return speechVolumePercent / 100
}

function settleTrustedPlayback(
  playback: TrustedPlayback,
  status: 'played' | 'failed',
): void {
  if (trustedPlayback !== playback) {
    return
  }
  // Fix terminal ownership before touching optional visual state so no DOM
  // behavior can make a completed clip current again or suppress settlement.
  trustedPlayback = null
  stopTrustedSpeechMouthSampling(playback, false)
  try {
    playback.source?.disconnect()
  } catch {
    // The result is already fixed; native cleanup details are not observable.
  }
  try {
    playback.analyser?.disconnect()
  } catch {
    // Visual analysis cleanup cannot change the already-fixed audio result.
  }
  try {
    playback.gain?.disconnect()
  } catch {
    // The result is already fixed; native cleanup details are not observable.
  }
  void playback.context.close().catch(() => {})
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

    let context: TrustedAudioContext
    try {
      context = new Context({ sampleRate: TRUSTED_SPEECH_SAMPLE_RATE_HZ })
    } catch {
      ipcRenderer.send(
        TRUSTED_SPEECH_SETTLED_CHANNEL,
        Object.freeze({ playbackId: metadata.playbackId, status: 'failed' }),
      )
      return
    }
    const playback: TrustedPlayback = {
      analyser: null,
      context,
      envelope: CLOSED_SPEECH_MOUTH_ENVELOPE,
      playbackId: metadata.playbackId,
      gain: null,
      mouthSamples: null,
      mouthTimer: null,
      outputGain: 0,
      source: null,
      started: false,
    }
    trustedPlayback = playback
    const ownedBytes = Uint8Array.from(bytes).buffer as ArrayBuffer
    void (async (): Promise<void> => {
      try {
        await context.resume()
        const volume = await routeTrustedSpeechOutput(context, playback)
        const audio = await context.decodeAudioData(ownedBytes)
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
        const source = context.createBufferSource()
        const analyser = context.createAnalyser()
        const gain = context.createGain()
        playback.source = source
        playback.analyser = analyser
        playback.gain = gain
        playback.mouthSamples = new Uint8Array(128)
        playback.outputGain = volume
        source.buffer = audio
        analyser.fftSize = 256
        gain.gain.value = volume
        source.connect(analyser)
        analyser.connect(gain)
        gain.connect(context.destination)
        source.onended = () => settleTrustedPlayback(playback, 'played')
        source.start()
        playback.started = true
      } catch {
        settleTrustedPlayback(playback, 'failed')
        return
      }
      try {
        publishTrustedSpeechVisual(playback, true)
        refreshTrustedSpeechMouthSampling()
      } catch {
        // This outer boundary keeps future visual changes from revising a
        // source that Web Audio has already started successfully.
        stopTrustedSpeechMouthSampling(playback, true)
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
      playback.source?.stop()
    } catch {
      // A decode-pending or already-ended source still settles as cancelled.
    }
    settleTrustedPlayback(playback, 'failed')
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

  getSnapshot: () =>
    ipcRenderer.invoke(
      'backend:get-snapshot',
    ) as Promise<BackendSnapshot>,

  restartBackend: () =>
    ipcRenderer.invoke(
      'backend:restart',
    ) as Promise<BackendSnapshot>,

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

/** Exercise the renderer shell through its user-visible desktop workflows. */

import { createRequire } from 'node:module'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

import {
  expect,
  test,
  type ElectronApplication,
  type Page,
} from '@playwright/test'
import { _electron as electron } from 'playwright'

type BackendStatus =
  | 'starting'
  | 'handshaking'
  | 'initializing'
  | 'ready'
  | 'stopping'
  | 'stopped'
  | 'error'

interface BackendSnapshot {
  revision: number
  status: BackendStatus
  capabilities: string[]
  models: string[]
  chatId?: string
  chatTitle?: string
  activeGeneration?: {
    requestId: string
    chatId: string
    kind: 'send' | 'retry'
    userText?: string
    userMessageId?: string
    assistantMessageId?: string
    reply: string
    usesProjectKnowledge?: boolean
    stopping: boolean
  }
  activeKnowledgeOperation?: {
    requestId: string
    projectId: string
    cancellable: boolean
  }
  error?: string
  modelName?: string
}

interface SelectedFile {
  mediaType?: string
  name: string
  sizeBytes: number
}

interface AttachmentScope {
  kind: 'chat' | 'project'
  id: string
}

interface AttachmentState {
  scope: AttachmentScope
  attachments: Array<{
    attachmentId: string
    fileName: string
    mediaType: string
    sizeBytes: number
    status: 'ready'
  }>
  maxFileBytes: number
  maxFileCount: number
}

interface ChatSessionSummary {
  chatId: string
  title: string
  mode: 'chat' | 'work'
  createdAt: string
  updatedAt: string
  messageCount: number
  projectId: string | null
  modelName: string
  pinned: boolean
  archived: boolean
}

interface ChatSessionState {
  activeChat: ChatSessionSummary & {
    messages: Array<{
      messageId: string
      role: 'system' | 'user' | 'assistant'
      content: string
      createdAt: string
      attachments: unknown[]
      groundedAnswer?: GroundedAnswer
    }>
  }
  chats: ChatSessionSummary[]
}

interface ProjectSummary {
  projectId: string
  name: string
  createdAt: string
  updatedAt: string
  customInstructions: string | null
  workspacePath: string | null
  archived: boolean
  chatCount: number
}

interface ProjectState {
  activeProject: ProjectSummary | null
  projects: ProjectSummary[]
  chatState: ChatSessionState
}

interface GroundedAnswer {
  status: 'answered' | 'insufficient_evidence'
  contextPassageCount: number
  statements: Array<{
    statementId: string
    kind: 'source_fact' | 'model_summary' | 'inference'
    text: string
    citationIds: string[]
  }>
  citations: Array<{
    citationId: string
    kind: 'prose' | 'code' | 'table'
    excerpt: string
    fileName: string
    mediaType: string
    pageNumber: number | null
    locations: Array<{
      kind: 'text' | 'table'
      blockOrdinal: number
      sourceStartCodePoint: number
      sourceEndCodePoint: number
      rowIndex?: number
      columnIndex?: number
    }>
  }>
}

interface KnowledgeState {
  kind: 'knowledge.state'
  projectId: string
  sources: Array<{
    sourceId: string
    fileName: string
    mediaType: string
    sizeBytes: number
    state: 'ready' | 'unindexed' | 'stale' | 'revoked' | 'processing'
    publishedAt: string | null
    operationId: string | null
  }>
  operations: Array<{
    operationId: string
    projectId: string
    kind: 'add' | 'replace' | 'reindex' | 'rebuild' | 'revoke' | 'delete'
    state: 'running' | 'cancel_requested' | 'recovery_required' | 'succeeded' | 'cancelled' | 'failed'
    phase: 'preparing' | 'importing' | 'indexing' | 'revoking' | 'cleaning' | 'publishing' | 'completed'
    progressPercent: number
    attempt: number
    createdAt: string
    updatedAt: string
    errorCode: string | null
    targetSourceId: string | null
    stagedSourceId: string | null
  }>
}

interface DesktopSettingsValues {
  modelName: string
  ollamaHost: string
  shortTermMemoryTokenBudget: number
  memoryRetrievalLimit: number
  dataImportMaxBytes: number
  transcriptionModel: 'tiny' | 'base' | 'small' | 'medium' | 'large-v3' | 'turbo'
  transcriptionDevice: 'auto' | 'cuda' | 'cpu'
  transcriptionLanguage: 'auto' | 'zh' | 'en'
  autoReadAloud: boolean
  speechRatePercent: number
  speechVolumePercent: number
  voiceProfileId: string
  voiceEmotion: 'neutral' | 'happy' | 'sad'
  captionsEnabled: boolean
  transcriptReviewMode: 'manual'
  automaticRelisten: boolean
}

interface DesktopSettingsState {
  revision: number
  updatedAt: string | null
  settings: DesktopSettingsValues
  activeSettings: DesktopSettingsValues
  restartRequired: boolean
  restartFields: Array<keyof DesktopSettingsValues>
  scopes: {
    project: {
      projectId: string
      projectName: string
      modelName: string | null
      inheritedModelName: string
    } | null
    chat: {
      chatId: string
      chatTitle: string
      modelName: string
    } | null
  }
  warning: string | null
}

type DesktopPetMode = 'disabled' | 'hidden' | 'visible'

interface DesktopPetState {
  revision: number
  updatedAt: string | null
  mode: DesktopPetMode
  runtime: 'absent' | 'loading' | 'visible' | 'failed'
  warning: string | null
}

interface VoiceSettingsState {
  kind: 'voice.settings'
  revision: number
  updatedAt: string | null
  inputDeviceId: string | null
  outputDeviceId: string | null
  transcriptionStatus: {
    state: 'unavailable' | 'available' | 'ready'
    model: DesktopSettingsValues['transcriptionModel']
    requestedDevice: DesktopSettingsValues['transcriptionDevice']
    resolvedDevice: 'cuda' | 'cpu' | null
    computeType: 'float16' | 'int8_float16' | 'int8' | 'float32' | null
    reason:
      | 'model_missing'
      | 'dependencies_missing'
      | 'runtime_probe_failed'
      | 'device_unavailable'
      | 'cuda_unavailable'
      | 'cuda_initialization_failed'
      | 'initialization_failed'
      | null
  }
  warning: string | null
}

interface VoiceTranscriptionResultControl {
  text: string
  language: 'zh' | 'en'
  languageProbability: number
}

type MicrophonePermissionStatus =
  | 'not-determined'
  | 'granted'
  | 'denied'
  | 'restricted'
  | 'unknown'

interface CallRecord {
  sequence: number
  method: string
  args: unknown[]
}

interface RendererTestControl {
  clearCalls(): void
  emitBackendEvent(event: unknown): void
  emitDesktopPetOpenChatRequested(): void
  emitDesktopPetState(state: DesktopPetState): void
  getPendingChatActionCount(): number
  getPendingChatListCount(): number
  getPendingCharacterPanelChangeCount(): number
  getPendingRestartCount(): number
  getPendingSettingsLoadCount(): number
  getPendingAttachmentActionCount(): number
  getPendingVoiceTranscriptionCount(): number
  getCalls(): CallRecord[]
  persistForReload(): void
  releaseNextChatAction(): boolean
  releaseNextChatList(): boolean
  releaseNextCharacterPanelChange(): boolean
  releaseNextRestart(): boolean
  releaseNextSettingsLoad(): boolean
  releaseNextAttachmentAction(): boolean
  releaseNextVoiceTranscription(): boolean
  setChatActionDelay(delayed: boolean): void
  setChatListDelay(delayed: boolean): void
  setCharacterPanelChangeDelay(delayed: boolean): void
  setRestartDelay(delayed: boolean): void
  setSnapshot(snapshot: BackendSnapshot): void
  setSettingsLoadDelay(delayed: boolean): void
  setAttachmentActionDelay(delayed: boolean): void
  setVoiceTranscriptionDelay(delayed: boolean): void
  setNextVoiceTranscriptionTerminalBeforeAcknowledgement(): void
  setChatState(state: ChatSessionState): void
  setProjectState(state: ProjectState): void
  setDesktopPetState(state: DesktopPetState): void
  setSettingsState(state: DesktopSettingsState): void
  setVoiceSettingsState(state: VoiceSettingsState): void
  setMicrophonePermissionStatus(status: MicrophonePermissionStatus): void
  failNextRestart(message: string): void
  failNextDesktopPetUpdate(message: string): void
  failNextSettingsUpdate(message: string): void
  failNextVoiceSettingsUpdate(message: string): void
  failNextVoiceCapture(message: string): void
  failNextVoiceTranscription(message: string): void
  setNextVoiceTranscriptionResult(
    result: VoiceTranscriptionResultControl,
  ): void
  cancelNextAttachmentPicker(): void
  failNextAttachmentAction(message: string): void
  failNextSend(message: string): void
  setAttachmentState(state: AttachmentState): void
  setKnowledgeState(state: KnowledgeState): void
  setSelectedFiles(files: SelectedFile[]): void
  setSelectedWorkspace(workspacePath: string | null): void
}

type TestWindow = Window & {
  elysiaDesktopTest: RendererTestControl
}

const require = createRequire(import.meta.url)
const electronExecutablePath = require('electron') as string
const testDirectory = path.dirname(fileURLToPath(import.meta.url))
const desktopDirectory = path.resolve(testDirectory, '..', '..')
const electronMainPath = path.join(testDirectory, 'electron-main.cjs')
const rendererPath = path.join(desktopDirectory, 'dist', 'index.html')
const mockPreloadPath = path.join(testDirectory, 'mock-preload.cjs')

let electronApp: ElectronApplication | undefined
let page: Page

function readySnapshot(
  overrides: Partial<BackendSnapshot> = {},
): BackendSnapshot {
  return {
    revision: 1,
    status: 'ready',
    capabilities: ['chat.stream'],
    models: ['qwen3.5:9b'],
    chatId: 'chat-test',
    chatTitle: 'Elysia Chat',
    modelName: 'qwen3.5:9b',
    ...overrides,
  }
}

async function emitEvent(event: unknown): Promise<void> {
  await page.evaluate((nextEvent) => {
    ;(window as TestWindow).elysiaDesktopTest.emitBackendEvent(nextEvent)
  }, event)
}

async function emitEvents(events: unknown[]): Promise<void> {
  await page.evaluate((nextEvents) => {
    const control = (window as TestWindow).elysiaDesktopTest
    for (const event of nextEvents) {
      control.emitBackendEvent(event)
    }
  }, events)
}

async function persistBackendForReload(): Promise<void> {
  await page.evaluate(() => {
    ;(window as TestWindow).elysiaDesktopTest.persistForReload()
  })
}

async function replaceRendererWindow(): Promise<void> {
  const runningApp = electronApp
  expect(runningApp).toBeDefined()
  const nextWindow = runningApp!.waitForEvent('window')
  await runningApp!.evaluate(async ({ BrowserWindow }, paths) => {
    const previousWindow = BrowserWindow.getAllWindows()[0]
    const replacementWindow = new BrowserWindow({
      width: 1180,
      height: 780,
      show: true,
      webPreferences: {
        preload: paths.preloadPath,
        contextIsolation: true,
        nodeIntegration: false,
        sandbox: true,
        partition: `elysia-ui-test-${process.pid}`,
      },
    })
    await replacementWindow.loadFile(paths.rendererPath)
    previousWindow?.close()
  }, {
    preloadPath: mockPreloadPath,
    rendererPath,
  })
  page = await nextWindow
  await page.waitForLoadState('domcontentloaded')
  await page.waitForFunction(() => (
    'elysiaDesktopTest' in window
    && (window as TestWindow).elysiaDesktopTest
      .getCalls()
      .some((call) => call.method === 'onBackendEvent.subscribe')
  ))
}

async function emitSnapshot(snapshot: BackendSnapshot): Promise<void> {
  await emitEvent({ type: 'snapshot', snapshot })
}

async function clearCalls(): Promise<void> {
  await page.evaluate(() => {
    ;(window as TestWindow).elysiaDesktopTest.clearCalls()
  })
}

async function setChatState(state: ChatSessionState): Promise<void> {
  await page.evaluate((nextState) => {
    ;(window as TestWindow).elysiaDesktopTest.setChatState(nextState)
  }, state)
}

async function setProjectState(state: ProjectState): Promise<void> {
  await page.evaluate((nextState) => {
    ;(window as TestWindow).elysiaDesktopTest.setProjectState(nextState)
  }, state)
}

async function setKnowledgeState(state: KnowledgeState): Promise<void> {
  await page.evaluate((nextState) => {
    ;(window as TestWindow).elysiaDesktopTest.setKnowledgeState(nextState)
  }, state)
}

async function setSettingsState(
  state: DesktopSettingsState,
): Promise<void> {
  await page.evaluate((nextState) => {
    ;(window as TestWindow).elysiaDesktopTest.setSettingsState(nextState)
  }, state)
}

async function setDesktopPetState(state: DesktopPetState): Promise<void> {
  await page.evaluate((nextState) => {
    ;(window as TestWindow).elysiaDesktopTest.setDesktopPetState(nextState)
  }, state)
}

async function emitDesktopPetState(state: DesktopPetState): Promise<void> {
  await page.evaluate((nextState) => {
    ;(window as TestWindow).elysiaDesktopTest.emitDesktopPetState(nextState)
  }, state)
}

async function emitDesktopPetOpenChatRequested(): Promise<void> {
  await page.evaluate(() => {
    ;(window as TestWindow).elysiaDesktopTest
      .emitDesktopPetOpenChatRequested()
  })
}

async function setVoiceSettingsState(
  state: VoiceSettingsState,
): Promise<void> {
  await page.evaluate((nextState) => {
    ;(window as TestWindow).elysiaDesktopTest.setVoiceSettingsState(nextState)
  }, state)
}

async function setMicrophonePermissionStatus(
  status: MicrophonePermissionStatus,
): Promise<void> {
  await page.evaluate((nextStatus) => {
    ;(window as TestWindow).elysiaDesktopTest
      .setMicrophonePermissionStatus(nextStatus)
  }, status)
}

interface AudioMockDevice {
  deviceId: string
  kind: 'audioinput' | 'audiooutput'
  label: string
  groupId?: string
}

interface AudioMockStats {
  enumerateCalls: number
  getUserMediaCalls: MediaStreamConstraints[]
  trackStopCount: number
  contextsCreated: number
  contextsClosed: number
  processorsCreated: number
  audioProcessCalls: number
  sinkIds: string[]
  oscillatorDurations: number[]
}

const defaultAudioMockDevices: AudioMockDevice[] = [
  {
    deviceId: 'default',
    kind: 'audioinput',
    label: 'Default - Built-in microphone',
  },
  {
    deviceId: 'communications',
    kind: 'audioinput',
    label: 'Communications - Built-in microphone',
  },
  {
    deviceId: 'mic-built-in',
    kind: 'audioinput',
    label: 'Built-in microphone',
  },
  {
    deviceId: 'mic-usb',
    kind: 'audioinput',
    label: 'USB microphone',
  },
  {
    deviceId: 'default',
    kind: 'audiooutput',
    label: 'Default - Built-in speakers',
  },
  {
    deviceId: 'speaker-built-in',
    kind: 'audiooutput',
    label: 'Built-in speakers',
  },
  {
    deviceId: 'speaker-usb',
    kind: 'audiooutput',
    label: 'USB speakers',
  },
]

async function installAudioMock(
  devices: AudioMockDevice[] = defaultAudioMockDevices,
): Promise<void> {
  await page.context().addInitScript((initialDevices) => {
    let availableDevices = initialDevices.map((device) => ({
      ...device,
      groupId: device.groupId ?? '',
      toJSON: () => ({ ...device }),
    }))
    let nextCaptureError: string | null = null
    let deferNextEnumeration = false
    let deferredEnumeration: (() => void) | null = null
    let deferNextResume = false
    let deferredResume: (() => void) | null = null
    let deferNextSinkSelection = false
    let deferredSinkSelection: (() => void) | null = null
    let echoCancellationAvailable = true
    const tracks: FakeTrack[] = []
    const processors = new Set<FakeScriptProcessor>()
    const stats: AudioMockStats = {
      enumerateCalls: 0,
      getUserMediaCalls: [],
      trackStopCount: 0,
      contextsCreated: 0,
      contextsClosed: 0,
      processorsCreated: 0,
      audioProcessCalls: 0,
      sinkIds: [],
      oscillatorDurations: [],
    }

    class FakeTrack extends EventTarget {
      readonly kind = 'audio'
      private stopped = false

      constructor(private readonly echoCancellation: boolean) {
        super()
      }

      stop(): void {
        if (!this.stopped) {
          this.stopped = true
          stats.trackStopCount += 1
        }
      }

      end(): void {
        this.dispatchEvent(new Event('ended'))
      }

      getSettings(): MediaTrackSettings {
        return { echoCancellation: this.echoCancellation }
      }
    }

    class FakeMediaDevices extends EventTarget {
      async enumerateDevices(): Promise<MediaDeviceInfo[]> {
        stats.enumerateCalls += 1
        if (deferNextEnumeration) {
          deferNextEnumeration = false
          await new Promise<void>((resolve) => {
            deferredEnumeration = resolve
          })
          deferredEnumeration = null
        }
        return availableDevices as unknown as MediaDeviceInfo[]
      }

      async getUserMedia(
        constraints: MediaStreamConstraints,
      ): Promise<MediaStream> {
        stats.getUserMediaCalls.push(structuredClone(constraints))
        if (nextCaptureError !== null) {
          const name = nextCaptureError
          nextCaptureError = null
          throw new DOMException('Synthetic audio failure.', name)
        }
        const audio = typeof constraints.audio === 'object'
          && constraints.audio !== null
          ? constraints.audio
          : null
        const echoCancellation = audio?.echoCancellation
        const requiresEchoCancellation = typeof echoCancellation === 'object'
          && echoCancellation !== null
          && echoCancellation.exact === true
        const track = new FakeTrack(
          requiresEchoCancellation && echoCancellationAvailable,
        )
        tracks.push(track)
        return {
          getAudioTracks: () => [track],
          getTracks: () => [track],
        } as unknown as MediaStream
      }
    }

    class FakeAudioParam {
      setValueAtTime(): void {}
      linearRampToValueAtTime(): void {}
    }

    class FakeAudioNode {
      connect(): FakeAudioNode {
        return this
      }

      disconnect(): void {}
    }

    class FakeAnalyser extends FakeAudioNode {
      fftSize = 2_048
      smoothingTimeConstant = 0

      getByteTimeDomainData(samples: Uint8Array): void {
        samples.fill(144)
      }
    }

    class FakeOscillator extends FakeAudioNode {
      type = 'sine'
      frequency = new FakeAudioParam()
      onended: (() => void) | null = null
      private started = false

      start(): void {
        this.started = true
      }

      stop(when?: number): void {
        if (typeof when === 'number') {
          stats.oscillatorDurations.push(when)
          window.setTimeout(() => { this.onended?.() }, 10)
          return
        }
        if (!this.started) {
          throw new DOMException('Oscillator has not started.', 'InvalidStateError')
        }
      }
    }

    class FakeGain extends FakeAudioNode {
      gain = new FakeAudioParam()
    }

    class FakeAudioBuffer {
      readonly length: number
      readonly numberOfChannels = 1
      private readonly samples: Float32Array

      constructor(samples: Float32Array) {
        this.samples = samples
        this.length = samples.length
      }

      getChannelData(): Float32Array {
        return this.samples
      }
    }

    class FakeScriptProcessor extends FakeAudioNode {
      onaudioprocess: ((event: AudioProcessingEvent) => void) | null = null

      override disconnect(): void {
        processors.delete(this)
      }

      process(samples: Float32Array): void {
        const output = new Float32Array(samples.length)
        this.onaudioprocess?.({
          inputBuffer: new FakeAudioBuffer(samples),
          outputBuffer: new FakeAudioBuffer(output),
        } as unknown as AudioProcessingEvent)
        stats.audioProcessCalls += 1
      }
    }

    class FakeAudioContext {
      state: AudioContextState = 'suspended'
      currentTime = 0
      sampleRate = 48_000
      destination = new FakeAudioNode()

      constructor() {
        stats.contextsCreated += 1
      }

      async resume(): Promise<void> {
        if (deferNextResume) {
          deferNextResume = false
          await new Promise<void>((resolve) => {
            deferredResume = resolve
          })
          deferredResume = null
        }
        if (this.state !== 'closed') {
          this.state = 'running'
        }
      }

      async close(): Promise<void> {
        if (this.state !== 'closed') {
          this.state = 'closed'
          stats.contextsClosed += 1
        }
      }

      async setSinkId(sinkId: string): Promise<void> {
        stats.sinkIds.push(sinkId)
        if (deferNextSinkSelection) {
          deferNextSinkSelection = false
          await new Promise<void>((resolve) => {
            deferredSinkSelection = resolve
          })
          deferredSinkSelection = null
        }
      }

      createMediaStreamSource(): FakeAudioNode {
        return new FakeAudioNode()
      }

      createAnalyser(): FakeAnalyser {
        return new FakeAnalyser()
      }

      createOscillator(): FakeOscillator {
        return new FakeOscillator()
      }

      createGain(): FakeGain {
        return new FakeGain()
      }

      createScriptProcessor(): FakeScriptProcessor {
        const processor = new FakeScriptProcessor()
        processors.add(processor)
        stats.processorsCreated += 1
        return processor
      }
    }

    const mediaDevices = new FakeMediaDevices()
    Object.defineProperty(navigator, 'mediaDevices', {
      configurable: true,
      value: mediaDevices,
    })
    Object.defineProperty(window, 'AudioContext', {
      configurable: true,
      value: FakeAudioContext,
    })

    Object.defineProperty(window, '__elysiaAudioMock', {
      configurable: true,
      value: {
        dispatchDeviceChange(nextDevices: AudioMockDevice[]): void {
          availableDevices = nextDevices.map((device) => ({
            ...device,
            groupId: device.groupId ?? '',
            toJSON: () => ({ ...device }),
          }))
          mediaDevices.dispatchEvent(new Event('devicechange'))
        },
        emitAudioFrames(amplitude: number, count: number): void {
          for (let frame = 0; frame < count; frame += 1) {
            const samples = new Float32Array(960)
            for (let index = 0; index < samples.length; index += 1) {
              samples[index] = amplitude * Math.sin(
                2 * Math.PI * 440 * (frame * samples.length + index) / 48_000,
              )
            }
            for (const processor of [...processors]) {
              processor.process(samples)
            }
          }
        },
        emitSpeechFrames(amplitude: number, count: number): void {
          for (let frame = 0; frame < count; frame += 1) {
            const samples = new Float32Array(960)
            const envelope = 0.75 + 0.2 * Math.sin(frame * 0.73)
            for (let index = 0; index < samples.length; index += 1) {
              const sampleIndex = frame * samples.length + index
              samples[index] = amplitude * envelope * (
                0.4 * Math.sin(2 * Math.PI * 120 * sampleIndex / 48_000)
                + 0.28 * Math.sin(2 * Math.PI * 240 * sampleIndex / 48_000)
                + 0.2 * Math.sin(2 * Math.PI * 720 * sampleIndex / 48_000)
                + 0.12 * Math.sin(2 * Math.PI * 1_450 * sampleIndex / 48_000)
              )
            }
            for (const processor of [...processors]) {
              processor.process(samples)
            }
          }
        },
        endLatestTrack(): boolean {
          const track = tracks.at(-1)
          track?.end()
          return track !== undefined
        },
        failNextCapture(name: string): void {
          nextCaptureError = name
        },
        setEchoCancellationAvailable(available: boolean): void {
          echoCancellationAvailable = available
        },
        deferNextEnumeration(): void {
          deferNextEnumeration = true
        },
        releaseEnumeration(): boolean {
          if (deferredEnumeration === null) {
            return false
          }
          deferredEnumeration()
          return true
        },
        deferNextResume(): void {
          deferNextResume = true
        },
        releaseResume(): boolean {
          if (deferredResume === null) {
            return false
          }
          deferredResume()
          return true
        },
        deferNextSinkSelection(): void {
          deferNextSinkSelection = true
        },
        releaseSinkSelection(): boolean {
          if (deferredSinkSelection === null) {
            return false
          }
          deferredSinkSelection()
          return true
        },
        getStats(): AudioMockStats {
          return structuredClone(stats)
        },
      },
    })
  }, devices)
  await persistBackendForReload()
  await page.reload()
  await page.waitForLoadState('domcontentloaded')
  await page.waitForFunction(() => (
    'elysiaDesktopTest' in window
    && (window as TestWindow).elysiaDesktopTest
      .getCalls()
      .some((call) => call.method === 'onBackendEvent.subscribe')
  ))
}

async function audioMockStats(): Promise<AudioMockStats> {
  return page.evaluate(() => (
    (window as Window & {
      __elysiaAudioMock: { getStats(): AudioMockStats }
    }).__elysiaAudioMock.getStats()
  ))
}

async function emitAudioFrames(amplitude: number, count: number): Promise<void> {
  await page.evaluate(({ frameAmplitude, frameCount }) => {
    ;(window as Window & {
      __elysiaAudioMock: {
        emitAudioFrames(amplitude: number, count: number): void
      }
    }).__elysiaAudioMock.emitAudioFrames(frameAmplitude, frameCount)
  }, { frameAmplitude: amplitude, frameCount: count })
}

async function emitSpeechFrames(amplitude: number, count: number): Promise<void> {
  await page.evaluate(({ frameAmplitude, frameCount }) => {
    ;(window as Window & {
      __elysiaAudioMock: {
        emitSpeechFrames(amplitude: number, count: number): void
      }
    }).__elysiaAudioMock.emitSpeechFrames(frameAmplitude, frameCount)
  }, { frameAmplitude: amplitude, frameCount: count })
}

async function endLatestAudioTrack(): Promise<boolean> {
  return page.evaluate(() => (
    (window as Window & {
      __elysiaAudioMock: { endLatestTrack(): boolean }
    }).__elysiaAudioMock.endLatestTrack()
  ))
}

async function failNextAudioCapture(name: string): Promise<void> {
  await page.evaluate((errorName) => {
    ;(window as Window & {
      __elysiaAudioMock: { failNextCapture(name: string): void }
    }).__elysiaAudioMock.failNextCapture(errorName)
  }, name)
}

async function setEchoCancellationAvailable(available: boolean): Promise<void> {
  await page.evaluate((nextAvailable) => {
    ;(window as Window & {
      __elysiaAudioMock: {
        setEchoCancellationAvailable(available: boolean): void
      }
    }).__elysiaAudioMock.setEchoCancellationAvailable(nextAvailable)
  }, available)
}

async function deferNextAudioEnumeration(): Promise<void> {
  await page.evaluate(() => {
    ;(window as Window & {
      __elysiaAudioMock: { deferNextEnumeration(): void }
    }).__elysiaAudioMock.deferNextEnumeration()
  })
}

async function releaseAudioEnumeration(): Promise<boolean> {
  return page.evaluate(() => (
    (window as Window & {
      __elysiaAudioMock: { releaseEnumeration(): boolean }
    }).__elysiaAudioMock.releaseEnumeration()
  ))
}

async function deferNextAudioResume(): Promise<void> {
  await page.evaluate(() => {
    ;(window as Window & {
      __elysiaAudioMock: { deferNextResume(): void }
    }).__elysiaAudioMock.deferNextResume()
  })
}

async function releaseAudioResume(): Promise<boolean> {
  return page.evaluate(() => (
    (window as Window & {
      __elysiaAudioMock: { releaseResume(): boolean }
    }).__elysiaAudioMock.releaseResume()
  ))
}

async function deferNextSinkSelection(): Promise<void> {
  await page.evaluate(() => {
    ;(window as Window & {
      __elysiaAudioMock: { deferNextSinkSelection(): void }
    }).__elysiaAudioMock.deferNextSinkSelection()
  })
}

async function releaseSinkSelection(): Promise<boolean> {
  return page.evaluate(() => (
    (window as Window & {
      __elysiaAudioMock: { releaseSinkSelection(): boolean }
    }).__elysiaAudioMock.releaseSinkSelection()
  ))
}

async function dispatchAudioDeviceChange(
  devices: AudioMockDevice[],
): Promise<void> {
  await page.evaluate((nextDevices) => {
    ;(window as Window & {
      __elysiaAudioMock: {
        dispatchDeviceChange(devices: AudioMockDevice[]): void
      }
    }).__elysiaAudioMock.dispatchDeviceChange(nextDevices)
  }, devices)
}

async function failNextSettingsUpdate(message: string): Promise<void> {
  await page.evaluate((nextMessage) => {
    ;(window as TestWindow).elysiaDesktopTest
      .failNextSettingsUpdate(nextMessage)
  }, message)
}

async function failNextDesktopPetUpdate(message: string): Promise<void> {
  await page.evaluate((nextMessage) => {
    ;(window as TestWindow).elysiaDesktopTest
      .failNextDesktopPetUpdate(nextMessage)
  }, message)
}

async function setVoiceTranscriptionDelay(delayed: boolean): Promise<void> {
  await page.evaluate((nextDelayed) => {
    ;(window as TestWindow).elysiaDesktopTest
      .setVoiceTranscriptionDelay(nextDelayed)
  }, delayed)
}

async function setNextVoiceTranscriptionResult(
  result: VoiceTranscriptionResultControl,
): Promise<void> {
  await page.evaluate((nextResult) => {
    ;(window as TestWindow).elysiaDesktopTest
      .setNextVoiceTranscriptionResult(nextResult)
  }, result)
}

async function setNextVoiceTranscriptionTerminalBeforeAcknowledgement(): Promise<void> {
  await page.evaluate(() => {
    ;(window as TestWindow).elysiaDesktopTest
      .setNextVoiceTranscriptionTerminalBeforeAcknowledgement()
  })
}

async function failNextVoiceTranscription(message: string): Promise<void> {
  await page.evaluate((nextMessage) => {
    ;(window as TestWindow).elysiaDesktopTest
      .failNextVoiceTranscription(nextMessage)
  }, message)
}

async function releaseNextVoiceTranscription(): Promise<boolean> {
  return page.evaluate(() => (
    (window as TestWindow).elysiaDesktopTest.releaseNextVoiceTranscription()
  ))
}

type DraftStorageFailureName = 'QuotaExceededError' | 'SecurityError'

async function rejectChatDraftStorageWrites(
  failureName: DraftStorageFailureName,
): Promise<void> {
  await page.evaluate((nextFailureName) => {
    const testWindow = window as Window & {
      restoreVoiceDraftStorage?: () => void
    }
    testWindow.restoreVoiceDraftStorage?.()
    const originalSetItem = Storage.prototype.setItem
    testWindow.restoreVoiceDraftStorage = () => {
      Storage.prototype.setItem = originalSetItem
      delete testWindow.restoreVoiceDraftStorage
    }
    Storage.prototype.setItem = function setItem(
      key: string,
      value: string,
    ): void {
      if (key === 'elysia.chat-drafts.v1') {
        throw new DOMException(
          `Test ${nextFailureName}`,
          nextFailureName,
        )
      }
      originalSetItem.call(this, key, value)
    }
  }, failureName)
}

async function restoreChatDraftStorageWrites(): Promise<void> {
  await page.evaluate(() => {
    const testWindow = window as Window & {
      restoreVoiceDraftStorage?: () => void
    }
    testWindow.restoreVoiceDraftStorage?.()
  })
}

async function failNextRestart(message: string): Promise<void> {
  await page.evaluate((nextMessage) => {
    ;(window as TestWindow).elysiaDesktopTest.failNextRestart(nextMessage)
  }, message)
}

async function setRestartDelay(delayed: boolean): Promise<void> {
  await page.evaluate((nextDelayed) => {
    ;(window as TestWindow).elysiaDesktopTest.setRestartDelay(nextDelayed)
  }, delayed)
}

async function releaseNextRestart(): Promise<boolean> {
  return page.evaluate(() => (
    (window as TestWindow).elysiaDesktopTest.releaseNextRestart()
  ))
}

async function setSettingsLoadDelay(delayed: boolean): Promise<void> {
  await page.evaluate((nextDelayed) => {
    ;(window as TestWindow).elysiaDesktopTest
      .setSettingsLoadDelay(nextDelayed)
  }, delayed)
}

async function releaseNextSettingsLoad(): Promise<boolean> {
  return page.evaluate(() => (
    (window as TestWindow).elysiaDesktopTest.releaseNextSettingsLoad()
  ))
}

async function setSelectedWorkspace(
  workspacePath: string | null,
): Promise<void> {
  await page.evaluate((nextWorkspacePath) => {
    ;(window as TestWindow).elysiaDesktopTest
      .setSelectedWorkspace(nextWorkspacePath)
  }, workspacePath)
}

function chatSummary(
  chatId: string,
  title: string,
  overrides: Partial<ChatSessionSummary> = {},
): ChatSessionSummary {
  return {
    chatId,
    title,
    mode: 'chat',
    createdAt: '2026-08-25T12:00:00+00:00',
    updatedAt: '2026-08-25T12:30:00+00:00',
    messageCount: 0,
    projectId: null,
    modelName: 'qwen3.5:9b',
    pinned: false,
    archived: false,
    ...overrides,
  }
}

function projectSummary(
  projectId: string,
  name: string,
  overrides: Partial<ProjectSummary> = {},
): ProjectSummary {
  return {
    projectId,
    name,
    createdAt: '2026-08-25T11:00:00+00:00',
    updatedAt: '2026-08-25T12:30:00+00:00',
    customInstructions: null,
    workspacePath: null,
    archived: false,
    chatCount: 0,
    ...overrides,
  }
}

function desktopSettingsState(
  overrides: Partial<DesktopSettingsState> = {},
): DesktopSettingsState {
  const settings = overrides.settings ?? {
    modelName: 'qwen3.5:9b',
    ollamaHost: 'http://localhost:11434',
    shortTermMemoryTokenBudget: 2048,
    memoryRetrievalLimit: 5,
    dataImportMaxBytes: 16_777_216,
    transcriptionModel: 'small',
    transcriptionDevice: 'auto',
    transcriptionLanguage: 'auto',
    autoReadAloud: true,
    speechRatePercent: 100,
    speechVolumePercent: 100,
    voiceProfileId: 'default',
    voiceEmotion: 'neutral',
    captionsEnabled: true,
    transcriptReviewMode: 'manual',
    automaticRelisten: false,
  }
  const defaultState: DesktopSettingsState = {
    revision: 0,
    updatedAt: null,
    settings,
    activeSettings: overrides.activeSettings ?? { ...settings },
    restartRequired: false,
    restartFields: [],
    scopes: {
      project: null,
      chat: {
        chatId: 'chat-test',
        chatTitle: 'Elysia Chat',
        modelName: 'qwen3.5:9b',
      },
    },
    warning: null,
  }
  return {
    ...defaultState,
    ...overrides,
    settings,
    activeSettings: overrides.activeSettings ?? { ...settings },
  }
}

function desktopPetState(
  overrides: Partial<DesktopPetState> = {},
): DesktopPetState {
  return {
    revision: 0,
    updatedAt: null,
    mode: 'disabled',
    runtime: 'absent',
    warning: null,
    ...overrides,
  }
}

function voiceSettingsState(
  overrides: Partial<VoiceSettingsState> = {},
): VoiceSettingsState {
  return {
    kind: 'voice.settings',
    revision: 0,
    updatedAt: null,
    inputDeviceId: null,
    outputDeviceId: null,
    transcriptionStatus: {
      state: 'ready',
      model: 'small',
      requestedDevice: 'auto',
      resolvedDevice: 'cpu',
      computeType: 'int8',
      reason: 'cuda_unavailable',
    },
    warning: null,
    ...overrides,
  }
}

async function openSettings(
  snapshot: BackendSnapshot = readySnapshot(),
): Promise<void> {
  await emitSnapshot(snapshot)
  await pressControlShortcut(',')
  await expect(
    page.getByRole('heading', { name: 'Settings', exact: true }),
  ).toBeVisible()
  await expect(
    page.getByRole('heading', { name: 'General', exact: true }),
  ).toBeVisible()
}

async function openVoiceCapturePage(): Promise<void> {
  await emitSnapshot(readySnapshot({
    capabilities: [
      'chat.stream',
      'voice.settings',
      'voice.capture',
      'voice.transcription',
    ],
  }))
  await page.getByRole('button', { name: 'Start voice' }).click()
  await expect(page.getByRole('main', { name: 'Voice capture' })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Start microphone' }))
    .toBeEnabled()
}

async function openVoiceWithFinalTranscript(text: string): Promise<void> {
  await emitSnapshot(readySnapshot({
    capabilities: [
      'chat.stream',
      'voice.settings',
      'voice.capture',
      'voice.transcription',
      'voice.speech',
      'voice.speech.cancel',
    ],
  }))
  await setVoiceTranscriptionDelay(true)
  await setNextVoiceTranscriptionResult({
    text,
    language: 'en',
    languageProbability: 0.99,
  })
  await page.getByRole('button', { name: 'Start voice' }).click()
  await page.getByRole('button', { name: 'Start microphone' }).click()
  await emitSpeechFrames(0.08, 10)
  await emitAudioFrames(0, 30)
  expect(await releaseNextVoiceTranscription()).toBe(true)
  await expect(page.getByRole('textbox', { name: 'Final transcript' }))
    .toHaveValue(text)
}

async function getCalls(): Promise<CallRecord[]> {
  return page.evaluate(() => (
    (window as TestWindow).elysiaDesktopTest.getCalls()
  ))
}

async function pressControlShortcut(key: string): Promise<void> {
  await page.keyboard.down('Control')
  try {
    await page.keyboard.press(key)
  } finally {
    await page.keyboard.up('Control')
  }
}

async function waitForTwoAnimationFrames(): Promise<void> {
  await page.evaluate(() => new Promise<void>((resolve) => {
    window.requestAnimationFrame(() => {
      window.requestAnimationFrame(() => { resolve() })
    })
  }))
}

async function setWindowAndZoom(
  width: number,
  height: number,
  zoomFactor: number,
): Promise<void> {
  if (electronApp === undefined) {
    throw new Error('Electron is not running.')
  }

  await electronApp.evaluate(
    ({ BrowserWindow }, settings) => {
      const window = BrowserWindow.getAllWindows()[0]
      if (window === undefined) {
        throw new Error('The UI test window is missing.')
      }
      window.setContentSize(settings.width, settings.height, false)
      window.webContents.setZoomFactor(settings.zoomFactor)
    },
    { width, height, zoomFactor },
  )

  await expect.poll(async () => electronApp?.evaluate(
    ({ BrowserWindow }) => (
      BrowserWindow.getAllWindows()[0]?.webContents.getZoomFactor()
    ),
  )).toBeCloseTo(zoomFactor, 2)
  await waitForTwoAnimationFrames()
}

async function expectShellWithoutHorizontalOverflow(): Promise<void> {
  const layout = await page.evaluate(() => {
    function rectangle(selector: string): DOMRect {
      const element = document.querySelector(selector)
      if (!(element instanceof HTMLElement)) {
        throw new Error(`Missing layout element: ${selector}`)
      }
      return element.getBoundingClientRect()
    }

    const root = document.documentElement
    const body = document.body
    const workspace = rectangle('.workspace-surface')
    const messages = rectangle('.message-scroll')
    const composer = rectangle('.composer-zone')
    const composerCard = rectangle('.composer-card')

    return {
      viewportWidth: window.innerWidth,
      rootScrollWidth: root.scrollWidth,
      bodyScrollWidth: body.scrollWidth,
      workspace: {
        left: workspace.left,
        right: workspace.right,
        top: workspace.top,
        bottom: workspace.bottom,
      },
      messages: {
        left: messages.left,
        right: messages.right,
        top: messages.top,
        bottom: messages.bottom,
        height: messages.height,
      },
      composer: {
        left: composer.left,
        right: composer.right,
        top: composer.top,
        bottom: composer.bottom,
        height: composer.height,
      },
      composerCard: {
        left: composerCard.left,
        right: composerCard.right,
      },
    }
  })

  expect(layout.rootScrollWidth).toBeLessThanOrEqual(
    layout.viewportWidth + 1,
  )
  expect(layout.bodyScrollWidth).toBeLessThanOrEqual(
    layout.viewportWidth + 1,
  )
  expect(layout.workspace.left).toBeGreaterThanOrEqual(-1)
  expect(layout.workspace.right).toBeLessThanOrEqual(
    layout.viewportWidth + 1,
  )
  expect(layout.messages.left).toBeGreaterThanOrEqual(
    layout.workspace.left - 1,
  )
  expect(layout.messages.right).toBeLessThanOrEqual(
    layout.workspace.right + 1,
  )
  expect(layout.messages.height).toBeGreaterThan(0)
  expect(layout.messages.bottom).toBeLessThanOrEqual(
    layout.composer.top + 1,
  )
  expect(layout.composer.left).toBeGreaterThanOrEqual(
    layout.workspace.left - 1,
  )
  expect(layout.composer.right).toBeLessThanOrEqual(
    layout.workspace.right + 1,
  )
  expect(layout.composer.bottom).toBeLessThanOrEqual(
    layout.workspace.bottom + 1,
  )
  expect(layout.composer.height).toBeGreaterThan(0)
  expect(layout.composerCard.left).toBeGreaterThanOrEqual(
    layout.composer.left - 1,
  )
  expect(layout.composerCard.right).toBeLessThanOrEqual(
    layout.composer.right + 1,
  )
}

async function readThemeState(): Promise<{
  preference?: string
  resolved?: string
  stored: string | null
  colorScheme: string
}> {
  return page.evaluate(() => ({
    preference: document.documentElement.dataset.themePreference,
    resolved: document.documentElement.dataset.theme,
    stored: window.localStorage.getItem('elysia.theme'),
    colorScheme: document.documentElement.style.colorScheme,
  }))
}

async function readCharacterPerformanceState(): Promise<{
  preference?: string
  resolved?: string
  stored: string | null
}> {
  return page.evaluate(() => ({
    preference: document.documentElement.dataset.characterPerformancePreference,
    resolved: document.documentElement.dataset.characterPerformance,
    stored: window.localStorage.getItem('elysia.characterPerformance'),
  }))
}

test.beforeEach(async () => {
  electronApp = await electron.launch({
    args: [electronMainPath],
    colorScheme: 'dark',
    cwd: desktopDirectory,
    executablePath: electronExecutablePath,
  })
  page = await electronApp.firstWindow()
  await page.waitForLoadState('domcontentloaded')
  await page.waitForFunction(() => (
    'elysiaDesktopTest' in window
    && (window as TestWindow).elysiaDesktopTest
      .getCalls()
      .some((call) => call.method === 'onBackendEvent.subscribe')
  ))
  await expect(
    page.getByRole('heading', { name: 'Talk with Elysia' }),
  ).toBeVisible()
})

test.afterEach(async () => {
  const runningApp = electronApp
  electronApp = undefined
  if (runningApp !== undefined) {
    await runningApp.close()
  }
})

test('shows starting, ready, and retryable error feedback', async () => {
  await expect(page.locator('.connection-pill')).toContainText('Connecting')
  await expect(page.locator('.loading-state')).toContainText(
    'The local conversation will unlock automatically.',
  )
  await expect(page.getByLabel('Message Elysia')).toBeDisabled()

  await emitSnapshot(readySnapshot())
  await expect(page.locator('.connection-pill')).toContainText('Connected')
  await expect(page.locator('.loading-state')).toHaveCount(0)
  await expect(page.getByLabel('Message Elysia')).toBeEnabled()

  await emitSnapshot({
    ...readySnapshot(),
    revision: 2,
    status: 'error',
    error: 'The test Backend is unavailable.',
  })
  await expect(page.locator('.connection-pill')).toContainText(
    'Connection error',
  )
  await expect(page.getByRole('alert')).toContainText(
    'The test Backend is unavailable.',
  )
  await expect(page.getByLabel('Message Elysia')).toBeDisabled()

  await clearCalls()
  await page.getByRole('button', { name: 'Retry' }).click()
  await expect.poll(async () => (
    (await getCalls()).map((call) => call.method)
  )).toContain('restartBackend')
})

test('ignores a stale error snapshot while a newer Chat is streaming', async () => {
  await emitSnapshot(readySnapshot({
    revision: 5,
    chatId: 'chat-streaming',
  }))
  const composer = page.getByLabel('Message Elysia')
  await expect(composer).toBeEnabled()
  await composer.fill('Keep this reply streaming.')
  await composer.press('Enter')
  await expect.poll(async () => (
    (await getCalls()).filter((call) => call.method === 'sendMessage').length
  )).toBe(1)

  await emitEvent({
    type: 'chat-chunk',
    requestId: 'test-request-1',
    chatId: 'chat-streaming',
    chunk: 'A reply that is still arriving',
  })
  const messageColumn = page.locator('.message-column')
  const streamingReply = page.getByLabel('Message from Elysia').last()
  await expect(messageColumn).toHaveAttribute('aria-busy', 'true')
  await expect(streamingReply.locator('.stream-caret')).toBeVisible()

  await emitSnapshot({
    ...readySnapshot({
      revision: 4,
      chatId: 'chat-streaming',
    }),
    status: 'error',
    error: 'This stale error must not replace revision 5.',
  })

  await expect(page.locator('.connection-pill')).toContainText('Connected')
  await expect.soft(messageColumn).toHaveAttribute('aria-busy', 'true')
  await expect.soft(streamingReply.locator('.stream-caret')).toBeVisible()
  await expect.soft(streamingReply).not.toContainText('Reply interrupted')
  await expect.soft(page.getByText(
    'This stale error must not replace revision 5.',
    { exact: true },
  )).toHaveCount(0)
})

test('announces Chat errors as assertive error notifications', async () => {
  const errorMessage = 'The local model interrupted this reply.'
  await emitSnapshot(readySnapshot())
  const composer = page.getByLabel('Message Elysia')
  await expect(composer).toBeEnabled()
  await composer.fill('Start a reply that will fail.')
  await composer.press('Enter')
  await expect.poll(async () => (
    (await getCalls()).filter((call) => call.method === 'sendMessage').length
  )).toBe(1)

  await emitEvent({
    type: 'chat-error',
    requestId: 'test-request-1',
    chatId: 'chat-test',
    code: 'MODEL_INTERRUPTED',
    message: errorMessage,
    retryable: true,
  })

  const notification = page.locator('.inline-alert').filter({
    hasText: errorMessage,
  })
  await expect(notification).toBeVisible()
  await expect.soft(notification).toHaveAttribute('role', 'alert')
  await expect.soft(notification).toHaveAttribute('aria-live', 'assertive')
  await expect.soft(notification).toHaveClass(/inline-alert-error/)
})

test('supports global navigation shortcuts and restores search focus', async () => {
  await pressControlShortcut('k')
  const searchInput = page.getByPlaceholder('Search chats')
  await expect(searchInput).toBeVisible()
  await expect(searchInput).toBeFocused()

  await searchInput.fill('missing chat')
  await expect(page.getByText('No matching chats')).toBeVisible()
  await searchInput.press('Escape')
  await expect(searchInput).toHaveCount(0)
  await expect(page.getByRole('button', { name: 'Search chats' })).toBeFocused()

  const sidebar = page.locator('#app-sidebar')
  await expect(sidebar).toHaveAttribute('aria-hidden', 'false')
  await pressControlShortcut('b')
  await expect(sidebar).toHaveAttribute('aria-hidden', 'true')
  await pressControlShortcut('b')
  await expect(sidebar).toHaveAttribute('aria-hidden', 'false')

  await pressControlShortcut(',')
  await expect(
    page.getByRole('heading', { name: 'Settings', exact: true }),
  ).toBeVisible()
  await expect(
    page.getByRole('button', { name: 'Settings' }),
  ).toHaveAttribute('aria-current', 'page')
})

test('renders canonical Chat metadata and opens persisted sessions', async () => {
  const brandIcon = page.locator('.brand-mark img')
  await expect(brandIcon).toBeVisible()
  await expect(brandIcon).toHaveAttribute('src', './elysia-icon.png')
  await expect.poll(() => brandIcon.evaluate((element) => (
    (element as HTMLImageElement).naturalWidth
  ))).toBeGreaterThan(0)

  const first = chatSummary('chat-first', 'Pinned work', {
    mode: 'work',
    projectId: 'project_alpha',
    pinned: true,
    messageCount: 1,
  })
  const second = chatSummary('chat-second', 'Personal notes', {
    updatedAt: '2026-08-25T12:10:00+00:00',
  })
  const chatState: ChatSessionState = {
    activeChat: {
      ...first,
      messages: [{
        messageId: 'message-persisted',
        role: 'assistant',
        content: 'Loaded from persisted history.',
        createdAt: '2026-08-25T12:30:00+00:00',
        attachments: [],
      }],
    },
    chats: [first, second],
  }
  const project = projectSummary('project_alpha', 'Sidebar Project', {
    chatCount: 1,
  })
  await setProjectState({
    activeProject: project,
    projects: [project],
    chatState,
  })
  await emitSnapshot(readySnapshot({
    chatId: first.chatId,
    chatTitle: first.title,
  }))

  const firstRow = page.getByRole('button', {
    name: `Open chat ${first.title}`,
  })
  await expect(firstRow).toContainText('Work')
  await expect(firstRow).toContainText('project_alpha')
  await expect(firstRow).toContainText('Pinned')
  await expect(page.getByLabel('1 projects')).toBeVisible()
  await expect(page.getByText('Loaded from persisted history.')).toBeVisible()

  await clearCalls()
  await page.getByRole('button', {
    name: `Open chat ${second.title}`,
  }).click()
  await expect.poll(async () => (
    (await getCalls()).find((call) => call.method === 'openChat')?.args
  )).toEqual([second.chatId])
  await expect(page.locator('.chat-heading strong')).toHaveText(second.title)

  await clearCalls()
  await page.getByRole('button', { name: 'Create chat' }).click()
  await expect.poll(async () => (
    (await getCalls()).find((call) => call.method === 'createChat')?.args
  )).toEqual([{ title: 'New Chat', mode: 'chat' }])
  await expect(page.getByRole('button', { name: 'Open chat New Chat' })).toBeVisible()
})

test('renames, pins, and confirms permanent Chat deletion', async () => {
  await emitSnapshot(readySnapshot())
  await expect(page.getByRole('button', {
    name: 'Open chat Elysia Chat',
  })).toBeVisible()

  await page.getByRole('button', {
    name: 'More actions for Elysia Chat',
  }).click()
  await page.getByRole('menuitem', { name: 'Rename' }).click()
  const renameDialog = page.getByRole('dialog', { name: 'Rename Chat' })
  await expect(renameDialog).toBeVisible()
  await renameDialog.getByLabel('Chat title').fill('Renamed locally')
  await renameDialog.getByRole('button', { name: 'Save' }).click()
  await expect.poll(async () => (
    (await getCalls()).find((call) => call.method === 'renameChat')?.args
  )).toEqual([{ chatId: 'chat-test', title: 'Renamed locally' }])
  await expect(page.getByRole('button', {
    name: 'Open chat Renamed locally',
  })).toBeVisible()

  await clearCalls()
  await page.getByRole('button', {
    name: 'More actions for Renamed locally',
  }).click()
  await page.getByRole('menuitem', { name: 'Pin' }).click()
  await expect.poll(async () => (
    (await getCalls()).find((call) => call.method === 'setChatPinned')?.args
  )).toEqual([{ chatId: 'chat-test', pinned: true }])

  await clearCalls()
  await page.getByRole('button', {
    name: 'More actions for Renamed locally',
  }).click()
  await page.getByRole('menuitem', { name: 'Delete' }).click()
  const deleteDialog = page.getByRole('dialog', {
    name: /Delete “Renamed locally”/,
  })
  await expect(deleteDialog).toBeVisible()
  await deleteDialog.getByRole('button', { name: 'Cancel' }).click()
  expect((await getCalls()).some((call) => call.method === 'deleteChat')).toBe(false)

  await page.getByRole('button', {
    name: 'More actions for Renamed locally',
  }).click()
  await page.getByRole('menuitem', { name: 'Delete' }).click()
  await page.getByRole('dialog', {
    name: /Delete “Renamed locally”/,
  }).getByRole('button', { name: 'Delete Chat' }).click()
  await expect.poll(async () => (
    (await getCalls()).find((call) => call.method === 'deleteChat')?.args
  )).toEqual(['chat-test'])
  await expect(page.getByRole('button', {
    name: 'Open chat Renamed locally',
  })).toHaveCount(0)
})

test('requires confirmation for bulk archive and delete actions', async () => {
  const first = chatSummary('chat-bulk-one', 'Bulk one')
  const second = chatSummary('chat-bulk-two', 'Bulk two', {
    mode: 'work',
  })
  await setChatState({
    activeChat: { ...first, messages: [] },
    chats: [first, second],
  })
  await emitSnapshot(readySnapshot({
    chatId: first.chatId,
    chatTitle: first.title,
  }))
  await expect(page.getByRole('button', { name: 'Select chats' })).toBeEnabled()

  await page.getByRole('button', { name: 'Select chats' }).click()
  await page.getByRole('checkbox', { name: 'Select all visible' }).check()
  await page.getByRole('button', { name: 'Archive', exact: true }).click()
  const archiveDialog = page.getByRole('dialog', {
    name: 'Archive 2 selected chats?',
  })
  await expect(archiveDialog).toBeVisible()
  expect((await getCalls()).some(
    (call) => call.method === 'setChatArchived',
  )).toBe(false)
  await archiveDialog.getByRole('button', { name: 'Archive chats' }).click()
  await expect.poll(async () => (
    (await getCalls())
      .filter((call) => call.method === 'setChatArchived')
      .map((call) => call.args[0])
  )).toEqual([
    { chatId: first.chatId, archived: true },
    { chatId: second.chatId, archived: true },
  ])

  await page.getByRole('button', { name: 'Archived' }).click()
  await expect(page.getByRole('button', {
    name: `Open chat ${first.title}`,
  })).toBeVisible()
  await clearCalls()
  await page.getByRole('button', { name: 'Select chats' }).click()
  await page.getByRole('checkbox', { name: 'Select all visible' }).check()
  await page.getByRole('button', { name: 'Delete', exact: true }).click()
  const deleteDialog = page.getByRole('dialog', {
    name: 'Delete 2 selected chats?',
  })
  await expect(deleteDialog).toBeVisible()
  expect((await getCalls()).some((call) => call.method === 'deleteChat')).toBe(false)
  await deleteDialog.getByRole('button', { name: 'Delete chats' }).click()
  await expect.poll(async () => (
    (await getCalls())
      .filter((call) => call.method === 'deleteChat')
      .map((call) => call.args[0])
  )).toEqual([first.chatId, second.chatId])
})

test('moves focus to the workspace trigger when the wide sidebar closes', async () => {
  await setWindowAndZoom(1180, 780, 1)
  const sidebar = page.locator('#app-sidebar')
  const sidebarControl = page.getByRole('button', { name: 'Search chats' })

  await expect(sidebar).toHaveAttribute('aria-hidden', 'false')
  await sidebarControl.focus()
  await expect(sidebarControl).toBeFocused()
  await pressControlShortcut('b')
  await expect(sidebar).toHaveAttribute('aria-hidden', 'true')
  await waitForTwoAnimationFrames()

  const workspaceTrigger = page.getByRole('button', {
    name: 'Show navigation',
  })
  await expect(workspaceTrigger).toBeFocused()
  expect(await page.evaluate(() => document.activeElement?.tagName)).not.toBe(
    'BODY',
  )
})

test('settles a pending panel open before leaving Chat for Settings', async () => {
  await emitSnapshot(readySnapshot())
  await page.evaluate(() => {
    ;(window as TestWindow).elysiaDesktopTest
      .setCharacterPanelChangeDelay(true)
  })
  await clearCalls()

  await page.getByRole('button', { name: 'Expand Elysia panel' }).click()
  await expect.poll(() => page.evaluate(() => (
    (window as TestWindow).elysiaDesktopTest
      .getPendingCharacterPanelChangeCount()
  ))).toBe(1)
  await expect.poll(async () => (
    (await getCalls())
      .filter((call) => call.method === 'setCharacterPanelOpen')
      .map((call) => call.args[0])
  )).toEqual([true])

  await pressControlShortcut(',')
  await expect(
    page.getByRole('heading', { name: 'Settings', exact: true }),
  ).toBeVisible()
  await expect(page.locator('#character-panel')).toHaveCount(0)

  await page.evaluate(() => {
    ;(window as TestWindow).elysiaDesktopTest
      .releaseNextCharacterPanelChange()
  })
  await waitForTwoAnimationFrames()
  const queuedCalls = (await getCalls())
    .filter((call) => call.method === 'setCharacterPanelOpen')
    .map((call) => call.args[0])
  expect.soft(
    queuedCalls,
    'leaving Chat must queue a close behind the pending open',
  ).toEqual([true, false])

  const pendingClose = await page.evaluate(() => (
    (window as TestWindow).elysiaDesktopTest
      .getPendingCharacterPanelChangeCount()
  ))
  if (pendingClose > 0) {
    await page.evaluate(() => {
      ;(window as TestWindow).elysiaDesktopTest
        .releaseNextCharacterPanelChange()
    })
    await waitForTwoAnimationFrames()
  }

  await page.getByRole('button', { name: 'Back to chat' }).click()
  await expect.soft(page.locator('#character-panel')).toHaveCount(0)
  await expect.soft(page.locator('.panel-toggle')).toHaveAttribute(
    'aria-expanded',
    'false',
  )
  const finalCalls = (await getCalls())
    .filter((call) => call.method === 'setCharacterPanelOpen')
    .map((call) => call.args[0])
  expect.soft(finalCalls).toEqual([true, false])

  await page.evaluate(() => {
    ;(window as TestWindow).elysiaDesktopTest
      .setCharacterPanelChangeDelay(false)
  })
})

test('persists system, light, and dark theme choices', async () => {
  await openSettings()

  await expect.poll(readThemeState).toEqual({
    preference: 'system',
    resolved: 'dark',
    stored: null,
    colorScheme: 'dark',
  })

  await page.getByText('Light', { exact: true }).click()
  await expect(page.getByRole('radio', { name: /^Light/ })).toBeChecked()
  await expect.poll(readThemeState).toEqual({
    preference: 'light',
    resolved: 'light',
    stored: 'light',
    colorScheme: 'light',
  })

  await page.getByText('Dark', { exact: true }).click()
  await expect(page.getByRole('radio', { name: /^Dark/ })).toBeChecked()
  await expect.poll(readThemeState).toEqual({
    preference: 'dark',
    resolved: 'dark',
    stored: 'dark',
    colorScheme: 'dark',
  })
  expect(
    (await getCalls())
      .filter((call) => call.method === 'setThemePreference')
      .map((call) => call.args[0]),
  ).toEqual(expect.arrayContaining(['system', 'light', 'dark']))

  await page.emulateMedia({ colorScheme: 'light' })
  await page.reload()
  await page.waitForFunction(() => (
    'elysiaDesktopTest' in window
    && (window as TestWindow).elysiaDesktopTest
      .getCalls()
      .some((call) => call.method === 'onBackendEvent.subscribe')
  ))
  await expect.poll(readThemeState).toEqual({
    preference: 'dark',
    resolved: 'dark',
    stored: 'dark',
    colorScheme: 'dark',
  })
  await expect.poll(async () => (
    (await getCalls())
      .filter((call) => call.method === 'setThemePreference')
      .map((call) => call.args[0])
  )).toContain('dark')

  await emitSnapshot(readySnapshot())
  await pressControlShortcut(',')
  await page.getByText('System', { exact: true }).click()
  await expect(page.getByRole('radio', { name: /^System/ })).toBeChecked()
  await expect.poll(readThemeState).toEqual({
    preference: 'system',
    resolved: 'light',
    stored: 'system',
    colorScheme: 'light',
  })

  await page.emulateMedia({ colorScheme: 'dark' })
  await expect.poll(readThemeState).toEqual({
    preference: 'system',
    resolved: 'dark',
    stored: 'system',
    colorScheme: 'dark',
  })

  const nativeThemeUpdates = (await getCalls())
    .filter((call) => call.method === 'setThemePreference')
    .map((call) => call.args[0])
  expect(nativeThemeUpdates).toEqual(expect.arrayContaining([
    'system',
    'dark',
  ]))
})

test('persists character performance and honors reduced motion', async () => {
  await page.emulateMedia({ reducedMotion: 'no-preference' })
  await openSettings()
  await expect.poll(readCharacterPerformanceState).toEqual({
    preference: 'animated',
    resolved: 'animated',
    stored: null,
  })

  await page.getByText('Still', { exact: true }).click()
  await expect(page.getByRole('radio', { name: /^Still/ })).toBeChecked()
  await expect.poll(readCharacterPerformanceState).toEqual({
    preference: 'still',
    resolved: 'still',
    stored: 'still',
  })

  await page.getByRole('button', { name: 'Back to chat' }).click()
  await page.getByRole('button', { name: 'Expand Elysia panel' }).click()
  const artwork = page.locator('.character-panel .character-artwork')
  await expect(artwork).toHaveAttribute('data-character-performance', 'still')
  await expect.poll(() => artwork.locator('.character-artwork-frame').evaluate(
    (element) => window.getComputedStyle(element).animationName,
  )).toBe('none')

  await page.locator('.character-panel').getByRole('button', {
    name: 'Close Elysia character panel',
  }).click()
  await pressControlShortcut(',')
  await page.getByText('Animated', { exact: true }).click()
  await expect.poll(readCharacterPerformanceState).toEqual({
    preference: 'animated',
    resolved: 'animated',
    stored: 'animated',
  })
  await page.getByRole('button', { name: 'Back to chat' }).click()
  await page.getByRole('button', { name: 'Expand Elysia panel' }).click()
  await expect.poll(() => artwork.locator('.character-artwork-frame').evaluate(
    (element) => window.getComputedStyle(element).animationName,
  )).toBe('character-resting')

  await page.emulateMedia({ reducedMotion: 'reduce' })
  await expect.poll(readCharacterPerformanceState).toEqual({
    preference: 'animated',
    resolved: 'still',
    stored: 'animated',
  })
  await expect(artwork).toHaveAttribute('data-character-performance', 'still')
  await expect.poll(() => artwork.locator('.character-artwork-frame').evaluate(
    (element) => window.getComputedStyle(element).animationName,
  )).toBe('none')

  await page.locator('.character-panel').getByRole('button', {
    name: 'Close Elysia character panel',
  }).click()
  const composer = page.getByLabel('Message Elysia')
  await composer.fill('Character motion is optional.')
  await composer.press('Enter')
  await expect.poll(async () => (
    (await getCalls()).filter((call) => call.method === 'sendMessage').length
  )).toBe(1)
})

test('shows all Settings areas, ownership scopes, and no secret controls', async () => {
  const scopedChat = chatSummary('chat-settings-scope', 'Scoped Chat', {
    projectId: 'project-settings-scope',
    modelName: 'qwen3.5:9b',
  })
  const scopedProject = projectSummary(
    'project-settings-scope',
    'Scoped Project',
    { chatCount: 1 },
  )
  await setProjectState({
    activeProject: scopedProject,
    projects: [scopedProject],
    chatState: {
      activeChat: { ...scopedChat, messages: [] },
      chats: [scopedChat],
    },
  })
  await setSettingsState(desktopSettingsState({ revision: 4 }))
  await openSettings(readySnapshot({
    chatId: scopedChat.chatId,
    chatTitle: scopedChat.title,
  }))

  await expect(page.locator('.settings-section h2')).toHaveText([
    'General',
    'Model',
    'Speech recognition',
    'Memory',
    'Voice behavior',
    'Audio devices',
    'Files',
    'Work',
    'Privacy',
    'Appearance',
  ])

  const scopes = page.locator('[aria-label="Settings scopes"]')
  await expect(scopes.getByText('Global', { exact: true })).toBeVisible()
  await expect(scopes.getByText('Project', { exact: true })).toBeVisible()
  await expect(scopes.getByText('Chat', { exact: true })).toBeVisible()
  await expect(scopes).toContainText('Scoped Project')
  await expect(scopes).toContainText('Inherits qwen3.5:9b')
  await expect(scopes).toContainText('Scoped Chat')
  await expect(scopes).toContainText('Pinned to qwen3.5:9b')

  const secretControlMetadata = await page.locator(
    '.settings-view input, .settings-view select, .settings-view textarea',
  ).evaluateAll((controls) => controls.map((control) => [
    control.getAttribute('type'),
    control.getAttribute('name'),
    control.getAttribute('id'),
    control.getAttribute('placeholder'),
    control.getAttribute('autocomplete'),
    control.getAttribute('aria-label'),
  ].filter(Boolean).join(' ')).join('\n'))
  expect(secretControlMetadata).not.toMatch(
    /api[-_ ]?key|access[-_ ]?token|auth(?:entication)?[-_ ]?token|password|secret/iu,
  )
  await expect(page.locator('.settings-view input[type="password"]'))
    .toHaveCount(0)
})

test('manages the optional Desktop Pet with revisioned immediate controls', async () => {
  await openSettings()

  const controls = page.getByRole('group', {
    name: 'Desktop Pet visibility',
  })
  const off = controls.getByRole('radio', { name: /^Off/ })
  const hidden = controls.getByRole('radio', { name: /^Hidden/ })
  const visible = controls.getByRole('radio', { name: /^Visible/ })
  const resetPosition = page.getByRole('button', { name: 'Reset position' })

  await expect(off).toBeChecked()
  await expect(page.getByText(
    'Preference: disabled. Native window: absent.',
  )).toBeVisible()
  await clearCalls()

  await controls.getByText('Visible', { exact: true }).click()
  await expect(visible).toBeChecked()
  await expect(page.getByText(
    'Preference: visible. Native window: visible.',
  )).toBeVisible()

  await resetPosition.click()
  await expect(resetPosition).toBeEnabled()

  await controls.getByText('Hidden', { exact: true }).click()
  await expect(hidden).toBeChecked()
  await expect(page.getByText(
    'Preference: hidden. Native window: absent.',
  )).toBeVisible()

  await controls.getByText('Off', { exact: true }).click()
  await expect(off).toBeChecked()
  await expect(page.getByText(
    'Preference: disabled. Native window: absent.',
  )).toBeVisible()

  const calls = await getCalls()
  expect(calls.filter((call) => call.method === 'updateDesktopPet').map(
    (call) => call.args,
  )).toEqual([
    [{ expectedRevision: 0, mode: 'visible' }],
    [{ expectedRevision: 1, mode: 'hidden' }],
    [{ expectedRevision: 2, mode: 'disabled' }],
  ])
  expect(calls.filter(
    (call) => call.method === 'resetDesktopPetPosition',
  )).toHaveLength(1)
})

test('recovers canonical Desktop Pet state after an update fails', async () => {
  await openSettings()
  const visibleState = desktopPetState({
    revision: 7,
    updatedAt: '2026-10-01T12:07:00.000Z',
    mode: 'visible',
    runtime: 'visible',
  })
  await emitDesktopPetState(visibleState)

  const controls = page.getByRole('group', {
    name: 'Desktop Pet visibility',
  })
  await expect(controls.getByRole('radio', { name: /^Visible/ })).toBeChecked()

  await setDesktopPetState(desktopPetState({
    revision: 8,
    updatedAt: '2026-10-01T12:08:00.000Z',
    mode: 'hidden',
    runtime: 'absent',
  }))
  const failure = 'Desktop Pet storage is temporarily unavailable.'
  await failNextDesktopPetUpdate(failure)
  await clearCalls()

  await controls.getByText('Off', { exact: true }).click()
  await expect(page.getByRole('alert')).toHaveText(failure)
  await expect(controls.getByRole('radio', { name: /^Hidden/ })).toBeChecked()
  await expect(page.getByText(
    'Preference: hidden. Native window: absent.',
  )).toBeVisible()

  const calls = await getCalls()
  expect(calls.filter((call) => (
    call.method === 'updateDesktopPet'
    || call.method === 'getDesktopPetState'
  )).map((call) => ({ method: call.method, args: call.args }))).toEqual([
    {
      method: 'updateDesktopPet',
      args: [{ expectedRevision: 7, mode: 'disabled' }],
    },
    { method: 'getDesktopPetState', args: [] },
  ])
})

test('retries a failed visible Desktop Pet from Settings', async () => {
  await openSettings()
  await emitDesktopPetState(desktopPetState({
    revision: 9,
    updatedAt: '2026-10-01T12:09:00.000Z',
    mode: 'visible',
    runtime: 'failed',
    warning: 'The Desktop Pet could not be displayed.',
  }))
  await clearCalls()

  const retry = page.getByRole('button', { name: 'Retry Desktop Pet' })
  await expect(retry).toBeVisible()
  await retry.click()

  await expect(page.getByText(
    'Preference: visible. Native window: visible.',
  )).toBeVisible()
  await expect(retry).toHaveCount(0)
  const calls = await getCalls()
  expect(calls.filter((call) => call.method === 'updateDesktopPet').map(
    (call) => call.args,
  )).toEqual([[{ expectedRevision: 9, mode: 'visible' }]])
})

test('returns to Chat when the Desktop Pet requests the main surface', async () => {
  await openSettings()
  await expect.poll(async () => (
    (await getCalls()).some(
      (call) => call.method === 'onDesktopPetOpenChatRequested.subscribe',
    )
  )).toBe(true)

  await emitDesktopPetOpenChatRequested()

  await expect(page.getByRole('heading', { name: 'Talk with Elysia' }))
    .toBeVisible()
  await expect(page.getByRole('heading', { name: 'Settings', exact: true }))
    .toHaveCount(0)
  await expect(page.getByLabel('Message Elysia')).toBeFocused()
})

test('saves exact audio devices and restores them in a replacement window', async () => {
  await installAudioMock()
  await openSettings()

  const microphone = page.getByRole('combobox', { name: /^Microphone/ })
  const speaker = page.getByRole('combobox', { name: /^Speaker/ })
  await expect(microphone).toBeEnabled()
  await expect(speaker).toBeEnabled()
  await expect(microphone.locator('option')).toHaveText([
    'System default',
    'Built-in microphone',
    'USB microphone',
  ])
  await expect(speaker.locator('option')).toHaveText([
    'System default',
    'Built-in speakers',
    'USB speakers',
  ])

  await microphone.selectOption('mic-usb')
  await speaker.selectOption('speaker-usb')
  await clearCalls()
  await page.getByRole('button', { name: 'Save devices' }).click()
  await expect(page.getByText('Revision 1.')).toBeVisible()

  expect((await getCalls()).find(
    (call) => call.method === 'updateVoiceSettings',
  )?.args).toEqual([{
    expectedRevision: 0,
    inputDeviceId: 'mic-usb',
    outputDeviceId: 'speaker-usb',
  }])

  await persistBackendForReload()
  await replaceRendererWindow()
  await pressControlShortcut(',')
  await expect(page.getByRole('heading', { name: 'Audio devices', exact: true }))
    .toBeVisible()
  await expect(page.getByRole('combobox', { name: /^Microphone/ }))
    .toHaveValue('mic-usb')
  await expect(page.getByRole('combobox', { name: /^Speaker/ }))
    .toHaveValue('speaker-usb')
  expect((await audioMockStats()).getUserMediaCalls).toHaveLength(0)
})

test('keeps an unsaved device draft across a same-revision Backend reload', async () => {
  await installAudioMock()
  await openSettings()
  const microphone = page.getByRole('combobox', { name: /^Microphone/ })
  await microphone.selectOption('mic-usb')
  await expect(page.getByRole('button', { name: 'Save devices' })).toBeEnabled()
  const previousReads = (await getCalls()).filter(
    (call) => call.method === 'getVoiceSettings',
  ).length

  await emitSnapshot({
    ...readySnapshot(),
    revision: 2,
    status: 'error',
    error: 'The test Backend needs repair.',
  })

  await expect.poll(async () => (
    (await getCalls()).filter(
      (call) => call.method === 'getVoiceSettings',
    ).length
  )).toBeGreaterThan(previousReads)
  await expect(microphone).toHaveValue('mic-usb')
  await expect(page.getByRole('button', { name: 'Save devices' })).toBeEnabled()
})

test('keeps an unsaved device draft while global Settings finish loading', async () => {
  await installAudioMock()
  await setSettingsLoadDelay(true)
  await emitSnapshot(readySnapshot())
  await pressControlShortcut(',')

  await expect(page.getByRole('heading', { name: 'Settings', exact: true }))
    .toBeVisible()
  await expect.poll(() => page.evaluate(() => (
    (window as TestWindow).elysiaDesktopTest.getPendingSettingsLoadCount()
  ))).toBe(1)
  const microphone = page.getByRole('combobox', { name: /^Microphone/ })
  await expect(microphone).toBeEnabled()
  await microphone.selectOption('mic-usb')

  expect(await releaseNextSettingsLoad()).toBe(true)
  await setSettingsLoadDelay(false)
  await expect(page.getByRole('heading', { name: 'General', exact: true }))
    .toBeVisible()
  await expect(microphone).toHaveValue('mic-usb')
  await expect(page.getByRole('button', { name: 'Save devices' })).toBeEnabled()
})

test('reports denied microphone permission without touching real media', async () => {
  await setMicrophonePermissionStatus('denied')
  await installAudioMock()
  await openSettings()

  await expect(page.getByText('Microphone access denied', { exact: true }))
    .toBeVisible()
  await clearCalls()
  await page.getByRole('button', { name: 'Open Windows settings' }).click()
  expect((await getCalls()).some(
    (call) => call.method === 'openMicrophonePrivacySettings',
  )).toBe(true)

  await failNextAudioCapture('NotAllowedError')
  await page.getByRole('button', { name: 'Test microphone' }).click()
  await expect(page.getByRole('alert')).toContainText(
    'Microphone access was denied by Windows or Elysia.',
  )
  const stats = await audioMockStats()
  expect(stats.getUserMediaCalls).toEqual([{ audio: true, video: false }])
  expect(stats.trackStopCount).toBe(0)
})

test('reports a microphone that Windows cannot open because it is busy', async () => {
  await installAudioMock()
  await openSettings()
  await failNextAudioCapture('NotReadableError')

  await page.getByRole('button', { name: 'Test microphone' }).click()

  await expect(page.getByRole('alert')).toContainText(
    'The microphone is busy or could not be opened by Windows.',
  )
  expect((await audioMockStats()).getUserMediaCalls).toEqual([
    { audio: true, video: false },
  ])
})

test('cancels microphone resources while AudioContext resume is pending', async () => {
  await installAudioMock()
  await openSettings()
  await deferNextAudioResume()

  await page.getByRole('button', { name: 'Test microphone' }).click()
  await expect(page.getByRole('button', { name: 'Cancel microphone test' }))
    .toBeVisible()
  await expect.poll(async () => (await audioMockStats()).contextsCreated).toBe(1)
  await page.getByRole('button', { name: 'Cancel microphone test' }).click()

  await expect.poll(async () => (await audioMockStats()).trackStopCount).toBe(1)
  await expect.poll(async () => (await audioMockStats()).contextsClosed).toBe(1)
  expect(await releaseAudioResume()).toBe(true)
  await waitForTwoAnimationFrames()
  await expect(page.getByRole('button', { name: 'Test microphone' })).toBeVisible()
  expect((await audioMockStats()).trackStopCount).toBe(1)
  expect((await audioMockStats()).contextsClosed).toBe(1)
})

test('cancels speaker resources while output routing is pending', async () => {
  await installAudioMock()
  await openSettings()
  await page.getByRole('combobox', { name: /^Speaker/ })
    .selectOption('speaker-usb')
  await deferNextSinkSelection()

  await page.getByRole('button', { name: 'Test speaker' }).click()
  await expect(page.getByRole('button', { name: 'Cancel speaker test' }))
    .toBeVisible()
  await expect.poll(async () => (await audioMockStats()).sinkIds)
    .toEqual(['speaker-usb'])
  await page.getByRole('button', { name: 'Cancel speaker test' }).click()

  await expect.poll(async () => (await audioMockStats()).contextsClosed).toBe(1)
  expect((await audioMockStats()).oscillatorDurations).toHaveLength(0)
  expect(await releaseSinkSelection()).toBe(true)
  await waitForTwoAnimationFrames()
  await expect(page.getByRole('button', { name: 'Test speaker' })).toBeVisible()
  expect((await audioMockStats()).oscillatorDurations).toHaveLength(0)
  expect((await audioMockStats()).contextsClosed).toBe(1)
})

test('keeps missing selections while tests safely follow system defaults', async () => {
  await setVoiceSettingsState(voiceSettingsState({
    revision: 3,
    updatedAt: '2026-09-10T12:00:00+00:00',
    inputDeviceId: 'mic-usb',
    outputDeviceId: 'speaker-usb',
  }))
  await installAudioMock()
  await openSettings()
  const beforeChange = (await audioMockStats()).enumerateCalls

  await dispatchAudioDeviceChange([
    {
      deviceId: 'default',
      kind: 'audioinput',
      label: 'Default microphone',
    },
    {
      deviceId: 'mic-built-in',
      kind: 'audioinput',
      label: 'Built-in microphone',
    },
    {
      deviceId: 'default',
      kind: 'audiooutput',
      label: 'Default speakers',
    },
    {
      deviceId: 'speaker-built-in',
      kind: 'audiooutput',
      label: 'Built-in speakers',
    },
  ])

  await expect(page.getByText(
    'The saved microphone is disconnected. System default is used safely until it returns or you save another choice.',
  )).toBeVisible()
  await expect(page.getByText(
    'The saved speaker is disconnected. System default is used safely until it returns or you save another choice.',
  )).toBeVisible()
  await expect(page.getByRole('combobox', { name: /^Microphone/ }))
    .toHaveValue('mic-usb')
  await expect(page.getByRole('combobox', { name: /^Speaker/ }))
    .toHaveValue('speaker-usb')
  await expect.poll(async () => (await audioMockStats()).enumerateCalls)
    .toBeGreaterThan(beforeChange)

  await page.getByRole('button', { name: 'Test microphone' }).click()
  await expect(page.getByRole('button', { name: 'Stop microphone test' }))
    .toBeVisible()
  expect((await audioMockStats()).getUserMediaCalls.at(-1)).toEqual({
    audio: true,
    video: false,
  })
  await page.getByRole('button', { name: 'Stop microphone test' }).click()
  await expect.poll(async () => (await audioMockStats()).trackStopCount).toBe(1)

  await page.getByRole('button', { name: 'Test speaker' }).click()
  await expect.poll(async () => (await audioMockStats()).oscillatorDurations)
    .toEqual([0.8])
  await expect.poll(async () => (await audioMockStats()).sinkIds.at(-1)).toBe('')
  await expect.poll(async () => (await audioMockStats()).contextsClosed)
    .toBe(2)
})

test('reports and releases a microphone that disconnects during its test', async () => {
  await installAudioMock()
  await openSettings()
  await page.getByRole('combobox', { name: /^Microphone/ })
    .selectOption('mic-usb')
  await page.getByRole('button', { name: 'Test microphone' }).click()
  await expect(page.getByRole('button', { name: 'Stop microphone test' }))
    .toBeVisible()

  await dispatchAudioDeviceChange(defaultAudioMockDevices.filter(
    (device) => device.deviceId !== 'mic-usb',
  ))

  await expect(page.getByRole('alert')).toContainText(
    'The microphone disconnected during the test.',
  )
  await expect.poll(async () => (await audioMockStats()).trackStopCount).toBe(1)
  await expect.poll(async () => (await audioMockStats()).contextsClosed).toBe(1)
})

test('stops microphone tracks and audio contexts when Settings closes', async () => {
  await installAudioMock()
  await openSettings()
  await page.getByRole('button', { name: 'Test microphone' }).click()
  await expect(page.getByRole('button', { name: 'Stop microphone test' }))
    .toBeVisible()

  await page.getByRole('button', { name: 'Back to chat' }).click()
  await expect(page.getByRole('heading', { name: 'Talk with Elysia' }))
    .toBeVisible()
  await expect.poll(async () => (await audioMockStats()).trackStopCount).toBe(1)
  await expect.poll(async () => (await audioMockStats()).contextsClosed).toBe(1)
})

test('stops the Chat microphone test when navigation changes context', async () => {
  await installAudioMock()
  await page.getByRole('button', { name: 'Test microphone input' }).click()
  await expect.poll(async () => (await audioMockStats()).getUserMediaCalls)
    .toHaveLength(1)
  await expect(page.getByRole('button', { name: 'Stop microphone test' }))
    .toHaveAttribute('aria-pressed', 'true')

  await page.getByRole('button', { name: /^Projects/ }).click()
  await expect(page.getByRole('heading', { name: 'Projects', exact: true }))
    .toBeVisible()
  await expect.poll(async () => (await audioMockStats()).trackStopCount).toBe(1)
  await expect.poll(async () => (await audioMockStats()).contextsClosed).toBe(1)
})

test('does not open the Chat microphone after navigation interrupts discovery', async () => {
  await installAudioMock()
  await deferNextAudioEnumeration()

  await page.getByRole('button', { name: 'Test microphone input' }).click()
  await expect.poll(async () => (await audioMockStats()).enumerateCalls).toBe(1)
  await page.getByRole('button', { name: /^Projects/ }).click()
  await expect(page.getByRole('heading', { name: 'Projects', exact: true }))
    .toBeVisible()

  expect(await releaseAudioEnumeration()).toBe(true)
  await waitForTwoAnimationFrames()
  expect((await audioMockStats()).getUserMediaCalls).toHaveLength(0)
  expect((await audioMockStats()).contextsCreated).toBe(0)
  expect((await audioMockStats()).trackStopCount).toBe(0)
})

test('routes one bounded low-volume speaker test without microphone access', async () => {
  await installAudioMock()
  await openSettings()
  await page.getByRole('combobox', { name: /^Speaker/ }).selectOption('speaker-usb')
  await page.getByRole('button', { name: 'Test speaker' }).click()

  await expect.poll(async () => (await audioMockStats()).sinkIds)
    .toEqual(['speaker-usb'])
  await expect.poll(async () => (await audioMockStats()).oscillatorDurations)
    .toEqual([0.8])
  await expect.poll(async () => (await audioMockStats()).contextsClosed).toBe(1)
  expect((await audioMockStats()).getUserMediaCalls).toHaveLength(0)
})

test('blocks Voice capture with safe local transcription recovery guidance', async () => {
  await installAudioMock()
  await emitSnapshot(readySnapshot({
    capabilities: [
      'chat.stream',
      'voice.settings',
      'voice.capture',
      'voice.transcription',
    ],
  }))
  const cases = [
    {
      reason: 'model_missing' as const,
      message: 'Install the selected small model in models/weights/faster-whisper/small, then restart the Backend.',
    },
    {
      reason: 'dependencies_missing' as const,
      message: 'Install the optional local speech-recognition dependencies, then restart the Backend.',
    },
    {
      reason: 'device_unavailable' as const,
      message: 'No supported local transcription device is available. Reinstall the optional runtime, then restart the Backend.',
    },
  ]

  for (const [index, unavailable] of cases.entries()) {
    await test.step(`blocks capture for ${unavailable.reason}`, async () => {
      await setVoiceSettingsState(voiceSettingsState({
        transcriptionStatus: {
          state: 'unavailable',
          model: 'small',
          requestedDevice: 'auto',
          resolvedDevice: null,
          computeType: null,
          reason: unavailable.reason,
        },
      }))
      await page.getByRole('button', { name: 'Start voice' }).click()
      await expect(page.getByRole('main', { name: 'Voice capture' }))
        .toBeVisible()
      const microphone = page.getByRole('button', { name: 'Start microphone' })
      await expect(microphone).toBeDisabled()
      await expect(microphone).toHaveAttribute('title', unavailable.message)
      await expect(page.getByText(unavailable.message, { exact: true }))
        .toBeVisible()
      expect((await audioMockStats()).getUserMediaCalls).toHaveLength(0)
      if (index < cases.length - 1) {
        await page.getByRole('button', { name: 'Close voice' }).click()
      }
    })
  }
})

test('keeps the microphone off until Voice capture is explicitly started', async () => {
  await installAudioMock()
  await openVoiceCapturePage()

  expect((await audioMockStats()).getUserMediaCalls).toHaveLength(0)
  await clearCalls()
  await page.getByRole('button', { name: 'Start microphone' }).click()
  await expect(page.getByText('Listening for speech', { exact: true }))
    .toBeVisible()

  expect((await audioMockStats()).getUserMediaCalls).toEqual([{
    audio: { channelCount: { ideal: 1 } },
    video: false,
  }])
  await page.getByRole('button', { name: 'Cancel capture' }).click()
  await expect(page.getByText('Capture cancelled', { exact: true }))
    .toBeVisible()
  await expect.poll(async () => (await audioMockStats()).trackStopCount).toBe(1)
  await expect.poll(async () => (await audioMockStats()).contextsClosed).toBe(1)
  const calls = await getCalls()
  expect(calls.some((call) => call.method === 'submitVoiceCapture')).toBe(false)
  expect(calls.some((call) => call.method === 'beginVoiceTranscription'))
    .toBe(false)
  expect(calls.some((call) => call.method === 'sendMessage')).toBe(false)
})

test('submits one bounded utterance to local transcription without a Chat Turn', async () => {
  await installAudioMock()
  const globalSettings = desktopSettingsState()
  globalSettings.activeSettings = {
    ...globalSettings.activeSettings,
    transcriptionLanguage: 'zh',
  }
  await setSettingsState(globalSettings)
  await setVoiceSettingsState(voiceSettingsState({
    inputDeviceId: 'mic-usb',
  }))
  await openVoiceCapturePage()
  await setVoiceTranscriptionDelay(true)
  await clearCalls()

  await page.getByRole('button', { name: 'Start microphone' }).click()
  await expect(page.getByText('Listening for speech', { exact: true }))
    .toBeVisible()
  await emitSpeechFrames(0.08, 10)
  await expect(page.getByText('Speech detected', { exact: true }))
    .toBeVisible()
  await emitAudioFrames(0, 30)
  await expect(page.getByText('Transcribing locally', { exact: true }))
    .toBeVisible()
  await expect.poll(() => page.evaluate(() => (
    (window as TestWindow).elysiaDesktopTest
      .getPendingVoiceTranscriptionCount()
  ))).toBe(1)

  const transcriptionCalls = (await getCalls()).filter(
    (call) => call.method === 'beginVoiceTranscription',
  )
  expect(transcriptionCalls).toHaveLength(1)
  const request = transcriptionCalls[0]?.args[0] as {
    sessionId: string
    chatId: string
    sampleRateHz: number
    channelCount: number
    sampleFormat: string
    sampleCount: number
    speechStartSample: number
    speechEndSample: number
    pcmBase64: string
    language: string
  }
  expect(request.sessionId).toMatch(/^voice_[A-Za-z0-9_-]+$/u)
  expect(request.chatId).toBe('chat-test')
  expect(request.sampleRateHz).toBe(16_000)
  expect(request.channelCount).toBe(1)
  expect(request.sampleFormat).toBe('s16le')
  expect(request.sampleCount).toBeGreaterThanOrEqual(3_200)
  expect(request.sampleCount).toBeLessThanOrEqual(480_000)
  expect(request.sampleCount % 320).toBe(0)
  expect(request.speechStartSample % 320).toBe(0)
  expect(request.speechEndSample - request.speechStartSample)
    .toBeGreaterThanOrEqual(3_200)
  expect(Buffer.from(request.pcmBase64, 'base64').byteLength)
    .toBe(request.sampleCount * 2)
  expect(request.language).toBe('zh')
  expect((await getCalls()).some(
    (call) => call.method === 'submitVoiceCapture',
  )).toBe(false)
  expect((await getCalls()).some((call) => call.method === 'sendMessage'))
    .toBe(false)
  expect((await audioMockStats()).getUserMediaCalls.at(-1)).toEqual({
    audio: {
      deviceId: { exact: 'mic-usb' },
      channelCount: { ideal: 1 },
    },
    video: false,
  })
  await expect.poll(async () => (await audioMockStats()).trackStopCount).toBe(1)
  await expect.poll(async () => (await audioMockStats()).contextsClosed).toBe(1)
})

test('accepts a terminal transcription before its begin acknowledgement', async () => {
  await installAudioMock()
  await openVoiceCapturePage()
  await setNextVoiceTranscriptionResult({
    text: 'Terminal arrived before acknowledgement',
    language: 'en',
    languageProbability: 0.93,
  })
  await setNextVoiceTranscriptionTerminalBeforeAcknowledgement()
  await clearCalls()

  await page.getByRole('button', { name: 'Start microphone' }).click()
  await emitSpeechFrames(0.08, 10)
  await emitAudioFrames(0, 30)

  await expect(page.getByText('Transcript ready', { exact: true }))
    .toBeVisible()
  await expect(page.getByRole('textbox', { name: 'Final transcript' }))
    .toHaveValue('Terminal arrived before acknowledgement')
  expect((await getCalls()).filter(
    (call) => call.method === 'beginVoiceTranscription',
  )).toHaveLength(1)
  expect((await getCalls()).some(
    (call) => call.method === 'stopVoiceTranscription',
  )).toBe(false)
  await expect.poll(() => page.evaluate(() => (
    (window as TestWindow).elysiaDesktopTest
      .getPendingVoiceTranscriptionCount()
  ))).toBe(0)
})

test('reviews and edits a transcript before an explicit Chat send', async () => {
  await installAudioMock()
  await openVoiceCapturePage()
  await setVoiceTranscriptionDelay(true)
  await setNextVoiceTranscriptionResult({
    text: 'Unedited local transcript',
    language: 'en',
    languageProbability: 0.96,
  })
  await clearCalls()

  await page.getByRole('button', { name: 'Start microphone' }).click()
  await emitSpeechFrames(0.08, 10)
  await emitAudioFrames(0, 30)
  await expect(page.getByText('Transcribing locally', { exact: true }))
    .toBeVisible()
  expect(await releaseNextVoiceTranscription()).toBe(true)

  await expect(page.getByText('Transcript ready', { exact: true }))
    .toBeVisible()
  const transcript = page.getByRole('textbox', { name: 'Final transcript' })
  await expect(transcript).toHaveValue('Unedited local transcript')
  expect((await getCalls()).some((call) => call.method === 'sendMessage'))
    .toBe(false)

  await transcript.fill('Edited local transcript')
  await page.getByRole('button', { name: 'Use transcript in message' }).click()
  const composer = page.getByLabel('Message Elysia')
  await expect(composer).toHaveValue('Edited local transcript')
  expect((await getCalls()).some((call) => call.method === 'sendMessage'))
    .toBe(false)

  await page.getByRole('button', { name: 'Send message' }).click()
  await expect.poll(async () => (
    (await getCalls()).filter((call) => call.method === 'sendMessage').length
  )).toBe(1)
  const send = (await getCalls()).find(
    (call) => call.method === 'sendMessage',
  )
  expect(send?.args[0]).toMatchObject({
    chatId: 'chat-test',
    message: 'Edited local transcript',
  })
})

test('sends a reviewed transcript through Chat and follows trusted speech status', async () => {
  await installAudioMock()
  await page.evaluate(() => {
    ;(window as TestWindow).elysiaDesktopTest.setAttachmentState({
      scope: { kind: 'chat', id: 'chat-test' },
      attachments: [{
        attachmentId: 'attachment_voice_isolation',
        fileName: 'keep-for-text-message.md',
        mediaType: 'text/markdown',
        sizeBytes: 512,
        status: 'ready',
      }],
      maxFileBytes: 16_777_216,
      maxFileCount: 10,
    })
  })
  await emitSnapshot(readySnapshot({
    capabilities: [
      'chat.stream',
      'voice.settings',
      'voice.capture',
      'voice.transcription',
      'voice.speech',
      'voice.speech.cancel',
    ],
  }))
  const composer = page.getByLabel('Message Elysia')
  await composer.fill('Keep this typed draft')
  await expect(page.getByText('keep-for-text-message.md', { exact: true }))
    .toBeVisible()
  await setVoiceTranscriptionDelay(true)
  await setNextVoiceTranscriptionResult({
    text: 'Send this reviewed voice turn',
    language: 'en',
    languageProbability: 0.99,
  })
  await clearCalls()

  await page.getByRole('button', { name: 'Start voice' }).click()
  const call = page.getByRole('main', { name: 'Voice capture' })
  const characterArtwork = call.locator('.character-artwork')
  await page.getByRole('button', { name: 'Start microphone' }).click()
  await emitSpeechFrames(0.08, 10)
  await emitAudioFrames(0, 30)
  expect(await releaseNextVoiceTranscription()).toBe(true)
  const transcript = page.getByRole('textbox', { name: 'Final transcript' })
  await expect(transcript).toHaveValue('Send this reviewed voice turn')
  expect((await getCalls()).some((call) => call.method === 'sendMessage'))
    .toBe(false)

  await page.evaluate(() => {
    ;(window as TestWindow).elysiaDesktopTest.setChatActionDelay(true)
  })
  await page.getByRole('button', { name: 'Send transcript' }).click()
  await expect.poll(async () => (
    (await getCalls()).filter((call) => call.method === 'sendMessage').length
  )).toBe(1)
  const send = (await getCalls()).find(
    (call) => call.method === 'sendMessage',
  )
  expect(send?.args).toEqual([{
    chatId: 'chat-test',
    message: 'Send this reviewed voice turn',
    attachmentIds: [],
    useProjectKnowledge: false,
  }])
  await expect(page.locator('.call-microphone-state'))
    .toHaveText('Monitoring interruptions')
  await expect(page.locator('.call-state')).toHaveText('Elysia is thinking')
  await expect(call).toHaveAttribute('data-character-state', 'thinking')
  await expect(characterArtwork).toHaveAttribute(
    'data-character-state',
    'thinking',
  )
  await expect(transcript).not.toBeEditable()

  await emitEvent({
    type: 'voice-speech-status',
    kind: 'playing',
    requestId: 'wrong-request',
    chatId: 'chat-test',
    sequence: 0,
  })
  await expect(page.locator('.call-microphone-state'))
    .toHaveText('Monitoring interruptions')
  await expect(page.locator('.call-state')).toHaveText('Elysia is thinking')
  await emitEvent({
    type: 'voice-speech-status',
    kind: 'playing',
    requestId: 'test-request-1',
    chatId: 'chat-test',
    sequence: 0,
  })
  await expect(page.locator('.call-microphone-state'))
    .toHaveText('Monitoring interruptions')
  await expect(page.locator('.call-state')).toHaveText('Elysia is thinking')
  await emitEvent({
    type: 'chat-chunk',
    requestId: 'test-request-1',
    chatId: 'chat-test',
    chunk: 'The local reply started.',
  })
  await expect(page.locator('.call-state')).toHaveText('Elysia is thinking')
  await expect(page.getByText('The local reply started.', { exact: true }))
    .toBeVisible()
  await expect.poll(() => page.evaluate(() => (
    (window as TestWindow).elysiaDesktopTest.getPendingChatActionCount()
  ))).toBe(1)
  await page.evaluate(() => {
    const control = (window as TestWindow).elysiaDesktopTest
    control.releaseNextChatAction()
    control.setChatActionDelay(false)
  })
  await expect(page.locator('.call-state')).toHaveText('Elysia is speaking')
  await expect(call).toHaveAttribute('data-character-state', 'speaking')
  await expect(characterArtwork).toHaveAttribute(
    'data-character-state',
    'speaking',
  )
  await emitEvent({
    type: 'voice-speech-status',
    kind: 'played',
    requestId: 'test-request-1',
    chatId: 'chat-test',
    sequence: 0,
  })
  await expect(page.locator('.call-microphone-state'))
    .toHaveText('Monitoring interruptions')
  await expect(page.locator('.call-state')).toHaveText('Elysia is thinking')
  await emitEvent({
    type: 'voice-speech-status',
    kind: 'terminal',
    requestId: 'test-request-1',
    chatId: 'chat-test',
    state: 'completed',
  })
  await emitEvent({
    type: 'chat-complete',
    requestId: 'test-request-1',
    chatId: 'chat-test',
    reply: 'Trusted local speech finished.',
  })
  await expect(page.getByText('Ready to listen', { exact: true })).toBeVisible()
  await expect(call).toHaveAttribute('data-character-state', 'idle')
  await expect(characterArtwork).toHaveAttribute('data-character-state', 'idle')
  await expect(page.getByLabel('Final transcript')).toHaveCount(0)

  await page.getByRole('button', { name: 'Close voice' }).click()
  await expect(composer).toHaveValue('Keep this typed draft')
  await expect(page.getByText('keep-for-text-message.md', { exact: true }))
    .toBeVisible()
  await expect(page.getByText('Trusted local speech finished.', { exact: true }))
    .toBeVisible()
})

test('starts one safe automatic capture only after a clean Voice reply', async () => {
  const settings = desktopSettingsState()
  settings.settings = {
    ...settings.settings,
    automaticRelisten: true,
  }
  settings.activeSettings = {
    ...settings.activeSettings,
    automaticRelisten: true,
  }
  await setSettingsState(settings)
  await installAudioMock()
  await emitSnapshot(readySnapshot({
    capabilities: [
      'chat.stream',
      'voice.settings',
      'voice.capture',
      'voice.transcription',
      'voice.speech',
      'voice.speech.cancel',
    ],
  }))
  await setVoiceTranscriptionDelay(true)
  await setNextVoiceTranscriptionResult({
    text: 'Continue after this reviewed turn',
    language: 'en',
    languageProbability: 0.98,
  })

  await page.getByRole('button', { name: 'Start voice' }).click()
  await expect(page.getByRole('button', { name: 'Auto-continue' }))
    .toHaveAttribute('aria-pressed', 'true')
  expect((await audioMockStats()).getUserMediaCalls).toHaveLength(0)

  await page.getByRole('button', { name: 'Start microphone' }).click()
  await emitSpeechFrames(0.08, 10)
  await emitAudioFrames(0, 30)
  expect(await releaseNextVoiceTranscription()).toBe(true)
  await page.getByRole('button', { name: 'Send transcript' }).click()
  await expect.poll(async () => (
    (await getCalls()).filter((call) => call.method === 'sendMessage').length
  )).toBe(1)
  await emitEvent({
    type: 'chat-chunk',
    requestId: 'test-request-1',
    chatId: 'chat-test',
    chunk: 'A caption arrives before the terminal.',
  })
  await expect(page.getByText(
    'A caption arrives before the terminal.',
    { exact: true },
  )).toBeVisible()
  await emitEvent({
    type: 'chat-complete',
    requestId: 'test-request-1',
    chatId: 'chat-test',
    reply: 'The clean reply is complete.',
  })
  await emitEvent({
    type: 'voice-speech-status',
    kind: 'terminal',
    requestId: 'test-request-1',
    chatId: 'chat-test',
    state: 'completed',
  })

  await expect(page.locator('.call-state')).toHaveText('Listening for speech')
  await expect.poll(async () => (await audioMockStats()).getUserMediaCalls.length)
    .toBe(3)
  expect((await getCalls()).filter(
    (call) => call.method === 'sendMessage',
  )).toHaveLength(1)
})

test('mute stops monitoring and unmute never opens the microphone', async () => {
  await installAudioMock()
  await openVoiceWithFinalTranscript('Keep thinking while muted')
  await clearCalls()

  await page.getByRole('button', { name: 'Send transcript' }).click()
  await expect(page.locator('.call-microphone-state'))
    .toHaveText('Monitoring interruptions')
  await expect.poll(async () => (await audioMockStats()).getUserMediaCalls.length)
    .toBe(2)

  await page.getByRole('button', { name: 'Mute' }).click()
  await expect(page.locator('.call-state')).toHaveText('Elysia is thinking')
  await expect(page.locator('.call-microphone-state'))
    .toHaveText('Microphone muted')
  await expect.poll(async () => (await audioMockStats()).trackStopCount).toBe(2)
  await page.getByRole('button', { name: 'Unmute' }).click()
  await waitForTwoAnimationFrames()
  expect((await audioMockStats()).getUserMediaCalls).toHaveLength(2)
  await expect(page.locator('.call-microphone-state'))
    .toHaveText('Microphone off')
})

test('mute discards interruption PCM already waiting for the old terminal', async () => {
  await installAudioMock()
  await openVoiceWithFinalTranscript('Interrupt this reply, then mute')
  await clearCalls()

  await page.getByRole('button', { name: 'Send transcript' }).click()
  await expect(page.locator('.call-microphone-state'))
    .toHaveText('Monitoring interruptions')
  await emitSpeechFrames(0.08, 10)
  await expect(page.locator('.call-state')).toHaveText('Interrupting Elysia')
  await emitAudioFrames(0, 30)
  await expect(page.getByText(
    'Your next message is ready. Waiting for the previous reply to stop before local transcription.',
    { exact: true },
  )).toBeVisible()
  expect((await getCalls()).some(
    (call) => call.method === 'beginVoiceTranscription',
  )).toBe(false)

  await page.getByRole('button', { name: 'Mute' }).click()
  await expect(page.locator('.call-state')).toHaveText('Ready to listen')
  await expect(page.locator('.call-microphone-state'))
    .toHaveText('Microphone muted')
  await emitEvent({
    type: 'chat-error',
    requestId: 'test-request-1',
    chatId: 'chat-test',
    code: 'request.cancelled',
    message: 'Generation cancelled.',
    retryable: false,
  })
  await waitForTwoAnimationFrames()

  expect((await getCalls()).some(
    (call) => call.method === 'beginVoiceTranscription',
  )).toBe(false)
  await expect(page.getByRole('textbox', { name: 'Final transcript' }))
    .toHaveCount(0)
})

test('protects an unsent transcript and opens Audio settings safely', async () => {
  await installAudioMock()
  await openVoiceWithFinalTranscript('Do not discard without confirmation')

  const closeDialogPromise = page.waitForEvent('dialog')
  const closePromise = page.getByRole('button', { name: 'Close voice' }).click()
  const closeDialog = await closeDialogPromise
  expect(closeDialog.message()).toBe(
    'Discard this reviewed transcript and close Voice?',
  )
  await closeDialog.dismiss()
  await closePromise
  await expect(page.getByRole('main', { name: 'Voice capture' })).toBeVisible()

  const shortcutDialogPromise = page.waitForEvent('dialog')
  const shortcutPromise = page.keyboard.press('Control+,')
  const shortcutDialog = await shortcutDialogPromise
  expect(shortcutDialog.message()).toBe(
    'Discard this reviewed transcript and close Voice?',
  )
  await shortcutDialog.dismiss()
  await shortcutPromise
  await expect(page.getByRole('main', { name: 'Voice capture' })).toBeVisible()

  const settingsDialogPromise = page.waitForEvent('dialog')
  const settingsPromise = page.getByRole('button', {
    name: 'Audio settings',
  }).click()
  const settingsDialog = await settingsDialogPromise
  await settingsDialog.accept()
  await settingsPromise
  await expect(page.getByRole('heading', { name: 'Audio devices' }))
    .toBeVisible()
  await expect(page.getByRole('main', { name: 'Voice capture' })).toHaveCount(0)
})

test('hangs up trusted playback and rejects late speech status', async () => {
  await installAudioMock()
  await emitSnapshot(readySnapshot({
    capabilities: [
      'chat.stream',
      'voice.settings',
      'voice.capture',
      'voice.transcription',
      'voice.speech',
      'voice.speech.cancel',
    ],
  }))
  await setVoiceTranscriptionDelay(true)
  await setNextVoiceTranscriptionResult({
    text: 'Hang up this spoken reply',
    language: 'en',
    languageProbability: 0.98,
  })
  await clearCalls()

  await page.getByRole('button', { name: 'Start voice' }).click()
  await page.getByRole('button', { name: 'Start microphone' }).click()
  await emitSpeechFrames(0.08, 10)
  await emitAudioFrames(0, 30)
  expect(await releaseNextVoiceTranscription()).toBe(true)
  await page.evaluate(() => {
    ;(window as TestWindow).elysiaDesktopTest.setChatActionDelay(true)
  })
  await page.getByRole('button', { name: 'Send transcript' }).click()
  await expect.poll(async () => (
    (await getCalls()).filter((call) => call.method === 'sendMessage').length
  )).toBe(1)
  await expect.poll(() => page.evaluate(() => (
    (window as TestWindow).elysiaDesktopTest.getPendingChatActionCount()
  ))).toBe(1)

  // Closing Voice before the invoke acknowledgement must still stop the first
  // trusted playback event without allowing that event to choose ownership.
  await page.getByRole('button', { name: 'Close voice' }).click()
  await expect(page.getByRole('main', { name: 'Voice capture' })).toHaveCount(0)
  await emitEvent({
    type: 'voice-speech-status',
    kind: 'playing',
    requestId: 'test-request-1',
    chatId: 'chat-test',
    sequence: 0,
  })
  await expect.poll(async () => (
    (await getCalls()).filter(
      (call) => call.method === 'stopSpeechPlayback',
    ).length
  )).toBe(1)
  expect((await getCalls()).find(
    (call) => call.method === 'stopSpeechPlayback',
  )?.args).toEqual(['test-request-1', 'chat-test'])
  await page.evaluate(() => {
    const control = (window as TestWindow).elysiaDesktopTest
    control.releaseNextChatAction()
    control.setChatActionDelay(false)
  })

  await emitEvents([
    {
      type: 'voice-speech-status',
      kind: 'played',
      requestId: 'test-request-1',
      chatId: 'chat-test',
      sequence: 0,
    },
    {
      type: 'voice-speech-status',
      kind: 'terminal',
      requestId: 'test-request-1',
      chatId: 'chat-test',
      state: 'completed',
    },
    {
      type: 'chat-complete',
      requestId: 'test-request-1',
      chatId: 'chat-test',
      reply: 'The text reply still completed safely.',
    },
  ])
  await expect(page.getByText(
    'The text reply still completed safely.',
    { exact: true },
  )).toBeVisible()
  expect((await getCalls()).filter(
    (call) => call.method === 'stopSpeechPlayback',
  )).toHaveLength(1)
})

test('holds pre-ACK barge-in PCM until the exact cancelled terminal', async () => {
  await installAudioMock()
  await openVoiceWithFinalTranscript('Start a reply before its acknowledgement')
  await setNextVoiceTranscriptionResult({
    text: 'Replacement after the interrupted reply',
    language: 'en',
    languageProbability: 0.98,
  })
  await page.evaluate(() => {
    ;(window as TestWindow).elysiaDesktopTest.setChatActionDelay(true)
  })
  await clearCalls()

  await page.getByRole('button', { name: 'Send transcript' }).click()
  await expect.poll(() => page.evaluate(() => (
    (window as TestWindow).elysiaDesktopTest.getPendingChatActionCount()
  ))).toBe(1)
  await expect(page.locator('.call-microphone-state'))
    .toHaveText('Monitoring interruptions')
  await expect.poll(async () => (await audioMockStats()).getUserMediaCalls.length)
    .toBe(2)
  expect((await audioMockStats()).getUserMediaCalls.at(-1)).toEqual({
    audio: {
      channelCount: { ideal: 1 },
      echoCancellation: { exact: true },
    },
    video: false,
  })

  await emitSpeechFrames(0.08, 10)
  await expect(page.locator('.call-state')).toHaveText('Interrupting Elysia')
  expect((await getCalls()).some((call) => call.method === 'stopGeneration'))
    .toBe(false)
  await emitAudioFrames(0, 30)
  await expect(page.getByText(
    'Your next message is ready. Waiting for the previous reply to stop before local transcription.',
    { exact: true },
  )).toBeVisible()
  expect((await getCalls()).some(
    (call) => call.method === 'beginVoiceTranscription',
  )).toBe(false)

  await page.evaluate(() => {
    const control = (window as TestWindow).elysiaDesktopTest
    control.releaseNextChatAction()
    control.setChatActionDelay(false)
  })
  await expect.poll(async () => (
    (await getCalls()).find((call) => call.method === 'stopGeneration')?.args
  )).toEqual(['test-request-1'])
  await expect.poll(async () => (
    (await getCalls()).find(
      (call) => call.method === 'stopSpeechPlayback',
    )?.args
  )).toEqual(['test-request-1', 'chat-test'])

  await emitEvent({
    type: 'chat-chunk',
    requestId: 'test-request-1',
    chatId: 'chat-test',
    chunk: 'OLD PARTIAL MUST NOT SURVIVE',
  })
  await expect(page.getByText('OLD PARTIAL MUST NOT SURVIVE', { exact: true }))
    .toHaveCount(0)
  expect((await getCalls()).some(
    (call) => call.method === 'beginVoiceTranscription',
  )).toBe(false)

  await emitEvent({
    type: 'chat-error',
    requestId: 'test-request-1',
    chatId: 'chat-test',
    code: 'request.cancelled',
    message: 'Generation cancelled.',
    retryable: false,
  })
  await expect.poll(async () => (
    (await getCalls()).filter(
      (call) => call.method === 'beginVoiceTranscription',
    ).length
  )).toBe(1)
  const replacementRequest = (await getCalls()).find(
    (call) => call.method === 'beginVoiceTranscription',
  )?.args[0] as { chatId: string; pcmBase64: string; sessionId: string }
  expect(replacementRequest.chatId).toBe('chat-test')
  expect(replacementRequest.sessionId).toMatch(/^voice_[A-Za-z0-9_-]+$/u)
  expect(replacementRequest.pcmBase64.length).toBeGreaterThan(0)
  expect(await releaseNextVoiceTranscription()).toBe(true)
  await expect(page.getByRole('textbox', { name: 'Final transcript' }))
    .toHaveValue('Replacement after the interrupted reply')
})

test('ignores playback and wrong-Chat events before exact speaking barge-in', async () => {
  await installAudioMock()
  await openVoiceWithFinalTranscript('Speak this reply until I interrupt')
  await setNextVoiceTranscriptionResult({
    text: 'Fresh utterance after speaking interruption',
    language: 'en',
    languageProbability: 0.97,
  })
  await clearCalls()

  await page.getByRole('button', { name: 'Send transcript' }).click()
  await expect.poll(async () => (
    (await getCalls()).filter((call) => call.method === 'sendMessage').length
  )).toBe(1)
  await expect(page.locator('.call-microphone-state'))
    .toHaveText('Monitoring interruptions')
  await expect.poll(async () => (await audioMockStats()).getUserMediaCalls.length)
    .toBe(2)

  await emitEvent({
    type: 'voice-speech-status',
    kind: 'playing',
    requestId: 'test-request-1',
    chatId: 'chat-test',
    sequence: 0,
  })
  await emitEvent({
    type: 'voice-speech-status',
    kind: 'playing',
    requestId: 'test-request-1',
    chatId: 'chat-other',
    sequence: 0,
  })
  await waitForTwoAnimationFrames()
  expect((await getCalls()).some((call) => call.method === 'stopGeneration'))
    .toBe(false)
  expect((await getCalls()).some(
    (call) => call.method === 'stopSpeechPlayback',
  )).toBe(false)

  await emitSpeechFrames(0.08, 10)
  await expect(page.locator('.call-state')).toHaveText('Interrupting Elysia')
  await expect.poll(async () => (
    (await getCalls()).find((call) => call.method === 'stopGeneration')?.args
  )).toEqual(['test-request-1'])
  await expect.poll(async () => (
    (await getCalls()).find(
      (call) => call.method === 'stopSpeechPlayback',
    )?.args
  )).toEqual(['test-request-1', 'chat-test'])
  await emitAudioFrames(0, 30)
  expect((await getCalls()).some(
    (call) => call.method === 'beginVoiceTranscription',
  )).toBe(false)

  await emitEvent({
    type: 'chat-error',
    requestId: 'test-request-1',
    chatId: 'chat-other',
    code: 'request.cancelled',
    message: 'Wrong Chat terminal.',
    retryable: false,
  })
  await expect(page.locator('.call-state')).toHaveText('Interrupting Elysia')
  expect((await getCalls()).some(
    (call) => call.method === 'beginVoiceTranscription',
  )).toBe(false)

  await emitEvent({
    type: 'chat-error',
    requestId: 'test-request-1',
    chatId: 'chat-test',
    code: 'request.cancelled',
    message: 'Generation cancelled.',
    retryable: false,
  })
  await expect.poll(async () => (
    (await getCalls()).filter(
      (call) => call.method === 'beginVoiceTranscription',
    ).length
  )).toBe(1)
  await emitEvents([
    {
      type: 'voice-speech-status',
      kind: 'played',
      requestId: 'test-request-1',
      chatId: 'chat-test',
      sequence: 0,
    },
    {
      type: 'voice-speech-status',
      kind: 'terminal',
      requestId: 'test-request-1',
      chatId: 'chat-test',
      state: 'cancelled',
    },
  ])
  await expect(page.getByText('Elysia is speaking', { exact: true }))
    .toHaveCount(0)
  expect((await getCalls()).filter(
    (call) => call.method === 'stopGeneration',
  )).toHaveLength(1)
  expect((await getCalls()).filter(
    (call) => call.method === 'stopSpeechPlayback',
  )).toHaveLength(1)
  expect(await releaseNextVoiceTranscription()).toBe(true)
  await expect(page.getByRole('textbox', { name: 'Final transcript' }))
    .toHaveValue('Fresh utterance after speaking interruption')
})

test('fails closed when reply-time echo cancellation is unavailable', async () => {
  await installAudioMock()
  await openVoiceWithFinalTranscript('Continue safely without barge-in')
  await setEchoCancellationAvailable(false)
  await clearCalls()

  await page.getByRole('button', { name: 'Send transcript' }).click()
  await expect.poll(async () => (
    (await getCalls()).filter((call) => call.method === 'sendMessage').length
  )).toBe(1)
  await expect.poll(async () => (await audioMockStats()).getUserMediaCalls.length)
    .toBe(2)
  expect((await audioMockStats()).getUserMediaCalls.at(-1)).toEqual({
    audio: {
      channelCount: { ideal: 1 },
      echoCancellation: { exact: true },
    },
    video: false,
  })
  await expect(page.getByText(
    'Barge-in requires microphone echo cancellation that can be verified.',
    { exact: true },
  )).toBeVisible()
  await expect.poll(async () => (await audioMockStats()).trackStopCount).toBe(2)

  await emitSpeechFrames(0.08, 12)
  await waitForTwoAnimationFrames()
  expect((await getCalls()).some((call) => call.method === 'stopGeneration'))
    .toBe(false)
  expect((await getCalls()).some(
    (call) => call.method === 'stopSpeechPlayback',
  )).toBe(false)

  await emitEvent({
    type: 'chat-complete',
    requestId: 'test-request-1',
    chatId: 'chat-test',
    reply: 'The original reply completed without unsafe self-interruption.',
  })
  await page.getByRole('button', { name: 'Close voice' }).click()
  await expect(page.getByText(
    'The original reply completed without unsafe self-interruption.',
    { exact: true },
  )).toBeVisible()
})

test('hangup discards held barge-in PCM before the old terminal arrives', async () => {
  await installAudioMock()
  await openVoiceWithFinalTranscript('Interrupt and then hang up')
  await setNextVoiceTranscriptionResult({
    text: 'This abandoned utterance must never surface',
    language: 'en',
    languageProbability: 0.96,
  })
  await clearCalls()

  await page.getByRole('button', { name: 'Send transcript' }).click()
  await expect(page.locator('.call-microphone-state'))
    .toHaveText('Monitoring interruptions')
  await expect.poll(async () => (await audioMockStats()).getUserMediaCalls.length)
    .toBe(2)
  await emitSpeechFrames(0.08, 10)
  await expect(page.locator('.call-state')).toHaveText('Interrupting Elysia')
  await emitAudioFrames(0, 30)
  await expect(page.getByText(
    'Your next message is ready. Waiting for the previous reply to stop before local transcription.',
    { exact: true },
  )).toBeVisible()
  expect((await getCalls()).some(
    (call) => call.method === 'beginVoiceTranscription',
  )).toBe(false)

  await page.getByRole('button', { name: 'Close voice' }).click()
  await expect(page.getByRole('main', { name: 'Voice capture' })).toHaveCount(0)
  await emitEvent({
    type: 'chat-error',
    requestId: 'test-request-1',
    chatId: 'chat-test',
    code: 'request.cancelled',
    message: 'Generation cancelled.',
    retryable: false,
  })
  await waitForTwoAnimationFrames()
  expect((await getCalls()).some(
    (call) => call.method === 'beginVoiceTranscription',
  )).toBe(false)
  await expect(page.getByText(
    'This abandoned utterance must never surface',
    { exact: true },
  )).toHaveCount(0)
  await expect.poll(async () => (await audioMockStats()).trackStopCount).toBe(2)
  await expect.poll(async () => (await audioMockStats()).contextsClosed).toBe(2)
})

test('admits barge-in after Chat completed while exact speech still plays', async () => {
  await installAudioMock()
  await openVoiceWithFinalTranscript('Finish the text while speech keeps playing')
  await setNextVoiceTranscriptionResult({
    text: 'New utterance after completed text',
    language: 'en',
    languageProbability: 0.98,
  })
  await clearCalls()

  await page.getByRole('button', { name: 'Send transcript' }).click()
  await expect(page.locator('.call-microphone-state'))
    .toHaveText('Monitoring interruptions')
  await expect.poll(async () => (await audioMockStats()).getUserMediaCalls.length)
    .toBe(2)
  await emitEvent({
    type: 'chat-chunk',
    requestId: 'test-request-1',
    chatId: 'chat-test',
    chunk: 'The answer began before completion.',
  })
  await emitEvent({
    type: 'voice-speech-status',
    kind: 'playing',
    requestId: 'test-request-1',
    chatId: 'chat-test',
    sequence: 0,
  })
  await emitEvent({
    type: 'chat-complete',
    requestId: 'test-request-1',
    chatId: 'chat-test',
    reply: 'The committed answer remains while speech is playing.',
  })
  await expect(page.locator('.call-microphone-state'))
    .toHaveText('Monitoring interruptions')
  expect((await getCalls()).some((call) => call.method === 'stopGeneration'))
    .toBe(false)

  await emitSpeechFrames(0.08, 10)
  await expect.poll(async () => (
    (await getCalls()).find(
      (call) => call.method === 'stopSpeechPlayback',
    )?.args
  )).toEqual(['test-request-1', 'chat-test'])
  expect((await getCalls()).some((call) => call.method === 'stopGeneration'))
    .toBe(false)
  await expect(page.locator('.call-state')).not.toHaveText('Interrupting Elysia')

  // The Chat terminal already committed, so completed replacement audio must
  // enter STT directly rather than wait for an impossible second terminal.
  await emitAudioFrames(0, 30)
  await expect.poll(async () => (
    (await getCalls()).filter(
      (call) => call.method === 'beginVoiceTranscription',
    ).length
  )).toBe(1)
  expect((await getCalls()).filter(
    (call) => call.method === 'stopSpeechPlayback',
  )).toHaveLength(1)
  expect(await releaseNextVoiceTranscription()).toBe(true)
  await expect(page.getByRole('textbox', { name: 'Final transcript' }))
    .toHaveValue('New utterance after completed text')
})

test('releases held barge-in PCM after a non-cancellation Chat error', async () => {
  await installAudioMock()
  await openVoiceWithFinalTranscript('Do not restore this failed old prompt')
  await setNextVoiceTranscriptionResult({
    text: 'New utterance after old generation failure',
    language: 'en',
    languageProbability: 0.97,
  })
  await clearCalls()

  await page.getByRole('button', { name: 'Send transcript' }).click()
  await expect(page.locator('.call-microphone-state'))
    .toHaveText('Monitoring interruptions')
  await emitEvent({
    type: 'chat-chunk',
    requestId: 'test-request-1',
    chatId: 'chat-test',
    chunk: 'Old partial reply.',
  })
  await emitSpeechFrames(0.08, 10)
  await expect(page.locator('.call-state')).toHaveText('Interrupting Elysia')
  await emitAudioFrames(0, 30)
  await expect(page.getByText(
    'Your next message is ready. Waiting for the previous reply to stop before local transcription.',
    { exact: true },
  )).toBeVisible()
  expect((await getCalls()).some(
    (call) => call.method === 'beginVoiceTranscription',
  )).toBe(false)

  await emitEvent({
    type: 'chat-error',
    requestId: 'test-request-1',
    chatId: 'chat-test',
    code: 'generation.failed',
    message: 'The old local generation failed.',
    retryable: true,
  })
  await expect.poll(async () => (
    (await getCalls()).filter(
      (call) => call.method === 'beginVoiceTranscription',
    ).length
  )).toBe(1)
  await expect(page.locator('.call-state')).toHaveText('Transcribing locally')
  await expect(page.getByText('Interrupting Elysia', { exact: true }))
    .toHaveCount(0)
  expect(await releaseNextVoiceTranscription()).toBe(true)
  await expect(page.getByRole('textbox', { name: 'Final transcript' }))
    .toHaveValue('New utterance after old generation failure')

  const discardDialogPromise = page.waitForEvent('dialog')
  const closePromise = page.getByRole('button', { name: 'Close voice' }).click()
  const discardDialog = await discardDialogPromise
  await discardDialog.accept()
  await closePromise
  await expect(page.getByLabel('Message Elysia')).toHaveValue('')
  await expect(page.getByLabel('Message from you')).toHaveCount(0)
})

test('drops abandoned interruption metadata before reconnect capture', async () => {
  await installAudioMock()
  await openVoiceWithFinalTranscript('Abandon this interruption before reconnect')
  await clearCalls()

  await page.getByRole('button', { name: 'Send transcript' }).click()
  await expect(page.locator('.call-microphone-state'))
    .toHaveText('Monitoring interruptions')
  await emitSpeechFrames(0.08, 10)
  await expect(page.locator('.call-state')).toHaveText('Interrupting Elysia')
  await page.getByRole('button', { name: 'Close voice' }).click()
  await expect(page.getByRole('main', { name: 'Voice capture' })).toHaveCount(0)

  await emitSnapshot({
    revision: 2,
    status: 'error',
    capabilities: [],
    models: [],
    chatId: 'chat-test',
    chatTitle: 'Elysia Chat',
    error: 'Synthetic Backend disconnect.',
    modelName: 'qwen3.5:9b',
  })
  await expect(page.locator('.connection-pill')).toContainText('Connection error')
  await emitSnapshot(readySnapshot({
    revision: 3,
    capabilities: [
      'chat.stream',
      'voice.settings',
      'voice.capture',
      'voice.transcription',
      'voice.speech',
      'voice.speech.cancel',
    ],
  }))
  await expect(page.locator('.connection-pill')).toContainText('Connected')
  await setNextVoiceTranscriptionResult({
    text: 'Fresh ordinary capture after reconnect',
    language: 'en',
    languageProbability: 0.99,
  })
  await clearCalls()

  await page.getByRole('button', { name: 'Start voice' }).click()
  await page.getByRole('button', { name: 'Start microphone' }).click()
  await expect(page.locator('.call-state')).toHaveText('Listening for speech')
  await emitSpeechFrames(0.08, 10)
  await emitAudioFrames(0, 30)
  await expect.poll(async () => (
    (await getCalls()).filter(
      (call) => call.method === 'beginVoiceTranscription',
    ).length
  )).toBe(1)
  expect(await releaseNextVoiceTranscription()).toBe(true)
  await expect(page.getByRole('textbox', { name: 'Final transcript' }))
    .toHaveValue('Fresh ordinary capture after reconnect')
})

test('stops Composer speech before opening a new Voice Session', async () => {
  await emitSnapshot(readySnapshot({
    capabilities: [
      'chat.stream',
      'voice.settings',
      'voice.capture',
      'voice.transcription',
      'voice.speech',
      'voice.speech.cancel',
    ],
  }))
  await clearCalls()

  const composer = page.getByLabel('Message Elysia')
  await composer.fill('Read this ordinary Chat reply')
  await composer.press('Enter')
  await expect.poll(async () => (
    (await getCalls()).filter((call) => call.method === 'sendMessage').length
  )).toBe(1)
  await emitEvent({
    type: 'voice-speech-status',
    kind: 'playing',
    requestId: 'test-request-1',
    chatId: 'chat-test',
    sequence: 0,
  })
  await emitEvent({
    type: 'chat-complete',
    requestId: 'test-request-1',
    chatId: 'chat-test',
    reply: 'The text finished before its speech playback.',
  })

  await page.getByRole('button', { name: 'Start voice' }).click()
  await expect(page.getByRole('main', { name: 'Voice capture' })).toBeVisible()
  await expect.poll(async () => (
    (await getCalls()).filter(
      (call) => call.method === 'stopSpeechPlayback',
    ).length
  )).toBe(1)
  expect((await getCalls()).find(
    (call) => call.method === 'stopSpeechPlayback',
  )?.args).toEqual(['test-request-1', 'chat-test'])
})

test('stops active speech when its owning Chat changes', async () => {
  const source = chatSummary('chat-speech-source', 'Speech Source')
  const destination = chatSummary('chat-speech-destination', 'Speech Destination')
  await setChatState({
    activeChat: { ...source, messages: [] },
    chats: [source, destination],
  })
  await emitSnapshot(readySnapshot({
    chatId: source.chatId,
    chatTitle: source.title,
    capabilities: ['chat.stream', 'voice.speech', 'voice.speech.cancel'],
  }))
  await clearCalls()

  const composer = page.getByLabel('Message Elysia')
  await composer.fill('Speak only inside the source Chat')
  await composer.press('Enter')
  await expect.poll(async () => (
    (await getCalls()).filter((call) => call.method === 'sendMessage').length
  )).toBe(1)
  await emitEvent({
    type: 'voice-speech-status',
    kind: 'playing',
    requestId: 'test-request-1',
    chatId: source.chatId,
    sequence: 0,
  })
  await emitEvent({
    type: 'chat-complete',
    requestId: 'test-request-1',
    chatId: source.chatId,
    reply: 'Source text is complete.',
  })

  await page.getByRole('button', {
    name: `Open chat ${destination.title}`,
  }).click()
  await expect(page.locator('#chat-title')).toHaveText(destination.title)
  await expect.poll(async () => (
    (await getCalls()).filter(
      (call) => call.method === 'stopSpeechPlayback',
    ).length
  )).toBe(1)
  expect((await getCalls()).find(
    (call) => call.method === 'stopSpeechPlayback',
  )?.args).toEqual(['test-request-1', source.chatId])
})

test('settles a Voice Chat turn when speech capability is unavailable', async () => {
  await installAudioMock()
  await openVoiceCapturePage()
  await setVoiceTranscriptionDelay(true)
  await setNextVoiceTranscriptionResult({
    text: 'Complete as a text-only Voice turn',
    language: 'en',
    languageProbability: 0.97,
  })
  await clearCalls()

  await page.getByRole('button', { name: 'Start microphone' }).click()
  await emitSpeechFrames(0.08, 10)
  await emitAudioFrames(0, 30)
  expect(await releaseNextVoiceTranscription()).toBe(true)
  await page.getByRole('button', { name: 'Send transcript' }).click()
  await expect(page.locator('.call-microphone-state'))
    .toHaveText('Monitoring interruptions')
  await emitEvent({
    type: 'chat-complete',
    requestId: 'test-request-1',
    chatId: 'chat-test',
    reply: 'Text-only completion is ready.',
  })

  await expect(page.getByText('Ready to listen', { exact: true })).toBeVisible()
  await expect(page.getByLabel('Final transcript')).toHaveCount(0)
  expect((await getCalls()).some(
    (call) => call.method === 'stopSpeechPlayback',
  )).toBe(false)
})

test('continues Voice as text when speech capability disappears mid-turn', async () => {
  await installAudioMock()
  await emitSnapshot(readySnapshot({
    capabilities: [
      'chat.stream',
      'voice.settings',
      'voice.capture',
      'voice.transcription',
      'voice.speech',
      'voice.speech.cancel',
    ],
  }))
  await setVoiceTranscriptionDelay(true)
  await setNextVoiceTranscriptionResult({
    text: 'Keep the reply visible if speech disappears',
    language: 'en',
    languageProbability: 0.97,
  })
  await clearCalls()

  await page.getByRole('button', { name: 'Start voice' }).click()
  await page.getByRole('button', { name: 'Start microphone' }).click()
  await emitSpeechFrames(0.08, 10)
  await emitAudioFrames(0, 30)
  expect(await releaseNextVoiceTranscription()).toBe(true)
  await page.getByRole('button', { name: 'Send transcript' }).click()
  await expect(page.locator('.call-microphone-state'))
    .toHaveText('Monitoring interruptions')

  await emitSnapshot(readySnapshot({
    revision: 2,
    capabilities: [
      'chat.stream',
      'voice.settings',
      'voice.capture',
      'voice.transcription',
    ],
  }))
  await expect(page.getByText(
    'Speech playback became unavailable. The text reply will continue safely.',
    { exact: true },
  )).toBeVisible()
  await emitEvent({
    type: 'chat-complete',
    requestId: 'test-request-1',
    chatId: 'chat-test',
    reply: 'The safe text reply completed.',
  })

  await expect(page.getByText('Ready to listen', { exact: true })).toBeVisible()
  await expect(page.getByText(
    'Speech playback became unavailable. The text reply will continue safely.',
    { exact: true },
  )).toBeVisible()
  await expect(page.getByRole('button', { name: 'Start microphone' })).toBeEnabled()
})

test('recovers a rejected Voice send without duplicating its transcript', async () => {
  await installAudioMock()
  await emitSnapshot(readySnapshot({
    capabilities: [
      'chat.stream',
      'voice.settings',
      'voice.capture',
      'voice.transcription',
      'voice.speech',
      'voice.speech.cancel',
    ],
  }))
  const composer = page.getByLabel('Message Elysia')
  await composer.fill('Keep this typed draft only')
  await setVoiceTranscriptionDelay(true)
  await setNextVoiceTranscriptionResult({
    text: 'Retry this Voice transcript once',
    language: 'en',
    languageProbability: 0.98,
  })
  await clearCalls()

  await page.getByRole('button', { name: 'Start voice' }).click()
  await page.getByRole('button', { name: 'Start microphone' }).click()
  await emitSpeechFrames(0.08, 10)
  await emitAudioFrames(0, 30)
  expect(await releaseNextVoiceTranscription()).toBe(true)
  const transcript = page.getByRole('textbox', { name: 'Final transcript' })
  await expect(transcript).toHaveValue('Retry this Voice transcript once')
  await page.evaluate(() => {
    const control = (window as TestWindow).elysiaDesktopTest
    control.failNextSend('The Voice request could not start.')
    control.setChatActionDelay(true)
  })
  await page.getByRole('button', { name: 'Send transcript' }).click()
  await expect.poll(() => page.evaluate(() => (
    (window as TestWindow).elysiaDesktopTest.getPendingChatActionCount()
  ))).toBe(1)

  await emitSnapshot(readySnapshot({
    revision: 2,
    capabilities: [
      'chat.stream',
      'voice.settings',
      'voice.capture',
      'voice.transcription',
    ],
  }))
  await page.evaluate(() => {
    const control = (window as TestWindow).elysiaDesktopTest
    control.releaseNextChatAction()
    control.setChatActionDelay(false)
  })

  await expect(page.getByText('Voice action failed', { exact: true }))
    .toBeVisible()
  await expect(transcript).toBeEditable()
  await expect(transcript).toHaveValue('Retry this Voice transcript once')
  await expect(page.getByRole('alert')).toContainText(
    'The transcript was not sent. Review it and try again.',
  )
  await expect.poll(() => page.evaluate(() => (
    window.localStorage.getItem('elysia.pending-chat-send.v1')
  ))).toBeNull()

  await page.getByRole('button', { name: 'Send transcript' }).click()
  await expect.poll(async () => (
    (await getCalls()).filter((call) => call.method === 'sendMessage').length
  )).toBe(2)
  await emitEvent({
    type: 'chat-complete',
    requestId: 'test-request-1',
    chatId: 'chat-test',
    reply: 'The retried Voice turn completed.',
  })
  await expect(page.getByText('Ready to listen', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: 'Close voice' }).click()
  await expect(composer).toHaveValue('Keep this typed draft only')
})

test('shows a Voice Chat failure before returning its transcript to the draft', async () => {
  await installAudioMock()
  await openVoiceCapturePage()
  await setVoiceTranscriptionDelay(true)
  await setNextVoiceTranscriptionResult({
    text: 'Recover this failed Voice turn',
    language: 'en',
    languageProbability: 0.97,
  })
  await clearCalls()

  await page.getByRole('button', { name: 'Start microphone' }).click()
  await emitSpeechFrames(0.08, 10)
  await emitAudioFrames(0, 30)
  expect(await releaseNextVoiceTranscription()).toBe(true)
  await page.getByRole('button', { name: 'Send transcript' }).click()
  await emitEvent({
    type: 'chat-error',
    requestId: 'test-request-1',
    chatId: 'chat-test',
    code: 'generation.failed',
    message: 'The local reply failed safely.',
    retryable: true,
  })

  await expect(page.getByText('Voice action failed', { exact: true }))
    .toBeVisible()
  const call = page.getByRole('main', { name: 'Voice capture' })
  await expect(call).toHaveAttribute('data-character-state', 'error')
  await expect(call.locator('.character-artwork'))
    .toHaveAttribute('data-character-state', 'error')
  await expect(page.getByRole('alert')).toContainText(
    'The local reply failed safely. Your transcript was restored to the Chat draft.',
  )
  await page.getByRole('button', { name: 'Close voice' }).click()
  await expect(page.getByLabel('Message Elysia'))
    .toHaveValue('Recover this failed Voice turn')
})

test('safely appends a reviewed transcript after an existing draft', async () => {
  await installAudioMock()
  await emitSnapshot(readySnapshot({
    capabilities: [
      'chat.stream',
      'voice.settings',
      'voice.capture',
      'voice.transcription',
    ],
  }))
  const composer = page.getByLabel('Message Elysia')
  await composer.fill('Keep this existing draft')
  await setVoiceTranscriptionDelay(true)
  await setNextVoiceTranscriptionResult({
    text: '追加这段转写',
    language: 'zh',
    languageProbability: 0.99,
  })
  await clearCalls()

  await page.getByRole('button', { name: 'Start voice' }).click()
  await page.getByRole('button', { name: 'Start microphone' }).click()
  await emitSpeechFrames(0.08, 10)
  await emitAudioFrames(0, 30)
  await expect(page.getByText('Transcribing locally', { exact: true }))
    .toBeVisible()
  expect(await releaseNextVoiceTranscription()).toBe(true)
  await expect(page.getByRole('textbox', { name: 'Final transcript' }))
    .toHaveValue('追加这段转写')
  await page.getByRole('button', {
    name: 'Append transcript to message',
  }).click()

  await expect(composer).toHaveValue(
    'Keep this existing draft\n\n追加这段转写',
  )
  expect((await getCalls()).some((call) => call.method === 'sendMessage'))
    .toBe(false)
})

test('keeps the transcript editor when draft storage rejects writes', async () => {
  await installAudioMock()
  await openVoiceCapturePage()
  await setVoiceTranscriptionDelay(true)
  await setNextVoiceTranscriptionResult({
    text: 'Protect this final transcript',
    language: 'en',
    languageProbability: 0.98,
  })

  await page.getByRole('button', { name: 'Start microphone' }).click()
  await emitSpeechFrames(0.08, 10)
  await emitAudioFrames(0, 30)
  expect(await releaseNextVoiceTranscription()).toBe(true)
  const transcript = page.getByRole('textbox', { name: 'Final transcript' })
  await expect(transcript).toHaveValue('Protect this final transcript')

  const failureCases = [
    ['QuotaExceededError', 'Quota-protected final transcript'],
    ['SecurityError', 'Security-protected final transcript'],
  ] as const
  for (const [failureName, transcriptText] of failureCases) {
    await test.step(`preserves the editor after ${failureName}`, async () => {
      await transcript.fill(transcriptText)
      await rejectChatDraftStorageWrites(failureName)
      try {
        await page.getByRole('button', {
          name: 'Use transcript in message',
        }).click()
        await expect(page.getByRole('alert')).toHaveText(
          'The transcript could not be protected in local draft storage. Free some disk space and try again.',
        )
        await expect(transcript).toHaveValue(transcriptText)
        await expect(page.getByRole('main', { name: 'Voice capture' }))
          .toBeVisible()
      } finally {
        await restoreChatDraftStorageWrites()
      }
    })
  }
})

test('rejects a transcript that would exceed the durable draft limit', async () => {
  const existingDraftLength = 999_990
  await page.evaluate((draftLength) => {
    window.localStorage.setItem(
      'elysia.chat-drafts.v1',
      JSON.stringify({ 'chat-test': 'd'.repeat(draftLength) }),
    )
  }, existingDraftLength)
  await page.reload()
  await page.waitForFunction(() => (
    'elysiaDesktopTest' in window
    && (window as TestWindow).elysiaDesktopTest
      .getCalls()
      .some((call) => call.method === 'onBackendEvent.subscribe')
  ))
  await installAudioMock()
  await openVoiceCapturePage()
  await setVoiceTranscriptionDelay(true)
  await setNextVoiceTranscriptionResult({
    text: 'This transcript crosses the boundary',
    language: 'en',
    languageProbability: 0.97,
  })

  await page.getByRole('button', { name: 'Start microphone' }).click()
  await emitSpeechFrames(0.08, 10)
  await emitAudioFrames(0, 30)
  expect(await releaseNextVoiceTranscription()).toBe(true)
  const transcript = page.getByRole('textbox', { name: 'Final transcript' })
  await expect(transcript).toHaveValue('This transcript crosses the boundary')
  await page.getByRole('button', {
    name: 'Append transcript to message',
  }).click()

  await expect(page.getByRole('alert')).toHaveText(
    'The existing message draft is too long to append this transcript.',
  )
  await expect(transcript).toHaveValue('This transcript crosses the boundary')
  await expect(page.getByRole('main', { name: 'Voice capture' })).toBeVisible()
  await expect.poll(() => page.evaluate(() => {
    const raw = window.localStorage.getItem('elysia.chat-drafts.v1')
    if (raw === null) {
      return null
    }
    const drafts = JSON.parse(raw) as Record<string, string>
    return drafts['chat-test']?.length ?? null
  })).toBe(existingDraftLength)
})

test('retains a final transcript across Backend failure and recovery', async () => {
  await installAudioMock()
  await openVoiceCapturePage()
  await setVoiceTranscriptionDelay(true)
  await setNextVoiceTranscriptionResult({
    text: 'Transcript before Backend failure',
    language: 'en',
    languageProbability: 0.95,
  })

  await page.getByRole('button', { name: 'Start microphone' }).click()
  await emitSpeechFrames(0.08, 10)
  await emitAudioFrames(0, 30)
  expect(await releaseNextVoiceTranscription()).toBe(true)
  const transcript = page.getByRole('textbox', { name: 'Final transcript' })
  const call = page.getByRole('main', { name: 'Voice capture' })
  const artwork = call.locator('.character-artwork')
  await transcript.fill('Edited transcript survives Backend restart')

  await emitSnapshot({
    revision: 2,
    status: 'error',
    capabilities: [],
    models: [],
    chatId: 'chat-test',
    chatTitle: 'Elysia Chat',
    error: 'The local Backend stopped unexpectedly.',
    modelName: 'qwen3.5:9b',
  })
  await expect(transcript)
    .toHaveValue('Edited transcript survives Backend restart')
  await expect(page.getByText('Transcript ready', { exact: true }))
    .toBeVisible()
  await expect(call).toHaveAttribute('data-character-state', 'error')
  await expect(artwork).toHaveAttribute('data-character-state', 'error')

  await emitSnapshot(readySnapshot({
    revision: 3,
    capabilities: [
      'chat.stream',
      'voice.settings',
      'voice.capture',
      'voice.transcription',
    ],
  }))
  await expect(transcript)
    .toHaveValue('Edited transcript survives Backend restart')
  await expect(call).toHaveAttribute('data-character-state', 'idle')
  await expect(artwork).toHaveAttribute('data-character-state', 'idle')
  await page.getByRole('button', { name: 'Use transcript in message' }).click()
  await expect(page.getByLabel('Message Elysia'))
    .toHaveValue('Edited transcript survives Backend restart')
  expect((await getCalls()).some((call) => call.method === 'sendMessage'))
    .toBe(false)
})

test('cancels transcription and ignores its deliberately late result', async () => {
  await installAudioMock()
  await openVoiceCapturePage()
  await setVoiceTranscriptionDelay(true)
  await setNextVoiceTranscriptionResult({
    text: 'This late transcript must be discarded',
    language: 'en',
    languageProbability: 0.95,
  })
  await clearCalls()

  await page.getByRole('button', { name: 'Start microphone' }).click()
  await emitSpeechFrames(0.08, 10)
  await emitAudioFrames(0, 30)
  await expect(page.getByText('Transcribing locally', { exact: true }))
    .toBeVisible()
  await page.getByRole('button', { name: 'Cancel transcription' }).click()
  await expect.poll(async () => (
    (await getCalls()).filter(
      (call) => call.method === 'stopVoiceTranscription',
    ).length
  )).toBe(1)
  expect(await releaseNextVoiceTranscription()).toBe(true)

  await expect(page.getByText('Transcription cancelled', { exact: true }))
    .toBeVisible()
  await expect(page.getByLabel('Final transcript')).toHaveCount(0)
  await expect(page.getByText(
    'This late transcript must be discarded',
    { exact: true },
  )).toHaveCount(0)
  expect((await getCalls()).some((call) => call.method === 'sendMessage'))
    .toBe(false)
})

test('cancels a pending transcription before switching to another Chat', async () => {
  const sourceChat = chatSummary('chat-test', 'Source Chat')
  const destinationChat = chatSummary('chat-voice-destination', 'Destination Chat')
  await setChatState({
    activeChat: { ...sourceChat, messages: [] },
    chats: [sourceChat, destinationChat],
  })
  await installAudioMock()
  await openVoiceCapturePage()
  await setVoiceTranscriptionDelay(true)
  await setNextVoiceTranscriptionResult({
    text: 'Late transcript scoped to Source Chat',
    language: 'en',
    languageProbability: 0.92,
  })
  await clearCalls()

  await page.getByRole('button', { name: 'Start microphone' }).click()
  await emitSpeechFrames(0.08, 10)
  await emitAudioFrames(0, 30)
  await expect(page.getByText('Transcribing locally', { exact: true }))
    .toBeVisible()

  // Voice is a focused surface, so leaving it is the first half of choosing a
  // different Chat; the pending request must be cancelled at that boundary.
  await page.keyboard.press('Escape')
  await page.getByRole('button', {
    name: 'Open chat Destination Chat',
  }).click()
  await expect(page.locator('#chat-title')).toHaveText('Destination Chat')
  await expect.poll(async () => (
    (await getCalls()).filter(
      (call) => call.method === 'stopVoiceTranscription',
    ).length
  )).toBe(1)
  expect((await getCalls()).find(
    (call) => call.method === 'stopVoiceTranscription',
  )?.args).toEqual(['test-voice-transcription-1'])

  expect(await releaseNextVoiceTranscription()).toBe(true)
  await waitForTwoAnimationFrames()
  await expect(page.getByLabel('Message Elysia')).toHaveValue('')
  await expect(page.getByText(
    'Late transcript scoped to Source Chat',
    { exact: true },
  )).toHaveCount(0)
  expect((await getCalls()).some((call) => call.method === 'sendMessage'))
    .toBe(false)
})

test('closes a pending transcription and ignores its late terminal event', async () => {
  await installAudioMock()
  await openVoiceCapturePage()
  await setVoiceTranscriptionDelay(true)
  await setNextVoiceTranscriptionResult({
    text: 'Closed voice must never surface this result',
    language: 'en',
    languageProbability: 0.94,
  })
  await clearCalls()

  await page.getByRole('button', { name: 'Start microphone' }).click()
  await emitSpeechFrames(0.08, 10)
  await emitAudioFrames(0, 30)
  await expect(page.getByText('Transcribing locally', { exact: true }))
    .toBeVisible()
  await page.getByRole('button', { name: 'Close voice' }).click()
  await expect(page.getByRole('main', { name: 'Voice capture' })).toHaveCount(0)
  await expect.poll(async () => (
    (await getCalls()).filter(
      (call) => call.method === 'stopVoiceTranscription',
    ).length
  )).toBe(1)
  expect(await releaseNextVoiceTranscription()).toBe(true)

  await expect(page.getByLabel('Message Elysia')).toHaveValue('')
  await expect(page.getByText(
    'Closed voice must never surface this result',
    { exact: true },
  )).toHaveCount(0)
  expect((await getCalls()).some((call) => call.method === 'sendMessage'))
    .toBe(false)
})

test('shows transcription failure and records a fresh utterance on retry', async () => {
  await installAudioMock()
  await openVoiceCapturePage()
  await setVoiceTranscriptionDelay(true)
  await clearCalls()
  await failNextVoiceTranscription('Local speech recognition failed safely.')

  await page.getByRole('button', { name: 'Start microphone' }).click()
  await expect(page.getByText('Listening for speech', { exact: true }))
    .toBeVisible()
  await emitSpeechFrames(0.08, 10)
  await emitAudioFrames(0, 30)
  await expect(page.getByText('Transcribing locally', { exact: true }))
    .toBeVisible()
  expect(await releaseNextVoiceTranscription()).toBe(true)
  await expect(page.getByText('Transcription failed', { exact: true }))
    .toBeVisible()
  await expect(page.getByText(
    'Local speech recognition failed safely.',
    { exact: true },
  )).toBeVisible()
  await expect.poll(async () => (await audioMockStats()).trackStopCount).toBe(1)
  await expect.poll(async () => (await audioMockStats()).contextsClosed).toBe(1)
  expect((await getCalls()).filter(
    (call) => call.method === 'beginVoiceTranscription',
  )).toHaveLength(1)
  expect((await getCalls()).some((call) => call.method === 'sendMessage'))
    .toBe(false)

  await setNextVoiceTranscriptionResult({
    text: 'Fresh retry transcript',
    language: 'en',
    languageProbability: 0.97,
  })
  await page.getByRole('button', { name: 'Record again' }).click()
  await expect(page.getByText('Listening for speech', { exact: true }))
    .toBeVisible()
  await emitSpeechFrames(0.08, 10)
  await emitAudioFrames(0, 30)
  await expect(page.getByText('Transcribing locally', { exact: true }))
    .toBeVisible()
  expect(await releaseNextVoiceTranscription()).toBe(true)
  await expect(page.getByText('Transcript ready', { exact: true }))
    .toBeVisible()
  await expect(page.getByRole('textbox', { name: 'Final transcript' }))
    .toHaveValue('Fresh retry transcript')
  await expect.poll(async () => (await audioMockStats()).trackStopCount).toBe(2)
  await expect.poll(async () => (await audioMockStats()).contextsClosed).toBe(2)
  const transcriptionRequests = (await getCalls())
    .filter((call) => call.method === 'beginVoiceTranscription')
    .map((call) => call.args[0] as { sessionId: string })
  expect(transcriptionRequests).toHaveLength(2)
  expect(transcriptionRequests[0]?.sessionId)
    .not.toBe(transcriptionRequests[1]?.sessionId)
  expect((await getCalls()).some((call) => call.method === 'sendMessage'))
    .toBe(false)
})

test('caps continuous Voice input at the 30 second PCM boundary', async () => {
  await installAudioMock()
  await openVoiceCapturePage()
  await setVoiceTranscriptionDelay(true)
  await clearCalls()

  await page.getByRole('button', { name: 'Start microphone' }).click()
  await expect(page.getByText('Listening for speech', { exact: true }))
    .toBeVisible()
  await emitSpeechFrames(0.08, 1_500)
  await expect(page.getByText('Transcribing locally', { exact: true }))
    .toBeVisible()

  const captureCall = (await getCalls()).find(
    (call) => call.method === 'beginVoiceTranscription',
  )
  expect(captureCall).toBeDefined()
  const request = captureCall?.args[0] as {
    sampleCount: number
    speechEndSample: number
    pcmBase64: string
  }
  expect(request.sampleCount).toBe(480_000)
  expect(request.speechEndSample).toBe(480_000)
  expect(request.pcmBase64).toHaveLength(1_280_000)
  expect((await getCalls()).some((call) => call.method === 'sendMessage'))
    .toBe(false)
  await expect.poll(async () => (await audioMockStats()).trackStopCount).toBe(1)
  await expect.poll(async () => (await audioMockStats()).contextsClosed).toBe(1)
})

test('discards silence and short noise without submitting audio', async () => {
  await installAudioMock()
  await openVoiceCapturePage()
  await clearCalls()

  await page.getByRole('button', { name: 'Start microphone' }).click()
  await expect(page.getByText('Listening for speech', { exact: true }))
    .toBeVisible()
  await emitAudioFrames(0.08, 5)
  await emitAudioFrames(0, 495)
  await expect(page.getByText('No speech detected', { exact: true }))
    .toBeVisible()
  await expect(page.getByText(
    'No usable speech was captured. Nothing was sent or added to Chat.',
    { exact: true },
  )).toBeVisible()

  const calls = await getCalls()
  expect(calls.some((call) => call.method === 'submitVoiceCapture')).toBe(false)
  expect(calls.some((call) => call.method === 'beginVoiceTranscription'))
    .toBe(false)
  expect(calls.some((call) => call.method === 'sendMessage')).toBe(false)
  await expect.poll(async () => (await audioMockStats()).trackStopCount).toBe(1)
  await expect.poll(async () => (await audioMockStats()).contextsClosed).toBe(1)
})

test('releases an active Voice capture on Escape and device disconnect', async () => {
  await installAudioMock()
  await openVoiceCapturePage()
  await clearCalls()

  await page.getByRole('button', { name: 'Start microphone' }).click()
  await expect(page.getByText('Listening for speech', { exact: true }))
    .toBeVisible()
  await page.keyboard.press('Escape')
  await expect(page.getByRole('main', { name: 'Voice capture' })).toHaveCount(0)
  await expect.poll(async () => (await audioMockStats()).trackStopCount).toBe(1)
  await expect.poll(async () => (await audioMockStats()).contextsClosed).toBe(1)

  await openVoiceCapturePage()
  await page.getByRole('button', { name: 'Start microphone' }).click()
  await expect(page.getByText('Listening for speech', { exact: true }))
    .toBeVisible()
  expect(await endLatestAudioTrack()).toBe(true)
  await expect(page.locator('.call-state'))
    .toHaveText('Microphone unavailable')
  await expect(page.getByRole('alert')).toContainText(
    'The microphone disconnected during voice capture.',
  )
  await expect.poll(async () => (await audioMockStats()).trackStopCount).toBe(2)
  await expect.poll(async () => (await audioMockStats()).contextsClosed).toBe(2)
  const calls = await getCalls()
  expect(calls.some((call) => call.method === 'submitVoiceCapture')).toBe(false)
  expect(calls.some((call) => call.method === 'beginVoiceTranscription'))
    .toBe(false)
  expect(calls.some((call) => call.method === 'sendMessage')).toBe(false)
})

test('saves exact global Settings and restarts the Backend to apply them', async () => {
  await setSettingsState(desktopSettingsState({ revision: 7 }))
  await openSettings(readySnapshot({
    models: ['qwen3.5:9b', 'llama3.2:3b'],
  }))
  await clearCalls()

  const expectedSettings: DesktopSettingsValues = {
    modelName: 'llama3.2:3b',
    ollamaHost: 'http://127.0.0.1:11435',
    shortTermMemoryTokenBudget: 4096,
    memoryRetrievalLimit: 8,
    dataImportMaxBytes: 33_554_432,
    transcriptionModel: 'medium',
    transcriptionDevice: 'cpu',
    transcriptionLanguage: 'zh',
    autoReadAloud: false,
    speechRatePercent: 125,
    speechVolumePercent: 42,
    voiceProfileId: 'elysia',
    voiceEmotion: 'happy',
    captionsEnabled: false,
    transcriptReviewMode: 'manual',
    automaticRelisten: true,
  }
  await page.getByLabel('Default model').fill(
    expectedSettings.modelName,
  )
  await page.getByLabel('Ollama origin').fill(
    `${expectedSettings.ollamaHost}/`,
  )
  await page.getByLabel('Short-term token budget').fill('4096')
  await page.getByLabel('Retrieved memories per turn').fill('8')
  await page.getByLabel('Maximum import size (bytes)').fill('33554432')
  await page.getByLabel('Speech model').selectOption(
    expectedSettings.transcriptionModel,
  )
  await page.getByLabel('Recognition device').selectOption(
    expectedSettings.transcriptionDevice,
  )
  await page.getByLabel('Default recognition language').selectOption(
    expectedSettings.transcriptionLanguage,
  )
  await page.getByLabel('Read replies aloud').selectOption('false')
  await page.getByLabel('Speech rate (%)').fill('125')
  await page.getByLabel('Speech volume (%)').fill('42')
  await page.getByLabel('Voice profile').fill('elysia')
  await page.getByLabel('Voice emotion').selectOption('happy')
  await page.getByLabel('Call captions').selectOption('false')
  await expect(page.getByLabel('Transcript review')).toBeDisabled()
  await page.getByLabel('Continue listening after replies').selectOption('true')
  await page.getByRole('button', { name: 'Save changes' }).click()

  await expect.poll(async () => (
    (await getCalls()).find(
      (call) => call.method === 'updateSettings',
    )?.args
  )).toEqual([{
    expectedRevision: 7,
    settings: expectedSettings,
  }])
  const restartAlert = page.locator('.inline-alert').filter({
    hasText: 'Backend restart required',
  })
  await expect(restartAlert).toBeVisible()
  await expect(restartAlert).toContainText(
    'default model, Ollama origin, short-term memory budget, memory retrieval limit, file import limit, speech-recognition model, speech-recognition device, speech-recognition language, speech rate, voice profile',
  )

  await clearCalls()
  await restartAlert.getByRole('button', { name: 'Restart Backend' }).click()
  await expect.poll(async () => (
    (await getCalls()).find(
      (call) => call.method === 'restartBackend',
    )?.args
  )).toEqual([])
  await expect(restartAlert).toHaveCount(0)
})

test('applies live Voice behavior choices without requiring a restart', async () => {
  await setSettingsState(desktopSettingsState({ revision: 11 }))
  await openSettings()
  await clearCalls()

  await page.getByLabel('Read replies aloud').selectOption('false')
  await page.getByLabel('Speech volume (%)').fill('35')
  await page.getByLabel('Call captions').selectOption('false')
  await page.getByLabel('Continue listening after replies').selectOption('true')
  await page.getByRole('button', { name: 'Save changes' }).click()

  await expect.poll(async () => (
    (await getCalls()).find(
      (call) => call.method === 'updateSettings',
    )?.args[0]
  )).toMatchObject({
    expectedRevision: 11,
    settings: {
      autoReadAloud: false,
      speechRatePercent: 100,
      speechVolumePercent: 35,
      voiceProfileId: 'default',
      voiceEmotion: 'neutral',
      captionsEnabled: false,
      transcriptReviewMode: 'manual',
      automaticRelisten: true,
    },
  })
  await expect(page.locator('.inline-alert').filter({
    hasText: 'Backend restart required',
  })).toHaveCount(0)
  await expect(page.getByText('Global settings are up to date')).toBeVisible()
})

test('discards a Settings draft without persisting it', async () => {
  await setSettingsState(desktopSettingsState({ revision: 2 }))
  await openSettings()
  await clearCalls()

  const origin = page.getByLabel('Ollama origin')
  const budget = page.getByLabel('Short-term token budget')
  await origin.fill('http://127.0.0.1:11434')
  await budget.fill('3072')
  await expect(page.getByText('Unsaved global changes')).toBeVisible()
  await page.getByRole('button', { name: 'Discard', exact: true }).click()

  await expect(origin).toHaveValue('http://localhost:11434')
  await expect(budget).toHaveValue('2048')
  await expect(page.getByRole('button', { name: 'Save changes' }))
    .toBeDisabled()
  expect((await getCalls()).filter(
    (call) => call.method === 'updateSettings',
  )).toHaveLength(0)
})

test('blocks invalid Settings before they reach the Desktop API', async () => {
  await setSettingsState(desktopSettingsState())
  await openSettings()
  await clearCalls()

  const origin = page.getByLabel('Ollama origin')
  const budget = page.getByLabel('Short-term token budget')
  const save = page.getByRole('button', { name: 'Save changes' })
  await origin.fill('ftp://localhost:11434')
  await expect(save).toBeDisabled()
  expect((await getCalls()).filter(
    (call) => call.method === 'updateSettings',
  )).toHaveLength(0)

  await origin.fill('http://localhost:11434')
  await budget.fill('0')
  await expect(save).toBeDisabled()
  expect((await getCalls()).filter(
    (call) => call.method === 'updateSettings',
  )).toHaveLength(0)

  await budget.fill('2048')
  await page.getByLabel('Speech rate (%)').fill('49')
  await expect(save).toBeDisabled()
  await page.getByLabel('Speech rate (%)').fill('100')
  await page.getByLabel('Voice profile').fill('../voice')
  await expect(save).toBeDisabled()
  await page.getByLabel('Voice profile').fill('default')
  await page.getByLabel('Speech volume (%)').fill('101')
  await expect(save).toBeDisabled()
  expect((await getCalls()).filter(
    (call) => call.method === 'updateSettings',
  )).toHaveLength(0)
})

test('keeps an unsaved Settings draft after persistence fails', async () => {
  await setSettingsState(desktopSettingsState({ revision: 3 }))
  await openSettings()
  await clearCalls()
  await failNextSettingsUpdate('Settings storage is temporarily unavailable.')

  const origin = page.getByLabel('Ollama origin')
  await origin.fill('http://127.0.0.1:11434')
  await page.getByRole('button', { name: 'Save changes' }).click()

  await expect.poll(async () => (
    (await getCalls()).filter(
      (call) => call.method === 'updateSettings',
    ).length
  )).toBe(1)
  const errorAlert = page.locator('.inline-alert').filter({
    hasText: 'Settings were not saved',
  })
  await expect(errorAlert).toContainText(
    'Settings storage is temporarily unavailable.',
  )
  await expect(origin).toHaveValue('http://127.0.0.1:11434')
  await expect(page.getByText('Unsaved global changes')).toBeVisible()
  await expect(page.getByRole('button', { name: 'Save changes' }))
    .toBeEnabled()
})

test('confirms dirty Settings shortcuts and preserves the draft when cancelled', async () => {
  await setSettingsState(desktopSettingsState({ revision: 4 }))
  await openSettings()

  const origin = page.getByLabel('Ollama origin')
  const draftOrigin = 'http://127.0.0.1:11434'
  await origin.fill(draftOrigin)
  await expect(page.getByText('Unsaved global changes')).toBeVisible()

  const controlKDialogPromise = page.waitForEvent('dialog')
  const controlKPromise = pressControlShortcut('k')
  const controlKDialog = await controlKDialogPromise
  expect(controlKDialog.type()).toBe('confirm')
  expect(controlKDialog.message()).toBe('Discard unsaved Settings changes?')
  await controlKDialog.dismiss()
  await controlKPromise

  await expect(page.getByRole('heading', {
    name: 'Settings',
    exact: true,
  })).toBeVisible()
  await expect(origin).toHaveValue(draftOrigin)
  await expect(page.getByPlaceholder('Search chats')).toHaveCount(0)

  const escapeDialogPromise = page.waitForEvent('dialog')
  const escapePromise = page.keyboard.press('Escape')
  const escapeDialog = await escapeDialogPromise
  expect(escapeDialog.type()).toBe('confirm')
  expect(escapeDialog.message()).toBe('Discard unsaved Settings changes?')
  await escapeDialog.dismiss()
  await escapePromise

  await expect(page.getByRole('heading', {
    name: 'Settings',
    exact: true,
  })).toBeVisible()
  await expect(origin).toHaveValue(draftOrigin)
})

test('blocks restart for a dirty draft and locks Backend fields while restarting', async () => {
  const desiredSettings: DesktopSettingsValues = {
    modelName: 'qwen3.5:9b',
    ollamaHost: 'http://127.0.0.1:11434',
    shortTermMemoryTokenBudget: 2048,
    memoryRetrievalLimit: 5,
    dataImportMaxBytes: 16_777_216,
    transcriptionModel: 'small',
    transcriptionDevice: 'auto',
    transcriptionLanguage: 'auto',
    autoReadAloud: true,
    speechRatePercent: 100,
    speechVolumePercent: 100,
    voiceProfileId: 'default',
    voiceEmotion: 'neutral',
    captionsEnabled: true,
    transcriptReviewMode: 'manual',
    automaticRelisten: false,
  }
  await setSettingsState(desktopSettingsState({
    revision: 5,
    settings: desiredSettings,
    activeSettings: {
      ...desiredSettings,
      ollamaHost: 'http://localhost:11434',
    },
    restartRequired: true,
    restartFields: ['ollamaHost'],
  }))
  await openSettings()

  const restartAlert = page.locator('.inline-alert').filter({
    hasText: 'Backend restart required',
  })
  const restartButton = restartAlert.getByRole('button', {
    name: 'Restart Backend',
  })
  const retrievalLimit = page.getByLabel('Retrieved memories per turn')
  await retrievalLimit.fill('6')
  await expect(restartButton).toBeDisabled()
  await expect(restartAlert).toContainText(
    'Save or discard the current draft first.',
  )

  await page.getByRole('button', { name: 'Discard', exact: true }).click()
  await expect(restartButton).toBeEnabled()
  await setRestartDelay(true)
  await restartButton.click()
  await expect.poll(() => page.evaluate(() => (
    (window as TestWindow).elysiaDesktopTest.getPendingRestartCount()
  ))).toBe(1)

  const backendFields = page.locator('.settings-field input')
  await expect.poll(() => backendFields.evaluateAll((inputs) => (
    inputs.every((input) => (input as HTMLInputElement).disabled)
  ))).toBe(true)
  await expect(page.getByRole('radio', { name: /^System/ })).toBeEnabled()

  expect(await releaseNextRestart()).toBe(true)
  await setRestartDelay(false)
  await expect(restartAlert).toHaveCount(0)
  await expect.poll(() => backendFields.evaluateAll((inputs) => (
    inputs.every((input) => !(input as HTMLInputElement).disabled)
  ))).toBe(true)
})

test('locks Backend fields while Settings reload without disabling Appearance', async () => {
  await setSettingsState(desktopSettingsState({ revision: 6 }))
  await openSettings()
  await setSettingsLoadDelay(true)
  await clearCalls()

  await emitSnapshot({
    ...readySnapshot(),
    revision: 2,
    status: 'error',
    error: 'Saved Settings need repair.',
  })
  await expect.poll(() => page.evaluate(() => (
    (window as TestWindow).elysiaDesktopTest.getPendingSettingsLoadCount()
  ))).toBe(1)

  const backendFields = page.locator('.settings-field input')
  await expect.poll(() => backendFields.evaluateAll((inputs) => (
    inputs.every((input) => (input as HTMLInputElement).disabled)
  ))).toBe(true)
  await expect(page.getByRole('radio', { name: /^System/ })).toBeEnabled()

  expect(await releaseNextSettingsLoad()).toBe(true)
  await setSettingsLoadDelay(false)
  await expect.poll(() => backendFields.evaluateAll((inputs) => (
    inputs.every((input) => !(input as HTMLInputElement).disabled)
  ))).toBe(true)
})

test('reports a Backend restart failure inside Settings', async () => {
  const settings = desktopSettingsState({
    revision: 7,
    restartRequired: true,
    restartFields: ['modelName'],
  })
  settings.activeSettings = {
    ...settings.settings,
    modelName: 'llama3.2:3b',
  }
  await setSettingsState(settings)
  await openSettings()
  const failure = 'The saved model is not installed.'
  await failNextRestart(failure)

  await page.getByRole('button', { name: 'Restart Backend' }).click()
  const errorAlert = page.locator('.inline-alert').filter({
    hasText: failure,
  })
  await expect(errorAlert).toBeVisible()
  await expect(errorAlert).toHaveAttribute('role', 'alert')
  await expect(page.getByRole('button', { name: 'Restart Backend' }))
    .toBeEnabled()
  await expect(page.getByLabel('Default model')).toBeEnabled()
})

test('disables Settings save while a Chat reply is active', async () => {
  await setSettingsState(desktopSettingsState({ revision: 8 }))
  await emitSnapshot(readySnapshot())
  const composer = page.getByLabel('Message Elysia')
  await composer.fill('Keep Settings read-only while this reply is active.')
  await composer.press('Enter')
  await expect.poll(async () => (
    (await getCalls()).filter((call) => call.method === 'sendMessage').length
  )).toBe(1)

  await pressControlShortcut(',')
  await expect(page.getByRole('heading', {
    name: 'General',
    exact: true,
  })).toBeVisible()
  await clearCalls()
  await page.getByLabel('Ollama origin').fill('http://127.0.0.1:11434')

  const save = page.getByRole('button', { name: 'Save changes' })
  await expect(save).toBeDisabled()
  await expect(page.getByText(
    /Wait for the current reply before saving\./,
  )).toBeVisible()
  expect((await getCalls()).filter(
    (call) => call.method === 'updateSettings',
  )).toHaveLength(0)

  await emitEvent({
    type: 'chat-error',
    requestId: 'test-request-1',
    chatId: 'chat-test',
    code: 'MODEL_INTERRUPTED',
    message: 'The test reply ended.',
    retryable: true,
  })
  await expect(save).toBeEnabled()
})

test('cancels dirty Settings Chat navigation before opening another Chat', async () => {
  const first = chatSummary('chat-first-settings', 'First Settings Chat')
  const second = chatSummary('chat-second-settings', 'Second Settings Chat')
  await setChatState({
    activeChat: { ...first, messages: [] },
    chats: [first, second],
  })
  await setSettingsState(desktopSettingsState({ revision: 9 }))
  await emitSnapshot(readySnapshot({
    chatId: first.chatId,
    chatTitle: first.title,
  }))
  await expect(page.getByRole('button', {
    name: `Open chat ${second.title}`,
  })).toBeVisible()
  await pressControlShortcut(',')
  await expect(page.getByRole('heading', {
    name: 'General',
    exact: true,
  })).toBeVisible()

  const origin = page.getByLabel('Ollama origin')
  const draftOrigin = 'http://127.0.0.1:11434'
  await origin.fill(draftOrigin)
  await expect(page.getByText('Unsaved global changes')).toBeVisible()
  await clearCalls()

  const dialogPromise = page.waitForEvent('dialog')
  const clickPromise = page.getByRole('button', {
    name: `Open chat ${second.title}`,
  }).click()
  const dialog = await dialogPromise
  expect(dialog.message()).toBe('Discard unsaved Settings changes?')
  await dialog.dismiss()
  await clickPromise

  await expect(page.getByRole('heading', {
    name: 'Settings',
    exact: true,
  })).toBeVisible()
  await expect(origin).toHaveValue(draftOrigin)
  expect((await getCalls()).filter(
    (call) => call.method === 'openChat',
  )).toHaveLength(0)
})

test('keeps Settings save controls reachable at compact high zoom', async () => {
  await setSettingsState(desktopSettingsState({ revision: 5 }))
  await openSettings()
  await setWindowAndZoom(960, 640, 2)

  const retrievalLimit = page.getByLabel('Retrieved memories per turn')
  await retrievalLimit.fill('6')
  const save = page.getByRole('button', { name: 'Save changes' })
  await save.scrollIntoViewIfNeeded()
  await expect(save).toBeVisible()

  const layout = await page.evaluate(() => {
    const view = document.querySelector('.settings-view')
    const content = document.querySelector('.settings-content')
    const saveBar = document.querySelector('.settings-save-bar')
    if (
      !(view instanceof HTMLElement)
      || !(content instanceof HTMLElement)
      || !(saveBar instanceof HTMLElement)
    ) {
      throw new Error('The Settings layout is incomplete.')
    }
    const viewRect = view.getBoundingClientRect()
    const saveBarRect = saveBar.getBoundingClientRect()
    return {
      viewportWidth: window.innerWidth,
      viewportHeight: window.innerHeight,
      rootScrollWidth: document.documentElement.scrollWidth,
      bodyScrollWidth: document.body.scrollWidth,
      contentClientWidth: content.clientWidth,
      contentScrollWidth: content.scrollWidth,
      viewLeft: viewRect.left,
      viewRight: viewRect.right,
      saveBarLeft: saveBarRect.left,
      saveBarRight: saveBarRect.right,
      saveBarTop: saveBarRect.top,
      saveBarBottom: saveBarRect.bottom,
    }
  })
  expect(layout.rootScrollWidth).toBeLessThanOrEqual(layout.viewportWidth + 1)
  expect(layout.bodyScrollWidth).toBeLessThanOrEqual(layout.viewportWidth + 1)
  expect(layout.contentScrollWidth).toBeLessThanOrEqual(
    layout.contentClientWidth + 1,
  )
  expect(layout.viewLeft).toBeGreaterThanOrEqual(-1)
  expect(layout.viewRight).toBeLessThanOrEqual(layout.viewportWidth + 1)
  expect(layout.saveBarLeft).toBeGreaterThanOrEqual(-1)
  expect(layout.saveBarRight).toBeLessThanOrEqual(layout.viewportWidth + 1)
  expect(layout.saveBarTop).toBeGreaterThanOrEqual(-1)
  expect(layout.saveBarBottom).toBeLessThanOrEqual(layout.viewportHeight + 1)

  await clearCalls()
  await save.click()
  await expect.poll(async () => (
    (await getCalls()).find(
      (call) => call.method === 'updateSettings',
    )?.args[0]
  )).toEqual({
    expectedRevision: 5,
    settings: {
      modelName: 'qwen3.5:9b',
      ollamaHost: 'http://localhost:11434',
      shortTermMemoryTokenBudget: 2048,
      memoryRetrievalLimit: 6,
      dataImportMaxBytes: 16_777_216,
      transcriptionModel: 'small',
      transcriptionDevice: 'auto',
      transcriptionLanguage: 'auto',
      autoReadAloud: true,
      speechRatePercent: 100,
      speechVolumePercent: 100,
      voiceProfileId: 'default',
      voiceEmotion: 'neutral',
      captionsEnabled: true,
      transcriptReviewMode: 'manual',
      automaticRelisten: false,
    },
  })
})

test('uses Shift+Enter for a line and Enter to send through DesktopApi', async () => {
  await emitSnapshot(readySnapshot())
  const composer = page.getByLabel('Message Elysia')
  await expect(composer).toBeEnabled()
  await clearCalls()

  await composer.fill('first line')
  await composer.press('Shift+Enter')
  await composer.type('second line')
  await expect(composer).toHaveValue('first line\nsecond line')
  expect(
    (await getCalls()).filter((call) => call.method === 'sendMessage'),
  ).toHaveLength(0)

  await composer.press('Enter')
  await expect(composer).toHaveValue('')
  await expect.poll(async () => (
    (await getCalls()).filter((call) => call.method === 'sendMessage')
  )).toHaveLength(1)

  const sendCall = (await getCalls()).find(
    (call) => call.method === 'sendMessage',
  )
  expect(sendCall?.args).toEqual([{
    chatId: 'chat-test',
    message: 'first line\nsecond line',
    attachmentIds: [],
    useProjectKnowledge: false,
  }])
  await expect(page.getByLabel('Message from you')).toContainText(
    'first line',
  )
})

const layoutCases = [
  { width: 640, height: 480, zoom: 1 },
  { width: 960, height: 640, zoom: 1 },
  { width: 960, height: 640, zoom: 1.5 },
  { width: 960, height: 640, zoom: 2 },
  { width: 1180, height: 780, zoom: 1 },
  { width: 1180, height: 780, zoom: 1.5 },
  { width: 1180, height: 780, zoom: 2 },
] as const

for (const layoutCase of layoutCases) {
  test(
    `keeps layout separated at ${layoutCase.width}x${layoutCase.height} and ${layoutCase.zoom * 100}% zoom`,
    async () => {
      await emitSnapshot(readySnapshot())
      await setWindowAndZoom(
        layoutCase.width,
        layoutCase.height,
        layoutCase.zoom,
      )
      await expect(page.getByLabel('Message Elysia')).toBeVisible()
      await expectShellWithoutHorizontalOverflow()
    },
  )
}

test('keeps a stressed composer reachable at compact high zoom', async () => {
  const files = Array.from({ length: 4 }, (_, index) => ({
    name: `high-zoom-attachment-${index}-${'f'.repeat(120)}.txt`,
    sizeBytes: 4_096 + index,
  }))
  const longDraft = Array.from(
    { length: 12 },
    (_, index) => `draft line ${index + 1}: ${'d'.repeat(96)}`,
  ).join('\n')
  const longError = [
    'The local Backend could not finish restoring the conversation.',
    'Review the connection and retry when local services are available.',
  ].join(' ').repeat(4)

  await emitSnapshot(readySnapshot())
  await setWindowAndZoom(960, 640, 2)
  await page.evaluate((nextFiles) => {
    ;(window as TestWindow).elysiaDesktopTest.setSelectedFiles(nextFiles)
  }, files)
  await page.getByRole('button', { name: 'Choose files' }).click()
  await page.getByLabel('Message Elysia').fill(longDraft)
  await emitSnapshot({
    ...readySnapshot(),
    revision: 2,
    status: 'error',
    error: longError,
  })

  await expect(page.locator('.attachment-chip')).toHaveCount(files.length)
  await expect(page.getByRole('alert')).toContainText(longError)
  await expect(page.getByRole('button', { name: 'Retry' })).toBeVisible()
  await waitForTwoAnimationFrames()

  const stressLayout = await page.evaluate(() => {
    function bounds(selector: string): {
      bottom: number
      height: number
      left: number
      right: number
      top: number
    } {
      const element = document.querySelector(selector)
      if (!(element instanceof HTMLElement)) {
        throw new Error(`Missing stress-layout element: ${selector}`)
      }
      const rectangle = element.getBoundingClientRect()
      return {
        bottom: rectangle.bottom,
        height: rectangle.height,
        left: rectangle.left,
        right: rectangle.right,
        top: rectangle.top,
      }
    }

    const workspace = bounds('.workspace-surface')
    const composer = bounds('.composer-zone')
    const messages = bounds('.message-scroll')
    return {
      bodyScrollWidth: document.body.scrollWidth,
      composer,
      messages,
      rootScrollWidth: document.documentElement.scrollWidth,
      viewportWidth: window.innerWidth,
      workspace,
    }
  })

  expect.soft(stressLayout.rootScrollWidth).toBeLessThanOrEqual(
    stressLayout.viewportWidth + 1,
  )
  expect.soft(stressLayout.bodyScrollWidth).toBeLessThanOrEqual(
    stressLayout.viewportWidth + 1,
  )
  expect.soft(stressLayout.composer.top).toBeGreaterThanOrEqual(
    stressLayout.workspace.top - 1,
  )
  expect.soft(stressLayout.composer.bottom).toBeLessThanOrEqual(
    stressLayout.workspace.bottom + 1,
  )
  expect.soft(stressLayout.messages.height).toBeGreaterThan(0)
  expect.soft(stressLayout.messages.bottom).toBeLessThanOrEqual(
    stressLayout.composer.top + 1,
  )
  const reachableControls = [
    '.attachment-list',
    '.inline-alert',
    '.inline-alert-action',
    '#chat-composer',
    'button[aria-label="Choose files"]',
    'select[aria-label="AI model"]',
    'button[aria-label="Send message"]',
  ]
  for (const selector of reachableControls) {
    const locator = page.locator(selector)
    await locator.scrollIntoViewIfNeeded()
    const control = await locator.evaluate((element, controlSelector) => {
      const rectangle = element.getBoundingClientRect()
      return {
        selector: controlSelector,
        bottom: rectangle.bottom,
        height: rectangle.height,
        left: rectangle.left,
        right: rectangle.right,
        top: rectangle.top,
      }
    }, selector)
    expect.soft(control.height, control.selector).toBeGreaterThan(0)
    expect.soft(control.top, control.selector).toBeGreaterThanOrEqual(
      stressLayout.composer.top - 1,
    )
    expect.soft(control.bottom, control.selector).toBeLessThanOrEqual(
      stressLayout.composer.bottom + 1,
    )
    expect.soft(control.left, control.selector).toBeGreaterThanOrEqual(
      stressLayout.composer.left - 1,
    )
    expect.soft(control.right, control.selector).toBeLessThanOrEqual(
      stressLayout.composer.right + 1,
    )
  }
})

test('traps compact sidebar focus and restores its trigger on close', async () => {
  await setWindowAndZoom(960, 640, 2)
  const sidebar = page.locator('#app-sidebar')
  const trigger = page.getByRole('button', { name: 'Show navigation' })

  await expect(sidebar).toHaveAttribute('aria-hidden', 'true')
  await trigger.focus()
  await trigger.click()
  await expect(sidebar).toHaveAttribute('aria-hidden', 'false')
  await waitForTwoAnimationFrames()

  const focusIsInSidebar = async (): Promise<boolean> => page.evaluate(() => (
    document.activeElement !== null
    && document.querySelector('#app-sidebar')?.contains(document.activeElement)
    === true
  ))
  expect.soft(
    await focusIsInSidebar(),
    'opening the compact sidebar should move focus inside it',
  ).toBe(true)

  await page.evaluate(() => {
    const sidebarElement = document.querySelector('#app-sidebar')
    const focusable = sidebarElement?.querySelectorAll<HTMLElement>(
      'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
    )
    focusable?.item(focusable.length - 1).focus()
  })
  await page.keyboard.press('Tab')
  expect.soft(
    await focusIsInSidebar(),
    'Tab from the final sidebar control should wrap within the sidebar',
  ).toBe(true)

  await page.evaluate(() => {
    const firstFocusable = document.querySelector<HTMLElement>(
      '#app-sidebar a[href], #app-sidebar button:not([disabled]), #app-sidebar input:not([disabled]), #app-sidebar select:not([disabled]), #app-sidebar textarea:not([disabled]), #app-sidebar [tabindex]:not([tabindex="-1"])',
    )
    firstFocusable?.focus()
  })
  await page.keyboard.press('Shift+Tab')
  expect.soft(
    await focusIsInSidebar(),
    'Shift+Tab from the first sidebar control should wrap within the sidebar',
  ).toBe(true)

  await page.keyboard.press('Escape')
  await expect(sidebar).toHaveAttribute('aria-hidden', 'true')
  await expect.soft(
    trigger,
    'Escape should restore focus to the sidebar trigger',
  ).toBeFocused()

  await trigger.focus()
  await trigger.press('Enter')
  await expect(sidebar).toHaveAttribute('aria-hidden', 'false')
  await waitForTwoAnimationFrames()
  expect.soft(
    await focusIsInSidebar(),
    'reopening the compact sidebar should move focus inside it',
  ).toBe(true)
  const scrimPoint = await page.evaluate(() => ({
    x: window.innerWidth - 8,
    y: window.innerHeight / 2,
  }))
  await page.mouse.click(scrimPoint.x, scrimPoint.y)
  await expect(sidebar).toHaveAttribute('aria-hidden', 'true')
  await expect.soft(
    trigger,
    'clicking the scrim should restore focus to the sidebar trigger',
  ).toBeFocused()
})

test('contains long titles, files, and unbroken messages', async () => {
  const longTitle = `Chat-${'超长标题🙂'.repeat(80)}`
  const longModel = `model-${'x'.repeat(420)}`
  const longUserMessage = `user-${'u'.repeat(1_600)}`
  const longAssistantMessage = `assistant-${'a'.repeat(1_800)}`
  const files = Array.from({ length: 12 }, (_, index) => ({
    name: `attachment-${index}-${'f'.repeat(240)}.txt`,
    sizeBytes: 2_048 + index,
  }))

  await emitSnapshot(readySnapshot({
    chatId: 'chat-long',
    chatTitle: longTitle,
    modelName: longModel,
    models: [longModel],
  }))
  await setWindowAndZoom(960, 640, 1.5)
  await page.evaluate((nextFiles) => {
    ;(window as TestWindow).elysiaDesktopTest.setSelectedFiles(nextFiles)
  }, files)
  await page.getByRole('button', { name: 'Choose files' }).click()
  await expect(page.locator('.attachment-chip')).toHaveCount(files.length)

  const composer = page.getByLabel('Message Elysia')
  await composer.fill(longUserMessage)
  await composer.press('Enter')
  await expect(page.getByLabel('Message from you')).toContainText(
    longUserMessage,
  )
  await expect.poll(async () => (
    (await getCalls()).filter((call) => call.method === 'sendMessage').length
  )).toBe(1)

  await emitEvent({
    type: 'chat-chunk',
    requestId: 'test-request-1',
    chatId: 'chat-long',
    chunk: longAssistantMessage,
  })
  await emitEvent({
    type: 'chat-complete',
    requestId: 'test-request-1',
    chatId: 'chat-long',
    reply: longAssistantMessage,
  })
  await expect(page.getByLabel('Message from Elysia').last()).toContainText(
    longAssistantMessage,
  )
  await expect(page.locator('.chat-heading strong')).toHaveAttribute(
    'title',
    longTitle,
  )
  await expect(page.getByLabel('AI model')).toHaveAttribute('title', longModel)

  await expectShellWithoutHorizontalOverflow()
  const containedRegions = await page.evaluate(() => {
    const selectors = [
      '.chat-heading',
      '.model-picker',
      '.message-attachment-list',
      '.message-column',
    ]
    return selectors.map((selector) => {
      const element = document.querySelector(selector)
      if (!(element instanceof HTMLElement)) {
        throw new Error(`Missing long-content element: ${selector}`)
      }
      const bounds = element.getBoundingClientRect()
      return {
        selector,
        left: bounds.left,
        right: bounds.right,
      }
    })
  })
  for (const region of containedRegions) {
    expect(region.left, region.selector).toBeGreaterThanOrEqual(-1)
    expect(region.right, region.selector).toBeLessThanOrEqual(
      (await page.evaluate(() => window.innerWidth)) + 1,
    )
  }
})

test('shows explicit empty states for Projects and Memory', async () => {
  await emitSnapshot(readySnapshot())
  await page.getByRole('button', { name: /^Projects/ }).click()
  await expect(
    page.getByRole('heading', { name: 'No Projects yet' }),
  ).toBeVisible()
  await expect(page.getByRole('button', { name: 'Create Project' })).toBeVisible()

  await page.getByRole('button', { name: 'Memory', exact: true }).click()
  await expect(
    page.getByRole('heading', { name: 'No memory to show yet' }),
  ).toBeVisible()
  await expect(page.getByText(
    'Memory browsing and editing will use the scoped Python services when that feature is added.',
  )).toBeVisible()
})

test('renders canonical Projects, scoped Chats, and every Project entry point', async () => {
  const projectChat = chatSummary('chat-project', 'Architecture', {
    mode: 'work',
    projectId: 'project-alpha',
    messageCount: 4,
  })
  const unassignedChat = chatSummary('chat-unassigned', 'Loose notes', {
    updatedAt: '2026-08-25T12:10:00+00:00',
  })
  const alpha = projectSummary('project-alpha', 'Alpha Workspace', {
    customInstructions: 'Use the Project vocabulary.',
    workspacePath: 'D:\\Elysia_AI',
    chatCount: 1,
  })
  const archived = projectSummary('project-archive', 'Past research', {
    archived: true,
  })
  const chatState: ChatSessionState = {
    activeChat: { ...projectChat, messages: [] },
    chats: [projectChat, unassignedChat],
  }
  await setProjectState({
    activeProject: alpha,
    projects: [alpha, archived],
    chatState,
  })
  await setKnowledgeState({
    kind: 'knowledge.state',
    projectId: alpha.projectId,
    sources: [{
      sourceId: 'attachment_source_existing',
      fileName: 'architecture.pdf',
      mediaType: 'application/pdf',
      sizeBytes: 8_192,
      state: 'ready',
      publishedAt: '2026-08-25T12:00:00+00:00',
      operationId: null,
    }],
    operations: [],
  })
  await emitSnapshot(readySnapshot({
    chatId: projectChat.chatId,
    chatTitle: projectChat.title,
    capabilities: ['chat.stream', 'knowledge.management'],
  }))

  await page.getByRole('button', { name: /^Projects/ }).click()
  await expect(page.getByRole('heading', { name: 'Projects', exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Open project Alpha Workspace' }))
    .toHaveAttribute('aria-current', 'page')
  await expect(page.getByRole('button', { name: 'Open project Past research' }))
    .toContainText('Archived')
  await expect(page.getByRole('heading', { name: 'Alpha Workspace' })).toBeVisible()

  const projectChats = page.getByRole('region', { name: 'Project Chats' })
  await expect(projectChats).toContainText(projectChat.title)
  await expect(projectChats).toContainText('Current')
  const unassignedChats = page.getByRole('region', { name: 'Unassigned Chats' })
  await expect(unassignedChats).toContainText(unassignedChat.title)

  const sectionNavigation = page.getByRole('navigation', {
    name: 'Project sections',
  })
  await sectionNavigation.getByRole('button', { name: 'Sources' }).click()
  await expect(page.getByRole('heading', {
    name: 'Project Sources',
  })).toBeVisible()
  await expect(page.getByText('architecture.pdf', { exact: true })).toBeVisible()
  await page.evaluate(() => {
    ;(window as TestWindow).elysiaDesktopTest.setSelectedFiles([
      { name: 'project-context.txt', sizeBytes: 512, mediaType: 'text/plain' },
    ])
  })
  const projectFiles = page.getByRole('region', {
    name: 'Project Sources for Alpha Workspace',
  })
  await projectFiles.getByRole('button', {
    name: 'Add sources',
    exact: true,
  }).click()
  await expect(projectFiles.getByText('project-context.txt', { exact: true }))
    .toBeVisible()
  expect((await getCalls()).find(
    (call) => call.method === 'chooseProjectSources',
  )?.args).toEqual([alpha.projectId])
  await sectionNavigation.getByRole('button', { name: 'Memory' }).click()
  await expect(page.getByRole('heading', {
    name: "Project Memory isn't connected yet",
  })).toBeVisible()
  await sectionNavigation.getByRole('button', { name: 'Settings' }).click()
  await expect(page.getByRole('heading', { name: 'Project Settings' })).toBeVisible()
  await expect(page.getByLabel('Project name')).toHaveValue(alpha.name)
  await expect(page.getByLabel('Custom instructions')).toHaveValue(
    alpha.customInstructions ?? '',
  )
  await expect(page.getByText(alpha.workspacePath ?? '')).toBeVisible()
})

test('keeps Project Sources isolated while switching Projects and makes archived sources read-only', async () => {
  const chat = chatSummary('chat-source-projects', 'Source projects')
  const active = projectSummary('project-source-a', 'Source A')
  const archived = projectSummary('project-source-b', 'Source B', {
    archived: true,
  })
  await setProjectState({
    activeProject: active,
    projects: [active, archived],
    chatState: {
      activeChat: { ...chat, messages: [] },
      chats: [chat],
    },
  })
  await setKnowledgeState({
    kind: 'knowledge.state',
    projectId: active.projectId,
    sources: [{
      sourceId: 'attachment_source_a',
      fileName: 'only-a.pdf',
      mediaType: 'application/pdf',
      sizeBytes: 4_096,
      state: 'ready',
      publishedAt: '2026-08-25T12:00:00+00:00',
      operationId: null,
    }],
    operations: [],
  })
  await setKnowledgeState({
    kind: 'knowledge.state',
    projectId: archived.projectId,
    sources: [{
      sourceId: 'attachment_source_b',
      fileName: 'only-b.docx',
      mediaType: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
      sizeBytes: 5_120,
      state: 'stale',
      publishedAt: '2026-08-25T12:05:00+00:00',
      operationId: null,
    }],
    operations: [],
  })
  await emitSnapshot(readySnapshot({
    capabilities: ['chat.stream', 'knowledge.management'],
  }))

  await page.getByRole('button', { name: /^Projects/ }).click()
  const sections = page.getByRole('navigation', { name: 'Project sections' })
  await sections.getByRole('button', { name: 'Sources' }).click()
  await expect(page.getByText('only-a.pdf', { exact: true })).toBeVisible()
  await expect(page.getByText('only-b.docx', { exact: true })).toHaveCount(0)

  await page.getByRole('button', { name: 'Open project Source B' }).click()
  await sections.getByRole('button', { name: 'Sources' }).click()
  const panel = page.getByRole('region', { name: 'Project Sources for Source B' })
  await expect(panel.getByText('only-b.docx', { exact: true })).toBeVisible()
  await expect(panel.getByText('only-a.pdf', { exact: true })).toHaveCount(0)
  await expect(panel.getByText(/archived.*read-only/i)).toBeVisible()
  for (const actionName of [
    'Add sources',
    'Rebuild all',
    'Revoke all',
    'Replace',
    'Reindex',
    'Export original',
    'Delete',
  ]) {
    await expect(panel.getByRole('button', { name: actionName })).toBeDisabled()
  }
})

test('keeps knowledge progress cancellable and settles authoritative outcomes', async () => {
  const chat = chatSummary('chat-knowledge-progress', 'Knowledge progress')
  const project = projectSummary('project-knowledge-progress', 'Progress Project')
  await setProjectState({
    activeProject: project,
    projects: [project],
    chatState: {
      activeChat: { ...chat, messages: [] },
      chats: [chat],
    },
  })
  await setKnowledgeState({
    kind: 'knowledge.state',
    projectId: project.projectId,
    sources: [{
      sourceId: 'attachment_progress_source',
      fileName: 'progress.pdf',
      mediaType: 'application/pdf',
      sizeBytes: 4_096,
      state: 'ready',
      publishedAt: '2026-08-25T12:00:00+00:00',
      operationId: null,
    }],
    operations: [],
  })
  await emitSnapshot(readySnapshot({
    capabilities: ['chat.stream', 'knowledge.management'],
  }))
  await page.getByRole('button', { name: /^Projects/ }).click()
  await page.getByRole('navigation', { name: 'Project sections' })
    .getByRole('button', { name: 'Sources' }).click()
  await expect(page.getByText('progress.pdf', { exact: true })).toBeVisible()
  await clearCalls()

  const runningOperation: KnowledgeState['operations'][number] = {
    operationId: 'knowledge_00000000000000000000000000000001',
    projectId: project.projectId,
    kind: 'reindex',
    state: 'running',
    phase: 'indexing',
    progressPercent: 35,
    attempt: 1,
    createdAt: '2026-08-25T12:01:00+00:00',
    updatedAt: '2026-08-25T12:01:01+00:00',
    errorCode: null,
    targetSourceId: 'attachment_progress_source',
    stagedSourceId: null,
  }
  await emitEvent({
    type: 'protocol-event',
    name: 'knowledge.operation.changed',
    requestId: 'knowledge-request-progress',
    data: {
      projectId: project.projectId,
      operation: runningOperation,
      cancellable: true,
    },
  })
  await expect(page.getByText('35%', { exact: true })).toBeVisible()
  expect((await getCalls()).filter(
    (call) => call.method === 'listProjectKnowledge',
  )).toHaveLength(0)

  await page.getByRole('button', { name: 'Stop current' }).click()
  await expect.poll(async () => (
    (await getCalls()).find(
      (call) => call.method === 'stopKnowledgeOperation',
    )?.args
  )).toEqual(['knowledge-request-progress'])
  await expect(page.getByRole('button', { name: 'Stop current' })).toBeDisabled()
  expect((await getCalls()).filter(
    (call) => call.method === 'listProjectKnowledge',
  )).toHaveLength(0)

  await emitEvent({
    type: 'protocol-event',
    name: 'knowledge.operation.changed',
    requestId: 'knowledge-request-progress',
    data: {
      projectId: project.projectId,
      cancellable: false,
      operation: {
        ...runningOperation,
        state: 'cancel_requested',
        updatedAt: '2026-08-25T12:01:01.500000+00:00',
      },
    },
  })
  await expect(page.getByRole('button', { name: 'Stopping…' })).toBeDisabled()

  await emitEvent({
    type: 'protocol-event',
    name: 'knowledge.operation.completed',
    requestId: 'knowledge-request-progress',
    data: {
      projectId: project.projectId,
      cancellable: true,
      operation: {
        ...runningOperation,
        state: 'cancelled',
        phase: 'completed',
        progressPercent: 35,
        updatedAt: '2026-08-25T12:01:02+00:00',
      },
    },
  })
  expect((await getCalls()).filter(
    (call) => call.method === 'listProjectKnowledge',
  )).toHaveLength(0)
  // The durable terminal event makes a second stop invalid immediately, but
  // the Renderer retains request correlation until Electron supplies the
  // authoritative post-response state below.
  await expect(page.getByRole('button', { name: 'Stop current' })).toBeDisabled()

  await emitEvent({
    type: 'knowledge-operation-settled',
    requestId: 'knowledge-request-progress',
    projectId: project.projectId,
    state: {
      kind: 'knowledge.state',
      projectId: project.projectId,
      sources: [{
        sourceId: 'attachment_progress_source',
        fileName: 'progress.pdf',
        mediaType: 'application/pdf',
        sizeBytes: 4_096,
        state: 'ready',
        publishedAt: '2026-08-25T12:00:00+00:00',
        operationId: null,
      }],
      operations: [{
        ...runningOperation,
        state: 'cancelled',
        phase: 'completed',
        progressPercent: 35,
        updatedAt: '2026-08-25T12:01:02+00:00',
      }],
    },
  })
  await expect(page.getByRole('button', { name: 'Stop current' })).toBeDisabled()
  await expect.poll(async () => (
    (await getCalls()).filter(
      (call) => call.method === 'listProjectKnowledge',
    ).length
  )).toBe(0)

  await clearCalls()
  await emitEvent({
    type: 'protocol-event',
    name: 'knowledge.operation.changed',
    requestId: 'knowledge-request-error',
    data: {
      projectId: project.projectId,
      cancellable: true,
      operation: {
        ...runningOperation,
        operationId: 'knowledge_00000000000000000000000000000002',
        progressPercent: 10,
        updatedAt: '2026-08-25T12:02:00+00:00',
      },
    },
  })
  await expect(page.getByRole('button', { name: 'Stop current' })).toBeEnabled()
  await emitEvent({
    type: 'knowledge-operation-error',
    requestId: 'knowledge-request-error',
    projectId: project.projectId,
    code: 'knowledge.operation_failed',
    message: 'The source failed at a safe indexing boundary.',
    retryable: true,
  })
  await expect(page.getByRole('alert')).toContainText(
    'The source failed at a safe indexing boundary.',
  )
  await expect(page.getByRole('button', { name: 'Stop current' })).toBeDisabled()
  await expect.poll(async () => (
    (await getCalls()).filter(
      (call) => call.method === 'listProjectKnowledge',
    ).length
  )).toBe(1)
})

test('restores Knowledge stop authority from the Electron snapshot', async () => {
  const chat = chatSummary('chat-knowledge-resume', 'Knowledge resume')
  const project = projectSummary('project-knowledge-resume', 'Resume Project')
  await setProjectState({
    activeProject: project,
    projects: [project],
    chatState: {
      activeChat: { ...chat, messages: [] },
      chats: [chat],
    },
  })
  await setKnowledgeState({
    kind: 'knowledge.state',
    projectId: project.projectId,
    sources: [],
    operations: [],
  })
  const requestId = 'knowledge-request-after-renderer-reload'
  const staleActiveSnapshot = readySnapshot({
    revision: 80,
    capabilities: ['chat.stream', 'knowledge.management'],
    activeKnowledgeOperation: {
      requestId,
      projectId: project.projectId,
      cancellable: true,
    },
  })
  await emitSnapshot(staleActiveSnapshot)

  await page.getByRole('button', { name: /^Projects/ }).click()
  await page.getByRole('navigation', { name: 'Project sections' })
    .getByRole('button', { name: 'Sources' }).click()
  await expect(page.getByRole('button', { name: 'Stop current' })).toBeEnabled()

  await clearCalls()
  await page.getByRole('button', { name: 'Stop current' }).click()
  await expect.poll(async () => (
    (await getCalls()).find(
      (call) => call.method === 'stopKnowledgeOperation',
    )?.args
  )).toEqual([requestId])
  await expect(page.getByRole('button', { name: 'Stop current' })).toBeDisabled()

  await emitEvent({
    type: 'knowledge-operation-error',
    requestId,
    projectId: project.projectId,
    code: 'request.cancelled',
    message: 'Knowledge operation was cancelled.',
    retryable: false,
  })
  // A getSnapshot() promise started before the terminal event may resolve
  // afterward with the same revision. The request tombstone must prevent that
  // stale dynamic ownership from resurrecting a completed operation.
  await emitSnapshot(staleActiveSnapshot)
  await expect(page.getByRole('button', { name: 'Stop current' })).toBeDisabled()
})

test('retries deferred cross-Project Source reads after Knowledge success and error terminals', async () => {
  const chat = chatSummary('chat-cross-project-lease', 'Cross-Project lease')
  const owner = projectSummary('project-lease-owner', 'Lease Owner')
  const visible = projectSummary('project-deferred-reader', 'Deferred Reader')
  const ownerState: KnowledgeState = {
    kind: 'knowledge.state',
    projectId: owner.projectId,
    sources: [],
    operations: [],
  }
  await setProjectState({
    activeProject: owner,
    projects: [owner, visible],
    chatState: {
      activeChat: { ...chat, messages: [] },
      chats: [chat],
    },
  })
  await setKnowledgeState(ownerState)
  await setKnowledgeState({
    kind: 'knowledge.state',
    projectId: visible.projectId,
    sources: [{
      sourceId: 'attachment_deferred_reader',
      fileName: 'deferred-reader.pdf',
      mediaType: 'application/pdf',
      sizeBytes: 5_120,
      state: 'ready',
      publishedAt: '2026-08-25T12:10:00+00:00',
      operationId: null,
    }],
    operations: [],
  })
  await emitSnapshot(readySnapshot({
    capabilities: ['chat.stream', 'knowledge.management'],
  }))

  await page.getByRole('button', { name: /^Projects/ }).click()
  await page.getByRole('button', { name: `Open project ${visible.name}` }).click()
  await expect(page.getByRole('heading', { name: visible.name })).toBeVisible()
  await clearCalls()

  const successfulRequestId = 'knowledge-cross-project-success'
  await emitSnapshot(readySnapshot({
    revision: 20,
    capabilities: ['chat.stream', 'knowledge.management'],
    activeKnowledgeOperation: {
      requestId: successfulRequestId,
      projectId: owner.projectId,
      cancellable: true,
    },
  }))
  await persistBackendForReload()
  await replaceRendererWindow()
  await page.getByRole('button', { name: /^Projects/ }).click()
  const sections = page.getByRole('navigation', { name: 'Project sections' })
  await sections.getByRole('button', { name: 'Sources' }).click()
  const panel = page.getByRole('region', {
    name: `Project Sources for ${visible.name}`,
  })
  await expect(panel.getByRole('alert')).toContainText(
    'Wait for the current knowledge operation to finish.',
  )
  await expect.poll(async () => (
    (await getCalls()).filter((call) => (
      call.method === 'listProjectKnowledge'
      && call.args[0] === visible.projectId
    )).length
  )).toBe(1)

  await emitEvent({
    type: 'knowledge-operation-settled',
    requestId: successfulRequestId,
    projectId: owner.projectId,
    state: ownerState,
  })
  await expect(panel.getByText('deferred-reader.pdf', { exact: true })).toBeVisible()
  await expect(panel.getByRole('alert')).toHaveCount(0)
  await expect.poll(async () => (
    (await getCalls()).filter((call) => (
      call.method === 'listProjectKnowledge'
      && call.args[0] === visible.projectId
    )).length
  )).toBe(2)

  const failedRequestId = 'knowledge-cross-project-error'
  await emitSnapshot(readySnapshot({
    revision: 21,
    capabilities: ['chat.stream', 'knowledge.management'],
    activeKnowledgeOperation: {
      requestId: failedRequestId,
      projectId: owner.projectId,
      cancellable: true,
    },
  }))
  await persistBackendForReload()
  await replaceRendererWindow()
  await page.getByRole('button', { name: /^Projects/ }).click()
  await page.getByRole('navigation', { name: 'Project sections' })
    .getByRole('button', { name: 'Sources' }).click()
  const reloadedPanel = page.getByRole('region', {
    name: `Project Sources for ${visible.name}`,
  })
  await expect(reloadedPanel.getByRole('alert')).toContainText(
    'Wait for the current knowledge operation to finish.',
  )
  await clearCalls()

  await emitEvent({
    type: 'knowledge-operation-error',
    requestId: failedRequestId,
    projectId: owner.projectId,
    code: 'knowledge.failed',
    message: 'Project Sources could not be updated.',
    retryable: true,
  })
  await expect(reloadedPanel.getByRole('alert')).toHaveCount(0)
  await expect(reloadedPanel.getByText('deferred-reader.pdf', { exact: true }))
    .toBeVisible()
  await expect.poll(async () => (
    (await getCalls()).filter((call) => (
      call.method === 'listProjectKnowledge'
      && call.args[0] === visible.projectId
    )).length
  )).toBe(1)
  expect((await getCalls()).some((call) => (
    call.method === 'listProjectKnowledge'
    && call.args[0] === owner.projectId
  ))).toBe(true)
})

test('retries deferred Sources after grounded Chat completion and failure', async () => {
  const owner = projectSummary('project-grounded-owner', 'Grounded Owner', {
    chatCount: 1,
  })
  const visible = projectSummary('project-grounded-reader', 'Grounded Reader')
  const chat = chatSummary('chat-grounded-lease', 'Grounded lease', {
    projectId: owner.projectId,
  })
  await setProjectState({
    activeProject: visible,
    projects: [owner, visible],
    chatState: {
      activeChat: { ...chat, messages: [] },
      chats: [chat],
    },
  })
  await setKnowledgeState({
    kind: 'knowledge.state',
    projectId: visible.projectId,
    sources: [{
      sourceId: 'attachment_grounded_reader',
      fileName: 'grounded-reader.docx',
      mediaType: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
      sizeBytes: 7_168,
      state: 'ready',
      publishedAt: '2026-08-25T12:20:00+00:00',
      operationId: null,
    }],
    operations: [],
  })

  const completedRequestId = 'grounded-generation-complete'
  await emitSnapshot(readySnapshot({
    revision: 30,
    chatId: chat.chatId,
    chatTitle: chat.title,
    capabilities: ['chat.stream', 'knowledge.management'],
    activeGeneration: {
      requestId: completedRequestId,
      chatId: chat.chatId,
      kind: 'send',
      userText: 'Use the Project corpus.',
      reply: '',
      usesProjectKnowledge: true,
      stopping: false,
    },
  }))
  await page.getByRole('button', { name: /^Projects/ }).click()
  await page.getByRole('navigation', { name: 'Project sections' })
    .getByRole('button', { name: 'Sources' }).click()
  const panel = page.getByRole('region', {
    name: `Project Sources for ${visible.name}`,
  })
  await expect(panel.getByRole('alert')).toContainText(
    'Wait for the current knowledge operation to finish.',
  )
  await clearCalls()

  await emitEvent({
    type: 'chat-complete',
    requestId: completedRequestId,
    chatId: chat.chatId,
    reply: 'Grounded answer completed.',
  })
  await expect(panel.getByRole('alert')).toHaveCount(0)
  await expect(panel.getByText('grounded-reader.docx', { exact: true })).toBeVisible()
  await expect.poll(async () => (
    (await getCalls()).filter((call) => (
      call.method === 'listProjectKnowledge'
      && call.args[0] === visible.projectId
    )).length
  )).toBe(1)

  const failedRequestId = 'grounded-generation-error'
  await emitSnapshot(readySnapshot({
    revision: 31,
    chatId: chat.chatId,
    chatTitle: chat.title,
    capabilities: ['chat.stream', 'knowledge.management'],
    activeGeneration: {
      requestId: failedRequestId,
      chatId: chat.chatId,
      kind: 'send',
      userText: 'Use the Project corpus again.',
      reply: '',
      usesProjectKnowledge: true,
      stopping: false,
    },
  }))
  await persistBackendForReload()
  await replaceRendererWindow()
  await page.getByRole('button', { name: /^Projects/ }).click()
  await page.getByRole('navigation', { name: 'Project sections' })
    .getByRole('button', { name: 'Sources' }).click()
  const reloadedPanel = page.getByRole('region', {
    name: `Project Sources for ${visible.name}`,
  })
  await expect(reloadedPanel.getByRole('alert')).toContainText(
    'Wait for the current knowledge operation to finish.',
  )
  await clearCalls()

  await emitEvent({
    type: 'chat-error',
    requestId: failedRequestId,
    chatId: chat.chatId,
    code: 'knowledge.answer_failed',
    message: 'Grounded Chat failed safely.',
    retryable: true,
  })
  await expect(reloadedPanel.getByRole('alert')).toHaveCount(0)
  await expect(reloadedPanel.getByText('grounded-reader.docx', { exact: true }))
    .toBeVisible()
  await expect.poll(async () => (
    (await getCalls()).filter((call) => (
      call.method === 'listProjectKnowledge'
      && call.args[0] === visible.projectId
    )).length
  )).toBe(1)
})

test('restores a forward-only export lease and ignores its stale snapshot after settlement', async () => {
  const chat = chatSummary('chat-export-lease', 'Export lease')
  const owner = projectSummary('project-export-owner', 'Export Owner')
  const visible = projectSummary('project-export-reader', 'Export Reader')
  await setProjectState({
    activeProject: owner,
    projects: [owner, visible],
    chatState: {
      activeChat: { ...chat, messages: [] },
      chats: [chat],
    },
  })
  await setKnowledgeState({
    kind: 'knowledge.state',
    projectId: owner.projectId,
    sources: [],
    operations: [],
  })
  await setKnowledgeState({
    kind: 'knowledge.state',
    projectId: visible.projectId,
    sources: [{
      sourceId: 'attachment_export_reader',
      fileName: 'export-reader.pdf',
      mediaType: 'application/pdf',
      sizeBytes: 8_192,
      state: 'ready',
      publishedAt: '2026-08-25T12:30:00+00:00',
      operationId: null,
    }],
    operations: [],
  })
  await emitSnapshot(readySnapshot({
    capabilities: ['chat.stream', 'knowledge.management'],
  }))
  await page.getByRole('button', { name: /^Projects/ }).click()
  await page.getByRole('button', { name: `Open project ${visible.name}` }).click()
  await expect(page.getByRole('heading', { name: visible.name })).toBeVisible()
  await page.getByRole('navigation', { name: 'Project sections' })
    .getByRole('button', { name: 'Sources' }).click()
  await expect(page.getByText('export-reader.pdf', { exact: true })).toBeVisible()

  const exportRequestId = 'knowledge-export-active'
  const activeExportSnapshot = readySnapshot({
    revision: 50,
    capabilities: ['chat.stream', 'knowledge.management'],
    activeKnowledgeOperation: {
      requestId: exportRequestId,
      projectId: owner.projectId,
      cancellable: false,
    },
  })
  await emitSnapshot(activeExportSnapshot)
  let panel = page.getByRole('region', {
    name: `Project Sources for ${visible.name}`,
  })
  for (const actionName of [
    'Add sources',
    'Rebuild all',
    'Revoke all',
    'Replace',
    'Reindex',
    'Export original',
    'Delete',
    'Stop current',
  ]) {
    await expect(panel.getByRole('button', { name: actionName })).toBeDisabled()
  }

  await persistBackendForReload()
  await replaceRendererWindow()
  await page.getByRole('button', { name: /^Projects/ }).click()
  await page.getByRole('navigation', { name: 'Project sections' })
    .getByRole('button', { name: 'Sources' }).click()
  panel = page.getByRole('region', {
    name: `Project Sources for ${visible.name}`,
  })
  await expect(panel.getByRole('alert')).toContainText(
    'Wait for the current knowledge operation to finish.',
  )
  for (const actionName of [
    'Add sources',
    'Rebuild all',
    'Revoke all',
    'Stop current',
  ]) {
    await expect(panel.getByRole('button', { name: actionName })).toBeDisabled()
  }
  await clearCalls()

  await emitEvent({
    type: 'knowledge-export-settled',
    requestId: exportRequestId,
    projectId: owner.projectId,
    sourceId: 'attachment_export_owner',
    result: {
      kind: 'knowledge.export',
      fileName: 'owner-source.pdf',
      mediaType: 'application/pdf',
      bytesWritten: 4_096,
    },
  })
  await expect(panel.getByRole('alert')).toHaveCount(0)
  await expect(panel.getByText('export-reader.pdf', { exact: true })).toBeVisible()
  await expect.poll(async () => (
    (await getCalls()).filter((call) => (
      call.method === 'listProjectKnowledge'
      && call.args[0] === visible.projectId
    )).length
  )).toBe(1)
  for (const actionName of [
    'Add sources',
    'Rebuild all',
    'Revoke all',
    'Replace',
    'Reindex',
    'Export original',
    'Delete',
  ]) {
    await expect(panel.getByRole('button', { name: actionName })).toBeEnabled()
  }
  await expect(panel.getByRole('button', { name: 'Stop current' })).toBeDisabled()

  // A delayed getSnapshot from the export's renderer epoch must not restore
  // authority after Electron has emitted the request-correlated terminal.
  await emitSnapshot(activeExportSnapshot)
  for (const actionName of [
    'Add sources',
    'Rebuild all',
    'Revoke all',
    'Replace',
    'Reindex',
    'Export original',
    'Delete',
  ]) {
    await expect(panel.getByRole('button', { name: actionName })).toBeEnabled()
  }
  await expect(panel.getByRole('button', { name: 'Stop current' })).toBeDisabled()
})

test('does not offer Stop for forward-only Knowledge recovery', async () => {
  const chat = chatSummary('chat-knowledge-recovery', 'Knowledge recovery')
  const project = projectSummary('project-knowledge-recovery', 'Recovery Project')
  const recoveryOperation: KnowledgeState['operations'][number] = {
    operationId: 'knowledge_00000000000000000000000000000003',
    projectId: project.projectId,
    kind: 'reindex',
    state: 'recovery_required',
    phase: 'indexing',
    progressPercent: 45,
    attempt: 2,
    createdAt: '2026-08-25T12:03:00+00:00',
    updatedAt: '2026-08-25T12:03:01+00:00',
    errorCode: 'index_failed',
    targetSourceId: 'attachment_recovery_source',
    stagedSourceId: null,
  }
  await setProjectState({
    activeProject: project,
    projects: [project],
    chatState: {
      activeChat: { ...chat, messages: [] },
      chats: [chat],
    },
  })
  await setKnowledgeState({
    kind: 'knowledge.state',
    projectId: project.projectId,
    sources: [],
    operations: [recoveryOperation],
  })
  await emitSnapshot(readySnapshot({
    capabilities: ['chat.stream', 'knowledge.management'],
  }))
  await page.getByRole('button', { name: /^Projects/ }).click()
  await page.getByRole('navigation', { name: 'Project sections' })
    .getByRole('button', { name: 'Sources' }).click()
  await expect(page.getByRole('button', {
    name: 'Recover',
    exact: true,
  })).toBeEnabled()

  await emitEvent({
    type: 'protocol-event',
    name: 'knowledge.operation.changed',
    requestId: 'knowledge-recovery-active',
    data: {
      projectId: project.projectId,
      cancellable: false,
      operation: {
        ...recoveryOperation,
        state: 'running',
        attempt: 3,
        updatedAt: '2026-08-25T12:03:02+00:00',
      },
    },
  })

  await expect(page.getByRole('button', { name: 'Stop current' })).toBeDisabled()
  await expect(page.getByRole('button', { name: 'Rebuild all' })).toBeDisabled()
})

test('opts a Project Chat into grounded answers and focuses safe citation details', async () => {
  const project = projectSummary('project-grounded', 'Grounded Project', {
    chatCount: 1,
  })
  const chat = chatSummary('chat-grounded', 'Grounded Chat', {
    projectId: project.projectId,
    messageCount: 2,
  })
  const groundedAnswer: GroundedAnswer = {
    status: 'answered',
    contextPassageCount: 1,
    statements: [{
      statementId: 'statement-1',
      kind: 'source_fact',
      text: 'The indexed total is 42.',
      citationIds: ['citation-1'],
    }],
    citations: [{
      citationId: 'citation-1',
      kind: 'table',
      excerpt: 'The indexed total is 42.',
      fileName: 'safe-report.pdf',
      mediaType: 'application/pdf',
      pageNumber: 3,
      locations: [{
        kind: 'table',
        blockOrdinal: 4,
        rowIndex: 2,
        columnIndex: 1,
        sourceStartCodePoint: 15,
        sourceEndCodePoint: 25,
      }],
    }],
  }
  const chatState: ChatSessionState = {
    activeChat: {
      ...chat,
      messages: [{
        messageId: 'user-grounded',
        role: 'user',
        content: 'What is the indexed total?',
        createdAt: '2026-08-25T12:00:00+00:00',
        attachments: [],
      }, {
        messageId: 'assistant-grounded',
        role: 'assistant',
        content: 'The indexed total is 42.',
        createdAt: '2026-08-25T12:00:01+00:00',
        attachments: [],
        groundedAnswer,
      }],
    },
    chats: [chat],
  }
  await setProjectState({
    activeProject: project,
    projects: [project],
    chatState,
  })
  await emitSnapshot(readySnapshot({
    chatId: chat.chatId,
    chatTitle: chat.title,
    capabilities: ['chat.stream', 'knowledge.management'],
  }))

  const toggle = page.getByRole('switch', { name: /Use Project Sources/ })
  await expect(toggle).toBeVisible()
  await toggle.check()
  const citation = page.getByRole('button', {
    name: /safe-report\.pdf/,
  })
  await citation.focus()
  await citation.press('Enter')
  const detail = page.getByRole('region', {
    name: 'Citation from safe-report.pdf',
  })
  await expect(detail).toBeFocused()
  await expect(detail).toContainText('Page 3')
  await expect(detail).toContainText('Block 5')
  await expect(detail).toContainText('Row 3, column 2')
  await expect(detail).toContainText('Characters 15–25')
  await expect(detail.locator('a')).toHaveCount(0)

  await clearCalls()
  const composer = page.getByLabel('Message Elysia')
  await composer.fill('Answer only from the Project corpus.')
  await composer.press('Enter')
  await expect.poll(async () => (
    (await getCalls()).find((call) => call.method === 'sendMessage')?.args
  )).toEqual([{
    chatId: chat.chatId,
    message: 'Answer only from the Project corpus.',
    attachmentIds: [],
    useProjectKnowledge: true,
  }])
})

test('refreshes Projects once after navigating during a pending Chat action', async () => {
  const activeChat = chatSummary('chat-before-create', 'Existing Chat')
  const project = projectSummary('project-action-sync', 'Action Sync')
  await setProjectState({
    activeProject: project,
    projects: [project],
    chatState: {
      activeChat: { ...activeChat, messages: [] },
      chats: [activeChat],
    },
  })
  await emitSnapshot(readySnapshot({
    chatId: activeChat.chatId,
    chatTitle: activeChat.title,
  }))
  await expect.poll(async () => (
    (await getCalls()).filter((call) => call.method === 'listProjects').length
  )).toBeGreaterThan(0)
  await clearCalls()

  await page.evaluate(() => {
    ;(window as TestWindow).elysiaDesktopTest.setChatActionDelay(true)
  })
  await page.getByRole('button', { name: 'Create chat' }).click()
  await expect.poll(() => page.evaluate(() => (
    (window as TestWindow).elysiaDesktopTest.getPendingChatActionCount()
  ))).toBe(1)

  await page.getByRole('button', { name: /^Projects/ }).click()
  expect(
    (await getCalls()).filter((call) => call.method === 'listProjects'),
  ).toHaveLength(0)

  await page.evaluate(() => {
    ;(window as TestWindow).elysiaDesktopTest.releaseNextChatAction()
  })
  const unassignedChats = page.getByRole('region', { name: 'Unassigned Chats' })
  await expect(unassignedChats).toContainText('New Chat')
  expect(
    (await getCalls()).filter((call) => call.method === 'listProjects'),
  ).toHaveLength(1)
})

test('refreshes Projects once when a Chat completes after navigation', async () => {
  const projectChat = chatSummary('chat-stream-project', 'Streaming Project Chat', {
    projectId: 'project-stream-sync',
  })
  const project = projectSummary('project-stream-sync', 'Stream Sync', {
    chatCount: 1,
  })
  await setProjectState({
    activeProject: project,
    projects: [project],
    chatState: {
      activeChat: { ...projectChat, messages: [] },
      chats: [projectChat],
    },
  })
  await emitSnapshot(readySnapshot({
    chatId: projectChat.chatId,
    chatTitle: projectChat.title,
  }))
  await expect.poll(async () => (
    (await getCalls()).filter((call) => call.method === 'listProjects').length
  )).toBeGreaterThan(0)
  await clearCalls()

  const composer = page.getByLabel('Message Elysia')
  await composer.fill('Refresh this Project when the reply completes.')
  await composer.press('Enter')
  await expect.poll(async () => (
    (await getCalls()).filter((call) => call.method === 'sendMessage').length
  )).toBe(1)

  await page.getByRole('button', { name: /^Projects/ }).click()
  expect(
    (await getCalls()).filter((call) => call.method === 'listProjects'),
  ).toHaveLength(0)

  await emitEvent({
    type: 'chat-complete',
    requestId: 'test-request-1',
    chatId: projectChat.chatId,
    reply: 'Canonical reply.',
  })
  const projectChats = page.getByRole('region', { name: 'Project Chats' })
  await expect(projectChats).toContainText('2 messages')
  const calls = await getCalls()
  expect(calls.filter((call) => call.method === 'listProjects')).toHaveLength(1)
  expect(calls.filter((call) => call.method === 'listChats')).toHaveLength(0)
})

test('creates and edits a Project through canonical DesktopApi responses', async () => {
  await emitSnapshot(readySnapshot())
  await page.getByRole('button', { name: /^Projects/ }).click()

  const createTrigger = page.getByRole('button', { name: 'New Project' })
  await createTrigger.click()
  const createDialog = page.getByRole('dialog', { name: 'Create Project' })
  await expect(createDialog.getByLabel('Project name')).toBeFocused()
  await createDialog.getByRole('button', { name: 'Create Project' }).click()
  await expect(createDialog.getByRole('alert')).toContainText(
    'Enter a name for this Project.',
  )
  await createDialog.press('Escape')
  await expect(createDialog).toHaveCount(0)
  await expect(createTrigger).toBeFocused()

  await createTrigger.click()
  const reopenedDialog = page.getByRole('dialog', { name: 'Create Project' })
  await reopenedDialog.getByLabel('Project name').fill('Local Research')
  await reopenedDialog.getByLabel('Custom instructions').fill(
    'Prefer evidence from this workspace.',
  )
  await clearCalls()
  await reopenedDialog.getByRole('button', { name: 'Create Project' }).click()
  await expect.poll(async () => (
    (await getCalls()).find((call) => call.method === 'createProject')?.args
  )).toEqual([{
    name: 'Local Research',
    customInstructions: 'Prefer evidence from this workspace.',
  }])
  await expect(page.getByRole('heading', { name: 'Local Research' })).toBeFocused()

  await page.getByRole('navigation', { name: 'Project sections' })
    .getByRole('button', { name: 'Settings' }).click()
  await page.getByLabel('Project name').fill('Renamed Research')
  await page.getByLabel('Custom instructions').fill('   ')
  await clearCalls()
  await page.getByRole('button', { name: 'Save Settings' }).click()
  await expect.poll(async () => (
    (await getCalls()).find((call) => call.method === 'updateProject')?.args
  )).toEqual([{
    projectId: expect.stringMatching(/^project-created-/),
    name: 'Renamed Research',
    customInstructions: null,
  }])
  await expect(page.getByRole('heading', { name: 'Renamed Research' })).toBeVisible()
  await expect(page.getByText('Project settings saved.')).toBeVisible()
})

test('binds, cancels, and confirms unbinding a Project workspace', async () => {
  const activeChat = chatSummary('chat-workspace', 'Workspace Chat', {
    projectId: 'project-workspace',
  })
  const project = projectSummary('project-workspace', 'Workspace Project', {
    chatCount: 1,
  })
  await setProjectState({
    activeProject: project,
    projects: [project],
    chatState: {
      activeChat: { ...activeChat, messages: [] },
      chats: [activeChat],
    },
  })
  await emitSnapshot(readySnapshot({
    chatId: activeChat.chatId,
    chatTitle: activeChat.title,
  }))
  await page.getByRole('button', { name: /^Projects/ }).click()
  await page.getByRole('navigation', { name: 'Project sections' })
    .getByRole('button', { name: 'Settings' }).click()

  await setSelectedWorkspace('D:\\Bound Workspace')
  await clearCalls()
  await page.getByRole('button', { name: 'Bind Workspace' }).click()
  await expect.poll(async () => (
    (await getCalls()).map((call) => call.method)
  )).toEqual(['chooseProjectWorkspace'])
  expect((await getCalls()).find(
    (call) => call.method === 'chooseProjectWorkspace',
  )?.args).toEqual([project.projectId])
  await expect(page.getByText('D:\\Bound Workspace')).toBeVisible()

  await setSelectedWorkspace(null)
  await clearCalls()
  await page.getByRole('button', { name: 'Replace Workspace' }).click()
  await expect.poll(async () => (
    (await getCalls()).map((call) => call.method)
  )).toEqual(['chooseProjectWorkspace'])
  await expect(page.getByText('Workspace selection canceled. Nothing changed.'))
    .toBeVisible()
  await expect(page.getByText('D:\\Bound Workspace')).toBeVisible()

  const unbindTrigger = page.getByRole('button', { name: 'Unbind Workspace' })
  await unbindTrigger.click()
  const unbindDialog = page.getByRole('dialog', {
    name: /Unbind workspace from “Workspace Project”/,
  })
  await unbindDialog.press('Escape')
  await expect(unbindDialog).toHaveCount(0)
  await expect(unbindTrigger).toBeFocused()

  await unbindTrigger.click()
  await clearCalls()
  await page.getByRole('dialog', {
    name: /Unbind workspace from “Workspace Project”/,
  }).getByRole('button', { name: 'Unbind Workspace' }).click()
  await expect.poll(async () => (
    (await getCalls()).find(
      (call) => call.method === 'clearProjectWorkspace',
    )?.args
  )).toEqual([project.projectId])
  await expect(page.getByText('No workspace is bound.')).toBeVisible()
})

test('moves Project Chats and keeps archived Projects read-only until restored', async () => {
  const linked = chatSummary('chat-linked', 'Linked Chat', {
    projectId: 'project-move',
  })
  const unassigned = chatSummary('chat-loose', 'Unassigned Chat')
  const project = projectSummary('project-move', 'Move Project', {
    chatCount: 1,
  })
  const destination = projectSummary(
    'project-destination',
    'Destination Project',
  )
  await setProjectState({
    activeProject: project,
    projects: [project, destination],
    chatState: {
      activeChat: { ...linked, messages: [] },
      chats: [linked, unassigned],
    },
  })
  await emitSnapshot(readySnapshot({
    chatId: linked.chatId,
    chatTitle: linked.title,
    capabilities: ['chat.stream', 'knowledge.management'],
  }))
  const knowledgeToggle = page.getByRole('switch', {
    name: /Use Project Sources/,
  })
  await knowledgeToggle.check()
  await page.getByRole('button', { name: /^Projects/ }).click()

  await clearCalls()
  await page.getByRole('button', { name: `Move here ${unassigned.title}` }).click()
  await expect.poll(async () => (
    (await getCalls()).find((call) => call.method === 'moveChatToProject')?.args
  )).toEqual([{
    chatId: unassigned.chatId,
    projectId: project.projectId,
  }])
  await expect(page.getByRole('region', { name: 'Project Chats' }))
    .toContainText(unassigned.title)

  await clearCalls()
  await page.getByRole('button', { name: `Remove ${linked.title}` }).click()
  await expect.poll(async () => (
    (await getCalls()).find((call) => call.method === 'moveChatToProject')?.args
  )).toEqual([{
    chatId: linked.chatId,
    projectId: null,
  }])
  await expect(page.getByRole('region', { name: 'Unassigned Chats' }))
    .toContainText(linked.title)

  await page.getByRole('button', {
    name: `Open project ${destination.name}`,
  }).click()
  await page.getByRole('button', { name: `Move here ${linked.title}` }).click()
  await expect.poll(async () => (
    (await getCalls()).filter(
      (call) => call.method === 'moveChatToProject',
    ).at(-1)?.args
  )).toEqual([{
    chatId: linked.chatId,
    projectId: destination.projectId,
  }])

  await page.getByRole('button', { name: /^Chat$/ }).click()
  await expect(knowledgeToggle).toBeVisible()
  await expect(knowledgeToggle).not.toBeChecked()
  await page.getByRole('button', { name: /^Projects/ }).click()

  await clearCalls()
  await page.getByRole('button', { name: 'Archive Project' }).click()
  const archiveDialog = page.getByRole('dialog', {
    name: /Archive “Destination Project”/,
  })
  expect((await getCalls()).some(
    (call) => call.method === 'setProjectArchived',
  )).toBe(false)
  await archiveDialog.getByRole('button', { name: 'Archive Project' }).click()
  await expect.poll(async () => (
    (await getCalls()).find((call) => call.method === 'setProjectArchived')?.args
  )).toEqual([{
    projectId: destination.projectId,
    archived: true,
  }])
  await expect(page.getByText('Read-only Project')).toBeVisible()
  await page.getByRole('button', { name: /^Chat$/ }).click()
  await expect(knowledgeToggle).toHaveCount(0)
  await page.getByRole('button', { name: /^Projects/ }).click()
  await page.getByRole('navigation', { name: 'Project sections' })
    .getByRole('button', { name: 'Settings' }).click()
  await expect(page.getByLabel('Project name')).toBeDisabled()
  await expect(page.getByRole('button', { name: 'Save Settings' })).toBeDisabled()

  await clearCalls()
  await page.getByRole('button', { name: 'Restore Project' }).click()
  await expect.poll(async () => (
    (await getCalls()).find((call) => call.method === 'setProjectArchived')?.args
  )).toEqual([{
    projectId: destination.projectId,
    archived: false,
  }])
  await expect(page.getByText('Read-only Project')).toHaveCount(0)

  await page.getByRole('button', { name: /^Chat$/ }).click()
  await expect(knowledgeToggle).toBeVisible()
  await expect(knowledgeToggle).not.toBeChecked()
  await clearCalls()
  const composer = page.getByLabel('Message Elysia')
  await composer.fill('Do not inherit the previous Project corpus.')
  await composer.press('Enter')
  await expect.poll(async () => (
    (await getCalls()).find((call) => call.method === 'sendMessage')?.args
  )).toEqual([{
    chatId: linked.chatId,
    message: 'Do not inherit the previous Project corpus.',
    attachmentIds: [],
    useProjectKnowledge: false,
  }])
})

test('contains Project surfaces and dialog focus at compact high zoom', async () => {
  const longName = `Project ${'界'.repeat(120)}`
  const activeChat = chatSummary('chat-project-zoom', 'Compact Project Chat', {
    projectId: 'project-zoom',
  })
  const project = projectSummary('project-zoom', longName, {
    workspacePath: `D:\\${'workspace-segment-'.repeat(18)}`,
    chatCount: 1,
  })
  await setProjectState({
    activeProject: project,
    projects: [project],
    chatState: {
      activeChat: { ...activeChat, messages: [] },
      chats: [activeChat],
    },
  })
  await emitSnapshot(readySnapshot({
    chatId: activeChat.chatId,
    chatTitle: activeChat.title,
  }))
  await page.getByRole('button', { name: /^Projects/ }).click()
  await setWindowAndZoom(960, 640, 2)

  const layout = await page.evaluate(() => {
    const selectors = [
      '.project-view',
      '.project-topbar',
      '.project-split-view',
      '.project-list-panel',
      '.project-detail',
    ]
    return {
      viewportWidth: window.innerWidth,
      rootScrollWidth: document.documentElement.scrollWidth,
      bodyScrollWidth: document.body.scrollWidth,
      regions: selectors.map((selector) => {
        const element = document.querySelector(selector)
        if (!(element instanceof HTMLElement)) {
          throw new Error(`Missing Project region: ${selector}`)
        }
        const bounds = element.getBoundingClientRect()
        return { selector, left: bounds.left, right: bounds.right }
      }),
    }
  })
  expect(layout.rootScrollWidth).toBeLessThanOrEqual(layout.viewportWidth + 1)
  expect(layout.bodyScrollWidth).toBeLessThanOrEqual(layout.viewportWidth + 1)
  for (const region of layout.regions) {
    expect(region.left, region.selector).toBeGreaterThanOrEqual(-1)
    expect(region.right, region.selector).toBeLessThanOrEqual(
      layout.viewportWidth + 1,
    )
  }

  const createTrigger = page.getByRole('button', { name: 'New Project' })
  await createTrigger.click()
  const dialog = page.getByRole('dialog', { name: 'Create Project' })
  await expect(dialog.getByLabel('Project name')).toBeFocused()
  const dialogBounds = await dialog.evaluate((element) => {
    const bounds = element.getBoundingClientRect()
    return {
      top: bounds.top,
      right: bounds.right,
      bottom: bounds.bottom,
      left: bounds.left,
      viewportWidth: window.innerWidth,
      viewportHeight: window.innerHeight,
    }
  })
  expect(dialogBounds.left).toBeGreaterThanOrEqual(-1)
  expect(dialogBounds.top).toBeGreaterThanOrEqual(-1)
  expect(dialogBounds.right).toBeLessThanOrEqual(dialogBounds.viewportWidth + 1)
  expect(dialogBounds.bottom).toBeLessThanOrEqual(dialogBounds.viewportHeight + 1)
  await dialog.press('Escape')
  await expect(createTrigger).toBeFocused()
})

test('renders safe GFM, copies exact content, and delegates trusted links', async () => {
  const markdown = [
    '# Local answer',
    '',
    '- [x] Persisted',
    '- [ ] Pending',
    '',
    '| Item | State |',
    '| --- | --- |',
    '| Memory | Local |',
    '',
    '```ts',
    'const answer = 42',
    '```',
    '',
    '<script>window.__elysiaXss = true</script>',
    '',
    '![tracking pixel](https://tracking.invalid/elysia.png)',
    '',
    '[Open docs](https://example.com/docs?q=elysia)',
    '',
    '[Unsafe link](javascript:alert(1))',
  ].join('\n')
  const summary = chatSummary('chat-markdown', 'Markdown Chat', {
    messageCount: 2,
  })
  const requestedUrls: string[] = []
  page.on('request', (request) => { requestedUrls.push(request.url()) })

  await setChatState({
    activeChat: {
      ...summary,
      messages: [
        {
          messageId: 'user-markdown',
          role: 'user',
          content: '**This stays literal user text.**',
          createdAt: '2026-08-25T12:30:00+00:00',
          attachments: [],
        },
        {
          messageId: 'assistant-markdown',
          role: 'assistant',
          content: markdown,
          createdAt: '2026-08-25T12:31:00+00:00',
          attachments: [],
        },
      ],
    },
    chats: [summary],
  })
  await emitSnapshot(readySnapshot({
    chatId: summary.chatId,
    chatTitle: summary.title,
  }))

  const userMessage = page.locator('[data-message-id="user-markdown"]')
  const assistant = page.locator('[data-message-id="assistant-markdown"]')
  await expect(assistant.getByRole('heading', { name: 'Local answer' })).toBeVisible()
  await expect(assistant.getByRole('checkbox')).toHaveCount(2)
  await expect(assistant.getByRole('checkbox').first()).toBeChecked()
  await expect(assistant.getByRole('table')).toContainText('Memory')
  await expect(userMessage.locator('strong')).toHaveCount(0)
  await expect(userMessage).toContainText('**This stays literal user text.**')
  await expect(assistant).toContainText('Image blocked: tracking pixel')
  await expect(assistant.getByRole('img')).toHaveCount(0)
  await expect(assistant.getByRole('link', { name: 'Unsafe link' })).toHaveCount(0)
  expect(await page.evaluate(() => '__elysiaXss' in window)).toBe(false)
  await waitForTwoAnimationFrames()
  expect(
    requestedUrls.filter((url) => url.includes('tracking.invalid')),
  ).toEqual([])

  await clearCalls()
  await assistant.getByRole('button', { name: 'Copy code' }).click()
  await expect.poll(async () => (
    (await getCalls()).find((call) => call.method === 'copyText')?.args
  )).toEqual(['const answer = 42'])

  await clearCalls()
  await assistant.getByRole('button', { name: 'Copy message' }).click()
  await expect.poll(async () => (
    (await getCalls()).find((call) => call.method === 'copyText')?.args
  )).toEqual([markdown])

  await clearCalls()
  await assistant.getByRole('link', { name: 'Open docs' }).click()
  await expect.poll(async () => (
    (await getCalls()).find((call) => call.method === 'openExternalUrl')?.args
  )).toEqual(['https://example.com/docs?q=elysia'])

  await setWindowAndZoom(960, 640, 2)
  const containment = await assistant.evaluate((element) => {
    const messageBounds = element.getBoundingClientRect()
    const code = element.querySelector('.code-block')?.getBoundingClientRect()
    return {
      viewportWidth: window.innerWidth,
      messageLeft: messageBounds.left,
      messageRight: messageBounds.right,
      codeLeft: code?.left,
      codeRight: code?.right,
      rootScrollWidth: document.documentElement.scrollWidth,
    }
  })
  expect(containment.rootScrollWidth).toBeLessThanOrEqual(
    containment.viewportWidth + 1,
  )
  expect(containment.messageLeft).toBeGreaterThanOrEqual(-1)
  expect(containment.messageRight).toBeLessThanOrEqual(
    containment.viewportWidth + 1,
  )
  expect(containment.codeLeft).toBeGreaterThanOrEqual(
    containment.messageLeft - 1,
  )
  expect(containment.codeRight).toBeLessThanOrEqual(
    containment.messageRight + 1,
  )
})

test('stops only the active generation and exposes its cancelled state', async () => {
  await emitSnapshot(readySnapshot())
  const composer = page.getByLabel('Message Elysia')
  await expect(composer).toBeEnabled()
  await composer.fill('Please stream a long reply.')
  await composer.press('Enter')
  await expect(page.getByRole('button', { name: 'Stop generation' })).toBeVisible()

  await emitEvent({
    type: 'chat-chunk',
    requestId: 'test-request-1',
    chatId: 'chat-test',
    chunk: 'Partial reply',
  })
  const reply = page.getByLabel('Message from Elysia').last()
  await expect(reply.getByText('Generating', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: 'Stop generation' }).click()
  await expect.poll(async () => (
    (await getCalls()).find((call) => call.method === 'stopGeneration')?.args
  )).toEqual(['test-request-1'])
  await expect(
    page.getByRole('button', { name: 'Stopping generation' }),
  ).toBeDisabled()

  await emitEvent({
    type: 'chat-error',
    requestId: 'test-request-1',
    chatId: 'chat-test',
    code: 'request.cancelled',
    message: 'Generation cancelled.',
    retryable: false,
  })
  await expect(reply.getByText('Stopped', { exact: true })).toBeVisible()
  await expect(reply).toContainText('Partial reply')
  await expect(page.getByText(
    'Generation stopped. No partial reply was saved.',
  )).toBeVisible()
  await expect(page.getByRole('button', { name: 'Stop generation' })).toHaveCount(0)
})

test('regenerates and edit-retries only the persisted tail pair', async () => {
  const summary = chatSummary('chat-retry', 'Retry Chat', { messageCount: 2 })
  await setChatState({
    activeChat: {
      ...summary,
      messages: [
        {
          messageId: 'user-retry',
          role: 'user',
          content: 'Original question',
          createdAt: '2026-08-25T12:30:00+00:00',
          attachments: [],
        },
        {
          messageId: 'assistant-retry',
          role: 'assistant',
          content: 'Original answer',
          createdAt: '2026-08-25T12:31:00+00:00',
          attachments: [],
        },
      ],
    },
    chats: [summary],
  })
  await emitSnapshot(readySnapshot({
    chatId: summary.chatId,
    chatTitle: summary.title,
  }))

  const assistant = page.locator('[data-message-id="assistant-retry"]')
  await expect(assistant.getByRole('button', { name: 'Regenerate' })).toBeVisible()
  await clearCalls()
  await assistant.getByRole('button', { name: 'Regenerate' }).click()
  await expect.poll(async () => (
    (await getCalls()).find((call) => call.method === 'retryMessage')?.args
  )).toEqual([{
    chatId: summary.chatId,
    userMessageId: 'user-retry',
    assistantMessageId: 'assistant-retry',
    useProjectKnowledge: false,
  }])
  await expect(assistant.getByText('Generating', { exact: true })).toBeVisible()

  await emitEvent({
    type: 'chat-complete',
    requestId: 'test-request-1',
    chatId: summary.chatId,
    reply: 'Regenerated answer',
  })
  await expect(assistant).toContainText('Regenerated answer')
  await expect(assistant.getByText('Complete', { exact: true })).toBeVisible()

  await clearCalls()
  await assistant.getByRole('button', { name: 'Edit & retry' }).click()
  await expect(page.getByRole('form', {
    name: 'Edit and retry message',
  })).toHaveCount(1)
  const editForm = assistant.getByRole('form', { name: 'Edit and retry message' })
  const editBox = editForm.getByLabel('Edit your last message')
  await expect(editBox).toBeFocused()
  await editBox.fill('Edited question')
  await expect.poll(() => page.evaluate(() => (
    window.localStorage.getItem('elysia.retry-edit-draft.v1')
  ))).toContain('Edited question')
  await editForm.getByRole('button', { name: 'Retry edited message' }).click()
  await expect.poll(async () => (
    (await getCalls()).find((call) => call.method === 'retryMessage')?.args
  )).toEqual([{
    chatId: summary.chatId,
    userMessageId: 'user-retry',
    assistantMessageId: 'assistant-retry',
    useProjectKnowledge: false,
    message: 'Edited question',
  }])

  await emitEvent({
    type: 'chat-complete',
    requestId: 'test-request-2',
    chatId: summary.chatId,
    reply: 'Answer to edited question',
  })
  await expect(page.locator('[data-message-id="user-retry"]')).toContainText(
    'Edited question',
  )
  await expect(assistant).toContainText('Answer to edited question')
  await expect(assistant.getByText('Complete', { exact: true })).toBeVisible()
  await expect.poll(() => page.evaluate(() => (
    window.localStorage.getItem('elysia.retry-edit-draft.v1')
  ))).toBeNull()
})

test('restores an edited retry after its renderer window closes', async () => {
  const summary = chatSummary('chat-retry-window', 'Retry Window', {
    messageCount: 2,
  })
  const canonicalState: ChatSessionState = {
    activeChat: {
      ...summary,
      messages: [
        {
          messageId: 'user-retry-window',
          role: 'user',
          content: 'Original window question',
          createdAt: '2026-08-25T12:30:00+00:00',
          attachments: [],
        },
        {
          messageId: 'assistant-retry-window',
          role: 'assistant',
          content: 'Original window answer',
          createdAt: '2026-08-25T12:31:00+00:00',
          attachments: [],
        },
      ],
    },
    chats: [summary],
  }
  await setChatState(canonicalState)
  await emitSnapshot(readySnapshot({
    chatId: summary.chatId,
    chatTitle: summary.title,
  }))
  const assistant = page.locator('[data-message-id="assistant-retry-window"]')
  await assistant.getByRole('button', { name: 'Edit & retry' }).click()
  const replacement = 'Replacement survives a closed renderer'
  await assistant.getByLabel('Edit your last message').fill(replacement)
  await assistant.getByRole('button', { name: 'Retry edited message' }).click()
  await expect.poll(() => page.evaluate(() => {
    const raw = window.localStorage.getItem('elysia.retry-edit-draft.v1')
    return raw === null ? null : JSON.parse(raw).operationId
  })).not.toBeNull()

  await replaceRendererWindow()
  await page.evaluate(({ nextSnapshot, nextState }) => {
    const control = (window as TestWindow).elysiaDesktopTest
    control.setChatState(nextState)
    control.setSnapshot(nextSnapshot)
  }, {
    nextSnapshot: readySnapshot({
      chatId: summary.chatId,
      chatTitle: summary.title,
    }),
    nextState: canonicalState,
  })
  await emitSnapshot(readySnapshot({
    chatId: summary.chatId,
    chatTitle: summary.title,
  }))

  const restoredAssistant = page.locator(
    '[data-message-id="assistant-retry-window"]',
  )
  await expect(restoredAssistant.getByRole('form', {
    name: 'Edit and retry message',
  })).toBeVisible()
  await expect(restoredAssistant.getByLabel('Edit your last message'))
    .toHaveValue(replacement)
  await expect.poll(() => page.evaluate(() => {
    const raw = window.localStorage.getItem('elysia.retry-edit-draft.v1')
    return raw === null ? null : JSON.parse(raw).operationId ?? null
  })).toBeNull()
  await restoredAssistant.getByRole('button', { name: 'Cancel' }).click()
  await expect.poll(() => page.evaluate(() => (
    window.localStorage.getItem('elysia.retry-edit-draft.v1')
  ))).toBeNull()
})

test('keeps a saved retry edit isolated from another Chat', async () => {
  const chatA = chatSummary('chat-edit-a', 'Edit A', { messageCount: 2 })
  const chatB = chatSummary('chat-edit-b', 'Edit B', { messageCount: 2 })
  const stateFor = (
    active: ChatSessionSummary,
    userId: string,
    assistantId: string,
    question: string,
  ): ChatSessionState => ({
    activeChat: {
      ...active,
      messages: [
        {
          messageId: userId,
          role: 'user',
          content: question,
          createdAt: '2026-08-25T12:30:00+00:00',
          attachments: [],
        },
        {
          messageId: assistantId,
          role: 'assistant',
          content: `Answer for ${active.title}`,
          createdAt: '2026-08-25T12:31:00+00:00',
          attachments: [],
        },
      ],
    },
    chats: [chatA, chatB],
  })
  const stateA = stateFor(
    chatA,
    'user-edit-a',
    'assistant-edit-a',
    'Question A',
  )
  const stateB = stateFor(
    chatB,
    'user-edit-b',
    'assistant-edit-b',
    'Question B',
  )
  await setChatState(stateA)
  await emitSnapshot(readySnapshot({
    chatId: chatA.chatId,
    chatTitle: chatA.title,
  }))
  const assistantA = page.locator('[data-message-id="assistant-edit-a"]')
  await assistantA.getByRole('button', { name: 'Edit & retry' }).click()
  await assistantA.getByLabel('Edit your last message').fill('Saved edit for A')

  await page.evaluate((nextState) => {
    ;(window as TestWindow).elysiaDesktopTest.setChatState(nextState)
  }, stateB)
  await page.getByRole('button', { name: 'Open chat Edit B' }).click()
  const assistantB = page.locator('[data-message-id="assistant-edit-b"]')
  await expect(assistantB.getByRole('button', { name: 'Regenerate' })).toBeDisabled()
  await expect(assistantB.getByRole('button', { name: 'Edit & retry' })).toBeDisabled()
  await page.getByLabel('Message Elysia').fill('A separate Chat draft')
  await expect(page.getByRole('button', { name: 'Send message' })).toBeDisabled()
  await expect(page.getByText(
    'A saved edited retry is waiting in another Chat. Return to it to retry or cancel.',
    { exact: true },
  )).toBeVisible()
  await expect.poll(() => page.evaluate(() => (
    window.localStorage.getItem('elysia.retry-edit-draft.v1')
  ))).toContain('Saved edit for A')

  await page.evaluate((nextState) => {
    ;(window as TestWindow).elysiaDesktopTest.setChatState(nextState)
  }, stateA)
  await page.getByRole('button', { name: 'Open chat Edit A' }).click()
  await expect(page.locator('[data-message-id="assistant-edit-a"]')
    .getByLabel('Edit your last message')).toHaveValue('Saved edit for A')
})

test('moves an orphaned retry edit into the Chat composer', async () => {
  const summary = chatSummary('chat-orphaned-edit', 'Changed Retry Chat', {
    messageCount: 2,
  })
  const canonicalState: ChatSessionState = {
    activeChat: {
      ...summary,
      messages: [
        {
          messageId: 'new-user-tail',
          role: 'user',
          content: 'A newer canonical question',
          createdAt: '2026-08-25T12:30:00+00:00',
          attachments: [],
        },
        {
          messageId: 'new-assistant-tail',
          role: 'assistant',
          content: 'A newer canonical answer',
          createdAt: '2026-08-25T12:31:00+00:00',
          attachments: [],
        },
      ],
    },
    chats: [summary],
  }
  await setChatState(canonicalState)
  await emitSnapshot(readySnapshot({
    chatId: summary.chatId,
    chatTitle: summary.title,
  }))
  await persistBackendForReload()
  await page.evaluate((chatId) => {
    window.localStorage.setItem(
      'elysia.retry-edit-draft.v1',
      JSON.stringify({
        chatId,
        userMessageId: 'old-user-tail',
        assistantMessageId: 'old-assistant-tail',
        text: 'Move this saved edit to the composer',
      }),
    )
  }, summary.chatId)

  await page.reload()
  await page.waitForFunction(() => (
    'elysiaDesktopTest' in window
    && (window as TestWindow).elysiaDesktopTest
      .getCalls()
      .some((call) => call.method === 'onBackendEvent.subscribe')
  ))

  await expect(page.getByLabel('Message Elysia')).toHaveValue(
    'Move this saved edit to the composer',
  )
  await expect(page.getByText(
    'The original retry target is no longer available, so its saved edit was moved to the Chat composer.',
    { exact: true },
  )).toBeVisible()
  await expect.poll(() => page.evaluate(() => (
    window.localStorage.getItem('elysia.retry-edit-draft.v1')
  ))).toBeNull()
  await expect(page.locator('[data-message-id="new-assistant-tail"]')
    .getByRole('button', { name: 'Edit & retry' })).toBeEnabled()
})

test('rescues a retry edit whose Chat was deleted', async () => {
  const summary = chatSummary('chat-survivor', 'Surviving Chat')
  const survivingState: ChatSessionState = {
    activeChat: { ...summary, messages: [] },
    chats: [summary],
  }
  await setChatState(survivingState)
  await emitSnapshot(readySnapshot({
    chatId: summary.chatId,
    chatTitle: summary.title,
  }))
  await persistBackendForReload()
  await page.evaluate(() => {
    window.localStorage.setItem(
      'elysia.retry-edit-draft.v1',
      JSON.stringify({
        chatId: 'chat-that-was-deleted',
        userMessageId: 'deleted-user-tail',
        assistantMessageId: 'deleted-assistant-tail',
        text: 'Rescue this edit from the deleted Chat',
      }),
    )
  })
  await page.addInitScript(() => {
    const originalRemoveItem = Storage.prototype.removeItem
    Storage.prototype.removeItem = function removeItem(key: string): void {
      if (key === 'elysia.retry-edit-draft.v1') {
        throw new DOMException('Test retry cleanup failure', 'SecurityError')
      }
      originalRemoveItem.call(this, key)
    }
  })

  await page.reload()
  await page.waitForFunction(() => (
    'elysiaDesktopTest' in window
    && (window as TestWindow).elysiaDesktopTest
      .getCalls()
      .some((call) => call.method === 'onBackendEvent.subscribe')
  ))

  await expect(page.getByLabel('Message Elysia')).toHaveValue(
    'Rescue this edit from the deleted Chat',
  )
  await expect.poll(() => page.evaluate(() => (
    window.localStorage.getItem('elysia.retry-edit-draft.v1')
  ))).toBe('null')
  await expect(page.getByRole('button', { name: 'Send message' })).toBeEnabled()

  await page.getByLabel('Message Elysia').press('Enter')
  await emitEvent({
    type: 'chat-complete',
    requestId: 'test-request-1',
    chatId: summary.chatId,
    reply: 'The rescued edit was answered once',
  })
  await persistBackendForReload()
  await page.reload()
  await page.waitForFunction(() => (
    'elysiaDesktopTest' in window
    && (window as TestWindow).elysiaDesktopTest
      .getCalls()
      .some((call) => call.method === 'onBackendEvent.subscribe')
  ))
  await expect(page.getByLabel('Message Elysia')).toHaveValue('')
  await expect(page.getByText(
    'Rescue this edit from the deleted Chat',
    { exact: true },
  )).toBeVisible()
})

test('preserves picker drafts on cancel and recovers a failed keyboard removal', async () => {
  await emitSnapshot(readySnapshot())
  await page.evaluate(() => {
    ;(window as TestWindow).elysiaDesktopTest.setSelectedFiles([
      { name: '课程笔记🙂.md', sizeBytes: 2_048, mediaType: 'text/markdown' },
      { name: 'reference.pdf', sizeBytes: 4_096, mediaType: 'application/pdf' },
    ])
  })
  await page.getByRole('button', { name: 'Choose files', exact: true }).click()

  const chatFiles = page.getByRole('region', { name: /This message/ })
  await expect(chatFiles.getByText('课程笔记🙂.md', { exact: true })).toBeVisible()
  await expect(chatFiles.getByText('reference.pdf', { exact: true })).toBeVisible()
  await expect(chatFiles.getByText('Ready', { exact: true })).toHaveCount(2)

  await page.evaluate(() => {
    const control = (window as TestWindow).elysiaDesktopTest
    control.setSelectedFiles([
      { name: 'too-large.bin', sizeBytes: 99_000_000 },
    ])
    control.failNextAttachmentAction('The file exceeds the local 16 MB limit.')
  })
  await chatFiles.getByRole('button', { name: 'Choose files', exact: true }).click()
  await expect(chatFiles.getByRole('alert')).toContainText('exceeds the local 16 MB limit')
  await expect(chatFiles.getByText('too-large.bin', { exact: true })).toHaveCount(0)
  await chatFiles.getByRole('button', { name: 'Dismiss attachment error' }).click()

  await page.evaluate(() => {
    ;(window as TestWindow).elysiaDesktopTest.cancelNextAttachmentPicker()
  })
  await chatFiles.getByRole('button', { name: 'Choose files', exact: true }).click()
  await expect(chatFiles.locator('.attachment-item')).toHaveCount(2)

  await page.evaluate(() => {
    ;(window as TestWindow).elysiaDesktopTest.failNextAttachmentAction(
      'The local copy could not be removed.',
    )
  })
  const firstRemove = chatFiles.getByRole('button', { name: 'Remove 课程笔记🙂.md' })
  await firstRemove.click()
  await expect(chatFiles.getByRole('alert')).toContainText(
    'The local copy could not be removed.',
  )
  await expect(firstRemove).toBeFocused()

  await firstRemove.click()
  await expect(chatFiles.getByText('课程笔记🙂.md', { exact: true })).toHaveCount(0)
  await expect(chatFiles.getByRole('button', { name: 'Remove reference.pdf' }))
    .toBeFocused()

  const calls = await getCalls()
  expect(calls.filter((call) => call.method === 'chooseAttachments').at(-1)?.args)
    .toEqual([{ kind: 'chat', id: 'chat-test' }])
  expect(calls.filter((call) => call.method === 'removeAttachment')).toHaveLength(2)
})

test('adds a dropped file, retains it after send rejection, and persists its chip', async () => {
  await emitSnapshot(readySnapshot())
  const startingUrl = page.url()
  const chatFiles = page.getByRole('region', { name: /This message/ })
  await expect(chatFiles.getByRole('button', {
    name: 'Choose files',
    exact: true,
  })).toBeEnabled()

  await chatFiles.evaluate((surface) => {
    const transfer = new DataTransfer()
    transfer.items.add(new File(
      ['# local only'],
      'dropped-notes.md',
      { type: 'text/markdown' },
    ))
    surface.dispatchEvent(new DragEvent('dragenter', {
      bubbles: true,
      cancelable: true,
      dataTransfer: transfer,
    }))
  })
  await expect(chatFiles).toHaveClass(/drag-active/)
  await expect(chatFiles.getByText(/Drop files into This message/)).toBeVisible()

  await chatFiles.evaluate((surface) => {
    const transfer = new DataTransfer()
    transfer.items.add(new File(
      ['# local only'],
      'dropped-notes.md',
      { type: 'text/markdown' },
    ))
    surface.dispatchEvent(new DragEvent('drop', {
      bubbles: true,
      cancelable: true,
      dataTransfer: transfer,
    }))
  })
  await expect(page).toHaveURL(startingUrl)
  await expect(chatFiles.getByText('dropped-notes.md', { exact: true })).toBeVisible()

  const attachmentId = await page.evaluate(() => {
    const calls = (window as TestWindow).elysiaDesktopTest.getCalls()
    const addCall = calls.find((call) => call.method === 'acceptDroppedAttachments')
    return addCall === undefined ? null : 'attachment_test_1'
  })
  expect(attachmentId).toBe('attachment_test_1')

  await page.evaluate(() => {
    ;(window as TestWindow).elysiaDesktopTest.failNextSend('Message storage is busy.')
  })
  const composer = page.getByLabel('Message Elysia')
  await composer.fill('Keep this file attached.')
  await composer.press('Enter')
  await expect(page.getByRole('alert')).toContainText('Message storage is busy.')
  await expect(composer).toHaveValue('Keep this file attached.')
  await expect(chatFiles.getByText('dropped-notes.md', { exact: true })).toBeVisible()

  await composer.press('Enter')
  await expect(composer).toHaveValue('')
  await expect(chatFiles.getByText('dropped-notes.md', { exact: true })).toHaveCount(0)
  const optimisticMessage = page.getByLabel('Message from you').last()
  await expect(optimisticMessage).toContainText('Keep this file attached.')
  await expect(optimisticMessage).toContainText('dropped-notes.md')
  await expect(optimisticMessage).toContainText('not read or indexed yet')

  const sendCalls = (await getCalls()).filter((call) => call.method === 'sendMessage')
  expect(sendCalls).toHaveLength(2)
  expect(sendCalls[0]?.args).toEqual([{
    chatId: 'chat-test',
    message: 'Keep this file attached.',
    attachmentIds: ['attachment_test_1'],
    useProjectKnowledge: false,
  }])
  expect(sendCalls[1]?.args).toEqual(sendCalls[0]?.args)

  await emitEvent({
    type: 'chat-complete',
    requestId: 'test-request-1',
    chatId: 'chat-test',
    reply: 'The file is stored, but I did not read it.',
  })
  await expect(page.getByLabel('Message from you').last()).toContainText(
    'dropped-notes.md',
  )
})

test('sends an attachment-only message through the canonical Chat request', async () => {
  await emitSnapshot(readySnapshot())
  await page.evaluate(() => {
    ;(window as TestWindow).elysiaDesktopTest.setSelectedFiles([
      { name: 'attachment-only.pdf', sizeBytes: 8_192, mediaType: 'application/pdf' },
    ])
  })
  await page.getByRole('button', { name: 'Choose files', exact: true }).click()

  const composer = page.getByLabel('Message Elysia')
  await expect(composer).toHaveValue('')
  const sendButton = page.getByRole('button', { name: 'Send message' })
  await expect(sendButton).toBeEnabled()
  await sendButton.click()

  const sendCall = (await getCalls()).find((call) => call.method === 'sendMessage')
  expect(sendCall?.args).toEqual([{
    chatId: 'chat-test',
    message: '',
    attachmentIds: ['attachment_test_1'],
    useProjectKnowledge: false,
  }])
  const optimisticMessage = page.getByLabel('Message from you').last()
  await expect(optimisticMessage).toContainText('attachment-only.pdf')
  await expect(optimisticMessage).toContainText('not read or indexed yet')
})

test('reloads a recovered Chat draft after a picker cancellation race', async () => {
  await emitSnapshot(readySnapshot())
  await page.evaluate(() => {
    ;(window as TestWindow).elysiaDesktopTest.setSelectedFiles([
      { name: 'recover-after-error.md', sizeBytes: 512, mediaType: 'text/markdown' },
    ])
  })
  await page.getByRole('button', { name: 'Choose files', exact: true }).click()
  const chatFiles = page.getByRole('region', { name: /This message/ })
  await page.getByLabel('Message Elysia').fill('Start a failing generation')
  await page.getByLabel('Message Elysia').press('Enter')
  await expect(chatFiles.getByText('recover-after-error.md', { exact: true })).toHaveCount(0)

  await page.evaluate(() => {
    const control = (window as TestWindow).elysiaDesktopTest
    control.setAttachmentActionDelay(true)
    control.cancelNextAttachmentPicker()
  })
  await page.getByRole('button', { name: 'Choose files', exact: true }).click()
  await expect.poll(async () => page.evaluate(() => (
    (window as TestWindow).elysiaDesktopTest.getPendingAttachmentActionCount()
  ))).toBe(1)

  await emitEvent({
    type: 'chat-error',
    requestId: 'test-request-1',
    chatId: 'chat-test',
    code: 'chat.cancelled',
    message: 'Generation stopped.',
    retryable: false,
  })
  await page.evaluate(() => {
    const control = (window as TestWindow).elysiaDesktopTest
    control.releaseNextAttachmentAction()
    control.setAttachmentActionDelay(false)
  })

  await expect(chatFiles.getByText('recover-after-error.md', { exact: true })).toBeVisible()
})

test('compensates when a cancelled picker supersedes a pending canonical list', async () => {
  await page.evaluate(() => {
    const control = (window as TestWindow).elysiaDesktopTest
    control.setAttachmentState({
      scope: { kind: 'chat', id: 'chat-test' },
      attachments: [{
        attachmentId: 'attachment_load_first',
        fileName: 'recover-after-stale-list.md',
        mediaType: 'text/markdown',
        sizeBytes: 768,
        status: 'ready',
      }],
      maxFileBytes: 16_777_216,
      maxFileCount: 10,
    })
    control.setAttachmentActionDelay(true)
  })
  await emitSnapshot(readySnapshot())
  await expect.poll(async () => page.evaluate(() => (
    (window as TestWindow).elysiaDesktopTest.getPendingAttachmentActionCount()
  ))).toBe(1)

  const chatFiles = page.getByRole('region', { name: /This message/ })
  await expect(chatFiles.getByText(
    'recover-after-stale-list.md',
    { exact: true },
  )).toHaveCount(0)
  await page.evaluate(() => {
    ;(window as TestWindow).elysiaDesktopTest.cancelNextAttachmentPicker()
  })
  await chatFiles.getByRole('button', { name: 'Choose files', exact: true }).click()
  await expect.poll(async () => page.evaluate(() => (
    (window as TestWindow).elysiaDesktopTest.getPendingAttachmentActionCount()
  ))).toBe(2)

  await page.evaluate(() => {
    ;(window as TestWindow).elysiaDesktopTest.releaseNextAttachmentAction()
  })
  await expect.poll(async () => page.evaluate(() => (
    (window as TestWindow).elysiaDesktopTest.getPendingAttachmentActionCount()
  ))).toBe(1)
  await page.evaluate(() => {
    const control = (window as TestWindow).elysiaDesktopTest
    control.releaseNextAttachmentAction()
    control.setAttachmentActionDelay(false)
  })

  await expect.poll(async () => (
    (await getCalls()).filter((call) => call.method === 'listAttachments').length
  )).toBe(2)
  await expect(chatFiles.getByText(
    'recover-after-stale-list.md',
    { exact: true },
  )).toBeVisible()
})

test('isolates an A stream, drafts, and file previews while viewing Chat B', async () => {
  const chatA = chatSummary('chat-a', 'Chat A')
  const chatB = chatSummary('chat-b', 'Chat B')
  await setChatState({
    activeChat: { ...chatA, messages: [] },
    chats: [chatA, chatB],
  })
  await emitSnapshot(readySnapshot({
    chatId: chatA.chatId,
    chatTitle: chatA.title,
  }))

  const composer = page.getByLabel('Message Elysia')
  await composer.fill('Start Chat A generation')
  await composer.press('Enter')
  await expect(page.getByRole('button', { name: 'Stop generation' })).toBeVisible()
  await composer.fill('Unsent draft for A')
  await page.evaluate(() => {
    ;(window as TestWindow).elysiaDesktopTest.setSelectedFiles([
      { name: 'chat-a.txt', sizeBytes: 10 },
    ])
  })
  await page.getByRole('button', { name: 'Choose files' }).click()

  await page.getByRole('button', { name: 'Open chat Chat B' }).click()
  await expect(page.locator('#chat-title')).toHaveText('Chat B')
  await expect(page.getByLabel('Message Elysia')).toHaveValue('')
  await expect(page.getByText('chat-a.txt', { exact: true })).toHaveCount(0)
  await expect(page.getByRole('button', { name: 'Stop generation' })).toHaveCount(0)
  await expect(page.getByRole('button', { name: 'Send message' })).toBeDisabled()

  await page.getByLabel('Message Elysia').fill('Unsent draft for B')
  await page.evaluate(() => {
    ;(window as TestWindow).elysiaDesktopTest.setSelectedFiles([
      { name: 'chat-b.txt', sizeBytes: 20 },
    ])
  })
  await page.getByRole('button', { name: 'Choose files' }).click()
  await emitEvent({
    type: 'chat-chunk',
    requestId: 'stale-request',
    chatId: chatA.chatId,
    chunk: 'STALE A CONTENT',
  })
  await emitEvent({
    type: 'chat-chunk',
    requestId: 'test-request-1',
    chatId: chatA.chatId,
    chunk: 'Hidden Chat A stream',
  })
  await expect(page.getByText('Hidden Chat A stream', { exact: true })).toHaveCount(0)

  await page.getByRole('button', { name: 'Open chat Chat A' }).click()
  await expect(page.locator('#chat-title')).toHaveText('Chat A')
  await expect(page.getByLabel('Message Elysia')).toHaveValue('Unsent draft for A')
  await expect(page.getByText('chat-a.txt', { exact: true })).toBeVisible()
  await expect(page.getByText('chat-b.txt', { exact: true })).toHaveCount(0)
  await expect(page.getByText('Hidden Chat A stream', { exact: true })).toBeVisible()
  await expect(page.getByText('STALE A CONTENT', { exact: true })).toHaveCount(0)
  await expect(page.getByRole('button', { name: 'Stop generation' })).toBeVisible()

  await page.getByRole('button', { name: 'Open chat Chat B' }).click()
  await expect(page.getByLabel('Message Elysia')).toHaveValue('Unsent draft for B')
  await expect(page.getByText('chat-b.txt', { exact: true })).toBeVisible()
  await expect(page.getByText('chat-a.txt', { exact: true })).toHaveCount(0)

  await emitEvent({
    type: 'progress',
    requestId: 'test-request-1',
    operation: 'chat.generate',
    completed: 0,
    total: null,
    message: 'Hidden Chat A progress',
  })
  await expect(page.getByText('Hidden Chat A progress', { exact: true })).toHaveCount(0)
  await emitEvent({
    type: 'chat-complete',
    requestId: 'test-request-1',
    chatId: chatA.chatId,
    reply: 'Completed Chat A answer',
  })
  await expect(page.locator('#chat-title')).toHaveText('Chat B')
  await expect(page.getByText('Completed Chat A answer', { exact: true })).toHaveCount(0)
  await expect(page.getByRole('button', { name: 'Send message' })).toBeEnabled()

  await page.getByRole('button', { name: 'Open chat Chat A' }).click()
  await expect(page.getByText('Completed Chat A answer', { exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Stop generation' })).toHaveCount(0)
})

test('keeps global shortcuts inside native modal boundaries', async () => {
  await emitSnapshot(readySnapshot())
  await setWindowAndZoom(960, 640, 2)
  await page.getByRole('button', { name: 'Show navigation' }).click()
  const sidebar = page.locator('#app-sidebar')
  await page.getByRole('button', {
    name: 'More actions for Elysia Chat',
  }).click()
  await page.getByRole('menuitem', { name: 'Rename' }).click()

  const dialog = page.getByRole('dialog', { name: 'Rename Chat' })
  const input = dialog.getByLabel('Chat title')
  await expect(input).toBeFocused()
  for (const key of ['b', 'k', ',']) {
    await pressControlShortcut(key)
    await expect(dialog).toBeVisible()
    await expect(sidebar).toHaveAttribute('aria-hidden', 'false')
    await expect(input).toBeFocused()
  }
  await expect(page.getByPlaceholder('Search chats')).toHaveCount(0)
  await expect(page.getByRole('heading', { name: 'Settings' })).toHaveCount(0)

  await dialog.getByRole('button', { name: 'Save' }).focus()
  await page.keyboard.press('Tab')
  await expect(input).toBeFocused()
  await page.keyboard.press('Shift+Tab')
  await expect(dialog.getByRole('button', { name: 'Save' })).toBeFocused()
  await page.keyboard.press('Escape')
  await expect(dialog).toHaveCount(0)
  await expect(page.getByRole('button', {
    name: 'More actions for Elysia Chat',
  })).toBeFocused()
})

test('shares one decoded Elysia state atlas without disturbing Chat', async () => {
  await emitSnapshot(readySnapshot({
    capabilities: [
      'chat.stream',
      'voice.settings',
      'voice.capture',
      'voice.transcription',
    ],
  }))
  const composer = page.getByLabel('Message Elysia')
  await composer.fill('Keep this draft while viewing Elysia')
  await clearCalls()

  await page.getByRole('button', { name: 'Expand Elysia panel' }).click()
  const panel = page.locator('.character-panel')
  await expect(panel).toHaveAttribute('data-character-state', 'idle')
  const panelImage = panel.getByRole('img', {
    name: 'Elysia character portrait',
    exact: true,
  })
  await expect(panelImage).toBeVisible()
  await expect.poll(() => panelImage.evaluate((element) => {
    const image = element as HTMLImageElement
    return image.complete && image.naturalWidth > 0 && image.naturalHeight > 0
  })).toBe(true)
  const panelSource = await panelImage.evaluate(
    (element) => (element as HTMLImageElement).currentSrc,
  )
  expect(panelSource).toContain('/character/elysia-state-atlas.png')
  await expect(panel.locator('.character-artwork'))
    .toHaveAttribute('data-character-asset', 'atlas')
  await expect(panel.locator('.character-artwork'))
    .toHaveAttribute('data-character-expression', 'soft-smile')
  await expect(panel.locator('.character-artwork'))
    .toHaveAttribute('data-character-action', 'resting')
  await expect(page.getByText('Character artwork', { exact: true }))
    .toHaveCount(0)
  await expect(page.getByText(
    'Visual assets arrive in a later character feature.',
    { exact: true },
  )).toHaveCount(0)

  await panel.getByRole('button', {
    name: 'Close Elysia character panel',
  }).click()
  await expect(panel).toHaveCount(0)
  await expect(page.locator('#chat-title')).toHaveText('Elysia Chat')
  await expect(composer).toHaveValue('Keep this draft while viewing Elysia')
  await expect(composer).toBeEnabled()

  await page.getByRole('button', { name: 'Start voice' }).click()
  const call = page.getByRole('main', { name: 'Voice capture' })
  await expect(call).toHaveAttribute('data-character-state', 'idle')
  const callImage = call.getByRole('img', {
    name: 'Elysia character portrait',
    exact: true,
  })
  await expect(callImage).toBeVisible()
  await expect.poll(() => callImage.evaluate((element) => {
    const image = element as HTMLImageElement
    return image.complete && image.naturalWidth > 0 && image.naturalHeight > 0
  })).toBe(true)
  await expect.poll(() => callImage.evaluate(
    (element) => (element as HTMLImageElement).currentSrc,
  )).toBe(panelSource)
  await expect.poll(() => call.locator('.character-artwork-frame').evaluate(
    (element) => {
      const bounds = element.getBoundingClientRect()
      return Math.abs((bounds.width / bounds.height) - 0.75)
    },
  )).toBeLessThan(0.005)
  await expect(call.getByText('Character artwork', { exact: true }))
    .toHaveCount(0)
  await call.getByRole('button', { name: 'Close voice' }).click()

  await composer.focus()
  await expect(composer).toBeFocused()
  await composer.press('Enter')
  await expect.poll(async () => (
    (await getCalls()).filter((callRecord) => (
      callRecord.method === 'sendMessage'
    )).length
  )).toBe(1)
})

test('projects Chat activity and failure through the shared character state', async () => {
  await emitSnapshot(readySnapshot())
  await page.getByRole('button', { name: 'Expand Elysia panel' }).click()
  const panel = page.locator('.character-panel')
  const artwork = panel.locator('.character-artwork')
  const captionState = panel.locator('.character-caption > strong')
  const composer = page.getByLabel('Message Elysia')
  await expect(panel).toHaveAttribute('data-character-state', 'idle')
  await expect(artwork).toHaveAttribute('data-character-state', 'idle')
  await expect(artwork).toHaveAttribute('data-character-expression', 'soft-smile')
  await expect(artwork).toHaveAttribute('data-character-action', 'resting')
  await expect(captionState).toHaveText('Ready')
  await expect(panel.locator('.soft-status')).toHaveText('Ready')

  await composer.fill('Show the conversational state.')
  await composer.press('Enter')
  await expect(panel).toHaveAttribute('data-character-state', 'thinking')
  await expect(artwork).toHaveAttribute('data-character-state', 'thinking')
  await expect(artwork).toHaveAttribute('data-character-expression', 'focused')
  await expect(artwork).toHaveAttribute('data-character-action', 'thinking')
  await expect(captionState).toHaveText('Thinking')
  await emitEvent({
    type: 'chat-error',
    requestId: 'test-request-1',
    chatId: 'chat-test',
    code: 'generation.failed',
    message: 'The character state test failed safely.',
    retryable: true,
  })
  await expect(panel).toHaveAttribute('data-character-state', 'error')
  await expect(artwork).toHaveAttribute('data-character-state', 'error')
  await expect(artwork).toHaveAttribute('data-character-expression', 'concerned')
  await expect(artwork).toHaveAttribute('data-character-action', 'alert')
  await expect(captionState).toHaveText('Needs attention')
  await expect(panel.locator('.soft-status')).toHaveText('Ready')
})

test('uses only the active closed emotion to select a reviewed expression', async () => {
  const initialSettings = desktopSettingsState()
  const happySettings: DesktopSettingsValues = {
    ...initialSettings.settings,
    voiceEmotion: 'happy',
  }
  await setSettingsState({
    ...initialSettings,
    settings: happySettings,
    activeSettings: happySettings,
  })
  await openSettings()
  await expect(page.getByLabel('Voice emotion')).toHaveValue('happy')
  await page.getByRole('button', { name: 'Back to chat' }).click()
  await page.getByRole('button', { name: 'Expand Elysia panel' }).click()

  const artwork = page.locator('.character-panel .character-artwork')
  await expect(artwork).toHaveAttribute('data-character-emotion', 'happy')
  await expect(artwork).toHaveAttribute('data-character-expression', 'happy')
  await expect(artwork).toHaveAttribute(
    'data-character-asset',
    'expression-atlas',
  )
  await expect(artwork.getByRole('img', { name: 'Elysia happy expression' }))
    .toHaveAttribute('src', './character/elysia-expression-atlas.png')
})

test('keeps the Chat character speaking for exact managed playback', async () => {
  await emitSnapshot(readySnapshot({
    capabilities: ['chat.stream', 'voice.speech'],
  }))
  await page.getByRole('button', { name: 'Expand Elysia panel' }).click()
  const panel = page.locator('.character-panel')
  const artwork = panel.locator('.character-artwork')
  const composer = page.getByLabel('Message Elysia')

  await composer.fill('Show trusted playback.')
  await composer.press('Enter')
  await expect.poll(async () => (
    (await getCalls()).filter((call) => call.method === 'sendMessage').length
  )).toBe(1)
  await emitEvent({
    type: 'voice-speech-status',
    kind: 'playing',
    requestId: 'test-request-1',
    chatId: 'chat-test',
    sequence: 0,
  })
  await expect(panel).toHaveAttribute('data-character-state', 'speaking')
  await expect(artwork).toHaveAttribute('data-character-state', 'speaking')
  await expect(artwork).toHaveAttribute('data-character-asset', 'speech-atlas')
  await expect(artwork.getByRole('img')).toHaveAttribute(
    'src',
    './character/elysia-speech-atlas.png',
  )
  await artwork.locator('.character-artwork-speech-atlas').evaluate(
    (element) => {
      ;(element as HTMLImageElement).src = 'file:///missing-speech-atlas.png'
    },
  )
  await expect(artwork).toHaveAttribute(
    'data-character-asset',
    'expression-atlas',
  )
  await artwork.locator('.character-artwork-expression-atlas').evaluate((element) => {
    ;(element as HTMLImageElement).src = 'file:///missing-expression-atlas.png'
  })
  await expect(artwork).toHaveAttribute('data-character-asset', 'atlas')

  await emitEvent({
    type: 'voice-speech-status',
    kind: 'played',
    requestId: 'test-request-1',
    chatId: 'chat-test',
    sequence: 0,
  })
  await expect(panel).toHaveAttribute('data-character-state', 'thinking')
  await emitEvent({
    type: 'chat-complete',
    requestId: 'test-request-1',
    chatId: 'chat-test',
    reply: 'Trusted playback finished.',
  })
  await expect(panel).toHaveAttribute('data-character-state', 'idle')
})

test('does not revive playback that completed before the Chat acknowledgement', async () => {
  await emitSnapshot(readySnapshot({
    capabilities: ['chat.stream', 'voice.speech'],
  }))
  await page.getByRole('button', { name: 'Expand Elysia panel' }).click()
  await page.evaluate(() => {
    ;(window as TestWindow).elysiaDesktopTest.setChatActionDelay(true)
  })

  const panel = page.locator('.character-panel')
  const composer = page.getByLabel('Message Elysia')
  await composer.fill('Complete speech before acknowledgement.')
  await composer.press('Enter')
  await expect.poll(() => page.evaluate(() => (
    (window as TestWindow).elysiaDesktopTest.getPendingChatActionCount()
  ))).toBe(1)
  await emitEvents([
    {
      type: 'voice-speech-status',
      kind: 'playing',
      requestId: 'test-request-1',
      chatId: 'chat-test',
      sequence: 0,
    },
    {
      type: 'voice-speech-status',
      kind: 'terminal',
      requestId: 'test-request-1',
      chatId: 'chat-test',
      state: 'completed',
    },
  ])
  await expect(panel).not.toHaveAttribute('data-character-state', 'speaking')

  await page.evaluate(() => {
    const control = (window as TestWindow).elysiaDesktopTest
    control.releaseNextChatAction()
    control.setChatActionDelay(false)
  })
  await expect(panel).toHaveAttribute('data-character-state', 'thinking')
  await emitEvent({
    type: 'voice-speech-status',
    kind: 'playing',
    requestId: 'test-request-1',
    chatId: 'chat-test',
    sequence: 1,
  })
  await expect(panel).toHaveAttribute('data-character-state', 'thinking')
  await emitEvent({
    type: 'chat-complete',
    requestId: 'test-request-1',
    chatId: 'chat-test',
    reply: 'The early playback stayed closed.',
  })
  await expect(panel).toHaveAttribute('data-character-state', 'idle')
})

test('scopes Work and Knowledge character activity to the current Project Chat', async () => {
  const project = projectSummary('project-character-state', 'Character State')
  const workChat = chatSummary('chat-character-state', 'Character Work', {
    mode: 'work',
    projectId: project.projectId,
  })
  await setProjectState({
    activeProject: { ...project, chatCount: 1 },
    projects: [{ ...project, chatCount: 1 }],
    chatState: {
      activeChat: { ...workChat, messages: [] },
      chats: [workChat],
    },
  })
  await emitSnapshot(readySnapshot({
    capabilities: ['chat.stream', 'knowledge.management'],
    chatId: workChat.chatId,
    chatTitle: workChat.title,
  }))
  await expect(page.locator('#chat-title')).toHaveText(workChat.title)
  await page.getByRole('button', { name: 'Expand Elysia panel' }).click()
  const panel = page.locator('.character-panel')
  const artwork = panel.locator('.character-artwork')
  const captionState = panel.locator('.character-caption > strong')

  const composer = page.getByLabel('Message Elysia')
  await composer.fill('Show the Work state.')
  await composer.press('Enter')
  await expect(panel).toHaveAttribute('data-character-state', 'working')
  await expect(artwork).toHaveAttribute('data-character-state', 'working')
  await expect(artwork).toHaveAttribute('data-character-expression', 'focused')
  await expect(artwork).toHaveAttribute('data-character-action', 'working')
  await expect(captionState).toHaveText('Working')
  await emitEvent({
    type: 'chat-complete',
    requestId: 'test-request-1',
    chatId: workChat.chatId,
    reply: 'The Work state is complete.',
  })
  await expect(panel).toHaveAttribute('data-character-state', 'idle')

  await emitSnapshot(readySnapshot({
    revision: 2,
    capabilities: ['chat.stream', 'knowledge.management'],
    chatId: workChat.chatId,
    chatTitle: workChat.title,
    activeKnowledgeOperation: {
      requestId: 'knowledge-other-project',
      projectId: 'project-other-character-state',
      cancellable: true,
    },
  }))
  await expect(panel).toHaveAttribute('data-character-state', 'idle')
  await expect(artwork).toHaveAttribute('data-character-state', 'idle')

  await emitSnapshot(readySnapshot({
    revision: 3,
    capabilities: ['chat.stream', 'knowledge.management'],
    chatId: workChat.chatId,
    chatTitle: workChat.title,
    activeKnowledgeOperation: {
      requestId: 'knowledge-current-project',
      projectId: project.projectId,
      cancellable: true,
    },
  }))
  await expect(panel).toHaveAttribute('data-character-state', 'working')
  await expect(artwork).toHaveAttribute('data-character-state', 'working')
  await expect(captionState).toHaveText('Working')
  await emitEvent({
    type: 'knowledge-operation-error',
    requestId: 'knowledge-current-project',
    projectId: project.projectId,
    code: 'knowledge.operation_failed',
    message: 'The Project source operation failed safely.',
    retryable: true,
  })
  await expect(panel).toHaveAttribute('data-character-state', 'error')
  await expect(artwork).toHaveAttribute('data-character-state', 'error')
  await expect(captionState).toHaveText('Needs attention')
})

test('keeps a background Chat generation out of the visible character state', async () => {
  await emitSnapshot(readySnapshot({
    activeGeneration: {
      requestId: 'background-character-generation',
      chatId: 'chat-background-character',
      kind: 'send',
      userText: 'Background work',
      reply: '',
      stopping: false,
    },
  }))
  await page.getByRole('button', { name: 'Expand Elysia panel' }).click()
  const panel = page.locator('.character-panel')
  await expect(panel).toHaveAttribute('data-character-state', 'idle')
  await expect(panel.locator('.character-artwork'))
    .toHaveAttribute('data-character-state', 'idle')
})

test('projects Voice listening without letting device state choose artwork', async () => {
  await installAudioMock()
  await emitSnapshot(readySnapshot({
    capabilities: [
      'chat.stream',
      'voice.settings',
      'voice.capture',
      'voice.transcription',
    ],
  }))
  await page.getByRole('button', { name: 'Start voice' }).click()
  const call = page.getByRole('main', { name: 'Voice capture' })
  const artwork = call.locator('.character-artwork')
  await expect(call).toHaveAttribute('data-character-state', 'idle')
  await expect(artwork).toHaveAttribute('data-character-state', 'idle')

  await page.getByRole('button', { name: 'Start microphone' }).click()
  await expect(call).toHaveAttribute('data-character-state', 'listening')
  await expect(artwork).toHaveAttribute('data-character-state', 'listening')
  await expect(artwork).toHaveAttribute('data-character-expression', 'attentive')
  await expect(artwork).toHaveAttribute('data-character-action', 'listening')
  await expect(page.getByText('Listening for speech', { exact: true }))
    .toBeVisible()

  await page.getByRole('button', { name: 'Cancel capture' }).click()
  await expect(call).toHaveAttribute('data-character-state', 'idle')
  await expect(artwork).toHaveAttribute('data-character-state', 'idle')
})

test('keeps character and Voice controls usable through both image fallbacks', async () => {
  await emitSnapshot(readySnapshot({
    capabilities: [
      'chat.stream',
      'voice.settings',
      'voice.capture',
      'voice.transcription',
    ],
  }))
  await page.getByRole('button', { name: 'Expand Elysia panel' }).click()
  const panel = page.locator('.character-panel')
  const panelImage = panel.getByRole('img', {
    name: 'Elysia character portrait',
    exact: true,
  })
  await panelImage.evaluate((element) => {
    ;(element as HTMLImageElement).src = 'file:///missing-panel-atlas.png'
  })
  const panelArtwork = panel.locator('.character-artwork')
  await expect(panelArtwork).toHaveAttribute('data-character-asset', 'portrait')
  const panelPortrait = panel.getByRole('img', {
    name: 'Elysia character portrait',
    exact: true,
  })
  await expect.poll(() => panelPortrait.evaluate(
    (element) => (element as HTMLImageElement).currentSrc,
  )).toContain('/character/elysia-portrait.png')
  await panelPortrait.evaluate((element) => {
    ;(element as HTMLImageElement).src = 'file:///missing-panel-portrait.png'
  })
  await expect(panel.getByRole('img', {
    name: 'Elysia character portrait unavailable',
  })).toBeVisible()
  await expect(panelArtwork).toHaveAttribute('data-character-asset', 'unavailable')
  await expect(panelArtwork)
    .toHaveAttribute('data-character-state', 'idle')
  await expect(panelImage).toHaveCount(0)

  const closePanel = panel.getByRole('button', {
    name: 'Close Elysia character panel',
  })
  await expect(closePanel).toBeEnabled()
  await closePanel.click()
  await expect(panel).toHaveCount(0)
  await expect(page.getByLabel('Message Elysia')).toBeEnabled()

  await page.getByRole('button', { name: 'Start voice' }).click()
  const call = page.getByRole('main', { name: 'Voice capture' })
  const callImage = call.getByRole('img', {
    name: 'Elysia character portrait',
    exact: true,
  })
  await callImage.evaluate((element) => {
    ;(element as HTMLImageElement).src = 'file:///missing-call-atlas.png'
  })
  const callArtwork = call.locator('.character-artwork')
  await expect(callArtwork).toHaveAttribute('data-character-asset', 'portrait')
  const callPortrait = call.getByRole('img', {
    name: 'Elysia character portrait',
    exact: true,
  })
  await callPortrait.evaluate((element) => {
    ;(element as HTMLImageElement).src = 'file:///missing-call-portrait.png'
  })
  await expect(call.getByRole('img', {
    name: 'Elysia character portrait unavailable',
  })).toBeVisible()
  await expect(callArtwork).toHaveAttribute('data-character-asset', 'unavailable')
  await expect(callImage).toHaveCount(0)
  await expect(call.getByRole('button', { name: 'Start microphone' }))
    .toBeEnabled()
  await call.getByRole('button', { name: 'Close voice' }).click()
  await expect(call).toHaveCount(0)
  await expect(page.getByLabel('Message Elysia')).toBeEnabled()
})

test('makes the compact character panel modal and directly dismissible', async () => {
  await emitSnapshot(readySnapshot())
  await setWindowAndZoom(960, 640, 2)
  const trigger = page.getByRole('button', { name: 'Expand Elysia panel' })
  await trigger.focus()
  await trigger.click()

  const panel = page.getByRole('dialog', { name: 'Here with you' })
  const close = panel.getByRole('button', {
    name: 'Close Elysia character panel',
  })
  await expect(panel).toBeVisible()
  await expect(panel.getByRole('img', {
    name: 'Elysia character portrait',
    exact: true,
  })).toBeVisible()
  await expect(close).toBeFocused()
  await expect(page.locator('#main-content')).toHaveAttribute('inert', '')

  await page.keyboard.press('Tab')
  await expect(close).toBeFocused()
  for (const key of ['b', 'k', ',']) {
    await pressControlShortcut(key)
    await expect(panel).toBeVisible()
    await expect(close).toBeFocused()
  }

  await page.keyboard.press('Escape')
  await expect(panel).toHaveCount(0)
  await expect(trigger).toBeFocused()
})

test('exposes field errors and restores focus across Settings navigation', async () => {
  await emitSnapshot(readySnapshot())
  await pressControlShortcut(',')
  const heading = page.getByRole('heading', { name: 'Settings', exact: true })
  await expect(heading).toBeFocused()

  const origin = page.getByLabel('Ollama origin')
  await origin.fill('http://localhost:11434/private')
  await expect(origin).toHaveAttribute('aria-invalid', 'true')
  await expect(origin).toHaveAccessibleDescription(
    /without credentials or a path/i,
  )
  await expect(page.getByText(
    'Enter an HTTP or HTTPS Ollama origin without credentials or a path.',
    { exact: true },
  )).toBeVisible()
  await expect(page.getByRole('button', { name: 'Save changes' })).toBeDisabled()

  const back = page.getByRole('button', { name: 'Back to chat' })
  page.once('dialog', async (dialog) => { await dialog.accept() })
  await back.focus()
  await back.press('Enter')
  await expect(page.getByLabel('Message Elysia')).toBeFocused()
})

test('moves focus into and back out of Chat selection mode', async () => {
  await emitSnapshot(readySnapshot())
  const trigger = page.getByRole('button', { name: 'Select chats' })
  await trigger.focus()
  await trigger.press('Enter')
  await expect(page.getByRole('checkbox', {
    name: 'Select all visible',
  })).toBeFocused()
  await page.keyboard.press('Escape')
  await expect(trigger).toBeFocused()
})

test('keeps focus visible in Windows forced-colors mode', async () => {
  await page.emulateMedia({ forcedColors: 'active' })
  await emitSnapshot(readySnapshot())
  const composer = page.getByLabel('Message Elysia')
  await composer.focus()
  const composerOutline = await page.locator('.composer-card').evaluate(
    (element) => ({
      style: getComputedStyle(element).outlineStyle,
      width: getComputedStyle(element).outlineWidth,
    }),
  )
  expect(composerOutline.style).not.toBe('none')
  expect(composerOutline.width).not.toBe('0px')

  await pressControlShortcut('k')
  const searchOutline = await page.locator('.search-box').evaluate(
    (element) => ({
      style: getComputedStyle(element).outlineStyle,
      width: getComputedStyle(element).outlineWidth,
    }),
  )
  expect(searchOutline.style).not.toBe('none')
  expect(searchOutline.width).not.toBe('0px')
})

test('counts Chat rename limits by Unicode code point and contains long dialogs', async () => {
  const astralTitle = '🙂'.repeat(200)
  await emitSnapshot(readySnapshot())
  await page.getByRole('button', {
    name: 'More actions for Elysia Chat',
  }).click()
  await page.getByRole('menuitem', { name: 'Rename' }).click()
  const renameDialog = page.getByRole('dialog', { name: 'Rename Chat' })
  const titleInput = renameDialog.getByLabel('Chat title')
  await titleInput.fill(`${astralTitle}🙂`)
  await expect(titleInput).toHaveValue(astralTitle)
  await expect(renameDialog.getByText('200/200', { exact: true })).toBeVisible()
  await renameDialog.getByRole('button', { name: 'Save' }).click()
  await expect.poll(async () => (
    (await getCalls()).find((call) => call.method === 'renameChat')?.args
  )).toEqual([{ chatId: 'chat-test', title: astralTitle }])

  await setWindowAndZoom(960, 640, 2)
  await page.getByRole('button', { name: 'Show navigation' }).click()
  await page.getByRole('button', {
    name: `More actions for ${astralTitle}`,
  }).click()
  await page.getByRole('menuitem', { name: 'Delete' }).click()
  const deleteDialog = page.getByRole('dialog', {
    name: new RegExp(`Delete`),
  })
  const geometry = await deleteDialog.locator('.chat-action-dialog-card').evaluate(
    (element) => ({
      clientWidth: element.clientWidth,
      scrollWidth: element.scrollWidth,
    }),
  )
  expect(geometry.scrollWidth).toBeLessThanOrEqual(geometry.clientWidth + 1)
  await expect(deleteDialog.getByRole('button', { name: 'Delete Chat' })).toBeVisible()
})

test('does not send while a Chinese IME composition is active', async () => {
  await emitSnapshot(readySnapshot())
  const composer = page.getByLabel('Message Elysia')
  await composer.fill('你好，爱莉希雅')
  await composer.evaluate((element) => {
    element.dispatchEvent(new KeyboardEvent('keydown', {
      key: 'Enter',
      bubbles: true,
      cancelable: true,
      isComposing: true,
    }))
  })
  expect((await getCalls()).some((call) => call.method === 'sendMessage')).toBe(false)
  await composer.press('Enter')
  await expect.poll(async () => (
    (await getCalls()).filter((call) => call.method === 'sendMessage').length
  )).toBe(1)
})

test('keeps Chat drafts and canonical history across renderer reload', async () => {
  const summary = chatSummary('chat-reload', 'Reloaded Chat', {
    messageCount: 1,
  })
  await setProjectState({
    activeProject: null,
    projects: [],
    chatState: {
      activeChat: {
        ...summary,
        messages: [{
          messageId: 'message-before-reload',
          role: 'assistant',
          content: 'Persisted before renderer refresh.',
          createdAt: '2026-08-25T12:30:00+00:00',
          attachments: [],
        }],
      },
      chats: [summary],
    },
  })
  await emitSnapshot(readySnapshot({
    chatId: summary.chatId,
    chatTitle: summary.title,
  }))
  await page.getByLabel('Message Elysia').fill('Unsent 中文 draft survives reload')
  await expect.poll(() => page.evaluate(() => (
    window.localStorage.getItem('elysia.chat-drafts.v1')
  ))).toContain('Unsent 中文 draft survives reload')
  await persistBackendForReload()

  await page.reload()
  await page.waitForFunction(() => (
    'elysiaDesktopTest' in window
    && (window as TestWindow).elysiaDesktopTest
      .getCalls()
      .some((call) => call.method === 'onBackendEvent.subscribe')
  ))
  await expect(page.locator('#chat-title')).toHaveText(summary.title)
  await expect(page.getByText(
    'Persisted before renderer refresh.',
    { exact: true },
  )).toBeVisible()
  await expect(page.getByLabel('Message Elysia')).toHaveValue(
    'Unsent 中文 draft survives reload',
  )
})

test('resumes an Electron-owned stream after renderer reload', async () => {
  await emitSnapshot(readySnapshot({
    capabilities: [
      'chat.stream',
      'voice.settings',
      'voice.capture',
      'voice.transcription',
      'voice.speech',
      'voice.speech.cancel',
    ],
  }))
  const composer = page.getByLabel('Message Elysia')
  await composer.fill('Continue safely after refresh')
  await composer.press('Enter')
  await emitEvents([
    {
      type: 'chat-chunk',
      requestId: 'test-request-1',
      chatId: 'chat-test',
      chunk: 'First ',
    },
    {
      type: 'chat-chunk',
      requestId: 'test-request-1',
      chatId: 'chat-test',
      chunk: 'half',
    },
  ])
  await persistBackendForReload()
  await page.reload()
  await page.waitForFunction(() => (
    'elysiaDesktopTest' in window
    && (window as TestWindow).elysiaDesktopTest
      .getCalls()
      .some((call) => call.method === 'onBackendEvent.subscribe')
  ))

  const reply = page.getByLabel('Message from Elysia').last()
  await expect(reply).toContainText('First half')
  await expect(page.getByRole('button', { name: 'Stop generation' })).toBeVisible()
  await clearCalls()
  await emitEvent({
    type: 'voice-speech-status',
    kind: 'playing',
    requestId: 'test-request-1',
    chatId: 'chat-test',
    sequence: 0,
  })
  await emitEvent({
    type: 'chat-chunk',
    requestId: 'test-request-1',
    chatId: 'chat-test',
    chunk: ' and second half.',
  })
  await emitEvent({
    type: 'chat-complete',
    requestId: 'test-request-1',
    chatId: 'chat-test',
    reply: 'First half and second half.',
  })
  await expect(page.getByText(
    'First half and second half.',
    { exact: true },
  )).toBeVisible()
  await expect(page.getByLabel('Message from you')).toHaveCount(1)
  await expect(page.getByLabel('Message from Elysia')).toHaveCount(1)
  await expect(page.getByRole('button', { name: 'Stop generation' })).toHaveCount(0)
  await expect.poll(() => page.evaluate(() => (
    window.localStorage.getItem('elysia.pending-chat-send.v1')
  ))).toBeNull()

  await page.getByRole('button', { name: 'Start voice' }).click()
  await expect(page.getByRole('main', { name: 'Voice capture' })).toBeVisible()
  await expect.poll(async () => (
    (await getCalls()).filter(
      (call) => call.method === 'stopSpeechPlayback',
    ).length
  )).toBe(1)
  expect((await getCalls()).find(
    (call) => call.method === 'stopSpeechPlayback',
  )?.args).toEqual(['test-request-1', 'chat-test'])
  await page.getByRole('button', { name: 'Close voice' }).click()

  await emitSnapshot(readySnapshot({
    activeGeneration: {
      requestId: 'test-request-1',
      chatId: 'chat-test',
      kind: 'send',
      userText: 'Continue safely after refresh',
      reply: 'First half',
      stopping: false,
    },
  }))
  await expect(page.getByRole('button', { name: 'Stop generation' })).toHaveCount(0)
  await expect(page.getByLabel('Message from you')).toHaveCount(1)
  await expect(page.getByLabel('Message from Elysia')).toHaveCount(1)
})

test('restores a send draft when Backend failure interrupts generation', async () => {
  await emitSnapshot(readySnapshot())
  const composer = page.getByLabel('Message Elysia')
  await composer.fill('Recover this prompt after Backend failure')
  await composer.press('Enter')
  await emitEvent({
    type: 'chat-chunk',
    requestId: 'test-request-1',
    chatId: 'chat-test',
    chunk: 'Partial before failure',
  })
  await emitSnapshot({
    ...readySnapshot(),
    revision: 2,
    status: 'error',
    capabilities: [],
    models: [],
    error: 'The local Backend stopped unexpectedly.',
    activeGeneration: {
      requestId: 'test-request-1',
      chatId: 'chat-test',
      kind: 'send',
      userText: 'Recover this prompt after Backend failure',
      reply: 'Partial before failure',
      stopping: false,
    },
  })

  await expect(page.getByRole('button', { name: 'Stop generation' })).toHaveCount(0)
  await expect(composer).toHaveValue('Recover this prompt after Backend failure')
  await expect(page.getByText(
    'The local Backend stopped unexpectedly.',
    { exact: true },
  )).toBeVisible()
})

test('retains the pending send until a recovered draft is durably stored', async () => {
  await emitSnapshot(readySnapshot())
  const composer = page.getByLabel('Message Elysia')
  await composer.fill('Keep the durable pending copy')
  await composer.press('Enter')
  await composer.fill('Newer local draft')
  await page.evaluate(() => {
    const originalSetItem = Storage.prototype.setItem
    const testWindow = window as Window & { restoreDraftStorage?: () => void }
    testWindow.restoreDraftStorage = () => {
      Storage.prototype.setItem = originalSetItem
      delete testWindow.restoreDraftStorage
    }
    Storage.prototype.setItem = function setItem(key: string, value: string): void {
      if (key === 'elysia.chat-drafts.v1') {
        throw new DOMException('Test quota exceeded', 'QuotaExceededError')
      }
      originalSetItem.call(this, key, value)
    }
  })

  await emitEvent({
    type: 'chat-error',
    requestId: 'test-request-1',
    chatId: 'chat-test',
    code: 'generation.failed',
    message: 'Generation failed safely.',
    retryable: true,
  })

  await expect(composer).toHaveValue(
    'Keep the durable pending copy\n\nNewer local draft',
  )
  await expect(page.getByRole('button', { name: 'Send message' })).toBeDisabled()
  await expect.poll(() => page.evaluate(() => (
    window.localStorage.getItem('elysia.pending-chat-send.v1')
  ))).toContain('Keep the durable pending copy')

  await page.evaluate(() => {
    const testWindow = window as Window & { restoreDraftStorage?: () => void }
    testWindow.restoreDraftStorage?.()
  })
  await composer.fill('Keep the durable pending copy\n\nNewer local draft!')
  await expect.poll(() => page.evaluate(() => (
    window.localStorage.getItem('elysia.pending-chat-send.v1')
  ))).toBeNull()
})

test('keeps a recovered prompt inside the 100-draft storage cap', async () => {
  const recoveredChatId = 'chat-recovered-overflow'
  const recoveredPrompt = 'Keep the 101st recovered prompt'
  await page.evaluate(({ chatId, prompt }) => {
    const drafts = Object.fromEntries(Array.from(
      { length: 100 },
      (_, index) => [`chat-existing-${index}`, `Draft ${index}`],
    ))
    window.localStorage.setItem(
      'elysia.chat-drafts.v1',
      JSON.stringify(drafts),
    )
    window.localStorage.setItem(
      'elysia.pending-chat-send.v1',
      JSON.stringify({
        operationId: 'recover-overflow-operation',
        chatId,
        userText: prompt,
        baseMessageCount: 0,
      }),
    )
  }, { chatId: recoveredChatId, prompt: recoveredPrompt })

  await page.reload()
  await page.waitForFunction(() => (
    'elysiaDesktopTest' in window
    && (window as TestWindow).elysiaDesktopTest
      .getCalls()
      .some((call) => call.method === 'onBackendEvent.subscribe')
  ))
  await emitSnapshot(readySnapshot())

  await expect.poll(() => page.evaluate(() => (
    window.localStorage.getItem('elysia.pending-chat-send.v1')
  ))).toBeNull()
  await expect.poll(() => page.evaluate((chatId) => {
    const raw = window.localStorage.getItem('elysia.chat-drafts.v1')
    const drafts = raw === null ? {} : JSON.parse(raw)
    return {
      count: Object.keys(drafts).length,
      recovered: drafts[chatId] ?? null,
    }
  }, recoveredChatId)).toEqual({ count: 100, recovered: recoveredPrompt })

  await page.reload()
  await page.waitForFunction(() => (
    'elysiaDesktopTest' in window
    && (window as TestWindow).elysiaDesktopTest
      .getCalls()
      .some((call) => call.method === 'onBackendEvent.subscribe')
  ))
  await expect.poll(() => page.evaluate((chatId) => {
    const raw = window.localStorage.getItem('elysia.chat-drafts.v1')
    const drafts = raw === null ? {} : JSON.parse(raw)
    return drafts[chatId] ?? null
  }, recoveredChatId)).toBe(recoveredPrompt)
})

test('fails closed when recovery storage cannot be read', async () => {
  const protectedDraft = 'Do not overwrite this protected draft'
  await page.evaluate((draft) => {
    window.localStorage.setItem(
      'elysia.chat-drafts.v1',
      JSON.stringify({ 'chat-test': draft }),
    )
  }, protectedDraft)
  await page.addInitScript(() => {
    const key = 'elysia.chat-drafts.v1'
    const originalGetItem = Storage.prototype.getItem
    const originalSetItem = Storage.prototype.setItem
    const originalRemoveItem = Storage.prototype.removeItem
    let mutations = 0
    const testWindow = window as Window & {
      readProtectedRecoveryState?: () => {
        mutations: number
        value: string | null
      }
    }
    testWindow.readProtectedRecoveryState = () => ({
      mutations,
      value: originalGetItem.call(window.localStorage, key),
    })
    Storage.prototype.getItem = function getItem(storageKey: string): string | null {
      if (storageKey === key) {
        throw new DOMException('Test storage read failure', 'SecurityError')
      }
      return originalGetItem.call(this, storageKey)
    }
    Storage.prototype.setItem = function setItem(
      storageKey: string,
      value: string,
    ): void {
      if (storageKey === key) {
        mutations += 1
      }
      originalSetItem.call(this, storageKey, value)
    }
    Storage.prototype.removeItem = function removeItem(storageKey: string): void {
      if (storageKey === key) {
        mutations += 1
      }
      originalRemoveItem.call(this, storageKey)
    }
  })

  await page.reload()
  await expect(page.getByRole('heading', {
    name: 'Elysia could not display this view.',
  })).toBeVisible()
  const recoveryState = await page.evaluate(() => {
    const testWindow = window as Window & {
      readProtectedRecoveryState?: () => {
        mutations: number
        value: string | null
      }
    }
    return testWindow.readProtectedRecoveryState?.()
  })
  expect(recoveryState).toEqual({
    mutations: 0,
    value: JSON.stringify({ 'chat-test': protectedDraft }),
  })
})

test('does not lock Chat when completed-send cleanup fails', async () => {
  await emitSnapshot(readySnapshot())
  const composer = page.getByLabel('Message Elysia')
  await composer.fill('First prompt with failed cleanup')
  await composer.press('Enter')
  await expect.poll(() => page.evaluate(() => (
    window.localStorage.getItem('elysia.pending-chat-send.v1')
  ))).toContain('First prompt with failed cleanup')
  await page.evaluate(() => {
    const originalRemoveItem = Storage.prototype.removeItem
    Storage.prototype.removeItem = function removeItem(key: string): void {
      if (key === 'elysia.pending-chat-send.v1') {
        throw new DOMException('Test cleanup failure', 'SecurityError')
      }
      originalRemoveItem.call(this, key)
    }
  })

  await emitEvent({
    type: 'chat-complete',
    requestId: 'test-request-1',
    chatId: 'chat-test',
    reply: 'First reply committed safely',
  })
  await expect(page.getByText(
    'First reply committed safely',
    { exact: true },
  )).toBeVisible()

  await composer.fill('Second prompt remains available')
  await expect(page.getByRole('button', { name: 'Send message' })).toBeEnabled()
  await composer.press('Enter')
  await expect.poll(async () => (
    (await getCalls()).filter((call) => call.method === 'sendMessage').length
  )).toBe(2)
  await expect.poll(() => page.evaluate(() => (
    window.localStorage.getItem('elysia.pending-chat-send.v1')
  ))).toContain('Second prompt remains available')
})

test('does not let an unrelated terminal event consume an unbound pending send', async () => {
  await emitSnapshot(readySnapshot())
  await page.evaluate(() => {
    const control = (window as TestWindow).elysiaDesktopTest
    control.failNextSend('The request failed before an id was returned.')
    const originalSetItem = Storage.prototype.setItem
    const testWindow = window as Window & { restoreDraftStorage?: () => void }
    testWindow.restoreDraftStorage = () => {
      Storage.prototype.setItem = originalSetItem
      delete testWindow.restoreDraftStorage
    }
    Storage.prototype.setItem = function setItem(key: string, value: string): void {
      if (key === 'elysia.chat-drafts.v1') {
        throw new DOMException('Test quota exceeded', 'QuotaExceededError')
      }
      originalSetItem.call(this, key, value)
    }
  })
  const composer = page.getByLabel('Message Elysia')
  await composer.fill('Pending without a Backend request id')
  await composer.press('Enter')
  await expect(page.getByText(
    'The request failed before an id was returned.',
    { exact: true },
  )).toBeVisible()
  await expect.poll(() => page.evaluate(() => {
    const raw = window.localStorage.getItem('elysia.pending-chat-send.v1')
    const pending = raw === null ? null : JSON.parse(raw)
    return pending === null
      ? null
      : { text: pending.userText, hasRequestId: 'requestId' in pending }
  })).toEqual({
    text: 'Pending without a Backend request id',
    hasRequestId: false,
  })

  await emitEvent({
    type: 'chat-error',
    requestId: 'unrelated-request',
    chatId: 'chat-test',
    code: 'generation.failed',
    message: 'An unrelated request failed.',
    retryable: true,
  })
  await expect.poll(() => page.evaluate(() => (
    window.localStorage.getItem('elysia.pending-chat-send.v1')
  ))).toContain('Pending without a Backend request id')
  await expect(composer).toHaveValue('Pending without a Backend request id')
  await expect(page.getByRole('button', { name: 'Send message' })).toBeDisabled()

  await page.evaluate(() => {
    const testWindow = window as Window & { restoreDraftStorage?: () => void }
    testWindow.restoreDraftStorage?.()
  })
  await composer.fill('Pending without a Backend request id!')
  await expect.poll(() => page.evaluate(() => (
    window.localStorage.getItem('elysia.pending-chat-send.v1')
  ))).toBeNull()
})

test('keeps both durable copies when a recovered draft exceeds its limit', async () => {
  const newerDraft = 'n'.repeat(999_990)
  const recoveredPrompt = 'recovered prompt remains protected'
  await page.evaluate(({ draft, prompt }) => {
    window.localStorage.setItem(
      'elysia.chat-drafts.v1',
      JSON.stringify({ 'chat-test': draft }),
    )
    window.localStorage.setItem(
      'elysia.pending-chat-send.v1',
      JSON.stringify({
        operationId: 'oversized-recovery-operation',
        chatId: 'chat-test',
        userText: prompt,
        baseMessageCount: 0,
      }),
    )
  }, { draft: newerDraft, prompt: recoveredPrompt })

  await page.reload()
  await page.waitForFunction(() => (
    'elysiaDesktopTest' in window
    && (window as TestWindow).elysiaDesktopTest
      .getCalls()
      .some((call) => call.method === 'onBackendEvent.subscribe')
  ))
  await emitSnapshot(readySnapshot())

  await expect.poll(() => page.getByLabel('Message Elysia').inputValue())
    .toHaveLength(recoveredPrompt.length + 2 + newerDraft.length)
  const durableState = await page.evaluate(() => ({
    drafts: window.localStorage.getItem('elysia.chat-drafts.v1'),
    pending: window.localStorage.getItem('elysia.pending-chat-send.v1'),
  }))
  expect(durableState.drafts).toContain(`"chat-test":"${'n'.repeat(32)}`)
  expect(durableState.pending).toContain(recoveredPrompt)
})

test('uses the committed count floor for an immediate second send', async () => {
  await emitSnapshot(readySnapshot())
  await page.evaluate(() => {
    ;(window as TestWindow).elysiaDesktopTest.setChatListDelay(true)
  })
  const composer = page.getByLabel('Message Elysia')
  await composer.fill('First message')
  await composer.press('Enter')
  await emitEvent({
    type: 'chat-complete',
    requestId: 'test-request-1',
    chatId: 'chat-test',
    reply: 'First reply',
  })
  await expect.poll(() => page.evaluate(() => (
    (window as TestWindow).elysiaDesktopTest.getPendingChatListCount()
  ))).toBe(1)

  await composer.fill('Second message before history refresh')
  await composer.press('Enter')
  await expect.poll(() => page.evaluate(() => {
    const raw = window.localStorage.getItem('elysia.pending-chat-send.v1')
    return raw === null ? null : JSON.parse(raw).baseMessageCount
  })).toBe(2)

  await replaceRendererWindow()
  const firstSummary = chatSummary('chat-test', 'Elysia Chat', { messageCount: 2 })
  const firstCommittedState: ChatSessionState = {
    activeChat: {
      ...firstSummary,
      messages: [
        {
          messageId: 'user-first',
          role: 'user',
          content: 'First message',
          createdAt: '2026-08-25T12:30:00+00:00',
          attachments: [],
        },
        {
          messageId: 'assistant-first',
          role: 'assistant',
          content: 'First reply',
          createdAt: '2026-08-25T12:31:00+00:00',
          attachments: [],
        },
      ],
    },
    chats: [firstSummary],
  }
  await page.evaluate(({ nextSnapshot, nextState }) => {
    const control = (window as TestWindow).elysiaDesktopTest
    control.setChatState(nextState)
    control.setSnapshot(nextSnapshot)
  }, {
    nextSnapshot: readySnapshot(),
    nextState: firstCommittedState,
  })
  await emitSnapshot(readySnapshot())

  await expect(page.getByLabel('Message Elysia')).toHaveValue(
    'Second message before history refresh',
  )
  await expect.poll(() => page.evaluate(() => (
    window.localStorage.getItem('elysia.pending-chat-send.v1')
  ))).toBeNull()
})

test('recovers a pending send beside a newer draft after a window boundary', async () => {
  await emitSnapshot(readySnapshot())
  const composer = page.getByLabel('Message Elysia')
  await composer.fill('Submitted before the window closed')
  await composer.press('Enter')
  await composer.fill('A newer draft written during generation')
  await expect.poll(() => page.evaluate(() => (
    window.localStorage.getItem('elysia.pending-chat-send.v1')
  ))).toContain('Submitted before the window closed')

  const runningApp = electronApp
  expect(runningApp).toBeDefined()
  const nextWindow = runningApp!.waitForEvent('window')
  await runningApp!.evaluate(async ({ BrowserWindow }, paths) => {
    const previousWindow = BrowserWindow.getAllWindows()[0]
    const replacementWindow = new BrowserWindow({
      width: 1180,
      height: 780,
      show: true,
      webPreferences: {
        preload: paths.preloadPath,
        contextIsolation: true,
        nodeIntegration: false,
        sandbox: true,
        partition: `elysia-ui-test-${process.pid}`,
      },
    })
    await replacementWindow.loadFile(paths.rendererPath)
    previousWindow?.close()
  }, {
    preloadPath: mockPreloadPath,
    rendererPath,
  })
  page = await nextWindow
  await page.waitForLoadState('domcontentloaded')
  await page.waitForFunction(() => (
    'elysiaDesktopTest' in window
    && (window as TestWindow).elysiaDesktopTest
      .getCalls()
      .some((call) => call.method === 'onBackendEvent.subscribe')
  ))
  await emitSnapshot(readySnapshot())

  await expect(page.getByLabel('Message Elysia')).toHaveValue(
    'Submitted before the window closed\n\nA newer draft written during generation',
  )
  await expect(page.getByText(
    /An unfinished prompt was restored to Elysia Chat's draft/,
  )).toBeVisible()
  await expect.poll(() => page.evaluate(() => (
    window.localStorage.getItem('elysia.pending-chat-send.v1')
  ))).toBeNull()
})

test('does not restore a pending send already present in canonical history', async () => {
  await emitSnapshot(readySnapshot())
  const composer = page.getByLabel('Message Elysia')
  await composer.fill('Committed while the renderer was unavailable')
  await composer.press('Enter')
  await composer.fill('Keep only this newer draft')

  const summary = chatSummary('chat-test', 'Elysia Chat', { messageCount: 2 })
  const canonicalState: ChatSessionState = {
    activeChat: {
      ...summary,
      messages: [
        {
          messageId: 'user-committed-offscreen',
          role: 'user',
          content: 'Committed while the renderer was unavailable',
          createdAt: '2026-08-25T12:30:00+00:00',
          attachments: [],
        },
        {
          messageId: 'assistant-committed-offscreen',
          role: 'assistant',
          content: 'Canonical reply committed safely.',
          createdAt: '2026-08-25T12:31:00+00:00',
          attachments: [],
        },
      ],
    },
    chats: [summary],
  }
  await page.evaluate(({ nextSnapshot, nextState }) => {
    const control = (window as TestWindow).elysiaDesktopTest
    control.setChatState(nextState)
    control.setSnapshot(nextSnapshot)
  }, {
    nextSnapshot: readySnapshot(),
    nextState: canonicalState,
  })
  await persistBackendForReload()
  await page.reload()
  await page.waitForFunction(() => (
    'elysiaDesktopTest' in window
    && (window as TestWindow).elysiaDesktopTest
      .getCalls()
      .some((call) => call.method === 'onBackendEvent.subscribe')
  ))

  await expect(page.getByText(
    'Canonical reply committed safely.',
    { exact: true },
  )).toBeVisible()
  await expect(page.getByLabel('Message Elysia')).toHaveValue(
    'Keep only this newer draft',
  )
  await expect(page.getByText(/An unfinished prompt was restored/)).toHaveCount(0)
  await expect.poll(() => page.evaluate(() => (
    window.localStorage.getItem('elysia.pending-chat-send.v1')
  ))).toBeNull()
})

test('rehydrates a hidden retry with its canonical message pair', async () => {
  const chatA = chatSummary('chat-retry-a', 'Retry A', { messageCount: 2 })
  const chatB = chatSummary('chat-retry-b', 'Retry B')
  await setChatState({
    activeChat: {
      ...chatA,
      messages: [
        {
          messageId: 'user-retry-hidden',
          role: 'user',
          content: 'Original hidden question',
          createdAt: '2026-08-25T12:30:00+00:00',
          attachments: [],
        },
        {
          messageId: 'assistant-retry-hidden',
          role: 'assistant',
          content: 'Original hidden answer',
          createdAt: '2026-08-25T12:31:00+00:00',
          attachments: [],
        },
      ],
    },
    chats: [chatA, chatB],
  })
  await emitSnapshot(readySnapshot({
    chatId: chatA.chatId,
    chatTitle: chatA.title,
  }))

  await page.locator('[data-message-id="assistant-retry-hidden"]')
    .getByRole('button', { name: 'Regenerate' })
    .click()
  await emitEvent({
    type: 'chat-chunk',
    requestId: 'test-request-1',
    chatId: chatA.chatId,
    chunk: 'Replacement in progress',
  })
  await page.getByRole('button', { name: `Open chat ${chatB.title}` }).click()
  await persistBackendForReload()
  await page.reload()
  await page.waitForFunction(() => (
    'elysiaDesktopTest' in window
    && (window as TestWindow).elysiaDesktopTest
      .getCalls()
      .some((call) => call.method === 'onBackendEvent.subscribe')
  ))

  await page.getByRole('button', { name: `Open chat ${chatA.title}` }).click()
  await expect(page.getByText('Original hidden question', { exact: true })).toBeVisible()
  await expect(page.getByText('Replacement in progress', { exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Stop generation' })).toBeVisible()

  await emitEvent({
    type: 'chat-error',
    requestId: 'test-request-1',
    chatId: chatA.chatId,
    code: 'generation.failed',
    message: 'Retry failed safely.',
    retryable: true,
  })
  await expect(page.getByText('Original hidden answer', { exact: true })).toBeVisible()
  await expect(page.getByText('Replacement in progress', { exact: true })).toHaveCount(0)
  await expect(page.getByRole('button', { name: 'Stop generation' })).toHaveCount(0)
})

test('recovers an offline non-Chat view without losing its Chat draft', async () => {
  await emitSnapshot(readySnapshot())
  await page.getByLabel('Message Elysia').fill('Keep this draft while offline')
  await page.getByRole('button', { name: /^Projects/ }).click()
  await emitSnapshot({
    ...readySnapshot(),
    revision: 2,
    status: 'error',
    error: 'The local Backend stopped unexpectedly.',
  })
  const alert = page.getByRole('alert').filter({
    hasText: 'Local Backend unavailable',
  })
  await expect(alert).toContainText('stopped unexpectedly')
  await expect(alert.getByRole('button', { name: 'Retry connection' })).toBeVisible()

  await emitSnapshot({ ...readySnapshot(), revision: 3 })
  await expect(alert).toHaveCount(0)
  await page.getByRole('button', { name: 'Chat', exact: true }).click()
  await expect(page.getByLabel('Message Elysia')).toHaveValue(
    'Keep this draft while offline',
  )
})

test('keeps long multi-chunk output responsive and out of the live region', async () => {
  await emitSnapshot(readySnapshot())
  await setWindowAndZoom(960, 640, 1.5)
  const composer = page.getByLabel('Message Elysia')
  await composer.fill('Generate a long bilingual response')
  await composer.press('Enter')

  const chunks = Array.from(
    { length: 240 },
    (_, index) => `片段${String(index).padStart(3, '0')} English🙂 `,
  )
  for (let offset = 0; offset < chunks.length; offset += 40) {
    await emitEvents(chunks.slice(offset, offset + 40).map((chunk) => ({
      type: 'chat-chunk',
      requestId: 'test-request-1',
      chatId: 'chat-test',
      chunk,
    })))
  }
  const expectedReply = chunks.join('')
  const streamed = page.getByLabel('Message from Elysia').last()
  await expect(streamed).toContainText(expectedReply)
  await expect(page.locator('.message-column')).toHaveAttribute('aria-live', 'off')
  await expect(page.locator('[data-generation-announcement]')).toHaveText(
    'Elysia is generating a reply.',
  )
  await composer.fill('Next draft remains editable during generation')
  await expect(page.getByRole('button', { name: 'Stop generation' })).toBeVisible()

  await emitEvent({
    type: 'chat-complete',
    requestId: 'test-request-1',
    chatId: 'chat-test',
    reply: expectedReply,
  })
  await expect(page.locator('[data-generation-announcement]')).toHaveText(
    'Elysia is ready for your next message.',
  )
  await expect(composer).toHaveValue('Next draft remains editable during generation')
  await expectShellWithoutHorizontalOverflow()
})

test('restores a failed hidden Chat prompt before another turn replaces it', async () => {
  const chatA = chatSummary('chat-failed-a', 'Failed Chat A')
  const chatB = chatSummary('chat-next-b', 'Next Chat B')
  await setChatState({
    activeChat: { ...chatA, messages: [] },
    chats: [chatA, chatB],
  })
  await emitSnapshot(readySnapshot({
    chatId: chatA.chatId,
    chatTitle: chatA.title,
  }))
  const composer = page.getByLabel('Message Elysia')
  await composer.fill('Recover this failed prompt')
  await composer.press('Enter')
  await page.getByRole('button', { name: 'Open chat Next Chat B' }).click()
  await emitEvent({
    type: 'chat-error',
    requestId: 'test-request-1',
    chatId: chatA.chatId,
    code: 'model.unavailable',
    message: 'The model stopped responding.',
    retryable: true,
  })
  await composer.fill('A separate prompt in Chat B')
  await composer.press('Enter')
  await page.getByRole('button', { name: 'Open chat Failed Chat A' }).click()
  await expect(composer).toHaveValue('Recover this failed prompt')
  await expect(page.getByText('A separate prompt in Chat B', {
    exact: true,
  })).toHaveCount(0)
})

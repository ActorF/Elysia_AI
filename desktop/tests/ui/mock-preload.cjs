/** Provide a deterministic, stateful Desktop API double to renderer UI tests. */

const { contextBridge } = require('electron')

const RELOAD_STATE_KEY = 'elysia.ui-test.backend-state.v1'

function takeReloadState() {
  try {
    const raw = window.sessionStorage.getItem(RELOAD_STATE_KEY)
      ?? window.localStorage.getItem(RELOAD_STATE_KEY)
    window.sessionStorage.removeItem(RELOAD_STATE_KEY)
    window.localStorage.removeItem(RELOAD_STATE_KEY)
    return raw === null ? null : JSON.parse(raw)
  } catch {
    return null
  }
}

const initialSnapshot = {
  revision: 0,
  status: 'starting',
  capabilities: [],
  models: [],
}

function defaultChatState(chatId = 'chat-test', title = 'Elysia Chat') {
  const summary = {
    chatId,
    title,
    mode: 'chat',
    createdAt: '2026-08-25T12:00:00+00:00',
    updatedAt: '2026-08-25T12:00:00+00:00',
    messageCount: 0,
    projectId: null,
    modelName: 'qwen3.5:9b',
    pinned: false,
    archived: false,
  }
  return {
    activeChat: { ...summary, messages: [] },
    chats: [summary],
  }
}

function defaultProjectState() {
  return {
    activeProject: null,
    projects: [],
    chatState: clone(chatState),
  }
}

function defaultSettingsState() {
  const values = {
    modelName: 'qwen3.5:9b',
    ollamaHost: 'http://localhost:11434',
    shortTermMemoryTokenBudget: 2048,
    memoryRetrievalLimit: 5,
    dataImportMaxBytes: 16777216,
  }
  return {
    revision: 0,
    updatedAt: null,
    settings: values,
    activeSettings: { ...values },
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
}

function defaultVoiceSettingsState() {
  return {
    revision: 0,
    updatedAt: null,
    inputDeviceId: null,
    outputDeviceId: null,
    warning: null,
  }
}

function attachmentScopeKey(scope) {
  return `${scope.kind}:${scope.id}`
}

function defaultAttachmentState(scope) {
  return {
    scope: clone(scope),
    attachments: [],
    maxFileBytes: 16_777_216,
    maxFileCount: 10,
  }
}

function mediaTypeFor(file) {
  if (typeof file.mediaType === 'string' && file.mediaType) {
    return file.mediaType
  }
  if (typeof file.type === 'string' && file.type) {
    return file.type
  }
  const extension = String(file.name ?? '').split('.').pop()?.toLowerCase()
  return extension === 'txt'
    ? 'text/plain'
    : extension === 'md'
      ? 'text/markdown'
      : extension === 'pdf'
        ? 'application/pdf'
        : 'application/octet-stream'
}

function attachmentStateFor(scope) {
  const key = attachmentScopeKey(scope)
  if (!attachmentStates.has(key)) {
    attachmentStates.set(key, defaultAttachmentState(scope))
  }
  return attachmentStates.get(key)
}

function addAttachmentFiles(scope, files) {
  const current = attachmentStateFor(scope)
  const attachments = [...current.attachments]
  for (const file of files) {
    const fileName = String(file.name)
    const sizeBytes = Number(file.sizeBytes ?? file.size)
    const mediaType = mediaTypeFor(file)
    const duplicate = attachments.some((item) => (
      item.fileName === fileName
      && item.sizeBytes === sizeBytes
      && item.mediaType === mediaType
    ))
    if (!duplicate) {
      attachments.push({
        attachmentId: `attachment_test_${nextAttachmentNumber}`,
        fileName,
        mediaType,
        sizeBytes,
        status: 'ready',
      })
      nextAttachmentNumber += 1
    }
  }
  const nextState = { ...current, attachments }
  attachmentStates.set(attachmentScopeKey(scope), nextState)
  return clone(nextState)
}

let snapshot = clone(initialSnapshot)
let chatState = defaultChatState()
let projectState = defaultProjectState()
let settingsState = defaultSettingsState()
let voiceSettingsState = defaultVoiceSettingsState()
let microphonePermissionStatus = 'granted'
let nextSettingsError = null
let nextVoiceSettingsError = null
let nextVoiceCaptureError = null
let nextRestartError = null
let chatMessages = new Map([
  [chatState.activeChat.chatId, clone(chatState.activeChat.messages)],
])
let pendingGenerations = new Map()
let selectedFiles = []
let attachmentStates = new Map()
let nextAttachmentError = null
let nextAttachmentPickerCancelled = false
let nextSendError = null
let selectedWorkspace = null
let nextRequestNumber = 1
let nextAttachmentNumber = 1
let nextCallSequence = 1
let nextProjectUpdateNumber = 1
let calls = []
let delayChatActions = false
let pendingChatActions = []
let delayChatLists = false
let pendingChatLists = []
let delayCharacterPanelChanges = false
let pendingCharacterPanelChanges = []
let delayRestarts = false
let pendingRestarts = []
let delaySettingsLoads = false
let pendingSettingsLoads = []
let delayAttachmentActions = false
let pendingAttachmentActions = []
const backendListeners = new Set()

const reloadState = takeReloadState()
if (reloadState !== null) {
  snapshot = clone(reloadState.snapshot)
  chatState = clone(reloadState.chatState)
  projectState = clone(reloadState.projectState)
  settingsState = clone(reloadState.settingsState)
  voiceSettingsState = clone(
    reloadState.voiceSettingsState ?? defaultVoiceSettingsState(),
  )
  microphonePermissionStatus = reloadState.microphonePermissionStatus
    ?? 'granted'
  chatMessages = new Map(clone(reloadState.chatMessages))
  pendingGenerations = new Map(clone(reloadState.pendingGenerations))
  attachmentStates = new Map(clone(reloadState.attachmentStates))
  nextRequestNumber = reloadState.nextRequestNumber
  nextAttachmentNumber = reloadState.nextAttachmentNumber
  nextProjectUpdateNumber = reloadState.nextProjectUpdateNumber
}

function clone(value) {
  if (value === undefined) {
    return undefined
  }
  return JSON.parse(JSON.stringify(value))
}

function record(method, args = []) {
  calls.push({
    sequence: nextCallSequence,
    method,
    args: clone(args),
  })
  nextCallSequence += 1
}

function synchronizeChatState() {
  if (snapshot.chatId === undefined) {
    return
  }
  saveActiveMessages()
  const matching = chatState.chats.find(
    (chat) => chat.chatId === snapshot.chatId,
  )
  const summary = matching ?? {
    ...chatState.activeChat,
    messages: undefined,
    chatId: snapshot.chatId,
    title: snapshot.chatTitle ?? 'Chat',
    modelName: snapshot.modelName ?? chatState.activeChat.modelName,
  }
  delete summary.messages
  chatState = {
    activeChat: {
      ...summary,
      messages: messagesForChat(summary.chatId),
    },
    chats: matching === undefined ? [summary] : chatState.chats,
  }
}

function saveActiveMessages() {
  chatMessages.set(
    chatState.activeChat.chatId,
    clone(chatState.activeChat.messages),
  )
}

function messagesForChat(chatId) {
  return clone(chatMessages.get(chatId) ?? [])
}

function projectUpdatedAt() {
  const seconds = String(nextProjectUpdateNumber).padStart(2, '0')
  nextProjectUpdateNumber += 1
  return `2026-08-25T13:00:${seconds}+00:00`
}

function synchronizeProjectState() {
  const projects = projectState.projects.map((project) => ({
    ...project,
    chatCount: chatState.chats.filter(
      (chat) => chat.projectId === project.projectId,
    ).length,
  }))
  const activeProjectId = projectState.activeProject?.projectId
  projectState = {
    activeProject: activeProjectId === undefined
      ? null
      : projects.find((project) => project.projectId === activeProjectId) ?? null,
    projects,
    chatState: clone(chatState),
  }
}

function projectResult() {
  synchronizeProjectState()
  return clone(projectState)
}

function settingsResult() {
  const activeProject = projectState.activeProject
  settingsState.scopes = {
    project: activeProject === null
      ? null
      : {
          projectId: activeProject.projectId,
          projectName: activeProject.name,
          modelName: null,
          inheritedModelName: settingsState.settings.modelName,
        },
    chat: chatState.activeChat === undefined
      ? null
      : {
          chatId: chatState.activeChat.chatId,
          chatTitle: chatState.activeChat.title,
          modelName: chatState.activeChat.modelName,
        },
  }
  return clone(settingsState)
}

function activateChat(chatId) {
  const summary = chatState.chats.find((chat) => chat.chatId === chatId)
  if (summary === undefined || summary.archived) {
    throw new Error('Chat cannot be opened.')
  }
  saveActiveMessages()
  chatState = {
    ...chatState,
    activeChat: {
      ...summary,
      messages: messagesForChat(chatId),
    },
  }
  snapshot = {
    ...snapshot,
    revision: snapshot.revision + 1,
    chatId,
    chatTitle: summary.title,
  }
  return clone(chatState)
}

function releaseNextCharacterPanelChange() {
  const release = pendingCharacterPanelChanges.shift()
  release?.()
  return release !== undefined
}

function releaseAllCharacterPanelChanges() {
  while (releaseNextCharacterPanelChange()) {
    // Drain every test-controlled IPC completion before resetting the mock.
  }
}

async function waitForChatAction() {
  if (!delayChatActions) {
    return
  }
  await new Promise((resolve) => {
    pendingChatActions.push(resolve)
  })
}

function releaseNextChatAction() {
  const release = pendingChatActions.shift()
  release?.()
  return release !== undefined
}

function releaseAllChatActions() {
  while (releaseNextChatAction()) {
    // Drain every test-controlled Chat action before resetting the mock.
  }
}

async function waitForChatList() {
  if (!delayChatLists) {
    return
  }
  await new Promise((resolve) => {
    pendingChatLists.push(resolve)
  })
}

function releaseNextChatList() {
  const release = pendingChatLists.shift()
  release?.()
  return release !== undefined
}

function releaseAllChatLists() {
  while (releaseNextChatList()) {
    // Drain every test-controlled Chat list before resetting the mock.
  }
}

async function waitForAttachmentAction() {
  if (!delayAttachmentActions) {
    return
  }
  await new Promise((resolve) => {
    pendingAttachmentActions.push(resolve)
  })
}

function releaseNextAttachmentAction() {
  const release = pendingAttachmentActions.shift()
  release?.()
  return release !== undefined
}

function releaseAllAttachmentActions() {
  while (releaseNextAttachmentAction()) {
    // Drain every test-controlled Attachment action before resetting the mock.
  }
}

async function waitForRestart() {
  if (!delayRestarts) {
    return
  }
  await new Promise((resolve) => {
    pendingRestarts.push(resolve)
  })
}

function releaseNextRestart() {
  const release = pendingRestarts.shift()
  release?.()
  return release !== undefined
}

function releaseAllRestarts() {
  while (releaseNextRestart()) {
    // Drain every test-controlled Backend restart before resetting the mock.
  }
}

async function waitForSettingsLoad() {
  if (!delaySettingsLoads) {
    return
  }
  await new Promise((resolve) => {
    pendingSettingsLoads.push(resolve)
  })
}

function releaseNextSettingsLoad() {
  const release = pendingSettingsLoads.shift()
  release?.()
  return release !== undefined
}

function releaseAllSettingsLoads() {
  while (releaseNextSettingsLoad()) {
    // Drain every test-controlled Settings read before resetting the mock.
  }
}

const desktopApi = {
  rendererReady: async () => {
    record('rendererReady')
  },

  setThemePreference: async (theme) => {
    record('setThemePreference', [theme])
  },

  getSnapshot: async () => {
    record('getSnapshot')
    return clone(snapshot)
  },

  restartBackend: async () => {
    record('restartBackend')
    await waitForRestart()
    if (nextRestartError !== null) {
      const message = nextRestartError
      nextRestartError = null
      throw new Error(message)
    }
    settingsState = {
      ...settingsState,
      activeSettings: clone(settingsState.settings),
      restartRequired: false,
      restartFields: [],
    }
    snapshot = {
      ...snapshot,
      revision: snapshot.revision + 1,
      modelName: settingsState.settings.modelName,
    }
    return clone(snapshot)
  },

  getSettings: async () => {
    record('getSettings')
    await waitForSettingsLoad()
    return settingsResult()
  },

  updateSettings: async (request) => {
    record('updateSettings', [request])
    if (nextSettingsError !== null) {
      const message = nextSettingsError
      nextSettingsError = null
      throw new Error(message)
    }
    if (request.expectedRevision !== settingsState.revision) {
      throw new Error('Settings changed elsewhere. Reload them before saving.')
    }
    const changed = Object.keys(request.settings).filter(
      (field) => request.settings[field] !== settingsState.activeSettings[field],
    )
    const same = JSON.stringify(request.settings) === JSON.stringify(
      settingsState.settings,
    )
    settingsState = {
      ...settingsState,
      revision: same ? settingsState.revision : settingsState.revision + 1,
      updatedAt: same
        ? settingsState.updatedAt
        : '2026-08-25T13:30:00+00:00',
      settings: clone(request.settings),
      restartRequired: changed.length > 0,
      restartFields: changed,
      warning: null,
    }
    return settingsResult()
  },

  getVoiceSettings: async () => {
    record('getVoiceSettings')
    return clone(voiceSettingsState)
  },

  updateVoiceSettings: async (request) => {
    record('updateVoiceSettings', [request])
    if (nextVoiceSettingsError !== null) {
      const message = nextVoiceSettingsError
      nextVoiceSettingsError = null
      throw new Error(message)
    }
    if (request.expectedRevision !== voiceSettingsState.revision) {
      throw new Error('Voice settings changed elsewhere. Reload before saving.')
    }
    const same = request.inputDeviceId === voiceSettingsState.inputDeviceId
      && request.outputDeviceId === voiceSettingsState.outputDeviceId
    voiceSettingsState = {
      revision: same
        ? voiceSettingsState.revision
        : voiceSettingsState.revision + 1,
      updatedAt: same
        ? voiceSettingsState.updatedAt
        : '2026-09-10T12:00:00+00:00',
      inputDeviceId: request.inputDeviceId,
      outputDeviceId: request.outputDeviceId,
      warning: null,
    }
    return clone(voiceSettingsState)
  },

  submitVoiceCapture: async (request) => {
    record('submitVoiceCapture', [request])
    if (nextVoiceCaptureError !== null) {
      const message = nextVoiceCaptureError
      nextVoiceCaptureError = null
      throw new Error(message)
    }
    return {
      sessionId: request.sessionId,
      chatId: request.chatId,
      sampleRateHz: request.sampleRateHz,
      channelCount: request.channelCount,
      sampleFormat: request.sampleFormat,
      sampleCount: request.sampleCount,
      speechStartSample: request.speechStartSample,
      speechEndSample: request.speechEndSample,
      durationMs: request.sampleCount * 1_000 / request.sampleRateHz,
      speechDurationMs: (
        (request.speechEndSample - request.speechStartSample)
        * 1_000
        / request.sampleRateHz
      ),
      sha256Hex: 'a'.repeat(64),
    }
  },

  getMicrophonePermissionStatus: async () => {
    record('getMicrophonePermissionStatus')
    return microphonePermissionStatus
  },

  openMicrophonePrivacySettings: async () => {
    record('openMicrophonePrivacySettings')
  },

  sendMessage: async (request) => {
    record('sendMessage', [request])
    if (nextSendError !== null) {
      const message = nextSendError
      nextSendError = null
      throw new Error(message)
    }
    const scope = { kind: 'chat', id: request.chatId }
    const attachmentState = attachmentStateFor(scope)
    const attachmentIds = new Set(request.attachmentIds)
    const attachments = attachmentState.attachments.filter((attachment) => (
      attachmentIds.has(attachment.attachmentId)
    ))
    if (attachments.length !== attachmentIds.size) {
      throw new Error('One or more attachments are not ready in this Chat.')
    }
    attachmentStates.set(attachmentScopeKey(scope), {
      ...attachmentState,
      attachments: attachmentState.attachments.filter(
        (attachment) => !attachmentIds.has(attachment.attachmentId),
      ),
    })
    const requestId = `test-request-${nextRequestNumber}`
    nextRequestNumber += 1
    pendingGenerations.set(requestId, {
      kind: 'send',
      request: clone(request),
      attachments: clone(attachments),
    })
    snapshot = {
      ...snapshot,
      activeGeneration: {
        requestId,
        chatId: request.chatId,
        kind: 'send',
        userText: request.message,
        reply: '',
        stopping: false,
      },
    }
    return { requestId }
  },

  retryMessage: async (request) => {
    record('retryMessage', [request])
    const requestId = `test-request-${nextRequestNumber}`
    nextRequestNumber += 1
    pendingGenerations.set(requestId, {
      kind: 'retry',
      request: clone(request),
    })
    snapshot = {
      ...snapshot,
      activeGeneration: {
        requestId,
        chatId: request.chatId,
        kind: 'retry',
        ...(request.message === undefined ? {} : { userText: request.message }),
        userMessageId: request.userMessageId,
        assistantMessageId: request.assistantMessageId,
        reply: '',
        stopping: false,
      },
    }
    return { requestId }
  },

  stopGeneration: async (requestId) => {
    record('stopGeneration', [requestId])
    if (!pendingGenerations.has(requestId)) {
      throw new Error('Generation is no longer running.')
    }
    if (snapshot.activeGeneration?.requestId === requestId) {
      snapshot = {
        ...snapshot,
        activeGeneration: {
          ...snapshot.activeGeneration,
          stopping: true,
        },
      }
    }
  },

  copyText: async (text) => {
    record('copyText', [text])
  },

  openExternalUrl: async (url) => {
    record('openExternalUrl', [url])
  },

  listChats: async (includeArchived) => {
    record('listChats', [includeArchived])
    await waitForChatList()
    synchronizeChatState()
    return clone({
      ...chatState,
      chats: includeArchived
        ? chatState.chats
        : chatState.chats.filter((chat) => !chat.archived),
    })
  },

  createChat: async (request) => {
    record('createChat', [request])
    await waitForChatAction()
    const chatId = `chat-created-${nextRequestNumber}`
    nextRequestNumber += 1
    const summary = {
      ...defaultChatState(chatId, request.title).activeChat,
      mode: request.mode,
    }
    delete summary.messages
    chatState = {
      activeChat: { ...summary, messages: [] },
      chats: [summary, ...chatState.chats],
    }
    return activateChat(chatId)
  },

  openChat: async (chatId) => {
    record('openChat', [chatId])
    return activateChat(chatId)
  },

  renameChat: async (request) => {
    record('renameChat', [request])
    chatState.chats = chatState.chats.map((chat) => (
      chat.chatId === request.chatId
        ? { ...chat, title: request.title }
        : chat
    ))
    if (chatState.activeChat.chatId === request.chatId) {
      chatState.activeChat.title = request.title
    }
    return clone(chatState)
  },

  setChatPinned: async (request) => {
    record('setChatPinned', [request])
    chatState.chats = chatState.chats.map((chat) => (
      chat.chatId === request.chatId
        ? { ...chat, pinned: request.pinned }
        : chat
    ))
    if (chatState.activeChat.chatId === request.chatId) {
      chatState.activeChat.pinned = request.pinned
    }
    return clone(chatState)
  },

  setChatArchived: async (request) => {
    record('setChatArchived', [request])
    chatState.chats = chatState.chats.map((chat) => (
      chat.chatId === request.chatId
        ? { ...chat, archived: request.archived }
        : chat
    ))
    if (request.archived && chatState.activeChat.chatId === request.chatId) {
      const fallback = chatState.chats.find((chat) => !chat.archived)
      if (fallback !== undefined) {
        return activateChat(fallback.chatId)
      }
      const replacement = defaultChatState(
        `chat-replacement-${nextRequestNumber}`,
        'Elysia Chat',
      )
      nextRequestNumber += 1
      chatState = {
        activeChat: replacement.activeChat,
        chats: [replacement.chats[0], ...chatState.chats],
      }
      return activateChat(replacement.activeChat.chatId)
    }
    return clone(chatState)
  },

  deleteChat: async (chatId) => {
    record('deleteChat', [chatId])
    attachmentStates.delete(attachmentScopeKey({ kind: 'chat', id: chatId }))
    chatState.chats = chatState.chats.filter((chat) => chat.chatId !== chatId)
    if (chatState.activeChat.chatId === chatId) {
      const fallback = chatState.chats.find((chat) => !chat.archived)
      if (fallback === undefined) {
        const replacement = defaultChatState(
          `chat-replacement-${nextRequestNumber}`,
          'Elysia Chat',
        )
        nextRequestNumber += 1
        chatState = replacement
        return activateChat(replacement.activeChat.chatId)
      }
      return activateChat(fallback.chatId)
    }
    return clone(chatState)
  },

  listProjects: async () => {
    record('listProjects')
    synchronizeChatState()
    return projectResult()
  },

  createProject: async (request) => {
    record('createProject', [request])
    const projectId = `project-created-${nextRequestNumber}`
    nextRequestNumber += 1
    const createdAt = projectUpdatedAt()
    const project = {
      projectId,
      name: request.name,
      createdAt,
      updatedAt: createdAt,
      customInstructions: request.customInstructions,
      workspacePath: null,
      archived: false,
      chatCount: 0,
    }
    projectState = {
      ...projectState,
      activeProject: project,
      projects: [project, ...projectState.projects],
    }
    return projectResult()
  },

  openProject: async (projectId) => {
    record('openProject', [projectId])
    const project = projectState.projects.find(
      (candidate) => candidate.projectId === projectId,
    )
    if (project === undefined) {
      throw new Error('Project does not exist.')
    }
    projectState = { ...projectState, activeProject: project }
    return projectResult()
  },

  updateProject: async (request) => {
    record('updateProject', [request])
    let found = false
    projectState.projects = projectState.projects.map((project) => {
      if (project.projectId !== request.projectId) {
        return project
      }
      found = true
      return {
        ...project,
        name: request.name,
        customInstructions: request.customInstructions,
        updatedAt: projectUpdatedAt(),
      }
    })
    if (!found) {
      throw new Error('Project does not exist.')
    }
    return projectResult()
  },

  chooseProjectWorkspace: async (projectId) => {
    record('chooseProjectWorkspace', [projectId])
    if (selectedWorkspace === null) {
      return null
    }
    let found = false
    projectState.projects = projectState.projects.map((project) => {
      if (project.projectId !== projectId) {
        return project
      }
      found = true
      return {
        ...project,
        workspacePath: selectedWorkspace,
        updatedAt: projectUpdatedAt(),
      }
    })
    if (!found) {
      throw new Error('Project does not exist.')
    }
    return projectResult()
  },

  clearProjectWorkspace: async (projectId) => {
    record('clearProjectWorkspace', [projectId])
    let found = false
    projectState.projects = projectState.projects.map((project) => {
      if (project.projectId !== projectId) {
        return project
      }
      found = true
      return {
        ...project,
        workspacePath: null,
        updatedAt: projectUpdatedAt(),
      }
    })
    if (!found) {
      throw new Error('Project does not exist.')
    }
    return projectResult()
  },

  setProjectArchived: async (request) => {
    record('setProjectArchived', [request])
    let found = false
    projectState.projects = projectState.projects.map((project) => {
      if (project.projectId !== request.projectId) {
        return project
      }
      found = true
      return {
        ...project,
        archived: request.archived,
        updatedAt: projectUpdatedAt(),
      }
    })
    if (!found) {
      throw new Error('Project does not exist.')
    }
    return projectResult()
  },

  moveChatToProject: async (request) => {
    record('moveChatToProject', [request])
    if (
      request.projectId !== null
      && !projectState.projects.some(
        (project) => project.projectId === request.projectId && !project.archived,
      )
    ) {
      throw new Error('Destination Project is unavailable.')
    }
    let found = false
    chatState.chats = chatState.chats.map((chat) => {
      if (chat.chatId !== request.chatId) {
        return chat
      }
      found = true
      return { ...chat, projectId: request.projectId }
    })
    if (!found) {
      throw new Error('Chat does not exist.')
    }
    if (chatState.activeChat.chatId === request.chatId) {
      chatState.activeChat.projectId = request.projectId
    }
    return projectResult()
  },

  selectModel: async (modelName) => {
    record('selectModel', [modelName])
    snapshot = {
      ...snapshot,
      revision: snapshot.revision + 1,
      modelName,
    }
    return clone(snapshot)
  },

  listAttachments: async (scope) => {
    record('listAttachments', [scope])
    await waitForAttachmentAction()
    return clone(attachmentStateFor(scope))
  },

  chooseAttachments: async (scope) => {
    record('chooseAttachments', [scope])
    await waitForAttachmentAction()
    if (nextAttachmentError !== null) {
      const message = nextAttachmentError
      nextAttachmentError = null
      selectedFiles = []
      throw new Error(message)
    }
    if (nextAttachmentPickerCancelled) {
      nextAttachmentPickerCancelled = false
      selectedFiles = []
      return { cancelled: true, state: null }
    }
    const state = addAttachmentFiles(scope, selectedFiles)
    selectedFiles = []
    return { cancelled: false, state }
  },

  acceptDroppedAttachments: async (scope, files) => {
    const safeFiles = Array.from(files, (file) => ({
      name: file.name,
      sizeBytes: file.size,
      type: file.type,
    }))
    record('acceptDroppedAttachments', [scope, safeFiles])
    if (nextAttachmentError !== null) {
      const message = nextAttachmentError
      nextAttachmentError = null
      throw new Error(message)
    }
    return addAttachmentFiles(scope, safeFiles)
  },

  removeAttachment: async (scope, attachmentId) => {
    record('removeAttachment', [scope, attachmentId])
    if (nextAttachmentError !== null) {
      const message = nextAttachmentError
      nextAttachmentError = null
      throw new Error(message)
    }
    const current = attachmentStateFor(scope)
    if (!current.attachments.some((item) => item.attachmentId === attachmentId)) {
      throw new Error('Attachment does not exist in this scope.')
    }
    const state = {
      ...current,
      attachments: current.attachments.filter(
        (item) => item.attachmentId !== attachmentId,
      ),
    }
    attachmentStates.set(attachmentScopeKey(scope), state)
    return clone(state)
  },

  setCharacterPanelOpen: async (open) => {
    record('setCharacterPanelOpen', [open])
    if (delayCharacterPanelChanges) {
      await new Promise((resolve) => {
        pendingCharacterPanelChanges.push(resolve)
      })
    }
  },

  onBackendEvent: (listener) => {
    record('onBackendEvent.subscribe')
    backendListeners.add(listener)
    return () => {
      backendListeners.delete(listener)
      record('onBackendEvent.unsubscribe')
    }
  },
}

const testControl = {
  reset: () => {
    releaseAllChatActions()
    releaseAllChatLists()
    releaseAllCharacterPanelChanges()
    releaseAllRestarts()
    releaseAllSettingsLoads()
    releaseAllAttachmentActions()
    snapshot = clone(initialSnapshot)
    chatState = defaultChatState()
    projectState = defaultProjectState()
    settingsState = defaultSettingsState()
    voiceSettingsState = defaultVoiceSettingsState()
    microphonePermissionStatus = 'granted'
    nextSettingsError = null
    nextVoiceSettingsError = null
    nextVoiceCaptureError = null
    nextRestartError = null
    chatMessages = new Map([
      [chatState.activeChat.chatId, clone(chatState.activeChat.messages)],
    ])
    pendingGenerations = new Map()
    selectedFiles = []
    attachmentStates = new Map()
    nextAttachmentError = null
    nextAttachmentPickerCancelled = false
    nextSendError = null
    selectedWorkspace = null
    nextRequestNumber = 1
    nextAttachmentNumber = 1
    nextCallSequence = 1
    nextProjectUpdateNumber = 1
    calls = []
    delayChatActions = false
    delayChatLists = false
    delayCharacterPanelChanges = false
    delayRestarts = false
    delaySettingsLoads = false
    delayAttachmentActions = false
  },

  setSnapshot: (nextSnapshot) => {
    snapshot = clone(nextSnapshot)
  },

  setChatState: (nextChatState) => {
    chatState = clone(nextChatState)
    chatMessages = new Map(chatState.chats.map((chat) => [
      chat.chatId,
      chat.chatId === chatState.activeChat.chatId
        ? clone(chatState.activeChat.messages)
        : [],
    ]))
    synchronizeProjectState()
  },

  setProjectState: (nextProjectState) => {
    projectState = clone(nextProjectState)
    chatState = clone(nextProjectState.chatState)
    chatMessages = new Map(chatState.chats.map((chat) => [
      chat.chatId,
      chat.chatId === chatState.activeChat.chatId
        ? clone(chatState.activeChat.messages)
        : [],
    ]))
    synchronizeProjectState()
  },

  setSettingsState: (nextSettingsState) => {
    settingsState = clone(nextSettingsState)
  },

  setVoiceSettingsState: (nextVoiceSettingsState) => {
    voiceSettingsState = clone(nextVoiceSettingsState)
  },

  setMicrophonePermissionStatus: (status) => {
    microphonePermissionStatus = status
  },

  failNextSettingsUpdate: (message) => {
    nextSettingsError = message
  },

  failNextVoiceSettingsUpdate: (message) => {
    nextVoiceSettingsError = message
  },

  failNextVoiceCapture: (message) => {
    nextVoiceCaptureError = message
  },

  failNextRestart: (message) => {
    nextRestartError = message
  },

  emitBackendEvent: (event) => {
    const nextEvent = clone(event)
    if (nextEvent.type === 'snapshot') {
      snapshot = clone(nextEvent.snapshot)
    }
    if (
      nextEvent.type === 'chat-chunk'
      && snapshot.activeGeneration?.requestId === nextEvent.requestId
    ) {
      snapshot = {
        ...snapshot,
        activeGeneration: {
          ...snapshot.activeGeneration,
          reply: snapshot.activeGeneration.reply + nextEvent.chunk,
        },
      }
    }
    if (nextEvent.type === 'chat-complete') {
      const createdAt = '2026-08-25T12:01:00+00:00'
      const generation = pendingGenerations.get(nextEvent.requestId)
      let messages = messagesForChat(nextEvent.chatId)
      const assistantMessage = {
        messageId: generation?.kind === 'retry'
          ? generation.request.assistantMessageId
          : `assistant-${nextEvent.requestId}`,
        role: 'assistant',
        content: nextEvent.reply,
        createdAt,
        attachments: [],
      }

      if (generation?.kind === 'send') {
        messages = [
          ...messages,
          {
            messageId: `user-${nextEvent.requestId}`,
            role: 'user',
            content: generation.request.message,
            createdAt,
            attachments: clone(generation.attachments ?? []),
          },
          assistantMessage,
        ]
      } else if (generation?.kind === 'retry') {
        messages = messages.map((message) => {
          if (message.messageId === generation.request.userMessageId) {
            return {
              ...message,
              content: generation.request.message ?? message.content,
            }
          }
          if (message.messageId === generation.request.assistantMessageId) {
            return { ...message, content: nextEvent.reply }
          }
          return message
        })
      } else {
        messages = [
          ...messages.filter(
            (message) => message.messageId !== assistantMessage.messageId,
          ),
          assistantMessage,
        ]
      }

      chatMessages.set(nextEvent.chatId, clone(messages))
      if (nextEvent.chatId === chatState.activeChat.chatId) {
        chatState.activeChat.messages = clone(messages)
        chatState.activeChat.messageCount = messages.length
        chatState.activeChat.updatedAt = createdAt
      }
      chatState.chats = chatState.chats.map((chat) => (
        chat.chatId === nextEvent.chatId
          ? {
              ...chat,
              messageCount: messages.length,
              updatedAt: createdAt,
            }
          : chat
      ))
      pendingGenerations.delete(nextEvent.requestId)
      if (snapshot.activeGeneration?.requestId === nextEvent.requestId) {
        snapshot = { ...snapshot }
        delete snapshot.activeGeneration
      }
    }
    if (nextEvent.type === 'chat-error') {
      const generation = pendingGenerations.get(nextEvent.requestId)
      if (generation?.kind === 'send' && generation.attachments?.length > 0) {
        const scope = { kind: 'chat', id: nextEvent.chatId }
        const current = attachmentStateFor(scope)
        attachmentStates.set(attachmentScopeKey(scope), {
          ...current,
          attachments: [
            ...current.attachments,
            ...generation.attachments.filter((attachment) => (
              !current.attachments.some(
                (currentItem) => currentItem.attachmentId === attachment.attachmentId,
              )
            )),
          ],
        })
      }
      pendingGenerations.delete(nextEvent.requestId)
      if (snapshot.activeGeneration?.requestId === nextEvent.requestId) {
        snapshot = { ...snapshot }
        delete snapshot.activeGeneration
      }
    }
    for (const listener of backendListeners) {
      listener(clone(nextEvent))
    }
  },

  setSelectedFiles: (files) => {
    selectedFiles = clone(files)
  },

  setAttachmentState: (state) => {
    attachmentStates.set(attachmentScopeKey(state.scope), clone(state))
  },

  cancelNextAttachmentPicker: () => {
    nextAttachmentPickerCancelled = true
  },

  failNextAttachmentAction: (message) => {
    nextAttachmentError = message
  },

  failNextSend: (message) => {
    nextSendError = message
  },

  setSelectedWorkspace: (workspacePath) => {
    selectedWorkspace = workspacePath
  },

  setCharacterPanelChangeDelay: (delayed) => {
    delayCharacterPanelChanges = delayed
    if (!delayed) {
      releaseAllCharacterPanelChanges()
    }
  },

  setChatActionDelay: (delayed) => {
    delayChatActions = delayed
    if (!delayed) {
      releaseAllChatActions()
    }
  },

  setChatListDelay: (delayed) => {
    delayChatLists = delayed
    if (!delayed) {
      releaseAllChatLists()
    }
  },

  setRestartDelay: (delayed) => {
    delayRestarts = delayed
    if (!delayed) {
      releaseAllRestarts()
    }
  },

  setSettingsLoadDelay: (delayed) => {
    delaySettingsLoads = delayed
    if (!delayed) {
      releaseAllSettingsLoads()
    }
  },

  setAttachmentActionDelay: (delayed) => {
    delayAttachmentActions = delayed
    if (!delayed) {
      releaseAllAttachmentActions()
    }
  },

  getPendingChatActionCount: () => pendingChatActions.length,

  releaseNextChatAction,

  getPendingChatListCount: () => pendingChatLists.length,

  releaseNextChatList,

  getPendingRestartCount: () => pendingRestarts.length,

  releaseNextRestart,

  getPendingSettingsLoadCount: () => pendingSettingsLoads.length,

  releaseNextSettingsLoad,

  getPendingAttachmentActionCount: () => pendingAttachmentActions.length,

  releaseNextAttachmentAction,

  getPendingCharacterPanelChangeCount: () => (
    pendingCharacterPanelChanges.length
  ),

  releaseNextCharacterPanelChange,

  getCalls: () => clone(calls),

  persistForReload: () => {
    const reloadState = JSON.stringify({
      snapshot,
      chatState,
      projectState,
      settingsState,
      voiceSettingsState,
      microphonePermissionStatus,
      chatMessages: [...chatMessages.entries()],
      pendingGenerations: [...pendingGenerations.entries()],
      attachmentStates: [...attachmentStates.entries()],
      nextRequestNumber,
      nextAttachmentNumber,
      nextProjectUpdateNumber,
    })
    window.sessionStorage.setItem(RELOAD_STATE_KEY, reloadState)
    window.localStorage.setItem(RELOAD_STATE_KEY, reloadState)
  },

  clearCalls: () => {
    nextCallSequence = 1
    calls = []
  },
}

contextBridge.exposeInMainWorld('elysiaDesktop', desktopApi)
contextBridge.exposeInMainWorld('elysiaDesktopTest', testControl)

const { contextBridge } = require('electron')

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

let snapshot = clone(initialSnapshot)
let chatState = defaultChatState()
let projectState = defaultProjectState()
let settingsState = defaultSettingsState()
let nextSettingsError = null
let nextRestartError = null
let chatMessages = new Map([
  [chatState.activeChat.chatId, clone(chatState.activeChat.messages)],
])
let pendingGenerations = new Map()
let selectedFiles = []
let selectedWorkspace = null
let nextRequestNumber = 1
let nextCallSequence = 1
let nextProjectUpdateNumber = 1
let calls = []
let delayChatActions = false
let pendingChatActions = []
let delayCharacterPanelChanges = false
let pendingCharacterPanelChanges = []
let delayRestarts = false
let pendingRestarts = []
let delaySettingsLoads = false
let pendingSettingsLoads = []
const backendListeners = new Set()

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

  sendMessage: async (request) => {
    record('sendMessage', [request])
    const requestId = `test-request-${nextRequestNumber}`
    nextRequestNumber += 1
    pendingGenerations.set(requestId, {
      kind: 'send',
      request: clone(request),
    })
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
    return { requestId }
  },

  stopGeneration: async (requestId) => {
    record('stopGeneration', [requestId])
    if (!pendingGenerations.has(requestId)) {
      throw new Error('Generation is no longer running.')
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

  chooseFiles: async () => {
    record('chooseFiles')
    return clone(selectedFiles)
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
    releaseAllCharacterPanelChanges()
    releaseAllRestarts()
    releaseAllSettingsLoads()
    snapshot = clone(initialSnapshot)
    chatState = defaultChatState()
    projectState = defaultProjectState()
    settingsState = defaultSettingsState()
    nextSettingsError = null
    nextRestartError = null
    chatMessages = new Map([
      [chatState.activeChat.chatId, clone(chatState.activeChat.messages)],
    ])
    pendingGenerations = new Map()
    selectedFiles = []
    selectedWorkspace = null
    nextRequestNumber = 1
    nextCallSequence = 1
    nextProjectUpdateNumber = 1
    calls = []
    delayChatActions = false
    delayCharacterPanelChanges = false
    delayRestarts = false
    delaySettingsLoads = false
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

  failNextSettingsUpdate: (message) => {
    nextSettingsError = message
  },

  failNextRestart: (message) => {
    nextRestartError = message
  },

  emitBackendEvent: (event) => {
    const nextEvent = clone(event)
    if (nextEvent.type === 'snapshot') {
      snapshot = clone(nextEvent.snapshot)
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
            attachments: [],
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
    }
    if (nextEvent.type === 'chat-error') {
      pendingGenerations.delete(nextEvent.requestId)
    }
    for (const listener of backendListeners) {
      listener(clone(nextEvent))
    }
  },

  setSelectedFiles: (files) => {
    selectedFiles = clone(files)
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

  getPendingChatActionCount: () => pendingChatActions.length,

  releaseNextChatAction,

  getPendingRestartCount: () => pendingRestarts.length,

  releaseNextRestart,

  getPendingSettingsLoadCount: () => pendingSettingsLoads.length,

  releaseNextSettingsLoad,

  getPendingCharacterPanelChangeCount: () => (
    pendingCharacterPanelChanges.length
  ),

  releaseNextCharacterPanelChange,

  getCalls: () => clone(calls),

  clearCalls: () => {
    nextCallSequence = 1
    calls = []
  },
}

contextBridge.exposeInMainWorld('elysiaDesktop', desktopApi)
contextBridge.exposeInMainWorld('elysiaDesktopTest', testControl)

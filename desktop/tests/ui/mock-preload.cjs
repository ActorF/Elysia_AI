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

let snapshot = clone(initialSnapshot)
let chatState = defaultChatState()
let selectedFiles = []
let nextRequestNumber = 1
let nextCallSequence = 1
let calls = []
let delayCharacterPanelChanges = false
let pendingCharacterPanelChanges = []
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
      messages: matching === undefined ? [] : chatState.activeChat.messages,
    },
    chats: matching === undefined ? [summary] : chatState.chats,
  }
}

function activateChat(chatId) {
  const summary = chatState.chats.find((chat) => chat.chatId === chatId)
  if (summary === undefined || summary.archived) {
    throw new Error('Chat cannot be opened.')
  }
  chatState = {
    ...chatState,
    activeChat: {
      ...summary,
      messages: chatState.activeChat.chatId === chatId
        ? chatState.activeChat.messages
        : [],
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
    return clone(snapshot)
  },

  sendMessage: async (request) => {
    record('sendMessage', [request])
    const requestId = `test-request-${nextRequestNumber}`
    nextRequestNumber += 1
    return { requestId }
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
    releaseAllCharacterPanelChanges()
    snapshot = clone(initialSnapshot)
    chatState = defaultChatState()
    selectedFiles = []
    nextRequestNumber = 1
    nextCallSequence = 1
    calls = []
    delayCharacterPanelChanges = false
  },

  setSnapshot: (nextSnapshot) => {
    snapshot = clone(nextSnapshot)
  },

  setChatState: (nextChatState) => {
    chatState = clone(nextChatState)
  },

  emitBackendEvent: (event) => {
    const nextEvent = clone(event)
    if (nextEvent.type === 'snapshot') {
      snapshot = clone(nextEvent.snapshot)
    }
    if (
      nextEvent.type === 'chat-complete'
      && nextEvent.chatId === chatState.activeChat.chatId
    ) {
      const createdAt = '2026-08-25T12:01:00+00:00'
      const assistantMessage = {
        messageId: `assistant-${nextEvent.requestId}`,
        role: 'assistant',
        content: nextEvent.reply,
        createdAt,
        attachments: [],
      }
      chatState.activeChat.messages = [
        ...chatState.activeChat.messages.filter(
          (message) => message.messageId !== assistantMessage.messageId,
        ),
        assistantMessage,
      ]
      chatState.activeChat.messageCount = chatState.activeChat.messages.length
      chatState.activeChat.updatedAt = createdAt
      chatState.chats = chatState.chats.map((chat) => (
        chat.chatId === nextEvent.chatId
          ? {
              ...chat,
              messageCount: chatState.activeChat.messageCount,
              updatedAt: createdAt,
            }
          : chat
      ))
    }
    for (const listener of backendListeners) {
      listener(clone(nextEvent))
    }
  },

  setSelectedFiles: (files) => {
    selectedFiles = clone(files)
  },

  setCharacterPanelChangeDelay: (delayed) => {
    delayCharacterPanelChanges = delayed
    if (!delayed) {
      releaseAllCharacterPanelChanges()
    }
  },

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

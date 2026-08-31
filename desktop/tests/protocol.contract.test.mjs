import assert from 'node:assert/strict'
import { EventEmitter } from 'node:events'
import { readFile } from 'node:fs/promises'
import path from 'node:path'
import test from 'node:test'
import { pathToFileURL } from 'node:url'

import { BackendProcess } from '../dist-electron/backend-process.js'
import { parseSafeExternalUrl } from '../dist-electron/external-url.js'
import { isTrustedRendererUrl } from '../dist-electron/renderer-source.js'
import {
  PROTOCOL_NAME,
  PROTOCOL_VERSION,
  MAX_PROTOCOL_FRAME_BYTES,
  ProtocolValidationError,
  createRequest,
  hasNonBlankCodePoint,
  parseChatResult,
  parseClientRequest,
  parseHandshakeResult,
  parseInitializeResult,
  parseChatStateResult,
  parseProjectStateResult,
  parseSettingsStateResult,
  parseServerMessage,
  trimProtocolBlankCharacters,
} from '../dist-electron/protocol.js'

const fixturePath = new URL(
  '../../desktop_protocol/fixtures/v1.samples.json',
  import.meta.url,
)
const fixtures = JSON.parse(await readFile(fixturePath, 'utf8'))
const schemaPath = new URL(
  '../../desktop_protocol/schema/v1.schema.json',
  import.meta.url,
)
const schema = JSON.parse(await readFile(schemaPath, 'utf8'))

test('TypeScript uses the frame limit declared by the JSON Schema', () => {
  assert.equal(
    MAX_PROTOCOL_FRAME_BYTES,
    schema['x-elysia-frameMaxBytes'],
  )
})

test('JSON Schema declares the exact public settings surface', () => {
  assert.deepEqual(
    Object.keys(schema.$defs.settingsValues.properties),
    [
      'modelName',
      'ollamaHost',
      'shortTermMemoryTokenBudget',
      'memoryRetrievalLimit',
      'dataImportMaxBytes',
    ],
  )
  assert.equal(schema.$defs.settingsValues.additionalProperties, false)
  assert.ok(schema.$defs.settingsGetRequest)
  assert.ok(schema.$defs.settingsUpdateRequest)
  assert.ok(schema.$defs.settingsStateResult)
})

for (const sample of fixtures.validClientMessages) {
  test(`TypeScript accepts client sample: ${sample.name}`, () => {
    const parsed = parseClientRequest(sample.message)
    assert.deepEqual(parsed.protocol, fixtures.protocol)
  })
}

for (const sample of fixtures.validServerMessages) {
  test(`TypeScript accepts server sample: ${sample.name}`, () => {
    const parsed = parseServerMessage(sample.message)
    assert.deepEqual(parsed.protocol, fixtures.protocol)

    if (sample.name === 'handshake response') {
      assert.equal(parsed.ok, true)
      assert.equal(
        parseHandshakeResult(parsed.result).protocol.version,
        PROTOCOL_VERSION,
      )
    }
    if (sample.name === 'initialize response') {
      assert.equal(parsed.ok, true)
      assert.equal(
        parseInitializeResult(parsed.result).chatId,
        'chat_fixture',
      )
    }
    if (sample.name === 'chat response') {
      assert.equal(parsed.ok, true)
      assert.equal(parseChatResult(parsed.result).chatId, 'chat_fixture')
    }
    if (sample.name === 'chat state response') {
      assert.equal(parsed.ok, true)
      const result = parseChatStateResult(parsed.result)
      assert.equal(result.activeChat.chatId, 'chat_fixture')
      assert.equal(result.activeChat.messages.length, 2)
      assert.deepEqual(
        result.chats.map((chat) => chat.chatId),
        ['chat_fixture', 'chat_second'],
      )
    }
    if (sample.name === 'project state response') {
      assert.equal(parsed.ok, true)
      const result = parseProjectStateResult(parsed.result)
      assert.equal(result.activeProject.projectId, 'project_fixture')
      assert.equal(result.projects.length, 2)
      assert.equal(result.projects[0].chatCount, 1)
      assert.equal(result.chatState.activeChat.chatId, 'chat_fixture')
    }
    if (sample.name === 'settings state response') {
      assert.equal(parsed.ok, true)
      const result = parseSettingsStateResult(parsed.result)
      assert.equal(result.revision, 3)
      assert.equal(result.restartRequired, true)
      assert.deepEqual(result.restartFields, [
        'modelName',
        'ollamaHost',
        'shortTermMemoryTokenBudget',
        'memoryRetrievalLimit',
        'dataImportMaxBytes',
      ])
      assert.equal(result.scopes.project.projectId, 'project_fixture')
      assert.equal(result.scopes.project.modelName, null)
      assert.equal(result.scopes.chat.chatId, 'chat_fixture')
    }
    if (sample.name === 'settings recovered defaults response') {
      assert.equal(parsed.ok, true)
      const result = parseSettingsStateResult(parsed.result)
      assert.equal(result.revision, 0)
      assert.equal(result.updatedAt, null)
      assert.equal(result.restartRequired, false)
      assert.deepEqual(result.restartFields, [])
      assert.deepEqual(result.scopes, { project: null, chat: null })
      assert.match(result.warning, /Recovered safe defaults/)
    }
  })
}

for (const sample of fixtures.invalidClientMessages) {
  test(`TypeScript rejects client sample: ${sample.name}`, () => {
    assert.throws(
      () => parseClientRequest(sample.message),
      ProtocolValidationError,
    )
  })
}

for (const sample of fixtures.invalidServerMessages) {
  test(`TypeScript rejects server sample: ${sample.name}`, () => {
    assert.throws(
      () => parseServerMessage(sample.message),
      ProtocolValidationError,
    )
  })
}

test('TypeScript request builder produces the versioned envelope', () => {
  assert.deepEqual(
    createRequest('shutdown-1', 'shutdown', {}),
    {
      type: 'request',
      protocol: {
        name: PROTOCOL_NAME,
        version: PROTOCOL_VERSION,
      },
      id: 'shutdown-1',
      method: 'shutdown',
      params: {},
    },
  )
})

test('TypeScript validates revisioned settings requests without secrets', () => {
  assert.deepEqual(
    createRequest('settings-get-1', 'settings.get', {}).params,
    {},
  )
  const settings = {
    modelName: 'qwen3.5:9b',
    ollamaHost: 'http://127.0.0.1:11434',
    shortTermMemoryTokenBudget: 2048,
    memoryRetrievalLimit: 5,
    dataImportMaxBytes: 16777216,
  }
  assert.deepEqual(
    createRequest('settings-update-1', 'settings.update', {
      expectedRevision: 0,
      settings,
    }).params,
    { expectedRevision: 0, settings },
  )

  const secret = 'never-echo-this-secret'
  assert.throws(
    () => parseClientRequest({
      type: 'request',
      protocol: fixtures.protocol,
      id: 'settings-update-secret',
      method: 'settings.update',
      params: {
        expectedRevision: 0,
        settings: { ...settings, apiKey: secret },
      },
    }),
    (error) => (
      error instanceof ProtocolValidationError
      && !error.message.includes(secret)
    ),
  )
})

test('TypeScript validates regenerate and edit-and-retry requests', () => {
  assert.deepEqual(
    createRequest('retry-1', 'chat.retry', {
      chatId: 'chat_fixture',
      userMessageId: 'message_user',
      assistantMessageId: 'message_assistant',
    }).params,
    {
      chatId: 'chat_fixture',
      userMessageId: 'message_user',
      assistantMessageId: 'message_assistant',
    },
  )
  assert.equal(
    createRequest('retry-2', 'chat.retry', {
      chatId: 'chat_fixture',
      userMessageId: 'message_user',
      assistantMessageId: 'message_assistant',
      message: 'Edited prompt',
    }).params.message,
    'Edited prompt',
  )
  assert.throws(
    () => parseClientRequest({
      type: 'request',
      protocol: fixtures.protocol,
      id: 'retry-invalid',
      method: 'chat.retry',
      params: {
        chatId: 'chat_fixture',
        userMessageId: 'message_user',
        assistantMessageId: 'message_assistant',
        message: '\ufeff\u0085',
      },
    }),
    ProtocolValidationError,
  )
})

test('renderer text helpers follow the protocol blank definition', () => {
  assert.equal(hasNonBlankCodePoint('\u0085\ufeff'), false)
  assert.equal(hasNonBlankCodePoint('\u0085hello\ufeff'), true)
  assert.equal(
    trimProtocolBlankCharacters('\u0085\ufeffhello\u00a0'),
    'hello',
  )
})

test('TypeScript rejects a successful response without an id', () => {
  assert.throws(
    () => parseServerMessage({
      type: 'response',
      protocol: fixtures.protocol,
      id: null,
      ok: true,
      result: {},
    }),
    ProtocolValidationError,
  )
})

function projectStateResponse() {
  const sample = fixtures.validServerMessages.find(
    (candidate) => candidate.name === 'project state response',
  )
  assert.ok(sample)
  return structuredClone(sample.message)
}

function settingsStateResponse() {
  const sample = fixtures.validServerMessages.find(
    (candidate) => candidate.name === 'settings state response',
  )
  assert.ok(sample)
  return structuredClone(sample.message)
}

for (const invalidState of [
  'active-absent',
  'active-mismatch',
  'duplicate-project',
  'dangling-chat-project',
  'wrong-chat-count',
]) {
  test(`TypeScript rejects Project state invariant: ${invalidState}`, () => {
    const message = projectStateResponse()
    const result = message.result

    if (invalidState === 'active-absent') {
      result.activeProject.projectId = 'project_missing'
    } else if (invalidState === 'active-mismatch') {
      result.activeProject.name = 'Stale Project'
    } else if (invalidState === 'duplicate-project') {
      result.projects.push(structuredClone(result.projects[0]))
    } else if (invalidState === 'dangling-chat-project') {
      result.chatState.chats[1].projectId = 'project_missing'
    } else {
      result.projects[0].chatCount = 2
      result.activeProject.chatCount = 2
    }

    assert.throws(
      () => parseServerMessage(message),
      ProtocolValidationError,
    )
  })
}

function createPendingChat(method = 'chat.stream') {
  const events = []
  const backend = new BackendProcess('.', (event) => events.push(event))
  backend.pendingRequests.set('chat-state-1', {
    method,
    chatId: 'chat_fixture',
    nextSequence: 0,
    streamCompleted: false,
    streamedReply: '',
    streamedLength: 0,
  })
  return { backend, events }
}

function streamFrame(sequence, chunk, done) {
  return JSON.stringify({
    type: 'stream',
    protocol: fixtures.protocol,
    requestId: 'chat-state-1',
    stream: 'chat.reply',
    sequence,
    chunk,
    done,
  })
}

function responseFrame(reply) {
  return JSON.stringify({
    type: 'response',
    protocol: fixtures.protocol,
    id: 'chat-state-1',
    ok: true,
    result: { chatId: 'chat_fixture', reply },
  })
}

test('Backend state machine accepts one ordered matching Chat stream', () => {
  const { backend, events } = createPendingChat()

  backend.handleProtocolLine(streamFrame(0, '你', false))
  backend.handleProtocolLine(streamFrame(1, '好', false))
  backend.handleProtocolLine(streamFrame(2, '', true))
  backend.handleProtocolLine(responseFrame('你好'))

  assert.deepEqual(
    events.map((event) => event.type),
    ['chat-chunk', 'chat-chunk', 'chat-complete'],
  )
})

test('Backend state machine accepts a retry over the Chat reply stream', () => {
  const { backend, events } = createPendingChat('chat.retry')

  backend.handleProtocolLine(streamFrame(0, 'replacement', false))
  backend.handleProtocolLine(streamFrame(1, '', true))
  backend.handleProtocolLine(responseFrame('replacement'))

  assert.deepEqual(
    events.map((event) => event.type),
    ['chat-chunk', 'chat-complete'],
  )
})

test('Backend sends an exact retry request and tracks it as generation', () => {
  const writes = []
  const backend = new BackendProcess('.', () => undefined)
  backend.child = {
    stdin: {
      writable: true,
      write: (value) => writes.push(value),
    },
  }
  backend.snapshot = {
    revision: 1,
    status: 'ready',
    capabilities: ['chat.retry'],
    models: ['qwen3.5:9b'],
    modelName: 'qwen3.5:9b',
    chatId: 'chat_fixture',
    chatTitle: 'Elysia Chat',
  }

  const { requestId } = backend.beginRetry({
    chatId: 'chat_fixture',
    userMessageId: 'message_user',
    assistantMessageId: 'message_assistant',
    message: '  Edited prompt  ',
  })
  const request = JSON.parse(writes.at(-1))

  assert.equal(request.id, requestId)
  assert.equal(request.method, 'chat.retry')
  assert.deepEqual(request.params, {
    chatId: 'chat_fixture',
    userMessageId: 'message_user',
    assistantMessageId: 'message_assistant',
    message: 'Edited prompt',
  })
  assert.equal(backend.pendingRequests.get(requestId).method, 'chat.retry')
})

test('Backend stop request settles independently from cancelled generation', async () => {
  const writes = []
  const events = []
  const backend = new BackendProcess('.', (event) => events.push(event))
  backend.child = {
    stdin: {
      writable: true,
      write: (value) => writes.push(value),
    },
  }
  backend.pendingRequests.set('generation-1', {
    method: 'chat.retry',
    chatId: 'chat_fixture',
    nextSequence: 0,
    streamCompleted: false,
    streamedReply: '',
    streamedLength: 0,
  })

  const stopping = backend.stopGeneration('generation-1')
  const cancelRequest = JSON.parse(writes.at(-1))
  assert.equal(cancelRequest.method, 'request.cancel')
  assert.deepEqual(cancelRequest.params, { requestId: 'generation-1' })

  backend.handleProtocolLine(JSON.stringify({
    type: 'response',
    protocol: fixtures.protocol,
    id: cancelRequest.id,
    ok: true,
    result: { stopped: true },
  }))
  await stopping
  assert.equal(backend.pendingRequests.has('generation-1'), true)

  backend.handleProtocolLine(JSON.stringify({
    type: 'response',
    protocol: fixtures.protocol,
    id: 'generation-1',
    ok: false,
    error: {
      code: 'request.cancelled',
      message: 'Generation was stopped.',
      retryable: false,
    },
  }))
  assert.deepEqual(events.at(-1), {
    type: 'chat-error',
    requestId: 'generation-1',
    chatId: 'chat_fixture',
    code: 'request.cancelled',
    message: 'Generation was stopped.',
    retryable: false,
  })
})

test('Backend rejects success after cancellation was accepted', async () => {
  const writes = []
  const backend = new BackendProcess('.', () => undefined)
  backend.child = {
    stdin: {
      writable: true,
      write: (value) => writes.push(value),
    },
    kill: () => undefined,
  }
  backend.pendingRequests.set('generation-cancelled', {
    method: 'chat.stream',
    chatId: 'chat_fixture',
    nextSequence: 0,
    streamCompleted: false,
    streamedReply: '',
    streamedLength: 0,
  })

  const stopping = backend.stopGeneration('generation-cancelled')
  const cancelRequest = JSON.parse(writes.at(-1))
  backend.handleProtocolLine(JSON.stringify({
    type: 'response',
    protocol: fixtures.protocol,
    id: cancelRequest.id,
    ok: true,
    result: { stopped: true },
  }))
  await stopping

  backend.handleProtocolLine(JSON.stringify({
    type: 'stream',
    protocol: fixtures.protocol,
    requestId: 'generation-cancelled',
    stream: 'chat.reply',
    sequence: 0,
    chunk: '',
    done: true,
  }))

  assert.equal(backend.getSnapshot().status, 'error')
  assert.match(
    backend.getSnapshot().error,
    /completed a stream after accepting its cancellation/,
  )
})

test('Backend withholds success that precedes a contradictory cancel ack', async () => {
  const writes = []
  const events = []
  const backend = new BackendProcess('.', (event) => events.push(event))
  backend.child = {
    stdin: {
      writable: true,
      write: (value) => writes.push(value),
    },
    kill: () => undefined,
  }
  backend.pendingRequests.set('generation-raced', {
    method: 'chat.stream',
    chatId: 'chat_fixture',
    nextSequence: 0,
    streamCompleted: false,
    streamedReply: '',
    streamedLength: 0,
  })

  const stopping = backend.stopGeneration('generation-raced')
  const cancelRequest = JSON.parse(writes.at(-1))
  backend.handleProtocolLine(JSON.stringify({
    type: 'stream',
    protocol: fixtures.protocol,
    requestId: 'generation-raced',
    stream: 'chat.reply',
    sequence: 0,
    chunk: 'Too late',
    done: false,
  }))
  backend.handleProtocolLine(JSON.stringify({
    type: 'stream',
    protocol: fixtures.protocol,
    requestId: 'generation-raced',
    stream: 'chat.reply',
    sequence: 1,
    chunk: '',
    done: true,
  }))
  backend.handleProtocolLine(JSON.stringify({
    type: 'response',
    protocol: fixtures.protocol,
    id: 'generation-raced',
    ok: true,
    result: { chatId: 'chat_fixture', reply: 'Too late' },
  }))

  assert.equal(
    events.some((event) => event.type === 'chat-complete'),
    false,
  )

  backend.handleProtocolLine(JSON.stringify({
    type: 'response',
    protocol: fixtures.protocol,
    id: cancelRequest.id,
    ok: true,
    result: { stopped: true },
  }))

  await assert.rejects(stopping, /non-cancelled generation/)
  assert.equal(
    events.some((event) => event.type === 'chat-complete'),
    false,
  )
  assert.equal(backend.getSnapshot().status, 'error')
  assert.match(
    backend.getSnapshot().error,
    /non-cancelled generation after accepting cancellation/,
  )
})

test('Backend releases deferred success when cancellation is rejected', async () => {
  const writes = []
  const events = []
  const backend = new BackendProcess('.', (event) => events.push(event))
  backend.child = {
    stdin: {
      writable: true,
      write: (value) => writes.push(value),
    },
  }
  backend.pendingRequests.set('generation-committed', {
    method: 'chat.stream',
    chatId: 'chat_fixture',
    nextSequence: 1,
    streamCompleted: true,
    streamedReply: 'Committed reply',
    streamedLength: 15,
  })

  const stopping = backend.stopGeneration('generation-committed')
  const cancelRequest = JSON.parse(writes.at(-1))
  backend.handleProtocolLine(JSON.stringify({
    type: 'response',
    protocol: fixtures.protocol,
    id: 'generation-committed',
    ok: true,
    result: { chatId: 'chat_fixture', reply: 'Committed reply' },
  }))
  assert.equal(events.length, 0)

  backend.handleProtocolLine(JSON.stringify({
    type: 'response',
    protocol: fixtures.protocol,
    id: cancelRequest.id,
    ok: false,
    error: {
      code: 'request.not_cancellable',
      message: 'Generation already committed.',
      retryable: false,
    },
  }))

  await assert.rejects(stopping, /already committed/)
  assert.deepEqual(events.at(-1), {
    type: 'chat-complete',
    requestId: 'generation-committed',
    chatId: 'chat_fixture',
    reply: 'Committed reply',
  })
})

test('Backend rolls back pending generation when stdin write throws', () => {
  const backend = new BackendProcess('.', () => undefined)
  backend.child = {
    stdin: {
      writable: true,
      write: () => { throw new Error('write EOF') },
    },
    kill: () => undefined,
  }
  backend.snapshot = {
    revision: 1,
    status: 'ready',
    capabilities: ['chat.stream'],
    models: ['qwen3.5:9b'],
    modelName: 'qwen3.5:9b',
    chatId: 'chat_fixture',
    chatTitle: 'Elysia Chat',
  }

  assert.throws(
    () => backend.beginChat({
      chatId: 'chat_fixture',
      message: 'Hello',
    }),
    /Could not write to the Python Backend/,
  )
  assert.equal(backend.pendingRequests.size, 0)
  assert.equal(backend.getSnapshot().status, 'error')
})

test('Backend protocol failure rejects pending renderer actions before exit', async () => {
  const writes = []
  let killCount = 0
  const backend = new BackendProcess('.', () => undefined)
  backend.child = {
    stdin: {
      writable: true,
      write: (value) => writes.push(value),
    },
    kill: () => {
      killCount += 1
      return true
    },
  }
  backend.snapshot = {
    revision: 1,
    status: 'ready',
    capabilities: ['chat.sessions', 'project.management', 'request.cancel'],
    models: ['qwen3.5:9b'],
    modelName: 'qwen3.5:9b',
    chatId: 'chat_fixture',
    chatTitle: 'Elysia Chat',
  }

  const chatAction = backend.openChat('chat_other')
  const projectAction = backend.listProjects()
  const settingsAction = backend.getSettings()
  backend.pendingRequests.set('generation-pending', {
    method: 'chat.stream',
    chatId: 'chat_fixture',
    nextSequence: 0,
    streamCompleted: false,
    streamedReply: '',
    streamedLength: 0,
  })
  const cancellation = backend.stopGeneration('generation-pending')
  const rejections = [
    chatAction,
    projectAction,
    settingsAction,
    cancellation,
  ].map(
    (action) => assert.rejects(action, /Protocol connection failed/),
  )

  backend.protocolFailure('Protocol connection failed.')

  await Promise.all(rejections)
  assert.equal(killCount, 1)
  assert.equal(backend.getSnapshot().status, 'error')
})

test('Backend allows opening another Chat while generation remains tracked', async () => {
  const writes = []
  const backend = new BackendProcess('.', () => undefined)
  backend.child = {
    stdin: {
      writable: true,
      write: (value) => writes.push(value),
    },
  }
  backend.snapshot = {
    revision: 1,
    status: 'ready',
    capabilities: ['chat.stream', 'chat.sessions'],
    models: ['qwen3.5:9b'],
    modelName: 'qwen3.5:9b',
    chatId: 'chat_fixture',
    chatTitle: 'Elysia Chat',
  }
  backend.pendingRequests.set('generation-a', {
    method: 'chat.stream',
    chatId: 'chat_fixture',
    nextSequence: 0,
    streamCompleted: false,
    streamedReply: '',
    streamedLength: 0,
  })

  const opening = backend.openChat('chat_second')
  const openRequest = JSON.parse(writes.at(-1))
  assert.equal(openRequest.method, 'chat.open')
  assert.deepEqual(openRequest.params, { chatId: 'chat_second' })

  const sample = fixtures.validServerMessages.find(
    (candidate) => candidate.name === 'chat state response',
  )
  const response = structuredClone(sample.message)
  response.id = openRequest.id
  response.result.activeChat = {
    ...response.result.chats[1],
    messages: [],
  }
  backend.handleProtocolLine(JSON.stringify(response))

  const state = await opening
  assert.equal(state.activeChat.chatId, 'chat_second')
  assert.equal(backend.pendingRequests.has('generation-a'), true)
  assert.equal(backend.getSnapshot().chatId, 'chat_second')
})

test('Backend state machine rejects a stream sequence gap', () => {
  const { backend, events } = createPendingChat()

  backend.handleProtocolLine(streamFrame(1, 'out of order', false))

  assert.equal(events.at(-1).type, 'snapshot')
  assert.equal(events.at(-1).snapshot.status, 'error')
})

test('Backend state machine rejects a response that differs from chunks', () => {
  const { backend, events } = createPendingChat()

  backend.handleProtocolLine(streamFrame(0, 'visible', false))
  backend.handleProtocolLine(streamFrame(1, '', true))
  backend.handleProtocolLine(responseFrame('persisted differently'))

  assert.equal(events.at(-1).type, 'snapshot')
  assert.match(events.at(-1).snapshot.error, /streamed reply/)
})

test('Backend state machine rejects data after a terminal chunk', () => {
  const { backend, events } = createPendingChat()

  backend.handleProtocolLine(streamFrame(0, '', true))
  backend.handleProtocolLine(streamFrame(1, 'late', false))

  assert.equal(events.at(-1).type, 'snapshot')
  assert.equal(events.at(-1).snapshot.status, 'error')
})

test('Backend state machine resolves a typed Chat session action', async () => {
  const events = []
  const backend = new BackendProcess('.', (event) => events.push(event))
  backend.snapshot = {
    revision: 1,
    status: 'ready',
    capabilities: ['chat.sessions'],
    models: ['qwen3.5:9b'],
    modelName: 'qwen3.5:9b',
    chatId: 'chat_fixture',
    chatTitle: 'Old title',
  }
  let resolveState
  let rejectState
  const statePromise = new Promise((resolve, reject) => {
    resolveState = resolve
    rejectState = reject
  })
  backend.pendingRequests.set('chat-list-1', {
    method: 'chat.list',
    nextSequence: 0,
    streamCompleted: false,
    streamedReply: '',
    streamedLength: 0,
    resolveChatState: resolveState,
    rejectChatState: rejectState,
  })
  const sample = fixtures.validServerMessages.find(
    (candidate) => candidate.name === 'chat state response',
  )
  assert.ok(sample)

  backend.handleProtocolLine(JSON.stringify(sample.message))
  const state = await statePromise

  assert.equal(state.activeChat.chatId, 'chat_fixture')
  assert.equal(backend.getSnapshot().chatTitle, 'Elysia Chat')
  assert.equal(events.at(-1).type, 'snapshot')
})

test('Backend state machine resolves a typed Project action atomically', async () => {
  const events = []
  const backend = new BackendProcess('.', (event) => events.push(event))
  backend.snapshot = {
    revision: 1,
    status: 'ready',
    capabilities: ['chat.sessions', 'project.management'],
    models: ['qwen3.5:9b'],
    modelName: 'qwen3.5:9b',
    chatId: 'chat_fixture',
    chatTitle: 'Old title',
  }
  let resolveState
  let rejectState
  const statePromise = new Promise((resolve, reject) => {
    resolveState = resolve
    rejectState = reject
  })
  backend.pendingRequests.set('project-list-1', {
    method: 'project.list',
    nextSequence: 0,
    streamCompleted: false,
    streamedReply: '',
    streamedLength: 0,
    resolveProjectState: resolveState,
    rejectProjectState: rejectState,
  })
  const response = projectStateResponse()
  response.id = 'project-list-1'

  backend.handleProtocolLine(JSON.stringify(response))
  const state = await statePromise

  assert.equal(state.activeProject.projectId, 'project_fixture')
  assert.equal(state.chatState.activeChat.chatId, 'chat_fixture')
  assert.equal(backend.getSnapshot().chatTitle, 'Elysia Chat')
  assert.equal(events.at(-1).type, 'snapshot')
})

test('Backend sends and strictly resolves typed Settings actions', async () => {
  const writes = []
  const backend = new BackendProcess('.', () => undefined)
  backend.child = {
    stdin: {
      writable: true,
      write: (value) => writes.push(value),
    },
  }
  backend.snapshot = {
    revision: 1,
    status: 'ready',
    capabilities: ['settings.management'],
    models: ['qwen3.5:9b', 'llama3.2:3b'],
    modelName: 'qwen3.5:9b',
    chatId: 'chat_fixture',
    chatTitle: 'Elysia Chat',
  }

  const getting = backend.getSettings()
  const getRequest = JSON.parse(writes.at(-1))
  assert.equal(getRequest.method, 'settings.get')
  assert.deepEqual(getRequest.params, {})

  const getResponse = settingsStateResponse()
  getResponse.id = getRequest.id
  backend.handleProtocolLine(JSON.stringify(getResponse))
  const state = await getting
  assert.deepEqual(state, getResponse.result)

  const update = {
    expectedRevision: state.revision,
    settings: {
      ...state.settings,
      memoryRetrievalLimit: 9,
    },
  }
  const updating = backend.updateSettings(update)
  const updateRequest = JSON.parse(writes.at(-1))
  assert.equal(updateRequest.method, 'settings.update')
  assert.deepEqual(updateRequest.params, update)

  const updateResponse = settingsStateResponse()
  updateResponse.id = updateRequest.id
  updateResponse.result.revision = state.revision + 1
  updateResponse.result.settings = update.settings
  backend.handleProtocolLine(JSON.stringify(updateResponse))
  const updated = await updating
  assert.deepEqual(updated, updateResponse.result)
})

test('Backend rejects a pending Settings action on a malformed result', async () => {
  const writes = []
  let killCount = 0
  const backend = new BackendProcess('.', () => undefined)
  backend.child = {
    stdin: {
      writable: true,
      write: (value) => writes.push(value),
    },
    kill: () => {
      killCount += 1
      return true
    },
  }
  backend.snapshot = {
    revision: 1,
    status: 'ready',
    capabilities: ['settings.management'],
    models: ['qwen3.5:9b'],
    modelName: 'qwen3.5:9b',
    chatId: 'chat_fixture',
    chatTitle: 'Elysia Chat',
  }

  const getting = backend.getSettings()
  const request = JSON.parse(writes.at(-1))
  const response = settingsStateResponse()
  const secret = 'must-not-appear-in-errors'
  response.id = request.id
  response.result.settings.apiKey = secret
  backend.handleProtocolLine(JSON.stringify(response))

  await assert.rejects(
    getting,
    (error) => (
      error instanceof Error
      && /Invalid Backend protocol frame/.test(error.message)
      && !error.message.includes(secret)
    ),
  )
  assert.equal(killCount, 1)
  assert.equal(backend.getSnapshot().status, 'error')
})

test('Backend keeps an initialized child available for Settings repair', async () => {
  const writes = []
  let killCount = 0
  const backend = new BackendProcess('.', () => undefined)
  const child = {
    stdin: {
      writable: true,
      write: (value) => writes.push(value),
    },
    kill: () => {
      killCount += 1
      return true
    },
  }
  backend.child = child
  backend.snapshot = {
    revision: 1,
    status: 'initializing',
    capabilities: ['settings.management'],
    models: [],
  }
  backend.initializeRequestId = 'initialize-repair'
  backend.pendingRequests.set('initialize-repair', {
    method: 'initialize',
    nextSequence: 0,
    streamCompleted: false,
    streamedReply: '',
    streamedLength: 0,
  })

  backend.handleProtocolLine(JSON.stringify({
    type: 'response',
    protocol: fixtures.protocol,
    id: 'initialize-repair',
    ok: false,
    error: {
      code: 'backend.initialization_failed',
      message: 'Saved settings need repair.',
      retryable: false,
    },
  }))

  assert.equal(backend.child, child)
  assert.equal(killCount, 0)
  assert.equal(backend.getSnapshot().status, 'error')

  const getting = backend.getSettings()
  const request = JSON.parse(writes.at(-1))
  assert.equal(request.method, 'settings.get')
  const response = settingsStateResponse()
  response.id = request.id
  backend.handleProtocolLine(JSON.stringify(response))

  assert.equal((await getting).revision, response.result.revision)
  assert.equal(backend.child, child)
})

test('Backend restart settles only after ready and rejects on error', async () => {
  const backend = new BackendProcess('.', () => undefined)
  backend.stop = async () => undefined
  backend.start = () => {
    backend.updateSnapshot({ status: 'initializing' })
  }

  let settled = false
  const restarting = backend.restart()
  restarting.then(
    () => { settled = true },
    () => { settled = true },
  )
  await new Promise((resolve) => setImmediate(resolve))
  assert.equal(settled, false)

  backend.updateSnapshot({
    status: 'ready',
    modelName: 'qwen3.5:9b',
    models: ['qwen3.5:9b'],
    chatId: 'chat_fixture',
    chatTitle: 'Elysia Chat',
  })
  assert.equal((await restarting).status, 'ready')
  assert.equal(settled, true)

  const failingBackend = new BackendProcess('.', () => undefined)
  failingBackend.stop = async () => undefined
  failingBackend.start = () => {
    failingBackend.updateSnapshot({ status: 'initializing' })
  }
  const failingRestart = failingBackend.restart()
  const rejected = assert.rejects(
    failingRestart,
    /Could not initialize saved settings/,
  )
  await new Promise((resolve) => setImmediate(resolve))
  failingBackend.updateSnapshot({
    status: 'error',
    error: 'Could not initialize saved settings.',
  })
  await rejected
})

test('Backend rejects a concurrent restart before stopping twice', async () => {
  const backend = new BackendProcess('.', () => undefined)
  const firstRestartChild = { identity: 'first-restart-child' }
  backend.child = firstRestartChild

  let stopCalls = 0
  let releaseInitialStop
  const initialStop = new Promise((resolve) => {
    releaseInitialStop = resolve
  })
  backend.stop = async () => {
    stopCalls += 1
    await initialStop
  }
  backend.start = () => {
    backend.updateSnapshot({ status: 'initializing' })
  }

  const firstRestart = backend.restart()
  await assert.rejects(
    backend.restart(),
    /A Backend restart is already pending/,
  )
  assert.equal(stopCalls, 1)
  assert.equal(backend.child, firstRestartChild)

  releaseInitialStop()
  await new Promise((resolve) => setImmediate(resolve))
  backend.updateSnapshot({
    status: 'ready',
    modelName: 'qwen3.5:9b',
    models: ['qwen3.5:9b'],
    chatId: 'chat_fixture',
    chatTitle: 'Elysia Chat',
  })
  assert.equal((await firstRestart).status, 'ready')
})

test('Backend explicit stop promptly rejects an in-flight restart', async () => {
  const backend = new BackendProcess('.', () => undefined)
  backend.stop = async () => undefined
  backend.start = () => {
    backend.updateSnapshot({ status: 'initializing' })
  }

  const restarting = backend.restart()
  const outcomePromise = restarting.then(
    () => ({ kind: 'resolved' }),
    (error) => ({ kind: 'rejected', error }),
  )
  await new Promise((resolve) => setImmediate(resolve))
  assert.notEqual(backend.restartCompletion, null)

  delete backend.stop
  await backend.stop()
  const outcome = await Promise.race([
    outcomePromise,
    new Promise((resolve) => {
      setTimeout(() => resolve({ kind: 'timeout' }), 100)
    }),
  ])
  if (outcome.kind === 'timeout') {
    backend.rejectRestartCompletion('Test cleanup after restart timeout.')
  }

  assert.equal(outcome.kind, 'rejected')
  assert.match(
    outcome.error.message,
    /stopped before its restart completed/,
  )
  assert.equal(backend.getSnapshot().status, 'stopped')
})

test('Backend stop rejects pending Chat, Project, and Settings actions', async () => {
  const backend = new BackendProcess('.', () => undefined)
  const child = new EventEmitter()
  child.stdin = {
    writable: true,
    write: () => true,
  }
  child.kill = () => true
  backend.child = child
  backend.snapshot = {
    revision: 1,
    status: 'ready',
    capabilities: ['chat.sessions', 'project.management'],
    models: ['qwen3.5:9b'],
    modelName: 'qwen3.5:9b',
    chatId: 'chat_fixture',
    chatTitle: 'Elysia Chat',
  }

  let rejectChat
  const chatPromise = new Promise((_resolve, reject) => {
    rejectChat = reject
  })
  let rejectProject
  const projectPromise = new Promise((_resolve, reject) => {
    rejectProject = reject
  })
  backend.pendingRequests.set('chat-list-stop', {
    method: 'chat.list',
    nextSequence: 0,
    streamCompleted: false,
    streamedReply: '',
    streamedLength: 0,
    rejectChatState: rejectChat,
  })
  backend.pendingRequests.set('project-list-stop', {
    method: 'project.list',
    nextSequence: 0,
    streamCompleted: false,
    streamedReply: '',
    streamedLength: 0,
    rejectProjectState: rejectProject,
  })
  let rejectSettings
  const settingsPromise = new Promise((_resolve, reject) => {
    rejectSettings = reject
  })
  backend.pendingRequests.set('settings-get-stop', {
    method: 'settings.get',
    nextSequence: 0,
    streamCompleted: false,
    streamedReply: '',
    streamedLength: 0,
    rejectSettingsState: rejectSettings,
  })

  const stopping = backend.stop()
  await assert.rejects(chatPromise, /stopping before the action completed/)
  await assert.rejects(projectPromise, /stopping before the action completed/)
  await assert.rejects(settingsPromise, /stopping before the action completed/)
  child.emit('exit', 0, null)
  await stopping
})

test('renderer source policy accepts only the exact development document', () => {
  const policy = {
    appPath: '/application',
    developmentUrl: 'http://localhost:5173',
    isPackaged: false,
    platform: 'linux',
  }

  assert.equal(isTrustedRendererUrl('http://localhost:5173/', policy), true)
  assert.equal(isTrustedRendererUrl('http://127.0.0.1:5173/', policy), false)
  assert.equal(isTrustedRendererUrl('http://localhost:5173/iframe', policy), false)
  assert.equal(isTrustedRendererUrl('https://localhost:5173/', policy), false)
})

test('renderer source policy accepts only the packaged index file', () => {
  const appPath = path.resolve('fixture-app')
  const policy = {
    appPath,
    developmentUrl: 'http://localhost:5173',
    isPackaged: true,
    platform: process.platform,
  }
  const indexUrl = pathToFileURL(
    path.join(appPath, 'dist', 'index.html'),
  ).href
  const otherUrl = pathToFileURL(
    path.join(appPath, 'dist', 'other.html'),
  ).href

  assert.equal(isTrustedRendererUrl(indexUrl, policy), true)
  assert.equal(isTrustedRendererUrl(otherUrl, policy), false)
})

test('external link policy allows credential-free HTTP(S) URLs only', () => {
  assert.equal(
    parseSafeExternalUrl('https://example.com/docs?q=elysia#message'),
    'https://example.com/docs?q=elysia#message',
  )
  assert.throws(() => parseSafeExternalUrl('javascript:alert(1)'))
  assert.throws(() => parseSafeExternalUrl('https://user@example.com/'))
  assert.throws(() => parseSafeExternalUrl('https://example.com\0.invalid/'))
  assert.throws(() => parseSafeExternalUrl(' https://example.com/'))
})
